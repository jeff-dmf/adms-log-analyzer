"""Layered ADMS Registry: parsing of ``Registry/*.xml`` files and precedence resolution.

Implements the rule in docs/ARQUITETURA.md section 7.2.1: files are grouped by
``Product/@Name``; each file is one layer (-1 for ``AdapterX.xml``, N for
``AdapterX_N[_label].xml``); a key present in a single layer is ``only``; on overlap
the highest layer ``wins`` and the others are ``overridden``; a tie at the highest
layer is ``ambiguous`` and has no effective value.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from dmslog.aliases import Aliases

DEFAULT_MAX_XML_BYTES = 4 * 1024 * 1024

STATUS_ONLY = "only"
STATUS_WINS = "wins"
STATUS_OVERRIDDEN = "overridden"
STATUS_AMBIGUOUS = "ambiguous"

RegistryValue = str | tuple[str, ...]


class RegistryError(Exception):
    """A Registry/configuration XML file could not be read or is not valid."""


@dataclass(frozen=True, slots=True)
class RegistryLayer:
    product: str
    file: str
    layer: int
    label: str
    # (key_path, name) -> value; a <Key> list is (<Key name>, '') -> tuple of values
    values: dict[tuple[str, str], RegistryValue]


@dataclass(frozen=True, slots=True)
class ConfigParam:
    product: str
    key_path: str
    name: str
    value: RegistryValue | None
    source_file: str
    layer: int
    label: str
    effective: bool
    status: str


_FILENAME_RE = re.compile(r"^(?P<product>.+?)(?:_(?P<layer>\d+)(?:_(?P<label>.+))?)?$")


def parse_registry_filename(filename: str) -> tuple[str, int, str]:
    """Return ``(product_guess, layer, label)`` for ``<Product>[_<N>[_<label>]].xml``."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if name.lower().endswith(".xml"):
        name = name[:-4]
    match = _FILENAME_RE.match(name)
    if match is None or match.group("layer") is None:
        return name, -1, ""
    return match.group("product"), int(match.group("layer")), match.group("label") or ""


def load_xml_root(
    data: bytes, *, source: str = "<bytes>", max_bytes: int = DEFAULT_MAX_XML_BYTES
) -> ET.Element:
    """Parse XML bytes safely.

    Rejects oversized input and any DTD/entity declaration (no entity expansion, no
    external entities); wraps every parser failure in :class:`RegistryError`.
    """
    if len(data) > max_bytes:
        raise RegistryError(f"{source}: file exceeds the {max_bytes}-byte limit")
    if _has_dtd(data):
        raise RegistryError(f"{source}: DTD/entity declarations are not allowed")
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, ValueError, RecursionError, MemoryError) as exc:
        raise RegistryError(f"{source}: malformed XML ({exc})") from exc
    if _too_deep(root):
        raise RegistryError(f"{source}: XML nesting exceeds {MAX_XML_DEPTH} levels")
    return root


MAX_XML_DEPTH = 32


def _has_dtd(data: bytes) -> bool:
    """True when the document declares a DOCTYPE or ENTITY, whatever its encoding."""
    candidates = [data.decode("utf-8", errors="replace")]
    if data[:4] in (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff") or data[:4] in (
        b"<\x00\x00\x00",
        b"\x00\x00\x00<",
    ):
        candidates.append(data.decode("utf-32", errors="replace"))
    for codec in ("utf-16-le", "utf-16-be"):
        candidates.append(data.decode(codec, errors="replace"))
        candidates.append(data[1:].decode(codec, errors="replace"))
    for text in candidates:
        lowered = text.lower()
        if "<!doctype" in lowered or "<!entity" in lowered:
            return True
    return False


def _too_deep(root: ET.Element) -> bool:
    """Iterative depth check (never recurses)."""
    stack = [(root, 1)]
    while stack:
        element, depth = stack.pop()
        if depth > MAX_XML_DEPTH:
            return True
        stack.extend((child, depth + 1) for child in element)
    return False


def read_limited(path: Path, max_bytes: int) -> bytes:
    """Read a file refusing anything larger than ``max_bytes``."""
    try:
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError as exc:
        raise RegistryError(f"{path}: cannot read file ({exc})") from exc
    if len(data) > max_bytes:
        raise RegistryError(f"{path}: file exceeds the {max_bytes}-byte limit")
    return data


def _text(element: ET.Element) -> str:
    return (element.text or "").strip()


def parse_registry_xml(
    source: Path | bytes,
    *,
    filename: str | None = None,
    max_bytes: int = DEFAULT_MAX_XML_BYTES,
    aliases: Aliases | None = None,
) -> RegistryLayer:
    """Parse one ``<OASYSDNARegistry><Product Name=...>`` file into a layer.

    ``filename`` is required for bytes input (it carries the layer and label).
    Raises :class:`RegistryError` for oversized, malformed or unexpected files. With ``aliases`` the
    file name (layer label, product guess) and the product name carry the public spelling.
    """
    if isinstance(source, bytes):
        data = source
        file_name = filename or "<bytes>"
    else:
        file_name = filename or source.name
        data = read_limited(Path(source), max_bytes)
    root = load_xml_root(data, source=file_name, max_bytes=max_bytes)
    if root.tag != "OASYSDNARegistry":
        raise RegistryError(f"{file_name}: root element is {root.tag!r}, expected 'OASYSDNARegistry'")
    products = root.findall("Product")
    if len(products) != 1 or not products[0].get("Name"):
        raise RegistryError(f"{file_name}: expected exactly one named <Product> element")
    product = products[0]
    if aliases:
        file_name = aliases.apply_name(file_name)
    guess, layer, label = parse_registry_filename(file_name)
    values: dict[tuple[str, str], RegistryValue] = {}
    _collect_values(product, "", values)
    return RegistryLayer(
        product=(aliases.apply_name(product.get("Name") or "") if aliases else product.get("Name")) or guess,
        file=file_name,
        layer=layer,
        label=label,
        values=values,
    )


def _collect_values(element: ET.Element, key_path: str, out: dict[tuple[str, str], RegistryValue]) -> None:
    for child in element:
        name = child.get("Name")
        if name is None:
            continue
        if child.tag == "Value":
            out[(key_path, name)] = _text(child)
        elif child.tag == "Key":
            sub_path = f"{key_path}/{name}" if key_path else name
            if any(c.tag == "Key" for c in child):
                _collect_values(child, sub_path, out)
            else:
                # A <Key> is a list, compared as a whole: unit (key_path=<Key name>, name='').
                out[(sub_path, "")] = tuple(_text(v) for v in child if v.tag == "Value")


def resolve_registry(layers: list[RegistryLayer]) -> list[ConfigParam]:
    """Apply the precedence rule; returns one row per (layer, key), deterministic order."""
    units: dict[tuple[str, str, str], list[tuple[RegistryLayer, RegistryValue]]] = defaultdict(list)
    for layer in layers:
        for (key_path, name), value in layer.values.items():
            units[(layer.product, key_path, name)].append((layer, value))
    rows: list[ConfigParam] = []
    for (product, key_path, name), entries in units.items():
        top = max(entry[0].layer for entry in entries)
        at_top = [e for e in entries if e[0].layer == top]
        for layer, value in entries:
            if len(entries) == 1:
                status, effective = STATUS_ONLY, True
            elif layer.layer != top:
                status, effective = STATUS_OVERRIDDEN, False
            elif len(at_top) > 1:
                status, effective = STATUS_AMBIGUOUS, False
            else:
                status, effective = STATUS_WINS, True
            rows.append(
                ConfigParam(
                    product=product,
                    key_path=key_path,
                    name=name,
                    value=value,
                    source_file=layer.file,
                    layer=layer.layer,
                    label=layer.label,
                    effective=effective,
                    status=status,
                )
            )
    rows.sort(key=lambda r: (r.product, r.key_path, r.name, r.layer, r.source_file))
    return rows
