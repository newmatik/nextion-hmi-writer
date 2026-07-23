# Driving the Nextion Editor

Practical, tested knowledge for operating the Nextion Editor and for generating Nextion projects
**programmatically**. The rest of this repository is a byte-level reference for the `.HMI` container;
this document is the operator's companion — how the editor behaves, what its settings mean, where the
hard limits are, and how to automate the few steps that only exist inside its Windows GUI.

Findings marked **Confirmed** were verified against **Nextion Editor 1.68.1.3034** on Windows 11.
Findings marked **Uncertain** are derived or reported and not yet confirmed first-hand. As with the
format docs, everything is pinned to that editor version.

## When you need this

The editor is a Windows-only GUI with no command-line interface and no textual project import format.
For a ten-component demo that is fine. It becomes the bottleneck as soon as a project is:

- **large** — hundreds of components placed by hand take days and must be redone after design changes;
- **generated** — the layout lives in JSON, YAML or code and the HMI should follow automatically;
- **reviewable** — a binary `.HMI` produces no useful diffs in version control.

There are two answers, and this repository supports both: build the `.HMI` yourself
([`nextion_hmi_binary.py`](research/nextion_hmi_binary.py) +
[`nextion_hmi_writer.py`](research/nextion_hmi_writer.py)), and automate the unavoidable GUI steps —
font generation, image preview minting, compile-to-`.TFT` — from the outside
([`editor_control.py`](research/editor_control.py),
[`capture_screenshots.py`](research/capture_screenshots.py)).

## Install layout — Confirmed

Standard installation: `C:\Program Files (x86)\Nextion Editor\`

| File                                             | Meaning                                                                          |
| ------------------------------------------------ | -------------------------------------------------------------------------------- |
| `Nextion Editor.exe`                             | thin launcher (~210 KB); its version resource is the authoritative editor version |
| `ACTR.dll`                                        | the actual application (~9.9 MB, packed; a text search inside yields nothing)     |
| `ResView.exe`, `PictureBox.exe`, `GmovMaker.exe` | resource viewer, image tool and video tool                                        |

Read the version without launching:

```powershell
(Get-Item 'C:\Program Files (x86)\Nextion Editor\Nextion Editor.exe').VersionInfo.FileVersion
```

**Always pin the editor version in project documentation.** Compiled `.TFT` files and the `.HMI`
container layout depend on it. "Made with Nextion Editor" is not a reproducible statement.

## Project settings — Confirmed

The `Setting` dialog has three tabs. All three matter and all three are easy to misconfigure.

### Device

Product series: **Basic · Discovery · Enhanced · Intelligent · Edge**. Pick the exact model, for
example `NX3224F028_011` (Discovery, 2.8 in, 4 MB flash, 3584 B RAM, 64 MHz).

**The stated resolution describes the native panel orientation.** For `NX3224F028_011` it reads
`240X320`, i.e. portrait. A 320×240 landscape surface comes from _Display direction_, not from a
different model.

### Display

- **Display direction**: `0`/`180` = portrait, `90`/`270` = landscape. `90` and `270` show the same
  screen rotated 180° against each other. Which one is correct depends on the mechanical mounting and
  is decided on the real panel.
- **Character Encoding**: sets both the encoding of `.txt` attributes and the code page of generated
  fonts. It must be fixed **before** generating fonts and before entering component text. Changing it
  later affects the project, the fonts and the MCU's string encoding.

  Full list in 1.68 (Confirmed): `ascii`, `iso-8859-1/2/3/4/5/6/7/8/9/11/13/15`, `gb2312`, `big5`,
  `ks_c_5601-1987`, `shift-jis`, `koi8-r`, `windows-874/1255/1256/1257/1258`, `utf-8`.

  **`windows-1252` is absent.** `iso-8859-1` covers `° · ÄÖÜäöüß` and Western-European languages, but
  not `– — … ‹ › „ ”` and not `← ○ ●`.

  **ISO-8859-1 vs UTF-8:** ISO-8859-1 is one byte per character and small fonts, enough when Polish,
  Czech, Turkish, Greek and Cyrillic are not needed. UTF-8 covers those and typographic punctuation
  but costs far more flash (see [Fonts](#fonts)). The encoding reaches into the firmware: the degree
  sign is the single byte `0xB0` in ISO-8859-1 but the sequence `0xC2 0xB0` in UTF-8. MCU code and
  byte-exact tests must match the project encoding. In the writer this is the `TEXT_ENCODING`
  constant in [`nextion_hmi_writer.py`](research/nextion_hmi_writer.py).

### Project

| Option                                   | Recommendation                                                                                       |
| ---------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Open Password                            | Set only if truly needed; it changes the page header on disk and complicates parsing the `.HMI`      |
| One-time update of TFT files             | Renames the `.TFT` after a successful SD update; useful in the field, disable during development     |
| Ignore image resources at compile time   | Disable for a real build; only for quick logic-only compiles                                         |
| Ignore library resources at compile time | Likewise disable for a real build                                                                    |
| Memory file storage size                 | Reserves non-volatile display memory; leave empty when the MCU holds the state of a safety system    |

## Editor UI — Confirmed

Useful when driving the editor from the outside (which regions to click, what holds focus):

- **Menus**: `File · Tools · Setting · Help · About`. Project settings live under `Setting`, the Font
  Creator and other resource tools under `Tools`.
- **Toolbar**:
  `Open · New · Save · Compile · Debug · Upload · Copy · Cut · Paste · Lock · Unlock · Delete · Undo · Redo · Device ID`
  plus zoom. `Debug` opens the simulator, `Upload` writes serially to a panel.
- **Panes**: _Toolbox_ top-left; resource tabs `Picture · Fonts · Gmov · Video · Audio` bottom-left;
  canvas in the middle with `Display` and `Program.s`; _Page_ top-right; _Attribute_ bottom-right;
  _Output_ and _Event_ at the bottom.
- **Event pane**: a selector per event (`Touch Press Event`, `Touch Release Event`, …) with a **Send
  Component ID** checkbox, off by default (as wanted for semantic events). Pages have their own
  `Preinitialize` and `Postinitialize` events.

The right-hand **Page** list is what [`capture_screenshots.py`](research/capture_screenshots.py)
walks with the Down arrow — click a page there to give the list keyboard focus.

### Attribute table

Every component exposes a flat attribute list. The full byte encoding is in
[`page-and-component-format.md`](page-and-component-format.md); the fields that bite most often:

- `font` and `bco`/`pco` are **indices and numbers, not names** — a component says `font=2`,
  `bco=65535`. Font order and the `RGB888 → RGB565` conversion therefore belong in the generator.
- `id` is assigned by the editor per page and is **unstable — never use it as a contract.**
- `txt_maxl` defaults small (often 10). Size it for the **longest** runtime string, or text is
  truncated unnoticed.
- `objname` is the MCU handle (`t0.txt="…"`). Keep it short ASCII; 14 characters is a safe upper
  bound, and it must still fit with any theme suffix such as `home_d`.

## Component model and the limits that decide a design

These rules decide whether a design is buildable at all. Consider them before drawing.

### Nextion never scales images — Confirmed

An image is always copied 1:1. A **Picture** is fixed to the native size of its resource; an image
**Button** (`sta=2`) needs an image of **exactly** the button size. A generic 32×32 icon cannot be
reused across 40×48, 48×48 and 96×82 buttons. Either pre-compose one image per component at its exact
size, or drop the image. For generated assets, compose **per component** (background at the right
size, icon centred) and de-duplicate by `(variant, width, height, icon, enabled)`.

### Button background modes

| `sta` | Meaning      | Notes                                                                       |
| ----- | ------------ | --------------------------------------------------------------------------- |
| 0     | Crop image   | a cut-out of a larger image at the component's page coordinates             |
| 1     | Solid colour | `bco` normal, `bco2` pressed; rectangular only, **no rounded corners**      |
| 2     | Image        | `pic` normal, `pic2` pressed; both exactly the component size               |

With `sta=2` Nextion draws `txt` using `font`, `pco`, `xcen` and `ycen` over the image, so
"icon + label" works via a pre-composed background plus Nextion text. Crop mode (`sta=0`) effectively
needs a full-screen background image per page: at RGB565 a 320×240 background is 153,600 B, so 34
pages is 5.2 MB and overruns 4 MB of flash. Budget first (Uncertain in the exact per-project total).

### Flash budget — Confirmed

Images live uncompressed as **RGB565, 2 bytes per pixel**:

```text
bytes = width * height * 2
```

Subtract a reserve and add fonts separately. Only the compile reports real usage; everything before
it is an estimate and must be labelled as one.

### Text clips, it does not wrap — Confirmed

Text longer than its box is cut at the edge; a taller box does not wrap. So **pre-wrap static text**
in the generator (one text component per line) and make sure **dynamic text fits on one line** —
check every enum text and widest number format against the field width. Size `txt_maxl` for the
longest possible string, not just the initial value.

### Component id vs semantic events

The native **Send Component ID** transmits page and component index, and the editor renumbers those
on every add, delete or reorder. The MCU must not depend on them. Disable Send Component ID and send
your own stable frame in the Touch Release event:

```text
printh A5 5A 01 32 14 00 22
```

A stable frame carries preamble, protocol version, event id, arguments and a checksum. The firmware
then depends on a fixed contract instead of editor bookkeeping.

## Fonts

`.zi` fonts are made with `Tools → Font Generator` (the dialog is titled **Font Creator**). They
**cannot** be reproduced on a build server — the generator is part of the Windows GUI — so version
the `.zi` files and pin them by SHA-256. Each **font family + pixel height** pair is its own resource
with its own id, and the ids are indices (`font=2`), so the order is part of the contract.

### The 16-pixel floor — Confirmed

The `Height` list offers nothing below **16**. The field is editable and accepts smaller values (12
produces a structurally valid `.zi`), but 12-pixel text is practically unreadable on a 2.8 in 320×240
panel. Treat 16 as a hard design floor: "file generated" is not "legible on the panel".

### UTF-8 subsets and their real cost — Confirmed

With `utf-8`, `Range` becomes a subset picker (the **Language Choice** dialog offers ~142 Unicode
blocks; blocks 01 and 02 cover German/English/French/Spanish/Dutch, 03 adds Eastern Europe and
Turkey). But **the subset shrinks the glyph set, not the dominant index.** The editor writes UTF-8
fonts in double-byte mode (`0x05 = 1`) with a full 65,536-codepoint table that costs ~640 KB
regardless of the selection: a measured 16-pixel font with 384 characters is **665 KB**, of which
only ~10 KB is glyph data, and the compile takes the full 665 KB. Three sizes are therefore ≥ ~1.9 MB.

An ISO-8859-1 font needs only a 256-entry table, dropping three sizes from ~1.9 MB to ~50 KB. ZI v6
has a compact subset mode (`0x05 = 2`) but the Font Creator does not seem to emit it for subset
selections. See [`font-zi-format.md`](font-zi-format.md) for the header fields; reading back the
header is the cheapest check that a font matches your assumptions.

## Generating an `.HMI` safely

Do **not** synthesise the container from scratch. The reliable path is **clone-and-patch**, and it is
what [`nextion_hmi_writer.py`](research/nextion_hmi_writer.py) implements:

1. In the editor, build an **exemplar** holding one instance of every component type and attribute you
   need. Use deliberately **odd, unique values** — e.g. `x=101, y=103, w=107, h=109, txt="ZZ7Q1"` — so
   fields can be found by concrete value instead of guessed offsets.
2. Write a parser and serializer and first prove `serialize(parse(f)) == f` **byte for byte**
   (`roundtrip_is_exact()` in [`nextion_hmi_binary.py`](research/nextion_hmi_binary.py)). Until that
   holds, the format model is incomplete and nothing may be generated.
3. **Clone and patch, do not synthesise.** Take each component from the exemplar and overwrite only
   understood attributes; unknown default bytes are preserved. Changed top-level sections are
   tombstoned and appended with a new record and payload; both directory copies and their checksum
   are updated. This is exactly what the writer's `add_*` / `assemble*` functions do.
4. **Use the editor as an oracle.** Open the generated file, save it in the editor, parse again and
   compare. If the editor corrects any bytes, the model is wrong there.

Incremental saving helps diffs less than expected: the editor appends rather than rewriting and only
touches changed sections. Unique test values are more reliable. The full derivation is in
[`methodology.md`](methodology.md); the checksum details are in
[`section-checksum.md`](section-checksum.md).

## Proof chain and its limits

Each step proves only what it proves. State plainly which are done and which are open.

| Step                              | Proves                                              | Does not prove                       |
| --------------------------------- | --------------------------------------------------- | ------------------------------------ |
| Generator round-trip byte-exact   | the format model is complete                        | editor behaviour                     |
| Editor opens the generated `.HMI` | container and object structure are valid            | runtime behaviour                    |
| Editor saves with no diff         | the editor accepts the bytes as canonical           | —                                    |
| Editor compiles to `.TFT`         | resources, fonts and flash budget are valid         | —                                    |
| Editor debug simulator            | layout, navigation, uplink frames, downlink commands | UART levels, touch, reset, sleep/wake |
| Real panel                        | everything that remains                             | —                                    |

A simulator run is never a hardware release. Name which stages are proven and which are open.

## Flashing a panel — Uncertain

- **microSD**: FAT32, ≤ 32 GB, exactly one `.TFT` in the root. Power-cycle the panel; the update runs
  at start.
- **Serial**: Upload from the editor over a USB-TTL adapter with 3.3 V logic.

Both are reported, not yet confirmed first-hand in this document.

## Common traps

- Component and page names are short ASCII; **14 characters** is a safe cap, and a name must still fit
  with a theme suffix such as `home_d`.
- Light and dark themes as **duplicate page sets** make switching a single `page` command with no
  runtime recolouring — but double the page and image count. Check the flash budget first.
- The editor has no CLI. Compile and font generation stay manual; design the build so that only those
  steps are manual and their outputs are pinned by hash.

---

This is an independent reverse-engineering companion, not affiliated with or endorsed by the vendor,
and it contains no vendor source code. Nextion is a product of Shenzhen TJC Technology.
