"""ERS identity, Enterprise/Sensitive pairing and the "one ERS per case" rule.

An ERS capture is a pair of zips named
`ERS_CL_<GUID>_<yyyymmddHHMMSS>_[Sensitive_]Enterprise_<description>.zip` (docs/ERS.md).
The two halves of the same capture share the GUID and the timestamp.
"""

from __future__ import annotations

import os
import re
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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
    """The case already holds a different collection (the API turns this into HTTP 409).

    `reason` is one of: "different_guid", "different_capture", "not_an_ers", "case_has_folder".
    """

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


def check_can_add(existing: Iterable[ErsIdentity | None], new: ErsIdentity | None) -> None:
    """Enforce "one ERS per case" (decision U5).

    `existing` has one entry per collection already in the case: its ErsIdentity, or None for
    a folder or single file. Accepted: an empty case, the other half of the same capture
    (same GUID and timestamp) and the very same zip again (re-ingestion). Anything else
    raises CollectionConflictError.
    """
    held = list(existing)
    if not held:
        return
    if new is None:
        raise CollectionConflictError("not_an_ers", "the case already has a collection; open a new case")
    for current in held:
        if current is None:
            raise CollectionConflictError(
                "case_has_folder", "the case holds a folder or file collection; open a new case"
            )
        if current.guid != new.guid:
            raise CollectionConflictError(
                "different_guid",
                f"ERS {new.guid} differs from the case's ERS {current.guid}; open a new case",
            )
        if current.timestamp != new.timestamp:
            raise CollectionConflictError(
                "different_capture",
                f"capture {new.timestamp} differs from the case's capture {current.timestamp}; "
                "open a new case",
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


def plan_collection(path: str | os.PathLike[str]) -> CollectionPlan:
    """Discover a zip, folder or file and describe it as one collection."""
    target = Path(path)
    sources = tuple(discover(target))
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
