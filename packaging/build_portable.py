"""Build the portable Windows folder/zip (no installer): embeddable Python + dmslog.pyz.

Runs on Linux or Windows:  python packaging/build_portable.py
Output: dist/dmslog-portable-win64/  and  dist/dmslog-portable-win64.zip
"""
from __future__ import annotations

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

README_TXT = (
    "dmslog portable (Windows x64)\r\n"
    "No installation and no administrator rights needed.\r\n"
    "\r\n"
    "Usage (in a terminal opened in this folder):\r\n"
    "  dmslog.bat              start the viewer (same as 'serve') and open the browser\r\n"
    "  dmslog.bat serve        start the viewer and open the default browser\r\n"
    "  dmslog.bat --help       list all commands\r\n"
    "Press Ctrl+C in the console window to stop the server.\r\n"
    "\r\n"
    "Copy this whole folder wherever you like. Logs are read in place and nothing\r\n"
    "leaves this PC: the server listens on 127.0.0.1 only.\r\n"
)


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


def build() -> None:
    embed = fetch_embed()
    if OUT.exists():
        shutil.rmtree(OUT)
    py_dir = OUT / "python"
    with zipfile.ZipFile(embed) as z:
        z.extractall(py_dir)

    # The ._pth file is left as shipped (isolated, no site): the .pyz puts itself on sys.path.

    shutil.copyfile(build_pyz.build(), OUT / "dmslog.pyz")
    (OUT / "dmslog.bat").write_text(LAUNCHER, encoding="utf-8", newline="")
    (OUT / "LEIA-ME.txt").write_text(README_TXT, encoding="utf-8", newline="")

    zip_path = OUT.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(OUT), "zip", OUT.parent, OUT.name)
    print(f"done: {OUT}\n        {zip_path} ({zip_path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    build()
