"""Reader and writer for the binary Nextion Editor project container (`.HMI`).

The format is vendor-proprietary and undocumented. The layout described here was reverse engineered
against Nextion Editor 1.68.1.3034 and holds only for that editor version.

Layout
------

Container::

    u32                      directory record count
    n × 28 bytes             directory record:
                                 char name[16]   null-terminated
                                 u32  start      absolute file position
                                 u32  size
                                 u8   deleted
                                 u8   reserved[3]
    then                     payload, starting at 0x700000

A deleted record is marked by zeroing the first byte of its name. The editor appends new versions
instead of overwriting existing ones; a project file therefore contains its own edit history and
never shrinks.

Sections are named ``main.HMI`` (project settings), ``Program.s`` (global code), ``<n>.pa``
(page n), ``<n>.zi`` (font n) as well as ``<n>.i`` and ``<n>.is`` (image n). Resources are addressed
by their index, not by a name; the order is therefore part of the contract.

Page section::

    u32   checksum
    u32   data size    equals the section size
    u32   info_addr    always 56
    u32   object count
    ...   8 bytes, unassigned
    char  name[16]     from 0x18
    ...
    from info_addr:    object count × <u32 start, u32 size, u32 unknown>
                       start is relative to info_addr
    object data:       at info_addr + start

Object 0 is the page itself, the remaining objects are its components.

Component::

    <u32 len>"att-NN"                         NN = number of attribute records
    NN × <u32 L><char name[16]><value[L-16]>  value width follows from L
    event markers                             e.g. "codesup-1", suffix = number of code lines
    4 null bytes                              terminates every component

Records with ``L < 16`` are markers without a name field, records with ``L >= 16`` are attributes.

Bytes not understood
--------------------

The reader keeps every unassigned byte unchanged and the writer returns it unchanged.
``roundtrip_is_exact()`` proves this byte for byte. Only once that proof holds may anything be
generated from the model.

The checksums of ``main.HMI`` and the page sections are implemented in ``nextion_hmi_checksum``.
Resource files carry their format header at offset 0 and no such checksum.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from research.nextion_hmi_checksum import directory_checksum, with_page_checksum

DIRECTORY_RECORD_SIZE = 28
DIRECTORY_MIRROR_OFFSET = 0x80000
NAME_FIELD_SIZE = 16
PAGE_HEADER_SIZE = 56
PAGE_NAME_OFFSET = 0x18
CONTENT_HEADER_SIZE = 12
COMPONENT_TERMINATOR = b"\x00\x00\x00\x00"

# Component types, confirmed on a discovery panel.
TYPE_PAGE = 121
TYPE_TEXT = 116
TYPE_BUTTON = 98
TYPE_PICTURE = 112
TYPE_PROGRESS = 106
TYPE_NUMBER = 54
TYPE_TIMER = 51
TYPE_VARIABLE = 52

EVENT_MARKERS = (
    "codesload",
    "codesloadend",
    "codesdown",
    "codesup",
    "codesunload",
    "codestimer",
)


def _decode_name(raw: bytes) -> str:
    """Decodes a null-terminated latin-1 name field."""

    return raw.split(b"\x00")[0].decode("latin-1")


class HmiFormatError(ValueError):
    """The file does not match the expected container format."""


@dataclass
class Attribute:
    """An attribute record of a component."""

    name: str
    value: bytes

    @property
    def record_length(self) -> int:
        return NAME_FIELD_SIZE + len(self.value)

    def as_int(self) -> int:
        """Interprets the value as an unsigned integer (little endian)."""

        return int.from_bytes(self.value, "little")

    def set_int(self, value: int) -> None:
        """Writes an integer back using the existing value width."""

        self.value = int(value).to_bytes(len(self.value), "little")

    def encode(self) -> bytes:
        name = self.name.encode("ascii").ljust(NAME_FIELD_SIZE, b"\x00")
        if len(name) != NAME_FIELD_SIZE:
            raise HmiFormatError(f"attribute name too long: {self.name}")
        return struct.pack("<I", self.record_length) + name + self.value


@dataclass
class Marker:
    """An event marker, for example ``att-39`` or ``codesup-1``."""

    text: str

    def split_count(self) -> tuple[str, int | None]:
        """Splits a ``name-count`` marker; returns ``(name, count)`` or ``(text, None)``."""

        head, separator, tail = self.text.rpartition("-")
        if separator and tail.isdigit():
            return head, int(tail)
        return self.text, None

    def encode(self) -> bytes:
        # latin-1, not ascii: markers are parsed with ``decode("latin-1")``, so a marker carrying a
        # byte >= 0x80 must re-encode 1:1 instead of raising and breaking the byte-exact round-trip.
        raw = self.text.encode("latin-1")
        if len(raw) >= NAME_FIELD_SIZE:
            # A record with L >= 16 is re-read as an Attribute, so an over-long marker (e.g.
            # "codesloadend-100") would silently desynchronise the component. Fail loudly instead.
            raise HmiFormatError(
                f"marker text {self.text!r} encodes to {len(raw)} bytes; markers must stay under "
                f"{NAME_FIELD_SIZE} bytes or they re-parse as attributes"
            )
        return struct.pack("<I", len(raw)) + raw


@dataclass
class CodeLine:
    """A line of event code, as it follows a ``codes*`` marker."""

    raw: bytes

    def encode(self) -> bytes:
        return struct.pack("<I", len(self.raw)) + self.raw


Record = Attribute | Marker | CodeLine


@dataclass
class Component:
    """A page object with its attributes, markers and code lines."""

    records: list[Record]
    trailer: bytes = COMPONENT_TERMINATOR
    unknown_flag: int = 0

    def attribute(self, name: str) -> Attribute | None:
        for record in self.records:
            if isinstance(record, Attribute) and record.name == name:
                return record
        return None

    def require(self, name: str) -> Attribute:
        attribute = self.attribute(name)
        if attribute is None:
            raise HmiFormatError(f"attribute missing: {name}")
        return attribute

    @property
    def type_id(self) -> int:
        return self.require("type").as_int()

    @property
    def objname(self) -> str:
        return self.require("objname").value.decode("latin-1")

    def encode(self) -> bytes:
        return b"".join(record.encode() for record in self.records) + self.trailer


@dataclass
class Page:
    """A page section (``<n>.pa``)."""

    header: bytearray
    components: list[Component]
    content_flags: list[int] = field(default_factory=list)

    @property
    def checksum(self) -> int:
        return struct.unpack_from("<I", self.header, 0)[0]

    @property
    def name(self) -> str:
        raw = bytes(self.header[PAGE_NAME_OFFSET : PAGE_NAME_OFFSET + NAME_FIELD_SIZE])
        return _decode_name(raw)

    def encode(self) -> bytes:
        """Reassembles the section.

        After the rebuild the checksum is recomputed with the method verified for Editor
        1.68.1.3034. Unchanged pages therefore still come out byte for byte identical.
        """

        bodies = [component.encode() for component in self.components]
        table_size = len(bodies) * CONTENT_HEADER_SIZE
        table = bytearray()
        offset = table_size
        for body, flag in zip(bodies, self._flags(len(bodies))):
            table += struct.pack("<III", offset, len(body), flag)
            offset += len(body)

        header = bytearray(self.header)
        total = PAGE_HEADER_SIZE + table_size + sum(len(body) for body in bodies)
        struct.pack_into("<I", header, 4, total)
        struct.pack_into("<I", header, 8, PAGE_HEADER_SIZE)
        struct.pack_into("<I", header, 12, len(bodies))
        section = bytes(header) + bytes(table) + b"".join(bodies)
        return with_page_checksum(section)

    def _flags(self, count: int) -> list[int]:
        flags = list(self.content_flags)
        while len(flags) < count:
            flags.append(0)
        return flags[:count]


@dataclass
class Section:
    """A directory entry with its payload."""

    name_field: bytes
    start: int
    size: int
    deleted: int
    reserved: bytes
    data: bytes

    @property
    def name(self) -> str:
        return _decode_name(self.name_field)

    @property
    def is_live(self) -> bool:
        return self.deleted == 0 and bool(self.name)

    def encode_record(self) -> bytes:
        return struct.pack(
            "<16sIIB3s",
            self.name_field.ljust(NAME_FIELD_SIZE, b"\x00")[:NAME_FIELD_SIZE],
            self.start,
            self.size,
            self.deleted,
            self.reserved.ljust(3, b"\x00")[:3],
        )


@dataclass
class HmiFile:
    """A complete project container.

    ``gap`` holds every byte that belongs to no section. This is the only way to write the file back
    byte for byte without losing the 7 MiB preamble and the remains of deleted sections.
    """

    sections: list[Section]
    gap: bytes
    total_size: int

    def live(self, name: str) -> Section | None:
        for section in self.sections:
            if section.is_live and section.name == name:
                return section
        return None

    def pages(self) -> Iterator[Section]:
        for section in self.sections:
            if section.is_live and section.name.endswith(".pa"):
                yield section

    def encode(self) -> bytes:
        out = bytearray(self.gap)
        if len(out) != self.total_size:
            raise HmiFormatError("base buffer does not match the file size")
        directory_size = 4 + len(self.sections) * DIRECTORY_RECORD_SIZE
        if DIRECTORY_MIRROR_OFFSET + directory_size > len(out):
            raise HmiFormatError("mirrored section directory does not fit into the container")
        directory = bytearray(directory_size)
        struct.pack_into("<I", directory, 0, len(self.sections))
        for index, section in enumerate(self.sections):
            offset = 4 + index * DIRECTORY_RECORD_SIZE
            directory[offset : offset + DIRECTORY_RECORD_SIZE] = section.encode_record()
            if len(section.data) != section.size:
                raise HmiFormatError(
                    f"section {section.name!r}: data length {len(section.data)} does not match "
                    f"size {section.size}"
                )
            end = section.start + section.size
            if end > len(out):
                raise HmiFormatError(f"section {section.name!r} extends past the container end")
            out[section.start : end] = section.data
        checksum = struct.pack("<I", directory_checksum(directory))
        # Editor 1.68 keeps two byte-identical directory copies. If only the first one is updated,
        # it rejects the project with "Wrong Hmifile or Hmifile has been damaged".
        out[:directory_size] = directory
        out[DIRECTORY_MIRROR_OFFSET : DIRECTORY_MIRROR_OFFSET + directory_size] = directory
        out[directory_size : directory_size + 4] = checksum
        mirror_checksum = DIRECTORY_MIRROR_OFFSET + directory_size
        out[mirror_checksum : mirror_checksum + 4] = checksum
        return bytes(out)


def parse_container(raw: bytes) -> HmiFile:
    """Reads the directory and cuts out the sections."""

    if len(raw) < 4:
        raise HmiFormatError("file too short")
    count = struct.unpack_from("<I", raw, 0)[0]
    if count == 0 or 4 + count * DIRECTORY_RECORD_SIZE > len(raw):
        raise HmiFormatError(f"implausible section count: {count}")
    directory_size = 4 + count * DIRECTORY_RECORD_SIZE
    if len(raw) >= DIRECTORY_MIRROR_OFFSET + directory_size:
        mirror = raw[DIRECTORY_MIRROR_OFFSET : DIRECTORY_MIRROR_OFFSET + directory_size]
        if raw[:directory_size] != mirror:
            raise HmiFormatError("mirrored section directories differ from one another")
        expected = directory_checksum(raw[:directory_size])
        primary = struct.unpack_from("<I", raw, directory_size)[0]
        backup = struct.unpack_from("<I", raw, DIRECTORY_MIRROR_OFFSET + directory_size)[0]
        if primary != expected or backup != expected:
            raise HmiFormatError("checksum of the section directory is invalid")

    sections: list[Section] = []
    for index in range(count):
        offset = 4 + index * DIRECTORY_RECORD_SIZE
        name_field, start, size, deleted, reserved = struct.unpack_from("<16sIIB3s", raw, offset)
        if size and start + size > len(raw):
            raise HmiFormatError(f"section {index} lies outside the file")
        sections.append(
            Section(
                name_field=name_field,
                start=start,
                size=size,
                deleted=deleted,
                reserved=reserved,
                data=raw[start : start + size],
            )
        )

    # Everything that is not a section is kept as the base buffer. ``raw`` is immutable and
    # ``HmiFile.encode`` copies it before writing, so it can be shared without a defensive copy.
    return HmiFile(sections=sections, gap=raw, total_size=len(raw))


def parse_page(data: bytes) -> Page:
    """Splits a page section into header, object table and components."""

    if len(data) < PAGE_HEADER_SIZE:
        raise HmiFormatError("page section too short")
    _checksum, data_size, info_addr, objects = struct.unpack_from("<IIII", data, 0)
    if data_size != len(data):
        raise HmiFormatError(f"data size {data_size} does not match {len(data)}")
    if info_addr != PAGE_HEADER_SIZE:
        raise HmiFormatError(f"unexpected info_addr {info_addr}")

    components: list[Component] = []
    flags: list[int] = []
    for index in range(objects):
        entry = info_addr + index * CONTENT_HEADER_SIZE
        start, size, flag = struct.unpack_from("<III", data, entry)
        body = data[info_addr + start : info_addr + start + size]
        if len(body) != size:
            raise HmiFormatError(f"object {index} incomplete")
        components.append(parse_component(body))
        flags.append(flag)

    return Page(header=bytearray(data[:PAGE_HEADER_SIZE]), components=components, content_flags=flags)


def parse_component(body: bytes) -> Component:
    """Splits a component into attributes, markers and code lines."""

    records: list[Record] = []
    position = 0
    expect_code = 0
    while position + 4 <= len(body):
        length = struct.unpack_from("<I", body, position)[0]
        if length == 0 or position + 4 + length > len(body):
            break
        payload = body[position + 4 : position + 4 + length]
        position += 4 + length

        if expect_code > 0:
            records.append(CodeLine(raw=payload))
            expect_code -= 1
            continue

        if length < NAME_FIELD_SIZE:
            marker = Marker(text=payload.decode("latin-1"))
            records.append(marker)
            expect_code = _marker_code_lines(marker)
            continue

        name = _decode_name(payload[:NAME_FIELD_SIZE])
        records.append(Attribute(name=name, value=payload[NAME_FIELD_SIZE:]))

    return Component(records=records, trailer=body[position:])


def _marker_code_lines(marker: Marker) -> int:
    """Returns the line count of a ``codes*`` marker, otherwise 0."""

    head, count = marker.split_count()
    if head in EVENT_MARKERS and count is not None:
        return count
    return 0


def roundtrip_is_exact(path: Path) -> bool:
    """The proof: parsing and writing back must yield the same bytes.

    As long as this test does not hold for every reference file, the format model is incomplete and
    nothing may be generated.
    """

    raw = path.read_bytes()
    container = parse_container(raw)
    if container.encode() != raw:
        return False

    # Additionally check every page section on its own.
    for section in container.pages():
        if parse_page(section.data).encode() != section.data:
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="check and display Nextion .HMI containers")
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)

    failures = 0
    for path in args.files:
        raw = path.read_bytes()
        container = parse_container(raw)
        exact = roundtrip_is_exact(path)
        failures += 0 if exact else 1
        live = [s for s in container.sections if s.is_live]
        print(f"{path.name}: {len(raw)} bytes, {len(container.sections)} records, {len(live)} live")
        for section in live:
            print(f"    {section.name:<14} {section.size:>8} bytes")
        for section in container.pages():
            page = parse_page(section.data)
            print(
                f"    page {page.name!r}: checksum 0x{page.checksum:08X}, "
                f"{len(page.components)} objects"
            )
        print(f"    round-trip byte-exact: {'yes' if exact else 'NO'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
