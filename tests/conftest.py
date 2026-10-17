"""Shared test setup: every test runs without a knowledge pack unless it installs one (ARQUITETURA D10).

The real pack is derived from product documentation and never lives in the repository, so no test may
see it, not even when the developer has one installed in the default data directory or in
``DMSLOG_PACK_DIR``. Tests that need a pack use the synthetic ``tests/fixtures/knowledge/mini_pack``
through the ``use_pack`` fixture.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from dmslog import paths
from dmslog.knowledge import pack as pack_module

MINI_PACK = Path(__file__).resolve().parent / "fixtures" / "knowledge" / "mini_pack"


@pytest.fixture(autouse=True)
def _no_knowledge_pack(monkeypatch, tmp_path_factory):
    monkeypatch.delenv(pack_module.PACK_ENV, raising=False)
    empty_data_dir = tmp_path_factory.getbasetemp() / "no-pack-data-dir"
    monkeypatch.setattr(pack_module, "_settings", pack_module._Settings(None, empty_data_dir, True))
    monkeypatch.setattr(pack_module, "app_dir", lambda: None)
    pack_module.clear_cache()
    yield
    pack_module.clear_cache()


@pytest.fixture
def mini_pack_dir() -> Path:
    """The committed synthetic pack (read-only: copy it before changing anything)."""
    return MINI_PACK


@pytest.fixture
def use_pack():
    """Install a pack directory for this test (as ``--pack-dir`` would); restored afterwards."""

    def install(directory):
        pack_module.configure(pack_dir=directory)
        return pack_module.pack_status()

    return install


@pytest.fixture
def extended_pack(tmp_path):
    """Copy of the mini pack plus synthetic rows: ``extended_pack(adapters=[...], parameters=[...])``."""

    def build(**extra):
        out = tmp_path / "extended_pack"
        if out.exists():
            shutil.rmtree(out)
        shutil.copytree(MINI_PACK, out)
        files = {
            "adapters": "adapters.json",
            "error_codes": "error_codes.json",
            "operations": "operations.json",
            "parameters": "parameters.json",
        }
        for key, rows in extra.items():
            if key == "process_map":
                continue
            path = out / files[key]
            data = json.loads(path.read_text(encoding="utf-8"))
            data[key].extend(rows)
            path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if "process_map" in extra:
            path = out / "adapters.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["process_map"].update(extra["process_map"])
            path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return out

    return build


@pytest.fixture(scope="session", autouse=True)
def _no_fsync_in_tests():
    """Durability is not under test and ``fsync`` is slow on Windows runners (thousands of writes in the
    property tests): atomic writes skip it, except in the tests that set ``paths.SYNC_WRITES`` back."""
    previous = paths.SYNC_WRITES
    paths.SYNC_WRITES = False
    yield
    paths.SYNC_WRITES = previous
