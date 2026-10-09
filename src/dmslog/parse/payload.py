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
        well_formed, 1 if partial else 0,
    )  # fmt: skip
