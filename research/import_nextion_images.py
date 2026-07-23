"""Imports the generated BMP resources into an Editor project in a controlled way.

On import the Nextion Editor mints a proprietary preview (``.i``) next to the embedded original BMP
(``.is``). That preview does not have to be reverse-engineered: the script lets the pinned Editor
1.68.1.3034 produce it once and then verifies the saved HMI.

The run is deliberately hard-limited. It uses no retry loop on the File menu, never overwrites an
existing file and aborts on any unexpected dialog state.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import pyautogui
import pyperclip

from research import editor_control as editor
from research.nextion_hmi_binary import parse_container

IMPORT_LIST_BLANK = (1500, 800)
IMPORT_FILENAME = (900, 985)


def _click_picture_add() -> None:
    """Clicks the native plus button regardless of relocated dock panels."""

    import ctypes
    from ctypes import wintypes

    main = editor.editor_win()
    if main is None:
        raise editor.EditorStuck("Image import: Editor window missing")
    matches: list[tuple[int, int, int, int]] = []
    user32 = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(handle, _parameter):
        length = user32.GetWindowTextLengthW(handle)
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, buffer, length + 1)
            if buffer.value == "Picture":
                rect = wintypes.RECT()
                user32.GetWindowRect(handle, ctypes.byref(rect))
                if rect.right - rect.left > 150 and rect.bottom - rect.top > 300:
                    matches.append((rect.left, rect.top, rect.right, rect.bottom))
        return True

    user32.EnumChildWindows(main._hWnd, visit, 0)
    if len(matches) != 1:
        raise editor.EditorStuck(f"Image import: Picture panel is not unique: {matches}")
    left, top, _right, _bottom = matches[0]
    pyautogui.click(left + 24, top + 50)


def _native_file_dialog_present() -> bool:
    """Checks without pywinauto whether a native file dialog is still open."""

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found = False

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(handle, _parameter):
        nonlocal found
        name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(handle, name, 256)
        if name.value == "#32770" and user32.IsWindowVisible(handle):
            found = True
            return False
        return True

    user32.EnumWindows(visit, 0)
    return found


def import_image(path: Path) -> None:
    """Imports exactly one BMP and confirms the modal success message."""

    editor.focus()
    _click_picture_add()
    dialog = editor.wait_dialog(12.0)
    if dialog is None:
        raise editor.EditorStuck("Image import: Open dialog did not appear")

    value = os.path.normpath(str(path.resolve()))
    # The shell dialog remembers multi-selections from an earlier import. A click into the
    # guaranteed empty lower area of the file list clears it; otherwise the old selection wins
    # despite a correctly set filename field.
    pyautogui.click(*IMPORT_LIST_BLANK)
    time.sleep(0.15)
    pyautogui.click(*IMPORT_FILENAME)
    pyautogui.hotkey("ctrl", "a")
    pyperclip.copy(value)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.15)
    pyautogui.press("enter")

    # The Editor's own MessageForm occasionally blocks pywinauto under 64-bit Python. The imported
    # BMPs are validated beforehand, so the default button (OK) via Enter is sufficient. The final
    # container check prevents a failure from slipping through as a success.
    time.sleep(1.5)
    editor.check_abort()
    if _native_file_dialog_present():
        pyautogui.press("escape")
        editor.escalate("Image import: unexpected native dialog")
    pyautogui.press("enter")
    time.sleep(0.45)


def save_current(path: Path) -> None:
    """Saves once and waits for the file to actually be updated."""

    before = path.stat().st_mtime_ns
    editor.focus()
    pyautogui.click(*editor.TB_SAVE)
    deadline = time.time() + 15.0
    while time.time() < deadline:
        editor.check_abort()
        editor.clear_popups()
        if editor.get_dialog(0.2) is None and path.stat().st_mtime_ns != before:
            return
        time.sleep(0.25)
    raise editor.EditorAborted("Save: file was not updated")


def verify(path: Path, expected: list[Path], offset: int = 0) -> None:
    """Checks count, dimensions and byte-exact BMP embedding."""

    hmi = parse_container(path.read_bytes())
    live = {section.name: section.data for section in hmi.sections if not section.deleted}
    for relative_index, bmp_path in enumerate(expected):
        index = offset + relative_index
        preview = live.get(f"{index}.i")
        source = live.get(f"{index}.is")
        if preview is None or source is None:
            raise RuntimeError(f"Image {index}: .i/.is missing")
        bmp = bmp_path.read_bytes()
        if source[27:] != bmp:
            raise RuntimeError(f"Image {index}: embedded BMP differs")
        if preview[12:16] != source[12:16]:
            raise RuntimeError(f"Image {index}: preview dimensions differ")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("images", nargs="+", type=Path)
    args = parser.parse_args()

    base = args.base.resolve()
    output = args.output.resolve()
    images = [path.resolve() for path in args.images]
    if output.exists() and not args.resume:
        raise SystemExit(f"Output already exists: {output}")
    if not base.is_file() or any(not path.is_file() for path in images):
        raise SystemExit("Base or at least one BMP is missing")

    editor.set_run_limits(max_seconds=180.0, max_actions=300)
    editor.setup()
    if args.resume:
        if output.name.lower() not in editor.title().lower():
            raise editor.EditorAborted(
                f"Resume refused: expected {output.name}, title={editor.title()!r}"
            )
    else:
        editor.open_file(str(base))
        if not editor.save_as(str(output)):
            raise editor.EditorAborted("Initial Save as failed")
    for index, image in enumerate(images[args.start :], start=args.start):
        import_image(image)
        print(f"[{index + 1:02d}/{len(images):02d}] {image.name}", flush=True)
        if (index + 1) % 4 == 0:
            save_current(output)
    if len(images) % 4:
        save_current(output)
    editor.open_file(str(base))
    verify(output, images, args.offset)
    print(f"OK: {len(images)} images in {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
