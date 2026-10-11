"""Loader for the knowledge pack (ARQUITETURA 7.1).

The pack is derived from product documentation, so it is NOT part of the repository nor of the
public artifacts (ARQUITETURA D10). It is installed locally and found at run time, in this order
(``pack_candidates``):

1. ``--pack-dir DIR`` (``configure(pack_dir=...)``), then the ``DMSLOG_PACK_DIR`` environment
   variable. An explicit location is the only candidate: there is no fallback when it is wrong.
2. ``<data dir>/knowledge/pack/`` (``%LOCALAPPDATA%\\dmslog`` or ``$XDG_DATA_HOME/dmslog``).
3. ``knowledge/pack/`` next to ``dmslog.pyz`` (portable folder or Linux tarball).

The first implicit candidate that exists and is not empty is used; it is not skipped when invalid.
Without a pack the app works without vault knowledge: ``pack_status()`` says ``not_installed`` (or
``invalid``), ``current_pack()`` returns ``None`` and ``load_pack()`` raises ``PackError``. The pack is
loaded lazily, once per process, and validated with ``validate_pack``. The dataclasses are frozen.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dmslog.knowledge.pack_schema import PACK_FILES, validate_pack

PACK_ENV = "DMSLOG_PACK_DIR"
PACK_SUBDIR = ("knowledge", "pack")

STATE_INSTALLED = "installed"
STATE_NOT_INSTALLED = "not_installed"
STATE_INVALID = "invalid"

SOURCE_OPTION = "option"
SOURCE_ENV = "env"
SOURCE_DATA_DIR = "data_dir"
SOURCE_PORTABLE = "portable"
SOURCE_GIVEN = "given"  # a Pack object handed in by the caller (tests, tools)


class PackError(Exception):
    """The knowledge pack is missing, unreadable or does not match the schema."""


class PackNotInstalled(PackError):
    """No knowledge pack was found in any of the searched locations."""


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


EMPTY_PACK = Pack(
    manifest=Manifest(schema_version=0, generator_version="", vault_commit=None, notice=""),
    adapters=(),
    process_map={},
    operations=(),
    errors=(),
    params=(),
)


@dataclass(frozen=True, slots=True)
class PackStatus:
    """Where the pack came from and whether it is usable (``dmslog info``, API, UI)."""

    state: str  # STATE_*
    source: str | None  # SOURCE_* of the selected location, None when nothing was found
    path: Path | None
    searched: tuple[tuple[str, Path], ...]
    manifest: Manifest | None = None
    error: str | None = None

    @property
    def installed(self) -> bool:
        return self.state == STATE_INSTALLED

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "installed": self.installed,
            "source": self.source,
            "path": None if self.path is None else str(self.path),
            "schema_version": self.manifest.schema_version if self.manifest else None,
            "generator_version": self.manifest.generator_version if self.manifest else None,
            "vault_commit": self.manifest.vault_commit if self.manifest else None,
            "error": self.error,
        }


def app_dir() -> Path | None:
    """Folder that holds ``dmslog.pyz`` when running from the zipapp; None from a source tree."""
    import dmslog

    here = Path(dmslog.__file__).parent  # .../dmslog.pyz/dmslog inside the zipapp
    for parent in here.parents:
        if parent.is_file():  # the archive itself
            return parent.parent
    return None


def pack_candidates(
    pack_dir: str | os.PathLike[str] | None = None,
    data_dir: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
    portable_dir: str | os.PathLike[str] | None = None,
) -> list[tuple[str, Path]]:
    """Ordered ``(source, directory)`` candidates. An explicit option or variable is the only one."""
    env = os.environ if env is None else env
    if pack_dir:
        return [(SOURCE_OPTION, Path(pack_dir).expanduser())]
    if env.get(PACK_ENV):
        return [(SOURCE_ENV, Path(env[PACK_ENV]).expanduser())]
    candidates: list[tuple[str, Path]] = []
    if data_dir is not None:
        candidates.append((SOURCE_DATA_DIR, Path(data_dir).joinpath(*PACK_SUBDIR)))
    if portable_dir is not None:
        candidates.append((SOURCE_PORTABLE, Path(portable_dir).joinpath(*PACK_SUBDIR)))
    return candidates


def _present(directory: Path) -> bool:
    try:
        return directory.is_dir() and any(directory.iterdir())
    except OSError:
        return False


def locate_pack(candidates: list[tuple[str, Path]]) -> tuple[PackStatus, Pack | None]:
    """Pick the location, validate it and load it. Never raises."""
    searched = tuple(candidates)
    explicit = len(candidates) == 1 and candidates[0][0] in (SOURCE_OPTION, SOURCE_ENV)
    for source, directory in candidates:
        if not explicit and not _present(directory):
            continue
        try:
            pack = load_pack_from(directory)
        except (PackError, OSError, ValueError, TypeError, KeyError) as exc:
            return PackStatus(STATE_INVALID, source, directory, searched, None, str(exc)), None
        return PackStatus(STATE_INSTALLED, source, directory, searched, pack.manifest), pack
    return PackStatus(STATE_NOT_INSTALLED, None, None, searched), None


@dataclass(slots=True)
class _Settings:
    pack_dir: str | None = None
    data_dir: Path | None = None
    data_dir_set: bool = False


_lock = threading.Lock()
_settings = _Settings()
_cached: tuple[PackStatus, Pack | None] | None = None


def configure(
    pack_dir: str | os.PathLike[str] | None = None,
    data_dir: str | os.PathLike[str] | None = None,
) -> None:
    """Set the ``--pack-dir`` option and the data directory of this process (CLI start-up)."""
    global _settings
    with _lock:
        _settings = _Settings(
            None if not pack_dir else os.fspath(pack_dir),
            None if data_dir is None else Path(data_dir),
            data_dir is not None,
        )
    clear_cache()


def _current() -> tuple[PackStatus, Pack | None]:
    global _cached
    cached = _cached
    if cached is not None:
        return cached
    with _lock:
        if _cached is None:
            settings = _settings
            if settings.data_dir_set:
                data_dir = settings.data_dir
            else:
                from dmslog.paths import default_data_dir

                data_dir = default_data_dir()
            _cached = locate_pack(pack_candidates(settings.pack_dir, data_dir, None, app_dir()))
        return _cached


def pack_status() -> PackStatus:
    """State of the process pack (loads it on first use)."""
    return _current()[0]


def current_pack() -> Pack | None:
    """The process pack, or None when it is not installed or invalid."""
    return _current()[1]


def load_pack() -> Pack:
    """The process pack; raises ``PackNotInstalled`` / ``PackError`` when it is not usable."""
    status, pack = _current()
    if pack is None:
        if status.state == STATE_NOT_INSTALLED:
            raise PackNotInstalled("knowledge pack not installed")
        raise PackError(status.error or "invalid knowledge pack")
    return pack


def clear_cache() -> None:
    """Forget the cached pack (after ``configure`` and in tests)."""
    global _cached
    with _lock:
        _cached = None
