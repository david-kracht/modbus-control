import pytest
from unittest.mock import MagicMock, patch
import os

from modbus_common import AppConfig, DeviceConfig
from modbus_ctrl_cli.dashboard import resolve_device

def test_resolve_device_adhoc():
    device = resolve_device(host="10.0.0.1", port=1502, unit_id=5, schema_name="v30")
    assert device.name == "AdHoc_10.0.0.1"
    assert device.host == "10.0.0.1"
    assert device.port == 1502
    assert device.unit_id == 5
    assert device.schema_name == "v30"

@patch('modbus_common.AppConfig.load_from_yaml')
@patch('modbus_ctrl_core.config.MODBUS_DEVICES_YAML')
def test_resolve_device_target_name(mock_yaml_path, mock_load):
    mock_yaml_path.exists.return_value = True
    app_config = AppConfig(devices=[
        DeviceConfig(name="Device1", host="1.1.1.1"),
        DeviceConfig(name="Device2", host="2.2.2.2")
    ])
    mock_load.return_value = app_config
    
    device = resolve_device(target="Device2")
    assert device.host == "2.2.2.2"
    assert device.name == "Device2"

@patch('modbus_common.AppConfig.load_from_yaml')
@patch('modbus_ctrl_core.config.MODBUS_DEVICES_YAML')
def test_resolve_device_target_index(mock_yaml_path, mock_load):
    mock_yaml_path.exists.return_value = True
    app_config = AppConfig(devices=[
        DeviceConfig(name="Device1", host="1.1.1.1"),
        DeviceConfig(name="Device2", host="2.2.2.2")
    ])
    mock_load.return_value = app_config
    
    device = resolve_device(target="1")
    assert device.host == "2.2.2.2"
    assert device.name == "Device2"

@patch('modbus_ctrl_core.config.MODBUS_DEVICES_YAML')
def test_resolve_device_target_empty_config(mock_yaml_path):
    mock_yaml_path.exists.return_value = False
    with pytest.raises(ValueError) as exc:
        resolve_device(target="Device1")
    assert "devices.yaml is empty or could not be loaded" in str(exc.value)

@patch('modbus_common.AppConfig.load_from_yaml')
@patch('modbus_ctrl_core.config.MODBUS_DEVICES_YAML')
def test_resolve_device_target_not_found(mock_yaml_path, mock_load):
    mock_yaml_path.exists.return_value = True
    app_config = AppConfig(devices=[DeviceConfig(name="Device1", host="1.1.1.1")])
    mock_load.return_value = app_config
    
    with pytest.raises(ValueError) as exc:
        resolve_device(target="Device2")
    assert "not found by index or name" in str(exc.value)

def test_resolve_device_fallback_env(monkeypatch):
    monkeypatch.setattr('modbus_ctrl_core.config.DEFAULT_MODBUS_HOST', "8.8.8.8")
    with patch('modbus_ctrl_core.config.MODBUS_DEVICES_YAML') as mock_yaml_path:
        mock_yaml_path.exists.return_value = False
        device = resolve_device()
        assert device.host == "8.8.8.8"
        assert device.name == "AdHoc_8.8.8.8"

def test_resolve_device_no_config_no_fallback(monkeypatch):
    monkeypatch.setattr('modbus_ctrl_core.config.DEFAULT_MODBUS_HOST', None)
    with patch('modbus_ctrl_core.config.MODBUS_DEVICES_YAML') as mock_yaml_path:
        mock_yaml_path.exists.return_value = False
        with pytest.raises(ValueError) as exc:
            resolve_device()
        assert "No devices configured" in str(exc.value)
