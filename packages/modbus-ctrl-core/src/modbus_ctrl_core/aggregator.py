import logging
from typing import TypedDict, Any
from modbus_schema_common.models import ModbusRegisterBase, ModbusRegisterType

logger = logging.getLogger(__name__)

class ReadBlock(TypedDict):
    """
    Represents a contiguous block of Modbus registers that can be read in a single network request.
    This optimizes communication by minimizing protocol overhead.
    """
    register_type: ModbusRegisterType
    start_addr: int
    count: int
    registers: list[Any]  # list of ModbusRegisterBase or derived

def build_read_blocks(
    registers: list[Any],
    gap_threshold: int = 5,
) -> list[ReadBlock]:
    """
    Groups a list of registers into optimized read blocks.
    Groups are separated by register_type, and registers within a type are merged
    if the gap between them is <= gap_threshold and the total size is within limits.
    """
    # Group by register type
    by_type: dict[ModbusRegisterType, list[Any]] = {}
    for r in registers:
        by_type.setdefault(r.register_type, []).append(r)

    blocks: list[ReadBlock] = []

    for rtype, rlist in by_type.items():
        if not rlist:
            continue

        # Sort registers by address
        sorted_regs = sorted(rlist, key=lambda r: r.address_dec)

        # Limits per Modbus spec
        if rtype in (ModbusRegisterType.HOLDING_REGISTER, ModbusRegisterType.INPUT_REGISTER):
            max_limit = 125
        else:
            max_limit = 2000

        current_block: ReadBlock | None = None

        for reg in sorted_regs:
            reg_start = reg.address_dec
            # For coils/discrete inputs, count is 1. For registers, it's register_count.
            reg_count = getattr(reg, "register_count", 1)

            if current_block is None:
                current_block = ReadBlock(
                    register_type=rtype,
                    start_addr=reg_start,
                    count=reg_count,
                    registers=[reg],
                )
            else:
                block_end = current_block["start_addr"] + current_block["count"]
                gap = reg_start - block_end
                new_count = reg_start + reg_count - current_block["start_addr"]

                if gap >= 0 and gap <= gap_threshold and new_count <= max_limit:
                    # Merge into current block
                    current_block["count"] = new_count
                    current_block["registers"].append(reg)
                else:
                    # Save current block and start a new one
                    blocks.append(current_block)
                    current_block = ReadBlock(
                        register_type=rtype,
                        start_addr=reg_start,
                        count=reg_count,
                        registers=[reg],
                    )

        if current_block:
            blocks.append(current_block)

    return blocks
