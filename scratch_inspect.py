import asyncio
from modbus_ctrl_core.engine import resolve_schema
from modbus_ctrl_core import translator
from efoy_modbus.models import ModbusRegisterType, ModbusDataType
from pymodbus.datastore import ModbusDeviceContext, ModbusSparseDataBlock
from pymodbus.simulator.simruntime import SimRuntime

async def test():
    schema = resolve_schema('v10')
    di_vals = {}
    co_vals = {}
    ir_vals = {}
    hr_vals = {}
    for reg in schema.registers:
        addr = reg.address_dec
        rtype = reg.register_type
        mock_val = 0
        if rtype in (ModbusRegisterType.INPUT_REGISTER, ModbusRegisterType.HOLDING_REGISTER):
            sf = getattr(reg, "scale_factor", 1.0)
            words = translator.pack_register_value(mock_val, reg.data_type, scale_factor=sf)
            target = ir_vals if rtype == ModbusRegisterType.INPUT_REGISTER else hr_vals
            for offset, w in enumerate(words):
                target[addr + offset] = w
        else:
            target = di_vals if rtype == ModbusRegisterType.DISCRETE_INPUT else co_vals
            target[addr] = bool(mock_val)

    di_block = ModbusSparseDataBlock(di_vals)
    co_block = ModbusSparseDataBlock(co_vals)
    ir_block = ModbusSparseDataBlock(ir_vals)
    hr_block = ModbusSparseDataBlock(hr_vals)

    print("hr_vals keys:", sorted(hr_vals.keys()))
    print("ir_vals keys:", sorted(ir_vals.keys()))

    device_context = ModbusDeviceContext(di=di_block, co=co_block, ir=hr_block, hr=ir_block)
    runtime = SimRuntime(device_context.simdevice)
    print("h block start:", runtime.block['h'][0])
    print("h block len:", runtime.block['h'][1])
    offset = 40081 - runtime.block['h'][0]
    print("40081 offset:", offset)
    if 0 <= offset < len(runtime.block['h'][3]):
        print("40081 flag:", runtime.block['h'][3][offset])
    else:
        print("40081 out of range")

asyncio.run(test())
