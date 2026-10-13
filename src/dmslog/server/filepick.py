"""Native file picker and built-in folder browser (task 9.16, ARQUITETURA 10.4).

A browser never reveals the full path of a file chosen with ``<input type="file">``, and copying
multi-GB ERS archives over HTTP is not an option. So the local server opens the operating system's
own dialog on the user's desktop and returns the chosen paths, and offers a read-only folder browser
(``list_dir``) that the viewer shows when there is no native dialog.

* Windows: a child ``python.exe -I -c <fixed script>`` calls ``GetOpenFileNameW`` (files) or
  ``SHBrowseForFolderW`` (folders) through ctypes. No PowerShell (blocked or flagged on locked-down
  PCs), nothing to install, and the child can be killed (cancel, timeout, shutdown).
* Linux: ``zenity`` or ``kdialog`` (``kdialog`` first on KDE), found at run time, only with a
  ``DISPLAY`` or ``WAYLAND_DISPLAY``. The XDG portal is not used: its answer is a D-Bus signal that
  ``gdbus call`` cannot wait for, and the stdlib has no D-Bus client.

Nothing the client sends becomes a process argument: the command lines are built from fixed values
(``kind`` and ``multiple`` only select between them). Every path returned by a dialog is validated
again (text, absolute, exists, ``.zip`` file or folder) before it reaches the client. One dialog at a
time; it is cancelled by ``Picker.cancel`` or after ``PICK_TIMEOUT_S``.
"""

from __future__ import annotations

import json
import ntpath
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import messages
from ..ingest.pipeline import is_reserved_case_name

IS_WINDOWS = os.name == "nt"

MAX_PATH_CHARS = 4096
MAX_LIST_ENTRIES = 5000  # entries returned by list_dir (folders + .zip files), then ``truncated``
MAX_SCAN_ENTRIES = 100_000  # directory entries looked at by list_dir, then ``truncated``
MAX_PICKED = 1000  # paths kept from one dialog answer
MAX_DIALOG_OUTPUT = 4 * 1024 * 1024  # bytes of dialog stdout accepted
PICK_TIMEOUT_S = 15 * 60  # an abandoned dialog is closed after this

KIND_ZIP = "zip"
KIND_FOLDER = "folder"
PICK_KINDS = (KIND_ZIP, KIND_FOLDER)

NATIVE_WINDOWS = "windows-comdlg"
NATIVE_ZENITY = "zenity"
NATIVE_KDIALOG = "kdialog"

_DEVICE_PREFIXES = ("\\\\?\\", "\\\\.\\", "\\??\\")  # \\?\  \\.\  \??\ (same policy as tools/bench.py)
_WINDOWS_BAD_CHARS = frozenset('<>"|?*')
_DRIVE_ONLY = re.compile(r"[A-Za-z]:\Z")
_DRIVE_ROOT = re.compile(r"[A-Za-z]:\\")
_FILE_ATTRIBUTE_HIDDEN = 0x2


class PathRejectedError(ValueError):
    """A client or dialog path that fails validation; ``code`` is the stable API error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class PickerError(Exception):
    """The native dialog could not be shown or answered something unreadable."""


class PickerBusyError(Exception):
    """Another dialog is open."""


# --------------------------------------------------------------------------- path validation


def _is_text(value: str) -> bool:
    """No control characters and no lone surrogates (undecodable file names)."""
    return not any(ord(ch) < 32 or ord(ch) == 0x7F or 0xD800 <= ord(ch) <= 0xDFFF for ch in value)


def check_path(raw: Any, *, windows: bool = IS_WINDOWS, allow_unc: bool = False) -> str:
    """Return the normalized absolute form of ``raw`` or raise ``PathRejectedError``.

    Refused: non-text, empty or longer than ``MAX_PATH_CHARS``, NUL or control characters, relative
    paths (and drive-relative ``C:foo`` on Windows). On Windows also device and extended-length
    prefixes (``\\\\?\\``, ``\\\\.\\``, ``\\??\\``), UNC paths unless ``allow_unc``, characters Windows
    forbids (``<>"|?*``, ``:`` after the drive) and reserved device names (``CON``, ``nul.txt``...) in
    any component. ``..`` and ``.`` are resolved lexically (symlinks are not resolved). ``windows``
    selects the rules (``ntpath``/``posixpath``), so both are testable on any OS.
    """
    if not isinstance(raw, str) or not raw or len(raw) > MAX_PATH_CHARS:
        raise PathRejectedError("invalid_path", messages.FS_BAD_PATH.format(limit=MAX_PATH_CHARS))
    if not _is_text(raw):
        raise PathRejectedError("invalid_path", messages.FS_PATH_NOT_TEXT)
    if windows:
        return _check_windows(raw, allow_unc)
    if not raw.startswith("/"):
        raise PathRejectedError("path_not_absolute", messages.FS_PATH_NOT_ABSOLUTE)
    path = posixpath.normpath(raw)
    if path.startswith("//"):  # POSIX keeps a leading "//"; it means "/" on Linux
        path = "/" + path.lstrip("/")
    return path


def _check_windows(raw: str, allow_unc: bool) -> str:
    text = raw.replace("/", "\\")
    if text.upper().startswith(_DEVICE_PREFIXES):
        raise PathRejectedError("device_path", messages.FS_DEVICE_PATH)
    unc = text.startswith("\\\\")
    if unc and not allow_unc:
        raise PathRejectedError("unc_path", messages.FS_UNC_PATH)
    if _DRIVE_ONLY.match(text):
        text += "\\"
    if not unc and not _DRIVE_ROOT.match(text):
        raise PathRejectedError("path_not_absolute", messages.FS_PATH_NOT_ABSOLUTE)
    path = ntpath.normpath(text)
    drive, rest = ntpath.splitdrive(path)
    if unc and (not drive or drive.count("\\") < 3):  # needs \\server\share
        raise PathRejectedError("path_not_absolute", messages.FS_PATH_NOT_ABSOLUTE)
    for part in rest.split("\\"):
        if not part:
            continue
        if ":" in part or any(ch in _WINDOWS_BAD_CHARS for ch in part):
            raise PathRejectedError("invalid_path", messages.FS_PATH_BAD_CHARS)
        if is_reserved_case_name(part.rstrip(" .")):
            raise PathRejectedError("reserved_name", messages.FS_RESERVED_NAME)
    return path


# --------------------------------------------------------------------------- folder browser


def _entry_json(entry: os.DirEntry[str], kind: str, windows: bool) -> dict[str, Any]:
    link = entry.is_symlink()
    size: int | None = None
    mtime: int | None = None
    hidden = entry.name.startswith(".")
    broken = False
    try:
        info = entry.stat()  # follows symlinks
    except FileNotFoundError:
        broken = True
    except OSError:
        info = None
    else:
        mtime = int(info.st_mtime * 1000)
        if kind == KIND_ZIP:
            size = info.st_size
        if windows:
            hidden = bool(getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_HIDDEN)
    return {
        "name": entry.name,
        "path": entry.path,
        "type": "dir" if kind == KIND_FOLDER else "zip",
        "size": size,
        "mtime": mtime,
        "link": link,
        "broken": broken,
        "hidden": hidden,
    }


def _classify(entry: os.DirEntry[str]) -> str | None:
    """KIND_FOLDER, KIND_ZIP or None (other files, broken links and unreadable entries are skipped)."""
    try:
        if entry.is_dir():  # follows symlinks: a link to a folder is a folder
            return KIND_FOLDER
        if entry.name.casefold().endswith(".zip") and entry.is_file():
            return KIND_ZIP
        if entry.is_symlink() and entry.name.casefold().endswith(".zip"):
            return KIND_ZIP  # broken link to an archive: listed and marked ``broken``
    except OSError:
        return None
    return None


def list_dir(
    raw_path: Any,
    *,
    windows: bool = IS_WINDOWS,
    limit: int = MAX_LIST_ENTRIES,
    scan_limit: int = MAX_SCAN_ENTRIES,
    shortcut_list: Callable[[], list[dict[str, str]]] | None = None,
) -> dict[str, Any]:
    """Folders and ``.zip`` files of one local folder; read-only (only names and ``stat``).

    Folders come first, then archives, each by case-insensitive name. Names that are not text
    (undecodable bytes, control characters) are counted in ``skipped`` and not returned, as their
    paths could not round-trip. Symbolic links are listed and marked (``link``; ``broken`` when the
    target is missing); following one is the user's choice. Raises ``PathRejectedError`` (bad path,
    ``path_not_found``, ``not_a_directory``, ``permission_denied``, ``path_unreadable``).
    """
    path = check_path(raw_path, windows=windows)
    try:
        st = os.stat(path)
    except FileNotFoundError as exc:
        raise PathRejectedError("path_not_found", messages.FS_NOT_FOUND) from exc
    except PermissionError as exc:
        raise PathRejectedError("permission_denied", messages.FS_PERMISSION_DENIED) from exc
    except OSError as exc:
        raise PathRejectedError("path_unreadable", messages.FS_UNREADABLE) from exc
    if not stat.S_ISDIR(st.st_mode):
        raise PathRejectedError("not_a_directory", messages.FS_NOT_A_DIRECTORY)

    found: list[tuple[int, str, str, dict[str, Any]]] = []
    skipped = 0
    other_files = 0
    truncated = False
    try:
        with os.scandir(path) as it:
            for scanned, entry in enumerate(it):
                if scanned >= scan_limit:
                    truncated = True
                    break
                kind = _classify(entry)
                if kind is None:
                    other_files += 1
                    continue
                if not _is_text(entry.name):
                    skipped += 1
                    continue
                order = 0 if kind == KIND_FOLDER else 1
                found.append((order, entry.name.casefold(), entry.name, _entry_json(entry, kind, windows)))
    except PermissionError as exc:
        raise PathRejectedError("permission_denied", messages.FS_PERMISSION_DENIED) from exc
    except OSError as exc:
        raise PathRejectedError("path_unreadable", messages.FS_UNREADABLE) from exc

    found.sort(key=lambda item: item[:3])
    if len(found) > limit:
        truncated = True
        found = found[:limit]
    pathmod = ntpath if windows else posixpath
    parent: str | None = pathmod.dirname(path)
    if parent == path or not parent:
        parent = None
    return {
        "path": path,
        "parent": parent,
        "entries": [item[3] for item in found],
        "truncated": truncated,
        "skipped": skipped,
        "other_files": other_files,
        "shortcuts": (shortcut_list or shortcuts)(),
    }


def _windows_known_folder(guid: str) -> str | None:
    """``SHGetKnownFolderPath`` (follows redirection to OneDrive or a network folder); None on failure."""
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        raw = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(guid).bytes_le)
        out = ctypes.c_void_p()
        shell32 = ctypes.WinDLL("shell32")
        ole32 = ctypes.WinDLL("ole32")
        shell32.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.HANDLE,
                                                 ctypes.POINTER(ctypes.c_void_p)]  # fmt: skip
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        if shell32.SHGetKnownFolderPath(ctypes.byref(raw), 0, None, ctypes.byref(out)) != 0:
            return None
        try:
            return ctypes.wstring_at(out.value)
        finally:
            ole32.CoTaskMemFree(out)
    except Exception:
        return None


_KNOWN_FOLDERS = {
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
}
_XDG_NAMES = {"desktop": "XDG_DESKTOP_DIR", "downloads": "XDG_DOWNLOAD_DIR", "documents": "XDG_DOCUMENTS_DIR"}
_DEFAULT_NAMES = {"desktop": "Desktop", "downloads": "Downloads", "documents": "Documents"}


def xdg_user_dirs(text: str, home: str) -> dict[str, str]:
    """Parse ``user-dirs.dirs`` (``XDG_DOWNLOAD_DIR="$HOME/Downloads"``); only ``$HOME/...`` or absolute."""
    found: dict[str, str] = {}
    wanted = {value: key for key, value in _XDG_NAMES.items()}
    for line in text.splitlines():
        name, sep, value = line.strip().partition("=")
        if not sep or name not in wanted:
            continue
        value = value.strip().strip('"')
        if value.startswith("$HOME/"):
            value = posixpath.join(home, value[len("$HOME/") :])
        elif not value.startswith("/"):
            continue
        found[wanted[name]] = value
    return found


def _windows_drives() -> list[str]:
    try:
        import ctypes

        mask = ctypes.WinDLL("kernel32").GetLogicalDrives()
    except Exception:
        return []
    return [f"{chr(65 + n)}:\\" for n in range(26) if mask & (1 << n)]


def shortcuts(env: Mapping[str, str] | None = None, windows: bool = IS_WINDOWS) -> list[dict[str, str]]:
    """Starting points for the browser: home, desktop, downloads, documents, then drives (Windows) or
    ``/``, ``/media``, ``/mnt``, ``/run/media/<user>`` (Linux). Only folders that exist; drives are not
    probed (an empty card reader or a slow mapped drive could hang)."""
    env = os.environ if env is None else env
    home = os.path.expanduser("~")
    out: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(kind: str, path: str | None, probe: bool = True) -> None:
        if not path:
            return
        path = os.path.normpath(path)  # "$HOME/" in user-dirs.dirs is the home itself
        if path in seen:
            return
        if probe and not os.path.isdir(path):
            return
        seen.add(path)
        out.append({"kind": kind, "path": path})

    add("home", home)
    if windows:
        for kind, guid in _KNOWN_FOLDERS.items():
            add(kind, _windows_known_folder(guid) or os.path.join(home, _DEFAULT_NAMES[kind]))
        for drive in _windows_drives():
            add("drive", drive, probe=False)
        return out
    config = env.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    try:
        text = Path(config, "user-dirs.dirs").read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        text = ""
    xdg = xdg_user_dirs(text, home)
    for kind in _KNOWN_FOLDERS:
        add(kind, xdg.get(kind) or os.path.join(home, _DEFAULT_NAMES[kind]))
    add("root", "/")
    add("media", "/media")
    add("media", "/mnt")
    user = env.get("USER") or env.get("LOGNAME")
    if user and "/" not in user:
        add("media", f"/run/media/{user}")
    return out


# --------------------------------------------------------------------------- native dialog


@dataclass(frozen=True, slots=True)
class NativeTool:
    kind: str  # NATIVE_WINDOWS | NATIVE_ZENITY | NATIVE_KDIALOG
    executable: str


def detect_native(
    *,
    platform: str = sys.platform,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    executable: str | None = None,
) -> NativeTool | None:
    """The dialog available here, or None. Windows: this Python (ctypes is in the embeddable build).
    Linux and other POSIX: a display plus ``zenity`` or ``kdialog`` (``kdialog`` first on KDE)."""
    env = os.environ if env is None else env
    if platform == "win32":
        exe = sys.executable if executable is None else executable
        return NativeTool(NATIVE_WINDOWS, exe) if exe else None
    if platform == "darwin":
        return None  # not a target (Windows and Linux)
    if not (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        return None
    order = [NATIVE_ZENITY, NATIVE_KDIALOG]
    if "KDE" in (env.get("XDG_CURRENT_DESKTOP") or "").upper():
        order.reverse()
    for name in order:
        found = which(name)
        if found:
            return NativeTool(name, found)
    return None


# Runs in a child "python -I -c" on Windows. argv: mode ("zip" | "zip-multi" | "folder" | "probe"),
# title, filter label, all-files label (fixed server texts). Prints one ASCII JSON line:
# {"status": "ok", "buffer": "..."} | {"status": "cancel"} | {"status": "error", "code": n}.
WINDOWS_DIALOG_SCRIPT = r"""
import ctypes, json, sys
from ctypes import wintypes as w

def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

class OPENFILENAMEW(ctypes.Structure):
    _fields_ = [("lStructSize", w.DWORD), ("hwndOwner", w.HWND), ("hInstance", w.HINSTANCE),
                ("lpstrFilter", ctypes.c_void_p), ("lpstrCustomFilter", ctypes.c_void_p),
                ("nMaxCustFilter", w.DWORD), ("nFilterIndex", w.DWORD), ("lpstrFile", ctypes.c_void_p),
                ("nMaxFile", w.DWORD), ("lpstrFileTitle", ctypes.c_void_p), ("nMaxFileTitle", w.DWORD),
                ("lpstrInitialDir", ctypes.c_void_p), ("lpstrTitle", ctypes.c_void_p), ("Flags", w.DWORD),
                ("nFileOffset", w.WORD), ("nFileExtension", w.WORD), ("lpstrDefExt", ctypes.c_void_p),
                ("lCustData", w.LPARAM), ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", ctypes.c_void_p),
                ("pvReserved", ctypes.c_void_p), ("dwReserved", w.DWORD), ("FlagsEx", w.DWORD)]

class BROWSEINFOW(ctypes.Structure):
    _fields_ = [("hwndOwner", w.HWND), ("pidlRoot", ctypes.c_void_p), ("pszDisplayName", ctypes.c_void_p),
                ("lpszTitle", ctypes.c_void_p), ("ulFlags", w.UINT), ("lpfn", ctypes.c_void_p),
                ("lParam", w.LPARAM), ("iImage", ctypes.c_int)]

mode = sys.argv[1]
title, filter_label, all_label = sys.argv[2], sys.argv[3], sys.argv[4]
comdlg32 = ctypes.WinDLL("comdlg32")
shell32 = ctypes.WinDLL("shell32")
ole32 = ctypes.WinDLL("ole32")
user32 = ctypes.WinDLL("user32")
comdlg32.GetOpenFileNameW.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
comdlg32.GetOpenFileNameW.restype = w.BOOL
comdlg32.CommDlgExtendedError.restype = w.DWORD
shell32.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFOW)]
shell32.SHBrowseForFolderW.restype = ctypes.c_void_p
shell32.SHGetPathFromIDListEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p, w.DWORD, ctypes.c_int]
shell32.SHGetPathFromIDListEx.restype = w.BOOL
ole32.OleInitialize.argtypes = [ctypes.c_void_p]
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
user32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID]
user32.CreateWindowExW.restype = w.HWND
user32.DestroyWindow.argtypes = [w.HWND]
user32.SetForegroundWindow.argtypes = [w.HWND]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
if mode == "probe":
    out({"status": "probe", "ofn": ctypes.sizeof(OPENFILENAMEW), "bi": ctypes.sizeof(BROWSEINFOW),
         "ptr": ctypes.sizeof(ctypes.c_void_p)})
    sys.exit(0)
try:
    user32.SetProcessDPIAware()
except Exception:
    pass
ole32.OleInitialize(None)
# A hidden topmost owner, centered on the primary screen: the dialog opens above the browser.
cx, cy = user32.GetSystemMetrics(0) // 2, user32.GetSystemMetrics(1) // 2
owner = user32.CreateWindowExW(0x8 | 0x80, "STATIC", title, 0x80000000, cx, cy, 0, 0, None, None, None, None)
if owner:
    user32.SetForegroundWindow(owner)
try:
    if mode == "folder":
        name = ctypes.create_unicode_buffer(32768)
        title_buf = ctypes.create_unicode_buffer(title)
        bi = BROWSEINFOW()
        bi.hwndOwner = owner
        bi.pszDisplayName = ctypes.addressof(name)
        bi.lpszTitle = ctypes.addressof(title_buf)
        bi.ulFlags = 0x1 | 0x40 | 0x200  # RETURNONLYFSDIRS | NEWDIALOGSTYLE | NONEWFOLDERBUTTON
        pidl = shell32.SHBrowseForFolderW(ctypes.byref(bi))
        if not pidl:
            out({"status": "cancel"})
        else:
            path = ctypes.create_unicode_buffer(32768)
            ok = shell32.SHGetPathFromIDListEx(pidl, ctypes.addressof(path), 32768, 0)
            ole32.CoTaskMemFree(pidl)
            out({"status": "ok", "buffer": path.value} if ok else {"status": "error", "code": -1})
    else:
        size = 1 << 18
        buf = ctypes.create_unicode_buffer(size)
        flt = ctypes.create_unicode_buffer(filter_label + "\0*.zip\0" + all_label + "\0*.*\0\0")
        title_buf = ctypes.create_unicode_buffer(title)
        ofn = OPENFILENAMEW()
        ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
        ofn.hwndOwner = owner
        ofn.lpstrFilter = ctypes.addressof(flt)
        ofn.nFilterIndex = 1
        ofn.lpstrFile = ctypes.addressof(buf)
        ofn.nMaxFile = size
        ofn.lpstrTitle = ctypes.addressof(title_buf)
        # EXPLORER | FILEMUSTEXIST | PATHMUSTEXIST | HIDEREADONLY | NOCHANGEDIR | DONTADDTORECENT
        ofn.Flags = 0x80000 | 0x1000 | 0x800 | 0x4 | 0x8 | 0x02000000
        if mode == "zip-multi":
            ofn.Flags |= 0x200  # ALLOWMULTISELECT
        if comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
            raw = ctypes.wstring_at(ctypes.addressof(buf), size)
            end = raw.find("\0\0")
            out({"status": "ok", "buffer": raw[: end if end >= 0 else size]})
        else:
            code = comdlg32.CommDlgExtendedError()
            out({"status": "cancel"} if code == 0 else {"status": "error", "code": code})
finally:
    if owner:
        user32.DestroyWindow(owner)
"""


def build_command(tool: NativeTool, kind: str, multiple: bool) -> list[str]:
    """The fixed argument list for one dialog; only ``kind``/``multiple`` choose among constants."""
    if kind not in PICK_KINDS:
        raise ValueError(kind)
    title = messages.FS_DIALOG_TITLE_FOLDER if kind == KIND_FOLDER else messages.FS_DIALOG_TITLE_ZIP
    if tool.kind == NATIVE_WINDOWS:
        mode = KIND_FOLDER if kind == KIND_FOLDER else ("zip-multi" if multiple else KIND_ZIP)
        zip_label = f"{messages.FS_DIALOG_FILTER_ZIP} (*.zip)"
        all_label = f"{messages.FS_DIALOG_FILTER_ALL} (*.*)"
        return [tool.executable, "-I", "-c", WINDOWS_DIALOG_SCRIPT, mode, title, zip_label, all_label]
    if tool.kind == NATIVE_ZENITY:
        argv = [tool.executable, "--file-selection", f"--title={title}", "--separator=\n"]
        if kind == KIND_FOLDER:
            argv.append("--directory")
        else:
            argv += [f"--file-filter={messages.FS_DIALOG_FILTER_ZIP} | *.zip *.ZIP *.Zip",
                     f"--file-filter={messages.FS_DIALOG_FILTER_ALL} | *"]  # fmt: skip
            if multiple:
                argv.append("--multiple")
        return argv
    if tool.kind == NATIVE_KDIALOG:
        home = os.path.expanduser("~")
        if kind == KIND_FOLDER:
            return [tool.executable, "--title", title, "--getexistingdirectory", home]
        argv = [tool.executable, "--title", title, "--getopenfilename", home,
                f"{messages.FS_DIALOG_FILTER_ZIP} (*.zip *.ZIP *.Zip)"]  # fmt: skip
        if multiple:
            argv += ["--multiple", "--separate-output"]
        return argv
    raise ValueError(tool.kind)


def parse_comdlg_buffer(buffer: str) -> list[str]:
    """``GetOpenFileNameW`` answer up to the double NUL: one full path, or a folder then file names."""
    parts = [part for part in buffer.split("\0") if part]
    if len(parts) <= 1:
        return parts
    folder = parts[0]
    return [ntpath.join(folder, name) for name in parts[1:]]


def parse_lines(stdout: bytes) -> list[str]:
    """One path per line (zenity with ``--separator=\\n``, kdialog with ``--separate-output``).

    Decoded with the file system encoding (undecodable bytes stay as surrogates and are refused by
    validation). A trailing CR is dropped; empty lines are ignored."""
    paths = []
    for line in stdout.split(b"\n"):
        line = line.rstrip(b"\r")
        if line:
            paths.append(os.fsdecode(line))
    return paths


@dataclass(frozen=True, slots=True)
class DialogAnswer:
    paths: list[str]
    cancelled: bool


def parse_answer(tool_kind: str, returncode: int, stdout: bytes) -> DialogAnswer:
    """Map a dialog's exit code and stdout to paths / cancelled, or raise ``PickerError``."""
    if len(stdout) > MAX_DIALOG_OUTPUT:
        raise PickerError("dialog output too large")
    if tool_kind == NATIVE_WINDOWS:
        if returncode != 0:
            raise PickerError(f"dialog helper exited with {returncode}")
        try:
            answer = json.loads(stdout.decode("ascii").strip().splitlines()[-1])
        except (UnicodeDecodeError, ValueError, IndexError) as exc:
            raise PickerError("unreadable dialog answer") from exc
        status = answer.get("status") if isinstance(answer, dict) else None
        if status == "cancel":
            return DialogAnswer([], True)
        if status == "ok" and isinstance(answer.get("buffer"), str):
            paths = parse_comdlg_buffer(answer["buffer"])
            return DialogAnswer(paths, not paths)
        raise PickerError(f"dialog error {answer.get('code') if isinstance(answer, dict) else None}")
    if tool_kind in (NATIVE_ZENITY, NATIVE_KDIALOG):
        if returncode == 1:
            return DialogAnswer([], True)
        if returncode != 0:
            raise PickerError(f"{tool_kind} exited with {returncode}")
        paths = parse_lines(stdout)
        return DialogAnswer(paths, not paths)
    raise PickerError(f"unknown dialog {tool_kind}")


def validate_picked(
    paths: Iterable[str], kind: str, *, windows: bool = IS_WINDOWS
) -> tuple[list[str], list[dict[str, str]]]:
    """Keep the dialog paths that are what was asked for; the rest become per-item errors.

    The user chose them, so UNC paths are accepted here (not in the browser). Duplicates are dropped
    and at most ``MAX_PICKED`` are kept."""
    ok: list[str] = []
    errors: list[dict[str, str]] = []
    seen: set[str] = set()

    def fail(path: str, code: str, message: str) -> None:
        errors.append({"path": path, "error": code, "message": message})

    for raw in paths:
        if len(ok) + len(errors) >= MAX_PICKED:
            break
        shown = raw if _is_text(raw) else raw.encode("utf-8", "replace").decode("utf-8")
        try:
            path = check_path(raw, windows=windows, allow_unc=True)
        except PathRejectedError as exc:
            fail(shown, exc.code, exc.message)
            continue
        key = path.casefold() if windows else path
        if key in seen:
            continue
        seen.add(key)
        try:
            st = os.stat(path)
        except FileNotFoundError:
            fail(path, "path_not_found", messages.FS_NOT_FOUND)
            continue
        except OSError:
            fail(path, "path_unreadable", messages.FS_UNREADABLE)
            continue
        if kind == KIND_FOLDER:
            if not stat.S_ISDIR(st.st_mode):
                fail(path, "not_a_directory", messages.FS_NOT_A_DIRECTORY)
                continue
        elif not stat.S_ISREG(st.st_mode) or not path.casefold().endswith(".zip"):
            fail(path, "not_a_zip", messages.FS_NOT_A_ZIP)
            continue
        ok.append(path)
    return ok, errors


# --------------------------------------------------------------------------- picker (one dialog at a time)


class Picker:
    """Opens the native dialog in a child process; one at a time, cancellable, with a timeout.

    ``pick`` blocks the calling thread (an HTTP handler thread of the ThreadingHTTPServer: the rest of
    the server keeps answering) until the user answers, ``cancel`` is called or the timeout passes.
    """

    def __init__(
        self,
        detect: Callable[[], NativeTool | None] = detect_native,
        *,
        timeout: float = PICK_TIMEOUT_S,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        windows: bool = IS_WINDOWS,
    ) -> None:
        self._detect = detect
        self._timeout = timeout
        self._popen = popen
        self._windows = windows
        self._busy = threading.Lock()
        self._state = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._cancel_requested = False
        self._closed = False

    def capabilities(self) -> dict[str, Any]:
        tool = self._detect()
        platform = "other"
        if sys.platform == "win32":
            platform = "windows"
        elif sys.platform.startswith("linux"):
            platform = "linux"
        return {
            "native": {"available": tool is not None, "kind": tool.kind if tool else None},
            "browse": True,
            "platform": platform,
            "kinds": list(PICK_KINDS),
        }

    def pick(self, kind: str, multiple: bool) -> dict[str, Any]:
        """``{paths, cancelled, timed_out, errors, truncated}``; raises PickerError / PickerBusyError."""
        tool = self._detect()
        if tool is None:
            raise PickerError("no native dialog")
        if not self._busy.acquire(blocking=False):
            raise PickerBusyError
        try:
            answer, timed_out = self._run(tool, build_command(tool, kind, multiple))
            raw = answer.paths if kind == KIND_ZIP and multiple else answer.paths[:1]
            paths, errors = validate_picked(raw, kind, windows=self._windows)
            return {
                "paths": paths,
                "cancelled": answer.cancelled,
                "timed_out": timed_out,
                "errors": errors,
                "truncated": len(raw) > MAX_PICKED,
            }
        finally:
            self._busy.release()

    def _run(self, tool: NativeTool, argv: list[str]) -> tuple[DialogAnswer, bool]:
        kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
                                  "stderr": subprocess.DEVNULL, "shell": False}  # fmt: skip
        if self._windows:
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with self._state:
            if self._closed:
                return DialogAnswer([], True), False
            self._cancel_requested = False
            try:
                proc = self._popen(argv, **kwargs)
            except OSError as exc:
                raise PickerError("dialog could not start") from exc
            self._proc = proc
        timed_out = False
        try:
            try:
                stdout, _ = proc.communicate(timeout=self._timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                stdout, _ = proc.communicate()
        finally:
            with self._state:
                self._proc = None
                cancelled = self._cancel_requested
        if timed_out or cancelled:
            return DialogAnswer([], True), timed_out
        return parse_answer(tool.kind, proc.returncode, stdout or b""), False

    def cancel(self) -> bool:
        """Close the open dialog (kill its process); True when there was one."""
        with self._state:
            proc = self._proc
            if proc is None:
                return False
            self._cancel_requested = True
        try:
            proc.kill()
        except OSError:
            pass
        return True

    def close(self) -> None:
        """Server shutdown: cancel the open dialog and refuse new ones."""
        with self._state:
            self._closed = True
        self.cancel()
