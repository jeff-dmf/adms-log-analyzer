"""Parsing layer 1: line envelopes (ARQUITETURA 6.1).

An envelope recognizes the header prefix of a log line (timestamp, PID, TID, level)
and says where the message text starts. Everything here works on raw bytes; offsets
are byte offsets and nothing is decoded.

Contract for every envelope: the header decision for a line must depend only on a
prefix of that line (no look-ahead to the end of line). Appending bytes to a line never
turns a header into a non-header or changes the parsed fields. The record grouping
(``records.py``) relies on this to be chunk- and resume-safe.

New envelopes are added by registering an instance with :func:`register_envelope`.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

BOM = b"\xef\xbb\xbf"

# Envelope level scale stored in entry.level: 1 FATAL .. 8 VERBOSE (ARQUITETURA 5).
LEVELS: dict[bytes, int] = {
    b"FATAL": 1,
    b"EXIT": 2,
    b"ERROR": 3,
    b"ALWAYS": 4,
    b"DEBUGLOG": 5,
    b"WARNING": 6,
    b"INFO": 7,
    b"VERBOSE": 8,
}
LEVEL_NAMES: dict[int, str] = {value: key.decode("ascii") for key, value in LEVELS.items()}
_LEVEL_ALT = b"|".join(LEVELS)


@dataclass(slots=True)
class Header:
    ts_ms: int | None
    pid: int | None
    tid: int | None
    level: int | None
    msg_start: int  # offset of the message text within the line (bytes)
    process: str | None = None  # oasErrLog only


@runtime_checkable
class Envelope(Protocol):
    """A line envelope.

    ``match`` receives one line (with or without its end of line) and returns the parsed
    header, or None when the line is a continuation. Envelopes may also implement
    ``scan(buf, pos, endpos) -> Iterator[tuple[int, Header]]`` yielding the start offset
    and header of every header line starting in ``[pos, endpos)``; ``pos`` is always a
    line start. ``records.py`` uses ``scan`` when present (fast path) and falls back to
    calling ``match`` line by line otherwise. Both must agree.
    """

    name: str

    def match(self, line: bytes) -> Header | None: ...


# Timestamp cache: "YYYY-MM-DD HH:MM:SS" -> ms since the epoch, with the local log time
# encoded as if it were UTC (ARQUITETURA 5, U6). None marks an impossible date.
_EPOCH = datetime(1970, 1, 1)
_MS = timedelta(milliseconds=1)
_TS_CACHE: dict[bytes, int | None] = {}
_TS_CACHE_MAX = 100_000


def _second_ms(sec: bytes) -> int | None:
    try:
        return _TS_CACHE[sec]
    except KeyError:
        pass
    try:
        moment = datetime(
            int(sec[0:4]), int(sec[5:7]), int(sec[8:10]), int(sec[11:13]), int(sec[14:16]), int(sec[17:19])
        )
        value: int | None = (moment - _EPOCH) // _MS
    except ValueError:
        value = None
    if len(_TS_CACHE) >= _TS_CACHE_MAX:
        _TS_CACHE.clear()
    _TS_CACHE[sec] = value
    return value


def parse_ts(sec: bytes, millis: bytes) -> int | None:
    """Return ms for ``YYYY-MM-DD HH:MM:SS`` plus a 3-digit millisecond part, or None."""
    base = _second_ms(sec)
    return None if base is None else base + int(millis)


class RegexEnvelope:
    """Envelope defined by a line-start regex compiled with ``re.MULTILINE``.

    The pattern must start with ``^`` and must not be able to cross a newline.
    """

    name: str = ""
    pattern: re.Pattern[bytes]

    def _header(self, m: re.Match[bytes]) -> Header | None:
        raise NotImplementedError

    def match(self, line: bytes) -> Header | None:
        m = self.pattern.match(line)
        return None if m is None else self._header(m)

    def scan(self, buf: bytes | bytearray, pos: int, endpos: int) -> Iterator[tuple[int, Header]]:
        header = self._header
        for m in self.pattern.finditer(buf, pos, endpos):
            h = header(m)
            if h is not None:
                yield m.start(), h

    def __repr__(self) -> str:
        return f"<envelope {self.name}>"


class AdmsEnvelope(RegexEnvelope):
    """Standard ADMS adapter/service line: ``ts [PID=n][TID=0xh] LEVEL - msg`` (TID optional)."""

    name = "adms"
    pattern = re.compile(
        rb"^(?:\xef\xbb\xbf)?(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.(\d{3}) "
        rb"\[PID=(\d{1,10})\](?:\[TID=0x([0-9a-fA-F]{1,15})\])? (" + _LEVEL_ALT + rb") - ",
        re.MULTILINE,
    )

    def _header(self, m: re.Match[bytes]) -> Header | None:
        sec, millis, pid, tid, level = m.groups()
        ts = parse_ts(sec, millis)
        if ts is None:
            return None
        return Header(ts, int(pid), None if tid is None else int(tid, 16), LEVELS[level], m.end() - m.start())


class OasErrEnvelope(RegexEnvelope):
    """``oasErrLog.log``: ``ts <Process>_<pid> [PID=n][TID=0xh] - (LEVEL) msg``.

    The ``(LEVEL)`` marker and the TID are optional, and the process token is kept as
    written apart from a trailing ``_<digits>`` suffix.
    """

    name = "oaserr"
    pattern = re.compile(
        rb"^(?:\xef\xbb\xbf)?(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.(\d{3}) (\S+?) "
        rb"\[PID=(\d{1,10})\](?:\[TID=0x([0-9a-fA-F]{1,15})\])? - (?:\((" + _LEVEL_ALT + rb")\) )?",
        re.MULTILINE,
    )
    _proc_suffix = re.compile(rb"_\d+\Z")

    def _header(self, m: re.Match[bytes]) -> Header | None:
        sec, millis, proc, pid, tid, level = m.groups()
        ts = parse_ts(sec, millis)
        if ts is None:
            return None
        process = self._proc_suffix.sub(b"", proc).decode("utf-8", "replace")
        return Header(
            ts,
            int(pid),
            None if tid is None else int(tid, 16),
            None if level is None else LEVELS[level],
            m.end() - m.start(),
            process,
        )


class PlainEnvelope:
    """Fallback for unknown text files: every line is a record without timestamp."""

    name = "plain"

    def match(self, line: bytes) -> Header | None:
        return Header(None, None, None, None, 3 if line.startswith(BOM) else 0)

    def scan(self, buf: bytes | bytearray, pos: int, endpos: int) -> Iterator[tuple[int, Header]]:
        find = buf.find
        startswith = buf.startswith
        while pos < endpos:
            yield pos, Header(None, None, None, None, 3 if startswith(BOM, pos, endpos) else 0)
            nl = find(b"\n", pos, endpos)
            if nl < 0:
                return
            pos = nl + 1

    def __repr__(self) -> str:
        return f"<envelope {self.name}>"


ADMS = AdmsEnvelope()
OASERR = OasErrEnvelope()
PLAIN = PlainEnvelope()

ENVELOPES: dict[str, Envelope] = {}
# Generic envelopes declared in families.toml (parse/formats.py): considered by detect_envelope only
# when no envelope of ENVELOPES recognizes a line, so they never change a built-in detection.
FALLBACK_ENVELOPES: dict[str, Envelope] = {}


def register_envelope(envelope: Envelope) -> Envelope:
    """Register an envelope by name; later detection considers it too."""
    ENVELOPES[envelope.name] = envelope
    return envelope


def register_fallback_envelope(envelope: Envelope) -> Envelope:
    """Register a fallback envelope (see ``detect_envelope``); its name must be new."""
    if envelope.name in ENVELOPES:
        raise ValueError(f"envelope {envelope.name!r} is already registered")
    FALLBACK_ENVELOPES[envelope.name] = envelope
    return envelope


for _env in (ADMS, OASERR, PLAIN):
    register_envelope(_env)


def _load_fallbacks() -> None:
    from .formats import ensure_registered  # lazy: formats imports this module

    ensure_registered()


def get_envelope(name: str) -> Envelope:
    """Return the envelope called ``name``, built-in or fallback (KeyError when unknown)."""
    try:
        return ENVELOPES[name]
    except KeyError:
        _load_fallbacks()
        return FALLBACK_ENVELOPES[name]


def is_known_envelope(name: str) -> bool:
    """True when ``get_envelope(name)`` resolves (a source indexed with an envelope that no longer
    exists must be re-indexed instead of resumed)."""
    try:
        get_envelope(name)
    except KeyError:
        return False
    return True


def envelope_or_plain(name: str) -> Envelope:
    """``get_envelope(name)``, or ``plain`` when no envelope has that name (rule hint of an empty file)."""
    try:
        return get_envelope(name)
    except KeyError:
        return PLAIN


def detect_envelope(head: bytes, *, max_lines: int = 50, fallbacks: bool = True) -> Envelope:
    """Pick the envelope of a file from its first bytes.

    Each registered envelope except ``plain`` counts how many of the first ``max_lines``
    lines it recognizes as headers; the highest count wins (ties go to the envelope
    registered first).

    Only when none of them recognizes a single line (the file would be ``plain``), and
    ``fallbacks`` is true, the fallback envelopes of families.toml get a turn: the one
    with most header lines wins (ties: declaration order), provided it passes its own
    threshold (``accepts``: a minimum of header lines and a minimum share of the non-blank
    sampled lines). Otherwise ``plain`` is returned.
    """
    lines = head.split(b"\n", max_lines)[:max_lines]
    best: Envelope = PLAIN
    best_count = 0
    for envelope in tuple(ENVELOPES.values()):
        if envelope.name == PLAIN.name:
            continue
        count = sum(1 for line in lines if envelope.match(line) is not None)
        if count > best_count:
            best, best_count = envelope, count
    if best_count or not fallbacks:
        return best
    _load_fallbacks()
    nonblank = sum(1 for line in lines if line.strip(b" \t\r\n" + BOM))
    for envelope in tuple(FALLBACK_ENVELOPES.values()):
        count = sum(1 for line in lines if envelope.match(line) is not None)
        accepts = getattr(envelope, "accepts", None)
        if count > best_count and (accepts is None or accepts(count, nonblank)):
            best, best_count = envelope, count
    return best
