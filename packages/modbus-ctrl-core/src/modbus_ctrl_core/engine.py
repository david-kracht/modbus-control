from __future__ import annotations
import logging
from typing import Any, Optional

from modbus_schema_common.models import ModbusRegisterBase, ModbusRegisterType
from modbus_schema_common.resolver import resolve_schema
from modbus_common import DeviceConfig
from modbus_common.client import ModbusClientWrapper
from modbus_common.aggregator import build_read_blocks
from modbus_common import translator
from pymodbus.pdu import ExceptionResponse

logger = logging.getLogger(__name__)


class ModbusControlEngine:
    """Connects to a Modbus device, reads/writes registers via the schema."""

    def __init__(
        self,
        device: DeviceConfig,
        unsupported_registers: Optional[set[str]] = None,
    ):
        self.device = device
        self.schema = resolve_schema(device.schema_name)
        self.client = ModbusClientWrapper(host=device.host, port=device.port)
        self.unsupported_registers = unsupported_registers or set()
        self.newly_failed_registers: set[str] = set()

        if device.registers:
            self.schema = self.schema.model_copy(deep=True)
            reg_map = {r.name: r for r in self.schema.registers}
            filtered = [reg_map[n] for n in device.registers if n in reg_map]
            if filtered:
                self.schema.registers = filtered

        if self.unsupported_registers:
            self.schema = self.schema.model_copy(deep=True)
            self.schema.registers = [
                r for r in self.schema.registers
                if r.name not in self.unsupported_registers
            ]

        self.registers_by_name: dict[str, ModbusRegisterBase] = {
            r.name: r for r in self.schema.registers
        }
        self.registers_by_addr: dict[int, ModbusRegisterBase] = {
            r.address_dec: r for r in self.schema.registers
        }

    async def read_all(self, gap_threshold: int = 5) -> dict[str, Any]:
        """Read all configured registers and return name → value dict."""
        self.newly_failed_registers = set()
        await self.client.connect()
        if not self.client.connected:
            raise ConnectionError(
                f"Could not connect to {self.device.host}:{self.device.port}"
            )

        blocks = build_read_blocks(
            self.schema.registers, gap_threshold=gap_threshold
        )
        results: dict[str, Any] = {}

        for block in blocks:
            start = block["start_addr"]
            count = block["count"]
            rtype = block["register_type"]
            slave = self.device.unit_id
            proto_start = block["registers"][0].protocol_address_dec

            logger.debug(
                "Reading block: %s[%d:%d] proto=%d",
                rtype.value, start, start + count, proto_start,
            )

            try:
                res = await self._fc_read(rtype, proto_start, count, slave)

                if res.isError():
                    logger.warning(
                        "Block error %s[%d:%d]: %s — individual fallback",
                        rtype.value, start, start + count, res,
                    )
                    await self._read_individual(
                        block["registers"], rtype, slave, results
                    )
                    continue

                for reg in block["registers"]:
                    offset = reg.address_dec - start
                    if rtype in (
                        ModbusRegisterType.DISCRETE_INPUT,
                        ModbusRegisterType.COIL,
                    ):
                        if offset < len(res.bits):
                            results[reg.name] = res.bits[offset]
                    else:
                        num_regs = reg.register_count
                        if offset + num_regs <= len(res.registers):
                            raw = res.registers[offset:offset + num_regs]
                            results[reg.name] = translator.unpack_register_value(
                                raw, reg.data_type,
                                byte_order=self.schema.byte_order,
                                word_order=self.schema.word_order,
                            )

            except Exception as e:  # noqa: BLE001
                curr = __import__("asyncio").current_task()
                if curr is not None and curr.cancelling() > 0:
                    raise __import__("asyncio").CancelledError() from e
                logger.warning(
                    "Block exception %s[%d:%d]: %s — individual fallback",
                    rtype.value, start, start + count, e,
                )
                if not self.client.connected:
                    await self.client.connect()
                try:
                    await self._read_individual(
                        block["registers"], rtype, slave, results
                    )
                except Exception as inner:  # noqa: BLE001
                    logger.error("Individual fallback failed: %s", inner)

        return results

    async def _fc_read(self, rtype: ModbusRegisterType, addr: int, count: int, slave: int):
        """Dispatch a single FC read."""
        c = self.client.client
        if rtype == ModbusRegisterType.DISCRETE_INPUT:
            return await c.read_discrete_inputs(addr, count=count, device_id=slave)
        if rtype == ModbusRegisterType.COIL:
            return await c.read_coils(addr, count=count, device_id=slave)
        if rtype == ModbusRegisterType.INPUT_REGISTER:
            return await c.read_input_registers(addr, count=count, device_id=slave)
        return await c.read_holding_registers(addr, count=count, device_id=slave)

    async def _read_individual(
        self,
        registers: list[ModbusRegisterBase],
        rtype: ModbusRegisterType,
        slave: int,
        results: dict[str, Any],
    ) -> None:
        """Fallback: read each register individually."""
        for reg in registers:
            proto_start = reg.protocol_address_dec
            count = reg.register_count
            try:
                res = await self._fc_read(rtype, proto_start, count, slave)

                if res.isError():
                    logger.debug(
                        "Individual read failed %s@%d: %s",
                        reg.name, reg.address_dec, res,
                    )
                    if (
                        isinstance(res, ExceptionResponse)
                        and res.exception_code in (1, 2)
                    ):
                        self.newly_failed_registers.add(reg.name)
                    continue

                if rtype in (
                    ModbusRegisterType.DISCRETE_INPUT,
                    ModbusRegisterType.COIL,
                ):
                    if res.bits:
                        results[reg.name] = res.bits[0]
                elif len(res.registers) >= count:
                    results[reg.name] = translator.unpack_register_value(
                        res.registers[:count], reg.data_type,
                        byte_order=self.schema.byte_order,
                        word_order=self.schema.word_order,
                    )

            except Exception as e:  # noqa: BLE001
                logger.debug(
                    "Individual exception %s@%d: %s",
                    reg.name, reg.address_dec, e,
                )
                if not self.client.connected:
                    await self.client.connect()

    async def write_registers(self, writes: dict[str, Any]) -> dict[str, str]:
        """Write registers by name or decimal address string."""
        await self.client.connect()
        if not self.client.connected:
            raise ConnectionError(
                f"Could not connect to {self.device.host}:{self.device.port}"
            )

        results: dict[str, str] = {}
        slave = self.device.unit_id

        for key, val in writes.items():
            reg = self.registers_by_name.get(key)
            if not reg:
                try:
                    reg = self.registers_by_addr.get(int(key))
                except (ValueError, TypeError):
                    pass
            if not reg:
                results[key] = "Error: Register not found in schema"
                continue

            rtype = reg.register_type
            if rtype not in (
                ModbusRegisterType.COIL,
                ModbusRegisterType.HOLDING_REGISTER,
            ):
                results[reg.name] = (
                    f"Error: Register type {rtype.value} is read-only"
                )
                continue

            try:
                proto_addr = reg.protocol_address_dec
                c = self.client.client
                if rtype == ModbusRegisterType.COIL:
                    res = await c.write_coil(
                        proto_addr, bool(val), device_id=slave
                    )
                else:
                    raw_regs = translator.pack_register_value(
                        val, reg.data_type,
                        byte_order=self.schema.byte_order,
                        word_order=self.schema.word_order,
                    )
                    if len(raw_regs) == 1:
                        res = await c.write_register(
                            proto_addr, raw_regs[0], device_id=slave
                        )
                    else:
                        res = await c.write_registers(
                            proto_addr, raw_regs, device_id=slave
                        )

                results[reg.name] = (
                    f"Error: {res}" if res.isError() else "Success"
                )

            except Exception as e:  # noqa: BLE001
                logger.error("Write failed %s: %s", reg.name, e)
                results[reg.name] = f"Error: {e}"

        return results
