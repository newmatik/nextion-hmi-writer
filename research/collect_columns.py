"""Measures checksum columns G(t) over a WIDE window (long txt) instead of only over 69 bytes.

Why: the previous column model was measured ONLY inside the txt window (t in [117,185], 69 values),
but a page uses t in [0,1730]. ~96 % of the used G(t) is unvalidated extrapolation of the degree-32
recurrence. Equal-length samples cannot expose this in principle (there the error is constant and
hides inside the fitted INIT). Consequence: Z(L) is noise and the checksum remains unsolved for
arbitrary length.

Idea: raise `txt_maxl` and use a LONG txt — the string itself then spans N page bytes. A single-bit
flip at position i directly yields the column at the corresponding trailing distance:
  G(t_i)[bit] = ck(variant) XOR ck(base).

For the RECURRENCE, ONE bit level suffices: we flip bit 0 (0x41 'A' -> 0x40 '@') at each of the N
positions. That is N samples (instead of N*8) and determines the true recurrence degree.

All samples have the SAME page length — a length deviation = faulty sample.

Usage:  python -m research.collect_columns [N]      (default 200)
EMERGENCY STOP: create the file ec.ABORT_FILE.
"""

from __future__ import annotations

import glob
import json
import os
import sys

import research.editor_control as ec

FOLDER = os.path.abspath(os.path.join("samples", "reference"))
BASE_NAME = "starter.HMI"
BASE = os.path.join(FOLDER, BASE_NAME)
UNLOCK = os.path.join(FOLDER, "cya100.HMI")     # to unlock the written samples
RESULT_JSON = os.path.join(FOLDER, ".columns_result.json")
TXT_MAXL = 400
FILL = "A"                                      # 0x41
FLIP = "@"                                      # 0x40 = 0x41 ^ 0x01  -> bit 0 flipped
PREFIX = "cw"


def _save(value: str) -> str:
    ec.select_t0()
    ec.scroll_to_txt()
    ec.set_txt(value)
    full = os.path.join(FOLDER, ec.fresh_name(FOLDER, PREFIX) + ".HMI")
    ec.save_as(full)
    return full


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
    ec.set_run_limits(max_seconds=30 * 60, max_actions=14000)
    print(f"EMERGENCY STOP any time: create file -> {ec.ABORT_FILE}", flush=True)
    ec.setup()
    ec.reset_to_idle()
    try:
        if BASE_NAME.lower() not in ec.title().lower():
            ec.open_file(BASE)
        ec.select_t0()
        names = ec.component_names()
        if len(names) > 2:
            raise SystemExit(f"Stray component: {names}")
        ec.scroll_to_txt()
        ec.set_txtmaxl(TXT_MAXL)

        base_txt = FILL * n
        files: dict[str, str] = {"base": _save(base_txt)}
        for i in range(n):
            chars = list(base_txt)
            chars[i] = FLIP
            files[str(i)] = _save("".join(chars))
            if (i + 1) % 25 == 0:
                print(f"  ... {i + 1}/{n} columns saved", flush=True)
        for _ in range(2):                       # sacrificial saves protect the last real sample
            try:
                _save(base_txt)
            except Exception:
                pass
    except ec.EditorAborted as e:
        print(f"ABORTED: {e}", flush=True)
        return 2

    ec.open_file(UNLOCK)                         # unlocks all samples

    # Read back: all samples MUST have the same length.
    recs = {}
    for key, path in files.items():
        r = ec.read_page_section(path) if os.path.exists(path) else None
        if r:
            recs[key] = (r[2], r[1])             # (len, ck)
    if "base" not in recs:
        print("Base sample missing — abort.")
        return 1
    L0, ck0 = recs["base"]
    good = {k: ck for k, (L, ck) in recs.items() if L == L0}
    print(f"Base length={L0}  usable samples={len(good)}/{len(files)}")

    cols = {}
    for i in range(n):
        k = str(i)
        if k in good:
            cols[i] = good[k] ^ ck0              # G(t_i)[bit0]
    print(f"measured columns (bit 0): {len(cols)}/{n}")
    payload = {"base_len": L0, "base_ck": ck0, "n": n, "fill": FILL, "flip": FLIP,
               "columns_bit0": {str(i): v for i, v in sorted(cols.items())}}
    with open(RESULT_JSON, "w") as fh:
        json.dump(payload, fh, indent=1)
    print("Result ->", RESULT_JSON)
    for f in glob.glob(os.path.join(FOLDER, PREFIX + "*.HMI")):
        pass                                     # raw samples stay (gitignored) for re-checks
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
