"""Synthetic NBFX writer: the inverse of ``dmslog.msgqueue.nbfx``, for tests only (issue #74, section 3.5).

Nothing here comes from real data: every value is invented. The writer emits the 12-byte header, every
record the decoder supports, arrays, lists, and (for the error tests) dictionary records, raw bytes and
malformed lengths. ``expected_tree`` computes, with independent code, the tree the decoder must return.

Input model: ``E`` (element), ``Val`` (typed text), ``ArrayOf`` (array record), ``Comment``, ``Raw``.
"""

from __future__ import annotations

import base64
import decimal
import struct
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from dmslog.msgqueue.nbfx import HEADER_SIGNATURE, Node, Text

HEADER = HEADER_SIGNATURE + b"\x00\x00\x00\x00"
END_ELEMENT = b"\x01"

# writer kind -> (record code, fixed payload size or None, decoded kind)
_SIMPLE = {
    "zero": (0x80, 0, "int"),
    "one": (0x82, 0, "int"),
    "false": (0x84, 0, "bool"),
    "true": (0x86, 0, "bool"),
    "empty": (0xA8, 0, "empty"),
    "int8": (0x88, 1, "int"),
    "int16": (0x8A, 2, "int"),
    "int32": (0x8C, 4, "int"),
    "int64": (0x8E, 8, "int"),
    "float": (0x90, 4, "float"),
    "double": (0x92, 8, "double"),
    "decimal": (0x94, 16, "decimal"),
    "datetime": (0x96, 8, "datetime"),
    "uniqueid": (0xAC, 16, "uniqueid"),
    "timespan": (0xAE, 8, "timespan"),
    "guid": (0xB0, 16, "guid"),
    "uint64": (0xB2, 8, "int"),
    "bool": (0xB4, 1, "bool"),
}
_ARRAY_CODES = {
    "int16": 0x8A,
    "int32": 0x8C,
    "int64": 0x8E,
    "float": 0x90,
    "double": 0x92,
    "decimal": 0x94,
    "datetime": 0x96,
    "timespan": 0xAE,
    "guid": 0xB0,
    "bool": 0xB4,
}


@dataclass(frozen=True)
class Val:
    """A typed text value. ``value`` depends on ``kind`` (see ``_payload``). ``width`` picks 8/16/32-bit
    length fields for ``chars``, ``unicode`` and ``bytes``; ``None`` picks the smallest that fits."""

    kind: str
    value: Any = None
    width: int | None = None


@dataclass(frozen=True)
class Comment:
    text: str


@dataclass(frozen=True)
class Raw:
    """Bytes written as they are (malformed input for the error tests)."""

    data: bytes


@dataclass(frozen=True)
class E:
    name: str
    prefix: str = ""
    xmlns: tuple[tuple[str, str], ...] = ()
    attrs: tuple[tuple[str, str, Val], ...] = ()  # (prefix, name, value)
    children: tuple[Any, ...] = ()  # E | Val | Comment | ArrayOf | Raw
    long_form: bool = False  # use Element (0x41) even when a single-letter prefix record exists
    fuse: bool = True  # fuse a last text child with EndElement (odd record)


@dataclass(frozen=True)
class ArrayOf:
    name: str
    type: str  # key of _ARRAY_CODES
    values: tuple[Any, ...]
    prefix: str = ""
    xmlns: tuple[tuple[str, str], ...] = ()
    attrs: tuple[tuple[str, str, Val], ...] = ()


def guid(n: int) -> uuid.UUID:
    """Fabricated GUID number ``n``."""
    return uuid.UUID(int=n)


# --- primitives -----------------------------------------------------------------------------------------


def mbi31(value: int) -> bytes:
    """MultiByteInt31 (not range-checked, so tests can build invalid ones with ``raw_mbi``)."""
    out = bytearray()
    while True:
        part = value & 0x7F
        value >>= 7
        if value:
            out.append(part | 0x80)
        else:
            out.append(part)
            return bytes(out)


def string(text: str) -> bytes:
    raw = text.encode("utf-8")
    return mbi31(len(raw)) + raw


def _length(width: int | None, size: int) -> tuple[int, bytes]:
    """(index 0/1/2 into the 8/16/32-bit record triple, length field)."""
    if width is None:
        width = 8 if size < 0x100 else 16 if size < 0x10000 else 32
    if width == 8:
        return 0, struct.pack("<B", size)
    if width == 16:
        return 1, struct.pack("<H", size)
    return 2, struct.pack("<I", size)


def _decimal_payload(value: decimal.Decimal) -> bytes:
    sign, digits, exponent = value.as_tuple()
    assert isinstance(exponent, int) and -28 <= exponent <= 0
    magnitude = int("".join(map(str, digits)) or "0")
    return struct.pack("<HBBIQ", 0, -exponent, 0x80 if sign else 0, magnitude >> 64, magnitude & (2**64 - 1))


def _payload(val: Val) -> tuple[int, bytes]:
    """(even record code, payload bytes) of a typed value."""
    kind, v = val.kind, val.value
    if kind in ("chars", "unicode", "bytes"):
        if kind == "chars":
            raw = v.encode("utf-8")
            base = 0x98
        elif kind == "unicode":
            raw = v.encode("utf-16-le")
            base = 0xB6
        else:
            raw = v
            base = 0x9E
        index, length = _length(val.width, len(raw))
        return base + 2 * index, length + raw
    code, _size, _decoded = _SIMPLE[kind]
    if kind in ("zero", "one", "false", "true", "empty"):
        return code, b""
    if kind == "int8":
        return code, struct.pack("<b", v)
    if kind == "int16":
        return code, struct.pack("<h", v)
    if kind == "int32":
        return code, struct.pack("<i", v)
    if kind == "int64":
        return code, struct.pack("<q", v)
    if kind == "uint64":
        return code, struct.pack("<Q", v)
    if kind == "float":
        return code, struct.pack("<f", v)
    if kind == "double":
        return code, struct.pack("<d", v)
    if kind == "decimal":
        return code, _decimal_payload(v)
    if kind == "datetime":
        ticks, dt_kind = v
        return code, struct.pack("<Q", (dt_kind << 62) | ticks)
    if kind == "timespan":
        return code, struct.pack("<q", v)
    if kind in ("guid", "uniqueid"):
        return code, v.bytes_le
    if kind == "bool":
        return code, struct.pack("<B", 1 if v else 0)
    raise ValueError(f"unknown writer kind {kind}")


def text_record(val: Val, *, end: bool = False) -> bytes:
    """Bytes of a text record; ``end`` fuses an EndElement (odd code). Lists use ``Val("list", [Val...])``."""
    if val.kind == "list":
        body = b"".join(bytes([_payload(item)[0]]) + _payload(item)[1] for item in val.value)
        return bytes([0xA4 | int(end)]) + body + b"\xa6"
    code, payload = _payload(val)
    return bytes([code | int(end)]) + payload


# --- expected values (independent of the decoder) -------------------------------------------------------


def _float32_expected(v: float) -> str:
    packed = struct.pack("<f", v)
    exact = struct.unpack("<f", packed)[0]
    if exact != exact:
        return "NaN"
    if exact in (float("inf"), float("-inf")):
        return "INF" if exact > 0 else "-INF"
    for digits in range(1, 10):
        candidate = float(f"{exact:.{digits}g}")
        try:
            if struct.pack("<f", candidate) == packed:
                return repr(candidate)
        except OverflowError:
            continue
    raise AssertionError("unreachable")


def _double_expected(v: float) -> str:
    if v != v:
        return "NaN"
    if v in (float("inf"), float("-inf")):
        return "INF" if v > 0 else "-INF"
    return repr(v)


def _datetime_expected(ticks: int, dt_kind: int) -> str:
    seconds, sub = divmod(ticks, 10_000_000)
    moment = datetime(1, 1, 1) + timedelta(seconds=seconds)
    out = moment.isoformat()
    if sub:
        out += "." + f"{sub:07d}".rstrip("0")
    return out + ("Z" if dt_kind == 1 else "")


def _timespan_expected(ticks: int) -> str:
    total_seconds, sub = divmod(abs(ticks), 10_000_000)
    days, total_seconds = divmod(total_seconds, 86400)
    hours, total_seconds = divmod(total_seconds, 3600)
    minutes, seconds = divmod(total_seconds, 60)
    time_part = ""
    if hours:
        time_part += f"{hours}H"
    if minutes:
        time_part += f"{minutes}M"
    if seconds or sub:
        time_part += str(seconds) + ("." + f"{sub:07d}".rstrip("0") if sub else "") + "S"
    day_part = f"{days}D" if days else ""
    if not day_part and not time_part:
        time_part = "0S"
    return ("-" if ticks < 0 else "") + "P" + day_part + ("T" + time_part if time_part else "")


def expected_text(val: Val) -> Text:
    kind, v = val.kind, val.value
    if kind == "list":
        return Text(" ".join(expected_text(item).value for item in v), "chars")
    if kind in ("chars", "unicode"):
        return Text(v, "chars")
    if kind == "bytes":
        return Text(base64.b64encode(v).decode("ascii"), "bytes")
    decoded = _SIMPLE[kind][2]
    text = {
        "zero": lambda: "0",
        "one": lambda: "1",
        "false": lambda: "false",
        "true": lambda: "true",
        "empty": lambda: "",
        "float": lambda: _float32_expected(v),
        "double": lambda: _double_expected(v),
        "decimal": lambda: format(v, "f"),
        "datetime": lambda: _datetime_expected(*v),
        "timespan": lambda: _timespan_expected(v),
        "guid": lambda: str(v),
        "uniqueid": lambda: str(v),
        "bool": lambda: "true" if v else "false",
    }.get(kind, lambda: str(v))()
    return Text(text, decoded)


# --- elements -------------------------------------------------------------------------------------------


def _element_record(prefix: str, name: str, long_form: bool) -> bytes:
    if not prefix:
        return b"\x40" + string(name)
    if len(prefix) == 1 and "a" <= prefix <= "z" and not long_form:
        return bytes([0x5E + ord(prefix) - ord("a")]) + string(name)
    return b"\x41" + string(prefix) + string(name)


def _attribute_records(xmlns, attrs) -> bytes:
    out = bytearray()
    for prefix, uri in xmlns:
        out += (b"\x09" + string(prefix) + string(uri)) if prefix else (b"\x08" + string(uri))
    for prefix, name, val in attrs:
        if not prefix:
            out += b"\x04" + string(name)
        elif len(prefix) == 1 and "a" <= prefix <= "z":
            out += bytes([0x26 + ord(prefix) - ord("a")]) + string(name)
        else:
            out += b"\x05" + string(prefix) + string(name)
        out += text_record(val)
    return bytes(out)


def _array_bytes(arr: ArrayOf) -> bytes:
    code = _ARRAY_CODES[arr.type] | 1  # the value type is the WithEndElement record
    out = bytearray(b"\x03")
    out += _element_record(arr.prefix, arr.name, False) + _attribute_records(arr.xmlns, arr.attrs)
    out += END_ELEMENT + bytes([code]) + mbi31(len(arr.values))
    for v in arr.values:
        item_code, payload = _payload(Val(arr.type, v))
        assert item_code | 1 == code
        out += payload
    return bytes(out)


def _leaf_bytes(child: Any) -> bytes:
    if isinstance(child, Val):
        return text_record(child)
    if isinstance(child, Comment):
        return b"\x02" + string(child.text)
    if isinstance(child, ArrayOf):
        return _array_bytes(child)
    if isinstance(child, Raw):
        return child.data
    raise TypeError(type(child))


def encode_element(root: E) -> bytes:
    """Records of an element tree (without the header); iterative like the decoder."""
    out = bytearray()
    work: list[E | bytes] = [root]  # bytes are emitted as they are; the stack pops from the end
    while work:
        item = work.pop()
        if isinstance(item, bytes):
            out += item
            continue
        out += _element_record(item.prefix, item.name, item.long_form)
        out += _attribute_records(item.xmlns, item.attrs)
        children = list(item.children)
        if children and item.fuse and isinstance(children[-1], Val):
            work.append(text_record(children.pop(), end=True))
        else:
            work.append(END_ELEMENT)
        for child in reversed(children):
            work.append(child if isinstance(child, E) else _leaf_bytes(child))
    return bytes(out)


def encode(root: E, *, header: bytes = HEADER) -> bytes:
    """A whole BLOB: 12-byte header + the element tree."""
    return header + encode_element(root)


def doc(*records: bytes, header: bytes = HEADER) -> bytes:
    """Header plus hand-made record bytes (error tests)."""
    return header + b"".join(records)


def expected_tree(root: E) -> Node:
    """The tree ``decode(encode(root))`` must return. Iterative."""
    # Build bottom-up with an explicit stack of (element, child results list, iterator).
    result: list[Node] = []
    stack: list[tuple[E, list[Any], int]] = [(root, [], 0)]
    while stack:
        element, built, index = stack.pop()
        if index < len(element.children):
            stack.append((element, built, index + 1))
            child = element.children[index]
            if isinstance(child, E):
                stack.append((child, [], 0))
            elif isinstance(child, Val):
                built.append(expected_text(child))
            elif isinstance(child, ArrayOf):
                for v in child.values:
                    built.append(
                        Node(
                            child.name,
                            child.prefix,
                            tuple(child.xmlns),
                            tuple((p, n, expected_text(a).value) for p, n, a in child.attrs),
                            (expected_text(Val(child.type, v)),),
                        )
                    )
            # Comment / Raw produce nothing
            continue
        node = Node(
            element.name,
            element.prefix,
            tuple(element.xmlns),
            tuple((p, n, expected_text(a).value) for p, n, a in element.attrs),
            tuple(built),
        )
        if stack:
            stack[-1][1].append(node)
        else:
            result.append(node)
    return result[0]


# --- the observed message shape (invented values) -------------------------------------------------------

DATACONTRACT_NS = "http://schemas.example.invalid/datacontract"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"


def make_message(n: int = 1, *, nil_payload: bool = False) -> E:
    """A message in the observed shape: root with default xmlns and ``xmlns:i``, header/payload fields,
    ``...FieldSpecified`` pairs and ``i:nil``. All values are invented."""
    header = E(
        "headerField",
        children=(
            E("verbField", children=(Val("chars", f"verb-{n}"),)),
            E("verbFieldSpecified", children=(Val("true"),)),
            E("noun", children=(Val("chars", f"text-{n}"),)),
            E("messageId", children=(Val("guid", guid(n)),)),
            E("revision", children=(Val("int32", n),)),
        ),
    )
    payload = E(
        "payloadField",
        attrs=(("i", "nil", Val("true")),) if nil_payload else (),
        children=()
        if nil_payload
        else (
            E("item", children=(Val("chars", "text-a"),)),
            E("count", children=(Val("int32", n),)),
            ArrayOf("value", "int32", (n, n + 1, n + 2)),
        ),
    )
    return E(
        "WorksEventMessageType",
        xmlns=(("", DATACONTRACT_NS), ("i", XSI_NS)),
        children=(header, payload),
    )
