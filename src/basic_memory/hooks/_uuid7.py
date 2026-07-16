"""RFC 9562 UUIDv7 generation for envelope ids.

Constraint: stdlib ``uuid.uuid7()`` exists only on Python 3.14+, and Basic
Memory's floor is 3.12, so this tiny generator fills the gap. Swap it for
``uuid.uuid7()`` when the floor rises.

The 48-bit millisecond timestamp prefix means UUIDv7 strings sort
lexicographically into chronological order — the projector processes
``sorted(glob)`` with no mtime/stat dependence.
"""

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Build a UUIDv7: 48-bit unix-ms timestamp, version/variant bits, 74 random bits."""
    unix_ts_ms = time.time_ns() // 1_000_000
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big") & 0x3FFF_FFFF_FFFF_FFFF
    value = (unix_ts_ms & 0xFFFF_FFFF_FFFF) << 80
    value |= 0x7 << 76  # version 7
    value |= rand_a << 64
    value |= 0b10 << 62  # RFC 4122/9562 variant
    value |= rand_b
    return uuid.UUID(int=value)


def uuid7_unix_ms(value: uuid.UUID) -> int:
    """Extract the millisecond capture timestamp from a UUIDv7.

    Inbox retention derives envelope age from the id itself, so pruning never
    depends on filesystem mtimes (which rename/copy can disturb).

    Only version 7 carries a timestamp in those bits: shifting a v1/v4 UUID
    would yield a garbage "capture time" that retention could act on — so any
    other version fails fast with ValueError (callers treat that as "not a
    UUIDv7 name", never as an age).
    """
    if value.version != 7:
        raise ValueError(f"not a UUIDv7: {value}")
    return value.int >> 80
