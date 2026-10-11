import hashlib
import importlib.util
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from dmslog import __version__

PACKAGING = Path(__file__).resolve().parents[2] / "packaging"
MINI_PACK = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "knowledge" / "mini_pack"


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


def test_pyz_ships_curated_knowledge_but_never_the_pack(build_pyz, tmp_path, monkeypatch):
    # even with a local pack left in the source tree (ignored by git), the zipapp has none (D10)
    source = tmp_path / "src-copy" / "dmslog"
    shutil.copytree(build_pyz.SRC_PACKAGE, source, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(MINI_PACK, source / "knowledge" / "pack", dirs_exist_ok=True)
    monkeypatch.setattr(build_pyz, "SRC_PACKAGE", source)
    pyz = build_pyz.build()
    with zipfile.ZipFile(pyz) as archive:
        names = set(archive.namelist())
    assert not [n for n in names if "knowledge/pack/" in n]
    curated = Path(__file__).resolve().parents[2] / "src" / "dmslog" / "knowledge" / "curated"
    for toml in curated.glob("*.toml"):
        assert f"dmslog/knowledge/curated/{toml.name}" in names
    check = load_packaging_module("check_no_pack")
    assert check.artifact_problems(pyz) == []


def run_info(pyz, data_dir):
    env = {k: v for k, v in os.environ.items() if k != "DMSLOG_PACK_DIR"}
    result = subprocess.run(
        [sys.executable, "-I", str(pyz), "info", "--data-dir", str(data_dir)],
        capture_output=True, text=True, encoding="utf-8", env=env, check=True,
    )
    return next(line for line in result.stdout.splitlines() if line.startswith("knowledge pack:"))


def test_pyz_works_without_pack_and_finds_one_next_to_it(build_pyz, tmp_path):
    pyz = build_pyz.build()
    data_dir = tmp_path / "data"
    assert run_info(pyz, data_dir).startswith("knowledge pack: not installed")
    shutil.copytree(MINI_PACK, pyz.parent / "knowledge" / "pack")  # portable layout
    line = run_info(pyz, data_dir)
    assert line.startswith("knowledge pack: installed (portable:") and "synthetic-test-fixture" in line
    shutil.copytree(MINI_PACK, data_dir / "knowledge" / "pack")  # the data directory wins
    assert run_info(pyz, data_dir).startswith("knowledge pack: installed (data_dir:")


def test_portable_pack_option_copies_a_valid_pack_only(build_pyz, tmp_path):
    module = load_packaging_module("build_portable")
    target = tmp_path / "portable" / "knowledge" / "pack"
    module.copy_pack(MINI_PACK, target)
    assert sorted(p.name for p in target.iterdir()) == sorted(p.name for p in MINI_PACK.iterdir())
    broken = tmp_path / "broken"
    broken.mkdir()
    with pytest.raises(SystemExit):
        module.copy_pack(broken, tmp_path / "other" / "knowledge" / "pack")
    assert not (tmp_path / "other").exists()


# -- guard: nothing of the pack is tracked or shipped (ARQUITETURA D10) ---------------------------


@pytest.fixture
def check_no_pack():
    return load_packaging_module("check_no_pack")


def test_guard_flags_tracked_pack_files_and_manifests(check_no_pack):
    manifest = b'{"schema_version": 1, "generator_version": "1", "notice": "real", "files": {}}'
    synthetic = (MINI_PACK / "manifest.json").read_bytes()
    files = {
        "src/dmslog/knowledge/pack/adapters.json": b"{}",
        "docs/elsewhere/manifest.json": manifest,
        "tests/fixtures/knowledge/mini_pack/manifest.json": synthetic,
        "tests/fixtures/other/manifest.json": synthetic,  # synthetic, but not the allowed path
        "web/manifest.json": b'{"name": "app"}',  # not a pack manifest
        "README.md": b"x",
    }
    problems = check_no_pack.tracked_problems(files, files.__getitem__)
    assert problems == [
        "src/dmslog/knowledge/pack/adapters.json: tracked under src/dmslog/knowledge/pack/",
        "docs/elsewhere/manifest.json: tracked knowledge pack manifest",
        "tests/fixtures/other/manifest.json: tracked knowledge pack manifest",
    ]
    fake_real = "tests/fixtures/knowledge/mini_pack/manifest.json"
    assert check_no_pack.tracked_problems([fake_real], lambda _n: manifest)


def test_guard_nothing_tracked_under_the_pack_path(check_no_pack):
    root = check_no_pack.ROOT
    if shutil.which("git") is None or not (root / ".git").exists():
        pytest.skip("not a git checkout")
    names = check_no_pack.tracked_files(root)
    assert names, "git ls-files returned nothing"
    assert check_no_pack.tracked_problems(names, lambda n: (root / n).read_bytes()) == []


def test_guard_inspects_archives_and_folders(check_no_pack, tmp_path):
    clean = tmp_path / "clean.zip"
    with zipfile.ZipFile(clean, "w") as archive:
        archive.writestr("dmslog/knowledge/curated/hints.toml", "")
    assert check_no_pack.artifact_problems(clean) == []
    dirty = tmp_path / "dirty.zip"
    with zipfile.ZipFile(dirty, "w") as archive:
        archive.writestr("dmslog-portable-win64/knowledge/pack/adapters.json", "{}")
    assert check_no_pack.artifact_problems(dirty)
    folder = tmp_path / "folder"
    (folder / "somewhere").mkdir(parents=True)
    shutil.copy(MINI_PACK / "manifest.json", folder / "somewhere" / "manifest.json")
    assert check_no_pack.artifact_problems(folder)
    tarball = tmp_path / "x.tar.gz"
    with tarfile.open(tarball, "w:gz") as tar:
        tar.add(MINI_PACK / "adapters.json", arcname="dmslog-linux/knowledge/pack/adapters.json")
    assert check_no_pack.artifact_problems(tarball)


def test_built_pyz_passes_the_guard(build_pyz, check_no_pack):
    assert check_no_pack.artifact_problems(build_pyz.build()) == []


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


def test_portable_folder_and_zip_include_readme(build_pyz, tmp_path, monkeypatch):
    module = load_packaging_module("build_portable")
    fake_embed = tmp_path / "embed.zip"
    with zipfile.ZipFile(fake_embed, "w") as z:
        z.writestr("python.exe", b"")
    monkeypatch.setattr(module, "fetch_embed", lambda: fake_embed)
    monkeypatch.setattr(module, "OUT", tmp_path / "dmslog-portable-win64")
    module.build()
    readme = (module.OUT / "README.txt").read_bytes()
    assert not (module.OUT / "LEIA-ME.txt").exists()
    assert b"\r\n" in readme and b"\n" not in readme.replace(b"\r\n", b"")
    text = readme.decode("utf-8")
    for needle in ("dmslog.bat", "%LOCALAPPDATA%\\dmslog", "127.0.0.1", "PRIVATE USE ONLY"):
        assert needle in text
    with zipfile.ZipFile(module.OUT.with_suffix(".zip")) as z:
        names = set(z.namelist())
    assert {
        "dmslog-portable-win64/README.txt",
        "dmslog-portable-win64/dmslog.bat",
        "dmslog-portable-win64/dmslog.pyz",
    } <= names


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
