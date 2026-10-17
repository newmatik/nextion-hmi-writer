"""Synthetic tests for ``nextion_hmi_binary`` and the checksum helpers.

No ``.HMI`` files are needed: containers, pages and components are built in memory.

Invocation from the repository root::

    python -m unittest research.test_hmi_binary
"""

from __future__ import annotations

import struct
import unittest

from research.nextion_hmi_binary import (
    COMPONENT_TERMINATOR,
    DIRECTORY_MIRROR_OFFSET,
    DIRECTORY_RECORD_SIZE,
    PAGE_HEADER_SIZE,
    Attribute,
    CodeLine,
    Component,
    HmiFile,
    HmiFormatError,
    Marker,
    Section,
    parse_component,
    parse_container,
    parse_page,
)
from research.nextion_hmi_checksum import (
    directory_checksum,
    main_checksum,
    page_checksum,
    update,
    update_words,
    with_main_checksum,
    with_page_checksum,
)


def _record(name: str, start: int, size: int, deleted: int = 0) -> bytes:
    return struct.pack("<16sIIB3s", name.encode("latin-1"), start, size, deleted, b"\x00\x00\x00")


def _build_container(sections: list[tuple[str, bytes, int]], total: int = 0x80000 + 0x1000) -> bytes:
    """Builds a minimal valid container; each section is ``(name, data, deleted)``."""

    directory = bytearray(struct.pack("<I", len(sections)))
    raw = bytearray(total)
    position = 0x1000
    for name, data, deleted in sections:
        directory += _record(name, position, len(data), deleted)
        raw[position : position + len(data)] = data
        position += len(data)
    checksum = struct.pack("<I", directory_checksum(directory))
    size = len(directory)
    raw[:size] = directory
    raw[size : size + 4] = checksum
    raw[DIRECTORY_MIRROR_OFFSET : DIRECTORY_MIRROR_OFFSET + size] = directory
    raw[DIRECTORY_MIRROR_OFFSET + size : DIRECTORY_MIRROR_OFFSET + size + 4] = checksum
    return bytes(raw)


def _component(objname: str, type_id: int, code: list[bytes] | None = None) -> Component:
    records: list = [
        Attribute("type", bytes([type_id])),
        Attribute("objname", objname.encode("latin-1")),
    ]
    if code:
        records.append(Marker(f"codesup-{len(code)}"))
        records.extend(CodeLine(line) for line in code)
    return Component(records=records)


def _page_section(components: list[Component], name: str = "page0") -> bytes:
    from research.nextion_hmi_binary import Page

    header = bytearray(PAGE_HEADER_SIZE)
    header[0x18 : 0x18 + len(name)] = name.encode("latin-1")
    return Page(header=header, components=components).encode()


class TestChecksumHelpers(unittest.TestCase):
    def test_update_is_incremental(self) -> None:
        data = bytes(range(40))
        whole = update(0xFFFFFFFF, data)
        split = update(update(0xFFFFFFFF, data[:17]), data[17:])
        self.assertEqual(whole, split)

    def test_update_masks_state_to_32_bits(self) -> None:
        self.assertEqual(update(0x1_FFFF_FFFF, b"\x01"), update(0xFFFFFFFF, b"\x01"))

    def test_update_words_rejects_unaligned_input(self) -> None:
        with self.assertRaises(ValueError):
            update_words(0xFFFFFFFF, b"\x00\x01\x02")

    def test_page_checksum_rejects_short_section(self) -> None:
        with self.assertRaises(ValueError):
            page_checksum(b"\x00" * 15)

    def test_main_checksum_rejects_short_section(self) -> None:
        with self.assertRaises(ValueError):
            main_checksum(b"\x00" * 19)

    def test_with_page_checksum_ignores_old_result_field(self) -> None:
        section = bytearray(64)
        struct.pack_into("<I", section, 12, 3)
        a = with_page_checksum(section)
        section[0:4] = b"\xde\xad\xbe\xef"
        self.assertEqual(with_page_checksum(section), a)
        self.assertEqual(struct.unpack_from("<I", a, 0)[0], page_checksum(a))

    def test_with_main_checksum_stores_result_in_first_word(self) -> None:
        section = bytes(range(32))
        result = with_main_checksum(section)
        self.assertEqual(struct.unpack_from("<I", result, 0)[0], main_checksum(section))
        self.assertEqual(result[4:], section[4:])


class TestAttributeAndMarker(unittest.TestCase):
    def test_attribute_int_roundtrip_keeps_width(self) -> None:
        attribute = Attribute("x", b"\x00\x00")
        attribute.set_int(0x1234)
        self.assertEqual(attribute.value, b"\x34\x12")
        self.assertEqual(attribute.as_int(), 0x1234)

    def test_attribute_set_int_overflow_raises(self) -> None:
        with self.assertRaises(OverflowError):
            Attribute("x", b"\x00").set_int(256)

    def test_attribute_encode_layout(self) -> None:
        encoded = Attribute("id", b"\x05").encode()
        self.assertEqual(struct.unpack_from("<I", encoded, 0)[0], 17)
        self.assertEqual(encoded[4:20], b"id" + b"\x00" * 14)
        self.assertEqual(encoded[20:], b"\x05")

    def test_attribute_name_too_long(self) -> None:
        with self.assertRaises(HmiFormatError):
            Attribute("n" * 17, b"").encode()

    def test_attribute_latin1_name_roundtrips(self) -> None:
        component = Component(records=[Attribute("\xe4b", b"\x01")])
        parsed = parse_component(component.encode())
        self.assertEqual(parsed.records[0].name, "\xe4b")

    def test_marker_split_count(self) -> None:
        self.assertEqual(Marker("codesup-3").split_count(), ("codesup", 3))
        self.assertEqual(Marker("att-39").split_count(), ("att", 39))
        self.assertEqual(Marker("plain").split_count(), ("plain", None))
        self.assertEqual(Marker("a-b").split_count(), ("a-b", None))

    def test_marker_too_long_is_rejected(self) -> None:
        with self.assertRaises(HmiFormatError):
            Marker("codesloadend-100").encode()

    def test_marker_encode(self) -> None:
        self.assertEqual(Marker("att-2").encode(), struct.pack("<I", 5) + b"att-2")


class TestComponent(unittest.TestCase):
    def test_parse_roundtrip_with_code_lines(self) -> None:
        component = _component("b0", 98, [b"page 1", b"print 1"])
        encoded = component.encode()
        parsed = parse_component(encoded)
        self.assertEqual(parsed.encode(), encoded)
        self.assertEqual(parsed.type_id, 98)
        self.assertEqual(parsed.objname, "b0")
        self.assertEqual([r.raw for r in parsed.records if isinstance(r, CodeLine)], [b"page 1", b"print 1"])
        self.assertEqual(parsed.trailer, COMPONENT_TERMINATOR)

    def test_unknown_marker_does_not_consume_code_lines(self) -> None:
        body = Marker("att-2").encode() + Attribute("type", b"\x01").encode() + COMPONENT_TERMINATOR
        parsed = parse_component(body)
        self.assertEqual([type(r) for r in parsed.records], [Marker, Attribute])

    def test_code_line_shorter_than_name_field_is_not_a_marker(self) -> None:
        # A short line that follows a codes* marker must stay a CodeLine, not become a Marker.
        parsed = parse_component(_component("t0", 116, [b"x"]).encode())
        self.assertIsInstance(parsed.records[-1], CodeLine)

    def test_truncated_record_stops_parsing_and_lands_in_trailer(self) -> None:
        good = Attribute("type", b"\x01").encode()
        bad = struct.pack("<I", 500) + b"short"
        parsed = parse_component(good + bad)
        self.assertEqual(len(parsed.records), 1)
        self.assertEqual(parsed.trailer, bad)
        self.assertEqual(parsed.encode(), good + bad)

    def test_require_missing_attribute(self) -> None:
        with self.assertRaises(HmiFormatError):
            Component(records=[]).require("type")
        self.assertIsNone(Component(records=[]).attribute("type"))


class TestPage(unittest.TestCase):
    def test_page_roundtrip_is_byte_exact(self) -> None:
        section = _page_section([_component("page0", 121), _component("t0", 116, [b"a"])])
        page = parse_page(section)
        self.assertEqual(page.name, "page0")
        self.assertEqual(len(page.components), 2)
        self.assertEqual(page.encode(), section)

    def test_page_checksum_matches_stored_value(self) -> None:
        section = _page_section([_component("page0", 121)])
        page = parse_page(section)
        self.assertEqual(page.checksum, page_checksum(section))

    def test_adding_component_updates_header_and_checksum(self) -> None:
        page = parse_page(_page_section([_component("page0", 121)]))
        page.components.append(_component("n0", 54))
        reparsed_bytes = page.encode()
        reparsed = parse_page(reparsed_bytes)
        self.assertEqual(len(reparsed.components), 2)
        self.assertEqual(struct.unpack_from("<I", reparsed_bytes, 4)[0], len(reparsed_bytes))
        self.assertEqual(struct.unpack_from("<I", reparsed_bytes, 12)[0], 2)
        self.assertEqual(reparsed.checksum, page_checksum(reparsed_bytes))
        # New components get flag 0 when no flag is recorded for them.
        self.assertEqual(reparsed.content_flags, [0, 0])

    def test_content_flags_are_preserved(self) -> None:
        from research.nextion_hmi_binary import Page

        header = bytearray(PAGE_HEADER_SIZE)
        page = Page(header=header, components=[_component("a", 1), _component("b", 1)], content_flags=[7, 9])
        self.assertEqual(parse_page(page.encode()).content_flags, [7, 9])

    def test_rejects_short_section(self) -> None:
        with self.assertRaises(HmiFormatError):
            parse_page(b"\x00" * 10)

    def test_rejects_size_mismatch(self) -> None:
        section = bytearray(_page_section([_component("page0", 121)]))
        struct.pack_into("<I", section, 4, len(section) + 1)
        with self.assertRaises(HmiFormatError):
            parse_page(bytes(section))

    def test_rejects_unexpected_info_addr(self) -> None:
        section = bytearray(_page_section([_component("page0", 121)]))
        struct.pack_into("<I", section, 8, 60)
        with self.assertRaises(HmiFormatError):
            parse_page(bytes(section))

    def test_rejects_object_count_beyond_table(self) -> None:
        section = bytearray(_page_section([_component("page0", 121)]))
        struct.pack_into("<I", section, 12, 1000)
        with self.assertRaises(HmiFormatError):
            parse_page(bytes(section))

    def test_rejects_incomplete_object(self) -> None:
        section = bytearray(_page_section([_component("page0", 121)]))
        struct.pack_into("<I", section, PAGE_HEADER_SIZE + 4, 10_000)
        with self.assertRaises(HmiFormatError):
            parse_page(bytes(section))


class TestContainer(unittest.TestCase):
    def setUp(self) -> None:
        self.page = _page_section([_component("page0", 121)])
        self.raw = _build_container(
            [("main.HMI", b"M" * 32, 0), ("0.pa", self.page, 0), ("1.pa", b"old", 1), ("", b"", 0)]
        )

    def test_roundtrip_is_byte_exact(self) -> None:
        container = parse_container(self.raw)
        self.assertEqual(container.encode(), self.raw)

    def test_live_and_pages(self) -> None:
        container = parse_container(self.raw)
        self.assertIsNotNone(container.live("main.HMI"))
        self.assertIsNone(container.live("1.pa"))  # deleted
        self.assertEqual([s.name for s in container.pages()], ["0.pa"])
        self.assertEqual(container.live("0.pa").data, self.page)

    def test_section_is_live_requires_name(self) -> None:
        self.assertFalse(Section(b"\x00" * 16, 0, 0, 0, b"\x00" * 3, b"").is_live)

    def test_modified_section_updates_both_directory_copies(self) -> None:
        container = parse_container(self.raw)
        section = container.live("main.HMI")
        section.data = b"N" * 32
        out = container.encode()
        again = parse_container(out)  # validates mirror equality and checksums
        self.assertEqual(again.live("main.HMI").data, b"N" * 32)

    def test_encode_detects_size_mismatch(self) -> None:
        container = parse_container(self.raw)
        container.live("main.HMI").data = b"short"
        with self.assertRaises(HmiFormatError):
            container.encode()

    def test_encode_detects_section_past_end(self) -> None:
        container = parse_container(self.raw)
        section = container.live("main.HMI")
        section.start = container.total_size - 4
        with self.assertRaises(HmiFormatError):
            container.encode()

    def test_encode_detects_gap_size_mismatch(self) -> None:
        container = parse_container(self.raw)
        container.total_size += 1
        with self.assertRaises(HmiFormatError):
            container.encode()

    def test_rejects_too_short(self) -> None:
        with self.assertRaises(HmiFormatError):
            parse_container(b"\x01")

    def test_rejects_zero_and_implausible_count(self) -> None:
        with self.assertRaises(HmiFormatError):
            parse_container(struct.pack("<I", 0) + b"\x00" * 64)
        with self.assertRaises(HmiFormatError):
            parse_container(struct.pack("<I", 1000) + b"\x00" * 64)

    def test_rejects_file_too_short_for_mirror(self) -> None:
        raw = bytearray(4 + DIRECTORY_RECORD_SIZE + 4)
        struct.pack_into("<I", raw, 0, 1)
        with self.assertRaises(HmiFormatError):
            parse_container(bytes(raw))

    def test_rejects_differing_mirror(self) -> None:
        raw = bytearray(self.raw)
        raw[DIRECTORY_MIRROR_OFFSET + 8] ^= 0xFF
        with self.assertRaisesRegex(HmiFormatError, "differ"):
            parse_container(bytes(raw))

    def test_rejects_bad_checksum(self) -> None:
        raw = bytearray(self.raw)
        size = 4 + 4 * DIRECTORY_RECORD_SIZE
        raw[size] ^= 0xFF
        with self.assertRaisesRegex(HmiFormatError, "checksum"):
            parse_container(bytes(raw))

    def test_rejects_section_outside_file(self) -> None:
        directory = bytearray(struct.pack("<I", 1)) + _record("a.pa", 0xFFFFFF, 10)
        checksum = struct.pack("<I", directory_checksum(directory))
        raw = bytearray(0x80000 + 0x1000)
        size = len(directory)
        for base in (0, DIRECTORY_MIRROR_OFFSET):
            raw[base : base + size] = directory
            raw[base + size : base + size + 4] = checksum
        with self.assertRaisesRegex(HmiFormatError, "outside"):
            parse_container(bytes(raw))

    def test_hmifile_type(self) -> None:
        self.assertIsInstance(parse_container(self.raw), HmiFile)


if __name__ == "__main__":
    unittest.main()
