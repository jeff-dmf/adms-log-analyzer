"""Property tests of the panels (ROADMAP 9.11): validation never fails any other way than PanelError and is
idempotent, the file round-trips, and the refresh plan equals a Python reference of "a new record matches"."""

import atexit
import json
import shutil
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from query_helpers import build_case, open_ro, py_match  # noqa: E402
from test_filters_properties import _compile, leaf, trees  # noqa: E402

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


@lru_cache(maxsize=1)
def _case():
    folder = Path(tempfile.mkdtemp(prefix="dmslog-panels-case-"))
    atexit.register(shutil.rmtree, folder, True)
    rows = build_case(folder / "index.sqlite")
    return open_ro(folder / "index.sqlite"), rows


@SETTINGS
@given(trees(leaf), st.integers(0, 260), st.booleans())
def test_the_refresh_plan_equals_the_reference(tree, since, events_changed):
    if _compile(tree) is None:
        return
    conn, rows = _case()
    until = max(r["id"] for r in rows)
    w_rows = {"id": "r", "title": "", "query": {"filter": tree}, "chart": "table", "options": {}}
    w_events = {
        "id": "e",
        "title": "",
        "query": {"rows": "events", "filter": tree},
        "chart": "table",
        "options": {},
    }
    panel = {"id": "a" * 12, "rev": 1, "name": "p", "widgets": [w_rows, w_events]}
    plan = R.refresh_plan(conn, [panel], since_id=since, until_id=until, events_changed=events_changed)
    named = {w for p in plan["panels"] for w in p["widgets"]}
    fresh = any(since < r["id"] <= until and py_match(tree, r) for r in rows)
    if plan["conservative"]:  # only a timeout may widen the plan, never narrow it
        assert ("r" in named) or not fresh
        return
    assert ("r" in named) == (fresh or (events_changed and R.depends_on_derived(w_rows["query"])))
    assert ("e" in named) == (events_changed or fresh)


class _Clock:
    t = 0.0

    def __call__(self):
        return self.t


steps = st.lists(
    st.tuples(st.sampled_from(["cycle", "fail", "unknown"]), st.floats(0, 8), st.integers(0, 40)),
    min_size=1, max_size=25,
)  # fmt: skip


@SETTINGS
@given(steps, st.integers(1, 6))
def test_no_range_is_ever_lost_between_cycles_failures_and_eviction(plan, table):
    """Oracle: ``unreported`` is the lowest id not yet covered by an UNTHROTTLED event, tracked from the steps
    alone. Every unthrottled event must start at or below it (a full refresh covers everything)."""
    from dmslog.store import panelrun

    folder = Path(tempfile.mkdtemp(prefix="dmslog-panels-watch-"))
    try:
        conn, rows = _case()
        P.create(folder, {"name": "p", "widgets": [{"id": "w", "query": {}, "chart": "table"}]})
        clock = _Clock()
        watch = R.PanelWatch(clock, 5.0)
        old = panelrun.MAX_THROTTLE_CASES
        panelrun.MAX_THROTTLE_CASES = table
        try:
            unreported: dict[str, int | None] = {}
            seen: set[str] = set()
            top = 0
            for kind, dt, extra in plan:
                clock.t += dt
                case = f"c{extra % 8}"
                baseline = top
                top += 1 + extra % 5
                if kind == "fail":
                    watch.note_failure(case, baseline)
                    unreported[case] = min(unreported.get(case, baseline), baseline)
                    continue
                known = None if kind == "unknown" else baseline
                low = 0 if known is None else known
                before = unreported.get(case)
                body = watch.process_cycle(conn, folder, case, known, top, events_changed=False)
                want = low if before is None else min(low, before)
                if body["throttled"]:
                    assert body["from_id"] <= want
                    unreported[case] = want
                else:
                    assert body["full"] or (body["from_id"] is not None and body["from_id"] <= want)
                    unreported.pop(case, None)
                seen.add(case)
        finally:
            panelrun.MAX_THROTTLE_CASES = old
    finally:
        shutil.rmtree(folder, ignore_errors=True)
