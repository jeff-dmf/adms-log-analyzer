"""Fast paths of the derivation pass: RowEntry, lazy fields, early exits, link runs (synthetic data only)."""

from __future__ import annotations

import json
import random
from typing import Any

import pytest

from dmslog.derive import build_derivers
from dmslog.derive.base import Event, RowEntry, StoredEntry, run_in_memory
from dmslog.derive.engine import _runs
from dmslog.derive.stats import StatsDeriver

RULES = [
    {
        "name_field": "op",
        "start": ["S-1"],
        "end_ok": ["E-1"],
        "fail": ["F-1"],
        "check_key": "request_id",
        "business_error": ["B-1", "B-2"],
    },
    {"name": "Fixed", "start": ["S-2"], "end": ["E-2"]},
]


def make_pair(
    i: int,
    ts: int | None,
    tid: int | None,
    code: str | None,
    cls: str | None,
    fields: dict[str, Any] | None,
    msg: str,
    cont: str = "",
    keys: tuple[tuple[str, str], ...] = (),
    fields_json: str | None = None,
) -> tuple[StoredEntry, RowEntry]:
    """The same record as a StoredEntry (hand-built) and as a RowEntry (the engine's representation)."""
    raw = (
        fields_json
        if fields_json is not None
        else (json.dumps(fields, separators=(",", ":")) if fields else "")
    )
    stored = StoredEntry(
        id=i, stream_id=0, ts=ts, tid=tid, pid=None, level=None, sev=4, tpl_code=code, tpl_class=cls,
        fields=json.loads(raw) if raw else {}, msg=msg, cont=cont, keys=keys,
    )  # fmt: skip
    row = RowEntry((i, ts, tid, 4, raw, msg, 0, cont, keys, code, cls))
    return stored, row


def test_row_entry_attributes() -> None:
    stored, row = make_pair(
        7, 1000, 3, "S-1", "start", {"op": "Get", "n": 5}, "hello", cont="a\nb", keys=(("request_id", "9"),)
    )
    for name in ("id", "ts", "tid", "sev", "tpl_code", "tpl_class", "msg", "cont", "keys", "fields"):
        assert getattr(row, name) == getattr(stored, name), name
    assert row.pid is None and row.level is None and row.stream_id == 0
    assert row.fields_raw == '{"op":"Get","n":5}'
    assert row.fields is row.fields  # parsed once, cached


@pytest.mark.parametrize(
    ("raw", "name", "expected"),
    [
        ("", "op", None),
        ('{"op":"Get"}', "op", "Get"),
        ('{"a":"x","op":"Get","b":1}', "op", "Get"),
        ('{"op":"G\\"et"}', "op", 'G"et'),  # escaped quote: falls back to the JSON parser
        ('{"op":"caf\\u0065"}', "op", "cafe"),
        ('{"op":5}', "op", 5),
        ('{"op":[1,2]}', "op", [1, 2]),
        ('{"hop":"x"}', "op", None),  # another key that ends with the name
        ('{"msg":"has op inside"}', "op", None),
        ('{"msg":"a \\"op\\":\\"b\\""}', "op", None),  # the text of a value that looks like a pair
        ('{"op":null}', "op", None),
        ('{"op": "spaced"}', "op", "spaced"),  # other separators: parser fallback
    ],
)
def test_row_entry_field_matches_parser(raw: str, name: str, expected: Any) -> None:
    stored, row = make_pair(1, 1, 1, None, None, None, "m", fields_json=raw)
    assert row.field(name) == expected
    assert stored.field(name) == expected


def test_row_entry_bad_json_is_empty() -> None:
    row = RowEntry((1, 1, 1, 4, "{not json", "m", 0, "", (), None, None))
    assert row.fields == {}
    assert row.field("x") is None


def random_stream(seed: int, n: int = 400) -> list[tuple[StoredEntry, RowEntry]]:
    rnd = random.Random(seed)
    codes = [None, "S-1", "E-1", "F-1", "B-1", "B-2", "S-2", "E-2", "X-9", "GEN-DBG-01", "GEN-MAP-01"]
    pairs = []
    ts = 1_700_000_000_000
    for i in range(1, n + 1):
        ts += rnd.choice([1, 5, 50, 4_000_000, -4_000_000_00]) if rnd.random() < 0.1 else rnd.randint(0, 40)
        code = rnd.choice(codes)
        fields: dict[str, Any] = {}
        if rnd.random() < 0.6:
            fields["op"] = rnd.choice(["Get", "Put", "Del"])
        if rnd.random() < 0.15:
            fields["codes"] = rnd.sample(["E1", "E2", "E3"], rnd.randint(1, 3))
        if rnd.random() < 0.1:
            fields["exc_type"] = "System.Failure"
            fields["exc_msg"] = "bad"
        if code == "GEN-DBG-01":
            fields.update(from_name="Info", from_n=3, to_name="Debug", to_n=5)
        if code == "GEN-MAP-01":
            fields.update(map="m1", state=rnd.choice(["closed", "is now open.", "content:"]))
        keys = (("request_id", str(rnd.randint(1, 3))),) if rnd.random() < 0.5 else ()
        msg = rnd.choice(
            ["plain", "Request failed", "[Communication2] - Client-side statistics:", "Sending x"]
        )
        cont = "at Foo.Bar()\nat Baz()" if rnd.random() < 0.05 else ""
        pairs.append(
            make_pair(
                i, None if rnd.random() < 0.03 else ts, rnd.choice([1, 2, 3, None]), code,
                rnd.choice(["start", "end", "error", "other"]), fields or None, msg, cont, keys,
            )
        )  # fmt: skip
    return pairs


def signature(events: list[Event]) -> list[Any]:
    return [
        (e.kind, e.name, e.key, e.tid, e.start_entry_id, e.end_entry_id, e.ts_start, e.ts_end, e.duration_ms,
         e.status, e.sev, e.n_entries, e.detail_json(), tuple(e.entry_ids))
        for e in events
    ]  # fmt: skip


@pytest.mark.parametrize("family", ["AAVL", "AWFM", "SERVICE"])
@pytest.mark.parametrize("seed", range(6))
def test_row_entries_give_the_same_events_as_stored_entries(family: str, seed: int) -> None:
    pairs = random_stream(seed)
    a = run_in_memory(build_derivers(family, RULES), [p[0] for p in pairs])
    b = run_in_memory(build_derivers(family, RULES), [p[1] for p in pairs])
    assert signature(a) == signature(b)
    assert any(e.kind == "operation" for e in a)  # the stream exercises the operations deriver


def test_operation_fields_hint_does_not_lose_codes() -> None:
    """An entry joined to an operation contributes its error codes and exception (read lazily)."""
    rules = [{"name": "Op", "start": ["S-1"], "end_ok": ["E-1"]}]
    pairs = [
        make_pair(1, 10, 1, "S-1", "start", None, "begin"),
        make_pair(2, 11, 1, "X-9", "other", {"codes": ["A1", "A2"]}, "mid"),
        make_pair(3, 12, 1, "X-9", "other", {"exc_type": "System.IO.Failure", "exc_msg": "gone"}, "mid"),
        make_pair(4, 13, 1, "X-9", "other", {"unrelated": 1}, "mid"),
        make_pair(5, 14, 1, "E-1", "end", None, "end"),
    ]
    for side in (0, 1):
        events = run_in_memory(build_derivers("AWFM", rules), [p[side] for p in pairs])
        (op,) = [e for e in events if e.kind == "operation"]
        assert op.detail["error_codes"] == ["A1", "A2"]
        assert op.detail["exception"] == "System.IO.Failure: gone"
        assert op.entry_ids == [1, 2, 3, 4, 5]


def test_runs_compress_consecutive_ids_in_order() -> None:
    out: list[tuple[int, int, int]] = []
    _runs(9, [1, 2, 3, 7, 8, 10, 4, 5, 5], out)
    assert out == [(9, 1, 3), (9, 7, 8), (9, 10, 10), (9, 4, 5), (9, 5, 5)]
    out.clear()
    _runs(9, [], out)
    assert out == []


def stats_pair(i: int, tid: int | None, msg: str, ts: int = 1_000) -> StoredEntry:
    return make_pair(i, ts, tid, None, None, None, msg)[0]


def test_stats_blocks_close_only_on_their_own_thread() -> None:
    header = "[Communication2] - Client-side statistics:"
    d = StatsDeriver("AAVL")
    d.feed(stats_pair(1, 7, header))
    assert d.low_watermark() == 1
    d.feed(stats_pair(2, 8, "other thread"))  # another thread: the block stays open
    assert d.low_watermark() == 1
    d.feed(stats_pair(3, 7, "same thread"))  # its own thread ends it (no totals: skipped)
    assert d.low_watermark() is None
    assert d.skipped == 1
    d.feed(stats_pair(4, 9, header))
    d.feed(stats_pair(5, None, "no thread"))  # a record without thread ends every block
    assert d.low_watermark() is None
    assert d.skipped == 2
    d.feed(stats_pair(6, 9, header))
    d.feed(stats_pair(7, None, header.replace("Communication2", "Communication3")))
    assert d.low_watermark() == 6  # a second channel: both blocks open
    d.feed(stats_pair(8, 9, "same thread as the first"))  # closes the block of thread 9 and the one without
    assert d.low_watermark() is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"outer":{"op":"wrong"},"op":"right"}', "right"),
        ('{"outer":{"op":"nested"}}', None),
        ('{"op":"value"}', "value"),
        ('{"a":"op","op":"x"}', "x"),
        ('{"a":["x",{"op":"deep"}],"b":"y"}', None),
        ('{"o\\u0070":"escaped key"}', "escaped key"),
    ],
)
def test_row_entry_field_is_json_loads_on_tricky_shapes(raw: str, expected: Any) -> None:
    _, row = make_pair(1, 1, 1, None, None, None, "m", fields_json=raw)
    assert row.field("op") == json.loads(raw).get("op") == expected


def test_row_entry_field_property_equals_json_loads() -> None:
    hypothesis = pytest.importorskip("hypothesis")
    st = hypothesis.strategies
    keys = st.sampled_from(["op", "a", "b", "o", "p", "op2", 'q"', "\\", "\u00e9"])
    texts = st.text(alphabet=st.sampled_from(list('opab"\\{}[]:, \u00e9')), max_size=6)
    scalars = st.one_of(texts, st.integers(-5, 5), st.none(), st.booleans())
    values = st.recursive(
        scalars,
        lambda inner: st.one_of(st.lists(inner, max_size=3), st.dictionaries(keys, inner, max_size=3)),
        max_leaves=8,
    )
    docs = st.dictionaries(keys, values, max_size=4)

    @hypothesis.settings(max_examples=400, deadline=None)
    @hypothesis.given(doc=docs, ensure_ascii=st.booleans(), compact=st.booleans())
    def check(doc: dict[str, Any], ensure_ascii: bool, compact: bool) -> None:
        raw = json.dumps(doc, ensure_ascii=ensure_ascii, separators=(",", ":") if compact else None)
        row = RowEntry((1, 1, 1, 4, raw, "m", 0, "", (), None, None))
        for name in ("op", "a", "o"):
            assert row.field(name) == json.loads(raw).get(name), (raw, name)

    check()
