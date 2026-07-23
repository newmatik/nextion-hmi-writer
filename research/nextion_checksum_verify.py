"""Solves the Nextion section checksum via matrix recurrence and verifies it by holdout.

The checksum is GF(2)-linear with a large internal state (which is why the simple rank-32 matrix
method in ``nextion_checksum_solve.py`` fails). Every single-bit variant of the txt base yields a
column col(pos,bit) = ck(variant) XOR ck(base). The column sequence over the trailing position follows
a linear matrix recurrence col(t+d) = XOR c_i*col(t+i), which is extrapolated.

Verification:
  1. Self-check: the recurrence found reproduces ALL measured columns.
  2. Holdout: the recurrence is determined on the FIRST K positions only and must correctly predict
     the remaining measured positions (genuine extrapolation, not a mere fit).

Usage:  python -m research.nextion_checksum_verify [folder] [prefix]
"""

from __future__ import annotations

import glob
import os
import struct
import sys
from pathlib import Path

from research.nextion_checksum_recurrence import load_columns


def rank(vs):
    piv = {}
    for v in vs:
        x = v
        for k in sorted(piv):
            if (x >> k) & 1:
                x ^= piv[k]
        if x:
            piv[x.bit_length() - 1] = x
    return len(piv)


def find_recurrence(C, maxd):
    """Shortest matrix recurrence C[t+d] = XOR_i c_i C[t+i] over GF(2). C: list of [8 ints]."""
    for d in range(1, maxd):
        rows, rhs = [], []
        for t in range(len(C) - d):
            for bit in range(8):
                for ob in range(32):
                    mask = 0
                    for i in range(d):
                        if (C[t + i][bit] >> ob) & 1:
                            mask |= 1 << i
                    rows.append(mask)
                    rhs.append((C[t + d][bit] >> ob) & 1)
        M, R = list(rows), list(rhs)
        where = [-1] * d
        r = 0
        for col in range(d):
            piv = next((k for k in range(r, len(M)) if (M[k] >> col) & 1), -1)
            if piv < 0:
                continue
            M[r], M[piv] = M[piv], M[r]
            R[r], R[piv] = R[piv], R[r]
            for k in range(len(M)):
                if k != r and (M[k] >> col) & 1:
                    M[k] ^= M[r]
                    R[k] ^= R[r]
            where[col] = r
            r += 1
        if any(w < 0 for w in where):
            continue
        if any(M[k] == 0 and R[k] != 0 for k in range(len(M))):
            continue
        c = [R[where[i]] for i in range(d)]
        seq = [list(x) for x in C[:d]]
        ok = True
        for t in range(d, len(C)):
            nxt = [0] * 8
            for bit in range(8):
                v = 0
                for i in range(d):
                    if c[i]:
                        v ^= seq[t - d + i][bit]
                nxt[bit] = v
            if nxt != C[t]:
                ok = False
                break
            seq.append(nxt)
        if ok:
            return d, c
    return None, None


def predict_next(seq, d, c):
    nxt = [0] * 8
    for bit in range(8):
        v = 0
        for i in range(d):
            if c[i]:
                v ^= seq[len(seq) - d + i][bit]
        nxt[bit] = v
    return nxt


def main():
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("samples/reference")
    prefix = sys.argv[2] if len(sys.argv) > 2 else "cya"

    cols, base_ck, lo, base = load_columns(folder, prefix)
    npos = max(p for p, _ in cols) + 1
    COL = [[cols.get((p, b)) for b in range(8)] for p in range(npos)]
    block = []
    for p in range(npos):
        if all(COL[p][b] is not None for b in range(8)):
            block.append(p)
        else:
            break
    C = [COL[p] for p in block]
    print(f"base_ck = 0x{base_ck:08X}   window_start(lo) = {lo}   base_len = {len(base)}")
    print(f"complete contiguous positions: {len(C)} (of {npos})")

    print("cumulative rank of the columns:")
    for m in range(4, len(C) + 1, 8):
        print(f"   {m:3d} pos -> rank {rank([C[p][b] for p in range(m) for b in range(8)])}")

    d, c = find_recurrence(C, min(len(C) - 2, 200))
    if d is None:
        print("NO recurrence found — more contiguous positions needed.")
        return 1
    print(f"\nrecurrence degree (full fit): d = {d}")

    # ---- HOLDOUT ----
    K = max(2 * d + 6, int(len(C) * 0.6))
    K = min(K, len(C) - 2)
    dh, ch = find_recurrence(C[:K], min(K - 2, 200))
    if dh is None:
        print(f"Holdout: no recurrence on the first {K} positions.")
    else:
        seq = [list(x) for x in C[:dh]]
        for _ in range(dh, len(C)):
            seq.append(predict_next(seq, dh, ch))
        hit = sum(1 for t in range(K, len(C)) if seq[t] == C[t])
        tot = len(C) - K
        verdict = "== PASSED" if hit == tot else "!! FAILED"
        print(f"HOLDOUT: fit pos 0..{K-1} (d={dh}); predicts pos {K}..{len(C)-1}: "
              f"{hit}/{tot}  {verdict}")

    # ---- compute() same length + self-check ----
    T0 = len(base) - 1 - lo
    seq_cache = [list(x) for x in C]

    def col_for_trailing(t):
        p = T0 - t
        if p < 0:
            return None
        while len(seq_cache) <= p:
            seq_cache.append(predict_next(seq_cache, d, c))
        return seq_cache[p]

    def compute(page):
        if len(page) != len(base):
            return None
        ck = base_ck
        # Start at 4: bytes 0..3 are the stored checksum field, not payload. Independent reference
        # pages carry a different stored checksum, so counting byte 0 would make compute() bail and
        # the cross-check silently validate nothing.
        for i in range(4, len(page)):
            db = page[i] ^ base[i]
            if not db:
                continue
            col = col_for_trailing(len(page) - 1 - i)
            if col is None:
                return None
            for bit in range(8):
                if (db >> bit) & 1:
                    ck ^= col[bit]
        return ck

    ok = tot = 0
    for p in block:
        for b in range(8):
            var = bytearray(base)
            var[lo + p] ^= (1 << b)
            tot += 1
            if compute(bytes(var)) == (base_ck ^ COL[p][b]):
                ok += 1
    print(f"self-check (all single-bit variants): {ok}/{tot}")

    # ---- Cross-check against independent reference pages of the same length ----
    def page_and_ck(path):
        raw = open(path, "rb").read()
        n = struct.unpack_from("<I", raw, 0)[0]
        for i in range(n):
            o = 4 + i * 28
            if raw[o + 24] == 0 and raw[o:o + 16].split(b"\x00")[0] == b"0.pa":
                s, z = struct.unpack_from("<II", raw, o + 16)
                pg = raw[s:s + z]
                return pg, struct.unpack_from("<I", pg, 0)[0]
        return None

    print("cross-check independent reference pages (same length, different editor run):")
    any_ref = False
    for f in sorted(glob.glob(str(folder / "csa*.HMI")) + glob.glob(str(folder / "start*.HMI"))):
        try:
            pc = page_and_ck(f)
        except PermissionError:
            continue
        if not pc or len(pc[0]) != len(base):
            continue
        got = compute(pc[0])
        if got is None:
            continue
        any_ref = True
        mark = "OK" if got == pc[1] else "!! MISMATCH"
        print(f"   {os.path.basename(f):14s} expected 0x{pc[1]:08X} computed 0x{got:08X}  {mark}")
    if not any_ref:
        print("   (no independent reference of the same length available)")

    print(f"\n==> base_ck=0x{base_ck:08X}, d={d}. "
          f"{'VERIFIED' if ok == tot else 'NOT fully verified'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
