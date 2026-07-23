# The Nextion Editor `.HMI` file format

A reverse-engineered, byte-level reference for the binary project file (`.HMI`) written by the
**Nextion Editor**. This document set is written from first-hand analysis of files produced by
**Nextion Editor 1.68.1.3034 on Windows**, and it is complete enough to _read_ and _write_ `.HMI`
files programmatically.

`.HMI` is the Editor's editable project container (the design source). It is not the format flashed
to a panel — that is the compiled, read-only `.TFT`. This reference covers only `.HMI`. There is no
published specification for either format; everything here was decoded by inspecting the bytes and by
using the Editor itself as an oracle (save a controlled change, diff the result).

## Why this exists

The Nextion Editor is a Windows-only GUI with **no command-line interface** and **no textual project
import format**. For a small design that is fine. For a large or generated UI it is the bottleneck:
hundreds of components must be placed by hand and re-placed on every design change, and a binary
`.HMI` produces useless version-control diffs. Understanding the container lets you _generate_ the
project from a machine-readable source of truth instead.

The final obstacles to writing `.HMI` files were the page/manifest checksums and a second checksum over
the mirrored top-level directory. All three are now recovered for arbitrary content and length. The
generated-project path has passed the Editor-open and zero-error compile gates; see
[`section-checksum.md`](section-checksum.md).

## How to read this set

| Document                                                       | What it covers                                                                                                                                                  |
| -------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`container-format.md`](container-format.md)                   | The outer container: directory of sections, the 7 MiB slack region, tombstones, section naming, and the `main.HMI` project header + resource directory.         |
| [`page-and-component-format.md`](page-and-component-format.md) | The page section (`<n>.pa`): page header, object table, and the attribute/marker/event-code encoding of every component. Component type IDs and attribute sets. |
| [`font-zi-format.md`](font-zi-format.md)                       | The embedded font resource (`<n>.zi`): header fields and the flash-cost consequences of the codepoint table (the reason encoding choice dominates font size).   |
| [`image-format.md`](image-format.md)                           | The two image sections per picture: `<n>.is` (the real bitmap) and `<n>.i` (the Editor preview thumbnail).                                                      |
| [`section-checksum.md`](section-checksum.md)                   | The exact page, manifest and mirrored-directory checksum algorithms, plus the append-only update rule.                                                          |
| [`test-vectors.md`](test-vectors.md)                           | Real section bytes with their stored checksums, plus differential test pairs, so an implementation can be verified.                                             |
| [`methodology.md`](methodology.md)                             | How the format was decoded and how to generate `.HMI` files safely (clone-and-patch, round-trip proof, Editor-as-oracle).                                       |
| [`driving-the-editor.md`](driving-the-editor.md)               | The operator's companion: editor settings, component-model limits, fonts and flash budgeting, safe generation, and automating the GUI-only steps.               |

The documents above are the reference. Two directories carry the work behind them:

| Directory                         | What it holds                                                                                                                                                                  |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [`research/`](research/README.md) | The tooling that decoded the format: the reference reader/writer, the checksum solvers, the Editor drivers and the Frida trace. Research code, not a library — and not a spec. |
| [`samples/`](samples/README.md)   | The Editor working directory: measured data and calibration. The multi-gigabyte sample `.HMI` files are not shipped and are not needed.                                        |

## Using it

- **Read an `.HMI`:** `python -m research.nextion_hmi_binary path/to/project.HMI` lists the sections
  and proves the round-trip.
- **Write one:** [`research/nextion_hmi_writer.py`](research/nextion_hmi_writer.py) authors pages and
  components by clone-and-patch on top of the format core.
- **Drive the editor:** [`driving-the-editor.md`](driving-the-editor.md) is the operator's guide;
  [`research/editor_control.py`](research/editor_control.py) and
  [`research/capture_screenshots.py`](research/capture_screenshots.py) automate the GUI-only steps.

Install and calibration notes are in [`SETUP.md`](SETUP.md). The test suite is stdlib-only:
`python -m unittest research.test_vectors`.

## The shape of an `.HMI` in one picture

```
offset 0x000000  ┌───────────────────────────────────────────────┐
                 │ u32 section_count                             │
                 │ section_count × 28-byte directory records     │  <- the only thing near the start
                 │   { name[16], start, size, deleted, rsvd[3] } │
                 │ u32 directory_checksum                        │
offset 0x080000  │ byte-identical directory + checksum mirror    │
                 │ ... unused ...                                │
offset 0x700000  ├───────────────────────────────────────────────┤  <- payload always begins at 7 MiB
                 │ main.HMI    (project header + resource list)   │
                 │ Program.s   (global startup code, plain text)  │
                 │ 0.pa 1.pa … (pages: header + components)       │
                 │ 0.zi 1.zi … (fonts, embedded verbatim)         │
                 │ 0.is 0.i …  (images: real bitmap + thumbnail)  │
                 │ ... appended older/deleted section versions ...│
                 └───────────────────────────────────────────────┘
```

## Five things that surprise people

1. **File size means nothing.** The payload always starts at `0x700000` (7 MiB). An empty project is
   ~8 MB on disk while holding well under 1 MB of real content. Never infer anything from file size.
2. **The file never shrinks.** Sections are **tombstoned** (first byte of the name zeroed) and new
   versions are **appended**, FAT-style. Two projects with byte-identical live content can differ in
   size purely by how they were edited.
3. **Resources are referenced by index, not name.** A component says `font=0`, `pic=3`. There is no
   name resolution. **Resource order is a hard contract** — reorder fonts or images and every
   reference silently points at the wrong thing.
4. **There are three checksum wrappers.** Pages, `main.HMI`, and the mirrored top-level directory use
   the same CRC-32/MPEG-2 table with different input conventions. Every changed directory must be
   mirrored and its checksum recomputed.
5. **Text does not wrap, it clips.** Not a container fact, but it shapes every design that uses the
   container: a `Text` component truncates at its box edge and never word-wraps.

## Confidence legend

Findings are marked throughout:

- **Confirmed** — verified directly against Editor 1.68.1.3034 output, and where possible reproduced
  byte-for-byte from the reference files whose vectors appear in [`test-vectors.md`](test-vectors.md).
- **Uncertain** — inferred, partially understood, or observed but not fully explained. These are
  called out explicitly (e.g. the unidentified page-header bytes and the exact `.i` preview codec).

## Scope and disclaimer

This is an **independent reverse-engineering finding**, offered as a technical reference. It is tied
to **Nextion Editor 1.68.1.3034**; the compiled `.TFT` layout and container details are
version-sensitive, so always pin the Editor version alongside any `.HMI` tooling. Component type IDs
and attribute sets were confirmed on a Nextion **Discovery**-series panel; other series may differ in
which components exist, but the container and encoding mechanics are expected to be the same.

Nextion is a product of Shenzhen TJC Technology. This document is not affiliated with or endorsed by
the vendor and contains no vendor source code.

## License

MIT — see [`LICENSE`](LICENSE). Copyright © 2026 Newmatik GmbH.
