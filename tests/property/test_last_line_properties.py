"""Property: after any sequence of incremental ingestions (growth, rewrite, rotation, empty files),
``source.last_line`` equals the last line of each source's highest-id record (issue #142)."""

from __future__ import annotations

import sys
from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))

import test_pipeline as tp  # noqa: E402

from dmslog.ingest.pipeline import run_ingest  # noqa: E402

OTHER = f"{tp.LOGDIR}/WfmAdapter_2222.log"

STEP = st.one_of(
    st.tuples(st.just("grow"), st.integers(0, 12), st.integers(1, 5)),
    st.tuples(st.just("rewrite"), st.integers(0, 12), st.integers(1, 5)),
    st.tuples(st.just("rotate"), st.integers(0, 12), st.integers(1, 5)),
    st.tuples(st.just("other"), st.integers(0, 6), st.integers(1, 5)),
)


def apply(root: Path, step: tuple[str, int, int], seq: int) -> None:
    kind, n, cont = step
    current = root / tp.WFM
    data = tp.make_log(n, start=seq * 1000, cont_every=cont)
    if kind == "grow":
        current.write_bytes((current.read_bytes() if current.exists() else b"") + data)
    elif kind == "rewrite":
        current.write_bytes(data)
    elif kind == "rotate":
        rotated = root / tp.WFM_ROTATED
        if current.exists():
            rotated.unlink(missing_ok=True)
            current.rename(rotated)
        current.write_bytes(data)
    else:
        (root / OTHER).write_bytes(tp.make_log(n, pid=2222, start=seq * 1000, cont_every=cont))


@settings(max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(steps=st.lists(STEP, min_size=1, max_size=6))
def test_last_line_matches_the_records_after_every_ingestion(tmp_path_factory, steps):
    base = tmp_path_factory.mktemp("lastline")
    root = base / "Log"
    (root / tp.LOGDIR).mkdir(parents=True)
    (root / tp.WFM).write_bytes(b"")
    conn = tp.fresh_db(base, "case")
    try:
        run_ingest(conn, root)
        tp.assert_last_line_matches_records(conn)
        for seq, step in enumerate(steps, start=1):
            apply(root, step, seq)
            run_ingest(conn, root)
            tp.assert_last_line_matches_records(conn)
        tp.assert_consistent(conn)
    finally:
        conn.close()
