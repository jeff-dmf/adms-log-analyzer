# dmslog

Log analyzer and viewer for ADMS adapters (online interfaces), ADMS GridOps 3.9.
Load the logs of an ERS (zip), browse them in a local web viewer and analyze them with the
help of the adapters' knowledge base. Everything runs locally: the server listens on
`127.0.0.1` only and nothing leaves the machine.

Status: milestone M0 (skeleton). The server, health endpoint and packaging work; ingestion,
parsing and the viewer features arrive in later milestones (see `docs/ROADMAP.md`).

## Requirements

Python 3.11 or newer with `sqlite3` and FTS5 (checked at startup). Runtime has no third-party
dependencies.

## Run from source

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
python -m dmslog serve            # prints a URL with an access token and opens the browser
python -m dmslog serve --no-browser --port 8765
python -m dmslog info             # version, SQLite/FTS5, data directory
python -m dmslog --help
```

The data directory defaults to `%LOCALAPPDATA%\dmslog` on Windows and
`${XDG_DATA_HOME:-~/.local/share}/dmslog` elsewhere; override it with `--data-dir DIR`.

## Build and distribute

```bash
python packaging/build_pyz.py        # dist/dmslog.pyz (Windows and Linux)
python packaging/build_linux.py      # dist/dmslog-linux.tar.gz (pyz + dmslog launcher)
python packaging/build_portable.py   # dist/dmslog-portable-win64.zip (embeddable Python + pyz + dmslog.bat)
```

- Linux: `python3 dist/dmslog.pyz serve`, or extract the tarball and run `./dmslog serve`.
- Windows: unzip the portable folder and run `dmslog.bat serve` (no installation, no admin).

## Tests and lint

```bash
python -m pytest -q
ruff check
```
