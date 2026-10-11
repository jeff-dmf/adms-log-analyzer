"""Guard: the knowledge pack never enters the repository nor a public artifact (ARQUITETURA D10).

The pack is derived from product documentation. The pack is recognised by its CONTENT, whatever the
file name, extension or folder (a renamed manifest does not escape). The check fails (exit 1) when:

* a tracked file lives under ``src/dmslog/knowledge/pack/``;
* a tracked file (index content, not the filtered checkout) is a pack manifest, a pack data file
  (``adapters``/``error_codes``/``operations``/``parameters`` shape of ``pack_schema.py``), or a folder
  holds the four data files together;
* a tracked file is a Git LFS pointer, or ``.gitattributes`` uses ``filter=lfs`` (the real content could
  not be inspected);
* a tracked or built archive (``.zip``/``.pyz``/``.whl``/``.tar*``/``.tgz``/gzip/bzip2/xz, detected by
  name or magic), including archives nested inside archives, contains any of the above or a
  ``knowledge/pack`` entry;
* something cannot be inspected: unreadable or corrupt archive, depth/member/size limit exceeded.
  Failing is the safe default.

The only exception is the synthetic test fixture ``tests/fixtures/knowledge/mini_pack/`` (its manifest
notice starts with ``SYNTHETIC TEST FIXTURE``), tracked in that exact folder.

Usage:  python packaging/check_no_pack.py [--root REPO] [--artifact PATH ...]
"""
from __future__ import annotations

import argparse
import bz2
import codecs
import gzip
import io
import json
import lzma
import re
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath


def _norm(key: object) -> str:
    """Key spelling folded for comparison: case, spaces, hyphens and underscores do not matter."""
    return re.sub(r"[\s_\-]+", "", str(key)).lower()


ROOT = Path(__file__).resolve().parent.parent
PACK_PATH = "src/dmslog/knowledge/pack/"
SYNTHETIC_DIR = "tests/fixtures/knowledge/mini_pack"
SYNTHETIC_FIXTURE = SYNTHETIC_DIR + "/manifest.json"
SYNTHETIC_MARK = "SYNTHETIC TEST FIXTURE"
MANIFEST_KEYS = {_norm(k) for k in ("schema_version", "generator_version", "notice")}
# Structure of the real manifest: schema/generator versions, the confidentiality notice and the digest
# map of source notes. Any two of the structural keys plus a "notice" or the full triple is a manifest.
MANIFEST_SHAPE = {_norm(k) for k in ("schema_version", "generator_version", "vault_commit", "files")}
NOTICE_PHRASES = ("derived from Schneider Electric documentation", "do not publish this pack")
DATA_FILES = ("adapters.json", "error_codes.json", "operations.json", "parameters.json")
KEY_NOTICE = _norm("notice")
KEY_ADAPTERS = _norm("adapters")
KEY_PROCESS_MAP = _norm("process_map")
LFS_HEADER = b"version https://git-lfs.github.com/spec/v1"

# Limits of the archive inspector. Exceeding any of them fails the check (fail-safe).
MAX_DEPTH = 4
MAX_MEMBERS = 20_000
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_FILE_BYTES = MAX_MEMBER_BYTES

ARCHIVE_SUFFIXES = (
    ".zip", ".pyz", ".pyzw", ".whl", ".jar", ".tar", ".tgz", ".gz", ".bz2", ".xz", ".tbz2", ".txz",
)


class Uninspectable(Exception):
    """Content that cannot be inspected; reported as a problem (fail-safe)."""


def _field_signatures() -> dict[str, set[str]]:
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from dmslog.knowledge import pack_schema as schema
    finally:
        sys.path.pop(0)
    return {
        _norm("adapters"): {_norm(f) for f in schema._ADAPTER_FIELDS},
        _norm("error_codes"): {_norm(f) for f in schema._ERROR_FIELDS},
        _norm("operations"): {_norm(f) for f in schema._OPERATION_FIELDS},
        _norm("parameters"): {_norm(f) for f in schema._PARAMETER_FIELDS},
    }


_SIGNATURES: dict[str, set[str]] | None = None


def unnormalized_constants() -> list[str]:
    """Names of key/section constants that are not stored in normalised form (must be empty).

    Every lookup folds the data with ``_norm`` and compares it with these constants, so one that is
    not already folded can never match (it silently disables a check).
    """
    bad = [name for name, value in (("KEY_NOTICE", KEY_NOTICE), ("KEY_ADAPTERS", KEY_ADAPTERS),
                                    ("KEY_PROCESS_MAP", KEY_PROCESS_MAP)) if _norm(value) != value]
    bad += [f"MANIFEST_KEYS:{k}" for k in MANIFEST_KEYS if _norm(k) != k]
    bad += [f"MANIFEST_SHAPE:{k}" for k in MANIFEST_SHAPE if _norm(k) != k]
    for section, fields in _signatures().items():
        bad += [f"section:{section}"] if _norm(section) != section else []
        bad += [f"{section}.{f}" for f in fields if _norm(f) != f]
    return bad


def _signatures() -> dict[str, set[str]]:
    global _SIGNATURES
    if _SIGNATURES is None:
        _SIGNATURES = _field_signatures()
    return _SIGNATURES


_BOMS = (
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xef\xbb\xbf", "utf-8"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)
_STRIP = "\ufeff \t\r\n\x0b\x0c"
_BLOCK = 4096


def _encoding(data: bytes) -> str:
    """Encoding of JSON-like bytes: BOM first, then the NUL pattern of BOM-less UTF-16/32, else UTF-8."""
    for bom, name in _BOMS:
        if data.startswith(bom):
            return name
    head = data[:4]
    if len(head) >= 4:
        if head[:3] == b"\x00\x00\x00" and head[3]:
            return "utf-32-be"
        if head[0] and head[1:] == b"\x00\x00\x00":
            return "utf-32-le"
    if len(head) >= 2:
        if head[0] == 0 and head[1]:
            return "utf-16-be"
        if head[0] and head[1] == 0:
            return "utf-16-le"
    return "utf-8"


def _first_significant(data: bytes, encoding: str) -> str:
    """First non-blank character of ``data`` ("" when there is none).

    The blank prefix may be arbitrarily long, so it is walked in blocks until a character shows up;
    a binary or an ordinary text file stops at the first block.
    """
    decoder = codecs.getincrementaldecoder(encoding)(errors="ignore")
    for start in range(0, len(data), _BLOCK):
        text = decoder.decode(data[start : start + _BLOCK]).lstrip(_STRIP)
        if text:
            return text[0]
    return ""


def _json_like_text(data: bytes) -> str | None:
    """Decoded text of ``data`` when it looks like JSON (first non-blank character ``{`` or ``[``).

    Only a small head is decoded to decide, so binaries and unrelated files cost nothing.
    """
    encoding = _encoding(data)
    if _first_significant(data, encoding) not in ("{", "["):
        return None
    try:
        return data.decode(encoding).lstrip(_STRIP)
    except UnicodeDecodeError:
        return data.decode(encoding, errors="ignore").lstrip(_STRIP)


if unnormalized_constants():  # a programming error, never a data condition
    raise RuntimeError(f"check_no_pack: constants not normalised: {unnormalized_constants()}")


def _norm_keys(obj: dict) -> dict[str, object]:
    return {_norm(key): value for key, value in obj.items()}


def _parse_object(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _json_object(data: bytes) -> dict | None:
    text = _json_like_text(data)
    return None if text is None else _parse_object(text)


_NOTICE_LOWER = tuple(phrase.lower() for phrase in NOTICE_PHRASES)


def _is_manifest(value: dict) -> bool:
    folded = _norm_keys(value)
    keys = set(folded)
    notice = folded.get(KEY_NOTICE)
    if isinstance(notice, str) and any(phrase in notice.lower() for phrase in _NOTICE_LOWER):
        return True
    if MANIFEST_KEYS <= keys:
        return True
    return KEY_NOTICE in keys and len(MANIFEST_SHAPE & keys) >= 2


def _threshold(fields: set[str]) -> int:
    return max(3, int(len(fields) * 0.7))


def _is_data_file(value: dict) -> bool:
    folded = _norm_keys(value)
    for key, fields in _signatures().items():
        rows = folded.get(key)
        if not isinstance(rows, list):
            continue
        wanted = fields
        if not rows:
            if key == KEY_ADAPTERS and isinstance(folded.get(KEY_PROCESS_MAP), dict):
                return True
            continue
        for row in rows[:5]:
            if isinstance(row, dict) and len(wanted & set(_norm_keys(row))) >= _threshold(wanted):
                return True
    return False


def _quoted_keys(text: str) -> set[str]:
    return {_norm(m) for m in re.findall(r'"((?:[^"\\]|\\.){1,64})"\s*:', text)}


def _text_kind(text: str) -> str | None:
    """Structural check by text for JSON-like content that does not parse (truncated, damaged)."""
    lowered = text.lower()
    if any(phrase in lowered for phrase in _NOTICE_LOWER):
        return "knowledge pack manifest"
    keys = _quoted_keys(text)
    if len(keys & (MANIFEST_KEYS | MANIFEST_SHAPE)) >= 2:
        return "knowledge pack manifest"
    for key, fields in _signatures().items():
        wanted = fields
        if key in keys and len(wanted & keys) >= _threshold(wanted):
            return "knowledge pack data file"
    return None


def pack_content_kind(data: bytes) -> str | None:
    """What pack content ``data`` is (by structure, not by name), or None."""
    text = _json_like_text(data)
    if text is None:
        return None
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):
        return _text_kind(text)
    if not isinstance(value, dict):
        return None
    if _is_manifest(value):
        return "knowledge pack manifest"
    if _is_data_file(value):
        return "knowledge pack data file"
    return None


def looks_like_pack_manifest(data: bytes) -> bool:
    return pack_content_kind(data) == "knowledge pack manifest"


def is_synthetic(data: bytes) -> bool:
    value = _json_object(data)
    notice = _norm_keys(value).get(KEY_NOTICE, "") if value else ""
    return isinstance(notice, str) and notice.startswith(SYNTHETIC_MARK)


def tracked_files(root: Path = ROOT) -> list[str]:
    """Tracked paths (POSIX), submodule entries excluded (there is nothing of ours to read)."""
    out = subprocess.run(
        ["git", "ls-files", "-s", "-z"], cwd=root, check=True, capture_output=True
    ).stdout.decode("utf-8")
    names = []
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, _, name = entry.partition("\t")
        if meta.split(" ", 1)[0] != "160000":
            names.append(name)
    return names


@contextmanager
def git_index_reader(root: Path = ROOT):
    """``read(name)`` returning the blob staged in the index (the raw content, no smudge filter)."""
    proc = subprocess.Popen(
        ["git", "cat-file", "--batch"], cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )

    def read(name: str) -> bytes:
        if "\n" in name:
            raise Uninspectable("path contains a newline")
        proc.stdin.write(f":{name}\n".encode())
        proc.stdin.flush()
        header = proc.stdout.readline().split()
        if len(header) != 3 or header[1] != b"blob":
            raise Uninspectable("git cannot read the index entry")
        size = int(header[2])
        if size > MAX_FILE_BYTES:
            remaining = size + 1
            while remaining:
                chunk = proc.stdout.read(min(remaining, 1 << 20))
                if not chunk:
                    break
                remaining -= len(chunk)
            raise Uninspectable(f"larger than {MAX_FILE_BYTES} bytes, cannot be inspected")
        data = proc.stdout.read(size)
        proc.stdout.read(1)
        return data

    try:
        yield read
    finally:
        proc.stdin.close()
        proc.wait()
        proc.stdout.close()


class _Budget:
    def __init__(self) -> None:
        self.members = 0
        self.total = 0

    def take(self, size: int) -> None:
        self.members += 1
        self.total += size
        if self.members > MAX_MEMBERS:
            raise Uninspectable(f"more than {MAX_MEMBERS} members")
        if self.total > MAX_TOTAL_BYTES:
            raise Uninspectable(f"more than {MAX_TOTAL_BYTES} bytes once unpacked")


def _has_archive_suffix(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith(ARCHIVE_SUFFIXES) or ".tar." in lowered


def _bounded(stream, limit: int = MAX_MEMBER_BYTES) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise Uninspectable(f"a member is larger than {limit} bytes")
    return data


def _iter_archive(name: str, data: bytes, budget: _Budget) -> Iterator[tuple[str, bytes]] | None:
    """Members of ``data`` when it is an archive (by magic or by name); None when it is not one.

    Raises Uninspectable on corrupt archives and on limits.
    """
    named = _has_archive_suffix(name)
    if len(data) >= 22 and zipfile.is_zipfile(io.BytesIO(data)):
        return _zip_members(data, budget)
    compressed = data[:2] == b"\x1f\x8b" or data[:3] == b"BZh" or data[:6] == b"\xfd7zXZ\x00"
    is_tar = data[257:262] == b"ustar"
    if compressed or is_tar:
        return _stream_members(name, data, budget)
    if named and data:
        raise Uninspectable("named like an archive but is not a readable one")
    return None


def _zip_members(data: bytes, budget: _Budget) -> Iterator[tuple[str, bytes]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        infos = [info for info in archive.infolist() if not info.is_dir()]
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError, NotImplementedError) as exc:
        raise Uninspectable(f"unreadable zip ({exc})") from exc
    for info in infos:
        budget.take(info.file_size)
        if info.file_size > MAX_MEMBER_BYTES:
            raise Uninspectable(f"member {info.filename} is larger than {MAX_MEMBER_BYTES} bytes")
        try:
            with archive.open(info) as handle:
                yield info.filename, _bounded(handle)
        except Uninspectable:
            raise
        except (zipfile.BadZipFile, OSError, ValueError, RuntimeError, NotImplementedError, EOFError) as exc:
            raise Uninspectable(f"unreadable zip member {info.filename} ({exc})") from exc


def _stream_members(name: str, data: bytes, budget: _Budget) -> Iterator[tuple[str, bytes]]:
    try:
        tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:*")  # noqa: SIM115
    except tarfile.TarError:
        tar = None
    except (OSError, EOFError, ValueError, lzma.LZMAError) as exc:
        raise Uninspectable(f"unreadable archive ({exc})") from exc
    if tar is not None:
        with tar:
            try:
                for member in tar:
                    if not member.isfile():
                        continue
                    budget.take(member.size)
                    if member.size > MAX_MEMBER_BYTES:
                        raise Uninspectable(f"member {member.name} is larger than {MAX_MEMBER_BYTES} bytes")
                    handle = tar.extractfile(member)
                    yield member.name, (_bounded(handle) if handle else b"")
            except Uninspectable:
                raise
            except (tarfile.TarError, OSError, EOFError, ValueError, lzma.LZMAError) as exc:
                raise Uninspectable(f"unreadable tar ({exc})") from exc
        return
    opener = gzip.open if data[:2] == b"\x1f\x8b" else bz2.open if data[:3] == b"BZh" else lzma.open
    if data[:2] != b"\x1f\x8b" and data[:3] != b"BZh" and data[:6] != b"\xfd7zXZ\x00":
        raise Uninspectable("unreadable archive (not a tar nor a compressed stream)")
    try:
        with opener(io.BytesIO(data)) as handle:
            payload = _bounded(handle)
    except Uninspectable:
        raise
    except (OSError, EOFError, ValueError, lzma.LZMAError) as exc:
        raise Uninspectable(f"unreadable compressed stream ({exc})") from exc
    budget.take(len(payload))
    inner = PurePosixPath(name).name
    for suffix in (".gz", ".bz2", ".xz", ".tgz"):
        if inner.lower().endswith(suffix):
            inner = inner[: -len(suffix)]
    yield inner or "content", payload


def _describe(label: str | None, name: str, what: str) -> str:
    return f"{name}: tracked {what}" if label is None else f"{label}: contains {what} ({name})"


def _set_problems(label: str | None, names: Iterable[str], allowed_dirs: set[str] = frozenset()) -> list[str]:
    """Folders (inside one container) that hold the four pack data files together."""
    by_dir: dict[str, set[str]] = {}
    for name in names:
        path = PurePosixPath(name)
        by_dir.setdefault(str(path.parent), set()).add(path.name)
    problems = []
    for folder, files in sorted(by_dir.items()):
        if folder in allowed_dirs or not set(DATA_FILES) <= files:
            continue
        shown = f"{folder}/" if folder != "." else ""
        listed = shown + "{" + ",".join(DATA_FILES) + "}"
        problems.append(_describe(label, listed, "knowledge pack data file set"))
    return problems


def _inspect(
    label: str | None, name: str, data: bytes, depth: int, budget: _Budget, problems: list[str]
) -> None:
    """Content rules for one blob, descending into archives. ``label`` None = tracked, top level."""
    if data.startswith(LFS_HEADER):
        problems.append(_describe(label, name, "Git LFS pointer (real content not inspectable)"))
        return
    if PurePosixPath(name).name == ".gitattributes" and b"filter=lfs" in data:
        problems.append(_describe(label, name, "Git LFS filter"))
    try:
        members = _iter_archive(name, data, budget)
        if members is None:
            kind = pack_content_kind(data)
            if kind:
                problems.append(_describe(label, name, kind))
            return
        if depth >= MAX_DEPTH:
            raise Uninspectable(f"archives nested deeper than {MAX_DEPTH} levels")
        inner_label = name if label is None else f"{label}!{name}"
        listing: list[str] = []
        for member_name, member_data in members:
            listing.append(member_name)
            parts = PurePosixPath(member_name).parts
            if any(parts[i : i + 2] == ("knowledge", "pack") for i in range(len(parts) - 1)):
                problems.append(f"{inner_label}: contains {member_name}")
                continue
            _inspect(inner_label, member_name, member_data, depth + 1, budget, problems)
        problems.extend(_set_problems(inner_label, listing))
    except Uninspectable as exc:
        shown = name if label is None else f"{label}!{name}"
        problems.append(f"{shown}: cannot be inspected ({exc})")


def tracked_problems(names: Iterable[str], read: Callable[[str], bytes]) -> list[str]:
    """Problems among tracked ``names`` (POSIX paths); ``read(name)`` returns the file bytes.

    A tracked file missing from the working tree (``FileNotFoundError``) is skipped. Any other read
    failure, and ``Uninspectable``, is a problem.
    """
    names = list(names)
    problems: list[str] = []
    synthetic_dir = False
    if SYNTHETIC_FIXTURE in names:
        try:
            synthetic_dir = is_synthetic(read(SYNTHETIC_FIXTURE))
        except (OSError, Uninspectable):
            synthetic_dir = False
    for name in names:
        if name.startswith(PACK_PATH):
            problems.append(f"{name}: tracked under {PACK_PATH}")
            continue
        try:
            data = read(name)
        except FileNotFoundError:
            continue
        except (OSError, Uninspectable) as exc:
            problems.append(f"{name}: cannot be inspected ({exc})")
            continue
        path = PurePosixPath(name)
        in_fixture = str(path.parent) == SYNTHETIC_DIR and path.name in (*DATA_FILES, "manifest.json")
        if synthetic_dir and in_fixture:
            continue  # the synthetic fixture, in its exact folder
        _inspect(None, name, data, 0, _Budget(), problems)
    problems.extend(_set_problems(None, names, {SYNTHETIC_DIR} if synthetic_dir else set()))
    return problems


def _artifact_files(path: Path) -> Iterator[tuple[str, Callable[[], bytes]]]:
    if path.is_dir():
        for item in sorted(path.rglob("*")):
            if item.is_file():
                yield item.relative_to(path).as_posix(), lambda item=item: _read_limited(item)
    else:
        yield path.name, lambda: _read_limited(path)


def _read_limited(path: Path) -> bytes:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise Uninspectable(f"larger than {MAX_FILE_BYTES} bytes, cannot be inspected")
    return path.read_bytes()


def artifact_problems(path: Path) -> list[str]:
    """Problems inside a built artifact (archive or folder), nested archives included."""
    problems: list[str] = []
    label = str(path)
    budget = _Budget()
    listing: list[str] = []
    try:
        for name, read in _artifact_files(path):
            listing.append(name)
            parts = PurePosixPath(name).parts
            if any(parts[i : i + 2] == ("knowledge", "pack") for i in range(len(parts) - 1)):
                problems.append(f"{label}: contains {name}")
                continue
            try:
                data = read()
            except (OSError, Uninspectable) as exc:
                problems.append(f"{label}!{name}: cannot be inspected ({exc})")
                continue
            if path.is_dir():
                _inspect(label, name, data, 0, budget, problems)
            else:
                # the artifact itself: report its members as "contained in" the artifact
                inner: list[str] = []
                _inspect(None, str(path), data, 0, budget, inner)
                problems.extend(p.replace(f"{path}: tracked", f"{path}: contains", 1) for p in inner)
    except OSError as exc:
        problems.append(f"{label}: cannot be inspected ({exc})")
    if path.is_dir():
        problems.extend(_set_problems(label, listing))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail when the knowledge pack is tracked or shipped.")
    parser.add_argument("--artifact", nargs="*", type=Path, default=[], metavar="PATH",
                        help="built .pyz/.zip/.tar.gz files or folders to inspect as well")
    parser.add_argument("--root", type=Path, default=ROOT,
                        help="git repository to inspect (default: this one)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    with git_index_reader(root) as read:
        problems = tracked_problems(tracked_files(root), read)
    for artifact in args.artifact:
        problems.extend(artifact_problems(artifact))
    for problem in problems:
        print(f"check_no_pack: {problem}", file=sys.stderr)
    if problems:
        print("check_no_pack: the knowledge pack must stay outside the repository and the public artifacts",
              file=sys.stderr)
        return 1
    print(f"check_no_pack: ok ({len(args.artifact)} artifact(s) inspected)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
