"""Create Nextion Editor components and pages by clone-and-patch.

The basis is an *exemplar* ``.HMI`` built by hand in Nextion Editor 1.68.1.3034 that holds one
instance of every component type you need. For each new component the matching type template is
cloned and only its understood attributes are overwritten. Every byte that is not understood is
therefore kept editor-conformant.

The section checksums are verified against the editor and recomputed on serialisation. The writer
thus produces loadable containers; the editor remains the final compatibility and compile authority.

This module is deliberately stdlib-only and independent of any asset (BMP/SVG) generator. It builds
on the canonical ``research.nextion_hmi_binary`` and ``research.nextion_hmi_checksum`` — the
executable form of the format reference in the repository root.

Text encoding
-------------

``TEXT_ENCODING`` defaults to ``latin-1`` (ISO-8859-1), matching a project whose Nextion *Character
Encoding* is ISO-8859-1. It is the single knob to change for a UTF-8 project — it must always match
the project's editor encoding, or ``.txt`` bytes will not match what the panel renders.

Providing an exemplar
---------------------

There is no bundled exemplar (component bytes are editor- and version-specific). Build one once in
the pinned editor with an odd, unique value per field (e.g. ``x=101, y=103, w=107, h=109``) so
fields can be located by value, then pass its path to :func:`load_templates` /
:func:`load_page_header`. See ``methodology.md`` and ``driving-the-editor.md``.
"""

from __future__ import annotations

import copy
import struct
from collections.abc import Sequence
from pathlib import Path

from research.nextion_hmi_binary import (
    NAME_FIELD_SIZE,
    CodeLine,
    Component,
    HmiFile,
    Marker,
    Page,
    Section,
    TYPE_BUTTON,
    TYPE_NUMBER,
    TYPE_PAGE,
    TYPE_PICTURE,
    TYPE_PROGRESS,
    TYPE_TEXT,
    TYPE_TIMER,
    TYPE_VARIABLE,
    parse_container,
    parse_page,
)
from research.nextion_hmi_checksum import with_main_checksum

# Must match the project's Nextion "Character Encoding" setting. See the module docstring.
TEXT_ENCODING = "latin-1"


class WriterError(RuntimeError):
    """Failure while assembling a Nextion structure."""


def load_templates(exemplar: Path) -> dict[int, Component]:
    """Reads one component template per type from the exemplar.

    If a type has several representatives the first one wins. The returned components are deep copies
    and may be patched freely.
    """

    container = parse_container(exemplar.read_bytes())
    page_section = next(container.pages(), None)
    if page_section is None:
        raise WriterError("exemplar contains no page")
    page = parse_page(page_section.data)

    templates: dict[int, Component] = {}
    for component in page.components:
        type_attr = component.attribute("type")
        if type_attr is None:
            continue
        type_id = type_attr.as_int()
        templates.setdefault(type_id, copy.deepcopy(component))
    return templates


def load_page_header(exemplar: Path) -> bytearray:
    """Reads the full page header including unknown editor fields."""

    container = parse_container(exemplar.read_bytes())
    section = next(container.pages(), None)
    if section is None:
        raise WriterError("exemplar contains no page")
    return bytearray(parse_page(section.data).header)


def _patch(component: Component, name: str, value: bytes) -> None:
    """Overwrites the value of an existing attribute in its existing width."""

    attribute = component.attribute(name)
    if attribute is None:
        raise WriterError(f"template does not know attribute {name!r}")
    if len(value) != len(attribute.value):
        raise WriterError(
            f"width of {name!r} differs: template {len(attribute.value)} != {len(value)}"
        )
    attribute.value = value


def _u8(value: int) -> bytes:
    return int(value).to_bytes(1, "little")


def _u16(value: int) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise WriterError(f"value out of range for u16: {value}")
    return int(value).to_bytes(2, "little")


def _u32(value: int) -> bytes:
    if not 0 <= value <= 0xFFFFFFFF:
        raise WriterError(f"value out of range for u32: {value}")
    return int(value).to_bytes(4, "little")


def _set_geometry(component: Component, x: int, y: int, w: int, h: int) -> None:
    """Sets position and size and the dependent ``endx``/``endy``."""

    _patch(component, "x", _u16(x))
    _patch(component, "y", _u16(y))
    _patch(component, "w", _u16(w))
    _patch(component, "h", _u16(h))
    if component.attribute("endx") is not None:
        _patch(component, "endx", _u16(x + w - 1))
    if component.attribute("endy") is not None:
        _patch(component, "endy", _u16(y + h - 1))


def _set_objname(component: Component, objname: str) -> None:
    attribute = component.require("objname")
    raw = objname.encode("ascii")
    if len(raw) > 14:
        raise WriterError(f"object name is longer than 14 ASCII characters: {objname!r}")
    attribute.value = raw


def _append_component(page: Page, component: Component) -> None:
    """Assigns the page-local object id and appends the component."""

    _patch(component, "id", _u8(len(page.components)))
    page.components.append(component)
    page.content_flags.append(0)


def _set_event(component: Component, marker_name: str, lines: Sequence[str]) -> None:
    """Replaces a ``codes*`` event block with new code lines.

    The marker (e.g. ``codesup``) keeps its name, its line count is adjusted, and the following code
    lines are rewritten. Existing lines of the same block are discarded.
    """

    records = component.records
    for index, record in enumerate(records):
        if isinstance(record, Marker):
            head, count = record.split_count()
            if head == marker_name and count is not None:
                # drop the old code lines of this block
                del records[index + 1 : index + 1 + count]
                record.text = f"{marker_name}-{len(lines)}"
                for offset, line in enumerate(lines):
                    records.insert(index + 1 + offset, CodeLine(raw=line.encode(TEXT_ENCODING)))
                return
    raise WriterError(f"event marker {marker_name!r} not found")


def _release_lines(
    release_printh: str | None,
    release_commands: Sequence[str],
    navigate_page: str | None,
) -> list[str]:
    """Builds the touch-release code lines: optional ``printh``, commands, optional page jump."""

    lines: list[str] = []
    if release_printh is not None:
        lines.append(release_printh)
    lines.extend(release_commands)
    if navigate_page is not None:
        lines.append(f"page {navigate_page}")
    return lines


def new_page(
    templates: dict[int, Component],
    page_header: bytearray,
    name: str,
    background565: int,
    postinitialize: list[str] | None = None,
) -> Page:
    """Creates an empty page with its page object (object 0)."""

    if TYPE_PAGE not in templates:
        raise WriterError("no page template")
    page_object = copy.deepcopy(templates[TYPE_PAGE])
    _set_objname(page_object, name)
    _patch(page_object, "id", _u8(0))
    if page_object.attribute("bco") is not None:
        _patch(page_object, "bco", _u16(background565))
    _set_event(page_object, "codesloadend", postinitialize or [])
    header = bytearray(page_header)
    header[0:16] = b"\x00" * 16
    header[0x18 : 0x18 + NAME_FIELD_SIZE] = b"\x00" * NAME_FIELD_SIZE
    name_bytes = name.encode("ascii")
    header[0x18 : 0x18 + len(name_bytes)] = name_bytes
    return Page(header=header, components=[page_object], content_flags=[0])


def add_text(
    templates: dict[int, Component],
    page: Page,
    *,
    objname: str,
    x: int,
    y: int,
    w: int,
    h: int,
    text: str,
    font: int,
    color565: int,
    background565: int,
    max_length: int = 80,
    xcen: int = 1,
    ycen: int = 1,
    press_commands: Sequence[str] = (),
    release_printh: str | None = None,
    release_commands: Sequence[str] = (),
    navigate_page: str | None = None,
) -> None:
    """Adds a static text object to the page."""

    component = copy.deepcopy(templates[TYPE_TEXT])
    _set_objname(component, objname)
    _set_geometry(component, x, y, w, h)
    _patch(component, "font", _u8(font))
    _patch(component, "pco", _u16(color565))
    _patch(component, "bco", _u16(background565))
    _patch(component, "sta", _u8(1))
    _patch(component, "xcen", _u8(xcen))
    _patch(component, "ycen", _u8(ycen))
    if component.attribute("isbr") is not None:
        _patch(component, "isbr", _u8(0))
    raw = text.encode(TEXT_ENCODING)
    maxl = max(len(raw), max_length)
    component.require("txt_maxl").value = _u16(maxl)
    component.require("txt").value = raw

    _set_event(component, "codesdown", press_commands)
    _set_event(component, "codesup", _release_lines(release_printh, release_commands, navigate_page))
    _append_component(page, component)


def add_button(
    templates: dict[int, Component],
    page: Page,
    *,
    objname: str,
    x: int,
    y: int,
    w: int,
    h: int,
    text: str,
    font: int,
    fill565: int,
    fill_pressed565: int,
    text565: int,
    pic: int | None = None,
    pic_pressed: int | None = None,
    release_printh: str | None = None,
    release_commands: Sequence[str] = (),
    navigate_page: str | None = None,
    xcen: int = 1,
    ycen: int = 1,
) -> None:
    """Adds a button to the page.

    ``sta`` follows from the picture assignment: with ``pic`` an image button (``sta=2``), otherwise
    a solid-colour button (``sta=1``). The touch-release event optionally gets a ``printh`` and a
    ``page`` jump — in exactly that order.
    """

    component = copy.deepcopy(templates[TYPE_BUTTON])
    _set_objname(component, objname)
    _set_geometry(component, x, y, w, h)
    _patch(component, "font", _u8(font))
    _patch(component, "pco", _u16(text565))
    _patch(component, "bco", _u16(fill565))
    _patch(component, "bco2", _u16(fill_pressed565))
    _patch(component, "xcen", _u8(xcen))
    _patch(component, "ycen", _u8(ycen))
    if component.attribute("isbr") is not None:
        _patch(component, "isbr", _u8(0))
    if pic is not None:
        _patch(component, "sta", _u8(2))
        _patch(component, "pic", _u16(pic))
        _patch(component, "pic2", _u16(pic_pressed if pic_pressed is not None else pic))
    else:
        _patch(component, "sta", _u8(1))
    raw = text.encode(TEXT_ENCODING)
    maxl = max(len(raw), 80)
    component.require("txt_maxl").value = _u16(maxl)
    component.require("txt").value = raw

    _set_event(component, "codesup", _release_lines(release_printh, release_commands, navigate_page))

    _append_component(page, component)


def add_picture(
    templates: dict[int, Component],
    page: Page,
    *,
    objname: str,
    x: int,
    y: int,
    w: int,
    h: int,
    pic: int,
    press_commands: Sequence[str] = (),
    release_printh: str | None = None,
    release_commands: Sequence[str] = (),
    navigate_page: str | None = None,
) -> None:
    """Adds a picture object to the page (fixed image size)."""

    component = copy.deepcopy(templates[TYPE_PICTURE])
    _set_objname(component, objname)
    _set_geometry(component, x, y, w, h)
    _patch(component, "pic", _u16(pic))
    _set_event(component, "codesdown", press_commands)
    _set_event(component, "codesup", _release_lines(release_printh, release_commands, navigate_page))
    _append_component(page, component)


def add_variable(
    templates: dict[int, Component],
    page: Page,
    *,
    objname: str,
    numeric: bool = True,
    initial: int | str = 0,
    max_length: int = 80,
    global_scope: bool = True,
) -> None:
    """Adds a global variable (``sta`` 0 = numeric, 1 = text)."""

    component = copy.deepcopy(templates[TYPE_VARIABLE])
    _set_objname(component, objname)
    if component.attribute("vscope") is not None:
        _patch(component, "vscope", _u8(1 if global_scope else 0))
    _patch(component, "sta", _u8(0 if numeric else 1))
    if numeric:
        _patch(component, "val", _u32(int(initial)))
    else:
        raw = str(initial).encode(TEXT_ENCODING)
        component.require("txt").value = raw
        component.require("txt_maxl").value = _u16(max(max_length, len(raw)))
    _append_component(page, component)


def add_progress(
    templates: dict[int, Component],
    page: Page,
    *,
    objname: str,
    x: int,
    y: int,
    w: int,
    h: int,
    value: int,
    empty565: int,
    fill565: int,
) -> None:
    """Adds a progress bar (``val`` 0..100, ``bco`` empty, ``pco`` filled)."""

    component = copy.deepcopy(templates[TYPE_PROGRESS])
    _set_objname(component, objname)
    _set_geometry(component, x, y, w, h)
    _patch(component, "val", _u8(max(0, min(100, value))))
    _patch(component, "bco", _u16(empty565))
    _patch(component, "pco", _u16(fill565))
    _append_component(page, component)


def add_number(
    templates: dict[int, Component],
    page: Page,
    *,
    objname: str,
    value: int = 0,
    visible: bool = False,
) -> None:
    """Adds a Number object, optionally placed outside the visible area."""

    component = copy.deepcopy(templates[TYPE_NUMBER])
    _set_objname(component, objname)
    _patch(component, "val", _u32(value))
    if not visible:
        # The editor also validates fully off-screen components and requires at least 2 x 2 pixels.
        _set_geometry(component, 320, 240, 2, 2)
    _append_component(page, component)


def add_timer(
    templates: dict[int, Component],
    page: Page,
    *,
    objname: str,
    period_ms: int,
    enabled: bool,
    lines: list[str],
) -> None:
    """Adds a timer (``tim`` in ms, ``en`` on/off, with ``codestimer`` code)."""

    component = copy.deepcopy(templates[TYPE_TIMER])
    _set_objname(component, objname)
    _patch(component, "tim", _u16(period_ms))
    _patch(component, "en", _u8(1 if enabled else 0))
    _set_event(component, "codestimer", lines)
    _append_component(page, component)


def _build_main_hmi(main: Section, page_count: int, font_count: int, image_count: int) -> bytes:
    """Clones ``main.HMI``'s 0x60 header and appends a fresh resource directory and checksum.

    The resource order — images, then fonts, then pages — defines the ids that components reference.
    """

    resources: list[tuple[str, str]] = []
    resources.extend(("i", f"{index}.i") for index in range(image_count))
    resources.extend(("zi", f"{index}.zi") for index in range(font_count))
    resources.extend(("pa", f"{index}.pa") for index in range(page_count))
    main_data = bytearray(main.data[:0x60])
    # header_length (0x04) and the resource-directory offset (0x18) both point at 0x60; keep them in
    # step so a differing exemplar cannot yield a self-contradictory main.HMI.
    struct.pack_into("<I", main_data, 0x04, 0x60)
    struct.pack_into("<I", main_data, 0x18, 0x60)
    struct.pack_into("<I", main_data, 0x1C, len(resources))
    for extension, name in resources:
        main_data += extension.encode("ascii").ljust(8, b"\x00")
        main_data += name.encode("ascii").ljust(8, b"\x00")
    return with_main_checksum(bytes(main_data))


def assemble(
    base_container: HmiFile,
    pages: list[bytes],
    fonts: list[bytes],
    images: list[tuple[bytes, bytes]],
) -> HmiFile:
    """Assembles a container from finished section bytes.

    ``images`` is a list of (``.i``, ``.is``) pairs. Their order determines the image ids. The
    ``base_container`` supplies the base buffer, the model header and the template for
    ``Program.s``/``main.HMI``.

    ``pages`` must already be serialised, checksum-bearing ``.pa`` sections. Fonts and image pairs
    are embedded verbatim; ``main.HMI`` is cloned from the ISO-8859-1 base and given a new resource
    directory and a new checksum.
    """

    program = base_container.live("Program.s")
    main = base_container.live("main.HMI")
    if program is None or main is None or len(main.data) < 0x60:
        raise WriterError("base does not fully contain Program.s/main.HMI")

    main_bytes = _build_main_hmi(main, len(pages), len(fonts), len(images))

    payloads: list[tuple[str, bytes]] = [("Program.s", program.data)]
    payloads.extend((f"{index}.zi", data) for index, data in enumerate(fonts))
    payloads.extend((f"{index}.is", pair[1]) for index, pair in enumerate(images))
    payloads.extend((f"{index}.i", pair[0]) for index, pair in enumerate(images))
    payloads.extend((f"{index}.pa", data) for index, data in enumerate(pages))
    payloads.append(("main.HMI", main_bytes))

    start = 0x700000
    sections: list[Section] = []
    for name, data in payloads:
        sections.append(
            Section(
                name_field=name.encode("ascii"),
                start=start,
                size=len(data),
                deleted=0,
                reserved=b"\x00\x00\x00",
                data=data,
            )
        )
        start += len(data)

    directory_end = 4 + len(sections) * 28
    if directory_end > 0x700000:
        raise WriterError("section directory overruns the start of the payload")
    prefix = bytearray(base_container.gap[:0x700000])
    if len(prefix) < 0x700000:
        prefix.extend(b"\x00" * (0x700000 - len(prefix)))
    total_size = start
    prefix.extend(b"\x00" * (total_size - len(prefix)))
    return HmiFile(sections=sections, gap=bytes(prefix), total_size=total_size)


def assemble_from_skeleton(
    skeleton: HmiFile,
    pages: list[bytes],
    fonts: list[bytes],
    images: list[tuple[bytes, bytes]],
) -> HmiFile:
    """Mounts the project into a container fully produced by the editor.

    Directory count, tombstones, unknown record fields and every unassigned byte of the skeleton are
    kept. Changed live sections are tombstoned and appended as new records plus payload at the end of
    the file.
    """

    expected = {
        "Program.s",
        "main.HMI",
        *(f"{index}.pa" for index in range(len(pages))),
        *(f"{index}.zi" for index in range(len(fonts))),
        *(f"{index}.i" for index in range(len(images))),
        *(f"{index}.is" for index in range(len(images))),
    }
    live_names = {section.name for section in skeleton.sections if section.is_live}
    missing = sorted(expected - live_names)
    if missing:
        raise WriterError(f"skeleton does not contain all target sections: {missing}")

    # The resources are already in their final order in the editor skeleton. This keeps previews and
    # unknown resource metadata unchanged.
    for index, font in enumerate(fonts):
        section = skeleton.live(f"{index}.zi")
        if section is None or section.data != font:
            raise WriterError(f"skeleton font {index} differs from the target resource")
    for index, (preview, source) in enumerate(images):
        if preview[12:16] != source[12:16]:
            raise WriterError(f"skeleton image preview {index} has wrong dimensions")

    main = skeleton.live("main.HMI")
    if main is None or len(main.data) < 0x60:
        raise WriterError("skeleton contains no complete main.HMI")
    replacements = {f"{index}.pa": data for index, data in enumerate(pages)}
    replacements.update({f"{index}.i": pair[0] for index, pair in enumerate(images)})
    replacements.update({f"{index}.is": pair[1] for index, pair in enumerate(images)})
    replacements["main.HMI"] = _build_main_hmi(main, len(pages), len(fonts), len(images))

    result = copy.deepcopy(skeleton)
    for section in result.sections:
        if (
            section.is_live
            and section.name not in expected
            and any(section.name.endswith(suffix) for suffix in (".pa", ".zi", ".i", ".is"))
        ):
            section.name_field = b"\x00" + section.name_field[1:]
            section.deleted = 1
    raw = bytearray(result.gap)
    cursor = len(raw)
    replaced: set[str] = set()
    # Do not bend the existing record: its old data would then remain in the container without a
    # matching tombstone and the editor reports "Wrong resource file". The editor itself instead
    # zeroes the first name byte, marks the record deleted and appends a new record plus data.
    for name, data in replacements.items():
        section = result.live(name)
        if section is None:
            continue
        name_field = section.name_field
        reserved = section.reserved
        section.name_field = b"\x00" + name_field[1:]
        section.deleted = 1
        result.sections.append(
            Section(
                name_field=name_field,
                start=cursor,
                size=len(data),
                deleted=0,
                reserved=reserved,
                data=data,
            )
        )
        raw.extend(data)
        cursor += len(data)
        replaced.add(name)
    if replaced != set(replacements):
        absent = sorted(set(replacements) - replaced)
        raise WriterError(f"skeleton sections not replaced: {absent}")
    result.gap = bytes(raw)
    result.total_size = len(raw)
    return result
