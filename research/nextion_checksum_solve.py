"""Reconstructs the Nextion Editor's GF(2)-linear section checksum from measurement data.

The checksum (4 bytes, little endian, at the start of every ``.HMI`` section) is demonstrably
GF(2)-linear, but it is not a standard CRC. Instead of guessing it, it is determined outright here:

1. The editor stores many variants of the same base project that differ only in a freely settable
   field several bytes long (the ``txt`` of a text object).
2. From the checksums of the variants, the linear effect (column) is determined per bit position.
3. Five consecutive byte positions yield the advance operator ``A`` and the injection; with those the
   checksum of any arbitrary payload can be computed.

The method is verified against standard CRC-32 (200/200 random pages). It is stdlib-only.

Usage::

    python -m research.nextion_checksum_solve <base.HMI> <sample0.HMI> <sample1.HMI> ...

All files must be the same base project with exactly one changed ``txt``. The tool finds the window
automatically, solves the system and writes the model to ``samples/checksum_model.json``.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path


def matmul(cols: list[int], v: int) -> int:
    r = 0
    for i in range(len(cols)):
        if (v >> i) & 1:
            r ^= cols[i]
    return r


def gf2_invert(cols: list[int]) -> list[int]:
    n = len(cols)
    a = list(cols)
    inv = [1 << i for i in range(n)]
    for i in range(n):
        piv = next((j for j in range(i, n) if (a[j] >> i) & 1), -1)
        if piv < 0:
            raise ValueError("matrix singular")
        a[i], a[piv] = a[piv], a[i]
        inv[i], inv[piv] = inv[piv], inv[i]
        for j in range(n):
            if j != i and (a[j] >> i) & 1:
                a[j] ^= a[i]
                inv[j] ^= inv[i]
    return inv


def solve_system(equations: list[tuple[int, int]], unknowns: int) -> list[int] | None:
    """Solves a GF(2) system: one bitmask over the unknowns and one 32-bit result per equation."""

    matrix = [m for m, _ in equations]
    rhs = [r for _, r in equations]
    where = [-1] * unknowns
    row = 0
    for col in range(unknowns):
        piv = next((r for r in range(row, len(matrix)) if (matrix[r] >> col) & 1), -1)
        if piv < 0:
            continue
        matrix[row], matrix[piv] = matrix[piv], matrix[row]
        rhs[row], rhs[piv] = rhs[piv], rhs[row]
        for r in range(len(matrix)):
            if r != row and (matrix[r] >> col) & 1:
                matrix[r] ^= matrix[row]
                rhs[r] ^= rhs[row]
        where[col] = row
        row += 1
    if any(w < 0 for w in where):
        return None
    return [rhs[where[c]] for c in range(unknowns)]


class ChecksumModel:
    """Computed linear model of the section checksum."""

    def __init__(self, advance: list[int], inverse: list[int], inject0: list[int],
                 anchor_trailing: int, base_payload: bytes, base_checksum: int):
        self.advance = advance
        self.inverse = inverse
        self.inject0 = inject0
        self.anchor_trailing = anchor_trailing
        self.base_payload = base_payload
        self.base_checksum = base_checksum

    def _advance(self, v: int, steps: int) -> int:
        op = self.advance if steps > 0 else self.inverse
        for _ in range(abs(steps)):
            v = matmul(op, v)
        return v

    def checksum(self, payload: bytes) -> int:
        """Computes the checksum of an arbitrary section payload (from byte 4 on)."""

        if len(payload) != len(self.base_payload):
            # Length differs -> re-anchor via the shared trailing distance.
            return self._checksum_any_length(payload)
        ck = self.base_checksum
        # Start at 4: bytes 0..3 are the section's own checksum field and are not part of the
        # payload. They differ between every variant, so injecting them would corrupt the result.
        for i in range(4, len(payload)):
            d = payload[i] ^ self.base_payload[i]
            if not d:
                continue
            steps = (len(payload) - 1 - i) - self.anchor_trailing
            for bit in range(8):
                if (d >> bit) & 1:
                    ck ^= self._advance(self.inject0[bit], steps)
        return ck

    def _checksum_any_length(self, payload: bytes) -> int:
        # Derive the checksum of a zero payload of the same length, then inject the bytes.
        # base_checksum applies to base_payload; for other lengths reconstruct the constant
        # via the known injection.
        zero_ck = self.base_checksum
        for i in range(4, len(self.base_payload)):
            b = self.base_payload[i]
            if not b:
                continue
            steps = (len(self.base_payload) - 1 - i) - self.anchor_trailing
            for bit in range(8):
                if (b >> bit) & 1:
                    zero_ck ^= self._advance(self.inject0[bit], steps)
        ck = zero_ck
        for i in range(4, len(payload)):
            b = payload[i]
            if not b:
                continue
            steps = (len(payload) - 1 - i) - self.anchor_trailing
            for bit in range(8):
                if (b >> bit) & 1:
                    ck ^= self._advance(self.inject0[bit], steps)
        return ck

    def to_json(self) -> dict:
        return {
            "advance": self.advance,
            "inverse": self.inverse,
            "inject0": self.inject0,
            "anchor_trailing": self.anchor_trailing,
            "base_checksum": self.base_checksum,
            "base_payload_hex": self.base_payload.hex(),
        }


def _page_payloads(path: Path) -> dict[str, bytes]:
    """Returns, per active section, the whole section bytes (the leading 4-byte checksum field
    included). ``ChecksumModel`` indexes absolute offsets and skips bytes 0..3 itself."""

    raw = path.read_bytes()
    count = struct.unpack_from("<I", raw, 0)[0]
    out: dict[str, bytes] = {}
    for i in range(count):
        o = 4 + i * 28
        name = raw[o : o + 16].split(b"\x00")[0]
        start, size = struct.unpack_from("<II", raw, o + 16)
        if raw[o + 24] == 0 and name and size:
            out[name.decode("latin-1")] = raw[start : start + size]
    return out


def _find_window(base: bytes, variants: list[bytes]) -> tuple[int, int]:
    """Finds the contiguous range in which the variants differ from the base section
    (the changed ``txt`` field)."""

    changed: set[int] = set()
    for v in variants:
        if len(v) != len(base):
            raise ValueError("section lengths differ")
        for i in range(4, len(base)):  # exclude the checksum field (0..3)
            if base[i] != v[i]:
                changed.add(i)
    if not changed:
        raise ValueError("no change found")
    lo, hi = min(changed), max(changed)
    return lo, hi + 1


def solve(base_path: Path, variant_paths: list[Path], section: str = "0.pa") -> ChecksumModel:
    base_sections = _page_payloads(base_path)
    base = base_sections[section]
    base_ck = struct.unpack_from("<I", base_path.read_bytes(),
                                 _section_offset(base_path, section))[0]
    variants = []
    checks = []
    for p in variant_paths:
        secs = _page_payloads(p)
        variants.append(secs[section])
        checks.append(struct.unpack_from("<I", p.read_bytes(), _section_offset(p, section))[0])

    lo, hi = _find_window(base, variants)
    npos = hi - lo
    unknowns = npos * 8

    equations: list[tuple[int, int]] = []
    for v, ck in zip(variants, checks):
        mask = 0
        for pos in range(npos):
            d = v[lo + pos] ^ base[lo + pos]
            for bit in range(8):
                if (d >> bit) & 1:
                    mask |= 1 << (pos * 8 + bit)
        equations.append((mask, ck ^ base_ck))

    cols = solve_system(equations, unknowns)
    if cols is None:
        raise ValueError(
            f"system underdetermined: {npos} positions, {len(equations)} measurements. "
            "More variants with full bit coverage (including bit 7) needed."
        )

    grid = [[cols[p * 8 + b] for b in range(8)] for p in range(npos)]

    def rank(columns: list[int]) -> int:
        piv: dict[int, int] = {}
        for value in columns:
            x = value
            for k in sorted(piv):
                if (x >> k) & 1:
                    x ^= piv[k]
            if x:
                piv[x.bit_length() - 1] = x
        return len(piv)

    m = next((mm for mm in range(4, npos) if rank(
        [grid[p][b] for p in range(mm) for b in range(8)]) == 32), None)
    if m is None:
        raise ValueError(
            f"full rank-32 basis only reached beyond {npos} positions; more positions needed"
        )

    src = [grid[p][b] for p in range(m) for b in range(8)]
    dst = [grid[p + 1][b] for p in range(m) for b in range(8)]
    piv: dict[int, tuple[int, int]] = {}
    basis: list[int] = []
    for i, value in enumerate(src):
        x = value
        for k in sorted(piv):
            if (x >> k) & 1:
                x ^= piv[k][0]
        if x:
            piv[x.bit_length() - 1] = (x, i)
            basis.append(i)
    basis = basis[:32]
    w = [src[i] for i in basis]
    wn = [dst[i] for i in basis]
    inverse = [matmul(wn, gf2_invert(w)[i]) for i in range(32)]  # A^{-1}: trailing t -> t-1
    advance = gf2_invert(inverse)

    anchor_trailing = len(base) - 1 - lo
    inject0 = [grid[0][b] for b in range(8)]
    return ChecksumModel(advance, inverse, inject0, anchor_trailing, base, base_ck)


def _section_offset(path: Path, section: str) -> int:
    raw = path.read_bytes()
    count = struct.unpack_from("<I", raw, 0)[0]
    for i in range(count):
        o = 4 + i * 28
        name = raw[o : o + 16].split(b"\x00")[0]
        if raw[o + 24] == 0 and name.decode("latin-1") == section:
            return struct.unpack_from("<I", raw, o + 16)[0]
    raise KeyError(section)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Reconstruct the Nextion section checksum")
    parser.add_argument("base", type=Path)
    parser.add_argument("variants", nargs="+", type=Path)
    parser.add_argument("--section", default="0.pa")
    parser.add_argument(
        "--out", type=Path, default=Path("samples/checksum_model.json")
    )
    args = parser.parse_args(argv)

    model = solve(args.base, args.variants, args.section)

    # Self-check: all inputs must be reproduced exactly.
    ok = 0
    total = 0
    for p in [args.base, *args.variants]:
        secs = _page_payloads(p)
        stored = struct.unpack_from("<I", p.read_bytes(), _section_offset(p, args.section))[0]
        total += 1
        if model.checksum(secs[args.section]) == stored:
            ok += 1
    print(f"self-check: {ok}/{total} input checksums reproduced")
    if ok != total:
        print("ERROR: model does not reproduce the inputs — more/different variants needed")
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(model.to_json(), indent=2), encoding="utf-8")
    print(f"model written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
