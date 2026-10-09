"""Payload queries over a synthetic case (rows inserted directly; every value is fictitious)."""

from __future__ import annotations

import pytest

from dmslog.store import create_case_db, payloads
from dmslog.store.events import Cursor
from dmslog.store.payloads import PayloadFilter

T0 = 1_900_000_000_000
PREFIX = "Message content: "
SCRIPT_XML = "<Doc><note>&lt;b&gt; &amp; <script>alert(1)</script></note></Doc>"
BIG_ROWS = 6000  # ~170k characters


def big_xml() -> str:
    rows = [f'  <Row n="{i}">value-{i:05d}-xxxxxxxxxxxxxxxxxxxx</Row>' for i in range(BIG_ROWS)]
    return "<Table>\n" + "\n".join(rows) + "\n</Table>"


def add_entry(conn, entry_id, source_id, ts, msg, cont=None, tid=7):
    conn.execute(
        "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, tid, sev, tpl_id, msg, flags)"
        " VALUES (?,?,?,?,?,?,?,4,0,?,?)",
        (entry_id, source_id, entry_id, entry_id, 1, ts, tid, msg, 64 if cont else 0),
    )
    if cont is not None:
        conn.execute("INSERT INTO entry_body(entry_id, cont) VALUES (?,?)", (entry_id, cont))


def add_payload(conn, payload_id, entry_id, xml, *, prefix=PREFIX, **kw):
    """Insert a payload row whose span is ``xml`` placed after ``prefix`` in the record's msg."""
    row = {
        "event_id": None, "event_conf": None, "ref_entry_id": None, "pair_id": None, "role": "request",
        "direction": "in", "peer": "WFM", "operation": "SendWorks", "msg_id": None, "corr_id": None,
        "fmt": "xml", "root": "Doc", "well_formed": 1, "truncated": 0, "reason": None,
    }  # fmt: skip
    row.update(kw)
    cols = ", ".join(row)
    conn.execute(
        f"INSERT INTO payload(id, entry_id, char_off, char_len, {cols})"  # noqa: S608
        f" VALUES (?, ?, ?, ?, {', '.join('?' * len(row))})",
        (payload_id, entry_id, len(prefix), len(xml), *row.values()),
    )


def seed(conn):
    """Layout (ids are fixed so tests can name them).

    entries: 1 (normal announcer), 10..19 (sensitive, ts tie at T0), 20 (T0+1000, big payload),
    21 (no ts), 22 (multi-line xml in cont, T0+2000), 30 (normal, other source).
    payloads: 1 request / 2 response (paired, linked to event 1), 3..10 request at the tie ts (orphans
    with reasons), 11 big json-ish, 12 script literal (no ts), 13 multi-line.
    """
    conn.execute("INSERT INTO collection(id, kind, path, added_at) VALUES (1, 'ers_zip', 'x.zip', 'now')")
    conn.execute(
        "INSERT INTO stream(id, collection_group, family, process, pid, sensitive) VALUES"
        " (1, 1, 'AWFM', 'WfmAdapter', 100, 0), (2, 1, 'AWFM', 'WfmAdapter', 100, 1)"
    )
    conn.execute(
        "INSERT INTO source(id, collection_id, stream_id, member, file_name, envelope) VALUES"
        " (1, 1, 1, 'SVC/Wfm.log', 'Wfm.log', 'adms'), (2, 1, 2, 'SVC/Wfm.sensitive.log', 'W.log', 'adms')"
    )
    conn.execute(
        "INSERT INTO event(id, stream_id, kind, name, key, ts_start, sev) VALUES"
        " (1, 1, 'operation', 'SendWorks', 'K1', ?, 4)",
        (T0,),
    )
    add_entry(conn, 1, 1, T0, "announce payload K1")
    xml_req = "<SendWorksRequest><ID>1</ID></SendWorksRequest>"
    xml_resp = "<SendWorksResponse><Reply>OK</Reply></SendWorksResponse>"
    for n in range(10, 20):
        add_entry(conn, n, 2, T0, PREFIX + f"<Doc><n>{n}</n></Doc>")
    add_entry(conn, 20, 2, T0 + 1000, PREFIX + "<Table>", "\n".join(big_xml().split("\n")[1:]))
    add_entry(conn, 21, 2, None, PREFIX + SCRIPT_XML)
    add_entry(conn, 22, 2, T0 + 2000, "Message: <Doc>", "  <a>1</a>\n  <b>2</b>\n</Doc>.")
    add_entry(conn, 30, 1, T0 + 3000, PREFIX + "<Content/>")
    # entries 10 and 11 hold the linked pair (request, response), announced by entry 1
    conn.execute("UPDATE entry SET msg = ? WHERE id = 10", (PREFIX + xml_req,))
    conn.execute("UPDATE entry SET msg = ? WHERE id = 11", (PREFIX + xml_resp,))
    add_payload(conn, 1, 10, xml_req, event_id=1, event_conf=3, ref_entry_id=1, pair_id=2,
                msg_id="m-1", operation="SendWorks")  # fmt: skip
    add_payload(conn, 2, 11, xml_resp, event_id=1, event_conf=2, pair_id=1, role="response",
                direction="out", corr_id="m-1", root="SendWorksResponse")  # fmt: skip
    for pid, n in zip(range(3, 11), range(12, 20), strict=True):
        add_payload(
            conn, pid, n, f"<Doc><n>{n}</n></Doc>", reason="no_operation_for_key",
            peer="CRS" if pid % 2 else "WFM", role="fault" if pid == 3 else "request",
            operation="Other" if pid > 7 else "SendWorks",
        )  # fmt: skip
    table = "<Table>\n" + "\n".join(big_xml().split("\n")[1:])
    add_payload(conn, 11, 20, table, root="Table", fmt="xml", operation=None, reason="not_in_operation")
    add_payload(conn, 12, 21, SCRIPT_XML, reason="ambiguous", fmt="xml")
    add_payload(conn, 13, 22, "<Doc>\n  <a>1</a>\n  <b>2</b>\n</Doc>", prefix="Message: ", event_id=1,
                event_conf=1, role="content", fmt="xml")  # fmt: skip
    add_payload(conn, 14, 30, "<Content/>", role="content", direction="out", peer="CRS", fmt="xml_seq",
                well_formed=None, truncated=1)  # fmt: skip
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    c = create_case_db(tmp_path / "case.sqlite")
    seed(c)
    yield c
    c.close()


def page_all(conn, flt, limit):
    seen, after = [], None
    while True:
        rows, nxt = payloads.list_payloads(conn, flt, limit, after)
        seen += [r["id"] for r in rows]
        if nxt is None:
            return seen
        after = Cursor.parse(nxt)


def test_keyset_paging_has_no_gaps_or_duplicates_on_tied_ts(conn):
    full, nxt = payloads.list_payloads(conn, PayloadFilter(), 1000)
    assert nxt is None
    ids = [r["id"] for r in full]
    assert len(ids) == len(set(ids)) == 14
    assert ids[0] == 12  # the record without a time sorts first
    for limit in (1, 2, 3, 5, 13, 14):
        assert page_all(conn, PayloadFilter(), limit) == ids
    # ties at T0 come ordered by payload id
    tied = [r["id"] for r in full if r["ts"] == T0]
    assert tied == sorted(tied) and len(tied) == 10


def test_filters(conn):
    def ids(**kw):
        return [r["id"] for r in payloads.list_payloads(conn, PayloadFilter(**kw), 1000)[0]]

    assert ids(roles=("response",)) == [2]
    assert ids(roles=("fault", "response")) == [2, 3]
    assert ids(directions=("out",)) == [2, 14]
    assert ids(peers=("CRS",)) == [3, 5, 7, 9, 14]
    assert ids(operations=("Other",)) == [8, 9, 10]
    assert ids(formats=("xml_seq",)) == [14]
    assert ids(event_id=1) == [1, 2, 13]
    assert ids(key_query="m-1") == [1, 2]
    assert ids(key_query="nothing") == []
    assert ids(orphan=False) == [1, 2, 13]
    assert len(ids(orphan=True)) == 11
    assert ids(roles=("request",), orphan=True, peers=("WFM",), operations=("SendWorks",)) == [12, 4, 6]


def test_orphans_carry_a_reason(conn):
    rows = payloads.list_payloads(conn, PayloadFilter(orphan=True), 1000)[0]
    reasons = {r["id"]: r["reason"] for r in rows}
    assert reasons[3] == "no_operation_for_key" and reasons[12] == "ambiguous"
    assert reasons[14] is None  # an orphan the pipeline gave no reason for
    assert all(r["event_id"] is None for r in rows)


def test_meta_shape_and_types(conn):
    row = next(r for r in payloads.list_payloads(conn, PayloadFilter(), 1000)[0] if r["id"] == 14)
    assert set(row) == {
        "id", "entry_id", "event_id", "event_conf", "ref_entry_id", "pair_id", "role", "direction",
        "peer", "operation", "msg_id", "corr_id", "fmt", "root", "well_formed", "truncated", "reason",
        "char_len", "ts", "sensitive", "member",
    }  # fmt: skip
    assert row["truncated"] is True and row["well_formed"] is None
    assert row["sensitive"] is False and row["member"] == "SVC/Wfm.log" and row["ts"] == T0 + 3000


def test_payloads_of_entry_include_the_announced_ones(conn):
    assert [p["id"] for p in payloads.payloads_of_entry(conn, 10)] == [1]
    assert [p["id"] for p in payloads.payloads_of_entry(conn, 1)] == [1]  # announcer (ref_entry_id)
    assert payloads.payloads_of_entry(conn, 999) == []
    assert payloads.payload_count(conn, 1) == 1 and payloads.payload_count(conn, 22) == 1
    assert payloads.payload_count(conn, 999) == 0


def test_text_slice_and_pair(conn):
    got = payloads.get_payload(conn, 1)
    assert got["text"] == "<SendWorksRequest><ID>1</ID></SendWorksRequest>"
    assert got["pair"]["id"] == 2 and got["truncated_display"] is False
    assert got["entry"] == {
        "id": 10,
        "ts": T0,
        "tid": 7,
        "member": "SVC/Wfm.sensitive.log",
        "sensitive": True,
    }
    assert payloads.get_payload(conn, 2)["pair"]["id"] == 1
    assert payloads.get_payload(conn, 3)["pair"] is None
    assert payloads.get_payload(conn, 999) is None


def test_multiline_payload_spans_msg_and_cont(conn):
    assert payloads.get_payload(conn, 13)["text"] == "<Doc>\n  <a>1</a>\n  <b>2</b>\n</Doc>"


def test_large_payload_is_served_whole_and_cap_is_reported(conn):
    got = payloads.get_payload(conn, 11)
    assert len(got["text"]) == got["payload"]["char_len"] > 170_000
    assert got["text"].startswith('<Table>\n  <Row n="0">') and got["text"].endswith("</Table>")
    assert got["truncated_display"] is False
    small = payloads.get_payload(conn, 11, cap=1000)
    assert len(small["text"]) == 1000 and small["truncated_display"] is True
    assert got["text"].startswith(small["text"])


def test_script_and_entities_stay_literal(conn):
    assert payloads.get_payload(conn, 12)["text"] == SCRIPT_XML


def test_injection_text_is_a_bound_value(conn):
    for s in ("' OR 1=1 --", '"); DROP TABLE payload; --', "%", "\x00"):
        flt = PayloadFilter(roles=(s,), peers=(s,), operations=(s,), key_query=s)
        assert payloads.list_payloads(conn, flt, 10)[0] == []
    assert conn.execute("SELECT count(*) FROM payload").fetchone()[0] == 14


def test_limit_is_clamped(conn):
    assert len(payloads.list_payloads(conn, PayloadFilter(), 0)[0]) == 1
    assert len(payloads.list_payloads(conn, PayloadFilter(), 10**6)[0]) == 14
