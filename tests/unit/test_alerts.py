"""Local alerts (store/alerts.py): rules file, evaluation over the new records, cooldown, open operations."""

import json

import pytest
from query_helpers import build_case, open_ro

from dmslog.store import alerts, db

ERRORS = {"sev": {"le": 2}}


def rule(name="Errors", **extra):
    return {"name": name, "kind": "match", "filter": ERRORS, "severity": "warning", **extra}


# -- the rules file ----------------------------------------------------------------------------------


def test_lifecycle_and_roundtrip(tmp_path):
    made = alerts.create(tmp_path, rule(" Errors ", severity="critical", min_count=3))
    assert made["name"] == "Errors" and made["enabled"] is True and made["min_count"] == 3
    alerts.create(tmp_path, {"name": "Slow", "kind": "open_operation", "timeout_param": "SendTimeout"})
    alerts.create(
        tmp_path, {"name": "Burst", "kind": "threshold", "filter": ERRORS, "threshold": 5, "window_s": 60}
    )
    listing = alerts.list_rules(tmp_path)
    assert [r["name"] for r in listing["rules"]] == ["Errors", "Slow", "Burst"] and not listing["invalid"]
    updated = alerts.update(tmp_path, "errors", {**rule("Errors 2"), "enabled": False})
    assert updated["enabled"] is False
    assert alerts.load(tmp_path).rules[0]["name"] == "Errors 2"
    alerts.delete(tmp_path, "ERRORS 2")
    assert [r["name"] for r in alerts.list_rules(tmp_path)["rules"]] == ["Slow", "Burst"]
    with pytest.raises(alerts.AlertError) as err:
        alerts.delete(tmp_path, "nope")
    assert err.value.code == "not_found"


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ({"name": "", "kind": "match", "filter": ERRORS}, "bad_name"),
        ({"name": "a", "kind": "zzz"}, "bad_kind"),
        ({"name": "a", "kind": "match", "filter": ERRORS, "severity": "loud"}, "bad_severity"),
        ({"name": "a", "kind": "match"}, "bad_filter"),
        ({"name": "a", "kind": "match", "filter": {"zzz": 1}}, "filter_invalid"),
        ({"name": "a", "kind": "match", "filter": {"source": 1}}, "case_specific"),
        ({"name": "a", "kind": "match", "filter": {"field": {"name": "x", "eq": None}}}, "bad_rule"),
        ({"name": "a", "kind": "match", "filter": ERRORS, "min_count": 0}, "bad_count"),
        ({"name": "a", "kind": "match", "filter": ERRORS, "enabled": "yes"}, "bad_enabled"),
        ({"name": "a", "kind": "match", "filter": ERRORS, "min_interval_s": -1}, "bad_interval"),
        ({"name": "a", "kind": "threshold", "filter": ERRORS, "threshold": 1}, "bad_window"),
        ({"name": "a", "kind": "open_operation"}, "bad_timeout_source"),
        ({"name": "a", "kind": "open_operation", "timeout_s": 5, "timeout_param": "X"}, "bad_timeout_source"),
        ({"name": "a", "kind": "open_operation", "timeout_param": "bad name"}, "bad_timeout_param"),
        ({"name": "a", "kind": "open_operation", "timeout_s": 0}, "bad_timeout"),
        ("x", "bad_rule"),
    ],
)
def test_invalid_rules_are_rejected(raw, code):
    with pytest.raises(alerts.AlertError) as err:
        alerts.validate_rule(raw)
    assert err.value.code == code


def test_duplicates_and_limits(tmp_path, monkeypatch):
    alerts.create(tmp_path, rule("A"))
    with pytest.raises(alerts.AlertError) as err:
        alerts.create(tmp_path, rule("a"))
    assert err.value.code == "duplicate_name"
    monkeypatch.setattr(alerts, "MAX_RULES", 1)
    with pytest.raises(alerts.AlertError) as err:
        alerts.create(tmp_path, rule("B"))
    assert err.value.code == "too_many_rules"


def test_reading_is_tolerant_and_keeps_unknown_keys(tmp_path):
    (tmp_path / alerts.FILENAME).write_text(
        'schema = 1\nfuture = "x"\n\n[[rule]]\nname = "ok"\nkind = "match"\n'
        'filter = {sev = {le = 2}}\nnote = "keep"\n'
        '\n[[rule]]\nname = "bad"\nkind = "match"\nfilter = {zzz = 1}\n',
        encoding="utf-8",
    )
    listing = alerts.list_rules(tmp_path)
    assert [r["name"] for r in listing["rules"]] == ["ok"]
    assert listing["problems"][0]["code"] == "filter_invalid" and not listing["invalid"]
    alerts.create(tmp_path, rule("two"))
    text = (tmp_path / alerts.FILENAME).read_text(encoding="utf-8")
    assert 'future = "x"' in text and 'note = "keep"' in text and "bad" not in text.split("[[rule]]", 1)[1]


@pytest.mark.parametrize("content", ["not toml [", "schema = 2\n", "schema = 1\nrule = 3\n"])
def test_an_unusable_file_is_never_overwritten(tmp_path, content):
    path = tmp_path / alerts.FILENAME
    path.write_text(content, encoding="utf-8")
    assert alerts.list_rules(tmp_path)["invalid"] is True
    with pytest.raises(alerts.AlertError) as err:
        alerts.create(tmp_path, rule())
    assert err.value.code == "file_invalid" and path.read_text(encoding="utf-8") == content


def test_the_file_holds_no_case_data(tmp_path):
    alerts.create(tmp_path, rule())
    text = (tmp_path / alerts.FILENAME).read_text(encoding="utf-8")
    assert "schema = 1" in text and str(tmp_path) not in text
    assert not list(tmp_path.glob("*.tmp"))


def test_duration_parsing():
    parse = alerts.parse_duration_s
    assert parse("30") == 30 and parse("1 min") == 60 and parse("2.5 s") == 2.5 and parse("500 ms") == 0.5
    assert parse("0:00:35") == 35 and parse("00:01:00") == 60
    assert parse("soon") is None and parse("") is None and parse(None) is None and parse("0") is None


# -- evaluation ----------------------------------------------------------------------------------------


@pytest.fixture
def case_conn(tmp_path):
    path = tmp_path / "case" / "index.sqlite"
    rows = build_case(path)
    conn = db.connect(path)
    yield conn, rows
    conn.close()


def last_ids(conn, n):
    return conn.execute("SELECT max(id) FROM entry").fetchone()[0] - n


def test_match_looks_only_at_the_new_records(case_conn):
    conn, _ = case_conn
    total = conn.execute("SELECT max(id) FROM entry").fetchone()[0]
    everything, _ = alerts.evaluate_rule(conn, rule(), 0, total)
    expected = conn.execute("SELECT count(*) FROM entry WHERE sev <= 2").fetchone()[0]
    assert everything.count == expected and expected > 3
    assert everything.sample_ids == sorted(everything.sample_ids) and len(everything.sample_ids) <= 5
    half = total // 2
    newer, _ = alerts.evaluate_rule(conn, rule(), half, total)
    assert (
        newer.count
        == conn.execute("SELECT count(*) FROM entry WHERE sev <= 2 AND id > ?", (half,)).fetchone()[0]
    )
    assert all(i > half for i in newer.sample_ids)
    none, _ = alerts.evaluate_rule(conn, rule(), total, total)
    assert none is None
    big, _ = alerts.evaluate_rule(conn, rule(min_count=10_000), 0, total)
    assert big is None


def test_match_by_template(case_conn):
    conn, _ = case_conn
    total = conn.execute("SELECT max(id) FROM entry").fetchone()[0]
    got, _ = alerts.evaluate_rule(
        conn, {"name": "t", "kind": "match", "severity": "info", "filter": {"tpl": "TST-001"}}, 0, total
    )
    assert got.count == conn.execute("SELECT count(*) FROM entry WHERE tpl_id = 1").fetchone()[0]


def test_threshold_counts_the_window_that_ends_at_the_newest_match(case_conn):
    conn, _ = case_conn
    total = conn.execute("SELECT max(id) FROM entry").fetchone()[0]
    base = {"name": "b", "kind": "threshold", "severity": "critical", "filter": ERRORS, "window_s": 1000}
    wide, _ = alerts.evaluate_rule(conn, {**base, "threshold": 1}, total - 5, total)
    assert (
        wide is not None
        and wide.count > 1
        and wide.count
        == conn.execute(
            "SELECT count(*) FROM entry WHERE sev <= 2 AND ts >= ? AND ts <= ?", (wide.ts_from, wide.ts_to)
        ).fetchone()[0]
    )
    assert alerts.evaluate_rule(conn, {**base, "threshold": 10_000}, total - 5, total)[0] is None
    # no new match: no alert even if the window is full of old ones
    assert alerts.evaluate_rule(conn, {**base, "threshold": 1}, total, total)[0] is None


def test_alert_event_carries_no_log_text(case_conn):
    conn, rows = case_conn
    got, _ = alerts.evaluate_rule(conn, rule(), 0, len(rows))
    body = json.dumps(got.to_event())
    assert set(got.to_event()) == {
        "rule",
        "kind",
        "severity",
        "count",
        "sample_ids",
        "ts_from",
        "ts_to",
        "event_ids",
    }
    assert all(r["msg"] not in body for r in rows[:50])


def add_open_operations(
    conn, ts_starts, process="WfmAdapter", param=("SendTimeout", "0:00:30"), last_ts=1_100_000
):
    with conn:
        conn.execute("UPDATE source SET last_ts = ? WHERE stream_id = 1", (last_ts,))
        if param is not None:
            conn.execute(
                "INSERT INTO config_param(collection_group, product, scope, key_path, name, value,"
                " source_file, layer, effective, status)"
                " VALUES (1, 'AdapterWfm', 'registry', '', ?, ?, 'f.xml', -1, 1, 'only')",
                param,
            )
        for k, ts in enumerate(ts_starts):
            conn.execute(
                "INSERT INTO event(id, stream_id, kind, name, ts_start, status, sev, start_entry_id)"
                " VALUES (?, 1, 'operation', 'Send', ?, 'open', 3, ?)",
                (100 + k, ts, 10 + k),
            )


def test_operation_without_end_after_the_adapter_timeout(case_conn):
    conn, _ = case_conn
    add_open_operations(conn, [1_000_000, 1_090_000])  # 100 s and 10 s before the newest record; timeout 30 s
    open_rule = {
        "name": "slow",
        "kind": "open_operation",
        "severity": "warning",
        "timeout_param": "SendTimeout",
    }
    assert alerts.rule_status(conn, open_rule) == {"available": True, "reason": None}
    seen: set = set()
    got, keys = alerts.evaluate_rule(conn, open_rule, 0, 1, seen)
    assert got.count == 1 and got.event_ids == [100] and got.sample_ids == [10]
    seen.update(keys)
    again, _ = alerts.evaluate_rule(conn, open_rule, 0, 1, seen)
    assert again is None  # already alerted
    fixed_rule = {"name": "f", "kind": "open_operation", "severity": "info", "timeout_s": 5}
    fixed, _ = alerts.evaluate_rule(conn, fixed_rule, 0, 1, set())
    assert fixed.count == 2  # a fixed timeout does not need the Registry (10 s > 5 s)
    named, _ = alerts.evaluate_rule(conn, {**open_rule, "operation": "Other"}, 0, 1, set())
    assert named is None


def test_open_operation_is_unavailable_without_a_timeout_value(case_conn):
    conn, _ = case_conn
    add_open_operations(conn, [1_000_000], param=None)
    open_rule = {
        "name": "slow",
        "kind": "open_operation",
        "severity": "warning",
        "timeout_param": "SendTimeout",
    }
    assert alerts.rule_status(conn, open_rule) == {"available": False, "reason": "timeout_unavailable"}
    assert alerts.evaluate_rule(conn, open_rule, 0, 1, set())[0] is None
    with conn:
        conn.execute(
            "INSERT INTO config_param(collection_group, product, scope, key_path, name, value, source_file,"
            " layer, effective, status)"
            " VALUES (1, 'AdapterWfm', 'registry', '', 'SendTimeout', 'later', 'f', -1, 1, 'only')"
        )
    assert alerts.rule_status(conn, open_rule)["available"] is False  # not a duration


def test_a_recent_operation_is_not_an_alert(case_conn):
    conn, _ = case_conn
    add_open_operations(conn, [1_095_000])  # 5 s before the newest record, timeout 30 s
    open_rule = {
        "name": "slow",
        "kind": "open_operation",
        "severity": "warning",
        "timeout_param": "SendTimeout",
    }
    assert alerts.evaluate_rule(conn, open_rule, 0, 1, set())[0] is None


# -- cycle: cooldown, dedupe, history --------------------------------------------------------------------


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def test_process_cycle_dedupes_cools_down_and_keeps_history(case_conn, tmp_path):
    conn, _ = case_conn
    data = tmp_path / "data"
    alerts.create(data, rule("Errors", min_interval_s=60))
    alerts.create(data, rule("Off", enabled=False))
    clock = Clock()
    state = alerts.AlertState(clock)
    first = state.process_cycle(conn, data, "demo", 0)
    assert [a["rule"] for a in first] == ["Errors"] and first[0]["case"] == "demo" and first[0]["id"] == 1
    clock.now += 10
    assert state.process_cycle(conn, data, "demo", 0) == []  # minimum delay of the rule
    clock.now += 60
    again = state.process_cycle(conn, data, "demo", 0)
    assert len(again) == 1 and again[0]["id"] == 2
    assert [a["id"] for a in state.history("demo")] == [1, 2]
    total = conn.execute("SELECT max(id) FROM entry").fetchone()[0]
    clock.now += 100
    assert state.process_cycle(conn, data, "demo", total) == []  # nothing new
    state.clear_history("demo")
    assert state.history("demo") == []


def test_process_cycle_survives_a_rule_that_times_out(case_conn, tmp_path, monkeypatch):
    conn, _ = case_conn
    data = tmp_path / "data"
    alerts.create(data, rule("Errors", min_interval_s=0))

    def interrupted(*args, **kwargs):
        raise alerts.query.QueryTimeout("simulated")

    monkeypatch.setattr(alerts, "evaluate_rule", interrupted)
    state = alerts.AlertState()
    assert state.process_cycle(conn, data, "demo", 0) == []  # interrupted: skipped, no exception


def test_open_operations_are_alerted_once_per_cycle_state(case_conn, tmp_path):
    conn, _ = case_conn
    add_open_operations(conn, [1_000_000])
    data = tmp_path / "data"
    alerts.create(data, {"name": "slow", "kind": "open_operation", "timeout_param": "SendTimeout",
                         "min_interval_s": 0})  # fmt: skip
    state = alerts.AlertState()
    assert len(state.process_cycle(conn, data, "demo", 0)) == 1
    assert state.process_cycle(conn, data, "demo", 0) == []  # the same operation is not alerted again
    state.forget_case("demo")
    assert len(state.process_cycle(conn, data, "demo", 0)) == 1


def test_read_only_connection_is_enough(tmp_path):
    path = tmp_path / "case" / "index.sqlite"
    build_case(path)
    conn = open_ro(path)
    try:
        total = conn.execute("SELECT max(id) FROM entry").fetchone()[0]
        assert alerts.evaluate_rule(conn, rule(), 0, total)[0] is not None
    finally:
        conn.close()
