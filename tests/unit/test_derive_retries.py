"""RetryDeriver: retry sequences by msgId (ARQUITETURA 6.5)."""

from dmslog.derive.base import StoredEntry, run_in_memory
from dmslog.derive.retries import RetryDeriver

A = "00000000-0000-4000-8000-00000000000a"
B = "00000000-0000-4000-8000-00000000000b"


class Log:
    """Builds a synthetic stream with increasing ids."""

    def __init__(self, code_family="ONT"):
        self.entries: list[StoredEntry] = []
        self.fam = code_family

    def _add(self, ts, tid, code, cls, msg, fields, sev=4, keys=()):
        self.entries.append(StoredEntry(
            id=len(self.entries) + 1, stream_id=1, ts=ts, tid=tid, pid=1, level=None, sev=sev,
            tpl_code=code, tpl_class=cls, fields=fields, msg=msg, keys=keys,
        ))  # fmt: skip

    def attempt(self, ts, mid, tid=5):
        if self.fam == "ONT":
            self._add(
                ts,
                tid,
                "ONT-011",
                "req_resp",
                "x",
                {"message_id": mid, "msg_type": "ExampleEventMessageType"},
                keys=(("msgid", mid),),
            )
        else:
            self._add(
                ts,
                tid,
                "SMN-007",
                "req_resp",
                "x",
                {"msg_id": mid, "msg_type": "ExampleEventMessageType"},
                keys=(("msgid", mid),),
            )

    def fail(self, ts, tid=5, exc="System.ServiceModel.EndpointNotFoundException"):
        code = "ONT-020" if self.fam == "ONT" else "SMN-019"
        self._add(
            ts, tid, code, "error", "x", {"msg_type": "ExampleEventMessageType", "exc_type": exc}, sev=2
        )

    def sent(self, ts, mid, tid=5):
        code = "ONT-012" if self.fam == "ONT" else "SMN-008"
        self._add(
            ts,
            tid,
            code,
            "req_resp",
            "x",
            {"message_id" if self.fam == "ONT" else "msg_id": mid},
            keys=(("msgid", mid),),
        )

    def gave_up(self, ts, mid, tid=5):
        code = "ONT-022" if self.fam == "ONT" else "SMN-018"
        self._add(
            ts,
            tid,
            code,
            "error",
            "x",
            {"message_id" if self.fam == "ONT" else "msg_id": mid},
            sev=2,
            keys=(("msgid", mid),),
        )

    def other(self, ts, tid=5):
        self._add(ts, tid, "ONT-013", "end", "y", {})


def derive(entries, **kw):
    return run_in_memory([RetryDeriver("ont", **kw)], entries)


def test_first_attempt_success_is_not_a_retry():
    log = Log()
    log.attempt(1000, A)
    log.sent(1100, A)
    assert derive(log.entries) == []


def test_two_attempts_then_sent():
    log = Log()
    log.attempt(1000, A)
    log.fail(1050)
    log.attempt(48000, A)
    log.sent(48100, A)
    (ev,) = derive(log.entries)
    assert ev.kind == "retry_seq" and ev.key == A and ev.status == "ok"
    assert ev.detail["op_key"] == A
    assert ev.detail["attempts"] == 2 and ev.detail["failures"] == 1
    assert ev.detail["first_ts"] == 1000 and ev.detail["last_ts"] == 48100
    assert ev.detail["intervals_ms"] == [47000]
    assert ev.detail["outcome"] == "sent"
    assert ev.detail["exc_types"] == ["System.ServiceModel.EndpointNotFoundException"]
    assert ev.start_entry_id == 1 and ev.end_entry_id == 4
    assert ev.sev == 3  # recovered: capped at warning
    assert ev.ts_start == 1000 and ev.duration_ms == 47100 and ev.tid == 5


def test_three_attempts_smn_shapes():
    log = Log("SMN")
    log.attempt(0, A)
    log.fail(10)
    log.attempt(47000, A)
    log.fail(47010)
    log.attempt(94000, A)
    log.sent(94100, A)
    (ev,) = derive(log.entries)
    assert ev.detail["attempts"] == 3 and ev.detail["failures"] == 2
    assert ev.detail["intervals_ms"] == [47000, 47000]
    assert ev.detail["exc_types"] == ["System.ServiceModel.EndpointNotFoundException"]


def test_never_succeeds_five_attempts_failed():
    log = Log()
    for i in range(5):
        log.attempt(i * 45000, A)
        log.fail(i * 45000 + 5)
    log.gave_up(5 * 45000, A)
    (ev,) = derive(log.entries)
    assert ev.detail["outcome"] == "failed" and ev.status == "fail"
    assert ev.detail["attempts"] == 5 and ev.detail["failures"] == 5
    assert ev.sev == 2
    assert len(ev.detail["intervals_ms"]) == 4


def test_never_closes_is_open_at_eof_only():
    log = Log()
    log.attempt(0, A)
    log.fail(5)
    log.attempt(40000, A)
    d = RetryDeriver("ont")
    for e in log.entries:
        assert list(d.feed(e)) == []
    assert d.low_watermark() == 1
    assert list(d.flush(eof=False)) == []
    (ev,) = d.flush(eof=True)
    assert ev.detail["outcome"] == "open" and ev.status == "open" and ev.detail["attempts"] == 2
    assert d.low_watermark() is None


def test_single_attempt_without_failure_open_is_silent():
    log = Log()
    log.attempt(0, A)
    assert derive(log.entries) == []


def test_interleaved_msg_ids_on_different_tids():
    log = Log()
    log.attempt(0, A, tid=1)
    log.attempt(10, B, tid=2)
    log.fail(20, tid=2)  # belongs to B (last attempt on tid 2)
    log.fail(25, tid=1)  # belongs to A
    log.attempt(40000, B, tid=2)
    log.attempt(40010, A, tid=1)
    log.sent(40100, A, tid=1)
    log.sent(40200, B, tid=2)
    evs = {e.key: e for e in derive(log.entries)}
    assert set(evs) == {A, B}
    for k in (A, B):
        assert evs[k].detail["attempts"] == 2 and evs[k].detail["failures"] == 1
    assert evs[A].tid == 1 and evs[B].tid == 2
    assert evs[B].detail["intervals_ms"] == [39990]
    assert evs[A].detail["intervals_ms"] == [40010]


def test_failure_on_a_tid_without_open_attempt_is_ignored():
    log = Log()
    log.fail(0, tid=9)
    log.other(1)
    assert derive(log.entries) == []


def test_failure_is_not_attributed_after_the_message_finished():
    log = Log()
    log.attempt(0, A)
    log.sent(10, A)
    log.fail(20)
    assert derive(log.entries) == []


def test_unknown_template_fallback_by_text():
    def e(i, ts, msg):
        return StoredEntry(
            id=i,
            stream_id=1,
            ts=ts,
            tid=3,
            pid=1,
            level=None,
            sev=4,
            tpl_code=None,
            tpl_class=None,
            fields={},
            msg=msg,
        )

    entries = [
        e(1, 0, f'Sending "ExampleEventMessageType" message: {A} with verb: Changed...'),
        e(
            2,
            5,
            'Error: Sending "ExampleEventMessageType" message failed. "System.TimeoutException" has occurred',
        ),
        e(3, 600, f'Sending "ExampleEventMessageType" message: {A.upper()} with verb: Changed...'),
        e(
            4,
            700,
            f'Sending "ExampleEventMessageType" message: {A} with verb: Changed finished successfully.',
        ),
    ]
    (ev,) = derive(entries)
    assert ev.key == A and ev.detail["attempts"] == 2 and ev.detail["outcome"] == "sent"


def test_known_but_unrelated_template_is_ignored():
    log = Log()
    log.attempt(0, A)
    log.other(5)
    log.attempt(10, A)
    log.sent(20, A)
    (ev,) = derive(log.entries)
    assert ev.detail["attempts"] == 2 and ev.detail["failures"] == 0


def test_none_ts_tid_and_out_of_order_ts_do_not_raise():
    log = Log()
    log.attempt(None, A, tid=None)
    log.fail(None, tid=None)
    log.attempt(500, A, tid=None)
    log.attempt(100, A, tid=None)  # goes back in time
    log.sent(None, A, tid=None)
    (ev,) = derive(log.entries)
    assert ev.detail["attempts"] == 3
    assert all(i >= 0 for i in ev.detail["intervals_ms"])


def test_open_sequences_are_bounded_and_evicted_as_open():
    log = Log()
    ids = [f"00000000-0000-4000-8000-{i:012x}" for i in range(6)]
    for i, mid in enumerate(ids):
        log.attempt(i * 10, mid, tid=i)
        log.fail(i * 10 + 1, tid=i)
    d = RetryDeriver("ont", max_open=3)
    events = []
    for e in log.entries:
        events.extend(d.feed(e))
    assert d.evicted_count == 3
    assert [ev.key for ev in events] == ids[:3]
    assert all(ev.detail["outcome"] == "open" and ev.detail["evicted"] for ev in events)
    assert d.low_watermark() == 7  # the 4th message's first attempt


def _rederive_equal(entries, cut):
    full = derive(entries)
    d = RetryDeriver("ont")
    prefix_events = []
    for e in entries[:cut]:
        prefix_events.extend(d.feed(e))
    prefix_events.extend(d.flush(eof=False))
    wm = d.low_watermark()
    start = wm if wm is not None else entries[cut - 1].id + 1
    rerun = run_in_memory([RetryDeriver("ont")], [e for e in entries if e.id >= start])
    kept = [ev for ev in prefix_events if ev.start_entry_id < start]
    return full, kept + rerun


def test_rederivation_contract_for_every_cut():
    log = Log()
    log.attempt(0, A, tid=1)
    log.attempt(5, B, tid=2)
    log.fail(10, tid=2)
    log.fail(12, tid=1)
    log.attempt(900, B, tid=2)
    log.sent(950, B, tid=2)
    log.attempt(1000, A, tid=1)
    log.fail(1005, tid=1)
    log.gave_up(1100, A, tid=1)
    log.attempt(1200, "00000000-0000-4000-8000-0000000000ff", tid=3)
    log.fail(1201, tid=3)
    key = lambda ev: (ev.start_entry_id, ev.key)  # noqa: E731
    for cut in range(1, len(log.entries) + 1):
        full, again = _rederive_equal(log.entries, cut)
        assert sorted(again, key=key) == sorted(full, key=key), cut


def test_deterministic():
    log = Log()
    log.attempt(0, A)
    log.fail(1)
    log.attempt(100, A)
    assert derive(log.entries) == derive(log.entries)
