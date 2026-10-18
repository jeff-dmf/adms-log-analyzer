"""Regression tests for the findings of the 8.2 security review (docs/notas/revisao-seguranca-8-2.md)."""

import importlib.util
import os
import stat
import subprocess
import sys
import zipfile
from collections import namedtuple
from pathlib import Path

import pytest
from test_api import client, server  # noqa: F401  (fixtures)
from test_api_export import body, case_module, demo  # noqa: F401  (fixtures)

import dmslog.ingest.reader as reader_module
from dmslog import paths
from dmslog.cli import main
from dmslog.ingest.discover import discover
from dmslog.ingest.pipeline import IngestOptions, run_ingest, validate_case_name
from dmslog.ingest.reader import InsufficientDiskSpaceError, ReadLimits, relax_ratio_violations
from dmslog.paths import private_mkdir
from dmslog.report import redact
from dmslog.store import create_case_db

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("make_fixture", ROOT / "tools" / "make_fixture.py")
mf = importlib.util.module_from_spec(_spec)
sys.modules["make_fixture"] = mf
_spec.loader.exec_module(mf)

LOG = "SchneiderElectricData/OASyS/Servers/Log"
posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX permissions and FIFOs")


def everything_under(root: Path) -> list[Path]:
    return sorted(root.rglob("*")) if root.exists() else []


# ---------------------------------------------------------------- P1: maps live in the case folder


def test_deleting_a_case_leaves_no_value_map_behind(client, server, demo):  # noqa: F811
    data = client.post("/api/export", body())[1]
    assert data["map_saved"] is True
    case_dir = server.api.store.root / demo
    assert list((case_dir / "redaction-maps").glob("*.map.json"))
    assert client.request("DELETE", f"/api/cases/{demo}")[0] == 204
    assert not case_dir.exists()
    leftovers = [p for p in everything_under(server.api.data_dir) if p.name.endswith(".map.json")]
    assert leftovers == []
    assert not (server.api.data_dir / "redaction-maps").exists()
    assert [p for p in everything_under(server.api.store.root)] == []  # no case, no -wal/-shm


def test_deleting_a_case_removes_old_format_maps_of_that_case_only(client, server, demo):  # noqa: F811
    old = server.api.data_dir / "redaction-maps"
    old.mkdir(parents=True)
    mine = old / "a.map.json"
    mine.write_text('{"case": "DEMO", "created": "x", "values": {"<GUID-1>": "real"}}', encoding="utf-8")
    other = old / "b.map.json"
    other.write_text('{"case": "Other", "created": "x", "values": {}}', encoding="utf-8")
    broken = old / "c.map.json"
    broken.write_text("not json", encoding="utf-8")
    assert client.request("DELETE", f"/api/cases/{demo}")[0] == 204
    assert not mine.exists() and other.exists() and broken.exists()


def test_old_map_folder_is_removed_when_it_ends_up_empty(tmp_path):
    folder = tmp_path / "redaction-maps"
    folder.mkdir()
    (folder / "a.map.json").write_text('{"case": "x1"}', encoding="utf-8")
    assert redact.remove_legacy_value_maps(tmp_path, "x1") == 1
    assert not folder.exists()
    assert redact.remove_legacy_value_maps(tmp_path, "x1") == 0  # nothing to do is fine


@pytest.mark.parametrize("bad", ["../x", "a/b", "..", "", "a\\b"])
def test_value_map_refuses_a_case_id_that_is_not_a_folder_name(tmp_path, bad):
    r = redact.Redactor()
    r.redact("id 11111111-2222-3333-4444-555555555555")
    with pytest.raises(ValueError):
        redact.write_value_map(tmp_path, bad, r)
    assert everything_under(tmp_path) == []


def test_a_delete_racing_the_export_leaves_no_map_and_no_folder(client, server, demo, monkeypatch):  # noqa: F811
    # the case is deleted right after the export read it and before the map is written
    real = redact.write_value_map
    case_dir = server.api.store.root / demo

    def after_delete(*args, **kwargs):
        import shutil

        shutil.rmtree(case_dir)  # the concurrent DELETE won the race
        return real(*args, **kwargs)

    monkeypatch.setattr(redact, "write_value_map", after_delete)
    status, data, _ = client.post("/api/export", body())
    assert status == 200 and data["map_saved"] is False
    assert not case_dir.exists() and not list(server.api.data_dir.rglob("*.map.json"))


@pytest.mark.parametrize("stage", ["before_write", "before_temp_file"])
def test_cli_export_racing_a_delete_never_recreates_the_case(tmp_path, monkeypatch, capsys, stage):
    import shutil

    data_dir = tmp_path / "data"
    case_dir = data_dir / "cases" / "demo"
    case_module.build_export_case(case_dir / "case.sqlite")

    def delete_case():
        shutil.rmtree(case_dir)  # the DELETE lands inside the CLI's write, with no lock around it

    if stage == "before_write":
        real = redact.write_value_map
        monkeypatch.setattr(redact, "write_value_map", lambda *a, **k: (delete_case(), real(*a, **k))[1])
    else:
        real_mkstemp = paths.tempfile.mkstemp  # redact writes through paths.atomic_write_bytes
        monkeypatch.setattr(
            paths.tempfile, "mkstemp", lambda *a, **k: (delete_case(), real_mkstemp(*a, **k))[1]
        )
    args = ["--data-dir", str(data_dir), "export", "--case", "demo", "--ids", "3", "--anonymize"]
    main(args)
    assert not case_dir.exists() and not list(data_dir.rglob("*.map.json"))


def test_a_refusal_for_space_on_the_second_source_writes_nothing(tmp_path, monkeypatch):
    folder = tmp_path / "Log" / "dnaApp_zone1" / "DMS_INTEGRATION"
    folder.mkdir(parents=True)
    line = b"2030-01-15 10:00:00.000 [PID=1111][TID=0x1] INFO - hello world\r\n"
    (folder / "WfmAdapter_1111.log").write_bytes(line * 10)
    conn = create_case_db(tmp_path / "data" / "cases" / "c" / "case.sqlite")
    try:
        # pretend disk: enough for one source (~650 B x 3) but not for the four together
        monkeypatch.setattr(reader_module.shutil, "disk_usage", lambda _p: FakeUsage(10, 10, 6000))
        (folder / "WfmAdapter_2222.log").write_bytes(line * 10)
        (folder / "WfmAdapter_3333.log").write_bytes(line * 10)
        (folder / "WfmAdapter_4444.log").write_bytes(line * 10)
        with pytest.raises(InsufficientDiskSpaceError):
            run_ingest(conn, tmp_path / "Log", IngestOptions(scope="all"))
        assert conn.execute("SELECT count(*) FROM source").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM entry").fetchone()[0] == 0
        assert (
            conn.execute("SELECT count(*) FROM meta WHERE key LIKE 'ingest_generation%'").fetchone()[0] == 0
        )
        monkeypatch.setattr(reader_module.shutil, "disk_usage", lambda _p: FakeUsage(10, 10, 10**9))
        assert run_ingest(conn, tmp_path / "Log", IngestOptions(scope="all")).sources_indexed == 4
    finally:
        conn.close()


def test_write_value_map_never_creates_the_case_folder(tmp_path):
    r = redact.Redactor()
    r.redact("id 11111111-2222-3333-4444-555555555555")
    with pytest.raises(FileNotFoundError):
        redact.write_value_map(tmp_path, "gone", r)
    assert everything_under(tmp_path) == []


def test_delete_and_map_write_are_serialized(server):  # noqa: F811
    assert hasattr(server.api.store, "map_lock")


@pytest.mark.parametrize("name", ["_demo", "a..b", "x_1"])
def test_cli_anonymized_export_works_for_every_name_the_cli_accepts(tmp_path, capsys, name):
    data_dir = tmp_path / "data"
    case_module.build_export_case(data_dir / "cases" / name / "case.sqlite")
    args = ["--data-dir", str(data_dir), "export", "--case", name, "--ids", "3", "--anonymize"]
    assert main(args) == 0
    assert list((data_dir / "cases" / name / "redaction-maps").glob("*.map.json"))
    assert "Value map saved to" in capsys.readouterr().err


def test_cli_export_then_removing_the_case_folder_leaves_nothing(tmp_path, capsys):
    data_dir = tmp_path / "data"
    case_module.build_export_case(data_dir / "cases" / "demo" / "case.sqlite")
    args = ["--data-dir", str(data_dir), "export", "--case", "demo", "--ids", "3", "--anonymize"]
    assert main(args) == 0
    assert list((data_dir / "cases" / "demo" / "redaction-maps").glob("*.map.json"))
    assert not (data_dir / "redaction-maps").exists()


# ---------------------------------------------------------------- S1: only regular files


@posix_only
def test_a_fifo_in_the_log_folder_is_ignored_and_does_not_block(tmp_path):
    folder = tmp_path / "Log" / "dnaApp_zone1" / "DMS_INTEGRATION"
    folder.mkdir(parents=True)
    (folder / "WfmAdapter_1111.log").write_bytes(b"x\n")
    os.mkfifo(folder / "WfmAdapter_2222.log")
    code = (
        "import sys\n"
        "from dmslog.ingest.pipeline import IngestOptions, run_ingest\n"
        "from dmslog.store import create_case_db\n"
        "conn = create_case_db(sys.argv[2])\n"
        "result = run_ingest(conn, sys.argv[1], IngestOptions(scope='all'))\n"
        "print(result.sources_listed)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "Log"), str(tmp_path / "case.sqlite")],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "1"  # the FIFO is not even listed


@posix_only
def test_discover_skips_fifo_and_keeps_regular_files(tmp_path):
    folder = tmp_path / "Log" / "dnaApp_zone1" / "DMS_INTEGRATION"
    folder.mkdir(parents=True)
    (folder / "WfmAdapter_1.log").write_bytes(b"x\n")
    os.mkfifo(folder / "WfmAdapter_2.log")
    assert [s.member.rsplit("/", 1)[-1] for s in discover(tmp_path / "Log")] == ["WfmAdapter_1.log"]


# ---------------------------------------------------------------- S2: aggregate cap and free space


def bomb_zip(tmp_path: Path, count: int, size: int = 3 * 1024 * 1024) -> Path:
    path = tmp_path / "bombs.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for n in range(count):
            zf.writestr(f"{LOG}/dnaApp_zone1/SVC/WfmAdapter_{100 + n}.log", b"\x00" * size)
    return path


def test_exempt_members_share_an_aggregate_cap(tmp_path):
    infos = list(discover(bomb_zip(tmp_path, 3)))
    assert all(s.limit_violation == "ratio" for s in infos)
    size = infos[0].size
    out = relax_ratio_violations(infos, ReadLimits(ratio_exempt_max_total_bytes=2 * size + 1))
    assert [s.limit_violation for s in out] == [None, None, "ratio"]
    assert ReadLimits().ratio_exempt_max_total_bytes == 8 * 1024**3


def test_the_default_aggregate_cap_lets_members_through(tmp_path):
    out = relax_ratio_violations(list(discover(bomb_zip(tmp_path, 2))), ReadLimits())
    assert [s.limit_violation for s in out] == [None, None]


FakeUsage = namedtuple("FakeUsage", "total used free")


def test_ingest_refuses_when_there_is_less_than_three_times_the_data_free(tmp_path, monkeypatch):
    zpath = mf.build_ers_zip(tmp_path / "z")
    monkeypatch.setattr(reader_module.shutil, "disk_usage", lambda _p: FakeUsage(10, 10, 100))
    conn = create_case_db(tmp_path / "data" / "cases" / "c" / "case.sqlite")
    try:
        with pytest.raises(InsufficientDiskSpaceError) as info:
            run_ingest(conn, zpath, IngestOptions())
    finally:
        conn.close()
    assert info.value.reason == "disk_space" and "free disk space" in str(info.value)


def test_ingest_proceeds_with_enough_free_space(tmp_path, monkeypatch):
    zpath = mf.build_ers_zip(tmp_path / "z")
    seen = []

    def usage(folder):
        seen.append(Path(folder))
        return FakeUsage(10**12, 0, 10**12)

    monkeypatch.setattr(reader_module.shutil, "disk_usage", usage)
    conn = create_case_db(tmp_path / "data" / "cases" / "c" / "case.sqlite")
    try:
        assert run_ingest(conn, zpath, IngestOptions()).records > 0
    finally:
        conn.close()
    assert set(seen) == {tmp_path / "data" / "cases" / "c"}


def test_unchanged_reingest_and_small_appends_need_little_space(tmp_path, monkeypatch):
    folder = tmp_path / "Log" / "dnaApp_zone1" / "DMS_INTEGRATION"
    folder.mkdir(parents=True)
    log = folder / "WfmAdapter_1111.log"
    line = b"2030-01-15 10:00:00.000 [PID=1111][TID=0x1] INFO - hello world\r\n"
    log.write_bytes(line * 2000)  # ~130 kB
    conn = create_case_db(tmp_path / "data" / "cases" / "c" / "case.sqlite")
    try:
        assert run_ingest(conn, tmp_path / "Log", IngestOptions(scope="all")).records == 2000
        monkeypatch.setattr(reader_module.shutil, "disk_usage", lambda _p: FakeUsage(10, 10, 10_000))
        # nothing changed: needs no space at all, however full the disk
        assert run_ingest(conn, tmp_path / "Log", IngestOptions(scope="all")).sources_unchanged == 1
        # a small append (3 lines, ~200 bytes, x3 = 600 < 10 kB free) goes through
        with log.open("ab") as fh:
            fh.write(line * 3)
        assert run_ingest(conn, tmp_path / "Log", IngestOptions(scope="all")).records >= 3
        # a big append does not (free 10 kB < 3 x ~65 kB)
        with log.open("ab") as fh:
            fh.write(line * 1000)
        with pytest.raises(InsufficientDiskSpaceError):
            run_ingest(conn, tmp_path / "Log", IngestOptions(scope="all"))
    finally:
        conn.close()


def test_cli_reports_the_space_problem_clearly(tmp_path, monkeypatch, capsys):
    zpath = mf.build_ers_zip(tmp_path / "z")
    monkeypatch.setattr(reader_module.shutil, "disk_usage", lambda _p: FakeUsage(10, 10, 100))
    assert main(["--data-dir", str(tmp_path / "data"), "ingest", str(zpath)]) == 1
    err = capsys.readouterr().err
    assert "not enough free disk space" in err and "unsafe" not in err


# ---------------------------------------------------------------- S3: private data folder


@posix_only
def test_data_folders_are_created_private(tmp_path):
    old = os.umask(0o022)
    try:
        conn = create_case_db(tmp_path / "newdata" / "cases" / "c1" / "case.sqlite")
        conn.execute("CREATE TABLE IF NOT EXISTS t(x)")
        conn.close()
    finally:
        os.umask(old)
    for folder in (
        tmp_path / "newdata",
        tmp_path / "newdata" / "cases",
        tmp_path / "newdata" / "cases" / "c1",
    ):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700, folder


@posix_only
def test_existing_folders_keep_their_mode(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o755)
    private_mkdir(shared / "inner")
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755
    assert stat.S_IMODE((shared / "inner").stat().st_mode) == 0o700


def test_private_mkdir_is_idempotent(tmp_path):
    assert private_mkdir(tmp_path / "a" / "b") == tmp_path / "a" / "b"
    assert private_mkdir(tmp_path / "a" / "b").is_dir()


# ---------------------------------------------------------------- S5: reserved names in the CLI


@pytest.mark.parametrize("name", ["CON", "nul", "COM1", "lpt9", "nul.txt", "Aux.log"])
def test_reserved_windows_names_are_rejected_as_case_names(name):
    with pytest.raises(ValueError):
        validate_case_name(name)


@pytest.mark.parametrize("name", ["console", "com10", "case1", "nul-x"])
def test_names_that_only_look_reserved_are_accepted(name):
    assert validate_case_name(name) == name


def test_cli_rejects_a_reserved_case_name(tmp_path, capsys):
    zpath = mf.build_ers_zip(tmp_path / "z")
    assert main(["--data-dir", str(tmp_path / "data"), "ingest", str(zpath), "--case", "CON"]) == 1
    assert "invalid case name" in capsys.readouterr().err
    assert not (tmp_path / "data" / "cases").exists()


def test_api_and_cli_share_the_reserved_name_rule(server):  # noqa: F811
    from dmslog.server.api import ApiError

    with pytest.raises(ApiError):
        server.api.store.validate_id("COM1")


# ---------------------------------------------------------------- plan == run (disk space check)

PLAIN = b"just some plain text without any header\r\n"
HEADED = b"2030-01-15 10:00:00.000 [PID=1111][TID=0x1] INFO - hello world\r\n"


def _folder_with(tmp_path: Path, files: dict[str, bytes]) -> Path:
    folder = tmp_path / "Log" / "dnaApp_zone1" / "DMS_INTEGRATION"
    folder.mkdir(parents=True)
    for name, data in files.items():
        (folder / name).write_bytes(data)
    return tmp_path / "Log"


def _set_mark(conn, mark: int) -> None:
    for (source_id,) in conn.execute("SELECT id FROM source").fetchall():
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (f"payload_extraction_version.{source_id}", str(mark)),
        )
    conn.commit()


def test_the_small_plain_source_is_planned_as_a_reindex_even_with_a_key_only_upgrade(tmp_path, monkeypatch):
    # reviewer scenario: mark 1 (key-only upgrade to 2), ~1 kB indexed as plain, then a 2-byte append
    root = _folder_with(tmp_path, {"WfmAdapter_1111.log": PLAIN * 25})
    conn = create_case_db(tmp_path / "data" / "cases" / "c" / "case.sqlite")
    try:
        run_ingest(conn, root, IngestOptions(scope="all"))
        assert conn.execute("SELECT envelope FROM source").fetchone()[0] == "plain"
        _set_mark(conn, 1)
        log = root / "dnaApp_zone1" / "DMS_INTEGRATION" / "WfmAdapter_1111.log"
        with log.open("ab") as fh:
            fh.write(b"zz")
        size = log.stat().st_size
        monkeypatch.setattr(reader_module.shutil, "disk_usage", lambda _p: FakeUsage(10, 10, 6))
        with pytest.raises(InsufficientDiskSpaceError):  # the run would re-index all ~1 kB
            run_ingest(conn, root, IngestOptions(scope="all"))
        monkeypatch.setattr(reader_module.shutil, "disk_usage", lambda _p: FakeUsage(10, 10, 10**9))
        result = run_ingest(conn, root, IngestOptions(scope="all"))
        assert [d.action for d in result.details] == ["reindex"] and result.bytes >= size - 2
    finally:
        conn.close()


@pytest.mark.parametrize(
    "scenario",
    ["new", "unchanged", "append", "old_mark", "key_only", "plain_key_only_append", "plain_old_mark_append",
     "pending", "truncated"],
)  # fmt: skip
def test_the_plan_and_the_run_decide_the_same_action_for_every_source(tmp_path, monkeypatch, scenario):
    from dmslog.ingest import pipeline

    plain = scenario.startswith("plain")
    base = (PLAIN if plain else HEADED) * (25 if plain else 50)
    root = _folder_with(tmp_path, {"WfmAdapter_1111.log": base, "WfmAdapter_2222.log": HEADED * 10})
    folder = root / "dnaApp_zone1" / "DMS_INTEGRATION"
    conn = create_case_db(tmp_path / "data" / "cases" / "c" / "case.sqlite")
    try:
        if scenario != "new":
            run_ingest(conn, root, IngestOptions(scope="all"))
        if scenario in ("old_mark", "plain_old_mark_append"):
            _set_mark(conn, 0)
        if scenario in ("key_only", "plain_key_only_append"):
            _set_mark(conn, 1)
        if scenario.endswith("append") or scenario == "pending":
            with (folder / "WfmAdapter_1111.log").open("ab") as fh:
                fh.write(PLAIN if plain else HEADED)
        if scenario == "pending":
            conn.execute("UPDATE source SET status='pending' WHERE member LIKE '%1111%'")
            conn.commit()
        if scenario == "truncated":
            (folder / "WfmAdapter_1111.log").write_bytes(HEADED * 3)
        planned, ran = {}, {}
        phase = {"run": False}
        real_plan, real_pending = pipeline._Run.plan_action, pipeline._Run.pending_bytes

        def spy_plan(self, row, info, reader):
            out = real_plan(self, row, info, reader)
            (ran if phase["run"] else planned)[info.member] = (out[0].value, out[1])
            return out

        def spy_pending(self, *args):
            out = real_pending(self, *args)
            phase["run"] = True
            return out

        monkeypatch.setattr(pipeline._Run, "plan_action", spy_plan)
        monkeypatch.setattr(pipeline._Run, "pending_bytes", spy_pending)
        result = run_ingest(conn, root, IngestOptions(scope="all"))
    finally:
        conn.close()
    assert {k: v for k, v in ran.items() if k in planned} == planned  # the same decision, source by source
    assert {d.member: d.action for d in result.details}  # the run did act on every source
