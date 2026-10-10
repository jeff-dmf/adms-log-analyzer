"""Redaction: every category, stable markers, terms, exceptions, payload rules, profile file, safety.

This file tests the PATTERN layer: every redactor here runs in standard mode (the strict nets are
tested in ``test_redact_strict.py`` and, in both modes, by the leak harness). All values are fictitious.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import replace
from pathlib import Path
from re import _constants as sre_constants  # type: ignore[attr-defined]
from re import _parser as sre_parser  # type: ignore[attr-defined]

import pytest

from dmslog.report import redact
from dmslog.report.redact import (
    CATEGORIES,
    Profile,
    ProfileError,
    default_profile,
    dump_profile_toml,
    load_profile,
    profile_from_dict,
    save_profile,
    write_value_map,
)


class Redactor(redact.Redactor):
    """The pattern layer alone: the profile is used in standard mode."""

    def __init__(self, profile: Profile | None = None, **kwargs) -> None:
        super().__init__(replace(profile or Profile(), mode="standard"), **kwargs)


GUID = "3f2a9c1e-7b4d-4e8a-9c21-5d6e7f8a9b0c"

# category -> (text containing exactly one sensitive value, the value that must disappear, marker)
CASES = {
    "guid": (f"message {GUID} done", GUID, "<GUID-1>"),
    "gid": ("crew GID: 1234567890123456 moved", "1234567890123456", "<GID-1>"),
    "ticket": ("opened INC0045123 today", "INC0045123", "<TICKET-1>"),
    "mrid": ("Crew mRID: CREW7788 assigned", "CREW7788", "<MRID-1>"),
    "plate": ("vehicle QRS4T56 left", "QRS4T56", "<PLATE-1>"),
    "host": ("connect hostname=dmsprod01 now", "dmsprod01", "<HOST-1>"),
    "ip": ("peer 10.20.30.40 answered", "10.20.30.40", "<IP-1>"),
    "user": ("request by user: jsilva ok", "jsilva", "<USER-1>"),
    "url": ("call https://esb.example.com:8443/Svc/Wfm.svc now", "esb.example.com", "<URL-1>"),
    "path": ("file C:\\Data\\acme\\x.log opened", "acme", "<PATH-1>"),
    "email": ("mail maria.souza@example.com sent", "maria.souza", "<EMAIL-1>"),
    "phone": ("call +55 21 99876-5432 back", "99876-5432", "<PHONE-1>"),
    "person": ("Address: Rua das Acacias 45", "Acacias", "<PERSON-1>"),
}


def test_every_category_has_a_case():
    assert set(CASES) == set(CATEGORIES)


@pytest.mark.parametrize("category", CATEGORIES)
def test_category_is_replaced_by_a_marker(category):
    text, value, marker = CASES[category]
    out = Redactor().redact(text)
    assert value not in out
    assert marker in out
    assert redact.LABELS[category] in marker


@pytest.mark.parametrize("category", CATEGORIES)
def test_turning_a_category_off_lets_its_value_through(category):
    text, value, _marker = CASES[category]
    related = {"url": ("host",)}  # the host inside a URL is a host too
    flags = {c: c != category and c not in related.get(category, ()) for c in CATEGORIES}
    # standard mode: the strict nets (long numbers, labels) still hide some values of a category that is off
    out = Redactor(Profile(categories=flags, mode="standard")).redact(text)
    assert value in out


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("INC 123456 and PROB0077", "123456"),
        ("PRB1234567 CHG0001", "1234567"),
        ('mRID(s): "CREW1"', "CREW1"),
        ("MRID 'ABC-9'.", "ABC-9"),
        ("<ns:mRID>INC 9988</ns:mRID>", "9988"),
        ("<GID>123456789012345678</GID>", "123456789012345678"),
        ("gid '987654321'", "987654321"),
        ("vehicle ABC-1234.", "1234"),
        ("server: fileserver01.corp.local", "fileserver01"),
        ("see app.corp.local now", "app.corp"),
        ("\\\\FILESRV\\share\\dir\\f.txt", "FILESRV"),
        ("ip fe80::1 up", "fe80"),
        ("addr 2001:db8::ff00:42:8329", "ff00"),
        ("CORP\\jsilva logged in", "jsilva"),
        ("user 'jsilva' ok", "jsilva"),
        ("account=dnaApp_acme", "dnaApp_acme"),
        ("'/var/log/dms/app.log'", "dms"),
        ('"C:\\Program Files\\Acme App\\bin"', "Acme App"),
        ("<Path>C:\\Program Files\\Acme App\\bin</Path>", "Acme App"),
        ("ftp://files.example.com/a/b.txt.", "files.example"),
        ("a@b.example.org", "a@b"),
        ("(21) 98765-4321", "98765"),
        ("+5521998765432", "5521998765432"),
        ("21 98765-4321", "4321"),
        ("customer name: Maria Souza", "Souza"),
        ("endereco = Rua A, 10", "Rua A"),
    ],
)
def test_more_shapes(text, value):
    assert value not in Redactor().redact(text)


@pytest.mark.parametrize(
    "text",
    [
        "System.Net.WebException: boom",
        "Version=4.0.0.0, Culture=neutral",
        "version 1.2.3.4 installed",
        "time 12:34:56 and 10:00:00.123",
        "2030-10-15 10:00:00.125 [PID=4001][TID=0x44c5] INFO - plain text",
        "date 15/10/2030 and 10/15/2030 and text/xml",
        "</Name> closes",
        "WFM-081 timeout code 5.3 GetIncidents",
        "range 2024-2025 ids 1234 5678",
        "IncidentRecord version=0.0.0.0",
    ],
)
def test_harmless_text_is_left_alone(text):
    assert Redactor().redact(text) == text


def test_same_value_same_marker_and_numbers_follow_first_appearance():
    a, b = "3f2a9c1e-7b4d-4e8a-9c21-5d6e7f8a9b0c", "a1b2c3d4-0000-4aaa-8bbb-1234567890ab"
    r = Redactor()
    out = r.redact(f"{a} {b} {a.upper()} {b}")
    assert out == "<GUID-1> <GUID-2> <GUID-1> <GUID-2>"
    # the numbering is per category and keeps going across calls
    assert (
        r.redact("host: srv01 then host: SRV02 then host: SRV01")
        == "host: <HOST-1> then host: <HOST-2> then host: <HOST-1>"
    )
    assert r.redact("host: srv03") == "host: <HOST-3>"


def test_markers_are_deterministic_across_instances():
    text = f"INC0001 {GUID} user: jdoe INC0002 user: jdoe 10.0.0.1 10.0.0.2"
    assert Redactor().redact(text) == Redactor().redact(text)


def test_equivalent_spellings_share_a_marker():
    r = Redactor()
    assert r.redact("INC 12 INC12 INC_12") == "<TICKET-1> <TICKET-1> <TICKET-1>"
    assert r.redact("ABC-1234 ABC1234") == "<PLATE-1> <PLATE-1>"
    assert r.redact("+55 21 99876-5432") == r.redact("+55 21 99876-5432")


def test_overlapping_matches_merge_instead_of_leaking_a_tail():
    # the e-mail starts first and swallows the host-looking tail; the URL swallows its guid and port
    out = Redactor().redact(f"x joe@mail.example.com y https://h.example.com/{GUID}/z?q=1 w")
    assert out == "x <EMAIL-1> y <URL-1> w"


def test_ticket_inside_mrid_label_is_one_ticket():
    assert Redactor().redact("mRID: INC 123456 done") == "mRID: <TICKET-1> done"


def test_user_terms_word_and_literal():
    profile = Profile(terms_word=("Acme", "Santa Maria"), terms_literal=("SE-ALPHA",))
    out = Redactor(profile).redact("ACME and acme, Acmeville, Santa  Maria? SE-ALPHA-3 se-alpha")
    assert "ACME" not in out and "acme," not in out
    assert "Acmeville" in out  # whole words only
    assert "Santa  Maria" in out  # the term has one space: literal characters
    assert "<TERM-" in out
    assert "SE-ALPHA" not in out and "se-alpha" in out  # literal terms are case sensitive
    assert out.count("<TERM-1>") == 2  # ACME and acme share a marker
    assert "-3" in out


def test_user_terms_work_with_every_category_off():
    flags = dict.fromkeys(CATEGORIES, False)
    r = Redactor(Profile(categories=flags, terms_word=("Acme",)))
    assert r.active
    assert r.redact(f"Acme {GUID}") == f"<TERM-1> {GUID}"


def test_exceptions_are_never_replaced():
    profile = Profile(exceptions=("DMS-1234", "SendWorks"), terms_word=("SendWorks", "Acme"))
    r = Redactor(profile, exceptions=["Extra-9999"])
    out = r.redact("code DMS-1234 and ABC-5555 SendWorks Extra-9999 Acme user: SendWorks")
    assert "DMS-1234" in out and "SendWorks" in out and "Extra-9999" in out
    assert "ABC-5555" not in out and "Acme" not in out
    assert out.count("SendWorks") == 2  # also as the value of a label


def test_exception_match_is_on_the_whole_matched_value_ignoring_case():
    r = Redactor(Profile(exceptions=("jsilva",)))
    assert r.redact("user: JSilva and user: jdoe") == "user: JSilva and user: <USER-1>"


def test_known_values_are_replaced_as_whole_words_only_when_their_category_is_on():
    known = {"host": ["ACMEHOST01"], "user": ["dnaApp_acme"]}
    out = Redactor(known=known).redact("on ACMEHOST01 as dnaApp_acme (ACMEHOST010 stays)")
    assert out == "on <HOST-1> as <USER-1> (ACMEHOST010 stays)"
    off = Redactor(Profile(categories={"host": False}), known=known).redact("on acmehost01")
    assert off == "on acmehost01"


# -- payloads ---------------------------------------------------------------------------------

XML = (
    "<ns0:Req xmlns:ns0='urn:x'>\n  <Customer id=\"7\">\n    <ns0:Name>Maria Souza</ns0:Name>\n"
    "    <Phone>11999990000</Phone>\n    <Addr Street=\"Rua das Acacias 45\" Zone='north'>\n"
    "      <City>Niteroi</City>\n    </Addr>\n    <Other>keep me</Other>\n"
    "    <Notes><![CDATA[call after 5pm]]></Notes>\n  <!-- Name: hidden --></Customer>\n</ns0:Req>."
)


def test_xml_elements_and_attributes_by_name():
    profile = Profile(payload_names=("Name", "Phone", "City", "Street", "Notes"), mode="standard")
    out = Redactor(profile).redact(XML, payload=True)
    for gone in ("Maria Souza", "11999990000", "Niteroi", "Rua das Acacias", "call after 5pm"):
        assert gone not in out
    assert "keep me" in out and "north" in out and 'id="7"' in out
    assert "<ns0:Name><PERSON-1></ns0:Name>" in out  # namespace prefix ignored, case-insensitive match
    assert 'Street="<PERSON-' in out
    assert out.endswith("</ns0:Req>.")  # the trailing dot of the adapter stays


def test_payload_names_off_without_person_category():
    out = Redactor(Profile(categories={"person": False}, payload_names=("Name",), mode="standard")).redact(
        XML, payload=True
    )
    assert "Maria Souza" in out


def test_regexes_still_run_inside_payloads():
    xml = f"<Header><MessageID>{GUID}</MessageID><Contact>joe@mail.example.com</Contact></Header>"
    out = Redactor(Profile(payload_names=("Nothing",))).redact(xml, payload=True)
    assert GUID not in out and "joe@" not in out


@pytest.mark.parametrize(
    "xml",
    [
        "<A><Name>Maria Souza",  # truncated in the text
        "<A><Name>Maria Souza</Na",  # truncated in a closing tag
        '<A><Name x="b',  # truncated in a tag
        "<A><Name>Maria Souza</B></Name></A>",  # mismatched closing tag
        "<A><Name>Maria Souza<B></A>",  # unclosed child
        "<A> 1 < 2 <Name>Maria Souza</Name>",  # a stray '<'
        "<?xml version='1.0'?><A><Name>Maria Souza</Name></A>",
        "<Name>Maria Souza</Name><Name>Maria Souza</Name>",  # sequence without a root
    ],
)
def test_malformed_xml_still_gets_the_element_rule(xml):
    out = Redactor(Profile(payload_names=("Name",))).redact(xml, payload=True)
    assert "Souza" not in out


def test_json_keys_scalars_escapes_and_truncation():
    text = '{"Name": "Ana \\"Q\\" Silva", "phone": 5511999990000, "ok": true, "id": 4, "Address":"Rua B 7'
    out = Redactor(Profile(payload_names=("Name", "Phone", "Address"), mode="standard")).redact(
        text, payload=True
    )
    assert "Silva" not in out and "5511999990000" not in out and "Rua B" not in out
    assert '"id": 4' in out and '"ok": true' in out
    nested = '{"c": {"name": "Z Y", "list": [1, 2]}}'
    assert "Z Y" not in Redactor(Profile(payload_names=("Name",), mode="standard")).redact(
        nested, payload=True
    )


def test_deep_and_hostile_payloads_do_not_blow_up():
    names = Profile(payload_names=("Name",))
    deep = "<A>" * 5000 + "<Name>Maria Souza</Name>" + "</A>" * 5000
    assert Redactor(names).redact(deep, payload=True)  # past the depth cap: no crash, no hang
    assert Redactor(names).redact("<" * 100_000, payload=True)
    assert Redactor(names).redact('{"Name":' * 20_000, payload=True)


# -- memory and chunks ------------------------------------------------------------------------


def test_marker_table_is_bounded(monkeypatch):
    monkeypatch.setattr(redact, "MAX_DISTINCT", 3)
    r = Redactor()
    out = r.redact("INC1 INC2 INC3 INC4 INC1 INC5")
    assert out == "<TICKET-1> <TICKET-2> <TICKET-3> <TICKET-*> <TICKET-1> <TICKET-*>"
    assert r.overflow == 2
    assert len(r.value_map()) == 3


def test_long_texts_are_redacted_whole_and_hugely_long_ones_fail_closed(monkeypatch):
    text = "\n".join(f"line {i} user: u{i % 3} host: h{i % 2} {GUID}" for i in range(4000))
    out = Redactor().redact(text)
    assert GUID not in out and "user: u" not in out
    assert out.count("<GUID-1>") == 4000 and out.count("\n") == 3999
    monkeypatch.setattr(redact, "MAX_TEXT_CHARS", 100)
    assert Redactor().redact(text) == "<REDACTED-TOO-LONG>"


def test_an_inactive_redactor_returns_the_same_object():
    r = Redactor(Profile(categories=dict.fromkeys(CATEGORIES, False)))
    text = f"nothing happens {GUID}"
    assert not r.active and r.redact(text) is text


# -- value map and legend ---------------------------------------------------------------------


def test_legend_has_counts_but_never_values():
    r = Redactor(Profile(terms_word=("Acme",)))
    r.redact(f"{GUID} {GUID} user: jdoe Acme")
    legend = r.legend()
    assert {row["category"]: (row["distinct"], row["replaced"]) for row in legend} == {
        "guid": (1, 2),
        "user": (1, 1),
        "term": (1, 1),
    }
    blob = json.dumps(legend)
    assert GUID not in blob and "jdoe" not in blob and "Acme" not in blob


def test_value_map_lives_in_the_data_dir_and_not_in_the_text(tmp_path):
    r = Redactor()
    out = r.redact(f"id {GUID}")
    assert GUID not in out
    assert r.value_map() == {"<GUID-1>": GUID}
    path = write_value_map(tmp_path, "case/../x", r, stamp="20301015-100000-000000")
    assert path is not None and path.parent == tmp_path / "redaction-maps"
    assert re.fullmatch(r"[0-9a-f]{24}\.map\.json", path.name)  # opaque: no case id, no value
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["values"] == {"<GUID-1>": GUID} and saved["case"] == "case/../x"
    other = write_value_map(tmp_path, "case/../x", r, stamp="20301015-100000-000000")
    assert other != path
    assert write_value_map(tmp_path, "c", Redactor()) is None  # nothing replaced, no file


# -- profile ----------------------------------------------------------------------------------


def test_default_profile_has_everything_on():
    p = default_profile()
    assert all(p.enabled(c) for c in CATEGORIES)
    assert p.terms_word == () and p.exceptions == () and "Name" in p.payload_names
    assert p.to_dict()["categories"] == dict.fromkeys(CATEGORIES, True)


def test_toml_round_trip_with_awkward_text(tmp_path):
    profile = Profile(
        name="my profile-1",
        categories={**dict.fromkeys(CATEGORIES, True), "ip": False},
        terms_word=(
            "Acme",
            'quote"and\\slash',
            "Jos" + chr(0xE9) + " d'Ar" + chr(0xE7) + "o",
            "emoji \U0001f600",
        ),
        terms_literal=("SE-ALPHA", "tab here"),
        exceptions=("SendWorks", "ERR-1"),
        payload_names=("Name", "ns:Phone"),
    )
    save_profile(tmp_path / "data", profile)
    stored = tmp_path / "data" / "redaction.toml"
    assert stored.is_file()
    assert load_profile(tmp_path / "data").to_dict() == profile.to_dict()
    text = stored.read_text(encoding="utf-8")
    assert "\r" not in text and not text.startswith("\ufeff")
    assert not list(stored.parent.glob("*.tmp"))


def test_load_defaults_when_there_is_no_file(tmp_path):
    assert load_profile(tmp_path).to_dict() == default_profile().to_dict()


def test_load_tolerates_a_bom_and_partial_files(tmp_path):
    (tmp_path / "redaction.toml").write_bytes(b"\xef\xbb\xbf[categories]\nip = false\n")
    p = load_profile(tmp_path)
    assert not p.enabled("ip") and p.enabled("guid")


@pytest.mark.parametrize(
    "content",
    ["[categories\nip = false", "version = 99\n", "[categories]\nnope = true\n", "[categories]\nip = 1\n"],
)
def test_load_rejects_bad_files(tmp_path, content):
    (tmp_path / "redaction.toml").write_text(content, encoding="utf-8")
    with pytest.raises(ProfileError):
        load_profile(tmp_path)


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        ([], "not_object"),
        ({"name": ""}, "bad_name"),
        ({"name": "x" * 65}, "bad_name"),
        ({"categories": []}, "bad_section"),
        ({"categories": {"nope": True}}, "unknown_category"),
        ({"categories": {"ip": "yes"}}, "bad_flag"),
        ({"terms": []}, "bad_section"),
        ({"terms": {"word": "Acme"}}, "bad_list"),
        ({"terms": {"word": [1]}}, "bad_list"),
        ({"terms": {"word": ["a"]}}, "bad_entry"),
        ({"terms": {"word": ["x" * 129]}}, "bad_entry"),
        ({"terms": {"literal": ["bad\x00nul"]}}, "bad_entry"),
        ({"terms": {"word": ["lone\ud800surrogate"]}}, "bad_entry"),
        ({"terms": {"word": [f"term{i}" for i in range(201)]}}, "too_many"),
        ({"exceptions": [""]}, "bad_entry"),
        ({"payload_names": ["has space"]}, "bad_element_name"),
    ],
)
def test_profile_validation(data, problem):
    with pytest.raises(ProfileError) as info:
        profile_from_dict(data)
    assert info.value.problem == problem


def test_profile_errors_never_contain_the_values():
    with pytest.raises(ProfileError) as info:
        profile_from_dict({"terms": {"word": ["secret-customer", "x" * 500]}})
    assert "secret-customer" not in str(info.value)


def test_terms_are_trimmed_and_deduplicated():
    p = profile_from_dict({"terms": {"word": [" Acme ", "Acme", "Beta"]}})
    assert p.terms_word == ("Acme", "Beta")


def test_dump_is_valid_toml_for_the_default():
    import tomllib

    data = tomllib.loads(dump_profile_toml(default_profile()))
    assert data["version"] == redact.PROFILE_VERSION and data["categories"]["guid"] is True


# -- regex safety -----------------------------------------------------------------------------


def _walk(items, in_repeat, problems, source):
    for op, av in items:
        if op in (sre_constants.MAX_REPEAT, sre_constants.MIN_REPEAT):
            low, high, sub = av
            single = len(sub) == 1 and sub[0][0] in (
                sre_constants.IN,
                sre_constants.LITERAL,
                sre_constants.ANY,
            )
            if high == sre_constants.MAXREPEAT and not single:
                problems.append(f"unbounded repeat of more than one character in {source}")
            repeats = high > 1
            if repeats and in_repeat:
                problems.append(f"nested repeat in {source}")
            _walk(sub, in_repeat or repeats, problems, source)
        elif op is sre_constants.SUBPATTERN:
            _walk(av[3], in_repeat, problems, source)
        elif op is sre_constants.BRANCH:
            for branch in av[1]:
                _walk(branch, in_repeat, problems, source)
        elif op in (sre_constants.ASSERT, sre_constants.ASSERT_NOT):
            _walk(av[1], in_repeat, problems, source)


def test_patterns_have_no_nested_repeats_and_unbounded_ones_only_on_one_character():
    problems: list[str] = []
    for name, rx in redact._RX.items():
        _walk(sre_parser.parse(rx.pattern, rx.flags & ~re.UNICODE), False, problems, name)
    for rx in (
        redact._literal_regex(["Acme", "a.b*c"], word=True),
        redact._literal_regex(["Acme", "a.b*c"], word=False),
    ):
        _walk(sre_parser.parse(rx.pattern, rx.flags & ~re.UNICODE), False, problems, "terms")
    assert problems == []


HOSTILE = [
    "a" * 200_000,
    "1." * 100_000,
    ":" * 200_000,
    "0123456789abcdef:" * 20_000,
    "<" * 100_000,
    "@" * 100_000,
    "a@" * 50_000,
    "http://" * 20_000,
    "gid " * 50_000,
    "mrid:" * 40_000,
    "C:\\" * 50_000,
    "/a" * 100_000,
    "user:" + "x" * 200_000,
    "ABC-" * 50_000,
    "+55 " * 50_000,
    "\\\\" * 100_000,
    "9" * 200_000,
]


@pytest.mark.parametrize("text", HOSTILE, ids=lambda t: f"{t[:8]!r}x{len(t)}")
def test_hostile_text_is_processed_in_bounded_time(text):
    redactor = Redactor(Profile(terms_word=("Acme",), terms_literal=("zz",)))
    started = time.perf_counter()
    redactor.redact(text)
    redactor.redact(text, payload=True)
    assert time.perf_counter() - started < 20  # generous: only catastrophic backtracking fails this


def test_redaction_module_has_no_user_term_logging():
    source = Path(redact.__file__).read_text(encoding="utf-8")
    assert "logging" not in source and "print(" not in source


# -- review round: evasions that used to leak ---------------------------------------------------

TAIL = "SECRETTAIL"


@pytest.mark.parametrize(
    "text",
    [
        "see https://h.example.com/" + "a" * 3000 + TAIL + " now",
        "see HTTPS://H.Example.com/" + "a" * 3000 + TAIL,
        "file C:\\" + "d\\" * 700 + TAIL + " end",
        "file '/var/" + "d/" * 700 + TAIL + "' end",
        "file \\\\srv\\share\\" + "d\\" * 700 + TAIL,
        "mail " + "l" * 100 + TAIL + "@mail.example.com ok",
        "mail joe@mail.example.com" + ".x" * 100 + "." + TAIL.lower() + " ok",
        "user: " + "u" * 100 + TAIL + " ok",
        "mRID: " + "m" * 100 + TAIL + " ok",
        "hostname=" + "h" * 300 + TAIL + " ok",
        "Address: " + "street " * 40 + TAIL,
        "GID: " + "1" * 30 + " ok",
        "INC" + "9" * 30 + " ok",
        "host " + "a" * 300 + TAIL + ".example.com ok",
    ],
)
def test_long_values_are_replaced_to_their_end(text):
    out = Redactor().redact(text)
    assert TAIL not in out and TAIL.lower() not in out
    assert "aaaa" not in out and "1111" not in out and "9999" not in out and "llll" not in out


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        ("opened inc0045123 today", "0045123"),
        ("opened Inc 0045123 today", "0045123"),
        ("problem prob0077 and Prb0078", "0077"),
        ("problem Prb0078", "0078"),
        ("vehicle qrs4t56 left", "4t56"),
        ("vehicle abc-1234 left", "1234"),
        ("call HTTPS://esb.example.com/x", "esb"),
        ("call Http://esb.example.com/x", "esb"),
        ("call FTP://esb.example.com/x", "esb"),
        ("call Net.TCP://esb.example.com/x", "esb"),
        ("USER: JSILVA ok", "JSILVA"),
        ("MRID: crew7788 ok", "7788"),
    ],
)
def test_case_variants_are_replaced(text, gone):
    assert gone not in Redactor().redact(text)


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        ("https%3A%2F%2Fesb.example.com%2FSvc%3Fx%3D1", "esb.example.com"),
        ("https%3a%2f%2fesb.example.com%2fSvc", "esb.example.com"),
        ("to joe%40mail.example.com now", "joe"),
        ("id %7B3f2a9c1e-7b4d-4e8a-9c21-5d6e7f8a9b0c%7D", "3f2a9c1e"),
        ("ip 10%2E20%2E30%2E40 up", "10%2E20"),
        ("user%3A%20jsilva ok", "jsilva"),
        ("INC%200045123", "0045123"),
        ("double https%253A%252F%252Fesb.example.com%252Fx", "esb.example.com"),
        ("Jos%C3%A9 called", "Jos"),
        ("path C%3A%5CData%5Cacme%5Cx.log", "acme"),
    ],
)
def test_percent_encoded_values_are_replaced(text, gone):
    profile = Profile(terms_word=("Jos" + chr(0xE9),))
    out = Redactor(profile).redact(text)
    assert gone not in out
    assert "<" in out


@pytest.mark.parametrize(
    ("text", "gone"),
    [
        ("to joe\\u0040mail.example.com now", "mail.example"),
        ("to joe&#64;mail.example.com now", "mail.example"),
        ("to joe&#x40;mail.example.com now", "mail.example"),
        ("id \\u0033f2a9c1e-7b4d-4e8a-9c21-5d6e7f8a9b0c", "9c21"),
        ("url h\\u0074tps://esb.example.com/x", "esb.example.com"),
        ("emoji-safe \\ud83d\\ude00 and \\ud800 lone user: jdoe", "jdoe"),
        ("a &amp; b user: jdoe &lt; c", "jdoe"),
    ],
)
def test_escaped_values_are_replaced(text, gone):
    out = Redactor().redact(text)
    assert gone not in out


def test_escape_decoding_keeps_the_original_text_around_the_match():
    out = Redactor().redact("a%20b joe%40mail.example.com c\\u0020d")
    assert out == "a%20b <EMAIL-1> c\\u0020d"
    # the same value, plain or encoded, shares a marker and the map keeps the decoded value
    r = Redactor()
    assert r.redact("joe@mail.example.com joe%40mail.example.com") == "<EMAIL-1> <EMAIL-1>"
    assert r.value_map() == {"<EMAIL-1>": "joe@mail.example.com"}


def test_unicode_escaped_json_key():
    names = Profile(payload_names=("Name",), mode="standard")
    out = Redactor(names).redact(
        '{"\\u004eame": "Ana Q", "n\\u0041me": "Bea R", "other": "keep"}', payload=True
    )
    assert "Ana" not in out and "Bea" not in out and "keep" in out
    out = Redactor(names).redact('{"Name\\ud83d\\ude00": "Cy S", "N\\u0061me": 5551234567}', payload=True)
    assert "5551234567" not in out and "Cy S" in out  # a different key (the emoji) is not Name


def test_json_rule_covers_the_whole_value():
    names = Profile(payload_names=("Name", "Address"), mode="standard")
    text = (
        '{"Name": ["Ana Q", "Bea R", {"deep": ["Cy S", 123456]}], "Address": {"street": "Rua X", "n": 45},'
        ' "ok": "keep", "list": ["keep2"]}'
    )
    out = Redactor(names).redact(text, payload=True)
    for gone in ("Ana", "Bea", "Cy S", "123456", "Rua X", '"n": 45', "street"):
        assert gone not in out
    assert "keep" in out and "keep2" in out and "ok" in out
    deep = '{"Name": ' + "[" * 300 + '"Zed Y"' + "]" * 300 + "}"
    assert "Zed" not in Redactor(names).redact(deep, payload=True)
    assert "Unclosed" not in Redactor(names).redact('{"Name": "Unclosed', payload=True)


def test_xml_attributes_quotes_limits_and_truncation():
    names = Profile(payload_names=("Name", "Phone"))
    cases = [
        '<Item Name="Ana ' + "x" * 3000 + 'TAILX" other="1"/>',
        '<Item Name="Ana > Q" other="1"/>',  # '>' inside a quoted value
        "<Item Name='Ana > Q'><Sub/></Item>",
        '<Item Name="Ana Q',  # truncated inside the value
        "<Item Name=Ana>text</Item>",  # unquoted
        '<Item\n  Phone\n =\n "5551234"/>',
        '<Item Phone = "5551234" Name = "Ana" />',
    ]
    for text in cases:
        out = Redactor(names).redact(text, payload=True)
        for secret in ("Ana", "TAILX", "5551234"):
            assert secret not in out, (text[:40], out[:80])
    out = Redactor(names).redact('<Item Name="Ana > Q" other="1"/>', payload=True)
    assert 'other="1"' in out


def test_xml_sensitive_elements_with_children_and_markup():
    names = Profile(payload_names=("Name",), mode="standard")
    text = (
        "<R><Name>Ana <b>Q</b> tail<!-- note Cy --><![CDATA[Dee E]]><i a='Eve F'>Gus H</i> more</Name>"
        "<Other>keep</Other></R>"
    )
    out = Redactor(names).redact(text, payload=True)
    for secret in ("Ana", "Q<", "tail", "Cy", "Dee", "Eve", "Gus", "more"):
        assert secret not in out
    assert "<Other>keep</Other>" in out
    nested = "<Name><First>Ana</First><Last>Souza</Last></Name>"
    out = Redactor(names).redact(nested, payload=True)
    assert "Ana" not in out and "Souza" not in out and "<First>" in out


def test_xml_depth_limit_fails_closed():
    names = Profile(payload_names=("Name",))
    deep = "<A>" * 400 + "<Name>Zed Y</Name><Other>Dee</Other>" + "</A>" * 400
    out = Redactor(names).redact(deep, payload=True)
    assert "Zed" not in out and "Dee" not in out
    open_sensitive = "<Name>" + "<A>" * 400 + "Zed Y"
    assert "Zed" not in Redactor(names).redact(open_sensitive, payload=True)


def test_escaped_markup_in_payloads_is_scanned_too():
    names = Profile(payload_names=("Name",))
    out = Redactor(names).redact("<Name>Ana&#32;Q</Name> %3CName%3EBea%3C%2FName%3E", payload=True)
    assert "Ana" not in out and "Bea" not in out


def test_label_at_the_very_end_does_not_fail():
    for text in ("user:", "user: ", "mRID =", "Address:\n"):
        assert Redactor().redact(text) == text


def test_review_round_two_regressions():
    r = Redactor()
    assert "SECRETTAIL" not in r.redact("https://public.example.com/%22SECRETTAIL%22")
    assert "SECRETTAIL" not in r.redact('{"u": "https:\\/\\/h.example.com\\/a\\/SECRETTAIL"}')
    assert "SecretSurname" not in r.redact('user: "Ana SecretSurname" x')
    assert "SECRETUSER" not in r.redact("user:\nSECRETUSER")
    names = Profile(payload_names=())
    for text in (
        '{"GID":1234567890123456}',
        '{"mRID":"SECRETMRID"}',
        "<mRID><Value>SECRETMRID</Value></mRID>",
    ):
        out = Redactor(names).redact(text, payload=True)
        assert "1234567890" not in out and "SECRETMRID" not in out
