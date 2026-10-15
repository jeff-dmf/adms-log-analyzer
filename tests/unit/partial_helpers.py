"""Helpers for the partial open tests (ROADMAP 9.9): the whole content of a case, compared without ids."""

from __future__ import annotations

import json
import sqlite3

# locator of a record: (collection path, member, line); ids differ between a partial and a full indexing
_LOC = (
    "(SELECT c.path || '|' || s.member || '|' || e.line_no FROM entry e JOIN source s ON s.id = e.source_id"
    " JOIN collection c ON c.id = s.collection_id WHERE e.id = {})"
)
# identity of an event: kind, name, stream identity and the locator of its first record
_EVENT = (
    "(SELECT ev.kind || '|' || coalesce(ev.name, '') || '|' || st.family || '|' || coalesce(st.pid, '')"
    " || '|' || st.sensitive || '|' || ev.ts_start || '|' || coalesce("
    + _LOC.format("ev.start_entry_id")
    + ", '')"
    " FROM event ev JOIN stream st ON st.id = ev.stream_id WHERE ev.id = {})"
)


def loc(expr: str) -> str:
    return _LOC.format(expr)


def _events(conn: sqlite3.Connection) -> list[tuple]:
    """Events with the record ids inside ``detail`` (``*_entry_id``) replaced by locators."""
    rows = conn.execute(
        "SELECT ev.kind, ev.name, ev.key, ev.tid, ev.ts_start, ev.ts_end, ev.duration_ms, ev.status, ev.sev,"
        f" ev.n_entries, ev.detail, st.family, st.pid, st.sensitive, {loc('ev.start_entry_id')},"
        f" {loc('ev.end_entry_id')} FROM event ev JOIN stream st ON st.id = ev.stream_id"
    ).fetchall()
    out = []
    for row in rows:
        detail = json.loads(row[10]) if row[10] else None
        if isinstance(detail, dict):
            for name, value in list(detail.items()):
                if name.endswith("entry_id") and isinstance(value, int):
                    detail[name] = conn.execute(f"SELECT {loc('?')}", (value,)).fetchone()[0]
        out.append((*row[:10], json.dumps(detail, sort_keys=True), *row[11:]))
    return out


def content(conn: sqlite3.Connection) -> dict:
    """Everything a full indexing produces, by locator (never by id)."""
    q = conn.execute
    return {
        "entries": q(
            "SELECT c.path, s.member, e.line_no, e.byte_off, e.nlines, e.ts, e.pid, e.tid, e.level, e.sev,"
            " e.flags, e.fields, e.msg, t.code, b.cont, " + _EVENT.format("e.event_id") + " FROM entry e"
            " JOIN source s ON s.id = e.source_id JOIN collection c ON c.id = s.collection_id"
            " LEFT JOIN template t ON t.id = e.tpl_id LEFT JOIN entry_body b ON b.entry_id = e.id"
            " ORDER BY 1, 2, 3"
        ).fetchall(),
        "keys": sorted(q(f"SELECT key_type, value, origin, {loc('entry_id')} FROM entry_key").fetchall()),
        "links": sorted(
            q(f"SELECT kind, confidence, {loc('a_entry_id')}, {loc('b_entry_id')} FROM link").fetchall()
        ),
        "events": sorted(_events(conn), key=repr),
        "payloads": sorted(
            q(
                f"SELECT {loc('p.entry_id')}, {_EVENT.format('p.event_id')}, p.event_conf,"
                f" {loc('p.ref_entry_id')},"
                " (SELECT "
                + loc("x.entry_id")
                + " || '@' || x.char_off FROM payload x WHERE x.id = p.pair_id),"
                " p.role, p.direction, p.peer, p.operation, p.msg_id, p.corr_id, p.fmt, p.root, p.char_off,"
                " p.char_len, p.well_formed, p.truncated, p.reason FROM payload p"
            ).fetchall(),
            key=repr,
        ),
        "stats": sorted(
            q(
                "SELECT st.family, st.pid, st.sensitive, x.ts, x.side, x.method, x.calls, x.req_bytes,"
                " x.resp_bytes, x.d_calls, x.d_req_bytes, x.d_resp_bytes FROM stat_sample x"
                " JOIN stream st ON st.id = x.stream_id"
            ).fetchall(),
            key=repr,
        ),
        "config_observed": sorted(
            q(
                f"SELECT st.family, st.pid, x.name, x.value, x.is_default, {loc('x.entry_id')}"
                " FROM config_observed x JOIN stream st ON st.id = x.stream_id"
            ).fetchall(),
            key=repr,
        ),
        "buckets": sorted(
            q(
                "SELECT s.member, b.minute, b.sev, t.code, b.n FROM bucket b"
                " JOIN source s ON s.id = b.source_id LEFT JOIN template t ON t.id = b.tpl_id"
            ).fetchall(),
            key=repr,
        ),
        "bucket_sev": sorted(
            q(
                "SELECT s.member, b.minute, b.sev, b.n FROM bucket_sev b JOIN source s ON s.id = b.source_id"
            ).fetchall()
        ),
        "unknown": sorted(
            q(f"SELECT family, signature, n, {loc('first_entry_id')} FROM unknown_sig").fetchall()
        ),
        "sources": q(
            "SELECT member, status, error, envelope, size, indexed_bytes, head_hash, tail_hash, n_entries,"
            " first_ts, last_ts, last_line FROM source ORDER BY member"
        ).fetchall(),
        "streams": sorted(q("SELECT family, process, pid, sensitive FROM stream").fetchall(), key=repr),
        "fts": q("SELECT count(*) FROM entry_fts_docsize").fetchone()[0],
    }


def assert_same(actual: dict, expected: dict) -> None:
    """Compare table by table (a failing table is named in the assertion)."""
    assert actual.keys() == expected.keys()
    for name in expected:
        assert actual[name] == expected[name], name


def assert_ids_follow_chains(conn: sqlite3.Connection) -> None:
    """Inside a source ids follow the file order, and along a rotation chain they grow (oldest file first)."""
    from dmslog.derive.engine import _id_ranges, _sources, chain_of

    for (stream_id,) in conn.execute("SELECT id FROM stream").fetchall():
        sources = _sources(conn, stream_id)
        chains: dict[str, list] = {}
        for src in sources:
            chains.setdefault(chain_of(src[7]), []).append(src)
        for chain in chains.values():
            _ranges, monotone = _id_ranges(conn, chain)
            assert monotone, (stream_id, [s[7] for s in chain])
    bad = conn.execute(
        "SELECT count(*) FROM entry a JOIN entry b ON b.source_id = a.source_id AND b.id > a.id"
        " AND b.byte_off <= a.byte_off"
    ).fetchone()[0]
    assert bad == 0


def assert_integrity(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO entry_fts(entry_fts) VALUES('integrity-check')")
    q = conn.execute
    assert (
        q("SELECT count(*) FROM entry_fts_docsize").fetchone()[0]
        == q("SELECT count(*) FROM entry_text").fetchone()[0]
    )
    assert (
        q("SELECT coalesce(sum(n), 0) FROM bucket").fetchone()[0]
        == q("SELECT count(ts) FROM entry").fetchone()[0]
    )
    rows = q(
        "SELECT s.id, s.last_line, (SELECT e.line_no + e.nlines - 1 FROM entry e WHERE e.source_id = s.id"
        " ORDER BY e.id DESC LIMIT 1) FROM source s"
    ).fetchall()
    assert [(sid, stored) for sid, stored, _ in rows] == [(sid, ref) for sid, _, ref in rows]
