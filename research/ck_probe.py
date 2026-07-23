"""Reusable Editor probe for checksum experiments.

Sets t0.txt to arbitrary values, saves one named .HMI per value (Save as), and finally reloads the
clean base (which unlocks every written file). Uses the verified coordinates and the correct filename
field position of the Open dialog (the found_index=0 Edit there is the search field!).

Usage:  python -m research.ck_probe <base.HMI> <name1> <txt1> [<name2> <txt2> ...]
txt may name a path via @file:, whose content (a single line) is then used as txt.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pyautogui
import pyperclip
import pygetwindow
from pywinauto import Application

COMBO = (1645, 331)
T0 = (1651, 373)
TXT = (1691, 776)
FILE = (791, 57)
SAVEAS = (842, 225)
OPENBTN = (816, 94)
FNAME = (840, 918)      # filename field in the file dialog
TITLEBAR = (1300, 18)

pyautogui.PAUSE = 0.12
pyautogui.FAILSAFE = True


def editor_win():
    ws = [w for w in pygetwindow.getAllWindows() if "Nextion Editor" in (w.title or "")]
    return ws[0] if ws else None


def title():
    w = editor_win()
    return w.title if w else ""


def focus():
    w = editor_win()
    if w:
        try:
            w.activate()
        except Exception:
            pass
    pyautogui.click(*TITLEBAR)
    time.sleep(0.25)


def get_dialog():
    try:
        a = Application(backend="win32").connect(class_name="#32770", timeout=0.4)
        wnd = a.window(class_name="#32770")
        return wnd if wnd.exists() else None
    except Exception:
        return None


def open_file(path: str):
    # IMPORTANT: the Windows dialog accepts only backslashes; forward slashes -> "Dateiname ungueltig"
    # (invalid filename).
    path = os.path.normpath(path)
    for _ in range(3):
        focus()
        pyautogui.click(*OPENBTN)
        time.sleep(0.8)
        # 'Save changes?' -> 'Nein' (No, discard) so that the Open dialog appears.
        d = get_dialog()
        if d is not None and d.window_text() not in ("Öffnen", "Open"):
            clicked = False
            for t in ("&Nein", "Nein", "&No", "No"):
                try:
                    b = d.child_window(title=t, class_name="Button")
                    if b.exists():
                        b.click_input()
                        clicked = True
                        break
                except Exception:
                    pass
            if not clicked:
                pyautogui.press("n")
            time.sleep(0.6)
        if get_dialog():
            break
    pyautogui.click(*FNAME)
    time.sleep(0.3)
    pyautogui.hotkey("ctrl", "a")
    pyautogui.typewrite(path, interval=0.004)
    time.sleep(0.2)
    pyautogui.press("enter")
    time.sleep(1.8)
    # dismiss any error box, then verify the title
    if get_dialog():
        pyautogui.press("escape")
        time.sleep(0.3)
    want = os.path.basename(path).lower()
    if want not in title().lower():
        # Do NOT continue — otherwise a follow-up action would save into the wrong file.
        raise RuntimeError(f"open_file: expected {want}, title={title()[-50:]}")


def select_t0():
    pyautogui.click(*COMBO)
    time.sleep(0.35)
    pyautogui.click(*T0)
    time.sleep(0.30)


def set_txt(value: str):
    pyautogui.doubleClick(*TXT)
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


FNAME_SAVE = (900, 825)     # filename field in the save dialog (coordinate, reliable)
SPEICHERN_BTN = (1393, 957)


def save_as(name: str):
    # Open the dialog robustly: force it to the foreground, deselect, retry the menu if needed.
    dlg = None
    for attempt in range(3):
        focus()
        pyautogui.press("escape")
        time.sleep(0.2)
        pyautogui.click(*FILE)
        time.sleep(0.6)
        pyautogui.click(*SAVEAS)
        for _ in range(15):
            dlg = get_dialog()
            if dlg:
                break
            time.sleep(0.2)
        if dlg:
            break
    if not dlg:
        return False
    # Set the filename by coordinate — in the modern dialog the pywinauto Edit hits the search field.
    pyautogui.click(*FNAME_SAVE)
    time.sleep(0.25)
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.05)
    pyautogui.typewrite(name, interval=0.02)
    time.sleep(0.2)
    pyautogui.click(*SPEICHERN_BTN)
    time.sleep(0.6)
    # Overwrite prompt ('... already exists. Replace?') -> Yes.
    # CAUTION: the default button is 'Nein' (= No), therefore do NOT press Enter.
    d = get_dialog()
    if d is not None and d.window_text() != "Speichern unter":
        clicked = False
        for t in ("&Ja", "Ja", "&Yes", "Yes"):
            try:
                b = d.child_window(title=t, class_name="Button")
                if b.exists():
                    b.click_input()
                    clicked = True
                    break
            except Exception:
                pass
        if not clicked:
            pyautogui.click(973, 554)  # 'Ja' (= Yes) button by coordinate
        time.sleep(0.4)
    for _ in range(15):
        if get_dialog() is None:
            return True
        time.sleep(0.2)
    return True


def main():
    base = str(Path(sys.argv[1]).resolve())
    pairs = sys.argv[2:]
    print("Editor:", title())
    open_file(base)
    print("after open:", title())
    for k in range(0, len(pairs), 2):
        name = pairs[k]
        val = pairs[k + 1]
        if val.startswith("@file:"):
            val = Path(val[6:]).read_text(encoding="latin-1").splitlines()[0]
        select_t0()
        set_txt(val)
        ok = save_as(name)
        print(f"  {name}.HMI  txt[{len(val)}]  {'ok' if ok else 'FAILED'}", flush=True)
    open_file(base)  # unlock
    print("done, base reloaded:", title())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
