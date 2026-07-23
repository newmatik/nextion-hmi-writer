"""Robust, self-observing control of the Nextion Editor (Windows).

This layer replaces the scattered `ck_*`/`auto_collect_*` drivers with a closed
perception-action-verification-healing loop. Core principles:

* **Canonical geometry.** The editor window is maximized; all coordinates apply to the
  maximized position on a 1800x1130 screen. If the geometry changes, recalibrate.
* **Native dialogs.** File dialogs (`#32770`) are operated through the pywinauto control tree,
  buttons by title (`Speichern`/`Öffnen`/`Ja`/`Nein`). The overwrite default is **Nein** (= No) —
  never blind Enter.
* **Hard invariants.** Never save onto a colliding name (scan for fresh names). Never
  continue if the loaded file is not the expected one (`open_file` raises) — this prevents
  the corruption of the wrong file. The base is a write-protected template.
* **Verification through the saved file.** Editing attributes in the WinForms grid is done by
  coordinate; the truth is the page length/checksum of the saved `.HMI`
  (`read_page_section`). Every sample is checked this way.
* **Escalation to the human/Claude.** If healing fails, `stuck.png` + control tree are
  written and the run stops cleanly.

The attribute cells (`txt`, `txt_maxl`) sit below the visible area; after selecting `t0`
exactly one click on the scrollbar brings them to fixed positions (`scroll_to_txt`).
"""

from __future__ import annotations

import glob
import os
import time
import warnings
from pathlib import Path

import pyautogui
import pyperclip
import pygetwindow

warnings.filterwarnings("ignore")
pyautogui.PAUSE = 0.10
pyautogui.FAILSAFE = True

# --- Canonical coordinates (maximized window, 1800x1130) ---------------------------------------
TITLEBAR = (900, 11)
MENU_FILE = (22, 49)
FILE_SAVEAS = (74, 220)
TB_OPEN = (47, 86)
TB_SAVE = (199, 86)
# Calibration of the attribute panel. CAUTION: these values apply to the panel position with
# ATTR_COMBO_REF_TOP; after an editor restart the panel can shift vertically — the offset is
# compensated at runtime via attr_offset(). Measured on 2026-07-16 after a restart:
# selection box top=550, grid rows ycen637 / pw666 / txt695 / txt_maxl724 (row spacing ~29).
GRID_SB_UP = (1783, 637)           # attribute grid scrollbar: up arrow (1 row per click)
GRID_SB_DOWN = (1783, 925)         # attribute grid scrollbar: down arrow (1 row per click)
TXT_ROW = 13                       # down-clicks from the top of the grid until 'txt' is visible
TXT_CELL = (1700, 695)             # value cell 'txt'      (after scroll_to_txt)
TXTMAXL_CELL = (1700, 724)         # value cell 'txt_maxl' (one row below)
# WinForms message 'MessageForm' (centered): button coordinates (fallback), layout Ja/Nein/Abbrechen
MSGFORM_BTN = {"yes": (952, 619), "no": (1047, 619), "cancel": (1142, 619), "ok": (952, 619)}

SHOT = os.path.join(os.environ.get("TEMP", "."), "nextion_editor_screen.png")
STUCK_PNG = os.path.join(os.environ.get("TEMP", "."), "nextion_editor_stuck.png")
STUCK_TXT = os.path.join(os.environ.get("TEMP", "."), "nextion_editor_stuck.txt")

# Canonical state: full screen on 1800x1130. All coordinates apply ONLY to this.
EXPECTED_SCREEN = (1800, 1130)
# When maximized, Windows reports the window slightly past the edge (frame). Tolerance is generous.
MAX_LEFT_MAX = 4          # left <= 4  (usually -10 .. 0)
MIN_WIDTH = 1790          # width >= 1790 (usually 1820)
MIN_HEIGHT = 1060         # height >= 1060 (usually 1090)


class EditorStuck(RuntimeError):
    """The control could no longer reach the expected state; escalation triggered."""


class EditorAborted(RuntimeError):
    """Run terminated by emergency stop, time budget or action budget."""


# --- Emergency stop and run limits -------------------------------------------------------------
# Lesson from an incident: a retry cascade blindly clicked the File button for minutes on end and
# could no longer be stopped (the machine had to be hard powered off). Therefore the rules now are:
#   * Create the EMERGENCY-STOP FILE -> every GUI action aborts immediately.
#   * Time and action budget -> a run cannot keep clicking forever.
#   * Fail fast instead of hammering: whatever does not work after a few attempts aborts the run.
ABORT_FILE = os.path.join(os.environ.get("TEMP", "."), "nextion_stop.txt")
_DEADLINE = None
_ACTIONS_LEFT = None


def set_run_limits(max_seconds: float | None = None, max_actions: int | None = None) -> None:
    """Set hard upper limits for a run (always call before longer automation runs)."""
    global _DEADLINE, _ACTIONS_LEFT
    _DEADLINE = (time.time() + max_seconds) if max_seconds else None
    _ACTIONS_LEFT = max_actions
    try:
        if os.path.exists(ABORT_FILE):
            os.remove(ABORT_FILE)          # clean up an old emergency stop
    except OSError:
        pass


def check_abort() -> None:
    """Check before every GUI action group: emergency-stop file, time budget, action budget."""
    global _ACTIONS_LEFT
    if os.path.exists(ABORT_FILE):
        raise EditorAborted(f"EMERGENCY STOP: {ABORT_FILE} exists — run stopped")
    if _DEADLINE is not None and time.time() > _DEADLINE:
        raise EditorAborted("Time budget exhausted — run stopped")
    if _ACTIONS_LEFT is not None:
        _ACTIONS_LEFT -= 1
        if _ACTIONS_LEFT < 0:
            raise EditorAborted("Action budget exhausted — run stopped")


# --- Window / perception -----------------------------------------------------------------------

def editor_win():
    for w in pygetwindow.getAllWindows():
        t = (w.title or "")
        if t.startswith("Nextion Editor") and "Visual Studio Code" not in t:
            return w
    return None


def title() -> str:
    w = editor_win()
    return w.title if w else ""


def screenshot(path: str = SHOT) -> str:
    time.sleep(0.15)
    pyautogui.screenshot().save(path)
    return path


def geometry_ok() -> bool:
    """True if screen and maximized window match the canonical position."""
    try:
        if tuple(pyautogui.size()) != EXPECTED_SCREEN:
            return False
    except Exception:
        return False
    w = editor_win()
    if not w:
        return False
    return (w.left <= MAX_LEFT_MAX and w.width >= MIN_WIDTH and w.height >= MIN_HEIGHT)


def ensure_geometry(retries: int = 4) -> None:
    """Force and verify the canonical full-screen position. Raises if it does not hold.

    This is the central safeguard: NEVER click blindly while the geometry is wrong —
    otherwise clicks land in the toolbox and create stray components (see project memory).
    """
    if geometry_ok():
        return
    for _ in range(retries):
        w = editor_win()
        if not w:
            time.sleep(0.5)
            continue
        try:
            # pygetwindow.maximize() is not reliable with the 32-bit WinForms application:
            # the call can return without an error even though the window only stays docked.
            # ShowWindow(SW_MAXIMIZE) by contrast forces the canonical position reproducibly.
            import ctypes

            if getattr(w, "isMinimized", False):
                w.restore()
            ctypes.windll.user32.ShowWindow(w._hWnd, 3)  # SW_MAXIMIZE
            ctypes.windll.user32.SetForegroundWindow(w._hWnd)
            time.sleep(0.15)
            if not (w.left <= MAX_LEFT_MAX and w.width >= MIN_WIDTH and w.height >= MIN_HEIGHT):
                w.maximize()
            time.sleep(0.4)
        except Exception:
            pass
        if geometry_ok():
            return
        time.sleep(0.5)
    escalate(f"Geometry not canonical: screen={tuple(pyautogui.size())} "
             f"win={(lambda w: (w.left, w.top, w.width, w.height) if w else None)(editor_win())}")


def foreground_title() -> str:
    try:
        aw = pygetwindow.getActiveWindow()
        return (aw.title or "") if aw else ""
    except Exception:
        return ""


def focus():
    """Reliably bring the editor to the foreground.

    Order matters: (1) canonical geometry, (2) blocking popups GONE (a modal cannot be bypassed
    with a title-bar click), (3) activate window + physical title-bar click,
    (4) verify the foreground. This way clicks never land in VS Code or behind a modal.
    """
    check_abort()                                  # emergency stop/budget before EVERY action group
    ensure_geometry()
    clear_popups()
    for _ in range(3):
        w = editor_win()
        if w:
            try:
                w.activate()
            except Exception:
                pass
        pyautogui.click(*TITLEBAR)
        time.sleep(0.2)
        ft = foreground_title()
        if ft.startswith("Nextion Editor") or ft in ("MessageForm", ""):
            return
        clear_popups()
        time.sleep(0.2)


def setup() -> None:
    """Find window, activate, maximize, report geometry."""
    w = editor_win()
    if not w:
        raise EditorStuck("No Nextion Editor window found.")
    try:
        if getattr(w, "isMinimized", False):
            w.restore()
        w.activate()
        time.sleep(0.2)
        w.maximize()
    except Exception:
        pass
    time.sleep(0.4)
    focus()


# --- Native dialogs ----------------------------------------------------------------------------

def get_dialog(timeout: float = 0.6):
    from pywinauto import Application
    try:
        app = Application(backend="win32").connect(class_name="#32770", timeout=timeout)
        wnd = app.window(class_name="#32770")
        return wnd if wnd.exists() else None
    except Exception:
        return None


def wait_dialog(timeout: float = 6.0):
    end = time.time() + timeout
    while time.time() < end:
        d = get_dialog(0.3)
        if d is not None:
            return d
        time.sleep(0.2)
    return None


def dialog_button(dlg, titles) -> bool:
    """Clicks a button by title in a #32770 dialog (first via child_window, then descendants)."""
    for t in titles:
        try:
            b = dlg.child_window(title=t, class_name="Button")
            if b.exists():
                b.click_input()
                return True
        except Exception:
            pass
    low = [t.replace("&", "").lower() for t in titles]
    try:
        for c in dlg.descendants():
            try:
                if "button" in c.friendly_class_name().lower() and (c.window_text() or "").replace("&", "").strip().lower() in low:
                    c.click_input()
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def message_form():
    """The editor's WinForms message ('MessageForm': save changes?, version mismatch, ...)."""
    from pywinauto import Application
    try:
        app = Application(backend="win32").connect(title="MessageForm", timeout=0.5)
        w = app.window(title="MessageForm")
        return w if w.exists() else None
    except Exception:
        return None


def handle_message_form(prefer, timeout: float = 0.5) -> bool:
    """If a MessageForm is open, click the desired button (English+German).

    `prefer` e.g. ["No"] to discard, ["Yes"] to save, ["OK"]. Coordinate fallback only for
    the known cases. Returns True if a MessageForm was handled.
    """
    end = time.time() + timeout
    w = None
    while time.time() < end:
        w = message_form()
        if w is not None:
            break
        time.sleep(0.1)
    if w is None:
        return False
    try:
        buttons = [c for c in w.descendants() if "button" in c.friendly_class_name().lower()]
    except Exception:
        buttons = []
    # process prefer IN ORDER (on the 'save changes?' prompt No must win over Yes)
    for p in prefer:
        variants = {p.replace("&", "").lower()}
        de = {"yes": "Ja", "no": "Nein", "cancel": "Abbrechen", "ok": "OK"}.get(p.lower())
        if de:
            variants.add(de.lower())
        for c in buttons:
            try:
                if (c.window_text() or "").replace("&", "").strip().lower() in variants:
                    c.click_input()
                    time.sleep(0.4)
                    return True
            except Exception:
                pass
    key = prefer[0].lower()
    if key in MSGFORM_BTN:
        pyautogui.click(*MSGFORM_BTN[key])
    time.sleep(0.4)
    return True


FOREIGN_DISMISS = ("Jetzt nicht", "Not now", "Später", "Later", "Abbrechen", "Cancel",
                   "Schließen", "Close", "Nein", "No", "Verwerfen", "Dismiss", "OK")


def dismiss_foreign_popup() -> bool:
    """Click away foreign Windows popups (e.g. 'Anmeldung erforderlich' = sign-in required,
    CrossDevice resume).

    Such system messages appear spontaneously, steal the focus and block the automation.
    Nextion windows and our own #32770/MessageForm dialogs are NOT touched.
    """
    from pywinauto import Desktop
    main = editor_win()
    main_h = main._hWnd if main else None
    try:
        wins = Desktop(backend="win32").windows()   # can raise when windows are disappearing
    except Exception:
        return False
    for w in wins:
        try:
            cls = w.class_name()
            txt = w.window_text() or ""
            if w.handle == main_h or cls == "#32770" or txt == "MessageForm":
                continue
            if not (cls in ("Shell_Dialog",) or "Resume" in cls or txt in ("Anmeldung erforderlich",)):
                continue
            for c in w.descendants():
                try:
                    if "button" in c.friendly_class_name().lower() and (c.window_text() or "").strip() in FOREIGN_DISMISS:
                        c.click_input()
                        time.sleep(0.5)
                        return True
                except Exception:
                    pass
        except Exception:
            pass
    return False


def clear_popups(rounds: int = 4, save_changes: str = "No") -> bool:
    """Read and close blocking popups anywhere on the screen — the self-unjammer.

    Handles the WinForms 'MessageForm' (save changes? -> `save_changes`, otherwise OK/Yes) and
    #32770 message boxes (OK/Ja/Nein). The actual file dialogs ('Öffnen'/'Speichern unter') are
    deliberately NOT touched — open_file/save_as fill those in inline.

    Fast path: if the Nextion main window is in the foreground, no modal popup can be blocking —
    then return immediately (saves the expensive pywinauto connections in the normal case).
    """
    if foreground_title().startswith("Nextion Editor"):
        return False
    acted_any = False
    for _ in range(rounds):
        acted = False
        if dismiss_foreign_popup():          # foreign Windows nags first (they steal the focus)
            acted = True
        if message_form() is not None:
            handle_message_form([save_changes, "OK", "Yes"], timeout=0.3)
            acted = True
        d = get_dialog(0.2)
        if d is not None:
            t = ""
            try:
                t = d.window_text()
            except Exception:
                pass
            if t not in ("Speichern unter", "Save As", "Öffnen", "Open") and _filename_edit(d) is None:
                # message box (buttons, but no filename edit) -> confirm
                if not dialog_button(d, ("OK", "&OK", "Ja", "&Ja", "Yes", "&Yes", "Nein", "&Nein", "No", "&No")):
                    pyautogui.press("enter")
                acted = True
        if not acted:
            break
        acted_any = True
        time.sleep(0.3)
    return acted_any


def menu_open() -> bool:
    """True if the File dropdown is open — protection against toolbox misclicks.

    Nextion is a WinForms application: the menu is NOT a native `#32768` but its own top-level
    popup of the class `WindowsForms10.Window` with an EMPTY title, hanging at the top left below
    the File menu (measured: rect ~ (1,63)-(242,439)). The 'MessageForm' drops out because it has
    a title and sits centered.
    """
    from pywinauto import Desktop
    main = editor_win()
    main_h = main._hWnd if main else None
    try:
        wins = Desktop(backend="win32").windows()
    except Exception:
        return False
    for w in wins:
        try:
            if w.handle == main_h:
                continue
            if not w.class_name().startswith("WindowsForms10.Window"):
                continue
            if (w.window_text() or "").strip():
                continue
            r = w.rectangle()
            if r.left < 60 and 50 < r.top < 110 and (r.bottom - r.top) > 250:
                return True
        except Exception:
            pass
    return False


def cancel_file_dialog() -> bool:
    """Safely CANCEL an open file dialog ('Öffnen'/'Speichern unter') — never save!"""
    d = get_dialog(0.3)
    if d is None:
        return False
    t = ""
    try:
        t = d.window_text()
    except Exception:
        pass
    if t in ("Speichern unter", "Save As", "Öffnen", "Open"):
        if not dialog_button(d, ("Abbrechen", "Cancel")):
            pyautogui.press("escape")
        time.sleep(0.4)
        return True
    return False


def reset_to_idle() -> None:
    """Clean up after interrupted runs: close messages and CANCEL file dialogs.

    Important against the trap 'interrupted run leaves a 'Speichern unter' dialog open', into
    which a follow-up action would otherwise blindly write (risk of overwriting the base).
    """
    for _ in range(4):
        acted = clear_popups()
        acted = cancel_file_dialog() or acted
        if not acted:
            break
        time.sleep(0.3)


def _filename_edit(dlg):
    """The filename edit in the file dialog (bottom, top>700) — NOT the search/address field."""
    best = None
    for c in dlg.descendants():
        try:
            if c.friendly_class_name() != "Edit":
                continue
            r = c.rectangle()
            if r.top > 700:
                if best is None or r.top < best.rectangle().top:
                    best = c
        except Exception:
            pass
    return best


def dismiss_stray_dialogs(max_rounds: int = 4) -> None:
    """Safely close open non-file dialogs (discard changes: Nein)."""
    for _ in range(max_rounds):
        d = get_dialog(0.3)
        if d is None:
            return
        txt = ""
        try:
            txt = d.window_text()
        except Exception:
            pass
        if txt in ("Speichern unter", "Öffnen", "Open", "Save As"):
            # real file dialog -> cancel
            if not dialog_button(d, ("Abbrechen", "Cancel")):
                pyautogui.press("escape")
        else:
            # message box: discard changes / confirm
            if not dialog_button(d, ("&Nein", "Nein", "&No", "No", "OK", "&Ja", "Ja")):
                pyautogui.press("escape")
        time.sleep(0.4)


# --- Open / save file --------------------------------------------------------------------------

def open_file(path: str) -> None:
    """Load base/file. Raises if the title afterwards does not show the file (corruption guard)."""
    path = os.path.normpath(str(Path(path).resolve()))
    want = os.path.basename(path).lower()
    for attempt in range(4):
        focus()
        pyautogui.click(*TB_OPEN)
        time.sleep(0.8)
        # 'save changes?' is a WinForms MessageForm -> No (discard)
        handle_message_form(["No"], timeout=1.5)
        d = wait_dialog(4.0)                 # #32770 'Öffnen' dialog
        if d is None:
            continue
        # In the modern shell dialog the control tree regularly confuses the search field and the
        # filename. The filename field sits stably 77 pixels above the bottom edge of the dialog.
        rect = d.rectangle()
        pyautogui.click(rect.left + (rect.right - rect.left) // 2, rect.bottom - 77)
        pyautogui.hotkey("ctrl", "a")
        pyperclip.copy(path)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.2)
        pyautogui.press("enter")           # 'Öffnen' is a split button -> Enter in the edit
        time.sleep(1.8)
        # Click away follow-up popups: 'Version mismatch' (MessageForm) or 'Datei wird verwendet'
        # (= file in use, #32770)
        clear_popups()
        if get_dialog(0.3):                # file dialog still open -> cancel
            pyautogui.press("escape")
            time.sleep(0.3)
        if want in title().lower():
            return
    raise EditorStuck(f"open_file: expected {want}, title={title()[-60:]!r}")


def fresh_name(folder: str, prefix: str) -> str:
    """Fresh, guaranteed collision-free base name (without .HMI) in the folder."""
    existing = {os.path.basename(p).lower() for p in glob.glob(os.path.join(folder, "*.HMI"))}
    i = 0
    while True:
        cand = f"{prefix}{i:04d}"
        if f"{cand.lower()}.hmi" not in existing:
            return cand
        i += 1


def save_as(full_path: str) -> bool:
    """Save the current state as an absolute .HMI (fresh names, no overwriting).

    Robust against the spontaneous 'Anmeldung erforderlich' nag: `dismiss_foreign_popup()` is
    called at several points, and success only counts once the file is REALLY on disk AND no file
    dialog is open any more. Otherwise the dialog is cancelled and retried.
    """
    full_path = os.path.normpath(str(Path(full_path)))
    for attempt in range(4):
        focus()                                       # discards the sign-in nag, among others
        pyautogui.press("escape")
        time.sleep(0.15)
        # Open the File menu AND verify that it is open. Otherwise the Save As click would fall into
        # the toolbox and create stray components (Number g0/g1) — the main cause of data corruption.
        menu_ok = False
        for _ in range(2):                            # DELIBERATELY few: do not hammer the button
            check_abort()
            pyautogui.click(*MENU_FILE)
            time.sleep(0.3)
            if menu_open():
                menu_ok = True
                break
            pyautogui.press("escape")
            dismiss_foreign_popup()
            time.sleep(0.2)
        if not menu_ok:
            # Fail fast: if the menu cannot be opened, the environment is blocked. Clicking on
            # achieves nothing and was the cause of the sustained fire on the File button.
            raise EditorAborted("File menu cannot be opened — run aborted (no continuous clicking)")
        pyautogui.click(*FILE_SAVEAS)
        d = wait_dialog(5.0)
        if d is None:
            dismiss_foreign_popup()
            cancel_file_dialog()
            continue
        dismiss_foreign_popup()                       # the nag could block the dialog
        edit = _filename_edit(d)
        if edit is not None:
            try:
                edit.set_edit_text("")
                edit.set_edit_text(full_path)
            except Exception:
                edit.click_input()
                pyautogui.hotkey("ctrl", "a")
                pyautogui.typewrite(full_path, interval=0.004)
        else:
            pyautogui.click(814, 750)
            pyautogui.hotkey("ctrl", "a")
            pyautogui.typewrite(full_path, interval=0.004)
        time.sleep(0.2)
        dismiss_foreign_popup()                       # once more before the Save click
        if not dialog_button(d, ("&Speichern", "Speichern", "&Save", "Save")):
            pyautogui.press("enter")
        time.sleep(0.5)
        # Overwrite prompt -> JA (= Yes; the default is Nein = No!). With fresh names it does not occur.
        d2 = get_dialog(0.25)
        if d2 is not None and d2.window_text() not in ("Speichern unter", "Save As"):
            dialog_button(d2, ("&Ja", "Ja", "&Yes", "Yes"))
            time.sleep(0.4)
        # Success ONLY if the file exists and no file dialog is open any more.
        for _ in range(16):
            dismiss_foreign_popup()
            if get_dialog(0.2) is None and os.path.exists(full_path):
                return True
            time.sleep(0.2)
        cancel_file_dialog()                          # got stuck -> cancel, new attempt
    return False


# --- Component selection / attributes ----------------------------------------------------------

ATTR_COMBO_REF_TOP = 550           # position of the selection box TXT_CELL & co. were calibrated to


def _attr_combo():
    """The component selection box at the top of the attribute panel (position-independent via pywinauto).

    It spans the full panel width (left ~1535); the cell editor combo box sits further right
    (left ~1638) and is excluded via `left < 1600`. The y position is deliberately kept wide,
    because the panel can shift vertically after an editor restart.
    """
    from pywinauto import Application
    w = editor_win()
    if not w:
        return None
    try:
        app = Application(backend="win32").connect(handle=w._hWnd, timeout=1.5)
        top = app.window(handle=w._hWnd)
        for c in top.descendants():
            try:
                if c.friendly_class_name() == "ComboBox":
                    r = c.rectangle()
                    if 1500 < r.left < 1600 and 400 < r.top < 780:
                        return c
            except Exception:
                pass
    except Exception:
        pass
    return None


def attr_offset() -> int:
    """Vertical offset of the attribute panel relative to the calibration.

    The panel layout shifts after an editor restart (measured: selection box top 613 -> 550).
    All attribute panel coordinates are therefore corrected at runtime by this offset instead of
    being hard-wired — otherwise clicks land in foreign fields (e.g. txt_maxl -> bco).
    """
    cb = _attr_combo()
    if cb is None:
        return 0
    try:
        return cb.rectangle().top - ATTR_COMBO_REF_TOP
    except Exception:
        return 0


def attr_combo_text() -> str:
    cb = _attr_combo()
    try:
        return cb.window_text() if cb else ""
    except Exception:
        return ""


def component_names() -> list:
    """All component names of the current page (from the selection box) — e.g. to see stray components."""
    cb = _attr_combo()
    try:
        return [t for t in cb.item_texts() if t] if cb else []
    except Exception:
        return []


def select_component(name: str) -> bool:
    """Select a component via the NATIVE selection box — NO canvas click, so no stray components.

    `name` e.g. 't0'. Success if the box afterwards starts with the name. The attribute grid follows
    the selection and is back at the top. This replaces the fragile canvas click (which on geometry
    drift hit the toolbox and created stray objects).
    """
    focus()
    for _ in range(3):
        cb = _attr_combo()
        if cb is None:
            time.sleep(0.3)
            continue
        target = None
        try:
            for it in cb.item_texts():
                if it == name or it.startswith(name + "("):
                    target = it
                    break
        except Exception:
            target = None
        if target is None:
            return False
        try:
            cb.select(target)
            time.sleep(0.5)
            cb2 = _attr_combo()                    # read fresh (window_text right after select is sluggish)
            if cb2 and (cb2.window_text() or "").startswith(name):
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def select_t0() -> bool:
    return select_component("t0")


def scroll_to_txt() -> None:
    """Position the attribute grid DETERMINISTICALLY: all the way up, then `TXT_ROW` rows down.

    The down arrow scrolls exactly one row per click — unlike the track click (indeterminate page
    size), which used to bring txt sometimes to y718, sometimes to y815 and let set_txt run into the
    wrong cell. The clicks run as a fast burst with PAUSE=0.
    """
    dy = attr_offset()                            # compensate the panel offset at runtime
    up = (GRID_SB_UP[0], GRID_SB_UP[1] + dy)
    dn = (GRID_SB_DOWN[0], GRID_SB_DOWN[1] + dy)
    old = pyautogui.PAUSE
    pyautogui.PAUSE = 0
    try:
        for _ in range(30):                       # safely all the way up (max ~29 rows of offset)
            pyautogui.click(*up)
            time.sleep(0.006)
        time.sleep(0.12)
        for _ in range(TXT_ROW):                  # exactly up to 'txt'
            pyautogui.click(*dn)
            time.sleep(0.012)
    finally:
        pyautogui.PAUSE = old
    time.sleep(0.15)


def _cell_editor(dy: int | None = None):
    """The edit control of the active attribute cell in edit mode (for safe setting/verifying).

    `dy` is the panel offset (see `attr_offset()`); the y window thus moves with the layout.
    """
    if dy is None:
        dy = attr_offset()
    from pywinauto import Application
    w = editor_win()
    if not w:
        return None
    try:
        app = Application(backend="win32").connect(handle=w._hWnd, timeout=1.0)
        top = app.window(handle=w._hWnd)
        for c in top.descendants():
            try:
                if c.friendly_class_name() == "Edit":
                    r = c.rectangle()
                    # The cell editor sits centered on the txt row -> window relative to TXT_CELL.
                    lo, hi = TXT_CELL[1] - 22 + dy, TXT_CELL[1] + 12 + dy
                    if lo < r.top < hi and r.left > 1600:
                        return c
            except Exception:
                pass
    except Exception:
        pass
    return None


def set_txt(value: str) -> bool:
    """Set txt SAFELY: edit the cell, write the value straight into the edit control, VERIFY, commit.

    The direct control route (instead of a clipboard paste) loses no characters; the read-back
    verification via `window_text()` catches the rare failure and retries. This eliminates the
    earlier off-by-one in the saved page-length grid.
    """
    dismiss_foreign_popup()                       # a spontaneous nag would otherwise block the cell
    ensure_geometry()
    dy = attr_offset()                            # compensate the panel offset at runtime
    cell = (TXT_CELL[0], TXT_CELL[1] + dy)
    for _ in range(6):
        pyautogui.doubleClick(*cell)
        time.sleep(0.3)
        ed = _cell_editor(dy)
        if ed is None:
            dismiss_foreign_popup()               # the nag could have just appeared
            pyautogui.press("escape")
            time.sleep(0.15)
            continue
        try:
            ed.set_edit_text("")                  # safely clear the field
            time.sleep(0.03)
            ed.set_edit_text(value)
            got = ""
            for _ in range(6):                    # read right after set_edit_text can lag/be truncated
                time.sleep(0.06)
                got = ed.window_text() or ""
                if got == value:
                    break
        except Exception:
            got = ""
        if got == value:
            pyautogui.press("enter")
            time.sleep(0.18)
            # The final commit happens on the next component change (next select_t0);
            # the LAST sample of each round is safeguarded by the closing dummy in the collector.
            return True
        pyautogui.press("escape")
        time.sleep(0.15)
    # NO paste fallback (it lost characters). Failure -> False; the collector re-takes the sample.
    return False


def set_txtmaxl(value: int) -> None:
    dy = attr_offset()                            # otherwise the value lands in a foreign cell (e.g. bco)
    pyautogui.doubleClick(TXTMAXL_CELL[0], TXTMAXL_CELL[1] + dy)
    time.sleep(0.25)
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.05)
    pyautogui.typewrite(str(value), interval=0.02)
    time.sleep(0.1)
    pyautogui.press("enter")
    time.sleep(0.25)


# --- .HMI reader (truth for the verification) --------------------------------------------------

def read_page_section(path: str, name: str = "0.pa"):
    """Reads the current (last, non-deleted) section `name` -> (bytes, checksum, size)."""
    with open(path, "rb") as fh:
        head = fh.read(0x700000)
        count = int.from_bytes(head[0:4], "little")
        chosen = None
        off = 4
        for _ in range(count):
            rec = head[off:off + 28]
            if len(rec) < 28:
                break
            nm = rec[0:16].split(b"\x00")[0].decode("latin-1", "replace")
            start = int.from_bytes(rec[16:20], "little")
            size = int.from_bytes(rec[20:24], "little")
            deleted = rec[24]
            if nm == name and deleted == 0 and size >= 4:
                chosen = (start, size)          # the last one wins (appended versions)
            off += 28
        if chosen is None:
            return None
        start, size = chosen
        fh.seek(start)
        page = fh.read(size)
    ck = int.from_bytes(page[0:4], "little")
    return page, ck, size


def page_len(path: str) -> int:
    r = read_page_section(path)
    return r[2] if r else -1


# --- Healing / escalation ----------------------------------------------------------------------

def heal(base_path: str) -> None:
    dismiss_stray_dialogs()
    focus()
    open_file(base_path)


def escalate(reason: str) -> None:
    try:
        screenshot(STUCK_PNG)
    except Exception:
        pass
    try:
        from pywinauto import Application
        lines = [f"REASON: {reason}", f"title={title()!r}"]
        w = editor_win()
        if w:
            app = Application(backend="win32").connect(handle=w._hWnd, timeout=1.0)
            for c in app.window(handle=w._hWnd).descendants():
                try:
                    r = c.rectangle()
                    lines.append(f"{c.friendly_class_name():16} {(c.window_text() or '')[:40]!r} ({r.left},{r.top},{r.right},{r.bottom})")
                except Exception:
                    pass
        Path(STUCK_TXT).write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        pass
    raise EditorStuck(reason)


if __name__ == "__main__":
    import sys
    verb = sys.argv[1] if len(sys.argv) > 1 else "setup"
    if verb == "setup":
        setup()
        print("title:", title())
    elif verb == "len":
        print(page_len(sys.argv[2]))
    elif verb == "shot":
        print(screenshot(sys.argv[2] if len(sys.argv) > 2 else SHOT))
