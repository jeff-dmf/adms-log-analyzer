"""Payload keys in entry_key (issue #67): extraction, limits, hostile input, writer, upgrade.

Synthetic data only.
"""

from __future__ import annotations

import time

import pytest
import test_pipeline as tp
from test_payload_link import Case

from dmslog.derive import payloads as pl
from dmslog.derive.engine import _entries
from dmslog.ingest import pipeline
from dmslog.ingest.pipeline import IngestOptions, run_ingest
from dmslog.parse import payload as pp
from dmslog.parse.payload import extract_payload, payload_keys
from dmslog.parse.templates import PayloadSpec, load_texts
from dmslog.store import keys as store_keys
from dmslog.store.writer import IndexWriter

mf = tp.mf

G1 = "00000000-0000-4000-8000-000000000001"
G2 = "00000000-0000-4000-8000-000000000002"
GID_HEX = "0x0400000000000001"
GID_DEC = str(int(GID_HEX, 16))


def keys(text: str) -> list[tuple[str, str]]:
    return list(payload_keys(text, 0, len(text)))


# --------------------------------------------------------------------------- what is extracted


REQUEST = f"""<ns0:ChangedThings xmlns:ns0="urn:example">
  <ns0:Header>
    <ns1:Verb xmlns:ns1="urn:m">changed</ns1:Verb>
    <ns1:MessageID xmlns:ns1="urn:m">{G1}</ns1:MessageID>
    <ns1:CorrelationID xmlns:ns1="urn:m">{G1}</ns1:CorrelationID>
  </ns0:Header>
  <ns0:Payload>
    <IncidentRecord>
      <mRID>INC 1</mRID>
      <Crews><mRID>CRW1</mRID><name>CRW1</name></Crews>
      <IncidentHazards><mRID>PROB 2</mRID></IncidentHazards>
      <Equipments><mRID>EQ-1</mRID></Equipments>
      <UsagePoint><mRID>123456789012345</mRID><meterId>1234</meterId></UsagePoint>
      <Substation><mRID>SUB</mRID></Substation>
      <TroubleTickets><mRID>TT 3</mRID></TroubleTickets>
    </IncidentRecord>
  </ns0:Payload>
</ns0:ChangedThings>"""


def test_request_gives_message_id_once_and_business_mrids_only():
    assert keys(REQUEST) == [
        ("msgid", G1),  # MessageID = CorrelationID: one row
        ("mrid", "INC 1"),
        ("mrid", "CRW1"),
        ("mrid", "PROB 2"),
        ("mrid", "TT 3"),
    ]


def test_response_gives_its_own_id_and_the_correlation_id():
    text = (
        "<ThingsResponseMessage><Header><MessageID>ABCD_000000000000000001</MessageID>"
        f"<CorrelationID>{G2}</CorrelationID></Header>"
        "<Payload><Crew><mRID>CRW1</mRID></Crew><MessageID>not-in-header</MessageID></Payload>"
        "</ThingsResponseMessage>"
    )
    assert keys(text) == [("msgid", "ABCD_000000000000000001"), ("msgid", G2), ("mrid", "CRW1")]


def test_vehicle_mrid_is_also_a_vehicle_key():
    text = "<ChangedVehicles><Vehicle><mRID>V 1</mRID></Vehicle></ChangedVehicles>"
    assert keys(text) == [("mrid", "V 1"), ("vehicle", "V 1")]


def test_switching_and_work_identifiers():
    text = (
        "<ChangedSwitchingPlans><SwitchingPlan><mRID>SP 1</mRID>"
        "<SwitchingSteps><mRID>SP 1 - ISS 1</mRID></SwitchingSteps>"
        "<SwitchingStepGroup><mRID>SG 1</mRID></SwitchingStepGroup>"
        "<Works><mRID>WR 1</mRID></Works><Incidents><mRID>INC 9</mRID></Incidents>"
        "<PSRType><mRID>Breaker</mRID></PSRType></SwitchingPlan></ChangedSwitchingPlans>"
    )
    assert keys(text) == [
        ("mrid", "SP 1"), ("mrid", "SP 1 - ISS 1"), ("mrid", "SG 1"), ("mrid", "WR 1"), ("mrid", "INC 9"),
    ]  # fmt: skip


def test_delta_gid_is_turned_decimal_like_the_log_lines():
    text = (
        '<delta id="7"><operation type="insert"><ResourceDescription>'
        f'<id gid="{GID_HEX}" type="1"/><Properties><Property id="X" value="{GID_HEX}"/></Properties>'
        "</ResourceDescription></operation></delta>"
    )
    assert keys(text) == [("gid", GID_DEC)]


def test_publication_sequence_gives_gids_and_the_custom_id():
    text = (
        '<ResourceDescription><id gid="0x10" type="1"/><Properties>'
        '<Property id="SM_COMMANDING_MODEL_CUSTOMID" value=" SP 4 "/>'
        '<Property id="SM_WORK_PLAN_DETAILS" value="free text"/></Properties></ResourceDescription>\n'
        "<ResourceDescription><id type='2' gid='0x11'/></ResourceDescription>"
    )
    assert keys(text) == [("gid", "16"), ("mrid", "SP 4"), ("gid", "17")]


def test_content_to_crs_and_ims_gids():
    text = (
        "<ArrayOfAssignmentStatusInfo><AssignmentStatusInfo><CrewId>0001234567890</CrewId>"
        "<ServiceAssignmentId>288230376151711745</ServiceAssignmentId><StatusId>99</StatusId>"
        "</AssignmentStatusInfo></ArrayOfAssignmentStatusInfo>"
    )
    assert keys(text) == [
        ("gid", "1234567890"),
        ("gid", "288230376151711745"),
        ("incident_gid", "288230376151711745"),
    ]
    ims = "<A><IncidentCrewAssigment><IncidentId>5</IncidentId></IncidentCrewAssigment></A>"
    assert keys(ims) == [("gid", "5"), ("incident_gid", "5")]


@pytest.mark.parametrize(
    "raw",
    ["0", "0x0", "-1", "0x", "0xZZ", "0x" + "1" * 17, "1" * 21, "1 2", "１２"],  # noqa: RUF001
)
def test_values_that_are_not_gids_are_skipped(raw):
    assert (
        keys(f"<A><CrewId>{raw}</CrewId><ResourceDescription><id gid='{raw}'/></ResourceDescription></A>")
        == []
    )


def test_identifier_normalization():
    assert keys("<Crew><mRID>  A &amp; B  </mRID></Crew>") == [("mrid", "A & B")]
    assert keys("<Crew><mRID>   </mRID></Crew>") == []
    assert keys("<Crew><mRID>a\x01b</mRID></Crew>") == []
    assert keys("<Crew><mRID>a\nb</mRID></Crew>") == []
    assert keys("<Crew><mRID>" + "x" * (pp.MAX_ID_CHARS + 1) + "</mRID></Crew>") == []
    assert keys("<Crew><mRID>" + "x" * pp.MAX_ID_CHARS + "</mRID></Crew>") == [
        ("mrid", "x" * pp.MAX_ID_CHARS)
    ]
    assert keys("<Crew><mRID>&lt;x&gt;</mRID></Crew>") == []  # never a '<' in a key
    assert keys("<Crew><mRID>INC 1</mRID><mRID>INC 1</mRID></Crew>") == [("mrid", "INC 1")]  # distinct
    assert keys("<Crew><mRID>&#65;&#x42;</mRID></Crew>") == [("mrid", "AB")]
    assert keys("<Crew><mRID>A&#xD;&#xA;B</mRID></Crew>") == []  # a line break is never part of a key
    assert keys("<Crew><mRID>&#xD800;</mRID></Crew>") == []  # a surrogate could not be stored
    assert keys("<Crew><mRID>&#0;x</mRID><mRID>&#x110000;</mRID></Crew>") == []
    assert keys("<Crew><mRID>&unknown;</mRID></Crew>") == [("mrid", "&unknown;")]
    publication = (
        '<ResourceDescription><Property id="SM_COMMANDING_MODEL_CUSTOMID" value="SP\t1"/>'
        "</ResourceDescription>"
    )
    assert keys(publication) == [("mrid", "SP 1")]  # attribute blanks are normalized as XML does


def test_only_leaves_with_their_closing_tag_count():
    assert keys("<Crew><mRID><x>1</x></mRID></Crew>") == []
    assert keys("<Crew><mRID>INC 1") == []  # a cut record never gives a cut value
    assert keys("<Crew><mRID>INC 1</mRID") == []
    assert keys("<Crew><mRID>a<b</mRID></Crew>") == []
    assert keys("<Crew><!-- <mRID>X</mRID> --><mRID>Y</mRID></Crew>") == [("mrid", "Y")]
    assert keys("<Crew><![CDATA[<mRID>X</mRID>]]><mRID>Y</mRID></Crew>") == [("mrid", "Y")]
    assert keys("<?xml version='1.0'?><Crew><mRID>Y</mRID></Crew>") == [("mrid", "Y")]
    assert keys("<Crew><!-- unterminated <mRID>X</mRID></Crew>") == []


def test_parent_is_tracked_across_unclosed_elements():
    # a missing close tag nearby does not shift every later parent
    text = (
        "<A><Crew><x><mRID>C1</mRID></Crew><Equipments><mRID>E</mRID></Equipments>"
        "<Crew><mRID>C2</mRID></Crew></A>"
    )
    assert keys(text) == [("mrid", "C2")]


def test_limits(monkeypatch):
    many = "<A>" + "".join(f"<Crew><mRID>C{i}</mRID></Crew>" for i in range(pp.MAX_KEYS + 50)) + "</A>"
    got = keys(many)
    assert len(got) == pp.MAX_KEYS and got[0] == ("mrid", "C0")
    monkeypatch.setattr(pp, "MAX_KEY_SCAN_CHARS", 40)
    assert keys("<A><Crew><mRID>C1</mRID></Crew>" + " " * 100 + "<Crew><mRID>C2</mRID></Crew></A>") == [
        ("mrid", "C1")
    ]
    monkeypatch.setattr(pp, "MAX_KEY_SCAN_CHARS", 1_000_000)
    monkeypatch.setattr(pp, "MAX_DEPTH", 3)
    deep = (
        "<A>" + "<B>" * 10 + "<Crew><mRID>X</mRID></Crew>" + "</B>" * 10 + "<Crew><mRID>Y</mRID></Crew></A>"
    )
    assert keys(deep) == [("mrid", "Y")]  # parents beyond the tracked depth are unknown


def test_payload_keys_respects_the_span():
    text = "prefix <Crew><mRID>A</mRID></Crew> suffix <Crew><mRID>B</mRID></Crew>"
    end = text.index(" suffix")
    assert payload_keys(text, text.index("<"), end) == (("mrid", "A"),)


HOSTILE = [
    "<" * 300_000,
    "<a" * 150_000,
    "<a " + "b" * 600_000 + ">",
    "<Crew>" * 100_000,
    "</Crew>" * 100_000,
    "<ResourceDescription><id " + "g" * 600_000,
    "<ResourceDescription><id " + 'a="1" ' * 100_000 + "/>",
    "<Property " + "id=" * 200_000 + "/>",
    "<Crew><mRID>" + "&amp;" * 200_000 + "</mRID></Crew>",
    "<Crew><mRID>" + "x" * 900_000 + "</mRID></Crew>",
    "\x00<\x00>" * 100_000,
    "<!--" * 200_000,
    "<![CDATA[" * 100_000,
    "<?" * 300_000,
]


@pytest.mark.parametrize("text", HOSTILE, ids=range(len(HOSTILE)))
def test_hostile_input_is_linear_and_never_raises(text):
    started = time.perf_counter()
    got = payload_keys(text, 0, len(text))
    assert time.perf_counter() - started < 2.0
    assert isinstance(got, tuple) and len(got) <= pp.MAX_KEYS


def test_never_raises_on_bad_spans():
    assert payload_keys("<a/>", 10, 2) == ()
    assert payload_keys("", 0, 0) == ()
    assert payload_keys(None, 0, 5) == ()  # type: ignore[arg-type]


# --------------------------------------------------------------------------- through extract_payload


def _spec(**kw) -> PayloadSpec:
    base = {"fmt": "xml", "start": "match_end", "strip_suffix": ".", "role": "by_root"}
    base.update(kw)
    return PayloadSpec(**base)


def test_extract_payload_carries_the_keys_of_xml_only():
    msg = "Message content: <ChangedThings>"
    cont = (
        f"  <Header><MessageID>{G1}</MessageID></Header>\n  <Crew><mRID>C1</mRID></Crew>\n</ChangedThings>."
    )
    info = extract_payload(_spec(), msg, cont, len("Message content: "), False)
    assert info is not None and info.keys == (("msgid", G1), ("mrid", "C1"))
    html = extract_payload(_spec(fmt="html", strip_suffix=None), msg, cont, len("Message content: "), False)
    assert html is not None and html.keys == ()
    text = extract_payload(_spec(fmt="text", strip_suffix=None), "Message content: x", None, 17, False)
    assert text is not None and text.keys == ()


def test_partial_record_keeps_only_complete_values():
    msg = "Message content: <ChangedThings>"
    cont = "  <Crew><mRID>C1</mRID></Crew>\n  <Crew><mRID>C2"
    info = extract_payload(_spec(), msg, cont, len("Message content: "), True)
    assert info is not None and info.truncated == 1 and info.keys == (("mrid", "C1"),)


# --------------------------------------------------------------------------- writer and pipeline

TOML = """
schema = 1
family = "AWFM"

[payload_defaults]
request = { direction = "in", peer = "WFM" }
response = { direction = "out", peer = "WFM" }

[[template]]
code = "TST-PLD"
class = "req_resp"
stream = "any"
regex = '''^Message content: (?=<)'''
payload = { fmt = "xml", start = "match_end", strip_suffix = ".", role = "by_root" }

[[template]]
code = "TST-SEND"
class = "other"
regex = '''^Sending (?P<msg_id>\\S+) for (?P<mrid>.+)\\.$'''
keys = [{ field = "msg_id", type = "msgid" }, "mrid"]
"""


def make_log(extra: list[str] | None = None) -> bytes:
    lines = [
        mf.header(0, "INFO", f"Sending {G1} for INC 1."),
        mf.header(10, "INFO", "Message content: <ChangedThings>"),
        f"  <Header><MessageID>{G1}</MessageID><CorrelationID>{G1}</CorrelationID></Header>",
        "  <Payload><IncidentRecord><mRID>INC 1</mRID><Crews><mRID>CRW1</mRID></Crews></IncidentRecord>",
        "  </Payload>",
        "</ChangedThings>.",
        mf.header(20, "INFO", "Message content: <ThingsResponseMessage>"),
        f"  <Header><MessageID>RESP_1</MessageID><CorrelationID>{G1}</CorrelationID></Header>",
        "</ThingsResponseMessage>.",
        *(extra or []),
    ]
    return mf.render(lines, eol="\r\n")


@pytest.fixture
def db(tmp_path):
    conn = tp.fresh_db(tmp_path, "c")
    yield conn
    conn.close()


def ingest(conn, tmp_path, data=None):
    root = tp.write_folder(tmp_path / "Log", {tp.WFM: data if data is not None else make_log()})
    return run_ingest(conn, root, IngestOptions(catalog=load_texts({"awfm.toml": TOML})))


def all_keys(conn):
    return conn.execute(
        "SELECT e.line_no, k.key_type, k.value, k.origin FROM entry_key k JOIN entry e ON e.id = k.entry_id"
        " ORDER BY 1, 2, 3"
    ).fetchall()


EXPECTED = [
    (1, "mrid", "INC 1", 0),
    (1, "msgid", G1, 0),
    (2, "mrid", "CRW1", 1),
    (2, "mrid", "INC 1", 1),
    (2, "msgid", G1, 1),
    (7, "msgid", G1, 1),
    (7, "msgid", "RESP_1", 1),
]


def test_ingest_writes_payload_keys_with_origin_1(tmp_path, db):
    result = ingest(db, tmp_path)
    assert result.sources_failed == 0 and result.payloads == 2
    assert all_keys(db) == EXPECTED


def test_key_search_finds_log_lines_and_payloads_together(tmp_path, db):
    ingest(db, tmp_path)
    records, nxt = store_keys.records_with_key(db, "mrid", "INC 1")
    assert [r["tpl_code"] for r in records] == ["TST-SEND", "TST-PLD"] and nxt is None
    records, _ = store_keys.records_with_key(db, "msgid", G1)
    assert [r["tpl_code"] for r in records] == ["TST-SEND", "TST-PLD", "TST-PLD"]
    types = {t["type"]: t["count"] for t in store_keys.key_types(db)}
    assert types == {"mrid": 3, "msgid": 4}


def test_reingest_is_idempotent(tmp_path, db):
    ingest(db, tmp_path)
    changes = db.total_changes
    again = ingest(db, tmp_path)
    assert again.sources_indexed == 0 and db.total_changes == changes
    assert all_keys(db) == EXPECTED


def test_append_adds_the_keys_of_new_payloads_only(tmp_path, db):
    ingest(db, tmp_path)
    more = [
        mf.header(30, "INFO", "Message content: <ChangedThings>"),
        "  <Payload><Crew><mRID>CRW2</mRID></Crew></Payload>",
        "</ChangedThings>.",
    ]
    result = ingest(db, tmp_path, make_log(more))
    assert result.sources_failed == 0
    assert all_keys(db) == [*EXPECTED, (10, "mrid", "CRW2", 1)]


def test_truncate_and_reindex_remove_payload_keys(tmp_path, db):
    ingest(db, tmp_path)
    w = IndexWriter(db)
    sid = db.execute("SELECT id FROM source WHERE n_entries > 0").fetchone()[0]
    off = db.execute("SELECT byte_off FROM entry WHERE line_no = 7").fetchone()[0]
    w.truncate_from(sid, off)
    w.commit()
    assert all_keys(db) == EXPECTED[:5]
    w.delete_source_entries(sid)
    w.commit()
    assert all_keys(db) == []


def test_old_case_gets_its_payload_keys_without_a_reindex(tmp_path, db):
    ingest(db, tmp_path)
    entries = db.execute("SELECT id, line_no FROM entry ORDER BY id").fetchall()
    sid = db.execute("SELECT id FROM source WHERE n_entries > 0").fetchone()[0]
    # what an extraction-version-1 case looks like: payload rows, no payload keys, marks at 1
    db.execute("DELETE FROM entry_key WHERE origin = 1")
    db.execute("UPDATE meta SET value = '1' WHERE key LIKE 'payload_extraction_version%'")
    db.commit()
    result = ingest(db, tmp_path)
    assert result.sources_failed == 0 and result.sources_indexed == 0 and result.payloads == 0
    assert db.execute("SELECT id, line_no FROM entry ORDER BY id").fetchall() == entries  # not reindexed
    assert all_keys(db) == EXPECTED
    marks = dict(db.execute("SELECT key, value FROM meta WHERE key LIKE 'payload_extraction_version%'"))
    assert marks[f"payload_extraction_version.{sid}"] == str(pipeline.PAYLOAD_EXTRACTION_VERSION)
    changes = db.total_changes
    assert ingest(db, tmp_path).sources_indexed == 0 and db.total_changes == changes  # once only


def test_refresh_is_idempotent_and_keeps_template_keys(tmp_path, db):
    ingest(db, tmp_path)
    w = IndexWriter(db)
    sid = db.execute("SELECT id FROM source WHERE n_entries > 0").fetchone()[0]
    db.execute("INSERT INTO entry_key(key_type, value, entry_id, origin) VALUES ('mrid', 'stale', 2, 1)")
    w.refresh_payload_keys(sid)
    w.commit()
    assert all_keys(db) == EXPECTED
    w.refresh_payload_keys(sid)
    w.commit()
    assert all_keys(db) == EXPECTED


def test_an_older_mark_outside_the_key_only_upgrades_still_reindexes(tmp_path, db, monkeypatch):
    ingest(db, tmp_path)
    db.execute("UPDATE meta SET value = '0' WHERE key LIKE 'payload_extraction_version.%'")
    db.commit()
    again = ingest(db, tmp_path)
    assert again.sources_indexed == 1
    assert all_keys(db) == EXPECTED


# --------------------------------------------------------------------------- never drive the derivation


def test_derivation_reads_template_keys_only(tmp_path, db):
    ingest(db, tmp_path)
    sid, lo, hi = db.execute("SELECT source_id, min(id), max(id) FROM entry GROUP BY source_id").fetchone()
    seen = {e.id: e.keys for e in _entries(db, [(sid, lo, hi)], lo)}
    line = dict(db.execute("SELECT id, line_no FROM entry"))
    by_line = {line[i]: k for i, k in seen.items()}
    assert by_line[1] == (("mrid", "INC 1"), ("msgid", G1))
    assert by_line[2] == () and by_line[7] == ()


def test_payload_linking_ignores_payload_keys(tmp_path):
    case = Case(tmp_path)
    try:
        ev = case.operation("Op", 1000, 1100, key="00000000-0000-4000-8000-0000000000aa")
        start = case.conn.execute("SELECT start_entry_id FROM event WHERE id = ?", (ev,)).fetchone()[0]
        case.conn.execute(
            "INSERT INTO entry_key(key_type, value, entry_id, origin) VALUES ('msgid', ?, ?, 1)", (G1, start)
        )
        only_payload_key, _ = case.payload("GEN-PLD-01", 1050, "request", mid=G1)
        template_key, _ = case.payload(
            "GEN-PLD-01", 1060, "request", mid="00000000-0000-4000-8000-0000000000aa"
        )
        case.run()
        assert case.row(only_payload_key)[0] is None
        assert case.conn.execute(
            "SELECT reason FROM payload WHERE id = ?", (only_payload_key,)
        ).fetchone() == (pl.R_NO_OPERATION,)
        assert case.row(template_key)[0] == ev
    finally:
        case.conn.close()
