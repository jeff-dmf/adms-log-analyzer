# Changelog

## [0.1.0]

First release. A local log analyzer and viewer for the ADMS online-interface adapters: load the logs of an ERS (zip or folder) and analyze them with the support of the adapter knowledge base.

- Ingestion of ERS zips and folders into a local SQLite index (FTS5), with a virtual grid, full-text search, filters, saved filters, severity, source merge and timeline.
- Adapter templates (message families), unknown-line reporting and the layered Registry configuration, checked against the startup log.
- Derived events: operations with duration and status, startup, statistics, levels, gaps and clock jumps.
- Normal/sensitive correlation, callbacks, retries and key-based search across the sources of a case.
- Payloads (request/response): extraction, XML viewer and link to the event.
- Live mode (`dmslog watch`, `serve --watch`, follow tail), bookmarks and record comparison.
- `.txt` export of a selection for support tickets (`dmslog export`): the original text by default, or best-effort anonymization only when explicitly requested (`--anonymize`). The redaction tooling itself is a development tool, separate from the app.
- Several captures in one case (collections by server/ERS group): per-collection clock offset applied at query time, follow a key across sources, record comparison by message type, repeated-record marks and the "Missing steps" panel.
- Ad hoc regex extractor (check, run, top values, promote a pattern to a message type), formats described by `[[envelope]]` entries, and mountable dashboard panels with server notices.
- Large files: open only part first (window and sources preview, two-stage progress, resume), plus versioned preferences export/import.
- New interface: top bar, status bar, dock with tabs, column picker and resize, responsive layout, filter strip, timeline, detail view, side column with the open case, case dialogs and empty state, keyboard navigation (regions, skip link, shortcuts dialog), row context menu and command palette (`Ctrl+K`), accessible grid, failures that keep what is on screen, scroll without forced layout, and a Follow tail with one total that pauses while the tab is hidden.
- Security and privacy review of the whole application (`docs/notas/`), with the fixes applied.
- Distribution: `dmslog.pyz` (Windows and Linux), Linux tarball and a Windows portable folder (embeddable Python 3.13, no installer, no admin). The public artifacts never include the knowledge pack.
