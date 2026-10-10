"""Polling watcher: keeps a case up to date while the adapters keep writing (``dmslog watch``).

Command line (a folder only: ERS zips are static)::

    dmslog watch PATH --case NAME [--interval SECONDS] [--all-logs]

``PATH`` is an installation ``Log`` folder (or an extracted ERS). Every ``--interval`` seconds (default
2) the watcher ``os.stat``-s the in-scope files and the folders that contain them. Only when something
differs (growth, truncation, rotation ``.log`` -> ``.log.0`` with a new ``.log``, a new or a removed
file) it runs the regular incremental ingestion (``pipeline.run_ingest``): the result equals a one-shot
ingestion of the same files. A cycle without changes writes nothing and prints nothing. One line is
printed per cycle that changed something, for example::

    12:00:02 +342 records in dnaApp_zone1/SVC/WfmAdapter_1111.log | events +12, links +3, payloads +2

Ctrl+C stops it cleanly. ``dmslog serve --watch PATH`` runs the same watcher inside the viewer's
process and publishes one ``watch`` event per change on the case's progress stream (SSE).

Guarantees:
- No file handle is kept between cycles: files are opened, read and closed inside ``run_ingest``
  (a handle kept open would block the rotation rename on Windows). Between cycles only ``os.stat``
  results are held.
- Work per cycle is bounded: ``os.stat`` of the in-scope files and of their folders. The folder tree is
  listed again (``discover``) only when a folder changed or every ``rescan_every`` cycles.
- Errors are retried with an exponential backoff (``backoff_delay``); the state is only advanced after
  a successful ingestion, so nothing is lost.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .ers import CollectionConflictError, select_sources
from .pipeline import (
    SCOPE_DEFAULT,
    IngestOptions,
    IngestResult,
    ProgressFn,
    SourceResult,
    build_plan,
    run_ingest,
)

DEFAULT_INTERVAL_S = 2.0
RESCAN_EVERY = 30  # cycles between full folder listings, a safety net for coarse folder timestamps
MAX_BACKOFF_S = 60.0
RECENT_DIR_NS = 3_000_000_000  # a folder modified within this window is listed again on the next poll
UNSETTLED = -1

# (size, mtime_ns, identity): identity is (st_dev, st_ino) when the file system reports them, else the ctime
FileStat = tuple[int, int, tuple[int, ...]]


class PartialFailureError(Exception):
    """A cycle indexed what it could, but some sources failed."""


class WatchStopped(Exception):
    """Raised by a progress callback to abandon the running cycle (the next run starts over)."""


@dataclass
class CycleReport:
    """What one cycle did. ``result`` is None when nothing changed on disk."""

    result: IngestResult | None = None
    cycle: int = 0
    rescanned: bool = False
    news: list[SourceResult] = field(default_factory=list)

    @property
    def failed(self) -> int:
        """Sources that failed in this cycle (the change stays pending and is retried with a backoff)."""
        return self.result.sources_failed if self.result is not None else 0

    @property
    def ran(self) -> bool:
        return self.result is not None

    @property
    def has_news(self) -> bool:
        """True when the cycle wrote records, derived data or touched a source."""
        r = self.result
        if r is None:
            return False
        return bool(
            self.news or r.events_derived or r.links_created or r.payloads or r.payloads_linked or r.warnings
        )


def backoff_delay(interval: float, failures: int) -> float:
    """Wait before the next attempt after ``failures`` consecutive failed cycles (0 = none)."""
    if failures <= 0:
        return interval
    return min(MAX_BACKOFF_S, max(interval, 0.1) * (2**failures))


def _stat(path: Path) -> FileStat | None:
    try:
        st = path.stat()
    except OSError:
        return None
    if st.st_ino and st.st_dev:  # 0 means "unknown" (some file systems, Python < 3.12 on Windows)
        identity: tuple[int, ...] = (st.st_dev, st.st_ino)
    else:
        identity = (st.st_ctime_ns,)
    return (st.st_size, st.st_mtime_ns, identity)


class Watcher:
    """Change detector plus incremental ingestion for one case database and one folder."""

    def __init__(
        self,
        conn: sqlite3.Connection | None,
        path: str | os.PathLike[str],
        options: IngestOptions | None = None,
        *,
        rescan_every: int = RESCAN_EVERY,
    ) -> None:
        self.conn = conn  # may be set per ingestion by a caller that does not keep the database open
        self.path = Path(path)
        if not self.path.is_dir():
            raise NotADirectoryError(str(self.path))
        self.options = options or IngestOptions(scope=SCOPE_DEFAULT)
        self.rescan_every = max(1, rescan_every)
        self.cycles = 0
        self._files: dict[str, Path] = {}  # member -> path of the in-scope files
        self._dirs: dict[Path, int | None] = {}  # folder -> mtime_ns when it was listed
        self._seen: dict[str, FileStat | None] | None = None  # stats after the last successful ingestion
        self._pending: dict[str, FileStat | None] | None = None  # stats taken by the last ``poll``
        self._planned = False
        self._rescanned = False

    # ---- change detection

    def poll(self) -> bool:
        """Stat the in-scope files; True when an ingestion is needed. Never writes anything."""
        self.cycles += 1
        self._rescanned = False
        if (
            not self._planned
            or self.cycles % self.rescan_every == 0
            or any(_dir_mtime(d) != m for d, m in self._dirs.items())
        ):
            self._rescan()
        stats = {member: _stat(path) for member, path in self._files.items()}
        self._pending = stats
        return self._seen is None or stats != self._seen

    def _rescan(self) -> None:
        plan = build_plan(self.path, self.options.limits)
        selected = select_sources(plan.sources, self.options.scope)
        self._files = {s.member: self.path.joinpath(*s.member.split("/")) for s in selected}
        root = self.path
        dirs: set[Path] = {root}
        for s in plan.sources:  # every discovered file, so a first in-scope file of a folder is noticed
            parent = root.joinpath(*s.member.split("/")).parent
            while parent != root and parent not in dirs:
                dirs.add(parent)
                parent = parent.parent
        now = time.time_ns()
        self._dirs = {d: _settled_mtime(d, now) for d in dirs}
        self._planned = True
        self._rescanned = True

    # ---- ingestion

    def ingest(self, progress: ProgressFn | None = None) -> CycleReport:
        """Run the incremental ingestion for the stats of the last ``poll``.

        The state advances only when ``run_ingest`` returns without failed sources; an exception or a
        partial failure leaves the change pending (it is retried by the next poll).
        """
        if self._pending is None:
            raise RuntimeError("ingest() needs a poll() first")
        if self.conn is None:
            raise RuntimeError("no database connection")
        stats = self._pending
        result = run_ingest(self.conn, self.path, self.options, progress)
        if result.sources_failed == 0:
            self._seen = stats
        news = [d for d in result.details if d.action != "unchanged"]
        return CycleReport(result=result, cycle=self.cycles, rescanned=self._rescanned, news=news)

    def cycle(self, progress: ProgressFn | None = None) -> CycleReport:
        """One polling cycle: detect, and ingest only when something changed."""
        if not self.poll():
            return CycleReport(cycle=self.cycles, rescanned=self._rescanned)
        return self.ingest(progress)

    # ---- loop

    def run(
        self,
        stop: threading.Event,
        interval: float = DEFAULT_INTERVAL_S,
        *,
        on_report: Callable[[CycleReport], None] | None = None,
        on_error: Callable[[Exception, float], None] | None = None,
    ) -> None:
        """Poll until ``stop`` is set. Failures back off; a conflicting case is raised to the caller."""
        failures = 0
        while not stop.is_set():
            try:
                report = self.cycle(_stop_check(stop))
                if report.failed:
                    if on_report is not None and report.has_news:
                        on_report(report)
                    raise PartialFailureError(f"{report.failed} source(s) failed")
            except CollectionConflictError:
                raise
            except WatchStopped:
                return
            except Exception as exc:  # a vanished folder, a locked database, a damaged file
                failures += 1
                delay = backoff_delay(interval, failures)
                if on_error is not None:
                    on_error(exc, delay)
            else:
                failures = 0
                delay = interval
                if on_report is not None and report.has_news:
                    on_report(report)
            stop.wait(delay)


def _stop_check(stop: threading.Event) -> ProgressFn:
    """Progress callback that abandons the running ingestion (at a batch boundary) once ``stop`` is set."""

    def check(_event: object) -> None:
        if stop.is_set():
            raise WatchStopped

    return check


def _dir_mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _settled_mtime(path: Path, now_ns: int) -> int | None:
    """The folder mtime to remember; a folder touched a moment ago is remembered as "unknown" so that
    the next poll lists it again (an entry created right after the listing, or inside the same
    timestamp tick of a coarse file system, would otherwise go unnoticed)."""
    mtime = _dir_mtime(path)
    if mtime is not None and now_ns - mtime < RECENT_DIR_NS:
        return UNSETTLED
    return mtime
