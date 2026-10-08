"""Source fingerprints and the incremental-ingestion decision (docs/ARQUITETURA.md, section 9.3).

A source is identified by the hash of its first bytes (``head_hash``) and, for growth checks,
by the hash of the bytes just before the indexed offset (``tail_hash``). Both cover at most
``HASH_SPAN`` bytes of the stream the parser sees (the UTF-8 stream; UTF-16 files are
transcoded first, see ``reader.py``). When fewer bytes exist, the hash covers all of them,
so the span of a stored hash is always ``min(HASH_SPAN, indexed_bytes)``.

This module is pure: it never touches the disk or the database. The pipeline gathers the
facts (lazily, through callables) and ``decide_action`` returns what to do.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

HASH_SPAN = 4096


def sha1_hex(data: bytes | bytearray | memoryview) -> str:
    return hashlib.sha1(data).hexdigest()


def span_for(indexed_bytes: int) -> int:
    """Number of bytes covered by a stored head/tail hash of a source with this many indexed bytes."""
    return min(HASH_SPAN, max(indexed_bytes, 0))


class HeadWindow:
    """Collects the first ``HASH_SPAN`` bytes of a stream."""

    def __init__(self, initial: bytes = b"") -> None:
        self._data = bytearray(initial[:HASH_SPAN])

    def update(self, chunk: bytes | bytearray | memoryview) -> None:
        room = HASH_SPAN - len(self._data)
        if room > 0:
            self._data += bytes(chunk[:room])

    def hexdigest(self, span: int) -> str:
        return sha1_hex(self._data[:span])


class TailWindow:
    """Keeps the last ``HASH_SPAN`` bytes fed to it."""

    def __init__(self) -> None:
        self._data = bytearray()

    def update(self, chunk: bytes | bytearray | memoryview) -> None:
        if len(chunk) >= HASH_SPAN:
            self._data = bytearray(chunk[-HASH_SPAN:])
            return
        self._data += chunk
        if len(self._data) > HASH_SPAN:
            del self._data[: len(self._data) - HASH_SPAN]

    def hexdigest(self) -> str:
        return sha1_hex(self._data)


class Action(Enum):
    NEW = "new"  # never indexed
    UNCHANGED = "unchanged"  # nothing to do, nothing is written
    APPEND = "append"  # the file grew: re-read from its last record
    RESUME = "resume"  # a previous run was interrupted: continue from its last record
    REINDEX = "reindex"  # rewritten, truncated or failed: drop its records and start over


@dataclass(frozen=True)
class KnownSource:
    """What the case database remembers about a source (subset of the ``source`` row)."""

    status: str
    size: int | None
    zip_crc: int | None
    head_hash: str | None
    indexed_bytes: int
    tail_hash: str | None


def decide_action(
    known: KnownSource | None,
    *,
    is_zip: bool,
    utf16: bool,
    size: int,
    zip_crc: int | None,
    head_matches: Callable[[], bool],
    tail_matches: Callable[[], bool],
) -> Action:
    """Choose what to do with one source on (re)ingestion.

    ``head_matches`` and ``tail_matches`` are evaluated lazily (they read the file) and only
    when the decision needs them. ``size`` is the current size of the file or zip member.
    """
    if known is None or known.status in ("skipped", "pending"):
        return Action.NEW
    if known.status == "error":
        return Action.REINDEX
    if is_zip:
        # Zip members are immutable: the CRC and size identify the content.
        if known.zip_crc != zip_crc or known.size != size:
            return Action.REINDEX
        return Action.UNCHANGED if known.status == "ready" else Action.RESUME
    if not head_matches():
        return Action.REINDEX
    if utf16:
        # Transcoded streams have no stable byte mapping to the file: any change is a full reindex.
        return Action.UNCHANGED if known.status == "ready" and known.size == size else Action.REINDEX
    if size < known.indexed_bytes:
        return Action.REINDEX
    if not tail_matches():
        return Action.REINDEX
    if known.status != "ready":
        return Action.RESUME
    return Action.UNCHANGED if size == known.indexed_bytes else Action.APPEND
