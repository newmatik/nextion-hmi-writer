"""Robust, semi-automatic checksum data collection — without coordinate automation.

Division of labour:
  * YOU in the Editor: edit the text cell, paste, save (focus stays safely with the Editor).
  * THIS SCRIPT: puts the next txt value into the clipboard and copies the saved base file
    automatically as a numbered sample as soon as it detects a save.

That way no focus or numbering error can occur. Requires ``pip install pyperclip``.

Procedure:
  1. Editor open with base_ck.HMI, text object t0 selected (txt row visible).
  2. Start this script. It puts the first value into the clipboard.
  3. In the Editor, per sample:  double-click txt cell  ->  Ctrl+A  ->  Ctrl+V  ->  Enter  ->  Ctrl+S
     The script reports every detected save and readies the next value.
  4. After 56 samples it stops; the script terminates by itself.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

BASE_DIR = Path("samples")
VALUES = BASE_DIR / "checksum_txt_values.txt"
SAMPLE_DIR = BASE_DIR / "checksum_samples"
BASE_HMI = BASE_DIR / "reference" / "base_ck.HMI"


def load_values() -> list[str]:
    rows: list[str] = []
    for line in VALUES.read_text(encoding="latin-1").splitlines():
        if line.startswith("#") or "\t" not in line:
            continue
        rows.append(line.split("\t", 1)[1])
    return rows


def main() -> int:
    try:
        import pyperclip
    except ImportError:
        print("Please install:  pip install pyperclip")
        return 1

    if not BASE_HMI.exists():
        print("Base file missing:", BASE_HMI)
        return 1

    values = load_values()
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"{len(values)} values to capture. Samples -> {SAMPLE_DIR}\n")

    def stamp() -> tuple[float, int]:
        st = BASE_HMI.stat()
        return (st.st_mtime, st.st_size)

    last = stamp()
    index = 0
    pyperclip.copy(values[0])
    print(f"[{index:02d}/{len(values)}] Value in clipboard: {values[0]!r}")
    print("   Now in the Editor:  double-click txt -> Ctrl+A -> Ctrl+V -> Enter -> Ctrl+S")

    while index < len(values):
        time.sleep(0.4)
        try:
            now = stamp()
        except FileNotFoundError:
            continue
        if now != last:
            # Save detected (mtime/size changed). Wait briefly, then copy.
            time.sleep(0.6)
            try:
                shutil.copyfile(BASE_HMI, SAMPLE_DIR / f"probe_{index:03d}.HMI")
            except PermissionError:
                # Editor may still be writing; try again.
                time.sleep(0.6)
                shutil.copyfile(BASE_HMI, SAMPLE_DIR / f"probe_{index:03d}.HMI")
            print(f"   -> probe_{index:03d}.HMI stored.")
            last = now
            index += 1
            if index < len(values):
                pyperclip.copy(values[index])
                print(f"[{index:02d}/{len(values)}] Next value ready: {values[index]!r}")

    print(f"\nDone: {len(values)} samples captured.")
    print("Report 'done' — I will check the samples and solve the checksum.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
