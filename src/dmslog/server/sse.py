"""Progress hub and Server-Sent Events streaming for ingest jobs.

One ``ProgressHub`` per case holds the events of the latest job. Producers (the ingest thread)
publish; any number of SSE handler threads wait on a ``threading.Condition`` (no busy waiting)
and write what is new. A client that disconnects is noticed on the next wake-up (at most
``POLL_S`` later) and its handler thread returns.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

KEEPALIVE_S = 15.0
POLL_S = 0.5
MAX_EVENTS = 5000
STICKY = frozenset({"watch", "panels"})


@dataclass(frozen=True, slots=True)
class HubEvent:
    seq: int
    name: str
    data: dict[str, Any]
    coalescable: bool = False


class ProgressHub:
    """Event log of the latest ingest job of one case."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._events: deque[HubEvent] = deque(maxlen=MAX_EVENTS)
        self._seq = 0
        self._running = False
        self._job_id: str | None = None
        # latest follow-mode events; survive try_begin(): late subscribers still get them
        self._last_sticky: dict[str, HubEvent] = {}

    @property
    def running(self) -> bool:
        with self._cond:
            return self._running

    @property
    def job_id(self) -> str | None:
        with self._cond:
            return self._job_id

    def try_begin(self, job_id: str) -> bool:
        """Start a job; False when one is already running (one job per case)."""
        with self._cond:
            if self._running:
                return False
            self._running = True
            self._job_id = job_id
            self._events.clear()
            return True

    def publish(self, name: str, data: dict[str, Any], *, coalesce: bool = False) -> None:
        """Append an event; a ``coalesce`` event replaces a directly preceding ``coalesce`` event."""
        with self._cond:
            self._seq += 1
            if coalesce and self._events and self._events[-1].coalescable:
                self._events.pop()
            event = HubEvent(self._seq, name, data, coalesce)
            self._events.append(event)
            if name in STICKY:
                self._last_sticky[name] = event
            self._cond.notify_all()

    def finish(self, data: dict[str, Any]) -> None:
        """Publish the final ``done`` event and mark the job as not running (atomically)."""
        with self._cond:
            self._seq += 1
            self._events.append(HubEvent(self._seq, "done", data))
            self._running = False
            self._cond.notify_all()

    def wait(self, after: int, timeout: float) -> tuple[list[HubEvent], int]:
        """Events with ``seq > after``; waits up to ``timeout`` seconds when there are none."""
        with self._cond:
            self._cond.wait_for(lambda: self._seq > after, timeout)
            events = [e for e in self._events if e.seq > after]
            if after == 0:  # a new subscriber learns about the latest follow-mode change
                have = {e.seq for e in events}
                events = sorted(
                    [*events, *(e for e in self._last_sticky.values() if e.seq not in have)],
                    key=lambda e: e.seq,
                )
            return events, self._seq

    def wake_all(self) -> None:
        with self._cond:
            self._cond.notify_all()


def format_event(name: str, data: dict[str, Any]) -> bytes:
    """One SSE frame. ``json.dumps`` never emits raw newlines, so the data stays on one line."""
    payload = json.dumps(data, separators=(",", ":"))
    return f"event: {name}\ndata: {payload}\n\n".encode()


KEEPALIVE_FRAME = b": keep-alive\n\n"


def stream(
    hub: ProgressHub,
    write: Callable[[bytes], None],
    client_gone: Callable[[], bool],
    stopping: threading.Event,
    *,
    keepalive_s: float | None = None,
    poll_s: float | None = None,
) -> None:
    """Serve one SSE client until it disconnects or the server stops.

    ``write`` raises ``OSError`` when the peer is gone; that also ends the loop.
    """
    keepalive = KEEPALIVE_S if keepalive_s is None else keepalive_s
    poll = POLL_S if poll_s is None else poll_s
    last_seq = 0
    last_write = time.monotonic()
    try:
        write(b"retry: 3000\n\n")
        while not stopping.is_set():
            events, last_seq = hub.wait(last_seq, min(poll, keepalive))
            for event in events:
                write(format_event(event.name, event.data))
                last_write = time.monotonic()
            if client_gone():
                return
            if time.monotonic() - last_write >= keepalive:
                write(KEEPALIVE_FRAME)
                last_write = time.monotonic()
    except (OSError, ValueError):
        return
