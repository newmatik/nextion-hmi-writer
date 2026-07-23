"""Diagnostic: determines the linear complexity (true recurrence degree) of the Z(L) sequence on the
zga grid (consecutive in k, constant step size) via Berlekamp-Massey per output bit.

Tells us the degree of the TRUE recurrence (the column model only yielded degree 32 on the
injection subspace; Z(L) needs the full minimal polynomial).
"""

from __future__ import annotations

import glob
import sys
from pathlib import Path

try:
    import numpy as np
except ImportError as exc:  # numpy ships only with the optional [solvers] extra
    raise SystemExit("numpy is required for this diagnostic; install it with: pip install -e .[solvers]") from exc

from research.nextion_checksum_model import build, page_and_ck

BASE_L = 1735


def berlekamp_massey_gf2(seq):
    """Linear complexity of a 0/1 sequence (Berlekamp-Massey over GF(2))."""
    n = len(seq)
    C = [1] + [0] * n
    B = [1] + [0] * n
    L, mdist = 0, 1
    for i in range(n):
        d = seq[i]
        for j in range(1, L + 1):
            d ^= C[j] & seq[i - j]
        if d:
            T = C[:]
            for j in range(n - mdist + 1):
                C[j + mdist] ^= B[j]
            if 2 * L <= i:
                L = i + 1 - L
                B = T
                mdist = 1
            else:
                mdist += 1
        else:
            mdist += 1
    return L


def main():
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("samples/reference")
    m = build(folder, "cya")
    c, d = m.c, m.d

    pages = {}
    maxlen = BASE_L
    for f in sorted(glob.glob(str(folder / "zga*.HMI"))):
        try:
            pc = page_and_ck(f)
        except PermissionError:
            continue
        if not pc:
            continue
        pg, ck = pc
        pages.setdefault(len(pg), (np.frombuffer(pg, dtype=np.uint8), ck))
        maxlen = max(maxlen, len(pg))
    print(f"zga pages: {len(pages)}, maxlen={maxlen}", flush=True)

    # fast Gcol
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
    print("Gcol done.", flush=True)

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

    Ls = sorted(pages)
    steps = [Ls[i + 1] - Ls[i] for i in range(len(Ls) - 1)]
    print(f"Lengths {Ls[0]}..{Ls[-1]}, uniform step={len(set(steps))==1} (step={steps[0] if steps else '-'})")
    Zseq = [Zof(*pages[L]) for L in Ls]
    # linear complexity per output bit
    comps = [berlekamp_massey_gf2([(z >> j) & 1 for z in Zseq]) for j in range(32)]
    print("linear complexity per bit:", comps)
    print("max linear complexity:", max(comps), " (number of support points:", len(Zseq), ")")
    if max(comps) > len(Zseq) // 2:
        print("!! degree > half the support points -> more grid points needed, degree not established.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
