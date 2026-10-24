"""Operations deriver: start/end/fail pairing per ``(rule, tid)`` -> ``operation`` events (ARQUITETURA 6.5).

Rules come from the family's ``[[operation]]`` tables (``TemplateSet.operations()``; a plain sequence
of mappings is accepted too, for unit tests). Keys understood:

``name`` / ``name_field``  operation name, or the field of the start entry that holds it
``start``                  template codes that open an operation
``end`` / ``end_ok``       codes that close it (``end``: ok unless its text says ``failed`` or
                           ``unsuccessfully``; ``end_ok``: ok)
``end_fail``               codes that close it as ``fail``
``fail``                   codes that mark the open operation of the same TID as ``fail``
``pair_by``                ``["tid"]`` (default): one open operation per ``(rule, tid)``. A key type
                           other than ``tid`` (``["job_id"]``): one open operation per
                           ``(rule, value of that key)``, whatever the thread (see below)
``check_key``              key type (``request_id``, ``msgid``, ...) that must agree between the
                           start and the end (an end with another value does not close it)
``business_error``         codes that must ALL be seen inside the operation (for example the
                           ``Detected errors`` template plus the ``SystemEventSender`` one) to make it
                           ``business_error``. Absent: the family does not declare it, the status
                           never becomes ``business_error``.

Pairing and policies
--------------------
* One open operation per ``(rule, tid)``. A new start while one is open closes the older as
  ``truncated`` (``fail`` if a fail code was seen; ``detail.closed_by = "new_start"``). The replacement is
  not counted on the new operation: a resumed pass does not see the operation closed in an earlier pass.
* Pairing by key (``pair_by = ["job_id"]``): the open state is ``(rule, key value)``. The value is read
  from the entry keys, else from the field of the same name. A start without the key is ignored and
  counted in ``keyless_starts``; an end/fail pairs by the key value of its own entry (without the key it
  is an orphan). ``entry_ids`` = the start, the end and the entries in between that carry the same key
  value (any thread). A new start with the same value, ``MAX_OPEN`` and the EOF flush behave as for TID.
* An end/fail without an open operation (or with another key or name) is ignored and counted in
  ``orphan_ends`` / ``orphan_fails``.
* Status: ``fail`` (a fail code seen, an ``end_fail`` end, or a failing end text) beats
  ``business_error`` beats ``ok``. No end at EOF: ``fail`` if a fail was seen, else ``truncated`` when
  the deriver was built with ``truncated=True`` (the stream was cut by the ERS; the ERS flag is not in
  the store, so the caller passes it), else ``open``.
* ``entry_ids``: the start and the end entry, plus the entries of the same TID in between that do not
  carry a different value of the rule's ``check_key``. At most ``MAX_ENTRY_IDS`` per operation are
  kept (``detail.entry_ids_dropped`` counts the rest; ``n_entries`` counts all).
* Bounded state: at most ``MAX_OPEN`` open operations; the oldest is closed as ``truncated``
  (``fail`` if seen)
  (``detail.closed_by = "evicted"``) when the cap is exceeded.
* ``low_watermark`` is the start entry id of the oldest open operation.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Iterable, Mapping
from typing import Any

from dmslog.derive.base import (
    FLAG_AFTER_SKIP,
    STATUS_BUSINESS_ERROR,
    STATUS_FAIL,
    STATUS_OK,
    STATUS_OPEN,
    STATUS_TRUNCATED,
    Event,
    StoredEntry,
)

MAX_OPEN = 10_000
MAX_ENTRY_IDS = 2_000
MAX_ERROR_CODES = 32
MAX_TEXT = 200

_SEV = {STATUS_OK: 4, STATUS_BUSINESS_ERROR: 3, STATUS_FAIL: 2, STATUS_OPEN: 3, STATUS_TRUNCATED: 3}
_FAIL_TEXT = re.compile(r"\b(?:failed|unsuccessfully)\b", re.IGNORECASE)
_NO_CODES: frozenset[str] = frozenset()
_FALLBACK_KEYS = ("request_id", "msgid", "gid")

_START, _END, _END_OK, _END_FAIL, _FAIL = "start", "end", "end_ok", "end_fail", "fail"
_ROLES = (_START, _END, _END_OK, _END_FAIL, _FAIL)


def _codes(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


class _Rule:
    __slots__ = ("business", "check_key", "idx", "name", "name_field", "pair_key")

    def __init__(self, idx: int, raw: Mapping[str, Any]) -> None:
        self.idx = idx
        self.name: str | None = raw.get("name")
        self.name_field: str | None = raw.get("name_field")
        self.check_key: str | None = raw.get("check_key")
        self.business = frozenset(_codes(raw.get("business_error")))
        # first pair_by entry that is not "tid": the key type that pairs the operation (None = by TID)
        self.pair_key: str | None = next((k for k in _codes(raw.get("pair_by")) if k != "tid"), None)


class _Op:
    """An open operation. Rarely set attributes are class-level defaults (cheaper to create)."""

    dropped = 0
    failed = False
    fail_entry: int | None = None
    fail_how: str | None = None
    biz: set[str] | None = None  # created when a business-error code is seen
    codes: list[str] | None = None  # created when an error code is seen
    exc: str | None = None
    skipped = False  # a stretch skipped by a reduced source lies inside it (duration not reliable)

    def __init__(
        self, rule: _Rule, entry: StoredEntry, name: str | None, from_field: bool, key: str | None, ts: int
    ):
        self.rule = rule
        self.name = name
        self.name_from_field = from_field
        self.key = key
        self.tid = entry.tid
        self.slot: tuple[int, Any] = (rule.idx, entry.tid)
        self.pair: tuple[str, str] | None = None  # (key type, value) of a key-paired operation
        self.start_id = entry.id
        self.ts_start = ts
        self.ids: list[int] = []
        self.n = 0


def _rules_of(rules: Any) -> list[Mapping[str, Any]]:
    if rules is None:
        return []
    ops = getattr(rules, "operations", None)
    if callable(ops):
        return list(ops())
    return [r for r in rules if isinstance(r, Mapping)]


def _key_of(entry: StoredEntry, key_type: str) -> str | None:
    for kind, value in entry.keys:
        if kind == key_type:
            return value
    value = entry.field(key_type)
    return None if value is None or isinstance(value, (list, dict)) else str(value)


class OperationsDeriver:
    kind = "operation"

    def __init__(self, family: str, rules: Any = None, *, truncated: bool = False) -> None:
        self.family = family
        self.truncated = truncated
        self.orphan_ends = 0
        self.orphan_fails = 0
        self.overlaps = 0
        self.keyless_starts = 0
        self.evicted = 0
        self.errors = 0
        self._rules: list[_Rule] = []
        self._by_code: dict[str, list[tuple[_Rule, str]]] = {}
        for idx, raw in enumerate(_rules_of(rules)):
            rule = _Rule(idx, raw)
            self._rules.append(rule)
            for role in _ROLES:
                for code in _codes(raw.get(role)):
                    self._by_code.setdefault(code, []).append((rule, role))
        self._open: OrderedDict[tuple[int, int | None], _Op] = OrderedDict()
        self._by_tid: dict[int, list[_Op]] = {}
        self._by_pair: dict[tuple[str, str], list[_Op]] = {}  # key-paired operations
        self._pair_types = tuple(dict.fromkeys(r.pair_key for r in self._rules if r.pair_key))
        self._last_ts = 0

    # ---------------------------------------------------------------- Deriver protocol

    def feed(self, entry: StoredEntry) -> Iterable[Event]:
        ts = entry.ts
        if ts is not None:
            self._last_ts = ts
        if entry.flags & FLAG_AFTER_SKIP:  # the operations open now span the skipped stretch
            for op in self._open.values():
                op.skipped = True
        if entry.tpl_code not in self._by_code:
            # No rule names this code: it can only join the open operation of its TID (if any). This is
            # the hottest path (most entries), so ``_join`` is inlined here.
            ops = self._by_tid.get(entry.tid)  # type: ignore[arg-type]  # None never is a key
            try:
                if ops:
                    self._fast_join(ops[-1], entry)
                if self._by_pair:
                    for kind in self._pair_types:
                        value = _key_of(entry, kind)
                        paired = None if value is None else self._by_pair.get((kind, value))
                        if paired:
                            self._fast_join(paired[-1], entry)
            except Exception:  # never raise on odd input; counted for diagnostics
                self.errors += 1
            return ()
        out: list[Event] = []
        try:
            self._feed(entry, out)
        except Exception:  # never raise on odd input; counted for diagnostics
            self.errors += 1
        return out

    def _fast_join(self, op: _Op, entry: StoredEntry) -> None:
        check = op.rule.check_key
        if check is not None and op.key is not None:
            other = _key_of(entry, check)
            if other is not None and other != op.key:
                return
        op.n += 1
        ids = op.ids
        if len(ids) < MAX_ENTRY_IDS:
            ids.append(entry.id)
        else:
            op.dropped += 1
        if op.rule.business and entry.tpl_code in op.rule.business:
            self._join_code(op, entry)
        raw = entry.fields_raw
        if raw is None or (raw and ('"codes"' in raw or '"exc_type"' in raw)):
            self._join_fields(op, entry.fields)

    def flush(self, *, eof: bool) -> Iterable[Event]:
        out: list[Event] = []
        if eof:
            for op in list(self._open.values()):
                if op.failed:
                    status = STATUS_FAIL
                elif self.truncated:
                    status = STATUS_TRUNCATED
                else:
                    status = STATUS_OPEN
                out.append(self._event(op, status, None, closed_by="eof"))
            self._open.clear()
            self._by_tid.clear()
            self._by_pair.clear()
        return out

    def low_watermark(self) -> int | None:
        for op in self._open.values():
            return op.start_id
        return None

    # ---------------------------------------------------------------- internals

    def _feed(self, entry: StoredEntry, out: list[Event]) -> None:
        joined = False
        listed_end = listed_fail = hit_end = hit_fail = False
        for rule, role in self._by_code.get(entry.tpl_code or "", ()):
            if role == _START:
                joined |= self._start(rule, entry, out)
            elif role == _FAIL:
                listed_fail = True
                hit = self._fail(rule, entry)
                hit_fail |= hit
                joined |= hit
            else:
                listed_end = True
                hit = self._end(rule, role, entry, out)
                hit_end |= hit
                joined |= hit
        # orphan only if NO rule listing the code had an open operation on the TID (rules may share codes)
        if listed_end and not hit_end:
            self.orphan_ends += 1
        if listed_fail and not hit_fail:
            self.orphan_fails += 1
        if not joined:
            if entry.tid is not None:
                ops = self._by_tid.get(entry.tid)
                if ops:
                    op = ops[-1]
                    if self._compatible(op, entry):
                        self._join(op, entry)
            if self._by_pair:
                for kind in self._pair_types:
                    value = _key_of(entry, kind)
                    paired = None if value is None else self._by_pair.get((kind, value))
                    if paired and self._compatible(paired[-1], entry):
                        self._join(paired[-1], entry)

    @staticmethod
    def _compatible(op: _Op, entry: StoredEntry) -> bool:
        """False when the entry carries another value of the rule's check key."""
        check = op.rule.check_key
        if check is None or op.key is None:
            return True
        other = _key_of(entry, check)
        return other is None or other == op.key

    def _join(self, op: _Op, entry: StoredEntry) -> None:
        op.n += 1
        ids = op.ids
        if len(ids) < MAX_ENTRY_IDS:
            ids.append(entry.id)
        else:
            op.dropped += 1
        if entry.tpl_code in op.rule.business:
            self._join_code(op, entry)
        raw = entry.fields_raw
        if raw is None or (raw and ('"codes"' in raw or '"exc_type"' in raw)):
            self._join_fields(op, entry.fields)

    @staticmethod
    def _join_code(op: _Op, entry: StoredEntry) -> None:
        if op.biz is None:
            op.biz = set()
        op.biz.add(entry.tpl_code)  # type: ignore[arg-type]

    @staticmethod
    def _join_fields(op: _Op, fields: dict[str, Any]) -> None:
        codes = fields.get("codes")
        if isinstance(codes, list):
            for code in codes:
                if op.codes is None:
                    op.codes = []
                if len(op.codes) < MAX_ERROR_CODES and str(code) not in op.codes:
                    op.codes.append(str(code))
        if op.exc is None and fields.get("exc_type"):
            text = f"{fields['exc_type']}: {fields.get('exc_msg') or ''}".strip().rstrip(":")
            op.exc = text.split("\n", 1)[0][:MAX_TEXT]

    def _unregister(self, op: _Op) -> None:
        if op.pair is not None:
            paired = self._by_pair.get(op.pair)
            if paired is not None:
                try:
                    paired.remove(op)
                except ValueError:
                    pass
                if not paired:
                    del self._by_pair[op.pair]
            return
        if op.tid is None:
            return
        ops = self._by_tid.get(op.tid)
        if ops is not None:
            try:
                ops.remove(op)
            except ValueError:
                pass
            if not ops:
                del self._by_tid[op.tid]

    def _start(self, rule: _Rule, entry: StoredEntry, out: list[Event]) -> bool:
        pair: tuple[str, str] | None = None
        if rule.pair_key:
            value = _key_of(entry, rule.pair_key)
            if value is None:
                self.keyless_starts += 1
                return False
            pair = (rule.pair_key, value)
            slot: tuple[int, Any] = (rule.idx, value)
        else:
            slot = (rule.idx, entry.tid)
        old = self._open.pop(slot, None)
        if old is not None:
            self._unregister(old)
            self.overlaps += 1
            out.append(self._event(old, STATUS_TRUNCATED, None, closed_by="new_start"))
        name, from_field = rule.name, False
        if name is None and rule.name_field:
            value = entry.field(rule.name_field)
            if value is not None and not isinstance(value, (list, dict)):
                name, from_field = str(value), True
        if name is None:
            name = entry.tpl_code
        if rule.check_key:
            key = _key_of(entry, rule.check_key)
        else:
            key = None
            for fallback in _FALLBACK_KEYS:
                key = _key_of(entry, fallback)
                if key:
                    break
            else:
                key = None
        ts = entry.ts if entry.ts is not None else self._last_ts
        op = _Op(rule, entry, name, from_field, key, ts)
        op.slot = slot
        self._open[slot] = op
        if pair is not None:
            op.pair = pair
            self._by_pair.setdefault(pair, []).append(op)
        elif entry.tid is not None:
            self._by_tid.setdefault(entry.tid, []).append(op)
        self._join(op, entry)
        while len(self._open) > MAX_OPEN:
            _, oldest = self._open.popitem(last=False)
            self._unregister(oldest)
            self.evicted += 1
            out.append(self._event(oldest, STATUS_TRUNCATED, None, closed_by="evicted"))
        return True

    def _match(self, rule: _Rule, entry: StoredEntry) -> _Op | None:
        if rule.pair_key:
            value = _key_of(entry, rule.pair_key)
            op = None if value is None else self._open.get((rule.idx, value))
        else:
            op = self._open.get((rule.idx, entry.tid))
        if op is None:
            return None
        if rule.check_key and op.key is not None:
            other = _key_of(entry, rule.check_key)
            if other is not None and other != op.key:
                return None
        if op.name_from_field and rule.name_field:
            other = entry.field(rule.name_field)
            if other is not None and str(other) != op.name:
                return None
        return op

    def _fail(self, rule: _Rule, entry: StoredEntry) -> bool:
        op = self._match(rule, entry)
        if op is None:
            return False
        if not op.failed:
            op.failed = True
            op.fail_entry = entry.id
            op.fail_how = "fail_code"
        self._join(op, entry)
        return True

    def _end(self, rule: _Rule, role: str, entry: StoredEntry, out: list[Event]) -> bool:
        op = self._match(rule, entry)
        if op is None:
            return False
        self._join(op, entry)
        del self._open[op.slot]
        self._unregister(op)
        how = None
        if role == _END_FAIL:
            how = "end_fail"
        elif role == _END and _FAIL_TEXT.search(entry.msg or ""):
            how = "end_text"
        if how is not None and not op.failed:
            op.failed, op.fail_how, op.fail_entry = True, how, entry.id
        if op.failed:
            status = STATUS_FAIL
        elif rule.business and rule.business <= (op.biz or _NO_CODES):
            status = STATUS_BUSINESS_ERROR
        else:
            status = STATUS_OK
        out.append(self._event(op, status, entry))
        return True

    def _event(self, op: _Op, status: str, end: StoredEntry | None, *, closed_by: str | None = None) -> Event:
        detail: dict[str, Any] = {}
        if status == STATUS_TRUNCATED and op.failed:
            status = STATUS_FAIL  # a seen fail code wins over the missing end
        if op.codes:
            detail["error_codes"] = list(op.codes)
        if op.exc:
            detail["exception"] = op.exc
        if op.failed:
            detail["fail_how"] = op.fail_how
            detail["fail_entry_id"] = op.fail_entry
        if op.dropped:
            detail["entry_ids_dropped"] = op.dropped
        if closed_by:
            detail["closed_by"] = closed_by
        if op.skipped:
            detail["skipped_range"] = True
        ts_end = end.ts if end is not None else None
        duration = None
        if ts_end is not None:
            if ts_end >= op.ts_start:
                duration = ts_end - op.ts_start
            else:
                detail["ts_out_of_order"] = True
        return Event(
            kind=self.kind,
            ts_start=op.ts_start,
            name=op.name,
            key=op.key if op.key is not None else (op.pair[1] if op.pair else None),
            tid=op.tid,
            start_entry_id=op.start_id,
            end_entry_id=end.id if end is not None else None,
            ts_end=ts_end,
            duration_ms=duration,
            status=status,
            sev=_SEV[status],
            n_entries=op.n,
            detail=detail,
            entry_ids=list(op.ids),
        )
