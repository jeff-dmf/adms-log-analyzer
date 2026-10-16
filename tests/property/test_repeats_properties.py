"""Property: repeats and missing steps (ROADMAP 9.12) are the same after a full, an incremental and a partial
indexing, and the repeats are those of a brute-force model.

Random folders: a rotation chain of one process (``.log.0`` + ``.log``) and sometimes a second process, whose
records come from a small alphabet (unknown lines with or without continuation lines, map markers with
fields, two threads) with small time steps that sometimes go back, so repeats, ties and the rotation boundary
all happen. The same final folder is indexed (1) at once, (2) incrementally: first a cut of the current file
(at a random byte, so the last record may be open), then rotated and grown into the final state, and (3) by
a partial open (random window and/or sources, the rest deferred) completed by a plain run. The repeats of
several specs, the marks of every record (by locator, never by id) and the missing steps must be equal in the
three, and equal to the model.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from dmslog.ingest.partial import FirstPass  # noqa: E402
from dmslog.ingest.pipeline import IngestOptions, run_ingest  # noqa: E402
from dmslog.store import create_case_db  # noqa: E402
from dmslog.store.events import Cursor, EventFilter  # noqa: E402
from dmslog.store.filters import compile_filter  # noqa: E402
from dmslog.store.repeats import RepeatSpec, parse_spec, repeat_marks  # noqa: E402
from dmslog.store.sequences import list_missing, missing_summary  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mf = _load("make_fixture", ROOT / "tools" / "make_fixture.py")

LOGDIR = Path("dnaApp_zone1") / "SVC"
EXAMPLES = int(os.environ.get("DMSLOG_REPEATS_EXAMPLES", "60"))
TEXTS = (
    "[Alpha] item 1 done",
    "[Alpha] item 2 done",
    '"OMS_CREW" map closed.',
    '"OMS_CREW" map is now open.',
    '"OMS_TRUCK" map closed.',
)
SPECS = (
    {},
    {"window_ms": 0},
    {"window_ms": 2500, "tid": True},
    {"fields": ["state"]},
    {"fields": ["map", "nope"], "window_ms": 400},
    {"fields": ["kv"]},
    {"fields": ["request_id", "kv"], "tid": True, "window_ms": 1500},
)
# WFM-S010 (sensitive): the continuation parser keeps the order of the lines, so the same values can be
# stored as JSON with the keys in another order (review of PR #200)
S010 = 'Changed crews request received. Request with ID "00000000-0000-4000-8000-00000000000{}" content:'
KV = (("Identifier: CREW0001", "Availability: Available"), ("Identifier: CREW0002", "Availability: Busy"))
s010 = st.tuples(
    st.one_of(st.just(0), st.integers(-300, 1200)),  # time step
    st.integers(1, 2),  # request id
    st.integers(0, 1),  # values
    st.booleans(),  # reversed continuation lines
    st.sampled_from([7, 9]),
)
# Expected sequences, scripted so that the outcome is known without the derivers (an independent oracle):
# unit -> the missing step it must produce (kind, missing, cause, proven), or None
UNITS = {
    "op_ok": None,
    "op_superseded": ("operation", "end", "superseded", True),
    "op_eof": ("operation", "end", "end_of_log", False),
    "startup_hot": None,
    "startup_timeout": ("startup", "hot", "timeout", True),
    "map_ok": None,
    "map_superseded": ("map_reload", "open", "superseded", True),
    "map_eof": ("map_reload", "open", "end_of_log", False),
    "retry_sent": None,
    "retry_failed": None,
    "retry_open": ("retry_seq", "outcome", "end_of_log", False),
}
STARTUP_EOF = ("startup", "hot", "end_of_log", False)
# The expected steps of each life cycle (ARQUITETURA 10.9), written here independently of the code.
STEPS = {
    "operation": ["start", "end"],
    "startup": ["start", "hot"],
    "map_reload": ["closed", "open"],
    "retry_seq": ["attempt", "outcome"],
}
MISSING = {"operation": "end", "startup": "hot", "map_reload": "open", "retry_seq": "outcome"}

record = st.tuples(
    st.one_of(st.just(0), st.integers(-800, 1500)),  # time step in ms (0: a tie; negative: back in time)
    st.integers(0, len(TEXTS) - 1),
    st.integers(0, 2),  # continuation lines (only on the unknown lines)
    st.sampled_from([7, 9]),  # thread
)
chain_spec = st.tuples(
    st.lists(record, max_size=25),  # .log.0
    st.lists(record, min_size=1, max_size=25),  # .log
)


def render(records, t0: int, pid: int) -> tuple[bytes, int]:
    ts = t0
    lines: list[str] = []
    for step, text, conts, tid in records:
        ts = max(0, ts + step)
        msg = TEXTS[text]
        lines.append(mf.header(ts, "INFO", msg, pid=pid, tid=tid))
        if msg.startswith("[Alpha]"):
            lines.extend(f"  detail {j % 2}" for j in range(conts))
    return mf.render(lines), ts


def render_s010(records, pid: int) -> bytes:
    ts = 500
    lines: list[str] = []
    for step, rid, values, reverse, tid in records:
        ts = max(0, ts + step)
        lines.append(mf.header(ts, "DEBUGLOG", S010.format(rid), pid=pid, tid=tid))
        kv = KV[values][::-1] if reverse else KV[values]
        lines.extend(kv)
    return mf.render(lines)


def render_units(units: list[str], startup_eof: bool, pid: int) -> bytes:
    """One Smn log: every unit on its own thread, 130 s apart (a startup block closes in 120 s)."""
    lines: list[str] = []
    t = 0

    def add(text: str, tid: int) -> None:
        nonlocal t
        lines.append(mf.header(t, "INFO", text, pid=pid, tid=tid))
        t += 10

    for i, unit in enumerate(units):
        tid, guid = 100 + i, f"00000000-0000-4000-8000-{i:012d}"
        start = f"[SendWorks] Sending following Work Request: WR {1000 + i}."
        end = "[SendWorks] Sending Work Request finished successfully."
        attempt = f'Sending "WorksEventMessageType" message: {guid} with verb: Changed...'
        if unit.startswith("op_"):
            add(start, tid)
            if unit == "op_superseded":
                add(f"[SendWorks] Sending following Work Request: WR {5000 + i}.", tid)
            if unit != "op_eof":
                add(end, tid)
        elif unit.startswith("startup_"):
            add("[Configuration] [LogLevel]: [Info]. Type: [ConfigurationRegistry]", tid)
            if unit == "startup_hot":
                add("[SystemStatusPublisher] - Server state changed. New state: HOT.", tid)
        elif unit.startswith("map_"):
            add(f'"M{i}" map closed.', tid)
            if unit == "map_superseded":
                t += 1500  # a second "closed" more than 1 s later is a new reload
                add(f'"M{i}" map closed.', tid)
            if unit != "map_eof":
                add(f'"M{i}" map is now open.', tid)
        else:
            add(attempt, tid)
            add(attempt, tid)
            if unit == "retry_sent":
                add(attempt.replace("Changed...", "Changed finished successfully."), tid)
            elif unit == "retry_failed":
                add(f"Request message with MessageId '{guid}' unsuccessfully sent. Reason: 'x'.", tid)
        t += 130_000
    if units:
        add("[Alpha] the last unit is over", 98)  # a later record: a startup without HOT times out
    if startup_eof:
        add("[Configuration] [LogLevel]: [Info]. Type: [ConfigurationRegistry]", 99)
    return mf.render(lines)


def write_state(base: Path, files: dict[str, bytes]) -> None:
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    for name, data in files.items():
        (base / name).write_bytes(data)


def final_files(chain, other, s010_records=(), units=(), startup_eof=False) -> dict[str, bytes]:
    old, t = render(chain[0], 0, 4444)
    new, _ = render(chain[1], t, 4444)
    files = {"WfmAdapter_4444.log": new}
    if old:
        files["WfmAdapter_4444.log.0"] = old
    if other:
        files["OntAdapter_5555.log"], _ = render(other, 300, 5555)
    if s010_records:
        files["WfmAdapter_4444.sensitive.log"] = render_s010(s010_records, 4444)
    if units or startup_eof:
        files["SmnAdapter_6000.log"] = render_units(list(units), startup_eof, 6000)
    return files


# --------------------------------------------------------------------------- what is compared


def _locators(conn) -> dict[int, tuple]:
    return {
        eid: (member, line)
        for eid, member, line in conn.execute(
            "SELECT e.id, s.member, e.line_no FROM entry e JOIN source s ON s.id = e.source_id"
        )
    }


def observed(conn) -> dict:
    loc = _locators(conn)
    out: dict = {}
    for k, spec in enumerate(SPECS):
        cf = compile_filter({"repeat": spec})
        hits = {loc[r[0]] for r in conn.execute(f"SELECT e.id FROM entry e WHERE {cf.sql}", cf.params)}
        neg = compile_filter({"not": {"repeat": spec}})
        rest = {loc[r[0]] for r in conn.execute(f"SELECT e.id FROM entry e WHERE {neg.sql}", neg.params)}
        assert hits.isdisjoint(rest) and len(hits) + len(rest) == len(loc)  # two-valued
        ids = sorted(loc)
        marks: dict = {}
        parsed = parse_spec(spec)
        for i in range(0, len(ids), 1000):
            for eid, mark in repeat_marks(conn, ids[i : i + 1000], parsed).items():
                marks[loc[eid]] = (loc[mark["prev"]], mark["dt_ms"], mark["earlier"])
        assert set(marks) == hits
        out[f"repeat{k}"] = hits
        out[f"marks{k}"] = marks
    items: list = []
    after = None
    while True:  # every page, by keyset
        page, nxt = list_missing(conn, EventFilter(), limit=3, after=after)
        items.extend(
            (
                loc.get(i["event"]["start_entry_id"]),
                i["event"]["kind"],
                i["event"]["name"],
                tuple(i["missing"]),
                i["cause"],
                i["proven"],
            )
            for i in page
        )
        if nxt is None:
            break
        after = Cursor.parse(nxt)
    out["missing"] = sorted(items, key=repr)
    out["summary"] = missing_summary(conn, EventFilter())
    assert out["summary"]["total"] == len(items)
    return out


def model(conn, spec: RepeatSpec) -> dict:
    """Brute force over every pair of records (the definition of ``store/repeats.py``)."""
    rows = conn.execute(
        "SELECT e.id, s.member, e.line_no, s.stream_id, e.ts, e.tpl_id, e.fields, e.msg, b.cont, e.tid,"
        " s.rotation, s.file_name, coalesce(c.ers_variant, '') || char(0) || c.path, e.source_id"
        " FROM entry e JOIN source s ON s.id = e.source_id JOIN collection c ON c.id = s.collection_id"
        " LEFT JOIN entry_body b ON b.entry_id = e.id"
    ).fetchall()

    def canon(value):  # by value: JSON keys sorted, arrays in order, types kept (1, 1.0 and true differ)
        return None if value is None else json.dumps(value, sort_keys=True, separators=(",", ":"))

    def key(r):
        data = json.loads(r[6]) if r[6] else None
        if spec.fields:
            k = tuple(canon((data or {}).get(n)) for n in spec.fields)
            if r[5] == 0:
                k += (r[7], r[8])
        else:
            k = (canon(data),) if data is not None else (None, r[7], r[8])
        return (r[3], r[5], k, r[9] if spec.tid else None)

    def rot(r):
        return (r[10] is None, -(r[10] or 0))

    def before(p, e):
        if p[4] != e[4]:
            return p[4] < e[4]
        if p[13] == e[13]:
            return p[2] < e[2]
        return (rot(p), p[11], p[1], p[12]) < (rot(e), e[11], e[1], e[12])

    out = {}
    for e in rows:
        if e[4] is None:
            continue
        ke = key(e)
        earlier = [
            p
            for p in rows
            if p is not e and p[4] is not None and key(p) == ke and e[4] - spec.window_ms <= p[4] <= e[4]
            and before(p, e)
        ]  # fmt: skip
        if earlier:
            closest = earlier[0]
            for p in earlier[1:]:
                if before(closest, p):
                    closest = p
            out[(e[1], e[2])] = ((closest[1], closest[2]), e[4] - closest[4], len(earlier))
    return out


def check_oracle(conn, units, startup_eof) -> None:
    expected = sorted([UNITS[u] for u in units if UNITS[u]] + ([STARTUP_EOF] if startup_eof else []))
    row = conn.execute("SELECT stream_id FROM source WHERE file_name = 'SmnAdapter_6000.log'").fetchone()
    if row is None:
        assert not expected
        return
    flt = EventFilter(stream_id=row[0])
    items = list_missing(conn, flt, limit=1000)[0]
    got = sorted((i["event"]["kind"], i["missing"][0], i["cause"], i["proven"]) for i in items)
    assert got == expected
    for i in items:  # the whole expected sequence, literally, and the step that is missing
        assert i["steps"] == STEPS[i["event"]["kind"]]
        assert i["missing"] == [MISSING[i["event"]["kind"]]]
    counts: dict = {}
    for kind, step, cause, _ in expected:
        group = counts.setdefault((kind, step), {})
        group[cause] = group.get(cause, 0) + 1
    summary = missing_summary(conn, flt)
    merged: dict = {}
    for g in summary["groups"]:
        group = merged.setdefault((g["kind"], g["missing"]), {})
        for cause, n in g["causes"].items():
            group[cause] = group.get(cause, 0) + n
    assert merged == counts and summary["total"] == len(expected)


# --------------------------------------------------------------------------- the property


@settings(max_examples=EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    chain=chain_spec,
    other=st.lists(record, max_size=15),
    s010_records=st.lists(s010, max_size=10),
    units=st.lists(st.sampled_from(sorted(UNITS)), max_size=8),
    startup_eof=st.booleans(),
    cut=st.floats(0, 1),  # where the first incremental pass cuts the current file
    window=st.one_of(st.none(), st.tuples(st.floats(0, 1), st.floats(0, 1))),
    pick=st.one_of(st.none(), st.sets(st.integers(0, 3), min_size=1, max_size=2)),
)
def test_repeats_and_missing_steps_do_not_depend_on_how_the_case_was_indexed(
    chain, other, s010_records, units, startup_eof, cut, window, pick
):
    work = Path(tempfile.mkdtemp(prefix="dmslog-repeats-"))
    plain = IngestOptions(chunk_size=700, batch_bytes=1500)
    try:
        files = final_files(chain, other, s010_records, units, startup_eof)
        folder = work / "Log"
        write_state(folder / LOGDIR, files)
        full = create_case_db(work / "full" / "case.sqlite")
        incr = create_case_db(work / "incr" / "case.sqlite")
        part = create_case_db(work / "part" / "case.sqlite")
        try:
            run_ingest(full, folder, plain)
            reference = observed(full)
            for k, spec in enumerate(SPECS):
                assert reference[f"marks{k}"] == model(full, parse_spec(spec)), spec
            # the missing steps of the scripted Smn log are the expected ones (kind, step, cause, proven)
            check_oracle(full, units, startup_eof)

            # incremental: the old content as the current file (cut anywhere), then rotated and grown
            old = files.get("WfmAdapter_4444.log.0")
            first = old if old else files["WfmAdapter_4444.log"]
            early = {"WfmAdapter_4444.log": first[: int(len(first) * cut)]}
            for name, data in files.items():  # every other file cut too
                if not name.startswith("WfmAdapter_4444.log"):
                    early[name] = data[: int(len(data) * (1 - cut))]
            write_state(folder / LOGDIR, early)
            run_ingest(incr, folder, plain)
            if old:  # grow the current file to the whole old content, then rotate
                write_state(folder / LOGDIR, {**early, "WfmAdapter_4444.log": old})
                run_ingest(incr, folder, plain)
            write_state(folder / LOGDIR, files)
            run_ingest(incr, folder, plain)
            assert observed(incr) == reference

            # partial open of the final folder, then completed
            times = sorted({r[0] for r in full.execute("SELECT ts FROM entry WHERE ts IS NOT NULL")})
            members = sorted(r[0] for r in full.execute("SELECT member FROM source"))
            lo = hi = None
            if window is not None and times:
                lo = times[min(int(window[0] * len(times)), len(times) - 1)]
                hi = times[min(int(window[1] * len(times)), len(times) - 1)]
                if lo >= hi:
                    lo, hi = hi, lo + 1
            chosen = frozenset(members[i % len(members)] for i in pick) if pick else None
            fp = FirstPass(members=chosen, ts_from=lo, ts_to=hi, defer_rest=True)
            run_ingest(part, folder, IngestOptions(chunk_size=700, batch_bytes=1500, first=fp))
            run_ingest(part, folder, plain)
            assert observed(part) == reference
        finally:
            full.close()
            incr.close()
            part.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
