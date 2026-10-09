"""Loader for the generated knowledge pack (ARQUITETURA 7.1).

The pack lives in ``dmslog/knowledge/pack/*.json`` and is read through ``importlib.resources``, so it
works from a source tree, an installed package and inside ``dmslog.pyz``. It is loaded lazily, once
per process, and validated with ``validate_pack`` on first load (an invalid pack raises
``PackError``). The dataclasses are frozen: the pack is read-only.
"""

from __future__ import annotations

import json
import tempfile
import threading
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from dmslog.knowledge.pack_schema import PACK_FILES, validate_pack


class PackError(Exception):
    """The knowledge pack is missing, unreadable or does not match the schema."""


@dataclass(frozen=True, slots=True)
class AdapterInfo:
    acronym: str
    title: str
    adapter: str | None
    type: str | None
    type_label: str | None
    operations: tuple[str, ...]
    config_note: str | None
    index_note: str | None
    document_title: str | None
    document_version: str | None
    aliases: tuple[str, ...]
    process_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OperationInfo:
    acronym: str
    adapter: str
    wsdl: str
    service: str
    operation: str
    soap_action: str
    input: str
    output: str
    endpoint: str
    direction: str | None


@dataclass(frozen=True, slots=True)
class VaultError:
    acronym: str
    adapter: str
    block: str
    id: str | None
    code: str
    adms_severity: str
    log: str | None
    dms_reason: str | None
    dms_details: str | None
    cim_severity: str | None
    cim_reason: str | None
    cim_details: str | None


@dataclass(frozen=True, slots=True)
class VaultParam:
    acronym: str
    adapter: str
    tab: str
    block: str
    name: str
    description: str
    value_type: str
    default: str
    domain: str
    log_relevant: bool


@dataclass(frozen=True, slots=True)
class Manifest:
    schema_version: int
    generator_version: str
    vault_commit: str | None
    notice: str


@dataclass(frozen=True, slots=True)
class Pack:
    manifest: Manifest
    adapters: tuple[AdapterInfo, ...]
    process_map: dict[str, str]
    operations: tuple[OperationInfo, ...]
    errors: tuple[VaultError, ...]
    params: tuple[VaultParam, ...]

    def adapter(self, acronym: str) -> AdapterInfo | None:
        for item in self.adapters:
            if item.acronym == acronym:
                return item
        return None


def _rows(data: dict[str, Any], key: str, cls: type) -> tuple:
    return tuple(cls(**row) for row in data[key])


def _adapter(row: dict[str, Any]) -> AdapterInfo:
    row = dict(row)
    for name in ("operations", "aliases", "process_names"):
        row[name] = tuple(row[name])
    return AdapterInfo(**row)


def _read_json_texts() -> dict[str, str]:
    root = resources.files("dmslog.knowledge") / "pack"
    texts: dict[str, str] = {}
    for name in PACK_FILES:
        try:
            texts[name] = (root / name).read_text(encoding="utf-8")
        except (OSError, FileNotFoundError) as exc:
            raise PackError(f"knowledge pack: cannot read {name} ({exc})") from exc
    return texts


def load_pack_from(directory: Path | str) -> Pack:
    """Load and validate a pack from a real directory (used by tests and by ``load_pack``)."""
    directory = Path(directory)
    problems = validate_pack(directory)
    if problems:
        shown = "; ".join(problems[:5])
        extra = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
        raise PackError(f"invalid knowledge pack in {directory}: {shown}{extra}")
    data = {
        name: json.loads((directory / name).read_text(encoding="utf-8")) for name in PACK_FILES
    }
    return _build(data)


def _build(data: dict[str, Any]) -> Pack:
    manifest = data["manifest.json"]
    return Pack(
        manifest=Manifest(
            schema_version=manifest["schema_version"],
            generator_version=manifest["generator_version"],
            vault_commit=manifest["vault_commit"],
            notice=manifest["notice"],
        ),
        adapters=tuple(_adapter(r) for r in data["adapters.json"]["adapters"]),
        process_map=dict(data["adapters.json"]["process_map"]),
        operations=_rows(data["operations.json"], "operations", OperationInfo),
        errors=_rows(data["error_codes.json"], "error_codes", VaultError),
        params=_rows(data["parameters.json"], "parameters", VaultParam),
    )


_lock = threading.Lock()
_cached: Pack | None = None


def load_pack() -> Pack:
    """The packaged pack, loaded and validated once per process."""
    global _cached
    if _cached is None:
        with _lock:
            if _cached is None:
                # validate_pack needs a real directory; inside the pyz the files are copied to a
                # temporary one (a few hundred kB, once per process).
                texts = _read_json_texts()
                with tempfile.TemporaryDirectory(prefix="dmslog-pack-") as tmp:
                    for name, text in texts.items():
                        (Path(tmp) / name).write_text(text, encoding="utf-8", newline="\n")
                    _cached = load_pack_from(tmp)
    return _cached


def clear_cache() -> None:
    """Forget the cached pack (tests)."""
    global _cached
    _cached = None
