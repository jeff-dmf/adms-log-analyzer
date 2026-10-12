"""Payload extraction at ingestion (ARQUITETURA 6.6, docs/formato/payloads.md).

Only records whose matched template declares ``payload`` come here (no heuristics for records without a
template). The payload is a span of the text of the record, ``msg + "\\n" + cont`` (``msg`` alone when the
record has no continuation); nothing is copied: the result holds offsets.

* start (``PayloadSpec.start``): ``match_end`` = right after the template match in ``msg``; ``msg`` = the
  start of ``msg``; ``first_cont_lt`` = the first continuation line that starts with ``<`` (leading blanks
  allowed). Blanks and line breaks before the payload are skipped, so a ``match_end`` that sits at the end of
  ``msg`` finds the XML on the next line.
* end: the end of the record without trailing blanks, minus ``strip_suffix`` when the text ends with it (the
  ``.`` the adapters append to request/response).
* ``fmt`` comes from the template; ``xml`` becomes ``xml_seq`` when the text has several top-level elements.
* ``role``: the template's, or by the root element (``by_root``: ``...ResponseMessage`` is a response,
  ``...Fault`` a fault, anything else a request). ``direction`` and ``peer`` come from the template or from
  the family's ``[payload_defaults]`` of that role.
* ``msg_id`` / ``corr_id``: the first ``MessageID`` / ``CorrelationID`` element, looked up with a bounded
  regex in the head of the payload (inside ``<Header>`` when there is one), without a parser.
* ``well_formed``: a tokenizer check (tags nest and close, no stray ``<``); JSON goes through ``json.loads``;
  HTML and text are not checked (``None``). A truncated payload (partial record) that does not close is
  ``None``, not ``0``: the record was cut, the content was not necessarily wrong.
* ``keys``: the correlation keys of an XML payload (``payload_keys``), written to ``entry_key`` with
  ``origin = 1`` (ARQUITETURA 6.6, "Chaves do payload"; docs/formato/payloads.md, section 6).

Everything is bounded by the size of the record and never raises: a record that cannot be read as a payload
gives ``None`` (the writer then stores no payload row and does not set the record flag).
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dmslog.parse.templates import PayloadSpec

HEAD_CHARS = 8192  # where MessageID / CorrelationID are looked for
MAX_ID_CHARS = 128
MAX_CHECK_CHARS = 2_000_000  # larger payloads are stored but not checked (well_formed None)
MAX_KEY_SCAN_CHARS = 1_048_576  # keys are looked for in the first 1 MiB characters of a payload
MAX_KEYS = 256  # distinct (key_type, value) pairs kept per payload
MAX_DEPTH = 256  # element nesting tracked for the parent lookup
MAX_ATTR_CHARS = 4096  # attributes of longer tags are not read

_BLANKS = " \t\r\n"
_ROOT = re.compile(r"<([A-Za-z_][\w.\-]*(?::[A-Za-z_][\w.\-]*)?)")
_HEADER_END = re.compile(r"</(?:[\w.\-]+:)?Header\s*>")
_IDS = re.compile(r"<(?:[\w.\-]+:)?(MessageID|CorrelationID)(?:\s[^>]*)?>([^<]*)<")
_NAME = r"(?:[^\W\d]|:)[^\s/>\"'=<]*+"
# A tag body (the text between '<' and '>'), possessive quantifiers throughout: linear, no backtracking.
_OPEN = re.compile(rf"({_NAME})((?:\s++{_NAME}\s*+=\s*+(?:\"[^\"]*+\"|'[^']*+'))*+)\s*+(/?)", re.DOTALL)
_CLOSE = re.compile(rf"/({_NAME})\s*+", re.DOTALL)

_XML_FORMATS = ("xml", "xml_seq", "html")
_START_BYTES = {"xml": "<", "xml_seq": "<", "html": "<", "json": "{[", "text": ""}
_DEFAULT_IN = ("request", "publication")  # direction guess when neither the template nor the family says


@dataclass(frozen=True, slots=True)
class PayloadInfo:
    """What the ingestion stores in a ``payload`` row (the linking columns are filled later)."""

    role: str
    direction: str
    peer: str | None
    operation: (
        str | None
    )  # local name of the root element (derive/payloads.py replaces it with the event name)
    msg_id: str | None
    corr_id: str | None
    fmt: str
    root: str | None
    char_off: int
    char_len: int
    well_formed: int | None
    truncated: int
    keys: tuple[tuple[str, str], ...] = ()  # (key_type, value) for entry_key, origin 1

    def row(self) -> tuple[object, ...]:
        """Values in the order of ``IndexWriter``'s payload INSERT (after ``entry_id``)."""
        return (
            self.role, self.direction, self.peer, self.operation, self.msg_id, self.corr_id, self.fmt,
            self.root, self.char_off, self.char_len, self.well_formed, self.truncated,
        )  # fmt: skip


def local_name(root: str | None) -> str | None:
    """``ns0:SwitchingStepsResponseMessage`` -> ``SwitchingStepsResponseMessage``."""
    if not root:
        return None
    return root.rsplit(":", 1)[-1]


def role_of_root(root: str | None) -> str:
    """The ``by_root`` role: Response -> response, Fault -> fault, anything else -> request."""
    name = local_name(root) or ""
    if name.endswith("ResponseMessage"):
        return "response"
    if name.endswith("Fault"):
        return "fault"
    return "request"


def check_xml(text: str, start: int, end: int) -> tuple[bool, int]:
    """Tokenizer check of ``text[start:end]``: ``(well formed, number of top-level elements)``.

    Elements must nest and close, no ``<`` outside a tag, comment, PI or CDATA, and no text outside the
    top-level elements. Entities, namespaces and attribute syntax are not validated, and a ``>`` inside an
    attribute value ends the tag (the adapters escape it).

    Linear by construction: no regex, every step is a ``str.find`` that moves forward and the first thing that
    does not tokenize (a ``<`` without its ``>``, an unterminated comment, a mismatched close) ends the check.
    Never raises.
    """
    try:
        stack: list[str] = []
        roots = 0
        pos = start
        find = text.find
        while True:
            lt = find("<", pos, end)
            if lt < 0:
                return (not stack and roots > 0 and not text[pos:end].strip()), roots
            if lt > pos and not stack and text[pos:lt].strip():
                return False, roots
            if lt + 1 >= end:
                return False, roots
            c = text[lt + 1]
            if c == "!" or c == "?":
                if text.startswith("!--", lt + 1):
                    close, width = find("-->", lt + 4, end), 3
                elif c == "?":
                    close, width = find("?>", lt + 2, end), 2
                elif text.startswith("![CDATA[", lt + 1):
                    close, width = find("]]>", lt + 9, end), 3
                else:
                    close, width = find(">", lt + 2, end), 1
                if close < 0:
                    return False, roots
                pos = close + width
                continue
            gt = find(">", lt + 1, end)
            if gt < 0:
                return False, roots
            pos = gt + 1
            body = text[lt + 1 : gt]
            if c == "/":
                m = _CLOSE.fullmatch(body)
                if m is None or not stack or stack.pop() != m.group(1):
                    return False, roots
                continue
            m = _OPEN.fullmatch(body)
            if m is None:
                return False, roots
            if not stack:
                roots += 1
            if not m.group(3):  # not self-closing
                stack.append(m.group(1))
    except Exception:  # noqa: BLE001 - a log line must never break ingestion
        return False, 0


def _ids(text: str, off: int, end: int) -> tuple[str | None, str | None]:
    head = text[off : min(end, off + HEAD_CHARS)]
    h = _HEADER_END.search(head)
    if h is not None:
        head = head[: h.end()]
    msg_id = corr_id = None
    for m in _IDS.finditer(head):
        value = m.group(2).strip()[:MAX_ID_CHARS] or None
        if m.group(1) == "MessageID":
            if msg_id is None:
                msg_id = value
        elif corr_id is None:
            corr_id = value
        if msg_id is not None and corr_id is not None:
            break
    return msg_id, corr_id


# --------------------------------------------------------------------------- keys of the payload
#
# Which elements give keys, chosen by measurement on the sample ERS (docs/formato/payloads.md, section 6.1).
# Key types are those the catalog already uses, so a payload key meets the same key of the log lines.

# mRID of these parents (local names) is an identifier of a business object: incident, hazard, trouble
# ticket, crew, assignment, vehicle, switching step/plan/group, work. mRIDs of equipment, usage points
# (customers), substations, zones, regions and PSR types stay out (FTS still finds them).
MRID_PARENTS = frozenset(
    {
        "IncidentRecord", "IncidentHazards", "TroubleTickets", "Incidents",
        "Crew", "Crews", "Assignments", "Vehicle",
        "SwitchingStep", "SwitchingSteps", "SwitchingStepGroup", "SwitchingPlan", "Work", "Works",
    }
)  # fmt: skip
# Leaf element (local name) -> key types of its decimal GID.
GID_LEAVES: dict[str, tuple[str, ...]] = {
    "CrewId": ("gid",),
    "IncidentId": ("gid", "incident_gid"),
    "ServiceAssignmentId": ("gid", "incident_gid"),
}
# <Property id="..." value="..."/> of a publication whose value is the mRID of the published entity.
PROPERTY_MRIDS = frozenset({"SM_COMMANDING_MODEL_CUSTOMID"})

# A tag: '<', optional '/', the name, the rest up to '>'. Possessive quantifiers: linear, no backtracking.
_TAG = re.compile(r"<(/?)([^\s/<>]++)([^<>]*+)>")
_ATTRS = re.compile(r"""([^\s=/<>"']++)\s*+=\s*+(?:"([^"<]*+)"|'([^'<]*+)')""")
_GID_HEX = re.compile(r"0[xX]([0-9a-fA-F]{1,16})")
_GID_DEC = re.compile(r"[0-9]{1,20}")
_ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}
_ENTITY = re.compile(r"&(?:(amp|lt|gt|quot|apos)|#([0-9]{1,7})|#x([0-9a-fA-F]{1,6}));")
_ATTR_BLANKS = str.maketrans("\t\n\r", "   ")  # attribute-value normalization of XML 1.0


def _entity(m: re.Match[str]) -> str:
    if m.group(1):
        return _ENTITIES[m.group(1)]
    code = int(m.group(2)) if m.group(2) else int(m.group(3), 16)
    if 0 < code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF:
        return chr(code)
    return "\x00"  # an invalid reference (or a surrogate, which SQLite cannot store) rejects the value


def _ident(raw: str) -> str | None:
    """An identifier as stored: the 5 XML entities and character references decoded, blanks trimmed, at
    most ``MAX_ID_CHARS`` characters, no control character; case and inner spaces kept (``INC 12`` matches
    the log lines)."""
    if len(raw) > 8 * MAX_ID_CHARS:
        return None
    value = _ENTITY.sub(_entity, raw) if "&" in raw else raw
    value = value.strip()
    if not value or len(value) > MAX_ID_CHARS or "<" in value:
        return None
    if any(ch < " " or ch == "\x7f" for ch in value):
        return None
    return value


def _gid(raw: str) -> str | None:
    """A DMS GID in the decimal form of the log lines (``0x0400...`` -> ``288230...``); 0 is not a GID."""
    value = raw.strip()
    m = _GID_HEX.fullmatch(value)
    if m is not None:
        number = int(m.group(1), 16)
    elif _GID_DEC.fullmatch(value):
        number = int(value)
    else:
        return None
    return str(number) if number else None


def _attrs(body: str) -> dict[str, str]:
    """Attributes of a tag body by local name (the first wins); a body over ``MAX_ATTR_CHARS`` gives none."""
    out: dict[str, str] = {}
    if len(body) > MAX_ATTR_CHARS:
        return out
    for m in _ATTRS.finditer(body):
        name = m.group(1).rsplit(":", 1)[-1]
        value = m.group(2) if m.group(2) is not None else m.group(3)
        out.setdefault(name, value.translate(_ATTR_BLANKS))
    return out


def payload_keys(text: str, start: int, end: int) -> tuple[tuple[str, str], ...]:
    """The correlation keys of the XML payload ``text[start:end]``: ``((key_type, value), ...)``.

    * ``msgid``: ``Header/MessageID`` and ``Header/CorrelationID``;
    * ``mrid``: ``mRID`` whose parent is in ``MRID_PARENTS`` (``Vehicle/mRID`` also as ``vehicle``), and the
      value of a ``Property`` in ``PROPERTY_MRIDS``;
    * ``gid``: ``id/@gid`` of a ``ResourceDescription`` (hex turned decimal) and the leaves of ``GID_LEAVES``
      (incident GIDs also as ``incident_gid``).

    Elements are matched by local name (any namespace prefix). Only the first ``MAX_KEY_SCAN_CHARS``
    characters are read, at most ``MAX_KEYS`` distinct pairs are returned, in document order. A leaf counts
    only when its closing tag follows its text, so a cut record never gives a cut value. Linear in the text,
    never raises (``()`` on any error).
    """
    try:
        return _payload_keys(text, start, min(end, start + MAX_KEY_SCAN_CHARS))
    except Exception:  # noqa: BLE001 - a log line must never break ingestion
        return ()


def _payload_keys(text: str, start: int, end: int) -> tuple[tuple[str, str], ...]:
    out: dict[tuple[str, str], None] = {}
    stack: list[str] = []
    deep = 0  # open elements beyond MAX_DEPTH (not tracked)
    open_name: str | None = None  # the last open tag when nothing else came after it
    open_end = 0

    def add(key_types: tuple[str, ...], value: str | None) -> bool:
        if value is None:
            return True
        for key_type in key_types:
            out.setdefault((key_type, value), None)
            if len(out) >= MAX_KEYS:
                return False
        return True

    find = text.find
    match = _TAG.match
    pos = start
    while True:
        lt = find("<", pos, end)
        if lt < 0:
            break
        nxt = text[lt + 1 : lt + 2]
        if nxt in ("!", "?"):  # comment, CDATA, declaration, processing instruction: skipped whole
            open_name = None
            if text.startswith("!--", lt + 1):
                close, width = find("-->", lt + 4, end), 3
            elif text.startswith("![CDATA[", lt + 1):
                close, width = find("]]>", lt + 9, end), 3
            else:
                close, width = find(">", lt + 2, end), 1
            if close < 0:
                break
            pos = close + width
            continue
        m = match(text, lt, end)
        if m is None:  # a stray '<': not a tag
            pos = lt + 1
            continue
        pos = m.end()
        closing, qname, body = m.group(1), m.group(2), m.group(3)
        name = qname.rsplit(":", 1)[-1]
        if closing:
            if open_name == name:  # a leaf: <name ...>text</name>
                parent = None if deep else (stack[-2] if len(stack) >= 2 else None)
                raw = text[open_end : m.start()]
                more = True
                if name == "mRID" and parent in MRID_PARENTS:
                    more = add(("mrid", "vehicle") if parent == "Vehicle" else ("mrid",), _ident(raw))
                elif name in ("MessageID", "CorrelationID") and parent == "Header":
                    more = add(("msgid",), _ident(raw))
                elif name in GID_LEAVES:
                    more = add(GID_LEAVES[name], _gid(raw))
                if not more:
                    break
            open_name = None
            if deep:
                deep -= 1
            elif stack and stack[-1] == name:
                stack.pop()
            elif name in stack[-8:]:  # tolerate a missing close tag nearby
                while stack and stack.pop() != name:
                    pass
            continue
        self_closing = body.endswith("/")
        if name == "id" and not deep and stack and stack[-1] == "ResourceDescription":
            if not add(("gid",), _gid(_attrs(body).get("gid", ""))):
                break
        elif name == "Property":
            attrs = _attrs(body)
            if attrs.get("id") in PROPERTY_MRIDS and not add(("mrid",), _ident(attrs.get("value", ""))):
                break
        if self_closing:
            open_name = None
            continue
        if len(stack) < MAX_DEPTH:
            stack.append(name)
        else:
            deep += 1
        open_name = name
        open_end = m.end()
    return tuple(out)


def _payload_start(spec: PayloadSpec, msg: str, cont: str | None, match_end: int) -> int | None:
    if spec.start == "match_end":
        return min(max(match_end, 0), len(msg))
    if spec.start == "msg":
        return 0
    if cont is None:  # first_cont_lt
        return None
    pos = len(msg) + 1
    for line in cont.split("\n"):
        stripped = line.lstrip(" \t")
        if stripped.startswith("<"):
            return pos + len(line) - len(stripped)
        pos += len(line) + 1
    return None


def extract_payload(
    spec: PayloadSpec,
    msg: str,
    cont: str | None,
    match_end: int,
    partial: bool,
    defaults: Mapping[str, Mapping[str, str]] | None = None,
) -> PayloadInfo | None:
    """Locate and describe the payload of a record; ``None`` when there is none. Never raises."""
    try:
        return _extract(spec, msg, cont, match_end, partial, defaults or {})
    except Exception:  # noqa: BLE001 - a log line must never break ingestion
        return None


def _extract(
    spec: PayloadSpec,
    msg: str,
    cont: str | None,
    match_end: int,
    partial: bool,
    defaults: Mapping[str, Mapping[str, str]],
) -> PayloadInfo | None:
    text = f"{msg}\n{cont}" if cont is not None else msg
    off = _payload_start(spec, msg, cont, match_end)
    if off is None:
        return None
    n = len(text)
    while off < n and text[off] in _BLANKS:
        off += 1
    end = n
    while end > off and text[end - 1] in _BLANKS:
        end -= 1
    suffix = spec.strip_suffix
    if suffix and end - off > len(suffix) and text.endswith(suffix, off, end):
        end -= len(suffix)
        while end > off and text[end - 1] in _BLANKS:
            end -= 1
    if end <= off:
        return None
    fmt = spec.fmt
    first = text[off]
    allowed = _START_BYTES.get(fmt, "")
    if allowed and first not in allowed:
        return None

    root = None
    msg_id = corr_id = None
    well_formed: int | None = None
    if fmt in _XML_FORMATS:
        m = _ROOT.match(text, off)
        root = m.group(1) if m else None
        if fmt != "html":
            msg_id, corr_id = _ids(text, off, end)
            if end - off <= MAX_CHECK_CHARS:
                ok, roots = check_xml(text, off, end)
                if ok:
                    well_formed = 1
                    if roots > 1:
                        fmt = "xml_seq"
                elif not partial:
                    well_formed = 0
    elif fmt == "json" and end - off <= MAX_CHECK_CHARS:
        try:
            json.loads(text[off:end])
            well_formed = 1
        except ValueError:
            well_formed = None if partial else 0

    keys = payload_keys(text, off, end) if fmt in ("xml", "xml_seq") else ()

    role = spec.role
    if role == "by_root":
        role = role_of_root(root)
    direction = spec.direction
    peer = spec.peer
    default = defaults.get(role)
    if direction is None and default is not None:
        direction = default.get("direction")
    if peer is None and default is not None:
        peer = default.get("peer")
    if direction not in ("in", "out"):
        direction = "in" if role in _DEFAULT_IN else "out"
    return PayloadInfo(
        role, direction, peer, local_name(root), msg_id, corr_id, fmt, root, off, end - off,
        well_formed, 1 if partial else 0, keys,
    )  # fmt: skip
