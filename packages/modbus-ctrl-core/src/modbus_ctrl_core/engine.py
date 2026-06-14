import json
import logging
from pathlib import Path
from typing import Any, Optional

from modbus_schema_common.models import (
    ModbusInterfaceSpecification,
    ModbusRegister,
    ModbusRegisterBase,
    ModbusRegisterType,
)
from modbus_ctrl_contracts import DeviceConfig
from modbus_ctrl_core.client import ModbusClientWrapper
from modbus_ctrl_core.aggregator import build_read_blocks, ReadBlock
from modbus_ctrl_core import translator

logger = logging.getLogger(__name__)

from modbus_schema_common.resolver import resolve_schema

class ModbusControlEngine:
    """
    Main orchestration engine for Modbus communications.

    This class provides a high-level API to interact with Modbus devices. It handles
    connection lifecycle, block aggregation for efficient reading, and translation
    of raw registers into strongly typed Python objects according to the device schema.
    """
    def __init__(self, device: DeviceConfig, unsupported_registers: Optional[set[str]] = None):
        self.device = device
        self.schema = resolve_schema(device.schema_name)
        self.client = ModbusClientWrapper(host=device.host, port=device.port)
        self.unsupported_registers = unsupported_registers or set()
        self.newly_failed_registers = set()
        
        # If device specifies a list of allowed registers, filter and sort them
        if device.registers:
            self.schema = self.schema.model_copy(deep=True)
            reg_map = {r.name: r for r in self.schema.registers}
            filtered_regs = [
                reg_map[name] for name in device.registers if name in reg_map
            ]
            if filtered_regs:
                self.schema.registers = filtered_regs

        # Filter out registers that are known to be unsupported by this device
        if self.unsupported_registers:
            self.schema = self.schema.model_copy(deep=True)
            self.schema.registers = [
                r for r in self.schema.registers if r.name not in self.unsupported_registers
            ]

        # Map registers by name and decimal address for fast lookup
        self.registers_by_name: dict[str, ModbusRegisterBase] = {
            r.name: r for r in self.schema.registers
        }
        self.registers_by_addr: dict[int, ModbusRegisterBase] = {
            r.address_dec: r for r in self.schema.registers
        }

    async def read_all(self, gap_threshold: int = 5) -> dict[str, Any]:
        """
        Connects to the Modbus device, reads all configured registers in optimal batches,
        unpacks raw registers into scaled values, and returns them as a key-value dictionary.
        """
        await self.client.connect()
        if not self.client.connected:
            raise ConnectionError(f"Could not connect to Modbus device at {self.device.host}:{self.device.port}")

        blocks = build_read_blocks(self.schema.registers, gap_threshold=gap_threshold)
        results: dict[str, Any] = {}

        for block in blocks:
            start = block["start_addr"]
            count = block["count"]
            rtype = block["register_type"]
            slave = self.device.unit_id

            logger.debug("Reading block: Type=%s, Start=%d, Count=%d", rtype.value, start, count)

            try:
                if rtype == ModbusRegisterType.DISCRETE_INPUT:
                    res = await self.client.client.read_discrete_inputs(start, count=count, device_id=slave)
                elif rtype == ModbusRegisterType.COIL:
                    res = await self.client.client.read_coils(start, count=count, device_id=slave)
                elif rtype == ModbusRegisterType.INPUT_REGISTER:
                    res = await self.client.client.read_input_registers(start, count=count, device_id=slave)
                elif rtype == ModbusRegisterType.HOLDING_REGISTER:
                    res = await self.client.client.read_holding_registers(start, count=count, device_id=slave)
                else:
                    continue

                if res.isError():
                    logger.warning("Modbus error reading block %s[%d:%d]: %s. Falling back to individual reads.", rtype.value, start, start+count, res)
                    await self._read_individual_registers(block["registers"], rtype, slave, results)
                    continue

                # Unpack each register in the block
                for reg in block["registers"]:
                    offset = reg.address_dec - start
                    
                    if rtype in (ModbusRegisterType.DISCRETE_INPUT, ModbusRegisterType.COIL):
                        # Bit-level reading
                        if offset < len(res.bits):
                            results[reg.name] = res.bits[offset]
                    else:
                        # Register-level reading
                        # Determine number of registers
                        num_regs = reg.register_count
                        if offset + num_regs <= len(res.registers):
                            raw_regs = res.registers[offset : offset + num_regs]
                            val = translator.unpack_register_value(
                                raw_regs,
                                reg.data_type,
                                byte_order=self.schema.byte_order,
                                word_order=self.schema.word_order,
                            )
                            results[reg.name] = val

            except Exception as e:
                logger.warning("Exception reading block %s[%d:%d]: %s. Falling back to individual reads.", rtype.value, start, start+count, e)
                try:
                    await self._read_individual_registers(block["registers"], rtype, slave, results)
                except Exception as inner_e:
                    logger.error("Failed individual fallback reads for block: %s", inner_e)

        return results

    async def _read_individual_registers(
        self,
        registers: list[ModbusRegisterBase],
        rtype: ModbusRegisterType,
        slave: int,
        results: dict[str, Any],
    ):
        """Read registers individually as a robust fallback when block read fails."""
        from pymodbus.pdu import ExceptionResponse
        for reg in registers:
            start = reg.address_dec
            count = reg.register_count
            try:
                if rtype == ModbusRegisterType.DISCRETE_INPUT:
                    res = await self.client.client.read_discrete_inputs(start, count=count, device_id=slave)
                elif rtype == ModbusRegisterType.COIL:
                    res = await self.client.client.read_coils(start, count=count, device_id=slave)
                elif rtype == ModbusRegisterType.INPUT_REGISTER:
                    res = await self.client.client.read_input_registers(start, count=count, device_id=slave)
                elif rtype == ModbusRegisterType.HOLDING_REGISTER:
                    res = await self.client.client.read_holding_registers(start, count=count, device_id=slave)
                else:
                    continue

                if res.isError():
                    logger.debug("Failed individual read for register %s at %d: %s", reg.name, start, res)
                    if isinstance(res, ExceptionResponse) and res.exception_code in (1, 2):
                        self.newly_failed_registers.add(reg.name)
                    continue

                if rtype in (ModbusRegisterType.DISCRETE_INPUT, ModbusRegisterType.COIL):
                    if len(res.bits) > 0:
                        results[reg.name] = res.bits[0]
                else:
                    if len(res.registers) >= count:
                        val = translator.unpack_register_value(
                            res.registers[:count],
                            reg.data_type,
                            byte_order=self.schema.byte_order,
                            word_order=self.schema.word_order,
                        )
                        results[reg.name] = val

            except Exception as e:
                logger.debug("Exception in individual read for register %s at %d: %s", reg.name, start, e)

    async def write_registers(self, writes: dict[str, Any]) -> dict[str, str]:
        """
        Executes writes for the given register values.
        Key can be register name or address string.
        """
        await self.client.connect()
        if not self.client.connected:
            raise ConnectionError(f"Could not connect to Modbus device at {self.device.host}:{self.device.port}")

        results: dict[str, str] = {}
        slave = self.device.unit_id

        for key, val in writes.items():
            # Resolve key to ModbusRegisterBase
            reg = self.registers_by_name.get(key)
            if not reg:
                # Try parsing as integer address
                try:
                    addr_dec = int(key)
                    reg = self.registers_by_addr.get(addr_dec)
                except ValueError:
                    pass

            if not reg:
                results[key] = f"Error: Register not found in schema"
                continue

            rtype = reg.register_type
            if rtype not in (ModbusRegisterType.COIL, ModbusRegisterType.HOLDING_REGISTER):
                results[reg.name] = f"Error: Register type {rtype.value} is read-only"
                continue

            try:
                if rtype == ModbusRegisterType.COIL:
                    # Coils accept raw boolean value
                    write_val = bool(val)
                    res = await self.client.client.write_coil(reg.address_dec, write_val, device_id=slave)
                else:
                    # Holding Register packing
                    raw_regs = translator.pack_register_value(
                        val,
                        reg.data_type,
                        byte_order=self.schema.byte_order,
                        word_order=self.schema.word_order,
                    )
                    if len(raw_regs) == 1:
                        res = await self.client.client.write_register(reg.address_dec, raw_regs[0], device_id=slave)
                    else:
                        res = await self.client.client.write_registers(reg.address_dec, raw_regs, device_id=slave)

                if res.isError():
                    results[reg.name] = f"Error: {res}"
                else:
                    results[reg.name] = f"Success"

            except Exception as e:
                logger.error("Failed to write to register %s: %s", reg.name, e)
                results[reg.name] = f"Error: {e}"

        return results
