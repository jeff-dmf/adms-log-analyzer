"""Learn the *shape* of a message from payloads the adapter already logged in the same case.

The queue body is a serialized .NET object; the adapter sends the same object over the wire with a
different serializer. The payloads in the log show how that wire form looks (element order, letter case,
namespaces, attributes, array layout). Only names, namespace URIs and order are learned: values are never
stored. Reading is token based (``payload.pretty.tokenize``): no XML parser, no entity is resolved.
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..payload.pretty import tokenize
from ..store.events import Cursor
from ..store.payloads import PayloadFilter, get_payload, list_payloads

if TYPE_CHECKING:
    import sqlite3

EXEMPLARS = 5  # payloads used per reconstruction
EXEMPLAR_TEXT_CAP = 512 * 1024  # characters read per exemplar
CACHE_SIZE = 64
MAX_DEPTH = 64  # deeper elements are ignored (bounds the path copies)
MAX_PATHS = 5000  # distinct element paths kept
MAX_CHILDREN = 500  # distinct child names kept per element
_MAX_PAGES = 50  # pages of 1000 payloads scanned looking for exemplars

_ATTR = re.compile(r"""([^\s=/>]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True, slots=True)
class ElementShape:
    """What the exemplars show about one element (names, never values)."""

    children: tuple[str, ...] = ()  # child names, exact case, stable merge of the orders seen
    namespace: str = ""  # effective namespace URI ('' = none)
    attrs: tuple[str, ...] = ()  # attribute names (xmlns declarations excluded)
    flat: frozenset[str] = frozenset()  # children that repeat directly (flat array)
    item: str | None = None  # the single child name, when every child has it (wrapped array)
    date_only: bool = False  # the text content looked like a date without time


@dataclass(frozen=True, slots=True)
class Shape:
    """Shape per path of local names below the root (``()`` is the root element itself)."""

    elements: Mapping[tuple[str, ...], ElementShape] = field(default_factory=dict)
    count: int = 0  # exemplars that contributed

    def get(self, path: tuple[str, ...]) -> ElementShape | None:
        return self.elements.get(path)


def _merge_order(base: list[str], new: Sequence[str]) -> None:
    """Stable fusion: names unseen in ``base`` go right after the name that precedes them in ``new``."""
    last = -1
    for name in new:
        if name in base:
            last = base.index(name)
        else:
            last += 1
            base.insert(last, name)


@dataclass(slots=True)
class _Acc:
    children: list[str] = field(default_factory=list)
    namespace: str | None = None
    attrs: list[str] = field(default_factory=list)
    flat: set[str] = field(default_factory=set)
    item: str | None = None
    item_conflict: bool = False
    date_only: bool | None = None


@dataclass(slots=True)
class _Frame:
    raw: str
    path: tuple[str, ...]
    scope: dict[str, str]
    namespace: str
    attrs: list[str]
    counts: dict[str, int] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)


def _local(raw: str) -> tuple[str, str]:
    prefix, sep, local = raw.partition(":")
    return (prefix, local) if sep else ("", raw)


def _open_frame(raw_tag: str, raw_name: str, parent: _Frame | None) -> _Frame:
    attrs: list[str] = []
    decls: dict[str, str] = {}
    for m in _ATTR.finditer(raw_tag, 1 + len(raw_name)):
        name = m.group(1)
        value = m.group(2) if m.group(2) is not None else m.group(3)
        if name == "xmlns":
            decls[""] = value
        elif name.startswith("xmlns:"):
            decls[name[6:]] = value
        else:
            attrs.append(_local(name)[1])
    scope = {**parent.scope, **decls} if parent is not None and decls else (parent.scope if parent else decls)
    prefix, local = _local(raw_name)
    path = (*parent.path, local) if parent is not None else ()
    return _Frame(raw_name, path, scope, scope.get(prefix, ""), attrs)


def _close_frame(f: _Frame, acc: dict[tuple[str, ...], _Acc]) -> None:
    if f.path not in acc and len(acc) >= MAX_PATHS:
        return
    a = acc.setdefault(f.path, _Acc())
    _merge_order(a.children, f.order)
    if a.namespace is None:
        a.namespace = f.namespace
    for name in f.attrs:
        if name not in a.attrs:
            a.attrs.append(name)
    a.flat.update(n for n, c in f.counts.items() if c >= 2)
    if len(f.order) == 1:
        if a.item is None and not a.item_conflict:
            a.item = f.order[0]
        elif a.item != f.order[0]:
            a.item, a.item_conflict = None, True
    elif len(f.order) > 1:
        a.item, a.item_conflict = None, True
    if not f.order:
        text = "".join(f.text).strip()
        is_date = bool(_DATE_ONLY.fullmatch(text))
        if text or a.date_only is not None:
            a.date_only = is_date if a.date_only is None else (a.date_only and is_date)


def _learn_one(text: str, acc: dict[tuple[str, ...], _Acc]) -> bool:
    tokens, _status, _pos = tokenize(text)
    stack: list[_Frame] = []
    seen_root = False
    skipped = 0  # open elements below MAX_DEPTH, ignored
    for kind, raw, name, _start in tokens:
        if skipped and kind != "c" and kind != "o":
            continue
        if kind in ("o", "s"):
            if not stack and seen_root:
                break
            if skipped or len(stack) >= MAX_DEPTH:
                skipped += kind == "o"
                continue
            parent = stack[-1] if stack else None
            frame = _open_frame(raw, name, parent)
            seen_root = True
            if parent is not None:
                local = frame.path[-1]
                if local not in parent.counts and len(parent.order) < MAX_CHILDREN:
                    parent.order.append(local)
                    parent.counts[local] = 0
                if local in parent.counts:
                    parent.counts[local] += 1
            if kind == "s":
                _close_frame(frame, acc)
            else:
                stack.append(frame)
        elif kind == "c":
            if skipped:
                skipped -= 1
                continue
            if not stack or stack[-1].raw != name:
                break
            _close_frame(stack.pop(), acc)
            if not stack:
                break
        elif kind in ("t", "d") and stack:
            stack[-1].text.append(raw)
    while stack:  # truncated text: keep what was seen
        _close_frame(stack.pop(), acc)
    return seen_root


def learn_shape(texts: Sequence[str]) -> Shape | None:
    """Shape of the given payload texts (most recent first), or None when none has a root element."""
    acc: dict[tuple[str, ...], _Acc] = {}
    used = 0
    for text in texts:
        if _learn_one(text[:EXEMPLAR_TEXT_CAP], acc):
            used += 1
    if not used:
        return None
    elements = {
        path: ElementShape(
            children=tuple(a.children),
            namespace=a.namespace or "",
            attrs=tuple(a.attrs),
            flat=frozenset(a.flat),
            item=a.item,
            date_only=bool(a.date_only),
        )
        for path, a in acc.items()
    }
    return Shape(elements, used)


def _local_root(root: str | None) -> str:
    return _local(root or "")[1]


def select_exemplar_ids(conn: sqlite3.Connection, root: str, noun: str | None = None) -> list[int]:
    """Ids of the newest exemplar payloads, most recent first (read-only).

    Well-formed outgoing XML requests whose root is ``root``; when there are none, those whose root ends
    with ``noun`` (same noun, any verb).
    """
    flt = PayloadFilter(roles=("request",), directions=("out",), formats=("xml",))
    exact: list[int] = []
    by_noun: list[int] = []
    after: Cursor | None = None
    for _ in range(_MAX_PAGES):
        rows, nxt = list_payloads(conn, flt, 1000, after)
        for meta in rows:
            if not meta["well_formed"]:
                continue
            local = _local_root(meta["root"])
            if local == root:
                exact.append(meta["id"])
            elif noun and local.endswith(noun):
                by_noun.append(meta["id"])
        after = Cursor.parse(nxt) if nxt else None
        if after is None:
            break
    chosen = exact or by_noun
    return list(reversed(chosen[-EXEMPLARS:]))


def select_exemplar_texts(conn: sqlite3.Connection, root: str, noun: str | None = None) -> list[str]:
    texts: list[str] = []
    for pid in select_exemplar_ids(conn, root, noun):
        got = get_payload(conn, pid, EXEMPLAR_TEXT_CAP)
        if got is not None and got["text"]:
            texts.append(got["text"])
    return texts


class ShapeCache:
    """Small LRU of shapes per ``(case, root, ingest_generation)``; shapes hold names only."""

    def __init__(self, size: int = CACHE_SIZE) -> None:
        self._size = size
        self._data: OrderedDict[tuple[Any, ...], Shape | None] = OrderedDict()
        self._lock = threading.Lock()

    def get_or_load(self, key: tuple[Any, ...], loader: Callable[[], Shape | None]) -> Shape | None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
        value = loader()
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self._size:
                self._data.popitem(last=False)
        return value

    def __len__(self) -> int:
        return len(self._data)


SHAPE_CACHE = ShapeCache()


def load_shape(
    conn: sqlite3.Connection, case: str, root: str, generation: int, noun: str | None = None
) -> Shape | None:
    """The shape learned from the case's payloads for ``root`` (cached; None without exemplars)."""
    return SHAPE_CACHE.get_or_load(
        (case, root, generation),
        lambda: learn_shape(select_exemplar_texts(conn, root, noun)),
    )
