"""A case groups collections (U21): two servers, each with an Enterprise + Sensitive ERS.

ARQUITETURA 5 (model), 9.1 (add rules), 10 (routes and filters).
"""

from __future__ import annotations

import csv
import io
import json

import multi_helpers as mh
import pytest

from dmslog.derive import correlate, engine
from dmslog.derive.engine import group_epoch
from dmslog.ingest.pipeline import run_ingest
from dmslog.report.export import ExportOptions, Selection, build_report
from dmslog.store import bookmarks as bm
from dmslog.store import collections as cs
from dmslog.store import create_case_db, events, keys, query
from dmslog.store.filters import compile_bucket_filter
from dmslog.store.writer import IndexWriter, id_floor


@pytest.fixture
def zips(tmp_path):
    return mh.build_all(tmp_path / "zips")


@pytest.fixture
def db(tmp_path):
    conn = create_case_db(tmp_path / "case.sqlite")
    yield conn
    conn.close()


def ingest(db, zips, *order):
    for server, kind in order:
        run_ingest(db, zips[(server, kind)])


BOTH = (("A", "enterprise"), ("A", "sensitive"), ("B", "enterprise"), ("B", "sensitive"))


def test_second_server_becomes_a_second_group_and_links_never_cross(db, zips):
    ingest(db, zips, *BOTH)
    a, b = mh.group_of(db, "A"), mh.group_of(db, "B")
    assert a != b
    links = mh.links_by_group(db)
    assert links == [(a, a, "normal_sensitive")] * mh.PAIRS + [(b, b, "normal_sensitive")] * mh.PAIRS
    groups = cs.list_collections(db)
    assert [(g["tag"], g["guid"], g["variants"], g["missing"], g["system"]) for g in groups] == [
        ("C1", mh.SERVERS["A"][0], ["enterprise", "sensitive"], None, "zonea"),
        ("C2", mh.SERVERS["B"][0], ["enterprise", "sensitive"], None, "zoneb"),
    ]
    assert [g["records"] for g in groups] == [mh.RECORDS, mh.RECORDS]
    assert all(g["status"] == "ready" and len(g["members"]) == 2 for g in groups)
    assert groups[0]["first_ts"] == groups[1]["first_ts"]  # same clock readings, different servers
    assert cs.ordering(groups) == "approximate" and cs.group_tags(db) == {a: "C1", b: "C2"}
    mh.assert_no_orphans(db)


@pytest.mark.parametrize("order", [("sensitive", "enterprise"), ("enterprise", "sensitive")])
def test_the_pair_of_the_second_server_in_any_order(db, zips, order):
    ingest(db, zips, ("A", "enterprise"), ("A", "sensitive"), *(("B", k) for k in order))
    b = mh.group_of(db, "B")
    assert db.execute("SELECT count(*) FROM collection WHERE ers_group = ?", (b,)).fetchone()[0] == 2
    assert mh.links_by_group(db).count((b, b, "normal_sensitive")) == mh.PAIRS
    lone = cs.list_collections(db)
    assert all(g["missing"] is None for g in lone)


def test_a_lone_sensitive_half_is_listed_as_missing_its_enterprise(db, zips):
    ingest(db, zips, ("A", "enterprise"), ("A", "sensitive"), ("B", "sensitive"))
    second = cs.list_collections(db)[1]
    assert second["variants"] == ["sensitive"] and second["missing"] == "enterprise"


def test_a_new_collection_does_not_recompute_the_first(db, zips, monkeypatch):
    ingest(db, zips, ("A", "enterprise"), ("A", "sensitive"))
    a = mh.group_of(db, "A")
    a_streams = {r[0] for r in db.execute("SELECT id FROM stream WHERE collection_group = ?", (a,))}
    before = mh.group_snapshot(db, a)
    derived, correlated = [], []
    real_derive, real_correlate = engine.derive_stream, correlate.correlate_group

    def spy_derive(conn, stream_id, *args, **kwargs):
        result = real_derive(conn, stream_id, *args, **kwargs)
        if not result.skipped:
            derived.append(stream_id)
        return result

    def spy_correlate(conn, group, *args, **kwargs):
        if real_correlate(conn, group, *args, **kwargs):
            correlated.append(group)
            return True
        return False

    monkeypatch.setattr(engine, "derive_stream", spy_derive)
    monkeypatch.setattr(correlate, "correlate_group", spy_correlate)
    ingest(db, zips, ("B", "enterprise"), ("B", "sensitive"))
    assert derived and not a_streams & set(derived)  # only B's streams were derived
    assert correlated == [mh.group_of(db, "B")]  # only B was correlated
    assert mh.group_snapshot(db, a) == before  # same rows, ids, links and signatures
    changes = db.total_changes
    for key in zips:  # everything again: nothing changes anywhere
        run_ingest(db, zips[key])
    assert db.total_changes == changes


def test_removing_a_collection_leaves_the_other_untouched_and_nothing_behind(db, zips):
    ingest(db, zips, *BOTH)
    a, b = mh.group_of(db, "A"), mh.group_of(db, "B")
    entry = db.execute(
        "SELECT e.id FROM entry e JOIN source s ON s.id = e.source_id"
        " JOIN collection c ON c.id = s.collection_id"
        " WHERE c.ers_group = ? ORDER BY e.id LIMIT 1",
        (b,),
    ).fetchone()[0]
    bm.create_bookmark(db, entry, color="red", note="on B")
    before = mh.group_snapshot(db, a)
    tops = {
        t: db.execute(f"SELECT max(id) FROM {t}").fetchone()[0] for t in ("collection", "source", "entry")
    }
    removed = IndexWriter(db).remove_group(b)
    assert (removed.collections, removed.records, removed.bookmarks) == (2, mh.RECORDS, 1)
    assert mh.group_snapshot(db, a) == before
    assert [g["group"] for g in cs.list_collections(db)] == [a]
    assert db.execute("SELECT count(*) FROM meta WHERE key LIKE ?", (f"%.{b}",)).fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM bookmark").fetchone()[0] == 0
    mh.assert_no_orphans(db)
    changes = db.total_changes
    ingest(db, zips, ("A", "enterprise"), ("A", "sensitive"))
    assert db.total_changes == changes  # A is not derived nor correlated again
    assert IndexWriter(db).remove_group(b) is None
    # ids of the removed group are never given again (saved views and open screens name them)
    for table, top in tops.items():
        assert id_floor(db, table) == top
    ingest(db, zips, ("B", "enterprise"))
    assert (
        db.execute("SELECT min(id) FROM collection WHERE ers_guid = ?", (mh.SERVERS["B"][0],)).fetchone()[0]
        > tops["collection"]
    )
    new_b = mh.group_of(db, "B")
    assert (
        db.execute(
            "SELECT min(e.id) FROM entry e JOIN source s ON s.id = e.source_id"
            " JOIN collection c ON c.id = s.collection_id WHERE c.ers_group = ?",
            (new_b,),
        ).fetchone()[0]
        > tops["entry"]
    )
    mh.assert_no_orphans(db)


def test_removal_is_one_transaction(db, zips, monkeypatch):
    ingest(db, zips, *BOTH)
    b = mh.group_of(db, "B")
    counts = {
        t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("entry", "link", "collection")
    }
    writer = IndexWriter(db)

    def boom(*_args, **_kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(writer, "commit", boom)
    with pytest.raises(RuntimeError):
        writer.remove_group(b)
    writer.rollback()
    assert {t: db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in counts} == counts


def test_epoch_of_a_group_is_independent_and_legacy_signatures_survive(db):
    assert group_epoch(db, "7", 3) == "7"  # a case indexed before U21: same signature as before
    db.execute("INSERT INTO meta(key, value) VALUES ('entries_epoch.3', '2')")
    assert group_epoch(db, "7", 3) == "7|2" and group_epoch(db, None, 4) is None


def test_filter_by_collection_in_pages_counts_buckets_events_and_keys(db, zips):
    ingest(db, zips, *BOTH)
    a, b = mh.group_of(db, "A"), mh.group_of(db, "B")
    only_b = {"collection": [b]}
    assert query.count(db, only_b).n == mh.RECORDS
    assert query.count(db, {"collection": [a, b]}).n == 2 * mh.RECORDS
    assert compile_bucket_filter(only_b) is not None  # the timeline keeps its fast path
    page = query.page(db, only_b, None, "fwd", 100, ["collection"])
    assert {r["collection"] for r in page.rows} == {b}
    every = query.page(db, None, None, "fwd", 100, ["collection"])
    assert {r["collection"] for r in every.rows} == {a, b}
    # the key search spans every collection, and keeps one when asked
    shared = mh.plate(3)
    records, _ = keys.records_with_key(db, "vehicle", shared)
    assert sorted(r["collection"] for r in records) == [a, b]
    records, _ = keys.records_with_key(db, "vehicle", shared, collection=b)
    assert [r["collection"] for r in records] == [b]
    all_events = events.list_events(db, events.EventFilter(), 1000)[0]
    b_events = events.list_events(db, events.EventFilter(collection=b), 1000)[0]
    b_streams = {r[0] for r in db.execute("SELECT id FROM stream WHERE collection_group = ?", (b,))}
    assert b_events and {e["stream_id"] for e in b_events} <= b_streams
    assert len(b_events) < len(all_events)


def test_bookmarks_find_their_collection_on_import(db, zips, tmp_path):
    ingest(db, zips, *BOTH)
    b = mh.group_of(db, "B")
    sens = db.execute(
        "SELECT e.id FROM entry e JOIN source s ON s.id = e.source_id"
        " JOIN collection c ON c.id = s.collection_id"
        " WHERE c.ers_group = ? AND c.ers_variant = 'sensitive' ORDER BY e.id LIMIT 1",
        (b,),
    ).fetchone()[0]
    bm.create_bookmark(db, sens, color="red", note="sensitive half of B")
    data = bm.export_bookmarks(db)
    other = create_case_db(tmp_path / "other" / "case.sqlite")
    try:
        ingest(other, zips, ("B", "enterprise"), ("A", "enterprise"), ("A", "sensitive"), ("B", "sensitive"))
        assert bm.import_bookmarks(other, data)["imported"] == 1
        row = other.execute(
            "SELECT c.ers_variant, c.ers_guid, b.entry_id IS NOT NULL FROM bookmark b"
            " JOIN collection c ON c.id = b.collection_id"
        ).fetchone()
        assert row == ("sensitive", mh.SERVERS["B"][0], 1)
    finally:
        other.close()


def test_report_names_the_collection_of_every_record(db, zips):
    ingest(db, zips, *BOTH)
    ids = [r[0] for r in db.execute("SELECT min(id) FROM entry GROUP BY source_id ORDER BY 1")]
    result = build_report(db, Selection(ids=tuple(ids)), ExportOptions(context=0))
    text = result.parts[0].text
    assert "Sources: 2 collections" in text
    assert f"C1: ERS 2030-01-15 10:30:00 (zonea) - Enterprise+Sensitive - GUID {mh.SERVERS['A'][0]}" in text
    assert "C2: ERS 2030-01-15 11:30:00 (zoneb)" in text
    assert "order across collections is approximate" in text
    heads = [line for line in text.splitlines() if line.startswith("[R")]
    assert len(heads) == 4 and {h.split()[3] for h in heads} == {"C1", "C2"}


def test_view_export_has_a_collection_column(db, zips):
    from dmslog.report import viewexport as ve

    ingest(db, zips, *BOTH)
    b = mh.group_of(db, "B")
    req = ve.parse_request({"kind": "view_csv", "columns": ["source", "collection"]})
    lines = "".join(ve.stream_view(db, req)).splitlines()
    assert lines[0] == "Record,Source,Collection"
    assert {line.rsplit(",", 1)[1] for line in lines[1:]} == {"C1", "C2"}
    req = ve.parse_request(
        {"kind": "values", "column": "collection", "unique": True, "filter": {"collection": [b]}}
    )
    assert "".join(ve.stream_view(db, req)) == "C2"


def test_events_csv_names_the_collection_of_each_event(db, zips):
    from dmslog.report import viewexport as ve

    ingest(db, zips, ("A", "enterprise"), ("A", "sensitive"))
    one = list(
        csv.DictReader(io.StringIO("".join(ve.stream_view(db, ve.parse_request({"kind": "events_csv"})))))
    )
    assert one and {r["Collection"] for r in one} == {"C1"}
    ingest(db, zips, ("B", "enterprise"), ("B", "sensitive"))
    both = list(
        csv.DictReader(io.StringIO("".join(ve.stream_view(db, ve.parse_request({"kind": "events_csv"})))))
    )
    assert {r["Collection"] for r in both} == {"C1", "C2"} and len(both) > len(one)
    assert list(both[0]).index("Collection") == list(both[0]).index("Source") + 1


@pytest.mark.parametrize(
    ("flt", "method"),
    [
        (None, "bucket"),
        ({"level": {"ge": 1}}, "exact"),
        ({"level": {"ge": 1}}, "sample"),
    ],
)
def test_distinct_groups_by_collection_with_tags_and_leaves(db, zips, monkeypatch, flt, method):
    from dmslog.store import distinct as D

    if method == "sample":  # the exact grouping times out; the level filter has no bucket estimate

        def too_slow(*args, **kwargs):
            raise query.QueryTimeout

        monkeypatch.setattr(D, "_entry_group_sql", too_slow)
    ingest(db, zips, *BOTH)
    a, b = mh.group_of(db, "A"), mh.group_of(db, "B")
    result = D.distinct(db, flt, "collection", span=True)
    assert result.method == method
    by_group = {v["value"]: v for v in result.values}
    assert set(by_group) == {a, b} and by_group[b]["label"] == "C2"
    if method != "sample":
        assert result.exact and by_group[a]["count"] == by_group[b]["count"] == mh.RECORDS
    assert by_group[b]["leaf"] == {"collection": b}
    assert query.count(db, by_group[b]["leaf"]).n == mh.RECORDS


def test_report_of_a_single_collection_is_unchanged(db, zips):
    ingest(db, zips, ("A", "enterprise"), ("A", "sensitive"))
    text = build_report(db, Selection(ids=(1,)), ExportOptions(context=0)).parts[0].text
    assert "Source: ERS 2030-01-15 10:30:00" in text and " C1 " not in text and "collections" not in text


def test_a_folder_joins_a_case_with_an_ers(db, zips, tmp_path):
    ingest(db, zips, ("A", "enterprise"))
    root = tmp_path / "Log"
    for name, data in mh.capture_files("B", "enterprise").items():
        target = root / name.split("/Log/", 1)[1]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    run_ingest(db, root)
    groups = cs.list_collections(db)
    assert [(g["kind"], g["missing"]) for g in groups] == [("ers", "sensitive"), ("folder", None)]
    assert json.dumps(groups)  # plain JSON


def test_a_preset_never_names_a_collection(tmp_path):
    from dmslog.store import presets

    with pytest.raises(presets.PresetError) as err:
        presets.create(tmp_path, {"name": "a", "filter": {"collection": [1]}})
    assert err.value.code == "case_specific"
