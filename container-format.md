# The `.HMI` container format

The `.HMI` file is a **custom binary container**. It is _not_ SQLite (there are no SQL markers
anywhere in the Editor binaries) and _not_ a ZIP. It is a flat directory of named **sections**
followed by a large payload region. This document describes the container envelope, the `main.HMI`
project header, and the `Program.s` global-code section. Pages, fonts and images have their own
documents.

Everything below is confirmed against Nextion Editor 1.68.1.3034 output and, where hex is shown, was
extracted directly from the reference files listed in [`test-vectors.md`](test-vectors.md).

## 1. The section directory

The file begins with a count and a table of fixed-size directory records:

```text
offset 0x00   u32   section_count
offset 0x04   section_count × 28-byte directory record:
after records u32  directory_checksum
```

Each 28-byte directory record:

| Offset | Size | Field            | Meaning                                                    |
| ------ | ---- | ---------------- | ---------------------------------------------------------- |
| `0x00` | 16   | `name[16]`       | Section name, null-padded ASCII (e.g. `0.pa`, `main.HMI`). |
| `0x10` | 4    | `start` (u32 LE) | **Absolute** file offset of the section payload.           |
| `0x14` | 4    | `size` (u32 LE)  | Section payload length in bytes.                           |
| `0x18` | 1    | `deleted` (u8)   | Tombstone flag; `0` = live.                                |
| `0x19` | 3    | `opaque[3]`      | Editor-managed; preserve verbatim.                         |

The complete directory and its checksum are mirrored byte-for-byte at `0x80000`. The checksum is the
word-wise CRC over `count + records + "ADEC"`; see [`section-checksum.md`](section-checksum.md).
Fixed markers also exist at `0x380000` (`0xFFFFFFFF`) and `0x6FFFF8` (`ver21234`). Payloads start at
`0x700000`.

### Payload starts at 7 MiB — always

The section payloads live from **`0x700000` (7 MiB)** onward. The container preallocates this large
slack region, so an _empty_ project is roughly 8 MB on disk while carrying well under 1 MB of real
content. **Do not infer anything from file size.**

### Tombstones and append-only saves

Two behaviours make the file grow monotonically:

- **Deletion is a tombstone.** A deleted section is not removed. Instead the **first byte of its
  name is zeroed** (a FAT-style trick), which the directory reader treats as "not live". Its payload
  bytes remain in the file.
- **Saves append.** When the Editor changes a section it writes a **new copy at the end** and points
  the directory at it, rather than rewriting in place. The file therefore accumulates its own edit
  history and **never shrinks**. Two projects with byte-identical live content can differ in total
  size purely by the path of edits that produced them.

A correct reader must (a) honour `deleted`, (b) treat a name whose first byte is `0x00` as dead, and
(c) resolve each live name to its directory record. Because a save tombstones the record it
supersedes, a well-formed file holds exactly one live record per name; a repeated _live_ name is
corruption to be surfaced, not silently resolved. A correct
_writer_ that wants byte-exact round-trips must preserve every non-section byte verbatim (the 7 MiB
lead-in and all tombstoned remnants), mirror the directory, and update both directory checksums; see
[`methodology.md`](methodology.md).

### Section naming scheme

Sections are numbered, not freely named. Resources are addressed by their **index**, so the number in
the name _is_ the resource id used by components (`font=0` → `0.zi`, `pic=3` → `3.i`/`3.is`).

| Name        | Content                                                                                                          |
| ----------- | ---------------------------------------------------------------------------------------------------------------- |
| `main.HMI`  | Project header: device/display/encoding config and the resource directory.                                       |
| `Program.s` | Global startup code (the `Program.s` tab), stored as plain text.                                                 |
| `<n>.pa`    | Page _n_ — header, object table, components. See [`page-and-component-format.md`](page-and-component-format.md). |
| `<n>.zi`    | Font _n_, embedded verbatim. See [`font-zi-format.md`](font-zi-format.md).                                       |
| `<n>.i`     | Image _n_ — the Editor **preview thumbnail**. See [`image-format.md`](image-format.md).                          |
| `<n>.is`    | Image _n_ — the **real bitmap** (a full BMP). See [`image-format.md`](image-format.md).                          |

**Confirmed:** a directory listing of an empty project (`ref_a_leer.HMI`) contains exactly
`main.HMI`, `Program.s`, `0.pa` and `0.zi`, all with `start >= 0x700000`.

## 2. `main.HMI` — project header and resource directory

`main.HMI` is a small fixed-shape section. Its leading `u32` is a **content checksum** of the same
family as the page checksum (see [`section-checksum.md`](section-checksum.md)); a 128-byte empty
project has checksum `0xE9D6261B`.

### Header layout (confirmed offsets)

| Offset      | Size | Field                              | Notes                                                                      |
| ----------- | ---- | ---------------------------------- | -------------------------------------------------------------------------- |
| `0x00`      | 4    | `checksum` (u32 LE)                | Validated content checksum.                                                |
| `0x04`      | 4    | `header_length` (u32 LE)           | Observed `0x60` (96) — the offset at which the resource directory starts.  |
| `0x08`      | 5    | device/model config                | Observed constant `01 44 21 64 01` for a given device.                     |
| `0x0D`      | 1    | **character-encoding selector**    | **`0x18` = utf-8, `0x03` = iso-8859-1** (see note).                        |
| `0x0E`      | 10   | display/model config               | Observed constant `4F 00 20 8E 9C BA 00 00 00 00`.                         |
| `0x18`      | 4    | resource-directory offset (u32 LE) | Observed `0x60`; equals `header_length` and points at `0x60`.              |
| `0x1C`      | 4    | **resource-record count** (u32 LE) | Must equal the number of records in the resource directory.                |
| `0x20…0x5F` | 64   | reserved / config                  | Mostly zero; a `0x02` byte is observed at `0x41`. Constant across samples. |
| `0x60…`     | 16×N | resource directory                 | One 16-byte record per resource (below).                                   |

The bytes from `0x08` to `0x5F` are **constant for a given device + display direction + character
encoding**. Across three real projects they differed in only two places: the encoding byte at `0x0D`
and the resource count at `0x1C`. The safe way to author `main.HMI` is to lift this block from an
exemplar built in the Editor with exactly the settings you want.

> **The encoding byte reaches into firmware.** In `iso-8859-1` the degree sign is one byte `0xB0`; in
> `utf-8` it is `0xC2 0xB0`. Any MCU code that composes `.txt` strings must agree with `0x0D`.

### Resource directory record (16 bytes)

Starting at offset `0x60`, one record per resource:

| Offset | Size | Field     | Example                                               |
| ------ | ---- | --------- | ----------------------------------------------------- |
| `0x00` | 8    | `ext[8]`  | short tag, null-padded: `zi`, `pa`, `i`               |
| `0x08` | 8    | `name[8]` | full section name, null-padded: `0.zi`, `0.pa`, `0.i` |

**Which sections are listed — confirmed:** the resource directory lists every **`.i`, `.zi` and
`.pa`** section. It does **not** list `.is`, `Program.s` or `main.HMI`. Each image contributes a
single `.i` entry; the matching `.is` rides along implicitly. The count at `0x1C` must match the
number of records exactly, or the Editor will not load the project.

### Worked example: empty project (`main.HMI`, 128 bytes)

```text
0000  1B 26 D6 E9 60 00 00 00 01 44 21 64 01 18 4F 00   .&..`....D!d..O.
0010  20 8E 9C BA 00 00 00 00 60 00 00 00 02 00 00 00    .......`.......
0020  00 00 00 00 01 00 00 00 00 00 00 00 00 00 00 00   ................
0030  00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00   ................
0040  00 02 00 00 00 00 00 00 00 00 00 00 00 00 00 00   ................
0050  00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00   ................
0060  7A 69 00 00 00 00 00 00 30 2E 7A 69 00 00 00 00   zi......0.zi....
0070  70 61 00 00 00 00 00 00 30 2E 70 61 00 00 00 00   pa......0.pa....
```

Decoded:

- `checksum = 0xE9D6261B`
- `header_length = 0x60`
- encoding byte `@0x0D = 0x18` → utf-8
- resource-directory offset `@0x18 = 0x60`
- resource count `@0x1C = 2`
- two records at `0x60`: `{ext="zi", name="0.zi"}`, `{ext="pa", name="0.pa"}`

### Worked example: two images added (`main.HMI`, 160 bytes)

The same header with `@0x1C = 4` and four records — `0.i`, `1.i`, `0.zi`, `0.pa`:

```text
0060  69 00 00 00 00 00 00 00 30 2E 69 00 00 00 00 00   i.......0.i.....
0070  69 00 00 00 00 00 00 00 31 2E 69 00 00 00 00 00   i.......1.i.....
0080  7A 69 00 00 00 00 00 00 30 2E 7A 69 00 00 00 00   zi......0.zi....
0090  70 61 00 00 00 00 00 00 30 2E 70 61 00 00 00 00   pa......0.pa....
```

Note the two images appear as `0.i`/`1.i` only — the corresponding `0.is`/`1.is` sections exist in the
container but are **not** in this directory. In a third sample (`ref_img.HMI`, `main.HMI` 176 bytes)
the encoding byte `@0x0D = 0x03` (iso-8859-1) and `@0x1C = 5` (four images plus the page, no font).

## 3. `Program.s` — global startup code

`Program.s` is stored as **plain text** (the project's character encoding), with no leading content
checksum. It is the code from the Editor's `Program.s` tab: the
one-time power-on initialisation (global variable declarations, baud rate, backlight, an optional
power-on `printh` banner, and the initial `page 0`).

A freshly created project's `Program.s` is the Editor's default 675-byte boilerplate, beginning:

```text
0000  2F 2F 54 68 65 20 66 6F 6C 6C 6F 77 69 6E 67 20   //The following
0010  63 6F 64 65 20 69 73 20 6F 6E 6C 79 20 72 75 6E   code is only run
0020  20 6F 6E 63 65 20 77 68 65 6E 20 70 6F 77 65 72    once when power
```

The first little-endian u32 is `0x68542F2F`, but that is simply ASCII `//Th`, not a checksum.
