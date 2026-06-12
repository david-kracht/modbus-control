import struct
from typing import Any
from modbus_schema_common.models import ModbusDataType

def _get_struct_formats(data_type: ModbusDataType) -> tuple[str, int]:
    """Return (format_char, num_registers)."""
    if data_type == ModbusDataType.UINT16:
        return ("H", 1)
    elif data_type == ModbusDataType.INT16:
        return ("h", 1)
    elif data_type == ModbusDataType.UINT32:
        return ("I", 2)
    elif data_type == ModbusDataType.INT32:
        return ("i", 2)
    elif data_type == ModbusDataType.FLOAT32:
        return ("f", 2)
    elif data_type == ModbusDataType.BIT:
        return ("H", 1)
    elif data_type == ModbusDataType.STRING:
        return ("s", -1)  # dynamic length
    raise ValueError(f"Unsupported data type: {data_type}")

def unpack_register_value(
    registers: list[int],
    data_type: ModbusDataType,
    byte_order: str = "big",
    word_order: str = "big",
) -> Any:
    """
    Unpack raw Modbus registers into Python types.
    The float32 data type is returned as a Python float as-is; integer types
    are returned as int. No scaling is applied — the data type determines
    the representation.
    """
    fmt_char, num_regs = _get_struct_formats(data_type)
    if num_regs > 0 and len(registers) < num_regs:
        raise ValueError(f"Expected at least {num_regs} registers for {data_type.value}, got {len(registers)}")

    bo = ">" if byte_order.lower() == "big" else "<"

    if data_type == ModbusDataType.STRING:
        # String data type
        raw_bytes = bytearray()
        for r in registers:
            # Pack word as 16-bit int in specified byte order
            word_bytes = struct.pack(f"{bo}H", r)
            raw_bytes.extend(word_bytes)
        try:
            val = raw_bytes.decode("utf-8", errors="ignore")
        except Exception:
            val = raw_bytes.decode("ascii", errors="ignore")
        return val.split("\x00")[0].strip()

    elif num_regs == 1:
        # 16-bit types (uint16, int16, bit)
        # Pack the register as a standard big-endian 16-bit word, then unpack using the device's byte order
        val_bytes = struct.pack(">H", registers[0])
        val = struct.unpack(f"{bo}{fmt_char}", val_bytes)[0]
        if data_type == ModbusDataType.BIT:
            return bool(val)
        return val

    elif num_regs == 2:
        # 32-bit types (uint32, int32, float32)
        # Pack both registers as standard big-endian 16-bit words
        w0_bytes = struct.pack(">H", registers[0])
        w1_bytes = struct.pack(">H", registers[1])

        # Identify high word and low word based on Word Order
        if word_order.lower() == "big":
            high_word_bytes = w0_bytes
            low_word_bytes = w1_bytes
        else:
            high_word_bytes = w1_bytes
            low_word_bytes = w0_bytes

        # Combine based on Byte Order expected by struct.unpack
        if byte_order.lower() == "big":
            combined_bytes = high_word_bytes + low_word_bytes
        else:
            combined_bytes = low_word_bytes + high_word_bytes

        return struct.unpack(f"{bo}{fmt_char}", combined_bytes)[0]

    return None

def pack_register_value(
    value: Any,
    data_type: ModbusDataType,
    byte_order: str = "big",
    word_order: str = "big",
    string_length_words: int = 16,
) -> list[int]:
    """
    Pack Python types into raw Modbus registers (16-bit unsigned ints).
    No scaling is applied — values are packed directly into the wire format
    determined by the data type.
    """
    fmt_char, num_regs = _get_struct_formats(data_type)
    bo = ">" if byte_order.lower() == "big" else "<"

    if data_type == ModbusDataType.STRING:
        text = str(value)
        # Pad with nulls to fill the requested number of words (2 bytes per word)
        byte_len = string_length_words * 2
        encoded = text.encode("utf-8", errors="ignore")[:byte_len]
        padded = encoded.ljust(byte_len, b"\x00")

        registers = []
        for i in range(0, byte_len, 2):
            word_bytes = padded[i:i+2]
            word = struct.unpack(f"{bo}H", word_bytes)[0]
            registers.append(word)
        return registers

    elif num_regs == 1:
        if data_type == ModbusDataType.BIT:
            raw_val = 1 if value else 0
        else:
            raw_val = int(round(value))

        # Pack value using the device's byte order, then unpack as standard big-endian 16-bit int
        val_bytes = struct.pack(f"{bo}{fmt_char}", raw_val)
        word = struct.unpack(">H", val_bytes)[0]
        return [word]

    elif num_regs == 2:
        if data_type in (ModbusDataType.UINT32, ModbusDataType.INT32):
            raw_val = int(round(value))
        elif data_type == ModbusDataType.FLOAT32:
            raw_val = float(value)
        else:
            raw_val = value

        # Pack the 32-bit value using the device's byte order
        combined_bytes = struct.pack(f"{bo}{fmt_char}", raw_val)

        partA = combined_bytes[0:2]
        partB = combined_bytes[2:4]

        # Determine which part is high word and which is low word
        if byte_order.lower() == "big":
            high_word_bytes = partA
            low_word_bytes = partB
        else:
            low_word_bytes = partA
            high_word_bytes = partB

        # Deconstruct based on Word Order
        if word_order.lower() == "big":
            reg0 = struct.unpack(">H", high_word_bytes)[0]
            reg1 = struct.unpack(">H", low_word_bytes)[0]
        else:
            reg0 = struct.unpack(">H", low_word_bytes)[0]
            reg1 = struct.unpack(">H", high_word_bytes)[0]

        return [reg0, reg1]

    return []
