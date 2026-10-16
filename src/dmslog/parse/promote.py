"""Promote an ad hoc extraction regex to a template definition (ROADMAP 9.8). Nothing is written.

The result is the TOML text of one ``[[template]]`` for ``src/dmslog/adapters/<family>.toml``: code, class
``other``, the regex, the field types and up to three examples with their fields. It is checked by the
catalog loader itself (``load_texts`` with ``check_examples``: grammar and work budget of the catalog,
examples classified with the declared fields), and the outcome is reported, never raised. Why only text:
the catalog is packaged, versioned and reviewed (its regexes go through the attack probes of the test
suite), the ingestion stamps records with its version, and there is no user template store; a template
reaches the users through a change to the repository (ARQUITETURA draft of 9.8).

A template is matched from the start of the first line (``re.match``), so an unanchored regex gets the
text that came before the match in every example as a literal prefix, or ``.*?`` when the examples
differ there (the leftmost match of a search is the first that ``^.*?`` reaches, so the fields agree).
"""

from __future__ import annotations

import functools
import tomllib
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from .. import messages
from .extractor import _META, Extractor, _esc, suggest_type
from .templates import _CODE_RE, _CONVERTERS, _FAMILY_RE, TemplateError, load_catalog, load_texts

FAMILY_RE = _FAMILY_RE
CODE_RE = _CODE_RE
MAX_EXAMPLES = 3
FREE_CODES = range(900, 1000)


class PromoteError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Example:
    id: int
    subject: str  # what the regex saw (first line without spaces and TABs at the ends)
    cut: bool
    family: str | None
    current: str | None  # template code the record has now (None = unknown)


def toml_str(s: str) -> str:
    """A TOML string for ``s``: a literal string when it can be one, else an escaped basic string."""
    if "'" not in s and all(c.isprintable() for c in s):
        return "'" + s + "'"
    out = ['"']
    for c in s:
        cp = ord(c)
        if c in '"\\':
            out.append("\\" + c)
        elif c.isprintable():
            out.append(c)
        else:
            out.append(f"\\u{cp:04x}" if cp < 0x10000 else f"\\U{cp:08x}")
    out.append('"')
    return "".join(out)


def _toml_value(v: Any) -> str:
    if isinstance(v, str):
        return toml_str(v)
    return repr(v)  # int or finite float: the same text in TOML


def _inline(table: dict[str, Any]) -> str:
    return "{ " + ", ".join(f"{k} = {_toml_value(v)}" for k, v in table.items()) + " }" if table else "{}"


@functools.cache
def _catalog():
    return load_catalog()


def free_code(family: str) -> str:
    """``<prefix>-9NN``: the most common code prefix of the family (``USR`` without templates), first free."""
    catalog = _catalog()
    fam = catalog.families.get(family)
    prefixes = Counter(t.code.rsplit("-", 1)[0] for t in fam.templates) if fam is not None else Counter()
    prefix = min(prefixes, key=lambda p: (-prefixes[p], p)) if prefixes else "USR"
    for n in FREE_CODES:
        code = f"{prefix}-{n}"
        if catalog.resolve(code) is None:
            return code
    return f"{prefix}-X"  # pragma: no cover - a family with 100 codes in the 900s


def _surrogate_free(s: str) -> bool:
    return not any(0xD800 <= ord(c) <= 0xDFFF for c in s)


def promote(
    ex: Extractor,
    records: Sequence[Example],
    *,
    family: str | None = None,
    code: str | None = None,
    tick: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """The template text and its check (module docstring). Raises PromoteError (``promote_needs_msg``,
    ``promote_no_match``, ``mixed_families``) when there is nothing sound to build."""
    if ex.target != "msg":
        raise PromoteError("promote_needs_msg")
    matched = []
    skipped_cut = 0
    tick = tick or (lambda: None)  # the caller's deadline: raises when it passed
    for rec in records:
        tick()
        if rec.cut:
            skipped_cut += 1
            continue
        m = ex.regex.match(rec.subject) if ex.anchored else ex.regex.search(rec.subject)
        tick()  # a deadline passed during the search is a timeout, never "no match"
        if m is not None and _surrogate_free(rec.subject):
            matched.append((rec, m))
    if not matched:
        raise PromoteError("promote_no_match")
    matched_ids = {rec.id for rec, _ in matched}
    if family is None:
        families = {rec.family for rec, _ in matched if rec.family}
        if len(families) != 1:
            raise PromoteError("mixed_families")
        (family,) = families
    warnings: list[str] = []
    if ex.anchored:
        head = "^"
    else:
        leads = {rec.subject[: m.start()] for rec, m in matched}
        if len(leads) == 1:
            head = "^" + "".join(_esc(ord(c), _META) for c in leads.pop())
        else:
            head = "^.*?"
            warnings.append("lead_varies")
    regex = head + ex.pattern
    kinds: dict[str, str] = {}
    for j, name in enumerate(ex.groups):
        kind = ex.types[j]
        if kind == "str":
            kind = suggest_type([v for _, m in matched if (v := m.group(name))])
        if kind != "str":
            kinds[name] = kind
    examples: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rec, m in matched:
        if rec.subject in seen or len(examples) >= MAX_EXAMPLES:
            continue
        seen.add(rec.subject)
        fields: dict[str, Any] = {}
        for name in ex.groups:
            v = m.group(name)
            if v is None:
                continue
            kind = kinds.get(name)
            fields[name] = _CONVERTERS[kind](v) if v and kind else v
        examples.append({"msg": rec.subject, "fields": fields})
    code = code or free_code(family)
    lines = ["[[template]]", f"code = {toml_str(code)}", 'class = "other"', f"regex = {toml_str(regex)}"]
    if kinds:
        lines.append(f"fields = {_inline(kinds)}")
    lines.append("examples = [")
    lines.extend(f"  {{ msg = {toml_str(e['msg'])}, fields = {_inline(e['fields'])} }}," for e in examples)
    lines.append("]")
    snippet = "\n".join(lines) + "\n"
    tick()
    error = None
    try:
        load_texts(
            {"promote.toml": f"schema = 1\nfamily = {toml_str(family)}\n\n{snippet}"}, check_examples=True
        )
    except TemplateError as exc:
        error = str(exc)[:2000]
    if _catalog().resolve(code) is not None:
        warnings.append("code_exists")
    if any(rec.current for rec, _ in matched):
        warnings.append("already_classified")
    if skipped_cut:
        warnings.append("cut_skipped")
    tomllib.loads(snippet)  # the text is valid TOML by construction (tested); fail loudly otherwise
    fam = _catalog().families.get(family)
    return {
        "toml": snippet,
        "file": f"src/dmslog/adapters/{fam.source}" if fam is not None and not fam.is_library else None,
        "template": {"code": code, "family": family, "regex": regex, "fields": kinds, "examples": examples},
        "check": {"loads": error is None, "error": error},
        "warnings": [{"code": w, "message": messages.PROMOTE_WARNINGS[w]} for w in warnings],
        "records": [
            {"id": rec.id, "matched": rec.id in matched_ids, "current": rec.current} for rec in records
        ],
    }
