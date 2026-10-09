"""parse/payload.py and its use by the writer (synthetic data only)."""

from __future__ import annotations

import json
import time

import pytest
import test_pipeline as tp

from dmslog.ingest.pipeline import IngestOptions, run_ingest
from dmslog.parse.payload import check_xml, extract_payload, local_name, role_of_root
from dmslog.parse.templates import PayloadSpec, load_texts
from dmslog.store.writer import FLAG_HAS_PAYLOAD

mf = tp.mf

DEFAULTS = {
    "request": {"direction": "in", "peer": "EXT"},
    "response": {"direction": "out", "peer": "EXT"},
    "fault": {"direction": "out", "peer": "EXT"},
}
G1 = "00000000-0000-4000-8000-000000000001"
G2 = "00000000-0000-4000-8000-000000000002"

REQUEST = (
    '<ChangedThings xmlns="http://example.invalid/t">\n'
    "  <Header>\n"
    f'    <MessageID xmlns="http://example.invalid/m">{G1}</MessageID>\n'
    f'    <CorrelationID xmlns="http://example.invalid/m">{G2}</CorrelationID>\n'
    "  </Header>\n"
    "  <Payload><Thing><id>t1</id></Thing></Payload>\n"
    "</ChangedThings>"
)


def spec(**kw) -> PayloadSpec:
    base = {"fmt": "xml", "start": "match_end", "strip_suffix": ".", "role": "by_root"}
    base.update(kw)
    return PayloadSpec(**base)


def extract(text: str, prefix: str = "Message content: ", sp: PayloadSpec | None = None, **kw):
    """A record whose first line is ``prefix`` + the first line of ``text``."""
    first, _, rest = text.partition("\n")
    msg = prefix + first
    return (
        extract_payload(sp or spec(), msg, rest or None, len(prefix), kw.get("partial", False), DEFAULTS),
        msg,
        (rest or None),
    )


def slice_of(info, msg: str, cont: str | None) -> str:
    text = msg + ("\n" + cont if cont is not None else "")
    return text[info.char_off : info.char_off + info.char_len]


# --------------------------------------------------------------------------- spans


def test_match_end_span_without_the_trailing_dot():
    info, msg, cont = extract(REQUEST + ".")
    assert slice_of(info, msg, cont) == REQUEST
    assert slice_of(info, msg, cont).endswith("</ChangedThings>")
    assert (info.fmt, info.root, info.operation, info.role) == (
        "xml",
        "ChangedThings",
        "ChangedThings",
        "request",
    )
    assert (info.direction, info.peer) == ("in", "EXT")
    assert (info.msg_id, info.corr_id) == (G1, G2)
    assert (info.well_formed, info.truncated) == (1, 0)


def test_no_suffix_keeps_the_text_and_blanks_are_skipped():
    info, msg, cont = extract(REQUEST + "   ", sp=spec(strip_suffix=None))
    assert slice_of(info, msg, cont) == REQUEST
    # a record that ends with the suffix twice loses only one
    info, msg, cont = extract(REQUEST + "..", sp=spec())
    assert slice_of(info, msg, cont) == REQUEST + "." and info.well_formed == 0


def test_start_msg_uses_the_whole_first_line():
    info = extract_payload(
        spec(start="msg", strip_suffix=None, role="delta"), '<delta id="7">', "  <x/>\n</delta>", 0, False
    )
    assert info.char_off == 0 and info.root == "delta" and info.well_formed == 1
    assert (info.role, info.direction, info.peer) == (
        "delta",
        "out",
        None,
    )  # no default: guessed out, no peer


def test_start_first_cont_lt_skips_key_value_lines():
    cont = 'DmsType: Plan\nOperation: Changed\n  <ResourceDescription a="1"/>\n<ResourceDescription a="2"/>'
    sp = spec(fmt="xml_seq", start="first_cont_lt", strip_suffix=None, role="publication")
    sp = PayloadSpec(sp.fmt, sp.start, None, sp.role, "in", "DMS")
    info = extract_payload(sp, "[Sub] Data received from publication:", cont, 0, False)
    text = "[Sub] Data received from publication:\n" + cont
    assert text[info.char_off :].startswith('<ResourceDescription a="1"/>')
    assert (info.fmt, info.well_formed, info.root) == ("xml_seq", 1, "ResourceDescription")
    assert (info.direction, info.peer, info.role) == ("in", "DMS", "publication")


def test_first_cont_lt_without_xml_line_gives_none():
    assert extract_payload(spec(start="first_cont_lt"), "Content:", "a: 1\nb: 2", 0, False) is None
    assert extract_payload(spec(start="first_cont_lt"), "Content:", None, 0, False) is None


def test_match_end_at_the_end_of_msg_finds_xml_on_the_next_line():
    info = extract_payload(
        spec(strip_suffix=None, role="content"), "Content being sent:", "<ArrayOfX>\n</ArrayOfX>", 19, False
    )
    assert info is not None and info.root == "ArrayOfX" and info.well_formed == 1


def test_nothing_that_looks_like_a_payload_gives_none():
    assert extract_payload(spec(), "Message content: not xml at all.", None, 17, False) is None
    assert extract_payload(spec(), "Message content: .", None, 17, False) is None
    assert extract_payload(spec(), "Message content:", None, 16, False) is None


# --------------------------------------------------------------------------- roles, formats


@pytest.mark.parametrize(
    ("root", "role"),
    [
        ("ns0:SwitchingStepsResponseMessage", "response"),
        ("CrewsResponseMessage", "response"),
        ("s:Fault", "fault"),
        ("Fault", "fault"),
        ("ChangedCrews", "request"),
        ("SomethingFaultMessage", "request"),
        (None, "request"),
    ],
)
def test_role_by_root(root, role):
    assert role_of_root(root) == role
    assert local_name("a:b") == "b" and local_name(None) is None


def test_by_root_picks_direction_and_peer_from_the_family_defaults():
    fault = '<s:Fault xmlns:s="x"><faultcode>c</faultcode></s:Fault>.'
    info, *_ = extract(
        "Message content: ".removesuffix("Message content: ") + fault, prefix="Message content: "
    )
    assert (info.role, info.direction, info.peer, info.root) == ("fault", "out", "EXT", "s:Fault")
    resp = "<ns1:ThingsResponseMessage><ns1:Header/></ns1:ThingsResponseMessage>."
    info, *_ = extract(resp)
    assert (info.role, info.direction, info.operation) == ("response", "out", "ThingsResponseMessage")


def test_template_direction_and_peer_win_over_the_defaults():
    info, *_ = extract(REQUEST + ".", sp=spec(direction="out", peer="SAP_PI"))
    assert (info.direction, info.peer) == ("out", "SAP_PI")


def test_fixed_role_ignores_the_root():
    info, *_ = extract(REQUEST + ".", sp=spec(role="content"))
    assert info.role == "content"


def test_xml_with_sibling_roots_becomes_xml_seq():
    info, *_ = extract("<A/>\n<B>1</B>", sp=spec(strip_suffix=None))
    assert (info.fmt, info.well_formed) == ("xml_seq", 1)


def test_json_and_text_formats():
    info, *_ = extract(
        '{"a": [1, 2]}', prefix="Body: ", sp=spec(fmt="json", strip_suffix=None, role="request")
    )
    assert (info.fmt, info.well_formed, info.root, info.msg_id) == ("json", 1, None, None)
    info, *_ = extract('{"a": ', prefix="Body: ", sp=spec(fmt="json", strip_suffix=None, role="request"))
    assert info.well_formed == 0
    info, *_ = extract("free text", prefix="Body: ", sp=spec(fmt="text", strip_suffix=None, role="other"))
    assert (info.fmt, info.well_formed) == ("text", None)


def test_html_is_not_checked_and_has_no_ids():
    info, *_ = extract(
        "<html><body>Bad gateway</body></html>", sp=spec(fmt="html", strip_suffix=None, role="other")
    )
    assert (info.fmt, info.root, info.well_formed, info.msg_id) == ("html", "html", None, None)


# --------------------------------------------------------------------------- ids


def test_ids_come_from_the_header_only():
    body = (
        "<Reply>\n  <Header><Verb>reply</Verb></Header>\n"
        f"  <Payload><MessageID>{G2}</MessageID></Payload>\n</Reply>"
    )
    info, *_ = extract(body + ".")
    assert (info.msg_id, info.corr_id) == (None, None)  # the id outside the header is not the message's
    info, *_ = extract("<R><Verb>x</Verb><ns:MessageID a='1'> " + G1 + " </ns:MessageID></R>")
    assert info.msg_id == G1  # no header: first occurrence, blanks trimmed, prefix allowed


def test_empty_ids_are_none_and_long_ids_are_cut():
    info, *_ = extract("<R><Header><MessageID></MessageID></Header></R>.")
    assert info.msg_id is None
    info, *_ = extract("<R><Header><MessageID>" + "x" * 500 + "</MessageID></Header></R>.")
    assert len(info.msg_id) == 128


# --------------------------------------------------------------------------- well-formedness


@pytest.mark.parametrize(
    ("text", "ok", "roots"),
    [
        ("<a><b>t</b><c x='1'/></a>", True, 1),
        ("<a>\ntext line\n  more text</a>", True, 1),
        ("<a><!-- c --><?pi x?><![CDATA[<<>>]]></a>", True, 1),
        ("<a/><b/>", True, 2),
        ("<a><b></a></b>", False, 1),
        ("<a><b>", False, 1),
        ("<a></a>junk", False, 1),
        ("<a>1 < 2</a>", False, 1),
        ("junk<a/>", False, 0),
        ("</a>", False, 0),
        ("", False, 0),
    ],
)
def test_check_xml(text, ok, roots):
    assert check_xml(text, 0, len(text)) == (ok, roots)


def test_malformed_payload_is_zero_unless_the_record_was_cut():
    info, *_ = extract("<a><b></a>.")
    assert info.well_formed == 0 and info.truncated == 0
    info, *_ = extract("<a><b>text", partial=True)
    assert info.well_formed is None and info.truncated == 1
    info, *_ = extract("<a>text</a>", partial=True)
    assert info.well_formed == 1 and info.truncated == 1


HOSTILE = {
    "long name, no >": lambda n: "<" + "a" * n,
    "long attributes, no >": lambda n: "<a " + "x" * n,
    "many tags, no >": lambda n: "<a " * (n // 3),
    "unterminated attribute quotes": lambda n: "<a " + "<a x='" * (n // 6),
    "unterminated comment": lambda n: "<!--" + "-" * n,
    "many comment starts": lambda n: "<!--" * (n // 4),
    "unterminated PI": lambda n: "<?" + "x" * n,
    "unterminated CDATA": lambda n: "<a><![CDATA[" + "x" * n,
    "deep nesting": lambda n: "<a>" * (n // 3),
    "close tags only": lambda n: "</a>" * (n // 4),
}


def _best_time(text: str) -> float:
    best = float("inf")
    for _ in range(3):
        t = time.perf_counter()
        check_xml(text, 0, len(text))
        best = min(best, time.perf_counter() - t)
    return best


@pytest.mark.parametrize("shape", sorted(HOSTILE))
def test_check_xml_cost_grows_linearly_on_hostile_input(shape):
    small, large = HOSTILE[shape](50_000), HOSTILE[shape](400_000)  # 8x the input
    assert check_xml(small, 0, len(small))[0] is False or shape in ("deep nesting",)
    t_small, t_large = _best_time(small), _best_time(large)
    assert t_large <= 20 * max(t_small, 2e-4), f"{shape}: {t_small:.5f}s -> {t_large:.5f}s"


def test_large_payload_is_extracted_whole():
    items = "".join(f"  <i>{n}</i>\n" for n in range(20000))
    text = f"<root>\n{items}</root>"
    info, msg, cont = extract(text + ".")
    assert info.char_len == len(text) and info.well_formed == 1
    assert slice_of(info, msg, cont) == text


def test_extract_never_raises():
    class Bad:
        fmt = "xml"
        start = "nonsense"
        strip_suffix = None
        role = "by_root"
        direction = None
        peer = None

    assert extract_payload(Bad(), "x", None, 0, False) is None  # type: ignore[arg-type]
    assert extract_payload(spec(), None, None, 0, False) is None  # type: ignore[arg-type]


# --------------------------------------------------------------------------- the writer


TOML = r"""
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
code = "TST-CNT"
class = "other"
regex = '''^Content being sent to (?P<target>CRS|WMS):$'''
payload = { fmt = "xml", start = "first_cont_lt", role = "content", direction = "out", peer = "CRS" }

[[template]]
code = "TST-PLAIN"
class = "other"
text = "plain record"
"""


def make_log() -> bytes:
    lines = [
        mf.header(0, "INFO", "Message content: " + "<ChangedThings>"),
        f"  <Header><MessageID>{G1}</MessageID></Header>",
        "</ChangedThings>.",
        mf.header(10, "INFO", "Content being sent to CRS:"),
        "<ArrayOfX>",
        "  <X/>",
        "</ArrayOfX>",
        mf.header(20, "INFO", "plain record"),
        "<not-a-payload/>",
        mf.header(30, "INFO", "Content being sent to WMS:"),
        "no xml here",
        mf.header(40, "INFO", "Message content: <ThingsResponseMessage>"),
        "  <Header/>",
        "</ThingsResponseMessage>.",
        mf.header(50, "INFO", "unclassified record <a/>"),
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


def test_ingest_writes_payload_rows_and_sets_the_flag(tmp_path, db):
    result = ingest(db, tmp_path)
    assert result.sources_failed == 0 and result.payloads == 3
    rows = db.execute(
        "SELECT e.line_no, e.flags, p.role, p.direction, p.peer, p.operation, p.msg_id, p.fmt, p.root,"
        " p.char_off, p.char_len, p.well_formed, p.truncated, e.msg, b.cont"
        " FROM payload p JOIN entry e ON e.id = p.entry_id LEFT JOIN entry_body b ON b.entry_id = e.id"
        " ORDER BY e.id"
    ).fetchall()
    assert [r[2:4] for r in rows] == [("request", "in"), ("content", "out"), ("response", "out")]
    assert [r[5] for r in rows] == ["ChangedThings", "ArrayOfX", "ThingsResponseMessage"]
    assert rows[0][6] == G1 and rows[1][6] is None
    assert rows[0][4] == "WFM" and rows[1][4] == "CRS"
    for row in rows:
        assert row[1] & FLAG_HAS_PAYLOAD
    for r in rows:
        text = r[13] + ("\n" + r[14] if r[14] is not None else "")
        piece = text[r[9] : r[9] + r[10]]
        assert piece.startswith("<") and piece.endswith(">")
        assert r[11] == 1 and r[12] == 0
    # records without a payload (or where the template's payload is absent) have no flag and no row
    flagged = db.execute("SELECT count(*) FROM entry WHERE flags & 64").fetchone()[0]
    assert flagged == 3 == db.execute("SELECT count(*) FROM payload").fetchone()[0]
    no_xml = db.execute("SELECT flags FROM entry WHERE msg = 'Content being sent to WMS:'").fetchone()[0]
    assert not no_xml & FLAG_HAS_PAYLOAD


def test_reindex_and_resume_delete_the_payload_rows_of_removed_entries(tmp_path, db):
    ingest(db, tmp_path)
    first = db.execute("SELECT id FROM payload ORDER BY id").fetchall()
    # a link target and a pair reference to a payload that will go away
    db.execute(
        "UPDATE payload SET pair_id = (SELECT max(id) FROM payload) WHERE id = (SELECT min(id) FROM payload)"
    )
    db.commit()
    from dmslog.store.writer import IndexWriter

    w = IndexWriter(db)
    sid = db.execute("SELECT id FROM source LIMIT 1").fetchone()[0]
    last_payload_entry = db.execute("SELECT max(entry_id) FROM payload").fetchone()[0]
    byte_off = db.execute("SELECT byte_off FROM entry WHERE id = ?", (last_payload_entry,)).fetchone()[0]
    w.truncate_from(sid, byte_off)
    w.commit()
    left = db.execute("SELECT id FROM payload ORDER BY id").fetchall()
    assert left == first[:-1]
    assert db.execute("SELECT pair_id FROM payload WHERE id = ?", (first[0][0],)).fetchone() == (None,)
    assert db.execute(
        "SELECT count(*) FROM payload WHERE entry_id NOT IN (SELECT id FROM entry)"
    ).fetchone() == (0,)
    w.delete_source_entries(sid)
    w.commit()
    assert db.execute("SELECT count(*) FROM payload").fetchone() == (0,)


def test_reingest_is_idempotent_and_same_rows(tmp_path, db):
    ingest(db, tmp_path)
    before = db.execute("SELECT * FROM payload ORDER BY id").fetchall()
    result = ingest(db, tmp_path)
    assert result.payloads == 0
    assert db.execute("SELECT * FROM payload ORDER BY id").fetchall() == before


def test_payload_row_is_json_free_of_text_copy(tmp_path, db):
    ingest(db, tmp_path)
    cols = [r[1] for r in db.execute("PRAGMA table_info(payload)")]
    assert "text" not in cols and "body" not in cols
    assert json.dumps(cols)  # offsets only: the text lives in entry/entry_body


# ----------------------------------------------------------------------- cases written before the payloads


def test_old_case_is_reextracted_once(tmp_path):
    from dmslog.store import db as store_db

    (tmp_path / "old").mkdir()
    conn = store_db.connect(tmp_path / "old" / "case.sqlite")
    store_db.MIGRATIONS[1](conn)
    store_db.MIGRATIONS[2](conn)
    conn.execute("PRAGMA user_version=2")  # a database as M5 left it
    first = ingest(conn, tmp_path)
    assert first.payloads == 3
    # what M5 would have: records flagged, nothing in the payload table, no extraction version
    conn.execute("DELETE FROM payload")
    conn.execute("DELETE FROM meta WHERE key LIKE 'payload_extraction_version%' OR key LIKE 'payloads.%'")
    conn.commit()
    assert conn.execute("SELECT count(*) FROM entry WHERE flags & 64").fetchone()[0] == 3
    assert store_db.migrate(conn) == store_db.SCHEMA_VERSION

    second = ingest(conn, tmp_path)
    assert second.sources_failed == 0 and second.payloads == 3
    assert conn.execute("SELECT count(*) FROM payload").fetchone() == (3,)
    assert conn.execute("SELECT value FROM meta WHERE key = 'payload_extraction_version'").fetchone() == (
        "1",
    )
    rows = conn.execute("SELECT count(*) FROM payload p JOIN entry e ON e.id = p.entry_id").fetchone()
    assert rows == (3,)

    changes = conn.total_changes
    third = ingest(conn, tmp_path)
    assert third.payloads == 0 and third.sources_indexed == 0 and conn.total_changes == changes
    conn.close()


def test_fresh_case_stores_the_extraction_version_without_forcing_anything(tmp_path, db):
    result = ingest(db, tmp_path)
    assert result.sources_indexed >= 1
    assert db.execute("SELECT value FROM meta WHERE key = 'payload_extraction_version'").fetchone() == ("1",)


def test_newer_extraction_version_reindexes_once(tmp_path, db, monkeypatch):
    from dmslog.ingest import pipeline

    monkeypatch.setattr(pipeline, "PAYLOAD_EXTRACTION_VERSION", 2)
    ingest(db, tmp_path)
    assert db.execute("SELECT value FROM meta WHERE key = 'payload_extraction_version'").fetchone() == ("2",)
    # a newer extraction version re-indexes the sources once
    monkeypatch.setattr(pipeline, "PAYLOAD_EXTRACTION_VERSION", 3)
    again = ingest(db, tmp_path)
    assert again.sources_indexed >= 1 and again.payloads == 3
    assert db.execute("SELECT value FROM meta WHERE key = 'payload_extraction_version'").fetchone() == ("3",)


# --------------------------------------------------------------------------- syntax edge cases vs expat


def _expat_accepts(text: str) -> bool:
    import xml.etree.ElementTree as ET

    try:
        ET.fromstring(text)
    except ET.ParseError:
        return False
    return True


ACCEPTED = [
    "<a/>",
    "<a></a>",
    "<a />",
    "<a x='1' y=\"2\"/>",
    "<a x = '1' ></a >",
    "<a><b/><c>t</c></a>",
    "<?xml version='1.0'?><a/>",
    "<a><?pi x ?></a>",
    "<a><!-- c --></a>",
    "<a><!----></a>",
    "<a><![CDATA[ <x> ]]></a>",
    "<a>\ntext\n</a>",
    "<_a:b xmlns:_a='u'><_a:c/></_a:b>",
]
REJECTED = [
    "<",
    "</",
    "< a></a>",
    "<a></ a>",
    "<a/ >",
    "<a b></a>",
    "<a b=1></a>",
    "<a b='1' c></a>",
    "<a b='1'c='2'/>",
    "<>",
    "</>",
    "<1a/>",
    "<a></a",
    "<a><!-- x ->",
    "<a><![CDATA[ x </a>",
    "<a><?pi </a>",
    "<a></b>",
    "<a>",
    "</a>",
]


@pytest.mark.parametrize("text", ACCEPTED)
def test_check_xml_accepts_what_expat_accepts(text):
    assert _expat_accepts(text)
    assert check_xml(text, 0, len(text))[0] is True


@pytest.mark.parametrize("text", REJECTED)
def test_check_xml_rejects_bad_tag_syntax_expat_rejects(text):
    assert not _expat_accepts(text)
    assert check_xml(text, 0, len(text))[0] is False


def test_extraction_version_is_tracked_per_source(tmp_path, db):
    nmc = f"{tp.LOGDIR}/NMC.log"
    root = tp.write_folder(
        tmp_path / "Log",
        {tp.WFM: make_log(), nmc: mf.render([mf.header(0, "INFO", "Message content: <a/>.")], eol="\r\n")},
    )
    catalog = load_texts({"awfm.toml": TOML})
    everything = IngestOptions(catalog=catalog, scope="all")
    default = IngestOptions(catalog=catalog)
    ingest_all = run_ingest(db, root, everything)
    sources = dict(db.execute("SELECT member, id FROM source WHERE n_entries > 0"))
    assert len(sources) == 2 and ingest_all.sources_failed == 0
    wfm_id = next(i for m, i in sources.items() if m.endswith("WfmAdapter_1111.log"))
    other_id = next(i for m, i in sources.items() if m.endswith("NMC.log"))

    def old_state():
        db.execute("DELETE FROM payload")
        db.execute("DELETE FROM meta WHERE key LIKE 'payload_extraction_version%' OR key LIKE 'payloads.%'")
        db.commit()

    def marks():
        return {
            key: value
            for key, value in db.execute(
                "SELECT key, value FROM meta WHERE key LIKE 'payload_extraction_version%'"
            )
        }

    old_state()
    first = run_ingest(db, root, default)  # only the Wfm log is in the default scope
    assert first.sources_selected == 1 and first.sources_indexed == 1
    assert marks() == {f"payload_extraction_version.{wfm_id}": "1"}  # the other source is NOT marked
    assert db.execute("SELECT count(*) FROM payload").fetchone() == (3,)

    changes = db.total_changes
    again = run_ingest(db, root, default)  # nothing to do, and the global marker is still absent
    assert again.sources_indexed == 0 and db.total_changes == changes
    assert "payload_extraction_version" not in marks()

    later = run_ingest(db, root, everything)  # selecting the other source upgrades it, and only it
    assert later.sources_indexed == 1 and later.sources_unchanged == 1
    assert marks() == {
        "payload_extraction_version": "1",
        f"payload_extraction_version.{wfm_id}": "1",
        f"payload_extraction_version.{other_id}": "1",
    }
    assert db.execute("SELECT count(*) FROM payload").fetchone() == (3,)  # NMC has no payload template
    changes = db.total_changes
    assert run_ingest(db, root, everything).sources_indexed == 0 and db.total_changes == changes
