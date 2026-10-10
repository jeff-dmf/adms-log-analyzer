"""Static and variable parts of a classified record, read from the template catalog (strict redaction).

The strict mode of :mod:`dmslog.report.redact` keeps the text a template declares (its literal
segments) and treats everything else as variable. This module answers three read-only questions:

* :func:`layout`: for the first line of a record and its template code, which characters are the
  template's own literal text (``static``) and where each captured field is;
* :attr:`Shape.proofs`: what a field's own regex proves about its value (only a finite set of
  literals counts); a field without a proof (and without an entry in ``redact.PROVEN_FIELDS``) is replaced
  whole by the strict mode;
* :func:`vocabulary`: the words of the catalog's literal text (plus a fixed list of common English
  words). Used only for whole standalone words of FREE text and for payload JSON keys, never to keep
  the value of a template field, a labelled value or a payload value;
* :func:`catalog_names`: operation names, family codes and template codes of the catalog.

How a line is split: the template regex is parsed (``re._parser``, as the catalog loader does) and
its TOP-LEVEL items are classified as literal runs, named groups and other (variable) items. The
regex is matched again on the line; named groups give exact spans. Each literal run is then placed
at the first position, at or after the end of the previous item, where its text occurs and before
the next known group. A literal run that cannot be placed is simply not static. Whatever placement
is chosen, a character is static only when the text there IS a literal of the catalog, so the
choice can make the output less readable but never shows data that is not catalog text.

Nothing here touches the case database or the catalog files; results are cached per process.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

try:  # the regex syntax tree (same private module the catalog loader uses)
    from re import _constants as _sre  # type: ignore[attr-defined]
    from re import _parser as _sre_parser  # type: ignore[attr-defined]

    _HAVE_PARSER = True
except ImportError:  # pragma: no cover - every supported Python (3.11+) has it
    _HAVE_PARSER = False

Span = tuple[int, int]

# Common English words of log messages. Only function words and generic verbs/nouns of software
# logs: no proper nouns. A word in this list is kept by the strict token rule.
COMMON_WORDS = frozenset(
    """
    a about above after again against all already also an and any are as at available back be
    because been before being below between both but by call called calling can cannot canceled
    cancelled check checking client close closed closing code complete completed connect connected
    connecting connection could count create created current data default delete deleted did do does
    done down during each elapsed empty end ended error errors event every exception expired failed
    failure false field file finished first for found from get got had has have having in info
    invalid is it its last left length level line list load loaded loading log many max message
    messages method min missing more ms name new next no none not null of off ok on one only open
    opened operation or other out over page process processed processing read ready received
    receiving record records request requests response result retry retrying returned run running
    s same sec second seconds send sending sent server service set should size skipped start
    started starting state status still stop stopped success successful successfully system task
    than that the then there this thread time timeout to too total true type unable unknown up
    update updated use used user using valid value values version waiting warning was were when
    which while will with within without would yes
    xml json utf amp lt gt quot apos nbsp http https soap version encoding
    able access accepted acknowledged action active add added adding address after again agent all
    allowed already alive attempt attempts authentication batch begin begins being blocked body buffer
    busy cache cancel cannot changed channel child closed command commit committed config configured
    connected consumer contains content context continue copy created critical current database date
    day days debug delay deleted denied details detected disabled disconnected done duration enabled
    encoding entry error exceeded exists exit expected export failed fault fetch fetched finish first
    flush format found free full general handler header health heartbeat hour hours id ignored import
    in incoming init initialized input insert inserted instance internal interval item items job key
    last latency limit listener local lock locked logged login logout lost main manager map mapping
    memory minute minutes mode model module monitor ms network new no node not notification now number
    object of offline old online operation option order outgoing output owner package parameter parse
    parsed partial password pending period poll port previous primary priority proxy publish published
    queue queued range rate reached reason receive reconnect reconnecting recovered refresh refused
    register registered reject rejected release reload remote removed reply report request required
    reset resource resources response restart restarted result resume retries return reverted rollback
    route rule save saved schedule scheduled schema see send session shutdown sign since skip slow
    source standby start state step stopped store stored stream subscribe subscribed subscriber
    subscription succeeded switch sync synchronization synchronized table target task test thread
    threads throw thrown timer timestamp token topic trace transaction transfer trigger try unavailable
    unexpected unhandled unregistered update upload url user validate validated validation verify
    waited wait warn window worker write written wrong
    """.split()
)

_LOG_LEVELS = frozenset(
    "fatal error err warn warning info information debug debuglog trace verbose notice critical".split()
)


@dataclass(frozen=True, slots=True)
class Shape:
    """What the strict redaction needs of one template."""

    code: str
    text: str | None  # exact-text template
    regex: re.Pattern[str] | None
    window: int
    items: tuple[tuple[str, Any], ...]  # top level: ("lit", text) | ("group", group number) | ("var", None)
    group_names: tuple[tuple[str, int], ...]  # every named group (any depth): (name, number)
    cont_parser: str | None = None  # the template's continuation parser (kv_tab, dotnet_stack...)
    # what the regex itself proves about a field: a frozenset of the only texts it can match (a finite
    # alternation of literals), or absent (a numeric regex proves nothing: a short id is digits too)
    proofs: Any = None
    field_types: Any = None


@dataclass(frozen=True, slots=True)
class Layout:
    """Where the static text and the fields of one line are (offsets in the line as given)."""

    static: tuple[Span, ...]
    fields: tuple[tuple[int, int, str], ...]  # (start, end, group name), non-empty captures only
    end: int  # where the template match ends; the rest of the line is not described by the template


@functools.lru_cache(maxsize=1)
def _catalog() -> Any:
    from ..parse.templates import load_catalog

    return load_catalog()


def _top_items(source: str) -> tuple[tuple[str, Any], ...]:
    if not _HAVE_PARSER:  # pragma: no cover
        return (("var", None),)
    tree = _sre_parser.parse(source, 0)
    items: list[tuple[str, Any]] = []
    run: list[str] = []
    for op, av in tree.data:
        if op is _sre.LITERAL:
            run.append(chr(av))
            continue
        if op is _sre.AT:  # anchors: no width
            continue
        if run:
            items.append(("lit", "".join(run)))
            run = []
        if op is _sre.SUBPATTERN and av[0] is not None:
            items.append(("group", av[0]))
        else:
            items.append(("var", None))
    if run:
        items.append(("lit", "".join(run)))
    return tuple(items)


@functools.lru_cache(maxsize=2048)
def shape(code: str | None) -> Shape | None:
    """The shape of the template ``code``, or None when the catalog does not know it."""
    if not code:
        return None
    try:
        tpl = _catalog().template(code)
    except (KeyError, LookupError):
        return None
    except Exception:  # noqa: BLE001 - a broken catalog only loses the structural rule (fail closed upstream)
        return None
    parser = tpl.cont.parser if tpl.cont is not None else None
    if tpl.regex is None:
        return Shape(tpl.code, tpl.text or "", None, tpl.window, (), (), parser, {}, {})
    try:
        items = _top_items(tpl.regex.pattern)
    except Exception:  # noqa: BLE001
        items = (("var", None),)
    names = tuple(sorted(tpl.regex.groupindex.items(), key=lambda kv: kv[1]))
    try:
        proofs = _field_proofs(tpl.regex.pattern)
    except Exception:  # noqa: BLE001 - no proof: the fields are redacted whole
        proofs = {}
    return Shape(tpl.code, None, tpl.regex, tpl.window, items, names, parser, proofs, dict(tpl.field_types))


MAX_ENUM_VALUES = 64


def _finite(items: list[Any]) -> set[str] | None:
    """Every text ``items`` can match when that is a small finite set of literals, else None."""
    out = {""}
    for op, av in items:
        if op is _sre.LITERAL:
            options = {chr(av)}
        elif op is _sre.AT:
            continue
        elif op is _sre.SUBPATTERN:
            found = _finite(av[3])
            if found is None:
                return None
            options = found
        elif op is _sre.BRANCH:
            options = set()
            for branch in av[1]:
                found = _finite(branch)
                if found is None:
                    return None
                options |= found
        elif op is _sre.IN and all(kind is _sre.LITERAL for kind, _v in av):
            options = {chr(v) for _k, v in av}
        elif op in (_sre.MAX_REPEAT, _sre.MIN_REPEAT) and av[1] <= 1:
            found = _finite(av[2])
            if found is None:
                return None
            options = found | ({""} if av[0] == 0 else set())
        else:
            return None
        out = {a + b for a in out for b in options}
        if len(out) > MAX_ENUM_VALUES:
            return None
    return out


def _field_proofs(source: str) -> dict[str, Any]:
    """Per named group: what its sub-pattern proves (see :attr:`Shape.proofs`)."""
    if not _HAVE_PARSER:  # pragma: no cover
        return {}
    tree = _sre_parser.parse(source, 0)
    names = {number: name for name, number in tree.state.groupdict.items()}
    proofs: dict[str, Any] = {}

    def walk(items: list[Any]) -> None:
        for op, av in items:
            if op is _sre.SUBPATTERN:
                if av[0] in names:
                    finite = _finite(av[3])
                    if finite is not None and any(finite):
                        proofs[names[av[0]]] = frozenset(v for v in finite if v)
                walk(av[3])
            elif op is _sre.BRANCH:
                for branch in av[1]:
                    walk(branch)
            elif op in (_sre.MAX_REPEAT, _sre.MIN_REPEAT, _sre.POSSESSIVE_REPEAT):
                walk(av[2])
            elif op in (_sre.ASSERT, _sre.ASSERT_NOT):
                walk(av[1])

    walk(tree.data)
    return {k: v for k, v in proofs.items() if v}


def layout(sh: Shape, line: str) -> Layout | None:
    """Split ``line`` (the first line of a record) by its template, or None when the template does
    not match it any more (the caller then treats the whole line as variable)."""
    stripped = line.strip()
    lead = len(line) - len(line.lstrip())
    if not stripped:
        return Layout((), (), 0)
    if sh.text is not None:
        return (
            Layout(((lead, lead + len(stripped)),), (), lead + len(stripped)) if stripped == sh.text else None
        )
    if sh.regex is None or len(stripped) > sh.window or "\n" in stripped:
        return None
    match = sh.regex.match(stripped)
    if match is None:
        return None
    fields = []
    for name, number in sh.group_names:
        start, end = match.span(number)
        if start >= 0 and end > start:
            fields.append((lead + start, lead + end, name))
    static: list[Span] = []
    items = sh.items
    cursor = 0
    exact = True
    for index, (kind, value) in enumerate(items):
        if kind == "lit":
            limit = match.end()
            for later_kind, later in items[index + 1 :]:
                if later_kind == "group":
                    later_start = match.start(later)
                    if later_start >= 0:
                        limit = later_start
                        break
            if exact and stripped.startswith(value, cursor) and cursor + len(value) <= limit:
                found = cursor
            else:
                found = stripped.find(value, cursor, limit)
            if found < 0:
                exact = False
                continue
            static.append((lead + found, lead + found + len(value)))
            cursor = found + len(value)
            exact = True
        elif kind == "group":
            start, end = match.span(value)
            if start >= 0 and start >= cursor:
                cursor = end
                exact = True
            else:
                exact = False
        else:
            exact = False
    return Layout(tuple(static), tuple(fields), lead + match.end())


def _literal_texts(items: list[Any]) -> Iterator[str]:
    run: list[str] = []
    for op, av in items:
        if op is _sre.LITERAL:
            run.append(chr(av))
            continue
        if run:
            yield "".join(run)
            run = []
        if op is _sre.SUBPATTERN:
            yield from _literal_texts(av[3])
        elif op is _sre.BRANCH:
            for branch in av[1]:
                yield from _literal_texts(branch)
        elif op in (_sre.MAX_REPEAT, _sre.MIN_REPEAT):
            yield from _literal_texts(av[2])
    if run:
        yield "".join(run)


_WORDS = re.compile(r"[^\W\d_]+")
_CAMEL_PARTS = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+")


def _words(text: str) -> Iterator[str]:
    for word in _WORDS.findall(text):
        yield word.casefold()
        if len(word) > 1:
            yield from (part.casefold() for part in _CAMEL_PARTS.findall(word))


@functools.lru_cache(maxsize=1)
def vocabulary() -> frozenset[str]:
    """Casefolded words of the catalog's literal text (template literals and exact texts, operation
    names, codes, titles) plus :data:`COMMON_WORDS` and the log level names. Template EXAMPLES are not
    used: they are sample log lines."""
    words: set[str] = set(COMMON_WORDS) | set(_LOG_LEVELS)
    try:
        catalog = _catalog()
        for tpl in catalog.templates():
            texts: list[str] = [tpl.code]
            if tpl.title:
                texts.append(tpl.title)
            if tpl.text is not None:
                texts.append(tpl.text)
            elif tpl.regex is not None and _HAVE_PARSER:
                texts.extend(_literal_texts(_sre_parser.parse(tpl.regex.pattern, 0).data))
                texts.extend(name.replace("_", " ") for name in tpl.regex.groupindex)
            for text in texts:
                words.update(_words(text))
        words.update(name.casefold() for name in catalog_names())
    except Exception:  # noqa: BLE001 - without a catalog the vocabulary is the fixed list (stricter)
        pass
    return frozenset(w for w in words if w)


@functools.lru_cache(maxsize=1)
def catalog_names() -> frozenset[str]:
    """Casefolded operation names, family codes and template codes of the catalog."""
    names: set[str] = set()
    try:
        catalog = _catalog()
        for family, fam in catalog.families.items():
            names.add(family.lstrip("_").casefold())
            for op in fam.operations:
                name = op.get("name") if isinstance(op, dict) or hasattr(op, "get") else None
                if isinstance(name, str):
                    names.add(name.casefold())
        for tpl in catalog.templates():
            names.add(tpl.code.casefold())
            names.update(alias.casefold() for alias in tpl.aliases)
    except Exception:  # noqa: BLE001
        pass
    return frozenset(n for n in names if n)


@functools.lru_cache(maxsize=1)
def product_codes() -> frozenset[str]:
    """Casefolded error codes and acronyms of the knowledge pack (product documentation): a closed set
    of public names the strict mode may keep wherever they stand alone."""
    import json
    from importlib import resources

    codes: set[str] = set()
    try:
        data = json.loads(
            resources.files("dmslog.knowledge").joinpath("pack/error_codes.json").read_text("utf-8")
        )
        for row in data.get("error_codes", []):
            for key in ("code", "id", "acronym"):
                value = row.get(key) if isinstance(row, dict) else None
                if isinstance(value, str) and 2 <= len(value) <= 128:
                    codes.add(value.casefold())
    except (OSError, ValueError, AttributeError):
        pass
    return frozenset(codes)


def log_levels() -> frozenset[str]:
    return _LOG_LEVELS
