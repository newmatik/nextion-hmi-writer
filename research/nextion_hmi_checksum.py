"""Checksums for Nextion HMI sections from Editor 1.68.1.3034.

The editor uses the forward-running CRC-32 polynomial ``0x04C11DB7``. The input is unusual: every
single byte is XOR-ed into the low byte of the 32-bit state, followed by four table rounds. One
input byte therefore amounts to 32 instead of the usual eight LFSR clocks.

The section-specific trailers were determined by a runtime trace of the editor helpers and verified
against 951 real page sections of varying length and object count.
"""

from __future__ import annotations

import struct

POLYNOMIAL = 0x04C11DB7
INITIAL_STATE = 0xFFFFFFFF


def _table_entry(value: int) -> int:
    for _ in range(8):
        value = ((value << 1) & 0xFFFFFFFF) ^ (
            POLYNOMIAL if value & 0x80000000 else 0
        )
    return value


CRC_TABLE = tuple(_table_entry(index << 24) for index in range(256))


def update(state: int, data: bytes | bytearray | memoryview) -> int:
    """Performs the byte-wise CRC update used by the editor."""

    state &= 0xFFFFFFFF
    for value in data:
        state ^= value
        for _ in range(4):
            state = ((state << 8) & 0xFFFFFFFF) ^ CRC_TABLE[state >> 24]
    return state


def update_words(state: int, data: bytes | bytearray | memoryview) -> int:
    """CRC kernel of the container directory with 32-bit LE input words."""

    raw = bytes(data)
    if len(raw) % 4:
        raise ValueError("word CRC requires a multiple of four bytes")
    state &= 0xFFFFFFFF
    for (value,) in struct.iter_unpack("<I", raw):
        state ^= value
        for _ in range(4):
            state = ((state << 8) & 0xFFFFFFFF) ^ CRC_TABLE[state >> 24]
    return state


def directory_checksum(directory: bytes | bytearray | memoryview) -> int:
    """Checksum over count and records of the mirrored HMI directory."""

    return update_words(INITIAL_STATE, bytes(directory) + b"ADEC")


def page_checksum(section: bytes | bytearray | memoryview) -> int:
    """Computes the checksum of a complete ``<n>.pa`` section."""

    data = bytes(section)
    if len(data) < 16:
        raise ValueError("page section is too short")
    object_count = struct.unpack_from("<I", data, 12)[0]
    state = update(INITIAL_STATE, data[4:])
    state = update(state, struct.pack("<I", len(data)))
    state = update(state, struct.pack("<I", object_count))
    return update(state, b"\x00\x4F")


def main_checksum(section: bytes | bytearray | memoryview) -> int:
    """Computes the checksum of ``main.HMI``."""

    data = bytes(section)
    if len(data) < 20:
        raise ValueError("main.HMI section is too short")
    state = update(INITIAL_STATE, data[4:])
    state = update(state, data[16:20])
    state = update(state, data[4:8])
    state = update(state, data[10:11])
    return update(state, data[14:15])


def with_page_checksum(section: bytes | bytearray | memoryview) -> bytes:
    """Returns a page section with an updated result field."""

    result = bytearray(section)
    struct.pack_into("<I", result, 0, page_checksum(result))
    return bytes(result)


def with_main_checksum(section: bytes | bytearray | memoryview) -> bytes:
    """Returns ``main.HMI`` with an updated result field."""

    result = bytearray(section)
    struct.pack_into("<I", result, 0, main_checksum(result))
    return bytes(result)
