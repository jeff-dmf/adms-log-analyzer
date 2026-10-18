"""Regression tests for the 8.2 review of the M9 delta (docs/notas/revisao-seguranca-8-2-m9.md)."""

import os

import pytest
from test_api import client, server  # noqa: F401  (fixtures)
from test_api_export import case_module, demo  # noqa: F401  (fixtures)

from dmslog import paths
from dmslog.report import redact
from dmslog.store import alerts, presets

TREE = {"level": ["error"]}


def _preset(name="Mine"):
    return {"name": name, "description": "d", "family": "AWFM", "filter": TREE}


def _rule(name="Errors"):
    return {"name": name, "kind": "match", "filter": TREE, "severity": "warning"}


def _busy_once(monkeypatch):
    """``os.replace`` refuses the first call with PermissionError (an antivirus holding the target)."""
    real = os.replace
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) == 1:
            raise PermissionError("held by another process (synthetic)")
        return real(src, dst)

    monkeypatch.setattr(paths.os, "replace", flaky)
    monkeypatch.setattr(paths, "REPLACE_RETRY_DELAY_S", 0)
    return calls


# ---------------------------------------------------------------- N1: one atomic write for every user file


@pytest.mark.parametrize(
    "save",
    [
        lambda d: presets.create(d, _preset()),
        lambda d: alerts.create(d, _rule()),
        lambda d: redact.save_profile(d, redact.default_profile()),
    ],
    ids=["presets", "alerts", "redaction"],
)
def test_user_files_retry_a_briefly_locked_target(tmp_path, monkeypatch, save):
    calls = _busy_once(monkeypatch)
    save(tmp_path)
    assert len(calls) == 2  # retried instead of failing with save_failed
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(
    "save",
    [
        lambda d: presets.create(d, _preset()),
        lambda d: alerts.create(d, _rule()),
        lambda d: redact.save_profile(d, redact.default_profile()),
    ],
    ids=["presets", "alerts", "redaction"],
)
def test_user_files_are_synced_before_the_replace(tmp_path, monkeypatch, save):
    monkeypatch.setattr(paths, "SYNC_WRITES", True)
    synced = []
    real = os.fsync
    monkeypatch.setattr(paths.os, "fsync", lambda fd: (synced.append(fd), real(fd)))
    save(tmp_path)
    assert synced  # an empty file after a power cut would silently drop the user's rules


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_user_files_stay_private(tmp_path):
    presets.create(tmp_path, _preset())
    alerts.create(tmp_path, _rule())
    redact.save_profile(tmp_path, redact.default_profile())
    for name in ("presets.toml", "alerts.toml", "redaction.toml"):
        assert (tmp_path / name).stat().st_mode & 0o777 == 0o600


def test_value_map_write_never_recreates_a_deleted_case_folder(tmp_path):
    folder = tmp_path / "cases" / "gone" / "redaction-maps"
    with pytest.raises(FileNotFoundError):
        paths.atomic_write_bytes(folder / "x.map.json", b"{}", create_parents=False)
    assert not (tmp_path / "cases").exists()


# ---------------------------------------------------------------- N2: no state of a deleted case in memory


def test_deleting_a_case_forgets_its_installation_knowledge(client, server, demo):  # noqa: F811
    api = server.api
    with api._knowledge_lock:
        api._case_knowledge[demo] = (("key",), object())
    assert client.request("DELETE", f"/api/cases/{demo}")[0] == 204
    with api._knowledge_lock:
        assert demo not in api._case_knowledge
