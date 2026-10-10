"""Effective installation configuration read from an ERS folder or zip (never extracted).

Reads ``OASyS/Servers/config/Registry/*.xml``, resolves the layers per product
(docs/ARQUITETURA.md 7.2.1) and follows the effective ``WebServiceConfiguration`` and
``*_ErrorConfigurationPath`` values to the XML files they point at.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from dmslog.aliases import Aliases

from .registry import (
    DEFAULT_MAX_XML_BYTES,
    ConfigParam,
    RegistryError,
    RegistryLayer,
    load_xml_root,
    parse_registry_xml,
    read_limited,
    resolve_registry,
    shown,
)

_REGISTRY_DIR = ("oasys", "servers", "config", "registry")
_DATA_ROOT_NAME = "schneiderelectricdata"
_OASINSTALLDATADIR = "%oasinstalldatadir%"
_ERROR_PATH_SUFFIX = "_ErrorConfigurationPath"
_WS_PARAM = "WebServiceConfiguration"
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def normalize_install_path(raw: str) -> str | None:
    """Turn a configured path into a path relative to the collection's data root.

    Handles ``%OASINSTALLDATADIR%\\ADMS\\x`` and ``%OASINSTALLDATADIR%ADMS\\x`` (missing
    separator), ``C:\\SchneiderElectricData\\\\ADMS\\x`` (repeated separators), and treats
    ``\\`` and ``/`` alike. Returns a ``/``-joined relative path (original case) or
    ``None`` if the value cannot be mapped to the data root.
    """
    text = raw.strip().replace("\\", "/")
    if text.lower().startswith(_OASINSTALLDATADIR):
        text = text[len(_OASINSTALLDATADIR) :]
    else:
        text = _DRIVE_RE.sub("", text, count=1)
        parts = [p for p in text.split("/") if p]
        for i, part in enumerate(parts):
            if part.lower() == _DATA_ROOT_NAME:
                return "/".join(parts[i + 1 :]) or None
        return None
    parts = [p for p in text.split("/") if p]
    return "/".join(parts) or None


class _Collection:
    """Read-only view over a folder or a zip: case-insensitive lookup of XML members."""

    def __init__(self, path: Path, max_bytes: int, aliases: Aliases | None = None) -> None:
        self.aliases = aliases
        self.path = path
        self.max_bytes = max_bytes
        self._zip: zipfile.ZipFile | None = None
        self._index: dict[str, str] = {}  # normalized lower-case key -> real member / path
        if path.is_dir():
            for item in path.rglob("*"):
                if item.suffix.lower() == ".xml" and item.is_file():
                    rel = item.relative_to(path).as_posix()
                    self._index.setdefault(rel.lower(), rel)
        elif zipfile.is_zipfile(path):
            self._zip = zipfile.ZipFile(path)
            for name in self._zip.namelist():
                if name.lower().endswith(".xml") and not name.endswith("/"):
                    norm = name.replace("\\", "/")
                    self._index.setdefault(norm.lower(), name)
        else:
            raise RegistryError(f"{shown(aliases, str(path))}: not a folder or a zip file")

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()

    def find(self, rel: str) -> str | None:
        return self._index.get(rel.replace("\\", "/").lower())

    def keys(self) -> list[str]:
        return sorted(self._index)

    def real(self, key: str) -> str:
        return self._index[key]

    def read(self, member: str) -> bytes:
        if self._zip is None:
            return read_limited(self.path / member, self.max_bytes, self.aliases)
        try:
            info = self._zip.getinfo(member)
            if info.file_size > self.max_bytes:
                raise RegistryError(
                    f"{shown(self.aliases, member)}: file exceeds the {self.max_bytes}-byte limit"
                )
            with self._zip.open(info) as handle:
                data = handle.read(self.max_bytes + 1)
        except (KeyError, OSError, zipfile.BadZipFile, RuntimeError) as exc:
            raise RegistryError(
                f"{shown(self.aliases, member)}: cannot read zip member ({shown(self.aliases, str(exc))})"
            ) from exc
        if len(data) > self.max_bytes:
            raise RegistryError(
                f"{shown(self.aliases, member)}: file exceeds the {self.max_bytes}-byte limit"
            )
        return data


@dataclass(frozen=True, slots=True)
class WebServiceOperation:
    operation: str
    message_log_level: str | None
    auditing_period: str | None
    num_of_retries: str | None
    retry_timeout: str | None


@dataclass(frozen=True, slots=True)
class ErrorEntry:
    adms_id: str | None
    adms_severity: str | None
    code: str | None
    severity: str | None
    reason: str | None
    details: str | None


@dataclass(frozen=True, slots=True)
class ErrorConfiguration:
    param_name: str  # e.g. CreatedMeterReadings_ErrorConfigurationPath
    path: str  # file inside the collection
    interface: str | None
    severity_eventing_threshold: str | None
    entries: tuple[ErrorEntry, ...]


@dataclass(frozen=True, slots=True)
class ProductConfig:
    product: str
    params: tuple[ConfigParam, ...]  # every layer row
    web_service_path: str | None = None
    operations: tuple[WebServiceOperation, ...] = ()
    error_configurations: tuple[ErrorConfiguration, ...] = ()

    def effective(self, name: str, key_path: str = "") -> ConfigParam | None:
        for param in self.params:
            if param.name == name and param.key_path == key_path and param.effective:
                return param
        return None


@dataclass(frozen=True, slots=True)
class InstallationConfig:
    products: tuple[ProductConfig, ...]
    problems: tuple[str, ...] = field(default=())

    def product(self, name: str) -> ProductConfig | None:
        for item in self.products:
            if item.product == name:
                return item
        return None


def _child_text(element: ET.Element, tag: str) -> str | None:
    child = element.find(tag)
    return None if child is None else (child.text or "").strip()


def _parse_web_services(root: ET.Element, aliases: Aliases | None = None) -> tuple[WebServiceOperation, ...]:
    ops = []
    for node in root.iter("WebServiceConfiguration"):
        name = node.get("Name")
        if not name:
            continue
        ops.append(
            WebServiceOperation(
                operation=aliases.apply_name(name) if aliases else name,
                message_log_level=_child_text(node, "MessageLogLevel"),
                auditing_period=_child_text(node, "AuditingPeriod"),
                num_of_retries=_child_text(node, "NumOfRetries"),
                retry_timeout=_child_text(node, "RetryTimeout"),
            )
        )
    return tuple(ops)


def _parse_error_config(root: ET.Element, param_name: str, path: str) -> ErrorConfiguration:
    entries = []
    for err in root.findall("Error"):
        adms = err.find("Adms")
        ext = err.find("ExternalSystem")
        entries.append(
            ErrorEntry(
                adms_id=None if adms is None else _child_text(adms, "Id"),
                adms_severity=None if adms is None else _child_text(adms, "Severity"),
                code=None if ext is None else _child_text(ext, "Code"),
                severity=None if ext is None else _child_text(ext, "Severity"),
                reason=None if ext is None else _child_text(ext, "Reason"),
                details=None if ext is None else _child_text(ext, "Details"),
            )
        )
    return ErrorConfiguration(
        param_name=param_name,
        path=path,
        interface=_child_text(root, "Interface"),
        severity_eventing_threshold=_child_text(root, "ErrorSeverityEventingThreshold"),
        entries=tuple(entries),
    )


def read_installation(
    path: Path | str, *, max_xml_bytes: int = DEFAULT_MAX_XML_BYTES, aliases: Aliases | None = None
) -> InstallationConfig:
    """Read a collection root (folder or zip) and resolve the effective configuration.

    Per-file problems (malformed, oversized, missing target) are collected in
    ``problems`` instead of aborting. Raises :class:`RegistryError` only when the
    path itself is unusable.
    """
    collection = _Collection(Path(path), max_xml_bytes, aliases)
    try:
        return _read(collection, aliases)
    finally:
        collection.close()


def _read(collection: _Collection, aliases: Aliases | None = None) -> InstallationConfig:
    problems: list[str] = []

    layers: list[RegistryLayer] = []
    data_root = ""  # prefix (with trailing "/") of the data root inside the collection
    found_root = False
    for key in collection.keys():
        parts = key.split("/")
        if len(parts) < 5 or tuple(parts[-5:-1]) != _REGISTRY_DIR:
            continue
        real = collection.real(key)
        if not found_root:
            prefix_len = len(real.replace("\\", "/").split("/")) - 5
            data_root = "/".join(real.replace("\\", "/").split("/")[:prefix_len])
            data_root = data_root + "/" if data_root else ""
            found_root = True
        elif not key.startswith(data_root.lower()):
            continue  # a second installation inside the same collection: ignore
        file_name = real.replace("\\", "/").rsplit("/", 1)[-1]
        try:
            data = collection.read(real)
        except RegistryError as exc:
            problems.append(str(exc))  # built from normalized components
            continue
        try:
            layers.append(
                parse_registry_xml(data, filename=file_name, max_bytes=collection.max_bytes, aliases=aliases)
            )
        except RegistryError as exc:
            problems.append(str(exc))  # built from normalized components

    by_product: dict[str, list[ConfigParam]] = defaultdict(list)
    for param in resolve_registry(layers):
        by_product[param.product].append(param)

    products = []
    for product in sorted(by_product):
        params = tuple(by_product[product])
        ws_path, operations, errors = None, (), []
        effective = {(p.key_path, p.name): p for p in params if p.effective}
        for (key_path, name), param in sorted(effective.items()):
            if key_path != "" or not isinstance(param.value, str) or not param.value:
                continue
            is_ws = name == _WS_PARAM
            if not is_ws and not name.endswith(_ERROR_PATH_SUFFIX):
                continue
            rel = normalize_install_path(param.value)
            member = collection.find(data_root + rel) if rel else None
            if member is None:
                # `product` is already public (parsed layer); only the raw parameter name is normalized
                problems.append(f"{product}.{shown(aliases, name)}: target file not found in the collection")
                continue
            try:
                root = load_xml_root(
                    collection.read(member), source=member, max_bytes=collection.max_bytes, aliases=aliases
                )
            except RegistryError as exc:
                problems.append(str(exc))  # built from normalized components
                continue
            if is_ws:
                ws_path = aliases.apply_path(member) if aliases else member
                operations = _parse_web_services(root, aliases)
            else:
                errors.append(
                    _parse_error_config(root, name, aliases.apply_path(member) if aliases else member)
                )
        products.append(
            ProductConfig(
                product=product,
                params=params,
                web_service_path=ws_path,
                operations=operations,
                error_configurations=tuple(errors),
            )
        )
    return InstallationConfig(products=tuple(products), problems=tuple(problems))
