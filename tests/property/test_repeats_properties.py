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
)

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


def write_state(base: Path, files: dict[str, bytes]) -> None:
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    for name, data in files.items():
        (base / name).write_bytes(data)


def final_files(chain, other) -> dict[str, bytes]:
    old, t = render(chain[0], 0, 4444)
    new, _ = render(chain[1], t, 4444)
    files = {"WfmAdapter_4444.log": new}
    if old:
        files["WfmAdapter_4444.log.0"] = old
    if other:
        files["OntAdapter_5555.log"], _ = render(other, 300, 5555)
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
        " s.rotation, s.file_name, s.collection_id, e.source_id"
        " FROM entry e JOIN source s ON s.id = e.source_id LEFT JOIN entry_body b ON b.entry_id = e.id"
    ).fetchall()

    def key(r):
        if spec.fields:
            data = json.loads(r[6]) if r[6] else {}
            k = tuple(repr(data.get(n)) for n in spec.fields)
            if r[5] == 0:
                k += (r[7], r[8])
        else:
            k = (r[6],) if r[6] is not None else (None, r[7], r[8])
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


# --------------------------------------------------------------------------- the property


@settings(max_examples=EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    chain=chain_spec,
    other=st.lists(record, max_size=15),
    cut=st.floats(0, 1),  # where the first incremental pass cuts the current file
    window=st.one_of(st.none(), st.tuples(st.floats(0, 1), st.floats(0, 1))),
    pick=st.one_of(st.none(), st.sets(st.integers(0, 3), min_size=1, max_size=2)),
)
def test_repeats_and_missing_steps_do_not_depend_on_how_the_case_was_indexed(chain, other, cut, window, pick):
    work = Path(tempfile.mkdtemp(prefix="dmslog-repeats-"))
    plain = IngestOptions(chunk_size=700, batch_bytes=1500)
    try:
        files = final_files(chain, other)
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

            # incremental: the old content as the current file (cut anywhere), then rotated and grown
            old = files.get("WfmAdapter_4444.log.0")
            first = old if old else files["WfmAdapter_4444.log"]
            early = {"WfmAdapter_4444.log": first[: int(len(first) * cut)]}
            if other:
                data = files["OntAdapter_5555.log"]
                early["OntAdapter_5555.log"] = data[: int(len(data) * (1 - cut))]
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
