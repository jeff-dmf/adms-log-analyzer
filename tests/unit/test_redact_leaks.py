"""Systematic leak harness for the redaction (and the export built on it).

Secrets of every category are planted in many wrappers (plain text, quotes, labels, JSON, XML,
escapes...) and the output must not contain any plant, nor any fragment of six characters or more of
one. A fragment is searched in the text and in the text with every non-alphanumeric character removed,
so separators cannot hide a partial leak. The reverse is checked too: a category switched off lets its
plants through, and the exception list keeps its entries.

Every table runs in both profile modes: ``strict`` (the default: template-aware, fail-closed nets) and
``standard`` (pattern rules only). Review round 3 added its variants (spans reached by a token
extension, structured fields under automatic exceptions, quoted values longer than 4,096 characters,
blank runs inside names, quoted/grouped GIDs, XML inside JSON strings and JSON inside XML text).

Known limits (what no rule can know; these must fail CLOSED where a hint exists):
* standard mode: a person or user name in free text with no label, no mapped payload field and no
  profile term ("Crew Ana Silva arrived", "failed for user jsilva") is not recognized; a lowercase name
  after a label keeps its second word;
* strict mode is an allowlist (review round 4): free text keeps only catalog/common words, catalog
  names and codes, compounds of catalog words, dates/times and numbers with a unit; so a name that IS
  such a word ("Will", "Grant") with no label, field or term stays. Labelled values, template fields
  without proof and payload values are replaced whole even when they look public (catalog words,
  short numbers, initials, camelCase, punctuation only), see ``test_values_that_look_public...``;
* a bare host name without a known top-level domain, a label or a profile term is not recognized in
  standard mode (strict: it is not a catalog word, so it goes);
* strict payloads are default-deny (review round 5): only markup syntax, allowed names (digit-free
  catalog-word names), JSON literals in JSON value positions and blanks survive; the generative test
  ``test_no_secret_survives_any_structured_position_in_strict_mode`` plants secrets in every position
  of arbitrary, also malformed, XML/JSON, with any Unicode blank, unquoted values with stray quotes or
  entities, invalid names around a public-looking word, and closing tags hidden in data (comments,
  CDATA, PIs, attributes, JSON strings, quoted text) that must not pair a word in brackets (rounds 6
  to 8). Names made only of catalog words stay where they are structural; an odd quote in plain text
  turns the following tags into data (safe, less readable). Round 9: a fail-closed gate validates
  every region before any skeleton is kept; a public-looking word may survive only as a name of a
  region that an independent XML parser (expat) also accepts. A malformed or truncated region is one
  value up to the end of the text (payloads cut by the log keep no skeleton). Round 10: expat is the
  authority on XML; arrays starting with numbers/literals, strings and quoted text holding markup,
  duplicate attributes, undefined entities and PI/comment edge cases are generated too. Round 11:
  single-quoted and escaped-quote strings (closed or not), JSON inside XML text nodes (valid or not)
  and runs of failed JSON candidates;
* an unclosed quote or bracket after a label redacts up to the end
  of the text; an element nested deeper than 200 levels, or a JSON value deeper than 200, redacts the
  rest of the payload; formats nested more than 3 levels deep (JSON in XML in JSON in XML) only get
  the field rules down to the third level (strict mode: the payload allowlist has no depth).
"""

from __future__ import annotations

import html
import importlib.util
import os
import re
import sys
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

import pytest
from hypothesis import HealthCheck, example, given, seed, settings
from hypothesis import strategies as st

from dmslog.report import export
from dmslog.report import redact as redact_module
from dmslog.report.export import ExportOptions, Selection, build_report
from dmslog.report.redact import CATEGORIES, Profile, Redactor
from dmslog.store.db import connect

ROOT = Path(__file__).resolve().parents[2]

# One settings profile for every generative test here, so that CI cannot flake them: no deadline, no
# health checks, derandomized and WITHOUT an example database (a run draws the same examples on every
# machine, fresh clone or not). Derandomization seeds from the test's source, so editing a test (or
# renaming a word in it) draws other examples: before relying on a change, explore with
# DMSLOG_HYPOTHESIS_SEED=<n> (random seed n instead) and DMSLOG_HYPOTHESIS_SCALE=<k> (k times the
# examples), with several seeds; every counterexample found goes below as an @example.
settings.register_profile(
    "dmslog-redact",
    deadline=None,
    derandomize=True,
    database=None,
    suppress_health_check=list(HealthCheck),
)
_PROFILE = settings.get_profile("dmslog-redact")
_SEED = os.environ.get("DMSLOG_HYPOTHESIS_SEED")
_SCALE = max(1, int(os.environ.get("DMSLOG_HYPOTHESIS_SCALE", "1")))


def _generative(max_examples: int):
    """The shared profile with ``max_examples`` (times the scale), on the seed asked for if any."""

    def wrap(test):
        configured = settings(_PROFILE, max_examples=max_examples * _SCALE, derandomize=_SEED is None)(test)
        return seed(int(_SEED))(configured) if _SEED is not None else configured

    return wrap


_spec = importlib.util.spec_from_file_location(
    "export_case_leaks", ROOT / "tests/fixtures/export/build_case.py"
)
case_module = importlib.util.module_from_spec(_spec)
sys.modules["export_case_leaks"] = case_module
_spec.loader.exec_module(case_module)

CUSTOMER = "Zorbax9Grid"
SUBSTATION = "SUBQX7"
PROFILE = Profile(terms_word=(CUSTOMER, SUBSTATION))


@dataclass(frozen=True)
class Plant:
    category: str  # the redaction category that must hide it ("term" for user terms)
    value: str
    standalone: bool  # recognized by its own shape; otherwise only behind a label or in a mapped field


PLANTS = [
    Plant("guid", "3f2a9c1e-7b4d-4e8a-9c21-5d6e7f8a9b0c", True),
    Plant("guid", "A1B2C3D4-0F0F-4AAA-8BBB-1234567890AB", True),
    Plant("guid", "9a8b7c6d5e4f30211223344556677889", True),
    Plant("ticket", "INC0045123", True),
    Plant("ticket", "inc0045123", True),
    Plant("ticket", "PROB0077331", True),
    Plant("ticket", "prob0077331", True),
    Plant("plate", "QRS4T56", True),
    Plant("plate", "qrs4t56", True),
    Plant("plate", "XQZ-8142", True),
    Plant("gid", "1234567890123456", False),
    Plant("gid", "123456 789012", False),  # round 3: a GID written in groups
    Plant("mrid", "CREWQ7788Z", False),
    Plant("host", "srvq7x9k2.corp.example.com", True),
    Plant("host", "DSPQ7X9K2N01", False),
    Plant("ip", "10.77.123.45", True),
    Plant("ip", "2001:db8:77aa::9f3c:42", True),
    Plant(
        "url", "https://api.zq4m8t1.example.com:8443/v1/items/R7T2P9?token=K5W3N8&x=%7Bq%7D#frag-Y6B1", True
    ),
    Plant("url", "HTTPS://Portal.ZQ4M8T1.example.com/Deep/Path/WithCase7K", True),
    Plant("path", "\\\\fsq7x9\\share\\dirq4\\file-w3.txt", True),
    Plant("path", "C:\\Users\\jq7x9k\\AppData\\Local\\dms-r4t2\\x.log", True),
    Plant("path", "/var/lib/dms-q7x9/spool/f3k2.dat", True),
    Plant("email", "maria.q7x9k@zq4m8t1.example.com", True),
    Plant("phone", "+55 21 99876-5432", True),
    Plant("phone", "(21) 98765-4321", True),
    Plant("user", "jsilvaq7x9", False),
    Plant("user", "Ana SecretSurnameQ", False),
    Plant("user", "Ana  SecretSurnameQ", False),  # round 3: a run of blanks inside the name
    Plant("user", "Bea da SecretSurnameQ", False),  # a lowercase particle inside the name
    Plant("person", "Marcelo Quintanilha-Zorzo", False),
    Plant("person", "Rua Das Acacias 45 Bloco Q7", False),
    Plant("term", CUSTOMER, True),
    Plant("term", SUBSTATION, True),
]

# -- how a value is written ---------------------------------------------------------------------


def _xml_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;")


def _entities(value: str) -> str:
    return "".join(f"&#{ord(c)};" for c in value)


def _uescape(value: str) -> str:
    return "".join(f"\\u{ord(c):04x}" for c in value)


def _slashes(value: str) -> str:
    return value.replace("/", "\\/").replace("\\", "\\\\") if "\\" in value else value.replace("/", "\\/")


def _json_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("/", "\\/")


TRANSFORMS = {
    "plain": lambda v: v,
    "percent": lambda v: quote(v, safe=""),
    "percent2": lambda v: quote(quote(v, safe=""), safe=""),
    "uescape": _uescape,
    "entities": _entities,
}

# wrappers any standalone plant can sit in: (name, template with {s}); {s} is the written value
GENERIC = {
    "text": "{s}",
    "sentence": "the value {s} was seen",
    "dq": '"{s}"',
    "sq": "'{s}'",
    "paren": "({s})",
    "equals": "value={s}",
    "newline": "first line\n{s}\nlast line",
    "json_string": '{{"key": "{s}"}}',
    "json_array": '["{s}", "other"]',
    "json_nested": '{{"a": {{"b": [{{"c": "{s}"}}]}}}}',
    "xml_text": "<x><y>{s}</y></x>",
    "xml_attr": '<x a="{s}" b="1"/>',
    "cdata": "<x><![CDATA[{s}]]></x>",
    "comment": "<x><!-- {s} --></x>",
}
# wrappers that write the value as the format would (escaped), used with the "plain" transform only
FORMAT_ESCAPED = {
    "json_string_escaped": lambda v: '{"key": "' + _json_string(v) + '"}',
    "json_slashes": lambda v: '{"url": "' + v.replace("/", "\\/") + '", "z": 1}',
    "xml_text_escaped": lambda v: "<x>" + _xml_text(v) + "</x>",
}
# wrappers for values that need a label or a mapped field to be recognized: category -> templates
LABELLED = {
    "gid": [
        "GID: {s}",
        'GID: "{s}"',  # round 3: quoted
        "GID=[{s}]",
        "gid = {s}",
        "GID\n:\n{s}",
        '{{"GID": {s}}}',
        '{{"gid": "{s}"}}',
        "<GID>{s}</GID>",
        '<x gid="{s}"/>',
    ],  # fmt: skip
    "mrid": [
        "mRID: {s}",
        "mrid = '{s}'",
        "mRID:\n{s}",
        "<mRID><Value>{s}</Value></mRID>",
        '{{"mRID": "{s}"}}',
        '{{"mRID": {{"v": ["{s}"]}}}}',
    ],  # fmt: skip
    "host": ["host: {s}", 'hostname="{s}"', "server=\n{s}", "<Host>{s}</Host>", '{{"Server": "{s}"}}'],
    "user": [
        "user: {s}",
        'user: "{s}"',
        "/a/b=user:{s}",  # round 3: the path extension reaches the start of the name
        'user: "x\n' + "y" * 4200 + '{s}"',  # round 3: closed after more than 4,096 characters
        "user: [{s}]",
        '{{"body":"<User>{s}</User>"}}',  # round 3: XML inside a JSON string
        '<Data>{{"user":"{s}"}}</Data>',  # round 3: JSON inside XML text
        '{{"body":"{{\\"user\\":\\"{s}\\"}}"}}',  # JSON inside a JSON string
        "<Data>&lt;User&gt;{s}&lt;/User&gt;</Data>",  # escaped XML inside XML text
        "Username='{s}'",
        "user:\n{s}",
        "login = {s}",
        "<UserName>{s}</UserName>",
        '{{"user": "{s}"}}',
        '{{"account": ["{s}"]}}',
        "<u user='{s}'/>",
    ],  # fmt: skip
    "person": [
        "Address: {s}",
        "customer name: {s}",
        'contact = "{s}"',
        "<Name>{s}</Name>",
        "<Customer><Name><First>{s}</First></Name></Customer>",
        '{{"Name": "{s}"}}',
        '{{"Name": ["{s}", "x"]}}',
        '<Addr Street="{s}"/>',
        "<Address>{s}</Address>",
    ],  # fmt: skip
}
# a few categories also appear behind labels / in mapped fields although they have their own shape
SHAPED_IN_FIELDS = {
    "guid": ["<MessageID>{s}</MessageID>"],
    "ticket": [
        "<Incident>{s}</Incident>",
        '{{"Incident": "{s}"}}',
        "<IncidentRecord><mRID>{s}</mRID></IncidentRecord>",
    ],
    "plate": ["<Plate>{s}</Plate>", '{{"plate": "{s}"}}'],
    "email": ["<Email>{s}</Email>", '{{"email": ["{s}"]}}'],
    "phone": ["<Phone>{s}</Phone>", '{{"Phone": "{s}"}}', "Phone: {s}"],
    "ip": ["<IPAddress>{s}</IPAddress>", '{{"ip": "{s}"}}'],
    "url": ["<Url>{s}</Url>", '{{"url": "{s}"}}'],
}  # fmt: skip


def cases():
    """(id, plant, text) for every plant in every wrapper that can hold it."""
    for plant in PLANTS:
        cat = plant.category
        for t_name, transform in TRANSFORMS.items():
            written = transform(plant.value)
            if plant.standalone:
                for w_name, template in GENERIC.items():
                    if t_name != "plain" and w_name not in (
                        "text",
                        "dq",
                        "json_string",
                        "xml_text",
                        "newline",
                    ):
                        continue
                    yield f"{cat}/{plant.value[:12]}/{t_name}/{w_name}", plant, template.format(s=written)
            for template in LABELLED.get(cat, []) + SHAPED_IN_FIELDS.get(cat, []):
                if t_name != "plain" and not template.startswith(
                    ("GID", "gid", "mRID", "user", "host", "Address")
                ):
                    continue
                yield f"{cat}/{plant.value[:12]}/{t_name}/{template[:18]}", plant, template.format(s=written)
        if plant.standalone:
            for w_name, build in FORMAT_ESCAPED.items():
                yield f"{cat}/{plant.value[:12]}/{w_name}", plant, build(plant.value)


CASES = list(cases())


def fragments(value: str, size: int = 6) -> set[str]:
    """Every run of ``size`` alphanumeric characters of the value (also across its separators)."""
    joined = "".join(re.findall(r"[A-Za-z0-9]+", value)).casefold()
    return {joined[i : i + size] for i in range(max(0, len(joined) - size + 1))}


def squeeze(text: str) -> str:
    return "".join(re.findall(r"[A-Za-z0-9]+", text)).casefold()


def leaked(output: str, plant: Plant, size: int = 6) -> list[str]:
    """Fragments of the plant found in the output."""
    flat = squeeze(output)
    return [f for f in fragments(plant.value, size) if f in flat]


MODES = ("strict", "standard")
PROFILES = {mode: replace(PROFILE, mode=mode) for mode in MODES}


def redact(text: str, profile: Profile = PROFILE, payload: bool = True) -> str:
    return Redactor(profile).redact(text, payload=payload)


# -- the table: every plant in every wrapper -----------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("plant,text", [(p, t) for _i, p, t in CASES], ids=[i for i, _p, _t in CASES])
def test_no_plant_survives(plant, text, mode):
    out = redact(text, PROFILES[mode])
    assert leaked(out, plant) == [], out
    if mode == "strict":  # strict runs the field rules on every text (record text, notes), not only payloads
        assert leaked(redact(text, PROFILES[mode], payload=False), plant) == []


def test_the_table_is_big_enough():
    assert len(CASES) > 700
    assert {p.category for p in PLANTS} >= set(CATEGORIES) | {"term"}


# -- hypothesis: plants x wrappers x padding x position -----------------------------------------

PADDING = st.text(alphabet="abc XYZ,.;:-_/\n\t\"'()[]{}<>=&%", max_size=24)


@_generative(600)
# declared limit (standard mode): a shape or a label glued to a letter or digit is another token
@example(
    case=next(c for c in CASES if c[0] == "guid/3f2a9c1e-7b4/plain/text"),
    before="",
    after="a",
    glue="",
    mode="standard",
)
@example(
    case=next(c for c in CASES if c[0] == "mrid/CREWQ7788Z/plain/mRID: {s}"),
    before="X",
    after="",
    glue="",
    mode="standard",
)
@example(
    case=next(c for c in CASES if c[0] == "user/Ana  SecretS/plain/user: {s}"),
    before="a",
    after="",
    glue="",
    mode="standard",
)
@given(
    case=st.sampled_from(CASES),
    before=PADDING,
    after=PADDING,
    glue=st.sampled_from(["", " ", "\n", ", ", " and "]),
    mode=st.sampled_from(MODES),
)
def test_no_plant_survives_in_any_position(case, before, after, glue, mode):
    _id, plant, text = case
    # the padding may open a quote or a tag: the plant must be hidden anyway, unless the padding
    # itself changes what the wrapper means (then only the plant's own recognizable shapes count)
    out = redact(before + glue + text + glue + after, PROFILES[mode])
    glued = (re.match(r"\w", after) and re.search(r"\w\Z", text)) or (
        re.search(r"\w\Z", before) and re.match(r"\w", text)
    )
    if mode == "standard" and not glue and glued:
        return  # declared limit (standard): a shape or label glued to a letter or digit is another token
    flat = squeeze(out)
    surviving = [f for f in fragments(plant.value) if f in flat]
    if surviving and plant.standalone and text.strip() == text and not _breaks_context(before, glue):
        pytest.fail(f"{_id}: {surviving} in {out!r}")
    if surviving and not plant.standalone and not _breaks_context(before, glue):
        pytest.fail(f"{_id}: {surviving} in {out!r}")


def _breaks_context(before: str, glue: str) -> bool:
    """Padding that turns a labelled/field wrapper into something else (a label cut by a quote, an
    element closed early...). Only a handful of the generated paddings do; they are skipped, the
    exhaustive table above has no padding at all."""
    return bool(re.search(r"[\"'<>\[\]{}&%/]", before)) or "\n" in before


# -- the reverse: switching a category off lets its plants through -------------------------------


@pytest.mark.parametrize(
    "plant", [p for p in PLANTS if p.standalone and p.category != "term"], ids=lambda p: p.value[:14]
)
def test_category_off_lets_the_plant_appear(plant):
    related = {"url": ("host", "path"), "path": ("host",), "email": ("host",)}
    off = {plant.category, *related.get(plant.category, ())}
    # standard mode: the strict nets would still hide parts of a value whose category is off
    profile = Profile(categories={c: c not in off for c in CATEGORIES}, payload_names=(), mode="standard")
    text = f"the value {plant.value} was seen"
    out = Redactor(profile).redact(text)
    assert plant.value in out
    on = Redactor(Profile(payload_names=())).redact(text)
    assert plant.value not in on


@pytest.mark.parametrize("plant", [p for p in PLANTS if p.standalone], ids=lambda p: p.value[:14])
def test_exception_entries_stay(plant):
    profile = Profile(exceptions=(plant.value,), terms_word=(CUSTOMER, SUBSTATION))
    out = Redactor(profile).redact(f"see {plant.value} now")
    assert plant.value in out


def test_user_exceptions_win_over_structured_fields_but_automatic_ones_do_not():
    value = "SendWorks"
    user = Redactor(Profile(exceptions=(value,))).redact("<Name>SendWorks</Name>", payload=True)
    auto = Redactor(Profile(), exceptions=[value]).redact("<Name>SendWorks</Name>", payload=True)
    assert value in user and value not in auto


# -- through the export: positions the redactor alone cannot see ------------------------------------

NOW = datetime(2030, 10, 15, 11, 0, 0)
EXPORT_SECRETS = [p for p in PLANTS if p.standalone] + [p for p in PLANTS if p.category in ("gid", "mrid")]


def edited(tmp_path, statements):
    db = case_module.build_export_case(tmp_path / f"leak-{uuid.uuid4().hex[:8]}.sqlite")
    rw = connect(db)
    with rw:
        for sql, params in statements:
            rw.execute(sql, params)
    rw.close()
    return connect(db, readonly=True)


def run_export(conn, selection, **options):
    options.setdefault("anonymize", True)
    result = build_report(conn, selection, ExportOptions(profile=PROFILE, **options), now=NOW)
    text = "".join(p.text for p in result.parts)
    return text + "\n".join(result.warnings) + repr(result.legend)


def wrapped(plant: Plant) -> str:
    if plant.category == "gid":
        return f"GID: {plant.value}"
    if plant.category == "mrid":
        return f"mRID: {plant.value}"
    return f"seen {plant.value} here"


POSITIONS = {
    "title": lambda p: (
        [("UPDATE meta SET value = ? WHERE key = 'name'", (wrapped(p),))],
        Selection(ids=(2,)),
        {},
    ),
    "title_cut": lambda p: (
        [
            (
                "UPDATE meta SET value = ? WHERE key = 'name'",
                ("x" * (export.MAX_LABEL_CHARS - 6) + " " + wrapped(p),),
            )
        ],
        Selection(ids=(2,)),
        {},
    ),
    "machine": lambda p: (
        [("UPDATE collection SET machine = ?", (p.value if p.category != "gid" else "m1",))],
        Selection(ids=(2,)),
        {},
    ),  # fmt: skip
    "payload_operation": lambda p: (
        [("UPDATE payload SET operation = ? WHERE entry_id = 7", (wrapped(p),))],
        Selection(ids=(7,)),
        {"include_payloads": True},
    ),
    "payload_peer": lambda p: (
        [("UPDATE payload SET peer = ? WHERE entry_id = 7", (wrapped(p),))],
        Selection(ids=(7,)),
        {"include_payloads": True},
    ),
    "exception_message": lambda p: (
        [("UPDATE event SET detail = ? WHERE id = 1", ('{"exception": "System.Boom: ' + wrapped(p) + '"}',))],
        Selection(events=(1,)),
        {},
    ),
    "event_key": lambda p: (
        [("UPDATE event SET key = ? WHERE id = 1", (wrapped(p),))],
        Selection(events=(1,)),
        {},
    ),
    "event_name": lambda p: (
        [("UPDATE event SET name = ? WHERE id = 1", (wrapped(p),))],
        Selection(events=(1,)),
        {},
    ),
    "bookmark_note": lambda p: (
        [("UPDATE bookmark SET note = ?", (wrapped(p),))],
        Selection(events=(1,)),
        {},
    ),
    "file_name": lambda p: (
        [
            (
                "UPDATE source SET file_name = ? WHERE id = 1",
                (wrapped(p).replace("/", "_").replace("\\", "_"),),
            )
        ],
        Selection(ids=(2,)),
        {},
    ),
    "cut_20000": lambda p: (
        [("UPDATE entry SET msg = ? WHERE id = 2", ("w " * 9990 + wrapped(p) + " tail " + "z " * 3000,))],
        Selection(ids=(2,)),
        {},
    ),
    "cut_20000_exact": lambda p: (
        [
            (
                "UPDATE entry SET msg = ? WHERE id = 2",
                ("w" * (export.MAX_ENTRY_CHARS - 9) + " " + wrapped(p) + " z",),
            )
        ],
        Selection(ids=(2,)),
        {},
    ),
    "payload_text": lambda p: (
        [
            (
                "UPDATE entry_body SET cont = ? WHERE entry_id = 7",
                ("<Req><Note>" + wrapped(p) + "</Note></Req>",),
            ),
            (
                "UPDATE payload SET char_len = ? WHERE entry_id = 7",
                (len("<Req><Note>" + wrapped(p) + "</Note></Req>"),),
            ),
        ],
        Selection(ids=(7,)),
        {"include_payloads": True},
    ),
}


@pytest.mark.parametrize("position", POSITIONS)
@pytest.mark.parametrize("plant", EXPORT_SECRETS, ids=lambda p: f"{p.category}-{p.value[:10]}")
def test_no_plant_survives_in_the_export(tmp_path, position, plant):
    if position == "file_name" and re.search(r"[/\\\\:]", plant.value):
        pytest.skip("a file name cannot hold these characters")
    statements, selection, options = POSITIONS[position](plant)
    conn = edited(tmp_path, statements)
    try:
        text = run_export(conn, selection, **options)
    finally:
        conn.close()
    assert leaked(text, plant) == []


READ_CAP_CATEGORIES = ("guid", "phone", "email", "ip", "url", "ticket")


@pytest.mark.parametrize("cap", ["msg", "cont"])
@pytest.mark.parametrize(
    "plant",
    [p for p in EXPORT_SECRETS if p.category in READ_CAP_CATEGORIES],
    ids=lambda p: f"{p.category}-{p.value[:10]}",
)
def test_no_plant_survives_the_sql_read_cap(tmp_path, monkeypatch, cap, plant):
    monkeypatch.setattr(export, "ROW_MSG_CHARS", 300)
    monkeypatch.setattr(export, "ROW_CONT_CHARS", 300)
    # the read cap falls after k characters of the plant, for every k
    for cut_after in range(1, len(plant.value) + 1, 3):
        pad = ("q " * 200)[: 300 - cut_after]
        text = pad + plant.value + " end " + "z " * 100
        statement = (
            ("UPDATE entry SET msg = ? WHERE id = 2", (text,))
            if cap == "msg"
            else ("UPDATE entry_body SET cont = ? WHERE entry_id = 3", (text,))
        )
        conn = edited(tmp_path, [statement])
        try:
            out = run_export(conn, Selection(ids=(2 if cap == "msg" else 3,)))
        finally:
            conn.close()
        size = 5 if plant.category in ("phone", "ip", "guid", "ticket") else 6
        assert leaked(out, plant, size) == [], (cut_after, out[-300:])


# -- review round 4 (strict mode): values that LOOK public must still go ------------------------------

# Catalog words, short numbers, initials, camelCase and punctuation-only values are not proven public:
# behind an identifier label or inside a payload they are replaced whole, even when every word is an
# automatic exception (a catalog name).
LOOKS_PUBLIC = ["Monitor 123456 A", "QuintanilhaZorzo", "SendWorks", "!@#$%"]
LOOKS_PUBLIC_PAYLOAD = ["<Body>{s}</Body>", '{{"note": "{s}"}}', '<x a="{s}"/>', "<x><![CDATA[{s}]]></x>"]
LOOKS_PUBLIC_LABELLED = [
    "customer: {s}",
    "customerName={s}",
    "user: {s}",
    "password: {s}",
    'user: "{s}"',
    "customer {s}",
    "request_user_id: {s}",
]


def _pieces(value: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-z0-9]{3,}|[^A-Za-z0-9\s]{2,}", value)]


@pytest.mark.parametrize("value", LOOKS_PUBLIC)
@pytest.mark.parametrize("template", LOOKS_PUBLIC_PAYLOAD + LOOKS_PUBLIC_LABELLED)
def test_values_that_look_public_do_not_survive_strict_mode(value, template):
    if template == "customer {s}" and not value[0].isalnum():
        pytest.skip("a label word without punctuation needs a word after it")
    text = template.format(s=value)
    automatic = re.findall(r"[A-Za-z]+", value)  # every word of it is a catalog name
    out = Redactor(Profile(payload_names=()), exceptions=automatic).redact(
        text, payload=template in LOOKS_PUBLIC_PAYLOAD
    )
    for piece in _pieces(value):
        assert piece not in out, out


# -- review round 5: generative structured payloads (strict mode, default-deny) ------------------------

SECRET_WORD = "Zq7Kx9Wv"
SECRET_NUM = "7731942"
# a value made only of catalog words: in a payload it is data like any other value (it is planted in
# value positions only, never as a name, where catalog words are allowed)
PUBLIC_LOOKING = "MonitorReady"
SECRET_FORMS = [
    SECRET_WORD,
    SECRET_NUM,
    f"{SECRET_WORD}!!!",
    f"!@#{SECRET_WORD}$%!",
    f"true {SECRET_WORD}",
    f"{SECRET_NUM}ms",
    f"Monitor {SECRET_NUM} A",
]
NAMES = ["Header", "Body", "customerName", "Note", "x", "ns0:Req", "Monitor7", SECRET_WORD, "MonitorReady"]
PUBLIC = [
    "",
    "OK",
    "true",
    "Monitor",
    "120 ms",
    "a < b",
    "null",
    "&nope;",
    "&#0;",
    "x > y",
    "\ud800",
    "a\udfff",
]


# blanks a structure may use, JSON or not (round 7): ASCII, VT, FF, NBSP, Unicode spaces, separators,
# zero-width characters and the BOM
BLANKS = ["", " ", " " * 12, "\n", "\t\n  ", "\r\n" + " " * 20, "\v", "\f", "\u00a0", "\u2003",
          "\u2028", "\u3000", "\u200b", "\ufeff", "\u180e", "\u2060"]  # fmt: skip
# names that CONTAIN a public-looking word but are not valid names (round 8): never kept, not even a part
BAD_NAMES = [f"{PUBLIC_LOOKING}&#65;", f"123{PUBLIC_LOOKING}", f"{PUBLIC_LOOKING}\u00e9", "Monitor!Ready"]


def _attr(name: str, value: str, quote: str, blank: str = "") -> str:
    if quote == "stray":  # an unquoted value with quotes or entities inside (round 7)
        return f' {name}={blank}x" {value.replace(">", "")} "'
    if quote == "entity":
        return f" {name}={blank}&quot; {value.replace('>', '')} &quot;"
    if not quote:  # unquoted (malformed when the value has blanks or quotes): round 6
        return f" {name}{blank}={blank}{value.replace(' ', '_').replace('>', '')}"
    return f" {name}{blank}={blank}{quote}{value.replace(quote, '')}{quote}"


def _json_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


@st.composite
def _documents(draw, depth: int = 0) -> str:
    """An XML or JSON document (or a mix), with a secret form in some position."""
    leaf = st.sampled_from(SECRET_FORMS + PUBLIC + [PUBLIC_LOOKING])
    if depth >= 3:
        return draw(leaf)
    kind = draw(
        st.sampled_from(
            [
                "text",
                "element",
                "comment",
                "cdata",
                "pi",
                "doctype",
                "json",
                "jsonxml",
                "entity",
                "fake_pair",
                "element",  # elements, arrays and quoted text twice as likely (rounds 9-10)
                "array",
                "quoted",
                "quoted",
                "squoted",  # round 11
                "escaped",
                "xml_json",
                "weak_run",
                "array",
            ]
        )
    )
    child = draw(_documents(depth + 1)) if kind != "text" else ""
    if kind == "text":
        return draw(leaf)
    if kind == "fake_pair":  # round 8: a closing tag inside data must not pair a word in brackets
        hide = draw(
            st.sampled_from(
                [
                    "<!-- </{n}> -->",
                    "<![CDATA[</{n}>]]>",
                    '<q a="</{n}>"/>',
                    "</{n}",
                    '"</{n}>"',
                    "'</{n}>'",
                    "<?pi </{n}> ?>",
                    '{{"a": "</{n}>"}}',
                ]
            )
        ).format(n=PUBLIC_LOOKING)
        return f"value <{PUBLIC_LOOKING}> {child} {hide}"
    if kind == "element":
        # the public-looking name often: it must survive only in a region expat accepts
        name = draw(st.sampled_from(NAMES + BAD_NAMES + [PUBLIC_LOOKING] * 4))
        attrs = "".join(
            _attr(
                draw(st.sampled_from(NAMES + BAD_NAMES)),
                draw(leaf),
                draw(st.sampled_from(['"', "'", "", "stray", "entity"])),
                draw(st.sampled_from(BLANKS)),
            )
            for _ in range(draw(st.integers(0, 2)))
        )
        if attrs and draw(st.integers(0, 3)) == 0:  # round 10: a duplicate attribute
            attrs = attrs + attrs
        inner = child + draw(_documents(depth + 1))
        if draw(st.integers(0, 4)) == 0:  # an opening tag never closed, or a word in angle brackets
            return f"<{name}{attrs}>{inner}"
        return f"<{name}{attrs}>{inner}</{name}>" if inner else f"<{name}{attrs}/>"
    if kind == "quoted":  # round 10: markup inside quoted plain text is data
        return f'value "{child}" end'
    if kind in ("squoted", "escaped") and draw(st.booleans()):
        child = f"<{PUBLIC_LOOKING}/>"  # round 11: markup that must stay inside the string
    if kind == "squoted":  # round 11: single quotes, closed or not, escaped quote inside
        return draw(st.sampled_from(["value '{c}' end", "value '{c}", "value 'a\\' {c}'"])).format(c=child)
    if kind == "escaped":  # round 11: escaped quotes inside a quoted string
        return draw(st.sampled_from(['"value \\"{c}\\""', '"a\\" {c}', '"{c}\\"'])).format(c=child)
    if kind == "xml_json":  # round 11: JSON inside an XML text node, valid or not
        name = draw(st.sampled_from(NAMES + [PUBLIC_LOOKING]))
        body = draw(
            st.sampled_from(
                [
                    '{{"status":"{c}":0}}',
                    '{{"{c}": 1,}}',
                    'sent {{"{c}": 1}} ok',
                    "{{",
                    '"{c}": 1',
                    '{{"k": "{c}"}}',
                ]
            )
        ).format(c=draw(st.sampled_from([PUBLIC_LOOKING, child.replace("<", "").replace("&", "")])))
        return f"<{name}>{body}</{name}>"
    if kind == "weak_run":  # round 11: failed JSON candidates in a row, then the child
        runs = ["[0x] ", "{x} ", "[1, 2 ", "[tru] "]
        # round 12: failed candidates that hold whole valid objects with a public-looking value
        runs += [f'[0,{{"status":"{PUBLIC_LOOKING}"}},x] ', f'[1, ["{PUBLIC_LOOKING}"], x] ',
                 f'[0,"a\\q",{{"k":"{PUBLIC_LOOKING}"}}] ', f'[0,{{"k":"{PUBLIC_LOOKING}"}}']  # fmt: skip
        # round 13: the failure INSIDE a string holding the public-looking word (bad escapes, raw
        # control characters, newlines), and other failing tokens next to it
        runs += [f'[0,{{"status":"{PUBLIC_LOOKING} \\q"}}] ', f'[0,{{"s":"{PUBLIC_LOOKING}\n"}}] ',
                 f'[0,{{"s":"{PUBLIC_LOOKING}\x01"}}] ', f'[0,{{"{PUBLIC_LOOKING} \\u12G4":1}}] ',
                 f'[0, x, "{PUBLIC_LOOKING}"] ', f"[0,'{PUBLIC_LOOKING}'] ", f'[0 "{PUBLIC_LOOKING}"] ',
                 f"[0, tru{PUBLIC_LOOKING}] "]  # fmt: skip
        # round 14: stray delimiters at the start of, or inside, the failing token
        runs += ["[0,> ", "[0,< ", "[0, x>y ", "[0,<b/> ", '[0, "<a>", x] ']
        return draw(st.sampled_from(runs)) * draw(st.integers(1, 5)) + child
    if kind == "array":  # round 10: arrays that start with a number or a literal, strings with markup
        first = draw(st.sampled_from(["0", "-1", "2.5", "true", "null"]))
        return "[" + first + ", " + _json_string(child) + "]"
    if kind == "comment":
        if draw(st.booleans()):  # round 10: a comment ending in '-' (malformed)
            return f"<!--{child.replace('--', '')}--->"
        return f"<!--{child.replace('--', '')}-->"
    if kind == "cdata":
        return f"<![CDATA[{child.replace(']]>', '')}]]>"
    if kind == "pi":
        target = draw(st.sampled_from(["xml", "pi", SECRET_WORD]))
        if draw(st.booleans()):  # round 10: '?>' inside a quoted pseudo-attribute
            return f'<?{target} version="?> {child}"?>'
        return f"<?{target} {child.replace('?>', '')}?>"
    if kind == "doctype":
        return f"<!DOCTYPE {child.replace('>', '')}>"
    if kind == "entity":
        return child.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    key = draw(
        st.sampled_from(["status", "note", "customerName", "Monitor7", "120ms", SECRET_WORD, SECRET_NUM])
    )
    value = draw(st.sampled_from(["string", "number", "literal", "nested", "list"]))
    if value == "string" or kind == "jsonxml":
        rendered = _json_string(child)
    elif value == "number":
        rendered = draw(st.sampled_from([SECRET_NUM, "12", "3.5"]))
    elif value == "literal":
        rendered = draw(st.sampled_from(["true", "false", "null"]))
    elif value == "list":
        rendered = "[" + ", ".join([_json_string(child), SECRET_NUM, "true"]) + "]"
    else:
        rendered = child if child.lstrip().startswith(("{", "[")) else _json_string(child)
    gap = st.sampled_from(BLANKS)  # any blank, JSON or not
    return "{" + draw(gap) + _json_string(key) + draw(gap) + ":" + draw(gap) + rendered + draw(gap) + "}"


@st.composite
def _planted(draw) -> str:
    doc = draw(_documents())
    if not any(form in doc for form in (SECRET_WORD, SECRET_NUM)):
        doc = draw(st.sampled_from(["<x>{}</x>", '{{"note": "{}"}}', "<x a='{}'/>", "<!--{}-->"])).format(
            draw(st.sampled_from(SECRET_FORMS))
        )
    if draw(st.booleans()):  # malformed: cut anywhere, or junk inserted anywhere
        cut = draw(st.integers(0, len(doc)))
        junk = draw(st.sampled_from(["", "<", '"', "]]>", "-->", "{", "'", ">"]))
        doc = doc[:cut] + junk + (doc[cut:] if draw(st.booleans()) else "")
    return doc


def _quoted_spans(doc: str) -> list[tuple[int, int]]:
    """The strings of ``doc`` read left to right, '...' and "..." alike, as the redactor documents them
    (backslash escapes; an apostrophe between two word characters is no quote; never closed, a string
    runs to the end)."""
    spans: list[tuple[int, int]] = []
    index = 0

    def wordy(at: int) -> bool:
        return 0 <= at < len(doc) and (doc[at].isalnum() or doc[at] == "_")

    while index < len(doc):
        quote = doc[index]
        if quote not in "\"'" or (quote == "'" and wordy(index - 1) and wordy(index + 1)):
            index += 1
            continue
        end = index + 1
        while end < len(doc):
            if doc[end] == "\\":
                end += 2
                continue
            if doc[end] == quote and not (quote == "'" and wordy(end - 1) and wordy(end + 1)):
                break
            end += 1
        spans.append((index, min(end + 1, len(doc))))
        index = end + 1
    return spans


def _structural_starts(doc: str) -> set[int]:
    """The '<' of ``doc`` that start markup in at least one plausible reading: outside every double
    quote counted over the raw text (round 9), or outside every string read by :func:`_quoted_spans`
    (nested quotes of one kind pair left to right: the redactor cannot tell them apart); and never
    after a stray '<'/'>' outside the strings of that reading."""
    starts: set[int] = set()
    spans = _quoted_spans(doc)
    blanked = list(doc)
    for a, b in spans:
        blanked[a:b] = " " * (b - a)
    outside = "".join(blanked)
    for start in (m.start() for m in re.finditer("<", doc)):
        before = doc[:start]
        unescaped_quotes = len(re.findall(r'(?<!\\)"', before))  # a backslash-escaped quote is no quote
        if not (unescaped_quotes % 2 or re.search(r"[<>]", re.sub(r"<[^<>]*>", "", before))):
            starts.add(start)
        elif outside[start] == "<" and not re.search(r"[<>]", re.sub(r"<[^<>]*>", "", outside[:start])):
            starts.add(start)
    return starts


def _only_validated_names(doc: str, out: str) -> bool:
    """Every surviving PUBLIC_LOOKING stands as a tag or attribute name, and the input has a region,
    starting at a '<' that starts markup in a plausible reading (:func:`_structural_starts`), that an
    independent XML parser (expat) accepts and in which it is EXACTLY an element or attribute name.
    Markup written escaped (``&lt;Name&gt;``, also escaped again) is read on the decoded text, as the
    redactor documents it; the delimiters around the name are then the ends of entities (``;``
    before, ``&`` after)."""
    for match in re.finditer(PUBLIC_LOOKING, out):
        before = out[match.start() - 1 : match.start()]
        after = out[match.end() : match.end() + 1]
        blank_before = before.isspace() or (before != "" and before in "".join(BLANKS))
        blank_after = after.isspace() or (after != "" and after in "".join(BLANKS))
        if not (blank_before or before in ("<", "/", ";")) or not (
            blank_after or after in (">", "/", "=", "&")
        ):
            return False
    readings = [doc]
    while "&" in readings[-1] and len(readings) < 4:  # escaped once, twice (&amp;#65;)...
        decoded = html.unescape(readings[-1])
        if decoded == readings[-1]:
            break
        readings.append(decoded)
    for text in readings:
        for start in sorted(_structural_starts(text)):
            for end in (m.end() for m in re.finditer(">", text[start:])):
                region = text[start : start + end]
                if PUBLIC_LOOKING not in region:
                    continue
                try:
                    root = ElementTree.fromstring(region)
                except (ElementTree.ParseError, UnicodeEncodeError):  # a lone surrogate: no XML either
                    continue
                if any(node.tag == PUBLIC_LOOKING or PUBLIC_LOOKING in node.attrib for node in root.iter()):
                    return True
    return False


def _free_words(doc: str) -> int:
    """How many times PUBLIC_LOOKING stands in ``doc`` as a FREE-TEXT word, which the free-text allowlist
    keeps by design (declared limit: free text keeps catalog words). Read independently and
    conservatively: blank-bounded (ASCII space or the ends), outside double-quoted strings paired left to
    right, and with nothing before it, outside the strings, that could open a region or make the quoting
    ambiguous: no ``< > { } [ ] ' & \\`` outside strings (a backslash inside one escapes the next
    character, as the redactor reads it), no quote glued to a word."""
    count = 0
    clean = True
    in_string = False
    index = 0
    size = len(PUBLIC_LOOKING)
    while index < len(doc):
        char = doc[index]
        if in_string:
            if char == "\\":
                index += 2
                continue
            if char == '"':
                in_string = False
                if index + 1 < len(doc) and (doc[index + 1].isalnum() or doc[index + 1] == "_"):
                    clean = False  # closed right against a word
            index += 1
            continue
        if char == '"':
            in_string = True
            if index and (doc[index - 1].isalnum() or doc[index - 1] == "_"):
                clean = False  # opened right after a word
        elif char in "<>{}[]'&\\":
            clean = False
        elif (
            clean
            and doc.startswith(PUBLIC_LOOKING, index)
            and (index == 0 or doc[index - 1] == " ")
            and (index + size == len(doc) or doc[index + size] == " ")
        ):
            count += 1
            index += size
            continue
        index += 1
    return count


def _secret_fragments(text: str) -> list[str]:
    found = []
    for secret in (SECRET_WORD, SECRET_NUM):
        for size in (5,):
            found.extend(
                secret[i : i + size] for i in range(len(secret) - size + 1) if secret[i : i + size] in text
            )
    return found


@_generative(3000)
@given(doc=_planted())
# found in a fresh clone (no example database): the planted value stands between three strings that
# pair left to right, i.e. as free text, where the allowlist keeps catalog words (declared limit)
@example(doc='value "<Header Header=x" MonitorReady " Header=x" MonitorReady ">Zq7Kx9WvZq7Kx9Wv" end')
# the same with escaped quotes: a backslash inside a string escapes the next character
@example(doc='"value \\"<Header Header=x" MonitorReady " Header=x" MonitorReady ">Zq7Kx9WvZq7Kx9Wv\\""')
# nested single quotes pair left to right: the element stands outside every string (a name kept)
@example(doc="value '<<Header>Zq7Kx9Wvvalue '<MonitorReady/>' end' end")
# the oracle's XML parser refuses a lone surrogate (it cannot even be encoded)
@example(doc="value '<Header><Header>Zq7Kx9Wv\ud800value '<MonitorReady/>' end' end")
# escaped markup is read on the decoded text: a valid element named by a catalog word keeps its name
@example(doc='&lt;MonitorReady Header="Zq7Kx9Wv"&gt;Zq7Kx9Wv[0, "Zq7Kx9Wv"]&lt;/MonitorReady&gt;')
@example(
    doc='&lt;MonitorReady MonitorReady&amp;#65;="Zq7Kx9Wv"&gt;Zq7Kx9Wv[0, "Zq7Kx9Wv"]&lt;/MonitorReady&gt;'
)
# a REAL leak (fixed in redact.py): a quote opened right after a word may close a string holding it
# an attribute name may be followed by any XML blank before its '='
@example(doc='&lt;Header MonitorReady\n=\n"Zq7Kx9Wv"&gt;Zq7Kx9Wv{"status":"Zq7Kx9Wv"}&lt;/Header&gt;')
@example(doc='value "<?xml <?Zq7Kx9Wv version=" MonitorReady"?>" end')
# a REAL leak (fixed in redact.py): escaped markup in a PAYLOAD skipped the decoded gate, so JSON in
# an escaped text node that is not exactly valid JSON kept its keys
@example(doc='&lt;Zq7Kx9Wv&gt;sent {"MonitorReady": 1} ok&lt;/Zq7Kx9Wv&gt;')
def test_no_secret_survives_any_structured_position_in_strict_mode(doc):
    redactor = Redactor(Profile(payload_names=()))
    # a payload; and the same text inside a record when it holds markup or JSON (free text without any
    # structure keeps numbers of up to 6 digits with a unit, a declared limit)
    structured = redact_module._RX["structured_hint"].search(doc) is not None
    for payload in (True, False) if structured else (True,):
        out = redactor.redact(doc, payload=payload)
        out.encode("utf-8")  # round 14: a lone surrogate never reaches the output
        leaked_fragments = [f for f in _secret_fragments(doc) if f in out]
        assert leaked_fragments == [], (payload, doc, out)
        if PUBLIC_LOOKING in out:  # round 9: only as a NAME of a region an XML parser accepts, or (record
            # text) as a free-text word outside every string and region, which the allowlist keeps
            free = 0 if payload else _free_words(doc)
            assert out.count(PUBLIC_LOOKING) <= free or _only_validated_names(doc, out), (payload, doc, out)


# -- review round 15: any split of a record into first line + continuation is never weaker --------------


@_generative(1500)
@given(doc=_planted(), data=st.data())
def test_a_record_split_anywhere_is_never_less_redacted_than_the_whole_text(doc, data):
    split = data.draw(st.integers(0, len(doc)))
    redactor = Redactor(Profile(payload_names=()))
    whole = redactor.redact(doc)
    parts = redactor.redact(doc, spans=redactor.record_spans(doc, split, None))
    parts.encode("utf-8")
    hidden = {token for token in re.findall(r"[A-Za-z0-9]{4,}", doc) if token not in whole}
    assert [token for token in hidden if token in parts] == [], (split, doc, whole, parts)


# -- review round 16: through the export, any piece is never less redacted than the whole record ---------
#
# Call sites audited (each one cuts, splits or re-joins record text): the read caps (_fetch_rows: a cut
# record has an unknown state -> its continuation and payloads are one value), the first line vs the
# continuation (record_spans reads them as ONE text), continuation parsers (structural state first),
# the pieces around payloads (_row_lines clips the record spans), the payload bodies (round 17: the
# record spans cut to the body are united with the payload's own redaction; opaque when the record
# was cut), auto-detected payloads (same rule),
# MAX_ENTRY_CHARS and excerpt lines (cut after redaction), CRLF/LF (normalised after redaction), notes,
# exception messages, keys and headers (one call each), context records (independent entries).

_CASE_DB: dict[str, Path] = {}
# captured once: the function-scoped monkeypatch is not undone between hypothesis examples, so wrapping
# the CURRENT export._fetch_rows would nest one wrapper per example (RecursionError after ~1000)
_REAL_FETCH_ROWS = export._fetch_rows


def _case_db(tmp_path_factory) -> Path:
    if "db" not in _CASE_DB:
        _CASE_DB["db"] = case_module.build_export_case(tmp_path_factory.mktemp("piece") / "case.sqlite")
    return _CASE_DB["db"]


def _secret_like(token: str) -> bool:
    return bool(re.search(r"[A-Za-z]", token) and re.search(r"[0-9]", token)) or "Ready" in token


@_generative(300)
@given(doc=_planted(), data=st.data())
def test_no_piece_of_an_exported_record_is_less_redacted_than_the_whole_record(
    doc, data, tmp_path_factory, monkeypatch
):
    inner = ""
    if data.draw(st.booleans()):
        # round 17: markup the whole record hides as data (CDATA, comment, PI, string) that a payload
        # starting inside it would read as structure
        inner = data.draw(
            st.sampled_from([f"<{PUBLIC_LOOKING}/>", f"<{PUBLIC_LOOKING}>x</{PUBLIC_LOOKING}>", doc])
        )
        wrapper = data.draw(
            st.sampled_from(
                ["<H><![CDATA[{}]]></H>", "<H><!-- {} --></H>", "<H><?p {} ?></H>", '{{"n": "{}"}}']
            )
        )
        doc = wrapper.format(inner)
    newline = doc.find("\n")
    split = data.draw(st.integers(0, len(doc) if newline < 0 else newline))
    if inner and data.draw(st.booleans()):
        split = 0  # keep the hidden markup whole in the continuation
    msg, cont = doc[:split], doc[split:]
    template = data.draw(st.sampled_from([None, "WFM-130C"]))
    if template:
        msg = "CRS request is rejected by DMZ validation rules: " + msg
    if data.draw(st.booleans()):
        cont = cont.replace("\n", "\r\n")
    whole = msg + ("\n" + cont if cont else "")
    payload_kind = data.draw(st.sampled_from(["table", "none", "auto"]))  # hypothesis favours the first
    if payload_kind == "auto":
        cont = "<" + cont if not cont.startswith("<") else cont
        whole = msg + "\n" + cont
    table: dict[int, list] = {}
    at = whole.find(inner) if inner else -1
    if payload_kind == "table" and len(whole) > 1:
        # one to three payloads with offsets ANYWHERE: inside regions, mid-token, right after a
        # delimiter (round 17: a payload that starts at a safe delimiter of a hidden region)
        delimiters = [i + d for i, c in enumerate(whole) if c in '<>[]!?-"{}:' for d in (0, 1)]
        point = st.integers(0, len(whole))
        if delimiters:
            point = st.one_of(point, st.sampled_from([i for i in delimiters if i <= len(whole)]))
        points = sorted(data.draw(st.lists(point, min_size=2, max_size=6, unique=True)))
        ranges = [(points[i], points[i + 1]) for i in range(0, len(points) - 1, 2)]
        if at >= 0 and data.draw(st.booleans()):  # one payload from there to the end of the record
            ranges = [(data.draw(st.integers(max(0, at - 9), min(len(whole) - 1, at + 2))), len(whole))]
        table = {2: [export._Payload(2, "request", "in", "", "", "xml", a, b) for a, b in ranges]}

    def fetch(conn, ids):
        rows = _REAL_FETCH_ROWS(conn, ids)
        row = rows[2]
        row.msg, row.cont, row.tpl = msg, cont or None, template
        return rows

    monkeypatch.setattr(export, "_fetch_rows", fetch)
    monkeypatch.setattr(export, "_fetch_payloads", lambda conn, ids: table)
    profile = Profile(payload_names=())
    conn = connect(_case_db(tmp_path_factory), readonly=True)
    try:
        result = build_report(
            conn,
            Selection(ids=(2,)),
            ExportOptions(anonymize=True, include_payloads=True, profile=profile),
            now=NOW,
        )
    finally:
        conn.close()
    exported = "".join(part.text for part in result.parts)
    exported.encode("utf-8")
    reference = Redactor(profile)
    redacted_whole = reference.redact(whole, spans=reference.record_spans(whole, len(msg), template))
    hidden = {t for t in re.findall(r"[A-Za-z0-9]{4,}", whole) if _secret_like(t) and t not in redacted_whole}
    assert [t for t in hidden if t in exported] == [], (msg, cont, payload_kind, table, redacted_whole)
