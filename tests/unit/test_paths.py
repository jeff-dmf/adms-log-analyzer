import sqlite3
from pathlib import Path

from dmslog import paths


def test_windows_default_uses_localappdata():
    env = {"LOCALAPPDATA": r"C:\Users\ana\AppData\Local"}
    result = paths.default_data_dir("win32", env, Path("home"))
    assert result == Path(r"C:\Users\ana\AppData\Local") / "dmslog"


def test_windows_falls_back_to_home_appdata():
    result = paths.default_data_dir("win32", {}, Path("home"))
    assert result == Path("home") / "AppData" / "Local" / "dmslog"


def test_linux_default_is_under_local_share():
    result = paths.default_data_dir("linux", {}, Path("/home/ana"))
    assert result == Path("/home/ana/.local/share/dmslog")


def test_linux_honors_xdg_data_home():
    result = paths.default_data_dir("linux", {"XDG_DATA_HOME": "/data/xdg"}, Path("/home/ana"))
    assert result == Path("/data/xdg/dmslog")


def test_linux_ignores_relative_xdg_data_home():
    result = paths.default_data_dir("linux", {"XDG_DATA_HOME": "relative"}, Path("/home/ana"))
    assert result == Path("/home/ana/.local/share/dmslog")


def test_override_wins_over_default(tmp_path):
    assert paths.resolve_data_dir(tmp_path / "x", "win32", {"LOCALAPPDATA": "C:/L"}) == tmp_path / "x"


def test_no_override_uses_default():
    assert paths.resolve_data_dir(None, "linux", {}, Path("/h")) == Path("/h/.local/share/dmslog")


def test_check_sqlite_fts5_reports_support():
    result = paths.check_sqlite_fts5()
    assert result["sqlite"] == sqlite3.sqlite_version
    assert result["fts5"] is True
    assert result["error"] is None


def test_check_sqlite_fts5_reports_missing_support(monkeypatch):
    class NoFts5Connection:
        def execute(self, *_args):
            raise sqlite3.OperationalError("no such module: fts5")

        def close(self):
            pass

    monkeypatch.setattr(paths.sqlite3, "connect", lambda _target: NoFts5Connection())
    result = paths.check_sqlite_fts5()
    assert result["fts5"] is False
    assert "fts5" in result["error"]
