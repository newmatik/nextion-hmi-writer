"""Creates an Editor-authentic HMI skeleton for the offline writer.

The run adds exactly three fonts and the missing pages to an image template already saved by the
Editor. It is hard-limited in time and actions, uses fresh output names exclusively and verifies the
result byte-exactly after unlocking the project.

Page and image counts are project-specific and therefore mandatory arguments::

    python -m research.create_nextion_skeleton --base <image.HMI> --output <new.HMI> \\
        --pages <n> --images <n> <font0.zi> <font1.zi> <font2.zi>

The three fonts are not a format limit -- the Editor and the container take any number. It is the
import routine here that is written for exactly three, so ``fonts`` is fixed at ``nargs=3`` rather
than left open; widening it means generalising ``import_font`` and the ``.zi`` check in ``verify``.
"""

from __future__ import annotations

import argparse
import ctypes
import os
import time
from ctypes import wintypes
from pathlib import Path

try:
    import pyautogui
    import pyperclip
except ImportError as exc:  # GUI drivers ship only with the optional [drivers] extra
    raise SystemExit("this tool needs the GUI drivers; install them with: pip install -e .[drivers]") from exc

from research import editor_control as editor
from research.nextion_hmi_binary import parse_container

FONT_TAB = (61, 1030)
FILE_LIST_BLANK = (1500, 800)
FILE_NAME = (900, 985)


def panel_rect(title: str) -> tuple[int, int, int, int]:
    """Returns the visible dock panel identified by its native window title."""

    main = editor.editor_win()
    if main is None:
        raise editor.EditorStuck("Editor window missing")
    user32 = ctypes.windll.user32
    matches: list[tuple[int, int, int, int]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(handle, _parameter):
        length = user32.GetWindowTextLengthW(handle)
        if length:
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(handle, buffer, length + 1)
            if buffer.value == title and user32.IsWindowVisible(handle):
                rect = wintypes.RECT()
                user32.GetWindowRect(handle, ctypes.byref(rect))
                if rect.right - rect.left > 150 and rect.bottom - rect.top > 250:
                    matches.append((rect.left, rect.top, rect.right, rect.bottom))
        return True

    user32.EnumChildWindows(main._hWnd, visit, 0)
    if len(matches) != 1:
        raise editor.EditorStuck(f"Panel {title!r} is not unique: {matches}")
    return matches[0]


def click_panel_add(title: str) -> None:
    left, top, _right, _bottom = panel_rect(title)
    editor.check_abort()
    pyautogui.click(left + 24, top + 50)


def import_font(path: Path) -> None:
    editor.focus()
    pyautogui.click(*FONT_TAB)
    time.sleep(0.4)
    click_panel_add("Fonts")
    if editor.wait_dialog(10.0) is None:
        raise editor.EditorStuck("Font import: Open dialog did not appear")
    pyautogui.click(*FILE_LIST_BLANK)
    time.sleep(0.15)
    pyautogui.click(*FILE_NAME)
    pyautogui.hotkey("ctrl", "a")
    value = os.path.normpath(str(path.resolve()))
    pyperclip.copy(value)
    pyautogui.hotkey("ctrl", "v")
    pyautogui.press("enter")
    time.sleep(1.4)
    editor.check_abort()
    if editor.get_dialog(0.2) is not None:
        pyautogui.press("escape")
        editor.escalate("Font import: native dialog stayed open")
    # A successful import ends with the Editor's own OK message.
    editor.handle_message_form(["OK"], timeout=1.0)
    time.sleep(0.35)


def save_current(path: Path) -> None:
    before = path.stat().st_mtime_ns
    editor.focus()
    pyautogui.click(*editor.TB_SAVE)
    deadline = time.time() + 12.0
    while time.time() < deadline:
        editor.check_abort()
        editor.clear_popups()
        if path.stat().st_mtime_ns != before and editor.get_dialog(0.2) is None:
            return
        time.sleep(0.25)
    raise editor.EditorAborted("Skeleton: saving was not confirmed")


def add_pages(total: int) -> None:
    _left, _top, _right, _bottom = panel_rect("Page")
    for index in range(1, total):
        editor.focus()
        click_panel_add("Page")
        time.sleep(0.25)
        print(f"[Page {index + 1:02d}/{total:02d}]", flush=True)


def verify(path: Path, fonts: list[Path], page_count: int, image_count: int) -> None:
    hmi = parse_container(path.read_bytes())
    live = {section.name: section.data for section in hmi.sections if not section.deleted}
    pages = sorted(name for name in live if name.endswith(".pa"))
    if len(pages) != page_count:
        raise RuntimeError(f"Skeleton: {len(pages)} pages instead of {page_count}")
    expected_pages = {f"{index}.pa" for index in range(page_count)}
    if set(pages) != expected_pages:
        raise RuntimeError("Skeleton: page numbers have gaps")
    for index, font in enumerate(fonts):
        data = live.get(f"{index}.zi")
        if data != font.read_bytes():
            raise RuntimeError(f"Skeleton: font {index} differs from {font.name}")
    images = [name for name in live if name.endswith(".is")]
    if len(images) != image_count:
        raise RuntimeError(f"Skeleton: {len(images)} images instead of {image_count}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pages", type=int, required=True)
    parser.add_argument("--images", type=int, required=True)
    parser.add_argument("fonts", nargs=3, type=Path)
    args = parser.parse_args()

    base = args.base.resolve()
    output = args.output.resolve()
    fonts = [path.resolve() for path in args.fonts]
    if output.exists():
        raise SystemExit(f"Output already exists: {output}")
    if not base.is_file() or any(not path.is_file() for path in fonts):
        raise SystemExit("Base or font missing")

    editor.set_run_limits(max_seconds=120.0, max_actions=220)
    try:
        editor.setup()
        editor.open_file(str(base))
        if not editor.save_as(str(output)):
            raise editor.EditorAborted("Skeleton: initial Save as failed")
        for index, font in enumerate(fonts):
            import_font(font)
            print(f"[Font {index + 1}/{len(fonts)}] {font.name}", flush=True)
        save_current(output)
        add_pages(args.pages)
        save_current(output)
        editor.open_file(str(base))
        verify(output, fonts, args.pages, args.images)
    except Exception as exc:
        editor.escalate(f"Skeleton creation aborted: {exc}")
        raise
    print(f"OK: {args.images} images, 3 fonts and {args.pages} pages in {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
