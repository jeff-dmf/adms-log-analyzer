r"""Ad hoc extraction regex (ROADMAP 9.8): a small grammar whose cost is bounded by construction.

The user types a regex with named groups; every group becomes a temporary column over the records of a
filter. Python's ``re`` has no step limit, so the regex is not accepted as such: a hand-written reader of
the ORIGINAL text accepts only the grammar below (whatever ``re`` would make of the rest), checks that
every choice of the pattern is decided by the next character, computes a bound of the work of one search
and derives from it how many characters of a record the regex may look at (its *window*). The pattern
that ``re`` compiles is then written back from the parsed form (:func:`emit`), with every character class
spelled out, so what runs is exactly what was checked, and the same text is valid in a template.

Grammar (anything else is an :class:`ExtractorError` with the offset in the text):

* an optional leading ``^`` (the regex is then tried at the start only: ``re.match``); no other anchor
  (``$``, ``\A``, ``\Z``, ``\b``, ``\B``), no alternation, look-around, back-reference, comment, flag,
  atomic group or conditional;
* atoms: a literal character (printable; the others through an escape), ``.`` (any character but a line
  feed), an escape (``\d \D \w \W \s \S`` with ASCII meaning: ``\d`` is 0-9, ``\w`` is ``[0-9A-Za-z_]``,
  ``\s`` is ``[\t\n\v\f\r ]``; ``\t \n \r \f \v``, ``\xHH``, ``\uHHHH``, ``\UHHHHHHHH``; an escaped ASCII
  punctuation character or space), a class ``[...]`` or ``[^...]`` of such characters, ranges and
  ``\d``-like escapes (at most 32 items; no nested sets or set operations; ``]`` only escaped);
* a greedy repeat of ONE atom: ``?``, ``*``, ``+``, ``{n}``, ``{m,n}``, ``{,n}``, ``{m,}`` with bounds up to
  64 (no lazy, possessive or repeated repeat; a literal ``{`` is escaped);
* groups ``(?P<name>...)`` (name ``[a-z][a-z0-9_]{0,31}``, at most 16, at least one) and ``(?:...)``, not
  empty, nested at most 2 deep; the only repeat of a group is ``?`` (at most 4 optional groups, not nested,
  never able to match the empty text);
* the whole pattern cannot match the empty text; at most 512 characters.

Determinism (checked right to left in one pass, like the generic headers of ``parse/formats.py``): a
variable repeat cannot match a character that can come right after it, and an optional group cannot start
like what follows it. What follows the end of the pattern is nothing (the match is complete), so a
trailing ``.*`` is fine. Then, at one start position, every give-back of a repeat fails at the first test
of what follows it, and nothing is consumed twice.

Cost model (one "step" = one test of one character against one atom). With ``k`` atoms, ``q`` optional
groups, ``g`` the most tests a failing continuation can make after a give-back (the atoms tried until the
first one that cannot be skipped), ``M`` the longest match (``None`` = unbounded) and ``L = min(rest, M)``
the characters a start position can consume, one start costs at most ``(g+1)(q+1)(L+k) + q*k`` (each
consumed character is tested once and given back at most once; each optional group adds one abandoned
attempt of its body and one failed skip). A search tries every start of a text of ``n`` characters, so
``steps(n) = (g+1)(q+1)(S(n) + k(n+1)) + q*k*(n+1)`` with ``S(n) = sum_{j<=n} min(j, M)``; an anchored
regex tries one start. The window is the largest ``n`` (at most the target's window) with
``steps(n) <= MAX_STEPS``, everything multiplied by the weight of the costliest atom
(:func:`set_weight`: ``re`` tests the ranges above U+FFFF of a class one by one); below
:data:`MIN_WINDOW` the regex is refused (a bounded repeat or a leading
``^`` lowers the cost). A test simulator (``tests/property``) counts the steps of a backtracking matcher
of this grammar and checks them against the bound, and the results against ``re``.
"""

from __future__ import annotations

import math
import re
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, NoReturn

MAX_PATTERN_CHARS = 512
MAX_GROUPS = 16
MAX_GROUP_DEPTH = 2
MAX_OPTIONAL_GROUPS = 4
MAX_CLASS_ITEMS = 32
MAX_BOUND = 64
MAX_STEPS = 1 << 22  # per search of one record: ~20 ms at the speeds measured (ARQUITETURA draft)
MIN_WINDOW = 128
TARGETS = ("msg", "text")
# Characters of a record a regex may look at, at most: the first line (as the templates) or the whole text.
TARGET_WINDOWS = {"msg": 4096, "text": 65536}
TYPES = ("str", "int", "hex", "guid", "float")
_NAME_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_REPEAT_RE = re.compile(r"\{([0-9]*)(,?)([0-9]*)\}")
_HEX = frozenset("0123456789abcdefABCDEF")
_INT_RE = re.compile(r"-?[0-9]{1,18}")
_HEX_RE = re.compile(r"0[xX][0-9a-fA-F]{1,15}")
_GUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

MAX_CP = 0x10FFFF
CharSet = tuple[tuple[int, int], ...]  # sorted, disjoint, non-adjacent inclusive ranges of code points


class ExtractorError(ValueError):
    """The regex is outside the grammar or too costly. ``offset`` points into the text (or is None)."""

    def __init__(self, detail: str, offset: int | None = None, code: str = "bad_regex") -> None:
        self.detail = detail
        self.offset = offset
        self.code = code
        super().__init__(detail if offset is None else f"{detail} (at offset {offset})")


# --------------------------------------------------------------------------- character sets


def _norm(ranges: list[tuple[int, int]]) -> CharSet:
    out: list[tuple[int, int]] = []
    for lo, hi in sorted(ranges):
        if out and lo <= out[-1][1] + 1:
            if hi > out[-1][1]:
                out[-1] = (out[-1][0], hi)
        else:
            out.append((lo, hi))
    return tuple(out)


def _union(a: CharSet, b: CharSet) -> CharSet:
    return _norm([*a, *b]) if a and b else (a or b)


def _complement(a: CharSet) -> CharSet:
    out: list[tuple[int, int]] = []
    nxt = 0
    for lo, hi in a:
        if lo > nxt:
            out.append((nxt, lo - 1))
        nxt = hi + 1
    if nxt <= MAX_CP:
        out.append((nxt, MAX_CP))
    return tuple(out)


def _overlap(a: CharSet, b: CharSet) -> int | None:
    """A code point in both sets, or None."""
    i = j = 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        if lo <= min(a[i][1], b[j][1]):
            return lo
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return None


def contains(cs: CharSet, cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in cs)


EMPTY: CharSet = ()
DIGIT: CharSet = ((0x30, 0x39),)
WORD: CharSet = ((0x30, 0x39), (0x41, 0x5A), (0x5F, 0x5F), (0x61, 0x7A))
SPACE: CharSet = ((0x09, 0x0D), (0x20, 0x20))
DOT: CharSet = _complement(((0x0A, 0x0A),))
_CLASSES = {
    "d": DIGIT,
    "D": _complement(DIGIT),
    "w": WORD,
    "W": _complement(WORD),
    "s": SPACE,
    "S": _complement(SPACE),
}
_CHAR_ESCAPES = {"t": 0x09, "n": 0x0A, "r": 0x0D, "f": 0x0C, "v": 0x0B}
_CODE_ESCAPES = {"x": 2, "u": 4, "U": 8}

# --------------------------------------------------------------------------- parsed form
# ("set", cs, offset) one character of the set; ("rep", lo, hi | None, cs, offset) a greedy repeat of one
# character of the set; ("group", name | None, items, offset); ("opt", name | None, items, offset) an
# optional group.
Item = tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class Parsed:
    anchored: bool
    items: tuple[Item, ...]
    groups: tuple[str, ...]  # named groups, in order of their opening parenthesis


def parse(source: str) -> Parsed:
    """Read ``source`` with the grammar of the module docstring; raise ExtractorError otherwise."""
    if not isinstance(source, str) or not source:
        raise ExtractorError("the regex is empty")
    if len(source) > MAX_PATTERN_CHARS:
        raise ExtractorError(f"the regex is longer than {MAX_PATTERN_CHARS} characters")
    text = source
    n = len(text)
    anchored = text.startswith("^")
    pos = 1 if anchored else 0
    groups: list[str] = []
    optional = 0

    def fail(message: str, at: int | None = None) -> NoReturn:
        raise ExtractorError(message, pos if at is None else at)

    def code_point(width: int, start: int) -> int:
        nonlocal pos
        digits = text[pos : pos + width]
        if len(digits) != width or not set(digits) <= _HEX:
            fail(f"\\{text[pos - 1]} needs exactly {width} hex digits", start)
        pos += width
        cp = int(digits, 16)
        if cp > MAX_CP:
            fail("the code point is above U+10FFFF", start)
        return cp

    def escape(in_class: bool) -> CharSet:
        """The set of the escape whose backslash was just read; advances ``pos``."""
        nonlocal pos
        start = pos - 1
        if pos >= n:
            fail("a trailing backslash", start)
        ch = text[pos]
        pos += 1
        if ch in _CLASSES:
            return _CLASSES[ch]
        if ch in _CHAR_ESCAPES:
            return ((_CHAR_ESCAPES[ch],) * 2,)
        if ch in _CODE_ESCAPES:
            cp = code_point(_CODE_ESCAPES[ch], start)
            return ((cp, cp),)
        if ch.isdigit():
            fail("back-references and octal escapes are not allowed; use \\xHH", start)
        if ch == "b" and in_class:
            fail("\\b is not allowed in a class; use \\x08 for a backspace", start)
        if ch in "AZzbBG":
            fail("anchors other than a leading '^' are not allowed", start)
        if ch.isascii() and (ch.isalnum() or not ch.isprintable()):
            fail(f"the escape \\{ch} is not allowed", start)
        if not ch.isascii():
            fail("only ASCII punctuation may be escaped; write the character itself or use \\uHHHH", start)
        return ((ord(ch), ord(ch)),)  # an escaped ASCII punctuation character or space is itself

    def literal(ch: str, at: int) -> CharSet:
        if not ch.isprintable():
            fail("control and invisible characters must be written as escapes (\\t, \\n, \\xHH, \\uHHHH)", at)
        return ((ord(ch), ord(ch)),)

    def char_class() -> CharSet:
        """The set of the class whose ``[`` was just read; advances ``pos`` past ``]``."""
        nonlocal pos
        start = pos - 1
        negated = text.startswith("^", pos)
        if negated:
            pos += 1
        ranges: list[tuple[int, int]] = []
        items = 0
        while True:
            if pos >= n:
                fail("an unterminated character class", start)
            ch = text[pos]
            if ch == "]":
                if not items:
                    fail("an empty class; write ']' as '\\]'", pos)
                pos += 1
                break
            if ch == "[" or text.startswith(("--", "&&", "~~", "||"), pos):
                fail("nested sets and set operations are not allowed in a class; escape them")
            items += 1
            if items > MAX_CLASS_ITEMS:
                fail(f"a class has at most {MAX_CLASS_ITEMS} items", start)
            at = pos
            pos += 1
            if ch == "\\":
                lo_set = escape(True)
            else:
                lo_set = literal(ch, at)
            if text.startswith("--", pos):
                fail("nested sets and set operations are not allowed in a class; escape them")
            if text.startswith("-", pos) and pos + 1 < n and text[pos + 1] != "]":
                if len(lo_set) != 1 or lo_set[0][0] != lo_set[0][1]:
                    fail("a range must go between two characters", at)
                pos += 1
                at2 = pos
                ch2 = text[pos]
                pos += 1
                if ch2 == "[":
                    fail("nested sets and set operations are not allowed in a class; escape them", at2)
                hi_set = escape(True) if ch2 == "\\" else literal(ch2, at2)
                if len(hi_set) != 1 or hi_set[0][0] != hi_set[0][1]:
                    fail("a range must go between two characters", at2)
                lo, hi = lo_set[0][0], hi_set[0][0]
                if lo > hi:
                    fail("a range must go from low to high", at)
                ranges.append((lo, hi))
            else:
                ranges.extend(lo_set)
        cs = _norm(ranges)
        if negated:
            cs = _complement(cs)
        if not cs:
            fail("the class matches no character", start)
        return cs

    def repeat() -> tuple[int, int | None] | None:
        """The repeat at ``pos`` as (lo, hi | None), or None; advances ``pos``."""
        nonlocal pos
        if pos >= n:
            return None
        ch = text[pos]
        start = pos
        if ch == "?":
            pos += 1
            bounds: tuple[int, int | None] = (0, 1)
        elif ch == "*":
            pos += 1
            bounds = (0, None)
        elif ch == "+":
            pos += 1
            bounds = (1, None)
        elif ch == "{":
            m = _REPEAT_RE.match(text, pos)
            if m is None:
                fail("a literal '{' must be escaped as '\\{'")
            lo_text, comma, hi_text = m.groups()
            if len(lo_text) > 3 or len(hi_text) > 3:  # checked before int(): no huge numbers
                fail(f"a repeat bound is at most {MAX_BOUND}")
            if not comma:
                if not lo_text:
                    fail("a literal '{' must be escaped as '\\{'")
                lo = hi = int(lo_text)
                if not 1 <= hi <= MAX_BOUND:
                    fail(f"a repeat count must be from 1 to {MAX_BOUND}")
                bounds = (lo, hi)
            elif not hi_text:
                if not lo_text:
                    fail("a repeat needs at least one bound")
                lo = int(lo_text)
                if lo > MAX_BOUND:
                    fail(f"a repeat bound is at most {MAX_BOUND}")
                bounds = (lo, None)
            else:
                lo, hi = int(lo_text or "0"), int(hi_text)
                if not 1 <= hi <= MAX_BOUND or lo > hi:
                    fail(f"a repeat {{m,n}} needs 0 <= m <= n and 1 <= n <= {MAX_BOUND}")
                bounds = (lo, hi)
            pos = m.end()
        else:
            return None
        if pos < n and text[pos] in "?+*{":
            fail("lazy, possessive and repeated repeats are not allowed", start)
        return bounds

    def sequence(depth: int) -> list[Item]:
        nonlocal pos
        items: list[Item] = []
        while pos < n:
            ch = text[pos]
            at = pos
            if ch == ")":
                if depth == 0:
                    fail("an unbalanced ')'")
                return items
            if ch in "*+?{":
                if ch == "{" and _REPEAT_RE.match(text, pos) is None:
                    fail("a literal '{' must be escaped as '\\{'")
                fail("a repeat with nothing to repeat")
            pos += 1
            if ch == "(":
                items.append(group(depth + 1, at))
                continue
            if ch == "^":
                fail("'^' is only allowed at the start of the regex", at)
            if ch == "$":
                fail("'$' is not supported; a repeat at the end already takes the rest of the line", at)
            if ch == "|":
                fail("alternation is not allowed; use a class or two extractions", at)
            if ch == ".":
                cs = DOT
            elif ch == "[":
                cs = char_class()
            elif ch == "\\":
                cs = escape(False)
            else:
                cs = literal(ch, at)
            bounds = repeat()
            items.append(("set", cs, at) if bounds is None else ("rep", bounds[0], bounds[1], cs, at))
        if depth:
            fail("a missing ')'")
        return items

    def group(depth: int, start: int) -> Item:
        """The group whose ``(`` was just read; advances ``pos`` past ``)`` and its repeat."""
        nonlocal pos, optional
        if depth > MAX_GROUP_DEPTH:
            fail(f"groups nested deeper than {MAX_GROUP_DEPTH} are not allowed", start)
        name: str | None = None
        if text.startswith("?:", pos):
            pos += 2
        elif text.startswith("?P<", pos):
            close = text.find(">", pos + 3)
            if close < 0 or not _NAME_RE.fullmatch(text, pos + 3, close):
                fail("a group name is lowercase snake_case: a-z, 0-9 and _, up to 32 characters", start)
            name = text[pos + 3 : close]
            if name in groups:
                fail(f"the group name {name!r} is used twice", start)
            groups.append(name)
            if len(groups) > MAX_GROUPS:
                fail(f"at most {MAX_GROUPS} named groups", start)
            pos = close + 1
        elif text.startswith(("?=", "?!", "?<=", "?<!"), pos):
            fail("look-arounds are not allowed", start)
        elif text.startswith("?#", pos):
            fail("comments are not allowed", start)
        elif text.startswith(("?P=", "?("), pos):
            fail("back-references and conditionals are not allowed", start)
        elif text.startswith("?", pos):
            fail("only (?:...) and (?P<name>...) groups are allowed (no flags or atomic groups)", start)
        else:
            fail("an unnamed capture group; use (?:...) or (?P<name>...)", start)
        body = sequence(depth)
        if pos >= n:
            fail("a missing ')'", start)
        pos += 1  # the ")"
        if not body:
            fail("an empty group", start)
        bounds = repeat()
        if bounds is None:
            return ("group", name, tuple(body), start)
        if bounds != (0, 1) or text[pos - 1] != "?":
            fail("the only repeat of a group is '?'; repeat single characters or classes instead", start)
        optional += 1
        if optional > MAX_OPTIONAL_GROUPS:
            fail(f"at most {MAX_OPTIONAL_GROUPS} optional groups", start)
        return ("opt", name, tuple(body), start)

    items = sequence(0)
    if not groups:
        raise ExtractorError("the regex needs at least one named group (?P<name>...)", None)
    if not items:
        raise ExtractorError("the regex is empty")
    return Parsed(anchored, tuple(items), tuple(groups))


# --------------------------------------------------------------------------- determinism and cost


def nullable(items: tuple[Item, ...]) -> bool:
    for item in items:
        kind = item[0]
        if kind == "set" or (kind == "rep" and item[1] > 0):
            return False
        if kind == "group" and not nullable(item[2]):
            return False
    return True


def _show(cp: int) -> str:
    ch = chr(cp)
    return repr(ch) if ch.isprintable() else f"U+{cp:04X}"


def check_determinism(parsed: Parsed) -> None:
    """Raise ExtractorError when a choice of the pattern is not decided by the next character."""

    def seq(items: tuple[Item, ...], follow: CharSet, in_opt: bool) -> CharSet:
        first = follow
        for item in reversed(items):
            first = node(item, first, in_opt)
        return first

    def node(item: Item, follow: CharSet, in_opt: bool) -> CharSet:
        kind = item[0]
        if kind == "set":
            return item[1]
        if kind == "rep":
            _, lo, hi, cs, at = item
            if hi is None or lo < hi:
                cp = _overlap(cs, follow)
                if cp is not None:
                    raise ExtractorError(
                        f"this repeat can also match what follows it (for example {_show(cp)}); make the next"
                        " part start with a character the repeat cannot match (e.g. [^\"]* before '\"')",
                        at,
                    )
            return _union(cs, follow) if lo == 0 else cs
        if kind == "group":
            return seq(item[2], follow, in_opt)
        _, _name, body, at = item  # "opt"
        if in_opt:
            raise ExtractorError("optional groups may not be nested", at)
        if nullable(body):
            raise ExtractorError("an optional group must not be able to match the empty text", at)
        inner = seq(body, EMPTY, True)
        cp = _overlap(inner, follow)
        if cp is not None:
            raise ExtractorError(
                f"this optional group can start like what follows it (for example {_show(cp)})", at
            )
        seq(body, follow, True)
        return _union(inner, follow)

    if nullable(parsed.items):
        raise ExtractorError(
            "the regex can match an empty text, so it would match at the start of any record"
        )
    seq(parsed.items, EMPTY, False)


@dataclass(frozen=True, slots=True)
class Cost:
    atoms: int  # k
    optional: int  # q
    reach: int  # g
    longest: int | None  # M (None = unbounded)
    weight: int = 1  # steps one test of the costliest atom counts for (see :func:`set_weight`)

    def steps(self, n: int, anchored: bool) -> int:
        """Bound of the character tests of one search (or one anchored match) on ``n`` characters,
        each counted with the weight of the costliest atom."""
        k, q, g, m = self.atoms, self.optional, self.reach, self.longest
        if anchored:
            reach = n if m is None else min(n, m)
            return self.weight * ((g + 1) * (q + 1) * (reach + k) + q * k)
        if m is None or m >= n:
            total = n * (n + 1) // 2
        else:
            total = m * (m + 1) // 2 + m * (n - m)
        return self.weight * ((g + 1) * (q + 1) * (total + k * (n + 1)) + q * k * (n + 1))


def _fail_tests(items: tuple[Item, ...], after: int) -> int:
    """Character tests that ``items`` followed by a continuation costing ``after`` make at a character that
    none of them can take (each nullable atom is tried and skipped; the first one that cannot be skipped
    ends it; an optional group tries its body, which stops inside, then is skipped)."""
    cur = after
    for item in reversed(items):
        kind = item[0]
        if kind == "set":
            cur = 1
        elif kind == "rep":
            cur = 1 + (cur if item[1] == 0 else 0)
        elif kind == "group":
            cur = _fail_tests(item[2], cur)
        else:
            cur = _fail_tests(item[2], 0) + cur
    return cur


def _reach(items: tuple[Item, ...], after: int) -> int:
    """Most tests a give-back of a variable repeat of ``items`` costs (what follows it fails at once)."""
    best = 0
    cur = after
    for item in reversed(items):
        kind = item[0]
        if kind == "set":
            cur = 1
        elif kind == "rep":
            _, lo, hi, _cs, _at = item
            if hi is None or lo < hi:
                best = max(best, cur)
            cur = 1 + (cur if lo == 0 else 0)
        else:
            best = max(best, _reach(item[2], cur))  # inside, what follows the group follows its end
            cur = _fail_tests(item[2], cur) if kind == "group" else _fail_tests(item[2], 0) + cur
    return best


def _measure(items: tuple[Item, ...]) -> tuple[int, int | None, int]:
    """(atoms, longest match or None, optional groups) of ``items``."""
    atoms, longest, optional = 0, 0, 0
    for item in items:
        kind = item[0]
        if kind == "set":
            atoms += 1
            longest = None if longest is None else longest + 1
        elif kind == "rep":
            atoms += 1
            hi = item[2]
            longest = None if longest is None or hi is None else longest + hi
        else:
            a, m, q = _measure(item[2])
            atoms += a
            optional += q + (kind == "opt")
            longest = None if longest is None or m is None else longest + m
    return atoms, longest, optional


ASTRAL_RANGES_PER_STEP = 4


def set_weight(cs: CharSet) -> int:
    """Steps one test of the set costs. ``re`` tests a class in constant time below U+10000 (bitmaps),
    but the ranges above U+FFFF of the emitted class one by one: measured, 32 of them cost ~7 times a
    plain test (73 ms per search at the window of weight 1, against ~10 ms). Each started group of
    :data:`ASTRAL_RANGES_PER_STEP` such ranges counts one more step."""
    if (len(cs) == 1 and cs[0][0] == cs[0][1]) or cs == DOT:
        return 1
    comp = _complement(cs)
    emitted = comp if 0 < len(comp) < len(cs) else cs  # the form :func:`_emit_set` writes
    astral = sum(1 for _lo, hi in emitted if hi > 0xFFFF)
    return 1 + -(-astral // ASTRAL_RANGES_PER_STEP)


def _weight(items: tuple[Item, ...]) -> int:
    best = 1
    for item in items:
        if item[0] == "set":
            best = max(best, set_weight(item[1]))
        elif item[0] == "rep":
            best = max(best, set_weight(item[3]))
        else:
            best = max(best, _weight(item[2]))
    return best


def cost_of(parsed: Parsed) -> Cost:
    atoms, longest, optional = _measure(parsed.items)
    return Cost(atoms, optional, _reach(parsed.items, 0), longest, _weight(parsed.items))


def window_for(cost: Cost, anchored: bool, limit: int, max_steps: int = MAX_STEPS) -> int:
    """Largest n <= limit with cost.steps(n) <= max_steps (steps grow with n), or -1."""
    if cost.steps(0, anchored) > max_steps:
        return -1
    lo, hi = 0, limit
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if cost.steps(mid, anchored) <= max_steps:
            lo = mid
        else:
            hi = mid - 1
    return lo


# --------------------------------------------------------------------------- emission


_META = frozenset(".^$*+?{}[]\\|()")
_CLASS_META = frozenset("\\]^[-&~|")


def _esc(cp: int, meta: frozenset[str]) -> str:
    ch = chr(cp)
    if ch in meta:
        return "\\" + ch
    if ch == " " or (ch.isprintable() and not (0xD800 <= cp <= 0xDFFF)):
        return ch
    if cp < 0x100:
        return f"\\x{cp:02x}"
    if cp < 0x10000:
        return f"\\u{cp:04x}"
    return f"\\U{cp:08x}"


def _emit_set(cs: CharSet) -> str:
    if len(cs) == 1 and cs[0][0] == cs[0][1]:
        return _esc(cs[0][0], _META)
    if cs == DOT:
        return "."
    comp = _complement(cs)
    negated = 0 < len(comp) < len(cs)
    parts: list[str] = []
    for lo, hi in comp if negated else cs:
        if lo == hi:
            parts.append(_esc(lo, _CLASS_META))
        elif hi == lo + 1:
            parts.append(_esc(lo, _CLASS_META) + _esc(hi, _CLASS_META))
        else:
            parts.append(_esc(lo, _CLASS_META) + "-" + _esc(hi, _CLASS_META))
    return ("[^" if negated else "[") + "".join(parts) + "]"


def _emit_rep(lo: int, hi: int | None) -> str:
    if hi is None:
        return "*" if lo == 0 else "+" if lo == 1 else f"{{{lo},}}"
    if (lo, hi) == (0, 1):
        return "?"
    return f"{{{lo}}}" if lo == hi else f"{{{lo},{hi}}}"


def emit_items(items: tuple[Item, ...]) -> str:
    out: list[str] = []
    for item in items:
        kind = item[0]
        if kind == "set":
            out.append(_emit_set(item[1]))
        elif kind == "rep":
            out.append(_emit_set(item[3]) + _emit_rep(item[1], item[2]))
        else:
            name = item[1]
            body = emit_items(item[2])
            if kind == "group" and name is None:
                out.append(body)  # a plain non-capturing group changes nothing
            else:
                head = f"(?P<{name}>" if name is not None else "(?:"
                out.append(head + body + (")?" if kind == "opt" else ")"))
    return "".join(out)


def emit(parsed: Parsed) -> str:
    """The pattern written back from the parsed form (classes spelled out), without the leading ``^``."""
    return emit_items(parsed.items)


# --------------------------------------------------------------------------- the extractor


def _converter(kind: str) -> Callable[[str], Any] | None:
    if kind == "str":
        return None
    from .templates import _CONVERTERS  # the field conversions of the templates (promotion keeps them)

    return _CONVERTERS[kind]


@dataclass(frozen=True, slots=True)
class Extractor:
    source: str  # the text the user typed
    pattern: str  # the emitted pattern (without '^'); what re compiles
    regex: re.Pattern[str]
    anchored: bool
    groups: tuple[str, ...]
    types: tuple[str, ...]  # per group
    target: str  # msg | text
    window: int  # characters of a record the regex looks at
    steps: int  # bound of one search on ``window`` characters
    cost: Cost
    converters: tuple[Callable[[str], Any] | None, ...]

    def run(self, subject: str) -> dict[str, Any] | None:
        """Values of the groups (converted) for ``subject`` (at most ``window`` characters), or None.
        A group that took no part is None; an empty capture stays ""."""
        m = self.regex.match(subject) if self.anchored else self.regex.search(subject)
        if m is None:
            return None
        out: dict[str, Any] = {}
        for name, conv in zip(self.groups, self.converters, strict=True):
            v = m.group(name)
            out[name] = conv(v) if v and conv is not None else v
        return out

    def subject(self, msg_part: str | None, cont_part: str | None) -> tuple[str, bool]:
        """The text the regex sees and whether the record was longer than the window.

        ``msg_part``/``cont_part`` are what :func:`parts` (or the same SQL) gives: for ``msg`` the first line
        without spaces and TABs at both ends, for ``text`` the raw first line and the continuation lines,
        each cut to ``window + 1`` characters."""
        w = self.window
        msg_part = msg_part or ""
        if self.target == "msg":
            text = msg_part
        else:
            text = msg_part if cont_part is None else msg_part + "\n" + cont_part
        return text[:w], len(text) > w

    def parts(self, msg: str, cont: str | None) -> tuple[str, str | None]:
        """The parts of a whole record that the SQL of ``store/extract.py`` reads (reference version)."""
        w = self.window + 1
        if self.target == "msg":
            return msg.strip(" \t")[:w], None
        return msg[:w], None if cont is None else cont[:w]

    def apply(self, msg: str, cont: str | None = None) -> tuple[dict[str, Any] | None, bool]:
        subject, cut = self.subject(*self.parts(msg, cont))
        return self.run(subject), cut


def compile_extractor(
    source: Any, *, target: str = "msg", types: Mapping[str, str] | None = None, max_steps: int = MAX_STEPS
) -> Extractor:
    """Validate ``source`` (grammar, determinism, cost) and compile it for ``target``.

    ``types`` maps group names to a conversion (``str`` by default; ``int``, ``hex``, ``guid``, ``float``
    as in the templates). Raises ExtractorError (``code``: ``bad_regex``, ``regex_too_costly``,
    ``bad_target``, ``bad_types``)."""
    if target not in TARGETS:
        raise ExtractorError("target must be 'msg' or 'text'", None, "bad_target")
    if not isinstance(source, str):
        raise ExtractorError("the regex must be a text")
    parsed = parse(source)
    check_determinism(parsed)
    types = dict(types or {})
    for name, kind in types.items():
        if name not in parsed.groups:
            raise ExtractorError(f"types names {str(name)[:40]!r}, which is not a group", None, "bad_types")
        if kind not in TYPES:
            raise ExtractorError(f"the type of {name!r} must be one of {', '.join(TYPES)}", None, "bad_types")
    cost = cost_of(parsed)
    window = window_for(cost, parsed.anchored, TARGET_WINDOWS[target], max_steps)
    if window < MIN_WINDOW:
        raise ExtractorError(
            "the regex could look at fewer than"
            f" {MIN_WINDOW} characters of a record within the step budget; bound the repeats ({{1,64}}"
            " instead of + or *), start with '^' or use fewer optional groups",
            None,
            "regex_too_costly",
        )
    pattern = emit(parsed)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # nothing ambiguous may reach re (the emission avoids it)
            rx = re.compile(pattern)
    except (re.error, Warning, RecursionError, OverflowError) as exc:  # pragma: no cover - by construction
        raise ExtractorError(f"the regex does not compile: {exc}") from None
    kinds = tuple(types.get(g, "str") for g in parsed.groups)
    return Extractor(
        source=source,
        pattern=pattern,
        regex=rx,
        anchored=parsed.anchored,
        groups=parsed.groups,
        types=kinds,
        target=target,
        window=window,
        steps=cost.steps(window, parsed.anchored),
        cost=cost,
        converters=tuple(_converter(k) for k in kinds),
    )


def suggest_type(raw_values: list[str]) -> str:
    """The narrowest conversion every non-empty value takes (``str`` when there is none)."""
    vals = [v for v in raw_values if v]
    if not vals:
        return "str"
    if all(_INT_RE.fullmatch(v) for v in vals):
        return "int"
    if all(_GUID_RE.fullmatch(v) for v in vals):
        return "guid"
    if all(_HEX_RE.fullmatch(v) for v in vals):
        return "hex"
    if all(_is_float(v) for v in vals):
        return "float"
    return "str"


def _is_float(v: str) -> bool:
    try:
        return math.isfinite(float(v))
    except ValueError:
        return False
