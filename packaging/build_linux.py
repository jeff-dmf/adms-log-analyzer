"""Build dist/dmslog-linux.tar.gz: dmslog.pyz plus the dmslog launcher script.

Run on any OS:  python packaging/build_linux.py
"""
from __future__ import annotations

import gzip
import io
import tarfile
from pathlib import Path

import build_pyz

ROOT = build_pyz.ROOT
LAUNCHER = ROOT / "packaging" / "dmslog.sh"
TARGET = build_pyz.DIST / "dmslog-linux.tar.gz"
PREFIX = "dmslog-linux"


def add_file(tar: tarfile.TarFile, source: Path, name: str, mode: int) -> None:
    data = source.read_bytes()
    if name.endswith("dmslog"):
        data = data.replace(b"\r\n", b"\n")  # the launcher must have LF endings
    info = tarfile.TarInfo(f"{PREFIX}/{name}")
    info.size = len(data)
    info.mode = mode
    info.mtime = build_pyz.FIXED_MTIME
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    tar.addfile(info, io.BytesIO(data))


def build() -> Path:
    pyz = build_pyz.build()
    if TARGET.exists():
        TARGET.unlink()
    with TARGET.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=build_pyz.FIXED_MTIME) as gz:
            with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
                add_file(tar, LAUNCHER, "dmslog", 0o755)
                add_file(tar, pyz, "dmslog.pyz", 0o644)
    print(f"built {TARGET} ({TARGET.stat().st_size / 1e3:.0f} kB)")
    return TARGET


if __name__ == "__main__":
    build()
