# Knowledge pack schema (version 1)

The pack is a set of deterministic JSON files generated from the vault by `tools/kb_build.py`
(see `docs/ARQUITETURA.md`, section 7.1). Every file is UTF-8, LF, `indent=2`, keys sorted, with a
trailing newline, and carries no timestamps, so two runs on the same vault are byte-identical.
`validate_pack(directory)` in `pack_schema.py` checks everything described here and returns a list of
error strings (empty = valid).

The pack is derived from product documentation: it is never committed nor shipped in a public
artifact. `tools/kb_build.py` writes it to `<data dir>/knowledge/pack/`, where `dmslog` finds it at run
time (also `--pack-dir`, `DMSLOG_PACK_DIR` or `knowledge/pack/` next to `dmslog.pyz`; see
`docs/ARQUITETURA.md`, D10). Without a pack the app runs without vault knowledge. The tests use the
synthetic `tests/fixtures/knowledge/mini_pack/`.

All examples below use synthetic values.

Conventions: text is copied from the vault as written (escaped pipes resolved, wiki links reduced to their
label, inline-code marks removed). `null` means "not stated"; an empty string means an empty table cell.
Rows are sorted by the keys listed per file. `acronym` is the document code of the adapter (`AWFM`, ...) and
links every file to `adapters.json`.

## adapters.json

```json
{
  "adapters": [
    {
      "acronym": "AAAA",
      "title": "Example Alpha Interface",
      "adapter": "Example Adapter",
      "type": "soap",
      "type_label": "SOAP/WSDL",
      "operations": ["CreatedExampleItems", "SendExampleItems"],
      "config_note": "12 Interfaces online/Example Alpha Interface/Arquivos/Configuration/AAAA Configuration.md",
      "document_title": "Example Suite Alpha Interface - Functional Specification",
      "document_version": "1.0 (January 2020)",
      "aliases": ["Example Adapter", "Example Alpha Interface"],
      "index_note": "12 Interfaces online/Example Alpha Interface/AAAA Index.md",
      "process_names": ["ExampleAdapter"]
    }
  ],
  "process_map": {"ExampleAdapter": "AAAA"}
}
```

| Field | Type | Meaning |
|---|---|---|
| `acronym` | string | Document code; unique; sort key. |
| `title` | string | Interface name. |
| `adapter` | string or null | Adapter (component) name. |
| `type` | `"soap"`, `"files"`, `"rest"`, `"multispeak"` or null | Normalized interface type. |
| `type_label` | string or null | Type exactly as written in the vault. |
| `operations` | list of strings | Operations listed in the overview (sorted). |
| `config_note`, `index_note` | string or null | Vault-relative path of the notes. |
| `document_title`, `document_version` | string or null | From the Index note and the overview table. |
| `aliases` | list of strings | Index note aliases (sorted). |
| `process_names` | list of strings | Process names (`<Name>Adapter`) found as prefixes of file names in the adapter configuration (sorted). |

`process_map` maps a process name to the acronym (process to adapter). It only holds what the vault states;
the rest is complemented by hand outside the generator.

## operations.json

Sorted by `acronym`, `service`, `operation`, `wsdl`, `endpoint`.

```json
{"operations": [{
  "acronym": "AAAA", "adapter": "Example Adapter", "wsdl": "SendExampleItemsService",
  "service": "SendExampleItems", "operation": "CreatedExampleItems",
  "soap_action": "http://example.org/SendExampleItems/CreatedExampleItems",
  "input": "CreatedExampleItems", "output": "ExampleResponseMessage",
  "endpoint": "http://example.org/SendExampleItems", "direction": "outbound"
}]}
```

All fields are strings except `direction`: `"inbound"` (service name starts with `Receive`, hosted by ADMS),
`"outbound"` (starts with `Send`) or null (heuristic from the service name; other services are not classified).

## error_codes.json

Sorted by `acronym`, `block`, `id`, `code`, `dms_reason`, `cim_reason`, `adms_severity`. The vault has two
table shapes; fields that a shape does not have are null.

```json
{"error_codes": [{
  "acronym": "AAAA", "adapter": "Example Adapter", "block": "Common",
  "id": null, "code": "1.8", "adms_severity": "FATAL", "log": null,
  "dms_reason": "InvalidMessage", "dms_details": "Received message is invalid. Reason: \"#reason\".",
  "cim_severity": null, "cim_reason": "InvalidMessage", "cim_details": "Invalid message."
}]}
```

| Field | Type | Meaning |
|---|---|---|
| `block` | string | Service or operation the group belongs to. |
| `id` | string or null | DMS ID (shape with `ID` column). |
| `code` | string | External system code (`1.8`). |
| `adms_severity` | string | ADMS severity as written (`FATAL`, `Error`, `WARNING`, ...). |
| `log` | string or null | `Log` column (ID shape). |
| `dms_reason`, `dms_details` | string or null | DMS reason/details (Code shape). |
| `cim_severity` | string or null | CIM severity (ID shape). |
| `cim_reason`, `cim_details` | string or null | CIM (external system) reason/details. |

## parameters.json

Sorted by `acronym`, `tab`, `block`, `name`.

```json
{"parameters": [{
  "acronym": "AAAA", "adapter": "Example Adapter", "tab": "Registry Configuration",
  "block": "Adapters Config Properties", "name": "LogLevel",
  "description": "Log levels: Fatal, Error, Info", "value_type": "String",
  "default": "DebugLog", "domain": "[Fatal, Error, Info]", "log_relevant": true
}]}
```

All fields are strings except `log_relevant` (boolean): true for the parameters that matter for log and timing
analysis: `AuditingPeriod`, `LogFileName`, `LogLevel`, `MessageLogLevel`, `ModelUpdatersLogLevel`,
`NumOfRetries`, `RetryTimeout`, `SendTimeout`, `WaitForCRSCallback`.

## rt_services.json (optional)

Catalog of RealTime services, sorted by `process`. The file may be absent (older pack): the app then has
no vault block for RT services. `schema_version` stays 1: an older app ignores the file, a newer app
treats a pack without it as "unknown". If the file exists but is invalid, only the RT block is dropped
(`Pack.rt_services_error` says why); the rest of the pack loads normally.

```json
{"rt_services": [{
  "process": "DMS_ExampleScheduler",
  "family": "DMSJOB",
  "title": "Example Scheduler",
  "summary": "Synthetic first sentence of the section.",
  "note": "01 Example/Example Architecture/Example Service Notes.md",
  "section": "1.2.3",
  "install_dir": "Example\\bin",
  "monitored": ["cpu", "ram"],
  "counters": ["Example Queue Count"],
  "usage_events": 3,
  "references": [
    {"doc": "EXMP", "note": "02 Example/EXMP 4 Example Procedure.md", "section": "4.1.2", "mentions": 2}
  ]
}]}
```

| Field | Type | Rule |
|---|---|---|
| `process` | string | Unique, sorted; letters, digits, `_`, `.`, `-`, at most 64 characters. |
| `family` | string | One of `ARBIP`, `DMSCORE`, `DMSFH`, `DMSJOB`, `DMSMODEL`, `DMSOMS`, `DMSRT`, `ERS`, `NMC`, `PSENGINE`. |
| `title`, `install_dir` | string or null | Single line, at most 300 characters. |
| `summary` | string or null | First sentence of the section, single line, at most 240 characters. |
| `note` | string or null | Vault-relative POSIX path of a `.md` note: no `..`, no leading `/`, no backslash or drive. |
| `section` | string or null | Section number such as `1.2.3` (digits and dots only). |
| `monitored` | list of strings | Distinct values from `cpu`, `ram`, `hdd`. |
| `counters` | list of strings | At most 64 names, each a single line of at most 300 characters. |
| `usage_events` | integer | 0 or more. |
| `references` | list (at most 8) | Objects with exactly `doc` (four capital letters), `note` (same rule as above), `section` (string or null) and `mentions` (integer, 1 or more). Pointers only, never text. |

The file holds at most 128 services and 1,000,000 bytes; any other key, missing field or wrong type makes
the file invalid. `validate_rt_services(directory)` checks it alone; `validate_pack(directory)` includes it.

## manifest.json

```json
{
  "schema_version": 1,
  "generator_version": "1.0.0",
  "vault_commit": "0123456789abcdef0123456789abcdef01234567",
  "notice": "This pack is derived from Schneider Electric documentation ... must stay private ...",
  "files": {"12 Interfaces online/Interfaces online.md": "<sha256 hex of the file bytes>"}
}
```

`vault_commit` is the HEAD of the git repository that contains the vault, or null. `files` lists every vault
file read (vault-relative POSIX path, sorted) with its sha256. There is no date: the output only changes when
the vault or the generator changes.
