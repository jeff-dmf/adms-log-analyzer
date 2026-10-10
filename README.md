# dmslog

Log analyzer and viewer for ADMS adapters (online interfaces), ADMS GridOps 3.9.
Load the logs of an ERS (zip) or of an installation `Log` folder, browse them in a local web
viewer (grid, search, filters, timeline, derived operations, normal/sensitive links, payloads) and
analyze them with the help of the adapters' knowledge base (operations, parameters, error codes).

Everything runs on your machine: no network access beyond loopback, no telemetry.

Status: first release 0.1.0 in preparation. Ingestion, templates, knowledge, derived events,
correlation and payloads are done; bookmarks and export are in review; live follow (`watch`) is not
implemented yet. Plan and state: `docs/ROADMAP.md`.

## Requirements

- Python 3.11 or newer with `sqlite3` built with FTS5 (checked at startup; `dmslog info` shows it).
- No third-party runtime dependencies and no installation. A modern browser (Edge or Chrome).
- Windows and Linux are both first-class.

## Quick start

Linux (a single zipapp):

```bash
python3 dmslog.pyz serve        # prints a URL with an access token and opens the browser
```

Windows (portable folder: embedded Python + `dmslog.pyz` + `dmslog.bat`; no admin, no internet):

```bat
dmslog.bat serve
```

From a source checkout:

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
python -m dmslog serve
```

## Loading an ERS

- In the viewer: create a case and add the ERS zip (or the `Log` folder) by its local path. A case
  holds one ERS (the Enterprise and Sensitive halves of the same capture go together).
- From the command line (indexes without opening the viewer):

```bash
python3 dmslog.pyz ingest /path/to/ERS.zip
python3 dmslog.pyz ingest /path/to/ERS.zip --case my-case --all-logs
python3 dmslog.pyz serve        # then open the case
```

Ingesting the same ERS again is safe and fast: unchanged files are not read again. The zip is read
in place (never extracted) and log files are opened, read and closed on every cycle, never held open.

## Commands

| Command | What it does |
|---|---|
| `serve [--port PORT] [--no-browser]` | Start the local web viewer on 127.0.0.1 (random free port by default). |
| `ingest PATH [--case NAME] [--all-logs]` | Index an ERS zip, `Log` folder or log file into a case. `--case` names the case (default: derived from the ERS GUID and capture time, or the folder name); `--all-logs` indexes every text log, not only the default scope. |
| `info` | Show version, Python, SQLite/FTS5, data directory and cases. |
| `export` | Text report of a selection (not implemented yet; arrives with milestone M7). |

Every command accepts `--data-dir DIR`; `--version` and `--help` are global. `templates` and `kb`
are placeholders for later milestones.

## Data directory

Cases and indexes live in the user's data directory, one folder per case (deleting the case deletes
its index):

- Windows: `%LOCALAPPDATA%\dmslog`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/dmslog`
- Anywhere else: `--data-dir DIR` (for example an encrypted USB drive).

## Privacy and security

- The server listens on `127.0.0.1` only. `serve` prints a URL with a random access token; the
  browser exchanges it for an `HttpOnly`, `SameSite=Strict` session cookie. Requests without a valid
  cookie get 403; `Host` is checked against DNS rebinding, and `Origin` on state-changing requests.
- No telemetry, no update check, no CDN, no external fonts. The front-end is vendored, with no build step.
- Log content is untrusted data: the UI renders it only as text (`textContent`), never as HTML.
- ERS samples, indexes and exports contain customer data and never leave the machine. Do not commit,
  upload or paste them anywhere (including issues). Indexes and `dist/` are git-ignored.
- Payloads (request/response XML) are always treated as sensitive.

## Tests and lint

```bash
python -m pytest -q
node --test tests/web/*.test.mjs
ruff check
```

Tests use only synthetic fixtures. The real ERS samples in `local-samples/` (Git LFS) are read by
scripts only, to learn formats.

## Build

```bash
python packaging/build_pyz.py        # dist/dmslog.pyz (Windows and Linux)
python packaging/build_linux.py      # dist/dmslog-linux.tar.gz (pyz + dmslog launcher)
python packaging/build_portable.py   # dist/dmslog-portable-win64.zip (embeddable Python + pyz + dmslog.bat)
```

## Repository layout

- `src/dmslog/`: the app (`ingest/`, `parse/`, `adapters/` TOML templates, `derive/`, `knowledge/`,
  `store/`, `server/`, `web/`, `payload/`, `report/`).
- `tests/`: pytest (`unit/`, `integration/`, `property/`) and Node tests for the front-end (`web/`).
- `docs/ARQUITETURA.md`: architecture, schema, API and decisions. `docs/ROADMAP.md`: milestones and
  state. `docs/formato/`: log formats per adapter. Process documents in `docs/` are in Portuguese.
- `packaging/`, `tools/`: builds, smoke checks and developer tools.

## License and privacy notice

Private repository, not licensed for redistribution. The knowledge pack (`src/dmslog/knowledge/pack`)
is derived from Schneider Electric documentation: the repository, its release artifacts and any build
that contains the pack must stay private and must not be published.
