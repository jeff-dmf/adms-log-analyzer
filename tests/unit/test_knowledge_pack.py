import dataclasses
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from dmslog.knowledge import pack as pack_module
from dmslog.knowledge.pack import (
    EMPTY_PACK,
    SOURCE_DATA_DIR,
    SOURCE_ENV,
    SOURCE_OPTION,
    SOURCE_PORTABLE,
    STATE_INSTALLED,
    STATE_INVALID,
    STATE_NOT_INSTALLED,
    PackError,
    PackNotInstalled,
    RtServicesError,
    current_pack,
    load_pack,
    load_pack_from,
    load_rt_services,
    locate_pack,
    pack_candidates,
    pack_status,
)
from dmslog.knowledge.pack_schema import validate_pack, validate_rt_services
from dmslog.knowledge.resolve import Knowledge

ROOT = Path(__file__).resolve().parents[2]
MINI_VAULT = ROOT / "tests" / "fixtures" / "mini_vault"

_spec = importlib.util.spec_from_file_location("kb_build", ROOT / "tools" / "kb_build.py")
kb_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kb_build)


@pytest.fixture
def built_pack_dir(tmp_path):
    out = tmp_path / "pack"
    assert (
        kb_build.main(["--vault", str(MINI_VAULT), "--out", str(out), "--vault-commit", "none", "--no-rt"])
        == 0
    )
    return out


def test_load_pack_from_directory(built_pack_dir):
    pack = load_pack_from(built_pack_dir)
    assert [a.acronym for a in pack.adapters] == ["AAAA", "ABBB"]
    assert pack.process_map == {"ExampleAdapter": "AAAA"}
    assert pack.adapter("AAAA").operations == ("CreatedExampleItems", "SendExampleItems")
    assert pack.adapter("ZZZZ") is None
    assert {e.code for e in pack.errors} == {"1.2", "1.8"}
    assert pack.manifest.schema_version == 1
    assert {p.name for p in pack.params} >= {"LogLevel", "SendTimeout"}


def test_pack_is_read_only(built_pack_dir):
    pack = load_pack_from(built_pack_dir)
    with pytest.raises(dataclasses.FrozenInstanceError):
        pack.adapters[0].title = "x"
    with pytest.raises(AttributeError):
        pack.adapters = ()


def test_invalid_pack_raises_clear_error(built_pack_dir):
    (built_pack_dir / "operations.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(PackError, match="operations.json"):
        load_pack_from(built_pack_dir)


def test_missing_directory_raises(tmp_path):
    with pytest.raises(PackError):
        load_pack_from(tmp_path / "nope")


# -- the synthetic fixture -----------------------------------------------------------------------


def test_mini_pack_fixture_is_valid_and_obviously_synthetic(mini_pack_dir):
    assert validate_pack(mini_pack_dir) == []
    pack = load_pack_from(mini_pack_dir)
    assert pack.manifest.notice.startswith("SYNTHETIC TEST FIXTURE")
    assert pack.manifest.vault_commit == "synthetic-test-fixture"
    assert {a.acronym for a in pack.adapters} == {"AAAA", "ABBB"}
    assert all(a.title.startswith("Example ") for a in pack.adapters)


# -- run-time resolution (ARQUITETURA D10) ----------------------------------------------------------


def copy_pack(source, target):
    shutil.copytree(source, target)
    return target


def test_without_pack_the_state_is_not_installed():
    status = pack_status()
    assert status.state == STATE_NOT_INSTALLED and not status.installed
    assert status.source is None and status.path is None and status.error is None
    assert [source for source, _ in status.searched] == [SOURCE_DATA_DIR]
    assert current_pack() is None
    with pytest.raises(PackNotInstalled):
        load_pack()
    assert status.as_dict()["installed"] is False


def test_explicit_pack_dir_is_loaded_once(use_pack, mini_pack_dir):
    status = use_pack(mini_pack_dir)
    assert (status.state, status.source, status.path) == (STATE_INSTALLED, SOURCE_OPTION, mini_pack_dir)
    assert status.manifest.vault_commit == "synthetic-test-fixture"
    first = load_pack()
    assert load_pack() is first is current_pack()
    assert status.as_dict()["vault_commit"] == "synthetic-test-fixture"


def test_explicit_location_has_no_fallback(use_pack, tmp_path, mini_pack_dir):
    data_dir = tmp_path / "data"
    copy_pack(mini_pack_dir, data_dir / "knowledge" / "pack")
    pack_module.configure(pack_dir=tmp_path / "missing", data_dir=data_dir)
    status = pack_status()
    assert (status.state, status.source) == (STATE_INVALID, SOURCE_OPTION)
    assert "not a directory" in status.error
    assert current_pack() is None
    with pytest.raises(PackError) as caught:
        load_pack()
    assert not isinstance(caught.value, PackNotInstalled)


def test_invalid_pack_is_reported_not_raised(tmp_path, mini_pack_dir):
    broken = copy_pack(mini_pack_dir, tmp_path / "broken")
    (broken / "parameters.json").write_text("{not json", encoding="utf-8")
    pack_module.configure(pack_dir=broken)
    status = pack_status()
    assert status.state == STATE_INVALID and "parameters.json" in status.error
    knowledge = Knowledge()
    assert not knowledge.has_pack and knowledge.error("AAAA", "1.2") is None


def test_environment_variable_then_option(monkeypatch, tmp_path, mini_pack_dir):
    monkeypatch.setenv(pack_module.PACK_ENV, str(mini_pack_dir))
    pack_module.configure(data_dir=tmp_path / "data")
    assert (pack_status().state, pack_status().source) == (STATE_INSTALLED, SOURCE_ENV)
    other = copy_pack(mini_pack_dir, tmp_path / "other")
    pack_module.configure(pack_dir=other, data_dir=tmp_path / "data")
    assert (pack_status().source, pack_status().path) == (SOURCE_OPTION, other)


def test_data_dir_pack_is_found(tmp_path, mini_pack_dir):
    data_dir = tmp_path / "data"
    copy_pack(mini_pack_dir, data_dir / "knowledge" / "pack")
    pack_module.configure(data_dir=data_dir)
    status = pack_status()
    assert (status.state, status.source) == (STATE_INSTALLED, SOURCE_DATA_DIR)
    assert status.path == data_dir / "knowledge" / "pack"


def test_candidates_order():
    env = {pack_module.PACK_ENV: "/env/pack"}
    assert pack_candidates("/opt/pack", "/data", env, "/app") == [(SOURCE_OPTION, Path("/opt/pack"))]
    assert pack_candidates(None, "/data", env, "/app") == [(SOURCE_ENV, Path("/env/pack"))]
    assert pack_candidates(None, "/data", {pack_module.PACK_ENV: ""}, "/app") == [
        (SOURCE_DATA_DIR, Path("/data", "knowledge", "pack")),
        (SOURCE_PORTABLE, Path("/app", "knowledge", "pack")),
    ]
    assert pack_candidates(None, None, {}, None) == []


def test_data_dir_wins_over_portable_and_empty_folders_are_skipped(tmp_path, mini_pack_dir):
    data_dir, app = tmp_path / "data", tmp_path / "app"
    copy_pack(mini_pack_dir, app / "knowledge" / "pack")
    (data_dir / "knowledge" / "pack").mkdir(parents=True)  # empty: skipped
    status, pack = locate_pack(pack_candidates(None, data_dir, {}, app))
    assert (status.state, status.source) == (STATE_INSTALLED, SOURCE_PORTABLE) and pack is not None
    copy_pack(mini_pack_dir, tmp_path / "data2" / "knowledge" / "pack")
    status, _ = locate_pack(pack_candidates(None, tmp_path / "data2", {}, app))
    assert status.source == SOURCE_DATA_DIR


def test_an_invalid_implicit_pack_is_not_skipped(tmp_path, mini_pack_dir):
    data_dir, app = tmp_path / "data", tmp_path / "app"
    copy_pack(mini_pack_dir, app / "knowledge" / "pack")
    (data_dir / "knowledge" / "pack").mkdir(parents=True)
    (data_dir / "knowledge" / "pack" / "manifest.json").write_text("{}", encoding="utf-8")
    status, pack = locate_pack(pack_candidates(None, data_dir, {}, app))
    assert (status.state, status.source, pack) == (STATE_INVALID, SOURCE_DATA_DIR, None)


def test_nothing_found(tmp_path):
    status, pack = locate_pack(pack_candidates(None, tmp_path / "data", {}, tmp_path / "app"))
    assert status.state == STATE_NOT_INSTALLED and pack is None
    assert [source for source, _ in status.searched] == [SOURCE_DATA_DIR, SOURCE_PORTABLE]


def test_the_package_ships_no_pack():
    package = ROOT / "src" / "dmslog" / "knowledge"
    assert not list(package.glob("pack/*.json")), "the knowledge pack must not live in the package"


def test_a_file_in_place_of_an_implicit_candidate_is_invalid_without_fallback(tmp_path, mini_pack_dir):
    data_dir, app = tmp_path / "data", tmp_path / "app"
    copy_pack(mini_pack_dir, app / "knowledge" / "pack")
    (data_dir / "knowledge").mkdir(parents=True)
    (data_dir / "knowledge" / "pack").write_text("not a folder", encoding="utf-8")
    status, pack = locate_pack(pack_candidates(None, data_dir, {}, app))
    assert (status.state, status.source, pack) == (STATE_INVALID, SOURCE_DATA_DIR, None)
    assert str(data_dir / "knowledge" / "pack") in status.error and "not a directory" in status.error


def test_an_unlistable_implicit_candidate_is_invalid_without_fallback(tmp_path, mini_pack_dir, monkeypatch):
    data_dir, app = tmp_path / "data", tmp_path / "app"
    copy_pack(mini_pack_dir, app / "knowledge" / "pack")
    locked = data_dir / "knowledge" / "pack"
    locked.mkdir(parents=True)
    real_iterdir = Path.iterdir

    def iterdir(self):
        if self == locked:
            raise PermissionError(13, "Permission denied")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    status, pack = locate_pack(pack_candidates(None, data_dir, {}, app))
    assert (status.state, status.source, pack) == (STATE_INVALID, SOURCE_DATA_DIR, None)
    assert str(locked) in status.error and "cannot be listed" in status.error


def test_an_unreachable_implicit_candidate_is_invalid_without_fallback(tmp_path, mini_pack_dir, monkeypatch):
    data_dir, app = tmp_path / "data", tmp_path / "app"
    copy_pack(mini_pack_dir, app / "knowledge" / "pack")
    locked = data_dir / "knowledge" / "pack"
    real_stat = Path.stat

    def stat(self, *args, **kwargs):
        if self == locked:
            raise PermissionError(13, "Permission denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    status, _ = locate_pack(pack_candidates(None, data_dir, {}, app))
    assert status.state == STATE_INVALID and status.source == SOURCE_DATA_DIR


# -- optional rt_services.json ---------------------------------------------------------------------


def _rt_row(**changes):
    row = {
        "process": "DMS_ExampleScheduler",
        "family": "DMSJOB",
        "title": None,
        "summary": None,
        "note": None,
        "section": None,
        "install_dir": None,
        "monitored": [],
        "counters": [],
        "usage_events": 0,
        "references": [],
    }
    row.update(changes)
    return row


def _write_rt(directory, rows):
    (directory / "rt_services.json").write_text(json.dumps({"rt_services": rows}), encoding="utf-8")


def test_mini_pack_rt_services_load_and_are_immutable(mini_pack_dir):
    pack = load_pack_from(mini_pack_dir)
    assert pack.rt_services_error is None
    assert [s.process for s in pack.rt_services] == ["DMS_ExampleScheduler", "DMS_ExampleWorker"]
    service = pack.rt_service("DMS_ExampleScheduler")
    assert service.family == "DMSJOB" and service.monitored == ("cpu", "ram")
    assert service.references[0].doc == "EXMP" and service.references[0].mentions == 2
    assert pack.rt_service("DMS_Unknown") is None
    bare = pack.rt_service("DMS_ExampleWorker")
    assert bare.title is None and bare.references == () and bare.counters == ()
    with pytest.raises(dataclasses.FrozenInstanceError):
        service.title = "x"
    with pytest.raises(dataclasses.FrozenInstanceError):
        service.references[0].mentions = 9


def test_pack_without_rt_services_is_valid(tmp_path, mini_pack_dir):
    old = copy_pack(mini_pack_dir, tmp_path / "old")
    (old / "rt_services.json").unlink()
    assert validate_pack(old) == []
    pack = load_pack_from(old)
    assert pack.rt_services == () and pack.rt_services_error is None and pack.rt_service("x") is None
    assert load_rt_services(old) == ()
    assert EMPTY_PACK.rt_services == ()


def test_invalid_rt_services_only_drops_the_rt_block(tmp_path, mini_pack_dir):
    broken = copy_pack(mini_pack_dir, tmp_path / "broken")
    _write_rt(broken, [_rt_row(family="NOPE")])
    assert any("rt_services.json" in p for p in validate_pack(broken))
    with pytest.raises(RtServicesError, match="rt_services.json"):
        load_rt_services(broken)
    pack = load_pack_from(broken)
    assert pack.rt_services == () and "rt_services.json" in pack.rt_services_error
    assert [a.acronym for a in pack.adapters] == ["AAAA", "ABBB"]
    pack_module.configure(pack_dir=broken)
    status = pack_status()
    assert status.state == STATE_INSTALLED and current_pack().rt_services_error


def test_unreadable_rt_services_json(tmp_path, mini_pack_dir):
    broken = copy_pack(mini_pack_dir, tmp_path / "broken")
    (broken / "rt_services.json").write_text("{not json", encoding="utf-8")
    assert validate_rt_services(broken)[0].startswith("rt_services.json: cannot be read")
    assert load_pack_from(broken).rt_services_error


@pytest.mark.parametrize("data", [[], {}, {"rt_services": {}}, {"rt_services": [], "extra": 1}])
def test_rt_services_top_level_shape(tmp_path, data):
    (tmp_path / "rt_services.json").write_text(json.dumps(data), encoding="utf-8")
    assert validate_rt_services(tmp_path)


_REF = {"doc": "EXMP", "note": "a.md", "section": None, "mentions": 1}


@pytest.mark.parametrize(
    "changes",
    [
        {"process": "bad name"},
        {"process": 3},
        {"family": "DMSXXX"},
        {"title": "a" * 301},
        {"title": "two\nlines"},
        {"summary": "x" * 241},
        {"summary": "line\nbreak"},
        {"note": "../escape.md"},
        {"note": "a/../b.md"},
        {"note": "/abs/note.md"},
        {"note": "dir\\note.md"},
        {"note": "C:/note.md"},
        {"note": "dir/note.txt"},
        {"section": "1.a"},
        {"section": "1..2"},
        {"monitored": ["cpu", "cpu"]},
        {"monitored": ["gpu"]},
        {"monitored": [1]},
        {"counters": [1]},
        {"counters": ["c"] * 65},
        {"counters": ["x" * 301]},
        {"usage_events": -1},
        {"usage_events": True},
        {"usage_events": "3"},
        {"references": [_REF] * 9},
        {"references": [{**_REF, "doc": "ex"}]},
        {"references": [{**_REF, "note": "../a.md"}]},
        {"references": [{**_REF, "section": "x"}]},
        {"references": [{**_REF, "mentions": 0}]},
        {"references": [{k: v for k, v in _REF.items() if k != "mentions"}]},
        {"references": [{**_REF, "text": "t"}]},
        {"references": ["a.md"]},
        {"unexpected": 1},
    ],
)
def test_rt_service_rejects_bad_values(tmp_path, changes):
    _write_rt(tmp_path, [_rt_row(**changes)])
    assert validate_rt_services(tmp_path), changes


def test_rt_service_accepts_limits(tmp_path):
    ref = {"doc": "EXMP", "note": "a/b.md", "section": "1.2", "mentions": 1}
    _write_rt(
        tmp_path,
        [_rt_row(summary="s" * 240, title="t" * 300, counters=["c"] * 64, references=[ref] * 8)],
    )
    assert validate_rt_services(tmp_path) == []


def test_rt_service_missing_field_and_null_rules(tmp_path):
    row = _rt_row()
    del row["monitored"]
    _write_rt(tmp_path, [row])
    assert any("missing field 'monitored'" in p for p in validate_rt_services(tmp_path))
    _write_rt(tmp_path, [_rt_row(process=None)])
    assert any("must not be null" in p for p in validate_rt_services(tmp_path))


def test_rt_services_must_be_unique_sorted_and_bounded(tmp_path):
    _write_rt(tmp_path, [_rt_row(process="B"), _rt_row(process="A")])
    assert any("sorted" in p for p in validate_rt_services(tmp_path))
    _write_rt(tmp_path, [_rt_row(process="A"), _rt_row(process="A")])
    assert any("duplicate" in p for p in validate_rt_services(tmp_path))
    _write_rt(tmp_path, [_rt_row(process=f"P{i:03d}") for i in range(129)])
    assert any("more than" in p for p in validate_rt_services(tmp_path))
    (tmp_path / "rt_services.json").write_text(" " * 1_000_001, encoding="utf-8")
    assert any("larger than" in p for p in validate_rt_services(tmp_path))


def _load_rt_json(mini_pack_dir):
    import json

    data = json.loads((mini_pack_dir / "rt_services.json").read_text(encoding="utf-8"))
    return data, data["rt_services"][0]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("process", "ExampleService\n"),
        ("section", "1.2\n"),
        ("title", "bad \x1b[31m title"),
        ("title", "line separator"),
        ("summary", "tab\there\x7f"),
    ],
)
def test_rt_services_reject_newline_tail_and_control_characters(tmp_path, mini_pack_dir, field, value):
    import json

    broken = copy_pack(mini_pack_dir, tmp_path / "ctl")
    data, row = _load_rt_json(broken)
    row[field] = value
    (broken / "rt_services.json").write_text(json.dumps(data), encoding="utf-8")
    assert validate_pack(broken) != []


def test_rt_services_reference_doc_with_newline_is_rejected(tmp_path, mini_pack_dir):
    import json

    broken = copy_pack(mini_pack_dir, tmp_path / "doc")
    data, row = _load_rt_json(broken)
    if not row["references"]:
        pytest.skip("fixture row without references")
    row["references"][0]["doc"] = "EXMP\n"
    (broken / "rt_services.json").write_text(json.dumps(data), encoding="utf-8")
    assert validate_pack(broken) != []


def test_deeply_nested_rt_services_drops_only_the_rt_block(tmp_path, mini_pack_dir):
    broken = copy_pack(mini_pack_dir, tmp_path / "deep")
    (broken / "rt_services.json").write_text("[" * 200_000 + "]" * 200_000, encoding="utf-8")
    pack = load_pack_from(broken)
    assert pack.rt_services == () and pack.rt_services_error
    pack_module.configure(pack_dir=broken)
    assert pack_status().state == STATE_INSTALLED
