"""User-visible strings for the CLI, API and errors, centralized for future locales."""

from __future__ import annotations

CLI_DESCRIPTION = "Log analyzer and viewer for ADMS adapters (online interfaces)."
HELP_DATA_DIR = (
    "Directory for cases and indexes "
    "(default: %%LOCALAPPDATA%%\\dmslog on Windows, ${XDG_DATA_HOME:-~/.local/share}/dmslog elsewhere)."
)
HELP_VERSION = "Show the version and exit."
HELP_SERVE = "Start the local web viewer."
HELP_PORT = "TCP port on 127.0.0.1 (default: a random free port)."
HELP_NO_BROWSER = "Do not open the default web browser."
HELP_INFO = "Show version, environment and data directory."
HELP_INGEST = "Index the logs of an ERS zip, folder or file (not implemented yet)."
HELP_EXPORT = "Export a text report of a selection (not implemented yet)."
HELP_TEMPLATES = "List message templates and their counts (not implemented yet)."
HELP_KB = "Build or inspect the knowledge pack (not implemented yet)."
HELP_STUB_ARGS = "Arguments (ignored until the command is implemented)."

NOT_IMPLEMENTED = "dmslog {command}: not implemented yet (milestone {milestone})"
INVALID_PORT = "invalid port: {value!r} (expected an integer from 0 to 65535)"

SQLITE_FTS5_MISSING = (
    "SQLite {version} in this Python build has no FTS5 support; "
    "dmslog needs it for full-text search. Use a Python build with FTS5 enabled "
    "(error: {error})."
)
SQLITE_OK = "ok"

SERVING = "dmslog is running at {url}"
SERVING_HINT = "Press Ctrl+C to stop."
STOPPED = "dmslog stopped."
BIND_FAILED = "cannot listen on 127.0.0.1:{port}: {error}"

INFO_LINES = (
    ("dmslog", "{version}"),
    ("commit", "{commit}"),
    ("python", "{python}"),
    ("platform", "{platform}"),
    ("sqlite", "{sqlite}"),
    ("fts5", "{fts5}"),
    ("data directory", "{data_dir}"),
)

HTTP_FORBIDDEN = "Forbidden"
HTTP_NOT_FOUND = "Not found"
HTTP_METHOD_NOT_ALLOWED = "Method not allowed"
