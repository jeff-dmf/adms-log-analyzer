# Changelog

## [0.1.0]

First release. A local log analyzer and viewer for the ADMS online-interface adapters: load the logs of an ERS (zip or folder) and analyze them with the support of the adapter knowledge base.

- Ingestion of ERS zips and folders into a local SQLite index (FTS5), with a virtual grid, full-text search, filters, saved filters, severity, source merge and timeline.
- Adapter templates (message families), unknown-line reporting and the layered Registry configuration, checked against the startup log.
- Derived events: operations with duration and status, startup, statistics, levels, gaps and clock jumps.
- Normal/sensitive correlation, callbacks, retries and key-based search across the sources of a case.
- Payloads (request/response): extraction, XML viewer and link to the event.
- Live mode (`dmslog watch`, `serve --watch`, follow tail), bookmarks and record comparison.
- Configurable redaction and `.txt` export for support tickets (`dmslog export`).
- Distribution: `dmslog.pyz` (Windows and Linux), Linux tarball and a Windows portable folder (embeddable Python 3.13, no installer, no admin). The public artifacts never include the knowledge pack.
