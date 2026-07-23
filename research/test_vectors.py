"""Checks the reference implementation against the vectors from ``test-vectors.md``.

The test reads the vectors from the document itself. Both therefore hold at once:

* the implementation agrees with the published reference, and
* the reference stays correct — a typo in the document makes this test fail.

No ``.HMI`` files are needed; the vectors are contained entirely in the document.

Invocation from the repository root::

    python -m unittest research.test_vectors
"""

from __future__ import annotations

import re
import struct
import unittest
from pathlib import Path

from research.nextion_hmi_checksum import main_checksum, page_checksum, update

DOCUMENT = Path(__file__).resolve().parents[1] / "test-vectors.md"

# "### `0.pa` — 769 bytes — checksum `0x3DBB4308`" or "... — content starts with `0x68542F2F`"
HEADING = re.compile(
    r"^### `(?P<name>[^`]+)` — (?P<size>\d+) bytes — "
    r"(?:checksum `0x(?P<checksum>[0-9A-Fa-f]{8})`|content starts with `0x[0-9A-Fa-f]{8}`)",
    re.MULTILINE,
)
NEXT_HEADING = re.compile(r"^#{2,3} ", re.MULTILINE)

# What the document is expected to contain. A drift guard is only worth its runtime if losing a
# vector fails it, so the coverage is pinned here rather than inferred from whatever happens to
# parse. Adding a vector to test-vectors.md is meant to fail this until the counts are updated.
EXPECTED_VECTORS = {"main.HMI": 3, "Program.s": 1, "0.pa": 1}
EXPECTED_CHECKSUM_VERIFIED = 4  # the three main.HMI vectors and 0.pa; Program.s carries none


def _vectors() -> list[dict[str, object]]:
    """Reads heading and matching hex block per vector from the document."""

    text = DOCUMENT.read_text(encoding="utf-8")
    found: list[dict[str, object]] = []
    for match in HEADING.finditer(text):
        # Bound the search to this vector's own section. Searching the whole remainder would let a
        # heading whose hex block is missing silently adopt the NEXT vector's bytes.
        tail = text[match.end() :]
        following = NEXT_HEADING.search(tail)
        if following is not None:
            tail = tail[: following.start()]
        block = re.search(r"```\w*\n(?P<hex>[0-9a-fA-F\s]+?)\n```", tail)
        if block is None:
            raise AssertionError(f"no hex block below vector {match.group('name')!r}")
        section = bytes.fromhex("".join(block.group("hex").split()))
        found.append(
            {
                "name": match.group("name"),
                "size": int(match.group("size")),
                "checksum": int(match.group("checksum"), 16) if match.group("checksum") else None,
                "section": section,
            }
        )
    if not found:
        raise AssertionError("test-vectors.md contains no readable vectors")
    return found


class TestDocumentedVectors(unittest.TestCase):
    """Every full-section vector from ``test-vectors.md``."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.vectors = _vectors()

    def test_document_contains_the_expected_vectors(self) -> None:
        """Guards the guard: a heading the parser stops seeing must fail, not shrink coverage."""

        counted: dict[str, int] = {}
        for vector in self.vectors:
            name = str(vector["name"])
            counted[name] = counted.get(name, 0) + 1
        self.assertEqual(counted, EXPECTED_VECTORS)

    def test_sizes_match_the_headings(self) -> None:
        for vector in self.vectors:
            with self.subTest(vector=vector["name"], size=vector["size"]):
                self.assertEqual(len(vector["section"]), vector["size"])

    def test_stored_checksum_is_the_first_four_bytes(self) -> None:
        for vector in self.vectors:
            if vector["checksum"] is None:
                continue
            with self.subTest(vector=vector["name"]):
                stored = int.from_bytes(vector["section"][:4], "little")
                self.assertEqual(stored, vector["checksum"])

    def test_checksums_are_reproduced(self) -> None:
        checked = 0
        for vector in self.vectors:
            if vector["checksum"] is None:
                continue  # Program.s carries no checksum.
            name = str(vector["name"])
            if name == "main.HMI":
                compute = main_checksum
            elif name.endswith(".pa"):
                compute = page_checksum
            else:
                continue
            with self.subTest(vector=name, size=vector["size"]):
                self.assertEqual(compute(vector["section"]), vector["checksum"])
                checked += 1
        self.assertEqual(
            checked,
            EXPECTED_CHECKSUM_VERIFIED,
            "checksum coverage changed -- a vector was lost, added or renamed",
        )

    def test_program_s_carries_no_checksum(self) -> None:
        """``Program.s`` begins with ASCII ``//Th`` — that is not a checksum."""

        for vector in self.vectors:
            if vector["name"] == "Program.s":
                self.assertEqual(vector["section"][:4], b"//Th")
                break
        else:
            self.fail("Program.s vector missing")


class TestDifferentialPairs(unittest.TestCase):
    """The GF(2) linearity and the behaviour across the trailing distance.

    A payload byte raised by ``delta`` with ``trailing`` bytes following it must alter the checksum
    under XOR by exactly the documented value — independently of the payload.

    ``trailing`` counts, as in the document, the bytes behind the changed byte within
    ``bytes[4:]``, measured through ``page_checksum``. The bare CRC kernel additionally sees the
    ten trailer bytes of the page section (length, object count, ``\\x00\\x4F``); the same effect
    appears there only at ``trailing + 10``.
    """

    PAIRS = [(0x50, 7, 0x3840A7F6), (0x82, 32, 0x8C061350)]
    PAGE_TRAILER_BYTES = 10

    def _section(self, trailing: int, payload: bool = False, overlap: int = 0) -> bytearray:
        """Minimal page section whose last payload byte has ``trailing`` following bytes.

        ``overlap`` is OR-ed into the byte the caller is about to mutate. It exists so the test
        cannot go vacuous: adding and XOR-ing a delta agree exactly when the byte shares no set bit
        with it (nothing carries), so a payload that happens not to overlap would let an addition
        pass and prove nothing. See ``test_effect_is_independent_of_the_payload``.
        """

        section = bytearray(4 + 12 + trailing + 1 + 8)
        if payload:
            for index in range(4, len(section)):
                section[index] = (index * 37 + 11) & 0xFF  # arbitrary, non-trivial payload
            section[self._position(len(section), trailing)] |= overlap
        # Stamped after the fill: length and object count must be equal in base and variant, or the
        # trailers stop cancelling in the XOR.
        struct.pack_into("<I", section, 12, 1)  # object count
        return section

    @staticmethod
    def _position(size: int, trailing: int) -> int:
        """Index of the byte that has exactly ``trailing`` bytes after it."""

        return size - 1 - trailing

    def _apply(self, delta: int, trailing: int, payload: bool, add: bool = False) -> int:
        """Checksum-XOR caused by changing the one byte, by XOR (the invariant) or by addition."""

        base = self._section(trailing, payload, overlap=delta if add or payload else 0)
        changed = bytearray(base)
        position = self._position(len(changed), trailing)
        if add:
            changed[position] = (changed[position] + delta) & 0xFF
        else:
            changed[position] ^= delta
        # Length and object count stay the same, the trailers cancel out in the XOR.
        return page_checksum(bytes(changed)) ^ page_checksum(bytes(base))

    def _xor_for(self, delta: int, trailing: int, payload: bool = False) -> int:
        return self._apply(delta, trailing, payload)

    def test_documented_pairs_hold(self) -> None:
        for delta, trailing, expected in self.PAIRS:
            with self.subTest(delta=hex(delta), trailing=trailing):
                self.assertEqual(self._xor_for(delta, trailing), expected)

    def test_effect_is_independent_of_the_payload(self) -> None:
        """The core of the linearity: the same XOR effect on arbitrary payload."""

        for delta, trailing, expected in self.PAIRS:
            with self.subTest(delta=hex(delta), trailing=trailing):
                self.assertEqual(self._xor_for(delta, trailing, payload=True), expected)

    def test_adding_the_delta_does_not_reproduce_the_pairs(self) -> None:
        """Proves the XOR test above is not vacuous.

        The delta must be XOR-ed rather than added: only the XOR-delta is payload-independent, since
        an addition carries into neighbouring bits. But the two agree exactly when the byte shares no
        set bit with the delta -- on a zero byte they always agree. So `test_effect_is_independent_of
        _the_payload` only proves something if an addition would genuinely fail it, and that is what
        this asserts. Without it, a payload that happens not to overlap silently guts both tests.
        """

        for delta, trailing, expected in self.PAIRS:
            with self.subTest(delta=hex(delta), trailing=trailing):
                self.assertNotEqual(self._apply(delta, trailing, payload=True, add=True), expected)

    def test_raw_kernel_sees_the_page_trailer(self) -> None:
        """Demonstrates the distance of ten bytes between the kernel view and the section view."""

        for delta, trailing, expected in self.PAIRS:
            with self.subTest(delta=hex(delta), trailing=trailing):
                raw = update(0, bytes([delta]) + bytes(trailing + self.PAGE_TRAILER_BYTES))
                self.assertEqual(raw, expected)

    def test_pairs_are_documented_in_the_reference(self) -> None:
        text = DOCUMENT.read_text(encoding="utf-8")
        for delta, trailing, expected in self.PAIRS:
            with self.subTest(delta=hex(delta)):
                row = re.search(
                    rf"\|\s*`0x{delta:02X}`\s*\|\s*{trailing}\s*\|\s*`0x([0-9A-Fa-f]{{8}})`\s*\|",
                    text,
                )
                self.assertIsNotNone(row, f"differential pair 0x{delta:02X} missing in the document")
                self.assertEqual(int(row.group(1), 16), expected)


if __name__ == "__main__":
    unittest.main()
