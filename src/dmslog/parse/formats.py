"""Generic line envelopes declared as data in ``families.toml`` (ARQUITETURA 6.1a, ROADMAP 9.14).

The built-in envelopes (``adms``, ``oaserr``) are code. Logs outside the 1.0 scope use other line
headers (installer and tool logs, .NET loggers); each such shape is one ``[[envelope]]`` table in
``families.toml``: a header regex with named groups for the timestamp, level, PID, TID and process,
plus examples that the loader checks. Nothing here is specific to a product.

Detection safety (see ``envelope.detect_envelope``): a generic envelope is a *fallback*. It is only
considered when no built-in envelope recognizes any line of the sample, so every file that was
detected as ``adms`` or ``oaserr`` before keeps exactly the same envelope; a generic envelope can
only take a file that would otherwise be ``plain``, and only with enough header lines.

Contract kept from ``envelope.Envelope``: the header decision depends only on a prefix of the line.
The regex is matched against the line without its end of line and at most ``HEADER_WINDOW`` bytes,
it must start with ``^``, and end anchors, look-arounds and ``.`` are rejected at load time.
"""

from __future__ import annotations

import math
import re
import threading
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

from .envelope import BOM, LEVELS, Header, parse_ts

FAMILIES_RESOURCE = "families.toml"
BUILTIN_NAMES = frozenset({"adms", "oaserr", "plain"})
HEADER_WINDOW = 512  # bytes of a line a generic header regex may look at
MAX_ENVELOPES = 32
DEFAULT_MIN_LINES = 2
DEFAULT_MIN_RATIO = 0.2
MAX_ID = (1 << 63) - 1  # SQLite integers are 64-bit

_ENVELOPE_KEYS = {"name", "description", "header", "tid_base", "levels", "min_lines", "min_ratio", "examples"}
_TS_GROUPS = ("year", "month", "day", "hour", "minute", "second")
_OPTIONAL_GROUPS = {"ms", "pid", "tid", "level", "process"}
_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_LEVEL_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}")
# Envelope level scale names (LEVELS) that a vendor level token may map to.
_LEVEL_NAMES = {key.decode("ascii"): value for key, value in LEVELS.items()}
# Default vendor level tokens (case-insensitive). DEBUG maps to DEBUGLOG, the level most ADMS lines
# use, so a .NET DEBUG line is ordinary output and not hidden as VERBOSE noise (ARQUITETURA 6.4).
DEFAULT_LEVELS: dict[str, str] = {
    "FATAL": "FATAL",
    "CRITICAL": "FATAL",
    "EXIT": "EXIT",
    "ERROR": "ERROR",
    "ERR": "ERROR",
    "ALWAYS": "ALWAYS",
    "DEBUGLOG": "DEBUGLOG",
    "DEBUG": "DEBUGLOG",
    "WARNING": "WARNING",
    "WARN": "WARNING",
    "INFO": "INFO",
    "INFORMATION": "INFO",
    "NOTICE": "INFO",
    "VERBOSE": "VERBOSE",
    "TRACE": "VERBOSE",
}


class EnvelopeConfigError(ValueError):
    """An ``[[envelope]]`` table of families.toml is invalid (``where`` names the table)."""


@dataclass(frozen=True)
class GenericEnvelope:
    """A line envelope compiled from one ``[[envelope]]`` table.

    Implements the ``Envelope`` protocol (``name``, ``match``, ``scan``) plus ``accepts``, the
    detection threshold. ``fallback_only`` marks it for ``detect_envelope``'s second pass.
    """

    name: str
    pattern: re.Pattern[bytes]
    levels: Mapping[bytes, int]
    tid_base: int
    min_lines: int
    min_ratio: float
    description: str = ""
    fallback_only: bool = True

    def match(self, line: bytes | bytearray) -> Header | None:
        end = len(line)
        if end and line[end - 1] == 0x0A:
            end -= 1
        if end and line[end - 1] == 0x0D:
            end -= 1
        return self._match_at(line, 0, end)

    def scan(self, buf: bytes | bytearray, pos: int, endpos: int) -> Iterator[tuple[int, Header]]:
        find = buf.find
        while pos < endpos:
            nl = find(b"\n", pos, endpos)
            nxt = endpos if nl < 0 else nl + 1
            end = nxt if nl < 0 else nl
            if end > pos and buf[end - 1] == 0x0D:
                end -= 1
            h = self._match_at(buf, pos, end)
            if h is not None:
                yield pos, h
            pos = nxt

    def _match_at(self, buf: bytes | bytearray, start: int, end: int) -> Header | None:
        """Header of the line ``buf[start:end]`` (end of line excluded), or None."""
        body = start + 3 if buf.startswith(BOM, start, end) else start
        m = self.pattern.match(buf, body, min(end, body + HEADER_WINDOW))
        if m is None or m.end() == body:
            return None
        g = m.groupdict()
        secs = m.group(*_TS_GROUPS)
        base = _SEC_CACHE.get(secs, _MISS)
        if base is _MISS:
            base = _second_ms(secs)
            if len(_SEC_CACHE) >= _SEC_CACHE_MAX:
                _SEC_CACHE.clear()
            _SEC_CACHE[secs] = base
        fraction = g.get("ms") or b"0"
        if base is None or not fraction.isdigit():
            return None  # impossible date: not a header (as in the built-in envelopes)
        level = g.get("level")
        process = g.get("process")
        return Header(
            base + int(fraction[:3].ljust(3, b"0")),
            _to_id(g.get("pid"), 10),
            _to_id(g.get("tid"), self.tid_base),
            None if level is None else self.levels.get(level.upper()),
            m.end() - start,
            None if not process else process.decode("utf-8", "replace"),
        )

    def accepts(self, hits: int, nonblank: int) -> bool:
        """Detection threshold: enough header lines among the ``nonblank`` sampled lines."""
        if hits <= 0:
            return False
        return hits >= min(self.min_lines, nonblank) and hits >= math.ceil(self.min_ratio * nonblank)

    def __repr__(self) -> str:
        return f"<envelope {self.name} (generic)>"


# (year, month, day, hour, minute, second) as captured -> ms of that second, None when impossible.
_SEC_CACHE: dict[tuple[bytes, ...], int | None] = {}
_SEC_CACHE_MAX = 100_000
_MISS = object()


def _second_ms(parts: tuple[bytes, ...]) -> int | None:
    """Milliseconds of a second (local log time encoded as UTC, as ``parse_ts``) or None."""
    year, *rest = (p or b"" for p in parts)
    if len(year) != 4 or not all(1 <= len(p) <= 2 for p in rest):
        return None
    sec = b"%s-%s-%s %s:%s:%s" % (year, *(p.rjust(2, b"0") for p in rest))
    if not sec.replace(b"-", b"").replace(b":", b"").replace(b" ", b"").isdigit():
        return None
    return parse_ts(sec, b"000")


def _to_id(raw: bytes | None, base: int) -> int | None:
    if not raw:
        return None
    try:
        value = int(raw, base)
    except ValueError:
        return None
    return value if value <= MAX_ID else None


# --------------------------------------------------------------------------- loading


def compile_envelopes(data: Mapping[str, Any]) -> list[GenericEnvelope]:
    """Compile the ``[[envelope]]`` tables of a parsed families.toml (none is fine)."""
    raw = data.get("envelope", [])
    if not isinstance(raw, list):
        raise EnvelopeConfigError("[[envelope]] must be an array of tables")
    if len(raw) > MAX_ENVELOPES:
        raise EnvelopeConfigError(f"more than {MAX_ENVELOPES} [[envelope]] tables")
    out: list[GenericEnvelope] = []
    seen: set[str] = set()
    for i, table in enumerate(raw):
        where = f"envelope #{i + 1}"
        if not isinstance(table, dict):
            raise EnvelopeConfigError(f"{where}: must be a table")
        env = _compile_one(table, where)
        if env.name in seen:
            raise EnvelopeConfigError(f"{where}: duplicate envelope name {env.name!r}")
        seen.add(env.name)
        out.append(env)
    return out


def _compile_one(table: Mapping[str, Any], where: str) -> GenericEnvelope:
    unknown = set(table) - _ENVELOPE_KEYS
    if unknown:
        raise EnvelopeConfigError(f"{where}: unknown keys {sorted(unknown)}")
    name = table.get("name")
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        raise EnvelopeConfigError(f"{where}: 'name' must be lowercase snake_case, up to 32 characters")
    if name in BUILTIN_NAMES:
        raise EnvelopeConfigError(f"{where}: {name!r} is a built-in envelope")
    where = f"envelope {name!r}"
    pattern = _compile_header(table.get("header"), where)
    tid_base = table.get("tid_base", 10)
    if tid_base not in (10, 16) or isinstance(tid_base, bool):
        raise EnvelopeConfigError(f"{where}: 'tid_base' must be 10 or 16")
    min_lines = table.get("min_lines", DEFAULT_MIN_LINES)
    if not isinstance(min_lines, int) or isinstance(min_lines, bool) or not 1 <= min_lines <= 50:
        raise EnvelopeConfigError(f"{where}: 'min_lines' must be an integer from 1 to 50")
    min_ratio = table.get("min_ratio", DEFAULT_MIN_RATIO)
    if not isinstance(min_ratio, (int, float)) or isinstance(min_ratio, bool) or not 0 <= min_ratio <= 1:
        raise EnvelopeConfigError(f"{where}: 'min_ratio' must be a number from 0 to 1")
    description = table.get("description", "")
    if not isinstance(description, str):
        raise EnvelopeConfigError(f"{where}: 'description' must be a string")
    env = GenericEnvelope(
        name=name,
        pattern=pattern,
        levels=_compile_levels(table.get("levels", {}), where),
        tid_base=tid_base,
        min_lines=min_lines,
        min_ratio=float(min_ratio),
        description=description,
    )
    _check_examples(env, table.get("examples"), where)
    return env


def _compile_header(source: Any, where: str) -> re.Pattern[bytes]:
    # Imported here: templates.py is large and only the loader needs its regex safety checks.
    from .templates import TemplateError, _compile_regex

    if not isinstance(source, str) or not source:
        raise EnvelopeConfigError(f"{where}: 'header' must be a non-empty string")
    if not source.isascii():
        raise EnvelopeConfigError(f"{where}: 'header' must be ASCII")
    if not source.startswith("^"):
        raise EnvelopeConfigError(f"{where}: 'header' must start with '^'")
    try:  # same structural and catastrophic-backtracking checks as the adapter templates
        rx, _info = _compile_regex(source, src="families.toml", where=where, window=HEADER_WINDOW)
    except TemplateError as exc:
        raise EnvelopeConfigError(f"{where}: {exc.detail}") from None
    _check_prefix_only(source, where)
    groups = set(rx.groupindex)
    missing = [g for g in _TS_GROUPS if g not in groups]
    if missing:
        raise EnvelopeConfigError(f"{where}: 'header' needs the named groups {missing}")
    extra = groups - set(_TS_GROUPS) - _OPTIONAL_GROUPS
    if extra:
        raise EnvelopeConfigError(f"{where}: unknown named groups {sorted(extra)}")
    # ``match(buf, pos, endpos)`` is already anchored at ``pos``; a literal ``^`` would only match at
    # the real start of ``buf`` (never after a BOM or in the middle of a chunk), so it is dropped.
    return re.compile(source[1:].encode("ascii"))


def _check_prefix_only(source: str, where: str) -> None:
    """Reject constructs that look past the header: ``$``/``\\Z``, look-arounds and ``.``."""
    from re import _constants as sre  # type: ignore[attr-defined]
    from re import _parser as sre_parser  # type: ignore[attr-defined]

    def walk(items: Any, top: bool) -> None:
        for index, (op, av) in enumerate(items):
            if op is sre.AT:
                if not (top and index == 0 and av is sre.AT_BEGINNING):
                    raise EnvelopeConfigError(f"{where}: only a leading '^' anchor is allowed in 'header'")
            elif op in (sre.ASSERT, sre.ASSERT_NOT):
                raise EnvelopeConfigError(f"{where}: look-arounds are not allowed in 'header'")
            elif op is sre.ANY:
                raise EnvelopeConfigError(f"{where}: '.' is not allowed in 'header'; use a character class")
            elif op is sre.SUBPATTERN:
                walk(av[-1], False)
            elif op in (sre.MAX_REPEAT, sre.MIN_REPEAT):
                walk(av[2], False)
            elif op is sre.BRANCH:
                for alt in av[1]:
                    walk(alt, False)

    walk(sre_parser.parse(source, 0).data, True)


def _compile_levels(raw: Any, where: str) -> dict[bytes, int]:
    if not isinstance(raw, dict):
        raise EnvelopeConfigError(f"{where}: 'levels' must be a table")
    merged = dict(DEFAULT_LEVELS)
    for token, target in raw.items():
        if not _LEVEL_TOKEN_RE.fullmatch(token):
            raise EnvelopeConfigError(f"{where}: invalid level token {token!r}")
        if target not in _LEVEL_NAMES:
            raise EnvelopeConfigError(f"{where}: level {token!r} must map to one of {sorted(_LEVEL_NAMES)}")
        merged[token.upper()] = target
    return {token.encode("ascii"): _LEVEL_NAMES[target] for token, target in merged.items()}


_PREFIX_PROBES = (b" tail", b" - [X] 9: tail", b" tail: more", b"\t0x1F end")


def _check_examples(env: GenericEnvelope, examples: Any, where: str) -> None:
    """Each example is a header line; appending text must not change its header (prefix contract)."""
    if not isinstance(examples, list) or not examples or not all(isinstance(e, str) for e in examples):
        raise EnvelopeConfigError(f"{where}: 'examples' must be a non-empty list of header lines")
    for example in examples:
        line = example.encode("utf-8")
        header = env.match(line)
        if header is None:
            raise EnvelopeConfigError(f"{where}: example is not recognized: {example[:80]!r}")
        for probe in _PREFIX_PROBES:
            if env.match(line + probe) != header:
                raise EnvelopeConfigError(
                    f"{where}: the header of an example changes when text is appended: {example[:80]!r}"
                )


# --------------------------------------------------------------------------- packaged set


@lru_cache(maxsize=1)
def packaged_envelopes() -> tuple[GenericEnvelope, ...]:
    """The generic envelopes of the packaged families.toml (works from a folder and from a zipapp)."""
    text = resources.files("dmslog").joinpath(FAMILIES_RESOURCE).read_text(encoding="utf-8-sig")
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise EnvelopeConfigError(f"invalid TOML: {exc}") from exc
    return tuple(compile_envelopes(data))


_LOCK = threading.Lock()
_LOADED = False


def ensure_registered() -> None:
    """Register the packaged generic envelopes as fallbacks, once (thread-safe)."""
    global _LOADED
    if _LOADED:
        return
    from .envelope import register_fallback_envelope

    with _LOCK:
        if _LOADED:
            return
        for env in packaged_envelopes():
            register_fallback_envelope(env)
        _LOADED = True
