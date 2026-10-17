"""Property tests of the panels (ROADMAP 9.11): validation never fails any other way than PanelError and is
idempotent, the file round-trips, and the refresh plan equals a Python reference of "a new record matches"."""

import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dmslog.store import panelrun as R  # noqa: E402
from dmslog.store import panels as P  # noqa: E402

SETTINGS = settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])

json_values = st.recursive(
    st.one_of(
        st.none(), st.booleans(), st.integers(-(2**70), 2**70), st.floats(allow_nan=True),
        st.text(max_size=40),
    ),
    lambda c: st.one_of(st.lists(c, max_size=5), st.dictionaries(st.text(max_size=12), c, max_size=5)),
    max_leaves=25,
)  # fmt: skip

ENTRY_DIMS = ["tpl", "sev", "level", "tid", "family", "fields.op", "key.msgid", "bucket:1m", "bucket:1h"]
MEASURES = ["count", "distinct:tid", "min:fields.n", "p90:fields.n", "sum:fields.n"]


@st.composite
def valid_widgets(draw, wid):
    dims = draw(st.lists(st.sampled_from(ENTRY_DIMS), max_size=3, unique=True))
    if sum(d.startswith("bucket") for d in dims) > 1:
        dims = [d for d in dims if not d.startswith("bucket")] + ["bucket:1m"]
    measures = draw(st.lists(st.sampled_from(MEASURES), min_size=1, max_size=3, unique=True))
    chart = draw(st.sampled_from(["table", "stat", "bar", "line"]))
    if chart == "stat":
        dims = []
    if chart == "bar" and not dims:
        dims = ["sev"]
    if chart == "line" and not any(d.startswith("bucket") for d in dims):
        dims = [d for d in dims if not d.startswith("bucket")][:2] + ["bucket:1m"]
    query = {"group_by": dims, "measures": measures, "limit": draw(st.integers(1, 200))}
    if draw(st.booleans()):
        query["filter"] = draw(st.sampled_from([{"sev": {"le": 3}}, {"tpl": [1, 2]}, {"not": {"tid": 16}}]))
    options = {}
    if chart in ("bar", "line") and draw(st.booleans()):
        options["y"] = draw(st.sampled_from(measures))
    if chart == "stat" and draw(st.booleans()):
        options["y"] = draw(st.sampled_from(measures))
    return {
        "id": wid,
        "title": draw(st.text(max_size=20)),
        "query": query,
        "chart": chart,
        "options": options,
    }


@st.composite
def valid_panels(draw):
    n = draw(st.integers(0, 4))
    ws = [draw(valid_widgets(f"w{k}")) for k in range(n)]
    return {"name": draw(st.text(min_size=1, max_size=30).filter(lambda t: t.strip() != "")), "widgets": ws}


def _try(raw):
    try:
        return P.validate_panel(raw)
    except P.PanelError as exc:
        assert exc.code in P.messages.PANEL_ERRORS
        return None


@SETTINGS
@given(json_values)
def test_arbitrary_json_is_accepted_or_refused_with_a_panel_error(raw):
    clean = _try(raw)
    if clean is not None:
        assert _try(clean) == clean
    wrapped = _try({"name": "n", "widgets": [{"id": "a", "chart": "table", "query": raw}]})
    if wrapped is not None:
        assert _try(wrapped) == wrapped


@SETTINGS
@given(valid_panels())
def test_valid_panels_are_idempotent_and_roundtrip_through_the_file(raw):
    clean = _try(raw)
    if clean is None:  # titles with only control characters, for instance
        return
    assert P.validate_panel(clean) == clean
    folder = Path(tempfile.mkdtemp(prefix="dmslog-panels-"))
    try:
        made = P.create(folder, raw)
        loaded = P.load(folder)
        assert loaded.panels == [made] and not loaded.problems
        assert made == {"id": made["id"], "rev": 1, **clean}
        P.panels_path(folder).read_bytes().decode("ascii")
        assert json.loads(P.panels_path(folder).read_text())["panels"] == [made]
    finally:
        shutil.rmtree(folder, ignore_errors=True)


class _Clock:
    t = 0.0

    def __call__(self):
        return self.t


steps = st.lists(
    st.tuples(st.sampled_from(["notice", "forget", "nopanels"]), st.floats(0, 8), st.integers(0, 12)),
    min_size=1, max_size=40,
)  # fmt: skip


@SETTINGS
@given(steps, st.integers(1, 6), st.booleans())
def test_seq_is_strictly_increasing_per_case_and_the_notice_is_idempotent(plan, table, same_to_id):
    """Oracle: a dict of the last seq seen per case. Whatever happens (throttling, forgetting, eviction of the
    bounded table), a published notice has a larger seq than any earlier one of its case, and apart from
    seq/throttle fields it depends only on the arguments, so repeating a notice changes nothing else."""
    folder = Path(tempfile.mkdtemp(prefix="dmslog-panels-watch-"))
    empty = Path(tempfile.mkdtemp(prefix="dmslog-panels-none-"))
    try:
        P.create(folder, {"name": "p", "widgets": [{"id": "w", "query": {}, "chart": "table"}]})
        clock = _Clock()
        old = R.MAX_THROTTLE_CASES
        R.MAX_THROTTLE_CASES = table
        try:
            watch = R.PanelWatch(clock, 5.0)
            last: dict[str, int] = {}
            core: dict[tuple[str, int], dict] = {}
            for kind, dt, extra in plan:
                clock.t += dt
                case = f"c{extra % 8}"
                if kind == "forget":
                    watch.forget_case(case)
                    continue
                to_id = 7 if same_to_id else extra
                if kind == "nopanels":
                    assert watch.notice(empty, case, to_id) is None
                    continue
                body = watch.notice(folder, case, to_id)
                assert body["seq"] > last.get(case, 0)
                last[case] = body["seq"]
                assert len(watch._last) <= table
                assert body["throttled"] == (body["retry_after_ms"] > 0)
                stable = {k: v for k, v in body.items() if k not in ("seq", "throttled", "retry_after_ms")}
                assert core.setdefault((case, to_id), stable) == stable
        finally:
            R.MAX_THROTTLE_CASES = old
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        shutil.rmtree(empty, ignore_errors=True)
