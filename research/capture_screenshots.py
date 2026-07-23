"""Screenshot every page of a Nextion project from a running, maximised editor.

The Nextion Editor has no command-line export for page previews. This driver captures one cropped
image per page by walking the right-hand *Page* list with the Down arrow and grabbing the canvas
after each step. It is intended for the same "drive the editor from the outside" workflow as
``editor_control.py``, kept deliberately dependency-light (Windows + Pillow only, raw ``ctypes`` for
input) so it is easy to audit.

Operator handshake
------------------

1. Open your project in the pinned Nextion Editor and **maximise** the window.
2. Run this script. During the countdown, click the **first** page in the right-hand Page list so it
   holds keyboard focus, then do not touch the mouse or keyboard.
3. The script screenshots the first page, then presses Down once per remaining page. After every grab
   it hashes the Page-list panel; if the hash does not change between two pages the selection did not
   advance (focus was lost) and the run aborts without touching existing output.

Geometry is calibration, not configuration
------------------------------------------

Every pixel rectangle below is tied to one screen size and one editor layout. The shipped defaults
were calibrated for a maximised editor on a **1800 x 1130** screen. On any other geometry the crops
are wrong: **recalibrate from a fresh full-screen grab, do not nudge the constants.** Take one raw
screenshot (the failed-run raws are kept for exactly this), read the canvas and Page-list rectangles
off it, and pass them via ``--config``. See ``driving-the-editor.md``.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Defaults calibrated for a maximised Nextion Editor 1.68.1.3034 on a 1800 x 1130 screen. Override
# every value via --config for a different geometry; see the module docstring. first_crop_box is None
# by default (the first page uses the same crop as the rest); set it only when the first/home page's
# canvas sits at a different position than the other pages.
DEFAULT_GEOMETRY = {
    "screen_size": [1800, 1130],
    "crop_box": [662, 360, 1063, 662],
    "first_crop_box": None,
    "page_panel_box": [1530, 140, 1798, 542],
}

EDITOR_TITLE_MARKER = "Nextion Editor"

VK_DOWN = 0x28
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", ctypes.wintypes.LONG),
        ("dy", ctypes.wintypes.LONG),
        ("mouseData", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", ctypes.wintypes.DWORD),
        ("wParamL", ctypes.wintypes.WORD),
        ("wParamH", ctypes.wintypes.WORD),
    )


class INPUT_UNION(ctypes.Union):
    _fields_ = (("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT))


class INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = (("type", ctypes.wintypes.DWORD), ("union", INPUT_UNION))


def _status(message: str = "", *, end: str = "\n") -> None:
    """Prints operator-facing progress to stderr so stdout stays machine-readable."""

    print(message, file=sys.stderr, end=end, flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture every page of a Nextion project from the maximised editor."
    )
    parser.add_argument(
        "--title",
        required=True,
        help="Substring of the editor window title identifying your project, e.g. the .HMI file "
        "name. Matched together with 'Nextion Editor'.",
    )
    pages = parser.add_mutually_exclusive_group(required=True)
    pages.add_argument(
        "--pages",
        help="Comma-separated page names in editor order, e.g. 'home,menu,status'.",
    )
    pages.add_argument(
        "--pages-file",
        type=Path,
        help="File listing the page order: a JSON array, a JSON object with a 'page_order' or "
        "'pages' key, or one name per line (blank lines and '#' comments ignored).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="JSON file with the pixel geometry (screen_size, crop_box, first_crop_box, "
        "page_panel_box). Any key not present falls back to the calibrated default.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("screenshots"),
        help="Target directory for the cropped page images, default: ./screenshots",
    )
    parser.add_argument(
        "--countdown",
        type=int,
        default=20,
        help="Lead time in seconds to select the first page, default: 20",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        help="Wait after each page change in seconds, default: 0.5",
    )
    return parser.parse_args(argv)


def load_geometry(config_path: Path | None) -> dict[str, tuple[int, ...] | None]:
    """Merges a --config JSON over the calibrated defaults and validates the shapes."""

    values = dict(DEFAULT_GEOMETRY)
    if config_path is not None:
        overrides = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(overrides, dict):
            raise RuntimeError("config file must contain a JSON object")
        unknown = set(overrides) - set(DEFAULT_GEOMETRY)
        if unknown:
            raise RuntimeError(f"unknown config keys: {sorted(unknown)}")
        values.update(overrides)

    geometry: dict[str, tuple[int, ...] | None] = {}
    for key in ("screen_size", "crop_box", "first_crop_box", "page_panel_box"):
        raw = values.get(key)
        if raw is None:
            geometry[key] = None
            continue
        expected = 2 if key == "screen_size" else 4
        if not isinstance(raw, (list, tuple)) or len(raw) != expected:
            raise RuntimeError(f"{key} must be a list of {expected} integers")
        geometry[key] = tuple(int(component) for component in raw)
    if geometry["screen_size"] is None or geometry["crop_box"] is None:
        raise RuntimeError("screen_size and crop_box are required")
    if geometry["page_panel_box"] is None:
        raise RuntimeError("page_panel_box is required for the focus check")
    return geometry


def load_page_names(args: argparse.Namespace) -> tuple[str, ...]:
    """Reads the page order from --pages or --pages-file and validates it."""

    if args.pages is not None:
        names = [name.strip() for name in args.pages.split(",")]
    else:
        text = args.pages_file.read_text(encoding="utf-8")
        names = _parse_page_file(text)

    names = [name for name in names if name]
    if not names:
        raise RuntimeError("no page names given")
    if len(names) != len(set(names)):
        raise RuntimeError("the page order contains duplicate names")
    return tuple(names)


def _parse_page_file(text: str) -> list[str]:
    """Accepts a JSON array, a JSON object (page_order/pages), or a plain name-per-line list."""

    stripped = text.strip()
    if stripped.startswith(("[", "{")):
        data = json.loads(stripped)
        if isinstance(data, list):
            return [str(item) for item in data]
        if isinstance(data, dict):
            for key in ("page_order", "pages"):
                if key in data:
                    return [str(item) for item in data[key]]
        raise RuntimeError("JSON page file needs an array or a 'page_order'/'pages' key")
    names = []
    for line in stripped.splitlines():
        entry = line.strip()
        if entry and not entry.startswith("#"):
            names.append(entry)
    return names


def activate_dpi_awareness() -> None:
    """Makes screenshots use the physical screen resolution."""

    user32 = ctypes.windll.user32
    try:
        per_monitor_aware_v2 = ctypes.c_void_p(-4)
        if user32.SetProcessDpiAwarenessContext(per_monitor_aware_v2):
            return
    except (AttributeError, OSError):
        # Older Windows versions only offer the following DPI call.
        pass
    user32.SetProcessDPIAware()


def foreground_window_title() -> str:
    user32 = ctypes.windll.user32
    window = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(window)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(window, buffer, len(buffer))
    return buffer.value


def ensure_editor_is_foreground(title_marker: str) -> None:
    title = foreground_window_title()
    if EDITOR_TITLE_MARKER not in title or title_marker not in title:
        raise RuntimeError(
            f"The expected Nextion Editor window is not in the foreground. "
            f"Active window: {title or '<no title>'}"
        )


def press_down() -> None:
    down = INPUT(
        type=INPUT_KEYBOARD,
        ki=KEYBDINPUT(wVk=VK_DOWN, wScan=0, dwFlags=0, time=0, dwExtraInfo=0),
    )
    up = INPUT(
        type=INPUT_KEYBOARD,
        ki=KEYBDINPUT(wVk=VK_DOWN, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=0),
    )
    inputs = (INPUT * 2)(down, up)
    send_input = ctypes.windll.user32.SendInput
    send_input.argtypes = (ctypes.wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    send_input.restype = ctypes.wintypes.UINT
    sent = send_input(2, inputs, ctypes.sizeof(INPUT))
    if sent != 2:
        raise ctypes.WinError()


def capture_page(
    *,
    title_marker: str,
    is_first: bool,
    geometry: dict[str, tuple[int, ...] | None],
    raw_path: Path,
    cropped_path: Path,
) -> str:
    from PIL import ImageGrab

    ensure_editor_is_foreground(title_marker)
    screenshot = ImageGrab.grab(all_screens=True)
    expected = geometry["screen_size"]
    if screenshot.size != expected:
        raise RuntimeError(
            f"unexpected screen size {screenshot.size}; expected {expected}. Recalibrate the crop "
            "geometry for this screen instead of reusing the defaults."
        )
    screenshot.save(raw_path)
    crop_box = geometry["crop_box"]
    if is_first and geometry["first_crop_box"] is not None:
        crop_box = geometry["first_crop_box"]
    screenshot.crop(crop_box).save(cropped_path)
    page_panel = screenshot.crop(geometry["page_panel_box"])
    return hashlib.sha256(page_panel.tobytes()).hexdigest()


def countdown(seconds: int) -> None:
    _status("Now click the FIRST page in the maximised Nextion Editor's page list.")
    _status("After that, do not touch the mouse or keyboard.")
    _status()
    for remaining in range(seconds, 0, -1):
        _status(f"Capture starts in {remaining:02d} s", end="\r")
        time.sleep(1)
    _status("Capture starts now.        ")


def run_capture(args: argparse.Namespace) -> None:
    if args.countdown < 0:
        raise ValueError("the countdown must not be negative")
    if args.delay < 0:
        raise ValueError("the delay must not be negative")

    from PIL import Image

    geometry = load_geometry(args.config)
    page_names = load_page_names(args)
    page_count = len(page_names)

    output_dir = args.output.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_name = datetime.now().strftime("_capture_%Y%m%d_%H%M%S")
    staging_dir = output_dir / run_name
    raw_dir = staging_dir / "raw"
    cropped_dir = staging_dir / "cropped"
    raw_dir.mkdir(parents=True)
    cropped_dir.mkdir(parents=True)
    _status(f"Working data: {staging_dir}")

    countdown(args.countdown)
    ensure_editor_is_foreground(args.title)

    previous_page_panel_hash: str | None = None
    previous_page_name: str | None = None
    for index, page_name in enumerate(page_names):
        if index > 0:
            ensure_editor_is_foreground(args.title)
            press_down()
            time.sleep(args.delay)

        raw_path = raw_dir / f"{index:02d}_{page_name}.png"
        cropped_path = cropped_dir / f"{page_name}.png"
        page_panel_hash = capture_page(
            title_marker=args.title,
            is_first=index == 0,
            geometry=geometry,
            raw_path=raw_path,
            cropped_path=cropped_path,
        )
        if page_panel_hash == previous_page_panel_hash:
            raise RuntimeError(
                f"The page list did not change between '{previous_page_name}' and '{page_name}'. "
                "The page list most likely lost keyboard focus."
            )
        previous_page_panel_hash = page_panel_hash
        previous_page_name = page_name
        _status(f"[{index + 1:02d}/{page_count:02d}] {page_name}")

    dimensions: set[tuple[int, int]] = set()
    for page_name in page_names:
        with Image.open(cropped_dir / f"{page_name}.png") as image:
            dimensions.add(image.size)
    if len(dimensions) != 1:
        raise RuntimeError(f"inconsistent crop dimensions: {sorted(dimensions)}")

    for page_name in page_names:
        source = cropped_dir / f"{page_name}.png"
        target = output_dir / f"{page_name}.png"
        os.replace(source, target)

    cropped_dir.rmdir()
    width, height = dimensions.pop()
    _status(f"Captured {page_count} screenshots at {width}x{height} pixels.")
    _status(f"Output: {output_dir}")
    _status(f"Raw frames kept in: {raw_dir}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if sys.platform != "win32":
        _status("Error: this script requires Windows.")
        return 1
    try:
        activate_dpi_awareness()
        run_capture(args)
    except Exception as error:  # noqa: BLE001 - report any failure to the operator and keep raws
        _status(f"Error: {error}")
        _status(
            "Existing target images were not replaced. The raw frames of the failed run are kept "
            "for inspection and recalibration."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
