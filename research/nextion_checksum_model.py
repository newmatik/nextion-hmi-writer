"""Complete, length-independent model of the Nextion section checksum.

From the measured single-bit columns (window of 72 bytes) the linear matrix recurrence (degree d) is
determined. The column sequence over the trailing position (distance from the end) is extrapolated by
the recurrence in BOTH directions: forward to trailing 0, backward (inverse recurrence, requires
c[0]=1) to arbitrarily high trailing positions. This yields:

    ck(page) = INIT XOR  XOR_{byte i, set bit b}  G[len(page)-1-i][b]

where G[t][b] is the column for bit b at trailing position t and INIT = ck(zero page). INIT is
reconstructed from the known base page. The model holds for ANY length.

Definitive test: the reference pages of other lengths (2787/3839/4891 bytes) were NOT part of the fit;
their editor checksums must be reproduced exactly.

CAVEAT -- this docstring records an earlier state and its "ANY length" claim did not survive. Two
later results in this package contradict it, and both are worth reading before trusting the model:

* ``nextion_checksum_zmodel`` establishes that INIT is not a constant. What is fitted here is the
  length-dependent base term at the base length, INIT = Z(1735), so the general rule is
  ``ck(page) = Z(len(page)) XOR column_sum(page)`` and this model is exact only at the base length.
* ``collect_columns`` records that G was measured only inside the txt window (t in [117,185]) while a
  real page uses t in [0,1730], leaving most of the applied G(t) as unvalidated extrapolation.

Neither limitation is visible in equal-length samples: there the error is constant and hides inside
the fitted INIT. The checksum that actually writes files today comes from the runtime trace in
``nextion_hmi_checksum``, not from this model; the model is kept because it proved the linearity and
bounded the problem.

Usage:  python -m research.nextion_checksum_model [folder] [prefix]  [--save path.json]
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import struct
from pathlib import Path

from research.nextion_checksum_recurrence import load_columns
from research.nextion_checksum_verify import find_recurrence


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


class ChecksumModel:
    def __init__(self, C, d, c, T0, base, base_ck):
        self.d = d
        self.c = c
        self.T0 = T0
        self.base = base
        self.base_len = len(base)
        self.base_ck = base_ck
        if not c[0]:
            raise ValueError("c[0]=0 — inverse recurrence not directly possible")
        # s[p] = column for trailing (T0 - p); s[0..len(C)-1] measured.
        self._s = {p: list(C[p]) for p in range(len(C))}
        self._nC = len(C)
        # Reconstruct INIT from the base page (needs G over the whole base length).
        # The checksum field itself (bytes 0..3) is NOT part of the payload and is excluded.
        acc = base_ck
        for i in range(4, len(base)):
            byte = base[i]
            if not byte:
                continue
            col = self.G(len(base) - 1 - i)
            for b in range(8):
                if (byte >> b) & 1:
                    acc ^= col[b]
        self.init = acc  # = ck(zero page of the same length); length-independent constant

    def _extend_forward(self, pmax):
        d, c = self.d, self.c
        # fill gaps up to pmax
        q = self._nC
        while q <= pmax:
            if q not in self._s:
                row = [0] * 8
                for b in range(8):
                    v = 0
                    for i in range(d):
                        if c[i]:
                            v ^= self._s[q - d + i][b]
                    row[b] = v
                self._s[q] = row
            q += 1

    def _extend_backward(self, pmin):
        d, c = self.d, self.c
        q = -1
        while q >= pmin:
            if q not in self._s:
                # C[q] = C[q+d] XOR XOR_{i=1}^{d-1} c[i] C[q+i]   (because c[0]=1)
                row = [0] * 8
                for b in range(8):
                    v = self._s[q + d][b]
                    for i in range(1, d):
                        if c[i]:
                            v ^= self._s[q + i][b]
                    row[b] = v
                self._s[q] = row
            q -= 1

    def G(self, t):
        """Column (8 values) for trailing position t."""
        p = self.T0 - t
        if p >= self._nC:
            self._extend_forward(p)
        elif p < 0:
            self._extend_backward(p)
        return self._s[p]

    def checksum(self, page: bytes) -> int:
        # Bytes 0..3 = checksum field, not part of the payload.
        ck = self.init
        L = len(page)
        for i in range(4, L):
            byte = page[i]
            if not byte:
                continue
            col = self.G(L - 1 - i)
            for b in range(8):
                if (byte >> b) & 1:
                    ck ^= col[b]
        return ck

    def to_json(self):
        return {
            "degree": self.d,
            "coeffs": self.c,
            "T0": self.T0,
            "base_ck": self.base_ck,
            "init": self.init,
            "columns": [[self._s[p][b] for b in range(8)] for p in range(self._nC)],
            "base_len": self.base_len,
        }

    @classmethod
    def from_json(cls, d):
        """Reconstruct the model from saved JSON (without needing the cya base samples)."""
        self = cls.__new__(cls)
        self.d = d["degree"]
        self.c = list(d["coeffs"])
        self.T0 = d["T0"]
        self.base_ck = d["base_ck"]
        self.init = d["init"]
        self._s = {p: list(col) for p, col in enumerate(d["columns"])}
        self._nC = len(d["columns"])
        self.base = None
        self.base_len = d.get("base_len")
        return self


def build(folder: Path, prefix: str) -> ChecksumModel:
    cols, base_ck, lo, base = load_columns(folder, prefix)
    npos = max(p for p, _ in cols) + 1
    complete = [p for p in range(npos) if all(cols.get((p, b)) is not None for b in range(8))]
    if not complete:
        raise SystemExit("no complete position")
    # choose the longest contiguous run of complete positions (the recurrence extrapolates the rest)
    best = (0, 0)
    s = 0
    while s < len(complete):
        e = s
        while e + 1 < len(complete) and complete[e + 1] == complete[e] + 1:
            e += 1
        if complete[e] - complete[s] > best[1] - best[0]:
            best = (complete[s], complete[e])
        s = e + 1
    run = list(range(best[0], best[1] + 1))
    C = [[cols[(p, b)] for b in range(8)] for p in run]
    d, c = find_recurrence(C, min(len(C) - 2, 200))
    if d is None:
        raise SystemExit("no recurrence found")
    lo_eff = lo + run[0]  # anchor position = first position of the run
    T0 = len(base) - 1 - lo_eff
    return ChecksumModel(C, d, c, T0, base, base_ck)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default="samples/reference")
    ap.add_argument("prefix", nargs="?", default="cya")
    ap.add_argument("--save", default="")
    args = ap.parse_args()
    folder = Path(args.folder)

    model = build(folder, args.prefix)
    print(f"degree d={model.d}  c[0]={model.c[0]}  base_ck=0x{model.base_ck:08X}  INIT=0x{model.init:08X}")
    print(f"base self-check: ck(base)=0x{model.checksum(model.base):08X} "
          f"(expected 0x{model.base_ck:08X})")

    # 1) all cya variants
    ok = tot = 0
    for f in sorted(glob.glob(str(folder / f"{args.prefix}*.HMI"))):
        try:
            pc = page_and_ck(f)
        except PermissionError:
            continue
        if not pc:
            continue
        tot += 1
        if model.checksum(pc[0]) == pc[1]:
            ok += 1
    print(f"cya variants (same length): {ok}/{tot}")

    # 2) DEFINITIVE: reference pages of OTHER lengths (not in the fit)
    print("reference pages of other lengths (definitive extrapolation test):")
    dl_ok = dl_tot = 0
    for f in sorted(glob.glob(str(folder / "csa*.HMI"))):
        try:
            pc = page_and_ck(f)
        except PermissionError:
            continue
        if not pc or len(pc[0]) == len(model.base):
            continue  # other lengths only
        got = model.checksum(pc[0])
        dl_tot += 1
        good = got == pc[1]
        dl_ok += good
        print(f"   {os.path.basename(f):14s} len={len(pc[0]):5d}  "
              f"expected 0x{pc[1]:08X}  computed 0x{got:08X}  {'OK' if good else '!! MISMATCH'}")

    verdict = "DEFINITIVELY VERIFIED" if (ok == tot and dl_ok == dl_tot and dl_tot > 0) else \
              ("same length ok, no foreign length" if ok == tot and dl_tot == 0 else "ERROR")
    print(f"\n==> {verdict}  (cya {ok}/{tot}, foreign length {dl_ok}/{dl_tot})")

    if args.save and ok == tot:
        Path(args.save).write_text(json.dumps(model.to_json(), indent=2), encoding="utf-8")
        print("model saved:", args.save)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
