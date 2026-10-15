"""Source discovery: ERS zip, installation folder or single file -> SourceInfo records.

Classification is driven by data (`families.toml`, docs/ARQUITETURA.md section 9.1a): nothing
in this module is specific to an adapter. Listing never decompresses anything; a zip is read
through `ZipFile.infolist()` only (names, sizes, CRC), and a folder through `os.stat`.
"""

from __future__ import annotations

import os
import re
import stat
import tomllib
import zipfile
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import datetime
from functools import lru_cache
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any

from dmslog.aliases import Aliases
from dmslog.parse.formats import EnvelopeConfigError, compile_envelopes

FAMILIES_RESOURCE = "families.toml"
ENVELOPES = ("adms", "oaserr", "plain")  # built-in; families.toml may declare more ([[envelope]])
SCOPES = ("default", "service", "optional")
FALLBACK_FAMILY = "UNKNOWN"

# Anti zip-bomb limits (docs/ARQUITETURA.md section 9.2). Member limits mark the member
# (`limit_violation`); structural limits abort the listing.
DEFAULT_MAX_MEMBER_BYTES = 4 * 1024**3
DEFAULT_MAX_RATIO = 200.0
RATIO_MIN_BYTES = 1024 * 1024  # small members may compress extremely well without being a bomb
DEFAULT_MAX_MEMBERS = 200_000
DEFAULT_MAX_TOTAL_BYTES = 64 * 1024**3
MAX_NAME_LENGTH = 1024


class FamiliesConfigError(ValueError):
    """families.toml is malformed."""


class UnsafeArchiveError(ValueError):
    """The zip has an unsafe member name (zip-slip) or exceeds a structural limit.

    `diag_reason` is fixed text and `diag_component` the raw name involved (see dmslog.diagnostics).
    """

    def __init__(self, reason: str, component: str = "") -> None:
        super().__init__(f"{reason}: {component}" if component else reason)
        self.diag_reason = reason
        self.diag_component = component


class SourceNotFoundError(FileNotFoundError):
    """The path given to discover() does not exist or is not a zip, folder or file."""

    def __init__(self, reason: str, component: str = "") -> None:
        super().__init__(f"{reason}: {component}" if component else reason)
        self.diag_reason = reason
        self.diag_component = component


@dataclass(frozen=True)
class Rule:
    """One compiled rule of families.toml."""

    family: str
    envelope: str
    scope: str
    anchor: bool
    indexable: bool
    regex: re.Pattern[str]


@dataclass(frozen=True)
class Classification:
    """Result of classifying one logical path."""

    family: str
    envelope: str
    scope: str
    anchor: bool
    indexable: bool
    rule_index: int | None  # None = fallback
    process: str | None
    pid: int | None
    account: str | None
    service: str | None
    sensitive: bool
    rotation: int | None


@dataclass(frozen=True)
class SourceInfo:
    """One physical log file (or zip member) found by discover()."""

    container: Path  # the zip, the folder or the file that was opened
    container_kind: str  # "zip" | "folder" | "file"
    member: str  # stable logical path: POSIX, relative to the container
    file_name: str
    family: str
    envelope: str  # expected envelope (a hint for detection)
    indexable: bool
    scope: str  # scope declared by the rule: default | service | optional
    default_scope: bool  # resolved: indexed when the user keeps the default scope
    process: str | None
    pid: int | None
    account: str | None  # dnaApp_<zone> folder
    service: str | None
    sensitive: bool
    rotation: int | None  # None = current .log; 0, 1, ... = .log.N
    size: int  # uncompressed bytes
    compressed_size: int | None
    zip_crc: int | None
    mtime: float | None
    anchor: bool = False  # adapter rule: its service marks the services in the default scope
    rule_index: int | None = None
    limit_violation: str | None = None  # None, "too_large", "ratio" or "encrypted"

    @property
    def zone(self) -> str | None:
        """Zone name from the dnaApp_<zone> folder (for example "mpdmz")."""
        if self.account and self.account.lower().startswith("dnaapp_"):
            return self.account[len("dnaApp_") :]
        return None

    @property
    def stream_key(self) -> tuple[str, str | None, int | None, bool]:
        """Identity of the logical stream: .log and .log.N of one process share it."""
        return (self.family, self.process, self.pid, self.sensitive)

    @property
    def selectable(self) -> bool:
        """True when the source can be indexed at all."""
        return self.indexable and self.limit_violation is None


# --------------------------------------------------------------------------- rules


class RuleSet:
    """Ordered rules (first match wins) plus the fallback."""

    def __init__(self, rules: list[Rule], fallback: Rule) -> None:
        self.rules = rules
        self.fallback = fallback

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuleSet:
        layouts = data.get("layouts", {})
        if not isinstance(layouts, dict):
            raise FamiliesConfigError("[layouts] must be a table")
        raw_rules = data.get("rule", [])
        if not isinstance(raw_rules, list) or not raw_rules:
            raise FamiliesConfigError("families.toml needs at least one [[rule]]")
        try:
            generic = tuple(env.name for env in compile_envelopes(data))
        except EnvelopeConfigError as exc:
            raise FamiliesConfigError(str(exc)) from None
        envelopes = (*ENVELOPES, *generic)
        rules = [_compile_rule(raw, layouts, f"rule #{i + 1}", envelopes) for i, raw in enumerate(raw_rules)]
        fb = data.get("fallback")
        if not isinstance(fb, dict):
            raise FamiliesConfigError("families.toml needs a [fallback] table")
        fallback = _compile_rule({**fb, "pattern": ""}, layouts, "[fallback]", envelopes)
        return cls(rules, fallback)

    @classmethod
    def from_text(cls, text: str) -> RuleSet:
        try:
            return cls.from_dict(tomllib.loads(text))
        except tomllib.TOMLDecodeError as exc:
            raise FamiliesConfigError(f"invalid TOML: {exc}") from exc

    def classify(self, path: str) -> Classification:
        """Classify a logical path (POSIX separators)."""
        for index, rule in enumerate(self.rules):
            match = rule.regex.search(path)
            if match:
                return _classification(rule, index, match)
        return _classification(self.fallback, None, None)

    @property
    def families(self) -> list[str]:
        seen: dict[str, None] = {}
        for rule in [*self.rules, self.fallback]:
            seen.setdefault(rule.family)
        return list(seen)


_RULE_KEYS = {"family", "envelope", "scope", "anchor", "indexable", "layout", "process", "pattern"}


def _compile_rule(
    raw: dict[str, Any], layouts: dict[str, str], where: str, envelopes: tuple[str, ...] = ENVELOPES
) -> Rule:
    unknown = set(raw) - _RULE_KEYS
    if unknown:
        raise FamiliesConfigError(f"{where}: unknown keys {sorted(unknown)}")
    family = raw.get("family")
    if not isinstance(family, str) or not family:
        raise FamiliesConfigError(f"{where}: 'family' is required")
    for key in ("envelope", "scope", "layout", "process", "pattern"):
        if key in raw and not isinstance(raw[key], str):
            raise FamiliesConfigError(f"{where}: {key!r} must be a string")
    for key in ("anchor", "indexable"):
        if key in raw and not isinstance(raw[key], bool):
            raise FamiliesConfigError(f"{where}: {key!r} must be true or false")
    envelope = raw.get("envelope", "plain")
    if envelope not in envelopes:
        raise FamiliesConfigError(f"{where}: envelope must be one of {envelopes}, got {envelope!r}")
    scope = raw.get("scope", "optional")
    if scope not in SCOPES:
        raise FamiliesConfigError(f"{where}: scope must be one of {SCOPES}, got {scope!r}")
    if "layout" in raw:
        name = raw["layout"]
        if name not in layouts:
            raise FamiliesConfigError(f"{where}: unknown layout {name!r}")
        template = layouts[name]
        if not isinstance(template, str):
            raise FamiliesConfigError(f"{where}: layout {name!r} must be a string")
        if "{process}" in template:
            if "process" not in raw:
                raise FamiliesConfigError(f"{where}: layout {name!r} needs 'process'")
            template = template.replace("{process}", f"(?:{raw['process']})")
        pattern = template
    elif "pattern" in raw:
        pattern = raw["pattern"]
    else:
        raise FamiliesConfigError(f"{where}: needs 'layout' or 'pattern'")
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise FamiliesConfigError(f"{where}: invalid pattern: {exc}") from exc
    return Rule(
        family=family,
        envelope=envelope,
        scope=scope,
        anchor=bool(raw.get("anchor", False)),
        indexable=bool(raw.get("indexable", True)),
        regex=regex,
    )


def _classification(rule: Rule, index: int | None, match: re.Match[str] | None) -> Classification:
    groups = match.groupdict() if match else {}
    pid = groups.get("pid")
    rotation = groups.get("rotation")
    return Classification(
        family=rule.family,
        envelope=rule.envelope,
        scope=rule.scope,
        anchor=rule.anchor,
        indexable=rule.indexable,
        rule_index=index,
        process=groups.get("process"),
        pid=int(pid) if pid else None,
        account=groups.get("account"),
        service=groups.get("service"),
        sensitive=bool(groups.get("sensitive")),
        rotation=int(rotation) if rotation else None,
    )


@lru_cache(maxsize=1)
def load_rules() -> RuleSet:
    """Load the packaged families.toml (works from a folder and from a zipapp)."""
    text = resources.files("dmslog").joinpath(FAMILIES_RESOURCE).read_text(encoding="utf-8-sig")
    return RuleSet.from_text(text)


# --------------------------------------------------------------------------- names


def normalize_member_name(name: str) -> str:
    """Return a safe, POSIX-style logical path or raise UnsafeArchiveError.

    Rejects absolute paths, drive letters, UNC paths, '..' components, NUL and control
    characters (zip-slip). Backslashes count as separators, as Windows tools write them.
    """
    if not name or len(name) > MAX_NAME_LENGTH:
        raise UnsafeArchiveError("invalid member name length", name[:80])
    if any(ord(ch) < 32 for ch in name):
        raise UnsafeArchiveError("control character in member name", name[:80])
    unified = name.replace("\\", "/")
    if unified.startswith("/") or re.match(r"^[A-Za-z]:", unified):
        raise UnsafeArchiveError("absolute member name", name[:80])
    parts = unified.split("/")
    if ".." in parts:
        raise UnsafeArchiveError("path traversal in member name", name[:80])
    cleaned = [p for p in parts if p not in ("", ".")]
    if not cleaned:
        raise UnsafeArchiveError("empty member name", name[:80])
    return "/".join(cleaned)


class _AliasedRules:
    """RuleSet view that classifies the alias-normalized path (real name -> public token)."""

    def __init__(self, rules: RuleSet, aliases: Aliases) -> None:
        self._rules = rules
        self._aliases = aliases

    def classify(self, path: str) -> Classification:
        return self._rules.classify(self._aliases.apply_path(path))


# --------------------------------------------------------------------------- discovery


def discover(
    path: str | os.PathLike[str],
    *,
    rules: RuleSet | None = None,
    max_member_bytes: int = DEFAULT_MAX_MEMBER_BYTES,
    max_ratio: float = DEFAULT_MAX_RATIO,
    max_members: int = DEFAULT_MAX_MEMBERS,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    aliases: Aliases | None = None,
) -> Iterator[SourceInfo]:
    """Yield a SourceInfo for every file of an ERS zip, an installation folder or one file.

    With `aliases`, the real spelling of a name in a member path is replaced by its public token
    before the path is classified, so `process` (and everything derived from it) carries the public
    name. `member` and `file_name` stay as in the container because the file is read back through them.

    The listing is complete before the first item is yielded because the default scope of
    service logs depends on which services host adapters. Nothing is decompressed or read.
    """
    target = Path(path)
    rules = rules or load_rules()
    rules = _AliasedRules(rules, aliases) if aliases else rules
    if target.is_dir():
        raw = list(_scan_folder(target, rules))
        kind = "folder"
    elif target.is_file() and zipfile.is_zipfile(target):
        raw = list(
            _scan_zip(
                target,
                rules,
                max_member_bytes=max_member_bytes,
                max_ratio=max_ratio,
                max_members=max_members,
                max_total_bytes=max_total_bytes,
            )
        )
        kind = "zip"
    elif target.is_file():
        raw = [_single_file(target, rules)]
        kind = "file"
    else:
        raise SourceNotFoundError("no such zip, folder or file", str(target))
    yield from resolve_scope(raw, explicit=kind == "file")


def resolve_scope(sources: Iterable[SourceInfo], *, explicit: bool = False) -> list[SourceInfo]:
    """Set `default_scope` on every source.

    `explicit` means the user named a single file: it is always in scope. Otherwise
    `default` rules are in scope, `optional` never, and `service` rules only when their
    service hosts an anchor (adapter) source; with no anchor at all they all count as
    default, so a server without adapters still opens with its service logs.
    """
    items = list(sources)
    if explicit:
        return [replace(s, default_scope=s.selectable) for s in items]
    anchors = {s.service.lower() for s in items if s.service and s.anchor and s.selectable}
    result = []
    for s in items:
        if not s.selectable:
            in_scope = False
        elif s.scope == "default":
            in_scope = True
        elif s.scope == "service":
            in_scope = (s.service or "").lower() in anchors if anchors else True
        else:
            in_scope = False
        result.append(replace(s, default_scope=in_scope))
    return result


def _build(
    container: Path,
    container_kind: str,
    member: str,
    cls: Classification,
    *,
    size: int,
    compressed_size: int | None,
    zip_crc: int | None,
    mtime: float | None,
    limit_violation: str | None,
) -> SourceInfo:
    return SourceInfo(
        container=container,
        container_kind=container_kind,
        member=member,
        file_name=PurePosixPath(member).name,
        family=cls.family,
        envelope=cls.envelope,
        indexable=cls.indexable,
        scope=cls.scope,
        default_scope=False,
        process=cls.process,
        pid=cls.pid,
        account=cls.account,
        service=cls.service,
        sensitive=cls.sensitive,
        rotation=cls.rotation,
        size=size,
        compressed_size=compressed_size,
        zip_crc=zip_crc,
        mtime=mtime,
        anchor=cls.anchor,
        rule_index=cls.rule_index,
        limit_violation=limit_violation,
    )


def _scan_zip(
    path: Path,
    rules: RuleSet | _AliasedRules,
    *,
    max_member_bytes: int,
    max_ratio: float,
    max_members: int,
    max_total_bytes: int,
) -> Iterator[SourceInfo]:
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise UnsafeArchiveError("not a valid zip", path.name) from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > max_members:
            raise UnsafeArchiveError(f"zip has {len(infos)} members (limit {max_members})")
        total = sum(i.file_size for i in infos)
        if total > max_total_bytes:
            raise UnsafeArchiveError(f"zip declares {total} uncompressed bytes (limit {max_total_bytes})")
        seen: set[str] = set()
        for info in infos:
            member = normalize_member_name(info.filename)
            if info.is_dir() or info.filename.endswith(("/", "\\")):
                continue
            key = member.lower()
            if key in seen:
                raise UnsafeArchiveError("duplicate member name", member[:80])
            seen.add(key)
            violation = _member_violation(info, max_member_bytes, max_ratio)
            yield _build(
                path,
                "zip",
                member,
                rules.classify(member),
                size=info.file_size,
                compressed_size=info.compress_size,
                zip_crc=info.CRC,
                mtime=_zip_mtime(info),
                limit_violation=violation,
            )


def _member_violation(info: zipfile.ZipInfo, max_member_bytes: int, max_ratio: float) -> str | None:
    if info.flag_bits & 0x1:
        return "encrypted"
    if info.file_size > max_member_bytes:
        return "too_large"
    if info.file_size >= RATIO_MIN_BYTES and info.file_size / max(info.compress_size, 1) > max_ratio:
        return "ratio"
    return None


def _zip_mtime(info: zipfile.ZipInfo) -> float | None:
    try:
        return datetime(*info.date_time).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _scan_folder(root: Path, rules: RuleSet | _AliasedRules) -> Iterator[SourceInfo]:
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted(files):
            full = Path(current) / name
            try:
                if full.is_symlink():
                    continue
                st = full.stat()
            except OSError:
                continue
            if not stat.S_ISREG(st.st_mode):
                continue  # FIFO, socket or device: opening one could block forever (docs: review 8.2, S1)
            member = normalize_member_name(full.relative_to(root).as_posix())
            yield _build(
                root,
                "folder",
                member,
                rules.classify(member),
                size=st.st_size,
                compressed_size=None,
                zip_crc=None,
                mtime=st.st_mtime,
                limit_violation=None,
            )


def _single_file(path: Path, rules: RuleSet | _AliasedRules) -> SourceInfo:
    st = path.stat()
    member = normalize_member_name(path.name)
    return _build(
        path,
        "file",
        member,
        rules.classify(member),
        size=st.st_size,
        compressed_size=None,
        zip_crc=None,
        mtime=st.st_mtime,
        limit_violation=None,
    )
