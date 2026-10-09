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
HELP_INGEST = "Index the logs of an ERS zip, folder or file into a case (safe to run again)."
HELP_INGEST_PATH = "ERS zip, Log folder or log file to index."
HELP_INGEST_CASE = "Case name (default: derived from the ERS GUID and capture time, or from the folder name)."
HELP_INGEST_ALL = "Index every text log of the collection, not only the default scope."
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

INGEST_START = "Indexing {path} into case {case} ({scope} scope)..."
INGEST_SCOPE_DEFAULT = "default"
INGEST_SCOPE_ALL = "all logs"
INGEST_PROGRESS = "  {member}: {percent:3d}%"
INGEST_SOURCE_DONE = "  [{action}] {member}: {records} records, {size_mb:.1f} MB"
INGEST_SOURCE_FAILED = "  [error] {member}: {error}"
INGEST_EVENTS = "Derived events: {events} (derivation took {seconds:.1f} s)"
INGEST_WARNING = "  [warning] {message}"
INGEST_SUMMARY = (
    "Sources: {selected} selected of {listed} listed ({indexed} indexed, {unchanged} unchanged, "
    "{skipped} not selected, {failed} failed)"
)
INGEST_SUMMARY_DATA = "Records: {records}  Lines: {lines}  Data: {size_mb:.1f} MB"
INGEST_SUMMARY_TIME = "Time: {seconds:.1f} s ({rate:.1f} MB/s)"
INGEST_CASE_DB = "Case database: {path}"
INGEST_NOT_FOUND = "cannot ingest: {error}"
INGEST_UNSAFE = "cannot ingest: unsafe or invalid archive: {error}"
INGEST_CONFLICT = "cannot add this collection to case {case}: {error}"
INGEST_INVALID_CASE = "{error}"

INFO_CASES_NONE = "none"
INFO_CASE_LINE = "{name}: {sources} sources, {entries} records ({path})"
INFO_CASE_UNREADABLE = "{name}: unreadable ({error})"

HTTP_FORBIDDEN = "Forbidden"
HTTP_NOT_FOUND = "Not found"
HTTP_METHOD_NOT_ALLOWED = "Method not allowed"

# --- JSON API (src/dmslog/server/api.py) ------------------------------------------------------
API_NOT_FOUND = "Unknown API route"
API_METHOD_NOT_ALLOWED = "Method not allowed for this route"
API_BAD_JSON = "The request body is not valid JSON"
API_BODY_NOT_OBJECT = "The request body must be a JSON object"
API_BODY_TOO_LARGE = "The request body is larger than {limit} bytes"
API_LENGTH_REQUIRED = "A Content-Length header is required (chunked bodies are not supported)"
API_BAD_LENGTH = "Invalid Content-Length header"
API_INVALID_CASE_ID = (
    "Invalid case id: use 1 to 80 letters, digits, '.', '_' or '-', starting with a letter or digit"
)
API_CASE_NOT_FOUND = "Case {case!r} does not exist"
API_CASE_AMBIGUOUS = "Case {case!r} is ambiguous: several folders differ only in letter case"
API_CASE_EXISTS = "Case {case!r} already exists"
API_CASE_BUSY = "Case {case!r} has an indexing job running"
API_CASE_DELETE_FAILED = "Case {case!r} could not be deleted: {error}"
API_CASE_CREATE_FAILED = "Case {case!r} could not be created: {error}"
API_MISSING_FIELD = "Missing or invalid field {field!r}"
API_BAD_SCOPE = "scope must be 'default' or 'all'"
API_PATH_NOT_FOUND = "Path does not exist: {path}"
API_PATH_NOT_ABSOLUTE = "The collection path must be absolute"
API_BAD_LIMIT = "limit must be an integer from 1 to {limit}"
API_BAD_INT = "{field} must be a non-negative integer"
API_BAD_ENTRY_ID = "Invalid entry id"
API_BAD_FAMILY = "family must be 1 to 32 letters, digits or underscores"
API_BAD_PRODUCT = "product must be 1 to 64 letters, digits, '.', '_' or '-'"
API_CONFIG_NOT_FOUND = "No stored configuration for product {product!r} (the collection has no Registry?)"
API_BAD_TEMPLATE = "template must be a template code of 1 to 32 letters, digits, '.', '_' or '-'"
API_TEMPLATE_NOT_FOUND = "Template {template!r} does not exist in this case"
API_BAD_ERROR_CODE = "code must be 1 to 128 letters, digits, '.', '_', ':' or '-'"
API_BAD_OPERATION = "operation must be 1 to 64 letters, digits, '.', '_' or '-'"
API_ERROR_NOT_FOUND = "Error code {code!r} is not defined for family {family!r}"
API_ENTRY_NOT_FOUND = "Entry {entry_id} does not exist in case {case!r}"
API_SUBSTR_NEEDS_SCOPE = (
    "Substring and regex search need a source or time-range condition at the top level of the filter"
)
API_BAD_EVENT_ID = "Invalid event id"
API_EVENT_NOT_FOUND = "Event {event_id} does not exist in case {case!r}"
API_BAD_EVENT_LIMIT = "limit must be an integer from 1 to {limit}"
API_BAD_EVENT_LIST = "{field} must be a comma-separated list of 1 to {limit} values of at most 128 characters"
API_BAD_EVENT_KIND = "kind must be a comma-separated list of lowercase names (letters, digits, '_')"
API_BAD_EVENT_STATUS = "status must be a comma-separated list of lowercase names (letters, digits, '_')"
API_BAD_EVENT_QUERY = "q must be 1 to 128 printable characters"
API_BAD_TIME = "{field} must be a log time like 'YYYY-MM-DD HH:MM:SS.mmm' (time part optional)"
API_BAD_CURSOR = "after is not a valid cursor"
API_BAD_STREAM_ID = "{field} must be a non-negative integer"
API_BAD_STAT_SIDE = "side must be 'client' or 'service'"
API_BAD_STAT_METHOD = "method must be 1 to 256 printable characters"
API_BAD_BUCKET = "bucket must be 'minute' or 'hour'"
API_QUERY_TIMEOUT = "The query took too long and was cancelled"
API_VIEW_BAD_ID = "Invalid saved filter id"
API_DATABASE_ERROR = "The case database could not be read"
API_INTERNAL_ERROR = "Internal error"

JOB_DONE_OK = "Indexed {records} records from {sources} sources"
JOB_DONE_WITH_FAILURES = "Indexed {records} records; {failed} of {sources} sources failed"
JOB_CANCELLED = "Indexing was cancelled because the server is stopping"
JOB_FAILED = "Indexing failed: {error}"
JOB_UNSAFE = "Unsafe or invalid archive: {error}"
