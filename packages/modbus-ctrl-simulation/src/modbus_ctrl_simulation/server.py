import argparse
import asyncio
import logging
import random
from pymodbus.server import StartAsyncTcpServer
from pymodbus.datastore import ModbusServerContext, ModbusDeviceContext, ModbusSparseDataBlock
from modbus_schema_common.models import ModbusRegisterType, ModbusDataType

from modbus_ctrl_core.engine import resolve_schema
from modbus_ctrl_core import translator

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("modbus-sim")

async def run_server(schema_name: str, host: str, port: int):
    # Load schema
    schema = resolve_schema(schema_name)
    logger.info("Simulating device: %s (Firmware: %s)", schema.device_name, schema.firmware or "Unknown")

    di_vals = {}
    co_vals = {}
    ir_vals = {}
    hr_vals = {}

    input_floats = {r.address_dec: r for r in schema.registers if r.register_type == ModbusRegisterType.INPUT_REGISTER and r.data_type == ModbusDataType.FLOAT32}
    trigger_coils = {r.address_dec for r in schema.registers if r.register_type == ModbusRegisterType.COIL and r.access == "WO"}

    # Find min/max range for each register type to prevent gaps from being marked INVALID
    ranges = {}
    for reg in schema.registers:
        addr = reg.address_dec
        rtype = reg.register_type
        count = getattr(reg, "register_count", 1)
        if rtype not in ranges:
            ranges[rtype] = (addr, addr + count - 1)
        else:
            cur_min, cur_max = ranges[rtype]
            ranges[rtype] = (min(cur_min, addr), max(cur_max, addr + count - 1))

    # Prepopulate ranges with default 0 / False
    for rtype, (r_min, r_max) in ranges.items():
        if rtype == ModbusRegisterType.DISCRETE_INPUT:
            for a in range(r_min, r_max + 1):
                di_vals[a] = False
        elif rtype == ModbusRegisterType.COIL:
            for a in range(r_min, r_max + 1):
                co_vals[a] = False
        elif rtype == ModbusRegisterType.INPUT_REGISTER:
            for a in range(r_min, r_max + 1):
                ir_vals[a] = 0
        elif rtype == ModbusRegisterType.HOLDING_REGISTER:
            for a in range(r_min, r_max + 1):
                hr_vals[a] = 0

    # Overwrite default values based on schema registers
    for reg in schema.registers:
        addr = reg.address_dec
        rtype = reg.register_type

        # Generate a mock value based on name/type
        mock_val = 0
        if reg.data_type == ModbusDataType.BIT:
            mock_val = False
        elif reg.data_type == ModbusDataType.STRING:
            mock_val = f"Sim-{reg.name[:8]}"
        elif reg.data_type == ModbusDataType.FLOAT32:
            # Generate a nice float value depending on register name
            if "temp" in reg.name.lower():
                mock_val = 22.5
            elif "vol" in reg.name.lower() or "dod" in reg.name.lower() or "soc" in reg.name.lower():
                mock_val = 80.0
            elif "bat" in reg.name.lower():
                mock_val = 13.2
            else:
                mock_val = 1.0
        else:
            # Integer types
            if reg.enum_values:
                # Use a valid enum key (first one)
                mock_val = int(next(iter(reg.enum_values.keys())))
            else:
                mock_val = 42

        # Convert to Modbus words
        if rtype in (ModbusRegisterType.INPUT_REGISTER, ModbusRegisterType.HOLDING_REGISTER):
            words = translator.pack_register_value(
                mock_val,
                reg.data_type,
                byte_order=schema.byte_order,
                word_order=schema.word_order,
            )
            target = ir_vals if rtype == ModbusRegisterType.INPUT_REGISTER else hr_vals
            for offset, w in enumerate(words):
                target[addr + offset] = w
        else:
            # Coil / Discrete Input
            target = di_vals if rtype == ModbusRegisterType.DISCRETE_INPUT else co_vals
            target[addr] = bool(mock_val)

    di_block = ModbusSparseDataBlock(di_vals)
    co_block = ModbusSparseDataBlock(co_vals)
    ir_block = ModbusSparseDataBlock(ir_vals)
    hr_block = ModbusSparseDataBlock(hr_vals)

    device_context = ModbusDeviceContext(di=di_block, co=co_block, ir=hr_block, hr=ir_block)
    server_context = ModbusServerContext(devices=device_context, single=True)

    # Hook the custom action function
    async def sim_action(func_code, start_address, address, count, registers, values):
        logger.info("sim_action: func_code=%d, start_address=%d, address=%d, count=%d, values=%s", func_code, start_address, address, count, values)
        if values is None:
            # Read request
            if func_code == 4:
                # Add jitter to float32 input registers on-the-fly
                for addr in range(address, address + count):
                    if addr in input_floats and (addr + 1) < start_address + len(registers):
                        offset0 = addr - start_address
                        offset1 = offset0 + 1
                        try:
                            reg = input_floats[addr]
                            val = translator.unpack_register_value(
                                [registers[offset0], registers[offset1]],
                                reg.data_type,
                                byte_order=schema.byte_order,
                                word_order=schema.word_order,
                            )
                            # Add small jitter
                            val += random.uniform(-0.05, 0.05)
                            new_words = translator.pack_register_value(
                                val,
                                reg.data_type,
                                byte_order=schema.byte_order,
                                word_order=schema.word_order,
                            )
                            registers[offset0] = new_words[0]
                            registers[offset1] = new_words[1]
                        except Exception as e:
                            logger.error("Jitter update failed for register %d: %s", addr, e)
        else:
            # Write request
            if func_code in (5, 15):
                for i, val in enumerate(values):
                    coil_addr = address + i
                    if coil_addr in trigger_coils and val:
                        logger.info("Trigger coil written to ON at address %d. Scheduling auto-reset to OFF in 500ms", coil_addr)
                        async def reset_coil(addr):
                            await asyncio.sleep(0.5)
                            word_idx = int(addr / 16) - start_address
                            bit_idx = addr % 16
                            if 0 <= word_idx < len(registers):
                                registers[word_idx] &= ~(1 << bit_idx)
                                logger.info("Trigger coil at address %d auto-reset to OFF", addr)
                        asyncio.create_task(reset_coil(coil_addr))
        return None

    device_context.simdevice.action = sim_action

    logger.info("Starting Modbus TCP Simulator on %s:%d...", host, port)
    await StartAsyncTcpServer(context=server_context, address=(host, port))
    await asyncio.Event().wait()

def main():
    from modbus_ctrl_core import config
    parser = argparse.ArgumentParser(description="Modbus TCP Mock Simulator Server")
    parser.add_argument("--schema", type=str, default=None, help="Schema key or path (e.g. v10, v20, v30)")
    parser.add_argument("--host", type=str, default=None, help="Host to listen on")
    parser.add_argument("--port", type=int, default=None, help="Port to listen on")
    args = parser.parse_args()

    schema = args.schema if args.schema is not None else config.SIM_SCHEMA
    host = args.host if args.host is not None else config.SIM_HOST
    port = args.port if args.port is not None else config.SIM_PORT

    try:
        asyncio.run(run_server(schema, host, port))
    except KeyboardInterrupt:
        logger.info("Simulator stopped.")

if __name__ == "__main__":
    main()
