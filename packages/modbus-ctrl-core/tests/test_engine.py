from __future__ import annotations
import pytest
from modbus_common import DeviceConfig
from modbus_ctrl_core import ModbusControlEngine

def test_engine_register_filtering():
    # Construct a device config with a restricted registers list (out of schema order)
    device = DeviceConfig(
        name="TestSimDevice",
        host="127.0.0.1",
        port=5020,
        schema_name="v10",
        registers=["BatBocConfig", "BatIdConfig"]
    )
    
    # Initialize ModbusControlEngine
    engine = ModbusControlEngine(device)
    
    # 1. Verify schema registers are filtered to exactly the specified list, preserving order
    assert len(engine.schema.registers) == 2
    assert [r.name for r in engine.schema.registers] == ["BatBocConfig", "BatIdConfig"]
    
    # 2. Verify registers lookup structures are restricted
    assert "BatBocConfig" in engine.registers_by_name
    assert "BatIdConfig" in engine.registers_by_name
    assert "BatEocConfig" not in engine.registers_by_name

    # 3. Verify address lookup is restricted too
    addr_boc = engine.registers_by_name["BatBocConfig"].address_dec
    assert addr_boc in engine.registers_by_addr
    
    # BatEocConfig address (40005) should not be mapped
    assert 40005 not in engine.registers_by_addr

def test_device_config_duplicate_registers():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError) as exc_info:
        DeviceConfig(
            host="127.0.0.1",
            schema_name="v10",
            registers=["BatBocConfig", "BatBocConfig"]
        )
    assert "Duplicate register names are not allowed in the config" in str(exc_info.value)

def test_device_config_invalid_register():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError) as exc_info:
        DeviceConfig(
            host="127.0.0.1",
            schema_name="v10",
            registers=["InvalidRegister"]
        )
    assert "are not defined in schema" in str(exc_info.value)

def test_app_config_load_corrupted_yaml():
    import pytest
    import tempfile
    from pathlib import Path
    from modbus_common import AppConfig

    yaml_content = """
devices:
- name: BadDevice
  host: 127.0.0.1
  schema_name: v10
  registers:
  - BatBocConfig
  - BatBocConfig
  - InvalidName
"""
    with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w", encoding="utf-8") as f:
        f.write(yaml_content)
        temp_path = Path(f.name)

    try:
        with pytest.raises(ValueError) as exc_info:
            AppConfig.load_from_yaml(temp_path)
        err_msg = str(exc_info.value)
        assert "Invalid configuration in" in err_msg
        assert "Duplicate register names are not allowed in the config" in err_msg
    finally:
        temp_path.unlink()

def test_engine_unsupported_registers_filtering():
    device = DeviceConfig(
        name="TestSimDevice",
        host="127.0.0.1",
        port=5020,
        schema_name="v10"
    )
    unsupported = {"BatBocConfig", "SystemOn"}
    engine = ModbusControlEngine(device, unsupported_registers=unsupported)
    
    reg_names = [r.name for r in engine.schema.registers]
    assert "BatBocConfig" not in reg_names
    assert "SystemOn" not in reg_names
    assert "BatIdConfig" in reg_names
    
    assert "BatBocConfig" not in engine.registers_by_name
    assert "SystemOn" not in engine.registers_by_name
    assert "BatIdConfig" in engine.registers_by_name


import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

@pytest.mark.anyio
@patch('modbus_ctrl_core.engine.ModbusClientWrapper')
async def test_engine_read_all_success(mock_client_cls):
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.connected = True
    
    # Mock the pymodbus client response
    mock_pymodbus = MagicMock()
    
    # Setup a fake response for read_holding_registers
    mock_response = MagicMock()
    mock_response.isError.return_value = False
    mock_response.registers = [100, 200, 300]
    
    mock_pymodbus.read_holding_registers = AsyncMock(return_value=mock_response)
    mock_pymodbus.read_discrete_inputs = AsyncMock(return_value=mock_response)
    mock_pymodbus.read_coils = AsyncMock(return_value=mock_response)
    mock_pymodbus.read_input_registers = AsyncMock(return_value=mock_response)
    
    mock_client.client = mock_pymodbus
    mock_client_cls.return_value = mock_client

    device = DeviceConfig(
        name="TestSimDevice",
        host="127.0.0.1",
        port=5020,
        schema_name="v10",
        registers=["BatBocConfig"]
    )
    
    engine = ModbusControlEngine(device)
    
    results = await engine.read_all(gap_threshold=5)
    assert "BatBocConfig" in results
    mock_client.connect.assert_called()
    mock_pymodbus.read_holding_registers.assert_called()

@pytest.mark.anyio
@patch('modbus_ctrl_core.engine.ModbusClientWrapper')
async def test_engine_read_all_fallback(mock_client_cls):
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.connected = True
    
    mock_pymodbus = MagicMock()
    
    # First response fails
    mock_fail = MagicMock()
    mock_fail.isError.return_value = True
    
    # Second response succeeds (fallback)
    mock_succ = MagicMock()
    mock_succ.isError.return_value = False
    mock_succ.registers = [50, 60]
    
    mock_pymodbus.read_holding_registers = AsyncMock(side_effect=[mock_fail, mock_succ])
    
    mock_client.client = mock_pymodbus
    mock_client_cls.return_value = mock_client

    device = DeviceConfig(name="TestSimDevice", host="127.0.0.1", schema_name="v10", registers=["BatBocConfig"])
    engine = ModbusControlEngine(device)
    
    results = await engine.read_all(gap_threshold=5)
    assert "BatBocConfig" in results

@pytest.mark.anyio
@patch('modbus_ctrl_core.engine.ModbusClientWrapper')
async def test_engine_write_registers(mock_client_cls):
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.connected = True
    mock_pymodbus = MagicMock()
    
    mock_write_res = MagicMock()
    mock_write_res.isError.return_value = False
    mock_pymodbus.write_register = AsyncMock(return_value=mock_write_res)
    mock_pymodbus.write_registers = AsyncMock(return_value=mock_write_res)
    
    mock_client.client = mock_pymodbus
    mock_client_cls.return_value = mock_client

    device = DeviceConfig(name="TestSimDevice", host="127.0.0.1", schema_name="v10", registers=["BatBocConfig"])
    engine = ModbusControlEngine(device)
    
    results = await engine.write_registers({"BatBocConfig": 150})
    assert results["BatBocConfig"] == "Success"
    mock_pymodbus.write_registers.assert_called()

@pytest.mark.anyio
@patch('modbus_ctrl_core.engine.ModbusClientWrapper')
async def test_engine_write_invalid(mock_client_cls):
    mock_client = MagicMock()
    mock_client.connect = AsyncMock()
    mock_client.connected = True
    mock_client_cls.return_value = mock_client

    device = DeviceConfig(name="TestSimDevice", host="127.0.0.1", schema_name="v10", registers=["BatBocConfig"])
    engine = ModbusControlEngine(device)
    
    results = await engine.write_registers({"NonexistentReg": 150})
    assert "Error: Register not found in schema" in results["NonexistentReg"]
