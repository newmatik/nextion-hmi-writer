# `research/` — the tools that decoded the format

This directory holds the working tooling that produced the format reference in the repository root.
It is **research code, not a library**: one-shot scripts, GUI drivers and solvers, kept because the
[methodology](../methodology.md) is only credible if the instruments are inspectable.

One deliberate exception to the English rule: the strings this tooling matches against the **Nextion
Editor's German-locale GUI** — window titles and button captions such as `"Speichern"`, `"Öffnen"`,
`"Ja"` and `"Nein"` — are data, not prose. Translating them would break the automation, so they stay
as they are and are glossed in English where they appear. Running the drivers against an
English-locale Editor means extending those caption lists, not editing the surrounding text.

> **Do not read this directory as a spec.** The docs at the repository root are the spec. These
> scripts are how it was obtained, preserved so the results can be re-derived and challenged.

## Requirements

The solvers are **stdlib-only**. Anything that drives the Editor needs a live **Nextion Editor
1.68.1.3034** on Windows plus:

```text
pip install pyautogui pywinauto pyperclip pygetwindow Pillow   # or: pip install -r ../requirements.txt
pip install frida            # only for trace_nextion_crc.py (x64 Python)
```

`Pillow` is only needed by `capture_screenshots.py`. Run everything from the repository root as a
module, e.g. `python -m research.nextion_checksum_model`. See [`../SETUP.md`](../SETUP.md) for the full
setup and calibration notes.

## The reference implementation

| File                      | Role                                                                                             |
| ------------------------- | ------------------------------------------------------------------------------------------------ |
| `nextion_hmi_binary.py`   | Container/page reader and writer — the executable form of the format docs.                       |
| `nextion_hmi_checksum.py` | The recovered section checksum (CRC-32 kernel `0x04C11DB7`, 4 table rounds per byte).            |
| `nextion_hmi_writer.py`   | Authors pages and components by clone-and-patch on the core; needs a hand-built exemplar `.HMI`. |

`nextion_hmi_binary.py` and `nextion_hmi_checksum.py` are the **canonical** copies. A consuming product may vendor a pinned copy; if it does, that
copy and this one must be diffed whenever either changes.

`test_vectors.py` verifies them against every vector in [`../test-vectors.md`](../test-vectors.md) —
parsed out of the document itself, so the reference and the implementation cannot drift apart. It is
stdlib-only and needs no `.HMI` files:

```text
python -m unittest research.test_vectors
```

## How the checksum fell

The section checksum was the last obstacle to writing `.HMI` files, and it took two independent
attacks. The order below is the order things actually happened.

### 1. Differential measurement — is it even linear?

| File                             | Role                                                                           |
| -------------------------------- | ------------------------------------------------------------------------------ |
| `nextion_checksum_solve.py`      | Proves GF(2)-linearity and solves the checksum from one-bit-flip samples.      |
| `nextion_checksum_recurrence.py` | Same, but for a checksum whose internal state is wider than its 32-bit output. |

`solve` establishes the key fact: the checksum is GF(2)-linear, so `ck(a XOR b) = ck(a) XOR ck(b)` and
every set bit contributes an independent column. Verified against standard CRC-32 (200/200 random
pages) before ever being pointed at the Editor.

### 2. The column model and its length problem

| File                         | Role                                                                |
| ---------------------------- | ------------------------------------------------------------------- |
| `nextion_checksum_verify.py` | Finds the linear matrix recurrence of the column sequence; holdout. |
| `nextion_checksum_model.py`  | Full column model `G`, extrapolated forwards and backwards.         |
| `collect_columns.py`         | Measures `G(t)` over a **wide** window, not just the 69-byte `txt`. |

This is where the honest failure lives, and it is worth reading before trusting any of it.
`collect_columns.py` exists because the first column model was measured **only inside the `txt`
window** (`t in [117,185]`) while a real page uses `t in [0,1730]` — about 96 % of the model was
unvalidated extrapolation. Equal-length samples **cannot** expose that error: it is constant across
them and hides inside the fitted `INIT`.

So the model was correct for one length and wrong everywhere else:

```text
ck(page) = INIT XOR column_sum(page)      # only true at the base length
ck(page) = Z(len) XOR column_sum(page)    # the real rule
```

### 3. Hunting `Z(L)`, the length-dependent base

| File                          | Role                                                               |
| ----------------------------- | ------------------------------------------------------------------ |
| `nextion_checksum_zsolve.py`  | Solves `Z(L)` from length support points.                          |
| `nextion_checksum_zmodel.py`  | Extends the column model to a fully length-independent model.      |
| `nextion_checksum_qsearch.py` | Searches the extra factor `Q` for the **full** minimal polynomial. |
| `ck_zdiag.py`                 | Berlekamp-Massey per output bit — the true recursion degree.       |
| `ck_zgrid.py`, `ck_zsweep.py` | Collect `Z(L)` support points on a length grid.                    |
| `collect_zlength.py`          | Gapless consecutive page lengths in 1-byte steps.                  |

`ck_zdiag` was the diagnostic that mattered: the column model gave degree 32 only on the _injection
subspace_, while `Z(L)` needs the full minimal polynomial. This line of attack was converging but
never closed — see the note below.

### 4. Tracing — what actually settled it

| File                   | Role                                                                      |
| ---------------------- | ------------------------------------------------------------------------- |
| `trace_nextion_crc.py` | Frida-hooks the CRC helpers in the running Editor and records real calls. |

The algebra was slow, so the Editor was asked directly. `ACTR.dll` (~9.9 MB, packed) is loaded via
`achmi.bin`; hooking the two helpers recovered the exact kernel and the section-specific trailers.
That result is `nextion_hmi_checksum.py`, and it is what the writer uses today — **not** the column
model. The algebraic work remains because it proved linearity, bounded the problem, and independently
corroborates the traced kernel.

## Driving the Editor

| File                         | Role                                                                                 |
| ---------------------------- | ------------------------------------------------------------------------------------ |
| `editor_control.py`          | The perceive-act-verify-heal loop. **Start here.** Supersedes the ad-hoc drivers.    |
| `ck_probe.py`                | Reusable probe: set `t0.txt`, save-as per value, reload a clean base.                |
| `validate_nextion_editor.py` | Opens exactly one `.HMI` and reports the title or the popup text, plus a screenshot. |
| `create_nextion_skeleton.py` | Adds fonts and pages to an image template; verifies the result byte-exactly.         |
| `import_nextion_images.py`   | Imports BMPs so the Editor mints its proprietary `.i` previews.                      |
| `capture_screenshots.py`     | Screenshots every page by walking the Page list; config-driven crop geometry.        |

Two facts about the Editor shape all of this:

- **It locks the open file exclusively.** Every variant is therefore written with _Save as_ to a fresh
  name, and the clean base is reloaded at the end to unlock what was written.
- **The overwrite prompt defaults to _No_.** Never blind-press Enter.

`import_nextion_images.py` earns its place: the `.i` preview does not have to be reverse-engineered at
all. Let the pinned Editor mint it once, then keep it. Knowing which battles to skip is part of the
method.

## Collecting samples

| File                          | Role                                                                    |
| ----------------------------- | ----------------------------------------------------------------------- |
| `auto_collect_heal.py`        | **The one that worked.** Self-healing, self-driving collection.         |
| `auto_collect_programs.py`    | Collects via the `Program.s` code editor instead of the `txt` cell.     |
| `auto_collect_cb.py`          | Canvas-free: selects `t0` from the component dropdown.                  |
| `auto_collect.py`             | The original four-click coordinate driver.                              |
| `collect_checksum_samples.py` | Ctrl+S variant that overwrites the base and copies it away.             |
| `watch_checksum_samples.py`   | Semi-automatic: no coordinate automation, watches for saves.            |
| `clipboard_feeder.py`         | Feeds the next `txt` value and auto-advances when a new sample appears. |

The progression is a lesson in GUI automation. Clicking the `txt` cell on the canvas drifts over
hundreds of iterations and silently **adds stray components** — pages grow, `txt='newtxt'`, and the
whole run is poisoned. `auto_collect_cb` made that structurally impossible by using the dropdown;
stray components still appeared roughly 1 in 80. `auto_collect_heal` stopped trying to prevent the
misclick and **repairs** it instead, turning a dead run into a 3-second reload.
`auto_collect_programs` sidesteps the canvas entirely — `Program.s` is a plain text editor, a large
click target, and it carries a checksum with the same function.

## Data

`../samples/` holds the small, meaningful artifacts:

- `checksum_model.json` — the solved column model
- `checksum_txt_values.txt` — the `txt` sample inputs
- `reference/.calib*.json` — Editor click calibration (screen-geometry dependent; recalibrate)
- `reference/.columns_*.json`, `reference/.zlength_result.json` — measurement results

The sample `.HMI` files themselves (~7 MB each, several hundred of them) are **not** in the repository
and are not needed to use the format reference — see [`../samples/README.md`](../samples/README.md).

## Status, honestly

- **Traced kernel:** recovered and verified; this is what writes files today.
- **Column model:** verified in both directions, but only meaningful together with `Z(L)`.
- **`Z(L)` by algebra:** converging, never closed. `collect_columns.py`'s own docstring records the
  dead end — _"Z(L) is noise and the checksum remains unsolved for arbitrary length"_ — which was
  true of the algebraic path at the time and was overtaken by the trace.
- **Read `nextion_checksum_model.py`'s caveat before trusting it.** Its docstring still opens with
  the earlier claim that the model holds for any length; the two findings that contradict it are
  recorded directly beneath.

The calibration coordinates assume a maximized Editor on a 1800x1130 screen. Different geometry means
recalibrating, not adjusting constants.
