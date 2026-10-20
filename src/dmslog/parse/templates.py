"""Declarative message templates per adapter family (ARQUITETURA 6.3).

One TOML file per family lives in ``dmslog/adapters/``; the schema is documented in
``dmslog/adapters/_schema.md``. This module loads and validates those files, compiles the
regular expressions, builds a dispatch table per (family, stream) and classifies records:

    catalog = load_catalog()                      # packaged files, via importlib.resources
    tset = catalog.for_family("AWFM", sensitive=False)
    tm = tset.classify(record)                    # TemplateMatch or None (never raises)

Templates look only at the first line of a record (``msg``, stripped). Named continuation
parsers (``cont``) extract fields from the continuation lines. Severity inference (6.4),
payload extraction (6.6) and the pipeline wiring are separate tasks: a match only carries
the template's hints (``sev_hint``, ``payload``, ``fts``, ``keys``).

Dispatch: exact texts are looked up in a dict first; regex templates are bucketed by the
first K characters of their literal prefix (K chosen per set), and templates without a
usable prefix are tried in every bucket, guarded by a required literal (``needle``). Within
the candidates the declaration order decides (family templates, then the libraries named in
``extends``); the first match wins.
"""

from __future__ import annotations

import bisect
import functools
import hashlib
import itertools
import math
import re
import sys
import tomllib
import warnings
from array import array
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from types import MappingProxyType
from typing import Any, NamedTuple, Protocol

try:  # regex syntax tree, used for the literal prefix and the backtracking checks
    from re import _constants as _sre  # type: ignore[attr-defined]
    from re import _parser as _sre_parser  # type: ignore[attr-defined]

    _HAVE_PARSER = True
except ImportError:  # pragma: no cover - every supported Python (3.11+) has it
    _HAVE_PARSER = False

SCHEMA_VERSION = 1
ADAPTERS_PACKAGE = "dmslog.adapters"
SKIPPED_FILES = frozenset({"_example.toml"})  # documentation, never loaded with the catalog
DEFAULT_LIBRARY = "_common"  # templates for families without a file of their own (UNKNOWN, ...)

CLASSES = ("start", "end", "req_resp", "validation", "error", "statistics", "startup", "lifecycle", "other")
STREAMS = ("any", "normal", "sensitive")
FIELD_TYPES = ("str", "int", "hex", "guid", "float", "list")
PAYLOAD_FORMATS = ("xml", "xml_seq", "html", "json", "text")
PAYLOAD_STARTS = ("match_end", "msg", "first_cont_lt")
PAYLOAD_ROLES = ("request", "response", "fault", "delta", "content", "publication", "other", "by_root")
DIRECTIONS = ("in", "out")
CODE_REF_KEYS = frozenset(
    {"start", "end", "end_ok", "end_fail", "fail", "normal", "sensitive", "codes", "a", "b"}
)

# Limits (see _schema.md, "Limits").
MAX_MATCH_CHARS = 4096  # largest (and default) match window: characters of the stripped msg matched
MIN_MATCH_WINDOW = 128  # smallest window a template may declare (``window = N``)
MAX_CONT_CHARS = 256 * 1024  # continuation parsers look at most at this many characters
MAX_REGEX_CHARS = 1000
MAX_GROUPS = 24  # capture groups per regex (the catalog needs 8; the unknown-signature sketches use up to 24)
MAX_GROUP_DEPTH = 3  # nesting depth of capture groups (the catalog needs 2)
MAX_CLASS_ITEMS = 32  # items of one character class: ranges, literals and categories (catalog: 4)
# Work budget of a regex (see _check_regex): its bound on the steps of one re.match, evaluated at
# the template's match window. Empirical calibration: the worst measured cost is about 19 ns per
# counted step (contrived shapes with big classes and deep captures, now rejected by the
# MAX_CLASS_ITEMS and MAX_GROUP_DEPTH limits), against 0.8 to 5 ns on the average fuzz and the
# catalog. Local empirical figure: accepted shapes at the budget take about 20-40 ms. Tests only
# assert a catastrophe threshold, tolerant to a runner several times slower.
MAX_REGEX_STEPS = 1 << 21
MAX_LIST_ITEMS = 64
MAX_INT_DIGITS = 18  # larger integers stay strings (SQLite integers are 64-bit)
MIN_NEEDLE = 4
MAX_DISPATCH_KEYS = 8192  # memoized dispatch keys per template set

_CODE_RE = re.compile(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*")
_FAMILY_RE = re.compile(r"_?[A-Za-z][A-Za-z0-9_]{0,31}")
_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_PEER_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}")


# --------------------------------------------------------------------------- errors


class TemplateError(ValueError):
    """Base class: an adapter TOML file is invalid. ``source`` is the file, ``where`` the item."""

    def __init__(self, message: str, *, source: str = "", where: str = "") -> None:
        self.source = source
        self.where = where
        self.detail = message
        prefix = ": ".join(p for p in (source, where) if p)
        super().__init__(f"{prefix}: {message}" if prefix else message)


class SchemaError(TemplateError):
    """Unknown key, wrong type, value outside the allowed set, missing required key."""


class DuplicateError(TemplateError):
    """A template code, alias, exact text or family name is defined twice."""


class InvalidRegexError(TemplateError):
    """A regex does not compile or breaks a structural rule (unnamed group, bad group name)."""


class UnsafeRegexError(InvalidRegexError):
    """A regex may backtrack catastrophically (nested or ambiguous repeats)."""


class TemplateReferenceError(TemplateError):
    """``extends`` or a rule references a family or template code that does not exist."""


class ExampleError(TemplateError):
    """A template does not classify its own examples as declared (raised by check_examples)."""


# --------------------------------------------------------------------------- definitions


@dataclass(frozen=True, slots=True)
class PayloadSpec:
    """Where the payload of a matched record is (ARQUITETURA 6.6)."""

    fmt: str  # xml | xml_seq | html | json | text
    start: str  # match_end | msg | first_cont_lt
    strip_suffix: str | None  # "." on request/response
    role: str  # request|response|fault|delta|content|publication|other|by_root
    direction: str | None = None  # None: from [payload_defaults] of the family
    peer: str | None = None


@dataclass(frozen=True, slots=True)
class KeySpec:
    """A field copied to entry_key: ``type`` is the key type (msgid, gid, request_id, ...)."""

    field: str
    type: str


@dataclass(frozen=True, slots=True)
class ContSpec:
    """A named continuation parser and its options."""

    parser: str
    options: Mapping[str, Any]
    outputs: frozenset[str]
    regex: re.Pattern[str] | None = None  # line_regex only


@dataclass(frozen=True, slots=True)
class Example:
    msg: str
    cont: str | None = None
    fields: Mapping[str, Any] | None = None  # expected subset of the extracted fields


@dataclass(frozen=True, slots=True)
class TemplateDef:
    code: str
    family: str
    cls: str
    sev: int | None
    regex: re.Pattern[str] | None  # exactly one of regex / text
    text: str | None
    keys: tuple[KeySpec, ...]
    refs_sensitive: bool
    literal_prefix: str | None
    fts: bool = True
    payload: PayloadSpec | None = None
    title: str | None = None
    aliases: tuple[str, ...] = ()
    stream: str = "any"
    cont: ContSpec | None = None
    field_types: Mapping[str, str] = field(default_factory=dict)
    sev_field: str | None = None
    sev_map: Mapping[str, int] | None = None
    needle: str | None = None  # required literal, checked with ``in`` before the regex
    alt_prefixes: tuple[str, ...] = ()  # dispatch prefixes (one per leading alternative)
    can_start: Callable[[str], bool] | None = field(default=None, compare=False, repr=False)
    optional_groups: bool = True
    window: int = MAX_MATCH_CHARS  # the template applies only to stripped messages of at most this length
    examples: tuple[Example, ...] = ()
    counter_examples: tuple[Example, ...] = ()
    source: str = ""
    def_version: str = ""

    @property
    def pattern(self) -> str:
        """Text stored in template.pattern."""
        if self.regex is not None:
            return self.regex.pattern
        return self.text or ""

    @property
    def field_names(self) -> frozenset[str]:
        names = set(self.regex.groupindex) if self.regex is not None else set()
        if self.cont is not None:
            names |= self.cont.outputs
        return frozenset(names)


@dataclass(frozen=True, slots=True)
class FamilyDef:
    """One parsed TOML file."""

    family: str
    source: str
    def_version: str  # sha1 (12 hex) of the file bytes
    description: str | None
    extends: tuple[str, ...]
    templates: tuple[TemplateDef, ...]
    payload_defaults: Mapping[str, Mapping[str, str]]
    operations: tuple[Mapping[str, Any], ...]
    sensitive_pairs: tuple[Mapping[str, Any], ...]
    link_rules: tuple[Mapping[str, Any], ...] = ()

    @property
    def is_library(self) -> bool:
        return self.family.startswith("_")


@dataclass(slots=True)
class TemplateMatch:
    """Result of classify(). ``match_end`` is an offset in the original (unstripped) msg."""

    tpl_id: int
    code: str
    cls: str
    sev_hint: int | None
    fts: bool
    payload: PayloadSpec | None
    fields: dict[str, Any]
    keys: list[tuple[str, str]]  # (key_type, value) for entry_key
    refs_sensitive: bool
    match_end: int


class _HasMsg(Protocol):
    msg: str
    cont: str | None


# --------------------------------------------------------------------------- field types


def _to_int(v: str) -> Any:
    if len(v) > MAX_INT_DIGITS:
        return v  # SQLite integers are 64-bit: keep long numbers as text
    try:
        return int(v)
    except ValueError:
        return v


def _to_hex(v: str) -> Any:
    s = v.strip()
    if s[:2] in ("0x", "0X"):
        s = s[2:]
    if not s or len(s) > 16:
        return v
    try:
        n = int(s, 16)
    except ValueError:
        return v
    # A 16-digit GID may not fit SQLite's signed 64-bit integer: it becomes its decimal text (the form every
    # other source of the same GID uses: payloads and the integration logs), never a hexadecimal text.
    return n if n <= 0x7FFFFFFFFFFFFFFF else str(n)


def _to_float(v: str) -> Any:
    try:
        f = float(v)
    except ValueError:
        return v
    return f if math.isfinite(f) else v


def _to_list(v: str) -> Any:
    items = []
    for part in v.split(","):
        part = part.strip().strip("\"'").strip()
        if part:
            items.append(part)
            if len(items) >= MAX_LIST_ITEMS:
                break
    return items


_CONVERTERS: dict[str, Callable[[str], Any]] = {
    "int": _to_int,
    "hex": _to_hex,
    "guid": str.lower,
    "float": _to_float,
    "list": _to_list,
}


# --------------------------------------------------------------------------- continuation parsers

_STACK_EXC = re.compile(
    r"(?:Stack ?trace: |Reason: )?\s*(?P<type>[A-Za-z_][\w.`]*(?:Exception|Error))(?::\s?(?P<msg>.*))?$"
)
_STACK_FRAME = re.compile(r"\s*at (?P<frame>[^\s(]+)")
_STACK_BUILD = re.compile(r"\\PD\\(?P<build>\d+)\\")
_STACK_INNER = re.compile(
    r"(?:Inner Exception(?: #\d+\))?|--->)[\s(]*(?:Inner Exception #\d+\)\s*)?"
    r"(?P<type>[A-Za-z_][\w.`]*(?:Exception|Error))"
)
_CODE_LINE = re.compile(r"[A-Za-z][\w.-]{0,127}")
_CSV_ROW = re.compile(r"([^,\s][^,]*),(\d{1,19}),(\d{1,19}),(\d{1,19})")
_KV_COLON = re.compile(r"\s*([A-Za-z][\w .()/-]{0,63}?):(?: (.*))?")
_KV_DASH = re.compile(r"\s*([A-Za-z][\w .()/]{0,63}?) - (.*)")


def _cont_lines(cont: str, max_lines: int) -> list[str]:
    """Complete lines of the first MAX_CONT_CHARS characters: a line the cap cuts is dropped."""
    text = cont[:MAX_CONT_CHARS]
    if len(cont) > MAX_CONT_CHARS and cont[MAX_CONT_CHARS] != "\n":
        cut = text.rfind("\n")  # the last line is incomplete: no parser sees a partial line
        if cut < 0:
            return []  # a single line longer than the cap: nothing complete to read (not an empty line)
        text = text[:cut]
    return text.split("\n", max_lines)[:max_lines]


def _p_codes_per_line(cont: str, opts: Mapping[str, Any], spec: ContSpec) -> dict[str, Any]:
    codes = []
    for line in _cont_lines(cont, int(opts.get("max_lines", 64))):
        s = line.strip()
        if s and _CODE_LINE.fullmatch(s):
            codes.append(s)
            if len(codes) >= int(opts.get("max_items", 32)):
                break
    return {"codes": codes} if codes else {}


def _p_stats_csv(cont: str, opts: Mapping[str, Any], spec: ContSpec) -> dict[str, Any]:
    rows = []
    n = 0
    lines = _cont_lines(cont, min(int(opts.get("max_rows", MAX_CONT_CHARS)), MAX_CONT_CHARS) + 8)
    # No row cap by default: the 256 KiB continuation cap bounds the work and the rows kept (a line is
    # at least 2 characters, so MAX_CONT_CHARS also bounds the number of lines).
    limit = int(opts.get("max_rows", MAX_CONT_CHARS))
    for line in lines:
        m = _CSV_ROW.fullmatch(line.strip())
        if m:
            n += 1
            if len(rows) < limit:
                rows.append([m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))])
    if not n:
        if len(cont) > MAX_CONT_CHARS:  # no row read before the cap (a line above it, maybe after a header)
            return {"rows": [], "n_rows": 0, "rows_cut": True}
        return {}
    out: dict[str, Any] = {"rows": rows, "n_rows": n}
    if len(cont) > MAX_CONT_CHARS or n > len(rows):
        out["rows_cut"] = True  # the continuation cap or ``max_rows`` left rows out: never silent
    return out


def _p_dotnet_stack(cont: str, opts: Mapping[str, Any], spec: ContSpec) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for line in _cont_lines(cont, int(opts.get("max_lines", 400))):
        s = line.strip()
        if not s:
            continue
        if "exc_type" not in out and not s.startswith("at "):
            m = _STACK_EXC.match(s)
            if m and " " not in m.group("type"):
                out["exc_type"] = m.group("type")
                if m.group("msg"):
                    out["exc_msg"] = m.group("msg")[:512]
        if "frame" not in out:
            m = _STACK_FRAME.match(line)
            if m and line.lstrip().startswith("at "):
                out["frame"] = m.group("frame")[:256]
        if "build" not in out and "\\PD\\" in line:
            m = _STACK_BUILD.search(line)
            if m:
                out["build"] = m.group("build")
        if "inner_type" not in out and ("Inner Exception" in s or "--->" in s):
            m = _STACK_INNER.search(s)
            if m:
                out["inner_type"] = m.group("type")
    return out


def _p_kv_tab(cont: str, opts: Mapping[str, Any], spec: ContSpec) -> dict[str, Any]:
    # Map dumps: counts items, never keeps the pairs (ARQUITETURA 6.3).
    n = cont.count("\n\t") + (1 if cont.startswith("\t") else 0)
    return {"items": n}


def _kv(cont: str, opts: Mapping[str, Any], rx: re.Pattern[str]) -> dict[str, Any]:
    kv: dict[str, str] = {}
    limit = int(opts.get("max_items", 32))
    for line in _cont_lines(cont, limit * 4 + 8):
        if line.lstrip().startswith("<"):
            break  # payload starts here (SMN-S110)
        m = rx.fullmatch(line)
        if m:
            key = m.group(1).strip()
            if key and key not in kv:
                kv[key] = (m.group(2) or "").strip()[:512]
                if len(kv) >= limit:
                    break
    return {"kv": kv, "n_items": len(kv)} if kv else {}


def _p_kv_colon(cont: str, opts: Mapping[str, Any], spec: ContSpec) -> dict[str, Any]:
    return _kv(cont, opts, _KV_COLON)


def _p_kv_dash(cont: str, opts: Mapping[str, Any], spec: ContSpec) -> dict[str, Any]:
    return _kv(cont, opts, _KV_DASH)


def _p_line_regex(cont: str, opts: Mapping[str, Any], spec: ContSpec) -> dict[str, Any]:
    assert spec.regex is not None
    lines = _cont_lines(cont, int(opts.get("line", 1)))
    idx = int(opts.get("line", 1)) - 1
    if idx >= len(lines):
        return {}
    line = lines[idx].strip()  # split lines: no line break
    if len(line) > MAX_MATCH_CHARS:  # a line that does not fit: no match (as for templates)
        return {}
    m = spec.regex.match(line)
    if m is None:
        return {}
    return {k: v for k, v in m.groupdict().items() if v is not None}


@dataclass(frozen=True, slots=True)
class _ContParser:
    fn: Callable[[str, Mapping[str, Any], ContSpec], dict[str, Any]]
    options: Mapping[str, type]
    outputs: frozenset[str]


CONT_PARSERS: Mapping[str, _ContParser] = MappingProxyType(
    {
        "codes_per_line": _ContParser(
            _p_codes_per_line, {"max_items": int, "max_lines": int}, frozenset({"codes"})
        ),
        "stats_csv": _ContParser(_p_stats_csv, {"max_rows": int}, frozenset({"rows", "n_rows", "rows_cut"})),
        "dotnet_stack": _ContParser(
            _p_dotnet_stack,
            {"max_lines": int},
            frozenset({"exc_type", "exc_msg", "frame", "build", "inner_type"}),
        ),
        "kv_tab": _ContParser(_p_kv_tab, {}, frozenset({"items"})),
        "kv_colon": _ContParser(_p_kv_colon, {"max_items": int}, frozenset({"kv", "n_items"})),
        "kv_dash": _ContParser(_p_kv_dash, {"max_items": int}, frozenset({"kv", "n_items"})),
        "line_regex": _ContParser(_p_line_regex, {"regex": str, "line": int}, frozenset()),
    }
)
_OPTION_RANGES = {
    "max_items": (1, 256),
    "max_lines": (1, 5000),
    "max_rows": (1, MAX_CONT_CHARS),
    "line": (1, 20),
}


# --------------------------------------------------------------------------- regex analysis


@dataclass(frozen=True, slots=True)
class _RegexInfo:
    prefix: str  # literal prefix common to every match ("" when none)
    needle: str | None  # longest other literal every match contains
    alts: tuple[str, ...] = ()  # literal prefixes of a leading alternation, one per alternative
    starts: Callable[[str], bool] | None = None  # can a match start with this character?
    optional_groups: bool = True  # some named group may not participate (value None)


def _compile_regex(
    source: str, *, src: str, where: str, window: int = MAX_MATCH_CHARS
) -> tuple[re.Pattern[str], _RegexInfo]:
    """Compile and check one template regex for texts of at most ``window`` characters; return it
    with its literal prefix and needle."""
    if not isinstance(source, str) or not source:
        raise SchemaError("regex must be a non-empty string", source=src, where=where)
    if len(source) > MAX_REGEX_CHARS:
        raise InvalidRegexError(f"regex longer than {MAX_REGEX_CHARS} characters", source=src, where=where)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # e.g. "possible nested set": ambiguous, rejected
            rx = re.compile(source)
    except (re.error, Warning) as exc:
        raise InvalidRegexError(f"regex does not compile: {exc}", source=src, where=where) from None
    if rx.groups != len(rx.groupindex):
        raise InvalidRegexError(
            "unnamed capture group; use (?:...) for grouping and (?P<name>...) for fields",
            source=src,
            where=where,
        )
    if rx.groups > MAX_GROUPS:
        raise InvalidRegexError(f"more than {MAX_GROUPS} named groups", source=src, where=where)
    for name in rx.groupindex:
        if not _NAME_RE.fullmatch(name):
            raise InvalidRegexError(
                f"group name {name!r} must be lowercase snake_case (a-z, 0-9, _), up to 32 characters",
                source=src,
                where=where,
            )
    if not _HAVE_PARSER:  # pragma: no cover
        return rx, _RegexInfo("", None)
    tree = _sre_parser.parse(source, 0)
    try:
        if tree.state.flags & ~re.UNICODE:
            raise _Unsafe("inline flags are not allowed")
        _check_regex(tree.data, window)
    except _Unsafe as exc:
        raise UnsafeRegexError(str(exc), source=src, where=where) from None
    info = _literals(tree.data)
    alts = tuple(dict.fromkeys(_leading(tree.data)))
    data = tree.data
    optional = _has_optional_group(data, False)
    return rx, _RegexInfo(
        info.prefix,
        info.needle,
        alts,
        lambda ch: _first_can(data, ord(ch)),
        optional,
    )


def _has_optional_group(items: list, optional: bool) -> bool:
    """Whether a capture group sits inside an alternation or an optional repeat."""
    for op, av in items:
        if op is _sre.SUBPATTERN:
            if optional and av[0] is not None:
                return True
            if _has_optional_group(av[3], optional):
                return True
        elif op in _REPEATS:
            if _has_optional_group(av[2], optional or av[0] == 0):
                return True
        elif op is _sre.BRANCH:
            if any(_has_optional_group(b, True) for b in av[1]):
                return True
        elif op in (_sre.ASSERT, _sre.ASSERT_NOT):
            if _has_optional_group(av[1], True):
                return True
    return False


class _Unsafe(Exception):
    pass


if _HAVE_PARSER:
    _REPEATS = frozenset({_sre.MAX_REPEAT, _sre.MIN_REPEAT})  # greedy and lazy; possessive is rejected
    _ENDS = frozenset({_sre.AT_END, _sre.AT_END_STRING})
    _CHECKS = frozenset({_sre.AT_BEGINNING, _sre.AT_BEGINNING_STRING, _sre.AT_BOUNDARY, _sre.AT_NON_BOUNDARY})
_MAX_BOUND = 64  # largest finite repeat bound ({n}, {m,n})

# Exact sets of code points: sorted, disjoint, non-adjacent inclusive ranges. _EOS stands for the end
# of the text, so that "$" can take part in the same overlap test as a character.
CharSet = tuple[tuple[int, int], ...]
_MAX_CP = 0x10FFFF
_EOS = _MAX_CP + 1
_EMPTY: CharSet = ()
_NEWLINE: CharSet = ((10, 10),)


def _cs(ranges: Iterable[tuple[int, int]]) -> CharSet:
    out: list[tuple[int, int]] = []
    for lo, hi in sorted(ranges):
        if out and lo <= out[-1][1] + 1:
            if hi > out[-1][1]:
                out[-1] = (out[-1][0], hi)
        else:
            out.append((lo, hi))
    return tuple(out)


def _cs_invert(a: CharSet) -> CharSet:
    """Complement among the code points (never contains _EOS)."""
    out, nxt = [], 0
    for lo, hi in a:
        if lo > nxt:
            out.append((nxt, lo - 1))
        nxt = hi + 1
    if nxt <= _MAX_CP:
        out.append((nxt, _MAX_CP))
    return tuple(out)


def _cs_overlap(a: CharSet, b: CharSet) -> bool:
    i = j = 0
    while i < len(a) and j < len(b):
        if a[i][1] < b[j][0]:
            i += 1
        elif b[j][1] < a[i][0]:
            j += 1
        else:
            return True
    return False


@functools.cache
def _categories() -> dict[str, CharSet]:
    """\\d, \\w, \\s and their negations as exact sets, computed with the running ``re`` itself."""
    code = next(t for t in "IL" if array(t).itemsize == 4)
    every = array(code, range(_MAX_CP + 1)).tobytes().decode(f"utf-32-{sys.byteorder[0]}e", "surrogatepass")
    out: dict[str, CharSet] = {}
    for name, esc in (("DIGIT", r"\d+"), ("WORD", r"\w+"), ("SPACE", r"\s+")):
        pos = tuple((m.start(), m.end() - 1) for m in re.finditer(esc, every))
        out["CATEGORY_" + name] = pos
        out["CATEGORY_NOT_" + name] = _cs_invert(pos)
    return out


def _class_set(items: list) -> CharSet:
    """Exact set of a character class ([...], \\d, ...). Unknown items are rejected."""
    ranges: list[tuple[int, int]] = []
    negate = False
    for op, av in items:
        if op is _sre.NEGATE:
            negate = True
        elif op is _sre.LITERAL:
            ranges.append((av, av))
        elif op is _sre.RANGE:
            ranges.append(av)
        elif op is _sre.CATEGORY and str(av) in _categories():
            ranges.extend(_categories()[str(av)])
        else:
            raise _Unsafe(f"unsupported character class item {op}")
    s = _cs(ranges)
    return _cs_invert(s) if negate else s


def _atom_set(op: Any, av: Any) -> CharSet | None:
    """Exact set of a one-character token, or None when ``op`` is not one."""
    if op is _sre.LITERAL:
        return ((av, av),)
    if op is _sre.NOT_LITERAL:
        return _cs_invert(((av, av),))
    if op is _sre.ANY:
        return _cs_invert(_NEWLINE)
    if op is _sre.IN:
        return _class_set(av)
    return None


# Bound on the work of a (sub)pattern, as the coefficients of a polynomial in N = n + 1, n being
# the text length: (3, 2) is 3 + 2 * N steps.
_Cost = tuple[int, ...]
_ONE: _Cost = (1,)
_N: _Cost = (0, 1)
_ALL: CharSet = ((0, _EOS),)  # every character and the end of the text


def _mul(a: _Cost, b: _Cost) -> _Cost:
    out = [0] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        for j, y in enumerate(b):
            out[i + j] += x * y
    return tuple(out)


def _add(a: _Cost, b: _Cost) -> _Cost:
    return tuple(x + y for x, y in itertools.zip_longest(a, b, fillvalue=0))


def _max(a: _Cost, b: _Cost) -> _Cost:
    return tuple(max(x, y) for x, y in itertools.zip_longest(a, b, fillvalue=0))


def _at(cost: _Cost, n: int) -> int:
    """Value of a cost polynomial for a text of ``n`` characters."""
    return sum(c * (n + 1) ** d for d, c in enumerate(cost))


def _check_limits(items: list, depth: int) -> None:
    """Whitelist limits on constructs whose constant factors the cost model does not count:
    character class size and capture group nesting."""
    for op, av in items:
        if op is _sre.IN:
            n = sum(1 for o, _ in av if o is not _sre.NEGATE)
            if n > MAX_CLASS_ITEMS:
                raise _Unsafe(f"character class with {n} items (limit {MAX_CLASS_ITEMS})")
        elif op is _sre.SUBPATTERN:
            d = depth + (av[0] is not None)
            if d > MAX_GROUP_DEPTH:
                raise _Unsafe(f"capture groups nested deeper than {MAX_GROUP_DEPTH}")
            _check_limits(av[3], d)
        elif op in _REPEATS:
            _check_limits(av[2], depth)
        elif op is _sre.BRANCH:
            for b in av[1]:
                _check_limits(b, depth)
        elif op in (_sre.ASSERT, _sre.ASSERT_NOT):
            _check_limits(av[1], depth)


def _check_regex(items: list, window: int) -> _Cost:
    """Bound the work of a parsed regex on a text of ``window`` characters; raise _Unsafe when
    the bound exceeds MAX_REGEX_STEPS. Returns the bound as a polynomial in N = n + 1.

    Rules (ARQUITETURA 6.3, _schema.md "Regex safety"):

    1. Whitelist: literals, ``.``, classes, anchors, groups without flags, alternation, greedy or
       lazy repeats with bounds up to 64 (or unbounded), lookarounds. Anything else is rejected.
    2. A repeat that can iterate more than once repeats one-character tokens only.
    3. The work bound below, evaluated at the window, stays within MAX_REGEX_STEPS.

    Work model. The text is one line (no line break) matched with ``re.match``. Every part P,
    entered at one position with a known rest R after it, gets these bounds:

    - ``work``: steps spent inside P, including every re-entry when R fails and backtracks into P;
    - ``out``: exits of P (ways P can end) whose next character R can start with. The other exits
      are rejected by R's first character test in one step, which ``work`` already pays for,
      because P spends at least one step per exit;
    - ``once``: steps inside a rest that cannot fail: its first entry ends the match (or the
      lookaround body) successfully, so it is paid once, however many times the parts before it
      could have reached it.

    In sequence, ``work(PR) = work(P) + out(P) * work(R)`` and ``out(PR) = out(P) * out(R)``.
    Alternatives always add their work (each one may be tried); their ``out`` is the largest one
    when no two can start with the same character, else the sum. A repeat of one-character tokens
    reads at most N characters and has at most N counts (``hi - lo + 1`` when bounded); its ``out``
    is 1 when R cannot start with a character it repeats. A lookaround costs its body's whole work
    on every visit and, since it runs before any character test, it counts as able to start with
    any character: a choice point before it (in sequence or as alternatives) is never
    discriminated, so the lookaround is paid on every exit that reaches it.
    """
    _check_limits(items, 0)
    info = _seq(items, _PATTERN_END)
    work = _add(info.work, info.once)
    while len(work) > 1 and not work[-1]:
        work = work[:-1]
    steps = _at(work, window)
    if steps > MAX_REGEX_STEPS:
        raise _Unsafe(
            f"too much possible backtracking ({steps} steps on a {window}-character line > "
            f"{MAX_REGEX_STEPS}): repeats, optionals, alternatives or lookarounds that can consume "
            "the same text; make them start with distinct characters or separate them with a "
            'literal they cannot match (for example [^"]* then "), or declare a smaller window'
        )
    return work


class _Follow(NamedTuple):
    """What comes after a point of the regex, up to the end of the pattern (or of the
    lookaround body being analysed)."""

    first: CharSet  # characters the rest can start with (_EOS for "$"; _ALL for a lookaround)
    free: bool  # the rest cannot fail: the first time it is entered, the (body) match succeeds
    end_ok: bool  # the rest succeeds when it is entered at the end of the text


_PATTERN_END = _Follow(_EMPTY, True, True)


class _Info(NamedTuple):
    """What a part of the regex can do, entered at one position with a given rest after it."""

    first: CharSet  # characters it can start with (_ALL when it can run a lookaround first)
    nullable: bool  # can match the empty string
    free: bool  # can match the empty string with no anchor or lookaround that may fail
    width: tuple[int, int | None]  # shortest and longest match (None: unbounded)
    out: _Cost  # exits that the rest can accept (see _check_regex)
    work: _Cost  # steps per visit, re-entries included
    once: _Cost  # steps paid at most once per match: inside a rest that cannot fail


_ZERO: _Cost = (0,)


def _to_the_end(op: Any, av: Any) -> bool:
    """``.*`` and the like: a greedy unbounded repeat, possibly empty, of one token that accepts
    every character a matched text can hold (all but the line break: the text is a single line).
    Its first exit is at the end of the text. (A lazy one tries the rest at every count first.)"""
    if op is not _sre.MAX_REPEAT or av[0] != 0 or av[1] != _sre.MAXREPEAT:
        return False
    atoms = _flat_atoms(av[2])
    return len(atoms) == 1 and _cs_invert(atoms[0]) in (_EMPTY, _NEWLINE)


def _seq(items: list, follow: _Follow) -> _Info:
    infos: list[_Info] = []
    k0 = len(items)  # first part from which the rest cannot fail
    for k in range(len(items) - 1, -1, -1):
        op, av = items[k]
        i = _node(op, av, follow)
        infos.append(i)
        # This part and the rest cannot fail when: the rest cannot fail and this part can match
        # the empty string unconditionally; or this part is a greedy ".*" and the rest succeeds
        # at the end of the text, where ".*" first exits whatever the text is.
        sure = (follow.free and i.free) or (follow.end_ok and _to_the_end(op, av))
        if sure:
            k0 = k
        end_ok = follow.end_ok and (i.free or (op is _sre.AT and av in _ENDS))
        follow = _Follow(_cs((*i.first, *follow.first)) if i.nullable else i.first, sure, end_ok)
    infos.reverse()
    first, nullable, free, lo, hi = _EMPTY, True, True, 0, 0
    out, work, once = _ONE, _ONE, _ZERO
    tail_out, tail_work = _ONE, _ZERO  # the parts from k0 on: entered at most once per match
    for k, i in enumerate(infos):
        once = _add(once, i.once)
        if k < k0:
            work = _add(work, _mul(out, i.work))
            out = _mul(out, i.out)
        else:
            tail_work = _add(tail_work, _mul(tail_out, i.work))
            tail_out = _mul(tail_out, i.out)
        if nullable:
            first = _cs((*first, *i.first))
        nullable, free = nullable and i.nullable, free and i.free
        wlo, whi = i.width
        lo, hi = lo + wlo, None if hi is None or whi is None else hi + whi
    if k0 < len(infos):
        once, out = _add(once, tail_work), tail_out
    return _Info(first, nullable, free, (lo, hi), out, work, once)


def _node(op: Any, av: Any, follow: _Follow) -> _Info:
    s = _atom_set(op, av)
    if s is not None:
        return _Info(s, False, False, (1, 1), _ONE, _ONE, _ZERO)
    if op is _sre.AT:
        if av in _ENDS:  # the matched text is stripped: "$" only matches at its very end
            return _Info(((_EOS, _EOS),), False, False, (0, 0), _ONE, _ONE, _ZERO)
        if av in _CHECKS:  # zero-width checks of one step: transparent, but they can fail
            return _Info(_EMPTY, True, False, (0, 0), _ONE, _ONE, _ZERO)
    elif op is _sre.SUBPATTERN:
        if av[1] or av[2]:
            raise _Unsafe("inline flags are not allowed")
        return _seq(av[3], follow)
    elif op is _sre.BRANCH:
        parts = [_seq(b, follow) for b in av[1]]
        starts = [_cs((*p.first, *follow.first)) if p.nullable else p.first for p in parts]
        ambiguous = any(_cs_overlap(x, y) for i, x in enumerate(starts) for y in starts[i + 1 :])
        out = functools.reduce(_add if ambiguous else _max, (p.out for p in parts))
        work = functools.reduce(_add, (p.work for p in parts))
        once = functools.reduce(_add, (p.once for p in parts))
        his = [p.width[1] for p in parts]
        width = (min(p.width[0] for p in parts), None if None in his else max(his))  # type: ignore[type-var]
        first = _cs(r for p in parts for r in p.first)
        nullable, free = any(p.nullable for p in parts), any(p.free for p in parts)
        return _Info(first, nullable, free, width, out, work, once)
    elif op in (_sre.ASSERT, _sre.ASSERT_NOT):
        # The body runs to its first match (or failure) on every visit; it runs before any
        # character test of what follows, so nothing before it is discriminated.
        body = _seq(av[1], _PATTERN_END)
        return _Info(_ALL, True, False, (0, 0), _ONE, _add(_add(body.work, body.once), _ONE), _ZERO)
    elif op in _REPEATS:
        return _repeat(*av, follow)
    elif op is getattr(_sre, "POSSESSIVE_REPEAT", None):
        raise _Unsafe("possessive repeats are not allowed")
    elif op is getattr(_sre, "ATOMIC_GROUP", None):
        raise _Unsafe("atomic groups are not allowed")
    elif op in (_sre.GROUPREF, _sre.GROUPREF_EXISTS):
        raise _Unsafe("backreferences and conditional groups are not allowed")
    raise _Unsafe(f"unsupported regex construct {op}")


def _repeat(lo: int, hi: int, body: list, follow: _Follow) -> _Info:
    unbounded = hi == _sre.MAXREPEAT
    if lo > _MAX_BOUND or (hi > _MAX_BOUND and not unbounded):
        raise _Unsafe(f"repeat bound above {_MAX_BOUND}")
    if hi <= 1:  # x? or x{1}: the body is visited at most once and may hold anything
        i = _seq(body, follow)
        if lo:
            return i
        take = _cs((*i.first, *follow.first)) if i.nullable else i.first
        out = _add(i.out, _ONE) if _cs_overlap(take, follow.first) else i.out
        return _Info(i.first, True, True, (0, i.width[1] if hi else 0), out, _add(i.work, _ONE), i.once)
    atoms = _flat_atoms(body)  # rule 2: one-character tokens only
    s, k = atoms[0], len(atoms)  # an iteration that is not the last one is followed by a char of s
    counts = _N if unbounded else (hi - lo + 1,)
    # Reading: at most N characters (k * hi when bounded); backtracking: one step per count.
    work = _add((k, 1) if unbounded else (k * hi,), counts)
    out = _ONE
    if lo < hi and not follow.free and _cs_overlap(s, follow.first):  # free: the first count succeeds
        out = counts
    width = (lo * k, None if unbounded else hi * k)
    return _Info(s, lo == 0, lo == 0, width, out, work, _ZERO)


_INSIDE = "inside a repeat that can iterate more than once"


def _flat_atoms(items: list) -> list[CharSet]:
    """Sets of the one-character tokens of a repeated body; reject anything else (rule 2)."""
    out: list[CharSet] = []
    for op, av in items:
        s = _atom_set(op, av)
        if s is not None:
            out.append(s)
        elif op is _sre.SUBPATTERN and not (av[1] or av[2]):
            out.extend(_flat_atoms(av[3]))
        elif op is _sre.BRANCH:
            raise _Unsafe(f"nested repeats: an alternation {_INSIDE}")
        elif op in _REPEATS:
            raise _Unsafe(f"nested repeats: a repeat (or optional) {_INSIDE}")
        else:
            raise _Unsafe(f"nested repeats: only literals and character classes may be in {_INSIDE[7:]}")
    if not out:
        raise _Unsafe("empty repeat")
    return out


def _literals(items: list) -> _RegexInfo:
    """Literal prefix (from the start) and the longest other required literal run."""
    runs: list[str] = []
    cur: list[str] = []
    prefix: str | None = None
    start = True
    for op, av in items:
        if start and op is _sre.AT and str(av) in ("AT_BEGINNING", "AT_BEGINNING_STRING"):
            continue
        start = False
        if op is _sre.LITERAL:
            cur.append(chr(av))
            continue
        run = "".join(cur)
        if prefix is None:
            prefix = run
        elif run:
            runs.append(run)
        cur = []
    run = "".join(cur)
    if prefix is None:
        prefix = run
    elif run:
        runs.append(run)
    best = max(runs, key=len, default="")
    return _RegexInfo(prefix, best if len(best) >= MIN_NEEDLE else None)


MAX_ALTERNATIVES = 16


def _leading(items: list) -> list[str]:
    """Literal prefixes a match must start with: several when the regex opens with an
    alternation (``(?:Creating new|New dal) proxy``), else one (possibly "")."""
    run: list[str] = []
    for op, av in items:
        if op is _sre.AT and not run and str(av) in ("AT_BEGINNING", "AT_BEGINNING_STRING"):
            continue
        if op is _sre.LITERAL:
            run.append(chr(av))
            continue
        head = "".join(run)
        if op is _sre.SUBPATTERN and not (av[1] & (re.IGNORECASE | re.LOCALE)):
            return [head + tail for tail in _leading(av[3])]
        if op is _sre.BRANCH:
            tails: list[str] = []
            for b in av[1]:
                tails.extend(_leading(b))
            if 0 < len(tails) <= MAX_ALTERNATIVES and all(tails):
                return [head + t for t in tails]
        return [head]
    return ["".join(run)]


def _first_can(items: list, c: int) -> bool:
    """Whether a match of ``items`` can start with character ``c``; conservative (True if unsure)."""
    for op, av in items:
        if op is _sre.AT:
            continue
        if op is _sre.LITERAL:
            return av == c
        if op is _sre.NOT_LITERAL:
            return av != c
        if op is _sre.ANY:
            return c != 10
        if op is _sre.IN:
            s = _class_set(av)
            i = bisect.bisect_right(s, (c, _EOS))
            return i > 0 and s[i - 1][1] >= c
        if op is _sre.SUBPATTERN:
            if av[1] & (re.IGNORECASE | re.LOCALE):
                return True
            return _first_can(av[3], c)
        if op in _REPEATS:
            return True if av[0] == 0 else _first_can(av[2], c)
        if op is _sre.BRANCH:
            return any(_first_can(b, c) for b in av[1])
        return True
    return True


# --------------------------------------------------------------------------- TOML parsing

_FILE_KEYS = frozenset(
    {
        "schema",
        "family",
        "description",
        "extends",
        "payload_defaults",
        "template",
        "operation",
        "sensitive_pair",
        "link_rule",
    }
)
_TEMPLATE_KEYS = frozenset(
    {
        "code",
        "aliases",
        "class",
        "sev",
        "sev_field",
        "sev_map",
        "title",
        "regex",
        "text",
        "fields",
        "keys",
        "refs_sensitive",
        "fts",
        "payload",
        "stream",
        "cont",
        "examples",
        "counter_examples",
        "window",
    }
)
_PAYLOAD_KEYS = frozenset({"fmt", "start", "strip_suffix", "role", "direction", "peer"})


def _get(
    table: Mapping[str, Any],
    key: str,
    kind: type | tuple[type, ...],
    *,
    src: str,
    where: str,
    required: bool = False,
    default: Any = None,
) -> Any:
    if key not in table:
        if required:
            raise SchemaError(f"missing required key {key!r}", source=src, where=where)
        return default
    value = table[key]
    if isinstance(value, bool) and kind in (int, (int,)):
        raise SchemaError(f"{key!r} must be an integer, not a boolean", source=src, where=where)
    if not isinstance(value, kind):
        names = kind.__name__ if isinstance(kind, type) else " or ".join(k.__name__ for k in kind)
        raise SchemaError(f"{key!r} must be {names}, got {type(value).__name__}", source=src, where=where)
    return value


def _str_list(table: Mapping[str, Any], key: str, *, src: str, where: str) -> tuple[str, ...]:
    value = _get(table, key, list, src=src, where=where, default=[])
    if not all(isinstance(v, str) for v in value):
        raise SchemaError(f"{key!r} must be a list of strings", source=src, where=where)
    return tuple(value)


def _unknown_keys(table: Mapping[str, Any], allowed: frozenset[str], *, src: str, where: str) -> None:
    extra = sorted(set(table) - allowed)
    if extra:
        raise SchemaError(
            f"unknown key(s) {', '.join(extra)}; allowed: {', '.join(sorted(allowed))}",
            source=src,
            where=where,
        )


def _one_of(value: Any, allowed: Iterable[str], key: str, *, src: str, where: str) -> None:
    allowed = tuple(allowed)
    if value not in allowed:
        raise SchemaError(
            f"{key!r} must be one of {', '.join(allowed)}, got {value!r}", source=src, where=where
        )


def _parse_payload(value: Any, *, src: str, where: str) -> PayloadSpec:
    if not isinstance(value, dict):
        raise SchemaError("'payload' must be a table", source=src, where=where)
    _unknown_keys(value, _PAYLOAD_KEYS, src=src, where=where + " payload")
    fmt = _get(value, "fmt", str, src=src, where=where, required=True)
    _one_of(fmt, PAYLOAD_FORMATS, "payload.fmt", src=src, where=where)
    start = _get(value, "start", str, src=src, where=where, required=True)
    _one_of(start, PAYLOAD_STARTS, "payload.start", src=src, where=where)
    role = _get(value, "role", str, src=src, where=where, required=True)
    _one_of(role, PAYLOAD_ROLES, "payload.role", src=src, where=where)
    strip = _get(value, "strip_suffix", str, src=src, where=where)
    if strip is not None and not (1 <= len(strip) <= 8):
        raise SchemaError("'payload.strip_suffix' must have 1 to 8 characters", source=src, where=where)
    direction = _get(value, "direction", str, src=src, where=where)
    if direction is not None:
        _one_of(direction, DIRECTIONS, "payload.direction", src=src, where=where)
    peer = _get(value, "peer", str, src=src, where=where)
    if peer is not None and not _PEER_RE.fullmatch(peer):
        raise SchemaError("'payload.peer' must be an identifier (A-Z, a-z, 0-9, _)", source=src, where=where)
    return PayloadSpec(fmt, start, strip, role, direction, peer)


def _parse_cont(value: Any, *, src: str, where: str) -> ContSpec:
    if isinstance(value, str):
        value = {"parser": value}
    if not isinstance(value, dict):
        raise SchemaError("'cont' must be a parser name or a table with 'parser'", source=src, where=where)
    name = _get(value, "parser", str, src=src, where=where + " cont", required=True)
    if name not in CONT_PARSERS:
        raise SchemaError(
            f"unknown cont parser {name!r}; known: {', '.join(sorted(CONT_PARSERS))}", source=src, where=where
        )
    parser = CONT_PARSERS[name]
    opts = {k: v for k, v in value.items() if k != "parser"}
    for key, val in opts.items():
        kind = parser.options.get(key)
        if kind is None:
            allowed = ", ".join(sorted(parser.options)) or "none"
            raise SchemaError(
                f"cont parser {name!r} has no option {key!r} (options: {allowed})", source=src, where=where
            )
        if not isinstance(val, kind) or isinstance(val, bool):
            raise SchemaError(f"cont option {key!r} must be {kind.__name__}", source=src, where=where)
        if key in _OPTION_RANGES and not (_OPTION_RANGES[key][0] <= val <= _OPTION_RANGES[key][1]):
            lo, hi = _OPTION_RANGES[key]
            raise SchemaError(f"cont option {key!r} must be between {lo} and {hi}", source=src, where=where)
    rx = None
    outputs = parser.outputs
    if name == "line_regex":
        if "regex" not in opts:
            raise SchemaError("cont parser 'line_regex' needs a 'regex' option", source=src, where=where)
        rx, _ = _compile_regex(opts["regex"], src=src, where=where + " cont")
        outputs = frozenset(rx.groupindex)
    return ContSpec(name, MappingProxyType(opts), outputs, rx)


def _parse_example(value: Any, *, src: str, where: str, positive: bool) -> Example:
    if isinstance(value, str):
        return Example(value)
    if not isinstance(value, dict):
        raise SchemaError("an example must be a string or a table with 'msg'", source=src, where=where)
    allowed = frozenset({"msg", "cont", "fields"} if positive else {"msg", "cont"})
    _unknown_keys(value, allowed, src=src, where=where + " example")
    msg = _get(value, "msg", str, src=src, where=where, required=True)
    cont = _get(value, "cont", str, src=src, where=where)
    fields = _get(value, "fields", dict, src=src, where=where) if positive else None
    return Example(msg, cont, MappingProxyType(dict(fields)) if fields is not None else None)


def _parse_template(t: Any, family: str, idx: int, *, src: str, version: str) -> TemplateDef:
    where = f"template #{idx + 1}"
    if not isinstance(t, dict):
        raise SchemaError("[[template]] entries must be tables", source=src, where=where)
    code = _get(t, "code", str, src=src, where=where, required=True)
    if not _CODE_RE.fullmatch(code) or len(code) > 32:
        raise SchemaError(
            f"code {code!r} must look like ABC-001 (A-Z, 0-9, '-'), up to 32 characters",
            source=src,
            where=where,
        )
    where = f"template {code}"
    _unknown_keys(t, _TEMPLATE_KEYS, src=src, where=where)
    aliases = _str_list(t, "aliases", src=src, where=where)
    for a in aliases:
        if not _CODE_RE.fullmatch(a) or a == code:
            raise SchemaError(f"alias {a!r} must be a code different from {code!r}", source=src, where=where)
    cls = _get(t, "class", str, src=src, where=where, required=True)
    _one_of(cls, CLASSES, "class", src=src, where=where)
    sev = _get(t, "sev", int, src=src, where=where)
    if sev is not None and not 1 <= sev <= 5:
        raise SchemaError("'sev' must be between 1 (critical) and 5 (noise)", source=src, where=where)
    title = _get(t, "title", str, src=src, where=where)
    stream = _get(t, "stream", str, src=src, where=where, default="any")
    _one_of(stream, STREAMS, "stream", src=src, where=where)
    fts = _get(t, "fts", bool, src=src, where=where, default=True)
    refs = _get(t, "refs_sensitive", bool, src=src, where=where, default=False)

    regex_src = t.get("regex")
    text = t.get("text")
    if (regex_src is None) == (text is None):
        raise SchemaError("exactly one of 'regex' or 'text' is required", source=src, where=where)
    rx = None
    info = _RegexInfo("", None)
    window = _get(t, "window", int, src=src, where=where, default=MAX_MATCH_CHARS)
    if not MIN_MATCH_WINDOW <= window <= MAX_MATCH_CHARS:
        raise SchemaError(
            f"'window' must be between {MIN_MATCH_WINDOW} and {MAX_MATCH_CHARS}", source=src, where=where
        )
    if "window" in t and regex_src is None:
        raise SchemaError("'window' applies to regex templates only", source=src, where=where)
    if regex_src is not None:
        rx, info = _compile_regex(regex_src, src=src, where=where, window=window)
    else:
        if not isinstance(text, str) or not text or text != text.strip() or "\n" in text:
            raise SchemaError(
                "'text' must be a non-empty single line without surrounding spaces", source=src, where=where
            )
        if len(text) > MAX_MATCH_CHARS:
            raise SchemaError(f"'text' longer than {MAX_MATCH_CHARS} characters", source=src, where=where)

    cont = _parse_cont(t["cont"], src=src, where=where) if "cont" in t else None
    names = set(rx.groupindex) if rx is not None else set()
    cont_names = set(cont.outputs) if cont is not None else set()
    all_names = names | cont_names

    ftypes = _get(t, "fields", dict, src=src, where=where, default={})
    for name, kind in ftypes.items():
        if name not in all_names:
            raise SchemaError(
                f"'fields' declares {name!r}, which is not a regex group nor a cont output",
                source=src,
                where=where,
            )
        if not isinstance(kind, str) or kind not in FIELD_TYPES:
            raise SchemaError(
                f"field type of {name!r} must be one of {', '.join(FIELD_TYPES)}", source=src, where=where
            )

    keys = []
    for item in _get(t, "keys", list, src=src, where=where, default=[]):
        if isinstance(item, str):
            fname, ktype = item, item
        elif isinstance(item, dict) and set(item) <= {"field", "type"} and "field" in item:
            fname = item["field"]
            ktype = item.get("type", fname)
        else:
            raise SchemaError(
                "'keys' items must be field names or {field, type} tables", source=src, where=where
            )
        if not isinstance(fname, str) or fname not in all_names:
            raise SchemaError(
                f"key field {fname!r} is not a regex group nor a cont output", source=src, where=where
            )
        if not isinstance(ktype, str) or not _NAME_RE.fullmatch(ktype):
            raise SchemaError(f"key type {ktype!r} must be lowercase snake_case", source=src, where=where)
        keys.append(KeySpec(fname, ktype))

    sev_field = _get(t, "sev_field", str, src=src, where=where)
    sev_map_raw = _get(t, "sev_map", dict, src=src, where=where)
    if (sev_field is None) != (sev_map_raw is None):
        raise SchemaError("'sev_field' and 'sev_map' go together", source=src, where=where)
    sev_map = None
    if sev_field is not None:
        if sev_field not in all_names:
            raise SchemaError(f"sev_field {sev_field!r} is not a field", source=src, where=where)
        for k, v in sev_map_raw.items():
            if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 5:
                raise SchemaError(f"sev_map value for {k!r} must be an integer 1..5", source=src, where=where)
        sev_map = MappingProxyType(dict(sev_map_raw))

    payload = _parse_payload(t["payload"], src=src, where=where) if "payload" in t else None
    examples = tuple(
        _parse_example(e, src=src, where=where, positive=True)
        for e in _get(t, "examples", list, src=src, where=where, default=[])
    )
    counter = tuple(
        _parse_example(e, src=src, where=where, positive=False)
        for e in _get(t, "counter_examples", list, src=src, where=where, default=[])
    )
    return TemplateDef(
        code=code,
        family=family,
        cls=cls,
        sev=sev,
        regex=rx,
        text=text,
        keys=tuple(keys),
        refs_sensitive=refs,
        literal_prefix=info.prefix or None,
        fts=fts,
        payload=payload,
        title=title,
        aliases=aliases,
        stream=stream,
        cont=cont,
        field_types=MappingProxyType(dict(ftypes)),
        sev_field=sev_field,
        sev_map=sev_map,
        needle=info.needle,
        alt_prefixes=info.alts,
        can_start=info.starts,
        optional_groups=info.optional_groups,
        window=window,
        examples=examples,
        counter_examples=counter,
        source=src,
        def_version=version,
    )


def parse_family(data: bytes | str, source: str = "<string>") -> FamilyDef:
    """Parse and validate one TOML file (references to other files are checked by the catalog)."""
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    version = hashlib.sha1(raw).hexdigest()[:12]
    try:
        doc = tomllib.loads(raw.decode("utf-8-sig"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise SchemaError(f"invalid TOML: {exc}", source=source) from None
    _unknown_keys(doc, _FILE_KEYS, src=source, where="")
    schema = _get(doc, "schema", int, src=source, where="", default=SCHEMA_VERSION)
    if schema != SCHEMA_VERSION:
        raise SchemaError(
            f"unsupported schema {schema}; this dmslog reads schema {SCHEMA_VERSION}", source=source
        )
    family = _get(doc, "family", str, src=source, where="", required=True)
    if not _FAMILY_RE.fullmatch(family):
        raise SchemaError(
            f"family {family!r} must be an identifier (libraries start with '_')", source=source
        )
    description = _get(doc, "description", str, src=source, where="")
    extends = _str_list(doc, "extends", src=source, where="")
    if family.startswith("_") and extends:
        raise SchemaError("a library (family starting with '_') cannot extend others", source=source)
    if len(set(extends)) != len(extends):
        raise DuplicateError("'extends' lists a library twice", source=source)

    defaults: dict[str, Mapping[str, str]] = {}
    for role, spec in _get(doc, "payload_defaults", dict, src=source, where="", default={}).items():
        where = f"payload_defaults.{role}"
        if role not in PAYLOAD_ROLES or role == "by_root":
            raise SchemaError(f"unknown payload role {role!r}", source=source, where=where)
        if not isinstance(spec, dict):
            raise SchemaError("must be a table {direction, peer}", source=source, where=where)
        _unknown_keys(spec, frozenset({"direction", "peer"}), src=source, where=where)
        direction = _get(spec, "direction", str, src=source, where=where, required=True)
        _one_of(direction, DIRECTIONS, "direction", src=source, where=where)
        peer = _get(spec, "peer", str, src=source, where=where)
        if peer is not None and not _PEER_RE.fullmatch(peer):
            raise SchemaError("'peer' must be an identifier", source=source, where=where)
        defaults[role] = MappingProxyType({"direction": direction, **({"peer": peer} if peer else {})})

    raw_templates = _get(doc, "template", list, src=source, where="", default=[])
    templates = tuple(
        _parse_template(t, family, i, src=source, version=version) for i, t in enumerate(raw_templates)
    )
    rules = {}
    for section in ("operation", "sensitive_pair", "link_rule"):
        items = _get(doc, section, list, src=source, where="", default=[])
        if not all(isinstance(i, dict) for i in items):
            raise SchemaError(f"[[{section}]] entries must be tables", source=source)
        rules[section] = tuple(items)
    return FamilyDef(
        family=family,
        source=source,
        def_version=version,
        description=description,
        extends=extends,
        templates=templates,
        payload_defaults=MappingProxyType(defaults),
        operations=rules["operation"],
        sensitive_pairs=rules["sensitive_pair"],
        link_rules=rules["link_rule"],
    )


# --------------------------------------------------------------------------- dispatch

# prefix, needle, match, template index, window
_Cand = tuple[str, str | None, Callable[[str], "re.Match[str] | None"], int, int]


def _choose_k(prefixes: list[str]) -> int:
    """Bucket width: minimize (templates without a bucket) + (largest bucket)."""
    best_k, best_cost = 2, None
    for k in range(2, 13):
        generic = sum(1 for p in prefixes if len(p) < k)
        buckets: dict[str, int] = {}
        for p in prefixes:
            if len(p) >= k:
                buckets[p[:k]] = buckets.get(p[:k], 0) + 1
        cost = generic + max(buckets.values(), default=0)
        if best_cost is None or cost < best_cost:
            best_k, best_cost = k, cost
    return best_k


class TemplateSet:
    """Templates of one family for one stream kind, with a dispatch table. Thread-safe to read."""

    __slots__ = (
        "family",
        "sensitive",
        "templates",
        "payload_defaults",
        "_ops",
        "_pairs",
        "_links",
        "_ids",
        "_gidx",
        "_exact",
        "_k",
        "_keyed",
        "_short",
        "_memo",
        "_starts",
        "_gi",
        "_conv",
    )

    def __init__(
        self,
        family: str,
        sensitive: bool,
        templates: tuple[TemplateDef, ...],
        ids: list[int],
        gidx: Mapping[str, int],
        payload_defaults: Mapping[str, Mapping[str, str]],
        operations: tuple[Mapping[str, Any], ...] = (),
        sensitive_pairs: tuple[Mapping[str, Any], ...] = (),
        link_rules: tuple[Mapping[str, Any], ...] = (),
    ) -> None:
        self.family = family
        self.sensitive = sensitive
        self.templates = templates
        self.payload_defaults = payload_defaults
        self._ops = operations
        self._pairs = sensitive_pairs
        self._links = link_rules
        self._ids = ids  # shared with the catalog: bind_ids() updates it in place
        self._gidx = gidx
        self._gi = tuple(gidx[t.code] for t in templates)
        self._conv = tuple(
            tuple((n, _CONVERTERS[k]) for n, k in t.field_types.items() if k != "str") for t in templates
        )
        self._exact: dict[str, int] = {}
        cands: list[_Cand] = []
        self._starts: dict[int, Callable[[str], bool] | None] = {}
        for i, t in enumerate(templates):
            if t.text is not None:
                self._exact.setdefault(t.text, i)
                continue
            assert t.regex is not None
            self._starts[i] = t.can_start
            for p in t.alt_prefixes or (t.literal_prefix or "",):
                cands.append((p, t.needle, t.regex.match, i, t.window))
        self._k = k = _choose_k([c[0] for c in cands])
        self._keyed: dict[str, list[_Cand]] = {}
        for c in cands:
            if len(c[0]) >= k:
                self._keyed.setdefault(c[0][:k], []).append(c)
        self._short = tuple(c for c in cands if len(c[0]) < k)  # tried for any key they fit
        self._memo: dict[str, tuple[_Cand, ...]] = {}

    def _for_key(self, key: str) -> tuple[_Cand, ...]:
        """Candidates for messages starting with ``key`` (the first K characters), in order."""
        found = self._memo.get(key)
        if found is not None:
            return found
        out = list(self._keyed.get(key, ()))
        for c in self._short:
            p = c[0]
            if p:
                if key.startswith(p):
                    out.append(c)
            else:
                starts = self._starts.get(c[3])
                if starts is None or starts(key[0]):
                    out.append(c)
        out.sort(key=lambda c: c[3])
        found = tuple(out)
        if len(self._memo) < MAX_DISPATCH_KEYS:
            self._memo[key] = found
        return found

    def __len__(self) -> int:
        return len(self.templates)

    def __repr__(self) -> str:
        kind = "sensitive" if self.sensitive else "normal"
        return f"<TemplateSet {self.family} {kind}: {len(self.templates)} templates>"

    def operations(self) -> tuple[Mapping[str, Any], ...]:
        """[[operation]] rules of the family (codes resolved to canonical codes)."""
        return self._ops

    def sensitive_pairs(self) -> tuple[Mapping[str, Any], ...]:
        return self._pairs

    def link_rules(self) -> tuple[Mapping[str, Any], ...]:
        """[[link_rule]] rules of the family (callbacks, retries, responses; codes canonical)."""
        return self._links

    def candidates(self, msg: str) -> int:
        """Number of regex templates that would be tried for ``msg`` (diagnostics, benchmarks)."""
        s = msg.strip()[:MAX_MATCH_CHARS]
        return len(self._for_key(s[: self._k])) if s else 0

    def classify(self, record: _HasMsg) -> TemplateMatch | None:
        """Classify a record (anything with ``msg`` and ``cont``). Never raises."""
        try:
            return self._classify(record.msg, record.cont)
        except Exception:  # noqa: BLE001 - a log line must never break ingestion
            return None

    def classify_text(self, msg: str, cont: str | None = None) -> TemplateMatch | None:
        try:
            return self._classify(msg, cont)
        except Exception:  # noqa: BLE001
            return None

    def _classify(self, msg: str, cont: str | None) -> TemplateMatch | None:
        s = msg.strip()
        lead = 0 if s is msg else len(msg) - len(msg.lstrip())
        if not s:
            return None
        if len(s) > MAX_MATCH_CHARS + 1:
            s = s[: MAX_MATCH_CHARS + 1]  # one more than the largest window: tells "fits" from "too long"
        if "\n" in s:  # the regex analysis assumes a single line (records never hold one)
            s = s[: s.index("\n")]
        m: re.Match[str] | None = None
        i = self._exact.get(s)
        if i is not None:
            end = lead + len(s)
        else:
            key = s[: self._k]
            cands = self._memo.get(key)
            if cands is None:
                cands = self._for_key(key)
            n = len(s)
            for prefix, needle, match, idx, window in cands:
                if prefix and not s.startswith(prefix):
                    continue
                if needle is not None and needle not in s:
                    continue
                if n > window:  # a template applies only to messages that fit its window
                    continue
                m = match(s)
                if m is not None:
                    i = idx
                    break
            else:
                return None
            end = lead + m.end()
        # Build the result (inline: this is the hot path).
        t = self.templates[i]
        conv = self._conv[i]
        if m is not None:
            fields: dict[str, Any] = m.groupdict()
            if t.optional_groups and None in fields.values():
                fields = {k: v for k, v in fields.items() if v is not None}
            for name, fn in conv:
                v = fields.get(name)
                if v:  # empty captures stay ""
                    fields[name] = fn(v)
        else:
            fields = {}
        spec = t.cont
        if spec is not None and cont:
            try:
                extra = CONT_PARSERS[spec.parser].fn(cont, spec.options, spec)
            except Exception:  # noqa: BLE001 - a malformed continuation only loses its fields
                extra = {}
            for name, value in extra.items():
                if name not in fields:  # regex fields win
                    fields[name] = value
            if conv and spec.parser == "line_regex":
                for name, fn in conv:
                    v = extra.get(name)
                    if v and fields.get(name) is v:
                        fields[name] = fn(v)
        sev = t.sev
        if t.sev_map is not None:
            v = fields.get(t.sev_field)  # type: ignore[arg-type]
            if isinstance(v, list):
                # list field: the most severe (lowest) mapped value wins; unmapped values are ignored
                mapped = [t.sev_map[str(x)] for x in v if str(x) in t.sev_map]
                if mapped:
                    sev = min(mapped)
            elif isinstance(v, (str, int)) and not isinstance(v, bool):
                sev = t.sev_map.get(str(v), sev)
        keys: list[tuple[str, str]] = []
        for ks in t.keys:
            v = fields.get(ks.field)
            if v is None or v == "":
                continue
            if isinstance(v, list):
                keys.extend((ks.type, str(x)) for x in v if x != "")
            elif not isinstance(v, dict):
                keys.append((ks.type, str(v)))
        return TemplateMatch(
            self._ids[self._gi[i]], t.code, t.cls, sev, t.fts, t.payload, fields, keys, t.refs_sensitive, end
        )


# --------------------------------------------------------------------------- catalog


class Catalog:
    """All loaded families: code/alias resolution, template ids and per-family sets."""

    def __init__(self, families: Mapping[str, FamilyDef]) -> None:
        self.families: Mapping[str, FamilyDef] = MappingProxyType(dict(families))
        self._by_code: dict[str, TemplateDef] = {}
        self._alias: dict[str, str] = {}
        texts: dict[tuple[str, str], str] = {}
        for fam in self.families.values():
            for t in fam.templates:
                where = f"template {t.code}"
                if t.code in self._by_code or t.code in self._alias:
                    other = self._by_code[t.code].source if t.code in self._by_code else "an alias"
                    raise DuplicateError(
                        f"code {t.code} is already defined in {other}", source=fam.source, where=where
                    )
                self._by_code[t.code] = t
                if t.text is not None:
                    key = (fam.family, t.text)
                    if key in texts:
                        raise DuplicateError(f"same text as {texts[key]}", source=fam.source, where=where)
                    texts[key] = t.code
        for fam in self.families.values():
            for t in fam.templates:
                for a in t.aliases:
                    if a in self._by_code or a in self._alias:
                        raise DuplicateError(
                            f"alias {a} is already a code or an alias",
                            source=fam.source,
                            where=f"template {t.code}",
                        )
                    self._alias[a] = t.code
        for fam in self.families.values():
            for lib in fam.extends:
                if lib not in self.families:
                    raise TemplateReferenceError(f"extends unknown library {lib!r}", source=fam.source)
                if not lib.startswith("_"):
                    raise TemplateReferenceError(
                        f"extends {lib!r}, which is not a library ('_' prefix)", source=fam.source
                    )
        # Provisional ids: 1..N in code order. The writer binds the ids of the case database.
        codes = sorted(self._by_code)
        self._gidx: dict[str, int] = {c: i for i, c in enumerate(codes)}
        self._ids: list[int] = list(range(1, len(codes) + 1))
        self._sets: dict[tuple[str, bool], TemplateSet] = {}
        self._rules = {name: self._resolve_rules(fam) for name, fam in self.families.items()}

    def _resolve_rules(
        self, fam: FamilyDef
    ) -> tuple[tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...]]:
        visible = {t.code for t in self._chain(fam.family)}
        out = []
        for section, items in (
            ("operation", fam.operations),
            ("sensitive_pair", fam.sensitive_pairs),
            ("link_rule", fam.link_rules),
        ):
            resolved = []
            for n, item in enumerate(items, 1):
                rule = dict(item)
                for key in CODE_REF_KEYS & set(rule):
                    value = rule[key]
                    refs = [value] if isinstance(value, str) else value
                    if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
                        raise SchemaError(
                            f"{key!r} must be a code or a list of codes",
                            source=fam.source,
                            where=f"{section} #{n}",
                        )
                    canon = []
                    for r in refs:
                        c = self.resolve(r)
                        if c is None or c not in visible:
                            raise TemplateReferenceError(
                                f"{key!r} references unknown template {r!r}",
                                source=fam.source,
                                where=f"{section} #{n}",
                            )
                        canon.append(c)
                    rule[key] = canon[0] if isinstance(value, str) else canon
                resolved.append(MappingProxyType(rule))
            out.append(tuple(resolved))
        return out[0], out[1], out[2]

    def _chain(self, family: str) -> list[TemplateDef]:
        fam = self.families.get(family)
        if fam is None:
            lib = self.families.get(DEFAULT_LIBRARY)
            return list(lib.templates) if lib is not None else []
        out = list(fam.templates)
        for lib in fam.extends:
            out.extend(self.families[lib].templates)
        return out

    def __len__(self) -> int:
        return len(self._by_code)

    def resolve(self, code: str) -> str | None:
        """Canonical code for a code or an old alias; None when unknown."""
        if code in self._by_code:
            return code
        return self._alias.get(code)

    def template(self, code: str) -> TemplateDef:
        canon = self.resolve(code)
        if canon is None:
            raise KeyError(code)
        return self._by_code[canon]

    def templates(self) -> Iterator[TemplateDef]:
        """Every template, in code order."""
        for code in sorted(self._by_code):
            yield self._by_code[code]

    def tpl_id(self, code: str) -> int:
        return self._ids[self._gidx[self.template(code).code]]

    def bind_ids(self, mapping: Mapping[str, int]) -> None:
        """Use the template ids of a case database (code -> template.id). Missing codes keep theirs."""
        for code, tid in mapping.items():
            canon = self.resolve(code)
            if canon is not None and isinstance(tid, int) and tid > 0:
                self._ids[self._gidx[canon]] = tid

    def for_family(self, family: str, *, sensitive: bool) -> TemplateSet:
        """Templates for a stream of ``family``. Families without a file get DEFAULT_LIBRARY."""
        key = (family, sensitive)
        tset = self._sets.get(key)
        if tset is None:
            excluded = "normal" if sensitive else "sensitive"
            chain = tuple(t for t in self._chain(family) if t.stream != excluded)
            fam = self.families.get(family)
            defaults = fam.payload_defaults if fam is not None else MappingProxyType({})
            ops, pairs, links = self._rules.get(family, ((), (), ()))
            tset = TemplateSet(family, sensitive, chain, self._ids, self._gidx, defaults, ops, pairs, links)
            self._sets[key] = tset
        return tset

    def check_examples(self) -> list[str]:
        """Verify every template against its examples; return the problems (empty = ok).

        A positive example must be classified as its template (so an earlier template that
        shadows it is reported) with the declared fields; a counter-example must not be.
        Inside one file, an example may not match any other template of that file.
        """
        problems: list[str] = []
        for fam in self.families.values():
            for t in fam.templates:
                streams = (False, True) if t.stream == "any" else ((t.stream == "sensitive"),)
                for sensitive in streams:
                    tset = self.for_family(fam.family, sensitive=sensitive)
                    for ex in t.examples:
                        tm = tset.classify_text(ex.msg, ex.cont)
                        got = tm.code if tm is not None else None
                        if got != t.code:
                            problems.append(f"{fam.source}: {t.code}: example {ex.msg!r} classified as {got}")
                            continue
                        assert tm is not None
                        for k, v in (ex.fields or {}).items():
                            if tm.fields.get(k) != v:
                                problems.append(
                                    f"{fam.source}: {t.code}: field {k!r} of {ex.msg!r} is "
                                    f"{tm.fields.get(k)!r}, expected {v!r}"
                                )
                    for ex in t.counter_examples:
                        tm = tset.classify_text(ex.msg, ex.cont)
                        if tm is not None and tm.code == t.code:
                            problems.append(f"{fam.source}: {t.code}: counter-example {ex.msg!r} matched")
            for t in fam.templates:
                for ex in t.examples:
                    s = ex.msg.strip()
                    for other in fam.templates:
                        if other is t or {t.stream, other.stream} == {"normal", "sensitive"}:
                            continue
                        if len(s) > other.window:  # `other` applies only to messages that fit its window
                            continue
                        hit = other.text == s if other.text is not None else other.regex.match(s) is not None  # type: ignore[union-attr]
                        if hit:
                            problems.append(
                                f"{fam.source}: example {ex.msg!r} of {t.code} also matches {other.code}"
                            )
        return list(dict.fromkeys(problems))


def _check_or_raise(catalog: Catalog) -> None:
    problems = catalog.check_examples()
    if problems:
        raise ExampleError(
            "; ".join(problems[:10]) + (f" (+{len(problems) - 10} more)" if len(problems) > 10 else "")
        )


def load_texts(files: Mapping[str, bytes | str], *, check_examples: bool = False) -> Catalog:
    """Build a catalog from {file name: TOML text}; files are read in name order."""
    families: dict[str, FamilyDef] = {}
    for name in sorted(files):
        fam = parse_family(files[name], source=name)
        if fam.family in families:
            raise DuplicateError(
                f"family {fam.family} is also defined in {families[fam.family].source}", source=name
            )
        families[fam.family] = fam
    catalog = Catalog(families)
    if check_examples:
        _check_or_raise(catalog)
    return catalog


def load_dir(
    directory: Traversable | Path, *, skip: Iterable[str] = SKIPPED_FILES, check_examples: bool = False
) -> Catalog:
    """Load every ``*.toml`` of a directory (a Path or an importlib.resources Traversable)."""
    skipped = set(skip)
    files = {}
    for entry in directory.iterdir():
        if entry.name.endswith(".toml") and entry.name not in skipped and entry.is_file():
            files[entry.name] = entry.read_bytes()
    return load_texts(files, check_examples=check_examples)


def load_catalog(*, check_examples: bool = False) -> Catalog:
    """Load the packaged adapter files (works inside the .pyz)."""
    return load_dir(resources.files(ADAPTERS_PACKAGE), check_examples=check_examples)


def compile_search_regex(source: str, *, window: int, max_steps: int) -> re.Pattern[str]:
    """Compile a user regex for ``re.search`` on one line of at most ``window`` characters.

    Same whitelist and construction limits as the catalog (``_compile_regex``), and a work budget for
    search: a search tries the pattern at every start, so the bound is ``window * work(window)``
    (the ``re.match`` bound of :func:`_check_regex` at every position) and it must stay within
    ``max_steps``. In practice a pattern with one unbounded repeat passes and two unbounded repeats in
    sequence (``a.*b\\d+``) do not; two conditions combined with AND do the same job.
    Raises InvalidRegexError (does not compile, unnamed group, ...) or UnsafeRegexError (budget).
    """
    rx, _info = _compile_regex(source, src="", where="regex", window=1)
    if not _HAVE_PARSER:  # pragma: no cover
        return rx
    try:
        info = _seq(_sre_parser.parse(source, 0).data, _PATTERN_END)
    except _Unsafe as exc:  # pragma: no cover - _compile_regex already ran the same analysis
        raise UnsafeRegexError(str(exc), where="regex") from None
    steps = _at(_add(info.work, info.once), window) * window
    if steps > max_steps:
        raise UnsafeRegexError(
            f"too much possible backtracking for a search on a {window}-character line ({steps} steps >"
            f" {max_steps}); keep one unbounded repeat (* or +) per regex, or combine two conditions",
            where="regex",
        )
    return rx
