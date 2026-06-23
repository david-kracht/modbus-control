import pytest
import math
from unittest.mock import patch
from modbus_schema_common.models import ModbusDataType
from modbus_ctrl_cli.dashboard import parse_ordinal_value, is_holding_register_sentinel

def test_parse_ordinal_value():
    assert parse_ordinal_value("123") == 123
    assert parse_ordinal_value("-123") == -123
    assert parse_ordinal_value("123.45") == 123.45
    assert parse_ordinal_value("-123.45") == -123.45
    
    with pytest.raises(ValueError):
        parse_ordinal_value("abc")
    with pytest.raises(ValueError):
        parse_ordinal_value("")

def test_is_holding_register_sentinel():
    assert is_holding_register_sentinel(None, ModbusDataType.INT16) is True
    assert is_holding_register_sentinel(-1, ModbusDataType.INT16) is True
    assert is_holding_register_sentinel(0, ModbusDataType.INT16) is False
    
    assert is_holding_register_sentinel(float('nan'), ModbusDataType.FLOAT32) is True
    assert is_holding_register_sentinel(1.23, ModbusDataType.FLOAT32) is False
    assert is_holding_register_sentinel(-1, ModbusDataType.FLOAT32) is False
    
    # Test string enum type fallback (in case it's not a python enum yet but a string)
    assert is_holding_register_sentinel(float('nan'), "float32") is True

@patch('select.select')
@patch('os.read')
@patch('sys.stdin.fileno', return_value=0)
def test_get_key_empty(mock_fileno, mock_read, mock_select):
    mock_select.return_value = ([], [], [])
    from modbus_ctrl_cli.dashboard import get_key, _key_queue
    _key_queue.clear()
    assert get_key() == ""

@patch('select.select')
@patch('os.read')
@patch('sys.stdin.fileno', return_value=0)
def test_get_key_data(mock_fileno, mock_read, mock_select):
    # simulate "hello" then arrow up "\x1b[1;2A"
    mock_select.side_effect = [([0], [], []), ([], [], [])]
    mock_read.return_value = b'a\x1b[A\x1b[1;2A'
    
    from modbus_ctrl_cli.dashboard import get_key, _key_queue
    _key_queue.clear()
    
    assert get_key() == "a"
    assert get_key() == "\x1b[A"
    assert get_key() == "\x1b[1;2A"
    assert get_key() == ""
