# Methodology: how the format was decoded, and how to write `.HMI` safely

This document records how the format above was reverse-engineered and, more usefully, the discipline
that makes _writing_ `.HMI` files reliable.

## 1. The environment and analysis path

- **Editor:** Nextion Editor **1.68.1.3034** on Windows. Pin this. The compiled `.TFT` layout and the
  `.HMI` container are version-sensitive; "we used Nextion Editor" is not a reproducible statement.
- **`Nextion Editor.exe` is a thin launcher** (~210 KB). The version resource on this file is the
  authoritative Editor version. Read it without launching anything:

  ```powershell
  (Get-Item 'C:\Program Files (x86)\Nextion Editor\Nextion Editor.exe').VersionInfo.FileVersion
  ```

- **The real application is `ACTR.dll`** (~9.9 MB, packed). Differential measurements first exposed
  the checksum's linear structure; targeted runtime tracing then recovered the exact CRC kernel and
  section-specific trailers. The top-level directory checksum was independently confirmed against
  Editor files.
- **There is no CLI.** Compilation and font generation are GUI-only. Design any pipeline so that the
  _only_ manual steps are "compile in the Editor" and "generate fonts in the Editor", and pin their
  outputs by hash.

So the entire method is **black-box**: feed the Editor controlled inputs, read its outputs, and infer
the format from differences.

## 2. The odd-magic-values trick

The single most effective decoding technique. Build an exemplar **by hand in the Editor** and give
every field a **deliberately odd, unique value**:

```
x = 101,  y = 103,  w = 107,  h = 109,  txt = "MAGREF",  txt_maxl = 42
```

Then locate each field in the binary by **searching for its exact value** (`0x65`, `0x67`, `"MAGREF"`,
`0x2A`, …) instead of guessing offsets. Prime-ish, non-round numbers avoid collisions with the many
zeros, default sizes and coordinates already in the file. Every offset in this reference was found
this way or by a controlled A/B save.

## 3. Clone-and-patch — never synthesise

Do **not** build sections from scratch. The reliable way to author components is:

1. Keep one hand-built **exemplar** containing one instance of every component type and every
   attribute you will set.
2. To create a new component, **deep-copy the matching type's record set** from the exemplar and
   **overwrite only the fields you understand** (`x`, `y`, `w`, `h`, `txt`, colours, event code…).
3. Everything you did _not_ touch — every default, and every byte you do not yet understand — is
   **carried through verbatim and stays valid**.

This is what makes writing tractable despite the unidentified page-header bytes, the `flag` word in
the object table, and the opaque `main.HMI` device block: they never have to be understood, only
preserved. Maintain invariants when you patch: `endx = x + w - 1`, `endy = y + h - 1`, `data_size`
equals the section size, the object-table `start`/`size` chain stays packed, and the `main.HMI`
resource count matches its directory.

## 4. The round-trip proof gate

**Before generating anything, prove you can reproduce an existing file byte-for-byte.** Write a
parser and a serializer and require:

```
serialize(parse(file)) == file      # exact bytes, including the 7 MiB lead-in and all tombstones
```

and, per page,

```
serialize_page(parse_page(section)) == section
```

Until that holds for every reference file, the format model is incomplete and you must not generate.
The parser must therefore keep **every** byte it does not classify: the base buffer (the whole file as
a canvas), tombstoned remnants, the object-table `flag` words, and unidentified header regions. The
reference parser does exactly this — it round-trips the container and every page section byte-exact,
which is the licence to start writing.

## 5. Let the Editor be the oracle

The Editor is the ground truth for "is this valid":

1. Open your generated `.HMI` in the Editor.
2. **Save it from the Editor**, re-parse, and diff.
3. If the Editor "corrected" something, your model is wrong there — investigate that exact delta.

Two cautions:

- **Incremental saves diff poorly.** The Editor **appends** rather than rewrites, and only rewrites
  sections it actually touched (see tombstones in [`container-format.md`](container-format.md)). A
  save produces a bigger file with new copies, not a clean minimal diff. The odd-magic-values trick
  and targeted A/B saves are more informative than diffing whole files.
- **A file that opens is not a file that works.** "It parses", "it opens", "it compiles" and "it runs
  on the panel" are four different claims.

## 6. Verification chain — what each step actually proves

State plainly which rows you have and which you do not. Never claim hardware readiness from a
simulator run.

| Step                               | Proves                                      | Does **not** prove                    |
| ---------------------------------- | ------------------------------------------- | ------------------------------------- |
| Generator round-trip is byte-exact | your format model is complete               | anything about the Editor             |
| Editor opens the generated `.HMI`  | container and object structure are valid    | anything at runtime                   |
| Editor re-saves it, diff is empty  | the Editor accepts your bytes as canonical  | —                                     |
| Editor compiles to `.TFT`          | resources, fonts and flash budget are valid | —                                     |
| Editor debug simulator             | layout, navigation, uplink/downlink frames  | UART levels, touch, reset, sleep/wake |
| Real panel                         | everything else                             | —                                     |

## 7. Reproducibility caveats

- **File size is not a fingerprint.** Two byte-identical designs can differ in size by edit history
  (append-only saves, tombstones). Compare _live section content_, not files.
- **Resource order is a contract.** `font=0`, `pic=3` are indexes. Fix the order of fonts and images
  and never reorder; a reorder silently repoints every reference.
- **Fonts and compilation have no CLI.** They can be driven through bounded Windows GUI automation,
  but `.zi` and `.TFT` outputs should be pinned by SHA-256 and checked back into the build evidence.
- **Pin the Editor version** next to any tooling, since the container and `.TFT` layouts move with it.

## 8. What is confirmed vs. still open

Confirmed and reproducible from the reference files:

- container directory, 7 MiB payload origin, tombstones, section naming;
- `main.HMI` header, encoding byte at `0x0D`, resource directory;
- page header, object table, and the full attribute/marker/event-code encoding;
- component type IDs and attribute sets (Discovery panel);
- `.zi` header fields and the UTF-8 codepoint-table flash cost;
- `.is` layout (header + `"bmp"` + full BMP);
- exact page, `main.HMI`, and mirrored-directory checksums for arbitrary lengths;
- an Editor-loaded generated HMI and a zero-error Editor compile.

Still open (documented as uncertain in the relevant files):

- the 8 unidentified page-header bytes at `0x10` and the 16 at `0x28`;
- the object-table `flag` word;
- the exact `.i` preview codec and its varying leading byte;
- the meaning of `.zi` byte `0x04` and the `0x08`/`0x1F` width split;
- the meaning of some opaque top-level directory tail bytes.

## 9. Measurement design: know the domain your model was measured on

A pass rate is not a validation. The column model was checked against **578 same-length variants** and
passed 578/578 — which reads as conclusive but, on its own, is not, for two structural reasons:

- The columns were only ever measured over the **69-byte `txt` window** (`t ∈ [117,185]`), while a page
  spans `t ∈ [0,1730]` — so ~96 % of the columns in use were _extrapolated_, never measured.
- Same-length variants share identical non-`txt` bytes, so any error there is the **same constant** in
  every sample — and the model's additive constant `INIT` is _fitted_ on that same base, so it silently
  **absorbs** exactly such a constant. A same-length test can therefore only exercise the window columns
  plus one fitted constant.

A related confound: at fixed `L`, **trailing distance and absolute position are interchangeable**
(`t = L-1-i` is a bijection), so same-length evidence cannot distinguish "keyed by trailing distance"
from "keyed by absolute position".

**How it was actually resolved — widen the measured domain.** Raise `txt_maxl`, fill `txt` with 200
identical characters, and flip **one bit of one character at a time**: each variant then differs from the
base in exactly one byte, so `G(t) = checksum(variant) ⊕ checksum(base)` is a _direct_ measurement —
200 real columns instead of 69, at a cost of 200 saves.

The outcome vindicated the model rather than breaking it:

- the degree-32 recurrence reproduced, **bit-identical**, from 200 columns, with a disjoint 68-group
  holdout at zero errors;
- a model built purely from **L = 1735** data predicted **200/200** columns measured at **L = 1863**,
  including 131 columns it had never seen — a genuine **cross-length** test that also settles the
  trailing-vs-absolute question;
- and `Z(1863)` extracted from two different same-length pages agreed exactly.

This empirical model was subsequently superseded by a runtime trace that recovered the exact
arbitrary-length CRC wrapper documented in [`section-checksum.md`](section-checksum.md). The experiment
remains useful as a lesson in validating reverse-engineered linear models.

**The transferable lessons:**

- If a model has a parameter (here: length), **the validation set must vary that parameter** — otherwise
  a fitted constant hides every error that is constant within the set.
- Treat a **fitted offset** (`INIT`) derived from the data it is validated on as a sponge for systematic
  error, and design a test it cannot absorb.
- State the _measured domain_ of an empirical model, not just its pass rate. "578/578" without "measured
  only over `t ∈ [117,185]`" reads far stronger than it is.
- When a suspicion like this arises, **widen the measured domain and settle it** — the cheap experiment
  above cost ~20 minutes and converted an assumption into a cross-length fact.
- Beware the trivial-looking control: comparing variants against the **wrong base** (a variant instead of
  the unflipped string) offsets every measurement by a constant and makes a perfectly good sequence look
  non-recurrent. That single mistake cost a full re-analysis here.
