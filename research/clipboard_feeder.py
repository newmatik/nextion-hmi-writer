"""Clipboard helper with auto-advancing for the checksum data collection.

The Nextion Editor locks the open ``.HMI`` exclusively, so every variant is written via
``Speichern unter`` (Save as) as a NEW, separate file in the watch folder (``samples/reference``).

This script puts the next ``txt`` value into the clipboard and advances AUTOMATICALLY as soon as it
detects a new sample file — no separate keypress in the terminal needed. That way the most common
mishap (forgetting to re-paste) cannot happen any more, as long as you really do re-paste for every
sample and the txt cell changes visibly.

Requires ``pip install pyperclip``.

Per sample:
  1. Script shows the value (it is in the clipboard).
  2. In the Editor:  double-click the txt cell -> Ctrl+A -> Ctrl+V -> Enter
     IMPORTANT: check that the txt cell now shows THIS value (not the old one!).
  3. File -> "Speichern unter" (Save as) -> folder checksum_samples -> any new name -> Save.
  4. The script detects the new file and advances to the next value by itself.
"""

from __future__ import annotations

import time
from pathlib import Path

BASE_DIR = Path("samples")
VALUES = BASE_DIR / "checksum_txt_values.txt"
# The Save-as dialog last showed the reference folder; the samples are written there.
WATCH_DIR = BASE_DIR / "reference"


def load_values() -> list[str]:
    rows: list[str] = []
    for line in VALUES.read_text(encoding="latin-1").splitlines():
        if line.startswith("#") or "\t" not in line:
            continue
        rows.append(line.split("\t", 1)[1])
    return rows


def hmi_set() -> set[str]:
    """Current .HMI files in the watch folder (excluding reference/base files)."""
    out = set()
    for p in list(WATCH_DIR.glob("*.HMI")) + list(WATCH_DIR.glob("*.hmi")):
        name = p.name.lower()
        if name.startswith("ref_") or name.startswith("base_ck"):
            continue
        out.add(p.name)
    return out


def main() -> int:
    try:
        import pyperclip
    except ImportError:
        print("Please install:  pip install pyperclip")
        return 1

    values = load_values()
    WATCH_DIR.mkdir(parents=True, exist_ok=True)
    target = WATCH_DIR.resolve()
    print("=" * 70)
    print(f"{len(values)} values.  Save as -> {target}")
    print("Per sample:  double-click txt -> Ctrl+A -> Ctrl+V -> Enter  (txt MUST change!)")
    print("           then Save as (new name) into the folder above.")
    print("The script advances automatically as soon as a new file appears.")
    print("=" * 70)

    # Count what is already there separately from what this run collects: the folder normally holds
    # the base the Editor reloads to unlock, and hmi_set() only filters the ref_/base_ck prefixes,
    # so a base under any other name (cya100.HMI, say) would otherwise be reported as a sample.
    present = len(hmi_set())
    collected = 0
    for index, value in enumerate(values):
        pyperclip.copy(value)
        print(f"\n[{index:03d}/{len(values)}]  PASTE (Ctrl+V) and then save:")
        print(f"      txt value:  {value!r}")
        # wait for a new file
        while len(hmi_set()) <= present + collected:
            time.sleep(0.3)
        collected = len(hmi_set()) - present
        print(f"      -> new sample detected ({collected} collected).")

    print(f"\nDone: {collected} samples in {target}. Close the Editor, then report 'done'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
