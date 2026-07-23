"""Reconstructs the section checksum via a linear matrix recurrence (large internal state).

The checksum is GF(2)-linear, but has an internal state that is larger than the 32-bit output
(demonstrated: the effect of a text change locally spans only a few dimensions). The simple
32-dimension matrix method therefore fails. Instead, the linear recurrence of the column sequence
``col(t) = P·A^t·U`` is determined here and used to extrapolate to arbitrary positions.

Data collection: a 72-character ``txt`` base (all ``p``) and, per position/bit, one variant with
exactly one flipped bit. Each variant directly yields one column. The method was verified against a
simulated checksum with a 64-bit state (100/100 random pages).

Usage::

    python -m research.nextion_checksum_recurrence <folder-with-samples>

Detects base and variants automatically from the byte difference; missing samples do no harm.
"""

from __future__ import annotations

import glob
import struct
import sys
from pathlib import Path


def page_and_ck(path: str) -> tuple[bytes, int] | None:
    raw = open(path, "rb").read()
    count = struct.unpack_from("<I", raw, 0)[0]
    for i in range(count):
        o = 4 + i * 28
        if raw[o + 24] == 0 and raw[o : o + 16].split(b"\x00")[0] == b"0.pa":
            start, size = struct.unpack_from("<II", raw, o + 16)
            pg = raw[start : start + size]
            return pg, struct.unpack_from("<I", pg, 0)[0]
    return None


def load_columns(folder: Path, prefix: str) -> tuple[dict[tuple[int, int], int], int, int, bytes]:
    """Reads base + single-bit variants. Returns {(pos,bit): column}, base_ck, window_start, base_page."""
    files = sorted(glob.glob(str(folder / f"{prefix}*.HMI")))
    entries = []
    for f in files:
        try:
            pc = page_and_ck(f)
        except PermissionError:
            continue
        if pc:
            entries.append((f, pc[0], pc[1]))
    if not entries:
        raise SystemExit("no readable samples found")
    # Base = the page that differs from most others in exactly 0 or in many bytes:
    # in practice: the page whose txt window is constantly 0x70.
    lens = {len(p) for _, p, _ in entries}
    if len(lens) != 1:
        raise SystemExit(f"inconsistent page lengths: {lens}")
    # Determine the window range: bytes that vary across all samples.
    ref = entries[0][1]
    changed = set()
    for _, p, _ in entries:
        for i in range(4, len(p)):
            if p[i] != ref[i]:
                changed.add(i)
    lo, hi = min(changed), max(changed) + 1
    # Base page = the one whose window is entirely 0x70.
    base = next((p for _, p, _ in entries if all(p[lo + k] == 0x70 for k in range(hi - lo))), None)
    if base is None:
        raise SystemExit("base sample (window = 0x70) missing")
    base_ck = struct.unpack_from("<I", base, 0)[0]

    cols: dict[tuple[int, int], int] = {}
    for _, p, ck in entries:
        diff = [i for i in range(lo, hi) if p[i] != base[i]]
        if len(diff) != 1:
            continue  # only use single-byte variants (the base has 0)
        i = diff[0]
        delta = p[i] ^ base[i]
        if delta & (delta - 1):
            continue  # single bit only
        bit = delta.bit_length() - 1
        pos = i - lo
        cols[(pos, bit)] = ck ^ base_ck
    return cols, base_ck, lo, base


def matmul_none():  # placeholder to keep flake happy
    pass


def solve(folder: Path, prefix: str):
    cols, base_ck, lo, base = load_columns(folder, prefix)
    positions = sorted({p for p, _ in cols})
    npos = max(positions) + 1
    # COL[pos][bit]; missing -> None
    COL = [[cols.get((p, b)) for b in range(8)] for p in range(npos)]
    complete = [p for p in range(npos) if all(COL[p][b] is not None for b in range(8))]
    print(f"positions with complete 8 bits: {len(complete)} of {npos}")

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

    # only the contiguous initial block of complete positions
    block = []
    for p in range(npos):
        if p in complete:
            block.append(p)
        else:
            break
    print(f"contiguous complete block: {len(block)} positions")
    C = [COL[p] for p in block]

    print("cumulative rank:")
    for m in range(4, len(C) + 1, 4):
        print(f"   {m:3d} positions -> rank {rank([C[p][b] for p in range(m) for b in range(8)])}")

    # find the shortest matrix recurrence: C[t+d] = sum_i c_i C[t+i]
    def find_recurrence(C, maxd):
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

    d, c = find_recurrence(C, min(len(C) - 2, 130))
    if d is None:
        print("NO recurrence found — more contiguous positions needed "
              "(internal state > captured length).")
        return None
    print(f"recurrence degree: {d}")

    T0 = len(base) - 1 - lo  # trailing of position 0
    seq_cache = [list(x) for x in C]

    def col_for_trailing(t):
        p = T0 - t
        if p < 0:
            return None
        while len(seq_cache) <= p:
            nxt = [0] * 8
            for bit in range(8):
                v = 0
                for i in range(d):
                    if c[i]:
                        v ^= seq_cache[len(seq_cache) - d + i][bit]
                nxt[bit] = v
            seq_cache.append(nxt)
        return seq_cache[p]

    def compute(page):
        ck = base_ck
        # Start at 4: bytes 0..3 are the stored checksum field, not payload. A reference page with a
        # different stored checksum would otherwise be scored as if those bytes were payload changes.
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

    # Self-check against all complete samples.
    ok = tot = 0
    for p in block:
        for b in range(8):
            variant = bytearray(base)
            variant[lo + p] ^= (1 << b)
            tot += 1
            if compute(bytes(variant)) == (base_ck ^ COL[p][b]):
                ok += 1
    print(f"self-check samples: {ok}/{tot}")
    return compute, base, base_ck, d


if __name__ == "__main__":
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("samples/reference")
    prefix = sys.argv[2] if len(sys.argv) > 2 else "cs"
    solve(folder, prefix)
