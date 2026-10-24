"""Reduced sources: the beginning plus the end of a very large log (ROADMAP R3b, ARQUITETURA 9.3c).

A source whose size passes the per-file limit (``source_cap_bytes``, 256 MiB by default) is indexed
*reduced*: the records whose header starts in the first ``head_bytes`` (the start-up, 1 MiB by default)
and the records from the first header at or after ``size - source_cap_bytes`` to the end. The stretch in
between is read only to count its lines, so ``line_no`` stays the real line of the file, and is never parsed
or stored.

``CappedGrouper`` is a push parser with the interface of ``RecordGrouper`` (``feed`` / ``finish`` /
``envelope``). Its result depends only on the bytes of the stream and the three numbers (size, limit, head),
never on how the stream was cut into chunks:

* ``h`` is the start of the first header line at or after ``head_bytes``; the head is ``[0, h)``;
* ``lo`` is the start of the first header line at or after ``max(size - limit, h)``; the tail is
  ``[lo, EOF)``;  without a header after the cut, ``lo`` is the end of the stream (the tail is empty);
* the stretch ``[h, lo)`` is skipped; when ``h`` is already past the cut nothing is skipped and the source is
  indexed whole, exactly as a plain ``RecordGrouper`` would do.

After the first indexing the state is kept in ``meta['capped.<source_id>']`` (:class:`CappedState`), written
in the same transaction as the first tail records. The limit applies to the first indexing only: a source that
grows is appended from its last record like any other (``ingest/pipeline.py``), starting at ``max(last record,
lo)``; a source that is rewritten or truncated is indexed again from scratch and the limit stored for its
collection group (``meta['source_cap.<group>']``) is applied again.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass

from dmslog.parse.envelope import Envelope
from dmslog.parse.records import DEFAULT_MAX_RECORD_BYTES, Record, RecordGrouper, RecordTooLargeError
from dmslog.store.writer import CAPPED_PREFIX, SOURCE_CAP_PREFIX

DEFAULT_SOURCE_CAP_BYTES = 256 * 1024**2
DEFAULT_HEAD_BYTES = 1024**2
# Bytes a reduced source may skip (read and counted, never stored): the listing limit of such a source.
DEFAULT_MAX_SKIPPED_MEMBER_BYTES = 64 * 1024**3

_HEAD, _FIND_H, _SKIP, _FIND_LO, _TAIL = range(5)


def is_candidate(size: int, cap_bytes: int, head_bytes: int = DEFAULT_HEAD_BYTES) -> bool:
    """True when a source of ``size`` bytes is indexed reduced (limit enabled and cut past the head)."""
    return cap_bytes > 0 and size - cap_bytes > head_bytes


def indexed_estimate(size: int, cap_bytes: int, head_bytes: int = DEFAULT_HEAD_BYTES) -> int:
    """Upper bound of the bytes a first indexing of ``size`` bytes stores (the disk-space check)."""
    return cap_bytes + head_bytes if is_candidate(size, cap_bytes, head_bytes) else size


@dataclass(frozen=True)
class CappedState:
    """What was skipped in a reduced source (stored as JSON in ``meta['capped.<source_id>']``)."""

    cap: int  # per-file limit in force
    head_bytes: int
    h: int  # byte offset where the skipped stretch starts (end of the head)
    h_line: int  # line number of ``h``
    lo: int  # byte offset where the tail starts
    lo_line: int  # line number of ``lo``
    skipped_bytes: int
    skipped_lines: int
    skipped_from_ts: int | None  # time of the last record of the head
    skipped_to_ts: int | None  # time of the first record of the tail
    size_at_cap: int  # size of the source when it was reduced

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> CappedState | None:
        try:
            data = json.loads(text)
            return cls(**data)
        except (ValueError, TypeError):
            return None


def capped_key(source_id: int) -> str:
    return f"{CAPPED_PREFIX}{source_id}"


def load_capped(conn: sqlite3.Connection, source_id: int) -> CappedState | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (capped_key(source_id),)).fetchone()
    return CappedState.from_json(row[0]) if row else None


def load_all_capped(conn: sqlite3.Connection) -> dict[int, CappedState]:
    """Every reduced source of the case in one query: ``{source_id: state}`` (file list)."""
    out: dict[int, CappedState] = {}
    for key, value in conn.execute("SELECT key, value FROM meta WHERE key LIKE ?", (CAPPED_PREFIX + "%",)):
        try:
            out[int(key[len(CAPPED_PREFIX) :])] = CappedState.from_json(value)
        except (ValueError, TypeError, KeyError):
            continue
    return out


def save_capped(conn: sqlite3.Connection, source_id: int, state: CappedState) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (capped_key(source_id), state.to_json())
    )


def drop_capped(conn: sqlite3.Connection, source_id: int) -> None:
    conn.execute("DELETE FROM meta WHERE key = ?", (capped_key(source_id),))


def source_cap_key(group: int) -> str:
    return f"{SOURCE_CAP_PREFIX}{group}"


def load_source_cap(conn: sqlite3.Connection, group: int) -> tuple[int, int] | None:
    """``(cap_bytes, head_bytes)`` stored for a collection group, or None."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (source_cap_key(group),)).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row[0])
        return int(data["cap"]), int(data["head"])
    except (ValueError, TypeError, KeyError):
        return None


def save_source_cap(conn: sqlite3.Connection, group: int, cap_bytes: int, head_bytes: int) -> None:
    """Remember the limit of a new collection group (an existing value is kept)."""
    value = json.dumps({"cap": cap_bytes, "head": head_bytes}, sort_keys=True, separators=(",", ":"))
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)", (source_cap_key(group), value))


class CappedGrouper:
    """Groups the head and the tail of a stream into records and skips what lies between (see the module)."""

    def __init__(
        self,
        envelope: Envelope,
        *,
        size: int,
        cap_bytes: int,
        head_bytes: int,
        max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
    ) -> None:
        if head_bytes < 1:
            raise ValueError("head_bytes must be positive")
        self.envelope = envelope
        self.size = size
        self.cap_bytes = cap_bytes
        self.head_bytes = head_bytes
        self._cut = size - cap_bytes  # T: the tail starts at the first header line at or after it
        self._max = max_record_bytes
        self._head = RecordGrouper(envelope, max_record_bytes=max_record_bytes)
        self._tail: RecordGrouper | None = None
        self._phase = _HEAD
        self._pos = 0  # stream bytes received so far
        self._nl = 0  # newlines in the bytes already processed (the carry has none)
        self._carry = b""  # incomplete last line of a header search
        self._out: list[Record] = []
        self._h: int | None = None
        self._h_line = 0
        self._lo: int | None = None
        self._lo_line = 0
        self._from_ts: int | None = None
        self._to_ts: int | None = None
        self._tail_started = False

    # ------------------------------------------------------------------ results

    @property
    def skipped(self) -> bool:
        """True once a stretch was skipped (``lo`` is known and lies past ``h``)."""
        return self._lo is not None and self._h is not None and self._lo > self._h

    @property
    def after_skip_off(self) -> int | None:
        """Offset of the first record after the skipped stretch (None: nothing skipped, or not known yet)."""
        return self._lo if self.skipped else None

    @property
    def tail_started(self) -> bool:
        """True once a record of the tail was returned by ``feed`` or ``finish``."""
        return self._tail_started

    def state(self) -> CappedState | None:
        """The state to store, once a stretch was skipped; None when the source is indexed whole."""
        if not self.skipped:
            return None
        assert self._h is not None and self._lo is not None
        return CappedState(
            cap=self.cap_bytes,
            head_bytes=self.head_bytes,
            h=self._h,
            h_line=self._h_line,
            lo=self._lo,
            lo_line=self._lo_line,
            skipped_bytes=self._lo - self._h,
            skipped_lines=self._lo_line - self._h_line,
            skipped_from_ts=self._from_ts,
            skipped_to_ts=self._to_ts,
            size_at_cap=self.size,
        )

    # ------------------------------------------------------------------ feeding

    def feed(self, chunk: bytes | bytearray | memoryview) -> list[Record]:
        """Add bytes of the stream; return the records that became final."""
        data = bytes(chunk)
        self._out = []
        base = self._pos
        self._pos += len(data)
        while data:
            if self._phase == _HEAD:
                data, base = self._in_head(data, base)
            elif self._phase == _FIND_H:
                data, base = self._in_find_h(data, base)
            elif self._phase == _SKIP:
                data, base = self._in_skip(data, base)
            elif self._phase == _FIND_LO:
                data, base = self._in_find_lo(data, base)
            else:
                data, base = self._in_tail(data, base)
        out, self._out = self._out, []
        return out

    def finish(self, *, final: bool) -> list[Record]:
        """End of the stream: the remaining records (the last one is ``partial`` as in ``RecordGrouper``)."""
        self._out = []
        phase = self._phase
        if phase == _HEAD:
            self._emit_head(self._head.finish(final=final))
        elif phase == _FIND_H:
            if self._carry:  # the whole stream is the head: no header after the head size
                self._emit_head(self._head.feed(self._carry))
                self._carry = b""
            self._emit_head(self._head.finish(final=final))
        elif phase == _SKIP:  # every byte after the head is skipped: the tail is empty
            self._set_lo(self._pos)
        elif phase == _FIND_LO:
            carry, self._carry = self._carry, b""
            if carry and self.envelope.match(carry) is not None:
                self._start_tail(self._pos - len(carry))
                assert self._tail is not None
                self._emit_tail(self._tail.feed(carry))
                self._emit_tail(self._tail.finish(final=final))
            else:  # the unterminated last line is not (yet) a header: skipped with the rest
                self._set_lo(self._pos)
        else:
            assert self._tail is not None
            self._emit_tail(self._tail.finish(final=final))
        out, self._out = self._out, []
        return out

    # ------------------------------------------------------------------ phases

    def _in_head(self, data: bytes, base: int) -> tuple[bytes, int]:
        """Feed the head until the end of the first line that starts at or after ``head_bytes``."""
        need = self.head_bytes - 1
        end = base + len(data)
        i = -1 if end <= need else data.find(b"\n", max(0, need - base))
        if i < 0:
            self._feed_head(data)
            return b"", end
        self._feed_head(data[: i + 1])
        self._phase = _FIND_H
        return data[i + 1 :], base + i + 1

    def _in_find_h(self, data: bytes, base: int) -> tuple[bytes, int]:
        """Lines after the head size up to the first header: they belong to the last head record."""
        prefix, rest, off, found = self._lines_until_header(data, base)
        if prefix:
            self._feed_head(prefix)
        if not found:
            return b"", base + len(data)
        self._emit_head(self._head.finish(final=True))
        self._h, self._h_line = off, self._nl + 1
        if off >= self._cut:  # the head already reaches the cut: nothing to skip
            self._start_tail(off)
        else:
            self._phase = _SKIP
        return rest, off

    def _in_skip(self, data: bytes, base: int) -> tuple[bytes, int]:
        """Count lines up to the end of the first line that starts at or after the cut."""
        need = self._cut - 1
        end = base + len(data)
        i = -1 if end <= need else data.find(b"\n", max(0, need - base))
        if i < 0:
            self._nl += data.count(b"\n")
            return b"", end
        self._nl += data.count(b"\n", 0, i + 1)
        self._phase = _FIND_LO
        return data[i + 1 :], base + i + 1

    def _in_find_lo(self, data: bytes, base: int) -> tuple[bytes, int]:
        """Skip lines up to the first header: it starts the tail."""
        prefix, rest, off, found = self._lines_until_header(data, base)
        self._nl += prefix.count(b"\n")
        if not found:
            return b"", base + len(data)
        self._start_tail(off)
        return rest, off

    def _in_tail(self, data: bytes, base: int) -> tuple[bytes, int]:
        assert self._tail is not None
        self._emit_tail(self._tail.feed(data))
        return b"", base + len(data)

    # ------------------------------------------------------------------ helpers

    def _lines_until_header(self, data: bytes, base: int) -> tuple[bytes, bytes, int, bool]:
        """Examine the complete lines of ``carry + data``.

        Returns ``(prefix, rest, offset, found)``: ``prefix`` = the complete non-header lines before the first
        header, ``rest`` = the stream from that header on, ``offset`` = its position (``found``). Without a
        header ``prefix`` holds every complete line and the unterminated last line stays in the carry.
        """
        carry = self._carry
        buf = carry + data if carry else data
        buf_off = base - len(carry)
        match = self.envelope.match
        find = buf.find
        idx = 0
        while True:
            nl = find(b"\n", idx)
            if nl < 0:
                break
            if match(buf[idx : nl + 1]) is not None:
                self._carry = b""
                return buf[:idx], buf[idx:], buf_off + idx, True
            idx = nl + 1
        self._carry = buf[idx:]
        if len(self._carry) > self._max:
            raise RecordTooLargeError(buf_off + idx, len(self._carry), self._max)
        return buf[:idx], b"", buf_off + idx, False

    def _feed_head(self, piece: bytes) -> None:
        self._nl += piece.count(b"\n")
        self._emit_head(self._head.feed(piece))

    def _emit_head(self, records: list[Record]) -> None:
        for r in records:
            if r.ts_ms is not None:
                self._from_ts = r.ts_ms
        self._out.extend(records)

    def _emit_tail(self, records: list[Record]) -> None:
        if records and not self._tail_started:
            self._tail_started = True
            self._to_ts = records[0].ts_ms
        self._out.extend(records)

    def _set_lo(self, off: int) -> None:
        self._lo, self._lo_line = off, self._nl + 1

    def _start_tail(self, off: int) -> None:
        self._set_lo(off)
        self._tail = RecordGrouper(
            self.envelope, start_off=off, start_line=self._lo_line, max_record_bytes=self._max
        )
        self._phase = _TAIL
