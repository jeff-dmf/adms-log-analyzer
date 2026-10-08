import hashlib
import importlib.util
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from dmslog import __version__

PACKAGING = Path(__file__).resolve().parents[2] / "packaging"


def load_packaging_module(name):
    spec = importlib.util.spec_from_file_location(name, PACKAGING / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def build_pyz(tmp_path, monkeypatch):
    sys.path.insert(0, str(PACKAGING))
    monkeypatch.setenv("DMSLOG_COMMIT", "testcommit")
    module = load_packaging_module("build_pyz")
    monkeypatch.setattr(module, "DIST", tmp_path)
    monkeypatch.setattr(module, "TARGET", tmp_path / "dmslog.pyz")
    yield module
    sys.path.remove(str(PACKAGING))


def test_pyz_runs_isolated_and_reports_version(build_pyz):
    pyz = build_pyz.build()
    result = subprocess.run(
        [sys.executable, "-I", str(pyz), "--version"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    assert result.stdout.strip() == __version__


def test_pyz_contains_web_assets_and_build_info_without_bytecode(build_pyz):
    pyz = build_pyz.build()
    with zipfile.ZipFile(pyz) as archive:
        names = set(archive.namelist())
        assert {"__main__.py", "dmslog/web/index.html", "dmslog/web/js/main.js", "dmslog/families.toml",
                "dmslog/_build_info.py"} <= names
        assert not [n for n in names if n.endswith(".pyc") or "__pycache__" in n]
        assert b"testcommit" in archive.read("dmslog/_build_info.py")


def test_pyz_build_is_deterministic(build_pyz):
    first = build_pyz.build().read_bytes()
    second = build_pyz.build().read_bytes()
    assert first == second


def test_pyz_serves_health_from_inside_the_archive(build_pyz):
    pyz = build_pyz.build()
    code = (
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import threading, http.client, dmslog\n"
        "assert dmslog.__file__.startswith(sys.argv[1]), dmslog.__file__\n"
        "from dmslog.server.app import DmslogServer\n"
        "s = DmslogServer(0)\n"
        "threading.Thread(target=s.serve_forever, daemon=True).start()\n"
        "c = http.client.HTTPConnection('127.0.0.1', s.port)\n"
        "c.request('GET', '/?token=' + s.token)\n"
        "cookie = c.getresponse().getheader('Set-Cookie').split(';')[0]\n"
        "c.close()\n"
        "c = http.client.HTTPConnection('127.0.0.1', s.port)\n"
        "c.request('GET', '/', headers={'Cookie': cookie})\n"
        "r = c.getresponse(); body = r.read()\n"
        "print(r.status, b'dmslog' in body)\n"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(pyz)], capture_output=True, text=True, encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "200 True"


def test_linux_tarball_layout(build_pyz, tmp_path, monkeypatch):
    module = load_packaging_module("build_linux")
    monkeypatch.setattr(module, "TARGET", tmp_path / "dmslog-linux.tar.gz")
    target = module.build()
    with tarfile.open(target) as tar:
        members = {m.name: m for m in tar.getmembers()}
        assert set(members) == {"dmslog-linux/dmslog", "dmslog-linux/dmslog.pyz"}
        assert members["dmslog-linux/dmslog"].mode == 0o755
        launcher = tar.extractfile("dmslog-linux/dmslog").read()
        assert launcher.startswith(b"#!/bin/sh\n") and b"\r" not in launcher


@pytest.mark.skipif(sys.platform == "win32", reason="TZ switching needs POSIX tzset")
def test_pyz_build_is_identical_across_time_zones(tmp_path):
    driver = (
        "import sys, pathlib; sys.path.insert(0, sys.argv[1]); import build_pyz as b; "
        "out = pathlib.Path(sys.argv[2]); b.DIST = out.parent; b.TARGET = out; b.build()"
    )
    digests = []
    for index, zone in enumerate(("UTC", "America/Sao_Paulo", "Asia/Tokyo")):
        target = tmp_path / f"tz{index}" / "dmslog.pyz"
        target.parent.mkdir()
        env = {**os.environ, "TZ": zone, "DMSLOG_COMMIT": "tz-test"}
        subprocess.run(
            [sys.executable, "-c", driver, str(PACKAGING), str(target)],
            check=True, capture_output=True, env=env,
        )
        digests.append(hashlib.sha256(target.read_bytes()).hexdigest())
    assert len(set(digests)) == 1, digests
