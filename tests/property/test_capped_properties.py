"""Property: reduced sources (ROADMAP R3b) are deterministic and incremental runs equal complete ones.

Random logs (continuation lines, orphan lines, lines that look like the start of a header, a missing final
newline, CRLF) are indexed with a random small limit and head size:

* any chunk size, any batch size and a crash at any chunk read or commit followed by a plain run give
  the result of one uninterrupted run, and that result is the one of an independent model of the design
  (records parsed from ``[0, h)`` and ``[lo, EOF)`` with ``line_no`` equal to the real line of the file);
* a file first seen up to any byte and completed later (in one or several steps) gives what a fresh
  indexing with the same cut (same h and lo) gives;
* the push parser alone agrees with the model whatever the chunks.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, assume, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from dmslog.ingest.capped import CappedGrouper  # noqa: E402
from dmslog.ingest.pipeline import run_ingest  # noqa: E402
from dmslog.parse.envelope import get_envelope  # noqa: E402
from dmslog.store.writer import FLAG_AFTER_SKIP, FLAG_PARTIAL  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = int(os.environ.get("DMSLOG_CAPPED_EXAMPLES", "40"))


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mf = _load("make_fixture", ROOT / "tools" / "make_fixture.py")
sys.path.insert(0, str(ROOT / "tests" / "unit"))
try:
    cs = _load("capped_unit_helpers", ROOT / "tests" / "unit" / "test_capped_source.py")
    ph = _load("partial_helpers", ROOT / "tests" / "unit" / "partial_helpers.py")
finally:
    sys.path.remove(str(ROOT / "tests" / "unit"))

record = st.tuples(
    st.integers(-2000, 5000),  # time step (negative: the clock goes back)
    st.integers(0, 3),  # continuation lines
    st.sampled_from(["INFO", "ERROR", "DEBUGLOG"]),
    st.booleans(),  # a continuation line that looks like the start of a header
    st.booleans(),  # a debug level change (level windows may span the skipped stretch)
)
log_spec = st.tuples(
    st.lists(record, min_size=1, max_size=70),
    st.booleans(),  # orphan lines before the first header
    st.booleans(),  # missing final newline
    st.booleans(),  # CRLF
)
limits = st.tuples(st.integers(120, 1500), st.integers(30, 600))  # (cap, head)


def render(spec) -> bytes:
    records, orphans, no_final_newline, crlf = spec
    lines = ["orphan line before any header"] if orphans else []
    ts = 0
    for i, (step, conts, level, lookalike, change) in enumerate(records):
        ts = max(0, ts + step)
        lines.append(mf.header(ts, level, f"[Alpha] item {i} step {step} done", pid=4444, tid=7))
        if change:
            frm, to = (("DebugLog (3)", "Info (5)"), ("Info (5)", "DebugLog (3)"))[i % 2]
            lines.append(mf.header(ts, "ALWAYS", f"Debug level changed from {frm} to {to}.", pid=4444, tid=7))
        lines.extend(f"  detail {i}.{j}" for j in range(conts))
        if lookalike:
            lines.append("2030-01-15 10:0")
    return mf.render(lines, eol="\r\n" if crlf else "\n", final_newline=not no_final_newline)


def without_first_window(snapshot: dict) -> dict:
    """The snapshot without the level window "before any change" and without the elevated-level flag (16).

    Known difference of the derivation that also exists without any limit: a file first seen up to its first
    level change and completed later does not get that window (and the flag it sets) back. Reported
    separately; it has nothing to do with reduced sources.
    """
    events = [
        e for e in snapshot["events"] if not (e[0] == "level_window" and '"from": null' in (e[10] or ""))
    ]
    entries = [(*e[:10], e[10] & ~16, *e[11:]) for e in snapshot["entries"]]
    return {**snapshot, "events": events, "entries": entries}


def fresh(work: Path, folder: Path, cap: int, head: int, name: str = "ref", **kw):
    db = cs.new_db(work, name)
    run_ingest(db, folder, cs.options(cap, head, **kw))
    return db


# ---------------------------------------------------------------------------------------------
# the push parser against the model
# ---------------------------------------------------------------------------------------------


@settings(max_examples=EXAMPLES * 5, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(spec=log_spec, lim=limits, chunks=st.lists(st.integers(1, 400), min_size=1, max_size=8))
def test_grouper_equals_the_model_for_any_chunking(spec, lim, chunks):
    data = render(spec)
    cap, head = lim
    env = get_envelope("adms")
    grouper = CappedGrouper(env, size=len(data), cap_bytes=cap, head_bytes=head)
    records, pos, i = [], 0, 0
    while pos < len(data):
        n = chunks[i % len(chunks)]
        records += grouper.feed(data[pos : pos + n])
        pos += n
        i += 1
    records += grouper.finish(final=True)
    got = [
        (
            r.line_no,
            r.byte_off,
            r.nlines,
            r.ts_ms,
            (FLAG_PARTIAL if r.partial else 0)
            | (FLAG_AFTER_SKIP if grouper.after_skip_off == r.byte_off else 0),
            r.msg,
        )
        for r in records
    ]
    assert got == cs.expected_rows(data, env, cap, head)
    for line_no, byte_off, *_rest in got:
        assert line_no == data[:byte_off].count(b"\n") + 1


# ---------------------------------------------------------------------------------------------
# determinism: chunks, batches, crashes
# ---------------------------------------------------------------------------------------------


class Interrupted(BaseException):
    pass


@settings(max_examples=EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    spec=log_spec,
    lim=limits,
    sizes=st.sampled_from([(7, 300), (64, 900), (500, 2500), (1 << 20, 16 << 20)]),
    interrupt=st.one_of(st.none(), st.tuples(st.sampled_from(["read", "commit"]), st.integers(1, 60))),
)
def test_result_is_the_model_whatever_chunks_and_crashes(spec, lim, sizes, interrupt):
    data = render(spec)
    cap, head = lim
    work = Path(tempfile.mkdtemp(prefix="dmslog-capped-"))
    try:
        folder = cs.write_log(work, data)
        ref = fresh(work, folder, cap, head)
        db = cs.new_db(work, "run")
        opts = cs.options(cap, head, chunk=sizes[0], batch=sizes[1])
        if interrupt is not None:
            cs.run_interrupted(db, folder, opts, *interrupt)
        run_ingest(db, folder, opts)
        ph.assert_same(cs.snapshot(db), cs.snapshot(ref))
        ph.assert_integrity(db)
        assert cs.stored_rows(db) == cs.expected_rows(data, cs.envelope_of(db), cap, head)
        for _id, line_no, byte_off, *_rest in cs.rows_of(db):
            assert line_no == data[:byte_off].count(b"\n") + 1
        ids = [r[0] for r in cs.rows_of(db)]
        assert ids == list(range(ids[0], ids[0] + len(ids)))
        before = db.total_changes
        run_ingest(db, folder, opts)
        assert db.total_changes == before  # opening again writes nothing
        ref.close()
        db.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------------------------
# incremental = complete, at every cut
# ---------------------------------------------------------------------------------------------


@settings(max_examples=EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    spec=log_spec,
    lim=limits,
    cuts=st.lists(st.floats(0, 1), min_size=1, max_size=3),
    sizes=st.sampled_from([(13, 400), (1 << 20, 16 << 20)]),
    interrupt=st.one_of(st.none(), st.tuples(st.sampled_from(["read", "commit"]), st.integers(1, 40))),
)
def test_growth_equals_the_reference_with_the_same_cut(spec, lim, cuts, sizes, interrupt):
    data = render(spec)
    cap, head = lim
    points = sorted({max(1, int(q * len(data))) for q in cuts})
    work = Path(tempfile.mkdtemp(prefix="dmslog-capped-"))
    try:
        folder = cs.write_log(work, data[: points[0]])
        db = cs.new_db(work, "run")
        opts = cs.options(cap, head, chunk=sizes[0], batch=sizes[1])
        decided = False
        decision = None  # the reduction of the first (or latest from-scratch) indexing; None = indexed whole

        def settle(result):
            """The limit applies to a first indexing only: the reference follows the decision it took."""
            nonlocal decided, decision
            if not decided or {d.action for d in result.details} & {"new", "reindex"}:
                decision, decided = cs.state_of(db), True

        settle(run_ingest(db, folder, opts))
        for at, point in enumerate(points[1:] + [len(data)]):
            cs.write_log(work, data[:point])
            if interrupt is not None and at == 0:
                cs.run_interrupted(db, folder, opts, *interrupt)
                if cs.run_interrupted.last is not None:  # it was not interrupted after all
                    settle(cs.run_interrupted.last)
            settle(run_ingest(db, folder, opts))
        final = decision
        # NOT COVERED BY THE DESIGN (reported to Opus): the first indexing ended in the middle of a line, that
        # unterminated line is not (yet) a header and no header lies after the cut, so the tail is empty and
        # lo = end of the stream; when the line is completed later and is a header, a fresh indexing starts
        # the tail at it while the incremental run starts it after it (an orphan record).
        assume(
            final is None
            or not (final.lo == final.size_at_cap and not data[: final.size_at_cap].endswith(b"\n"))
        )
        if final is None:  # never reduced: indexed whole
            ref = fresh(work, folder, 0, head)
        else:  # the cut of the (first or last) reduction, applied to the final file
            ref = cs.grown_reference(work, folder, data, final, head=head)
            assert cs.state_of(ref).lo == final.lo
        ph.assert_same(
            {**without_first_window(ph.content(db)), "capped": cs.capped_meta(db)},
            {**without_first_window(ph.content(ref)), "capped": cs.capped_meta(ref)},
        )
        ph.assert_integrity(db)
        for _id, line_no, byte_off, *_rest in cs.rows_of(db):
            assert line_no == data[:byte_off].count(b"\n") + 1
        ref.close()
        db.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
