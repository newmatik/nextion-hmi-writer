"""Collects Z(L) support points on a length grid by adding text components one after another
(reliably via a double click on the toolbox icon). Every drop is a new file zgNN with a known, parsed
length. The base is opened only once and is never overwritten.

Usage:  python -m research.ck_zgrid <base.HMI> <n>
"""

from __future__ import annotations

import string
import struct
import sys
import time
from pathlib import Path

import pyautogui

from research.ck_probe import open_file, save_as, focus

TEXT_TOOL = (810, 196)
OUT = Path("samples/reference")


def page_len(path: Path):
    try:
        with open(path, "rb") as f:
            d = f.read(8192)
    except (PermissionError, FileNotFoundError, OSError):
        return None
    if len(d) < 4:
        return None
    n = struct.unpack_from("<I", d, 0)[0]
    for i in range(min(n, 64)):
        o = 4 + i * 28
        if o + 28 > len(d):
            break
        if d[o + 24] == 0 and d[o : o + 16].split(b"\x00")[0] == b"0.pa":
            return struct.unpack_from("<I", d, o + 16 + 4)[0]
    return None


def main():
    base = str(Path(sys.argv[1]).resolve())
    n = int(sys.argv[2])
    # Pick a FRESH prefix that does NOT exist yet -> never triggers an overwrite prompt.
    prefix = "zg" + next(t for t in string.ascii_lowercase if not list(OUT.glob(f"zg{t}*.HMI")))
    print(f"Prefix: {prefix}", flush=True)
    open_file(base)
    for k in range(1, n + 1):
        focus()
        pyautogui.doubleClick(*TEXT_TOOL)
        time.sleep(0.6)
        ok = save_as(f"{prefix}{k:02d}")
        prev = page_len(OUT / f"{prefix}{k-1:02d}.HMI") if k >= 2 else None
        print(f"  {prefix}{k:02d} save={'ok' if ok else 'FAILED'}  {prefix}{k-1:02d}.len={prev}", flush=True)
    open_file(base)
    print("Lengths:", flush=True)
    for k in range(1, n + 1):
        print(f"  {prefix}{k:02d}: {page_len(OUT / f'{prefix}{k:02d}.HMI')}", flush=True)
    print("done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
