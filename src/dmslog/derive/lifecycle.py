"""Life-cycle derivers (ARQUITETURA 6.5, ROADMAP 4.4): startup, level, gaps, clock, maps, service.

``LifecycleDeriver`` is ONE deriver object that runs one small state machine per rule over the
entries of a single stream (fed in ``entry.id`` order). Every machine is deterministic, O(1)
amortized per entry, keeps bounded state (caps below, overflow is counted in ``detail``), never
raises on odd input (``ts``/``tid`` None, ``ts`` out of order) and never alters an entry.

Rules and events
----------------
==============  ==================  ====================================================
rule            event kind          what
==============  ==================  ====================================================
startup         ``startup``         first ``[Configuration]``/``[ServiceUrl]``/``=====``
                                    line until 5 s after ``Server state changed. New
                                    state: HOT`` (or 120 s without HOT). ``entry_ids`` =
                                    every entry of the block (the engine sets flags & 8).
level           ``level_window``    window between two ``Debug level changed from X (n)
                                    to Y (m).`` lines. ``detail``: ``from``, ``to``,
                                    ``from_num``, ``to_num``, ``level_name``,
                                    ``level_num`` (the level DURING the window; the main
                                    session decides flags & 16 from it). The first window
                                    starts at the first entry of the stream with the
                                    configured level unknown: ``from`` is None and
                                    ``level_name``/``level_num`` come from the ``from`` side
                                    of the first change (what was in force before it). A
                                    stream without any change yields no level window.
gaps            ``gap``             silence between two consecutive entries longer than
                                    the family heartbeat; only between entries of the
                                    stream (the period covered by the file), never before
                                    the first or after the last entry. Not between the last
                                    entry before a stretch skipped by a reduced source and the
                                    first one after it (entry.flags & 128). A level window
                                    that spans such a stretch has ``detail.skipped_range = true``
                                    (its duration is not reliable).
clock           ``clock_jump``      ``ts`` goes backwards by more than 30 min between two
                                    consecutive entries (``detail.back_ms``).
maps            ``map_reload``      ``"X" map closed.`` -> ``New "X" map content:`` (+
                                    items) -> ``"X" map is now open.``
pid_change      ``restart``         PID of the envelope changes between consecutive entries
                                    of the stream (families in ``PID_CHANGE_FAMILIES``).
                                    ``detail``: ``old_pid``, ``new_pid``, ``cause``
                                    (``"pid_change"``). Policy: see ``_PidChange``.
service         ``restart``,        ProcessMonitor / dnaService / arb* (family SERVICE,
                ``service_state``   templates ``SVC-*``).
==============  ==================  ====================================================

Gap heartbeat (silence limit per family)
----------------------------------------
Resolution order: constructor option ``gap_heartbeat_s`` > attribute ``gap_heartbeat`` of
``rules`` (seconds; the TemplateSet loader does not expose it yet, see ``resolve_heartbeat_ms``)
> ``DEFAULT_GAP_HEARTBEAT_S`` below. A family without heartbeat generates no gap events.
Defaults: AAVL, AOSR, ASMN (hourly communication statistics) = 61 min; ASSS (SiteSwitch) = 5 s.
A gap is a silence STRICTLY longer than the heartbeat.

Re-derivation contract
----------------------
``low_watermark()`` returns the smallest ``entry.id`` that must be fed again: the start of any
open startup / level window / map reload, the first entry of the stream while no level change
was seen (the first window starts there), and the LAST entry fed (the previous-timestamp state of
``gaps`` and ``clock``). Events are emitted only when complete; ``flush(eof=False)`` emits
nothing and ``flush(eof=True)`` emits the open ones with status ``open``.
The engine passes ``stream_start=False`` when it resumes from a checkpoint that is not the first
entry of the stream (then the first level window, which starts at the stream start, is not
re-created). Feeding from the
watermark gives the same events as the whole stream for every event with
``start_entry_id >= watermark`` (property tested).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from typing import Any

from dmslog.derive.base import FLAG_AFTER_SKIP, STATUS_OK, STATUS_OPEN, STATUS_TRUNCATED, Event, StoredEntry

KIND_STARTUP = "startup"
KIND_LEVEL = "level_window"
KIND_GAP = "gap"
KIND_CLOCK = "clock_jump"
KIND_MAP = "map_reload"
KIND_RESTART = "restart"
KIND_SERVICE_STATE = "service_state"

#: Startup block: closes this long after the HOT marker, or after this long without it.
STARTUP_HOT_GRACE_MS = 5_000
STARTUP_MAX_MS = 120_000
#: ``ts`` going backwards by more than this between two consecutive entries is a clock jump.
CLOCK_JUMP_MIN_BACK_MS = 30 * 60 * 1000
#: Families whose process restarts are derived from a change of the envelope PID.
PID_CHANGE_FAMILIES = frozenset({"DMSFH"})
#: Caps (bounded memory): overflow is counted in ``detail``.
MAX_STARTUP_ENTRY_IDS = 50_000
MAX_STARTUP_URLS = 50
MAX_OPEN_MAPS = 32
#: Entry ids attached to one map reload event (the rest is counted in ``detail.entry_ids_dropped``).
#: The startup block is capped by ``MAX_STARTUP_ENTRY_IDS``; level and service events attach no ids.
MAX_MAP_ENTRY_IDS = 64
#: A second ``closed`` for a map whose reload is pending, within this long of the last marker, is a repeat.
DUP_CLOSED_MS = 1_000
MAX_MAP_ITEM_LINES_SCAN = 1_000_000

#: Silence limit by family, in seconds (see the module docstring).
DEFAULT_GAP_HEARTBEAT_S: dict[str, float] = {
    "AAVL": 61 * 60,
    "AOSR": 61 * 60,
    "ASMN": 61 * 60,
    "ASSS": 5,
}

# Template codes of the catalog (src/dmslog/adapters/_common.toml and service.toml). Old IDs
# are resolved to canonical codes by the loader; they are listed here only for safety.
_CODES_LEVEL = frozenset({"GEN-DBG-01", "WFM-099", "ONT-099"})
_CODES_HOT = frozenset({"GEN-STU-01"})
_CODES_PORT = frozenset({"GEN-STU-04"})
_CODES_SERVICE_URL = frozenset({"GEN-CFG-04"})
_CODES_CONFIG = frozenset({"GEN-CFG-01", "GEN-CFG-02", "GEN-CFG-03", "GEN-CFG-06", "GEN-CFG-07"})
_CODES_MAP = frozenset({"GEN-MAP-01", "WFM-002", "WFM-003"})
_CODES_MAP_ENTRY = frozenset({"GEN-MAP-02"})
_MAP_ANY = _CODES_MAP | _CODES_MAP_ENTRY

_START_PREFIXES = ("[Configuration]", "[ServiceUrl]", "=====")

# Fallbacks for records without a template (e.g. families outside the catalog).
_RE_LEVEL = re.compile(r"^Debug level changed from (\w+) \((\d+)\) to (\w+) \((\d+)\)\.$")
_RE_HOT = re.compile(r"^\[SystemStatusPublisher\] - Server state changed\. New state: (\w+)\.?$")
_RE_MAP = re.compile(r'^(?:New )?"([^"]*)" map (content:|is now open\.|closed\.)$')


def resolve_heartbeat_ms(family: str, rules: Any = None, option_s: float | None = None) -> int | None:
    """Silence limit of the family in ms (None = no gap detection)."""
    value: Any = option_s
    if value is None:
        value = getattr(rules, "gap_heartbeat", None)
    if value is None:
        value = DEFAULT_GAP_HEARTBEAT_S.get(family)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(round(value * 1000))


def _field(entry: StoredEntry, *names: str) -> Any:
    for name in names:
        value = entry.field(name)
        if value is not None and value != "":
            return value
    return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------------------------
# startup
# ---------------------------------------------------------------------------------------------
class _Startup:
    def __init__(self) -> None:
        self._reset()

    def _reset(self) -> None:
        self.open = False
        self.start_id = 0
        self.start_ts: int | None = None
        self.last_id = 0
        self.last_ts: int | None = None
        self.deadline: int | None = None
        self.hot_ts: int | None = None
        self.hot_id: int | None = None
        self.ids: list[int] = []
        self.n = 0
        self.dropped_ids = 0
        self.n_config = 0
        self.port: int | None = None
        self.urls: list[str] = []
        self.dropped_urls = 0

    @staticmethod
    def is_trigger(e: StoredEntry) -> bool:
        return e.msg.lstrip().startswith(_START_PREFIXES)

    @staticmethod
    def hot_state(e: StoredEntry) -> str | None:
        if e.tpl_code in _CODES_HOT:
            state = _field(e, "state")
            return str(state) if state is not None else None
        if e.tpl_code is None:
            m = _RE_HOT.match(e.msg.strip())
            return m.group(1) if m else None
        return None

    def _begin(self, e: StoredEntry) -> None:
        self._reset()
        self.open = True
        self.start_id = e.id
        self.start_ts = e.ts
        self.deadline = None if e.ts is None else e.ts + STARTUP_MAX_MS

    def _close(self, closed_by: str, *, status: str) -> Event | None:
        self.open = False
        if self.start_ts is None:
            # No timestamp seen in the block: nothing to anchor an event on.
            return None
        end_ts = self.last_ts if self.last_ts is not None else self.start_ts
        detail: dict[str, Any] = {"closed_by": closed_by, "config_lines": self.n_config}
        if self.hot_ts is not None:
            detail["hot_ts"] = self.hot_ts
            detail["hot_entry_id"] = self.hot_id
        if self.port is not None:
            detail["port"] = self.port
        if self.urls:
            detail["service_urls"] = list(self.urls)
        if self.dropped_urls:
            detail["service_urls_dropped"] = self.dropped_urls
        if self.dropped_ids:
            detail["entry_ids_dropped"] = self.dropped_ids
        return Event(
            kind=KIND_STARTUP,
            ts_start=self.start_ts,
            ts_end=end_ts,
            duration_ms=max(0, end_ts - self.start_ts),
            start_entry_id=self.start_id,
            end_entry_id=self.last_id,
            status=status,
            sev=4,
            n_entries=self.n,
            detail=detail,
            entry_ids=list(self.ids),
        )

    def feed(self, e: StoredEntry) -> list[Event]:
        out: list[Event] = []
        if self.open and e.ts is not None and self.deadline is not None and e.ts > self.deadline:
            ev = self._close("hot" if self.hot_ts is not None else "timeout", status=STATUS_OK)
            if ev:
                out.append(ev)
        if not self.open:
            if not self.is_trigger(e):
                return out
            self._begin(e)
        # inside the block
        self.n += 1
        if len(self.ids) < MAX_STARTUP_ENTRY_IDS:
            self.ids.append(e.id)
        else:
            self.dropped_ids += 1
        self.last_id = e.id
        if e.ts is not None:
            self.last_ts = e.ts
            if self.start_ts is None:  # block started on a record without ts
                self.start_ts = e.ts
                self.deadline = e.ts + STARTUP_MAX_MS
        if e.tpl_code in _CODES_CONFIG or e.msg.startswith("[Configuration]"):
            self.n_config += 1
        if e.tpl_code in _CODES_PORT and self.port is None:
            self.port = _as_int(_field(e, "port"))
        if e.tpl_code in _CODES_SERVICE_URL:
            url = _field(e, "url")
            if url is not None:
                if len(self.urls) < MAX_STARTUP_URLS:
                    self.urls.append(str(url))
                else:
                    self.dropped_urls += 1
        if self.hot_ts is None and self.hot_state(e) == "HOT" and e.ts is not None:
            self.hot_ts = e.ts
            self.hot_id = e.id
            assert self.start_ts is not None
            self.deadline = min(e.ts + STARTUP_HOT_GRACE_MS, self.start_ts + STARTUP_MAX_MS)
        return out

    def flush(self, eof: bool) -> list[Event]:
        if not (eof and self.open):
            return []
        ev = self._close("eof", status=STATUS_OK if self.hot_ts is not None else STATUS_OPEN)
        return [ev] if ev else []

    def watermark(self) -> int | None:
        return self.start_id if self.open else None


# ---------------------------------------------------------------------------------------------
# level
# ---------------------------------------------------------------------------------------------
class _Level:
    def __init__(self, stream_start: bool = True) -> None:
        self.first_id: int | None = None  # first entry with ts, until the first change
        self.first_ts: int | None = None
        # Resuming mid-stream: the first window (before any change) belongs to an earlier pass.
        self.seen_change = not stream_start
        self.cur: dict[str, Any] | None = None  # the open window
        self.last_id: int | None = None
        self.prev_id: int | None = None  # the entry before ``last_id``
        self.last_ts: int | None = None
        self.n = 0
        self.first_skipped = False  # a skipped stretch lies inside the first window (before any change)

    def skip(self) -> None:
        """A record that follows a stretch skipped by a reduced source was fed: the open windows span it."""
        if self.cur is not None:
            self.cur["skipped"] = True
        if not self.seen_change and self.first_id is not None:
            self.first_skipped = True

    @staticmethod
    def parse(e: StoredEntry) -> tuple[str, int | None, str, int | None] | None:
        if e.tpl_code in _CODES_LEVEL:
            fn, tn = _field(e, "from_name"), _field(e, "to_name")
            if fn is None or tn is None:
                return None
            return str(fn), _as_int(_field(e, "from_n")), str(tn), _as_int(_field(e, "to_n"))
        if e.tpl_code is None:
            m = _RE_LEVEL.match(e.msg.strip())
            if m:
                return m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
        return None

    def _event(self, w: dict[str, Any], end_id: int | None, end_ts: int | None, status: str) -> Event:
        ts_end = end_ts if end_ts is not None else w["ts"]
        n = w["n"] if "base" not in w else self.n - w["base"]  # entries seen since the window opened
        return Event(
            kind=KIND_LEVEL,
            name=w["level_name"],
            ts_start=w["ts"],
            ts_end=ts_end,
            duration_ms=max(0, ts_end - w["ts"]),
            start_entry_id=w["id"],
            end_entry_id=end_id,
            status=status,
            sev=4,
            n_entries=n,
            detail={
                "from": w["from"],
                "to": w["to"],
                "from_num": w["from_num"],
                "to_num": w["to_num"],
                "level_name": w["level_name"],
                "level_num": w["level_num"],
                **({"skipped_range": True} if w.get("skipped") else {}),
            },
        )

    def change(self, e: StoredEntry) -> list[Event]:
        """Handle an entry that may be a level change (``n`` still excludes this entry)."""
        out: list[Event] = []
        change = self.parse(e)
        if change is not None:
            frm, frm_n, to, to_n = change
            ts = e.ts if e.ts is not None else self.last_ts
            if ts is not None:
                # The change entry may be the tail record that the ingestion deletes and re-adds, and a
                # resumed pass does not re-emit a window that began before it: end windows one entry earlier.
                end_id = self.prev_id if self.prev_id is not None else e.id
                if self.cur is not None:
                    out.append(self._event(self.cur, end_id, ts, STATUS_OK))
                elif not self.seen_change and self.first_id is not None and self.first_ts is not None:
                    first = {
                        "id": self.first_id,
                        "ts": self.first_ts,
                        "n": self.n,
                        "from": None,
                        "to": frm,
                        "from_num": None,
                        "to_num": frm_n,
                        "level_name": frm,
                        "level_num": frm_n,
                        "skipped": self.first_skipped,
                    }
                    out.append(self._event(first, end_id if self.n > 0 else e.id, ts, STATUS_OK))
                self.seen_change = True
                self.first_id = self.first_ts = None
                self.first_skipped = False
                self.cur = {
                    "id": e.id,
                    "ts": ts,
                    "base": self.n,  # ``n`` counts every entry; the window's own count is derived from it
                    "from": frm,
                    "to": to,
                    "from_num": frm_n,
                    "to_num": to_n,
                    "level_name": to,
                    "level_num": to_n,
                }
        return out

    def flush(self, eof: bool) -> list[Event]:
        if not eof or self.cur is None:
            return []
        return [self._event(self.cur, self.last_id, self.last_ts, STATUS_OPEN)]

    def watermark(self) -> int | None:
        marks = []
        if self.cur is not None:
            marks.append(self.cur["id"])
        if not self.seen_change and self.first_id is not None:
            marks.append(self.first_id)
        return min(marks) if marks else None


# ---------------------------------------------------------------------------------------------
# maps
# ---------------------------------------------------------------------------------------------
class _Maps:
    def __init__(self) -> None:
        self.open: dict[str, dict[str, Any]] = {}
        self.dropped = 0

    @staticmethod
    def parse(e: StoredEntry) -> tuple[str, str] | None:
        """(map name, state) with state in {'closed', 'content', 'open'} or None."""
        if e.tpl_code in _CODES_MAP:
            name, state = _field(e, "map"), _field(e, "state")
            if name is None or state is None:
                return None
            s = str(state)
        elif e.tpl_code is None:
            m = _RE_MAP.match(e.msg.strip())
            if not m:
                return None
            name, s = m.group(1), m.group(2)
        else:
            return None
        if s.startswith("closed"):
            return str(name), "closed"
        if s.startswith("content"):
            return str(name), "content"
        if s.startswith("is now open"):
            return str(name), "open"
        if s.startswith("cleared"):
            return str(name), "cleared"
        if s.startswith("integrity"):
            return str(name), "integrity"
        return None

    def _event(self, r: dict[str, Any], status: str, end_id: int, end_ts: int | None) -> Event:
        ts_end = end_ts if end_ts is not None else r["ts"]
        items = r["dump_items"] if r["dump_items"] is not None else r["added"]
        detail: dict[str, Any] = {"map": r["map"], "items": items}
        if r["dups"]:
            detail["closed_repeats"] = r["dups"]
        if r["n"] > len(r["ids"]):
            detail["entry_ids_dropped"] = r["n"] - len(r["ids"])
        if r["cleared"]:
            detail["cleared"] = True
        if r["integrity"]:
            detail["integrity_update"] = True
        if self.dropped:
            detail["maps_dropped"] = self.dropped
        return Event(
            kind=KIND_MAP,
            name=r["map"],
            ts_start=r["ts"],
            ts_end=ts_end,
            duration_ms=max(0, ts_end - r["ts"]),
            start_entry_id=r["id"],
            end_entry_id=end_id,
            status=status,
            sev=4,
            n_entries=r["n"],
            detail=detail,
            entry_ids=list(r["ids"]),
        )

    @staticmethod
    def _attach(r: dict[str, Any], entry_id: int) -> None:
        r["n"] += 1
        if len(r["ids"]) < MAX_MAP_ENTRY_IDS:
            r["ids"].append(entry_id)

    def feed(self, e: StoredEntry) -> list[Event]:
        out: list[Event] = []
        if e.tpl_code in _CODES_MAP_ENTRY:
            r = self.open.get(str(_field(e, "map")))
            if r is not None and _field(e, "action") in ("added", "updated"):
                r["added"] += 1
            return out
        parsed = self.parse(e)
        if parsed is None:
            return out
        name, state = parsed
        if state == "closed":
            if e.ts is None:
                return out
            old = self.open.get(name)
            if old is not None and old["last_ts"] is not None and abs(e.ts - old["last_ts"]) <= DUP_CLOSED_MS:
                # The adapter logs "closed" twice, a few ms apart, for one reload (seen on real
                # captures): the repeat belongs to the pending reload, it does not restart it.
                old["dups"] += 1
                self._attach(old, e.id)
                old["last_id"] = e.id
                return out
            if old is not None:
                out.append(self._event(old, STATUS_TRUNCATED, old["last_id"], old["last_ts"]))
                del self.open[name]
            if len(self.open) >= MAX_OPEN_MAPS:
                self.dropped += 1
                return out
            self.open[name] = {
                "map": name,
                "id": e.id,
                "ts": e.ts,
                "ids": [e.id],
                "n": 1,
                "dump_items": None,
                "added": 0,
                "dups": 0,
                "cleared": False,
                "integrity": False,
                "last_id": e.id,
                "last_ts": e.ts,
            }
            return out
        r = self.open.get(name)
        if r is None:
            return out  # a dump or "is now open" without a previous "closed": not a reload
        self._attach(r, e.id)
        r["last_id"] = e.id
        if e.ts is not None:
            r["last_ts"] = e.ts
        if state == "cleared":
            r["cleared"] = True
        elif state == "integrity":
            r["integrity"] = True
        elif state == "content":
            lines = e.cont.count("\n") + 1 if e.cont.strip() else 0
            r["dump_items"] = (r["dump_items"] or 0) + min(lines, MAX_MAP_ITEM_LINES_SCAN)
        else:  # open
            out.append(self._event(r, STATUS_OK, e.id, e.ts))
            del self.open[name]
        return out

    def flush(self, eof: bool) -> list[Event]:
        if not eof:
            return []
        out = [self._event(r, STATUS_OPEN, r["last_id"], r["last_ts"]) for r in self.open.values()]
        self.open.clear()
        return out

    def watermark(self) -> int | None:
        return min((r["id"] for r in self.open.values()), default=None)


# ---------------------------------------------------------------------------------------------
# service (family SERVICE: ProcessMonitor, dnaService, arb*)
# ---------------------------------------------------------------------------------------------
# template code -> (event kind, detail.change)
_SERVICE_POINT: dict[str, tuple[str, str]] = {
    "SVC-001": (KIND_SERVICE_STATE, "state_changed"),
    "SVC-002": (KIND_SERVICE_STATE, "partner_lost"),
    "SVC-003": (KIND_SERVICE_STATE, "partner_back"),
    "SVC-004": (KIND_SERVICE_STATE, "arbitration"),
    "SVC-005": (KIND_RESTART, "process_dead"),
    "SVC-006": (KIND_RESTART, "process_restarting"),
    "SVC-009": (KIND_SERVICE_STATE, "shutdown_requested"),
    "SVC-010": (KIND_SERVICE_STATE, "start"),
    "SVC-011": (KIND_SERVICE_STATE, "started"),
    "SVC-012": (KIND_SERVICE_STATE, "shutdown"),
    "SVC-018": (KIND_SERVICE_STATE, "warm_start_failed"),
    "SVC-019": (KIND_SERVICE_STATE, "auto_shutdown"),
}


class _Service:
    @staticmethod
    def feed(e: StoredEntry) -> list[Event]:
        spec = _SERVICE_POINT.get(e.tpl_code or "")
        if spec is None or e.ts is None:
            return []
        kind, change = spec
        service = _field(e, "service", "service2", "service3")
        detail: dict[str, Any] = {"change": change, "code": e.tpl_code}
        name: str | None = None if service is None else str(service)
        if e.tpl_code == "SVC-001":
            detail.update(
                host=_field(e, "host"), from_state=_field(e, "from_state"), to_state=_field(e, "to_state")
            )
        elif e.tpl_code in ("SVC-002", "SVC-003"):
            detail.update(host=_field(e, "host"), partner=_field(e, "partner"))
            if e.tpl_code == "SVC-002":
                detail["to_state"] = "FAIL"
        elif e.tpl_code == "SVC-004":
            if _field(e, "assert_host"):
                detail.update(change="assert", host=_field(e, "assert_host"))
            elif _field(e, "standdown_host"):
                detail.update(change="stand_down", host=_field(e, "standdown_host"))
            elif e.msg.startswith("Replication timeout"):
                detail["change"] = "replication_timeout"
            else:
                detail["change"] = "replication_wait"
        elif e.tpl_code == "SVC-005":
            crit = _field(e, "crit", "crit2")
            process = _field(e, "process", "process2")
            detail.update(
                critical=None if crit is None else str(crit) == "Critical",
                process=process,
                pid=_field(e, "proc_pid"),
                host=_field(e, "host"),
            )
            name = None if process is None else str(process)
            if service is not None:
                detail["service"] = str(service)
        elif e.tpl_code == "SVC-006":
            process = _field(e, "process")
            detail.update(process=process, pid=_field(e, "proc_pid"), service=service)
            name = None if process is None else str(process)
        elif service is not None:
            detail["service"] = str(service)
        detail = {k: v for k, v in detail.items() if v is not None}
        return [
            Event(
                kind=kind,
                name=name,
                tid=e.tid,
                ts_start=e.ts,
                ts_end=e.ts,
                duration_ms=0,
                start_entry_id=e.id,
                end_entry_id=e.id,
                status=STATUS_OK,
                sev=e.sev,
                n_entries=1,
                detail=detail,
            )
        ]


class _PidChange:
    """``restart`` when the envelope PID of a stream changes (one stream = one chain of files).

    Policy (no false positives from interleaving or rotation):

    * Entries without a PID are ignored.
    * A change is only a candidate; it is confirmed when the NEW pid appears in two consecutive
      entries (with a PID). A single stray record (A-B-A) is therefore not a restart.
    * The event takes the timestamp of the FIRST entry with the new PID.
    * ``flush`` never confirms a pending candidate (a single record is not proof).
    * The state is only (current pid, pending candidate). A resumed run gets the confirmed PID in
      force before the first entry fed (``seed``; the engine takes it from the last kept ``restart``
      event). ``watermark()`` = the last entry that carried the current pid keeps a pending
      candidate re-derivable when this rule holds the checkpoint.
    """

    def __init__(self) -> None:
        self.cur: int | None = None
        self.cur_id: int | None = None  # last entry id carrying ``cur``
        self.cand: tuple[int, int, int | None, int | None, int] | None = None  # new, old, ts, tid, id

    def feed(self, e: StoredEntry) -> list[Event]:
        pid = e.pid
        if pid is None:
            return []
        cand = self.cand
        if cand is not None:
            self.cand = None
            if pid == cand[0]:
                self.cur, self.cur_id = pid, e.id
                return [self._confirm(cand, e)]
        if self.cur is None or pid == self.cur:
            self.cur, self.cur_id = pid, e.id
            return []
        if e.ts is None:
            return []
        self.cand = (pid, self.cur, e.ts, e.tid, e.id)
        return []

    @staticmethod
    def _confirm(cand: tuple[int, int, int | None, int | None, int], e: StoredEntry) -> Event:
        new, old, ts, tid, first_id = cand
        return Event(
            kind=KIND_RESTART,
            tid=tid,
            ts_start=ts,  # type: ignore[arg-type]
            ts_end=ts,
            duration_ms=0,
            start_entry_id=first_id,
            end_entry_id=first_id,
            status=STATUS_OK,
            sev=e.sev if e.sev is not None else 4,
            n_entries=1,
            detail={"old_pid": old, "new_pid": new, "cause": "pid_change"},
        )

    def seed(self, pid: int | None) -> None:
        """Resume context: PID of the entry just before the first one fed (``cur`` without a watermark)."""
        if pid is not None and self.cur is None:
            self.cur = pid

    def watermark(self) -> int | None:
        return self.cur_id


# ---------------------------------------------------------------------------------------------
# the deriver
# ---------------------------------------------------------------------------------------------
class LifecycleDeriver:
    """Startup, level windows, gaps, clock jumps, map reloads and service events of one stream."""

    kind = "lifecycle"

    def __init__(
        self,
        family: str,
        rules: Any = None,
        *,
        gap_heartbeat_s: float | None = None,
        stream_start: bool = True,
    ) -> None:
        self.family = family
        self.heartbeat_ms = resolve_heartbeat_ms(family, rules, gap_heartbeat_s)
        self._startup = _Startup()
        self._level = _Level(stream_start)
        # gaps and clock both compare an entry with the previous one that has a ts
        self._prev_id: int | None = None
        self._prev_ts: int | None = None
        self._after_skip = False  # the next timed entry follows a stretch skipped by a reduced source
        self._maps = _Maps()
        self._pid = _PidChange() if family in PID_CHANGE_FAMILIES else None
        self._last_id: int | None = None

    def feed(self, entry: StoredEntry) -> Iterable[Event]:
        out: list[Event] = []
        try:
            code = entry.tpl_code
            msg = entry.msg
            ts = entry.ts
            startup = self._startup
            if startup.open or msg.startswith(_START_PREFIXES) or msg[:1].isspace():
                out.extend(startup.feed(entry))
            level = self._level
            if ts is not None:
                if level.first_id is None and not level.seen_change:
                    level.first_id, level.first_ts = entry.id, ts
                level.last_ts = ts
            if entry.flags & FLAG_AFTER_SKIP:
                level.skip()
            level.prev_id = level.last_id
            level.last_id = entry.id
            if code in _CODES_LEVEL or (code is None and "Debug level changed" in msg):
                out.extend(level.change(entry))
            level.n += 1
            if entry.flags & FLAG_AFTER_SKIP:
                self._after_skip = True
            if ts is not None:
                prev_ts = self._prev_ts
                if prev_ts is not None:
                    silence = ts - prev_ts
                    hb = self.heartbeat_ms
                    if hb is not None and silence > hb and not self._after_skip:
                        out.append(self._gap(entry, prev_ts, silence, hb))
                    if -silence > CLOCK_JUMP_MIN_BACK_MS:
                        out.append(self._clock_jump(entry, prev_ts, -silence))
                self._prev_id, self._prev_ts = entry.id, ts
                self._after_skip = False
            if code in _MAP_ANY or (code is None and '" map ' in msg):
                out.extend(self._maps.feed(entry))
            if code in _SERVICE_POINT:
                out.extend(_Service.feed(entry))
            if self._pid is not None:
                out.extend(self._pid.feed(entry))
        finally:
            self._last_id = entry.id
        return out

    @property
    def tracks_pid(self) -> bool:
        return self._pid is not None

    def resume_level(self, *, first_window_open: bool, n_before: int, last_id: int | None) -> None:
        """Called by the engine when it resumes mid-chain. ``first_window_open``: no level window starts
        before the checkpoint, so the first window (before any change) is derived again from here; the
        entries before the checkpoint (all untimed then) are only counted (``n_before``, ``last_id``) so
        that its entry count and end id equal those of the whole run. Otherwise the saved windows stand."""
        level = self._level
        level.seen_change = not first_window_open
        if first_window_open:
            level.n = n_before
            level.last_id = last_id

    def seed_previous_pid(self, pid: int | None) -> None:
        """Called by the engine when it resumes mid-chain: the CONFIRMED PID in force before the first
        entry fed (not the last PID seen, which may be an isolated candidate). Another rule may hold
        the checkpoint earlier or later than this rule's own watermark, so the PID state cannot rely
        on its watermark alone."""
        if self._pid is not None:
            self._pid.seed(pid)

    def _gap(self, e: StoredEntry, prev_ts: int, silence: int, heartbeat_ms: int) -> Event:
        return Event(
            kind=KIND_GAP,
            ts_start=prev_ts,
            ts_end=e.ts,
            duration_ms=silence,
            start_entry_id=self._prev_id,
            end_entry_id=e.id,
            status=STATUS_OK,
            sev=3,
            detail={"heartbeat_ms": heartbeat_ms, "silence_ms": silence},
        )

    def _clock_jump(self, e: StoredEntry, prev_ts: int, back: int) -> Event:
        return Event(
            kind=KIND_CLOCK,
            ts_start=e.ts,  # type: ignore[arg-type]
            ts_end=prev_ts,
            duration_ms=back,
            start_entry_id=self._prev_id,
            end_entry_id=e.id,
            status=STATUS_OK,
            sev=3,
            detail={"back_ms": back, "ts_before": prev_ts, "ts_after": e.ts},
        )

    def flush(self, *, eof: bool) -> Iterable[Event]:
        out: list[Event] = []
        out.extend(self._startup.flush(eof))
        out.extend(self._level.flush(eof))
        out.extend(self._maps.flush(eof))
        return out

    def low_watermark(self) -> int | None:
        marks = [
            m
            for m in (
                self._startup.watermark(),
                self._level.watermark(),
                self._maps.watermark(),
                None if self._pid is None else self._pid.watermark(),
                self._last_id,
            )
            if m is not None
        ]
        return min(marks) if marks else None


# ---------------------------------------------------------------------------------------------
# post-pass: gaps that are possibly a clock jump
# ---------------------------------------------------------------------------------------------
HOUR_MS = 3_600_000


def possible_clock_jumps(
    gaps_by_stream: Mapping[int, list[Event]], *, tolerance_ms: int = 60_000
) -> list[Event]:
    """Find a ~1 h gap that happens at the same time in ALL streams of a collection.

    Contract (the engine calls it in a post-pass, after deriving every stream of the collection):
    ``gaps_by_stream`` maps each stream id of the collection to its ``kind='gap'`` events (streams
    without gaps must be present with an empty list: a stream without a matching gap vetoes the
    result). A gap is a candidate when ``|duration_ms - 60 min| <= tolerance_ms``. Gaps of two
    streams are simultaneous when both ``ts_start`` and ``ts_end`` differ by at most
    ``tolerance_ms``. For every candidate of the lowest stream id that has a simultaneous
    candidate in every other stream, one event per stream is returned: a COPY of that stream's
    gap (the inputs are not modified) whose ``detail`` gains ``possible_clock_jump=True``,
    ``stream_id`` and ``streams`` (number of streams). The engine matches the copy to the stored
    gap by ``(stream_id, start_entry_id)`` and merges the detail. With fewer than two streams
    (or none) nothing is returned. Deterministic: ordered by ``(ts_start, stream_id)``.
    """
    ids = sorted(gaps_by_stream)
    if len(ids) < 2:
        return []

    def candidates(stream: int) -> list[Event]:
        found = [
            g
            for g in gaps_by_stream[stream]
            if g.kind == KIND_GAP
            and g.duration_ms is not None
            and g.ts_end is not None
            and abs(g.duration_ms - HOUR_MS) <= tolerance_ms
        ]
        return sorted(found, key=lambda g: (g.ts_start, g.start_entry_id or 0))

    cands = {s: candidates(s) for s in ids}
    if any(not c for c in cands.values()):
        return []
    result: list[tuple[int, int, Event]] = []
    for anchor in cands[ids[0]]:
        group: list[tuple[int, Event]] = [(ids[0], anchor)]
        for s in ids[1:]:
            near = [
                g
                for g in cands[s]
                if abs(g.ts_start - anchor.ts_start) <= tolerance_ms
                and abs((g.ts_end or 0) - (anchor.ts_end or 0)) <= tolerance_ms
            ]
            if not near:
                group = []
                break
            best = min(near, key=lambda g: (abs(g.ts_start - anchor.ts_start), g.start_entry_id or 0))
            group.append((s, best))
        for s, g in group:
            detail = dict(g.detail)
            detail.update(possible_clock_jump=True, stream_id=s, streams=len(ids))
            result.append((g.ts_start, s, replace(g, detail=detail, entry_ids=list(g.entry_ids))))
    result.sort(key=lambda t: (t[0], t[1]))
    return [ev for _, _, ev in result]
