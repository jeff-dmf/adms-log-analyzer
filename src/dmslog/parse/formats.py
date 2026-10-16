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
it must start with ``^`` and follow a restricted grammar checked on its text at load time.
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
from typing import Any, NoReturn

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
_ALL_GROUPS = frozenset(_TS_GROUPS) | _OPTIONAL_GROUPS
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
    return value if 0 <= value <= MAX_ID else None  # ids are never negative; SQLite integers are 64-bit


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
    if type(tid_base) is not int or tid_base not in (10, 16):
        raise EnvelopeConfigError(f"{where}: 'tid_base' must be 10 or 16")
    min_lines = table.get("min_lines", DEFAULT_MIN_LINES)
    if type(min_lines) is not int or not 1 <= min_lines <= 50:
        raise EnvelopeConfigError(f"{where}: 'min_lines' must be an integer from 1 to 50")
    min_ratio = table.get("min_ratio", DEFAULT_MIN_RATIO)
    if type(min_ratio) not in (int, float) or not 0 <= min_ratio <= 1:
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
    """Compile a header regex of the restricted grammar (see ``_check_grammar``) as a bytes regex."""
    if not isinstance(source, str) or not source:
        raise EnvelopeConfigError(f"{where}: 'header' must be a non-empty string")
    if not source.isascii():
        raise EnvelopeConfigError(f"{where}: 'header' must be ASCII")
    if not source.startswith("^"):
        raise EnvelopeConfigError(f"{where}: 'header' must start with '^'")
    if len(source) > MAX_HEADER_CHARS:
        raise EnvelopeConfigError(f"{where}: 'header' is longer than {MAX_HEADER_CHARS} characters")
    # The grammar is checked on the ORIGINAL text, before ``re`` compiles it: the ``re`` parser folds
    # and drops constructs (a one-byte alternation becomes a class, ``(?:...)`` is inlined, comments
    # vanish, a huge repeat bound overflows), so a check of its tree would accept things the grammar
    # does not. Only after the text is accepted (bounds <= MAX_REPEAT included) is it compiled.
    _check_grammar(source, where)
    # ``match(buf, pos, endpos)`` is already anchored at ``pos``; the leading ``^`` (the only anchor the
    # grammar allows) would only match at the real start of ``buf`` (never after a BOM or in the middle
    # of a chunk), so it is dropped.
    try:
        rx = re.compile(source[1:].encode("ascii"))
    except (re.error, RecursionError, OverflowError) as exc:  # e.g. a duplicate group name
        raise EnvelopeConfigError(f"{where}: 'header' does not compile as a bytes regex: {exc}") from None
    groups = set(rx.groupindex)
    missing = [g for g in _TS_GROUPS if g not in groups]
    if missing:
        raise EnvelopeConfigError(f"{where}: 'header' needs the named groups {missing}")
    extra = groups - set(_TS_GROUPS) - _OPTIONAL_GROUPS
    if extra:
        raise EnvelopeConfigError(f"{where}: unknown named groups {sorted(extra)}")
    return rx


# Restricted header grammar (ARQUITETURA 6.1a). Character sets are bitmasks over the 256 byte values,
# computed with the bytes semantics the matcher uses.
MAX_HEADER_CHARS = 1000
MAX_OPTIONAL_GROUPS = 4
MAX_GROUP_DEPTH = 2
MAX_REPEAT = 64
_ALL_BYTES = (1 << 256) - 1
_DIGITS = sum(1 << b for b in range(0x30, 0x3A))
_HEX = frozenset("0123456789abcdefABCDEF")
_GROUP_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,31}")
_REPEAT_RE = re.compile(r"\{([0-9]*)(,?)([0-9]*)\}")
_ESCAPES = {"t": 0x09}  # the only letter escapes that stand for one byte (plus \xHH)

# Parsed items: ("set", mask) one byte of the set; ("rep", lo, hi, mask) a repeat of one byte of the
# set; ("group", items) a group (named or not); ("opt", items) an optional group.
_Item = tuple[Any, ...]


class _Grammar(EnvelopeConfigError):
    """Internal: a grammar violation (re-raised with the envelope name)."""


def _parse_header(source: str) -> list[_Item]:
    r"""Parse the header text (without its leading ``^``) into items, or raise ``_Grammar``.

    A hand-written reader of the original text: every construct the grammar does not list is an error,
    whatever ``re`` would make of it.
    """
    text = source
    n = len(text)
    pos = 1  # after the leading "^" (the caller checked it)

    def fail(message: str) -> NoReturn:
        raise _Grammar(f"{message} (at offset {pos})")

    def escape() -> int:
        """Mask of the escape at ``pos`` (just after the backslash); advances ``pos``."""
        nonlocal pos
        if pos >= n:
            fail("a trailing backslash")
        ch = text[pos]
        pos += 1
        if ch == "d":
            return _DIGITS
        if ch == "x":
            hh = text[pos : pos + 2]
            if len(hh) != 2 or not set(hh) <= _HEX:
                fail("\\x needs two hex digits")
            pos += 2
            return 1 << int(hh, 16)
        if ch in _ESCAPES:
            return 1 << _ESCAPES[ch]
        if ch in "uUN":
            fail(f"'header' does not compile as a bytes regex: \\{ch} exists only in str patterns")
        if ch.isdigit():
            fail("back-references and octal escapes are not allowed; use \\xHH")
        if ch in "sSwWD":
            fail("only literals, ranges and \\d are allowed (no \\s, \\S, \\w, \\W, \\D)")
        if ch in "AZzbB":
            fail("only a leading '^' anchor is allowed")
        if ch.isalnum() or ch in "\n\r":
            fail(f"the escape \\{ch} is not allowed")
        return 1 << ord(ch)  # an escaped punctuation character or space is itself

    def char_class() -> int:
        """Mask of the class whose ``[`` was just read; advances ``pos`` past ``]``."""
        nonlocal pos
        if pos < n and text[pos] == "^":
            fail("negated characters are not allowed; list the allowed ones")
        mask = 0
        first = True
        while True:
            if pos >= n:
                fail("an unterminated character class")
            ch = text[pos]
            if ch == "]" and not first:
                pos += 1
                return mask
            first = False
            if ch == "[" or text.startswith(("--", "&&", "~~", "||"), pos):
                fail("nested sets and set operations are not allowed in a character class; escape them")
            pos += 1
            if ch == "\\":
                if text.startswith("d", pos):
                    pos += 1
                    mask |= _DIGITS
                    continue
                lo_mask = escape()
            else:
                lo_mask = 1 << ord(ch)
            if pos + 1 < n and text[pos] == "-" and text[pos + 1] != "]":
                pos += 1
                ch = text[pos]
                pos += 1
                if ch == "[":
                    fail("nested sets and set operations are not allowed in a character class; escape them")
                if ch == "\\":
                    if text.startswith("d", pos):
                        fail("\\d cannot end a range")
                    hi_mask = escape()
                else:
                    hi_mask = 1 << ord(ch)
                lo, hi = lo_mask.bit_length() - 1, hi_mask.bit_length() - 1
                if lo > hi:
                    fail("a range must go from low to high")
                mask |= ((1 << (hi + 1)) - 1) ^ ((1 << lo) - 1)
            else:
                mask |= lo_mask

    def repeat() -> tuple[int, int] | None:
        """The repeat at ``pos`` as (lo, hi), or None when none is there; advances ``pos``."""
        nonlocal pos
        if pos >= n:
            return None
        ch = text[pos]
        if ch in "*+":
            fail(f"a repeat bound must be from 1 to {MAX_REPEAT} (no '*' or '+')")
        if ch == "?":
            pos += 1
            bounds = (0, 1)
        elif ch == "{":
            m = _REPEAT_RE.match(text, pos)
            if m is None:
                fail("a literal '{' must be escaped as '\\{'")
            lo_text, comma, hi_text = m.groups()
            if not comma:
                hi_text = lo_text
            if not hi_text or len(lo_text) > 3 or len(hi_text) > 3:  # checked before int(): no overflow
                fail(f"a repeat bound must be from 1 to {MAX_REPEAT}")
            lo, hi = int(lo_text or "0"), int(hi_text)
            if not 1 <= hi <= MAX_REPEAT or lo > hi:
                fail(f"a repeat bound must be from 1 to {MAX_REPEAT}")
            pos = m.end()
            bounds = (lo, hi)
        else:
            return None
        if pos < n and text[pos] in "?+*{":
            fail("lazy, possessive and repeated repeats are not allowed")
        return bounds

    def sequence(depth: int) -> list[_Item]:
        nonlocal pos
        items: list[_Item] = []
        while pos < n:
            ch = text[pos]
            if ch == ")":
                if depth == 0:
                    fail("an unbalanced ')'")
                return items
            if ch in "*+?{":
                fail("a repeat with nothing to repeat")
            pos += 1
            if ch == "(":
                body = group(depth + 1)
                bounds = repeat()
                if bounds is None:
                    items.append(("group", body))
                elif bounds == (0, 1) and text[pos - 1] == "?":
                    items.append(("opt", body))
                else:
                    fail("a repeat applies to one character or class; the only repeat of a group is '?'")
                continue
            if ch == "^" or ch == "$":
                fail("only a leading '^' anchor is allowed")
            if ch == "|":
                fail("alternation is not allowed (a character class covers the level tokens)")
            if ch == ".":
                fail("'.' is not allowed; use a character class")
            if ch == "[":
                mask = char_class()
            elif ch == "\\":
                mask = escape()
            else:
                mask = 1 << ord(ch)
            bounds = repeat()
            items.append(("set", mask) if bounds is None else ("rep", *bounds, mask))
        if depth:
            fail("a missing ')'")
        return items

    def group(depth: int) -> list[_Item]:
        """The group whose ``(`` was just read; advances ``pos`` past ``)``."""
        nonlocal pos
        if depth > MAX_GROUP_DEPTH:
            fail(f"groups nested deeper than {MAX_GROUP_DEPTH} are not allowed")
        if not text.startswith("?", pos):
            fail("an unnamed capture group; use (?:...) and (?P<name>...)")
        if text.startswith("?:", pos):
            pos += 2
        elif text.startswith("?P<", pos):
            close = text.find(">", pos)
            if close < 0 or not _GROUP_NAME_RE.fullmatch(text, pos + 3, close):
                fail("an invalid group name")
            if text[pos + 3 : close] not in _ALL_GROUPS:
                fail(f"unknown named groups [{text[pos + 3 : close]!r}]")
            pos = close + 1
        elif text.startswith(("?=", "?!", "?<=", "?<!"), pos):
            fail("look-arounds are not allowed")
        elif text.startswith("?#", pos):
            fail("comments are not allowed")
        elif text.startswith("?P=", pos) or text.startswith("?(", pos):
            fail("back-references and conditionals are not allowed")
        else:
            fail("inline flags, atomic groups and other (?...) forms are not allowed")
        body = sequence(depth)
        if pos >= n:
            fail("a missing ')'")
        pos += 1  # the ")"
        if not body:
            fail("an empty group")
        return body

    return sequence(0)


def _check_grammar(source: str, where: str) -> None:
    r"""Accept only a small grammar whose every choice is decided by the next byte, else raise.

    Allowed: the leading ``^`` of the header and no other anchor; literal bytes (ASCII characters,
    escaped punctuation, ``\t``, ``\xHH``); character classes ``[...]`` of literals, ranges and ``\d``
    (exactly 0-9 in a bytes regex), never negated and without ``\s``/``\w``/``\S``/``\W``/``\D``/``.``;
    a greedy repeat ``{m,n}``/``{n}``/``?`` (n <= 64; no ``*``/``+``) of ONE such atom; groups
    ``(?P<name>...)`` and ``(?:...)``, non-empty, at most 2 deep, the only repeat of a group being ``?``
    (at most 4 optional groups, not nested, never able to match the empty string). No alternation,
    look-arounds, back-references, comments, inline flags. Checked on the text as written.

    Determinism (checked right to left, in one pass): a variable repeat must not be able to start what
    follows it, an optional group must not start like what follows it, and what follows the header is
    any byte, so neither may end the header. Then the one path the regex can take is fixed by bytes
    inside the match: appending bytes to a line never moves the end of the header nor changes a group.
    """
    optional_groups = 0

    def fail(text: str) -> NoReturn:
        raise EnvelopeConfigError(f"{where}: {text} in 'header'")

    def seq(items: list[_Item], follow: int, in_optional: bool) -> int:
        """First bytes of ``items`` followed by ``follow``; checks every choice on the way."""
        first = follow
        for item in reversed(items):
            first = node(item, first, in_optional)
        return first

    def node(item: _Item, follow: int, in_optional: bool) -> int:
        nonlocal optional_groups
        kind = item[0]
        if kind == "set":
            return item[1]
        if kind == "rep":
            _, lo, hi, mask = item
            if lo < hi and mask & follow:
                fail("a variable repeat may also match what follows it (or ends the header)")
            return mask | follow if lo == 0 else mask
        if kind == "group":
            return seq(item[1], follow, in_optional)
        body = item[1]  # "opt"
        if in_optional:
            fail("optional groups may not be nested")
        optional_groups += 1
        if optional_groups > MAX_OPTIONAL_GROUPS:
            fail(f"more than {MAX_OPTIONAL_GROUPS} optional groups")
        if nullable(body):
            fail("an optional group must not be able to match the empty string")
        inner = seq(body, 0, True)  # its own first bytes (not nullable: follow 0 adds none)
        if inner & follow:
            fail("an optional group may also match what follows it (or ends the header)")
        seq(body, follow, True)  # the choices inside, with the real follow
        return inner | follow

    def nullable(items: list[_Item]) -> bool:
        for item in items:
            kind = item[0]
            if kind == "set" or (kind == "rep" and item[1] > 0):
                return False
            if kind == "group" and not nullable(item[1]):
                return False
        return True

    try:
        items = _parse_header(source)
    except _Grammar as exc:
        fail(str(exc))
    seq(items, _ALL_BYTES, False)


def _compile_levels(raw: Any, where: str) -> dict[bytes, int]:
    if not isinstance(raw, dict):
        raise EnvelopeConfigError(f"{where}: 'levels' must be a table")
    merged = dict(DEFAULT_LEVELS)
    for token, target in raw.items():
        if not isinstance(token, str) or not _LEVEL_TOKEN_RE.fullmatch(token):
            raise EnvelopeConfigError(f"{where}: invalid level token {token!r}")
        if not isinstance(target, str) or target not in _LEVEL_NAMES:
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
