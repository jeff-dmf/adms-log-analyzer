"""Build the portable Windows folder/zip (no installer): embeddable Python + dmslog.pyz.

Runs on Linux or Windows:  python packaging/build_portable.py [--pack DIR]
Output: dist/dmslog-portable-win64/  and  dist/dmslog-portable-win64.zip

--pack DIR copies a validated knowledge pack to knowledge/pack/ in the folder and the zip. It is for
the maintainer's INTERNAL builds only: the pack is derived from product documentation and must never
be published, so CI never passes it (ARQUITETURA D10).
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

import build_pyz

PY_VERSION = "3.13.7"
PY_EMBED_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
# python.org publishes only MD5/signatures; this SHA-256 was pinned on the first download (TOFU).
PY_EMBED_SHA256 = "f6cca216a359be84797cabb54149ce5e062afb16cc7567eb7fc51cacb2d86b65"

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "dist" / ".cache"
OUT = ROOT / "dist" / "dmslog-portable-win64"

LAUNCHER = (
    "@echo off\r\n"
    'set "PY=%~dp0python\\python.exe"\r\n'
    'if "%~1"=="" (\r\n'
    '  "%PY%" -I "%~dp0dmslog.pyz" serve\r\n'
    ") else (\r\n"
    '  "%PY%" -I "%~dp0dmslog.pyz" %*\r\n'
    ")\r\n"
)

README_SRC = Path(__file__).resolve().parent / "portable-README.txt"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_embed() -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / Path(PY_EMBED_URL).name
    if not dest.exists():
        print(f"downloading {PY_EMBED_URL}")
        urllib.request.urlretrieve(PY_EMBED_URL, dest)
    digest = sha256(dest)
    if not PY_EMBED_SHA256:
        print(f"WARNING: PY_EMBED_SHA256 is empty; pin it in build_portable.py: {digest}")
    elif digest != PY_EMBED_SHA256:
        dest.unlink()
        sys.exit(f"embeddable Python SHA-256 mismatch: {digest}")
    return dest


def copy_pack(source: Path, target: Path) -> None:
    """Validate ``source`` and copy its pack files to ``target`` (internal builds only)."""
    sys.path.insert(0, str(ROOT / "src"))
    from dmslog.knowledge.pack_schema import PACK_FILES, validate_pack

    problems = validate_pack(source)
    if problems:
        sys.exit(f"invalid knowledge pack in {source}: {'; '.join(problems[:5])}")
    target.mkdir(parents=True)
    for name in PACK_FILES:
        shutil.copyfile(source / name, target / name)
    print(f"INTERNAL BUILD: knowledge pack copied from {source}; do not publish this folder or zip")


def build(pack: Path | None = None) -> None:
    embed = fetch_embed()
    if OUT.exists():
        shutil.rmtree(OUT)
    py_dir = OUT / "python"
    with zipfile.ZipFile(embed) as z:
        z.extractall(py_dir)

    # The ._pth file is left as shipped (isolated, no site): the .pyz puts itself on sys.path.

    shutil.copyfile(build_pyz.build(), OUT / "dmslog.pyz")
    (OUT / "dmslog.bat").write_text(LAUNCHER, encoding="utf-8", newline="")
    readme = README_SRC.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\n", "\r\n")
    (OUT / "README.txt").write_text(readme, encoding="utf-8", newline="")
    if pack is not None:
        copy_pack(pack, OUT / "knowledge" / "pack")

    zip_path = OUT.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(OUT), "zip", OUT.parent, OUT.name)
    print(f"done: {OUT}\n        {zip_path} ({zip_path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build the portable Windows folder and zip.")
    parser.add_argument(
        "--pack",
        type=Path,
        default=None,
        metavar="DIR",
        help="internal builds only: copy this knowledge pack to knowledge/pack/",
    )
    build(parser.parse_args().pack)
