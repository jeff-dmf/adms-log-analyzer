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
- Several captures in one case (collections by server/ERS group), repeated-record marks and the "Missing steps" panel, and following a key across sources.
- Ad hoc regex extractor in the dock (check, run, top values, "Only" to filter) and a "Promote to message type…" dialog that shows a validated TOML you can copy; it does not install the new message type. Formats can be described by `[[envelope]]` entries.
- Large files: open only part first (window and sources preview, two-stage progress, resume).
- Per-collection clock offset: an align dialog (anchor and target records from the record menu), a collection badge and an adjusted-time column. The offset is applied to the grid, count, histogram, context and next-highlight queries; events, statistics, exports, key search and comparison keep the original time.
- Compare views dock panel (compare two views by message type), user-mountable Panels dock panel (saved per user, run against the open case and refreshed on live updates), and a Preferences export/import dialog in the Export menu (redaction is off by default in the export; import has a Check step before Apply).
- New interface: top bar, status bar, dock with tabs, column picker and resize, responsive layout, filter strip, timeline, detail view, side column with the open case, case dialogs and empty state, keyboard navigation (regions, skip link, shortcuts dialog), row context menu and command palette (`Ctrl+K`), an accessible grid, failures that keep what is on screen, batched wheel scrolling, and a Follow tail with one total that pauses while the tab is hidden.
- Security and privacy reviews (`docs/notas/`): the first covered the pre-M9 code and the second the changes since then (no critical, high or medium finding); selected fixes were applied and a few low-severity items were left as documented decisions.
- Distribution: `dmslog.pyz` (Windows and Linux), Linux tarball and a Windows portable folder (embeddable Python 3.13, no installer, no admin). The public artifacts never include the knowledge pack.
