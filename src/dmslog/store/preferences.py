"""Versioned preferences package: export and import of the user's settings (ROADMAP 9.10).

What the user keeps lives in three user files of the data folder, each with its own validator and
writer: ``presets.toml`` (:mod:`dmslog.store.presets`), ``alerts.toml`` (:mod:`dmslog.store.alerts`) and
``redaction.toml`` (:mod:`dmslog.report.redact`). They stay the live storage (no new store, no database
migration: the preferences are per user, not per case, and the case database is never touched). This
module adds the *package*: one JSON document to move all of them between machines or to back them up.

Package (``schema`` 1), every section optional on import::

    {"schema": 1, "kind": "dmslog-preferences",
     "presets": [{"name", "description", "family", "filter"}, ...],     # user presets, not the curated ones
     "alert_rules": [{"name", "kind", "enabled", "severity", ...}, ...],
     "redaction": {"name", "categories", "terms", "exceptions", "payload_names", "mode"}}

Highlight rules are not part of it: they are stored per case (table ``highlight_rule``) and a rule
may refer to one case. Panels (9.11) do not exist yet; a new section means a new ``schema``.

Contract:

* **Untrusted input.** The package is data: size, depth, node and string limits, only JSON types, integers
  inside int64, finite floats, no duplicate keys, strings that encode to UTF-8; nothing in it is executed
  and no name or text of it is ever used as a file name or path (the three files are fixed).
* **Migration.** ``schema N`` goes to ``N + 1`` by pure functions chained in :data:`MIGRATIONS`; a newer
  schema than this program knows is refused, never guessed.
* **Report.** :func:`import_package` returns what entered, what was refused (per item, with a stable
  ``code``) and what was migrated. A structural error (not an object, bad schema, unknown section, limits)
  applies nothing. Otherwise each section is all-or-nothing: with ``on_item_error="reject_section"``
  (default) one refused item rejects its section, with ``"skip_item"`` the valid items enter. Sections
  are independent of each other, but the files are written together: if a write fails, the files already
  written are restored.
* **Modes.** ``merge`` (default) adds new items and updates the ones with the same identity (the order of
  the file is kept, the unknown keys of the existing item are kept); ``replace`` makes the section equal to
  the package (items not in the package are removed). Redaction is always replaced as a whole.
* **Idempotent.** An item equal to the stored one is ``unchanged`` and a section with no change does not
  touch its file; importing the same package twice, or what was just exported, changes nothing.
* **Deterministic export.** Sorted sections and keys, no timestamps: the same preferences give the same
  bytes.
* **Safe writes.** :func:`dmslog.paths.atomic_write_bytes` (temporary file in the same folder, fsync,
  ``os.replace``). Existing files that are invalid are never overwritten.
"""

from __future__ import annotations

import copy
import json
import math
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..paths import atomic_write_bytes
from ..report import redact
from . import alerts, presets

SCHEMA = 1
KIND = "dmslog-preferences"
SECTIONS = ("presets", "alert_rules", "redaction")
MODES = ("merge", "replace")
ITEM_POLICIES = ("reject_section", "skip_item")

MAX_PACKAGE_BYTES = 8 * 1024 * 1024
MAX_DEPTH = 64
MAX_NODES = 500_000
MAX_STRING_CHARS = 100_000
MAX_KEY_CHARS = 256
INT_MIN, INT_MAX = -(2**63), 2**63 - 1
MAX_LABEL_CHARS = 80
MAX_REMOVED_LISTED = 100

ERROR_MESSAGES = {
    "package_too_large": "the package is larger than {limit} bytes",
    "package_too_complex": "the package is nested too deeply or has too many values",
    "bad_json": "the package is not valid JSON (duplicate keys and NaN are refused)",
    "bad_value": "the package holds a value that is not allowed: {detail}",
    "not_object": "the package must be a JSON object",
    "schema_missing": "the package has no schema number",
    "bad_schema": "the schema must be an integer of at least 1",
    "schema_too_new": "the package has schema {found}, newer than the {current} this program knows",
    "bad_kind": "this is not a dmslog preferences package",
    "unknown_section": "the package has a section this version does not know",
    "migration_failed": "the package could not be migrated from schema {step}",
    "bad_option": "the option {option!r} has a value that is not allowed",
    "bad_section": "the section has the wrong type",
    "too_many_items": "the section has more than {limit} items",
    "bad_item": "an item must be an object",
    "unknown_field": "the item has a field this version does not know",
    "duplicate_name": "another item of the package has the same name",
    "reserved_name": "the name belongs to a curated preset",
    "file_invalid": "the stored file is not valid and was left untouched; fix or remove it first",
    "file_too_large": "the result would be larger than {limit} bytes",
    "not_representable": "the result cannot be stored faithfully",
    "too_many_items_total": "the result would have more than {limit} items",
    "invalid_profile": "the redaction profile is not valid ({problem}: {field})",
    "section_rejected": "not applied: another item of the section was refused",
    "save_failed": "a file could not be written; the files already written were restored",
    "rollback_failed": "a file could not be restored after a failed write: {files}",
}


class PreferencesError(ValueError):
    """The package or an option is unusable as a whole; ``code`` is stable and ``params`` fill the message."""

    def __init__(self, code: str, **params: Any) -> None:
        self.code = code
        self.params = params
        try:
            text = ERROR_MESSAGES.get(code, code).format(**params)
        except (KeyError, IndexError, ValueError):
            text = code
        super().__init__(text)


def _problem(code: str, **params: Any) -> dict[str, str]:
    return {"code": code, "message": str(PreferencesError(code, **params))}


# --------------------------------------------------------------------------- limits on untrusted data


def check_limits(value: Any) -> None:
    """Raise PreferencesError unless ``value`` is plain JSON data inside the limits (iterative)."""
    nodes = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise PreferencesError("package_too_complex")
        if node is None or isinstance(node, bool):
            continue
        if isinstance(node, int):
            if not INT_MIN <= node <= INT_MAX:
                raise PreferencesError("bad_value", detail="integer outside 64 bits")
        elif isinstance(node, float):
            if not math.isfinite(node):
                raise PreferencesError("bad_value", detail="non-finite number")
        elif isinstance(node, str):
            if len(node) > MAX_STRING_CHARS:
                raise PreferencesError("bad_value", detail="text too long")
            try:
                node.encode("utf-8")
            except UnicodeEncodeError:
                raise PreferencesError("bad_value", detail="text that is not valid Unicode") from None
        elif isinstance(node, list):
            stack.extend((item, depth + 1) for item in node)
        elif isinstance(node, dict):
            for key, item in node.items():
                if not isinstance(key, str) or len(key) > MAX_KEY_CHARS:
                    raise PreferencesError("bad_value", detail="object key")
                try:
                    key.encode("utf-8")
                except UnicodeEncodeError:
                    raise PreferencesError("bad_value", detail="object key") from None
                stack.append((item, depth + 1))
        else:
            raise PreferencesError("bad_value", detail="type " + type(node).__name__)


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate key")
        out[key] = value
    return out


def _reject_constant(_name: str) -> Any:
    raise ValueError("non-finite number")


def decode(package: Any) -> dict[str, Any]:
    """The package as a dict: JSON text/bytes are decoded strictly, a dict is checked as it is."""
    if isinstance(package, bytes | bytearray):
        if len(package) > MAX_PACKAGE_BYTES:
            raise PreferencesError("package_too_large", limit=MAX_PACKAGE_BYTES)
        try:
            package = bytes(package).decode("utf-8-sig")
        except UnicodeDecodeError:
            raise PreferencesError("bad_json") from None
    if isinstance(package, str):
        if len(package) > MAX_PACKAGE_BYTES or len(package.encode("utf-8", "replace")) > MAX_PACKAGE_BYTES:
            raise PreferencesError("package_too_large", limit=MAX_PACKAGE_BYTES)
        try:
            package = json.loads(
                package.lstrip("﻿"), object_pairs_hook=_no_duplicates, parse_constant=_reject_constant
            )
        except (ValueError, RecursionError):
            raise PreferencesError("bad_json") from None
    check_limits(package)
    if not isinstance(package, dict):
        raise PreferencesError("not_object")
    try:
        size = len(json.dumps(package, ensure_ascii=False, allow_nan=False).encode("utf-8"))
    except (ValueError, TypeError, RecursionError):
        raise PreferencesError("bad_json") from None
    if size > MAX_PACKAGE_BYTES:
        raise PreferencesError("package_too_large", limit=MAX_PACKAGE_BYTES)
    return package


# --------------------------------------------------------------------------- migration

Migration = Callable[[dict[str, Any]], dict[str, Any]]

# ``MIGRATIONS[n]`` turns a schema ``n`` package into a schema ``n + 1`` one. Pure: it returns a new dict
# and must not touch its argument (the chain passes a copy anyway). Schema 1 is the first version.
MIGRATIONS: dict[int, Migration] = {}


def migrate(
    package: dict[str, Any],
    current: int = SCHEMA,
    migrations: Mapping[int, Migration] | None = None,
) -> tuple[dict[str, Any], list[int]]:
    """Chain the migrations up to ``current``; returns the new package and the schemas it went through.

    Raises PreferencesError for a missing, invalid or newer schema, or a step that is missing or fails.
    """
    table = MIGRATIONS if migrations is None else migrations
    found = package.get("schema")
    if "schema" not in package:
        raise PreferencesError("schema_missing")
    if isinstance(found, bool) or not isinstance(found, int) or found < 1:
        raise PreferencesError("bad_schema")
    if found > current:
        raise PreferencesError("schema_too_new", found=found, current=current)
    data = copy.deepcopy(package)
    steps: list[int] = []
    version = found
    while version < current:
        step = table.get(version)
        if step is None:
            raise PreferencesError("migration_failed", step=version)
        try:
            result = step(copy.deepcopy(data))
            if not isinstance(result, dict):
                raise TypeError("migration must return an object")
            check_limits(result)
        except PreferencesError:
            raise PreferencesError("migration_failed", step=version) from None
        except Exception:  # noqa: BLE001 - a broken step is a refused package, never a crash
            raise PreferencesError("migration_failed", step=version) from None
        data = {**result, "schema": version + 1}
        steps.append(version)
        version += 1
    return data, steps


# --------------------------------------------------------------------------- canonical values


def _canon(value: Any) -> str:
    """Type-exact canonical text: sorted keys, ``true`` stays distinct from ``1`` and ``1`` from ``1.0``."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str
    )


def _label(raw: Any) -> str:
    name = raw.get("name") if isinstance(raw, dict) else None
    if not isinstance(name, str):
        return ""
    return "".join(ch if ch.isprintable() else " " for ch in name)[:MAX_LABEL_CHARS]


# --------------------------------------------------------------------------- list sections


@dataclass(frozen=True)
class _ListKind:
    """What differs between presets and alert rules; the merge logic is shared."""

    section: str
    known: frozenset[str]
    validate: Callable[[Any], dict[str, Any]]
    error_type: type[Exception]
    identity: Callable[[Mapping[str, Any]], Any]
    view: Callable[[Mapping[str, Any]], dict[str, Any]]  # the fields of the package (no `extra`)
    path: Callable[[Any], Path]
    load: Callable[[Any], Any]  # -> Document(items attr below, invalid, problems, top_extra)
    items_attr: str
    dump: Callable[[list[dict[str, Any]], Mapping[str, Any]], str]
    parse: Callable[[Any], Any]  # decoded TOML -> Document
    limit: int
    too_many_code: str
    reserved: Callable[[], set[Any]]


def _preset_view(p: Mapping[str, Any]) -> dict[str, Any]:
    return {k: p.get(k) for k in ("name", "description", "family", "filter")}


def _preset_reserved() -> set[Any]:
    return {presets.identity(p) for p in presets.builtin_presets()}


PRESETS = _ListKind(
    section="presets",
    known=presets.KNOWN_KEYS,
    validate=presets.validate_preset,
    error_type=presets.PresetError,
    identity=presets.identity,
    view=_preset_view,
    path=presets.presets_path,
    load=presets.load_user,
    items_attr="presets",
    dump=lambda items, extra: presets.dump_toml(items, extra),
    parse=presets.parse_document,
    limit=presets.MAX_PRESETS,
    too_many_code="too_many_presets",
    reserved=_preset_reserved,
)

ALERT_RULES = _ListKind(
    section="alert_rules",
    known=alerts.KNOWN_KEYS,
    validate=alerts.validate_rule,
    error_type=alerts.AlertError,
    identity=alerts.identity,
    view=alerts.public,
    path=alerts.alerts_path,
    load=alerts.load,
    items_attr="rules",
    dump=lambda items, extra: alerts.dump_toml(items, extra),
    parse=alerts.parse_document,
    limit=alerts.MAX_RULES,
    too_many_code="too_many_rules",
    reserved=set,
)

_KINDS = {"presets": PRESETS, "alert_rules": ALERT_RULES}


def _sort_key(kind: _ListKind, item: Mapping[str, Any]) -> tuple[str, str, str]:
    name = str(item["name"])
    family = str(item.get("family") or "") if kind is PRESETS else ""
    return (family, name.casefold(), name)


@dataclass
class SectionPlan:
    """Report of one section plus the file it would write (``None`` when nothing changes)."""

    status: str  # absent | unchanged | changed | rejected
    items: list[dict[str, Any]]
    counts: dict[str, int]
    removed: list[str]
    warnings: list[dict[str, str]]
    error: dict[str, str] | None = None
    path: Path | None = None
    data: bytes | None = None

    def report(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "counts": self.counts,
            "items": self.items,
            "removed": self.removed,
            "warnings": self.warnings,
            "error": self.error,
        }


def _counts() -> dict[str, int]:
    return {"added": 0, "updated": 0, "unchanged": 0, "rejected": 0, "removed": 0}


def _rejected(section_error: dict[str, str], items: list[dict[str, Any]] | None = None) -> SectionPlan:
    plan = SectionPlan("rejected", items or [], _counts(), [], [], section_error)
    plan.counts["rejected"] = sum(1 for i in plan.items if i["outcome"] == "rejected")
    return plan


def _item_error(index: int, raw: Any, code: str, message: str) -> dict[str, Any]:
    return {"index": index, "name": _label(raw), "outcome": "rejected", "code": code, "message": message}


def _check_item(kind: _ListKind, index: int, raw: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Validate one package item: (clean item, None) or (None, report row)."""
    if not isinstance(raw, dict):
        return None, _item_error(index, raw, "bad_item", ERROR_MESSAGES["bad_item"])
    if not set(raw) <= kind.known:
        return None, _item_error(index, raw, "unknown_field", ERROR_MESSAGES["unknown_field"])
    try:
        clean = kind.validate(raw)
        kind.dump([clean], {})  # a value the TOML file cannot hold is refused here, not at write time
        clean.pop("extra", None)
    except kind.error_type as exc:
        return None, _item_error(index, raw, getattr(exc, "code", "bad_item"), str(exc))
    except (presets.PresetError, RecursionError, UnicodeEncodeError, ValueError, TypeError):
        return None, _item_error(index, raw, "not_representable", ERROR_MESSAGES["not_representable"])
    return clean, None


def _plan_list(kind: _ListKind, data_dir: Path, raw_items: Any, mode: str, policy: str) -> SectionPlan:
    if not isinstance(raw_items, list):
        return _rejected(_problem("bad_section"))
    if len(raw_items) > kind.limit * 2:
        return _rejected(_problem("too_many_items", limit=kind.limit * 2))
    existing = kind.load(data_dir)
    if existing.invalid:
        return _rejected(_problem("file_invalid"))
    reserved = kind.reserved()
    rows: list[dict[str, Any]] = []
    valid: list[tuple[int, dict[str, Any]]] = []
    seen: set[Any] = set()
    for index, raw in enumerate(raw_items):
        clean, bad = _check_item(kind, index, raw)
        if clean is not None:
            key = kind.identity(clean)
            if key in reserved:
                clean, bad = None, _item_error(index, raw, "reserved_name", ERROR_MESSAGES["reserved_name"])
            elif key in seen:
                clean, bad = None, _item_error(index, raw, "duplicate_name", ERROR_MESSAGES["duplicate_name"])
            else:
                seen.add(key)
        if clean is None:
            rows.append(bad)  # type: ignore[arg-type]
        else:
            valid.append((index, clean))
            rows.append({"index": index, "name": clean["name"], "outcome": "pending"})
    refused = [r for r in rows if r["outcome"] == "rejected"]
    if refused and (policy == "reject_section" or mode == "replace"):  # replace needs every item
        for row in rows:
            if row["outcome"] == "pending":
                row["outcome"] = "not_applied"
                row["code"] = "section_rejected"
                row["message"] = ERROR_MESSAGES["section_rejected"]
        return _rejected(_problem("section_rejected"), rows)

    current: list[dict[str, Any]] = [dict(item) for item in getattr(existing, kind.items_attr)]
    by_key = {kind.identity(item): n for n, item in enumerate(current)}
    result = [] if mode == "replace" else list(current)
    incoming_keys = {kind.identity(c) for _, c in valid}
    over_limit = False
    for index, clean in valid:
        key = kind.identity(clean)
        row = rows[index]
        old = current[by_key[key]] if key in by_key else None
        if old is not None and "extra" in old:
            clean = {**clean, "extra": old["extra"]}
        if old is not None:
            row["outcome"] = "unchanged" if _canon(kind.view(old)) == _canon(kind.view(clean)) else "updated"
            if mode == "replace":
                result.append(clean)
            else:
                result[by_key[key]] = clean
        else:
            if len(result) >= kind.limit:
                row["outcome"] = "rejected"
                row["code"] = kind.too_many_code
                row["message"] = str(kind.error_type(kind.too_many_code, limit=kind.limit))
                over_limit = True
                continue
            row["outcome"] = "added"
            result.append(clean)
    removed = (
        [str(item["name"])[:MAX_LABEL_CHARS] for item in current if kind.identity(item) not in incoming_keys]
        if mode == "replace"
        else []
    )
    if over_limit and policy == "reject_section":
        for row in rows:
            if row["outcome"] in ("added", "updated", "unchanged"):
                row["outcome"] = "not_applied"
                row["code"] = "section_rejected"
                row["message"] = ERROR_MESSAGES["section_rejected"]
        return _rejected(_problem("too_many_items_total", limit=kind.limit), rows)

    counts = _counts()
    for row in rows:
        if row["outcome"] in counts:
            counts[row["outcome"]] += 1
    counts["rejected"] = sum(1 for r in rows if r["outcome"] == "rejected")
    counts["removed"] = len(removed)
    warnings: list[dict[str, str]] = []
    path = kind.path(data_dir)
    plan = SectionPlan("unchanged", rows, counts, removed[:MAX_REMOVED_LISTED], warnings, None, path)
    if [_canon(i) for i in result] == [_canon(i) for i in current]:
        return plan
    result_view = result
    text = kind.dump(result_view, existing.top_extra)
    try:
        data = text.encode("utf-8")
    except UnicodeEncodeError:
        return _rejected(_problem("not_representable"), rows)
    if len(data) > presets.MAX_FILE_BYTES:
        return _rejected(_problem("file_too_large", limit=presets.MAX_FILE_BYTES), rows)
    # Self-check: what would be stored must read back as exactly the planned items.
    try:
        back = kind.parse(tomllib.loads(text))
        again = getattr(back, kind.items_attr)
        faithful = (
            not back.invalid
            and not back.problems
            and [_canon(i) for i in again] == [_canon(i) for i in result]
        )
    except (tomllib.TOMLDecodeError, RecursionError, ValueError):
        faithful = False
    if not faithful:
        return _rejected(_problem("not_representable"), rows)
    if existing.problems:
        warnings.append(
            {
                "code": "existing_items_dropped",
                "message": f"{len(existing.problems)} stored item(s) that were already invalid are not kept",
            }
        )
    plan.status = "changed"
    plan.data = data
    return plan


# --------------------------------------------------------------------------- redaction section

_PROFILE_KEYS = frozenset({"name", "categories", "terms", "exceptions", "payload_names", "mode"})


def _plan_redaction(data_dir: Path, raw: Any) -> SectionPlan:
    if not isinstance(raw, dict):
        return _rejected(_problem("bad_section"))
    if not set(raw) <= _PROFILE_KEYS:
        return _rejected(_problem("unknown_field"))
    terms = raw.get("terms")
    if isinstance(terms, dict) and not set(terms) <= {"word", "literal"}:
        return _rejected(_problem("unknown_field"))
    try:
        profile = redact.profile_from_dict(raw)
    except redact.ProfileError as exc:
        return _rejected(_problem("invalid_profile", problem=exc.problem, field=exc.field))
    path = redact.profile_path(data_dir)
    counts = _counts()
    plan = SectionPlan("unchanged", [], counts, [], [], None, path)
    try:
        stored: dict[str, Any] | None = redact.load_profile(data_dir).to_dict() if path.exists() else None
    except redact.ProfileError:
        stored = None
        plan.warnings.append(
            {"code": "stored_profile_invalid", "message": "the stored profile was not valid and is replaced"}
        )
    if stored is not None and _canon(stored) == _canon(profile.to_dict()):
        counts["unchanged"] = 1
        return plan
    text = redact.dump_profile_toml(profile)
    try:
        data = text.encode("utf-8")
        back = redact.profile_from_dict(tomllib.loads(text))
        faithful = _canon(back.to_dict()) == _canon(profile.to_dict())
    except (UnicodeEncodeError, tomllib.TOMLDecodeError, redact.ProfileError, RecursionError):
        faithful = False
    if not faithful:
        return _rejected(_problem("not_representable"))
    counts["updated" if stored is not None else "added"] = 1
    plan.status = "changed"
    plan.data = data
    return plan


# --------------------------------------------------------------------------- export


def build_package(
    data_dir: str | Path, sections: Sequence[str] | None = None
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """The package for the stored preferences and the warnings for sections left out.

    A section whose file is invalid is left out (with a warning) instead of exported half; a missing
    redaction file is left out too (nothing was customised), a missing presets/alerts file is ``[]``.
    """
    root = Path(data_dir)
    wanted = SECTIONS if sections is None else tuple(sections)
    if not set(wanted) <= set(SECTIONS):
        raise PreferencesError("bad_option", option="sections")
    package: dict[str, Any] = {"schema": SCHEMA, "kind": KIND}
    warnings: list[dict[str, str]] = []
    with presets._LOCK, alerts._LOCK, redact._SAVE_LOCK:
        for name in SECTIONS:
            if name not in wanted:
                continue
            if name in _KINDS:
                kind = _KINDS[name]
                doc = kind.load(root)
                if doc.invalid:
                    warnings.append({"section": name, **_problem("file_invalid")})
                    continue
                items = sorted(
                    (kind.view(i) for i in getattr(doc, kind.items_attr)), key=lambda i: _sort_key(kind, i)
                )
                package[name] = items
            else:
                path = redact.profile_path(root)
                if not path.exists():
                    continue
                try:
                    package[name] = redact.load_profile(root).to_dict()
                except redact.ProfileError:
                    warnings.append({"section": name, **_problem("file_invalid")})
    return package, warnings


def dumps(package: Mapping[str, Any]) -> str:
    """Canonical text of a package: sorted keys, 2-space indent, UTF-8 characters kept, final newline."""
    return json.dumps(package, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def export_package(data_dir: str | Path, sections: Sequence[str] | None = None) -> dict[str, Any]:
    """``{"package": {...}, "text": canonical JSON, "warnings": [...]}``; the text is what to save."""
    package, warnings = build_package(data_dir, sections)
    text = dumps(package)
    if len(text.encode("utf-8")) > MAX_PACKAGE_BYTES:
        raise PreferencesError("package_too_large", limit=MAX_PACKAGE_BYTES)
    check_limits(package)  # what is exported must be importable
    return {"package": package, "text": text, "warnings": warnings}


# --------------------------------------------------------------------------- import


def _read_old(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _restore(path: Path, old: bytes | None) -> None:
    if old is None:
        path.unlink(missing_ok=True)
    else:
        atomic_write_bytes(path, old)


def _outcome(plans: Mapping[str, SectionPlan]) -> str:
    states = {p.status for p in plans.values()}
    if "rejected" in states:
        return "partial" if states - {"rejected"} - {"absent"} else "rejected"
    return "applied" if "changed" in states else "unchanged"


def import_package(
    data_dir: str | Path,
    package: Any,
    *,
    dry_run: bool = False,
    mode: str = "merge",
    on_item_error: str = "reject_section",
    migrations: Mapping[int, Migration] | None = None,
    current: int = SCHEMA,
) -> dict[str, Any]:
    """Validate (and unless ``dry_run`` apply) a preferences package; returns the report.

    Report keys: ``outcome`` (``rejected`` structural error, nothing applied; ``partial`` some sections
    refused; ``applied``; ``unchanged``; ``failed`` a write failed and was rolled back), ``ok``,
    ``applied`` (files were written), ``dry_run``, ``schema`` (``found``, ``current``, ``migrated``: the
    schemas it was migrated from), ``errors`` (structural), ``sections`` (per section: ``status``,
    ``counts``, ``items``, ``removed``, ``warnings``, ``error``) and ``summary``.
    ``migrations``/``current`` are for tests; the program always uses the defaults.
    """
    if not isinstance(dry_run, bool):
        raise PreferencesError("bad_option", option="dry_run")
    if mode not in MODES:
        raise PreferencesError("bad_option", option="mode")
    if on_item_error not in ITEM_POLICIES:
        raise PreferencesError("bad_option", option="on_item_error")
    report: dict[str, Any] = {
        "outcome": "rejected", "ok": False, "applied": False, "dry_run": dry_run,
        "schema": {"found": None, "current": current, "migrated": []},
        "errors": [], "sections": {}, "summary": {},
    }  # fmt: skip
    try:
        data = decode(package)
        found = data.get("schema")
        report["schema"]["found"] = found if isinstance(found, int) and not isinstance(found, bool) else None
        data, steps = migrate(data, current, migrations)
        report["schema"]["migrated"] = steps
        if data.get("kind") != KIND:
            raise PreferencesError("bad_kind")
        if not set(data) <= {"schema", "kind", *SECTIONS}:
            raise PreferencesError("unknown_section")
    except PreferencesError as exc:
        report["errors"].append({"code": exc.code, "message": str(exc)})
        return report

    root = Path(data_dir)
    plans: dict[str, SectionPlan] = {}
    with presets._LOCK, alerts._LOCK, redact._SAVE_LOCK:
        for name in SECTIONS:
            if name not in data:
                plans[name] = SectionPlan("absent", [], _counts(), [], [])
            elif name == "redaction":
                plans[name] = _plan_redaction(root, data[name])
            else:
                plans[name] = _plan_list(_KINDS[name], root, data[name], mode, on_item_error)
        report["sections"] = {n: p.report() for n, p in plans.items()}
        report["outcome"] = _outcome(plans)
        writes = [(n, p) for n, p in plans.items() if p.status == "changed" and p.data is not None]
        if not dry_run and writes:
            failure = _apply(writes)
            if failure is not None:
                report["outcome"] = "failed"
                report["errors"].append(failure)
                for n, _ in writes:
                    report["sections"][n]["status"] = "rejected"
                    report["sections"][n]["error"] = failure
            else:
                report["applied"] = True
    report["ok"] = report["outcome"] in ("applied", "unchanged")
    report["summary"] = {
        key: sum(p.counts[key] for p in plans.values())
        for key in ("added", "updated", "unchanged", "rejected", "removed")
    }
    return report


def _apply(writes: list[tuple[str, SectionPlan]]) -> dict[str, str] | None:
    """Write the planned files in order; on a failure restore the ones already written."""
    done: list[tuple[Path, bytes | None]] = []
    try:
        for _name, plan in writes:
            assert plan.path is not None and plan.data is not None
            old = _read_old(plan.path)
            atomic_write_bytes(plan.path, plan.data)
            done.append((plan.path, old))
    except OSError:
        stuck: list[str] = []
        for path, old in reversed(done):
            try:
                _restore(path, old)
            except OSError:
                stuck.append(path.name)
        if stuck:
            return _problem("rollback_failed", files=", ".join(stuck))
        return _problem("save_failed")
    return None
