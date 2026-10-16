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
import re
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from dmslog.ingest.ers import select_sources  # noqa: E402
from dmslog.ingest.partial import FirstPass, load_state, pending_work  # noqa: E402
from dmslog.ingest.pipeline import IngestOptions, build_plan, resume_pending, run_ingest  # noqa: E402
from dmslog.ingest.reader import ReadLimits, SourceReader  # noqa: E402
from dmslog.store import create_case_db  # noqa: E402
from dmslog.store.writer import IndexWriter  # noqa: E402

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


def write_other(root: Path) -> Path:
    """Another collection whose unknown records share signatures with the chain of ``write_case``."""
    base = root / LOGDIR
    base.mkdir(parents=True)
    lines = [mf.header(i * 700, "INFO", f"[Alpha] item {i} step {i} done", pid=6666, tid=7) for i in range(8)]
    (base / "WfmAdapter_6666.log").write_bytes(mf.render(lines))
    return root


def chosen_sources(selected, first: FirstPass) -> set[str]:
    """The members a first pass must index: the requested ones and the rest of their rotation chains."""
    if first.members is None:
        return {s.member for s in selected}

    def chain(info):
        return (info.stream_key, re.sub(r"\.\d+$", "", info.file_name))

    wanted = {chain(s) for s in selected if s.member in first.members}
    return {s.member for s in selected if chain(s) in wanted}


def expected_first(
    reference_rows: list[tuple], picked: set[str], first: FirstPass
) -> dict[str, set[int]] | None:
    """Lines each picked source must show at ``first_ready`` (None for a source whose time goes back: the
    window is only located approximately there)."""
    out: dict[str, set[int] | None] = {}
    by_member: dict[str, list[tuple]] = {}
    for row in reference_rows:
        by_member.setdefault(row[1], []).append(row)
    for member in picked:
        rows = sorted(by_member.get(member, []), key=lambda r: r[2])
        if not first.windowed:
            out[member] = {r[2] for r in rows}
            continue
        times = [r[5] for r in rows if r[5] is not None]
        if times != sorted(times):
            out[member] = None
            continue
        lo = (
            0
            if first.ts_from is None
            else next(
                (k for k, r in enumerate(rows) if r[5] is not None and r[5] >= first.ts_from), len(rows)
            )
        )
        hi = next(
            (k for k in range(lo, len(rows)) if first.ts_to is not None and rows[k][5] is not None
             and rows[k][5] >= first.ts_to),
            len(rows),
        )  # fmt: skip
        out[member] = {r[2] for r in rows[lo:hi]}
    return out


def alter(folder: Path, member: str, kind: str) -> None:
    """Change a source between the passes: same size inside, append, or a new head."""
    path = folder / member
    data = path.read_bytes()
    if kind == "inside":
        at = data.find(b"done", len(data) // 2)
        if at >= 0:
            path.write_bytes(data[:at] + b"DONE" + data[at + 4 :])
    elif kind == "append" and member.endswith(".log"):
        path.write_bytes(
            data + mf.render([mf.header(10**7, "INFO", "[Alpha] item 999 step 1 done", pid=4444)])
        )
    else:
        k = next((n for n, b in enumerate(data) if 0x61 <= b <= 0x7A), None)
        if k is not None:
            path.write_bytes(data[:k] + bytes([data[k] - 32]) + data[k + 1 :])


@settings(max_examples=EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    chain=chain_spec,
    pairs=avl_spec,
    families=st.lists(st.sampled_from(FAMILY_FILES), max_size=4, unique=True),
    firsts=st.lists(first_spec, min_size=1, max_size=2),
    # crash at the n-th chunk read or at the n-th writer commit (any point that writes)
    interrupt=st.one_of(st.none(), st.tuples(st.sampled_from(["read", "commit"]), st.integers(1, 80))),
    sizes=st.sampled_from([(300, 900), (1000, 2500), (1 << 20, 16 << 20)]),
    change=st.one_of(st.none(), st.tuples(st.integers(0, 40), st.sampled_from(["inside", "append", "head"]))),
    other=st.sampled_from(["none", "full", "partial"]),  # another collection added between the passes
    other_first=first_spec,  # its partial open
    resume=st.booleans(),  # complete with resume_pending (else plain runs of the paths)
    a_last=st.booleans(),  # plain runs: complete the other collection first
)
def test_partial_open_completed_equals_full_indexing(
    chain, pairs, families, firsts, interrupt, sizes, change, other, other_first, resume, a_last
):
    work = Path(tempfile.mkdtemp(prefix="dmslog-partial-"))
    chunk_size, batch_bytes = sizes
    plain = IngestOptions(chunk_size=chunk_size, batch_bytes=batch_bytes)
    try:
        folder = write_case(work / "Log", chain, pairs, families)
        second = write_other(work / "Other") if other != "none" else None
        full = create_case_db(work / "full" / "case.sqlite")
        part = create_case_db(work / "part" / "case.sqlite")
        try:
            run_ingest(full, folder, plain)
            first_ref = ph.content(full)
            times = sorted({r[5] for r in first_ref["entries"] if r[5] is not None})
            members = sorted(r[0] for r in full.execute("SELECT member FROM source"))
            for i, spec in enumerate(firsts):
                fp = first_pass(spec, times, members)
                options = IngestOptions(chunk_size=chunk_size, batch_bytes=batch_bytes, first=fp)
                seen: list[bool] = []

                def progress(event, _fp=fp, _check=i == 0, _seen=seen):
                    if event.phase != "first_ready" or not _check:
                        return
                    # what the first pass chose is committed and readable by another connection
                    selected = select_sources(build_plan(folder, ReadLimits()).sources, "default")
                    picked = chosen_sources(selected, _fp)
                    reader = sqlite3.connect(f"file:{work / 'part' / 'case.sqlite'}?mode=ro", uri=True)
                    try:
                        shown: dict[str, set[int]] = {}
                        for member, line in reader.execute(
                            "SELECT s.member, e.line_no FROM entry e JOIN source s ON s.id = e.source_id"
                        ):
                            shown.setdefault(member, set()).add(line)
                    finally:
                        reader.close()
                    assert set(shown) <= picked
                    for member, lines in expected_first(first_ref["entries"], picked, _fp).items():
                        if lines is not None:
                            assert shown.get(member, set()) == lines, member
                    _seen.append(True)

                stopped = False
                if i == 0 and interrupt is not None:
                    kind, at = interrupt
                    count = [0]
                    if kind == "read":
                        original = SourceReader.iter_chunks

                        def flaky(self, info, start=0, size=1 << 20, _orig=original, _count=count, _at=at):
                            for piece in _orig(self, info, start, size):
                                _count[0] += 1
                                if _count[0] == _at:
                                    raise Interrupted
                                yield piece

                        owner, name = SourceReader, "iter_chunks"
                    else:
                        original = IndexWriter.commit

                        def flaky(self, _orig=original, _count=count, _at=at):
                            _count[0] += 1
                            if _count[0] == _at:
                                raise Interrupted  # before the COMMIT: the open transaction is lost
                            return _orig(self)

                        owner, name = IndexWriter, "commit"
                    setattr(owner, name, flaky)
                    try:
                        run_ingest(part, folder, options, progress)
                    except Interrupted:
                        stopped = True
                        if part.in_transaction:  # what a crash does
                            part.rollback()
                    finally:
                        setattr(owner, name, original)
                else:
                    run_ingest(part, folder, options, progress)
                if i == 0 and not stopped:
                    assert seen, "first_ready was not reported"
                ph.assert_integrity(part)
                ph.assert_ids_follow_chains(part)
            if change is not None:  # only a source that is still incomplete (partial or never indexed)
                open_members = sorted(
                    m
                    for sid, m, status in part.execute("SELECT id, member, status FROM source")
                    if status == "pending" or load_state(part, sid) is not None
                )
                if open_members:
                    alter(folder, open_members[change[0] % len(open_members)], change[1])
            # the collections in the order they were added (a crash before A existed adds the other one first)
            a_first = part.execute(
                "SELECT 1 FROM collection WHERE path = ?", (str(folder.resolve()),)
            ).fetchone()
            if other == "full":
                run_ingest(part, second, plain)
            elif other == "partial":
                fp = first_pass(other_first, [0, 2000, 4000], ["dnaApp_zone1/SVC/WfmAdapter_6666.log"])
                run_ingest(part, second, replace(plain, first=replace(fp, defer_rest=True)))
            known = part.execute(
                "SELECT 1 FROM collection WHERE path = ?", (str(folder.resolve()),)
            ).fetchone()
            if resume and known:  # a crash before the collection existed leaves nothing to resume
                resume_pending(part, plain)
            elif resume:
                assert not pending_work(part) or other == "partial"
                run_ingest(part, folder, plain)
                resume_pending(part, plain)
            else:
                paths = [folder] + ([second] if other == "partial" else [])
                for path in reversed(paths) if a_last else paths:
                    run_ingest(part, path, plain)
            reference_db = create_case_db(work / "ref" / "case.sqlite")
            try:
                for path in [folder, second] if a_first else [second, folder]:
                    if path is not None:
                        run_ingest(reference_db, path, plain)
                reference = ph.content(reference_db)
            finally:
                reference_db.close()
            ph.assert_same(ph.content(part), reference)
            ph.assert_integrity(part)
            ph.assert_ids_follow_chains(part)
            assert part.execute("SELECT count(*) FROM meta WHERE key LIKE 'partial%'").fetchone()[0] == 0
            before = part.total_changes
            run_ingest(part, folder, plain)
            assert part.total_changes == before
        finally:
            full.close()
            part.close()
    finally:
        shutil.rmtree(work, ignore_errors=True)
