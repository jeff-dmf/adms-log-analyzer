"""Exception deriver (ARQUITETURA 6.5, row ``exceptions``).

One ``exception`` event per occurrence: an entry whose template class is ``error`` and that
carries a .NET exception type, or whose continuation looks like a stack trace. Occurrences
are grouped later (at query time) by ``Event.key`` = normalized signature = exception type +
first message line with the volatile parts replaced (GUIDs, numbers, quoted ids, hex, paths).
Frames are never part of the signature; at most ``MAX_FRAMES`` are kept in ``detail``.

The deriver is stateless: ``low_watermark`` is always ``None`` and re-derivation from any
checkpoint trivially gives the same events.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from dmslog.derive.base import Event, StoredEntry
from dmslog.parse.signature import signature

MAX_FRAMES = 8
MAX_CONT_LINES = 400  # lines of the continuation that are looked at
MAX_BUILD_SCAN_CHARS = 64 * 1024
MAX_MESSAGE_CHARS = 240
MAX_FRAME_CHARS = 200
UNKNOWN_TYPE = "UnknownException"

_FRAME = re.compile(r"^\s*at\s+\S")
_STACK_MARKER = re.compile(
    r"^\s*(?:Stack ?trace:?|Managed Code Stack Trace:?|Server stack trace:?"
    r"|Exception rethrown at \[\d+\]:?)\s*$",
    re.IGNORECASE,
)
_TYPE = r"[A-Za-z_][\w.`]*?(?:Exception|Error)"
# "[Reason: |Stack trace: |[...]] Type: message" or just "Type" at the head of a line.
_TYPE_LINE = re.compile(
    r"^\s*(?:Stack ?trace:\s*|Reason:\s*|Full exception:\s*|\[)?"
    rf"(?P<type>{_TYPE})\]?(?::\s*(?P<msg>.*))?$"
)
_TYPE_IN_MSG = re.compile(r"(?<![\w.`])(?P<type>[A-Za-z_][\w.`]*?Exception)\b(?::\s*(?P<msg>.*))?")
_EXC_TYPE_ATTR = re.compile(
    r'ExceptionType:\s*"?(?P<type>[A-Za-z_][\w.`]*)"?'
    r"(?:\s+Reason:\s*(?P<msg>.*?))?\.?\s*(?:StackTrace:.*)?$"
)
_SKIP_PREFIX = re.compile(r"^\s*(?:Reason:\s*|Stack ?trace:\s*|Full exception:\s*)", re.IGNORECASE)
_NOISE = re.compile(
    r"^\s*(?:---|ClientConnectionId:|Error Number:|Inner Exception|Exception rethrown"
    r"|Server stack trace|Managed Code Stack Trace|Stack ?trace)",
    re.IGNORECASE,
)
_BUILD_PD = re.compile(r"[A-Za-z]:\\PD\\(?P<build>\d+)\\")
_BUILD_CI = re.compile(r"[A-Za-z]:\\CI\\(?P<ver>[^\\\s]+)\\(?P<build>\d+)\\")


def _lines(cont: str) -> list[str]:
    return cont.split("\n", MAX_CONT_LINES)[:MAX_CONT_LINES] if cont else []


def _is_frame(line: str) -> bool:
    return bool(_FRAME.match(line))


def _looks_like_stack(lines: list[str]) -> bool:
    return any(_is_frame(ln) or _STACK_MARKER.match(ln) for ln in lines)


def _str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _find_type_and_message(entry: StoredEntry, lines: list[str]) -> tuple[str | None, str | None]:
    """Exception type and first message line: template fields first, then the text."""
    exc_type = _str(entry.field("exc_type"))
    message = _str(entry.field("exc_msg"))
    if exc_type is None:
        m = _EXC_TYPE_ATTR.search(entry.msg) if "ExceptionType:" in entry.msg else None
        if m:
            exc_type = m.group("type")
            message = message or _str(m.group("msg"))
    if exc_type is None and entry.tpl_class == "error":
        m = _TYPE_IN_MSG.search(entry.msg)
        if m:
            exc_type = m.group("type")
            message = message or _str(m.group("msg"))
    if exc_type is None:
        for line in lines:
            if _is_frame(line):
                continue
            m = _TYPE_LINE.match(line)
            if m:
                exc_type = m.group("type")
                message = message or _str(m.group("msg"))
                break
    if exc_type is not None and message is None:
        for line in lines:
            s = line.strip()
            if not s or _is_frame(line) or _NOISE.match(s):
                continue
            m = _TYPE_LINE.match(line)
            if m and m.group("type") == exc_type:
                message = _str(m.group("msg"))
                if message:
                    break
                continue
            message = _str(_SKIP_PREFIX.sub("", s))
            if message:
                break
    return exc_type, message


def _build(entry: StoredEntry, cont: str) -> tuple[str | None, str | None]:
    """Product build (and CI version) from the F:\\PD\\<build>\\ or F:\\CI\\<ver>\\<build>\\ paths."""
    text = cont[:MAX_BUILD_SCAN_CHARS]
    m = _BUILD_PD.search(text)
    if m:
        return m.group("build"), None
    m = _BUILD_CI.search(text)
    if m:
        return m.group("build"), m.group("ver")
    field_build = entry.field("build")
    if field_build is not None and str(field_build).strip():
        return str(field_build).strip(), None
    m = _BUILD_PD.search(entry.msg) or None
    return (m.group("build"), None) if m else (None, None)


def exception_signature(exc_type: str, message: str | None) -> str:
    """``<type>: <normalized first message line>`` (or just the type when there is no message)."""
    norm = signature(message) if message else ""
    return f"{exc_type}: {norm}" if norm else exc_type


class ExceptionDeriver:
    """Stateless deriver: one ``exception`` event per occurrence."""

    kind = "exception"

    def __init__(self, family: str, rules: Any = None) -> None:
        self.family = family
        self.rules = rules

    def feed(self, entry: StoredEntry) -> Iterable[Event]:
        if not entry.cont and entry.tpl_class != "error":
            return ()  # no continuation to look at and not an error record
        try:
            event = self._derive(entry)
        except Exception:  # noqa: BLE001 - a deriver must never raise on odd input
            return ()
        return (event,) if event is not None else ()

    def flush(self, *, eof: bool) -> Iterable[Event]:
        return ()

    def low_watermark(self) -> int | None:
        return None

    def _derive(self, entry: StoredEntry) -> Event | None:
        cont = entry.cont or ""
        if not cont and entry.tpl_class != "error":
            return None  # no continuation to look at and not an error record
        lines = _lines(cont)
        stack_like = _looks_like_stack(lines)
        if not stack_like and entry.tpl_class != "error":
            return None
        exc_type, message = _find_type_and_message(entry, lines)
        if exc_type is None:
            if not stack_like:
                return None
            exc_type = UNKNOWN_TYPE
        sig = exception_signature(exc_type, message)
        frames = [ln.strip()[:MAX_FRAME_CHARS] for ln in lines if _is_frame(ln)]
        build, ci_version = _build(entry, cont)
        detail: dict[str, Any] = {
            "type": exc_type,
            "message": (message or "")[:MAX_MESSAGE_CHARS],
            "signature": sig,
            "frames": frames[:MAX_FRAMES],
            "n_frames": len(frames),
        }
        if build:
            detail["build"] = build
        if ci_version:
            detail["ci_version"] = ci_version
        if entry.tpl_code:
            detail["tpl_code"] = entry.tpl_code
        inner = _str(entry.field("inner_type"))
        if inner:
            detail["inner_type"] = inner
        ts = entry.ts if entry.ts is not None else 0
        return Event(
            kind=self.kind,
            ts_start=ts,
            ts_end=ts,
            name=exc_type,
            key=sig,
            tid=entry.tid,
            start_entry_id=entry.id,
            end_entry_id=entry.id,
            sev=entry.sev,
            n_entries=1,
            detail=detail,
        )
