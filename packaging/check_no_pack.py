"""Guard: the knowledge pack never enters the repository nor a public artifact (ARQUITETURA D10).

The pack is derived from product documentation. This check fails (exit 1) when:

* a tracked file lives under ``src/dmslog/knowledge/pack/``, or a tracked ``manifest.json`` looks like a
  pack manifest, except the synthetic test fixture (whose notice says ``SYNTHETIC TEST FIXTURE``);
* with ``--artifact PATH ...``: a built ``.pyz``/``.zip``/``.tar.gz`` or folder contains a ``knowledge/pack``
  entry or a pack manifest.

Usage:  python packaging/check_no_pack.py [--artifact PATH ...]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
import zipfile
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
PACK_PATH = "src/dmslog/knowledge/pack/"
SYNTHETIC_FIXTURE = "tests/fixtures/knowledge/mini_pack/manifest.json"
SYNTHETIC_MARK = "SYNTHETIC TEST FIXTURE"
MANIFEST_KEYS = {"schema_version", "generator_version", "notice"}


def looks_like_pack_manifest(data: bytes) -> bool:
    try:
        value = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return False
    return isinstance(value, dict) and MANIFEST_KEYS <= set(value)


def is_synthetic(data: bytes) -> bool:
    try:
        notice = json.loads(data.decode("utf-8-sig")).get("notice", "")
    except (UnicodeDecodeError, ValueError, AttributeError):
        return False
    return isinstance(notice, str) and notice.startswith(SYNTHETIC_MARK)


def tracked_files(root: Path = ROOT) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True
    ).stdout.decode("utf-8")
    return [name for name in out.split("\0") if name]


def tracked_problems(names: Iterable[str], read: Callable[[str], bytes]) -> list[str]:
    """Problems among tracked ``names`` (POSIX paths); ``read(name)`` returns the file bytes.

    A tracked file missing from the working tree (deleted, deletion not committed yet) is skipped: the
    check is meant for a checkout, as in CI.
    """
    problems = []
    for name in names:
        try:
            data = read(name) if name.startswith(PACK_PATH) or name.endswith("manifest.json") else b""
        except OSError:
            continue
        if name.startswith(PACK_PATH):
            problems.append(f"{name}: tracked under {PACK_PATH}")
        elif PurePosixPath(name).name == "manifest.json":
            if looks_like_pack_manifest(data) and not (name == SYNTHETIC_FIXTURE and is_synthetic(data)):
                problems.append(f"{name}: tracked knowledge pack manifest")
    return problems


def _members(path: Path) -> Iterator[tuple[str, Callable[[], bytes]]]:
    if path.is_dir():
        for item in sorted(path.rglob("*")):
            if item.is_file():
                yield item.relative_to(path).as_posix(), item.read_bytes
    elif path.name.endswith((".tar.gz", ".tgz", ".tar")):
        with tarfile.open(path) as tar:
            for member in tar.getmembers():
                if member.isfile():
                    handle = tar.extractfile(member)
                    data = handle.read() if handle else b""
                    yield member.name, (lambda data=data: data)
    else:  # .pyz and .zip
        with zipfile.ZipFile(path) as archive:
            for name in archive.namelist():
                yield name, (lambda name=name: archive.read(name))


def artifact_problems(path: Path) -> list[str]:
    problems = []
    for name, read in _members(path):
        parts = PurePosixPath(name).parts
        if any(parts[i:i + 2] == ("knowledge", "pack") for i in range(len(parts) - 1)):
            problems.append(f"{path}: contains {name}")
        elif parts and parts[-1] == "manifest.json" and looks_like_pack_manifest(read()):
            problems.append(f"{path}: contains a knowledge pack manifest ({name})")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail when the knowledge pack is tracked or shipped.")
    parser.add_argument("--artifact", nargs="*", type=Path, default=[], metavar="PATH",
                        help="built .pyz/.zip/.tar.gz files or folders to inspect as well")
    args = parser.parse_args(argv)
    problems = tracked_problems(tracked_files(), lambda name: (ROOT / name).read_bytes())
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
