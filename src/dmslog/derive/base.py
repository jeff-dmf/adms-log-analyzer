"""Contract shared by every deriver (ARQUITETURA 6.5).

A deriver is a small state machine fed with the entries of ONE stream in ``entry.id`` order
(``.log.0`` before ``.log``). It yields ``Event`` rows and may attach entries to an event
(``event_id`` is assigned by the engine when the event is stored, see ``Event.entry_ids``).
Derivers know nothing about SQLite: ``run_in_memory`` is the harness the unit tests use, and
``derive/engine.py`` is the one that talks to the store.

Re-derivation contract (property tested): feeding the entries from a checkpoint with the state
a deriver reports through ``low_watermark`` gives the same events as feeding the whole stream.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from operator import itemgetter
from typing import Any, Protocol

# Event status values (event.status); see schema.sql.
STATUS_OK = "ok"
STATUS_BUSINESS_ERROR = "business_error"
STATUS_FAIL = "fail"
STATUS_TIMEOUT = "timeout"
STATUS_OPEN = "open"
STATUS_TRUNCATED = "truncated"


# Same output as ``json.dumps(detail, sort_keys=True, separators=(",", ":"))`` without building an encoder
# for every event.
_DETAIL_ENCODER = json.JSONEncoder(sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class StoredEntry:
    """An indexed record as the derivers see it (a read-only view of an ``entry`` row)."""

    id: int
    stream_id: int
    ts: int | None  # milliseconds; None only for the 'plain' envelope
    tid: int | None
    pid: int | None
    level: int | None
    sev: int
    tpl_code: str | None  # template.code, None for unknown records
    tpl_class: str | None  # template.class (start|end|req_resp|error|statistics|...)
    fields: dict[str, Any]  # extracted fields (typed)
    msg: str  # first line after the envelope
    cont: str = ""  # continuation lines joined with '\n' (may be empty)
    keys: tuple[tuple[str, str], ...] = ()  # (key_type, value) pairs

    @property
    def fields_raw(self) -> str | None:
        """Raw JSON of ``fields`` when known (always None here: use ``fields``)."""
        return None

    def field(self, name: str) -> Any:
        """``fields.get(name)``."""
        return self.fields.get(name)


class RowEntry(tuple):  # type: ignore[type-arg]
    """``StoredEntry`` look-alike built straight from a query row (the engine's fast path).

    The row is ``(id, ts, tid, sev, fields_json, msg, tpl_id, cont, keys, tpl_code, tpl_class)``:
    ``fields_json`` and ``cont`` are empty strings and ``keys`` an empty tuple when absent. ``pid`` and
    ``level`` are not read by any deriver and are None. ``fields`` is parsed (and cached) only when a
    deriver asks for it; ``fields_raw`` and ``field(name)`` let a deriver skip that parse. Same attribute
    names and values as ``StoredEntry``.
    """

    id = property(itemgetter(0))
    ts = property(itemgetter(1))
    tid = property(itemgetter(2))
    sev = property(itemgetter(3))
    fields_raw = property(itemgetter(4))  # "" when the record has no fields
    msg = property(itemgetter(5))
    cont = property(itemgetter(7))
    keys = property(itemgetter(8))
    tpl_code = property(itemgetter(9))
    tpl_class = property(itemgetter(10))
    pid = None
    level = None
    stream_id = 0

    def field(self, name: str) -> Any:
        """``fields.get(name)``, skipping the JSON parse when that is provably the same answer.

        Shortcut (raw text without any backslash, so no escapes and strings contain no quote): the
        quoted key occurs exactly once, it is followed by ``:"``, it sits at nesting depth 1 outside any
        string right after ``{`` or ``,``, and its value is the string up to the next quote. When the quoted
        key does not occur at all the answer is None. Everything else goes through ``json.loads`` (cached).
        """
        raw = self[4]
        if not raw:
            return None
        if "\\" not in raw and raw[0] == "{":
            token = f'"{name}"'
            n = raw.count(token)
            if n == 0:
                return None
            if n == 1:
                i = raw.find(token)
                if raw.startswith(':"', i + len(token)):
                    prefix = raw[:i]
                    if prefix[-1] in "{," and self._depth_outside_strings(prefix) == 1:
                        start = i + len(token) + 2
                        end = raw.find('"', start)
                        if end >= 0:
                            return raw[start:end]
        return self.fields.get(name)

    @staticmethod
    def _depth_outside_strings(prefix: str) -> int | None:
        """Nesting depth at the end of ``prefix`` (no backslashes), or None when it ends inside a string."""
        parts = prefix.split('"')
        if len(parts) % 2 == 0:
            return None
        depth = 0
        for outside in parts[::2]:
            depth += outside.count("{") + outside.count("[") - outside.count("}") - outside.count("]")
        return depth

    @cached_property
    def fields(self) -> dict[str, Any]:
        raw = self[4]
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}


@dataclass(slots=True)
class Event:
    """A derived event (one ``event`` row). ``detail`` is stored as JSON."""

    kind: str
    ts_start: int
    name: str | None = None
    key: str | None = None
    tid: int | None = None
    start_entry_id: int | None = None
    end_entry_id: int | None = None
    ts_end: int | None = None
    duration_ms: int | None = None
    status: str | None = None
    sev: int = 4
    n_entries: int | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    entry_ids: list[int] = field(default_factory=list)  # entries that get ``entry.event_id``

    def detail_json(self) -> str | None:
        return _DETAIL_ENCODER.encode(self.detail) if self.detail else None


@dataclass(frozen=True, slots=True)
class StatSample:
    """One ``stat_sample`` row (produced by the stats deriver, stored by the engine)."""

    ts: int
    side: str  # client|service
    method: str  # '[TOTAL]' or Contract.Method
    calls: int | None
    req_bytes: int | None
    resp_bytes: int | None
    d_calls: int | None  # None after a drop (restart) or for the first sample
    d_req_bytes: int | None
    d_resp_bytes: int | None


class Deriver(Protocol):
    """Contract of a deriver. ``kind`` names the events it mainly produces."""

    kind: str

    def feed(self, entry: StoredEntry) -> Iterable[Event]:
        """Consume one entry; return the events that became complete with it."""
        ...

    def flush(self, *, eof: bool) -> Iterable[Event]:
        """Close open state. ``eof=True``: the stream ended (open events become ``open``)."""
        ...

    def low_watermark(self) -> int | None:
        """Smallest ``entry.id`` still needed by an open event (None when nothing is open)."""
        ...


def run_in_memory(
    derivers: Sequence[Deriver], entries: Iterable[StoredEntry], *, eof: bool = True
) -> list[Event]:
    """Feed ``entries`` to ``derivers`` in order and return every event (test harness)."""
    events: list[Event] = []
    for entry in entries:
        for deriver in derivers:
            events.extend(deriver.feed(entry))
    for deriver in derivers:
        events.extend(deriver.flush(eof=eof))
    return events


def iter_samples(deriver: Any) -> Iterator[StatSample]:
    """Drain ``deriver.samples`` when a deriver exposes it (stats), else nothing."""
    yield from getattr(deriver, "samples", ())
