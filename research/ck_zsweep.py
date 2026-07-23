"""Collects Z(L) support points: varies t0.txt_maxl (=> the page length changes by 1 byte each time)
and saves one .HMI per value. From those, Z(L)=ck XOR sum(G over payload) is extracted with the
already verified column model (recursion). A few dozen consecutive lengths suffice to determine the
length recursion of Z(L) and to extrapolate it to any length.

Usage:  python -m research.ck_zsweep <base.HMI> <v_start> <v_end>   (txt_maxl from v_start..v_end)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

try:
    import pyautogui
except ImportError as exc:  # GUI drivers ship only with the optional [drivers] extra
    raise SystemExit("this tool needs the GUI drivers; install them with: pip install -e .[drivers]") from exc

from research.ck_probe import open_file, select_t0, save_as, focus

TXTMAXL = (1690, 807)


def set_txtmaxl(v: int):
    pyautogui.doubleClick(*TXTMAXL)
    time.sleep(0.30)
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.05)
    pyautogui.typewrite(str(v), interval=0.02)
    time.sleep(0.10)
    pyautogui.press("enter")
    time.sleep(0.30)


def main():
    base = str(Path(sys.argv[1]).resolve())
    v0, v1 = int(sys.argv[2]), int(sys.argv[3])
    open_file(base)
    print("after open (base):", flush=True)
    for v in range(v0, v1 + 1):
        focus()
        select_t0()
        set_txtmaxl(v)
        ok = save_as(f"z{v:03d}")
        print(f"  z{v:03d}.HMI  txt_maxl={v}  {'ok' if ok else 'FAILED'}", flush=True)
    open_file(base)
    print("done, base reloaded.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
