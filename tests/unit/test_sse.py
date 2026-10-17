"""Progress hub and SSE streaming, without sockets."""

import json
import threading
import time

from dmslog.server import sse


def parse_frames(chunks):
    frames = []
    for chunk in chunks:
        text = chunk.decode()
        if text.startswith("event: "):
            name, data = text.split("\n")[:2]
            frames.append((name[7:], json.loads(data[6:])))
    return frames


def test_format_event_is_a_single_data_line():
    frame = sse.format_event("progress", {"source": "a\nb", "n": 1})
    assert frame.endswith(b"\n\n")
    assert frame.count(b"\n") == 3  # event line, data line, blank line
    assert parse_frames([frame]) == [("progress", {"source": "a\nb", "n": 1})]


def test_hub_runs_one_job_at_a_time_and_replays_history():
    hub = sse.ProgressHub()
    assert hub.try_begin("j1") and hub.running and hub.job_id == "j1"
    assert not hub.try_begin("j2")
    hub.publish("progress", {"n": 1})
    hub.publish("progress", {"n": 2}, coalesce=True)
    hub.publish("progress", {"n": 3}, coalesce=True)
    hub.finish({"status": "ok"})
    assert not hub.running
    events, seq = hub.wait(0, 0)
    assert [(e.name, e.data) for e in events] == [
        ("progress", {"n": 1}),
        ("progress", {"n": 3}),
        ("done", {"status": "ok"}),
    ]
    assert seq == events[-1].seq
    assert hub.try_begin("j3")  # a new job clears the previous history
    assert hub.wait(0, 0)[0] == []


def test_wait_wakes_up_when_an_event_is_published():
    hub = sse.ProgressHub()
    result = []

    def waiter():
        result.append(hub.wait(0, 10))

    thread = threading.Thread(target=waiter)
    started = time.monotonic()
    thread.start()
    time.sleep(0.05)
    hub.publish("progress", {"n": 1})
    thread.join(5)
    assert time.monotonic() - started < 5
    assert [e.data for e in result[0][0]] == [{"n": 1}]


def run_stream(hub, *, gone=lambda: False, stopping=None, **kwargs):
    chunks = []
    stopping = stopping or threading.Event()
    thread = threading.Thread(
        target=sse.stream, args=(hub, chunks.append, gone, stopping), kwargs=kwargs, daemon=True
    )
    thread.start()
    return thread, chunks, stopping


def test_stream_delivers_events_and_keepalives_then_stops():
    hub = sse.ProgressHub()
    hub.try_begin("j")
    hub.publish("progress", {"n": 1})
    thread, chunks, stopping = run_stream(hub, keepalive_s=0.05, poll_s=0.01)
    time.sleep(0.2)
    hub.finish({"status": "ok"})
    time.sleep(0.1)
    stopping.set()
    thread.join(5)
    assert not thread.is_alive()
    assert [name for name, _ in parse_frames(chunks)] == ["progress", "done"]
    assert sse.KEEPALIVE_FRAME in chunks


def test_stream_ends_when_the_client_is_gone():
    hub = sse.ProgressHub()
    state = {"gone": False}
    thread, _, _ = run_stream(hub, gone=lambda: state["gone"], poll_s=0.01)
    time.sleep(0.05)
    assert thread.is_alive()
    state["gone"] = True
    thread.join(5)
    assert not thread.is_alive()


def test_stream_ends_when_writing_fails():
    hub = sse.ProgressHub()

    def broken(_data):
        raise BrokenPipeError

    thread = threading.Thread(target=sse.stream, args=(hub, broken, lambda: False, threading.Event()))
    thread.start()
    thread.join(5)
    assert not thread.is_alive()


def test_the_latest_panels_event_survives_a_new_job_for_late_subscribers():
    hub = sse.ProgressHub()
    assert hub.try_begin("j1")
    hub.publish("panels", {"to_id": 10})
    hub.publish("panels", {"to_id": 20})
    hub.finish({})
    assert hub.try_begin("j2")  # clears the event log of the previous job
    events, _ = hub.wait(0, 0.01)
    assert [(e.name, e.data["to_id"]) for e in events] == [("panels", 20)]
