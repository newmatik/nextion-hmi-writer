"""Collects consecutive page lengths (1-byte steps) for the Z(L) recurrence.

Lever (verified in phase B): the txt length of a text component changes the `0.pa` length by exactly
1 byte per character. Per round: load the base, raise txt_maxl, set the txt to `A`*N for every target
length N and save as a fresh `.HMI`; then reload the base (unlocks everything) and verify the lengths
back. Only samples with the correct length offset count; deviating ones are refilled in the next
round, until the whole sequence is gaplessly +1.

Usage:  python -m research.collect_zlength <N0> <N1> [prefix]     (N in [N0, N1))
Result: samples/reference/<prefix><N>.HMI  per valid length.
"""

from __future__ import annotations

import collections
import glob
import json
import os
import sys

import research.editor_control as ec

FOLDER = os.path.abspath(os.path.join("samples", "reference"))
BASE_NAME = "starter.HMI"                   # pristine 1735-byte base (only page0 + t0)
BASE = os.path.join(FOLDER, BASE_NAME)
EXPECTED_COMPONENTS = 2                     # page0 + t0; more = stray component -> alarm
RESULT_JSON = os.path.join(FOLDER, ".zlength_result.json")
TXT_MAXL = 400


def ensure_base() -> None:
    """Load the base only if it is not loaded anyway (prevents self-locking errors)."""
    if BASE_NAME.lower() not in ec.title().lower():
        ec.open_file(BASE)


def assert_no_stray() -> None:
    """Safety net: the page may only contain page0 + t0 (no stray component)."""
    names = ec.component_names()
    if len(names) > EXPECTED_COMPONENTS:
        raise SystemExit(f"Stray component detected: {names} — check base/control!")


def _save_one(N: int, prefix: str) -> str:
    ec.select_t0()
    ec.scroll_to_txt()
    ec.set_txt("A" * N)
    name = ec.fresh_name(FOLDER, prefix)
    full = os.path.join(FOLDER, name + ".HMI")
    ec.save_as(full)
    return full


def collect(n0: int, n1: int, prefix: str = "zc") -> dict:
    """ONE sequential, incremental pass (n0->n1). No refill.

    Why incremental and without refill: set_txt is reliable for small changes (N-1 -> N), but
    error-prone for large jumps (base 69 -> target). Refills of a single target always start from the
    base (large jump) and therefore fail reproducibly. Hence: run through gaplessly once; faulty
    individual samples become gaps. The Z solver later takes the longest consecutive run. n0 should be
    close to the base txt length (~69), so that the first step is small as well.
    """
    ec.reset_to_idle()
    ensure_base()
    ec.select_t0()
    assert_no_stray()
    ec.scroll_to_txt()
    ec.set_txtmaxl(TXT_MAXL)
    targets = list(range(n0, n1))
    pending: dict[int, str] = {}
    fails = 0
    for i, N in enumerate(targets):
        try:
            if not ec.geometry_ok():
                ec.ensure_geometry()
            pending[N] = _save_one(N, prefix)
            fails = 0
        except ec.EditorAborted:
            raise                                  # abort/budget/fail-fast: exit now, do NOT keep clicking
        except Exception as e:                     # a single sample must not kill the run ...
            fails += 1
            print(f"  ! sample N={N} skipped: {type(e).__name__}", flush=True)
            if fails >= 5:                         # ... but a series of failures may (environment broken)
                raise ec.EditorAborted(f"{fails} failures in a row — run aborted")
            try:
                ec.reset_to_idle()
            except Exception:
                pass
        if (i + 1) % 20 == 0:
            print(f"  ... {i + 1}/{len(targets)} saved", flush=True)
    for _ in range(2):                             # two sacrificial saves protect the last real targets
        try:
            _save_one(n1 - 1, prefix + "x")
        except Exception:
            pass
    ensure_base()                                  # unlocks all samples
    lens, offs = {}, []
    for N, full in pending.items():
        L = ec.page_len(full) if os.path.exists(full) else -1
        lens[N] = (full, L)
        if L > 0:
            offs.append(L - N)
    if not offs:
        print("  NO readable samples.")
        return {}
    print(f"  Offset mode={collections.Counter(offs).most_common(1)[0][0]}", flush=True)
    # Find the LONGEST run in which both N and the length grow by exactly 1. This is robust against
    # stray components sneaked in along the way (which break the delta-1 thread): the longest clean
    # section survives, instead of a modal value picking the wrong section.
    Ns = sorted(N for N in lens if lens[N][1] > 0)
    best, cur = [], []
    for N in Ns:
        if cur and N == cur[-1] + 1 and lens[N][1] == lens[cur[-1]][1] + 1:
            cur.append(N)
        else:
            cur = [N]
        if len(cur) > len(best):
            best = cur[:]
    result = {N: lens[N] for N in best}
    for N, (full, L) in lens.items():              # delete files not belonging to the longest run
        if N not in result and os.path.exists(full):
            try:
                os.remove(full)
            except OSError:
                pass
    print(f"  valid (longest run): {len(result)}/{len(pending)}  "
          f"N={best[0] if best else '-'}..{best[-1] if best else '-'}", flush=True)
    return result


def main() -> int:
    n0 = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    n1 = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    prefix = sys.argv[3] if len(sys.argv) > 3 else "zc"
    # HARD limits: a run must never keep clicking forever. Emergency stop possible at any time by
    # creating the file ec.ABORT_FILE (e.g. `type nul > %TEMP%\nextion_stop.txt`).
    ec.set_run_limits(max_seconds=25 * 60, max_actions=8000)
    print(f"EMERGENCY STOP any time: create file -> {ec.ABORT_FILE}", flush=True)
    ec.setup()
    ec.reset_to_idle()                             # clean up remnants of interrupted runs
    try:
        res = collect(n0, n1, prefix)
    except ec.EditorAborted as e:
        print(f"ABORTED: {e}", flush=True)
        return 2
    for f in glob.glob(os.path.join(FOLDER, prefix + "x*.HMI")):   # delete the closing dummies
        try:
            os.remove(f)
        except OSError:
            pass
    Ns = sorted(res)
    # determine the longest consecutive run
    best = cur = []
    for N in Ns:
        if cur and N == cur[-1] + 1:
            cur.append(N)
        else:
            cur = [N]
        if len(cur) > len(best):
            best = cur[:]
    print(f"\nDone: {len(Ns)} valid samples; longest consecutive run "
          f"{best[0] if best else '-'}..{best[-1] if best else '-'} ({len(best)})")
    payload = {"prefix": prefix,
               "samples": {str(N): {"file": os.path.basename(res[N][0]), "len": res[N][1]} for N in Ns},
               "longest_run": [best[0], best[-1]] if best else []}
    with open(RESULT_JSON, "w") as fh:
        json.dump(payload, fh, indent=1)
    print(f"Result -> {RESULT_JSON}")
    gaps = [N for N in range(n0, n1) if N not in res]
    print(f"GAPS at N={gaps}" if gaps else "Gapless.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
