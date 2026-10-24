"""User-visible strings for the CLI, API and errors, centralized for future locales."""

from __future__ import annotations

CLI_DESCRIPTION = "Log analyzer and viewer for ADMS adapters (online interfaces)."
HELP_DATA_DIR = (
    "Directory for cases and indexes "
    "(default: %%LOCALAPPDATA%%\\dmslog on Windows, ${XDG_DATA_HOME:-~/.local/share}/dmslog elsewhere)."
)
HELP_VERSION = "Show the version and exit."
API_FILE_TEXT_NOT_FOUND = "No such file in this case."
API_FILE_TEXT_NOT_VIEWABLE = "This file is not shown as text (indexed log or a type that is not text)."
API_FILE_TEXT_TOO_LARGE = "This file is larger than {limit} bytes and is not shown."
API_FILE_TEXT_BINARY = "This file looks binary and is not shown as text."
API_FILE_TEXT_MISSING = "The file is no longer available where the case was opened."
HELP_SERVE = "Start the local web viewer."
HELP_PORT = "TCP port on 127.0.0.1 (default: a random free port)."
HELP_NO_BROWSER = "Do not open the default web browser."
HELP_INFO = "Show version, environment, data directory and the cases found there."
HELP_INGEST = "Index the logs of an ERS zip, folder or file into a case (safe to run again)."
HELP_INGEST_PATH = "ERS zip, Log folder or log file to index."
HELP_INGEST_CASE = "Case name (default: derived from the ERS GUID and capture time, or from the folder name)."
HELP_INGEST_ALL = "Index every text log of the collection, not only the default scope."
HELP_INGEST_QUEUE = (
    "Also read the message-queue databases of the collection (LocalPersistenceStore). Off by default: "
    "they hold message bodies."
)
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
    ("aliases", "{aliases}"),
    ("knowledge pack", "{knowledge_pack}"),
)

INGEST_START = "Indexing {path} into case {case} ({scope} scope)..."
INGEST_SCOPE_DEFAULT = "default"
INGEST_SCOPE_ALL = "all logs"
INGEST_PROGRESS = "  {member}: {percent:3d}%"
INGEST_SOURCE_DONE = "  [{action}] {member}: {records} records, {size_mb:.1f} MB"
INGEST_SOURCE_FAILED = "  [error] {member}: {error}"
INGEST_EVENTS = "Derived events: {events} (derivation took {seconds:.1f} s)"
INGEST_LINKS = "Links: {links} (correlation took {seconds:.1f} s)"
INGEST_WARNING = "  [warning] {message}"
INGEST_SUMMARY = (
    "Sources: {selected} selected of {listed} listed ({indexed} indexed, {unchanged} unchanged, "
    "{skipped} not selected, {failed} failed)"
)
INGEST_SUMMARY_DATA = "Records: {records}  Lines: {lines}  Data: {size_mb:.1f} MB"
INGEST_SUMMARY_TIME = "Time: {seconds:.1f} s ({rate:.1f} MB/s)"
INGEST_CASE_DB = "Case database: {path}"
INGEST_QUEUE = "Message queue: {dbs} databases, {items} items ({pending} pending), {errors} with errors"
INGEST_QUEUE_FAILED = "Message queue not read: {error}"
INGEST_NOT_FOUND = "cannot ingest: {error}"
INGEST_UNSAFE = "cannot ingest: unsafe or invalid archive: {error}"
INGEST_CONFLICT = "cannot add this collection to case {case}: {error}"
INGEST_INVALID_CASE = "{error}"
INGEST_NOT_ENOUGH_SPACE = (
    "not enough free disk space to index: {free_mb} MB free in the data folder, "
    "{needed_mb} MB needed ({factor}x the data to index); free some space or use --data-dir on another disk"
)
INGEST_LIMIT = "cannot ingest: {error}"

INFO_CASES_NONE = "none"

# Knowledge pack (ARQUITETURA D10): installed locally, never shipped; the app works without it.
HELP_PACK_DIR = (
    "Knowledge pack directory (default: $DMSLOG_PACK_DIR, else <data dir>/knowledge/pack, "
    "else knowledge/pack next to dmslog.pyz)."
)
KNOWLEDGE_PACK_NOT_INSTALLED = (
    "Knowledge pack not installed: vault descriptions, parameter defaults and error-code texts are "
    "unavailable. Logs, events and the ERS configuration work as usual."
)
KNOWLEDGE_PACK_INVALID = (
    "Knowledge pack is invalid and was ignored: vault descriptions, parameter defaults and error-code "
    "texts are unavailable. Run 'dmslog info' for details."
)
INFO_PACK_INSTALLED = "installed ({source}: {path}; schema {schema}, generator {generator}, vault {vault})"
INFO_PACK_NOT_INSTALLED = "not installed (searched: {searched})"
INFO_PACK_INVALID = "invalid, ignored ({source}): {error}"
INFO_PACK_SEARCHED_NONE = "nothing"
INFO_ALIASES_LOADED = "{count} loaded"
INFO_ALIASES_IGNORED = "file ignored ({error})"

# Local aliases (<data dir>/aliases.toml); the texts never include alias values.
ALIASES_ERR_TOML = "aliases.toml is not valid TOML"
ALIASES_ERR_TOML_LINE = "aliases.toml is not valid TOML (line {line})"
ALIASES_ERR_UNKNOWN_KEYS = "aliases.toml: unknown key in {where}"
ALIASES_ERR_VERSION = "aliases.toml: 'version' must be the integer {expected}"
ALIASES_ERR_NOT_TABLES = "aliases.toml: 'alias' must be a list of [[alias]] tables"
ALIASES_ERR_PUBLIC = "aliases.toml: {where}: 'public' must be 1-64 characters from letters, digits, _ . -"
ALIASES_ERR_REAL = (
    "aliases.toml: {where}: 'real' must be text of 1-{limit} characters without control characters"
)
ALIASES_ERR_CONFLICT = "aliases.toml: {where}: the same 'real' maps to different 'public' values"
ALIASES_ERR_TOO_LARGE = "aliases.toml is larger than 1 MiB"
ALIASES_ERR_READ = "aliases.toml cannot be read"
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
API_BAD_FIRST = (
    "first must be an object with 'from' and/or 'to' (integer ms, from lower than to) and/or 'sources'"
    " (1 to 500 names as /collections/plan lists them)"
)
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
API_REPEAT_BAD_IDS = "ids must be a list of at most {limit} record ids"
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
JOB_QUEUE_DONE = "Message queue: {dbs} databases, {items} items ({pending} pending)"
JOB_QUEUE_REMOVED = "Message queue data removed: {dbs} databases, {items} items"
JOB_UNSAFE = "Unsafe or invalid archive: {error}"

API_BAD_KEY_TYPE = "key type must be 1 to 64 letters, digits, '.', '_' or '-'"
API_BAD_KEY_VALUE = "key value must be valid percent-encoded UTF-8 of 1 to {limit} printable characters"
API_BAD_KEY_LIMIT = "limit must be an integer from 1 to {limit}"
API_BAD_CONT_LINES = "cont_lines must be an integer from 1 to {limit}"
API_NO_COUNTERPART = "Entry {entry_id} has no linked record"

INGEST_PAYLOADS = "Payloads: {payloads} ({linked} linked to an operation, linking took {seconds:.1f} s)"

API_BAD_PAYLOAD_ID = "Invalid payload id"
API_PAYLOAD_NOT_FOUND = "Payload {payload_id} does not exist in case {case!r}"
API_BAD_PAYLOAD_FORMAT = "format must be 'raw' or 'pretty'"
API_BAD_PAYLOAD_LIST = "{field} must be a comma-separated list of 1 to {limit} values of at most 128 chars"
API_BAD_ORPHAN = "orphan must be 0 or 1"
API_BAD_PAYLOAD_QUERY = "q must be 1 to 128 printable characters"
# --- Redaction and export (src/dmslog/report, `dmslog export`, /api/redaction, /api/export) -------
REDACT_CATEGORY_TEXT = {
    "guid": "GUIDs",
    "gid": "GIDs (DMS global ids)",
    "ticket": "INC / PROB / PRB / CHG / RITM numbers",
    "mrid": "mRIDs",
    "plate": "Vehicle plates",
    "host": "Host and server names",
    "ip": "IP addresses",
    "user": "User and account names",
    "url": "URLs",
    "path": "File and folder paths",
    "email": "E-mail addresses",
    "phone": "Phone numbers",
    "person": "Person names and addresses (labels and payload elements)",
}
REDACT_TERM_TEXT = "User terms"
REDACT_PROFILE_PROBLEMS = {
    "not_object": "the profile must be an object",
    "bad_name": "the profile name must be 1 to 64 letters, digits, spaces, '.', '_' or '-'",
    "bad_section": "this section has the wrong type",
    "unknown_category": "unknown category",
    "bad_flag": "category flags must be true or false",
    "bad_list": "this field must be a list of strings",
    "too_many": "too many entries",
    "bad_entry": "an entry is empty, too short, too long or has control characters",
    "bad_element_name": "payload names must be 1 to 64 letters, digits, '_', '.', ':' or '-'",
    "invalid_toml": "the profile file is not valid TOML",
    "unreadable": "the profile file could not be read",
    "bad_version": "the profile file was written by a newer dmslog",
}
REDACT_BAD_PROFILE = "Invalid redaction profile: {problem} ({field})"
REDACT_SAVE_FAILED = "The redaction profile could not be saved: {error}"
REDACT_PROFILE_INVALID_FILE = (
    "The stored redaction profile is invalid ({problem}); save a new one to replace it"
)

EXPORT_BAD_SELECTION = "selection must be an object with a 'filter', 'ids' or 'event'/'events'"
EXPORT_BAD_IDS = "selection.ids must be a list of 1 to {limit} positive integers"
EXPORT_BAD_EVENTS = "selection.events must be a list of 1 to {limit} positive integers"
EXPORT_BAD_OPTIONS = "options must be an object"
EXPORT_REDACTION_NOT_REQUESTED = (
    "A redaction profile or mode only applies when anonymization is asked for (anonymize = true)"
)
EXPORT_BAD_OPTION = "Invalid option {field!r}: {problem}"
EXPORT_BAD_FILTER = "selection.filter must be a filter object"
EXPORT_NOTHING = "The selection matched no records"
EXPORT_CASE_NOT_FOUND_DB = "Case {case!r} has no database in {data_dir}"

EXPORT_TITLE = "dmslog report - {label}"
EXPORT_TITLE_PART = "dmslog report - {label} - part {part}/{total}"
EXPORT_CONTINUES = "(continued from the previous part)"
EXPORT_GENERATED = "Generated: {when} - dmslog {version} - pack {pack}"
EXPORT_SOURCE = "Source: ERS {ers_ts} ({machine}) - {variant}"
EXPORT_SOURCE_PLAIN = "Source: {kind} ({machine})"
EXPORT_SOURCE_UNKNOWN = "Source: unknown"
EXPORT_REDACTION_ON = 'Redaction: ON (profile "{profile}")'
EXPORT_REDACTION_OFF = "Redaction: OFF (original text)"
EXPORT_TIME_NOTE = "Time shown as logged (local server time, no conversion)."
EXPORT_UNKNOWN_VALUE = "unknown"
EXPORT_SECTION_SUMMARY = "== Summary =="
EXPORT_SECTION_EVENTS = "== Events =="
EXPORT_SECTION_RECORDS = "== Records =="
EXPORT_SECTION_PAYLOADS = "== Payloads =="
EXPORT_SECTION_LEGEND = "== Redaction legend =="
EXPORT_SUMMARY_COUNTS = (
    "{records} selected records, {events} events, {payloads} payloads, window {start} .. {end}"
)
EXPORT_SUMMARY_GROUP = "{family} {name}  count={count}  fail={fail}  business_error={business}  p90={p90}"
EXPORT_SUMMARY_MORE = "... and {count} more operation groups"
EXPORT_EVENT_HEAD = "[{n}] {start} .. {end}  {family} {name}  status={status}  duration={duration}  key={key}"
EXPORT_EVENT_CODES = "error codes: {codes}"
EXPORT_EVENT_EXCEPTION = "exception: {exception}"
EXPORT_EVENT_STREAM = "stream: {process} ({kind})"
EXPORT_EVENT_NOTE = "note: {note}"
EXPORT_RECORD_HEAD = "[R{n}] {when}  {source}  line {line}{event}"
EXPORT_RECORD_EVENT = "  event: {name} status={status}"
EXPORT_EXCERPT_HEAD = "--- log excerpt ({file}, lines {first}-{last}) ---"
EXPORT_EXCERPT_CUT = "[... {count} more characters of this record omitted]"
EXPORT_PAYLOAD_OMITTED = "[payload P{n}: {role}, {length} characters - omitted (payloads not included)]"
EXPORT_PAYLOAD_SEE = "[payload P{n}: {role}, {length} characters - see Payloads]"
EXPORT_PAYLOAD_HEAD = (
    "[P{n}] {when}  {file} line {line}  {role} {direction} {peer} {operation}  ({fmt}, {length} characters)"
)
EXPORT_PAYLOAD_CUT = "[... {count} more characters of this payload omitted]"
EXPORT_LEGEND_NOTE = "Values were replaced by stable markers: the same value always gets the same marker."
EXPORT_LEGEND_NOTE_2 = "Automatic anonymization is best effort: review the text before sharing it."
EXPORT_LEGEND_LINE = "{text}: {distinct} distinct values, {replaced} replacements (markers <{label}-n>)"
EXPORT_LEGEND_NONE = "No value was replaced."
EXPORT_LEGEND_OVERFLOW = "{count} replacements used the generic marker because the marker table is full."
EXPORT_TRUNCATED_NOTICE = (
    "[... {count} more lines not shown: the report exceeds {limit} bytes; export in numbered parts]"
)
EXPORT_LINE_CUT = "...[line cut]"
EXPORT_WARN_RECORD_LIMIT = "Only the first {limit} selected records were exported."
EXPORT_WARN_ENTRY_LIMIT = "The export stopped after {limit} log records (the text limit of one report)."
EXPORT_WARN_TEXT_LIMIT = "The export stopped after {limit} characters of log text."
EXPORT_WARN_EVENT_LIMIT = "Only the first {limit} entries of event {event} were exported."
EXPORT_WARN_MISSING = "{count} selected ids do not exist in this case."

HELP_EXPORT_CMD = (
    "Export a plain-text report (.txt) of selected records or events, e.g. for a support ticket. "
    "Choose a selection (--ids, --event or --filter-json). The report keeps the original text; "
    "add --anonymize to replace sensitive values with markers."
)
HELP_EXPORT_CASE = "Case to export from (as listed by `dmslog info`)."
HELP_EXPORT_IDS = "Comma-separated record ids to export."
HELP_EXPORT_EVENT = "Comma-separated event ids to export."
HELP_EXPORT_FILTER = (
    "Filter tree as JSON text, or @FILE to read it from a file (same format the viewer sends)."
)
HELP_EXPORT_ANONYMIZE = (
    "Replace sensitive values with stable markers, using the redaction profile (<data dir>/redaction.toml). "
    "Off by default."
)
HELP_EXPORT_NO_ANONYMIZE = "Keep the original text (the default; accepted for scripts that still pass it)."
HELP_EXPORT_PAYLOADS = (
    "Include request/response payloads (with --anonymize, also redacted per XML/JSON element)."
)
HELP_EXPORT_CONTEXT = (
    "Log records of context to include before and after each selected record or event (default: 0)."
)
HELP_EXPORT_MAX_BYTES = (
    "Size limit of one part in bytes (default: {default}); larger reports are split into numbered parts."
)
HELP_EXPORT_OUT = (
    "Write the report to this file (numbered parts get a -part1of3 suffix before the extension; "
    "default: stdout)."
)
EXPORT_CLI_MODE_NEEDS_ANONYMIZE = "--redaction-mode only applies together with --anonymize"
EXPORT_CLI_NEED_SELECTION = "dmslog export: give --ids, --event or --filter-json"
EXPORT_CLI_BAD_LIST = "dmslog export: {option} must be a comma-separated list of positive integers"
EXPORT_CLI_BAD_FILTER = "dmslog export: --filter-json is not valid JSON or not an object ({error})"
EXPORT_CLI_FAILED = "dmslog export: {error}"
EXPORT_CLI_WROTE = "Wrote {path} ({size} bytes)"
EXPORT_CLI_MAP = "Value map saved to {path} (local only, never share it)"
EXPORT_CLI_WARNING = "warning: {message}"
EXPORT_RECORD_MORE = "  (+{count} more selected records in this excerpt)"
EXPORT_EVENT_NO_RECORDS = "(no log records are attached to this event)"
# --- Strict redaction mode (src/dmslog/report/redact.py, template_shape.py) -------------------------
REDACT_CATEGORY_TEXT["value"] = "Other values (strict mode: template fields, labelled values, unknown words)"
REDACT_PROFILE_PROBLEMS["bad_mode"] = "mode must be 'strict' or 'standard'"
EXPORT_REDACTION_ON_MODE = 'Redaction: ON (profile "{profile}", {mode} mode)'
HELP_EXPORT_REDACTION_MODE = (
    "With --anonymize only: override the mode of the redaction profile for this export: 'strict' "
    "(the default mode of anonymization; template-aware, "
    "replaces every value it cannot prove harmless) or 'standard' (pattern rules only, more readable)."
)
API_BAD_BOOKMARK_ID = "Invalid bookmark id"

HELP_WATCH = "Keep a case up to date while the adapters write: index a Log folder every few seconds."
HELP_WATCH_PATH = "Installation Log folder (or extracted ERS) to follow."
HELP_WATCH_CASE = "Case name (required: the case that receives the new records)."
HELP_WATCH_INTERVAL = "Seconds between polls (default: 2)."
HELP_WATCH_ALL = "Follow every text log of the folder, not only the default scope."
HELP_SERVE_WATCH = "Also follow this Log folder while serving (see 'dmslog watch'); off by default."
HELP_SERVE_WATCH_CASE = "Case that receives the followed records (default: derived from the folder name)."
HELP_SERVE_WATCH_INTERVAL = "Seconds between polls of --watch (default: 2)."
WATCH_NOT_A_FOLDER = "dmslog watch follows a folder (zips are static): {path}"
WATCH_INVALID_INTERVAL = "invalid interval: {value!r} (expected a number of seconds from 0.2 to 3600)"
WATCH_START = "Watching {path} into case {case} every {interval:g} s ({scope}); Ctrl+C stops."
WATCH_CYCLE = "{time} {sources}{derived}"
WATCH_SOURCE = "+{records} records in {member}"
WATCH_SOURCE_ACTION = "+{records} records in {member} ({action})"
WATCH_DERIVED = " | events +{events}, links +{links}, payloads +{payloads}"
WATCH_WARNING = "warning: {message}"
WATCH_ERROR = "watch error: {error}; retrying in {delay:.0f} s"
WATCH_CONFLICT = "case {case!r} cannot follow this folder: {error}"
WATCH_STOPPED = "Stopped watching."
SERVE_WATCHING = "Following {path} into case {case} every {interval:g} s"
API_WATCH_DONE = "Followed {records} new records"

# Copy / export of the view (report/viewexport.py, POST /api/export/view)
VIEWEXPORT_BAD_KIND = "kind must be one of: {kinds}"
VIEWEXPORT_BAD_FIELD = "Invalid or missing field: {field}"
VIEWEXPORT_BAD_LIMIT = "max_rows must be an integer from 1 to {limit}"
VIEWEXPORT_BAD_COLUMN = "Unknown column: {name}"
VIEWEXPORT_TOO_MANY_COLUMNS = "At most {limit} columns can be exported"
VIEWEXPORT_BAD_CHOICE = "{field} must be one of: {choices}"
VIEWEXPORT_BAD_KEY_TYPE = "Invalid key type"
VIEWEXPORT_VALUES_NEED_SOURCE = "values needs exactly one of 'column' or 'key'"
VIEWEXPORT_BAD_EVENT_FILTER = "events must be an object with the event list parameters"
VIEWEXPORT_CUT_NOTICE = "[... {count} more characters of this record are not exported]"
VIEWEXPORT_CSV_HEADERS = {
    "id": "Record",
    "ts": "Time",
    "pid": "PID",
    "tid": "TID",
    "level": "Level",
    "template": "Template",
    "source": "Source",
    "collection": "Collection",
    "msg": "Message",
}
VIEWEXPORT_EVENT_HEADERS = (
    "Event", "Operation", "Kind", "Status", "Duration (ms)", "Start", "End", "Key", "Operation key",
    "TID", "Family", "Process", "Source", "Collection", "Records",
)  # fmt: skip

# Collections of a case (U21: a case groups ERS captures, folders and files; store/collections.py)
COLLECTION_DUPLICATE = (
    "this ERS ({guid}, captured {capture}) is already in the case from another file; "
    "add the same file again to resume or refresh it"
)
API_COLLECTION_NOT_FOUND = "Collection {group} does not exist in this case"
API_COLLECTION_FOLLOWED = "This folder is being followed (serve --watch); stop following it first"
API_BAD_QUEUE_OPTION = "message_queue must be true or false"
API_QUEUE_PATH_MISSING = "The collection's file or folder is no longer at its registered path"
API_COLLECTION_REMOVED = "Removed the collection: {records} records from {sources} sources"
EXPORT_SOURCE_MULTI = "Sources: {count} collections"
EXPORT_COLLECTION_ERS = "  {tag}: ERS {ers_ts} ({machine}) - {variant} - GUID {guid}"
EXPORT_COLLECTION_PLAIN = "  {tag}: {kind} ({machine})"
EXPORT_TIME_NOTE_MULTI = "Each collection keeps its own clock: the order across collections is approximate."
# File picker and folder browser (server/filepick.py, /api/fs/*)
FS_BAD_PATH = "path must be a non-empty text of at most {limit} characters"
FS_PATH_NOT_TEXT = "The path contains control characters or bytes that are not valid text"
FS_PATH_NOT_ABSOLUTE = "The path must be absolute (for example C:\\Logs or /home/user/logs)"
FS_DEVICE_PATH = "Device and extended-length paths (\\\\?\\, \\\\.\\) are not supported"
FS_UNC_PATH = "Network paths (\\\\server\\share) cannot be browsed; map the share to a drive letter"
FS_PATH_BAD_CHARS = "The path contains characters that Windows does not allow in names"
FS_PATH_TRAILING_DOT = "A name in the path ends with a space or a dot, which Windows does not keep"
FS_RESERVED_NAME = "The path contains a name Windows reserves for devices (CON, NUL, COM1...)"
FS_NOT_FOUND = "The path does not exist"
FS_NOT_A_DIRECTORY = "The path is not a folder"
FS_NOT_A_ZIP = "The path is not a .zip file"
FS_PERMISSION_DENIED = "Permission denied"
FS_UNREADABLE = "The path could not be read"
FS_BAD_PICK_KIND = "kind must be 'zip' or 'folder'"
FS_BAD_PICK_MULTIPLE = "multiple must be true or false"
FS_UNKNOWN_FIELD = "Unknown field {field!r}"
FS_PICKER_UNAVAILABLE = "No native file dialog is available here; use the built-in folder browser"
FS_PICKER_BUSY = "A file dialog is already open"
FS_PICKER_FAILED = "The native file dialog failed; use the built-in folder browser"
FS_DIALOG_TITLE_ZIP = "dmslog - Select ERS archives (.zip)"
FS_DIALOG_TITLE_FOLDER = "dmslog - Select a folder"
FS_DIALOG_FILTER_ZIP = "ERS archives"
FS_DIALOG_FILTER_ALL = "All files"
# Distinct values of a field (store/distinct.py, POST /api/entries/distinct).
DISTINCT_ERRORS = {
    "bad_field": (
        "unknown field {name!r} (expected tpl, source, sev, level, tid, pid, collection, "
        "fields.<name> or key.<type>)"
    ),
    "bad_top": "limit must be an integer from 1 to {limit}",
    "bad_span": "span must be true or false",
    "bad_exact": "exact must be true or false",
}
# Highlight rules (store/highlights.py, /api/highlights and /api/entries/highlight).
API_BAD_HIGHLIGHT_ID = "Invalid highlight rule id"
API_HIGHLIGHT_NEXT_TIMEOUT = (
    "No highlighted record was found in time; narrow the view (sources or time) and try again"
)
# Local alerts of the follow mode (store/alerts.py, /api/alerts/*).
ALERT_ERRORS = {
    "bad_rule": "an alert rule must be an object with name, kind and the fields of its kind",
    "bad_name": "the name must be a non-empty text of at most {limit} characters",
    "bad_kind": "the kind must be 'match', 'threshold' or 'open_operation'",
    "bad_severity": "the severity must be 'info', 'warning' or 'critical'",
    "bad_enabled": "enabled must be true or false",
    "bad_interval": "min_interval_s must be an integer from {lo} to {hi}",
    "bad_count": "the count must be an integer from {lo} to {hi}",
    "bad_window": "window_s must be an integer from {lo} to {hi}",
    "bad_timeout": "timeout_s must be an integer from {lo} to {hi}",
    "bad_timeout_source": "give exactly one of timeout_s and timeout_param",
    "bad_timeout_param": "timeout_param must be a Registry parameter name (letters, digits, underscore)",
    "bad_operation": "operation must be a non-empty text of at most {limit} characters",
    "bad_filter": "the filter must be a non-empty JSON object",
    "filter_invalid": "the filter is not valid: {detail}",
    "filter_too_large": "the filter is larger than {limit} bytes",
    "case_specific": "the filter uses {leaf!r}, which points at one case; a rule must work in any case",
    "extra_too_large": "the unknown fields of the rule are too large or too deeply nested",
    "duplicate_name": "an alert rule named {name!r} already exists",
    "too_many_rules": "at most {limit} alert rules can be kept",
    "not_found": "alert rule {name!r} does not exist",
    "file_invalid": "the alerts file is not valid and was left untouched; fix or remove it first",
    "file_too_large": "the alerts file would be larger than {limit} bytes; delete some rules",
    "save_failed": "the alerts file could not be written",
}
ALERT_TIMEOUT_UNAVAILABLE = (
    "The case has no value for this timeout parameter, so the rule cannot be evaluated; use a fixed timeout"
)
# User-built panels (store/panels.py, store/panelrun.py, /api/panels/*; ARQUITETURA 10.14 draft).
PANEL_ERRORS = {
    "bad_panel": "a panel must be an object with a name and a list of widgets",
    "unknown_key": "an object has a key that is not allowed here",
    "bad_name": "the name must be a non-empty text of at most {limit} characters",
    "bad_widgets": "widgets must be a list of at most {limit} widgets",
    "bad_widget": "a widget must be an object with id, title, query, chart and options",
    "bad_widget_id": "a widget id must be 1 to 32 characters: lowercase letters, digits, '-' or '_'",
    "duplicate_widget": "two widgets of the panel share an id",
    "bad_title": "the title must be a text of at most {limit} characters",
    "bad_chart": "the chart must be 'bar', 'line', 'table' or 'stat'",
    "bad_options": "options must be an object with only the keys allowed for the chart",
    "bad_option": "an option has a value that does not fit the chart or the query",
    "chart_needs": "the chart does not fit the query's grouping",
    "bad_query": "the query is not valid ({reason})",
    "query_too_large": "the query is too large, too deeply nested or holds a value out of range",
    "bad_data": "the panel is too large, too deeply nested or holds a value that is out of range",
    "case_specific": "the query filter points at one case; a panel must work in any case",
    "scan_needs_scope": "text and regex conditions need a source or time scope, which a panel cannot hold",
    "too_many_rows": "the widgets ask for more than {limit} rows in total",
    "panel_too_large": "the panel is larger than {limit} bytes",
    "duplicate_name": "a panel with this name already exists",
    "too_many_panels": "at most {limit} panels can be kept",
    "not_found": "the panel does not exist",
    "unknown_widget": "the panel has no such widget",
    "rev_conflict": "the panel was changed meanwhile; reload it and apply the change again",
    "bad_id": "the panel id is missing or malformed",
    "file_invalid": "the panels file is not valid and was left untouched; fix or remove it first",
    "file_too_large": "the panels file would be larger than {limit} bytes; delete some panels",
    "save_failed": "the panels file could not be written",
    "busy": "another process is changing the panels file; try again",
    "rev_limit": "the panel reached the largest revision number and cannot be changed again",
}
# Per-widget outcome of a panel run: static texts, never the query or any log text.
PANEL_WIDGET_ERRORS = {
    "invalid_query": "the widget's query is not valid for this case",
    "unknown_field": "a field or key used by the widget does not exist in this case",
    "timeout": "the widget's query did not finish in time; narrow its filter",
    "failed": "the widget's query failed",
    "budget": "the panel's time budget ran out before this widget",
}
# Declarative aggregation (store/aggregate.py, POST /api/entries/aggregate; ARQUITETURA 10.6).
AGGREGATE_ERRORS = {
    "bad_type": "expected {expected}",
    "bad_rows": "rows must be 'entries' or 'events'",
    "bad_dimension": "unknown dimension {name!r} for these rows",
    "bad_bucket": "unknown time bucket {name!r} (expected bucket:<width>, width one of {widths})",
    "bad_measure": "unknown measure {name!r} (expected count, distinct:<dimension> or <function>:<value>)",
    "bad_value": "unknown value {name!r} for these rows (expected {values})",
    "needs_events": "{name!r} needs rows = 'events'",
    "too_many_dims": "group_by takes at most {limit} dimensions",
    "too_many_measures": "measures takes at most {limit} items",
    "no_measure": "give at least one measure",
    "duplicate": "{name!r} appears more than once",
    "one_bucket": "group_by takes at most one time bucket",
    "too_many_keys": "at most {limit} key types per request part",
    "key_with_numeric": "distinct of a key cannot be combined with numeric measures",
    "bad_order": "order.by must be a dimension or a measure of the request, not {name!r}",
    "bad_direction": "order.dir must be 'asc' or 'desc'",
    "bad_limit": "limit must be an integer from 1 to {limit}",
    "bad_lookup": "unknown lookup {name!r} (expected one of {lookups})",
    "lookup_needs": "lookup {name!r} needs the dimension {dim!r}",
    "too_many_lookups": "lookups takes at most {limit} items",
    "bad_exact": "exact must be true or false",
    "events_needs_events": "events conditions need rows = 'events'",
    "bad_events": "unknown event condition {name!r} (expected kind, name or status)",
    "bad_events_list": "an event condition takes 1 to {limit} values",
    "bad_events_value": "an event condition value is printable text of 1 to 200 characters",
    "bad_format": "format must be 'json' or 'csv'",
    "bad_bom": "bom must be true or false",
}
API_AGGREGATE_TIMEOUT = (
    "The aggregation did not finish in time, not even as an estimate; narrow the filter (sources or time)"
    " and try again"
)
API_AGGREGATE_TIMEOUT_ROWS = (
    "The aggregation did not finish in time, not even as an estimate (up to about {rows} rows to"
    " aggregate); narrow the filter (sources or time) and try again"
)
AGGREGATE_CSV_DIMS = {
    "tpl": "Message type", "source": "Source", "collection": "Collection", "family": "Family",
    "sev": "Severity", "level": "Level", "tid": "TID", "pid": "PID", "name": "Operation", "kind": "Kind",
    "status": "Status", "key": "Event key", "bucket": "Time ({width})",
}  # fmt: skip
AGGREGATE_CSV_MEASURES = {
    "count": "Count", "distinct": "Distinct {arg}", "min": "Min {arg}", "max": "Max {arg}",
    "avg": "Average {arg}", "sum": "Sum {arg}", "p50": "p50 {arg}", "p90": "p90 {arg}", "p95": "p95 {arg}",
    "p99": "p99 {arg}",
}  # fmt: skip
AGGREGATE_CSV_VALUES = {"duration": "duration (ms)", "entries": "records"}
AGGREGATE_CSV_LOOKUPS = {
    "error.reason": "Error reason (pack)", "error.severity": "Error severity (pack)",
    "operation.direction": "Operation direction (pack)", "operation.service": "Operation service (pack)",
}  # fmt: skip
# Comparison of views by message type (store/viewcompare.py, POST /api/entries/compare; ARQUITETURA 10.10).
COMPARE_ERRORS = {
    "bad_type": "expected {expected}",
    "bad_mode": "mode must be one of {modes}",
    "bad_side": "a side is an object with the keys of the mode {mode!r}",
    "bad_side_key": "unknown key for the mode {mode!r} (expected {keys})",
    "missing_side_key": "the mode {mode!r} needs {key!r} on each side",
    "bad_window": "from and to are integers (ms of the log clock) with from < to",
    "bad_id": "expected a positive integer id",
    "bad_max_side": "max_side must be an integer from {lo} to {hi}",
    "bad_offset": "offset must be an integer from {lo} to {hi}",
    "bad_limit": "limit must be an integer from {lo} to {hi}",
    "bad_only_diff": "only_diff must be true or false",
    "event_not_found": "event {id} does not exist in this case",
    "stream_not_found": "process (stream) {id} does not exist in this case",
}
# Ad hoc extraction by regex (store/extract.py, parse/extractor.py, /api/extract/check and
# /api/entries/extract*; ROADMAP 9.8).
EXTRACT_ERRORS = {
    "bad_regex": "invalid regex: {detail}",
    "regex_too_costly": "the regex is too costly: {detail}",
    "bad_target": "target must be 'msg' (first line) or 'text' (whole record)",
    "bad_types": "invalid types: {detail}",
    "bad_type": "expected {expected}",
    "bad_limit": "limit must be an integer from 1 to {limit}",
    "bad_top": "top must be an integer from 1 to {limit}",
    "bad_ids": "ids must be a list of 1 to {limit} record ids",
    "unknown_member": "unknown member {name!r}",
    "bad_family": "family must be a family code such as AWFM",
    "bad_code": "code must look like ABC-001 (A-Z, 0-9, '-')",
    "promote_needs_msg": "only a regex on the first line (target 'msg') can become a template",
    "promote_no_match": (
        "the regex matches none of the given records (records longer than its window are skipped)"
    ),
    "mixed_families": "the records come from several families (or none); give the family",
    "result_too_large": (
        "the captured values pass {limit} characters; capture shorter parts or lower the number of records"
    ),
}
API_EXTRACT_TIMEOUT = (
    "The extraction did not finish in time; narrow the filter (sources or time) or lower the number of"
    " records"
)
PROMOTE_WARNINGS = {
    "lead_varies": "The text before the match differs between the examples; the template starts with '.*?'",
    "code_exists": "The code is already used by a packaged template; choose another one",
    "already_classified": "Some examples already have a template; the new one only applies to records no"
    " earlier template matches",
    "cut_skipped": "Records longer than the regex window were not used as examples",
}
