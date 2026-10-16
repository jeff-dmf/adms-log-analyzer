"""Versioned preferences package (store/preferences.py, ROADMAP 9.10): export, import with report,
migration chain, limits, atomic writes and rollback. Synthetic data only."""

import copy
import json
import os
import threading

import pytest

from dmslog import paths
from dmslog.report import redact
from dmslog.store import alerts, preferences, presets

TREE = {"or": [{"sev": {"le": 2}}, {"tpl": ["GEN-EXC-01"]}]}
ERRORS = {"sev": {"le": 2}}

PRESET_A = {"name": "My errors", "description": "errors", "family": None, "filter": TREE}
PRESET_B = {"name": "Wfm only", "description": "", "family": "WFM", "filter": ERRORS}
RULE_A = {"name": "Errors", "kind": "match", "filter": ERRORS, "severity": "warning", "min_count": 3}
RULE_B = {"name": "Slow", "kind": "open_operation", "timeout_param": "SendTimeout"}
PROFILE = {
    "name": "work",
    "categories": {"ip": False},
    "terms": {"word": ["Acme"], "literal": []},
    "exceptions": ["keep-me"],
    "payload_names": ["Name"],
    "mode": "standard",
}


def package(**sections):
    return {"schema": 1, "kind": preferences.KIND, **sections}


def fill(data_dir, *, order=1):
    items = [PRESET_A, PRESET_B][::order]
    for item in items:
        presets.create(data_dir, item)
    for rule in [RULE_A, RULE_B][::order]:
        alerts.create(data_dir, rule)
    redact.save_profile(data_dir, redact.profile_from_dict(PROFILE))


def snapshot(data_dir):
    return {p.name: p.read_bytes() for p in sorted(data_dir.iterdir()) if p.is_file()}


# -- export -----------------------------------------------------------------------------------------


def test_export_empty_dir_is_literal(tmp_path):
    out = preferences.export_package(tmp_path)
    assert out["package"] == {"schema": 1, "kind": "dmslog-preferences", "presets": [], "alert_rules": []}
    assert out["text"] == (
        '{\n  "alert_rules": [],\n  "kind": "dmslog-preferences",\n  "presets": [],\n  "schema": 1\n}\n'
    )
    assert out["warnings"] == []


def test_export_literal_package(tmp_path):
    fill(tmp_path)
    pkg = preferences.export_package(tmp_path)["package"]
    assert pkg["schema"] == 1 and pkg["kind"] == "dmslog-preferences"
    # sorted by (family, name): the global preset first, then WFM
    assert pkg["presets"] == [
        {"name": "My errors", "description": "errors", "family": None, "filter": TREE},
        {"name": "Wfm only", "description": "", "family": "WFM", "filter": ERRORS},
    ]
    assert [r["name"] for r in pkg["alert_rules"]] == ["Errors", "Slow"]
    assert pkg["alert_rules"][0] == {
        "name": "Errors", "kind": "match", "enabled": True, "severity": "warning", "min_interval_s": 30,
        "filter": ERRORS, "min_count": 3,
    }  # fmt: skip
    assert pkg["redaction"]["name"] == "work" and pkg["redaction"]["categories"]["ip"] is False
    assert "builtin" not in json.dumps(pkg)


def test_export_is_deterministic_and_order_independent(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    fill(a, order=1)
    fill(b, order=-1)
    text_a = preferences.export_package(a)["text"]
    assert text_a == preferences.export_package(a)["text"] == preferences.export_package(b)["text"]
    assert text_a.endswith("\n") and "T" not in "".join(json.loads(text_a)["kind"])  # no timestamps


def test_export_sections_and_invalid_files(tmp_path):
    fill(tmp_path)
    only = preferences.export_package(tmp_path, ["presets"])["package"]
    assert set(only) == {"schema", "kind", "presets"}
    with pytest.raises(preferences.PreferencesError) as err:
        preferences.export_package(tmp_path, ["nope"])
    assert err.value.code == "bad_option"
    (tmp_path / "alerts.toml").write_text("not [ toml", encoding="utf-8")
    (tmp_path / "redaction.toml").write_text("version = 99\n", encoding="utf-8")
    out = preferences.export_package(tmp_path)
    assert "alert_rules" not in out["package"] and "redaction" not in out["package"]
    assert {(w["section"], w["code"]) for w in out["warnings"]} == {
        ("alert_rules", "file_invalid"), ("redaction", "file_invalid")
    }  # fmt: skip


# -- round trip and idempotence ---------------------------------------------------------------------


def test_roundtrip_to_fresh_dir_and_idempotent(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    fill(src)
    exported = preferences.export_package(src)
    report = preferences.import_package(dst, exported["text"])
    assert report["outcome"] == "applied" and report["ok"] and report["applied"]
    assert report["summary"] == {"added": 5, "updated": 0, "unchanged": 0, "rejected": 0, "removed": 0}
    assert preferences.export_package(dst)["text"] == exported["text"]
    before = snapshot(dst)
    again = preferences.import_package(dst, exported["package"])
    assert again["outcome"] == "unchanged" and again["applied"] is False
    assert again["summary"]["unchanged"] == 5 and again["summary"]["added"] == 0
    assert snapshot(dst) == before


def test_second_import_does_not_touch_files(tmp_path, monkeypatch):
    fill(tmp_path / "s")
    pkg = preferences.export_package(tmp_path / "s")["package"]
    preferences.import_package(tmp_path / "d", pkg)
    calls = []
    monkeypatch.setattr(preferences, "atomic_write_bytes", lambda *a: calls.append(a))
    assert preferences.import_package(tmp_path / "d", pkg)["outcome"] == "unchanged"
    assert calls == []


def test_dry_run_reports_and_writes_nothing(tmp_path):
    fill(tmp_path / "s")
    pkg = preferences.export_package(tmp_path / "s")["package"]
    dst = tmp_path / "d"
    dry = preferences.import_package(dst, pkg, dry_run=True)
    assert not dst.exists() and dry["applied"] is False and dry["dry_run"] is True
    real = preferences.import_package(dst, pkg)
    assert {k: v for k, v in dry.items() if k not in ("applied", "dry_run")} == {
        k: v for k, v in real.items() if k not in ("applied", "dry_run")
    }
    assert real["applied"] is True


def test_dry_run_rejects_non_bool(tmp_path):
    with pytest.raises(preferences.PreferencesError):
        preferences.import_package(tmp_path, package(), dry_run=1)  # type: ignore[arg-type]


# -- merge, replace ---------------------------------------------------------------------------------


def test_merge_updates_in_place_adds_and_keeps_others_and_extras(tmp_path):
    presets.create(tmp_path, {**PRESET_A, "future_field": 1})
    presets.create(tmp_path, PRESET_B)
    changed = {**PRESET_A, "description": "new text"}
    extra = {"name": "Third", "filter": ERRORS}
    rep = preferences.import_package(tmp_path, package(presets=[extra, changed]))
    assert [i["outcome"] for i in rep["sections"]["presets"]["items"]] == ["added", "updated"]
    stored = presets.load_user(tmp_path).presets
    assert [p["name"] for p in stored] == ["My errors", "Wfm only", "Third"]  # file order kept
    assert stored[0]["description"] == "new text" and stored[0]["extra"] == {"future_field": 1}


def test_identity_is_case_insensitive_and_family_aware(tmp_path):
    presets.create(tmp_path, PRESET_A)
    other_family = {**PRESET_A, "family": "WFM"}
    rep = preferences.import_package(
        tmp_path, package(presets=[{**PRESET_A, "name": "MY ERRORS"}, other_family])
    )
    assert [i["outcome"] for i in rep["sections"]["presets"]["items"]] == ["updated", "added"]


def test_replace_removes_the_rest_and_orders_like_the_package(tmp_path):
    fill(tmp_path)
    rep = preferences.import_package(tmp_path, package(presets=[PRESET_B]), mode="replace")
    sec = rep["sections"]["presets"]
    assert sec["removed"] == ["My errors"] and sec["counts"]["removed"] == 1
    assert [p["name"] for p in presets.load_user(tmp_path).presets] == ["Wfm only"]
    assert len(alerts.load(tmp_path).rules) == 2  # absent sections are untouched
    again = preferences.import_package(tmp_path, package(presets=[PRESET_B]), mode="replace")
    assert again["outcome"] == "unchanged"


def test_replace_never_deletes_because_an_item_was_refused(tmp_path):
    fill(tmp_path)
    bad = {**PRESET_A, "filter": {"zzz": 1}}
    rep = preferences.import_package(
        tmp_path, package(presets=[bad]), mode="replace", on_item_error="skip_item"
    )
    assert rep["sections"]["presets"]["status"] == "rejected"
    assert {p["name"] for p in presets.load_user(tmp_path).presets} == {"My errors", "Wfm only"}


def test_replace_with_empty_list_clears(tmp_path):
    fill(tmp_path)
    rep = preferences.import_package(tmp_path, package(alert_rules=[]), mode="replace")
    assert rep["sections"]["alert_rules"]["counts"]["removed"] == 2
    assert alerts.load(tmp_path).rules == []


# -- refusals, per item and per section -------------------------------------------------------------


def codes(report, section):
    return [
        (i["index"], i.get("code"))
        for i in report["sections"][section]["items"]
        if i["outcome"] == "rejected"
    ]


def test_one_bad_item_rejects_only_its_section(tmp_path):
    pkg = package(
        presets=[PRESET_A, {"name": "bad", "filter": {"source": 1}}],
        alert_rules=[RULE_A],
    )
    rep = preferences.import_package(tmp_path, pkg)
    assert rep["outcome"] == "partial" and rep["ok"] is False and rep["applied"] is True
    assert rep["sections"]["presets"]["status"] == "rejected"
    assert codes(rep, "presets") == [(1, "case_specific")]
    assert rep["sections"]["presets"]["items"][0]["outcome"] == "not_applied"
    assert rep["sections"]["alert_rules"]["status"] == "changed"
    assert presets.load_user(tmp_path).presets == [] and len(alerts.load(tmp_path).rules) == 1


def test_skip_item_policy_lets_valid_items_in(tmp_path):
    pkg = package(presets=[PRESET_A, {"name": "bad", "filter": {"source": 1}}, PRESET_B])
    rep = preferences.import_package(tmp_path, pkg, on_item_error="skip_item")
    assert rep["summary"]["added"] == 2 and rep["summary"]["rejected"] == 1
    assert [p["name"] for p in presets.load_user(tmp_path).presets] == ["My errors", "Wfm only"]
    assert rep["outcome"] == "applied"


@pytest.mark.parametrize(
    ("section", "item", "code"),
    [
        ("presets", "text", "bad_item"),
        ("presets", {**PRESET_A, "surprise": 1}, "unknown_field"),
        ("presets", {**PRESET_A, "name": ""}, "bad_name"),
        ("presets", {**PRESET_A, "family": "no good"}, "bad_family"),
        ("presets", {**PRESET_A, "filter": {"zzz": 1}}, "filter_invalid"),
        ("presets", {**PRESET_A, "name": "Errors and exceptions"}, "reserved_name"),
        ("presets", {**PRESET_A, "filter": {"sev": {"le": None}}}, None),
        ("alert_rules", {**RULE_A, "enabled": "yes"}, "bad_enabled"),
        ("alert_rules", {**RULE_A, "enabled": 1}, "bad_enabled"),
        ("alert_rules", {**RULE_A, "min_count": True}, "bad_count"),
        ("alert_rules", {**RULE_A, "min_count": 1.0}, "bad_count"),
        ("alert_rules", {**RULE_A, "min_count": 2**62}, "bad_count"),
        ("alert_rules", {**RULE_A, "kind": "other"}, "bad_kind"),
        ("alert_rules", {**RULE_A, "extra_thing": 1}, "unknown_field"),
    ],
)
def test_item_refusals(tmp_path, section, item, code):
    rep = preferences.import_package(tmp_path, package(**{section: [item]}))
    assert rep["sections"][section]["status"] == "rejected"
    (row,) = rep["sections"][section]["items"]
    assert row["outcome"] == "rejected" and (code is None or row["code"] == code)
    assert snapshot(tmp_path) == {}


def test_duplicate_names_in_the_package(tmp_path):
    pkg = package(presets=[PRESET_A, {**PRESET_A, "name": "my ERRORS"}], alert_rules=[RULE_A, {**RULE_A}])
    rep = preferences.import_package(tmp_path, pkg, on_item_error="skip_item")
    assert codes(rep, "presets") == [(1, "duplicate_name")]
    assert codes(rep, "alert_rules") == [(1, "duplicate_name")]
    assert len(presets.load_user(tmp_path).presets) == 1


def test_redaction_refusals(tmp_path):
    bad_profiles = [
        "x",
        {**PROFILE, "mode": "nope"},
        {**PROFILE, "color": 1},
        {**PROFILE, "categories": {"ip": 1}},
        {**PROFILE, "terms": {"word": ["a"], "bogus": []}},
    ]
    for bad in bad_profiles:
        rep = preferences.import_package(tmp_path, package(redaction=bad))
        assert rep["sections"]["redaction"]["status"] == "rejected", bad
    assert not (tmp_path / "redaction.toml").exists()


def test_redaction_replaces_as_a_whole(tmp_path):
    rep = preferences.import_package(tmp_path, package(redaction=PROFILE))
    assert rep["sections"]["redaction"]["counts"]["added"] == 1
    rep = preferences.import_package(tmp_path, package(redaction={**PROFILE, "mode": "strict"}))
    assert rep["sections"]["redaction"]["counts"]["updated"] == 1
    assert redact.load_profile(tmp_path).mode == "strict"
    assert (
        preferences.import_package(tmp_path, package(redaction={**PROFILE, "mode": "strict"}))["outcome"]
        == "unchanged"
    )


def test_invalid_stored_files_are_never_overwritten(tmp_path):
    (tmp_path / "presets.toml").write_text("this is [ not toml", encoding="utf-8")
    (tmp_path / "alerts.toml").write_text("schema = 99\n", encoding="utf-8")
    before = snapshot(tmp_path)
    rep = preferences.import_package(
        tmp_path, package(presets=[PRESET_A], alert_rules=[RULE_A], redaction=PROFILE)
    )
    assert rep["sections"]["presets"]["error"]["code"] == "file_invalid"
    assert rep["sections"]["alert_rules"]["error"]["code"] == "file_invalid"
    assert rep["sections"]["redaction"]["status"] == "changed"
    after = snapshot(tmp_path)
    assert {k: after[k] for k in before} == before


def test_invalid_stored_redaction_is_replaced_with_warning(tmp_path):
    (tmp_path / "redaction.toml").write_text("version = 99\n", encoding="utf-8")
    rep = preferences.import_package(tmp_path, package(redaction=PROFILE))
    assert rep["sections"]["redaction"]["warnings"][0]["code"] == "stored_profile_invalid"
    assert redact.load_profile(tmp_path).name == "work"


def test_total_item_limit(tmp_path):
    for n in range(presets.MAX_PRESETS):
        presets.create(tmp_path, {"name": f"p{n}", "filter": ERRORS})
    rep = preferences.import_package(tmp_path, package(presets=[{"name": "one more", "filter": ERRORS}]))
    assert rep["sections"]["presets"]["error"]["code"] == "too_many_items_total"
    rep = preferences.import_package(
        tmp_path, package(presets=[{"name": "one more", "filter": ERRORS}]), on_item_error="skip_item"
    )
    assert codes(rep, "presets") == [(0, "too_many_presets")]
    huge = [{"name": f"q{n}", "filter": ERRORS} for n in range(presets.MAX_PRESETS * 2 + 1)]
    rep = preferences.import_package(tmp_path, package(presets=huge))
    assert rep["sections"]["presets"]["error"]["code"] == "too_many_items"
    assert len(presets.load_user(tmp_path).presets) == presets.MAX_PRESETS


def test_existing_invalid_items_are_reported_when_the_file_is_rewritten(tmp_path):
    presets.create(tmp_path, PRESET_A)
    text = (tmp_path / "presets.toml").read_text(encoding="utf-8")
    (tmp_path / "presets.toml").write_text(
        text + '\n[[preset]]\nname = "broken"\nfilter = {zzz = 1}\n', encoding="utf-8"
    )
    rep = preferences.import_package(tmp_path, package(presets=[PRESET_B]))
    assert rep["sections"]["presets"]["warnings"][0]["code"] == "existing_items_dropped"


# -- structural errors: nothing is applied ----------------------------------------------------------


def deep(n):
    out: object = 1
    for _ in range(n):
        out = [out]
    return out


@pytest.mark.parametrize(
    ("pkg", "code"),
    [
        ([], "not_object"),
        ("[]", "not_object"),
        ("{", "bad_json"),
        (b"\xff\xfe", "bad_json"),
        ('{"schema": 1, "schema": 1}', "bad_json"),
        ('{"schema": NaN}', "bad_json"),
        ({"kind": preferences.KIND}, "schema_missing"),
        ({"schema": True, "kind": preferences.KIND}, "bad_schema"),
        ({"schema": 1.0, "kind": preferences.KIND}, "bad_schema"),
        ({"schema": "1", "kind": preferences.KIND}, "bad_schema"),
        ({"schema": 0, "kind": preferences.KIND}, "bad_schema"),
        ({"schema": -3, "kind": preferences.KIND}, "bad_schema"),
        ({"schema": 2, "kind": preferences.KIND}, "schema_too_new"),
        ({"schema": 1}, "bad_kind"),
        ({"schema": 1, "kind": "other"}, "bad_kind"),
        (package(extra_section=[]), "unknown_section"),
        (package(presets=deep(preferences.MAX_DEPTH + 5)), "package_too_complex"),
        (package(presets=[[1]] * 10, other=float("inf")), "bad_value"),
        (package(presets=[], redaction={"name": "x\ud800"}), "bad_value"),
        (package(presets=[{"name": "n", "filter": {"sev": {"le": 2**70}}}]), "bad_value"),
        (package(presets=[{"name": "n", "filter": {"sev": {"le": object()}}}]), "bad_value"),
        ({"schema": 1, "kind": preferences.KIND, 5: 1}, "bad_value"),
        ("x" * (preferences.MAX_PACKAGE_BYTES + 1), "package_too_large"),
    ],
)
def test_structural_errors_apply_nothing(tmp_path, pkg, code):
    presets.create(tmp_path, PRESET_A)
    before = snapshot(tmp_path)
    rep = preferences.import_package(tmp_path, pkg)
    assert rep["outcome"] == "rejected" and rep["ok"] is False and rep["applied"] is False
    assert rep["errors"][0]["code"] == code and rep["sections"] == {}
    assert snapshot(tmp_path) == before


def test_wide_package_is_refused(tmp_path):
    pkg = package(presets=[0] * (preferences.MAX_NODES + 1))
    assert preferences.import_package(tmp_path, pkg)["errors"][0]["code"] == "package_too_complex"


def test_options_are_validated(tmp_path):
    for kw in ({"mode": "x"}, {"on_item_error": "x"}):
        with pytest.raises(preferences.PreferencesError) as err:
            preferences.import_package(tmp_path, package(), **kw)
        assert err.value.code == "bad_option"


def test_package_is_not_modified_and_names_never_become_paths(tmp_path):
    evil = {"name": "../../../etc/passwd", "filter": ERRORS}
    pkg = package(presets=[evil])
    frozen = copy.deepcopy(pkg)
    preferences.import_package(tmp_path / "d", pkg)
    assert pkg == frozen
    assert sorted(p.name for p in (tmp_path / "d").iterdir()) == ["presets.toml"]
    assert not (tmp_path / "etc").exists()


# -- migration chain --------------------------------------------------------------------------------


def v1_to_v2(data):
    out = {k: v for k, v in data.items() if k != "alerts"}
    if "alerts" in data:  # schema 1 of the fixture called the section "alerts"
        out["alert_rules"] = data["alerts"]
    return out


def v2_to_v3(data):
    out = dict(data)
    out["presets"] = [{**p, "description": p.get("desc", "")} for p in data.get("presets", [])]
    for p in out["presets"]:
        p.pop("desc", None)
    return out


CHAIN = {1: v1_to_v2, 2: v2_to_v3}
V1 = {
    "schema": 1,
    "kind": preferences.KIND,
    "alerts": [RULE_A],
    "presets": [{"name": "m", "desc": "d", "filter": ERRORS}],
}
V2 = {
    "schema": 2,
    "kind": preferences.KIND,
    "alert_rules": [RULE_A],
    "presets": [{"name": "m", "desc": "d", "filter": ERRORS}],
}


def test_migration_chain_from_every_old_version():
    for old, steps in ((V1, [1, 2]), (V2, [2])):
        frozen = copy.deepcopy(old)
        migrated, done = preferences.migrate(old, 3, CHAIN)
        assert old == frozen  # pure
        assert done == steps and migrated["schema"] == 3
        assert migrated["alert_rules"] == [RULE_A] and "alerts" not in migrated
        assert migrated["presets"] == [{"name": "m", "description": "d", "filter": ERRORS}]
    assert preferences.migrate({"schema": 3, "kind": "k"}, 3, CHAIN)[1] == []


def test_chain_equals_applying_steps_by_hand():
    by_hand = {**v2_to_v3(v1_to_v2(V1)), "schema": 3}
    assert preferences.migrate(V1, 3, CHAIN)[0] == by_hand


def test_import_reports_the_migration(tmp_path):
    rep = preferences.import_package(tmp_path, V1, migrations=CHAIN, current=3)
    assert rep["outcome"] == "applied" and rep["schema"] == {"found": 1, "current": 3, "migrated": [1, 2]}
    assert presets.load_user(tmp_path).presets[0]["description"] == "d"
    assert [r["name"] for r in alerts.load(tmp_path).rules] == ["Errors"]


@pytest.mark.parametrize(
    "table",
    [
        {1: v1_to_v2},  # step 2 missing
        {1: v1_to_v2, 2: lambda d: 1 / 0},
        {1: v1_to_v2, 2: lambda d: "not a dict"},
        {1: v1_to_v2, 2: lambda d: {"x": float("nan")}},
    ],
)
def test_broken_chain_is_a_structural_error(tmp_path, table):
    rep = preferences.import_package(tmp_path, V1, migrations=table, current=3)
    assert rep["outcome"] == "rejected" and rep["errors"][0]["code"] == "migration_failed"
    assert snapshot(tmp_path) == {}


def test_migration_cannot_change_the_schema_number_it_returns():
    out, _ = preferences.migrate(V1, 3, {1: lambda d: {**d, "schema": 99}, 2: lambda d: d})
    assert out["schema"] == 3


def test_shipped_chain_is_complete():
    assert set(preferences.MIGRATIONS) == set(range(1, preferences.SCHEMA))


# -- atomic writes ----------------------------------------------------------------------------------


def test_failure_in_the_second_file_restores_the_first(tmp_path, monkeypatch):
    presets.create(tmp_path, PRESET_A)
    before = snapshot(tmp_path)
    real = paths.atomic_write_bytes
    calls = []

    def flaky(path, data):
        calls.append(path.name)
        if path.name == "alerts.toml":
            raise OSError("disk full")
        real(path, data)

    monkeypatch.setattr(preferences, "atomic_write_bytes", flaky)
    rep = preferences.import_package(
        tmp_path, package(presets=[PRESET_B], alert_rules=[RULE_A], redaction=PROFILE)
    )
    assert (
        rep["outcome"] == "failed" and rep["applied"] is False and rep["errors"][0]["code"] == "save_failed"
    )
    assert snapshot(tmp_path) == before  # presets.toml restored, alerts and redaction never created
    assert calls[:2] == ["presets.toml", "alerts.toml"]
    assert not list(tmp_path.glob("*.tmp"))


def test_failure_of_the_restore_is_reported(tmp_path, monkeypatch):
    real = paths.atomic_write_bytes
    state = {"n": 0}

    def flaky(path, data):
        state["n"] += 1
        if state["n"] >= 2:
            raise OSError("boom")
        real(path, data)

    monkeypatch.setattr(preferences, "atomic_write_bytes", flaky)
    presets.create(tmp_path, PRESET_A)
    rep = preferences.import_package(tmp_path, package(presets=[PRESET_B], alert_rules=[RULE_A]))
    assert rep["errors"][0]["code"] == "rollback_failed" and "presets.toml" in rep["errors"][0]["message"]


def test_atomic_write_keeps_the_old_file_when_replace_fails(tmp_path, monkeypatch):
    target = tmp_path / "f.txt"
    target.write_bytes(b"old")
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("nope")))
    with pytest.raises(OSError):
        paths.atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"old" and [p.name for p in tmp_path.iterdir()] == ["f.txt"]


def test_atomic_write_keeps_the_old_file_when_fsync_fails(tmp_path, monkeypatch):
    target = tmp_path / "f.txt"
    target.write_bytes(b"old")
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("io")))
    with pytest.raises(OSError):
        paths.atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"old" and [p.name for p in tmp_path.iterdir()] == ["f.txt"]


def test_atomic_write_retries_a_busy_target(tmp_path, monkeypatch):
    target = tmp_path / "f.txt"
    real = os.replace
    state = {"n": 0}

    def busy(src, dst):
        state["n"] += 1
        if state["n"] < 3:
            raise PermissionError("in use")
        real(src, dst)

    monkeypatch.setattr(os, "replace", busy)
    monkeypatch.setattr(paths, "REPLACE_RETRY_DELAY_S", 0)
    paths.atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"new" and state["n"] == 3
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError("locked")))
    with pytest.raises(PermissionError):
        paths.atomic_write_bytes(target, b"newer")
    assert target.read_bytes() == b"new" and [p.name for p in tmp_path.iterdir()] == ["f.txt"]


def test_atomic_write_creates_missing_folders_and_is_private(tmp_path):
    target = tmp_path / "a" / "b" / "f.txt"
    paths.atomic_write_bytes(target, b"x")
    assert target.read_bytes() == b"x"
    if os.name == "posix":
        assert (target.stat().st_mode & 0o777) == 0o600


def test_concurrent_imports_leave_a_valid_state(tmp_path):
    packages = [package(presets=[{"name": f"p{n}", "filter": ERRORS}], redaction={**PROFILE, "name": f"w{n}"})
                for n in range(6)]  # fmt: skip
    threads = [threading.Thread(target=preferences.import_package, args=(tmp_path, p)) for p in packages]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(p["name"] for p in presets.load_user(tmp_path).presets) == [f"p{n}" for n in range(6)]
    assert redact.load_profile(tmp_path).name.startswith("w")
    assert not list(tmp_path.glob("*.tmp"))


def test_import_text_with_bom_and_unicode(tmp_path):
    item = {"name": "Caf\u00e9 \u00f1 \u65e5\u672c", "description": "a\u00e7a\u00ed", "filter": ERRORS}
    text = json.dumps(package(presets=[item]), ensure_ascii=True)
    rep = preferences.import_package(tmp_path, ("\ufeff" + text).encode("utf-8"))  # a BOM is refused
    assert rep["outcome"] == "rejected" and rep["errors"][0]["code"] == "bad_json"
    rep = preferences.import_package(tmp_path, text.encode("utf-8"))
    assert rep["outcome"] == "applied"
    assert presets.load_user(tmp_path).presets[0]["name"] == "Caf\u00e9 \u00f1 \u65e5\u672c"
    assert "Caf\u00e9 \u00f1 \u65e5\u672c" in preferences.export_package(tmp_path)["text"]


def test_report_never_echoes_untrusted_values_beyond_a_clean_label(tmp_path):
    pkg = package(presets=[{"name": "a\nb" + "x" * 500, "filter": {"zzz": 1}}])
    rep = preferences.import_package(tmp_path, pkg)
    row = rep["sections"]["presets"]["items"][0]
    assert len(row["name"]) <= preferences.MAX_LABEL_CHARS and "\n" not in row["name"]


# -- review round 1: replace/limits, strict decoding, static messages, extras, reconciliation -----------


def many(n, prefix="n"):
    return [{"name": f"{prefix}{i}", "filter": ERRORS} for i in range(n)]


def test_replace_with_skip_item_never_deletes_when_an_item_is_refused_for_the_limit(tmp_path):
    presets.create(tmp_path, {"name": "keep", "filter": ERRORS})
    before = snapshot(tmp_path)
    pkg = package(presets=many(presets.MAX_PRESETS + 1))
    for policy in ("skip_item", "reject_section"):
        rep = preferences.import_package(tmp_path, pkg, mode="replace", on_item_error=policy)
        assert rep["sections"]["presets"]["status"] == "rejected"
        assert rep["sections"]["presets"]["error"]["code"] == "too_many_items_total"
        assert rep["summary"]["added"] == 0 and snapshot(tmp_path) == before


def test_replace_result_is_checked_when_a_stored_name_comes_last(tmp_path):
    presets.create(tmp_path, {"name": "zz", "filter": ERRORS})
    before = snapshot(tmp_path)
    items = many(presets.MAX_PRESETS) + [{"name": "ZZ", "filter": TREE}]  # 201 in the end
    for policy in ("skip_item", "reject_section"):
        rep = preferences.import_package(
            tmp_path, package(presets=items), mode="replace", on_item_error=policy
        )
        assert rep["sections"]["presets"]["status"] == "rejected" and snapshot(tmp_path) == before
    assert len(presets.load_user(tmp_path).presets) == 1


def test_replace_at_exactly_the_limit_is_accepted(tmp_path):
    presets.create(tmp_path, {"name": "zz", "filter": ERRORS})
    items = many(presets.MAX_PRESETS - 1) + [{"name": "ZZ", "filter": TREE}]
    rep = preferences.import_package(tmp_path, package(presets=items), mode="replace")
    assert rep["outcome"] == "applied" and len(presets.load_user(tmp_path).presets) == presets.MAX_PRESETS


def test_merge_never_exceeds_the_limit(tmp_path):
    for item in many(presets.MAX_PRESETS - 1, "e"):
        presets.create(tmp_path, item)
    rep = preferences.import_package(tmp_path, package(presets=many(3)), on_item_error="skip_item")
    assert [i["outcome"] for i in rep["sections"]["presets"]["items"]] == ["added", "rejected", "rejected"]
    assert len(presets.load_user(tmp_path).presets) == presets.MAX_PRESETS


def raw_body(text: str) -> bytes:
    return text.encode("utf-8")


@pytest.mark.parametrize(
    "body",
    [
        b'{"package": {"schema": 1, "schema": 1, "kind": "dmslog-preferences"}}',
        b'{"package": {"schema": NaN, "schema": 1, "kind": "dmslog-preferences"}}',
        b'{"package": {"schema": Infinity, "kind": "dmslog-preferences"}}',
        '{"package": {"schema": 1, "kind": "dmslog-preferences"}}'.encode("utf-16"),
        '{"package": {"schema": 1, "kind": "dmslog-preferences"}}'.encode("utf-16-le"),
        b"\xef\xbb\xbf" + b'{"package": {"schema": 1, "kind": "dmslog-preferences"}}',
    ],
)
def test_strict_body_decoder_refuses(body):
    with pytest.raises(preferences.PreferencesError):
        preferences.parse_json_bytes(body)


def test_strict_decoder_accepts_plain_utf8():
    out = preferences.parse_json_bytes('{"a": ["\u00e9", 1.5, true, null]}'.encode())
    assert out == {"a": ["\u00e9", 1.5, True, None]}


def test_limits_apply_before_the_tree_is_built(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("json.loads must not be reached")

    monkeypatch.setattr(preferences.json, "loads", boom)
    hostile = {
        "deep": b"[" * 100_000,
        "wide": b"[" + b"0," * (preferences.MAX_NODES + 10) + b"0]",
        "long string": b'["' + b"x" * (preferences.MAX_STRING_CHARS + 10) + b'"]',
        "deep objects": b'{"a":' * 5000 + b"1" + b"}" * 5000,
    }
    for name, raw in hostile.items():
        with pytest.raises(preferences.PreferencesError) as err:
            preferences.parse_json_bytes(raw)
        assert err.value.code in ("package_too_complex", "bad_value"), name
    rep = preferences.import_package(tmp_path, hostile["wide"])
    assert rep["outcome"] == "rejected" and rep["errors"][0]["code"] == "package_too_complex"


def test_check_limits_counts_children_before_stacking_them():
    with pytest.raises(preferences.PreferencesError) as err:
        preferences.check_limits([0] * (preferences.MAX_NODES + 1))
    assert err.value.code == "package_too_complex"
    with pytest.raises(preferences.PreferencesError):
        preferences.check_limits({str(i): 0 for i in range(preferences.MAX_NODES + 1)})
    with pytest.raises(preferences.PreferencesError):
        preferences.check_limits({"k" * (preferences.MAX_KEY_CHARS + 1): 0})
    preferences.check_limits([0] * 1000)


SECRET = "SECRET_CUSTOMER_TERM"


def test_report_never_echoes_values_of_the_package(tmp_path):
    bad_filters = [
        {SECRET: 1},
        {"sev": {"le": SECRET}},
        {"tpl": [SECRET, {SECRET: 1}]},
        {"and": [{SECRET: 2}]},
    ]
    pkg = package(
        presets=[{"name": f"p{i}", "filter": f} for i, f in enumerate(bad_filters)]
        + [{"name": "q", "filter": ERRORS, "description": SECRET * 100}, {"name": "r", SECRET: 1}],
        alert_rules=[
            {"name": "a", "kind": "match", "filter": {SECRET: 1}},
            {"name": "b", "kind": SECRET},
            {"name": "c", "kind": "match", "filter": ERRORS, "severity": SECRET},
            {"name": "d", "kind": "open_operation", "timeout_param": SECRET + " x"},
            {"name": "e", "kind": "open_operation", "timeout_s": 5, "operation": SECRET * 100},
        ],
        redaction={"name": "x", "mode": SECRET},
    )
    for mode in ("merge", "replace"):
        for policy in ("reject_section", "skip_item"):
            rep = preferences.import_package(tmp_path, pkg, mode=mode, on_item_error=policy, dry_run=True)
            assert SECRET not in json.dumps(rep), (mode, policy)
    rep = preferences.import_package(tmp_path, package(redaction={SECRET: 1}))
    assert SECRET not in json.dumps(rep)
    rep = preferences.import_package(tmp_path, package(redaction={"name": "x", "categories": {SECRET: True}}))
    assert SECRET not in json.dumps(rep)
    rep = preferences.import_package(tmp_path, {"schema": SECRET, "kind": "x"})
    assert SECRET not in json.dumps(rep)
    rep = preferences.import_package(tmp_path, package(**{SECRET: []}))
    assert SECRET not in json.dumps(rep)


def write_presets_with_extras(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "presets.toml").write_text(
        'schema = 1\n\n[[preset]]\nname = "a"\nfilter = {sev = {le = 2}}\nnote = "keep me"\n',
        encoding="utf-8",
    )


def test_replace_drops_extras_so_two_folders_end_equal(tmp_path):
    with_extra, without = tmp_path / "x", tmp_path / "y"
    write_presets_with_extras(with_extra)
    presets.create(without, {"name": "a", "filter": ERRORS})
    pkg = package(presets=[{"name": "a", "filter": ERRORS}])
    r1 = preferences.import_package(with_extra, pkg, mode="replace")
    r2 = preferences.import_package(without, pkg, mode="replace")
    assert r1["sections"]["presets"]["items"][0]["outcome"] == "updated"  # the extra is dropped
    assert r2["sections"]["presets"]["items"][0]["outcome"] == "unchanged"
    assert (with_extra / "presets.toml").read_bytes() == (without / "presets.toml").read_bytes()
    assert preferences.import_package(with_extra, pkg, mode="replace")["outcome"] == "unchanged"


def test_merge_keeps_extras(tmp_path):
    write_presets_with_extras(tmp_path)
    preferences.import_package(tmp_path, package(presets=[{"name": "A", "filter": TREE}]))
    (item,) = presets.load_user(tmp_path).presets
    assert item["extra"] == {"note": "keep me"} and item["filter"] == TREE


def test_failed_write_reconciles_the_report(tmp_path, monkeypatch):
    real = paths.atomic_write_bytes

    def flaky(path, data):
        if path.name == "redaction.toml":
            raise OSError("disk full")
        real(path, data)

    monkeypatch.setattr(preferences, "atomic_write_bytes", flaky)
    pkg = package(presets=[PRESET_A, PRESET_B], alert_rules=[RULE_A], redaction=PROFILE)
    rep = preferences.import_package(tmp_path, pkg)
    assert rep["outcome"] == "failed" and snapshot(tmp_path) == {}
    assert rep["summary"] == {"added": 0, "updated": 0, "unchanged": 0, "rejected": 0, "removed": 0}
    for name in ("presets", "alert_rules", "redaction"):
        sec = rep["sections"][name]
        assert sec["status"] == "rejected" and sec["error"]["code"] == "save_failed"
        assert all(i["outcome"] == "not_applied" and i["code"] == "rolled_back" for i in sec["items"])


def test_failed_restore_marks_the_state_unknown(tmp_path, monkeypatch):
    real = paths.atomic_write_bytes
    state = {"n": 0}

    def flaky(path, data):
        state["n"] += 1
        if state["n"] == 1:
            real(path, data)
        else:
            raise OSError("boom")

    presets.create(tmp_path, PRESET_A)
    monkeypatch.setattr(preferences, "atomic_write_bytes", flaky)
    rep = preferences.import_package(tmp_path, package(presets=[PRESET_B], alert_rules=[RULE_A]))
    sec = rep["sections"]["presets"]
    assert rep["errors"][0]["code"] == "rollback_failed" and sec["state"] == "unknown"
    assert [i["outcome"] for i in sec["items"]] == ["unknown"] and sec["items"][0]["code"] == "state_unknown"
    other = rep["sections"]["alert_rules"]
    assert "state" not in other and other["items"][0]["outcome"] == "not_applied"
    assert rep["summary"]["added"] == 0
