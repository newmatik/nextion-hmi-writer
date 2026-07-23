# HMI checksums and directory integrity

This chapter documents the checks that Nextion Editor 1.68.1.3034 applies while loading an `.HMI`
project. All formulas below were verified against Editor-authored files and by opening a generated
project in the Editor.

## Status

The checksums are fully recovered. They are not ordinary named CRC profiles, although they use the
CRC-32/MPEG-2 polynomial. Three distinct wrappers exist:

- page-section checksum (`<n>.pa`);
- project-manifest checksum (`main.HMI`);
- top-level directory checksum, stored twice.

Resource blobs (`.zi`, `.i`, `.is`) start with format headers and do not carry the section checksum
described here.

## Shared CRC table

All three wrappers use the forward polynomial `0x04C11DB7` and initial state `0xFFFFFFFF`.

```python
POLY = 0x04C11DB7


def table_entry(value: int) -> int:
    for _ in range(8):
        value = ((value << 1) & 0xFFFFFFFF) ^ (POLY if value & 0x80000000 else 0)
    return value


TABLE = tuple(table_entry(index << 24) for index in range(256))
```

The Editor's byte kernel is unusual: one input byte causes four table rounds, equivalent to 32
LFSR steps rather than the usual eight.

```python
def update(state: int, data: bytes) -> int:
    for value in data:
        state ^= value
        for _ in range(4):
            state = ((state << 8) & 0xFFFFFFFF) ^ TABLE[state >> 24]
    return state
```

## Page checksum

The little-endian result is stored at page offset 0. The checksum field itself is excluded.

```python
import struct


def page_checksum(page: bytes) -> int:
    object_count = struct.unpack_from("<I", page, 12)[0]
    state = update(0xFFFFFFFF, page[4:])
    state = update(state, struct.pack("<I", len(page)))
    state = update(state, struct.pack("<I", object_count))
    return update(state, b"\x00\x4f")
```

This wrapper was reproduced across 951 local page/main samples of different lengths and object
counts. A stale page checksum leads to a page-loading error.

## `main.HMI` checksum

The little-endian result is stored at manifest offset 0.

```python
def main_checksum(main: bytes) -> int:
    state = update(0xFFFFFFFF, main[4:])
    state = update(state, main[16:20])
    state = update(state, main[4:8])
    state = update(state, main[10:11])
    return update(state, main[14:15])
```

## Top-level directory checksum

The primary directory begins at offset `0`. A byte-identical backup begins at `0x80000`. Each copy
has this layout:

```text
u32 count
count × 28-byte records
u32 checksum
```

Unlike page and manifest input, the directory kernel consumes little-endian 32-bit words. It XORs
one complete word into the state and then performs four table rounds.

```python
def update_words(state: int, data: bytes) -> int:
    if len(data) % 4:
        raise ValueError("word input must be aligned")
    for (value,) in struct.iter_unpack("<I", data):
        state ^= value
        for _ in range(4):
            state = ((state << 8) & 0xFFFFFFFF) ^ TABLE[state >> 24]
    return state


def directory_checksum(directory: bytes) -> int:
    return update_words(0xFFFFFFFF, directory + b"ADEC")
```

Here `directory` is exactly `u32 count + count × records`, without the stored checksum. Write the
little-endian result immediately after both directory copies. The fixed `ADEC` sentinel is part of
the checksum input.

The formula reproduced 592 Editor-authored directory vectors. Changing a record without updating
both directory copies and both checksums causes:

```text
Wrong resource file or resource file has been damaged
```

## Append-only update rule

Do not relocate a live section by merely changing its existing record. The Editor journals changes:

1. preserve the old record's start and size;
2. mark it deleted and clear the first byte of its name;
3. append the new payload at end-of-file;
4. append a new live directory record for that payload;
5. update the count, mirror the full directory, and recompute both directory checksums.

This keeps every historical payload accounted for. It also preserves unknown per-record bytes. A
safe writer starts from an Editor-authored skeleton with all required resource slots, clones component
records, and changes only fields it understands.

## Verification chain

A generated project should pass all of these gates:

1. parser/serializer round-trip is byte-exact on untouched Editor files;
2. both directory copies and checksums agree;
3. every page and `main.HMI` checksum recomputes exactly;
4. the Editor opens the generated `.HMI`;
5. the Editor compiles it with zero errors;
6. `File -> TFT file output` produces a `.TFT`.

The last two gates are compatibility checks, not hardware validation. Touch behaviour, serial I/O,
reset, sleep/wake, orientation, brightness and flash update still require a real panel.
