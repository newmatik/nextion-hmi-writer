# The page section (`<n>.pa`) and component encoding

Each page is one section named `<n>.pa` (`0.pa`, `1.pa`, …). It contains a fixed-size header, a table
of object descriptors, and the objects themselves. **Object 0 is the page** (its own attribute set);
objects 1..n are the components placed on it. This is the richest part of the format and the part a
generator spends most of its time in.

All layouts below are confirmed against Nextion Editor 1.68.1.3034; the worked example is a
byte-for-byte decode of the empty page `0.pa` in `ref_a_leer.HMI` (checksum `0x3DBB4308`, 769 bytes).

## 1. Page header (56 bytes)

| Offset | Size | Field | Notes |
| --- | --- | --- | --- |
| `0x00` | 4 | `checksum` (u32 LE) | Validated content checksum. See [`section-checksum.md`](section-checksum.md). |
| `0x04` | 4 | `data_size` (u32 LE) | **Equals the section size.** A writer must keep this in sync. |
| `0x08` | 4 | `info_addr` (u32 LE) | Offset of the object table. **Always `0x38` (56)** = end of this header. |
| `0x0C` | 4 | `object_count` (u32 LE) | Number of objects, including object 0 (the page). |
| `0x10` | 8 | *unidentified* | Observed `00 00 00 00 00 4F 21 00`. Uncertain. |
| `0x18` | 16 | `name[16]` | Page name, null-padded ASCII (e.g. `page0`). |
| `0x28` | 16 | *unidentified* | Observed `00 00 00 00 00 00 00 00 01 44 01 00 00 00 00 00`. Uncertain — the `01 44` recurs in `main.HMI`'s device block; likely page/model state. |

**Uncertain:** two regions are not yet explained — the 8 bytes at `0x10` and the 16 bytes at `0x28`.
They are constant for a given project/device and are safely carried through verbatim by a
clone-and-patch writer. Do not synthesise them from scratch.

## 2. Object table (ContentHeader)

Immediately at `info_addr` (`0x38`) is a table of `object_count` descriptors, each 12 bytes:

| Offset | Size | Field | Notes |
| --- | --- | --- | --- |
| `+0` | 4 | `start` (u32 LE) | Offset of the object body **relative to `info_addr`**. |
| `+4` | 4 | `size` (u32 LE) | Object body length in bytes. |
| `+8` | 4 | `flag` (u32 LE) | Purpose not confirmed; observed `0`. Uncertain. |

The object body then lives at `info_addr + start`, for `size` bytes.

Two invariants a writer must maintain:

- The **first `start` equals the table's own size** (`object_count × 12`). The table is contiguous
  with the first object body.
- Consecutive objects are packed: each `start` = previous `start` + previous `size`. The last
  `start + size` equals `data_size − info_addr`.

**Worked example (empty page, one object):** `object_count = 1`, so the table is 12 bytes.
`start = 0x0C` (= 12, the table size), `size = 0x02BD` (701), `flag = 0`. The body sits at
`0x38 + 0x0C = 0x44` and runs 701 bytes to `0x0301` = 769 = the section size.

## 3. Component (object) encoding

Every object — the page and each component — is a sequence of length-prefixed records, terminated by
four zero bytes:

```
Object := <u32 len>"att-NN"                 # NN = number of attribute records that follow
          NN × attribute-record             # <u32 L> <char name[16]> <value[L-16]>
          event-code block(s)               # marker + code-line records (below)
          00 00 00 00                       # terminator, present on every object
```

### Record types — told apart by length

Every record is `<u32 L> <L bytes>`. The value of `L` distinguishes the two record kinds:

- **`L >= 17` → attribute record.** The `L` bytes are `char name[16]` (null-padded) followed by
  `value[L-16]`.
- **`L < 16` → marker.** The `L` bytes are a bare ASCII string with **no** 16-byte name field. The
  opening `att-NN` and every `codes*-N` are markers.

There is **no type tag** for attribute values. The value width is simply `L − 16`:

| `L` | value width | typical use |
| --- | --- | --- |
| 17 | 1 byte | small enums, flags, 8-bit values |
| 18 | 2 bytes | coordinates, sizes, RGB565 colours, `txt_maxl` |
| 20 | 4 bytes | 32-bit integers (`val`), `groupid0/1` |
| ≥17, variable | `L−16` bytes | strings (`objname`, `txt`) |

Integers are **little-endian**. Strings are stored in the **project's character encoding** and are
**not** separately length-prefixed — the string simply occupies the whole `value` field (typically
null-terminated within a fixed width such as `txt_maxl`). Colours (`bco`, `pco`, `bco2`, …) are
**RGB565 stored little-endian** (`65535`/`0xFFFF` = white, `0` = black).

### Geometry invariant

Position and size are `x`, `y`, `w`, `h`, and the Editor keeps two derived values in sync:

```
endx = x + w - 1
endy = y + h - 1
```

A writer that changes geometry **must** recompute `endx`/`endy`, or the Editor will disagree with
its own render.

### Event-code blocks

Event handlers are stored as a marker whose suffix is the **number of code lines**, immediately
followed by that many code-line records (each an ordinary `<u32 L> <L bytes>` holding one raw line of
Nextion instruction text):

| Marker | Event |
| --- | --- |
| `codesload-N` | page **Preinitialize** |
| `codesloadend-N` | page **Postinitialize** |
| `codesdown-N` | **Touch Press** |
| `codesup-N` | **Touch Release** |
| `codesunload-N` | page **unload** |
| `codestimer-N` | **Timer** tick |

A handler with no code still carries its marker with `-0`. Which markers appear depends on the object
type — a page carries the page-level markers; a Timer carries `codestimer`; touchable components carry
`codesdown`/`codesup`.

## 4. Worked example — the page object, fully decoded

The single object of the empty page `0.pa` in `ref_a_leer.HMI`. This is a real, byte-exact decode:

```
[marker] "att-28"           # 28 attribute records follow
type      L=17 w=1  0x79 (121)     # 121 = Page
id        L=17 w=1  0
objname   L=21 w=5  "page0"
vscope    L=17 w=1  0
drag      L=17 w=1  0
sendkey   L=17 w=1  0
aph       L=17 w=1  0x7F (127)
movex     L=18 w=2  0
movey     L=18 w=2  0
x         L=18 w=2  0
y         L=18 w=2  0
w         L=18 w=2  320
h         L=18 w=2  240
endx      L=18 w=2  319          # = x + w - 1
endy      L=18 w=2  239          # = y + h - 1
effect    L=17 w=1  0
first     L=17 w=1  0
time      L=18 w=2  300
lockobj   L=17 w=1  0
groupid0  L=20 w=4  0
groupid1  L=20 w=4  0
up        L=17 w=1  0xFF
down      L=17 w=1  0xFF
left      L=17 w=1  0xFF
right     L=17 w=1  0xFF
sta       L=17 w=1  1
bco       L=18 w=2  0xFFFF (65535)   # page background colour (white)
pic       L=18 w=2  0xFFFF           # 0xFFFF = no crop-background image
[marker] "codesload-0"
[marker] "codesloadend-0"
[marker] "codesdown-0"
[marker] "codesup-0"
[marker] "codesunload-0"
00 00 00 00                          # object terminator
```

The `endx = 319 = 0 + 320 − 1` and `endy = 239 = 0 + 240 − 1` relationships are visible directly.

## 5. Component type IDs and attribute sets

Component **type IDs** and their attribute counts, confirmed on a Discovery-series panel:

| Component | `type` | attrs | Key attributes beyond the common set |
| --- | --- | --- | --- |
| Page | 121 | 28 | background `bco`; page-level event markers |
| Text | 116 | 39 | `sta` (0 crop / 1 solid / 2 image), `font`, `bco`, `pco`, `xcen`, `ycen`, `txt`, `txt_maxl`, `isbr`, `spax`, `spay` |
| Button | 98 | 42 | `sta`, `bco`/`bco2` (normal/pressed fill), `pic`/`pic2`, `pco`/`pco2`, `val`, `txt`; `codesdown`/`codesup` event code |
| Picture | 112 | 22 | `pic` (image id) — nothing else visual |
| Progress bar | 106 | 29 | `val`, `dez`, `dis`, `bco`, `pco`, `bpic`/`ppic` |
| Number | 54 | 39 | `val` (4-byte int), `lenth`, `format`, `font`, `xcen`, `ycen` |
| Timer | 51 | 9 | `tim` (ms), `en`; `codestimer` event code |
| Variable | 52 | 11 | `sta` (0 numeric / 1 text), `val`, `txt`, `txt_maxl`; `vscope` (0 local / 1 global) |

The `attrs` count is the `NN` in the object's opening `att-NN` marker. (Cross-check: a Text component
in a real page parses to 42 records = 1 `att-39` marker + 39 attributes + 2 event markers; a Variable
parses to 12 = 1 `att-11` marker + 11 attributes.)

### Common attribute prefix

Every component begins with the same block, in this order:

```
type, id, objname, vscope, drag, sendkey, aph, movex, movey,
x, y, w, h, endx, endy, effect, first, time, lockobj, groupid0, groupid1
```

- `type` — the numeric type from the table above. Read-only in the Editor; stored here.
- `id` — the per-page index the Editor assigns. **Unstable** — it is renumbered on any add/delete/
  reorder. Never build an MCU protocol on it (see below).
- `objname` — the name addressed from the MCU (`t0.txt="…"`). Short ASCII.
- `vscope` — `0` = local, `1` = global. Global objects are reachable from any page; this is how a
  protocol variable block is built.

### Type-specific notes

- **Text (116).** Draws `txt` using `font`, `pco`, `xcen`/`ycen`. `sta` selects background:
  `0` crop, `1` solid `bco`, `2` image. **`txt_maxl` is the allocation for the string and defaults
  low — it silently truncates anything longer.** Text **clips**, it does not word-wrap.
- **Button (98).** Same drawing as Text plus pressed state. `sta=1` uses `bco`/`bco2`
  (normal/pressed fill, rectangular only); `sta=2` uses `pic`/`pic2` (each exactly the button's
  pixel size, since Nextion never scales). Touch code lives in `codesdown`/`codesup`.
- **Picture (112).** Just `pic`. Locked to the referenced image's native size.
- **Number (54).** `val` is a 4-byte integer; `lenth`/`format` control digit count and base.
- **Timer (51).** `tim` in milliseconds, `en` on/off, periodic code in `codestimer`.
- **Variable (52).** A non-visual store. `sta=0` numeric (`val`), `sta=1` text (`txt`/`txt_maxl`).
  Set `vscope=1` for a global protocol variable.

## 6. Worked example — a Text component's key fields

Authoring a Text component with deliberately odd, unique values makes each field findable in the
binary by searching for its value. A Text with `x=101, y=103, w=107, h=109, txt="MAGREF",
txt_maxl=42` decodes to:

```
[marker] "att-39"
type      L=17  0x74 (116)     # Text
objname   L=18  "t0"
...
x         L=18  0x0065 (101)
y         L=18  0x0067 (103)
w         L=18  0x006B (107)
h         L=18  0x006D (109)
endx      L=18  0x00CF (207)   # 101 + 107 - 1
endy      L=18  0x00D3 (211)   # 103 + 109 - 1
...
txt       L=22  "MAGREF"       # value width = L-16 = 6
txt_maxl  L=18  0x002A (42)
```

## 7. Design consequences worth knowing

These follow from the encoding and from Editor behaviour; they shape any generator:

- **Text clips, it does not wrap.** Pre-wrap static text into **one Text component per line**;
  ensure every string a component can ever receive fits its width on one line. Raise `txt_maxl` from
  the *longest* possible value, not the initial one.
- **Do not use the native "Send Component ID".** Those touch frames carry the page and component
  *index*, which the Editor renumbers on any add/delete/reorder. Emit your own semantic frames from
  Touch Release instead (`printh A5 5A 01 …`), so the MCU depends on a stable contract.
- **Images are never scaled.** A Picture is locked to its resource's native size, and a `sta=2`
  button needs an image exactly the button's size. Pre-composite one image per distinct appearance.
