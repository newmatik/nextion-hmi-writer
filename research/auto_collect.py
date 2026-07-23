"""Fully automatic checksum data collection from the Nextion Editor.

Calibrate four click points once; after that the script sets the ``txt`` field for every value on its
own and saves each variant via 'Save as' as a numbered sample. No manual clicking per sample. Because
the Editor locks the open file exclusively, 'Save as' onto a new name is used deliberately.

Calibration points:
  1. the text object t0 on the canvas (for selecting it)
  2. the txt value cell in the attribute panel (on the right)
  3. the 'File' menu (top left corner)
  4. the 'Save as' entry in the opened File menu

Requires: ``pip install pyautogui pyperclip pygetwindow``. Windows.

Usage:
  Test (3 samples):  python -m research.auto_collect --test
  Full:              python -m research.auto_collect
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

BASE_DIR = Path("samples")
VALUES = BASE_DIR / "checksum_txt_values.txt"
OUT_DIR = BASE_DIR / "reference"
SECONDS = 12  # calibration time per point


def load_values() -> list[str]:
    rows = []
    for line in VALUES.read_text(encoding="latin-1").splitlines():
        if line.startswith("#") or "\t" not in line:
            continue
        rows.append(line.split("\t", 1)[1])
    return rows


def capture(pyautogui, label: str, seconds: int = SECONDS):
    print(f"\n>>> Put the mouse EXACTLY on {label}")
    print(f"    and hold it still for {seconds} seconds (the countdown shows the current position):")
    for r in range(seconds, 0, -1):
        p = pyautogui.position()
        print(f"    {r:2d}s left   position x={p.x:<4d} y={p.y:<4d}      ", end="\r")
        time.sleep(1)
    p = pyautogui.position()
    print(f"\n    -> captured: x={p.x} y={p.y}")
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--n", type=int, default=0, help="only the first N values (0 = all)")
    args = ap.parse_args()

    try:
        import pyautogui
        import pyperclip
        import pygetwindow
        from pywinauto import Application
    except ImportError as e:
        print("Please install:  pip install pyautogui pyperclip pygetwindow pywinauto")
        print("(missing:", e.name, ")")
        return 1

    def get_dialog():
        """Fast detection of the native save dialog (#32770)."""
        try:
            a = Application(backend="win32").connect(class_name="#32770", timeout=0.4)
            wnd = a.window(class_name="#32770")
            return wnd if wnd.exists() else None
        except Exception:
            return None

    def click_button(dlg, titles) -> bool:
        for t in titles:
            try:
                b = dlg.child_window(title=t, class_name="Button")
                if b.exists():
                    b.click_input()
                    return True
            except Exception:
                pass
        return False

    _diag = {"done": False}

    def complete_save_dialog(name: str) -> bool:
        dlg = None
        for _ in range(25):
            dlg = get_dialog()
            if dlg is not None:
                break
            time.sleep(0.2)
        if dlg is None:
            print("      ! save dialog not found")
            return False
        if not _diag["done"]:
            _diag["done"] = True
            try:
                btns = [b.window_text() for b in dlg.descendants(class_name="Button")][:8]
                print(f"      [diagnostics] buttons: {btns}")
            except Exception as e:
                print("      [diagnostics]", e)
        # Write the file name into the first Edit (this worked for 000/001).
        try:
            e = dlg.child_window(class_name="Edit", found_index=0)
            e.set_focus()
            e.set_edit_text(name)
        except Exception:
            pass
        time.sleep(0.15)
        # Click the save button by its caption (more reliable than Enter).
        if not click_button(dlg, ("&Speichern", "Speichern", "&Save", "Save")):
            try:
                dlg.type_keys("{ENTER}")
            except Exception:
                pass
        # Confirm a possible overwrite prompt ('Speichern unter' = 'Save as' is the dialog itself).
        time.sleep(0.5)
        conf = get_dialog()
        if conf is not None and conf.window_text() != "Speichern unter":
            if not click_button(conf, ("&Ja", "Ja", "&Yes", "Yes")):
                try:
                    conf.type_keys("{ENTER}")
                except Exception:
                    pass
        # Bounded wait for it to close (max ~3 s), never hang.
        for _ in range(15):
            if get_dialog() is None:
                return True
            time.sleep(0.2)
        return True

    pyautogui.PAUSE = 0.12
    pyautogui.FAILSAFE = True
    values = load_values()
    if args.n:
        values = values[: args.n]
    elif args.test:
        values = values[:3]
    w, h = pyautogui.size()
    print(f"Coordinate space: {w}x{h}. {len(values)} samples -> {OUT_DIR.resolve()}")

    wins = [x for x in pygetwindow.getAllWindows() if "Nextion Editor" in (x.title or "")]
    editor = wins[0] if wins else None
    print("Editor:", editor.title if editor else "NOT found (keep the window in the foreground)")

    def activate():
        if editor:
            try:
                editor.activate()
            except Exception:
                pass
            time.sleep(0.2)

    import json
    from collections import namedtuple
    P = namedtuple("P", "x y")
    calib_file = OUT_DIR / ".calib.json"

    reuse = False
    if calib_file.exists():
        ans = input("Reuse the calibration saved earlier? [y/n] ").strip().lower()
        reuse = ans in ("j", "y", "ja", "")
    if reuse:
        c = json.loads(calib_file.read_text())
        t0_obj, txt_cell, file_menu, save_as = (P(*c["t0"]), P(*c["txt"]),
                                                P(*c["file"]), P(*c["saveas"]))
        print("Calibration reused.")
    else:
        print("\n=== CALIBRATION (4 points, one time, 12 s each) ===")
        print("Keep the Editor visible; text object t0 visible on the canvas.")
        t0_obj = capture(pyautogui, "the text object t0 ON THE CANVAS (the small text box)")
        txt_cell = capture(pyautogui, "the txt VALUE CELL in the attribute panel on the right (row 'txt')")
        file_menu = capture(pyautogui, "the 'File' menu in the TOP LEFT CORNER")
        if file_menu.x > w * 0.25:
            print(f"    ! WARNING: File menu at x={file_menu.x} looks too far right "
                  f"(expected < {int(w*0.25)}). Restart if necessary.")
        activate()
        print("\nBriefly opening the File menu so you can aim at 'Save as' ...")
        pyautogui.click(file_menu.x, file_menu.y)
        time.sleep(0.7)
        save_as = capture(pyautogui, "the 'Save as' entry in the open menu")
        pyautogui.press("escape")
        time.sleep(0.3)
        calib_file.write_text(json.dumps({
            "t0": list(t0_obj), "txt": list(txt_cell),
            "file": list(file_menu), "saveas": list(save_as)}))

    print("\nCalibration:")
    print(f"  t0 object : {t0_obj}")
    print(f"  txt cell  : {txt_cell}")
    print(f"  File menu : {file_menu}")
    print(f"  Save as   : {save_as}")
    if input("All correct? Start? [y/n] ").strip().lower() not in ("j", "y", "ja", ""):
        print("Aborted — please restart.")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # Pick a fresh name prefix that does NOT exist yet. That way a leftover file from an aborted run
    # that is still locked by the Editor can never collide -> no overwrite dialogs.
    import string
    tag = next((t for t in string.ascii_lowercase if not list(OUT_DIR.glob(f"cs{t}*.HMI"))), None)
    if tag is None:
        print("No free prefixes left — please clean up the cs* files manually.")
        return 1
    prefix = f"cs{tag}"
    print(f"Name prefix of this run: {prefix}  (files {prefix}000.HMI ...)")

    print("\nStarting in 10 seconds — hands off mouse and keyboard!")
    print("(Abort at any time: move the mouse into a screen corner.)")
    for r in range(10, 0, -1):
        print(f"    ... {r:2d}   ", end="\r")
        time.sleep(1)
    print("\nRun in progress.")

    for index, value in enumerate(values):
        name = f"{prefix}{index:03d}"
        activate()
        # Escape forces selection mode (no tool active) -> a canvas click can only SELECT t0,
        # never create a new component.
        pyautogui.press("escape")
        time.sleep(0.10)
        pyautogui.click(t0_obj.x, t0_obj.y)          # (re-)select t0
        time.sleep(0.2)
        pyautogui.doubleClick(txt_cell.x, txt_cell.y)  # start editing the txt cell
        time.sleep(0.30)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.06)
        # Set the clipboard IMMEDIATELY before pasting and verify it — protects against the user
        # copying something by accident during the run.
        for _ in range(6):
            pyperclip.copy(value)
            time.sleep(0.04)
            if pyperclip.paste() == value:
                break
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.14)
        pyautogui.press("enter")
        time.sleep(0.30)
        pyautogui.click(file_menu.x, file_menu.y)
        time.sleep(0.5)   # File menu
        pyautogui.click(save_as.x, save_as.y)                        # trigger Save as
        ok = complete_save_dialog(name)                             # close the dialog reliably
        time.sleep(0.4)
        print(f"  [{index:03d}/{len(values)}] {name}.HMI  txt={value!r}  {'ok' if ok else 'ERROR'}")

    print(f"\nDone: {len(values)} samples (cs000..).")
    print("Report 'test done' (test) or 'done' (full) — I will check the files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
