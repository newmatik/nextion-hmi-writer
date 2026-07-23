"""Determines the length-dependent base term Z(L) of the Nextion section checksum.

The checksum is linear:  ck(page) = Z(L) XOR SUM_{byte i>=4, set bit b} G[len-1-i][b].
The column model G is already verified in BOTH directions (nextion_checksum_model). Only Z(L) is
missing = checksum of a zero payload of length L. Z(L) obeys the same linear recurrence (degree 32)
as G (the injection is controllable). Hence for every L:

    Z(1735 + m) = < trans(m), v >   with v = (Z(1735), ..., Z(1766))   (32 consecutive unknowns)

where trans(m) is the recurrence stepped forward m times (trans(i)=e_i for i<32,
trans(m)=XOR_i c_i*trans(m-32+i) for m>=32). From many length support points (zga grid +
csa references) v is solved per output bit over GF(2). Z(L), and with it the checksum for ANY
length, is then computable.

Usage:  python -m research.nextion_checksum_zsolve [folder]
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

import numpy as np

from research.nextion_checksum_model import build, page_and_ck

BASE_L = 1735


def gf2_solve(rows, rhs, nunk):
    """Solves GF(2) system (rows: bit masks over nunk unknowns, rhs: 0/1). Returns solution or None."""
    M = list(rows)
    R = list(rhs)
    where = [-1] * nunk
    r = 0
    for col in range(nunk):
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
    # consistency
    for k in range(len(M)):
        if M[k] == 0 and R[k]:
            return None
    sol = 0
    for col in range(nunk):
        if where[col] >= 0 and R[where[col]]:
            sol |= 1 << col
    return sol


def main():
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("samples/reference")
    cap = int(sys.argv[2]) if len(sys.argv) > 2 else 10 ** 9
    m = build(folder, "cya")
    c, d = m.c, m.d
    print(f"Column model: d={d} base_ck=0x{m.base_ck:08X}  (length cap={cap})", flush=True)

    files = sorted(glob.glob(str(folder / "zg*.HMI")) + glob.glob(str(folder / "csa*.HMI")))
    pages = {}  # L -> (numpy uint8 page, ck)
    maxlen = BASE_L
    for f in files:
        try:
            pc = page_and_ck(f)
        except PermissionError:
            continue
        if not pc:
            continue
        pg, ck = pc
        L = len(pg)
        if L == BASE_L or L > cap:
            continue
        pages.setdefault(L, (np.frombuffer(pg, dtype=np.uint8), ck))
        maxlen = max(maxlen, L)
    print(f"Pages read: {len(pages)}, maxlen={maxlen}", flush=True)

    # Unroll columns G[t][b] directly via lane recurrence (fast, no dict/function calls).
    # Recurrence:  G[t-d] = XOR_i c_i G[t-i]   (=> downwards).  With c[0]=1 upwards:
    #              G[t] = G[t-d] XOR XOR_{i=1}^{d-1} c_i G[t-i].
    maxt = maxlen - 1
    T0 = m.T0
    nC = m._nC
    g = [[0] * (maxt + 1) for _ in range(8)]
    for b in range(8):
        gb = g[b]
        for p in range(nC):  # insert measured support values
            t = T0 - p
            if 0 <= t <= maxt:
                gb[t] = m._s[p][b]
        for tl in range(T0 - nC, -1, -1):  # downwards to 0
            v = 0
            for i in range(d):
                if c[i]:
                    v ^= gb[tl + d - i]
            gb[tl] = v
        for t in range(T0 + 1, maxt + 1):  # upwards to maxt
            v = gb[t - d]
            for i in range(1, d):
                if c[i]:
                    v ^= gb[t - i]
            gb[t] = v
    Gcol = [np.array(g[b], dtype=np.uint32) for b in range(8)]
    # Sanity: the fast lane recurrence must agree with the verified model m.G.
    for t in (0, 50, 117, 150, 188, 200, 500, 2786):
        if t <= maxt:
            ref = m.G(t)
            if any(int(Gcol[b][t]) != ref[b] for b in range(8)):
                print(f"!! Gcol deviates from m.G at t={t} -> index error.")
                return 1
    print("Gcol precomputed (agrees with m.G).", flush=True)

    def Zof_np(arr, ck):
        L = len(arr)
        trailing = (L - 1) - np.arange(L, dtype=np.int64)
        total = 0
        a = arr[4:]
        tr = trailing[4:]
        for b in range(8):
            sel = tr[((a >> b) & 1).astype(bool)]
            if sel.size:
                total ^= int(np.bitwise_xor.reduce(Gcol[b][sel]))
        return ck ^ total

    samples = {BASE_L: m.init}
    for L, (arr, ck) in pages.items():
        samples[L] = Zof_np(arr, ck)
    Ls = sorted(samples)
    print(f"Length support points: {len(Ls)}  (min={Ls[0]}, max={Ls[-1]})", flush=True)

    # trans(m) up to max m, iteratively.
    maxm = Ls[-1] - BASE_L
    trans = [0] * (maxm + 1)
    for i in range(min(32, maxm + 1)):
        trans[i] = 1 << i
    for mm in range(32, maxm + 1):
        v = 0
        for i in range(32):
            if c[i]:
                v ^= trans[mm - 32 + i]
        trans[mm] = v

    # Holdout: csa references (completely different length grid, step 1052) + a few large zga.
    csa_L = [L for L in Ls if BASE_L < L < 7000]      # 2787/3839/4891
    zga_L = [L for L in Ls if L >= 7000]
    hold = csa_L + zga_L[-4:]
    fit = [L for L in Ls if L not in hold]
    print(f"Fit: {len(fit)} lengths, holdout: {len(hold)} (csa among them: {csa_L})", flush=True)

    v_bits = []
    for j in range(32):
        rows = [trans[L - BASE_L] for L in fit]
        rhs = [(samples[L] >> j) & 1 for L in fit]
        sol = gf2_solve(rows, rhs, 32)
        if sol is None:
            print(f"!! Bit {j}: system inconsistent -> Z recurrence does not fit (model assumption wrong).")
            return 1
        v_bits.append(sol)

    def Z(L):
        m_ = L - BASE_L
        if m_ < 0:
            return None
        while len(trans) <= m_:
            v = 0
            for i in range(32):
                if c[i]:
                    v ^= trans[len(trans) - 32 + i]
            trans.append(v)
        t = trans[m_]
        out = 0
        for j in range(32):
            if bin(t & v_bits[j]).count("1") & 1:
                out |= 1 << j
        return out

    hit = sum(1 for L in hold if Z(L) == samples[L])
    print(f"HOLDOUT Z(L): {hit}/{len(hold)} correct "
          f"({'PASSED' if hit == len(hold) else 'FAILED'})", flush=True)
    print(f"Z(1735)=0x{Z(1735):08X} (expected 0x{m.init:08X})")

    # DEFINITIVE: full checksum of the csa references (were NOT in the fit).
    print("Full checksum of csa references (independent):")
    okc = tot = 0
    for L in csa_L:
        arr, ck = pages[L]
        acc = Z(L) ^ (Zof_np(arr, ck) ^ ck)  # Z_solved XOR sum_G ; sum_G = Zof_np XOR ck
        tot += 1
        good = acc == ck
        okc += good
        print(f"   len={L:6d}  expected 0x{ck:08X} computed 0x{acc:08X}  {'OK' if good else '!! MISMATCH'}")
    print(f"\n==> {'Z(L) FULLY SOLVED' if hit==len(hold) and okc==tot and tot>0 else 'incomplete'} "
          f"(holdout {hit}/{len(hold)}, csa {okc}/{tot})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
