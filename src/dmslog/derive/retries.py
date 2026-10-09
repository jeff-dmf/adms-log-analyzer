"""Retry-sequence deriver (ARQUITETURA 6.5, row ``retries``).

Smn and Ont re-send the same message (same ``msgId``) when an attempt fails: the log shows
``Sending "<Type>" message: <guid> with verb: <Verb>...`` once per attempt (SMN-007,
ONT-011, ONT-016A), ``Error: Sending "<Type>" message failed. "<Exception>" has occurred...``
for each failed attempt (SMN-019, ONT-020; it carries no msgId, so it is attributed to the
last attempt of the same TID), and one of ``... finished successfully.`` (SMN-008, ONT-012,
ONT-016B, SMN-010) or ``Request message with MessageId '<guid>' unsuccessfully sent`` (SMN-017,
SMN-018, ONT-022) at the end.

One ``retry_seq`` event is produced per message that needed a retry (two or more attempts, or
at least one failed attempt, or a final failure). ``detail.op_key`` is the msgId, the key of
the ``operation`` event of the same message, so the engine/API can join them. Messages sent at
the first attempt produce no event. ``sev``: the most severe failure entry of the sequence;
a sequence that ended ``sent`` is capped at 3 (recovered).

Memory is bounded: at most ``MAX_OPEN`` open messages (the oldest is evicted as ``open``
and counted in ``evicted_count`` and ``detail.evicted``). Entries are not attached to the
event (``entry_ids`` stays empty): they already belong to the ``operation`` event.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from dmslog.derive.base import STATUS_FAIL, STATUS_OK, STATUS_OPEN, Event, StoredEntry

MAX_OPEN = 1024
MAX_INTERVALS = 64
MAX_EXC_TYPES = 5
MAX_TID_MAP = 4096

ATTEMPT_CODES = frozenset({"ONT-011", "ONT-016A", "SMN-007"})
SENT_CODES = frozenset({"ONT-012", "ONT-016B", "SMN-008", "SMN-010"})
ATTEMPT_FAILED_CODES = frozenset({"ONT-020", "SMN-019"})
FINAL_FAILED_CODES = frozenset({"ONT-022", "SMN-017", "SMN-018"})
_ALL_CODES = ATTEMPT_CODES | SENT_CODES | ATTEMPT_FAILED_CODES | FINAL_FAILED_CODES

_GUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
# Fallbacks for entries without a template (tpl_code None): the same shapes, by text.
_RX_ATTEMPT = re.compile(rf'^Sending "(?P<t>\w+)" message: (?P<id>{_GUID})(?: with verb: \w+)?\.\.\.$')
_RX_SENT = re.compile(
    rf'^Sending "(?P<t>\w+)" message: (?P<id>{_GUID})(?: with verb: \w+)? finished successfully\.$'
)
_RX_OK = re.compile(rf"^Request message with MessageId '(?P<id>{_GUID})' successfully sent\.")
_RX_FINAL = re.compile(rf"^Request message with MessageId '(?P<id>{_GUID})' unsuccessfully sent\.")
_RX_ATT_FAIL = re.compile(r'^Error: Sending "(?P<t>[^"]*)" message failed\. "(?P<x>[^"]*)" has occurred')

_ATTEMPT, _SENT, _ATT_FAIL, _FINAL = "attempt", "sent", "attempt_failed", "final_failed"


@dataclass(slots=True)
class _Seq:
    op_key: str
    first_entry_id: int
    tid: int | None
    msg_type: str | None
    first_ts: int
    last_ts: int
    last_attempt_ts: int | None = None
    attempts: int = 0
    failures: int = 0
    last_entry_id: int = 0
    sev: int = 4
    intervals_ms: list[int] = field(default_factory=list)
    exc_types: list[str] = field(default_factory=list)
    final_failed: bool = False


def _msg_id(entry: StoredEntry, regex_id: str | None) -> str | None:
    for key_type, value in entry.keys:
        if key_type == "msgid" and value:
            return value.strip().lower()
    for name in ("message_id", "msg_id", "msgId"):
        v = entry.field(name)
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    return regex_id.lower() if regex_id else None


class RetryDeriver:
    """Retry sequences by msgId; see the module docstring."""

    kind = "retry_seq"

    def __init__(self, family: str, rules: Any = None, *, max_open: int = MAX_OPEN) -> None:
        self.family = family
        self.rules = rules
        self.max_open = max(1, max_open)
        self.evicted_count = 0
        self._open: OrderedDict[str, _Seq] = OrderedDict()
        self._tid_last: OrderedDict[int, str] = OrderedDict()

    # ------------------------------------------------------------------ classification
    def _classify(self, entry: StoredEntry) -> tuple[str, str | None, str | None, str | None] | None:
        """(role, msg_id, msg_type, exc_type) or None when the entry is not part of a retry flow."""
        code = entry.tpl_code
        if code is not None:
            if code in ATTEMPT_CODES:
                role = _ATTEMPT
            elif code in SENT_CODES:
                role = _SENT
            elif code in ATTEMPT_FAILED_CODES:
                role = _ATT_FAIL
            elif code in FINAL_FAILED_CODES:
                role = _FINAL
            else:
                return None
            rid = None
        else:
            msg = entry.msg.strip()
            m = None
            role = ""
            for candidate, rx in (
                (_ATTEMPT, _RX_ATTEMPT),
                (_SENT, _RX_SENT),
                (_SENT, _RX_OK),
                (_FINAL, _RX_FINAL),
                (_ATT_FAIL, _RX_ATT_FAIL),
            ):
                m = rx.match(msg)
                if m:
                    role = candidate
                    break
            if m is None:
                return None
            rid = m.groupdict().get("id")
        msg_type = entry.field("msg_type")
        if not isinstance(msg_type, str):
            msg_type = None
        exc = entry.field("exc_type")
        return role, _msg_id(entry, rid), msg_type, exc if isinstance(exc, str) else None

    # ------------------------------------------------------------------ Deriver protocol
    def feed(self, entry: StoredEntry) -> Iterable[Event]:
        code = entry.tpl_code
        if code is not None:
            if code not in _ALL_CODES:
                return ()
        elif 'Sending "' not in entry.msg and "MessageId" not in entry.msg:
            return ()  # every text fallback needs one of these (cheap exit for the common case)
        try:
            return self._feed(entry)
        except Exception:  # noqa: BLE001 - a deriver must never raise on odd input
            return ()

    def _feed(self, entry: StoredEntry) -> list[Event]:
        info = self._classify(entry)
        if info is None:
            return []
        role, msg_id, msg_type, exc = info
        ts = entry.ts if entry.ts is not None else 0
        out: list[Event] = []
        if role == _ATTEMPT:
            if msg_id is None:
                return out
            seq = self._open.get(msg_id)
            if seq is None:
                seq = _Seq(msg_id, entry.id, entry.tid, msg_type, ts, ts)
                self._open[msg_id] = seq
                out.extend(self._evict())
            if seq.last_attempt_ts is not None and entry.ts is not None:
                seq.intervals_ms.append(max(0, ts - seq.last_attempt_ts))
                del seq.intervals_ms[MAX_INTERVALS:]
            if entry.ts is not None:
                seq.last_attempt_ts = ts
            seq.attempts += 1
            self._touch(seq, entry, ts)
            if entry.tid is not None:
                self._tid_last[entry.tid] = msg_id
                self._tid_last.move_to_end(entry.tid)
                while len(self._tid_last) > MAX_TID_MAP:
                    self._tid_last.popitem(last=False)
            return out
        if role == _ATT_FAIL:
            if msg_id is None and entry.tid is not None:
                msg_id = self._tid_last.get(entry.tid)
            seq = self._open.get(msg_id) if msg_id else None
            if seq is None:
                return out
            seq.failures += 1
            seq.sev = min(seq.sev, entry.sev)
            if exc and exc not in seq.exc_types and len(seq.exc_types) < MAX_EXC_TYPES:
                seq.exc_types.append(exc)
            self._touch(seq, entry, ts)
            return out
        # _SENT or _FINAL: the sequence ends
        seq = self._open.pop(msg_id, None) if msg_id else None
        if seq is None:
            return out
        self._forget_tid(seq)
        self._touch(seq, entry, ts)
        if role == _FINAL:
            seq.final_failed = True
            seq.sev = min(seq.sev, entry.sev)
            outcome = "failed"
        else:
            outcome = "sent"
        event = self._event(seq, outcome)
        if event is not None:
            out.append(event)
        return out

    def flush(self, *, eof: bool) -> Iterable[Event]:
        if not eof:
            return ()
        events = []
        for seq in self._open.values():
            event = self._event(seq, "open")
            if event is not None:
                events.append(event)
        self._open.clear()
        self._tid_last.clear()
        return events

    def low_watermark(self) -> int | None:
        if not self._open:
            return None
        return min(seq.first_entry_id for seq in self._open.values())

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _touch(seq: _Seq, entry: StoredEntry, ts: int) -> None:
        seq.last_entry_id = entry.id
        if entry.ts is not None:
            seq.last_ts = max(seq.last_ts, ts) if seq.last_ts else ts

    def _forget_tid(self, seq: _Seq) -> None:
        if seq.tid is not None and self._tid_last.get(seq.tid) == seq.op_key:
            del self._tid_last[seq.tid]

    def _evict(self) -> list[Event]:
        out: list[Event] = []
        while len(self._open) > self.max_open:
            _, seq = self._open.popitem(last=False)
            self._forget_tid(seq)
            self.evicted_count += 1
            event = self._event(seq, "open", evicted=True)
            if event is not None:
                out.append(event)
        return out

    def _event(self, seq: _Seq, outcome: str, *, evicted: bool = False) -> Event | None:
        qualifies = seq.attempts >= 2 or seq.failures >= 1 or seq.final_failed
        if not qualifies:
            return None
        detail: dict[str, Any] = {
            "op_key": seq.op_key,
            "attempts": seq.attempts,
            "failures": seq.failures,
            "first_ts": seq.first_ts,
            "last_ts": seq.last_ts,
            "intervals_ms": list(seq.intervals_ms),
            "outcome": outcome,
        }
        if seq.exc_types:
            detail["exc_types"] = list(seq.exc_types)
        if seq.msg_type:
            detail["msg_type"] = seq.msg_type
        if evicted:
            detail["evicted"] = True
        status = {"sent": STATUS_OK, "failed": STATUS_FAIL}.get(outcome, STATUS_OPEN)
        sev = max(seq.sev, 3) if outcome == "sent" else seq.sev
        return Event(
            kind=self.kind,
            ts_start=seq.first_ts,
            ts_end=seq.last_ts,
            duration_ms=max(0, seq.last_ts - seq.first_ts),
            name=seq.msg_type,
            key=seq.op_key,
            tid=seq.tid,
            start_entry_id=seq.first_entry_id,
            end_entry_id=seq.last_entry_id,
            status=status,
            sev=sev,
            n_entries=None,
            detail=detail,
        )
