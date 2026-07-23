"""Robust checksum data collection via the Program.s code editor (instead of the txt attribute cell).

The txt attribute cell on the canvas is too error-prone over hundreds of iterations: drifting clicks
accidentally add new components (pages grow, txt='newtxt'). The Program.s tab, by contrast, is a plain
text editor: a large click target, Ctrl+A + paste replaces cleanly, and NO components can be created.
Program.s is a checksum-carrying section — the same checksum function.

Calibration: three points — Program.s text area, 'File' menu, 'Save as' entry.

Requires: ``pip install pyautogui pyperclip pygetwindow pywinauto``.

Usage:
  Test (3):  python -m research.auto_collect_programs --test
  Full:      python -m research.auto_collect_programs
"""

from __future__ import annotations

import argparse
import json
import string
import time
from collections import namedtuple
from pathlib import Path

BASE_DIR = Path("samples")
VALUES = BASE_DIR / "checksum_txt_values.txt"
OUT_DIR = BASE_DIR / "reference"
CALIB = OUT_DIR / ".calib_programs.json"
P = namedtuple("P", "x y")


def load_values() -> list[str]:
    rows = []
    for line in VALUES.read_text(encoding="latin-1").splitlines():
        if line.startswith("#") or "\t" not in line:
            continue
        rows.append(line.split("\t", 1)[1])
    return rows


def capture(pyautogui, label, seconds=12):
    print(f"\n>>> Put the mouse EXACTLY on {label} and hold it still for {seconds}s:")
    for r in range(seconds, 0, -1):
        p = pyautogui.position()
        print(f"    {r:2d}s left  x={p.x:<4d} y={p.y:<4d}    ", end="\r")
        time.sleep(1)
    p = pyautogui.position()
    print(f"\n    -> {p}")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
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
    if args.test:
        values = values[:3]

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
    reuse = CALIB.exists() and input("Use the saved Program.s calibration? [y/n] ").strip().lower() in ("j", "y", "ja", "")
    if reuse:
        c = json.loads(CALIB.read_text())
        tab, pane, file_menu, save_as = P(*c["tab"]), P(*c["pane"]), P(*c["file"]), P(*c["saveas"])
        print("Calibration reused.")
    else:
        print("\n=== CALIBRATION ===  First open the 'Program.s' tab in the Editor!")
        tab = capture(pyautogui, "the 'Program.s' TAB (at the top, next to 'Display')")
        pane = capture(pyautogui, "the MIDDLE of the Program.s TEXT AREA (large light code area)")
        file_menu = capture(pyautogui, "the 'File' menu in the TOP LEFT CORNER")
        activate()
        pyautogui.click(file_menu.x, file_menu.y)
        time.sleep(0.7)
        save_as = capture(pyautogui, "the 'Save as' entry in the open menu")
        pyautogui.press("escape")
        time.sleep(0.3)
        CALIB.write_text(json.dumps({"tab": list(tab), "pane": list(pane),
                                     "file": list(file_menu), "saveas": list(save_as)}))

    print(f"\ntab={tab} pane={pane} file={file_menu} saveas={save_as}")
    if input("Start? [y/n] ").strip().lower() not in ("j", "y", "ja", ""):
        return 1

    prefix = "ps" + next(t for t in string.ascii_lowercase if not list(OUT_DIR.glob(f"ps{t}*.HMI")))
    print(f"Prefix: {prefix}")
    print("\nStarting in 10s — hands off! The Program.s tab must stay open.")
    for r in range(10, 0, -1):
        print(f"  {r:2d} ", end="\r")
        time.sleep(1)
    print()

    for index, value in enumerate(values):
        name = f"{prefix}{index:03d}"
        activate()
        # Dismiss any error dialog (e.g. 'No component to paste').
        stray = get_dialog()
        if stray is not None:
            try:
                stray.type_keys("{ENTER}")
            except Exception:
                pass
            time.sleep(0.3)
        pyautogui.click(tab.x, tab.y)       # BACK to the Program.s tab (Editor jumps to Display after a save)
        time.sleep(0.30)
        pyautogui.click(pane.x, pane.y)     # click into the code area (large target)
        time.sleep(0.20)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.06)
        for _ in range(6):
            pyperclip.copy(value)
            time.sleep(0.04)
            if pyperclip.paste() == value:
                break
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.14)
        pyautogui.click(file_menu.x, file_menu.y)
        time.sleep(0.5)
        pyautogui.click(save_as.x, save_as.y)
        ok = save_dialog(name)
        time.sleep(0.4)
        print(f"  [{index:03d}/{len(values)}] {name}.HMI  {'ok' if ok else 'ERROR'}")

    print(f"\nDone: {len(values)} samples ({prefix}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
