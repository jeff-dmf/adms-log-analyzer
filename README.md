# dmslog

Log analyzer and viewer for ADMS adapters (online interfaces), ADMS GridOps 3.9.
Load the logs of an ERS (zip) or of an installation `Log` folder, browse them in a local web
viewer (grid, search, filters, timeline, derived operations, normal/sensitive links, payloads) and
analyze them with the help of the adapters' knowledge base (operations, parameters, error codes).

Everything runs on your machine: no network access beyond loopback, no telemetry.

Status: first release 0.1.0 in preparation. Plan and state: `docs/ROADMAP.md`.

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

- In the viewer, to create a case: type the case name, then add **1 or more** ERS ZIPs to the list
  "ERS files of the new case" and press **Create case**. Press **Browse...** to pick the files in the
  system file dialog (Ctrl or Shift selects several at once; where no native dialog exists, a built-in
  folder browser opens instead), or paste a full path and press Enter. Each file shows its name, folder,
  size and whether the name says Enterprise or Sensitive; **Remove** drops one, **Clear all** drops
  all. The files are opened one after the other; a file the case cannot take (for example a capture
  from a different server) is marked in the list and does not stop the others. Today a case holds one
  ERS (the Enterprise and Sensitive halves of the same capture go together). To add a path to the
  selected case afterwards, use the path field under the case list and **Open**.
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
| `serve [--port PORT] [--no-browser]` | Start the local web viewer on 127.0.0.1 (random free port by default). Optionally follows a Log folder while serving: `--watch PATH [--watch-case NAME] [--watch-interval S] [--all-logs]`. |
| `ingest PATH [--case NAME] [--all-logs]` | Index an ERS zip, `Log` folder or log file into a case. `--case` names the case (default: derived from the ERS GUID and capture time, or the folder name); `--all-logs` indexes every text log, not only the default scope. |
| `watch PATH --case NAME [--interval S] [--all-logs]` | Keep a case up to date while the adapters write: polls an installation `Log` folder (default every 2 s) and indexes new records. Folders only (zips are static). Ctrl+C stops. |
| `export --case NAME (--ids ... \| --event ... \| --filter-json ...) [--anonymize [--redaction-mode strict\|standard]] [options]` | Write a plain-text (`.txt`) report of a selection. See below. |
| `info` | Show version, Python, SQLite/FTS5, data directory and cases. |

Every command accepts `--data-dir DIR`; `--version` and `--help` are global (`dmslog COMMAND --help`
lists every option). `templates` and `kb` are placeholders that report "not implemented yet".

## Exporting a report

`export` writes a text report (CRLF line endings, UTF-8) of a selection from an indexed case, for
example to attach to a support ticket.

```bash
python3 dmslog.pyz export --case my-case --event 12,15 --anonymize --out report.txt
python3 dmslog.pyz export --case my-case --ids 101,102 --context 5 --include-payloads --anonymize
python3 dmslog.pyz export --case my-case --filter-json @filter.json --out report.txt
```

- Selection (at least one): `--ids` (record ids), `--event` (event ids), `--filter-json` (a filter
  tree as JSON text or `@FILE`, in the format the viewer sends). `--context N` adds N records of
  context around each selected record or event.
- By default the report keeps the original text (the app runs locally and does not anonymize).
  `--anonymize` replaces sensitive values (GUIDs, hosts, IPs, users, URLs, paths, e-mails, phone
  numbers, ticket numbers and others) with stable markers, using the profile `redaction.toml` in the
  data directory; `--redaction-mode strict|standard` overrides the profile's mode for one export and
  is only accepted together with `--anonymize`: `strict` (default) also replaces every value it cannot
  prove harmless; `standard` applies pattern rules only and is more readable. `--no-anonymize` is
  still accepted and means the default. Anonymization is best effort: review the text before sharing it.
- `--include-payloads` adds request/response payloads (anonymized per XML/JSON element when
  `--anonymize` is set); without it they are omitted.
- `--out FILE` writes to a file; without it the report goes to stdout. `--max-bytes N` (default
  30000) limits one part; larger reports are split into numbered parts (`report-part1of3.txt`, ...).
- With `--anonymize`, a value map is saved in the case folder (`cases/<case>/redaction-maps/`, local only, never share it; deleting the case deletes it).

## Copy as (viewer)

The **Copy as** menu in the toolbar copies or downloads what the current view shows, with the original
text (no anonymization): a list of the values of a column or key (one per line or comma separated,
optional quotes, distinct only), the original lines of the view, the selected record, a CSV of the
view (visible columns plus extracted fields) and a CSV of the operations (duration, status,
operation, keys, time, source). CSV is UTF-8 with CRLF line ends and RFC 4180 quoting, with an optional
BOM for Excel on Windows; text cells that start with `=`, `+`, `-`, `@` or a tab get a leading
apostrophe so spreadsheets do not run them as formulas. Files are limited to 100,000 rows by default
(configurable); the menu warns when the view had more rows.

## Highlights (viewer)

**Highlights** in the toolbar keeps up to 20 color rules per case. A rule colors the records it
matches and hides nothing; the first enabled rule that matches gives the color. A condition is words
(full-text search), exact text, a regex (searched in the first 512 characters of the first line; one
unbounded repeat per regex), a key, a message type, a severity, a parsed field or the current filter.
In the record detail, **Highlight type = value** colors every record with that key, in the normal and
in the sensitive logs. Press `]` and `[` to go to the next and previous highlighted record of the view.
Rules are saved in the case and hold only what you typed.

## Data directory

Cases and indexes live in the user's data directory, one folder per case (deleting the case deletes
its index):

- Windows: `%LOCALAPPDATA%\dmslog`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/dmslog`
- Anywhere else: `--data-dir DIR` (for example an encrypted USB drive).

## Filter presets

The filter editor has a **Presets** panel: shortcuts for the adapters present in the open case
(for example "Errors and exceptions", "Failed operations"). Nothing is applied on its own; picking a
preset fills the filter tree. You can save the current tree as your own preset (for one adapter family
or for all), rename or delete it, and export or import presets as JSON or TOML. Your presets live in
`presets.toml` in the data directory and never contain paths or log data; the curated ones ship with
the app.

## Local alerts (follow mode)

While `dmslog serve --watch` follows a folder, the **Alerts** panel can warn you when the new records
match a rule: a banner in the viewer and, if you turn them on, browser notifications (the button
**Enable browser notifications** is the only thing that asks the browser for permission). Rules are a
template or a filter that matched new records, more than N matches in a time window, or an operation that
stayed open longer than a timeout. Create them from the current filter or from a preset; they are kept in
`alerts.toml` in the data directory (no paths or log data) and apply to every case. Nothing leaves this
computer: no e-mail, no network calls.

## Local aliases

The repository uses the neutral token `Custom` where a customer-specific adapter name used to be.
Real logs and configs still carry the original spelling. To make the analyzer recognize it, create
the optional file `aliases.toml` in the data directory (it is never part of the repository):

```toml
version = 1

[[alias]]
public = "Custom"            # token used in the repository: 1-64 chars, letters digits _ . -
real = "<original text>"     # spelling in real logs and configs: 1-128 chars, no control characters

[[alias]]                    # several entries may share one `public` (one per spelling or form)
public = "Custom"
real = "<original_underscore_form>"
```

At ingestion, `real` is replaced by `public` in names read from the ERS and in the text derived from
them: process and family classification, Registry file names, layer labels, product and Web Service
operation names, and error, progress and warning messages (stored `source.error`, CLI output, API
job messages). Replacement is one pass over the original text, longest `real` first, case-sensitive;
names and messages also match a whole `real` in any letter case.

What is **not** normalized: the index keeps the real `source.member` and `source.file_name` (the file is
reopened through them, and the sources list shows them), Registry parameter values (for example the
`WebServiceConfiguration` path), log message text and payloads, and the name of the zip or folder you
ingest. Do not share an index or export made with real names. A missing file means no aliases; an
invalid file is ignored with a message in `dmslog info`, which also shows how many aliases were loaded
(never their values). Re-ingest a case after changing the file.

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
