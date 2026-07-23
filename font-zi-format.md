# The font section (`<n>.zi`)

Fonts are `.zi` binaries produced by the Editor's **Font Creator** (`Tools → Font Generator`) and
embedded verbatim as sections named `<n>.zi`. The number is the font id components reference
(`font=0` → `0.zi`), so **font order is a contract** — fix it and never reorder.

The critical practical finding is not the header layout but the **flash cost**: an embedded UTF-8 font
carries a full 65,536-entry codepoint table (~640 KB) regardless of how few characters were
subsetted, whereas an ISO-8859-1 font needs only a 256-entry table (a few KB). Encoding choice, not
character count, dominates font flash.

All figures below are confirmed against real sections: the embedded font in `ref_a_leer.HMI`
(`0.zi`, 665,416 bytes) and the on-disk `seg16/20/28.zi` reference fonts.

## 1. Header layout

Offsets confirmed against generated files. The leading four bytes (`0x00`–`0x03`) are the font file's
own header/magic, not a container checksum (see the note at the end).

| Offset  | Size | Field                                      | Notes                                                                              |
| ------- | ---- | ------------------------------------------ | ---------------------------------------------------------------------------------- |
| `0x04`  | 1    | version                                    | See uncertainty note. Observed `0x18` (embedded UTF-8) and `0x03` (on-disk fonts). |
| `0x05`  | 1    | multi-byte mode                            | `0` = single-byte, `1` = double-byte, `2` = subset table.                          |
| `0x06`  | 1    | code page                                  | `0` observed.                                                                      |
| `0x07`  | 1    | **character height** (px)                  | Single byte; any value fits.                                                       |
| `0x08`  | 1    | character width                            | `0` = variable width in on-disk fonts; see note.                                   |
| `0x1F`  | 1    | variable-width flag                        | `1` = proportional.                                                                |
| `0x24`  | 4    | **character count** (u32 LE)               | Number of glyphs in the font.                                                      |
| `0x2C…` | var  | font name, then character map / glyph data | Null-padded ASCII name.                                                            |

Reading this header back is the cheapest way to confirm a font is what you think it is.

## 2. Worked example — the embedded UTF-8 font (`0.zi`, 665,416 bytes)

```
0000  04 FF 00 0A 18 01 00 10 FF FF 00 FF 00 00 01 00   ................
0010  06 0A 00 00 1C 27 0A 00 2C 00 00 00 FF 00 01 01   .....'..,.......
0020  05 01 00 00 80 01 00 00 00 00 00 00 73 65 67 31   ............seg1
0030  36 75 74 66 2D 38 00 00 01 00 00 00 40 01 02 00   6utf-8......@...
```

Decoded:

- `0x05 mode = 0x01` → double-byte (UTF-8)
- `0x07 height = 0x10` → 16 px
- `0x1F var-width flag = 0x01` → proportional
- `0x24 count = 0x00000180` → **384 characters**
- `0x2C name = "seg16utf-8"`

This font was subsetted in the Editor to 384 characters, yet the section is **665,416 bytes**. Of
that, only ~10 KB is glyph data; the rest is the fixed codepoint table (next section).

## 3. Why UTF-8 fonts are enormous — the codepoint table

The Editor writes UTF-8 fonts in **double-byte mode (`0x05 = 1`)** with a **full 65,536-entry
codepoint table**, roughly **640 KB, present regardless of how few characters were subsetted**.
Subsetting shrinks the glyph payload but **not** the index, and the index dominates.

Measured, and carried into the compiled `.TFT` verbatim:

| Font                | Encoding   | Height | Chars | Section size            |
| ------------------- | ---------- | ------ | ----- | ----------------------- |
| `0.zi` (seg16utf-8) | utf-8      | 16 px  | 384   | **665,416 B** (~650 KB) |
| `seg16.zi`          | iso-8859-1 | 16 px  | 224   | 7,129 B                 |
| `seg20.zi`          | iso-8859-1 | 20 px  | 224   | 8,946 B                 |
| `seg28.zi`          | iso-8859-1 | 28 px  | 224   | 13,517 B                |

So **each UTF-8 font costs ~0.65 MB of flash minimum**, and every distinct **(family, pixel height)**
pair is a separate font. Three UTF-8 sizes ≈ **1.9 MB** — nearly half a 4 MB panel — before a single
image. The three ISO sizes above total **~30 KB**. If the language set fits Latin-1, the saving is on
the order of **1.9 MB → 30 KB**. Choose the encoding with this cost in front of you, not after.

The ZI format defines a compact subset mode (`0x05 = 2`), but the Editor's Font Creator does **not**
appear to emit it for a subset selection — it still writes mode 1 with the full table.

## 4. Font Creator behaviour worth knowing

- **Height floor is effectively 16.** The `Height` dropdown lists nothing below 16, but it is an
  editable combo: typing a smaller value _does_ generate a structurally valid ZI whose header
  honestly reports that height (a 12 px ASCII font is 2,281 bytes, `test.zi`). **Do not take that as
  permission** — sub-16 text is unreadable on these panels. Treat 16 as a hard design floor.
- **Encoding list (Editor 1.68):** `ascii`, `iso-8859-1/2/3/4/5/6/7/8/9/11/13/15`, `gb2312`, `big5`,
  `ks_c_5601-1987`, `shift-jis`, `koi8-r`, `windows-874/1255/1256/1257/1258`, `utf-8`.
  **Note what is missing: `windows-1252`.** So the ISO-8859-1 route gives you no typographic
  punctuation (`– — … “ ”`), only the Latin-1 repertoire (`° · ÄÖÜäöüß`, all of Western Europe).
- **UTF-8 turns `Range` into a subset picker** — a Language Choice dialog with ~142 individually
  checkable Unicode blocks. `Select All` is ~56,338 characters (absurd; a full 56 px UTF-8 font is
  reported at ~23 MB). For German/English/French/Spanish/Dutch, blocks 01 (Basic Latin) + 02 (Latin-1
  Supplement) suffice; block 03 (Latin Extended-A) adds Eastern Europe and Turkey for 128 more.
- Whether a glyph like `←`, `○`, `●` renders depends on the **source TTF** containing it, not just on
  the block being ticked. Preview before relying on symbols; fall back to image assets otherwise.
- Fonts **cannot be regenerated on a build server** — the generator is part of the Windows GUI.
  Commit the `.zi` files and pin them by hash so a silently swapped font fails the build.

## 5. Uncertainties

- **Byte `0x04` ("version").** Observed `0x18` (24) in the embedded UTF-8 font but `0x03` in the
  on-disk `seg*.zi` fonts. Whether this is a format version, a per-font attribute, or something else
  is not resolved. Since fonts are embedded verbatim from the generator, a generator that reuses a
  generated `.zi` never needs to synthesise this byte.
- **Byte `0x08` (character width).** Reads `0` (= variable) in the on-disk fonts but `0xFF` in the
  embedded UTF-8 font, while `0x1F` independently flags variable width. The exact division of labour
  between `0x08` and `0x1F` is not fully pinned.
- **The codepoint table and glyph encoding** past the header were not decoded byte-by-byte; the
  headline flash-cost finding does not depend on that, and a generator embeds the `.zi` whole.
- **Leading four bytes.** For `.zi` (and `.i`/`.is`) sections, offset 0 holds the resource file's own
  header/magic, **not** the validated content checksum used for `main.HMI` and `.pa`. `Program.s` is
  plain text and also has no leading checksum. See
  [`section-checksum.md`](section-checksum.md).
