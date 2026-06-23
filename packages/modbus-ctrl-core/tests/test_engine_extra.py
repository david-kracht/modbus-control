import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from modbus_common import DeviceConfig
from modbus_ctrl_core import ModbusControlEngine

@pytest.mark.anyio
@patch('modbus_ctrl_core.engine.ModbusClientWrapper')
async def test_engine_read_all_connection_error(mock_client_cls):
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.connected = False  # Simulate failure
    mock_client_cls.return_value = mock_client

    device = DeviceConfig(host="127.0.0.1", schema_name="v10")
    engine = ModbusControlEngine(device)
    
    with pytest.raises(ConnectionError):
        await engine.read_all()

@pytest.mark.anyio
@patch('modbus_ctrl_core.engine.ModbusClientWrapper')
async def test_engine_read_all_block_exception(mock_client_cls):
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.connected = True
    
    mock_pymodbus = MagicMock()
    # Raise exception during read
    mock_pymodbus.read_holding_registers = AsyncMock(side_effect=Exception("Timeout"))
    
    # We expect individual fallback to be called, which will also use read_holding_registers
    # Let's make it succeed on the second try
    mock_succ = MagicMock()
    mock_succ.isError.return_value = False
    mock_succ.registers = [50, 60]
    
    mock_pymodbus.read_holding_registers.side_effect = [Exception("Timeout"), mock_succ]
    
    mock_client.client = mock_pymodbus
    mock_client_cls.return_value = mock_client

    device = DeviceConfig(host="127.0.0.1", schema_name="v10", registers=["BatBocConfig"])
    engine = ModbusControlEngine(device)
    
    res = await engine.read_all()
    assert res.get("BatBocConfig") is not None

@pytest.mark.anyio
@patch('modbus_ctrl_core.engine.ModbusClientWrapper')
async def test_engine_write_registers_connection_error(mock_client_cls):
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.connected = False
    mock_client_cls.return_value = mock_client

    device = DeviceConfig(host="127.0.0.1", schema_name="v10")
    engine = ModbusControlEngine(device)
    
    with pytest.raises(ConnectionError):
        await engine.write_registers({"BatBocConfig": 1})

@pytest.mark.anyio
@patch('modbus_ctrl_core.engine.ModbusClientWrapper')
async def test_engine_write_coil(mock_client_cls):
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.connected = True
    
    mock_pymodbus = MagicMock()
    mock_write_res = MagicMock()
    mock_write_res.isError.return_value = False
    mock_pymodbus.write_coil = AsyncMock(return_value=mock_write_res)
    mock_client.client = mock_pymodbus
    mock_client_cls.return_value = mock_client

    device = DeviceConfig(host="127.0.0.1", schema_name="v10", registers=["SystemOn"])
    engine = ModbusControlEngine(device)
    
    # SystemOn is typically a WO Coil
    # Let's dynamically add a mock coil to the schema if it's missing, but SystemOn might be in v10
    from modbus_schema_common.models import ModbusRegisterBase, ModbusRegisterType, ModbusDataType
    reg = ModbusRegisterBase(
        name="TestCoil",
        address_hex="0x0",
        address_dec=1,
        description="Test Coil",
        data_type=ModbusDataType.BIT,
        register_count=1,
        access="RW",
        register_type=ModbusRegisterType.COIL
    )
    reg.protocol_address_dec = 0
    engine.registers_by_name["TestCoil"] = reg
    
    results = await engine.write_registers({"TestCoil": True})
    assert results.get("TestCoil") == "Success"
    mock_pymodbus.write_coil.assert_called()

@pytest.mark.anyio
@patch('modbus_ctrl_core.engine.ModbusClientWrapper')
async def test_engine_write_exception(mock_client_cls):
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.connected = True
    
    mock_pymodbus = MagicMock()
    mock_pymodbus.write_registers = AsyncMock(side_effect=Exception("Write Error"))
    mock_client.client = mock_pymodbus
    mock_client_cls.return_value = mock_client

    device = DeviceConfig(host="127.0.0.1", schema_name="v10", registers=["BatBocConfig"])
    engine = ModbusControlEngine(device)
    
    results = await engine.write_registers({"BatBocConfig": 150})
    assert "Error: Write Error" in results.get("BatBocConfig")
