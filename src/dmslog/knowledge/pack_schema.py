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

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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


def validate_pack(directory) -> list[str]:
    """Validate the pack in ``directory`` and return error strings (empty = valid)."""
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

    for name, key in (("operations.json", "operations"), ("error_codes.json", "error_codes"),
                      ("parameters.json", "parameters")):
        data = _load(directory, name, [])
        if isinstance(data, dict) and isinstance(data.get(key), list):
            for index, row in enumerate(data[key]):
                if isinstance(row, dict) and acronyms and row.get("acronym") not in acronyms:
                    errors.append(f"{name}[{index}]: acronym '{row.get('acronym')}' is not in adapters.json")
    return errors
