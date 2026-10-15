"""Generic envelopes declared in families.toml and the fallback detection (ROADMAP 9.14)."""

import tomllib
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

import pytest

from dmslog.ingest.discover import FamiliesConfigError, RuleSet, load_rules
from dmslog.parse.envelope import (
    ADMS,
    BOM,
    ENVELOPES,
    FALLBACK_ENVELOPES,
    LEVELS,
    PLAIN,
    detect_envelope,
    envelope_or_plain,
    get_envelope,
    is_known_envelope,
    register_fallback_envelope,
)
from dmslog.parse.formats import (
    EnvelopeConfigError,
    GenericEnvelope,
    compile_envelopes,
    ensure_registered,
    packaged_envelopes,
)
from dmslog.parse.records import parse_bytes

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
DMY = b"15.10.2026 13:04:05.123 [TID=12] [DEBUG] - Starting step"
YMD_LEVEL = b"2026.10.15 13:04:05.123          INFO: Collecting files"
YMD_COLON = b"2026-10-15 13:04:05.123: Service stopped"
ADMS_LINE = b"2026-01-02 03:04:05.000 [PID=1][TID=0x1] INFO - a"
TS_GROUPS = r"^(?P<year>\d{4})-(?P<month>\d\d)-(?P<day>\d\d) (?P<hour>\d\d):(?P<minute>\d\d):(?P<second>\d\d)"


def _ms(*parts):
    return int(datetime(*parts, tzinfo=UTC).timestamp() * 1000)


def _packaged_data() -> dict:
    text = resources.files("dmslog").joinpath("families.toml").read_text(encoding="utf-8-sig")
    return tomllib.loads(text)


def _table(**over) -> dict:
    table = {"name": "t_env", "header": TS_GROUPS + r" (?P<level>[A-Z]{1,8}) \| ", "examples": []}
    table["examples"] = ["2026-10-15 01:02:03 INFO | x"]
    table.update(over)
    return table


def _one(**over) -> GenericEnvelope:
    return compile_envelopes({"envelope": [_table(**over)]})[0]


# --------------------------------------------------------------------------- packaged set


def test_packaged_envelopes_load_and_are_registered_as_fallbacks():
    names = [env.name for env in packaged_envelopes()]
    assert names == ["dmy_tid_level", "ymd_level_colon", "ymd_colon"]
    ensure_registered()
    for name in names:
        assert get_envelope(name) is FALLBACK_ENVELOPES[name]
        assert is_known_envelope(name)
        assert name not in ENVELOPES  # never in the first detection pass
    assert not is_known_envelope("no_such_envelope")
    with pytest.raises(KeyError):
        get_envelope("no_such_envelope")


def test_dmy_tid_level_fields():
    env = get_envelope("dmy_tid_level")
    line = DMY + b"\r\n"
    h = env.match(line)
    assert h.ts_ms == _ms(2026, 10, 15, 13, 4, 5) + 123
    assert (h.pid, h.tid, h.level, h.process) == (None, 12, LEVELS[b"DEBUGLOG"], None)
    assert line[h.msg_start :] == b"Starting step\r\n"
    padded = env.match(b"01.02.2026 00:00:00.000 [TID=1] [INFO ] - x")
    assert padded.level == LEVELS[b"INFO"]
    assert env.match(b"01.02.2026 00:00:00.000 [TID=1] [WARN ] - x").level == LEVELS[b"WARNING"]
    assert env.match(b"01.02.2026 00:00:00.000 [TID=1] [ODD] - x").level is None  # unknown token


def test_ymd_level_colon_and_ymd_colon_fields():
    h = get_envelope("ymd_level_colon").match(YMD_LEVEL)
    assert (h.ts_ms, h.level) == (_ms(2026, 10, 15, 13, 4, 5) + 123, LEVELS[b"INFO"])
    assert YMD_LEVEL[h.msg_start :] == b"Collecting files"
    h = get_envelope("ymd_colon").match(YMD_COLON)
    assert (h.ts_ms, h.level, h.tid) == (_ms(2026, 10, 15, 13, 4, 5) + 123, None, None)
    assert YMD_COLON[h.msg_start :] == b"Service stopped"


def test_bom_is_skipped_and_counted_in_msg_start():
    env = get_envelope("dmy_tid_level")
    assert env.match(BOM + DMY).msg_start == env.match(DMY).msg_start + 3


def test_impossible_dates_and_non_headers_are_continuations():
    env = get_envelope("dmy_tid_level")
    assert env.match(b"31.02.2026 13:04:05.123 [TID=12] [DEBUG] - x") is None  # 31 February
    assert env.match(b"15.13.2026 13:04:05.123 [TID=12] [DEBUG] - x") is None  # month 13
    assert env.match(b"   at Some.Method()") is None
    assert env.match(b"") is None
    assert env.match(DMY[:20]) is None  # cut inside the header


def test_huge_tid_is_dropped_not_overflowed():
    env = _one(header=TS_GROUPS + r" \[(?P<tid>[0-9A-Fa-f]{1,20})\] ", tid_base=16,
               examples=["2026-10-15 01:02:03 [1] x"])  # fmt: skip
    assert env.match(b"2026-10-15 01:02:03 [FFFFFFFFFFFFFFFFFFFF] x").tid is None
    assert env.match(b"2026-10-15 01:02:03 [ff] x").tid == 255


def test_scan_agrees_with_match_line_by_line():
    env = get_envelope("dmy_tid_level")
    buf = DMY + b"\r\n  at x\r\n" + BOM + DMY + b"\n\n31.02.2026 13:04:05.123 [TID=1] [INFO] - y\n" + DMY
    expected, pos = [], 0
    for line in buf.splitlines(keepends=True):
        h = env.match(line)
        if h is not None:
            expected.append((pos, h))
        pos += len(line)
    assert list(env.scan(buf, 0, len(buf))) == expected


def test_records_group_continuations_under_a_generic_header():
    env = get_envelope("dmy_tid_level")
    error = DMY.replace(b"[DEBUG]", b"[ERROR]")
    data = b"orphan line\n" + DMY + b"\n  detail 1\n  detail 2\n" + error + b"\n"
    records = parse_bytes(data, env)
    assert [(r.orphan, r.nlines, r.level) for r in records] == [
        (True, 1, None),
        (False, 3, LEVELS[b"DEBUGLOG"]),
        (False, 1, LEVELS[b"ERROR"]),
    ]
    assert records[1].msg == "Starting step" and records[1].cont == "  detail 1\n  detail 2"
    assert b"".join(data[r.byte_off : r.byte_off + r.nbytes] for r in records) == data


# --------------------------------------------------------------------------- detection


def test_generic_detection_only_when_builtins_see_nothing():
    assert detect_envelope(DMY + b"\n" + DMY + b"\n").name == "dmy_tid_level"
    assert detect_envelope(YMD_LEVEL + b"\n  more\n" + YMD_LEVEL + b"\n").name == "ymd_level_colon"
    assert detect_envelope(YMD_COLON + b"\n").name == "ymd_colon"  # a one-line file
    # one built-in header line among many generic ones: the built-in still wins (as before 9.14)
    mixed = (DMY + b"\n") * 30 + ADMS_LINE + b"\n"
    assert detect_envelope(mixed) is ADMS
    assert detect_envelope(mixed, fallbacks=False) is ADMS
    assert detect_envelope(DMY + b"\n" + DMY + b"\n", fallbacks=False) is PLAIN


def test_generic_detection_thresholds():
    # 1 header among 9 non-blank lines: below min_lines=2 and below the 0.2 ratio
    assert detect_envelope(DMY + b"\n" + b"text\n" * 8) is PLAIN
    # 2 headers among 20 non-blank lines: min_lines met, ratio 0.1 < 0.2
    assert detect_envelope((DMY + b"\n") * 2 + b"text\n" * 18) is PLAIN
    # 4 headers among 20: accepted; blank lines do not count against the ratio
    assert detect_envelope((DMY + b"\n") * 4 + b"text\n" * 16 + b"\n" * 20).name == "dmy_tid_level"
    assert detect_envelope(b"") is PLAIN
    assert detect_envelope(b"\n\n\n") is PLAIN


def test_accepts_contract():
    env = _one(min_lines=3, min_ratio=0.5)
    assert not env.accepts(0, 0)
    assert env.accepts(1, 1)  # a sample shorter than min_lines needs all its lines
    assert not env.accepts(2, 3)  # 2 < min(3, 3)
    assert env.accepts(3, 6) and not env.accepts(3, 7)  # ceil(0.5 * 7) = 4


def test_fallback_ties_go_to_declaration_order_and_most_hits_wins():
    a = _one(name="t_first")
    b = _one(name="t_second")
    c = _one(name="t_wide", header=TS_GROUPS + r" ", examples=["2026-10-15 01:02:03 x"])
    added = [a, b, c]
    try:
        for env in added:
            register_fallback_envelope(env)
        line = b"2026-10-15 01:02:03 INFO | x\n"
        # a, b and c all see both lines; the packaged envelopes see none; first declared wins
        assert detect_envelope(line * 2).name == "t_first"
        # c also sees the third line: most header lines wins
        assert detect_envelope(line * 2 + b"2026-10-15 01:02:03 y\n").name == "t_wide"
    finally:
        for env in added:
            FALLBACK_ENVELOPES.pop(env.name, None)


def test_fallback_name_cannot_shadow_a_builtin():
    env = _one()
    object.__setattr__(env, "name", "adms")
    with pytest.raises(ValueError, match="already registered"):
        register_fallback_envelope(env)


def test_fixture_logs_keep_their_detection():
    """Regression: no log fixture changes envelope because of the generic set."""
    checked = 0
    for path in sorted(FIXTURES.rglob("*")):
        if not path.is_file() or path.suffix in {".toml", ".json", ".md", ".py", ".xml"}:
            continue
        head = path.read_bytes()[: 64 * 1024]
        assert detect_envelope(head) is detect_envelope(head, fallbacks=False), path.name
        checked += 1
    assert checked >= 20
    assert detect_envelope((FIXTURES / "logs" / "plain.log").read_bytes()) is PLAIN


# --------------------------------------------------------------------------- configuration errors


@pytest.mark.parametrize(
    ("over", "message"),
    [
        ({"name": "Bad-Name"}, "snake_case"),
        ({"name": "plain"}, "built-in"),
        ({"extra": 1}, "unknown keys"),
        ({"header": ""}, "non-empty"),
        ({"header": TS_GROUPS[1:] + " "}, "start with"),
        ({"header": TS_GROUPS + " \u00e9"}, "ASCII"),
        ({"header": r"^(?P<year>\d{4}) "}, "named groups"),
        ({"header": TS_GROUPS + r" (?P<color>\w+) "}, "unknown named groups"),
        ({"header": TS_GROUPS + r" (\w+) "}, "unnamed"),
        ({"header": TS_GROUPS + r" .{1,9}\| "}, "'.' is not allowed"),
        ({"header": TS_GROUPS + r" (?P<level>[A-Z]{1,8}) \| $"}, "anchor"),
        ({"header": TS_GROUPS + r" (?=I)(?P<level>[A-Z]{1,8}) \| "}, "look-arounds"),
        ({"header": TS_GROUPS + r" (?:[A-Z]+)+ \| "}, "repeat bound"),
        ({"tid_base": 8}, "tid_base"),
        ({"tid_base": True}, "tid_base"),
        ({"min_lines": 0}, "min_lines"),
        ({"min_ratio": 1.5}, "min_ratio"),
        ({"levels": {"NOTE": "LOUD"}}, "must map"),
        ({"levels": {"bad token": "INFO"}}, "invalid level token"),
        ({"levels": []}, "must be a table"),
        ({"description": 3}, "description"),
        ({"examples": []}, "non-empty list"),
        ({"examples": ["not a header"]}, "not recognized"),
        (
            {
                "header": TS_GROUPS + r" (?P<process>[A-Za-z: ]{1,40}): ",
                "examples": ["2026-10-15 01:02:03 Svc: x"],
            },
            "may also match what follows",
        ),
        # review of PR #196: the probes accepted this one; appending "Z: " moved the end of the header
        (
            {
                "header": TS_GROUPS + r" (?P<process>[A-Z: ]{1,40}): ",
                "examples": ["2026-10-15 01:02:03 SVC: "],
            },
            "may also match what follows",
        ),
        # a variable repeat at the end of the header can always grow with appended text
        ({"header": TS_GROUPS + r" (?P<level>[A-Z]{1,8})"}, "may also match what follows"),
        ({"header": TS_GROUPS + r" \[(?P<pid>\d{1,9})\]\d{0,3}"}, "may also match what follows"),
        # optional group followed by something it can start with
        ({"header": TS_GROUPS + r"(?:\.(?P<ms>\d{1,6}))? ?(?P<level>[A-Z]{1,8}) \| "}, None),
        # the separator goes at the end of an optional group, not at its start (both start with " ")
        ({"header": TS_GROUPS + r" (?:(?P<pid>\d{1,6}) )?(?P<level>[A-Z]{1,8}) \| "}, None),
        (
            {"header": TS_GROUPS + r"(?: (?P<pid>\d{1,6}))? (?P<level>[A-Z]{1,8}) \| "},
            "may also match what follows",
        ),
        (
            {"header": TS_GROUPS + r" (?P<pid>\d{1,6})? ?(?P<tid>\d{1,6}) (?P<level>[A-Z]{1,8}) \| "},
            "may also match what follows",
        ),
        # no alternation at all in the restricted grammar (a class covers the level tokens)
        (
            {
                "header": TS_GROUPS + r" (?P<level>INFO|INFORMATION) \| ",
                "examples": ["2026-10-15 01:02:03 INFO | x"],
            },
            "alternation",
        ),
        # 2nd review of PR #196: a fixed repeat of a group hid a variable repeat from the follow check
        (
            {"header": TS_GROUPS + r" (?P<level>[A-Z]{1,8}){1}", "examples": ["2026-10-15 01:02:03 INFO"]},
            "the only repeat of a group",
        ),
        ({"header": TS_GROUPS + r" (?:[A-Z]{1,8}){2}"}, "the only repeat of a group"),
        # 2nd review: \S is wider in a bytes regex (0xA0 included) than its str set; no \S/\s/\w at all
        (
            {
                "header": TS_GROUPS + r" (?P<process>\S{1,40})\xa0",
                "examples": ["2026-10-15 01:02:03 Svc\u00a0"],
            },
            "only literals, ranges and",
        ),
        ({"header": TS_GROUPS + r" (?P<level>[\w]{1,8}) \| "}, "only literals, ranges and"),
        ({"header": TS_GROUPS + r" (?P<level>[^ ]{1,8}) \| "}, "negated"),
        ({"header": TS_GROUPS + r" [^|] \| "}, "negated"),
        # explicit bytes >= 0x80 are allowed when listed, and checked with their bytes semantics
        ({"header": TS_GROUPS + r" (?P<level>[A-Z\xa0]{1,8})\xa0\| "}, "may also match what follows"),
        # grammar limits
        ({"header": TS_GROUPS + r"(?: (?P<pid>[0-9]{1,6})(?: \[(?P<tid>[0-9]{1,6})\])?)? \| "}, "nested"),
        ({"header": TS_GROUPS + r"(?: ?)? (?P<level>[A-Z]{1,8}) \| "}, "empty string"),
        ({"header": TS_GROUPS + r" (?P<level>[A-Z]+) \| "}, "repeat bound"),
        (
            {"header": TS_GROUPS + r" (?P<level>[A-Z]{1,8}) \|(?:ab)?(?:cd)?(?:ef)?(?:gh)?(?:ij)? "},
            "more than 4",
        ),
        ({"header": TS_GROUPS + r" (?:I|[HI]X)(?P<level>[A-Z]{1,8}) \| "}, "alternation"),
        # wrongly typed values must be configuration errors, never TypeError later (review of PR #196)
        ({"tid_base": 10.0}, "tid_base"),
        ({"tid_base": "16"}, "tid_base"),
        ({"min_lines": 2.0}, "min_lines"),
        ({"min_ratio": "0.5"}, "min_ratio"),
        ({"levels": {"NOTE": ["INFO"]}}, "must map"),
        ({"levels": {"NOTE": 7}}, "must map"),
        ({"name": 3}, "snake_case"),
        ({"header": ["^x"]}, "non-empty string"),
        ({"examples": "2026-10-15 01:02:03 INFO | x"}, "non-empty list"),
        ({"examples": [3]}, "non-empty list"),
        # valid as a str regex, invalid as a bytes regex
        ({"header": TS_GROUPS + r" \u0041(?P<level>[A-Z]{1,8}) \| "}, "does not compile"),
        ({"header": TS_GROUPS + r" \N{LATIN CAPITAL LETTER A}(?P<level>[A-Z]{1,8}) \| "}, "does not compile"),
    ],
)
def test_invalid_envelope_tables_are_rejected(over, message):
    if message is None:  # control: an unambiguous header with optional groups is accepted
        compile_envelopes({"envelope": [_table(**over)]})
        return
    with pytest.raises(EnvelopeConfigError, match=message):
        compile_envelopes({"envelope": [_table(**over)]})


def test_tid_absent_from_the_examples_still_parses_later_headers():
    """Review of PR #196: with a float ``tid_base`` the examples (no TID) passed and a later TID raised."""
    env = _one(
        header=TS_GROUPS + r" (?:\[(?P<tid>[0-9a-f]{1,8})\] )?(?P<level>[A-Z]{1,8}) \| ",
        tid_base=16,
    )
    assert env.match(b"2026-10-15 01:02:03 [1f] INFO | x").tid == 31
    assert env.match(b"2026-10-15 01:02:03 INFO | x").tid is None


def test_negative_or_huge_ids_are_dropped():
    """Review of PR #196: a signed group gave ids SQLite cannot bind (OverflowError)."""
    env = _one(
        header=TS_GROUPS + r" (?P<pid>-?\d{1,30}) (?P<level>[A-Z]{1,8}) \| ",
        examples=["2026-10-15 01:02:03 12 INFO | x"],
    )
    assert env.match(b"2026-10-15 01:02:03 12 INFO | x").pid == 12
    assert env.match(b"2026-10-15 01:02:03 -5 INFO | x").pid is None
    assert env.match(b"2026-10-15 01:02:03 -" + b"9" * 30 + b" INFO | x").pid is None
    assert env.match(b"2026-10-15 01:02:03 " + b"9" * 30 + b" INFO | x").pid is None


def test_rule_values_with_wrong_types_are_configuration_errors():
    for bad in ({"anchor": "yes"}, {"indexable": 1}, {"pattern": ["x"]}, {"layout": ["single"]},
                {"process": 3, "layout": "single"}, {"family": 3}):  # fmt: skip
        data = _packaged_data()
        rule = {"family": "TOOL", "pattern": r"tool\.log$", **bad}
        data["rule"] = [rule, *data["rule"]]
        with pytest.raises(FamiliesConfigError):
            RuleSet.from_dict(data)
    data = _packaged_data()
    data["layouts"] = {**data["layouts"], "single": 3}
    with pytest.raises(FamiliesConfigError):
        RuleSet.from_dict(data)


def test_duplicate_and_malformed_envelope_lists():
    with pytest.raises(EnvelopeConfigError, match="duplicate"):
        compile_envelopes({"envelope": [_table(), _table()]})
    with pytest.raises(EnvelopeConfigError, match="array of tables"):
        compile_envelopes({"envelope": {"name": "x"}})
    with pytest.raises(EnvelopeConfigError, match="must be a table"):
        compile_envelopes({"envelope": ["x"]})
    assert compile_envelopes({}) == []


def test_custom_levels_extend_the_defaults():
    env = _one(levels={"Severe": "FATAL"})
    assert env.match(b"2026-10-15 01:02:03 SEVERE | x").level == LEVELS[b"FATAL"]
    assert env.match(b"2026-10-15 01:02:03 INFO | x").level == LEVELS[b"INFO"]


# --------------------------------------------------------------------------- families.toml / discover


def test_rules_may_name_a_declared_envelope_and_nothing_else():
    data = _packaged_data()
    data["rule"] = [{"family": "TOOL", "pattern": r"tool\.log$", "envelope": "ymd_colon"}, *data["rule"]]
    rules = RuleSet.from_dict(data)
    assert rules.classify("x/tool.log").envelope == "ymd_colon"
    data["rule"][0]["envelope"] = "no_such"
    with pytest.raises(FamiliesConfigError, match="envelope must be one of"):
        RuleSet.from_dict(data)
    broken = _packaged_data()
    broken["envelope"][0]["header"] = "no anchor"
    with pytest.raises(FamiliesConfigError, match="start with"):
        RuleSet.from_dict(broken)


def test_envelope_tables_do_not_change_path_classification():
    """Regression: the [[envelope]] tables never change the family of a path."""
    with_envelopes = load_rules()
    data = _packaged_data()
    del data["envelope"]
    without = RuleSet.from_dict(data)
    paths = [
        "Log/dnaApp_zone1/SVC/WfmAdapter_1111.log",
        "Log/dnaApp_zone1/SVC/WfmAdapter_1111.sensitive.log.0",
        "Log/BLTHost.SVC.log",
        "Log/oasErrLog.log",
        "Log/dnaService.SVC.log.2",
        "Log/NMC.log",
        "Log/arbIP_77.log",
        "Log/ConfigTool_42.log",
        "Log/Host1_LastShutdownReason.log",
        "Deployment/Logs/setup/install.log",
        "Log/ERS.log",
        "Log/AdminPager.log",
        "Log/config.xml",
        "Log/computer_info.txt",
        "Log/whatever.bin",
    ]
    for path in paths:
        assert with_envelopes.classify(path) == without.classify(path), path


def test_envelope_or_plain_never_raises():
    assert envelope_or_plain("adms") is ADMS
    assert envelope_or_plain("ymd_colon").name == "ymd_colon"
    assert envelope_or_plain("removed_generic") is PLAIN


def test_deeply_nested_optional_groups_are_rejected_fast():
    """2nd review of PR #196: nested optional groups made the old check exponential (depth 22 > 2 s)."""
    import time

    nested = ""
    for ch in reversed("abcdefghijklmnopqrstuv"):
        nested = f"(?:{ch}{nested})?"
    header = TS_GROUPS + " " + nested + r"\| "
    started = time.process_time()
    with pytest.raises(EnvelopeConfigError, match="nested"):
        compile_envelopes({"envelope": [_table(header=header)]})
    assert time.process_time() - started < 0.5
    longest = TS_GROUPS + " " + "x" * 1000
    with pytest.raises(EnvelopeConfigError, match="longer than"):
        compile_envelopes({"envelope": [_table(header=longest)]})
