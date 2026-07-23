# `samples/` — the Editor working directory

This is where the [research tools](../research/README.md) read their inputs and write their sample
`.HMI` files. It doubles as the Editor's working directory during a collection run.

## What is committed

| Path                             | What it is                                                       |
| -------------------------------- | ---------------------------------------------------------------- |
| `checksum_model.json`            | The solved column model (`nextion_checksum_solve.py` writes it). |
| `checksum_txt_values.txt`        | The `txt` sample inputs a collection run steps through.          |
| `reference/.calib*.json`         | Example editor click calibration; recalibrate for geometry.      |
| `reference/.columns_*.json`      | Measured checksum columns `G(t)`.                                |
| `reference/.zlength_result.json` | Measured `Z(L)` support points over consecutive page lengths.    |

## Where a run writes

The collectors do not all write to the same place, which matters when you go looking for the output:

| Directory           | Written by                                                                            |
| ------------------- | ------------------------------------------------------------------------------------- |
| `reference/`        | `auto_collect*`, `ck_zgrid`, `collect_columns`, `collect_zlength`, `clipboard_feeder` |
| `checksum_samples/` | `collect_checksum_samples`, `watch_checksum_samples`                                  |

Neither directory's `.HMI` files are committed, and both are created on first run.

## What is not

The sample `.HMI` files themselves. A collection run produces several hundred of them at ~7 MB each —
several gigabytes of regenerable measurement scratch — so `.HMI` anywhere under `samples/` is
gitignored.

**None of it is needed to use this repository.** The format reference stands on its own, and
[`test-vectors.md`](../test-vectors.md) carries real section bytes inline precisely so an
implementation can be verified without them.

## Re-running a collection

You need a base `.HMI` to probe. Build one by hand in Nextion Editor 1.68.1.3034 — a single page with
one Text component `t0` is enough — save it into `reference/`, and point the driver at it:

```text
python -m research.auto_collect_heal --auto
```

Two Editor behaviours dictate the file naming, and both are load-bearing:

- The Editor **locks the open project exclusively**, so most collectors write each variant via
  _Save as_ under a fresh name rather than overwriting. (The older `collect_checksum_samples.py`
  instead overwrites the base with _Ctrl+S_ and copies each result into `checksum_samples/`.)
- A run therefore needs a second, clean file to reload at the end to unlock everything it wrote. The
  drivers refer to that unlock file by name (`cya100.HMI` in `collect_columns.py`); any pristine base
  will do — adjust the constant to whatever you saved.

The calibration in `reference/.calib*.json` assumes a maximized Editor on a 1800x1130 screen. On any
other geometry, delete it and recalibrate rather than editing the coordinates.
