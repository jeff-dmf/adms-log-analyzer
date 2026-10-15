"""Property: a partial open, interrupted anywhere and completed later, equals a full indexing (ROADMAP 9.9).

Random folders (rotation chains with time going back now and then, continuation lines, orphan lines, a missing
final newline, normal and sensitive AVL logs that pair, and a random subset of the family fixtures) are opened
with a random first pass (window, sources or both; sometimes twice with different choices), interrupted at a
random chunk read or not, then completed by a plain run. The case must hold exactly what a full indexing of
the folder holds (records with flags, keys, payloads, events, links, statistics, buckets, unknown signatures,
source rows; by locator), ids must follow the file order and the rotation chains, and one more run writes
nothing.
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
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from dmslog.ingest.partial import FirstPass  # noqa: E402
from dmslog.ingest.pipeline import IngestOptions, run_ingest  # noqa: E402
from dmslog.ingest.reader import SourceReader  # noqa: E402
from dmslog.store import create_case_db  # noqa: E402

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
ph = _load("partial_helpers", ROOT / "tests" / "unit" / "partial_helpers.py")

FAMILIES = ROOT / "tests" / "fixtures" / "families" / "dnaApp_zone1" / "SVC"
FAMILY_FILES = sorted(p.name for p in FAMILIES.iterdir() if p.is_file())
LOGDIR = Path("dnaApp_zone1") / "SVC"
NORMAL = "Position is updated for vehicle(s). For more details check sensitive log."
EXAMPLES = int(os.environ.get("DMSLOG_PARTIAL_EXAMPLES", "25"))


class Interrupted(BaseException):
    pass


record = st.tuples(
    st.integers(-3000, 6000),  # time step (negative: the clock goes back)
    st.integers(0, 2),  # continuation lines
    st.sampled_from(["INFO", "ERROR", "DEBUGLOG"]),
)
chain_spec = st.tuples(
    st.lists(record, min_size=0, max_size=30),  # .log.0
    st.lists(record, min_size=0, max_size=30),  # .log
    st.booleans(),  # orphan lines at the start of .log.0
    st.booleans(),  # .log without its final newline
)
avl_spec = st.integers(0, 6)  # normal/sensitive pairs
first_spec = st.tuples(
    st.one_of(st.none(), st.floats(0, 1)),  # window start (quantile of the times)
    st.one_of(st.none(), st.floats(0, 1)),  # window end
    st.one_of(st.none(), st.sets(st.integers(0, 40), min_size=1, max_size=4)),  # sources (indexes)
    st.booleans(),  # defer the rest
)


def render_chain(spec, t0: int) -> tuple[bytes, bytes, int]:
    old, new, orphans, no_final_nl = spec
    ts = t0

    def lines_of(records):
        nonlocal ts
        out = []
        for i, (step, conts, level) in enumerate(records):
            ts = max(0, ts + step)
            out.append(mf.header(ts, level, f"[Alpha] item {i} step {step} done", pid=4444, tid=7))
            out.extend(f"  detail {i}.{j}" for j in range(conts))
        return out

    first = (["orphan line before any header"] if orphans else []) + lines_of(old)
    second = lines_of(new)
    return mf.render(first), mf.render(second, final_newline=not no_final_nl or not second), ts


def write_case(root: Path, chain, pairs: int, families: list[str]) -> Path:
    base = root / LOGDIR
    base.mkdir(parents=True)
    old, new, _ = render_chain(chain, 0)
    if old.strip():
        (base / "WfmAdapter_4444.log.0").write_bytes(old)
    (base / "WfmAdapter_4444.log").write_bytes(new)
    normal = [mf.header(500, "ALWAYS", "Debug level changed from DebugLog (3) to Info (5).", pid=5000)]
    sensitive = []
    for i in range(pairs):
        ts = 1000 + i * 7000
        normal.append(mf.header(ts, "DEBUGLOG", NORMAL, pid=5000, tid=16))
        text = f"Position is updated for the following vehicle(s): ZZZ0A{i:02d}."
        sensitive.append(mf.header(ts, "DEBUGLOG", text, pid=5000, tid=16))
    (base / "AVLAdapter_5000.log").write_bytes(mf.render(normal))
    if sensitive:
        (base / "AVLAdapter_5000.sensitive.log").write_bytes(mf.render(sensitive))
    for name in families:
        shutil.copyfile(FAMILIES / name, base / name)
    return root


def first_pass(spec, times: list[int], members: list[str]) -> FirstPass:
    q_from, q_to, picks, defer = spec

    def at(q):
        return None if q is None or not times else times[min(int(q * len(times)), len(times) - 1)]

    lo, hi = at(q_from), at(q_to)
    if lo is not None and hi is not None and lo >= hi:
        lo, hi = hi, lo + 1 if hi == lo else lo
    chosen = frozenset(members[i % len(members)] for i in picks) if picks and members else None
    return FirstPass(members=chosen, ts_from=lo, ts_to=hi, defer_rest=defer)


@settings(max_examples=EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    chain=chain_spec,
    pairs=avl_spec,
    families=st.lists(st.sampled_from(FAMILY_FILES), max_size=4, unique=True),
    firsts=st.lists(first_spec, min_size=1, max_size=2),
    interrupt=st.one_of(st.none(), st.integers(1, 80)),
    sizes=st.sampled_from([(300, 900), (1000, 2500), (1 << 20, 16 << 20)]),
)
def test_partial_open_completed_equals_full_indexing(chain, pairs, families, firsts, interrupt, sizes):
    work = Path(tempfile.mkdtemp(prefix="dmslog-partial-"))
    chunk_size, batch_bytes = sizes
    try:
        folder = write_case(work / "Log", chain, pairs, families)
        full = create_case_db(work / "full" / "case.sqlite")
        part = create_case_db(work / "part" / "case.sqlite")
        try:
            run_ingest(full, folder, IngestOptions(chunk_size=chunk_size, batch_bytes=batch_bytes))
            reference = ph.content(full)
            times = sorted({r[5] for r in reference["entries"] if r[5] is not None})
            members = sorted(r[0] for r in full.execute("SELECT member FROM source"))
            for i, spec in enumerate(firsts):
                options = IngestOptions(
                    chunk_size=chunk_size, batch_bytes=batch_bytes, first=first_pass(spec, times, members)
                )
                if i == 0 and interrupt is not None:
                    original = SourceReader.iter_chunks
                    count = [0]

                    def flaky(self, info, start=0, size=1 << 20, _orig=original, _count=count):
                        for piece in _orig(self, info, start, size):
                            _count[0] += 1
                            if _count[0] == interrupt:
                                raise Interrupted
                            yield piece

                    SourceReader.iter_chunks = flaky
                    try:
                        run_ingest(part, folder, options)
                    except Interrupted:
                        pass
                    finally:
                        SourceReader.iter_chunks = original
                else:
                    run_ingest(part, folder, options)
                ph.assert_integrity(part)
                ph.assert_ids_follow_chains(part)
            run_ingest(part, folder, IngestOptions(chunk_size=chunk_size, batch_bytes=batch_bytes))
            ph.assert_same(ph.content(part), reference)
            ph.assert_integrity(part)
            ph.assert_ids_follow_chains(part)
            assert part.execute("SELECT count(*) FROM meta WHERE key LIKE 'partial%'").fetchone()[0] == 0
            before = part.total_changes
            run_ingest(part, folder, IngestOptions(chunk_size=chunk_size, batch_bytes=batch_bytes))
            assert part.total_changes == before
        finally:
            full.close()
            part.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
