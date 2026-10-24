"""Schema of the knowledge pack and its validator (see PACK_SCHEMA.md).

Stdlib only. ``validate_pack(directory)`` returns a list of error strings; an empty list means
the pack is valid.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SCHEMA_VERSION = 1

PACK_FILES = ("adapters.json", "error_codes.json", "manifest.json", "operations.json", "parameters.json")

# Optional files: absent is fine (older pack), invalid is reported for that file only.
RT_SERVICES_FILE = "rt_services.json"
OPTIONAL_PACK_FILES = (RT_SERVICES_FILE,)

RT_FAMILIES = ("ARBIP", "DMSCORE", "DMSFH", "DMSJOB", "DMSMODEL", "DMSOMS", "DMSRT", "ERS", "NMC", "PSENGINE")
RT_MONITORED = ("cpu", "hdd", "ram")

# Hard limits of rt_services.json (a hand-edited or hostile file stays small and bounded).
RT_MAX_FILE_BYTES = 1_000_000
RT_MAX_SERVICES = 128
RT_MAX_REFERENCES = 8
RT_MAX_COUNTERS = 64
RT_MAX_SUMMARY = 240  # characters, one sentence
RT_MAX_TEXT = 300  # any other short text field

ADAPTER_TYPES = ("soap", "files", "rest", "multispeak", None)
DIRECTIONS = ("inbound", "outbound", None)

# Field name -> (accepted types, nullable). "str" also covers the empty string.
_ADAPTER_FIELDS = {
    "acronym": (str, False),
    "title": (str, False),
    "adapter": (str, True),
    "type": (str, True),
    "type_label": (str, True),
    "operations": (list, False),
    "config_note": (str, True),
    "document_title": (str, True),
    "document_version": (str, True),
    "aliases": (list, False),
    "index_note": (str, True),
    "process_names": (list, False),
}
_ERROR_FIELDS = {
    "acronym": (str, False),
    "adapter": (str, False),
    "block": (str, False),
    "id": (str, True),
    "code": (str, False),
    "adms_severity": (str, False),
    "log": (str, True),
    "dms_reason": (str, True),
    "dms_details": (str, True),
    "cim_severity": (str, True),
    "cim_reason": (str, True),
    "cim_details": (str, True),
}
_OPERATION_FIELDS = {
    "acronym": (str, False),
    "adapter": (str, False),
    "wsdl": (str, False),
    "service": (str, False),
    "operation": (str, False),
    "soap_action": (str, False),
    "input": (str, False),
    "output": (str, False),
    "endpoint": (str, False),
    "direction": (str, True),
}
_PARAMETER_FIELDS = {
    "acronym": (str, False),
    "adapter": (str, False),
    "tab": (str, False),
    "block": (str, False),
    "name": (str, False),
    "description": (str, False),
    "value_type": (str, False),
    "default": (str, False),
    "domain": (str, False),
    "log_relevant": (bool, False),
}
_MANIFEST_FIELDS = {
    "schema_version": (int, False),
    "generator_version": (str, False),
    "vault_commit": (str, True),
    "notice": (str, False),
    "files": (dict, False),
}

_RT_SERVICE_FIELDS = {
    "process": (str, False),
    "family": (str, False),
    "title": (str, True),
    "summary": (str, True),
    "note": (str, True),
    "section": (str, True),
    "install_dir": (str, True),
    "monitored": (list, False),
    "counters": (list, False),
    "usage_events": (int, False),
    "references": (list, False),
}
_RT_REFERENCE_FIELDS = {
    "doc": (str, False),
    "note": (str, False),
    "section": (str, True),
    "mentions": (int, False),
}

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROCESS = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_SECTION = re.compile(r"^[0-9]{1,3}(\.[0-9]{1,3}){0,8}$")
_DOC = re.compile(r"^[A-Z]{4}$")


def _check_fields(record, fields, where, errors):
    if not isinstance(record, dict):
        errors.append(f"{where}: expected an object")
        return
    for name, (kind, nullable) in fields.items():
        if name not in record:
            errors.append(f"{where}: missing field '{name}'")
            continue
        value = record[name]
        if value is None:
            if not nullable:
                errors.append(f"{where}: field '{name}' must not be null")
        elif kind is int and isinstance(value, bool) or not isinstance(value, kind):
            errors.append(f"{where}: field '{name}' must be {kind.__name__}")
    for name in record:
        if name not in fields:
            errors.append(f"{where}: unexpected field '{name}'")


def _check_str_list(value, where, errors):
    if isinstance(value, list) and not all(isinstance(item, str) for item in value):
        errors.append(f"{where}: must be a list of strings")


def _load(directory: Path, name: str, errors):
    path = directory / name
    if not path.is_file():
        errors.append(f"{name}: file is missing")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"{name}: cannot be read as JSON ({exc})")
        return None


def _validate_list_file(directory, name, key, fields, errors):
    data = _load(directory, name, errors)
    if data is None:
        return None, []
    if not isinstance(data, dict) or set(data) - {key, "process_map"} or key not in data:
        errors.append(f"{name}: top level must be an object with the key '{key}'")
        return data, []
    rows = data[key]
    if not isinstance(rows, list):
        errors.append(f"{name}: '{key}' must be a list")
        return data, []
    for index, row in enumerate(rows):
        _check_fields(row, fields, f"{name}[{index}]", errors)
    return data, rows


def _check_text(value, where, errors, limit=RT_MAX_TEXT):
    if not isinstance(value, str):
        return
    if len(value) > limit:
        errors.append(f"{where}: longer than {limit} characters")
    if "\n" in value or "\r" in value or "\x00" in value:
        errors.append(f"{where}: must be a single line")


def _check_note_path(value, where, errors):
    """A vault-relative POSIX note path: no absolute path, no drive, no backslash, no ``..``."""
    if not isinstance(value, str):
        return
    _check_text(value, where, errors)
    parts = value.split("/")
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or ":" in value
        or any(part in ("", ".", "..") for part in parts)
        or not value.endswith(".md")
    ):
        errors.append(f"{where}: must be a relative .md path without '..'")


def validate_rt_services(directory) -> list[str]:
    """Validate the optional ``rt_services.json``; absent file = no errors (older pack)."""
    path = Path(directory) / RT_SERVICES_FILE
    name = RT_SERVICES_FILE
    if not path.is_file():
        return []
    errors: list[str] = []
    try:
        if path.stat().st_size > RT_MAX_FILE_BYTES:
            return [f"{name}: larger than {RT_MAX_FILE_BYTES} bytes"]
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{name}: cannot be read as JSON ({exc})"]
    if not isinstance(data, dict) or set(data) != {"rt_services"}:
        return [f"{name}: top level must be an object with the single key 'rt_services'"]
    rows = data["rt_services"]
    if not isinstance(rows, list):
        return [f"{name}: 'rt_services' must be a list"]
    if len(rows) > RT_MAX_SERVICES:
        return [f"{name}: more than {RT_MAX_SERVICES} services"]
    seen: set[str] = set()
    previous = None
    for index, row in enumerate(rows):
        where = f"{name}[{index}]"
        before = len(errors)
        _check_fields(row, _RT_SERVICE_FIELDS, where, errors)
        if not isinstance(row, dict) or len(errors) > before:
            continue
        process = row["process"]
        if not _PROCESS.match(process):
            errors.append(f"{where}: field 'process' is not a valid process name")
        if process in seen:
            errors.append(f"{where}: duplicate process '{process}'")
        elif previous is not None and process < previous:
            errors.append(f"{where}: rows must be sorted by process")
        seen.add(process)
        previous = process
        if row["family"] not in RT_FAMILIES:
            errors.append(f"{where}: field 'family' has an unknown value")
        for field in ("title", "install_dir"):
            _check_text(row[field], f"{where}.{field}", errors)
        _check_text(row["summary"], f"{where}.summary", errors, RT_MAX_SUMMARY)
        if row["note"] is not None:
            _check_note_path(row["note"], f"{where}.note", errors)
        if row["section"] is not None and not _SECTION.match(row["section"]):
            errors.append(f"{where}: field 'section' is not a section number")
        if isinstance(row["usage_events"], bool) or row["usage_events"] < 0:
            errors.append(f"{where}: field 'usage_events' must be a non-negative integer")
        monitored = row["monitored"]
        _check_str_list(monitored, f"{where}.monitored", errors)
        if all(isinstance(item, str) for item in monitored) and (
            len(set(monitored)) != len(monitored) or not set(monitored) <= set(RT_MONITORED)
        ):
            errors.append(f"{where}: field 'monitored' must hold distinct values of {list(RT_MONITORED)}")
        counters = row["counters"]
        _check_str_list(counters, f"{where}.counters", errors)
        if len(counters) > RT_MAX_COUNTERS:
            errors.append(f"{where}: field 'counters' has more than {RT_MAX_COUNTERS} items")
        for item in counters[:RT_MAX_COUNTERS]:
            _check_text(item, f"{where}.counters", errors)
        references = row["references"]
        if len(references) > RT_MAX_REFERENCES:
            errors.append(f"{where}: field 'references' has more than {RT_MAX_REFERENCES} items")
        for ref_index, reference in enumerate(references[:RT_MAX_REFERENCES]):
            ref_where = f"{where}.references[{ref_index}]"
            before = len(errors)
            _check_fields(reference, _RT_REFERENCE_FIELDS, ref_where, errors)
            if not isinstance(reference, dict) or len(errors) > before:
                continue
            if not _DOC.match(reference["doc"]):
                errors.append(f"{ref_where}: field 'doc' must be a four-letter document code")
            _check_note_path(reference["note"], f"{ref_where}.note", errors)
            section = reference["section"]
            if section is not None and not _SECTION.match(section):
                errors.append(f"{ref_where}: field 'section' is not a section number")
            if isinstance(reference["mentions"], bool) or reference["mentions"] < 1:
                errors.append(f"{ref_where}: field 'mentions' must be a positive integer")
    return errors


def validate_pack(directory, include_optional: bool = True) -> list[str]:
    """Validate the pack in ``directory`` and return error strings (empty = valid).

    ``include_optional`` also validates the optional files (``rt_services.json``); the run-time
    loader passes False and handles them on their own, so a bad optional file never hides the pack.
    """
    directory = Path(directory)
    errors: list[str] = []
    if not directory.is_dir():
        return [f"{directory}: not a directory"]

    manifest = _load(directory, "manifest.json", errors)
    if manifest is not None:
        _check_fields(manifest, _MANIFEST_FIELDS, "manifest.json", errors)
        if isinstance(manifest, dict):
            if manifest.get("schema_version") != SCHEMA_VERSION:
                errors.append(f"manifest.json: schema_version must be {SCHEMA_VERSION}")
            files = manifest.get("files")
            if isinstance(files, dict):
                for rel, digest in files.items():
                    if not isinstance(digest, str) or not _SHA256.match(digest):
                        errors.append(f"manifest.json: files['{rel}'] must be a sha256 hex digest")
                    if not rel.endswith(".md"):
                        errors.append(f"manifest.json: files['{rel}'] is not a .md file")

    adapters_data, adapters = _validate_list_file(
        directory, "adapters.json", "adapters", _ADAPTER_FIELDS, errors
    )
    acronyms = set()
    for index, adapter in enumerate(adapters):
        if not isinstance(adapter, dict):
            continue
        where = f"adapters.json[{index}]"
        acronyms.add(adapter.get("acronym"))
        if adapter.get("type") not in ADAPTER_TYPES:
            errors.append(f"{where}: field 'type' has an unknown value")
        for name in ("operations", "aliases", "process_names"):
            _check_str_list(adapter.get(name), f"{where}.{name}", errors)
    if isinstance(adapters_data, dict):
        process_map = adapters_data.get("process_map")
        if not isinstance(process_map, dict):
            errors.append("adapters.json: 'process_map' must be an object")
        else:
            for process, acronym in process_map.items():
                if acronym not in acronyms:
                    errors.append(f"adapters.json: process_map['{process}'] points to unknown acronym")

    _, operations = _validate_list_file(directory, "operations.json", "operations", _OPERATION_FIELDS, errors)
    for index, row in enumerate(operations):
        if isinstance(row, dict) and row.get("direction") not in DIRECTIONS:
            errors.append(f"operations.json[{index}]: field 'direction' has an unknown value")

    families = (
        ("error_codes.json", "error_codes", _ERROR_FIELDS),
        ("parameters.json", "parameters", _PARAMETER_FIELDS),
    )
    for name, key, fields in families:
        _validate_list_file(directory, name, key, fields, errors)

    for name, key in (
        ("operations.json", "operations"),
        ("error_codes.json", "error_codes"),
        ("parameters.json", "parameters"),
    ):
        data = _load(directory, name, [])
        if isinstance(data, dict) and isinstance(data.get(key), list):
            for index, row in enumerate(data[key]):
                if isinstance(row, dict) and acronyms and row.get("acronym") not in acronyms:
                    errors.append(f"{name}[{index}]: acronym '{row.get('acronym')}' is not in adapters.json")
    if include_optional:
        errors.extend(validate_rt_services(directory))
    return errors
