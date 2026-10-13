"""ERS identity, Enterprise/Sensitive pairing and the rules that group collections in a case.

An ERS capture is a pair of zips named
`ERS_CL_<GUID>_<yyyymmddHHMMSS>_[Sensitive_]Enterprise_<description>.zip` (docs/ERS.md).
The two halves of the same capture share the GUID and the timestamp and form one collection group;
a case holds any number of groups (captures of different servers, folders, files: decision U21).
"""

from __future__ import annotations

import os
import re
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dmslog import messages
from dmslog.aliases import Aliases
from dmslog.ingest.discover import SourceInfo, discover

_NAME = re.compile(
    r"^ERS_CL_(?P<guid>[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12})"
    r"_(?P<ts>\d{14})_(?P<sensitive>Sensitive_)?(?P<product>[A-Za-z]+)(?:_(?P<desc>.*))?\.zip$",
    re.IGNORECASE,
)

VARIANT_ENTERPRISE = "enterprise"
VARIANT_SENSITIVE = "sensitive"

KIND_ERS_ZIP = "ers_zip"
KIND_FOLDER = "folder"
KIND_FILE = "file"


class CollectionConflictError(ValueError):
    """The input cannot be added to the case (the API turns this into HTTP 409).

    `reason`: "duplicate_collection" (a second copy of an ERS half the case already holds).
    """

    diag_safe = True  # messages hold GUIDs, capture times and fixed words only

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ErsIdentity:
    """Identity parsed from an ERS zip file name."""

    guid: str  # upper case
    timestamp: str  # yyyymmddHHMMSS, local time of the machine
    variant: str  # "enterprise" | "sensitive"
    product: str  # "Enterprise" in every sample
    description: str
    file_name: str

    @property
    def captured_at(self) -> datetime:
        return datetime.strptime(self.timestamp, "%Y%m%d%H%M%S")

    @property
    def group_key(self) -> tuple[str, str]:
        """Both halves of one capture share this key (stored as the same `ers_group`)."""
        return (self.guid, self.timestamp)


def parse_ers_name(name: str | os.PathLike[str]) -> ErsIdentity | None:
    """Parse an ERS zip file name; None when it does not follow the ERS convention."""
    base = os.path.basename(os.fspath(name))
    match = _NAME.match(base)
    if not match:
        return None
    ts = match["ts"]
    try:
        datetime.strptime(ts, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return ErsIdentity(
        guid=match["guid"].upper(),
        timestamp=ts,
        variant=VARIANT_SENSITIVE if match["sensitive"] else VARIANT_ENTERPRISE,
        product=match["product"],
        description=match["desc"] or "",
        file_name=base,
    )


def same_capture(a: ErsIdentity, b: ErsIdentity) -> bool:
    """True when both identities are halves (or repeats) of the same capture."""
    return a.group_key == b.group_key


ADD_SAME = "same"  # the collection is already in the case: re-ingest it (resume, growth, upgrade)
ADD_PAIR = "pair"  # the other half (Enterprise <-> Sensitive) of a capture in the case: joins its group
ADD_NEW = "new"  # another capture, server, folder or file: a collection group of its own (U21)


@dataclass(frozen=True)
class HeldCollection:
    """A collection already in the case, as the add rules see it."""

    id: int
    kind: str
    path: str  # resolved absolute path recorded at ingestion
    identity: ErsIdentity | None
    group: int  # collection.ers_group


@dataclass(frozen=True)
class AddDecision:
    action: str  # ADD_SAME | ADD_PAIR | ADD_NEW
    collection_id: int | None = None  # ADD_SAME: the collection to re-ingest
    group: int | None = None  # ADD_SAME / ADD_PAIR: the group the collection belongs to


def held_identity(
    kind: str, guid: str | None, stamp: str | None, variant: str | None, label: str | None, path: str
) -> ErsIdentity | None:
    """The ERS identity stored in a ``collection`` row (None for folders, files and non-ERS zips)."""
    if kind != KIND_ERS_ZIP or not guid or not stamp:
        return None
    return ErsIdentity(guid, stamp, variant or VARIANT_ENTERPRISE, "Enterprise", label or "", Path(path).name)


def classify_add(
    held: Sequence[HeldCollection], kind: str, path: str, identity: ErsIdentity | None
) -> AddDecision:
    """Where a new input goes in a case that groups collections (U21, ARQUITETURA 9.1).

    * the same resolved path and kind, or the same ERS half (GUID, capture time and variant) under another
      path: ``ADD_SAME`` (never a second collection);
    * the other half of a capture held by the case, in any order: ``ADD_PAIR`` (joins that group);
    * anything else (another GUID, another capture of the same GUID, a folder, a loose file, a zip whose
      name is not an ERS name): ``ADD_NEW``.

    Pure: the caller decides about duplicates (``refuse_duplicate``) and writes the rows.
    """
    for c in held:
        if c.path == path and c.kind == kind:
            return AddDecision(ADD_SAME, c.id, c.group)
    if identity is None:
        return AddDecision(ADD_NEW)
    for c in held:
        if c.identity is not None and c.identity.group_key == identity.group_key:
            if c.identity.variant == identity.variant:
                return AddDecision(ADD_SAME, c.id, c.group)
    for c in held:
        if c.identity is not None and c.identity.group_key == identity.group_key:
            return AddDecision(ADD_PAIR, None, c.group)
    return AddDecision(ADD_NEW)


def refuse_duplicate(held: Sequence[HeldCollection], decision: AddDecision, path: str) -> None:
    """Refuse a second copy of a collection the case already holds (HTTP 409 ``duplicate_collection``).

    A copy is the same ERS half (GUID, capture time and variant) under another path while the recorded file
    still exists. The same path is a re-ingest (resume, growth, extraction upgrade) and a half whose recorded
    file is gone was moved: both are accepted (the ingest re-points the path).
    """
    if decision.action != ADD_SAME:
        return
    current = next((c for c in held if c.id == decision.collection_id), None)
    if current is None or current.path == path:
        return
    try:
        still_there = Path(current.path).exists()
    except OSError:
        still_there = False
    try:
        same_file = still_there and os.path.samefile(current.path, path)  # another spelling of one file
    except OSError:
        same_file = False
    if still_there and not same_file:
        ident = current.identity
        raise CollectionConflictError(
            "duplicate_collection",
            messages.COLLECTION_DUPLICATE.format(
                guid=ident.guid if ident else "-", capture=ident.timestamp if ident else "-"
            ),
        )


def ers_group_for(identity: ErsIdentity) -> str:
    """Stable textual key of the pair, usable to derive the numeric `ers_group`."""
    return f"{identity.guid}_{identity.timestamp}"


@dataclass(frozen=True)
class CollectionPlan:
    """What a path adds to a case: identity, origin and the sources with the scope applied."""

    kind: str  # "ers_zip" | "folder" | "file"
    path: Path
    identity: ErsIdentity | None
    system: str | None  # zone from the dnaApp_<zone> folder, for example "mpdmz"
    machine: str | None  # not present in file names; filled in later from file contents
    label: str | None  # ERS description or folder/file name
    sources: tuple[SourceInfo, ...]

    @property
    def default_sources(self) -> tuple[SourceInfo, ...]:
        return tuple(s for s in self.sources if s.default_scope)

    @property
    def indexable_sources(self) -> tuple[SourceInfo, ...]:
        return tuple(s for s in self.sources if s.selectable)

    def selected(
        self, scope: str = "default", include: Iterable[str] = (), exclude: Iterable[str] = ()
    ) -> list[SourceInfo]:
        """Sources to index: `default` or `all` scope, plus/minus members by logical path."""
        return select_sources(self.sources, scope, include, exclude)


def select_sources(
    sources: Sequence[SourceInfo],
    scope: str = "default",
    include: Iterable[str] = (),
    exclude: Iterable[str] = (),
) -> list[SourceInfo]:
    """Apply the scope choice. Non-indexable and over-limit sources are never selected."""
    if scope not in ("default", "all"):
        raise ValueError(f"scope must be 'default' or 'all', got {scope!r}")
    plus = set(include)
    minus = set(exclude)
    chosen = []
    for s in sources:
        if not s.selectable or s.member in minus:
            continue
        if scope == "all" or s.default_scope or s.member in plus:
            chosen.append(s)
    return chosen


def plan_collection(path: str | os.PathLike[str], aliases: Aliases | None = None) -> CollectionPlan:
    """Discover a zip, folder or file and describe it as one collection."""
    target = Path(path)
    sources = tuple(discover(target, aliases=aliases))
    if target.is_dir():
        kind = KIND_FOLDER
    elif target.is_file() and zipfile.is_zipfile(target):
        kind = KIND_ERS_ZIP
    else:
        kind = KIND_FILE
    identity = parse_ers_name(target.name) if kind == KIND_ERS_ZIP else None
    accounts = sorted({s.zone for s in sources if s.zone})
    system = accounts[0] if len(accounts) == 1 else None
    label = identity.description if identity and identity.description else target.name
    return CollectionPlan(
        kind=kind,
        path=target,
        identity=identity,
        system=system,
        machine=None,
        label=label,
        sources=sources,
    )
