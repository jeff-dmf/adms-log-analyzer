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
