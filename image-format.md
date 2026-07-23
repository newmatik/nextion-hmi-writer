# The image sections (`<n>.is` and `<n>.i`)

Each imported picture produces **two** sections that share the index `n`:

- **`<n>.is`** — the **real bitmap**: a fixed header, the literal ASCII `"bmp"`, and then the
  complete original 24-bit BMP file, byte-exact. This is where the display pixels live.
- **`<n>.i`** — the Editor **preview thumbnail**: a downsampled representation used to draw the
  picture inside the Editor canvas. It is **not** the display bitmap.

Only `<n>.i` is listed in the `main.HMI` resource directory (see
[`container-format.md`](container-format.md)); the `<n>.is` rides along implicitly. Components
reference the image by index (`pic=3`).

**Nextion never scales an image.** A Picture is locked to its resource's native size, and a `sta=2`
button needs an image of exactly the button's size. Generators must **pre-composite one image per
distinct appearance** at its exact pixel size.

Both layouts below are confirmed against real sections in `ref_img.HMI` (a 16×16 image) and
`ref_c_text.HMI` (a 32×32 and a 48×48 image).

## 1. `<n>.is` — the real bitmap

Structure: a **24-byte binary header**, then the 3-byte ASCII marker `"bmp"`, then the **complete
original BMP file** unmodified. This is trivial to synthesise from any 24-bit BMP.

| Offset | Size | Field | Notes |
| --- | --- | --- | --- |
| `0x00` | 3 | magic | `0A 64 01` |
| `0x03` | 1 | type | `01` for `.is` |
| `0x04` | 4 | reserved | zero |
| `0x08` | 4 | `prefix_len` (u32 LE) | `0x1B` = **27** = offset from section start to the BMP's `"BM"`. |
| `0x0C` | 2 | `width` (u16 LE) | pixels |
| `0x0E` | 2 | `height` (u16 LE) | pixels |
| `0x10` | 4 | `bmp_size` (u32 LE) | Size of the embedded BMP file (matches its own header field). |
| `0x14` | 4 | reserved | zero |
| `0x18` | 3 | `"bmp"` | literal ASCII `62 6D 70` |
| `0x1B` | var | BMP file | complete original 24-bit BMP, byte-exact, starting with `"BM"` |

`prefix_len` (27) = 24-byte header + 3-byte `"bmp"`, i.e. exactly the offset at which `"BM"` begins.
Section size = `27 + bmp_size`.

### Worked example (`0.is`, 849 bytes, a 16×16 image)

```text
0000  0A 64 01 01 00 00 00 00 1B 00 00 00 10 00 10 00   .d..............
0010  36 03 00 00 00 00 00 00 62 6D 70 42 4D 36 03 00   6.......bmpBM6..
0020  00 00 00 00 00 36 00 00 00 28 00 00 00 10 00 00   .....6...(......
```

- magic `0A 64 01`, type `01`
- `prefix_len = 0x1B` (27)
- `width = 0x10` (16), `height = 0x10` (16)
- `bmp_size = 0x0336` (822)
- `"bmp"` at `0x18`, then `"BM"` at `0x1B`; the BMP's own file-size field is also `0x0336` (822)
- section size = 27 + 822 = **849** ✓

Larger images follow the identical shape — `ref_c_text.HMI` holds a 32×32 image (`width=0x20`,
`bmp_size=0x0C36`, section 3,153 B) and a 48×48 image (`width=0x30`, `bmp_size=0x1B36`, section
6,993 B). Note the leading `0A 64 01 01` is identical across all `.is` sections regardless of
content — it is a **format magic, not a per-content checksum**.

## 2. `<n>.i` — the Editor preview thumbnail

The `.i` section is a **downsampled preview**, produced for the Editor canvas, not the display. Its
header mirrors the `.is` header, but the payload is a custom row-block encoding that is **not** the
picture's pixels.

| Offset | Size | Field | Notes |
| --- | --- | --- | --- |
| `0x00` | 3 | magic | `?? 64 01` — first byte observed **`0A` or `0B`** (varies; see uncertainty). |
| `0x03` | 1 | type | `03` for `.i` |
| `0x04` | 4 | reserved | zero |
| `0x08` | 4 | `header_len` (u32 LE) | `0x18` = **24** |
| `0x0C` | 2 | `width` (u16 LE) | pixels |
| `0x0E` | 2 | `height` (u16 LE) | pixels |
| `0x10` | 4 | `payload_len` (u32 LE) | bytes after the 24-byte header (= section size − 24). |
| `0x14` | 4 | reserved | zero |
| `0x18` | 4 | `row_count`? (u32 LE) | observed `1`; purpose uncertain. |
| `0x1C` | 4 | `rowindex_off` (u32 LE) | offset to a row-index table (row_id, offset pairs). |
| `0x20…` | var | row blocks | each begins with a length word; then a 16-entry index. |

### Worked example (`0.i`, 508 bytes, a 16×16 image)

```text
0000  0B 64 01 03 00 00 00 00 18 00 00 00 10 00 10 00   .d..............
0010  E4 01 00 00 00 00 00 00 01 00 00 00 54 01 00 00   ............T...
0020  00 00 00 00 00 00 00 00 00 00 00 00 2E 00 00 00   ................
```

- magic `0B 64 01`, type `03`
- `header_len = 0x18` (24)
- `width = 0x10` (16), `height = 0x10` (16)
- `payload_len = 0x01E4` (484) = 508 − 24 ✓
- `rowindex_off = 0x0154` (340)
- first row-block length word `0x2E` (46) at `0x2C`

### Why it is a thumbnail, not the display bitmap

Two pieces of evidence:

1. **The row payloads are downsampled.** For a 16-pixel row the block carries only ~8 words (a 2:1
   reduction), far fewer than 16 pixels of colour data.
2. **Fixed filler words appear in every image.** The words `0xC5A1`, `0x7235`, `0xE7EC`, `0x71ED`
   each occur **exactly once per row in every image, regardless of content**. Content-derived pixel
   data cannot contain identical constants across unrelated images — this is preview scaffolding, not
   pixels.

Because the display pixels live entirely in `<n>.is`, a generator most likely needs only a **correct
`.is`** plus a **structurally valid `.i`** (a preview that the Editor accepts). Producing a
byte-perfect `.i` is not required to drive the panel — only to keep the Editor's canvas happy.

## 3. Uncertainties

- **The `.i` row-block codec is not fully reverse-engineered.** The header fields above are confirmed;
  the internal downsampling/encoding of the row blocks is only partially understood, and is
  deliberately left as "preview scaffolding" rather than decoded pixel-by-pixel.
- **The `.i` leading byte varies** (`0A` vs `0B`) between images. Whether this is a flag or a weak
  per-content check byte is unresolved. All `.is` sections observed use `0A`.
- **Flash cost of images.** Images land in the compiled `.TFT` as **RGB565, 2 bytes per pixel,
  uncompressed** (`bytes = width × height × 2`). Budget from that, not from the `.is`/`.i` section
  sizes, which include BMP/preview overhead.
