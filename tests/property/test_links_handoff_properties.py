"""Property tests for the handoff link rules: the engine agrees with a brute-force oracle, and a resumed
run (any cut of any file, any indexing order of the files, a reindexed source) equals a full run.

The links are compared by ``(member, line_no)``, never by entry id: ids depend on the indexing order.
Synthetic data only.
"""

import itertools
import sys
import tempfile
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from handoff_helpers import (  # noqa: E402
    FILES,
    RULE1,
    RULE2,
    HandoffDb,
    append_rest,
    load_world,
    oracle,
)

SETTINGS = settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
NAMES = list(FILES)

JOB = st.integers(min_value=1, max_value=3)
TS = st.integers(min_value=0, max_value=160)
FH_PID = st.sampled_from([31, 32, None])


def records(codes):
    one = st.tuples(TS, st.sampled_from(codes), JOB, FH_PID)
    return st.lists(one, max_size=7).map(lambda rs: sorted(rs, key=lambda r: r[0]))


@st.composite
def worlds(draw):
    world = {}
    for name, (_f, _p, _pid, codes) in FILES.items():
        rs = draw(records(list(codes)))
        # only the Launcher TST-003 carries a fh_pid; the other records have none
        world[name] = [(ts, c, j, fh if c == "TST-003" else None) for ts, c, j, fh in rs]
    return world


def expected(world):
    l1, c1 = oracle(world, "TST-001", "TST-002", False)
    l2, c2 = oracle(world, "TST-003", "TST-101", True)
    return l1, c1, l2, c2


def check(db, world):
    l1, c1, l2, c2 = expected(world)
    if not db.conn.execute("SELECT 1 FROM stream WHERE family = 'DMSJOB'").fetchone():
        assert db.links() == set() and db.stats() == {}  # no DMSJOB stream in the group: no rule runs
        return
    assert db.links() == (l1 | l2)
    stats = db.stats()
    for label, counters in ((RULE1, c1), (RULE2, c2)):
        got = {k: v for k, v in stats.get(label, {}).items() if v}
        assert got == {k: v for k, v in counters.items() if v}, label


def fresh():
    tmp = tempfile.TemporaryDirectory()
    return tmp, HandoffDb(Path(tmp.name) / "case.sqlite")


@SETTINGS
@given(worlds())
def test_the_engine_agrees_with_the_oracle(world):
    tmp, db = fresh()
    try:
        load_world(db, world)
        db.run()
        check(db, world)
    finally:
        db.close()
        tmp.cleanup()


@SETTINGS
@given(worlds(), st.data())
def test_a_resumed_run_equals_the_full_run_at_any_cut_and_order(world, data):
    cuts = {n: data.draw(st.integers(min_value=0, max_value=len(world[n]))) for n in NAMES}
    order1 = data.draw(st.permutations(NAMES))
    order2 = data.draw(st.permutations(NAMES))
    tmp, db = fresh()
    try:
        load_world(db, world, cuts, order1)
        db.run()
        append_rest(db, world, cuts, order2)
        db.run()
        check(db, world)
    finally:
        db.close()
        tmp.cleanup()


@SETTINGS
@given(worlds(), st.data())
def test_any_indexing_order_of_the_files_gives_the_same_links(world, data):
    order = data.draw(st.permutations(NAMES))
    tmp, db = fresh()
    try:
        load_world(db, world, None, order)
        db.run()
        check(db, world)
    finally:
        db.close()
        tmp.cleanup()


@SETTINGS
@given(worlds(), st.data())
def test_reindexing_a_source_returns_to_the_full_result(world, data):
    victim = data.draw(st.sampled_from(NAMES))
    tmp, db = fresh()
    try:
        load_world(db, world)
        db.run()
        db.delete_source(victim)
        db.run()
        check(db, {n: ([] if n == victim else r) for n, r in world.items()})
        for i, rec in enumerate(world[victim]):
            db.add(victim, i + 1, rec)
        db.run()
        check(db, world)
    finally:
        db.close()
        tmp.cleanup()


DENSE = {
    "sched.log": [
        (0, "TST-001", 1, None),
        (5, "TST-001", 1, None),
        (40, "TST-001", 2, None),
        (90, "TST-001", 3, None),
    ],
    "launch.log": [
        (6, "TST-002", 1, None),
        (41, "TST-002", 2, None),
        (50, "TST-003", 1, 31),
        (55, "TST-003", 2, 32),
        (60, "TST-003", 2, None),
        (95, "TST-002", 3, None),
    ],
    "fh1.log": [(52, "TST-101", 1, None), (130, "TST-101", 2, None)],
    "fh2.log": [(57, "TST-101", 2, None), (61, "TST-101", 2, None)],
}


@pytest.mark.parametrize("order", list(itertools.permutations(NAMES)))
def test_every_prefix_of_every_file_and_every_order_on_a_dense_world(order, tmp_path):
    """Exhaustive: a cut after every line of one file (the others whole), the files in every order."""
    n = 0
    for name in NAMES:
        for cut in range(len(DENSE[name]) + 1):
            db = HandoffDb(tmp_path / f"c{n}.sqlite")
            n += 1
            try:
                cuts = {name: cut}
                load_world(db, DENSE, cuts, order)
                db.run()
                append_rest(db, DENSE, cuts, tuple(reversed(order)))
                db.run()
                check(db, DENSE)
            finally:
                db.close()
