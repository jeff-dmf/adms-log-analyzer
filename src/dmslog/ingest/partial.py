"""Partial open (ROADMAP 9.9, ARQUITETURA 9.3b): index a time window and/or some sources first, the rest
later.

The first pass indexes, for each chosen source (expanded to its rotation chain), only the records whose
header time falls in ``[ts_from, ts_to)``; the second pass (the same ``run_ingest``, or any later one)
completes every source. The result after the second pass is the one of a full indexing: same records, keys,
payloads, buckets, events and links (compared by locator, ``(collection, member, line)``; ids differ).

Record ids follow the file order inside a source and along a rotation chain (the derivation checkpoint relies
on it), so the window cannot simply get the next ids: before indexing the window of a source, a range of
``lines + 1`` ids is reserved for the whole file (a record has at least one line), the window gets the ids
that leave room for the part before it, and ``meta['id_floor.entry']`` is raised to the end of the range so
that every later insert (any writer) lands above it. The second pass fills the part before the window
(``prefix``) and the part after it (``suffix``) inside that range, then turns the source into a normal one
(``indexed_bytes``, head and tail hashes, status ``ready``); growth of a live file after that is the usual
incremental append.

State of a source indexed out of order: ``meta['partial.<source_id>']`` (JSON, :class:`PartialState`), written
in the same transaction as the records it describes; the source row says ``status='indexing'`` with
``head_hash`` NULL (so the rotation follower and the incremental decision leave it alone). Resuming a segment
deletes its last stored record and parses again from it, like the normal resume. No schema change.

The window is located by a scan of the stream (``locate_window``): byte offset and line of the first header
whose time is ``>= ts_from`` and of the first header after it whose time is ``>= ts_to``, assuming the time
goes forward at 1 MiB granularity (the backwards steps of the logs are milliseconds to seconds, a DST step is
one hour): a window boundary may move by a few records. It is only a priority: the second pass indexes every
record anyway.

Sources chosen first get ids below sources that come before them in the regular order. The only result that
depends on the id order across sources is the example record of an unknown signature
(``unknown_sig.first_entry_id``): ``meta['partial_order.<collection_id>']`` names the families to repair, and
the run that completes the collection sets those examples as a full indexing would.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from dataclasses import asdict, dataclass

from dmslog.ingest.discover import SourceInfo
from dmslog.ingest.fingerprint import HASH_SPAN, sha1_hex, span_for
from dmslog.ingest.reader import SourceReader
from dmslog.parse.envelope import PLAIN, Envelope, detect_envelope, get_envelope
from dmslog.store.writer import PARTIAL_PREFIX, PARTIAL_SCOPE_PREFIX

DETECT_BYTES = 64 * 1024  # bytes examined to pick the envelope of a new source ...
DETECT_LINES = 50  # ... or this many lines, whichever comes first (what detect_envelope looks at)
LAST_HEADER_PROBE_LINES = 4096  # lines examined backwards to find the last timed header of a chunk

STATE_VERSION = 1
SEG_WINDOW = "window"
SEG_PREFIX = "prefix"
SEG_SUFFIX = "suffix"
SCOPE_KEY_PREFIX = PARTIAL_SCOPE_PREFIX  # meta['partial_scope.<collection_id>'] = scope of the partial run


@dataclass(frozen=True)
class FirstPass:
    """What a partial open indexes first (``IngestOptions.first``).

    ``members``: the sources to index first, as listed (real or shown member names); each one brings its
    rotation chain. None = every selected source. ``ts_from``/``ts_to``: the window ``[ts_from, ts_to)`` in
    ms of the log's local time (U6); None on one side = open on that side; both None = whole sources.
    ``defer_rest``: stop after the first pass (the rest is completed by a later ``run_ingest`` of the same
    path, e.g. ``resume_pending``); the API runs both passes in one job.
    """

    members: frozenset[str] | None = None
    ts_from: int | None = None
    ts_to: int | None = None
    defer_rest: bool = False

    def __post_init__(self) -> None:
        if self.members is not None and not self.members:
            raise ValueError("first pass: members must not be empty (use None for every source)")
        for value in (self.ts_from, self.ts_to):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError("first pass: ts_from and ts_to are integers (ms)")
        if self.ts_from is not None and self.ts_to is not None and self.ts_from >= self.ts_to:
            raise ValueError("first pass: ts_from must be lower than ts_to")

    @property
    def windowed(self) -> bool:
        return self.ts_from is not None or self.ts_to is not None


@dataclass(frozen=True)
class WindowSpan:
    """Where a window lies in the UTF-8 stream of a source (``locate_window``)."""

    envelope: str
    lo: int  # byte offset of the first record of the window (a line start)
    lo_line: int  # its line number (1-based)
    hi: int  # byte offset just after the window (a line start, or the end of the stream)
    hi_line: int  # line number at ``hi``
    size: int  # bytes of the stream
    lines: int  # lines of the stream (the last one may lack its newline)
    head_hash: str  # sha1 of the first span_for(size) bytes

    @property
    def whole(self) -> bool:
        return self.lo == 0 and self.hi >= self.size

    @property
    def empty(self) -> bool:
        return self.lo >= self.hi


@dataclass
class PartialState:
    """``meta['partial.<source_id>']``: a source whose records are being indexed out of file order.

    Ids: ``[base, win)`` for the prefix ``[0, lo)``, ``[win, end)`` for the window ``[lo, hi)`` and then
    the suffix ``[hi, size)`` (from ``sfx``, set when the suffix starts). ``segment`` is the one to do next.
    """

    segment: str
    base: int
    win: int
    end: int
    lo: int
    lo_line: int
    hi: int
    hi_line: int
    size: int  # stream bytes when the window was located (the suffix stops there)
    raw_size: int  # size of the file or member then (identity check)
    zip_crc: int | None
    head_hash: str
    envelope: str
    sfx: int | None = None
    v: int = STATE_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, text: str | None) -> PartialState | None:
        try:
            data = json.loads(text or "")
            state = cls(**data)
        except (TypeError, ValueError):
            return None
        if state.v != STATE_VERSION or state.segment not in (SEG_WINDOW, SEG_PREFIX, SEG_SUFFIX):
            return None
        return state


def state_key(source_id: int) -> str:
    return f"{PARTIAL_PREFIX}{int(source_id)}"


def load_state(conn: sqlite3.Connection, source_id: int) -> PartialState | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (state_key(source_id),)).fetchone()
    return PartialState.from_json(row[0]) if row else None


def save_state(conn: sqlite3.Connection, source_id: int, state: PartialState) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (state_key(source_id), state.to_json())
    )


def drop_state(conn: sqlite3.Connection, source_id: int) -> None:
    conn.execute("DELETE FROM meta WHERE key = ?", (state_key(source_id),))


def partial_source_ids(conn: sqlite3.Connection) -> set[int]:
    """Ids of the sources with a partial state (``partial.`` keys; '/' is the character after '.')."""
    rows = conn.execute(
        "SELECT substr(key, ?) FROM meta WHERE key >= ? AND key < ?",
        (len(PARTIAL_PREFIX) + 1, PARTIAL_PREFIX, PARTIAL_PREFIX[:-1] + "/"),
    )
    out: set[int] = set()
    for (text,) in rows:
        if text and text.isdigit():
            out.add(int(text))
    return out


# --------------------------------------------------------------------------- pending work


@dataclass(frozen=True)
class PendingCollection:
    """A collection whose sources are not all indexed yet (a partial open that did not finish)."""

    collection_id: int
    group: int
    path: str
    scope: str
    partial: int  # sources indexed out of order (window only, or part of the rest)
    pending: int  # selected sources not indexed at all yet
    indexing: int  # sources whose (in-order) indexing was interrupted


def pending_work(conn: sqlite3.Connection) -> list[PendingCollection]:
    """Collections a partial open left incomplete, with the scope it used (to complete them with
    ``run_ingest(path, IngestOptions(scope=...))``). Collections without a ``partial_scope`` mark (no
    partial open ever ran on them) are not listed: their pending sources follow the old rules."""
    marks = {
        int(k[len(SCOPE_KEY_PREFIX) :]): v
        for k, v in conn.execute(
            "SELECT key, value FROM meta WHERE key >= ? AND key < ?",
            (SCOPE_KEY_PREFIX, SCOPE_KEY_PREFIX[:-1] + "/"),
        )
        if k[len(SCOPE_KEY_PREFIX) :].isdigit()
    }
    if not marks:
        return []
    partial = partial_source_ids(conn)
    out: list[PendingCollection] = []
    for cid, scope in sorted(marks.items()):
        row = conn.execute(
            "SELECT path, coalesce(ers_group, id) FROM collection WHERE id = ?", (cid,)
        ).fetchone()
        if row is None:
            continue
        counts = {"partial": 0, "pending": 0, "indexing": 0}
        for sid, status in conn.execute("SELECT id, status FROM source WHERE collection_id = ?", (cid,)):
            if sid in partial:
                counts["partial"] += 1
            elif status == "pending":
                counts["pending"] += 1
            elif status == "indexing":
                counts["indexing"] += 1
        if any(counts.values()):
            out.append(PendingCollection(cid, int(row[1]), row[0], scope, **counts))
    return out


# --------------------------------------------------------------------------- locating the window


def detect_from_chunks(chunks: Iterator[bytes], fallback: str) -> tuple[Envelope, bytes, Iterator[bytes]]:
    """The envelope the pipeline would pick for this stream (same bytes examined), the bytes consumed, and
    the rest of the chunks."""
    pending: list[bytes] = []
    size = 0
    for chunk in chunks:
        pending.append(chunk)
        size += len(chunk)
        if size >= DETECT_BYTES or sum(c.count(b"\n") for c in pending) >= DETECT_LINES:
            data = b"".join(pending)
            return detect_envelope(data), data, chunks
    data = b"".join(pending)
    return (detect_envelope(data) if data else get_envelope(fallback)), data, chunks


def _last_timed_header(envelope: Envelope, buf: bytes, end: int) -> int | None:
    """Time of the last timed header among the complete lines ``buf[:end]`` (None: none in the probe)."""
    pos = end - 1  # the newline that ends the last line
    for _ in range(LAST_HEADER_PROBE_LINES):
        if pos < 0:
            return None
        start = buf.rfind(b"\n", 0, pos) + 1
        header = envelope.match(buf[start : pos + 1])
        if header is not None and header.ts_ms is not None:
            return header.ts_ms
        pos = start - 1
    return None


def _first_at_or_after(envelope: Envelope, buf: bytes, pos: int, end: int, target: int) -> int | None:
    scan = getattr(envelope, "scan", None)
    if scan is not None:
        for off, header in scan(buf, pos, end):
            if header.ts_ms is not None and header.ts_ms >= target:
                return off
        return None
    while pos < end:
        nl = buf.find(b"\n", pos, end)
        nxt = end if nl < 0 else nl + 1
        header = envelope.match(buf[pos:nxt])
        if header is not None and header.ts_ms is not None and header.ts_ms >= target:
            return pos
        pos = nxt
    return None


def locate_window(
    reader: SourceReader, info: SourceInfo, ts_from: int | None, ts_to: int | None, chunk_size: int
) -> WindowSpan:
    """Scan the stream of ``info`` once: envelope, window offsets, size, line count and head hash.

    ``lo`` = first header with a time ``>= ts_from`` (0 when ``ts_from`` is None), ``hi`` = first header at or
    after ``lo`` with a time ``>= ts_to`` (the end when ``ts_to`` is None or no header reaches it); a source
    without timed headers has an empty window at its end (or the whole source when ``ts_from`` is None).
    Cost: one read of the whole stream (decompression for a zip member), ``bytes.count`` per chunk, and the
    header regex only where a boundary is crossed (a chunk whose last timed header is before the boundary is
    skipped).
    """
    bounds: list[int | None] = [0 if ts_from is None else None, None]
    lines_at: list[int] = [1, 0]
    targets = (ts_from, ts_to)
    head = bytearray()
    offset = newlines = 0  # stream offset of the buffer, newlines before it
    carry = b""
    last_byte = b""
    with closing(reader.iter_chunks(info, 0, chunk_size)) as chunks:
        envelope, first, rest = detect_from_chunks(chunks, info.envelope)
        timed = envelope.name != PLAIN.name

        def feed(data: bytes, final: bool) -> None:
            nonlocal carry, offset, newlines
            buf = carry + data if carry else data
            end = len(buf) if final else buf.rfind(b"\n") + 1
            pos = 0
            while timed and pos < end:
                i = 0 if bounds[0] is None else 1
                target = targets[i]
                if target is None or bounds[i] is not None:
                    break
                if not final:
                    last = _last_timed_header(envelope, buf, end)
                    if last is not None and last < target:
                        break  # the whole chunk lies before the boundary
                found = _first_at_or_after(envelope, buf, pos, end, target)
                if found is None:
                    break
                bounds[i] = offset + found
                lines_at[i] = newlines + buf.count(b"\n", 0, found) + 1
                pos = found
            newlines += buf.count(b"\n", 0, end)
            offset += end
            carry = buf[end:]

        for data in (first, *rest):
            if not data:
                continue
            if len(head) < HASH_SPAN:
                head += data[: HASH_SPAN - len(head)]
            last_byte = data[-1:]
            feed(data, final=False)
        feed(b"", final=True)
    size = offset
    lines = newlines + (1 if size and last_byte != b"\n" else 0)
    lo, lo_line = (bounds[0], lines_at[0]) if bounds[0] is not None else (size, newlines + 1)
    hi, hi_line = (bounds[1], lines_at[1]) if bounds[1] is not None else (size, newlines + 1)
    if hi < lo:  # cannot happen (the end is searched from the start on); kept as a guard
        hi, hi_line = lo, lo_line
    return WindowSpan(
        envelope.name, lo, lo_line, hi, hi_line, size, lines, sha1_hex(bytes(head[: span_for(size)]))
    )


def head_hash_of(reader: SourceReader, info: SourceInfo, size: int) -> str:
    """sha1 of the first ``span_for(size)`` bytes of the stream (same span as ``WindowSpan.head_hash``)."""
    return sha1_hex(reader.read_head(info)[: span_for(size)])
