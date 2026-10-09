"""Per-family template fixtures (M2 task 2.6): coverage, determinism, leak scan and ingest counts.

The fixtures in ``tests/fixtures/families`` are generated from each template's own examples by
``tools/make_family_fixtures.py``. They must exercise every template of the packaged catalog.
Severity values are deliberately not asserted here.
"""

import importlib.util
import ipaddress
import json
import re
import sys
from collections import Counter
from pathlib import Path

import pytest

from dmslog.ingest.pipeline import IngestOptions, run_ingest
from dmslog.parse.templates import load_catalog
from dmslog.store import create_case_db

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "families"

_spec = importlib.util.spec_from_file_location(
    "make_family_fixtures", ROOT / "tools" / "make_family_fixtures.py"
)
mff = importlib.util.module_from_spec(_spec)
sys.modules["make_family_fixtures"] = mff
_spec.loader.exec_module(mff)

GUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
IPV4_RE = re.compile(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w.])")
LONG_DIGITS_RE = re.compile(r"\d{9,}")
HEADER_RE = re.compile(rb"^(?:\xef\xbb\xbf)?\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} ")
ALLOWED_NETS = [
    ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "127.0.0.0/8")
]
LOGS = sorted(rel for rel in mff.build_all() if rel.endswith(".log"))


@pytest.fixture(scope="module")
def catalog():
    return load_catalog()


@pytest.fixture(scope="module")
def ingested(tmp_path_factory):
    conn = create_case_db(tmp_path_factory.mktemp("families") / "case.sqlite")
    result = run_ingest(conn, FIXTURE_DIR, IngestOptions(scope="all"))
    yield conn, result
    conn.close()


def _rows(conn):
    """Entries of the fixture logs (the .gitattributes source is not a log: UNKNOWN family)."""
    return conn.execute(
        "SELECT e.id, e.tpl_id, e.msg, e.fields, s.family, s.sensitive, so.member FROM entry e "
        "JOIN source so ON so.id = e.source_id JOIN stream s ON s.id = so.stream_id "
        "WHERE so.member LIKE '%.log' ORDER BY e.id"
    ).fetchall()


# ---------------------------------------------------------------- generator


def test_fixtures_match_generator_bit_for_bit():
    assert mff.check_all(FIXTURE_DIR) == []


def test_generator_is_deterministic():
    assert mff.build_all() == mff.build_all()


def test_gitattributes_disables_text_conversion():
    assert (FIXTURE_DIR / ".gitattributes").read_bytes().strip() == b"* -text"


def test_file_shapes():
    files = mff.build_all()
    assert any(".sensitive." in rel for rel in LOGS) and any(".sensitive." not in rel for rel in LOGS)
    for rel in LOGS:
        data = files[rel]
        assert data.startswith(mff._mf.BOM) and data.endswith(b"\r\n"), rel
        assert b"\n" not in data.replace(b"\r\n", b""), f"bare LF in {rel}"
        n = sum(1 for line in data.split(b"\r\n") if HEADER_RE.match(line))
        assert n <= mff.MAX_RECORDS, rel
        if "AWDI" not in rel and "WDIAdapter" not in rel:
            assert n >= mff.MIN_RECORDS, f"{rel}: {n} records"


@pytest.mark.parametrize("rel", LOGS)
def test_no_real_data_patterns(rel):
    text = mff.build_all()[rel].decode("utf-8")
    for guid in GUID_RE.findall(text):
        assert guid.startswith(mff._mf.GUID_PREFIX), f"non synthetic GUID {guid}"
    assert not EMAIL_RE.findall(text), "e-mail address found"
    assert not LONG_DIGITS_RE.findall(text), "long digit sequence found"
    for match in IPV4_RE.finditer(text):
        octets = [int(g) for g in match.groups()]
        if all(o <= 255 for o in octets):
            addr = ipaddress.ip_address(".".join(map(str, octets)))
            assert any(addr in net for net in ALLOWED_NETS), f"non reserved IP {addr}"


def test_sanitize_neutralizes_leaks():
    out = mff.sanitize("id 11111111-2222-4333-8444-555555555555 n 1234567890 a@b.com")
    assert not GUID_RE.findall(out.replace(mff._mf.GUID_PREFIX, "X"))
    assert not LONG_DIGITS_RE.findall(out) and not EMAIL_RE.findall(out)
    assert mff.sanitize("no leak here 12345") == "no leak here 12345"


# ---------------------------------------------------------------- end-to-end ingest


def test_all_logs_ingested_in_their_family(ingested):
    conn, result = ingested
    assert result.sources_failed == 0
    got = {
        row[0]: (row[1], row[2])
        for row in conn.execute(
            "SELECT so.member, s.family, s.sensitive FROM source so JOIN stream s ON s.id = so.stream_id "
            "WHERE so.member LIKE '%.log'"
        )
    }
    assert set(got) == set(LOGS)
    by_process = {proc: fam for fam, (_, proc) in mff.PROCESSES.items()}
    for rel, (family, sensitive) in got.items():
        name = rel.rsplit("/", 1)[-1]
        proc = re.split(r"[._]", name)[0]
        assert family == mff.EXPECTED_FAMILY[by_process[proc]], rel
        assert sensitive == (1 if ".sensitive." in name else 0), rel


def test_record_counts(ingested):
    conn, _ = ingested
    files = mff.build_all()
    expected = {rel: sum(1 for line in files[rel].split(b"\r\n") if HEADER_RE.match(line)) for rel in LOGS}
    got = Counter(row[6] for row in _rows(conn))
    assert dict(got) == expected


def test_every_record_has_a_template_except_the_intentional_unknowns(ingested, catalog):
    conn, _ = ingested
    allowed = set(mff.UNKNOWN_TEXTS)
    for t in catalog.templates():
        allowed.update(mff.sanitize(ex.msg) for ex in t.counter_examples)
    unknown = [r for r in _rows(conn) if r[1] == 0]
    assert unknown, "the fixtures must keep some unknown records"
    stray = [(r[6], r[2]) for r in unknown if r[2] not in allowed]
    assert stray == []
    # every stream with templates also carries at least one intentionally unknown record
    streams = {r[6] for r in _rows(conn)}
    assert {r[6] for r in unknown} == streams
    assert sum(1 for r in _rows(conn) if r[1] != 0) > 500
    for text in mff.UNKNOWN_TEXTS:
        assert sum(1 for r in unknown if r[2] == text) >= len(streams) // 2


def test_false_positive_looking_lines_stay_unknown(ingested, catalog):
    conn, _ = ingested
    counter_msgs = {mff.sanitize(ex.msg) for t in catalog.templates() for ex in t.counter_examples}
    # a counter-example of one template can be a positive example of another one
    counter_msgs -= {mff.sanitize(ex.msg) for t in catalog.templates() for ex in t.examples}
    rows = [r for r in _rows(conn) if r[2] in counter_msgs]
    assert rows, "expected counter-example lines in the fixtures"
    assert all(r[1] == 0 for r in rows)


def test_every_template_of_the_catalog_is_hit(ingested, catalog):
    conn, _ = ingested
    hit = {
        row[0]
        for row in conn.execute("SELECT DISTINCT t.code FROM entry e JOIN template t ON t.id = e.tpl_id")
    }
    codes = {t.code for t in catalog.templates()}
    uncovered = sorted(codes - hit)
    assert uncovered == [], f"templates without a fixture record: {uncovered}"
    assert hit <= codes


def test_unknown_signatures_match_unknown_records(ingested):
    conn, _ = ingested
    unknown = [r for r in _rows(conn) if r[1] == 0]
    n_sig, total = conn.execute(
        "SELECT COUNT(*), SUM(n) FROM unknown_sig WHERE family <> 'UNKNOWN'"
    ).fetchone()
    assert n_sig > 0
    assert total == len(unknown)
    families = {
        r[0] for r in conn.execute("SELECT DISTINCT family FROM unknown_sig WHERE family <> 'UNKNOWN'")
    }
    assert families == {r[4] for r in unknown}


def _expected_key_rows(conn, catalog):
    """(entry_id, key_type, value) a keyed template must produce from the stored fields."""
    ids = {row[0]: row[1] for row in conn.execute("SELECT id, code FROM template")}
    out = set()
    for entry_id, tpl_id, _msg, fields, *_ in _rows(conn):
        if tpl_id == 0:
            continue
        template = catalog.template(ids[tpl_id])
        data = json.loads(fields) if fields else {}
        for spec in template.keys:
            value = data.get(spec.field)
            if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value) != "":
                out.add((entry_id, spec.type, str(value)))
    return out


def test_entry_keys_are_written_for_templates_that_declare_keys(ingested, catalog):
    conn, _ = ingested
    keyed = [t for t in catalog.templates() if t.keys]
    assert keyed
    expected = _expected_key_rows(conn, catalog)
    assert expected, "no keyed record in the fixtures"
    got = set(conn.execute("SELECT entry_id, key_type, value FROM entry_key"))
    missing = sorted(expected - got)
    assert missing == [], f"keys missing in entry_key: {missing[:5]}"
    ids = {row[0]: row[1] for row in conn.execute("SELECT id, code FROM template")}
    rows = _rows(conn)
    by_entry = {r[0]: ids.get(r[1]) for r in rows}
    keyed_hit = {by_entry[e] for e, _, _ in expected}
    assert len(keyed_hit) >= len(keyed) * 0.8, "most keyed templates should produce keys"
    assert {k for _, k, _ in got} >= {s.type for t in keyed for s in t.keys if s.type in {"msgid"}}


# ---------------------------------------------------------------- payload scenarios (tasks 6.2 and 6.4)

SCENARIO_PROCESS = {"AWFM": "WfmAdapter", "AONT": "OntAdapter", "ASMN": "SmnAdapter"}


def _scenario_stream(conn, catalog, family, sensitive):
    """Entries of the scenario block of one stream: (id, ts, tid, flags, code, msg, cont), file order."""
    n = len(mff.scenario_records(catalog, family, sensitive))
    assert n > 0
    rows = conn.execute(
        "SELECT e.id, e.ts, e.tid, e.flags, COALESCE(t.code, ''), e.msg, COALESCE(b.cont, '') "
        "FROM entry e JOIN source so ON so.id = e.source_id JOIN stream s ON s.id = so.stream_id "
        "LEFT JOIN template t ON t.id = e.tpl_id LEFT JOIN entry_body b ON b.entry_id = e.id "
        "WHERE so.member LIKE ? AND s.sensitive = ? ORDER BY e.id",
        (f"%{SCENARIO_PROCESS[family]}_%", 1 if sensitive else 0),
    ).fetchall()
    return rows[-n:]


def test_scenarios_are_classified_as_intended(ingested, catalog):
    conn, _ = ingested
    for family in mff.SCENARIO_FAMILIES:
        for sensitive in (False, True):
            recs = mff.scenario_records(catalog, family, sensitive)
            rows = _scenario_stream(conn, catalog, family, sensitive)
            assert [r[4] for r in rows] == [r.code for r in recs], (family, sensitive)


def test_scenarios_cover_the_documented_cases(catalog):
    files = mff.build_all()
    text = {rel: data.decode("utf-8") for rel, data in files.items() if rel.endswith(".log")}
    sens = "".join(t for rel, t in text.items() if ".sensitive." in rel)
    normal = "".join(t for rel, t in text.items() if ".sensitive." not in rel)
    # payload closed by ">." and elements with attributes on one line
    assert "</ChangedCrews>.\r\n" in sens and "</s:Fault>.\r\n" in sens
    # text with line breaks inside an element, XML escapes
    assert "segunda linha do coment\u00e1rio\r\n" in sens and "&amp; manuten\u00e7\u00e3o" in sens
    assert "Line one&#xD;&#xA;line two" in sens
    # WMS content in one line, HTML error body, delta, IMS in the sensitive log
    wms = '<ArrayOfDocumentCrewAssigment xmlns:xsd="http://www.w3.org/2001/XMLSchema" />'
    assert f"Content being sent to WMS:\r\n{wms}" in normal
    assert "<h1>Service Unavailable</h1>" in sens and '<delta id="7">' in sens
    assert "Content being sent to IMS:\r\n<ArrayOfIncidentCrewAssigment" in sens
    # announcements with and without MessageID
    assert "Message with MessageID: ''" in normal and "Message with MessageID: 'SYNT_0000001'" in normal
    # PSAttach / arb_pubSubLookup lines without [TID=...]
    no_tid = [ln for ln in normal.split("\r\n") if " [PID=" in ln and "[TID=" not in ln]
    assert len(no_tid) >= 4 and all("PSAttach" in ln or "arb_pubSubLookup" in ln for ln in no_tid)


def test_psattach_without_tid_is_ingested_with_null_tid(ingested, catalog):
    conn, _ = ingested
    for family in mff.SCENARIO_FAMILIES:
        rows = _scenario_stream(conn, catalog, family, False)
        no_tid = [r for r in rows if r[2] is None]
        assert no_tid and all(r[4] in ("GEN-PSA-01", "GEN-PSA-02") for r in no_tid)


def test_smn_sensitive_timestamp_goes_back_two_seconds(ingested, catalog):
    conn, _ = ingested
    rows = _scenario_stream(conn, catalog, "ASMN", True)
    back = [(a, b) for a, b in zip(rows, rows[1:], strict=False) if b[1] < a[1]]
    assert len(back) == 1
    a, b = back[0]
    assert a[1] - b[1] == 2000
    assert (a[4], b[4]) == ("SMN-S110", "SMN-S019")
    assert b[3] & 32  # flagged as out of order


def test_concurrent_operations_have_replies_without_ids(ingested, catalog):
    conn, _ = ingested
    normal = _scenario_stream(conn, catalog, "AONT", False)
    sens = _scenario_stream(conn, catalog, "AONT", True)
    empty = [r for r in normal if r[4] == "GEN-MSG-01" and "MessageID: ''" in r[5]]
    replies = [r for r in sens if r[5].startswith("Message: <ns1:IncidentsResponseMessage")]
    assert len(empty) == len(replies) == 2
    assert len({r[2] for r in empty}) == 2 and len({r[2] for r in replies}) == 2  # different threads
    assert not any("MessageID" in r[6] or "CorrelationID" in r[6] for r in replies)
    # both operations are open at once: two starts before the first end
    codes = [r[4] for r in normal]
    assert codes.index("ONT-010") < codes.index("ONT-012")
    assert codes.index("ONT-010", codes.index("ONT-010") + 1) < codes.index("ONT-012")


def _payload_text(msg: str, cont: str) -> str:
    """XML of a GEN-PLD-01 record: from the first `<` of the first line to the end, without the final dot."""
    text = msg[msg.index("<") :] + ("\n" + cont if cont else "")
    assert text.endswith(">.")
    return text[:-1]


def test_scenario_payloads_are_well_formed_xml(ingested, catalog):
    import xml.etree.ElementTree as ET

    conn, _ = ingested
    n = 0
    for family in mff.SCENARIO_FAMILIES:
        for r in _scenario_stream(conn, catalog, family, True):
            if r[4] == "GEN-PLD-01":
                ET.fromstring(_payload_text(r[5], r[6].replace("\r\n", "\n")))
                n += 1
            elif r[4] in ("WFM-S112", "WFM-S121"):
                ET.fromstring(r[5] + "\n" + r[6] if r[4] == "WFM-S112" else r[6])
                n += 1
            elif r[4] == "SMN-S110":
                body = "\n".join(ln for ln in r[6].split("\n") if ln.startswith(("<", " ")))
                ET.fromstring(f"<seq>{body}</seq>")
                n += 1
    assert n >= 14


def test_large_publication_record_is_not_cut(tmp_path):
    data, cont_chars = mff.large_publication_log()
    assert cont_chars > 65_536
    logs = tmp_path / "logs"
    folder = logs / mff.FOLDER
    folder.mkdir(parents=True)
    (folder / "SmnAdapter_4242.sensitive.log").write_bytes(data)
    conn = create_case_db(tmp_path / "case.sqlite")
    try:
        result = run_ingest(conn, logs, IngestOptions(scope="all"))
        assert result.sources_failed == 0
        rows = conn.execute(
            "SELECT t.code, length(COALESCE(b.cont, '')), e.nlines FROM entry e "
            "LEFT JOIN template t ON t.id = e.tpl_id "
            "LEFT JOIN entry_body b ON b.entry_id = e.id ORDER BY e.id"
        ).fetchall()
    finally:
        conn.close()
    assert [r[0] for r in rows] == ["GEN-PRX-S01", "SMN-S110", "GEN-PRX-S01"]
    assert rows[1][1] >= cont_chars  # the whole continuation is stored (CR/LF aside)
    assert rows[1][2] > 1000
