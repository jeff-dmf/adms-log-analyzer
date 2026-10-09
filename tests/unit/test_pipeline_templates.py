"""Template engine wired into the ingest: classification, keys, fields, FTS, unknown signatures, resume."""

import json

import pytest
import test_pipeline as tp

from dmslog.ingest.pipeline import IngestOptions, run_ingest
from dmslog.parse.signature import signature
from dmslog.parse.templates import Catalog, load_texts
from dmslog.store.writer import FLAG_HAS_PAYLOAD, FLAG_REF_SENSITIVE

mf = tp.mf

TOML = r"""
schema = 1
family = "AWFM"

[[template]]
code = "TST-001"
class = "end"
title = "Item done"
regex = '''^\[Alpha\] item (?P<num>\d+) done$'''
fields = { num = "int" }
keys = [{ field = "num", type = "gid" }]

[[template]]
code = "TST-002"
class = "lifecycle"
sev = 5
fts = false
text = "[Beta] noise"

[[template]]
code = "TST-003"
class = "req_resp"
sev_field = "kind"
sev_map = { Boom = 2 }
regex = '''^\[Gamma\] (?P<kind>Boom|Calm) ref=(?P<ref>\d+)$'''
keys = ["ref"]
refs_sensitive = true
payload = { fmt = "xml", start = "match_end", role = "request" }

[[template]]
code = "TST-004"
class = "statistics"
regex = '''^\[Stats\] table$'''
cont = { parser = "kv_colon", max_items = 8 }
"""


def make_catalog(text: str = TOML) -> Catalog:
    return load_texts({"awfm.toml": text})


def make_log(n: int = 60) -> bytes:
    lines = []
    for i in range(n):
        t = i * 10
        if i % 5 == 0:
            lines.append(mf.header(t, "INFO", f"[Alpha] item {i} done"))
        elif i % 5 == 1:
            lines.append(mf.header(t, "INFO", "[Beta] noise"))
        elif i % 5 == 2:
            kind = "Boom" if i % 2 == 0 else "Calm"
            lines.append(mf.header(t, "INFO", f"[Gamma] {kind} ref={i}"))
            lines.append("<Root>")
        elif i % 5 == 3:
            lines.append(mf.header(t, "INFO", "[Stats] table"))
            lines.append("left: 1")
            lines.append("right: 2")
        else:
            lines.append(mf.header(t, "INFO", f"[Delta] mystery {i} happened"))
    return mf.render(lines)


@pytest.fixture
def db(tmp_path):
    conn = tp.fresh_db(tmp_path, "c")
    yield conn
    conn.close()


def ingest(conn, path, catalog=None, **kw):
    return run_ingest(conn, path, IngestOptions(catalog=make_catalog() if catalog is None else catalog, **kw))


def folder(tmp_path, data=None):
    return tp.write_folder(tmp_path / "Log", {tp.WFM: data if data is not None else make_log()})


def tpl_ids(conn):
    return dict(conn.execute("SELECT code, id FROM template"))


def consistent(conn):
    conn.execute("INSERT INTO entry_fts(entry_fts) VALUES('integrity-check')")
    indexed = conn.execute(
        "SELECT count(*) FROM entry WHERE tpl_id NOT IN (SELECT id FROM template WHERE fts = 0)"
    ).fetchone()[0]
    assert conn.execute("SELECT count(*) FROM entry_fts_docsize").fetchone()[0] == indexed
    for sid, n in conn.execute("SELECT id, n_entries FROM source WHERE status='ready'").fetchall():
        assert conn.execute("SELECT count(*) FROM entry WHERE source_id=?", (sid,)).fetchone()[0] == n
    # bucket counts per template match the entries
    got = conn.execute("SELECT tpl_id, sum(n) FROM bucket GROUP BY tpl_id ORDER BY 1").fetchall()
    want = conn.execute("SELECT tpl_id, count(*) FROM entry WHERE ts IS NOT NULL GROUP BY tpl_id ORDER BY 1")
    assert got == want.fetchall()
    # unknown_sig matches the unclassified entries
    unknown = {}
    for (msg,) in conn.execute("SELECT msg FROM entry WHERE tpl_id = 0"):
        sig = signature(msg)
        unknown[sig] = unknown.get(sig, 0) + 1
    stored = {sig: n for sig, n in conn.execute("SELECT signature, n FROM unknown_sig")}
    assert stored == unknown
    for sig, first in conn.execute("SELECT signature, first_entry_id FROM unknown_sig"):
        row = conn.execute("SELECT msg, tpl_id FROM entry WHERE id=?", (first,)).fetchone()
        assert row[1] == 0 and signature(row[0]) == sig
        assert first == min(
            i for i, m in conn.execute("SELECT id, msg FROM entry WHERE tpl_id = 0") if signature(m) == sig
        )
    # entry_key rows point to existing entries
    assert conn.execute(
        "SELECT count(*) FROM entry_key WHERE entry_id NOT IN (SELECT id FROM entry)"
    ).fetchone() == (0,)


def test_templates_are_stored_and_records_classified(tmp_path, db):
    ingest(db, folder(tmp_path))
    ids = tpl_ids(db)
    assert set(ids) == {"TST-001", "TST-002", "TST-003", "TST-004"} and 0 not in ids.values()
    row = db.execute("SELECT family, class, sev, title, fts, def_version FROM template WHERE code='TST-001'")
    family, cls, sev, title, fts, version = row.fetchone()
    assert (family, cls, sev, title, fts) == ("AWFM", "end", None, "Item done", 1) and version
    assert db.execute("SELECT fts FROM template WHERE code='TST-002'").fetchone() == (0,)
    counts = dict(
        db.execute("SELECT t.code, count(*) FROM entry e JOIN template t ON t.id=e.tpl_id GROUP BY 1")
    )
    assert counts == {"TST-001": 12, "TST-002": 12, "TST-003": 12, "TST-004": 12}
    assert db.execute("SELECT count(*) FROM entry WHERE tpl_id=0").fetchone() == (12,)
    consistent(db)


def test_fields_keys_flags_and_severity(tmp_path, db):
    ingest(db, folder(tmp_path))
    ids = tpl_ids(db)
    fields, flags, sev = db.execute(
        "SELECT fields, flags, sev FROM entry WHERE tpl_id=? ORDER BY id LIMIT 1", (ids["TST-001"],)
    ).fetchone()
    assert json.loads(fields) == {"num": 0} and sev == 4 and not flags & FLAG_HAS_PAYLOAD
    assert db.execute(
        "SELECT key_type, value FROM entry_key WHERE entry_id = (SELECT min(id) FROM entry WHERE tpl_id=?)",
        (ids["TST-001"],),
    ).fetchall() == [("gid", "0")]
    # sev from sev_map (field), refs_sensitive and payload flags, key from a bare field name
    boom = db.execute(
        "SELECT id, sev, flags, fields FROM entry WHERE tpl_id=? AND msg LIKE '%Boom%' ORDER BY id LIMIT 1",
        (ids["TST-003"],),
    ).fetchone()
    assert boom[1] == 2 and boom[2] & FLAG_REF_SENSITIVE and boom[2] & FLAG_HAS_PAYLOAD
    assert json.loads(boom[3])["kind"] == "Boom"
    assert db.execute("SELECT key_type, value FROM entry_key WHERE entry_id=?", (boom[0],)).fetchall() == [
        ("ref", json.loads(boom[3])["ref"])
    ]
    calm = db.execute("SELECT sev FROM entry WHERE tpl_id=? AND msg LIKE '%Calm%' LIMIT 1", (ids["TST-003"],))
    assert calm.fetchone() == (4,)  # no sev hint: generic severity
    noise = db.execute("SELECT DISTINCT sev FROM entry WHERE tpl_id=?", (ids["TST-002"],)).fetchall()
    assert noise == [(5,)]


def test_cont_parser_output_stays_nested_in_fields(tmp_path, db):
    ingest(db, folder(tmp_path))
    (fields,) = db.execute(
        "SELECT fields FROM entry WHERE tpl_id=? LIMIT 1", (tpl_ids(db)["TST-004"],)
    ).fetchone()
    assert json.loads(fields) == {"kv": {"left": "1", "right": "2"}, "n_items": 2}


def test_fts_false_records_are_not_indexed(tmp_path, db):
    ingest(db, folder(tmp_path))

    def hits(term):
        return db.execute("SELECT count(*) FROM entry_fts WHERE entry_fts MATCH ?", (term,)).fetchone()[0]

    assert hits("Beta") == 0 and hits("noise") == 0
    assert hits("Alpha") == 12 and hits("Delta") == 12
    total = db.execute("SELECT count(*) FROM entry").fetchone()[0]
    assert db.execute("SELECT count(*) FROM entry_fts_docsize").fetchone()[0] == total - 12
    # the substring path still sees them
    assert db.execute("SELECT count(*) FROM entry WHERE msg LIKE '%Beta%'").fetchone() == (12,)


def test_unknown_signatures_are_stored(tmp_path, db):
    ingest(db, folder(tmp_path))
    rows = db.execute("SELECT family, signature, n, first_entry_id FROM unknown_sig").fetchall()
    assert len(rows) == 1
    family, sig, n, first = rows[0]
    assert (family, sig, n) == ("AWFM", "[Delta] mystery <n> happened", 12)
    assert db.execute("SELECT msg FROM entry WHERE id=?", (first,)).fetchone() == (
        "[Delta] mystery 4 happened",
    )


def test_empty_catalog_stores_everything_as_unknown(tmp_path, db):
    result = ingest(db, folder(tmp_path), catalog=Catalog({}))
    assert db.execute("SELECT count(*) FROM template").fetchone() == (0,)
    assert db.execute("SELECT count(DISTINCT tpl_id) FROM entry").fetchone() == (1,)
    assert db.execute("SELECT count(*) FROM entry WHERE tpl_id=0").fetchone() == (result.records,)
    assert db.execute("SELECT count(*) FROM entry_key").fetchone() == (0,)
    assert db.execute("SELECT sum(n) FROM unknown_sig").fetchone() == (result.records,)
    consistent(db)


def test_families_without_templates_use_the_catalog_fallback(tmp_path, db):
    lib = (
        'schema = 1\nfamily = "_common"\n[[template]]\ncode = "CMN-001"\n'
        'class = "other"\ntext = "[Beta] noise"\n'
    )
    cat = load_texts({"_common.toml": lib})
    ingest(db, folder(tmp_path), catalog=cat)
    assert db.execute("SELECT count(*) FROM entry WHERE tpl_id>0").fetchone() == (12,)  # AWFM has no file


def test_reingest_is_idempotent_and_writes_nothing(tmp_path, db):
    root = folder(tmp_path)
    ingest(db, root)
    before = tp.snapshot(db)
    keys = db.execute("SELECT * FROM entry_key ORDER BY 1, 2, 3").fetchall()
    changes = db.total_changes
    second = ingest(db, root)
    assert db.total_changes == changes and second.records == 0
    assert tp.snapshot(db) == before
    assert db.execute("SELECT * FROM entry_key ORDER BY 1, 2, 3").fetchall() == keys


def test_growth_keeps_keys_buckets_and_unknowns_consistent(tmp_path, db):
    root = folder(tmp_path, make_log(40))
    ingest(db, root)
    consistent(db)
    (root / tp.WFM).write_bytes(make_log(100))
    result = ingest(db, root)
    assert [d.action for d in result.details] == ["append"]
    consistent(db)
    clean = tp.fresh_db(tmp_path, "clean")
    try:
        ingest(clean, folder(tmp_path / "other", make_log(100)))
        assert _content(db) == _content(clean)
    finally:
        clean.close()


def _content(conn):
    entries = conn.execute(
        "SELECT t.code, e.sev, e.flags, e.fields, e.msg FROM entry e LEFT JOIN template t ON t.id=e.tpl_id"
        " ORDER BY e.byte_off"
    ).fetchall()
    keys = conn.execute(
        "SELECT k.key_type, k.value, e.byte_off FROM entry_key k JOIN entry e ON e.id=k.entry_id"
        " ORDER BY 3, 1, 2"
    ).fetchall()
    unknown = conn.execute("SELECT family, signature, n FROM unknown_sig ORDER BY 1, 2").fetchall()
    return entries, keys, unknown


@pytest.mark.parametrize("fail_at_chunk", [3, 6, 9])
def test_resume_after_interruption_keeps_keys_and_buckets(tmp_path, monkeypatch, fail_at_chunk):
    path = folder(tmp_path, make_log(400))
    options = IngestOptions(catalog=make_catalog(), chunk_size=1000, batch_bytes=2500)
    conn = tp.fresh_db(tmp_path, "interrupted")
    try:
        with monkeypatch.context() as patch:
            tp.flaky_reader(patch, fail_at_chunk)
            with pytest.raises(tp.Interrupted):
                run_ingest(conn, path, options)
        consistent(conn)  # whatever was committed is coherent
        result = run_ingest(conn, path, options)
        assert [d.action for d in result.details] == ["resume"]
        consistent(conn)
        clean = tp.fresh_db(tmp_path, "clean")
        try:
            run_ingest(clean, path, IngestOptions(catalog=make_catalog()))
            assert _content(conn) == _content(clean)
        finally:
            clean.close()
    finally:
        conn.close()


def test_reindex_removes_keys_buckets_and_unknowns_of_the_old_content(tmp_path, db):
    first = tp.write_ers(tmp_path / "z1", {tp.WFM: make_log(50)})
    ingest(db, first)
    other = tp.write_ers(tmp_path / "z2", {tp.WFM: make_log(30)})
    ingest(db, other)  # same ERS identity, different CRC: reindexed from scratch
    consistent(db)
    assert db.execute("SELECT count(*) FROM entry").fetchone()[0] < 50


def test_first_entry_id_is_repaired_when_its_record_is_removed(tmp_path, db):
    lines = [mf.header(i * 10, "INFO", f"[Delta] mystery {i} happened") for i in range(6)]
    root = tp.write_folder(
        tmp_path / "Log",
        {tp.WFM: mf.render(lines), f"{tp.LOGDIR}/WfmAdapter_2222.log": mf.render(lines[:3])},
    )
    ingest(db, root)
    consistent(db)
    (root / tp.WFM).write_bytes(mf.render(lines[:2] + [mf.header(99, "INFO", "[Delta] mystery 7 happened")]))
    ingest(db, root)
    consistent(db)


def test_changed_fts_flag_rebuilds_the_index(tmp_path, db):
    root = folder(tmp_path)
    ingest(db, root)
    ingest(db, root, catalog=make_catalog(TOML.replace("fts = false", "fts = true")))
    consistent(db)
    assert db.execute("SELECT count(*) FROM entry_fts WHERE entry_fts MATCH 'Beta'").fetchone() == (12,)
    assert db.execute("SELECT fts FROM template WHERE code='TST-002'").fetchone() == (1,)


def test_packaged_catalog_is_used_by_default(tmp_path, db):
    result = run_ingest(db, folder(tmp_path))
    assert result.records > 0
    consistent(db)
