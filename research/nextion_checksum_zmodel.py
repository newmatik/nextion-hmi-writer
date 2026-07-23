"""Solves the length-dependent base Z(L) and extends the column model into a FULLY
length-independent checksum model.

Background: `ChecksumModel` (research/nextion_checksum_model.py) computes

    ck(page) = INIT  XOR  column_sum(page)        , INIT = ck(zero page of the BASE LENGTH) = Z(1735)

That is correct only for the base length. For arbitrary length the rule is

    ck(page) = Z(len(page))  XOR  column_sum(page)

with Z(L) = checksum of a page of length L with payload 0. Z(L) obeys a linear GF(2) recurrence
(minimal polynomial of the byte state operator). From CONSECUTIVE length samples (step 1 byte,
collected with research/collect_zlength.py) this recurrence is determined via Berlekamp-Massey per bit +
GF(2) LCM; Z(L) and the checksum are then computable for ANY length.

Usage:  python -m research.nextion_checksum_zmodel [folder] [--save path.json]
Self-test without Editor:  python -m research.nextion_checksum_zmodel --selftest
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from pathlib import Path

from research.nextion_checksum_model import ChecksumModel, build, page_and_ck

BASE_L = 1735


# --- GF(2) polynomial arithmetic (poly as int, bit i = coefficient of x^i) ----------------------

def clmul(a: int, b: int) -> int:
    r = 0
    while b:
        if b & 1:
            r ^= a
        a <<= 1
        b >>= 1
    return r


def pdeg(p: int) -> int:
    return p.bit_length() - 1


def pmod(a: int, b: int) -> int:
    db = pdeg(b)
    while a and pdeg(a) >= db:
        a ^= b << (pdeg(a) - db)
    return a


def pgcd(a: int, b: int) -> int:
    while b:
        a, b = b, pmod(a, b)
    return a


def pdiv(a: int, b: int) -> int:
    db = pdeg(b)
    q = 0
    while a and pdeg(a) >= db:
        sh = pdeg(a) - db
        q ^= 1 << sh
        a ^= b << sh
    return q


def plcm(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return clmul(pdiv(a, pgcd(a, b)), b)


# --- Berlekamp-Massey: connection polynomial of a 0/1 sequence ---------------------------------

def bm_connection(seq):
    """Connection polynomial C (list, C[0]=1) with sum_{j=0..L} C[j] seq[i-j] = 0 for i>=L."""
    n = len(seq)
    C = [1] + [0] * n
    B = [1] + [0] * n
    L, m = 0, 1
    for i in range(n):
        d = seq[i]
        for j in range(1, L + 1):
            d ^= C[j] & seq[i - j]
        if d:
            T = C[:]
            for j in range(n - m + 1):
                C[j + m] ^= B[j]
            if 2 * L <= i:
                L = i + 1 - L
                B = T
                m = 1
            else:
                m += 1
        else:
            m += 1
    return C[:L + 1]


def minpoly_of_seq(seq) -> int:
    """Minimal polynomial (int) of the sequence = reversal of the connection polynomial."""
    C = bm_connection(seq)
    L = len(C) - 1
    mu = 0
    for i, ci in enumerate(C):
        if ci:
            mu |= 1 << (L - i)          # mu_i = C[L-i]
    return mu                            # mu_0 = C[L], mu_L = C[0] = 1


def solve_z_minpoly(Zseq) -> int:
    """Common minimal polynomial of the 32 bit sequences of Z(L) = LCM of the per-bit minimal polys."""
    mu = 1
    for bit in range(32):
        s = [(z >> bit) & 1 for z in Zseq]
        if any(s):
            mu = plcm(mu, minpoly_of_seq(s))
    return mu


# --- length-independent model -------------------------------------------------------------------

class ZModel:
    """ChecksumModel + Z(L) recurrence -> checksum for arbitrary length."""

    def __init__(self, base_model, mu: int, anchor_L: int, anchor_Z: list):
        self.m = base_model
        self.mu = mu
        self.D = pdeg(mu)
        self.coeff = [(mu >> i) & 1 for i in range(self.D + 1)]   # mu_0..mu_D, mu_0=mu_D=1
        if not self.coeff[0] or not self.coeff[self.D]:
            raise ValueError("Minimal polynomial needs mu_0=mu_D=1 for forward and backward roll")
        # Anchor: D consecutive Z values at L = anchor_L .. anchor_L+D-1
        self._z = {anchor_L + i: anchor_Z[i] for i in range(self.D)}

    def Z(self, L: int) -> int:
        if L in self._z:
            return self._z[L]
        D, co = self.D, self.coeff
        if L > max(self._z):
            k = max(self._z) + 1
            while k <= L:
                v = 0
                for i in range(D):                 # z[k] = sum_{i<D} mu_i z[k-D+i]
                    if co[i]:
                        v ^= self._z[k - D + i]
                self._z[k] = v
                k += 1
        else:
            k = min(self._z) - 1
            while k >= L:
                v = 0
                for i in range(1, D + 1):          # z[k] = sum_{i>=1} mu_i z[k+i]  (mu_0=1)
                    if co[i]:
                        v ^= self._z[k + i]
                self._z[k] = v
                k -= 1
        return self._z[L]

    def column_sum(self, page: bytes) -> int:
        return self.m.checksum(page) ^ self.m.init   # checksum = init XOR column_sum

    def checksum(self, page: bytes) -> int:
        return self.Z(len(page)) ^ self.column_sum(page)

    def to_json(self):
        d = self.m.to_json()
        d["z_minpoly"] = self.mu
        d["z_degree"] = self.D
        anchor_L = min(self._z)
        d["z_anchor_L"] = anchor_L
        d["z_anchor_Z"] = [self._z[anchor_L + i] for i in range(self.D)]
        return d


def load_samples(folder: Path, base_model):
    """Reads the consecutive zc samples (from .zlength_result.json or via glob) -> {L: Z(L)}."""
    rj = folder / ".zlength_result.json"
    files = []
    if rj.exists():
        data = json.loads(rj.read_text())
        files = [folder / v["file"] for v in data["samples"].values()]
    else:
        files = [Path(p) for p in glob.glob(str(folder / "zc*.HMI"))]
    zmap = {}
    for f in files:
        try:
            pc = page_and_ck(str(f))
        except PermissionError:
            continue
        if not pc:
            continue
        page, ck = pc
        L = len(page)
        colsum = base_model.checksum(page) ^ base_model.init
        zmap[L] = ck ^ colsum                       # Z(L)
    return zmap


def _load_base_model(folder: Path, prefix: str):
    """Load column model: preferably from saved JSON (cya base may no longer be present)."""
    mp = folder.parent / "checksum_model.json"
    if mp.exists():
        return ChecksumModel.from_json(json.loads(mp.read_text()))
    return build(folder, prefix)


def build_zmodel(folder: Path, prefix: str = "cya") -> tuple:
    base = _load_base_model(folder, prefix)
    zmap = load_samples(folder, base)
    Ls = sorted(zmap)
    if len(Ls) < 20:
        raise SystemExit(f"too few Z samples ({len(Ls)}) — run research.collect_zlength first")
    # choose the longest CONSECUTIVE run
    runs, cur = [], [Ls[0]]
    for a, b in zip(Ls, Ls[1:]):
        if b == a + 1:
            cur.append(b)
        else:
            runs.append(cur)
            cur = [b]
    runs.append(cur)
    run = max(runs, key=len)
    Zseq = [zmap[L] for L in run]
    mu = solve_z_minpoly(Zseq)
    D = pdeg(mu)
    anchor_L = run[0]
    model = ZModel(base, mu, anchor_L, [zmap[anchor_L + i] for i in range(D)])
    return base, model, run, zmap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default="samples/reference")
    ap.add_argument("--save", default="")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    folder = Path(args.folder)
    base, model, run, zmap = build_zmodel(folder)
    print(f"Z samples: {len(zmap)}, longest consecutive run {run[0]}..{run[-1]} ({len(run)})")
    print(f"Z minimal polynomial degree D={model.D}  (0x{model.mu:X})")

    # Holdout: recurrence on the first half, prediction of the second half
    half = len(run) // 2
    ok_hold = all(model.Z(run[k]) == zmap[run[k]] for k in range(half, len(run)))
    print(f"Holdout (prediction {run[half]}..{run[-1]}): {'OK' if ok_hold else 'FAILED'}")
    # Z(BASE_L) must equal INIT
    print(f"Z({BASE_L})=0x{model.Z(BASE_L):08X}  INIT=0x{base.init:08X}  "
          f"{'OK' if model.Z(BASE_L) == base.init else 'MISMATCH'}")

    # DEFINITIVE: reference pages of a different length (csa*) that the old model could NOT reproduce
    print("Foreign lengths (csa*, definitive test):")
    dl_ok = dl_tot = 0
    for f in sorted(glob.glob(str(folder / "csa*.HMI"))):
        try:
            pc = page_and_ck(f)
        except PermissionError:
            continue
        if not pc:
            continue
        got = model.checksum(pc[0])
        good = got == pc[1]
        dl_ok += good
        dl_tot += 1
        print(f"   {os.path.basename(f):12s} len={len(pc[0]):5d} expected 0x{pc[1]:08X} "
              f"computed 0x{got:08X} {'OK' if good else '!! MISMATCH'}")
    print(f"\n==> Foreign length {dl_ok}/{dl_tot}, holdout {'OK' if ok_hold else 'FAILED'}")

    if args.save and ok_hold and dl_ok == dl_tot and dl_tot > 0:
        Path(args.save).write_text(json.dumps(model.to_json(), indent=2), encoding="utf-8")
        print("Extended model saved:", args.save)
    return 0


def selftest() -> int:
    """Validates BM + LCM + roll against a synthetic LFSR vector sequence without the Editor."""
    # State operator: 32-bit LFSR (Galois) with known polynomial -> Z(L) = state after L steps.
    import random
    random.seed(12345)
    POLY = 0xEDB88320                      # some 32-bit tap pattern
    def step(s):
        return (s >> 1) ^ (POLY if (s & 1) else 0)
    s = 0xDEADBEEF
    Z = []
    for _ in range(200):
        Z.append(s)
        s = step(s)
    mu = solve_z_minpoly(Z[:160])
    D = pdeg(mu)
    print(f"[selftest] reconstructed degree D={D}")
    m = ZModel.__new__(ZModel)
    m.mu = mu
    m.D = D
    m.coeff = [(mu >> i) & 1 for i in range(D + 1)]
    m._z = {i: Z[i] for i in range(D)}
    okf = all(m.Z(k) == Z[k] for k in range(D, 200))       # forwards
    okb = True
    m2 = ZModel.__new__(ZModel)
    m2.mu = mu
    m2.D = D
    m2.coeff = m.coeff
    m2._z = {100 + i: Z[100 + i] for i in range(D)}
    okb = all(m2.Z(k) == Z[k] for k in range(0, 100))      # backwards
    print(f"[selftest] forwards {'OK' if okf else 'FAILED'}, backwards {'OK' if okb else 'FAILED'}")
    return 0 if (okf and okb) else 1


if __name__ == "__main__":
    raise SystemExit(main())
