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
HELP_INFO = "Show version, environment, data directory and the cases found there."
HELP_INGEST = "Index the logs of an ERS zip, folder or file into a case (safe to run again)."
HELP_INGEST_PATH = "ERS zip, Log folder or log file to index."
HELP_INGEST_CASE = "Case name (default: derived from the ERS GUID and capture time, or from the folder name)."
HELP_INGEST_ALL = "Index every text log of the collection, not only the default scope."
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
INGEST_NOT_FOUND = "cannot ingest: {error}"
INGEST_UNSAFE = "cannot ingest: unsafe or invalid archive: {error}"
INGEST_CONFLICT = "cannot add this collection to case {case}: {error}"
INGEST_INVALID_CASE = "{error}"

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
