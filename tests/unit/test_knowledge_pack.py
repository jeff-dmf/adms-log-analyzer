import dataclasses
import importlib.util
import shutil
from pathlib import Path

import pytest

from dmslog.knowledge import pack as pack_module
from dmslog.knowledge.pack import PackError, load_pack, load_pack_from

ROOT = Path(__file__).resolve().parents[2]
MINI_VAULT = ROOT / "tests" / "fixtures" / "mini_vault"

_spec = importlib.util.spec_from_file_location("kb_build", ROOT / "tools" / "kb_build.py")
kb_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kb_build)


@pytest.fixture
def mini_pack_dir(tmp_path):
    out = tmp_path / "pack"
    assert kb_build.main(["--vault", str(MINI_VAULT), "--out", str(out), "--vault-commit", "none"]) == 0
    return out


def test_load_pack_from_directory(mini_pack_dir):
    pack = load_pack_from(mini_pack_dir)
    assert [a.acronym for a in pack.adapters] == ["AAAA", "ABBB"]
    assert pack.process_map == {"ExampleAdapter": "AAAA"}
    assert pack.adapter("AAAA").operations == ("CreatedExampleItems", "SendExampleItems")
    assert pack.adapter("ZZZZ") is None
    assert {e.code for e in pack.errors} == {"1.2", "1.8"}
    assert pack.manifest.schema_version == 1
    assert {p.name for p in pack.params} >= {"LogLevel", "SendTimeout"}


def test_pack_is_read_only(mini_pack_dir):
    pack = load_pack_from(mini_pack_dir)
    with pytest.raises(dataclasses.FrozenInstanceError):
        pack.adapters[0].title = "x"
    with pytest.raises(AttributeError):
        pack.adapters = ()


def test_invalid_pack_raises_clear_error(mini_pack_dir):
    (mini_pack_dir / "operations.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(PackError, match="operations.json"):
        load_pack_from(mini_pack_dir)


def test_missing_directory_raises(tmp_path):
    with pytest.raises(PackError):
        load_pack_from(tmp_path / "nope")


def test_packaged_pack_loads_and_is_cached():
    pack_module.clear_cache()
    first = load_pack()
    assert first.adapters
    assert load_pack() is first


def test_packaged_pack_matches_source_directory():
    src = ROOT / "src" / "dmslog" / "knowledge" / "pack"
    direct = load_pack_from(src)
    pack_module.clear_cache()
    assert load_pack() == direct


def test_load_pack_reports_missing_resource(monkeypatch, tmp_path):
    pack_module.clear_cache()
    empty = tmp_path / "pack"
    empty.mkdir()
    shutil.copy(ROOT / "src" / "dmslog" / "knowledge" / "pack" / "manifest.json", empty)

    class FakeFiles:
        def __truediv__(self, _name):
            return empty

    monkeypatch.setattr(pack_module.resources, "files", lambda _pkg: FakeFiles())
    with pytest.raises(PackError):
        load_pack()
    pack_module.clear_cache()
