from modbus_schema_common.models import ModbusRegister, ModbusRegisterType, ModbusDataType
from modbus_ctrl_core.aggregator import build_read_blocks

def test_aggregator_group_by_type():
    # Setup different register types
    r1 = ModbusRegister(
        address_hex="0x0001",
        address_dec=1,
        name="Reg1",
        description="Holding register 1",
        data_type=ModbusDataType.UINT16,
        register_count=1,
        access="RO",
        register_type=ModbusRegisterType.HOLDING_REGISTER,
    )
    r2 = ModbusRegister(
        address_hex="0x0002",
        address_dec=2,
        name="Reg2",
        description="Input register 2",
        data_type=ModbusDataType.UINT16,
        register_count=1,
        access="RO",
        register_type=ModbusRegisterType.INPUT_REGISTER,
    )

    blocks = build_read_blocks([r1, r2])
    # Should create two blocks, one for holding and one for input
    assert len(blocks) == 2
    types = [b["register_type"] for b in blocks]
    assert ModbusRegisterType.HOLDING_REGISTER in types
    assert ModbusRegisterType.INPUT_REGISTER in types

def test_aggregator_gap_threshold():
    # Gap of 3 registers between r1 and r2
    r1 = ModbusRegister(
        address_hex="0x0001",
        address_dec=1,
        name="Reg1",
        description="Holding register 1",
        data_type=ModbusDataType.UINT16,
        register_count=1,
        access="RW",
        register_type=ModbusRegisterType.HOLDING_REGISTER,
    )
    r2 = ModbusRegister(
        address_hex="0x0005",
        address_dec=5,
        name="Reg2",
        description="Holding register 2",
        data_type=ModbusDataType.UINT16,
        register_count=1,
        access="RW",
        register_type=ModbusRegisterType.HOLDING_REGISTER,
    )

    # Threshold is 5, so gap of 3 (address 2, 3, 4 are empty) is <= 5. They should merge.
    blocks_merged = build_read_blocks([r1, r2], gap_threshold=5)
    assert len(blocks_merged) == 1
    assert blocks_merged[0]["start_addr"] == 1
    # Count should cover from 1 to 5 + 1 register = 5
    assert blocks_merged[0]["count"] == 5
    assert len(blocks_merged[0]["registers"]) == 2

    # Threshold is 2, so gap of 3 is > 2. They should NOT merge.
    blocks_split = build_read_blocks([r1, r2], gap_threshold=2)
    assert len(blocks_split) == 2
    assert blocks_split[0]["start_addr"] == 1
    assert blocks_split[0]["count"] == 1
    assert blocks_split[1]["start_addr"] == 5
    assert blocks_split[1]["count"] == 1

def test_aggregator_max_limits():
    # Holding registers limit is 125.
    r1 = ModbusRegister(
        address_hex="0x0001",
        address_dec=1,
        name="Reg1",
        description="Holding register 1",
        data_type=ModbusDataType.UINT16,
        register_count=1,
        access="RW",
        register_type=ModbusRegisterType.HOLDING_REGISTER,
    )
    r2 = ModbusRegister(
        address_hex="0x007E", # 126
        address_dec=126,
        name="Reg2",
        description="Holding register 2",
        data_type=ModbusDataType.UINT16,
        register_count=1,
        access="RW",
        register_type=ModbusRegisterType.HOLDING_REGISTER,
    )

    # The new count would be 126 - 1 + 1 = 126. Since 126 > 125, they should split, even though gap is 124 (which is > threshold anyway, but let's test limit)
    # Let's force a gap threshold of 150
    blocks = build_read_blocks([r1, r2], gap_threshold=150)
    assert len(blocks) == 2
