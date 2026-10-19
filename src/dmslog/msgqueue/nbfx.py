"""Decoder for .NET Binary Format: XML (NBFX, [MS-NBFX]) with inline strings only.

The input is an untrusted BLOB: the decoder is iterative (explicit stack, no recursion), single pass,
O(n), bounded by :class:`Limits`, and every failure is an :class:`NbfxError` that carries a code and a
byte offset but never any content of the BLOB. Records that need a static dictionary are rejected with
the ``dictionary_record`` code.
"""

from __future__ import annotations

import base64
import re
import struct
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NoReturn
from uuid import UUID

# The 12 bytes before the first record are not NBFX; only this observed 8-byte signature is accepted
# and the following 4 bytes are ignored.
HEADER_SIGNATURE = bytes.fromhex("0004000001060001")
HEADER_SIZE = 12

_MAX_INT31 = 2**31 - 1
_MAX_DATETIME_TICKS = 3_155_378_975_999_999_999
_TICKS_PER_SECOND = 10_000_000
_TICKS_PER_DAY = 864_000_000_000
_NAME_RE = re.compile(r"[^\W\d][\w.\-]*")
_XML_FORBIDDEN_RE = re.compile("[^\t\n\r -퟿-�\U00010000-\U0010ffff]")
_ATTR_ESCAPES = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "\t": "&#9;",
    "\n": "&#10;",
    "\r": "&#13;",
}
_TEXT_ESCAPES = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "\r": "&#13;"}

ERROR_CODES = (
    "bad_header",
    "truncated",
    "unknown_record",
    "dictionary_record",
    "bad_length",
    "bad_name",
    "bad_utf8",
    "too_large",
    "too_deep",
    "too_many_nodes",
    "text_too_large",
    "unbalanced",
    "trailing_data",
)

TEXT_KINDS = (
    "chars", "int", "bool", "double", "float", "decimal", "datetime", "guid", "uniqueid", "timespan",
    "bytes", "empty",
)  # fmt: skip


@dataclass(frozen=True)
class Limits:
    """Defensive bounds for one decode (all checked while reading, never after)."""

    max_bytes: int = 8 * 1024 * 1024
    max_depth: int = 256
    max_nodes: int = 200_000  # elements + texts + attributes (xmlns declarations count as attributes)
    max_text: int = 1024 * 1024  # per string (names, URIs, text values)
    max_total_text: int = 16 * 1024 * 1024
    max_attrs: int = 256  # attributes + xmlns declarations per element


@dataclass(frozen=True)
class Node:
    name: str  # local name
    prefix: str  # '' without prefix
    xmlns: tuple[tuple[str, str], ...]  # (prefix, uri) declared on this element; prefix '' = default
    attrs: tuple[tuple[str, str, str], ...]  # (prefix, name, text value)
    children: tuple[Node | Text, ...]


@dataclass(frozen=True)
class Text:
    value: str  # canonical XML text
    kind: str  # one of TEXT_KINDS


class NbfxError(ValueError):
    """Decoding failed. ``code`` is one of ERROR_CODES; the message never carries BLOB content."""

    def __init__(self, code: str, offset: int):
        super().__init__(f"NBFX error {code} at offset {offset}")
        self.code = code
        self.offset = offset


# --- value converters (fixed-size payloads) -------------------------------------------------------------


def float_text(value: float) -> str:
    """XML Schema spelling of a double: ``INF``, ``-INF``, ``NaN`` or the Python ``repr``."""
    if value != value:
        return "NaN"
    if value in (float("inf"), float("-inf")):
        return "INF" if value > 0 else "-INF"
    return repr(value)


def _float32_text(raw: bytes) -> str:
    (value,) = struct.unpack("<f", raw)
    if value != value or value in (float("inf"), float("-inf")):
        return float_text(value)
    for digits in range(1, 10):  # shortest decimal that reads back as the same binary32
        text = f"{value:.{digits}g}"
        try:
            if struct.pack("<f", float(text)) == raw:
                return repr(float(text))
        except OverflowError:  # a short spelling that rounds past the largest binary32
            continue
    return repr(value)  # pragma: no cover (nine digits always round-trip a binary32)


def _decimal_text(raw: bytes) -> str | None:
    _reserved, scale, sign, hi, lo = struct.unpack("<HBBIQ", raw)
    if scale > 28:
        return None
    digits = str((hi << 64) | lo)
    if scale:
        digits = digits.zfill(scale + 1)
        digits = f"{digits[:-scale]}.{digits[-scale:]}"
    return ("-" if sign and (hi or lo) else "") + digits


def _datetime_text(raw: bytes) -> str | None:
    (value,) = struct.unpack("<Q", raw)
    ticks, kind = value & 0x3FFFFFFFFFFFFFFF, value >> 62
    if ticks > _MAX_DATETIME_TICKS:
        return None
    days, rest = divmod(ticks, _TICKS_PER_DAY)
    moment = datetime(1, 1, 1) + timedelta(days=days, microseconds=rest // 10)
    text = (
        f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}"
        f"T{moment.hour:02d}:{moment.minute:02d}:{moment.second:02d}"
    )
    fraction = ticks % _TICKS_PER_SECOND
    if fraction:
        text += "." + f"{fraction:07d}".rstrip("0")
    # Kind 2/3 (local): no offset is written, the server's time zone is unknown.
    return text + ("Z" if kind == 1 else "")


def timespan_text(ticks: int) -> str:
    """ISO 8601 duration like ``XmlConvert.ToString(TimeSpan)``: zero parts are omitted."""
    sign = "-" if ticks < 0 else ""
    ticks = abs(ticks)
    days, rest = divmod(ticks, _TICKS_PER_DAY)
    hours, rest = divmod(rest, 36_000_000_000)
    minutes, rest = divmod(rest, 600_000_000)
    seconds, fraction = divmod(rest, _TICKS_PER_SECOND)
    out = f"{sign}P"
    if days:
        out += f"{days}D"
    if hours or minutes or seconds or fraction or not days:
        out += "T"
        if hours:
            out += f"{hours}H"
        if minutes:
            out += f"{minutes}M"
        if seconds or fraction or not (days or hours or minutes):
            out += str(seconds)
            if fraction:
                out += "." + f"{fraction:07d}".rstrip("0")
            out += "S"
    return out


def _guid_text(raw: bytes) -> str:
    return str(UUID(bytes_le=raw))


def _struct_text(fmt: str) -> Callable[[bytes], str]:
    return lambda raw: str(struct.unpack(fmt, raw)[0])


# record type (even code) -> (size in bytes, converter(raw) -> text or None when invalid, kind)
_FIXED: dict[int, tuple[int, Callable[[bytes], str | None], str]] = {
    0x88: (1, _struct_text("<b"), "int"),
    0x8A: (2, _struct_text("<h"), "int"),
    0x8C: (4, _struct_text("<i"), "int"),
    0x8E: (8, _struct_text("<q"), "int"),
    0x90: (4, _float32_text, "float"),
    0x92: (8, lambda raw: float_text(struct.unpack("<d", raw)[0]), "double"),
    0x94: (16, _decimal_text, "decimal"),
    0x96: (8, _datetime_text, "datetime"),
    0xAC: (16, _guid_text, "uniqueid"),
    0xAE: (8, lambda raw: timespan_text(struct.unpack("<q", raw)[0]), "timespan"),
    0xB0: (16, _guid_text, "guid"),
    0xB2: (8, _struct_text("<Q"), "int"),
    0xB4: (1, lambda raw: "true" if raw[0] else "false", "bool"),
}
_CONSTANT_TEXT = {
    0x80: ("0", "int"),
    0x82: ("1", "int"),
    0x84: ("false", "bool"),
    0x86: ("true", "bool"),
    0xA8: ("", "empty"),
}
# length-prefixed texts: record -> (width of the length field, encoding or None for raw bytes)
_VARIABLE = {
    0x98: (1, "utf-8"),
    0x9A: (2, "utf-8"),
    0x9C: (4, "utf-8"),
    0x9E: (1, None),
    0xA0: (2, None),
    0xA2: (4, None),
    0xB6: (1, "utf-16-le"),
    0xB8: (2, "utf-16-le"),
    0xBA: (4, "utf-16-le"),
}
_ARRAY_TYPES = frozenset({0x8A, 0x8C, 0x8E, 0x90, 0x92, 0x94, 0x96, 0xAE, 0xB0, 0xB4})

_START_LIST, _END_LIST = 0xA4, 0xA6
_DICTIONARY_RECORDS = frozenset(
    {0x06, 0x07, 0x0A, 0x0B, *range(0x0C, 0x26), *range(0x42, 0x5E), 0xAA, 0xAB, 0xBC, 0xBD}
)


def _is_text_record(rec: int) -> bool:
    return 0x80 <= rec <= 0xBD


def _is_attribute_record(rec: int) -> bool:
    return rec in (0x04, 0x05, 0x08, 0x09) or 0x26 <= rec <= 0x3F


def _is_element_record(rec: int) -> bool:
    return rec in (0x40, 0x41) or 0x5E <= rec <= 0x77


class _Open:
    """An element being built."""

    __slots__ = ("attrs", "children", "in_start", "name", "prefix", "xmlns")

    def __init__(self, prefix: str, name: str):
        self.prefix = prefix
        self.name = name
        self.xmlns: list[tuple[str, str]] = []
        self.attrs: list[tuple[str, str, str]] = []
        self.children: list[Node | Text] = []
        self.in_start = True

    def freeze(self) -> Node:
        return Node(self.name, self.prefix, tuple(self.xmlns), tuple(self.attrs), tuple(self.children))


class _Decoder:
    def __init__(self, data: bytes, limits: Limits):
        self.data = data
        self.n = len(data)
        self.pos = 0
        self.limits = limits
        self.nodes = 0
        self.total_text = 0

    # -- primitives
    def fail(self, code: str, offset: int | None = None) -> NoReturn:
        raise NbfxError(code, self.pos if offset is None else offset)

    def take(self, size: int) -> bytes:
        if size > self.n - self.pos:
            self.fail("truncated", self.n)
        raw = self.data[self.pos : self.pos + size]
        self.pos += size
        return raw

    def byte(self) -> int:
        if self.pos >= self.n:
            self.fail("truncated", self.n)
        value = self.data[self.pos]
        self.pos += 1
        return value

    def count_node(self) -> None:
        self.nodes += 1
        if self.nodes > self.limits.max_nodes:
            self.fail("too_many_nodes")

    def count_text(self, size: int, offset: int) -> None:
        if size > self.limits.max_text:
            self.fail("text_too_large", offset)
        self.total_text += size
        if self.total_text > self.limits.max_total_text:
            self.fail("text_too_large", offset)

    def int31(self) -> int:
        """MultiByteInt31: 7 bits per byte, at most 5 bytes, value <= 2^31 - 1."""
        start = self.pos
        value = 0
        for index in range(5):
            part = self.byte()
            value |= (part & 0x7F) << (7 * index)
            if not part & 0x80:
                if value > _MAX_INT31:
                    self.fail("bad_length", start)
                return value
        self.fail("bad_length", start)

    def length(self, width: int) -> int:
        start = self.pos
        value = int.from_bytes(self.take(width), "little", signed=width == 4)
        if value < 0:
            self.fail("bad_length", start)
        return value

    def string(self, *, decode: bool = True) -> str:
        """A MultiByteInt31-prefixed UTF-8 string."""
        start = self.pos
        size = self.int31()
        self.count_text(size, start)
        raw = self.take(size)
        if not decode:
            return ""
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            self.fail("bad_utf8", start)

    def name(self) -> str:
        start = self.pos
        value = self.string()
        if not _NAME_RE.fullmatch(value):
            self.fail("bad_name", start)
        return value

    def prefix(self) -> str:
        start = self.pos
        value = self.string()
        if value and not _NAME_RE.fullmatch(value):
            self.fail("bad_name", start)
        return value

    # -- texts
    def scalar_text(self, rec: int, start: int) -> tuple[str, str]:
        """Decode the payload of the text record ``rec`` (even code, already consumed)."""
        if rec in _CONSTANT_TEXT:
            return _CONSTANT_TEXT[rec]
        if rec in _FIXED:
            size, convert, kind = _FIXED[rec]
            value = convert(self.take(size))
            if value is None:
                self.fail("bad_length", start)
            self.count_text(len(value), start)
            return value, kind
        if rec in _VARIABLE:
            width, encoding = _VARIABLE[rec]
            size = self.length(width)
            self.count_text(size, start)
            raw = self.take(size)
            if encoding is None:
                return base64.b64encode(raw).decode("ascii"), "bytes"
            if encoding == "utf-16-le" and size % 2:
                self.fail("bad_length", start)
            try:
                return raw.decode(encoding), "chars"
            except UnicodeDecodeError:
                self.fail("bad_utf8", start)
        self.fail("unknown_record", start)

    def text_value(self, rec: int, start: int) -> tuple[str, str]:
        """A text record (even code) or a list: returns (value, kind)."""
        if rec in _DICTIONARY_RECORDS:
            self.fail("dictionary_record", start)
        if rec != _START_LIST:
            return self.scalar_text(rec, start)
        items: list[str] = []
        while True:
            item_start = self.pos
            item = self.byte()
            if item == _END_LIST:
                return " ".join(items), "chars"
            if item in _DICTIONARY_RECORDS:
                self.fail("dictionary_record", item_start)
            if item & 1 or not _is_text_record(item) or item == _START_LIST:
                self.fail("unknown_record", item_start)
            items.append(self.scalar_text(item, item_start)[0])
            self.count_node()

    # -- attributes and elements
    def attribute(self, rec: int, owner: _Open, start: int) -> None:
        if len(owner.attrs) + len(owner.xmlns) >= self.limits.max_attrs:
            self.fail("too_many_nodes", start)
        self.count_node()
        if rec in (0x08, 0x09):
            prefix = self.prefix() if rec == 0x09 else ""
            owner.xmlns.append((prefix, self.string()))
            return
        prefix = ""
        if rec == 0x05:
            prefix = self.prefix()
        elif rec != 0x04:
            prefix = chr(ord("a") + rec - 0x26)
        name = self.name()
        value_start = self.pos
        value_rec = self.byte()
        if value_rec in _DICTIONARY_RECORDS:
            self.fail("dictionary_record", value_start)
        if value_rec & 1 or not _is_text_record(value_rec):
            self.fail("unknown_record", value_start)
        value, _kind = self.text_value(value_rec, value_start)
        owner.attrs.append((prefix, name, value))

    def start_element(self, rec: int) -> _Open:
        prefix = ""
        if rec == 0x41:
            prefix = self.prefix()
        elif rec != 0x40:
            prefix = chr(ord("a") + rec - 0x5E)
        name = self.name()
        self.count_node()
        return _Open(prefix, name)

    def array(self, parent: _Open, depth: int) -> None:
        """Array record: a template element, EndElement, a value type and N packed values."""
        rec_pos = self.pos
        rec = self.byte()
        if rec in _DICTIONARY_RECORDS:
            self.fail("dictionary_record", rec_pos)
        if not _is_element_record(rec):
            self.fail("unknown_record", rec_pos)
        template = self.start_element(rec)
        while True:
            pos = self.pos
            nxt = self.byte()
            if nxt == 0x01:
                break
            if nxt in _DICTIONARY_RECORDS:
                self.fail("dictionary_record", pos)
            if not _is_attribute_record(nxt):
                self.fail("unknown_record", pos)
            self.attribute(nxt, template, pos)
        type_pos = self.pos
        type_rec = self.byte()
        if type_rec in _DICTIONARY_RECORDS:
            self.fail("dictionary_record", type_pos)
        if type_rec not in _ARRAY_TYPES:
            self.fail("unknown_record", type_pos)
        count_pos = self.pos
        count = self.int31()
        size, convert, kind = _FIXED[type_rec]
        if count * size > self.n - self.pos:
            self.fail("truncated", self.n)
        if depth + 1 > self.limits.max_depth:
            self.fail("too_deep", rec_pos)
        per_item = 2 + len(template.attrs) + len(template.xmlns)  # element + text + its attributes
        if count * per_item > self.limits.max_nodes - self.nodes + per_item - 1:
            self.fail("too_many_nodes", count_pos)
        for _ in range(count):
            value_pos = self.pos
            value = convert(self.take(size))
            if value is None:
                self.fail("bad_length", value_pos)
            self.count_text(len(value), value_pos)
            parent.children.append(
                Node(
                    template.name,
                    template.prefix,
                    tuple(template.xmlns),
                    tuple(template.attrs),
                    (Text(value, kind),),
                )
            )
        self.nodes += count * per_item - (per_item - 1)  # the template was already counted

    # -- main loop
    def run(self) -> Node:
        data = self.data
        if self.n > self.limits.max_bytes:
            self.fail("too_large", 0)
        head = data[: len(HEADER_SIGNATURE)]
        if head != HEADER_SIGNATURE[: len(head)]:
            self.fail("bad_header", 0)
        if self.n < HEADER_SIZE:
            self.fail("truncated", self.n)
        self.pos = HEADER_SIZE
        first = self.byte()
        if first in _DICTIONARY_RECORDS:
            self.fail("dictionary_record", HEADER_SIZE)
        if not _is_element_record(first):
            self.fail("bad_header", HEADER_SIZE)

        stack: list[_Open] = [self.start_element(first)]
        root: Node | None = None
        while stack:
            start = self.pos
            rec = self.byte()
            top = stack[-1]
            if rec == 0x01:
                node = stack.pop().freeze()
                if stack:
                    stack[-1].children.append(node)
                else:
                    root = node
            elif rec == 0x02:
                self.string(decode=False)  # comment: read and dropped
            elif rec in _DICTIONARY_RECORDS:
                self.fail("dictionary_record", start)
            elif rec == 0x03:
                top.in_start = False
                self.array(top, len(stack))
            elif _is_attribute_record(rec):
                if not top.in_start:
                    self.fail("unknown_record", start)
                self.attribute(rec, top, start)
            elif _is_element_record(rec):
                top.in_start = False
                if len(stack) >= self.limits.max_depth:
                    self.fail("too_deep", start)
                stack.append(self.start_element(rec))
            elif _is_text_record(rec):
                top.in_start = False
                value, kind = self.text_value(rec & 0xFE, start)
                self.count_node()
                top.children.append(Text(value, kind))
                if rec & 1:  # text + EndElement
                    node = stack.pop().freeze()
                    if stack:
                        stack[-1].children.append(node)
                    else:
                        root = node
            else:
                self.fail("unknown_record", start)
        if self.pos != self.n:
            # An EndElement after the root closed has nothing left to close.
            self.fail("unbalanced" if data[self.pos] == 0x01 else "trailing_data")
        assert root is not None
        return root


def decode(blob: bytes, limits: Limits = Limits()) -> Node:  # noqa: B008 (Limits is frozen)
    """Decode a message-queue BLOB (12-byte header + NBFX records) into a tree."""
    return _Decoder(bytes(blob), limits).run()


# --- serialization --------------------------------------------------------------------------------------


def _escape(text: str, table: dict[str, str]) -> str:
    text = _XML_FORBIDDEN_RE.sub("�", text)
    return "".join(table.get(ch, ch) for ch in text)


def to_xml(root: Node, *, max_chars: int = 4_000_000) -> str:
    """XML 1.0 text of a tree: escaped, without an XML declaration. Iterative.

    Characters forbidden in XML 1.0 become U+FFFD. Raises ``NbfxError("too_large")`` when the output
    would pass ``max_chars``.
    """
    out: list[str] = []
    size = 0

    def emit(piece: str) -> None:
        nonlocal size
        size += len(piece)
        if size > max_chars:
            raise NbfxError("too_large", 0)
        out.append(piece)

    work: list[Node | Text | str] = [root]  # a str is a closing tag waiting to be written
    while work:
        item = work.pop()
        if isinstance(item, str):
            emit(item)
        elif isinstance(item, Text):
            emit(_escape(item.value, _TEXT_ESCAPES))
        else:
            tag = f"{item.prefix}:{item.name}" if item.prefix else item.name
            head = [f"<{tag}"]
            for prefix, uri in item.xmlns:
                label = f"xmlns:{prefix}" if prefix else "xmlns"
                head.append(f' {label}="{_escape(uri, _ATTR_ESCAPES)}"')
            for prefix, name, value in item.attrs:
                label = f"{prefix}:{name}" if prefix else name
                head.append(f' {label}="{_escape(value, _ATTR_ESCAPES)}"')
            if item.children:
                emit("".join(head) + ">")
                work.append(f"</{tag}>")
                work.extend(reversed(item.children))
            else:
                emit("".join(head) + "/>")
    return "".join(out)
