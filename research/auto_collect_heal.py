"""Self-healing, self-driving checksum data collection from the Nextion Editor.

t0 is selected canvas-free via the component dropdown. Even so, over hundreds of iterations the Editor
rarely (about 1 in 80) drops a stray component at (0,0) — a misclick that coordinate automation cannot
rule out entirely. Instead of preventing it, it is repaired:

  * After every sample the PREVIOUS (already unlocked) file is checked. If the page has grown,
    a stray component was created.
  * The clean base ``csa000.HMI`` is then reloaded via the toolbar button 'Open' and collection
    resumes from the affected sample. A dead run turns into a 3 second reload.

Coordinates come from ``reference/.calib_heal.json`` (combo, t0entry, txt, file, saveas, openbtn).
In ``--auto`` mode everything runs without prompts; the script reloads the base at the start.

Usage:
  Test the reload:  python -m research.auto_collect_heal --test-reload
  Test:             python -m research.auto_collect_heal --auto --n 150
  Full:             python -m research.auto_collect_heal --auto
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
CALIB = OUT_DIR / ".calib_heal.json"
BASE_HMI = OUT_DIR / "csa000.HMI"
BASE_LEN = 1735
P = namedtuple("P", "x y")


def load_values():
    rows = []
    for line in VALUES.read_text(encoding="latin-1").splitlines():
        if line.startswith("#") or "\t" not in line:
            continue
        rows.append(line.split("\t", 1)[1])
    return rows


def page_len(path: Path):
    """0.pa page length; None if locked/missing. Reads only the header (fast)."""
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0)
    ap.add_argument("--auto", action="store_true", help="no prompts, reload the base at the start")
    ap.add_argument("--test-reload", action="store_true", help="only run the reload once")
    ap.add_argument("--no-initial-reload", action="store_true")
    ap.add_argument("--indices", type=str, default="", help="re-collect only these indices (prefix cya)")
    args = ap.parse_args()

    try:
        import pyautogui
        import pyperclip
        import pygetwindow
        from pywinauto import Application
    except ImportError as e:
        print("pip install pyautogui pyperclip pygetwindow pywinauto  (missing:", e.name, ")")
        return 1

    if not BASE_HMI.exists():
        print("MISSING:", BASE_HMI)
        return 1
    base_abs = str(BASE_HMI.resolve())
    base_name = BASE_HMI.name

    if not CALIB.exists():
        print("MISSING calibration:", CALIB)
        return 1
    c = json.loads(CALIB.read_text())
    combo, t0entry, txt_cell, file_menu, save_as, openbtn = (
        P(*c[k]) for k in ("combo", "t0entry", "txt", "file", "saveas", "openbtn"))

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

    def focus_editor():
        """Physical click on the title bar — forces the foreground (guards against click-through)."""
        activate()
        pyautogui.click(1300, 18)
        time.sleep(0.25)

    def wait_dialog(tries=25):
        for _ in range(tries):
            d = get_dialog()
            if d:
                return d
            time.sleep(0.2)
        return None

    def editor_title():
        ws = [x for x in pygetwindow.getAllWindows() if "Nextion Editor" in (x.title or "")]
        return ws[0].title if ws else ""

    def get_dialog():
        try:
            a = Application(backend="win32").connect(class_name="#32770", timeout=0.4)
            wnd = a.window(class_name="#32770")
            return wnd if wnd.exists() else None
        except Exception:
            return None

    def click_button(dlg, titles):
        for t in titles:
            try:
                b = dlg.child_window(title=t, class_name="Button")
                if b.exists():
                    b.click_input()
                    return True
            except Exception:
                pass
        return False

    def dismiss_stray():
        d = get_dialog()
        # 'Speichern unter' (= 'Save as') and 'Öffnen' (= 'Open') are the file dialogs themselves.
        if d is not None and d.window_text() not in ("Speichern unter", "Öffnen", "Open"):
            if not click_button(d, ("&Ja", "Ja", "&Yes", "Yes")):
                try:
                    d.type_keys("{ENTER}")
                except Exception:
                    pass
            time.sleep(0.3)

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
        if not click_button(dlg, ("&Speichern", "Speichern", "&Save", "Save")):
            try:
                dlg.type_keys("{ENTER}")
            except Exception:
                pass
        time.sleep(0.5)
        dismiss_stray()
        for _ in range(15):
            if get_dialog() is None:
                return True
            time.sleep(0.2)
        return True

    def reload_base():
        """Reload the clean base via the toolbar button 'Open'."""
        dlg = None
        for attempt in range(3):
            focus_editor()  # force the foreground, otherwise the activation swallows the click
            pyautogui.click(openbtn.x, openbtn.y)
            time.sleep(0.7)
            dismiss_stray()  # possibly 'unsaved changes'
            dlg = wait_dialog(15)
            if dlg:
                break
            print(f"      (Open dialog attempt {attempt+1} yielded no dialog, retrying)")
        if not dlg:
            print("      ! Open dialog not found")
            return False
        # Put the path into the Edit and send ENTER DIRECTLY to the Edit — the 'Öffnen' button is a
        # split button whose click_input does not trigger reliably.
        try:
            edit = dlg.child_window(class_name="Edit", found_index=0)
            edit.set_focus()
            edit.set_edit_text(base_abs)
            time.sleep(0.2)
            edit.type_keys("{ENTER}")
        except Exception:
            pass
        time.sleep(0.6)
        # Recovery: one ENTER either triggers 'Öffnen' or acknowledges an error box ('file is in
        # use', if the base is already open). Cancel afterwards if needed.
        for _ in range(5):
            if get_dialog() is None:
                break
            try:
                get_dialog().type_keys("{ENTER}")
            except Exception:
                pass
            time.sleep(0.5)
        if get_dialog() is not None:
            pyautogui.press("escape")
            time.sleep(0.4)
            if get_dialog() is not None:
                pyautogui.press("escape")
                time.sleep(0.4)
        dismiss_stray()  # 'replace current project?' -> 'Ja' (Yes)
        time.sleep(1.2)
        # Ground truth: is the base in the window title?
        loaded = base_name.lower() in editor_title().lower()
        if not loaded:
            print("      ! base not in title:", editor_title()[-46:])
        return loaded

    def select_t0():
        pyautogui.click(combo.x, combo.y)
        time.sleep(0.35)
        pyautogui.click(t0entry.x, t0entry.y)
        time.sleep(0.30)

    def do_sample(name, value):
        focus_editor()
        select_t0()
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
        pyautogui.click(file_menu.x, file_menu.y)
        time.sleep(0.5)
        pyautogui.click(save_as.x, save_as.y)
        return save_dialog(name)

    print(f"combo={combo} t0entry={t0entry} txt={txt_cell} file={file_menu} saveas={save_as} open={openbtn}")

    if args.test_reload:
        print("RELOAD TEST in 4s ...")
        time.sleep(4)
        ok = reload_base()
        print("reload_base ->", "OK" if ok else "ERROR")
        return 0

    if not args.auto:
        if input("Start? [y/n] ").strip().lower() not in ("j", "y", "ja", ""):
            return 1

    # Refill mode: only individual indices, prefix fixed to 'cya', no self-healing needed.
    if args.indices:
        sel = [int(x) for x in args.indices.split(",") if x.strip()]
        print(f"Refilling cya indices: {sel}  (starting in 5s, hands off)")
        for r in range(5, 0, -1):
            print(f"  {r} ", end="\r")
            time.sleep(1)
        print()
        for i in sel:
            ok = do_sample(f"cya{i:03d}", values[i])
            time.sleep(0.25)
            print(f"  refill cya{i:03d}.HMI  {'ok' if ok else 'ERROR'}", flush=True)
        # Unlock the last file so that the solver can read all cya files.
        reload_base()
        print("Refill done (base reloaded, all cya unlocked).", flush=True)
        return 0

    prefix = "cy" + next(t for t in string.ascii_lowercase if not list(OUT_DIR.glob(f"cy{t}*.HMI")))
    print(f"Prefix: {prefix}   base: {base_abs}   samples: {len(values)}")

    countdown = 5 if args.auto else 10
    print(f"Starting in {countdown}s — hands off mouse/keyboard/clipboard!")
    for r in range(countdown, 0, -1):
        print(f"  {r:2d} ", end="\r")
        time.sleep(1)
    print()

    if not args.no_initial_reload:
        print("Initial reload onto the clean base ...")
        if not reload_base():
            print("!! initial reload failed — aborting.")
            return 2

    heals = 0
    i = 0
    while i < len(values):
        name = f"{prefix}{i:03d}"
        ok = do_sample(name, values[i])
        time.sleep(0.25)
        tag = "ok" if ok else "ERROR"
        if i >= 1:
            L = page_len(OUT_DIR / f"{prefix}{i-1:03d}.HMI")
            if L is not None and L != BASE_LEN:
                heals += 1
                print(f"  [{i:03d}] STRAY at {i-1:03d} (len {L}); reload #{heals}, repeating from {i-1:03d}",
                      flush=True)
                if not reload_base():
                    print("  !! reload failed — aborting.")
                    return 2
                i -= 1
                continue
        print(f"  [{i:03d}/{len(values)}] {name}.HMI  {tag}", flush=True)
        i += 1

    # Unlock the last sample and check it.
    last = len(values) - 1
    if last >= 1:
        do_sample(f"{prefix}zzz", values[last])
        time.sleep(0.25)
        L = page_len(OUT_DIR / f"{prefix}{last:03d}.HMI")
        if L is not None and L != BASE_LEN:
            print(f"  last sample {last:03d} grew (len {L}) — reload + re-collect.", flush=True)
            reload_base()
            do_sample(f"{prefix}{last:03d}", values[last])
        try:
            (OUT_DIR / f"{prefix}zzz.HMI").unlink()
        except OSError:
            pass

    print(f"\nDone: {len(values)} samples ({prefix}). Self-heals: {heals}.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
