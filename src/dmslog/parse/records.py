"""Grouping of lines into records, safe for resuming (ARQUITETURA 6.2).

A record is a header line plus every following line that the envelope does not
recognize as a header. Lines before the first header form one orphan record.

Lines end at ``\\n``; a ``\\r`` right before it belongs to the end of line. Input may be
fed in chunks cut at any byte: only complete lines are examined until :meth:`finish`,
and the header decision depends only on a line prefix (see ``envelope.py``), so the
records never depend on how the input was chunked.

Resume model: every record except the last one is final. The last record is returned
with ``partial=True`` when the input is not final (live file) or when the input does
not end with a newline. To resume, persist the ``byte_off`` and ``line_no`` of the
last record, drop that record, and parse again from that offset.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import BinaryIO

from .envelope import BOM, Envelope, Header

DEFAULT_CHUNK = 1 << 20
_ORPHAN = Header(None, None, None, None, 0)  # field source for orphan records

_ScanFn = Callable[[bytearray, int, int], Iterable[tuple[int, Header]]]


@dataclass(slots=True)
class Record:
    byte_off: int
    line_no: int
    nlines: int
    ts_ms: int | None
    pid: int | None
    tid: int | None
    level: int | None
    msg: str  # first line after the envelope, without end of line (spaces preserved)
    cont: str | None  # continuation lines joined with "\n"; None when nlines == 1
    partial: bool
    nbytes: int = 0  # raw length in the file, end of line included
    process: str | None = None  # from the envelope (oasErrLog only)
    orphan: bool = False  # lines before the first header


def _scan_by_line(envelope: Envelope, buf: bytearray, pos: int, endpos: int) -> Iterator[tuple[int, Header]]:
    """Generic scan for envelopes without a ``scan`` method."""
    match = envelope.match
    while pos < endpos:
        nl = buf.find(b"\n", pos, endpos)
        nxt = endpos if nl < 0 else nl + 1
        h = match(bytes(buf[pos:nxt]))
        if h is not None:
            yield pos, h
        pos = nxt


def _strip_eol(raw: bytes | bytearray) -> bytes | bytearray:
    """Drop the end of line of the last line: one ``\\n`` and then one ``\\r``."""
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    if raw.endswith(b"\r"):
        raw = raw[:-1]
    return raw


class RecordGrouper:
    """Push parser: :meth:`feed` byte chunks, then call :meth:`finish` once."""

    def __init__(self, envelope: Envelope, *, start_off: int = 0, start_line: int = 1) -> None:
        self.envelope = envelope
        scan = getattr(envelope, "scan", None)
        self._scan: _ScanFn = scan if scan is not None else (lambda b, p, e: _scan_by_line(envelope, b, p, e))
        self._buf = bytearray()  # pending record (from index 0) plus the unterminated tail
        self._buf_off = start_off  # file offset of _buf[0]
        self._line_no = start_line  # line number of _buf[0]
        self._scan_pos = 0  # end of the complete lines already examined; _buf[_scan_pos:] has no "\n"
        self._header: Header | None = None  # header of the pending record; None = orphan lines
        self._finished = False

    @property
    def pending_offset(self) -> int:
        """File offset where the pending (not yet returned) data starts."""
        return self._buf_off

    def feed(self, chunk: bytes | bytearray | memoryview) -> list[Record]:
        """Add bytes; return the records that became final."""
        if self._finished:
            raise RuntimeError("feed() after finish()")
        if not chunk:
            return []
        buf = self._buf
        old_len = len(buf)
        buf += chunk
        nl = buf.rfind(b"\n", old_len)
        if nl < 0:
            return []
        return self._consume(nl + 1)

    def finish(self, *, final: bool) -> list[Record]:
        """End of the available input.

        Returns the remaining records. The last one is ``partial`` when ``final`` is
        False (the file may still grow) or when the input does not end with a newline.
        """
        if self._finished:
            raise RuntimeError("finish() called twice")
        self._finished = True
        buf = self._buf
        out = self._consume(len(buf)) if len(buf) > self._scan_pos else []
        if buf:
            partial = not final or not buf.endswith(b"\n")
            out.append(self._make(0, len(buf), self._header, partial, self._line_no))
            self._buf_off += len(buf)
            self._buf = bytearray()
        return out

    def _consume(self, end: int) -> list[Record]:
        """Examine lines in ``[_scan_pos, end)`` and emit the records they close."""
        buf = self._buf
        out: list[Record] = []
        rec_start = 0
        line_no = self._line_no
        header = self._header
        for pos, h in self._scan(buf, self._scan_pos, end):
            if pos > rec_start:
                rec = self._make(rec_start, pos, header, False, line_no)
                line_no += rec.nlines
                out.append(rec)
            rec_start = pos
            header = h
        self._header = header
        self._line_no = line_no
        if rec_start:
            del buf[:rec_start]
            self._buf_off += rec_start
        self._scan_pos = end - rec_start
        return out

    def _make(self, start: int, end: int, header: Header | None, partial: bool, line_no: int) -> Record:
        buf = self._buf
        nl = buf.find(b"\n", start, end)
        if nl < 0:
            first_end = next_line = end
        else:
            first_end, next_line = nl, nl + 1
        if first_end > start and buf[first_end - 1] == 0x0D:
            first_end -= 1
        if header is None:
            msg_start = start + 3 if buf.startswith(BOM, start, first_end) else start
        else:
            msg_start = min(start + header.msg_start, first_end)
        msg = buf[msg_start:first_end].decode("utf-8", "replace")
        if next_line >= end:
            cont = None
            nlines = 1
        else:
            nlines = buf.count(b"\n", start, end)
            if buf[end - 1] != 0x0A:
                nlines += 1
            cont = _strip_eol(buf[next_line:end]).replace(b"\r\n", b"\n").decode("utf-8", "replace")
        h = _ORPHAN if header is None else header
        return Record(
            byte_off=self._buf_off + start,
            line_no=line_no,
            nlines=nlines,
            ts_ms=h.ts_ms,
            pid=h.pid,
            tid=h.tid,
            level=h.level,
            msg=msg,
            cont=cont,
            partial=partial,
            nbytes=end - start,
            process=h.process,
            orphan=header is None,
        )


def iter_records(
    stream: BinaryIO,
    env: Envelope,
    *,
    start_off: int = 0,
    start_line: int = 1,
    final: bool,
    chunk_size: int = DEFAULT_CHUNK,
) -> Iterator[Record]:
    """Read ``stream`` to EOF and yield its records.

    ``stream`` must already be positioned at ``start_off`` (a record start, or 0), which
    is only used to compute offsets; nothing is seeked. See the module docstring for
    ``final`` and ``partial``.
    """
    grouper = RecordGrouper(env, start_off=start_off, start_line=start_line)
    read = stream.read
    while chunk := read(chunk_size):
        yield from grouper.feed(chunk)
    yield from grouper.finish(final=final)


def parse_bytes(
    data: bytes | bytearray, env: Envelope, *, start_off: int = 0, start_line: int = 1, final: bool = True
) -> list[Record]:
    """Parse an in-memory buffer in one go (convenience for tests and small inputs)."""
    grouper = RecordGrouper(env, start_off=start_off, start_line=start_line)
    return grouper.feed(data) + grouper.finish(final=final)
