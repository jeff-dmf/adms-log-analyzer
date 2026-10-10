"""Sequential reading of sources, with anti zip-bomb limits (docs/ARQUITETURA.md, section 9.2).

``SourceReader.iter_chunks(info, start)`` yields the UTF-8 byte stream of a source in chunks
of at most 1 MiB, starting at byte ``start`` of that stream:

* Folder or file sources are opened, read and closed for every chunk, so no handle stays open
  between chunks (on Windows an open handle blocks the ``.log`` -> ``.log.0`` rotation).
* Zip members are streamed through ``zipfile`` and never extracted to disk. Zip streams have no
  random access, so resuming means reading again and discarding up to ``start``.
* UTF-16 files (with a BOM, as written by installers) are transcoded to UTF-8 in streaming, so
  the parser always sees UTF-8. Offsets and line numbers of such sources refer to the
  transcoded stream. Resuming a transcoded stream also means reading again from the start.
  UTF-16 without a BOM is not detected and is read as it is.

Zip-bomb policy (applied on top of the listing limits of ``discover``):

* Member size, member count and total declared size are checked by ``discover``; the total
  number of decompressed bytes read in one run is bounded again here (``ReadBudget``).
* ``discover`` marks members whose compression ratio exceeds 200x as ``ratio``. Real logs reach
  that ratio (a repetitive 60 MB log compresses to 300 KB), so a high ratio alone does not
  reject a member: ``relax_ratio_violations`` accepts it while its declared size is at most
  ``ratio_exempt_max_bytes`` (1 GiB). Bytes are streamed in constant memory and ``zipfile``
  never returns more than the declared size, so what such a member can cost is bounded by that
  cap. Above the cap, a high ratio still rejects it.
"""

from __future__ import annotations

import codecs
import itertools
import zipfile
from collections.abc import Iterable, Iterator
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from types import TracebackType

from dmslog.ingest.discover import (
    DEFAULT_MAX_MEMBER_BYTES,
    DEFAULT_MAX_MEMBERS,
    DEFAULT_MAX_RATIO,
    DEFAULT_MAX_TOTAL_BYTES,
    SourceInfo,
    normalize_member_name,
)
from dmslog.ingest.fingerprint import HASH_SPAN

CHUNK_SIZE = 1 << 20
RATIO_EXEMPT_MAX_BYTES = 1024**3

BOM_UTF16_LE = b"\xff\xfe"
BOM_UTF16_BE = b"\xfe\xff"


class ReadLimitError(Exception):
    """A read limit was exceeded; ``reason`` is a short code (``total_bytes``, ``member_bytes``)."""

    diag_safe = True  # the message holds only numbers and fixed words

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ReadLimits:
    """Limits for one ingestion run."""

    max_member_bytes: int = DEFAULT_MAX_MEMBER_BYTES
    max_ratio: float = DEFAULT_MAX_RATIO
    ratio_exempt_max_bytes: int = RATIO_EXEMPT_MAX_BYTES
    max_members: int = DEFAULT_MAX_MEMBERS
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES


class ReadBudget:
    """Counts the raw (decompressed) bytes read in a run and enforces the run-wide limits."""

    def __init__(self, limits: ReadLimits) -> None:
        self.limits = limits
        self.total = 0

    def consume(self, n: int, member_total: int) -> None:
        self.total += n
        if member_total > self.limits.max_member_bytes:
            raise ReadLimitError(
                "member_bytes", f"member exceeded {self.limits.max_member_bytes} decompressed bytes"
            )
        if self.total > self.limits.max_total_bytes:
            raise ReadLimitError("total_bytes", f"run exceeded {self.limits.max_total_bytes} bytes read")


def relax_ratio_violations(sources: Iterable[SourceInfo], limits: ReadLimits) -> list[SourceInfo]:
    """Accept ``ratio`` violations of members at most ``ratio_exempt_max_bytes`` long.

    The caller must run ``resolve_scope`` again on the result so the default scope reflects it.
    """
    out = []
    for s in sources:
        if s.limit_violation == "ratio" and s.size <= limits.ratio_exempt_max_bytes:
            s = replace(s, limit_violation=None)
        out.append(s)
    return out


def _bom_encoding(first: bytes) -> str | None:
    if first.startswith(BOM_UTF16_LE):
        return "utf-16-le"
    if first.startswith(BOM_UTF16_BE):
        return "utf-16-be"
    return None


def _skip(chunks: Iterator[bytes], n: int) -> Iterator[bytes]:
    """Drop the first ``n`` bytes of a chunk stream."""
    for chunk in chunks:
        if n >= len(chunk):
            n -= len(chunk)
            continue
        yield chunk[n:] if n else chunk
        n = 0


def _transcode_utf16(raw: Iterator[bytes], encoding: str, first: bytes) -> Iterator[bytes]:
    """UTF-16 (BOM already identified in ``first``) to UTF-8, in streaming."""
    decoder = codecs.getincrementaldecoder(encoding)(errors="replace")
    pending = first[2:]
    if pending:
        text = decoder.decode(pending)
        if text:
            yield text.encode("utf-8")
    for chunk in raw:
        text = decoder.decode(chunk)
        if text:
            yield text.encode("utf-8")
    tail = decoder.decode(b"", final=True)
    if tail:
        yield tail.encode("utf-8")


class SourceReader:
    """Reads sources of any container kind. Use as a context manager (it caches open zips)."""

    def __init__(self, limits: ReadLimits | None = None, budget: ReadBudget | None = None) -> None:
        self.limits = limits or ReadLimits()
        self.budget = budget or ReadBudget(self.limits)
        self._zips: dict[Path, tuple[zipfile.ZipFile, dict[str, zipfile.ZipInfo]]] = {}

    def __enter__(self) -> SourceReader:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.close()

    def close(self) -> None:
        for archive, _members in self._zips.values():
            archive.close()
        self._zips.clear()

    # ------------------------------------------------------------------ raw access

    def _file_path(self, info: SourceInfo) -> Path:
        if info.container_kind == "file":
            return info.container
        return info.container.joinpath(*info.member.split("/"))

    def _zip_member(self, info: SourceInfo) -> tuple[zipfile.ZipFile, zipfile.ZipInfo]:
        entry = self._zips.get(info.container)
        if entry is None:
            archive = zipfile.ZipFile(info.container)
            names = {normalize_member_name(i.filename): i for i in archive.infolist() if not i.is_dir()}
            entry = (archive, names)
            self._zips[info.container] = entry
        archive, names = entry
        return archive, names[info.member]

    def _file_chunks(self, path: Path, start: int, chunk_size: int) -> Iterator[bytes]:
        """Read ``path`` from ``start`` to its current end, reopening the file for every chunk."""
        pos = start
        while True:
            with path.open("rb") as fh:
                fh.seek(pos)
                data = fh.read(chunk_size)
            if not data:
                return
            pos += len(data)
            yield data

    def _zip_chunks(self, info: SourceInfo, chunk_size: int) -> Iterator[bytes]:
        archive, zinfo = self._zip_member(info)
        with archive.open(zinfo) as fh:
            while data := fh.read(chunk_size):
                yield data

    def _guarded(self, raw: Iterator[bytes]) -> Iterator[bytes]:
        """Count raw bytes against the budget and the per-member limit."""
        member_total = 0
        for chunk in raw:
            member_total += len(chunk)
            self.budget.consume(len(chunk), member_total)
            yield chunk

    def file_encoding(self, info: SourceInfo) -> str | None:
        """``utf-16-le``/``utf-16-be`` when a file or folder source starts with that BOM."""
        with self._file_path(info).open("rb") as fh:
            return _bom_encoding(fh.read(2))

    def read_raw_range(self, info: SourceInfo, start: int, length: int) -> bytes:
        """Raw bytes of a folder or file source (not for zip members)."""
        with self._file_path(info).open("rb") as fh:
            fh.seek(start)
            return fh.read(length)

    # ------------------------------------------------------------------ public API

    def iter_chunks(self, info: SourceInfo, start: int = 0, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
        """Yield the UTF-8 byte stream of ``info`` from byte ``start`` (of that stream).

        Raises ``ReadLimitError`` when a limit is exceeded and ``zipfile.BadZipFile`` /
        ``OSError`` for damaged archives or unreadable files.
        """
        if info.container_kind != "zip":
            if self.file_encoding(info) is None:
                yield from self._guarded(self._file_chunks(self._file_path(info), start, chunk_size))
                return
            raw = self._file_chunks(self._file_path(info), 0, chunk_size)
        else:
            raw = self._zip_chunks(info, chunk_size)
        raw = self._guarded(raw)
        first = next(raw, b"")
        while 0 < len(first) < 2:  # a BOM split across tiny reads
            more = next(raw, b"")
            if not more:
                break
            first += more
        encoding = _bom_encoding(first)

        if encoding is not None:
            stream = _transcode_utf16(raw, encoding, first)
        else:
            stream = itertools.chain([first] if first else [], raw)
        yield from _skip(stream, start) if start else stream

    def read_head(self, info: SourceInfo, length: int = HASH_SPAN) -> bytes:
        """The first ``length`` bytes of the UTF-8 stream of ``info`` (fewer when it is shorter)."""
        out = bytearray()
        with closing(self.iter_chunks(info, 0, max(length, 16 * 1024))) as chunks:
            for chunk in chunks:
                out += chunk
                if len(out) >= length:
                    break
        return bytes(out[:length])

    def is_utf16(self, info: SourceInfo) -> bool:
        """True when the source is a UTF-16 file (cheap for files; zip members read a few bytes)."""
        if info.container_kind != "zip":
            return self.file_encoding(info) is not None
        archive, zinfo = self._zip_member(info)
        with archive.open(zinfo) as fh:
            return _bom_encoding(fh.read(2)) is not None
