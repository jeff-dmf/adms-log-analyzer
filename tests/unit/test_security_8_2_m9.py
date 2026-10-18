"""Regression tests for the 8.2 review of the M9 delta (docs/notas/revisao-seguranca-8-2-m9.md)."""

import os
import threading

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


class _FakeConn:
    """Stands for the request's read connection (no SQLite handle: the DELETE must succeed on Windows too).

    ``pause`` names the read to stop at: ``paths`` (first read of the case) or ``meta`` (last read)."""

    def __init__(self, pause=None):
        self.pause = pause
        self.reached, self.go = threading.Event(), threading.Event()

    def execute(self, sql, *args):
        if (self.pause == "paths" and "FROM collection" in sql) or (
            self.pause == "meta" and "FROM meta" in sql
        ):
            self.reached.set()
            assert self.go.wait(10)
        rows = (
            [("/synthetic/ERS_CL_00000000-0000-4000-8000-000000000001.zip",)] if "collection" in sql else []
        )
        return _Rows(rows)


class _Rows(list):
    def fetchone(self):
        return self[0] if self else None


def _load(api, cid, conn, results):
    results.append(api._knowledge(conn, cid, with_ers=True))


@pytest.mark.parametrize("pause", ["paths", "meta", "build"])
def test_a_load_racing_the_delete_does_not_republish(client, server, demo, monkeypatch, pause):  # noqa: F811
    """The load starts, the DELETE lands at ``pause`` (before the reads, after them, or while the knowledge is
    built), then the load finishes: the deleted case is not in the cache."""
    from dmslog.server import api as api_module

    api = server.api
    conn = _FakeConn(None if pause == "build" else pause)
    if pause == "build":
        real = api_module.Knowledge

        def slow_knowledge(*args, **kwargs):
            conn.reached.set()
            assert conn.go.wait(10)
            return real(*args, **kwargs)

        monkeypatch.setattr(api_module, "Knowledge", slow_knowledge)
    results = []
    worker = threading.Thread(target=_load, args=(api, demo, conn, results))
    worker.start()
    assert conn.reached.wait(10)
    assert client.request("DELETE", f"/api/cases/{demo}")[0] == 204
    conn.go.set()
    worker.join(10)
    assert results and not worker.is_alive()
    with api._knowledge_lock:
        assert demo not in api._case_knowledge


def test_a_load_that_starts_after_the_delete_does_not_cache_the_case(client, server, demo):  # noqa: F811
    """The request's connection was opened before the DELETE (POSIX keeps reading the removed file)."""
    api = server.api
    assert client.request("DELETE", f"/api/cases/{demo}")[0] == 204
    api._knowledge(_FakeConn(), demo, with_ers=True)
    with api._knowledge_lock:
        assert demo not in api._case_knowledge


def test_a_load_of_a_live_case_is_cached(server, demo):  # noqa: F811
    api = server.api
    api._knowledge(_FakeConn(), demo, with_ers=True)
    with api._knowledge_lock:
        assert demo in api._case_knowledge


def test_deleting_a_case_drops_the_events_its_hub_retains(client, server, demo):  # noqa: F811
    hub = server.api.jobs.hub(demo)
    assert hub.try_begin("job1")
    hub.publish("progress", {"source": "AdapterOne_1111.log"})
    hub.publish("watch", {"following": True})
    hub.finish({"status": "ok", "message": "synthetic"})
    assert hub.wait(0, 0)[0]
    assert client.request("DELETE", f"/api/cases/{demo}")[0] == 204
    assert hub.wait(0, 0)[0] == []  # no source names, alert copies or watch change left behind


def test_a_running_hub_keeps_its_events():
    from dmslog.server.sse import ProgressHub

    hub = ProgressHub()
    assert hub.try_begin("job1")
    hub.publish("progress", {"n": 1})
    assert hub.forget() is False
    assert len(hub.wait(0, 0)[0]) == 1
