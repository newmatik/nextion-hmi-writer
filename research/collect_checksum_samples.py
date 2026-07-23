"""Collects checksum measurement samples automatically from the Nextion Editor.

The Editor computes the section checksum on save. To determine the linear checksum model (see
``nextion_checksum_solve``), many variants of the same base project are needed that differ only in
the ``txt`` of a text object.

This script sets every value from ``checksum_txt_values.txt`` into the ``txt`` field in turn, saves
(Ctrl+S overwrites the base file) and copies the file as a numbered sample.

Requires ``pip install pyautogui pyperclip pygetwindow``. Windows.

Usage:
  Test run (only 2 samples, to check):   python -m research.collect_checksum_samples --test
  Full:                                  python -m research.collect_checksum_samples

The base path is fixed to samples/reference/base_ck.HMI (changeable via --base).
"""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

BASE_DIR = Path("samples")
VALUES = BASE_DIR / "checksum_txt_values.txt"
SAMPLE_DIR = BASE_DIR / "checksum_samples"
DEFAULT_BASE = BASE_DIR / "reference" / "base_ck.HMI"


def load_values() -> list[str]:
    rows: list[str] = []
    for line in VALUES.read_text(encoding="latin-1").splitlines():
        if line.startswith("#") or "\t" not in line:
            continue
        rows.append(line.split("\t", 1)[1])
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, default=DEFAULT_BASE)
    parser.add_argument("--test", action="store_true", help="only 2 samples, to check")
    args = parser.parse_args()

    try:
        import pyautogui
        import pyperclip
        import pygetwindow
    except ImportError as exc:
        print("Please install:  pip install pyautogui pyperclip pygetwindow")
        print("(missing:", exc.name, ")")
        return 1

    base = args.base.resolve()
    if not base.exists():
        print("Base file not found:", base)
        print("Please create it in the Editor and save it as base_ck.HMI (see instructions).")
        return 1

    values = load_values()
    if args.test:
        values = values[:2]
    print(f"{len(values)} txt values will be set.  Base: {base}")
    print(f"Screen coordinate space per pyautogui: {pyautogui.size()}")

    # Find the Nextion Editor window and bring it to the foreground.
    wins = [w for w in pygetwindow.getAllWindows() if "Nextion Editor" in (w.title or "")]
    if not wins:
        print("WARNING: No window with title 'Nextion Editor' found.")
        print("Leave the Editor visible in the foreground; the script tries anyway.")
        editor = None
    else:
        editor = wins[0]
        print(f"Editor window: '{editor.title}' at ({editor.left},{editor.top}) "
              f"size {editor.width}x{editor.height}")

    print("\nCALIBRATION")
    print("Move the mouse EXACTLY onto the cell on the right that shows 'AAAAAA'")
    print("(attribute panel on the far right, row 'txt'), and HOLD STILL.")
    for remaining in range(8, 0, -1):
        pos = pyautogui.position()
        print(f"  {remaining}s ... current mouse position {pos}   ", end="\r")
        time.sleep(1)
    txt_cell = pyautogui.position()
    print(f"\nValue cell captured at {txt_cell}.")
    print("To verify: the mouse now moves there briefly ...")
    pyautogui.moveTo(txt_cell.x, txt_cell.y, duration=0.4)
    time.sleep(0.5)

    ok = input("Is the mouse on the txt cell showing 'AAAAAA'? [y/n] ").strip().lower()
    if ok not in ("j", "y", "ja", ""):
        print("Aborted. Please restart and calibrate correctly.")
        return 1

    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    print("\nRun starts in 3 seconds — do NOT move mouse/keyboard ...")
    time.sleep(3)

    for index, value in enumerate(values):
        if editor is not None:
            try:
                editor.activate()
            except Exception:
                pass
            time.sleep(0.15)
        pyperclip.copy(value)
        pyautogui.doubleClick(txt_cell.x, txt_cell.y)  # open the cell for editing
        time.sleep(0.20)
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.05)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.12)
        pyautogui.press("enter")  # commit the value
        time.sleep(0.20)
        pyautogui.hotkey("ctrl", "s")  # overwrites the base file
        time.sleep(0.70)
        shutil.copyfile(base, SAMPLE_DIR / f"probe_{index:03d}.HMI")
        print(f"  Sample {index:03d} set: {value!r}          ")

    print(f"\nDone: {len(values)} samples in {SAMPLE_DIR}")
    if args.test:
        print("TEST RUN done. Check the 2 samples; if txt varies correctly, start without --test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
