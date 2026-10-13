"""View presets: named filter trees kept per family, shipped (curated) or written by the user.

* Curated presets come from the data file ``presets_builtin.toml`` of this package (read through
  ``importlib.resources``, so it also works inside ``dmslog.pyz``). They are read-only.
* User presets live in ``<data dir>/presets.toml`` (``schema = 1``). The file never holds paths of
  ERS packages or data of a case: a filter that points at one case (``source``, ``stream``,
  ``event`` ids, ``ts`` ranges) is rejected.

A preset is ``name`` + ``description`` + optional ``family`` (``None`` applies to every case) +
``filter`` (the same JSON tree the viewer sends; validated with :mod:`dmslog.store.filters`). The
identity of a preset is ``(name ignoring letter case, family)``.

Reading is tolerant: a missing file is empty, an invalid preset is skipped and reported (the others
stay usable), unknown keys of the file and of each preset are preserved when the file is rewritten.
A file that is not valid TOML, or has a newer schema, is never overwritten: writes fail with
``file_invalid`` so the user's file is not lost.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
import threading
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from ..paths import private_mkdir
from .filters import FilterError
from .views import MAX_NAME_CHARS, ViewError, sanitize_name, validate_filter

SCHEMA = 1
FILENAME = "presets.toml"
BUILTIN_FILE = "presets_builtin.toml"
MAX_PRESETS = 200
MAX_DESCRIPTION_CHARS = 200
MAX_FAMILY_CHARS = 32
MAX_IMPORT_BYTES = 1024 * 1024
# MAX_PRESETS presets of MAX_FILTER bytes (filters.MAX_JSON_BYTES) plus extras must fit in the file.
MAX_EXTRA_BYTES = 8 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
FAMILY_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,31}\Z")
# Leaves that point at one case (ids and time of that case); a preset must work in any case.
CASE_LEAVES = frozenset({"source", "stream", "event", "ts", "collection"})  # collection: group ids (U21)
KNOWN_KEYS = frozenset({"name", "description", "family", "filter"})
FORMATS = ("json", "toml")

ERROR_MESSAGES = {
    "bad_name": "the name must be a non-empty text of at most {limit} characters",
    "bad_description": "the description must be a text of at most {limit} characters",
    "bad_family": "the family must be a short code (letters, digits, underscore)",
    "bad_filter": "the filter must be a non-empty JSON object",
    "filter_invalid": "the filter is not valid: {detail}",
    "filter_too_large": "the filter is larger than {limit} bytes",
    "case_specific": "the filter uses {leaf!r}, which points at one case; a preset must work in any case",
    "bad_preset": "a preset must be an object with name, filter and optionally description and family",
    "duplicate_name": "a preset named {name!r} already exists for this family",
    "reserved_name": "{name!r} is the name of a curated preset for this family",
    "too_many_presets": "at most {limit} presets can be kept",
    "not_found": "preset {name!r} does not exist",
    "builtin": "curated presets cannot be changed or deleted",
    "file_invalid": "the presets file is not valid and was left untouched; fix or remove it first",
    "bad_format": "the format must be 'json' or 'toml'",
    "bad_import": "the import data is not a valid presets document",
    "import_too_large": "the import data is larger than {limit} bytes",
    "extra_too_large": "the unknown fields of the preset are too large or too deeply nested",
    "file_too_large": "the presets file would be larger than {limit} bytes; delete some presets",
    "save_failed": "the presets file could not be written",
}


class PresetError(ValueError):
    """A preset operation failed; ``code`` is stable and ``params`` fill the message."""

    messages: Mapping[str, str] = ERROR_MESSAGES

    def __init__(self, code: str, **params: Any) -> None:
        self.code = code
        self.params = params
        try:
            text = self.messages.get(code, code).format(**params)
        except (KeyError, IndexError, ValueError):
            text = code
        super().__init__(text)


# --------------------------------------------------------------------------- one preset


def normalize_family(raw: Any) -> str | None:
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str) or not FAMILY_PATTERN.match(raw):
        raise PresetError("bad_family")
    return raw.upper()


def identity(preset: Mapping[str, Any]) -> tuple[str, str]:
    return (str(preset["name"]).casefold(), preset.get("family") or "")


def _walk_leaves(tree: Any) -> list[str]:
    """Operator names of every node of the tree (iterative: the tree was bounded by the validator)."""
    names: list[str] = []
    stack = [tree]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        for key, value in node.items():
            names.append(key)
            if key in ("and", "or") and isinstance(value, list):
                stack.extend(value)
            elif key == "not":
                stack.append(value)
    return names


def _small(value: Any, limit: int) -> bool:
    """True when ``value`` serializes to at most ``limit`` bytes and is not nested too deeply."""
    try:
        return len(json.dumps(value, default=str, ensure_ascii=False).encode("utf-8")) <= limit
    except (RecursionError, ValueError, TypeError, UnicodeEncodeError):
        return False


def validate_preset(raw: Any) -> dict[str, Any]:
    """Return the clean preset (unknown keys kept in ``extra``); raises PresetError."""
    if not isinstance(raw, dict):
        raise PresetError("bad_preset")
    try:
        name = sanitize_name(raw.get("name"))
    except ViewError:
        raise PresetError("bad_name", limit=MAX_NAME_CHARS) from None
    description = raw.get("description", "")
    if description is None:
        description = ""
    if not isinstance(description, str) or len(description) > MAX_DESCRIPTION_CHARS:
        raise PresetError("bad_description", limit=MAX_DESCRIPTION_CHARS)
    description = " ".join(description.split())
    family = normalize_family(raw.get("family"))
    tree = raw.get("filter")
    try:
        validate_filter(tree)
    except FilterError as exc:
        raise PresetError("filter_invalid", detail=str(exc), path=exc.path) from None
    except ViewError as exc:
        if exc.code == "filter_too_large":
            raise PresetError("filter_too_large", **exc.params) from None
        raise PresetError("bad_filter") from None
    for leaf in _walk_leaves(tree):
        if leaf in CASE_LEAVES:
            raise PresetError("case_specific", leaf=leaf)
    clean: dict[str, Any] = {"name": name, "description": description, "family": family, "filter": tree}
    extra = {k: v for k, v in raw.items() if k not in KNOWN_KEYS}
    if extra:
        if not _small(extra, MAX_EXTRA_BYTES):
            raise PresetError("extra_too_large")
        clean["extra"] = extra
    return clean


def public(preset: Mapping[str, Any], builtin: bool) -> dict[str, Any]:
    return {
        "name": preset["name"],
        "description": preset.get("description", ""),
        "family": preset.get("family"),
        "filter": preset["filter"],
        "builtin": builtin,
    }


# --------------------------------------------------------------------------- TOML text


def _toml_string(text: str) -> str:
    out = json.dumps(text, ensure_ascii=False)
    return out.replace("\x7f", "\\u007f").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value:
            return "nan"
        if value in (float("inf"), float("-inf")):
            return "inf" if value > 0 else "-inf"
        return repr(value)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return (
            "{ " + ", ".join(f"{_toml_key(k)} = {_toml_value(v)}" for k, v in value.items()) + " }"
            if value
            else "{}"
        )
    raise PresetError("bad_preset")


def _toml_key(key: Any) -> str:
    text = str(key)
    return text if re.fullmatch(r"[A-Za-z0-9_-]+", text) else _toml_string(text)


def dump_toml(presets: list[dict[str, Any]], top_extra: Mapping[str, Any] | None = None) -> str:
    """The presets document as TOML text (LF, UTF-8)."""
    lines = [
        "# dmslog view presets (user file). Local to this machine; filters are the JSON trees of the viewer.",
        f"schema = {SCHEMA}",
    ]
    for key, value in (top_extra or {}).items():
        if key not in ("schema", "preset"):
            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
    for preset in presets:
        lines += ["", "[[preset]]", f"name = {_toml_string(preset['name'])}"]
        if preset.get("description"):
            lines.append(f"description = {_toml_string(preset['description'])}")
        if preset.get("family"):
            lines.append(f"family = {_toml_string(preset['family'])}")
        lines.append(f"filter = {_toml_value(preset['filter'])}")
        for key, value in (preset.get("extra") or {}).items():
            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- documents


@dataclass
class Document:
    """Result of reading a presets document: valid presets, skipped ones, preserved top-level keys."""

    presets: list[dict[str, Any]] = field(default_factory=list)
    problems: list[dict[str, Any]] = field(default_factory=list)  # {"index", "name", "code", "message"}
    top_extra: dict[str, Any] = field(default_factory=dict)
    invalid: bool = False  # the file as a whole is unusable (not TOML, wrong schema)


def parse_document(data: Any) -> Document:
    """Validate a decoded document (``{"schema": 1, "preset": [...]}``); bad presets are reported."""
    doc = Document()
    if not isinstance(data, dict):
        doc.invalid = True
        return doc
    schema = data.get("schema", SCHEMA)
    if isinstance(schema, bool) or not isinstance(schema, int) or schema > SCHEMA or schema < 1:
        doc.invalid = True
        return doc
    doc.top_extra = {k: v for k, v in data.items() if k not in ("schema", "preset")}
    if doc.top_extra and not _small(doc.top_extra, MAX_EXTRA_BYTES):
        doc.invalid = True
        return doc
    raw_list = data.get("preset", [])
    if not isinstance(raw_list, list):
        doc.invalid = True
        return doc
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_list):
        label = raw.get("name") if isinstance(raw, dict) and isinstance(raw.get("name"), str) else ""
        try:
            clean = validate_preset(raw)
            key = identity(clean)
            if key in seen:
                raise PresetError("duplicate_name", name=clean["name"])
        except PresetError as exc:
            doc.problems.append({"index": index, "name": label[:MAX_NAME_CHARS], "code": exc.code,
                                 "message": str(exc)})  # fmt: skip
            continue
        seen.add(key)
        doc.presets.append(clean)
    return doc


def _parse_toml_text(text: str) -> Document:
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, RecursionError):
        doc = Document()
        doc.invalid = True
        return doc
    return parse_document(data)


_builtin_cache: list[dict[str, Any]] | None = None


def builtin_presets() -> list[dict[str, Any]]:
    """The curated presets (validated once; an invalid curated entry is a bug caught by the tests)."""
    global _builtin_cache
    if _builtin_cache is None:
        text = resources.files("dmslog.store").joinpath(BUILTIN_FILE).read_text(encoding="utf-8")
        _builtin_cache = _parse_toml_text(text).presets
    return [dict(p) for p in _builtin_cache]


def presets_path(data_dir: str | os.PathLike[str]) -> Path:
    return Path(data_dir) / FILENAME


_LOCK = threading.Lock()


def load_user(data_dir: str | os.PathLike[str]) -> Document:
    """The user's presets; a missing file is empty, an unreadable or invalid file sets ``invalid``."""
    path = presets_path(data_dir)
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return Document(invalid=True)
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return Document()
    except (OSError, UnicodeDecodeError):
        return Document(invalid=True)
    return _parse_toml_text(text)


def _atomic_write(path: Path, data: bytes) -> None:
    private_mkdir(path.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        try:
            tmp.chmod(0o600)  # POSIX only
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _save(data_dir: str | os.PathLike[str], doc: Document) -> None:
    try:
        data = dump_toml(doc.presets, doc.top_extra).encode("utf-8")
    except (UnicodeEncodeError, RecursionError):
        raise PresetError("save_failed") from None
    if len(data) > MAX_FILE_BYTES:  # the next read would reject it: keep the old file
        raise PresetError("file_too_large", limit=MAX_FILE_BYTES)
    try:
        _atomic_write(presets_path(data_dir), data)
    except OSError:
        raise PresetError("save_failed") from None


def _writable(data_dir: str | os.PathLike[str]) -> Document:
    doc = load_user(data_dir)
    if doc.invalid:
        raise PresetError("file_invalid")
    return doc


def _builtin_keys() -> set[tuple[str, str]]:
    return {identity(p) for p in builtin_presets()}


# --------------------------------------------------------------------------- listing


def list_presets(data_dir: str | os.PathLike[str], families: set[str] | None = None) -> dict[str, Any]:
    """Curated and user presets; with ``families`` only global presets and those of these families.

    A user preset hides nothing: names that equal a curated one cannot be stored (``reserved_name``).
    """
    user = load_user(data_dir)
    wanted = {f.upper() for f in families} if families is not None else None

    def applies(p: Mapping[str, Any]) -> bool:
        return wanted is None or p.get("family") is None or p["family"] in wanted

    items = [public(p, True) for p in builtin_presets() if applies(p)]
    items += [public(p, False) for p in user.presets if applies(p)]
    items.sort(key=lambda p: (p["family"] or "", p["name"].casefold(), p["builtin"] is False))
    return {"presets": items, "invalid": user.invalid, "problems": user.problems}


# --------------------------------------------------------------------------- changes


def create(data_dir: str | os.PathLike[str], raw: Any) -> dict[str, Any]:
    clean = validate_preset(raw)
    with _LOCK:
        doc = _writable(data_dir)
        key = identity(clean)
        if key in _builtin_keys():
            raise PresetError("reserved_name", name=clean["name"])
        if any(identity(p) == key for p in doc.presets):
            raise PresetError("duplicate_name", name=clean["name"])
        if len(doc.presets) >= MAX_PRESETS:
            raise PresetError("too_many_presets", limit=MAX_PRESETS)
        doc.presets.append(clean)
        _save(data_dir, doc)
    return public(clean, False)


def update(data_dir: str | os.PathLike[str], name: Any, family: Any, raw: Any) -> dict[str, Any]:
    """Replace the user preset ``(name, family)`` by ``raw`` (rename and/or new filter)."""
    old_key = (str(name).casefold(), normalize_family(family) or "")
    clean = validate_preset(raw)
    with _LOCK:
        doc = _writable(data_dir)
        if old_key in _builtin_keys():
            raise PresetError("builtin")
        index = next((i for i, p in enumerate(doc.presets) if identity(p) == old_key), None)
        if index is None:
            raise PresetError("not_found", name=str(name))
        new_key = identity(clean)
        if new_key in _builtin_keys():
            raise PresetError("reserved_name", name=clean["name"])
        if any(i != index and identity(p) == new_key for i, p in enumerate(doc.presets)):
            raise PresetError("duplicate_name", name=clean["name"])
        if "extra" not in clean and "extra" in doc.presets[index]:
            clean["extra"] = doc.presets[index]["extra"]
        if "extra" in clean and not _small(clean["extra"], MAX_EXTRA_BYTES):
            raise PresetError("extra_too_large")
        doc.presets[index] = clean
        _save(data_dir, doc)
    return public(clean, False)


def delete(data_dir: str | os.PathLike[str], name: Any, family: Any) -> None:
    key = (str(name).casefold(), normalize_family(family) or "")
    with _LOCK:
        doc = _writable(data_dir)
        if key in _builtin_keys():
            raise PresetError("builtin")
        kept = [p for p in doc.presets if identity(p) != key]
        if len(kept) == len(doc.presets):
            raise PresetError("not_found", name=str(name))
        doc.presets = kept
        _save(data_dir, doc)


# --------------------------------------------------------------------------- import / export


def export_user(data_dir: str | os.PathLike[str], fmt: str) -> dict[str, Any]:
    """The user presets as ``{"format": "json", "data": {...}}`` or ``{"format": "toml", "text": ...}``."""
    if fmt not in FORMATS:
        raise PresetError("bad_format")
    doc = load_user(data_dir)
    if fmt == "toml":
        return {"format": "toml", "text": dump_toml(doc.presets), "count": len(doc.presets)}
    body = {"schema": SCHEMA, "preset": [{k: v for k, v in public(p, False).items() if k != "builtin"}
                                         for p in doc.presets]}  # fmt: skip
    return {"format": "json", "data": body, "count": len(doc.presets)}


def decode_import(fmt: Any, payload: Any) -> Document:
    """Decode import data: JSON as an object (or text) and TOML as text."""
    if fmt not in FORMATS:
        raise PresetError("bad_format")
    if fmt == "json":
        data = payload
        if not isinstance(payload, str):
            try:
                size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8", "replace"))
            except (RecursionError, ValueError, TypeError):
                raise PresetError("bad_import") from None
            if size > MAX_IMPORT_BYTES:
                raise PresetError("import_too_large", limit=MAX_IMPORT_BYTES)
        if isinstance(payload, str):
            if len(payload.encode("utf-8", "replace")) > MAX_IMPORT_BYTES:
                raise PresetError("import_too_large", limit=MAX_IMPORT_BYTES)
            try:
                data = json.loads(payload)
            except (ValueError, RecursionError):
                raise PresetError("bad_import") from None
        doc = parse_document(data)
    else:
        if not isinstance(payload, str):
            raise PresetError("bad_import")
        if len(payload.encode("utf-8", "replace")) > MAX_IMPORT_BYTES:
            raise PresetError("import_too_large", limit=MAX_IMPORT_BYTES)
        doc = _parse_toml_text(payload)
    if doc.invalid:
        raise PresetError("bad_import")
    return doc


def import_presets(data_dir: str | os.PathLike[str], fmt: Any, payload: Any) -> dict[str, Any]:
    """Merge presets into the user file: new ones are added, same identity are updated, the rest ignored.

    Returns ``{"added": n, "updated": n, "ignored": [{"name", "code", "message"}]}``; invalid presets
    and those that clash with a curated one are ignored without stopping the others.
    """
    incoming = decode_import(fmt, payload)
    ignored = [{"name": p["name"], "code": p["code"], "message": p["message"]} for p in incoming.problems]
    added = updated = 0
    with _LOCK:
        doc = _writable(data_dir)
        reserved = _builtin_keys()
        for preset in incoming.presets:
            key = identity(preset)
            if key in reserved:
                err = PresetError("reserved_name", name=preset["name"])
                ignored.append({"name": preset["name"], "code": err.code, "message": str(err)})
                continue
            index = next((i for i, p in enumerate(doc.presets) if identity(p) == key), None)
            if index is not None:
                old_extra = doc.presets[index].get("extra")
                if old_extra:  # the import replaces the known fields only
                    merged = {**old_extra, **preset.get("extra", {})}
                    if not _small(merged, MAX_EXTRA_BYTES):  # the union must stay readable
                        err = PresetError("extra_too_large")
                        ignored.append({"name": preset["name"], "code": err.code, "message": str(err)})
                        continue
                    preset["extra"] = merged
                doc.presets[index] = preset
                updated += 1
            elif len(doc.presets) >= MAX_PRESETS:
                err = PresetError("too_many_presets", limit=MAX_PRESETS)
                ignored.append({"name": preset["name"], "code": err.code, "message": str(err)})
            else:
                doc.presets.append(preset)
                added += 1
        if added or updated:
            _save(data_dir, doc)
    return {"added": added, "updated": updated, "ignored": ignored}
