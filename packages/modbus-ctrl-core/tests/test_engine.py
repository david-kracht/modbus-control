from modbus_ctrl_contracts import DeviceConfig
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
    from modbus_ctrl_contracts import AppConfig

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

