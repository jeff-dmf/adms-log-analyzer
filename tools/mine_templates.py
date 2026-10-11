"""Draft adapter templates from the records of an indexed case that no template matches.

Development tool (not part of the ``dmslog`` package). It reads the ``unknown_sig`` table of a
case database (records without a template, grouped by normalized signature; ARQUITETURA 6.7) and
proposes a SKETCH of a template file (TOML, schema 1) for one family: one ``[[template]]`` per
signature, with the record count, up to 3 real examples and a regex derived from the signature
(placeholders ``<ts> <guid> <url> <path> <ip> <hex> <s> <n>`` become named groups with safe
character classes). The sketch is a starting point, not a finished template: titles, classes,
severities, field names, keys and counter-examples are for the maintainer to fill in
(docs/FAMILIA-NOVA.md, steps 3 and 4).

Usage (from a checkout; the case must already be indexed with ``dmslog ingest``)::

    PYTHONPATH=src python tools/mine_templates.py --case my-case --family WIDGET --out widget.toml
    PYTHONPATH=src python tools/mine_templates.py --case-db path/to/case.sqlite --family WIDGET \\
        --min-count 5 --limit 30

The case is opened read-only; nothing is written except the ``--out`` file (default: stdout).
Without ``--family`` the tool works only when a single family has unknown records; otherwise it
lists the families and asks for one (a template file holds one family).

PRIVACY: the examples are real log messages and may hold customer or host data. Anonymize them
before committing anything produced here (the generated file starts with the same warning).
Every draft template is checked before it is written: it must load with the catalog loader
(including the regex safety validator) and its regex must match all of its own examples. A
signature that cannot be turned into a safe regex is kept as a commented-out block.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sqlite3
import sys
import unicodedata
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent.parent
if importlib.util.find_spec("dmslog") is None:  # run from a checkout without installing
    sys.path.insert(0, str(ROOT / "src"))

from dmslog.ingest.pipeline import case_db_path  # noqa: E402
from dmslog.parse.signature import MAX_INPUT_CHARS, MAX_SIGNATURE_CHARS, signature  # noqa: E402
from dmslog.parse.templates import ADAPTERS_PACKAGE, MAX_MATCH_CHARS, TemplateError, load_texts  # noqa: E402
from dmslog.paths import resolve_data_dir  # noqa: E402

DEFAULT_MIN_COUNT = 2
DEFAULT_LIMIT = 50
MAX_EXAMPLES = 3
MAX_SCAN_ROWS = 500_000  # unknown records read while looking for extra examples
MAX_GROUPS = 24  # capture groups of one regex (templates.MAX_GROUPS)
MAX_NAME_CHARS = 24
MAX_COMMENT_CHARS = 400  # longest value interpolated into one comment line
COMMON = "_common"
WINDOWS = (None, 720, 320, 128)  # None = the default window of the loader (4096)
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

WARNING = (
    "Examples are REAL messages from the ERS and may hold customer or host data. Anonymize them "
    "before committing this file."
)

_MARKER = re.compile(r"<(ts|guid|url|path|ip|hex|s|n)>")
_WORD = re.compile(r"([A-Za-z][A-Za-z0-9_]*)\W*$")
_SPECIAL = frozenset("\\.^$*+?{}[]|()")
_FIELD_TYPES = {"n": "int", "guid": "guid", "hex": "hex"}

# marker -> regex piece. "n" has a strict and a loose form; the strict one is tried first.
_PIECES = {
    "ts": r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d[\d.,:+Z-]*",
    "guid": r"\{?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}?",
    "url": r"\S+",
    "path": "[^\\s\"'<>|]+",
    "ip": r"[\d.]+(?::\d{1,5})?",
    "hex": r"[0-9a-fA-FxX]+",
    "s": "(?:'[^']*'|\"[^\"]*\")",
}
_N_STRICT = r"\d+(?:\.\d+)?"
_N_LOOSE = r"[\d.]+"


MAX_WARNINGS_SHOWN = 10


class Warnings:
    """Bounded warning collector: an exact total plus a sample of the first distinct messages."""

    def __init__(self, max_sample: int = MAX_WARNINGS_SHOWN) -> None:
        self.total = 0
        self.sample: list[str] = []
        self._max_sample = max_sample

    def add(self, message: str) -> None:
        self.total += 1
        if len(self.sample) < self._max_sample and message not in self.sample:
            self.sample.append(message)

    def __bool__(self) -> bool:
        return self.total > 0


@dataclass
class Group:
    """One signature of one family, with its examples."""

    family: str
    signature: str
    count: int
    first_entry_id: int
    examples: list[str] = field(default_factory=list)
    first_has_cont: bool = False


@dataclass
class Draft:
    """The sketch of one template: either a usable body or the reason it was skipped."""

    group: Group
    code: str
    body: str = ""
    skipped: str = ""  # detail for the ``# SKIPPED`` comment in the output file (may quote examples)
    skip_code: str = ""  # fixed, generic reason: the only thing the stderr summary may show
    examples: list[str] = field(default_factory=list)


REASON_NO_EXAMPLE = "no example record found"
REASON_TOO_LONG = "example too long"
REASON_NO_MATCH = "no regex matched its examples"
REASON_CATALOG = "catalog rejected the draft"
REASON_NO_REGEX = "no safe regex found"
REASON_NOT_CLASSIFIED = "the catalog does not classify its example"


class MineError(Exception):
    """The draft cannot be produced; the message says why (the CLI exits with EXIT_FAILED)."""


# --- TOML and regex text helpers ---------------------------------------------------------------


def toml_str(text: str) -> str:
    """A TOML basic string (double quotes); every control character is escaped."""
    out = ['"']
    for ch in text:
        code = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif code < 0x20 or code == 0x7F:
            out.append(f"\\u{code:04x}")
        elif 0xD800 <= code <= 0xDFFF:
            out.append("\\ufffd")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def comment_text(text: str, limit: int = MAX_COMMENT_CHARS) -> str:
    """Make a value safe to put in a ``#`` comment line: always one line, no control characters.

    Line breaks, control, format, surrogate and unassigned characters and U+2028/2029 become
    visible escapes (``\\n``, ``\\u0000``...), so a value can never end the comment and start TOML.
    """
    out: list[str] = []
    for ch in text:
        if ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif unicodedata.category(ch)[0] == "C" or unicodedata.category(ch) in ("Zl", "Zp"):
            out.append(f"\\u{ord(ch):04x}" if ord(ch) <= 0xFFFF else f"\\U{ord(ch):08x}")
        else:
            out.append(ch)
    result = "".join(out)
    if len(result) > limit:
        result = f"{result[:limit]}...[{len(result) - limit} more chars]"
    return result


def safe_text(value: object, limit: int = MAX_COMMENT_CHARS) -> str:
    """Make any value safe to print to the terminal (stderr): one line, no control characters."""
    return comment_text(str(value), limit)


def toml_regex(text: str) -> str:
    """A TOML literal string for a regex when that is safe, else a basic string."""
    unsafe = any(ord(c) < 0x20 or ord(c) == 0x7F for c in text) or "'''" in text or text.endswith("'")
    return toml_str(text) if unsafe else f"'''{text}'''"


def escape_literal(text: str) -> str:
    return "".join("\\" + c if c in _SPECIAL else c for c in text)


def clean_signature(sig: str) -> tuple[str, bool]:
    """Drop a marker cut in half by the signature limit; report whether the tail was cut."""
    cut = len(sig) >= MAX_SIGNATURE_CHARS - 1
    if cut:
        sig = re.sub(r"<[a-z]*$", "", sig).rstrip()
    return sig, cut


def field_names(sig: str) -> list[str]:
    """One snake_case group name per marker: the word before it, else the marker plus an index."""
    names: list[str] = []
    used: set[str] = set()
    pos = 0
    for index, m in enumerate(_MARKER.finditer(sig), 1):
        before = _WORD.search(_MARKER.sub(" ", sig[pos : m.start()]))
        pos = m.end()
        base = before.group(1).lower()[:MAX_NAME_CHARS] if before else ""
        if not base or base in used:
            base = f"{m.group(1)}_{index}"
        used.add(base)
        names.append(base)
    return names


def build_regex(sig: str, *, cut: bool, loose_n: bool, flexible_space: bool) -> tuple[str, dict[str, str]]:
    """Regex and typed fields for a signature. Markers past ``MAX_GROUPS`` stay anonymous."""
    names = field_names(sig)
    parts = ["^"]
    fields: dict[str, str] = {}
    pos = 0
    for index, m in enumerate(_MARKER.finditer(sig)):
        parts.append(_literal(sig[pos : m.start()], flexible_space))
        pos = m.end()
        kind = m.group(1)
        piece = (_N_LOOSE if loose_n else _N_STRICT) if kind == "n" else _PIECES[kind]
        if index < MAX_GROUPS:
            parts.append(f"(?P<{names[index]}>{piece})")
            if kind in _FIELD_TYPES:
                fields[names[index]] = _FIELD_TYPES[kind]
        else:
            parts.append(f"(?:{piece})")
    parts.append(_literal(sig[pos:], flexible_space))
    parts.append(".*" if cut else "$")
    return "".join(parts), fields


def _literal(text: str, flexible_space: bool) -> str:
    escaped = escape_literal(text)
    return re.sub(r" +", r"\\s+", escaped) if flexible_space else escaped


# --- draft building ----------------------------------------------------------------------------


def _template_toml(
    code: str, group: Group, examples: list[str], regex: str | None, text: str | None,
    fields: dict[str, str], window: int | None,
) -> str:  # fmt: skip
    lines = [
        "[[template]]",
        f"code = {toml_str(code)}",
        'class = "other"   # TODO: start | end | req_resp | validation | error | statistics | ...',
        "sev = 4          # TODO: 1 critical, 2 error, 3 warning, 4 info, 5 noise",
        'title = "TODO"',
    ]
    if text is not None:
        lines.append(f"text = {toml_str(text)}")
    else:
        lines.append(f"regex = {toml_regex(regex or '')}")
        if window is not None:
            lines.append(f"window = {window}")
        if fields:
            body = ", ".join(f'{name} = "{kind}"' for name, kind in fields.items())
            lines.append(f"fields = {{ {body} }}")
    lines.append("examples = [")
    lines.extend(f"    {toml_str(e)}," for e in examples)
    lines.append("]")
    return "\n".join(lines) + "\n"


def _family_file(family: str, templates: str) -> str:
    return f'schema = 1\nfamily = {toml_str(family)}\nextends = ["_common"]\n\n{templates}'


def load_draft(text: str, *, check_examples: bool = False):  # noqa: ANN201
    """Load a generated file with the catalog loader, next to the packaged ``_common`` library."""
    library = resources.files(ADAPTERS_PACKAGE).joinpath(f"{COMMON}.toml").read_bytes()
    return load_texts({f"{COMMON}.toml": library, "draft.toml": text}, check_examples=check_examples)


def _usable(family: str, body: str, examples: list[str]) -> tuple[str, str]:
    """``("", "")`` when the template loads and matches its examples, else ``(generic code, detail)``."""
    try:  # without ``extends``: the per-template check does not need the library
        catalog = load_texts({"draft.toml": f"schema = 1\nfamily = {toml_str(family)}\n\n{body}"})
    except TemplateError as exc:
        return REASON_CATALOG, str(exc)
    (template,) = catalog.families[family].templates
    for example in examples:
        if len(example.strip()) > min(template.window, MAX_MATCH_CHARS):  # the loader skips longer ones
            return REASON_TOO_LONG, f"example is longer than the match window ({template.window} characters)"
        if template.text is not None:
            hit = template.text == example.strip()
        else:
            hit = template.regex.match(example.strip()) is not None  # type: ignore[union-attr]
        if not hit:
            return REASON_NO_MATCH, f"does not match its example {example!r}"
    return "", ""


def draft_template(code: str, group: Group) -> Draft:
    """Sketch one template: the first regex variant that loads and matches every example."""
    sig, cut = clean_signature(group.signature)
    cut = cut or any(len(e) > MAX_INPUT_CHARS for e in group.examples)
    draft = Draft(group, code, examples=list(group.examples))
    if not group.examples:
        draft.skipped = draft.skip_code = REASON_NO_EXAMPLE
        return draft
    why, detail = "", ""
    if not _MARKER.search(sig) and not cut and len(set(group.examples)) == 1:
        text = group.examples[0].strip()
        body = _template_toml(code, group, group.examples, None, text, {}, None)
        why, detail = _usable(group.family, body, group.examples)
        if not why:
            draft.body = body
            return draft
    for loose_n, flexible_space in ((False, False), (True, False), (True, True)):
        regex, fields = build_regex(sig, cut=cut, loose_n=loose_n, flexible_space=flexible_space)
        for window in WINDOWS:
            body = _template_toml(code, group, group.examples, regex, None, fields, window)
            why, detail = _usable(group.family, body, group.examples)
            if not why:
                draft.body = body
                return draft
    draft.skip_code = why or REASON_NO_REGEX
    draft.skipped = detail or REASON_NO_REGEX
    return draft


# --- reading the case --------------------------------------------------------------------------


def open_case(path: Path) -> sqlite3.Connection:
    """Open a case database read-only; ValueError when it is missing or not a dmslog case."""
    if not path.is_file():
        raise ValueError(f"case database not found: {safe_text(path)}")
    for suffix in ("-wal", "-shm"):
        side = path.with_name(path.name + suffix)
        if side.is_file() and side.stat().st_size > 0:
            raise ValueError(
                f"the case has a pending write-ahead log ({safe_text(side.name)}); close the "
                "viewer/server or run "
                "dmslog on it first so the log is checkpointed, then retry (this tool never writes "
                "next to the case)"
            )
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?immutable=1", uri=True)
    try:
        conn.execute("SELECT family, signature, n, first_entry_id FROM unknown_sig LIMIT 1").fetchall()
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise ValueError(f"not a dmslog case database: {safe_text(path)} ({safe_text(exc)})") from None
    return conn


def family_totals(conn: sqlite3.Connection) -> list[tuple[str, int, int]]:
    """``(family, signatures, records)`` for every family with unknown records."""
    rows = conn.execute(
        "SELECT family, count(*), sum(n) FROM unknown_sig WHERE typeof(family) = 'text' "
        "GROUP BY family ORDER BY sum(n) DESC, family"
    ).fetchall()
    return [(r[0], r[1], r[2] if isinstance(r[2], int) else 0) for r in rows]


def _is_int(value: object) -> bool:
    """True for a real integer (SQLite columns are dynamically typed: bool/float/text/bytes are not)."""
    return isinstance(value, int) and not isinstance(value, bool)


def read_groups(
    conn: sqlite3.Connection, family: str, min_count: int, limit: int, warnings: Warnings | None = None
) -> list[Group]:
    """Up to ``limit`` valid signature groups of a family, most frequent first.

    Rows are validated before the limit and ``min_count`` apply, so an invalid row never uses up a
    slot. Rows with a wrong column type (or ``n`` < 1) are rejected; ``warnings`` only gets the
    column names, never the values.
    """
    cursor = conn.execute(
        "SELECT family, signature, n, first_entry_id FROM unknown_sig WHERE family = ? "
        "ORDER BY n DESC, signature",
        (family,),
    )
    groups: list[Group] = []
    for r in cursor:
        bad = [
            name
            for name, ok in (
                ("family", isinstance(r[0], str)),
                ("signature", isinstance(r[1], str)),
                ("n", _is_int(r[2]) and r[2] >= 1),
                ("first_entry_id", _is_int(r[3])),
            )
            if not ok
        ]
        if bad:
            if warnings is not None:
                warnings.add(f"unknown_sig row rejected: invalid value in column {', '.join(bad)}")
            continue
        if r[2] >= min_count and len(groups) < limit:
            groups.append(Group(r[0], r[1], r[2], r[3]))
    for group in groups:
        row = conn.execute("SELECT msg, flags FROM entry WHERE id = ?", (group.first_entry_id,)).fetchone()
        if row is None:
            continue
        bad = [name for name, ok in (("msg", isinstance(row[0], str)), ("flags", _is_int(row[1]))) if not ok]
        if bad:
            if warnings is not None:
                warnings.add(f"entry row rejected: invalid value in column {', '.join(bad)}")
            continue
        group.examples.append(row[0].strip())
        group.first_has_cont = bool(row[1] & 2)
    _fill_examples(conn, groups)
    return groups


def _unknown_messages(conn: sqlite3.Connection, family: str) -> Iterator[str]:
    cursor = conn.execute(
        "SELECT e.msg FROM entry e JOIN source s ON s.id = e.source_id JOIN stream st ON st.id = s.stream_id "
        "WHERE e.tpl_id = 0 AND st.family = ? ORDER BY e.id LIMIT ?",
        (family, MAX_SCAN_ROWS),
    )
    for (msg,) in cursor:
        if isinstance(msg, str):  # the column is dynamically typed
            yield msg


def _fill_examples(conn: sqlite3.Connection, groups: list[Group]) -> None:
    """Add distinct examples (up to ``MAX_EXAMPLES``) by scanning the family's unknown records."""
    wanted = {g.signature: g for g in groups if g.count > 1 and len(g.examples) < MAX_EXAMPLES}
    if not wanted or not groups:
        return
    for msg in _unknown_messages(conn, groups[0].family):
        text = msg.strip()
        group = wanted.get(signature(text))
        if group is None or text in group.examples:
            continue
        group.examples.append(text)
        if len(group.examples) >= MAX_EXAMPLES:
            del wanted[group.signature]
            if not wanted:
                return


# --- output ------------------------------------------------------------------------------------


def make_code(family: str) -> str:
    prefix = re.sub(r"[^A-Z0-9]", "", family.upper())[:20] or "X"
    return prefix if prefix[0].isalpha() else "X" + prefix


def _block(d: Draft) -> str:
    note = f"# {int(d.group.count)} records, first entry id {int(d.group.first_entry_id)}"
    if d.group.first_has_cont:
        note += ", has continuation lines"
    head = f"{note}\n# signature: {comment_text(d.group.signature)}\n"
    if d.skipped:
        commented = [f"# SKIPPED {d.code}: {comment_text(d.skipped)}"]
        commented += [f"# example: {comment_text(toml_str(e))}" for e in d.examples]
        return head + "\n".join(commented) + "\n"
    return head + d.body


def _assemble(family: str, drafts: list[Draft]) -> str:
    return _family_file(family, "\n".join(_block(d) for d in drafts))


def render(family: str, groups: Iterable[Group], source: str, shown: int, total: int) -> str:
    """Same as :func:`render_drafts`, keeping only the text."""
    return render_drafts(family, groups, source, shown, total)[0]


def render_drafts(
    family: str, groups: Iterable[Group], source: str, shown: int, total: int
) -> tuple[str, list[Draft]]:
    """The TOML text and the drafts (active ones have a body, the others a ``skipped`` reason).

    Header, one block per signature, skipped ones as comments.

    The result always loads with ``check_examples=True`` next to ``_common``: templates that
    conflict with each other are commented out (lowest record count first) until it does;
    MineError when that is not possible.
    """
    prefix = make_code(family)
    drafts = [draft_template(f"{prefix}-{i:03d}", g) for i, g in enumerate(groups, 1)]
    out = [
        f"# DRAFT templates for family {comment_text(family)}, mined from {comment_text(source)}.",
        f"# WARNING: {WARNING}",
        f"# {int(shown)} of {int(total)} signatures, most frequent first. Starting point only: fill",
        "# class, sev, title, field names, keys and counter_examples, then merge into src/dmslog/adapters/.",
        "# Validate with load_catalog(check_examples=True) (docs/FAMILIA-NOVA.md, step 4).",
        "",
    ]
    while True:
        text = _assemble(family, drafts)
        loser = _next_conflict(family, text, drafts)
        if loser is None:
            return "\n".join(out) + "\n" + text, drafts
        draft, reason = loser
        draft.body, draft.skipped, draft.skip_code = "", reason, reason


def _next_conflict(family: str, text: str, drafts: list[Draft]) -> tuple[Draft, str] | None:
    """None when the file passes every catalog check, else the draft to comment out and why."""
    try:
        catalog = load_draft(text)
    except TemplateError as exc:
        raise MineError(f"the draft does not load: {safe_text(exc, 300)}") from None
    problems = catalog.check_examples()
    if not problems:
        return None
    index = {d.code: i for i, d in enumerate(drafts) if d.body}
    candidates: list[tuple[int, int, Draft, str]] = []  # (count, -position): lowest count first
    for code, other in _conflicts(catalog, family, set(index)):
        pick = code
        if other and other in index and drafts[index[other]].group.count < drafts[index[code]].group.count:
            pick = other
        elif other and other in index and drafts[index[other]].group.count == drafts[index[code]].group.count:
            pick = max(code, other, key=index.__getitem__)  # tie: the later (less frequent) one
        draft = drafts[index[pick]]
        why = other if pick == code else code
        reason = f"conflicts with {safe_text(why)}" if why else REASON_NOT_CLASSIFIED
        candidates.append((draft.group.count, -index[pick], draft, reason))
    if not candidates:
        raise MineError(f"the draft fails the catalog checks: {safe_text('; '.join(problems), 300)}")
    _, _, draft, reason = min(candidates, key=lambda c: (c[0], c[1]))
    return draft, reason


def _conflicts(catalog, family: str, codes: set[str]) -> list[tuple[str, str]]:  # noqa: ANN001
    """``(code, other)`` pairs from the catalog's own example checks, for the given draft codes."""
    found: list[tuple[str, str]] = []
    templates = catalog.families[family].templates
    for t in templates:
        if t.code not in codes:
            continue
        streams = (False, True) if t.stream == "any" else ((t.stream == "sensitive"),)
        for sensitive in streams:
            tset = catalog.for_family(family, sensitive=sensitive)
            for ex in t.examples:
                tm = tset.classify_text(ex.msg, ex.cont)
                got = tm.code if tm is not None else ""
                if got != t.code:
                    found.append((t.code, got))
        for ex in t.examples:
            s = ex.msg.strip()
            for other in templates:
                if other is t or {t.stream, other.stream} == {"normal", "sensitive"}:
                    continue
                if len(s) > other.window:
                    continue
                hit = other.text == s if other.text is not None else other.regex.match(s) is not None
                if hit:
                    found.append((t.code, other.code))
    return list(dict.fromkeys(found))


# --- CLI ---------------------------------------------------------------------------------------


class _SafeParser(argparse.ArgumentParser):
    """Argparse whose usage errors never echo raw argument values (terminal escapes)."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {safe_text(message)}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = _SafeParser(
        prog="mine_templates.py",
        description=(
            "Propose a draft template file (TOML) for one family from the records of an indexed "
            "case that no template matches (table unknown_sig). Development tool: the examples are "
            "real log messages, so anonymize them before committing."
        ),
    )
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument("--case-db", metavar="PATH", type=Path, help="path of the case.sqlite file")
    where.add_argument("--case", metavar="NAME", help="case name under <data-dir>/cases/")
    parser.add_argument("--data-dir", metavar="DIR", help="dmslog data folder (default: the user's)")
    parser.add_argument(
        "--family", metavar="CODE", help="family code to mine (required when several have unknowns)"
    )
    parser.add_argument(
        "--min-count", metavar="N", type=int, default=DEFAULT_MIN_COUNT,
        help=f"ignore signatures seen fewer than N times (default {DEFAULT_MIN_COUNT})",
    )  # fmt: skip
    parser.add_argument(
        "--limit", metavar="N", type=int, default=DEFAULT_LIMIT,
        help=f"at most N signatures, most frequent first (default {DEFAULT_LIMIT})",
    )  # fmt: skip
    parser.add_argument("--out", metavar="FILE", type=Path, help="write the TOML here instead of stdout")
    return parser


def summarize(drafts: list[Draft], warnings: Warnings) -> list[str]:
    """Lines for stderr; empty when nothing was skipped or discarded."""
    skipped = [d for d in drafts if not d.body]
    if not skipped and not warnings:
        return []
    lines = [
        f"summary: {len(drafts)} signature groups, {len(drafts) - len(skipped)} active templates, "
        f"{len(skipped)} commented out as SKIPPED"
    ]
    lines += [
        f"  SKIPPED {safe_text(d.code)} ({int(d.group.count)} records): "
        f"{safe_text(d.skip_code or REASON_NO_REGEX)}"
        for d in skipped
    ]
    if warnings:
        lines.append(f"  {warnings.total} row(s) ignored because of invalid data:")
        lines += [f"    {safe_text(w)}" for w in warnings.sample]
        if warnings.total > len(warnings.sample):
            lines.append(f"    ... and {warnings.total - len(warnings.sample)} more")
    return lines


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.min_count < 1 or args.limit < 1:
        print("error: --min-count and --limit must be at least 1", file=sys.stderr)
        return EXIT_USAGE
    try:
        db = args.case_db or case_db_path(resolve_data_dir(args.data_dir), args.case)
        conn = open_case(Path(db))
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"error: {safe_text(exc)}", file=sys.stderr)
        return EXIT_FAILED
    try:
        totals = family_totals(conn)
        family = args.family
        if family is None:
            if len(totals) > 1:
                print("error: several families have unknown records; pass --family:", file=sys.stderr)
                for name, signatures, records in totals:
                    line = f"  {safe_text(name)}: {signatures} signatures, {records} records"
                    print(line, file=sys.stderr)
                return EXIT_USAGE
            family = totals[0][0] if totals else None
        known = {t[0]: t for t in totals}
        if family is None or family not in known:
            which = f" for family {safe_text(family)}" if family else ""
            print(f"error: no unknown records{which}", file=sys.stderr)
            return EXIT_FAILED
        warnings = Warnings()
        groups = read_groups(conn, family, args.min_count, args.limit, warnings)
    except sqlite3.Error as exc:
        print(f"error: cannot read the case: {safe_text(exc)}", file=sys.stderr)
        return EXIT_FAILED
    finally:
        conn.close()
    if not groups:
        for line in summarize([], warnings):
            print(line, file=sys.stderr)
        print(
            f"error: no signature of {safe_text(family)} reaches --min-count {args.min_count}",
            file=sys.stderr,
        )
        return EXIT_FAILED
    try:
        text, drafts = render_drafts(family, groups, Path(db).name, len(groups), known[family][1])
    except MineError as exc:
        print(f"error: {safe_text(exc)}", file=sys.stderr)
        return EXIT_FAILED
    for line in summarize(drafts, warnings):
        print(line, file=sys.stderr)
    if args.out is not None:
        try:
            args.out.write_text(text, encoding="utf-8", newline="\n")
        except OSError as exc:
            print(f"error: cannot write {safe_text(args.out)}: {safe_text(exc)}", file=sys.stderr)
            return EXIT_FAILED
        active = sum(1 for d in drafts if d.body)
        extra = f" ({len(drafts) - active} SKIPPED, commented out)" if active != len(drafts) else ""
        print(f"wrote {active} active draft templates to {safe_text(args.out)}{extra}", file=sys.stderr)
    else:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        sys.stdout.write(text)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(run())
