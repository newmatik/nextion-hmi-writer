"""Canvas-free checksum data collection: t0 is selected via the component dropdown in the attribute
panel (NOT via the canvas). That makes accidentally adding components structurally impossible — it
was the cause of the accumulation over long runs.

In addition the script keeps re-checking an already saved sample: if the page grows (a component was
added), it aborts IMMEDIATELY instead of wasting 48 minutes.

Calibration points (5): component dropdown, entry 't0' in the open dropdown, txt value cell,
'File' menu, 'Save as' entry.

Requires: pip install pyautogui pyperclip pygetwindow pywinauto

Usage:
  Test:  python -m research.auto_collect_cb --n 60
  Full:  python -m research.auto_collect_cb
"""

from __future__ import annotations

import argparse
import json
import string
import struct
import time
from collections import namedtuple
from pathlib import Path

BASE_DIR = Path("samples")
VALUES = BASE_DIR / "checksum_txt_values.txt"
OUT_DIR = BASE_DIR / "reference"
CALIB = OUT_DIR / ".calib_cb.json"
P = namedtuple("P", "x y")


def load_values():
    rows = []
    for line in VALUES.read_text(encoding="latin-1").splitlines():
        if line.startswith("#") or "\t" not in line:
            continue
        rows.append(line.split("\t", 1)[1])
    return rows


def page_len(path: Path):
    """Length of the 0.pa section (or None if locked/missing)."""
    try:
        d = path.read_bytes()
    except (PermissionError, FileNotFoundError):
        return None
    n = struct.unpack_from("<I", d, 0)[0]
    for i in range(n):
        o = 4 + i * 28
        if d[o + 24] == 0 and d[o : o + 16].split(b"\x00")[0] == b"0.pa":
            return struct.unpack_from("<I", d, o + 16 + 4)[0]
    return None


def capture(pyautogui, label, seconds=12):
    print(f"\n>>> Put the mouse EXACTLY on {label} and hold it still for {seconds}s:")
    for r in range(seconds, 0, -1):
        p = pyautogui.position()
        print(f"    {r:2d}s left  x={p.x:<4d} y={p.y:<4d}   ", end="\r")
        time.sleep(1)
    p = pyautogui.position()
    print(f"\n    -> {p}")
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0)
    args = ap.parse_args()
    try:
        import pyautogui
        import pyperclip
        import pygetwindow
        from pywinauto import Application
    except ImportError as e:
        print("pip install pyautogui pyperclip pygetwindow pywinauto  (missing:", e.name, ")")
        return 1

    pyautogui.PAUSE = 0.12
    pyautogui.FAILSAFE = True
    values = load_values()
    if args.n:
        values = values[: args.n]

    wins = [x for x in pygetwindow.getAllWindows() if "Nextion Editor" in (x.title or "")]
    editor = wins[0] if wins else None
    print("Editor:", editor.title if editor else "NOT found")

    def activate():
        if editor:
            try:
                editor.activate()
            except Exception:
                pass
            time.sleep(0.2)

    def get_dialog():
        try:
            a = Application(backend="win32").connect(class_name="#32770", timeout=0.4)
            wnd = a.window(class_name="#32770")
            return wnd if wnd.exists() else None
        except Exception:
            return None

    def save_dialog(name):
        dlg = None
        for _ in range(25):
            dlg = get_dialog()
            if dlg:
                break
            time.sleep(0.2)
        if not dlg:
            return False
        try:
            dlg.child_window(class_name="Edit", found_index=0).set_edit_text(name)
        except Exception:
            pass
        time.sleep(0.15)
        clicked = False
        for t in ("&Speichern", "Speichern", "&Save", "Save"):
            try:
                b = dlg.child_window(title=t, class_name="Button")
                if b.exists():
                    b.click_input()
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            try:
                dlg.type_keys("{ENTER}")
            except Exception:
                pass
        time.sleep(0.5)
        conf = get_dialog()
        # 'Speichern unter' (= 'Save as') is the save dialog itself; anything else is a prompt.
        if conf is not None and conf.window_text() != "Speichern unter":
            try:
                conf.type_keys("{ENTER}")
            except Exception:
                pass
        for _ in range(15):
            if get_dialog() is None:
                return True
            time.sleep(0.2)
        return True

    # Calibration
    reuse = CALIB.exists() and input("Use the saved CB calibration? [y/n] ").strip().lower() in ("j", "y", "ja", "")
    if reuse:
        c = json.loads(CALIB.read_text())
        combo, t0entry, txt_cell, file_menu, save_as = (P(*c[k]) for k in ("combo", "t0entry", "txt", "file", "saveas"))
        print("Calibration reused.")
    else:
        print("\n=== CALIBRATION (5 points) ===  Keep t0 selected in the Editor, attribute panel visible.")
        combo = capture(pyautogui, "the COMPONENT DROPDOWN at the top of the attribute panel (shows 't0(Text)')")
        activate()
        print("\nBriefly opening the dropdown ...")
        pyautogui.click(combo.x, combo.y)
        time.sleep(0.6)
        t0entry = capture(pyautogui, "the 't0(Text)' entry in the OPENED dropdown")
        pyautogui.press("escape")
        time.sleep(0.3)
        txt_cell = capture(pyautogui, "the txt VALUE CELL (row 'txt')")
        file_menu = capture(pyautogui, "the 'File' menu in the TOP LEFT CORNER")
        activate()
        pyautogui.click(file_menu.x, file_menu.y)
        time.sleep(0.7)
        save_as = capture(pyautogui, "the 'Save as' entry in the open menu")
        pyautogui.press("escape")
        time.sleep(0.3)
        CALIB.write_text(json.dumps({"combo": list(combo), "t0entry": list(t0entry),
                                     "txt": list(txt_cell), "file": list(file_menu), "saveas": list(save_as)}))

    print(f"\ncombo={combo} t0entry={t0entry} txt={txt_cell} file={file_menu} saveas={save_as}")
    if input("Start? [y/n] ").strip().lower() not in ("j", "y", "ja", ""):
        return 1

    prefix = "cx" + next(t for t in string.ascii_lowercase if not list(OUT_DIR.glob(f"cx{t}*.HMI")))
    print(f"Prefix: {prefix}")
    print("\nStarting in 10s — hands off, do not use mouse/keyboard/clipboard!")
    for r in range(10, 0, -1):
        print(f"  {r:2d} ", end="\r")
        time.sleep(1)
    print()

    base_len = None
    for index, value in enumerate(values):
        name = f"{prefix}{index:03d}"
        activate()
        # select t0 canvas-free via the dropdown
        pyautogui.click(combo.x, combo.y)
        time.sleep(0.35)
        pyautogui.click(t0entry.x, t0entry.y)
        time.sleep(0.30)
        # edit txt
        pyautogui.doubleClick(txt_cell.x, txt_cell.y)
        time.sleep(0.30)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.06)
        for _ in range(6):
            pyperclip.copy(value)
            time.sleep(0.04)
            if pyperclip.paste() == value:
                break
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.14)
        pyautogui.press("enter")
        time.sleep(0.30)
        # save
        pyautogui.click(file_menu.x, file_menu.y)
        time.sleep(0.5)
        pyautogui.click(save_as.x, save_as.y)
        ok = save_dialog(name)
        time.sleep(0.3)
        print(f"  [{index:03d}/{len(values)}] {name}.HMI  {'ok' if ok else 'ERROR'}")

        # Self-check: re-read a sample that has already been unlocked.
        if index >= 3:
            L = page_len(OUT_DIR / f"{prefix}{index-3:03d}.HMI")
            if L is not None:
                if base_len is None:
                    base_len = L
                elif L != base_len:
                    print(f"\n!!! ABORT: sample {index-3:03d} has page length {L} instead of {base_len} "
                          f"(a component was added). Stopped immediately at {index+1} samples.")
                    return 2

    print(f"\nDone: {len(values)} samples ({prefix}). Page length constant = {base_len}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
