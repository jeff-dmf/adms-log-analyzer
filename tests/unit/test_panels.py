"""User-built panels: validation, versioned JSON file and CRUD (store/panels.py, ROADMAP 9.11)."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from dmslog.store import panels as P

E_ACUTE = chr(0xE9)  # kept as an escape: the source must stay free of accented letters
NAO = "N" + chr(0xE3) + "o " + chr(0x1F600)
QUERY = {"group_by": ["sev"], "measures": ["count"]}
SERIES = {"group_by": ["bucket:1h", "sev"], "measures": ["count", "distinct:tid"]}
WIDGET = {"id": "w1", "title": "By severity", "query": QUERY, "chart": "bar"}
PANEL = {"name": "Overview", "widgets": [WIDGET]}


def code(call, *args, **kwargs):
    with pytest.raises(P.PanelError) as info:
        call(*args, **kwargs)
    return info.value.code


def widget(**changes):
    return {**copy.deepcopy(WIDGET), **changes}


def panel(*widgets, name="Overview"):
    return {"name": name, "widgets": list(widgets)}


# --- validation ----------------------------------------------------------------------------------


def test_a_valid_panel_is_normalized():
    clean = P.validate_panel(
        {"name": "  Over view\n", "widgets": [widget(title="  T  ", options={"y": "count", "x": "sev"})]}
    )
    assert clean["name"] == "Over view"
    (w,) = clean["widgets"]
    assert w["title"] == "T" and w["options"] == {"x": "sev", "y": "count"}
    assert P.validate_panel(clean | {"id": "0123456789ab", "rev": 3}, stored=True)["rev"] == 3


def test_an_empty_filter_means_no_filter():
    (w,) = P.validate_panel(panel(widget(query={**QUERY, "filter": {}})))["widgets"]
    assert "filter" not in w["query"]


@pytest.mark.parametrize(
    ("chart", "query", "options", "ok"),
    [
        ("table", QUERY, {}, True),
        ("table", QUERY, {"y": "count"}, False),
        ("stat", {"measures": ["count"]}, {"y": "count"}, True),
        ("stat", QUERY, {}, False),  # grouped: not a single number
        ("bar", QUERY, {"x": "sev", "y": "count", "top": 10, "stacked": True}, True),
        ("bar", {"measures": ["count"]}, {}, False),  # nothing to put on the axis
        ("line", SERIES, {"x": "bucket:1h", "series": "sev", "y": "distinct:tid"}, True),
        ("line", SERIES, {"x": "sev"}, False),  # the x axis of a line is the time bucket
        ("line", QUERY, {}, False),
        ("line", SERIES, {"series": "bucket:1h"}, False),  # series cannot be the x axis
        ("bar", QUERY, {"series": "sev"}, False),  # nor the default x of a bar
        ("bar", QUERY, {"y": "sev"}, False),  # y is a measure
        ("bar", QUERY, {"top": 0}, False),
        ("bar", QUERY, {"top": True}, False),  # bool is not an int
        ("bar", QUERY, {"top": 51}, False),
        ("bar", QUERY, {"top": 1.0}, False),
        ("bar", QUERY, {"stacked": 1}, False),
        ("bar", QUERY, {"colour": "red"}, False),
        ("pie", QUERY, {}, False),
        (None, QUERY, {}, False),
    ],
)
def test_chart_and_options_must_fit_the_query(chart, query, options, ok):
    candidate = panel(widget(chart=chart, query=query, options=options))
    if ok:
        P.validate_panel(candidate)
    else:
        assert code(P.validate_panel, candidate) in ("bad_options", "bad_option", "chart_needs", "bad_chart")


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ({"group_by": ["<script>"]}, "bad_dimension"),
        ({"measures": ["p90:fields.n;DROP"]}, "bad_value"),
        ({"rows": "tables"}, "bad_rows"),
        ({"limit": True}, "bad_limit"),
        ({"limit": 1001}, "bad_limit"),
        ({"limit": 2**70}, None),
        ({"exact": 1}, "bad_exact"),
        ({"group_by": ["sev", "sev"]}, "duplicate"),
        ({"order": {"by": "nope"}}, "bad_order"),
        ({"filter": {"sev": {"le": "x"}}}, None),
        ({"filter": {"nope": 1}}, None),
        ({"filter": []}, "bad_type"),
    ],
)
def test_a_query_is_checked_by_the_aggregation_validator(query, expected):
    c = code(P.validate_panel, panel(widget(query=query)))
    assert c in ("bad_query", "query_too_large", "bad_data")
    with pytest.raises(P.PanelError) as info:
        P.validate_panel(panel(widget(query=query)))
    if expected:
        assert info.value.params["reason"] == expected


def test_the_reason_never_echoes_input():
    hostile = "<img src=x onerror=alert(1)>"
    with pytest.raises(P.PanelError) as info:
        P.validate_panel(panel(widget(query={"group_by": [hostile]})))
    assert hostile not in str(info.value) and "<" not in str(info.value)
    with pytest.raises(P.PanelError) as info:
        P.validate_panel(panel(widget(query={"filter": {hostile: 1}})))
    assert hostile not in str(info.value)


@pytest.mark.parametrize("key", ["case", "format", "bom", "x"])
def test_the_query_takes_only_the_aggregation_members(key):
    assert code(P.validate_panel, panel(widget(query={**QUERY, key: 1}))) == "unknown_key"


@pytest.mark.parametrize(
    "tree",
    [{"source": [1]}, {"ts": {"ge": 1}}, {"and": [{"sev": 1}, {"not": {"stream": [2]}}]}, {"event": [1]},
     {"collection": [1]}],
)  # fmt: skip
def test_filters_that_point_at_one_case_are_refused(tree):
    assert code(P.validate_panel, panel(widget(query={**QUERY, "filter": tree}))) == "case_specific"


def test_text_conditions_need_a_scope_a_panel_cannot_hold():
    assert code(P.validate_panel, panel(widget(query={**QUERY, "filter": {"text": {"substr": "x"}}}))) in (
        "scan_needs_scope",
        "bad_query",
    )
    P.validate_panel(panel(widget(query={**QUERY, "filter": {"sev": {"le": 2}}})))


@pytest.mark.parametrize(
    "bad",
    [
        None, [], "x", 1,
        {"name": "n", "widgets": "w"},
        {"name": "n", "widgets": [1]},
        {"name": "n", "widgets": [{}]},
        {"name": "", "widgets": []},
        {"name": "n" * 81},
        {"name": "n", "extra": 1},
        {"name": "n", "id": "0123456789ab"},  # the server assigns ids
        {"name": "n", "widgets": [widget(id="Bad Id")]},
        {"name": "n", "widgets": [widget(id="")]},
        {"name": "n", "widgets": [widget(id="a" * 33)]},
        {"name": "n", "widgets": [widget(title="t" * 81)]},
        {"name": "n", "widgets": [widget(title=5)]},
        {"name": "n", "widgets": [widget(extra=1)]},
        {"name": "n", "widgets": [widget(), widget()]},
    ],
)  # fmt: skip
def test_malformed_panels_are_refused(bad):
    with pytest.raises(P.PanelError):
        P.validate_panel(bad)


def test_size_limits():
    many = [widget(id=f"w{k}") for k in range(P.MAX_WIDGETS + 1)]
    assert code(P.validate_panel, panel(*many)) == "bad_widgets"
    P.validate_panel(panel(*many[: P.MAX_WIDGETS]))
    big = [widget(id=f"w{k}", query={**QUERY, "limit": 1000}) for k in range(5)]
    assert code(P.validate_panel, panel(*big)) == "too_many_rows"
    P.validate_panel(panel(*big[:4]))


def test_hostile_input_is_cut_before_validation():
    deep = cur = {}
    for _ in range(100_000):  # would overflow a recursive validator
        cur["and"] = [{}]
        cur = cur["and"][0]
    assert code(P.validate_panel, panel(widget(query={**QUERY, "filter": deep}))) in (
        "query_too_large",
        "bad_data",
    )
    wide = {"or": [{"tid": k} for k in range(100_000)]}
    assert code(P.validate_panel, panel(widget(query={**QUERY, "filter": wide}))) in (
        "query_too_large",
        "bad_data",
    )
    for value in (2**63, -(2**63) - 1, float("nan"), float("inf")):
        assert not P.bounded({"a": [value]})
    assert P.bounded({"a": [2**63 - 1, -(2**63), 1.5, "x", None, True]})
    assert not P.bounded({"a": "x" * 2000})
    assert not P.bounded({1: 2})
    assert not P.bounded({"a": object()})
    assert not P.bounded([[] for _ in range(100_001)])


def test_names_are_compared_by_their_canonical_form(tmp_path):
    P.create(tmp_path, panel(name=f"Caf{E_ACUTE}"))
    assert code(P.create, tmp_path, panel(name="CAFÉ")) == "duplicate_name"
    assert code(P.create, tmp_path, panel(name=f"  caf{E_ACUTE.upper()} ")) == "duplicate_name"


# --- the file ------------------------------------------------------------------------------------


def test_crud_roundtrip(tmp_path):
    assert P.list_panels(tmp_path)["panels"] == []
    made = P.create(tmp_path, PANEL)
    assert made["rev"] == 1 and len(made["id"]) == 12
    listing = P.list_panels(tmp_path)
    assert listing["panels"] == [made] and listing["invalid"] is False and listing["limits"]["widgets"] == 16
    again = P.update(tmp_path, made["id"], panel(widget(chart="table", options={}), name="Renamed"), 1)
    assert again["rev"] == 2 and again["id"] == made["id"] and again["name"] == "Renamed"
    assert P.get_panel(tmp_path, made["id"]) == again
    P.delete(tmp_path, made["id"])
    assert P.list_panels(tmp_path)["panels"] == []
    assert code(P.delete, tmp_path, made["id"]) == "not_found"
    assert code(P.get_panel, tmp_path, made["id"]) == "not_found"


def test_update_guards(tmp_path):
    a, b = P.create(tmp_path, panel(name="A")), P.create(tmp_path, panel(name="B"))
    assert code(P.update, tmp_path, a["id"], panel(name="B")) == "duplicate_name"
    assert P.update(tmp_path, a["id"], panel(name="A"))["name"] == "A"  # own name again is fine
    assert code(P.update, tmp_path, a["id"], panel(name="A2"), 1) == "rev_conflict"  # rev is 2 now
    assert code(P.update, tmp_path, a["id"], panel(name="A2"), True) == "rev_conflict"
    assert code(P.update, tmp_path, "0" * 12, panel(name="X")) == "not_found"
    for bad in ("", "xyz", None, 5, "0123456789AB", "0123456789abc"):
        assert code(P.update, tmp_path, bad, panel(name="X")) == "bad_id"
        assert code(P.delete, tmp_path, bad) == "bad_id"
    assert [p["name"] for p in P.list_panels(tmp_path)["panels"]] == ["A", "B"] and b["rev"] == 1


def test_panel_count_limit(tmp_path):
    for k in range(P.MAX_PANELS):
        P.create(tmp_path, panel(name=f"P{k}"))
    assert code(P.create, tmp_path, panel(name="one more")) == "too_many_panels"


def test_a_failed_write_leaves_the_file_as_it_was(tmp_path, monkeypatch):
    made = P.create(tmp_path, PANEL)
    before = P.panels_path(tmp_path).read_bytes()

    def boom(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(P, "atomic_write_bytes", boom)
    assert code(P.update, tmp_path, made["id"], panel(name="Other")) == "save_failed"
    assert code(P.create, tmp_path, panel(name="New")) == "save_failed"
    assert code(P.delete, tmp_path, made["id"]) == "save_failed"
    assert P.panels_path(tmp_path).read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))  # no stray temporary file


def test_the_file_is_ascii_json_with_the_schema(tmp_path):
    P.create(tmp_path, panel(name=NAO))
    raw = P.panels_path(tmp_path).read_bytes()
    raw.decode("ascii")
    data = json.loads(raw)
    assert data["schema"] == 1 and data["panels"][0]["name"] == NAO


@pytest.mark.parametrize(
    "content",
    [b"not json", b"[]", b'{"schema": 2, "panels": []}', b'{"schema": true}', b'{"schema": "1"}',
     b'{"schema": 0}', b'{"panels": {}}', b'{"panels": [1], "x": NaN}', b"\xff\xfe\x00", b"[" * 200_000,
     b'{"schema": 1, "panels": [' + b"{}," * 41 + b"{}]}"],
)  # fmt: skip
def test_an_unusable_file_is_never_overwritten(tmp_path, content):
    P.panels_path(tmp_path).write_bytes(content)
    assert P.list_panels(tmp_path)["invalid"] is True
    for call in (
        lambda: P.create(tmp_path, PANEL),
        lambda: P.update(tmp_path, "0" * 12, PANEL),
        lambda: P.delete(tmp_path, "0" * 12),
    ):
        assert code(call) == "file_invalid"
    assert P.panels_path(tmp_path).read_bytes() == content


def test_an_oversized_file_is_invalid(tmp_path):
    P.panels_path(tmp_path).write_bytes(b" " * (P.MAX_FILE_BYTES + 1))
    assert P.load(tmp_path).invalid


def test_panels_that_fail_validation_are_reported_and_kept(tmp_path):
    good = P.create(tmp_path, PANEL)
    stray = {"id": "ffffffffffff", "rev": 1, "name": "Hand edited", "widgets": [{"bogus": True}]}
    data = json.loads(P.panels_path(tmp_path).read_text())
    data["panels"].append(stray)
    data["panels"].append(copy.deepcopy(data["panels"][0]))  # same id and name
    data["future_key"] = {"keep": "me"}
    P.panels_path(tmp_path).write_text(json.dumps(data))
    listing = P.list_panels(tmp_path)
    assert [p["id"] for p in listing["panels"]] == [good["id"]]
    assert listing["problems"] == [{"index": 1, "code": "bad_widget"}, {"index": 2, "code": "duplicate_name"}]
    assert "Hand edited" not in json.dumps(listing["problems"])
    P.create(tmp_path, panel(name="Second"))  # a write keeps the skipped entries and the unknown key
    after = json.loads(P.panels_path(tmp_path).read_text())
    assert stray in after["panels"] and after["future_key"] == {"keep": "me"}
    assert [p["name"] for p in after["panels"][:2]] == ["Overview", "Second"]


# --- versioning ----------------------------------------------------------------------------------


def _v1_to_v2(doc):
    doc = {k: v for k, v in doc.items() if k != "panels"} | {
        "schema": 2,
        "panels": [{**p, "name": p["name"] + "!"} for p in doc["panels"]],
    }
    return doc


def _v2_to_v3(doc):
    return {**doc, "schema": 3, "note": "migrated"}


def test_migrations_chain_and_are_pure():
    original = {"schema": 1, "panels": [{"name": "A"}]}
    frozen = copy.deepcopy(original)
    steps = {1: _v1_to_v2, 2: _v2_to_v3}
    out = P.migrate(original, migrations=steps, target=3)
    assert out == {"schema": 3, "panels": [{"name": "A!"}], "note": "migrated"}
    assert original == frozen
    assert P.migrate(original, migrations=steps, target=2)["schema"] == 2
    assert P.migrate(original, migrations=steps, target=1) == original
    assert P.migrate({"panels": []}, migrations={}, target=1)["schema"] == 1  # a missing schema is v1


@pytest.mark.parametrize(
    ("data", "steps", "target"),
    [
        ({"schema": 4}, {}, 3),  # newer than this build
        ({"schema": 1}, {}, 2),  # no step
        ({"schema": 1}, {1: lambda d: {**d, "schema": 5}}, 2),  # step lies about its result
        ({"schema": 1}, {1: lambda d: 1 / 0}, 2),
        ({"schema": 1}, {1: lambda d: []}, 2),
        ([], {}, 1),
    ],
)
def test_migration_failures_make_the_file_invalid(data, steps, target):
    with pytest.raises(P.FileInvalid):
        P.migrate(data, migrations=steps, target=target)


def test_an_old_file_is_read_through_the_chain(monkeypatch):
    monkeypatch.setattr(P, "SCHEMA", 2)
    doc = P.parse_document(
        {"schema": 1, "panels": [{"id": "0123456789ab", "rev": 1, "name": "A", "widgets": []}]},
        migrations={1: lambda d: {**d, "schema": 2}},
    )
    assert not doc.invalid and doc.panels[0]["name"] == "A" and doc.problems == []


# --- review round: ids, locks, final-document validation, copies, overflow -------------------------


@pytest.mark.parametrize("wid", ["_errors", "-count", "_", "-", "a_b-c", "0", "a" * 32])
def test_widget_ids_accept_the_documented_alphabet_from_the_first_character(wid):
    assert P.validate_panel(panel(widget(id=wid)))["widgets"][0]["id"] == wid


@pytest.mark.parametrize("wid", ["", "A", "a b", "a.b", "a" * 33, "\u00e9", "a\n", None, 5])
def test_widget_ids_outside_the_alphabet_are_refused(wid):
    assert code(P.validate_panel, panel(widget(id=wid))) == "bad_widget_id"


def _quarantine(tmp_path, extra):
    """A file with one valid panel and ``extra`` hand-edited invalid entries."""
    good = P.create(tmp_path, PANEL)
    data = json.loads(P.panels_path(tmp_path).read_text())
    data["panels"].extend(extra)
    P.panels_path(tmp_path).write_text(json.dumps(data))
    return good


def test_a_full_quarantine_cannot_make_the_file_invalid(tmp_path):
    stray = [{"id": f"{k:012x}", "rev": 1, "name": f"s{k}", "widgets": [{"bogus": 1}]} for k in range(39)]
    _quarantine(tmp_path, stray)  # 1 valid + 39 invalid = 40 = the reading limit
    before = P.panels_path(tmp_path).read_bytes()
    assert code(P.create, tmp_path, panel(name="one too many")) == "too_many_panels"
    assert P.panels_path(tmp_path).read_bytes() == before
    assert P.list_panels(tmp_path)["invalid"] is False  # still readable
    made = P.list_panels(tmp_path)["panels"][0]
    P.delete(tmp_path, made["id"])  # freeing a slot is allowed
    assert P.create(tmp_path, panel(name="fits now"))["rev"] == 1
    assert P.list_panels(tmp_path)["invalid"] is False


def test_rev_cannot_overflow(tmp_path):
    made = P.create(tmp_path, PANEL)
    data = json.loads(P.panels_path(tmp_path).read_text())
    data["panels"][0]["rev"] = 2**63 - 1
    P.panels_path(tmp_path).write_text(json.dumps(data))
    before = P.panels_path(tmp_path).read_bytes()
    assert code(P.update, tmp_path, made["id"], panel(name="Again")) == "rev_limit"
    assert P.panels_path(tmp_path).read_bytes() == before and not P.list_panels(tmp_path)["problems"]


def test_save_refuses_a_document_the_reader_would_reinterpret(tmp_path):
    doc = P.Document(panels=[{"id": "0123456789ab", "rev": 2**63, "name": "A", "widgets": []}])
    with pytest.raises(P.PanelError) as info:
        P._save(tmp_path, doc)
    assert info.value.code == "save_failed" and not P.panels_path(tmp_path).exists()


def test_deleting_a_panel_removes_its_quarantined_copies(tmp_path):
    good = P.create(tmp_path, PANEL)
    dup_id = copy.deepcopy(good) | {"widgets": [{"bogus": 1}]}  # same id, broken
    dup_name = {"id": "ffffffffffff", "rev": 1, "name": "OVERVIEW", "widgets": []}  # same name, other id
    data = json.loads(P.panels_path(tmp_path).read_text())
    data["panels"] += [dup_id, dup_name]
    P.panels_path(tmp_path).write_text(json.dumps(data))
    assert [p["code"] for p in P.list_panels(tmp_path)["problems"]] == ["bad_widget", "duplicate_name"]
    P.delete(tmp_path, good["id"])
    assert P.list_panels(tmp_path) == {
        "panels": [],
        "invalid": False,
        "problems": [],
        "limits": P.LIMITS,
    }  # nothing resurrects
    assert json.loads(P.panels_path(tmp_path).read_text())["panels"] == []


def test_renaming_does_not_resurrect_a_quarantined_copy_of_the_old_name(tmp_path):
    good = P.create(tmp_path, PANEL)
    ghost = {"id": "ffffffffffff", "rev": 1, "name": "overview", "widgets": []}
    data = json.loads(P.panels_path(tmp_path).read_text())
    data["panels"].append(ghost)
    P.panels_path(tmp_path).write_text(json.dumps(data))
    P.update(tmp_path, good["id"], panel(name="Elsewhere"))
    assert [p["name"] for p in P.list_panels(tmp_path)["panels"]] == ["Elsewhere"]
    assert P.list_panels(tmp_path)["problems"] == []


def test_the_lock_serializes_threads_and_times_out(tmp_path):
    import threading

    entered, release, outcome = threading.Event(), threading.Event(), []

    def holder():
        with P._exclusive(tmp_path):
            entered.set()
            release.wait(10)

    t = threading.Thread(target=holder)
    t.start()
    assert entered.wait(5)
    try:
        with pytest.raises(P.PanelError) as info, P._exclusive(tmp_path, timeout_s=0.1):
            outcome.append("inside")
        assert info.value.code == "busy" and outcome == []
    finally:
        release.set()
        t.join(5)
    with P._exclusive(tmp_path, timeout_s=1.0):
        pass  # free again
    assert (tmp_path / P.LOCK_FILENAME).exists()


WORKER = """
import json, sys
from dmslog.store import panels as P
data_dir, ident = sys.argv[1], int(sys.argv[2])
out = []
for k in range(8):
    try:
        P.create(data_dir, {"name": f"w{ident}-{k}", "widgets": []})
        out.append("ok")
    except P.PanelError as exc:
        out.append(exc.code)
print(json.dumps(out))
"""


def test_two_processes_never_lose_an_update(tmp_path):
    import subprocess
    import sys

    env = {**os.environ, "PYTHONPATH": str(Path(P.__file__).resolve().parents[2])}
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", WORKER, str(tmp_path), str(i)], stdout=subprocess.PIPE, env=env, text=True
        )
        for i in range(2)
    ]
    outs = [json.loads(p.communicate(timeout=120)[0]) for p in procs]
    assert all(p.returncode == 0 for p in procs)
    created = sum(o.count("ok") for o in outs)
    names = [p["name"] for p in P.list_panels(tmp_path)["panels"]]
    assert created == len(names) == len(set(names)) and created <= P.MAX_PANELS
    assert set(sum(outs, [])) <= {"ok", "too_many_panels"}  # no busy, no lost write, no corruption
    assert P.list_panels(tmp_path)["invalid"] is False


def test_if_rev_lets_exactly_one_of_two_stale_writers_win(tmp_path):
    made = P.create(tmp_path, PANEL)
    assert P.update(tmp_path, made["id"], panel(name="First"), 1)["rev"] == 2
    assert code(P.update, tmp_path, made["id"], panel(name="Late"), 1) == "rev_conflict"
