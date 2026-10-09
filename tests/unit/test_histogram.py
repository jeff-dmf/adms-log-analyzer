"""Timeline histogram (task 2.8): bucket table vs entry fallback, splits, limits, estimates, API."""

import sqlite3
import threading

import pytest
from query_helpers import build_case, entry_rows, open_ro, py_match
from test_api import Client, mf

from dmslog.server.app import DmslogServer
from dmslog.store import query as Q
from dmslog.store.db import create_case_db
from dmslog.store.filters import FilterError

SPREAD = 7000  # stretches the fixture's seconds into hours so several minute buckets exist


def spread_rows():
    rows = entry_rows()
    for r in rows:
        if r["ts"] is not None:
            r["ts"] = 1_760_000_000_000 + (r["ts"] - 1_000_000) * SPREAD
    return rows


def fill_bucket(path):
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(
            "INSERT INTO bucket(source_id, minute, sev, tpl_id, n) "
            "SELECT source_id, ts / 60000, sev, tpl_id, count(*) FROM entry WHERE ts IS NOT NULL "
            "GROUP BY 1, 2, 3, 4"
        )
    conn.close()


@pytest.fixture(scope="module")
def with_bucket(tmp_path_factory):
    path = tmp_path_factory.mktemp("hb") / "index.sqlite"
    rows = build_case(path, spread_rows())
    fill_bucket(path)
    conn = open_ro(path)
    yield conn, rows
    conn.close()


@pytest.fixture(scope="module")
def no_bucket(tmp_path_factory):
    path = tmp_path_factory.mktemp("hn") / "index.sqlite"
    rows = build_case(path, spread_rows())
    conn = open_ro(path)
    yield conn, rows
    conn.close()


def dense(h, rows, tree, split):
    """Per-key list of counts per bucket from the reference evaluator, in the layout of ``h``."""
    key = {"sev": "sev", "source": "source_id", "tpl": "tpl_id"}[split]
    out = {}
    for r in rows:
        if r["ts"] is None or not py_match(tree, r):
            continue
        idx = (r["ts"] - h.start) // h.bucket_ms
        out.setdefault(r[key], [0] * h.n_buckets)[idx] += 1
    return out


def as_dict(h):
    return {s["key"]: s["counts"] for s in h.series}


FILTERS = [
    None,
    {},
    {"sev": {"le": 2}},
    {"source": [1]},
    {"and": [{"source": [1, 2]}, {"not": {"sev": 5}}]},
    {"or": [{"tpl": [1]}, {"sev": 1}]},
    {"sensitive": True},
    {"and": []},
]


@pytest.mark.parametrize("split", ["sev", "source", "tpl"])
@pytest.mark.parametrize("tree", FILTERS)
@pytest.mark.parametrize("bucket_ms", [None, 60_000, 300_000])
def test_bucket_table_is_exact(with_bucket, tree, split, bucket_ms):
    conn, rows = with_bucket
    h = Q.histogram(conn, tree, bucket_ms, split)
    assert (h.method, h.exact) == ("bucket", True)
    ref = dense(h, rows, tree, split) if h.series else {}
    assert as_dict(h) == ref
    assert h.total == sum(sum(v) for v in ref.values())
    assert h.bucket_ms % 60_000 == 0 and h.start % h.bucket_ms == 0


@pytest.mark.parametrize("split", ["sev", "source", "tpl"])
@pytest.mark.parametrize("tree", FILTERS)
def test_entry_fallback_matches_bucket_and_reference(no_bucket, with_bucket, tree, split):
    conn, rows = no_bucket
    h = Q.histogram(conn, tree, 300_000, split)
    assert (h.method, h.exact) == ("entry", True)
    assert as_dict(h) == (dense(h, rows, tree, split) if h.series else {})
    hb = Q.histogram(with_bucket[0], tree, 300_000, split)
    assert (h.bucket_ms, h.start, h.n_buckets) == (hb.bucket_ms, hb.start, hb.n_buckets)
    assert as_dict(h) == as_dict(hb)


def test_unexpressible_filter_and_fine_buckets_use_entry(with_bucket):
    conn, rows = with_bucket
    for tree, width in (({"tid": 16}, None), ({"text": {"substr": "Frame"}}, 120_000), (None, 5000)):
        h = Q.histogram(conn, tree, width)
        assert (h.method, h.exact) == ("entry", True)
        assert as_dict(h) == dense(h, rows, tree, "sev")


def test_time_filter_is_exact_on_entry(with_bucket):
    conn, rows = with_bucket
    t0 = 1_760_000_000_000
    tree = {"ts": {"ge": t0 + 1_000_000, "lt": t0 + 9_000_000}}
    h = Q.histogram(conn, tree, None)
    assert (h.method, h.exact) == ("entry", True)
    assert as_dict(h) == dense(h, rows, tree, "sev") and h.total > 0


def test_null_ts_never_counted(with_bucket):
    conn, rows = with_bucket
    only_plain = Q.histogram(conn, {"source": 3})
    assert only_plain.series == [] and only_plain.n_buckets == 0 and only_plain.start is None
    assert only_plain.total == 0
    everything = Q.histogram(conn, None)
    assert everything.total == sum(1 for r in rows if r["ts"] is not None) < len(rows)


def test_empty_database_and_no_match(tmp_path):
    create_case_db(tmp_path / "i.sqlite").close()
    ro = open_ro(tmp_path / "i.sqlite")
    for width in (None, 60_000, 5000):
        h = Q.histogram(ro, None, width)
        assert h.series == [] and h.start is None and h.exact
    ro.close()


def test_bucket_count_is_limited(with_bucket):
    conn, rows = with_bucket
    span = max(r["ts"] for r in rows if r["ts"]) - min(r["ts"] for r in rows if r["ts"])
    assert span / 1000 > Q.MAX_BUCKETS  # a 1 s request over this window would exceed the limit
    h = Q.histogram(conn, {"text": {"substr": "message"}}, 1000)
    assert h.n_buckets <= Q.MAX_BUCKETS and h.bucket_ms > 1000
    assert as_dict(h) == dense(h, rows, {"text": {"substr": "message"}}, "sev")
    auto = Q.histogram(conn, None)
    assert auto.n_buckets <= Q.TARGET_BUCKETS and auto.bucket_ms in Q.BUCKET_LADDER_MS


def test_pick_bucket_ms():
    assert Q.pick_bucket_ms(10_000, None, 1000) == 1000
    assert Q.pick_bucket_ms(10_000, None, 60_000) == 60_000
    assert Q.pick_bucket_ms(86_400_000, None, 60_000) == 300_000
    assert Q.pick_bucket_ms(10**12, None, 1000) == Q.BUCKET_LADDER_MS[-1]
    assert Q.pick_bucket_ms(3_600_000, 90_000, 1000) == 90_000
    assert Q.pick_bucket_ms(10 * 86_400_000, 1000, 1000) * Q.MAX_BUCKETS >= 10 * 86_400_000


def test_many_templates_are_merged_into_other(tmp_path):
    path = tmp_path / "i.sqlite"
    rows = entry_rows()
    rows = [r for r in rows if r["ts"] is not None]
    for k, r in enumerate(rows):
        r["tpl_id"] = k % 30
    build_case(path, rows)
    fill_bucket(path)
    ro = open_ro(path)
    h = Q.histogram(ro, None, None, "tpl")
    assert len(h.series) == Q.MAX_SERIES and h.series[-1]["key"] is None
    assert h.total == len(rows) and sum(sum(s["counts"]) for s in h.series) == len(rows)
    totals = [sum(s["counts"]) for s in h.series[:-1]]
    assert totals == sorted(totals, reverse=True)
    ro.close()


def test_invalid_arguments(with_bucket):
    conn, _ = with_bucket
    for split in ("level", "", None, "sev; DROP TABLE entry"):
        with pytest.raises(Q.HistogramError):
            Q.histogram(conn, None, None, split)
    for width in (0, 999, -5, True, "60000", 1.5, Q.MAX_BUCKET_MS + 1):
        with pytest.raises(Q.HistogramError):
            Q.histogram(conn, None, width)
    with pytest.raises(FilterError):
        Q.histogram(conn, {"nope": 1})


@pytest.fixture
def instant_timeout(monkeypatch):
    monkeypatch.setattr(Q, "PROGRESS_OPS", 1)


def test_entry_timeout_falls_back_to_bucket_estimate(with_bucket, instant_timeout):
    conn, rows = with_bucket
    t0 = 1_760_000_000_000
    tree = {"ts": {"ge": t0, "le": t0 + 20_000_000}}
    h = Q.histogram(conn, tree, None, timeout_s=0, bucket_timeout_s=None)
    assert (h.method, h.exact) == ("bucket_estimate", False)
    assert h.bucket_ms % 60_000 == 0
    assert h.total >= sum(
        1 for r in rows if r["ts"] is not None and py_match(tree, r)
    )  # minute-rounded superset


def test_entry_timeout_without_bucket_samples_then_raises(no_bucket, instant_timeout):
    conn, rows = no_bucket
    h = Q.histogram(conn, None, None, timeout_s=0, estimate_timeout_s=None)
    assert (h.method, h.exact) == ("sample", False)
    assert 0 < h.total <= 2 * len(rows)
    with pytest.raises(Q.QueryTimeout):
        Q.histogram(conn, None, None, timeout_s=0, estimate_timeout_s=0)


# --- API -------------------------------------------------------------------------------------------


@pytest.fixture
def server(tmp_path):
    srv = DmslogServer(0, data_dir=tmp_path / "data")
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    thread.join(timeout=5)


@pytest.fixture
def client(server):
    return Client(server)


@pytest.fixture
def case(client, tmp_path):
    zip_path = mf.build_ers_zip(tmp_path / "z")
    assert client.post("/api/cases", {"name": "demo"})[0] == 201
    assert client.post("/api/cases/demo/collections", {"path": str(zip_path), "scope": "default"})[0] == 202
    client.wait_done("demo")
    return "demo"


def test_api_histogram_totals_and_shape(client, case):
    status, body, _ = client.post(
        "/api/histogram", {"case": case, "filter": None, "bucket_ms": None, "split": "sev"}
    )
    assert status == 200
    assert set(body) == {"bucket_ms", "start", "n_buckets", "split", "series", "total", "exact", "method"}
    assert body["split"] == "sev" and body["exact"] is True
    _, count, _ = client.post("/api/entries/count", {"case": case, "filter": {"ts": {"ge": 0}}})
    assert body["total"] == count["count"]
    assert all(len(s["counts"]) == body["n_buckets"] for s in body["series"])
    status, by_source, _ = client.post("/api/histogram", {"case": case, "split": "source"})
    assert status == 200 and by_source["total"] == body["total"] and by_source["split"] == "source"
    status, filtered, _ = client.post(
        "/api/histogram", {"case": "DEMO", "filter": {"sev": {"le": 2}}, "bucket_ms": 60000}
    )
    assert status == 200 and filtered["total"] <= body["total"]


def test_api_histogram_errors(client, case, monkeypatch):
    def post(body):
        return client.post("/api/histogram", body)

    assert post({"case": "nope"})[0] == 404
    assert post({"case": "../x"})[1]["error"] == "invalid_case_id"
    assert post({"filter": None})[0] == 400  # case missing
    status, body, _ = post({"case": case, "split": "bogus"})
    assert status == 400 and body["error"] == "bad_split"
    status, body, _ = post({"case": case, "bucket_ms": 5})
    assert status == 400 and body["error"] == "bad_bucket_ms"
    assert post({"case": case, "bucket_ms": "60000"})[0] == 400
    assert post({"case": case, "filter": [1]})[1]["error"] == "invalid_field"
    status, body, _ = post({"case": case, "filter": {"zzz": 1}})
    assert status == 400 and body["error"] == "unknown_operator"
    status, body, _ = post({"case": case, "filter": {"text": {"substr": "x"}}})
    assert status == 400 and body["error"] == "substr_needs_scope"
    status, body, _ = client.request("POST", "/api/histogram", raw=b"x" * (300 * 1024))
    assert status == 413 and body["error"] == "body_too_large"
    assert client.get("/api/histogram")[0] == 405

    def boom(*_a, **_k):
        raise Q.QueryTimeout("slow")

    monkeypatch.setattr(Q, "histogram", boom)
    status, body, _ = post({"case": case})
    assert status == 408 and body["error"] == "query_timeout"
