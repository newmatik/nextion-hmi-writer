"""Finds the FULL minimal polynomial of the Z(L) recurrence by searching for a small extra factor Q.

The column model yields P_col (degree 32) = the minimal polynomial on the injection subspace only.
Z(L) obeys the full minimal polynomial P_A = P_col * Q (Q small, since Berlekamp-Massey showed degree
~32-34). For every candidate Q (monic, degree g) the recurrence is built and v (the D consecutive
initial values) is solved from the length support points over GF(2). Consistency + holdout => the
correct Q. Z(L), and with it the checksum for arbitrary length, is then computable. PURELY
COMPUTATIONAL (no Editor GUI).

Usage:  python -m research.nextion_checksum_qsearch [folder] [maxlen]
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

import numpy as np

from research.nextion_checksum_model import build, page_and_ck

BASE_L = 1735


def gf2_solve(rows, rhs, nunk):
    M, R = list(rows), list(rhs)
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
    for k in range(len(M)):
        if M[k] == 0 and R[k]:
            return None
    sol = 0
    for col in range(nunk):
        if where[col] >= 0 and R[where[col]]:
            sol |= 1 << col
    return sol


def clmul(a, b):
    """Carryless (GF(2)) polynomial multiplication."""
    r = 0
    while b:
        if b & 1:
            r ^= a
        a <<= 1
        b >>= 1
    return r


def build_trans(pa_mask, D, maxm):
    """trans(m): bit mask over v[0..D-1] for Z(1735+m). Recurrence from P_A."""
    trans = [0] * (maxm + 1)
    for i in range(min(D, maxm + 1)):
        trans[i] = 1 << i
    pa_low = [(pa_mask >> i) & 1 for i in range(D)]  # coefficients p_i (i<D)
    for mm in range(D, maxm + 1):
        v = 0
        base = mm - D
        for i in range(D):
            if pa_low[i]:
                v ^= trans[base + i]
        trans[mm] = v
    return trans


def main():
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("samples/reference")
    cap = int(sys.argv[2]) if len(sys.argv) > 2 else 70000
    m = build(folder, "cya")
    c, d = m.c, m.d
    Pcol = (1 << 32) | sum((c[i] & 1) << i for i in range(32))  # x^32 + sum c_i x^i
    print(f"P_col=0x{Pcol:X} (degree 32), length cap={cap}", flush=True)

    # Read pages (<= cap) + fast Gcol + Z(L).
    pages = {}
    maxlen = BASE_L
    for f in sorted(glob.glob(str(folder / "zg*.HMI")) + glob.glob(str(folder / "csa*.HMI"))):
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
    maxt = maxlen - 1
    T0, nC = m.T0, m._nC
    g = [[0] * (maxt + 1) for _ in range(8)]
    for b in range(8):
        gb = g[b]
        for p in range(nC):
            t = T0 - p
            if 0 <= t <= maxt:
                gb[t] = m._s[p][b]
        for tl in range(T0 - nC, -1, -1):
            v = 0
            for i in range(d):
                if c[i]:
                    v ^= gb[tl + d - i]
            gb[tl] = v
        for t in range(T0 + 1, maxt + 1):
            v = gb[t - d]
            for i in range(1, d):
                if c[i]:
                    v ^= gb[t - i]
            gb[t] = v
    Gcol = [np.array(g[b], dtype=np.uint32) for b in range(8)]

    def Zof(arr, ck):
        L = len(arr)
        tr = ((L - 1) - np.arange(L, dtype=np.int64))[4:]
        a = arr[4:]
        total = 0
        for b in range(8):
            sel = tr[((a >> b) & 1).astype(bool)]
            if sel.size:
                total ^= int(np.bitwise_xor.reduce(Gcol[b][sel]))
        return ck ^ total

    samples = {BASE_L: m.init}
    for L, (arr, ck) in pages.items():
        samples[L] = Zof(arr, ck)
    Ls = sorted(samples)
    print(f"Support points: {len(Ls)} (up to {Ls[-1]})", flush=True)

    csa_L = [L for L in Ls if BASE_L < L < 7000]
    zga_L = [L for L in Ls if L >= 7000]
    hold = csa_L + zga_L[-3:]
    fit = [L for L in Ls if L not in hold]
    maxm = Ls[-1] - BASE_L

    for g_deg in range(1, 8):
        for qlow in range(1 << g_deg):
            Q = (1 << g_deg) | qlow
            Pa = clmul(Pcol, Q)
            D = 32 + g_deg
            if len(fit) < D:
                continue
            trans = build_trans(Pa, D, maxm)
            ok = True
            v_bits = []
            for j in range(32):
                rows = [trans[L - BASE_L] for L in fit]
                rhs = [(samples[L] >> j) & 1 for L in fit]
                sol = gf2_solve(rows, rhs, D)
                if sol is None:
                    ok = False
                    break
                v_bits.append(sol)
            if not ok:
                continue

            def Z(L, trans=trans, v_bits=v_bits, D=D):
                mm = L - BASE_L
                t = trans[mm]
                out = 0
                for j in range(32):
                    if bin(t & v_bits[j]).count("1") & 1:
                        out |= 1 << j
                return out

            hit = sum(1 for L in hold if Z(L) == samples[L])
            if hit == len(hold):
                print(f"\n*** FOUND: Q=0x{Q:X} (degree {g_deg}), P_A degree {D}. "
                      f"HOLDOUT {hit}/{len(hold)} PASSED. ***", flush=True)
                print(f"Z(1735)=0x{Z(BASE_L):08X} (expected 0x{m.init:08X})")
                # definitive csa sample
                allok = True
                for L in csa_L:
                    good = Z(L) == samples[L]
                    allok &= good
                    print(f"   csa len={L}: {'OK' if good else 'MISMATCH'}")
                print("==> Z(L) FULLY SOLVED." if allok else "==> csa mismatch.")
                return 0
    print("No matching Q found up to degree 7.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
