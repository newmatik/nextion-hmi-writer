"""Opens exactly one HMI in the Nextion Editor and exits with a diagnostic screenshot.

The tool deliberately has no retry loop for menu or toolbar clicks. It cleans up the initial state,
opens the file dialog once, waits at most 15 seconds and reports either the expected window title or
the visible popup text.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import pyautogui
import pyperclip

from research import editor_control as editor


def popup_text() -> str | None:
    window = editor.message_form()
    if window is None:
        # Load errors of Editor 1.68 are not a `MessageForm` but a second modal WinForms window
        # carrying the same title "Nextion Editor" as the main window.
        from pywinauto import Desktop

        main = editor.editor_win()
        main_handle = main._hWnd if main is not None else None
        try:
            candidates = [
                item
                for item in Desktop(backend="win32").windows(title="Nextion Editor")
                if item.handle != main_handle and item.is_visible()
            ]
        except Exception:
            candidates = []
        window = candidates[0] if candidates else None
    if window is None:
        return None
    texts: list[str] = []
    try:
        for child in window.descendants():
            value = (child.window_text() or "").strip()
            if value and value not in texts:
                texts.append(value)
    except Exception:
        pass
    return " | ".join(texts) or "MessageForm"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    path = args.path.resolve()
    if not path.is_file():
        raise SystemExit(f"File missing: {path}")
    shot = (args.screenshot or Path(os.environ.get("TEMP", ".")) / "nextion_validate.png").resolve()

    editor.set_run_limits(max_seconds=30.0, max_actions=30)
    # Close the diagnostic popup of a previous single run exactly once.
    if popup_text() is not None:
        pyautogui.press("enter")
        time.sleep(0.5)
    editor.reset_to_idle()
    editor.setup()
    editor.focus()
    pyautogui.click(*editor.TB_OPEN)
    dialog = editor.wait_dialog(8.0)
    if dialog is None:
        raise editor.EditorStuck("Single-shot opener: Open dialog did not appear")
    # In the modern shell dialog the control tree regularly confuses the search field and the
    # filename field. The filename field sits reliably 77 pixels above the dialog's bottom edge.
    rect = dialog.rectangle()
    pyautogui.click(rect.left + (rect.right - rect.left) // 2, rect.bottom - 77)
    pyautogui.hotkey("ctrl", "a")
    pyperclip.copy(os.path.normpath(str(path)))
    pyautogui.hotkey("ctrl", "v")
    pyautogui.press("enter")

    deadline = time.time() + 15.0
    while time.time() < deadline:
        editor.check_abort()
        if path.name.lower() in editor.title().lower():
            editor.screenshot(str(shot))
            print(f"OK: {editor.title()}")
            print(f"Screenshot: {shot}")
            return 0
        message = popup_text()
        if message is not None:
            editor.screenshot(str(shot))
            print(f"ERROR: {message}")
            print(f"Screenshot: {shot}")
            return 2
        time.sleep(0.2)
    editor.screenshot(str(shot))
    print(f"ERROR: timeout, title={editor.title()!r}")
    print(f"Screenshot: {shot}")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
