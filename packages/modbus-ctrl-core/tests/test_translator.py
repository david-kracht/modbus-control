import pytest
from modbus_schema_common.models import ModbusDataType
from modbus_ctrl_core.translator import pack_register_value, unpack_register_value

def test_uint16_translation():
    # Big-endian test (default)
    # 1234 -\u003e [1234]
    regs = pack_register_value(1234, ModbusDataType.UINT16)
    assert regs == [1234]
    
    val = unpack_register_value(regs, ModbusDataType.UINT16)
    assert val == 1234

    # Little-endian byte order
    regs_le = pack_register_value(1234, ModbusDataType.UINT16, byte_order="little")
    # 1234 in hex is 0x04D2. Packed as little-endian bytes: D2 04, which as uint16 is 0xD204 = 53764
    assert regs_le == [0xD204]
    val_le = unpack_register_value(regs_le, ModbusDataType.UINT16, byte_order="little")
    assert val_le == 1234

def test_int16_translation():
    # Negative value
    regs = pack_register_value(-500, ModbusDataType.INT16)
    # -500 in 16-bit signed is 0xFE0C (65036 unsigned)
    assert regs == [65036]
    val = unpack_register_value(regs, ModbusDataType.INT16)
    assert val == -500

    # Little endian byte order
    regs_le = pack_register_value(-500, ModbusDataType.INT16, byte_order="little")
    # 0xFE0C swapped is 0x0CFE = 3326
    assert regs_le == [3326]
    val_le = unpack_register_value(regs_le, ModbusDataType.INT16, byte_order="little")
    assert val_le == -500

def test_uint32_translation():
    # Big-endian, Big-word (default)
    # 70000 -\u003e 0x0001 0x1170
    regs = pack_register_value(70000, ModbusDataType.UINT32)
    assert regs == [1, 4464] # 0x0001, 0x1170
    val = unpack_register_value(regs, ModbusDataType.UINT32)
    assert val == 70000

    # Big-endian, Little-word
    regs_lw = pack_register_value(70000, ModbusDataType.UINT32, word_order="little")
    assert regs_lw == [4464, 1]
    val_lw = unpack_register_value(regs_lw, ModbusDataType.UINT32, word_order="little")
    assert val_lw == 70000

    # Little-endian, Big-word
    # Bytes of 70000 (0x00011170) -\u003e [70, 11, 01, 00]
    # Little endian 16-bit words: w0 (0x0001 -\u003e 0x0100 = 256), w1 (0x1170 -\u003e 0x7011 = 28689)
    regs_le = pack_register_value(70000, ModbusDataType.UINT32, byte_order="little", word_order="big")
    assert regs_le == [256, 28689]
    val_le = unpack_register_value(regs_le, ModbusDataType.UINT32, byte_order="little", word_order="big")
    assert val_le == 70000

def test_float32_translation():
    # 12.5 floating point — round-trip directly, no scaling
    regs = pack_register_value(12.5, ModbusDataType.FLOAT32)
    val = unpack_register_value(regs, ModbusDataType.FLOAT32)
    assert val == pytest.approx(12.5)

    # Negative float
    regs_neg = pack_register_value(-3.14, ModbusDataType.FLOAT32)
    val_neg = unpack_register_value(regs_neg, ModbusDataType.FLOAT32)
    assert val_neg == pytest.approx(-3.14, rel=1e-5)

def test_bit_translation():
    regs_true = pack_register_value(True, ModbusDataType.BIT)
    assert regs_true == [1]
    assert unpack_register_value(regs_true, ModbusDataType.BIT) is True

    regs_false = pack_register_value(False, ModbusDataType.BIT)
    assert regs_false == [0]
    assert unpack_register_value(regs_false, ModbusDataType.BIT) is False

def test_string_translation():
    regs = pack_register_value("Hello", ModbusDataType.STRING, string_length_words=4)
    # "Hello" is 5 chars, padded with 3 null bytes to make 8 bytes (4 words).
    # "He" -\u003e 0x4865, "ll" -\u003e 0x6C6C, "o\0" -\u003e 0x6F00, "\0\0" -\u003e 0x0000
    assert regs == [0x4865, 0x6C6C, 0x6F00, 0x0000]

    val = unpack_register_value(regs, ModbusDataType.STRING)
    assert val == "Hello"

    # Testing little-endian byte order string
    regs_le = pack_register_value("Hello", ModbusDataType.STRING, byte_order="little", string_length_words=4)
    # "He" -\u003e 0x6548, "ll" -\u003e 0x6C6C, "o\0" -\u003e 0x006F, "\0\0" -\u003e 0x0000
    assert regs_le == [0x6548, 0x6C6C, 0x006F, 0x0000]
    val_le = unpack_register_value(regs_le, ModbusDataType.STRING, byte_order="little")
    assert val_le == "Hello"
