"""Native file picker and folder browser (server/filepick.py, ARQUITETURA 10.4).

Everything that does not need a desktop is tested here: path validation for both rule sets, the
read-only folder listing, tool detection, the fixed command lines, the parsing of every dialog's
answer and the one-dialog-at-a-time picker (with fake processes and, on POSIX, a fake ``zenity``).
The real dialogs are checked by hand on Windows and Linux.
"""

from __future__ import annotations

import builtins
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from dmslog.server import filepick as fp

UMLAUT_ZIP = "\u00fc ERS.zip"
posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX file names, symlinks and permissions")
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows dialog helper")


def rejected(raw, **kwargs) -> str:
    with pytest.raises(fp.PathRejectedError) as info:
        fp.check_path(raw, **kwargs)
    return info.value.code


# --------------------------------------------------------------------------- check_path, POSIX rules


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/home/user/ERS", "/home/user/ERS"),
        ("/home/user/ERS/", "/home/user/ERS"),
        ("/home/user/../other/./x", "/home/other/x"),
        ("/../../etc", "/etc"),
        ("//home//user", "/home/user"),
        ("///", "/"),
        ("/data/A\u00e7\u00e3o ERS/a b.zip", "/data/A\u00e7\u00e3o ERS/a b.zip"),
    ],
)
def test_posix_paths_are_normalized_lexically(raw, expected):
    assert fp.check_path(raw, windows=False) == expected


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("relative/dir", "path_not_absolute"),
        ("~/ERS", "path_not_absolute"),
        ("", "invalid_path"),
        (None, "invalid_path"),
        (42, "invalid_path"),
        (["/tmp"], "invalid_path"),
        ("/tmp/a\x00b", "invalid_path"),
        ("/tmp/a\nb", "invalid_path"),
        ("/tmp/a\x7fb", "invalid_path"),
        ("/tmp/bad\udcff.zip", "invalid_path"),
        ("/" + "a" * fp.MAX_PATH_CHARS, "invalid_path"),
    ],
)
def test_posix_bad_paths_are_refused(raw, code):
    assert rejected(raw, windows=False) == code


def test_posix_reserved_windows_names_are_allowed():
    assert fp.check_path("/src/aux/con.txt", windows=False) == "/src/aux/con.txt"


# --------------------------------------------------------------------------- check_path, Windows rules


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("C:", "C:\\"),
        ("c:\\", "c:\\"),
        ("C:\\Logs\\ERS", "C:\\Logs\\ERS"),
        ("C:/Logs/ERS/", "C:\\Logs\\ERS"),
        ("C:\\Logs\\..\\..\\Windows", "C:\\Windows"),
        ("D:\\A\u00e7\u00e3o\\ERS 1.zip", "D:\\A\u00e7\u00e3o\\ERS 1.zip"),
        ("C:\\console\\nul_file\\comma", "C:\\console\\nul_file\\comma"),
    ],
)
def test_windows_paths_are_normalized(raw, expected):
    assert fp.check_path(raw, windows=True) == expected


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("C:foo", "path_not_absolute"),
        ("\\Logs", "path_not_absolute"),
        ("Logs\\ERS", "path_not_absolute"),
        ("\\\\server\\share\\ERS", "unc_path"),
        ("//server/share/ERS", "unc_path"),
        ("\\\\?\\C:\\Logs", "device_path"),
        ("//?/C:/Logs", "device_path"),
        ("\\\\.\\PhysicalDrive0", "device_path"),
        ("\\\\?\\UNC\\server\\share", "device_path"),
        ("\\??\\C:\\Logs", "device_path"),
        ("C:\\Logs\\CON", "reserved_name"),
        ("C:\\Logs\\nul.txt", "reserved_name"),
        ("C:\\Logs\\com1 \\x", "reserved_name"),
        ("C:\\Logs\\LPT9.", "reserved_name"),
        ("C:\\Logs\\aux.tar.gz", "reserved_name"),
        ("C:\\Logs\\a.zip:stream", "invalid_path"),
        ("C:\\Logs\\a<b", "invalid_path"),
        ("C:\\Logs\\what?.zip", "invalid_path"),
        ("C:\\Logs\\a|b", "invalid_path"),
        ("C:\\Logs\\a\x00b", "invalid_path"),
        ("C:\\Logs\\a\x1fb", "invalid_path"),
    ],
)
def test_windows_bad_paths_are_refused(raw, code):
    assert rejected(raw, windows=True) == code


def test_windows_unc_only_when_allowed():
    assert fp.check_path("\\\\server\\share\\ERS\\a.zip", windows=True, allow_unc=True) == (
        "\\\\server\\share\\ERS\\a.zip"
    )
    assert rejected("\\\\server", windows=True, allow_unc=True) == "path_not_absolute"
    assert rejected("\\\\?\\C:\\x", windows=True, allow_unc=True) == "device_path"


# --------------------------------------------------------------------------- list_dir


def no_shortcuts():
    return []


def make_tree(root: Path) -> Path:
    root.mkdir()
    for name in ("Beta", "alpha", "\u00c7\u00e3o", "with space", "Zeta"):
        (root / name).mkdir()
    for name in ("b.ZIP", "A.zip", "\u00fc ERS.zip", "c.zip"):
        (root / name).write_bytes(b"PK" + name.encode("utf-8"))
    (root / "notes.txt").write_text("x", encoding="utf-8")
    (root / "fake.zip.txt").write_text("x", encoding="utf-8")
    (root / "folder.zip").mkdir()  # a folder named like an archive is a folder
    return root


def test_list_dir_orders_folders_then_archives_case_insensitively(tmp_path):
    root = make_tree(tmp_path / "tree")
    listing = fp.list_dir(str(root), windows=os.name == "nt", shortcut_list=no_shortcuts)
    names = [e["name"] for e in listing["entries"]]
    assert names == [
        "alpha",
        "Beta",
        "folder.zip",
        "with space",
        "Zeta",
        "\u00c7\u00e3o",
        "A.zip",
        "b.ZIP",
        "c.zip",
        "\u00fc ERS.zip",
    ]
    types = {e["name"]: e["type"] for e in listing["entries"]}
    assert types["folder.zip"] == "dir" and types["b.ZIP"] == "zip"
    assert listing["other_files"] == 2 and listing["skipped"] == 0 and listing["truncated"] is False
    assert listing["path"] == str(root) and listing["parent"] == str(root.parent)
    assert listing["shortcuts"] == []


def test_list_dir_entry_fields(tmp_path):
    root = make_tree(tmp_path / "tree")
    entries = {e["name"]: e for e in fp.list_dir(str(root), shortcut_list=no_shortcuts)["entries"]}
    archive = entries["A.zip"]
    assert archive["path"] == str(root / "A.zip")
    assert archive["size"] == len(b"PKA.zip")
    assert isinstance(archive["mtime"], int) and abs(archive["mtime"] / 1000 - time.time()) < 3600
    assert archive["link"] is False and archive["broken"] is False and archive["hidden"] is False
    folder = entries["alpha"]
    assert folder["type"] == "dir" and folder["size"] is None and folder["path"] == str(root / "alpha")
    json.dumps(entries)  # JSON-ready


def test_list_dir_round_trips_entry_paths(tmp_path):
    root = make_tree(tmp_path / "tree")
    entries = fp.list_dir(str(root), shortcut_list=no_shortcuts)["entries"]
    inner = next(e for e in entries if e["name"] == "\u00c7\u00e3o")
    assert fp.list_dir(inner["path"], shortcut_list=no_shortcuts)["entries"] == []


def test_list_dir_never_opens_files(tmp_path, monkeypatch):
    root = make_tree(tmp_path / "tree")

    def refuse(*args, **kwargs):
        raise AssertionError("list_dir must not open files")

    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", refuse)
        patch.setattr(Path, "open", refuse)
        listing = fp.list_dir(str(root), shortcut_list=no_shortcuts)
    assert len(listing["entries"]) == 10


def test_list_dir_truncates(tmp_path):
    root = make_tree(tmp_path / "tree")
    listing = fp.list_dir(str(root), limit=3, shortcut_list=no_shortcuts)
    assert [e["name"] for e in listing["entries"]] == ["alpha", "Beta", "folder.zip"]
    assert listing["truncated"] is True
    assert fp.list_dir(str(root), scan_limit=2, shortcut_list=no_shortcuts)["truncated"] is True


def test_list_dir_parent_of_root_is_none():
    root = os.path.abspath(os.sep)
    listing = fp.list_dir(root, limit=1, scan_limit=10, shortcut_list=no_shortcuts)
    assert listing["parent"] is None


@pytest.mark.parametrize(("make", "code"), [
    (lambda p: p / "missing", "path_not_found"),
    (lambda p: p / "tree" / "A.zip", "not_a_directory"),
    (lambda p: "relative", "path_not_absolute"),
    (lambda p: str(p) + "\x00", "invalid_path"),
])  # fmt: skip
def test_list_dir_errors(tmp_path, make, code):
    make_tree(tmp_path / "tree")
    with pytest.raises(fp.PathRejectedError) as info:
        fp.list_dir(str(make(tmp_path)), shortcut_list=no_shortcuts)
    assert info.value.code == code


@posix_only
def test_list_dir_marks_symlinks_and_broken_links(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "far.zip").write_bytes(b"PK")
    root = tmp_path / "tree"
    root.mkdir()
    (root / "to-outside").symlink_to(outside)
    (root / "far-link.zip").symlink_to(outside / "far.zip")
    (root / "gone.zip").symlink_to(tmp_path / "nothing.zip")
    (root / "gone-dir").symlink_to(tmp_path / "nothing")
    entries = {e["name"]: e for e in fp.list_dir(str(root), shortcut_list=no_shortcuts)["entries"]}
    assert set(entries) == {"to-outside", "far-link.zip", "gone.zip"}
    assert entries["to-outside"]["type"] == "dir" and entries["to-outside"]["link"] is True
    assert entries["to-outside"]["path"] == str(root / "to-outside")  # never resolved
    assert entries["far-link.zip"]["link"] is True and entries["far-link.zip"]["size"] == 2
    assert entries["gone.zip"]["broken"] is True and entries["gone.zip"]["size"] is None


@posix_only
def test_list_dir_skips_names_that_are_not_text(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "ok.zip").write_bytes(b"PK")
    try:
        (Path(os.fsdecode(bytes(root) + b"/bad\xff.zip"))).write_bytes(b"PK")
        (root / "new\nline.zip").write_bytes(b"PK")
    except OSError:
        pytest.skip("file system refuses these names")
    listing = fp.list_dir(str(root), shortcut_list=no_shortcuts)
    assert [e["name"] for e in listing["entries"]] == ["ok.zip"]
    assert listing["skipped"] == 2
    json.dumps(listing, ensure_ascii=False).encode("utf-8")  # no lone surrogates left


@posix_only
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores permissions")
def test_list_dir_permission_denied(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        with pytest.raises(fp.PathRejectedError) as info:
            fp.list_dir(str(locked), shortcut_list=no_shortcuts)
        assert info.value.code == "permission_denied"
        listing = fp.list_dir(str(tmp_path), shortcut_list=no_shortcuts)  # the locked folder is still listed
        assert [e["name"] for e in listing["entries"]] == ["locked"]
    finally:
        locked.chmod(0o700)


# --------------------------------------------------------------------------- shortcuts


def test_xdg_user_dirs_parsing():
    text = "\n".join([
        "# comment",
        'XDG_DESKTOP_DIR="$HOME/"',
        'XDG_DOWNLOAD_DIR="$HOME/Transfer\u00eancias"',
        'XDG_DOCUMENTS_DIR="/data/docs"',
        'XDG_MUSIC_DIR="$HOME/Music"',
        'XDG_TEMPLATES_DIR="relative"',
    ])  # fmt: skip
    found = fp.xdg_user_dirs(text, "/home/u")
    assert found == {
        "desktop": "/home/u/",
        "downloads": "/home/u/Transfer\u00eancias",
        "documents": "/data/docs",
    }


@posix_only
def test_shortcuts_linux(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Baixados").mkdir(parents=True)
    (home / "Documents").mkdir()
    config = tmp_path / "config"
    config.mkdir()
    (config / "user-dirs.dirs").write_text(
        'XDG_DESKTOP_DIR="$HOME/"\nXDG_DOWNLOAD_DIR="$HOME/Baixados"\n', encoding="utf-8"
    )
    monkeypatch.setenv("HOME", str(home))
    found = fp.shortcuts({"XDG_CONFIG_HOME": str(config), "USER": "../x"}, windows=False)
    kinds = [(s["kind"], s["path"]) for s in found]
    assert kinds[:3] == [("home", str(home)), ("downloads", str(home / "Baixados")),
                         ("documents", str(home / "Documents"))]  # fmt: skip
    assert ("root", "/") in kinds
    assert all(os.path.isdir(path) for _, path in kinds)
    assert not any("../x" in path for _, path in kinds)


@windows_only
def test_shortcuts_windows_have_home_and_drives():
    found = fp.shortcuts()
    assert found[0]["kind"] == "home"
    assert any(s["kind"] == "drive" and s["path"].endswith(":\\") for s in found)


# --------------------------------------------------------------------------- detection and commands


KDE = {"DISPLAY": ":0", "XDG_CURRENT_DESKTOP": "KDE"}


def which_of(*names):
    return lambda name: f"/usr/bin/{name}" if name in names else None


@pytest.mark.parametrize(("platform", "env", "names", "expected"), [
    ("win32", {}, (), ("windows-comdlg", "C:\\py\\python.exe")),
    ("linux", {}, ("zenity", "kdialog"), None),
    ("linux", {"DISPLAY": ":0"}, ("zenity", "kdialog"), ("zenity", "/usr/bin/zenity")),
    ("linux", {"WAYLAND_DISPLAY": "wayland-1"}, ("kdialog",), ("kdialog", "/usr/bin/kdialog")),
    ("linux", KDE, ("zenity", "kdialog"), ("kdialog", "/usr/bin/kdialog")),
    ("linux", KDE, ("zenity",), ("zenity", "/usr/bin/zenity")),
    ("linux", {"DISPLAY": ":0"}, (), None),
    ("darwin", {"DISPLAY": ":0"}, ("zenity",), None),
])  # fmt: skip
def test_detect_native(platform, env, names, expected):
    tool = fp.detect_native(
        platform=platform, env=env, which=which_of(*names), executable="C:\\py\\python.exe"
    )
    assert (None if tool is None else (tool.kind, tool.executable)) == expected


def test_detect_native_windows_without_interpreter():
    assert fp.detect_native(platform="win32", env={}, executable="") is None


def test_zenity_commands_are_fixed():
    tool = fp.NativeTool("zenity", "/usr/bin/zenity")
    multi = fp.build_command(tool, "zip", True)
    assert multi[0] == "/usr/bin/zenity" and "--file-selection" in multi and "--multiple" in multi
    assert "--separator=\n" in multi and "--directory" not in multi
    assert any(a.startswith("--file-filter=") and "*.zip" in a for a in multi)
    assert "--multiple" not in fp.build_command(tool, "zip", False)
    folder = fp.build_command(tool, "folder", True)
    assert "--directory" in folder and "--multiple" not in folder


def test_kdialog_commands_are_fixed():
    tool = fp.NativeTool("kdialog", "/usr/bin/kdialog")
    multi = fp.build_command(tool, "zip", True)
    assert "--getopenfilename" in multi and multi[-2:] == ["--multiple", "--separate-output"]
    assert "--getexistingdirectory" in fp.build_command(tool, "folder", True)


def test_windows_command_runs_the_fixed_script():
    tool = fp.NativeTool("windows-comdlg", "C:\\py\\python.exe")
    argv = fp.build_command(tool, "zip", True)
    assert argv[:4] == ["C:\\py\\python.exe", "-I", "-c", fp.WINDOWS_DIALOG_SCRIPT]
    assert argv[4] == "zip-multi"
    assert fp.build_command(tool, "zip", False)[4] == "zip"
    assert fp.build_command(tool, "folder", True)[4] == "folder"
    compile(fp.WINDOWS_DIALOG_SCRIPT, "<dialog>", "exec")


def test_build_command_refuses_unknown_kind():
    with pytest.raises(ValueError):
        fp.build_command(fp.NativeTool("zenity", "/usr/bin/zenity"), "exe", True)


# --------------------------------------------------------------------------- parsing of dialog answers


@pytest.mark.parametrize(("buffer", "expected"), [
    ("C:\\ERS\\a.zip", ["C:\\ERS\\a.zip"]),
    ("C:\\ERS\0a.zip\0b c.zip\0", ["C:\\ERS\\a.zip", "C:\\ERS\\b c.zip"]),
    ("C:\\\0a.zip\0b.zip", ["C:\\a.zip", "C:\\b.zip"]),
    ("D:\\A\u00e7\u00e3o\0\u00fc.zip\0日本.zip",
     ["D:\\A\u00e7\u00e3o\\\u00fc.zip", "D:\\A\u00e7\u00e3o\\日本.zip"]),
    ("\\\\srv\\share\\ERS\0a.zip\0b.zip", ["\\\\srv\\share\\ERS\\a.zip", "\\\\srv\\share\\ERS\\b.zip"]),
    ("", []),
])  # fmt: skip
def test_parse_comdlg_buffer(buffer, expected):
    assert fp.parse_comdlg_buffer(buffer) == expected


def test_parse_comdlg_buffer_long_paths():
    folder = "C:\\" + "\\".join(["d" * 200] * 3)
    assert fp.parse_comdlg_buffer(folder + "\0" + "x.zip\0y.zip") == [folder + "\\x.zip", folder + "\\y.zip"]


@posix_only
def test_parse_lines():
    assert fp.parse_lines(b"/a/b.zip\n/c d/e.zip\n") == ["/a/b.zip", "/c d/e.zip"]
    assert fp.parse_lines(b"/a.zip\r\n\r\n/b.zip") == ["/a.zip", "/b.zip"]
    assert fp.parse_lines("/A\u00e7\u00e3o/\u00fc.zip\n".encode()) == ["/A\u00e7\u00e3o/\u00fc.zip"]
    assert fp.parse_lines(b"") == []
    (bad,) = fp.parse_lines(b"/x/bad\xff.zip\n")
    assert fp.validate_picked([bad], "zip", windows=False)[1][0]["error"] == "invalid_path"


@pytest.mark.parametrize("tool", ["zenity", "kdialog"])
def test_parse_answer_linux_tools(tool):
    assert fp.parse_answer(tool, 0, b"/a.zip\n/b.zip\n") == fp.DialogAnswer(["/a.zip", "/b.zip"], False)
    assert fp.parse_answer(tool, 1, b"") == fp.DialogAnswer([], True)
    assert fp.parse_answer(tool, 0, b"\n") == fp.DialogAnswer([], True)
    for code in (5, 255, -9):
        with pytest.raises(fp.PickerError):
            fp.parse_answer(tool, code, b"")


def win_answer(obj) -> bytes:
    return (json.dumps(obj) + "\r\n").encode("ascii")


def test_parse_answer_windows_helper():
    ok = fp.parse_answer("windows-comdlg", 0, win_answer({"status": "ok", "buffer": "C:\\E\0a.zip\0b.zip"}))
    assert ok == fp.DialogAnswer(["C:\\E\\a.zip", "C:\\E\\b.zip"], False)
    assert fp.parse_answer("windows-comdlg", 0, win_answer({"status": "cancel"})).cancelled is True
    for rc, out in [
        (0, win_answer({"status": "error", "code": 0x3003})),
        (0, b"not json\n"),
        (0, b""),
        (0, '{"status": "ok", "buffer": "C:\\\\\u00e9"}'.encode()),  # helper output is ASCII only
        (1, win_answer({"status": "cancel"})),
        (0, win_answer(["ok"])),
        (0, win_answer({"status": "ok", "buffer": 3})),
    ]:
        with pytest.raises(fp.PickerError):
            fp.parse_answer("windows-comdlg", rc, out)


def test_parse_answer_refuses_huge_output():
    with pytest.raises(fp.PickerError):
        fp.parse_answer("zenity", 0, b"/a.zip\n" * (fp.MAX_DIALOG_OUTPUT // 7 + 1))


# --------------------------------------------------------------------------- validate_picked


def test_validate_picked(tmp_path):
    root = make_tree(tmp_path / "tree")
    windows = os.name == "nt"
    names = ["A.zip", "b.ZIP", "A.zip", "notes.txt", "missing.zip", "alpha"]
    paths = [str(root / name) for name in names] + ["relative.zip", str(root / "x\x00.zip")]
    ok, errors = fp.validate_picked(paths, "zip", windows=windows)
    assert ok == [str(root / "A.zip"), str(root / "b.ZIP")]
    assert [e["error"] for e in errors] == [
        "not_a_zip", "path_not_found", "not_a_zip", "path_not_absolute", "invalid_path",
    ]  # fmt: skip
    assert all(set(e) == {"path", "error", "message"} for e in errors)


def test_validate_picked_folder(tmp_path):
    root = make_tree(tmp_path / "tree")
    ok, errors = fp.validate_picked(
        [str(root / "alpha"), str(root / "A.zip")], "folder", windows=os.name == "nt"
    )
    assert ok == [str(root / "alpha")] and errors[0]["error"] == "not_a_directory"


def test_validate_picked_caps_the_answer(tmp_path):
    archive = tmp_path / "a.zip"
    archive.write_bytes(b"PK")
    ok, errors = fp.validate_picked(
        [str(archive)] + ["rel"] * (fp.MAX_PICKED + 50), "zip", windows=os.name == "nt"
    )
    assert len(ok) + len(errors) == fp.MAX_PICKED


# --------------------------------------------------------------------------- Picker with fake processes


class FakeProc:
    def __init__(self, returncode=0, stdout=b"", hang=False):
        self.returncode = returncode
        self._stdout = stdout
        self._hang = hang
        self.killed = threading.Event()
        self.started = threading.Event()

    def communicate(self, timeout=None):
        self.started.set()
        if self._hang:
            if not self.killed.wait(timeout):
                raise subprocess.TimeoutExpired("fake", timeout)
            self.returncode = -9
            return b"", None
        return self._stdout, None

    def kill(self):
        self.killed.set()


ZENITY = fp.NativeTool("zenity", "/usr/bin/zenity")


def fake_picker(proc, tool=ZENITY, **kwargs):
    calls = []

    def popen(argv, **options):
        calls.append((argv, options))
        return proc

    picker = fp.Picker(lambda: tool, popen=popen, windows=os.name == "nt", **kwargs)
    return picker, calls


def test_picker_returns_validated_paths(tmp_path):
    root = make_tree(tmp_path / "tree")
    out = f"{root / 'A.zip'}\n{root / 'notes.txt'}\n".encode()
    picker, calls = fake_picker(FakeProc(0, out))
    result = picker.pick("zip", True)
    assert result["paths"] == [str(root / "A.zip")]
    assert result["cancelled"] is False and result["timed_out"] is False and result["truncated"] is False
    assert [e["error"] for e in result["errors"]] == ["not_a_zip"]
    ((argv, options),) = calls
    assert options["shell"] is False and options["stdin"] is subprocess.DEVNULL
    assert argv == fp.build_command(fp.NativeTool("zenity", "/usr/bin/zenity"), "zip", True)


def test_picker_single_keeps_one_path(tmp_path):
    root = make_tree(tmp_path / "tree")
    out = f"{root / 'A.zip'}\n{root / 'c.zip'}\n".encode()
    picker, _ = fake_picker(FakeProc(0, out))
    assert picker.pick("zip", False)["paths"] == [str(root / "A.zip")]


def test_picker_cancel_by_user():
    picker, _ = fake_picker(FakeProc(1, b""))
    assert picker.pick("zip", True) == {"paths": [], "cancelled": True, "timed_out": False, "errors": [],
                                        "truncated": False}  # fmt: skip


def test_picker_tool_failure():
    picker, _ = fake_picker(FakeProc(255, b""))
    with pytest.raises(fp.PickerError):
        picker.pick("zip", True)


def test_picker_unavailable():
    with pytest.raises(fp.PickerError):
        fp.Picker(lambda: None).pick("zip", True)
    assert fp.Picker(lambda: None).capabilities()["native"] == {"available": False, "kind": None}


def test_picker_start_failure():
    def popen(argv, **options):
        raise FileNotFoundError(argv[0])

    picker = fp.Picker(lambda: fp.NativeTool("zenity", "/nope/zenity"), popen=popen)
    with pytest.raises(fp.PickerError):
        picker.pick("zip", True)


def run_in_thread(func):
    box = {}

    def target():
        try:
            box["result"] = func()
        except Exception as exc:  # noqa: BLE001 - handed to the test
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, box


def test_picker_one_dialog_at_a_time_and_cancel():
    proc = FakeProc(hang=True)
    picker, _ = fake_picker(proc)
    thread, box = run_in_thread(lambda: picker.pick("zip", True))
    assert proc.started.wait(5)
    with pytest.raises(fp.PickerBusyError):
        picker.pick("zip", True)
    assert picker.cancel() is True
    thread.join(5)
    assert box["result"]["cancelled"] is True and box["result"]["paths"] == []
    assert picker.cancel() is False  # nothing open any more
    picker2, _ = fake_picker(FakeProc(1, b""))
    assert picker2.pick("zip", True)["cancelled"] is True


def test_picker_timeout_kills_the_dialog():
    proc = FakeProc(hang=True)
    picker, _ = fake_picker(proc, timeout=0.05)
    result = picker.pick("zip", True)
    assert result["cancelled"] is True and result["timed_out"] is True and proc.killed.is_set()


def test_picker_close_cancels_and_refuses_new_dialogs():
    proc = FakeProc(hang=True)
    picker, calls = fake_picker(proc)
    thread, box = run_in_thread(lambda: picker.pick("zip", True))
    assert proc.started.wait(5)
    picker.close()
    thread.join(5)
    assert box["result"]["cancelled"] is True
    assert picker.pick("zip", True)["cancelled"] is True
    assert len(calls) == 1


def test_capabilities_shape():
    caps = fp.Picker(lambda: fp.NativeTool("kdialog", "/usr/bin/kdialog")).capabilities()
    assert caps["native"] == {"available": True, "kind": "kdialog"}
    assert caps["browse"] is True and caps["kinds"] == ["zip", "folder"]
    assert caps["platform"] in ("windows", "linux", "other")


# --------------------------------------------------------------------------- real child process (fake zenity)


FAKE_ZENITY = """#!{python}
import os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "argv.txt"), "w", encoding="utf-8") as fh:
    fh.write("\\x1e".join(sys.argv[1:]))
mode = open(os.path.join(here, "mode.txt"), encoding="utf-8").read()
if mode == "hang":
    time.sleep(60)
if mode == "cancel":
    sys.exit(1)
sys.stdout.buffer.write(open(os.path.join(here, "answer.bin"), "rb").read())
"""


@pytest.fixture
def fake_zenity(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "zenity"
    script.write_text(FAKE_ZENITY.format(python=sys.executable), encoding="utf-8")
    script.chmod(0o755)

    def configure(mode, answer=b""):
        (bindir / "mode.txt").write_text(mode, encoding="utf-8")
        (bindir / "answer.bin").write_bytes(answer)

    configure("answer")
    return script, configure


@posix_only
def test_real_process_answer(tmp_path, fake_zenity):
    script, configure = fake_zenity
    root = make_tree(tmp_path / "tree")
    configure("answer", f"{root / UMLAUT_ZIP}\n{root / 'b.ZIP'}\n".encode())

    def which(name):
        return str(script) if name == "zenity" else None

    picker = fp.Picker(lambda: fp.detect_native(platform="linux", env={"DISPLAY": ":0"}, which=which))
    result = picker.pick("zip", True)
    assert result["paths"] == [str(root / "\u00fc ERS.zip"), str(root / "b.ZIP")] and result["errors"] == []
    argv = (script.parent / "argv.txt").read_text(encoding="utf-8").split("\x1e")
    assert argv == fp.build_command(fp.NativeTool("zenity", str(script)), "zip", True)[1:]


@posix_only
def test_real_process_cancel_and_kill(fake_zenity):
    script, configure = fake_zenity
    picker = fp.Picker(lambda: fp.NativeTool("zenity", str(script)))
    configure("cancel")
    assert picker.pick("zip", True)["cancelled"] is True
    configure("hang")
    thread, box = run_in_thread(lambda: picker.pick("zip", True))
    deadline = time.monotonic() + 10
    while not picker.cancel():
        assert time.monotonic() < deadline
        time.sleep(0.02)
    thread.join(10)
    assert box["result"]["cancelled"] is True and box["result"]["timed_out"] is False


# --------------------------------------------------------------------------- Windows helper (CI Windows)


@windows_only
def test_windows_helper_structures_match_the_api():
    argv = fp.build_command(fp.NativeTool("windows-comdlg", sys.executable), "zip", True)
    argv[4] = "probe"
    done = subprocess.run(argv, capture_output=True, timeout=60, check=True)
    probe = json.loads(done.stdout.decode("ascii").strip())
    if probe["ptr"] == 8:
        assert (probe["ofn"], probe["bi"]) == (152, 64)
    else:
        assert (probe["ofn"], probe["bi"]) == (88, 32)


@windows_only
def test_windows_detection_uses_this_python():
    tool = fp.detect_native()
    assert tool is not None and tool.kind == "windows-comdlg" and tool.executable == sys.executable
