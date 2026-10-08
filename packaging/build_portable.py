"""Monta a pasta/zip portátil do Windows (sem instalação): Python embeddable + app.

Roda em Linux ou Windows:  python packaging/build_portable.py
Saída: dist/dmslog-portable-win64/  e  dist/dmslog-portable-win64.zip
"""
from __future__ import annotations

import hashlib
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

PY_VERSION = "3.13.7"
PY_EMBED_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
# python.org publica só MD5/assinaturas; este SHA-256 foi fixado no primeiro download (TOFU).
PY_EMBED_SHA256 = "f6cca216a359be84797cabb54149ce5e062afb16cc7567eb7fc51cacb2d86b65"

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "dist" / ".cache"
OUT = ROOT / "dist" / "dmslog-portable-win64"

LAUNCHER = r"""@echo off
setlocal
set "HERE=%~dp0"
"%HERE%python\python.exe" -I -m dmslog %*
endlocal
"""


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
        print(f"baixando {PY_EMBED_URL}")
        urllib.request.urlretrieve(PY_EMBED_URL, dest)
    digest = sha256(dest)
    if not PY_EMBED_SHA256:
        print(f"AVISO: PY_EMBED_SHA256 vazio; fixe em build_portable.py: {digest}")
    elif digest != PY_EMBED_SHA256:
        dest.unlink()
        sys.exit(f"SHA-256 do Python embeddable não confere: {digest}")
    return dest


def build() -> None:
    embed = fetch_embed()
    if OUT.exists():
        shutil.rmtree(OUT)
    py_dir = OUT / "python"
    with zipfile.ZipFile(embed) as z:
        z.extractall(py_dir)

    # Faz o interpretador embutido enxergar a pasta app/ (modo -I ignora PYTHONPATH).
    (pth,) = py_dir.glob("python3*._pth")
    lines = [l for l in pth.read_text().splitlines() if l.strip() != "#import site"]
    pth.write_text("\n".join([*lines, r"..\app"]) + "\n")

    app = OUT / "app"
    shutil.copytree(ROOT / "src" / "dmslog", app / "dmslog",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    (OUT / "dmslog.bat").write_text(LAUNCHER.replace("\n", "\r\n"), newline="")
    (OUT / "LEIA-ME.txt").write_text(
        "dmslog portátil (Windows x64)\r\n"
        "Não precisa instalar nada nem de administrador.\r\n"
        "Execute dmslog.bat (ou dmslog.bat --help em um terminal).\r\n"
        "Copie esta pasta inteira para onde quiser.\r\n", newline="")

    zip_path = OUT.with_suffix(".zip")
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(OUT), "zip", OUT.parent, OUT.name)
    print(f"pronto: {OUT}\n        {zip_path} ({zip_path.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    build()
