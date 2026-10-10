"""Template engine: schema validation, dispatch, fields, continuation parsers (ARQUITETURA 6.3)."""

# ruff: noqa: E501 - real log lines and template regexes are kept on one line, as in the TOML files

import itertools
import time
from importlib import resources

import pytest

from dmslog.parse import templates as T
from dmslog.parse.records import Record
from dmslog.parse.templates import (
    DuplicateError,
    InvalidRegexError,
    SchemaError,
    TemplateError,
    TemplateReferenceError,
    UnsafeRegexError,
    load_catalog,
    load_texts,
    parse_family,
)

# Real templates from docs/formato/*.md (one or two per family, values anonymized): the schema
# must express every kind found there (prefix and prefix-less regexes, exact text, TABs and
# surrounding spaces, typed fields, continuation parsers, payloads, aliases, sev from a field).
REAL_COMMON = r"""
family = "_common"

[[template]]
code = "GEN-STA-03"
class = "statistics"
sev = 5
regex = '^\[Communication(?P<chan>\d+)\] - By (?P<order>call count|Request Message Size Total|Response Message Size Total):$'
fields = { chan = "int" }
cont = "stats_csv"

[[template]]
code = "GEN-STA-02"
aliases = ["WFM-090", "ONT-030", "COM-002", "COM-007"]
class = "statistics"
sev = 5
regex = '^\[Communication(?P<chan>\d+)\] - \[TOTAL\]\[(?P<side>Client|Service)\] Call count: (?P<calls>\d+), Request bytes: (?P<req_bytes>\d+) \(avg\. (?P<req_avg>[\d.]+)\), Response bytes: (?P<resp_bytes>\d+) \(avg\. (?P<resp_avg>[\d.]+)\)\. Total operations: (?P<ops>\d+)$'
fields = { calls = "int", req_bytes = "int", resp_bytes = "int", req_avg = "float", resp_avg = "float", ops = "int" }

[[template]]
code = "GEN-ERR-01"
aliases = ["WFM-050", "OSR-005"]
class = "validation"
text = "Detected errors:"
cont = "codes_per_line"
keys = [{ field = "codes", type = "errcode" }]

[[template]]
code = "GEN-EXC-01"
class = "error"
sev = 2
regex = '^Non Fatal Exception (?P<exc_type>[\w.`]+): (?P<exc_msg>.*)$'
cont = "dotnet_stack"
keys = [{ field = "exc_type", type = "exc_type" }]

[[template]]
code = "GEN-MSG-01"
aliases = ["WFM-100", "ONT-100"]
class = "req_resp"
refs_sensitive = true
regex = "^Message with MessageID: '(?P<msgid>[^']*)' will be logged in sensitive log\\.$"
keys = ["msgid"]

[[template]]
code = "GEN-PLD-01"
class = "req_resp"
stream = "sensitive"
regex = '^Message(?: content)?: (?=<)'
payload = { fmt = "xml", start = "match_end", strip_suffix = ".", role = "by_root" }

[[template]]
code = "GEN-EVT-02"
class = "other"
text = "Publishing EventData:"
cont = "kv_colon"

[[template]]
code = "GEN-C2T-02"
class = "other"
sev = 5
fts = false
regex = '^\[Communication2\] - TCP client connection id = (?P<conn_id>\d+) (?P<dir>sending|receiving) message msgId = (?P<c2_msgid>\d+), msgType = (?P<msg_type>\w+), msgSize = (?P<size>\d+)\.$'
fields = { conn_id = "int", c2_msgid = "int", size = "int" }
"""

REAL_WFM = r"""
family = "AWFM"
extends = ["_common"]

[payload_defaults]
request = { direction = "in", peer = "WFM" }
content = { direction = "out", peer = "CRS" }

[[template]]
code = "WFM-010"
class = "start"
regex = '^(?P<op>[A-Z][\w ]+?) request received\. Request with ID "(?P<request_id>[0-9a-fA-F-]{36})"\. Message content w\w+ in sensitive log\.$'
window = 992
fields = { request_id = "guid" }
keys = ["request_id"]
refs_sensitive = true

[[template]]
code = "WFM-081"
class = "error"
sev = 2
regex = '^ERROR - Processing "(?P<event_type>\w+)" message failed\. ExceptionType: "(?P<exc_type>[^"]+)" Reason: (?P<reason>.*)$'
cont = "dotnet_stack"
keys = [{ field = "exc_type", type = "exc_type" }]

[[template]]
code = "WFM-030"
class = "req_resp"
text = "Callback received:"
cont = { parser = "line_regex", regex = '^(?P<cb_type>\w+) GID: (?P<gid>\d+), Errors count: (?P<errors>\d+)\.' }
fields = { gid = "int", errors = "int" }
keys = ["gid"]

[[template]]
code = "WFM-040"
class = "validation"
regex = "^\\[Info\\] AlternateRadioID value will be ignored for vehicle with MRID '(?P<mrid>.*)\\.$"
keys = ["mrid"]

[[template]]
code = "WFM-120"
class = "req_resp"
regex = '^Content being sent to (?P<target>CRS|WMS):$'
payload = { fmt = "xml", start = "first_cont_lt", role = "content" }

[[template]]
code = "WFM-123"
class = "req_resp"
regex = '^Request IDs returned by (?P<target>CRS|IMS) are: "(?P<gids>[^"]*)"\.$'
fields = { gids = "list" }
keys = [{ field = "gids", type = "gid" }]

[[template]]
code = "WFM-001"
class = "other"
regex = '^New "(?P<map>\w+)" map content:$'
cont = "kv_tab"

[[operation]]
name_field = "op"
start = ["WFM-010"]
end_ok = ["WFM-090"]
fail = ["WFM-081"]
"""

REAL_OTHERS = {
    "aont.toml": r"""
family = "AONT"
extends = ["_common"]
[[template]]
code = "ONT-011"
class = "req_resp"
regex = '^Sending "(?P<msg_type>\w+)" message: (?P<message_id>[0-9a-f-]{36}) with verb: (?P<verb>\w+)\.\.\.$'
keys = [{ field = "message_id", type = "msgid" }]
""",
    "aavl.toml": r"""
family = "AAVL"
extends = ["_common"]
[[template]]
code = "AVL-001"
class = "start"
regex = '^"(?P<event>\w+)": (?P<message_id>[0-9a-f-]{36}) message processing started\.\.\.$'
keys = [{ field = "message_id", type = "msgid" }]
[[template]]
code = "AVL-005"
class = "validation"
text = "Details regarding validation errors can be found adapters sensistive log."
""",
    "aosr.toml": r"""
family = "AOSR"
extends = ["_common"]
[[template]]
code = "OSR-001"
class = "start"
regex = '^\[GetIncidents start\] - Get incidents request message Message Id: "(?P<msgid>[^"]*)" Context: "(?P<ctx>[^"]*)" Search filter: "(?P<filter>[^"]*)" received from user (?P<user>.*?)\. Processing message\.\.\.$'
keys = ["msgid"]
[[template]]
code = "OSR-004"
class = "end"
regex = '^\[GetIncidents end\] - Get incidents request message Message Id: "(?P<msgid>[^"]*)" .* failed\.$'
cont = "dotnet_stack"
""",
    "asss.toml": r"""
family = "ASSS"
[[template]]
code = "SSS-001"
class = "req_resp"
sev = 5
fts = false
text = "Adapter is on ACTIVE site."
""",
    "acrm.toml": r"""
family = "ACRM"
extends = ["_common"]
[[template]]
code = "CRM-003"
class = "end"
regex = '^\[GetTroubleTickets finished\] - Message with message ID (?P<msgid>\S*) ?processed\.$'
""",
    "aclc.toml": r"""
family = "ACLC"
extends = ["_common"]
[[template]]
code = "CLC-007"
class = "validation"
regex = '^\[Validation (?P<vsev>Info|Warning|Error|Critical)\]: (?P<code>\w+)$'
sev_field = "vsev"
sev_map = { Critical = 2, Error = 2, Warning = 3, Info = 4 }
keys = [{ field = "code", type = "errcode" }]
""",
    "asmn.toml": r"""
family = "ASMN"
extends = ["_common"]
[[template]]
code = "SMN-S110"
class = "req_resp"
stream = "sensitive"
text = "[WmsSubscriber] - Data received from publication:"
cont = "kv_colon"
payload = { fmt = "xml_seq", start = "first_cont_lt", role = "publication", direction = "in", peer = "DMS_PUBSUB" }
""",
    "blthost.toml": r"""
family = "BLTHOST"
[[template]]
code = "BLT-011"
class = "end"
regex = "^\\[IsReadyForModeSwitchProcessor\\] IsReadyForModeSwitch BLT execution completed\\. Result: LocalAndDownstreamNodesReady status: '(?P<ready>True|False)'$"
""",
}

GUID = "0b5c1f7e-1111-2222-3333-444455556666"


@pytest.fixture(scope="module")
def real():
    return load_texts({"_common.toml": REAL_COMMON, "awfm.toml": REAL_WFM, **REAL_OTHERS})


def tiny(body, family="X", extra=None):
    files = {"x.toml": f'family = "{family}"\n' + body}
    files.update(extra or {})
    return load_texts(files)


def tpl(regex=None, code="X-001", text=None, extra=""):
    src = f'[[template]]\ncode = "{code}"\nclass = "other"\n'
    if regex is not None:
        src += f"regex = '''{regex}'''\n"
    if text is not None:
        src += f"text = '''{text}'''\n"
    return src + extra


# --------------------------------------------------------------------------- real templates


@pytest.mark.parametrize(
    ("family", "sensitive", "msg", "cont", "code", "fields"),
    [
        (
            "AWFM", False,
            f'Changed crew assignments request received. Request with ID "{GUID.upper()}". '
            "Message content wirtten in sensitive log.",
            None, "WFM-010", {"op": "Changed crew assignments", "request_id": GUID},
        ),
        (
            "AWFM", False,
            'ERROR - Processing "ChangedCrewsEvent" message failed. ExceptionType: "System.TimeoutException" '
            "Reason: The operation timed out. StackTrace: ",
            "   at Example.CrsDataManager.Apply() in F:\\PD\\8308066\\1\\Product\\CrsDataManager.cs:line 120\n"
            "   at Example.Processor.Run()",
            "WFM-081",
            {"event_type": "ChangedCrewsEvent", "exc_type": "System.TimeoutException",
             "frame": "Example.CrsDataManager.Apply", "build": "8308066"},
        ),
        (
            "AWFM", False, "Callback received:",
            "OMS_CREW_CHANGE_REQ GID: 288230376151711744, Errors count: 0. For Status value check sensitive log.",
            "WFM-030", {"cb_type": "OMS_CREW_CHANGE_REQ", "gid": 288230376151711744, "errors": 0},
        ),
        (
            "AWFM", False, "[Info] AlternateRadioID value will be ignored for vehicle with MRID 'ABC 1234.",
            None, "WFM-040", {"mrid": "ABC 1234"},
        ),
        ("AWFM", False, "Content being sent to CRS:", "<ArrayOfAssignmentStatusInfo/>", "WFM-120", {"target": "CRS"}),
        (
            "AWFM", False, 'Request IDs returned by IMS are: "1001,1002,1003".', None, "WFM-123",
            {"target": "IMS", "gids": ["1001", "1002", "1003"]},
        ),
        ("AWFM", False, 'New "OMS_CREW_VEHICLE" map content:', "\tABC1234 - 1001\n\tABC1235 - 1002", "WFM-001",
         {"map": "OMS_CREW_VEHICLE", "items": 2}),
        (
            "AWFM", False, "[Communication2] - By call count: ",
            "[methodname, call count, request total size, response total size]\n"
            "TelventDMS.Common.ServiceContracts.IGDAServiceQueryContract.GetExtentValues,2494278,356696826,194553684\n",
            "GEN-STA-03",
            {"chan": 2, "order": "call count", "n_rows": 1,
             "rows": [["TelventDMS.Common.ServiceContracts.IGDAServiceQueryContract.GetExtentValues",
                       2494278, 356696826, 194553684]]},
        ),
        (
            "AWFM", False,
            "[Communication2] - [TOTAL][Service] Call count: 21, Request bytes: 39548 (avg. 1883.238), "
            "Response bytes: 504 (avg. 24). Total operations: 1",
            None, "GEN-STA-02", {"side": "Service", "calls": 21, "req_avg": 1883.238, "resp_avg": 24.0, "ops": 1},
        ),
        ("AWFM", False, "Detected errors:", "AssignmentDoesNotExist\nInvalidStatusTransition\n", "GEN-ERR-01",
         {"codes": ["AssignmentDoesNotExist", "InvalidStatusTransition"]}),
        ("AWFM", True, 'Message content: <ChangedCrews xmlns="http://iec.ch/TC57/2011/CrewsMessage">',
         "  <Header/>\n</ChangedCrews>.", "GEN-PLD-01", {}),
        ("AWFM", False, "Publishing EventData:", "Timestamp: 2026-01-01 10:00:00\nDetails: Something=1",
         "GEN-EVT-02", {"n_items": 2, "kv": {"Timestamp": "2026-01-01 10:00:00", "Details": "Something=1"}}),
        (
            "AONT", False, f'Sending "IncidentsEventMessageType" message: {GUID} with verb: Changed...', None,
            "ONT-011", {"msg_type": "IncidentsEventMessageType", "message_id": GUID, "verb": "Changed"},
        ),
        ("AAVL", False, f'"ChangedVehiclesCoordinatesEvent": {GUID} message processing started...', None,
         "AVL-001", {"event": "ChangedVehiclesCoordinatesEvent"}),
        ("AAVL", False, "\t\t Details regarding validation errors can be found adapters sensistive log.", None,
         "AVL-005", {}),
        (
            "AOSR", False,
            '[GetIncidents start] - Get incidents request message Message Id: "" Context: "active" '
            'Search filter: "INC 123" received from user . Processing message...',
            None, "OSR-001", {"msgid": "", "ctx": "active", "filter": "INC 123", "user": ""},
        ),
        (
            "AOSR", False,
            '[GetIncidents end] - Get incidents request message Message Id: "" Context: "archived" '
            'Search filter: "" failed. ',
            "Reason: One or more errors occurred.\nStack trace: System.AggregateException: One or more errors "
            "occurred. ---> System.Data.SqlClient.SqlException: Execution Timeout Expired.\n   at A.B.Read()",
            "OSR-004", {"exc_type": "System.AggregateException", "inner_type": "System.Data.SqlClient.SqlException",
                        "frame": "A.B.Read"},
        ),
        ("ASSS", False, " Adapter is on ACTIVE site. ", None, "SSS-001", {}),
        ("ACRM", False, "[GetTroubleTickets finished] - Message with message ID  processed.", None, "CRM-003",
         {"msgid": ""}),
        ("ACLC", False, "\t\t[Validation Warning]: CommentsMissing", None, "CLC-007", {"code": "CommentsMissing"}),
        (
            "ASMN", True, "[WmsSubscriber] - Data received from publication:",
            "DmsType: SWITCHING_STEP\nOperation: Changed\n<ResourceDescription>\n  <id gid=\"1\"/>\n"
            "</ResourceDescription>",
            "SMN-S110", {"kv": {"DmsType": "SWITCHING_STEP", "Operation": "Changed"}},
        ),
        (
            "BLTHOST", False,
            "[IsReadyForModeSwitchProcessor] IsReadyForModeSwitch BLT execution completed. Result: "
            "LocalAndDownstreamNodesReady status: 'False' ",
            "ResultSet contains:\n", "BLT-011", {"ready": "False"},
        ),
    ],
)  # fmt: skip
def test_real_templates_are_expressible(real, family, sensitive, msg, cont, code, fields):
    tm = real.for_family(family, sensitive=sensitive).classify_text(msg, cont)
    assert tm is not None, msg
    assert tm.code == code
    for k, v in fields.items():
        assert tm.fields[k] == v, k


def test_real_keys_sev_payload_and_flags(real):
    wfm = real.for_family("AWFM", sensitive=False)
    tm = wfm.classify_text(f'Changed crews request received. Request with ID "{GUID}". Message content written '
                           "in sensitive log.")  # fmt: skip
    assert tm.keys == [("request_id", GUID)] and tm.refs_sensitive and tm.cls == "start"
    tm = wfm.classify_text('Request IDs returned by CRS are: "1,2".')
    assert tm.keys == [("gid", "1"), ("gid", "2")]
    tm = wfm.classify_text("Detected errors:", "A1\n\nB2\nnot a code line\n")
    assert tm.keys == [("errcode", "A1"), ("errcode", "B2")]
    tm = wfm.classify_text("[Communication2] - TCP client connection id = 3 sending message msgId = 9, "
                           "msgType = Request, msgSize = 120.")  # fmt: skip
    assert tm.fts is False and tm.sev_hint == 5 and tm.fields["size"] == 120
    sens = real.for_family("AWFM", sensitive=True)
    msg = "  Message: <ns0:X>"
    tm = sens.classify_text(msg, "</ns0:X>.")
    assert tm.payload == T.PayloadSpec("xml", "match_end", ".", "by_root")
    assert msg[tm.match_end :] == "<ns0:X>"
    assert wfm.payload_defaults["request"] == {"direction": "in", "peer": "WFM"}
    clc = real.for_family("ACLC", sensitive=False)
    assert clc.classify_text("[Validation Critical]: InvalidUrl").sev_hint == 2
    assert clc.classify_text("[Validation Info]: HashMissing").sev_hint == 4


def test_stream_filter(real):
    assert real.for_family("AWFM", sensitive=False).classify_text("Message: <X>") is None
    assert real.for_family("AWFM", sensitive=True).classify_text("Message: <X>").code == "GEN-PLD-01"
    assert real.for_family("ASMN", sensitive=False).classify_text(
        "[WmsSubscriber] - Data received from publication:") is None  # fmt: skip


def test_aliases_and_operations_resolve(real):
    assert real.resolve("WFM-090") == "GEN-STA-02"
    assert real.resolve("GEN-STA-02") == "GEN-STA-02"
    assert real.resolve("NOPE-1") is None
    assert real.template("ONT-100").code == "GEN-MSG-01"
    with pytest.raises(KeyError):
        real.template("NOPE-1")
    (op,) = real.for_family("AWFM", sensitive=False).operations()
    assert op["end_ok"] == ["GEN-STA-02"] and op["start"] == ["WFM-010"] and op["name_field"] == "op"


def test_unknown_family_uses_common_library(real):
    tset = real.for_family("UNKNOWN", sensitive=False)
    assert {t.code for t in tset.templates} == {t.code for t in real.families["_common"].templates} - {
        "GEN-PLD-01"
    }
    assert tset.classify_text("Detected errors:").code == "GEN-ERR-01"
    assert tset.classify_text("something else") is None


def test_ids_are_provisional_then_bound(real):
    codes = sorted(t.code for t in real.templates())
    assert [real.tpl_id(c) for c in codes] == list(range(1, len(codes) + 1))
    cat = load_texts({"_common.toml": REAL_COMMON, "awfm.toml": REAL_WFM})
    wfm = cat.for_family("AWFM", sensitive=False)
    cat.bind_ids({"WFM-001": 501, "COM-002": 502, "UNKNOWN-1": 9})
    assert wfm.classify_text('New "M" map content:').tpl_id == 501
    assert cat.tpl_id("GEN-STA-02") == 502
    assert cat.for_family("AONT", sensitive=False).classify_text("Detected errors:").tpl_id == cat.tpl_id(
        "GEN-ERR-01"
    )


def test_def_version_and_pattern(real):
    t = real.template("GEN-ERR-01")
    assert t.pattern == "Detected errors:" and len(t.def_version) == 12
    assert real.template("WFM-001").pattern.startswith("^New")


# --------------------------------------------------------------------------- classify behaviour


def test_classify_record_strip_offsets_and_truncation():
    cat = tiny(tpl(r"^Hello (?P<who>\w+)(?P<rest>.*)$") + tpl(text="Bye now", code="X-002"))
    ts = cat.for_family("X", sensitive=False)
    rec = Record(0, 1, 1, 0, 1, 1, 7, "\t Hello world ", None, False)
    tm = ts.classify(rec)
    assert tm.code == "X-001" and tm.fields == {"who": "world", "rest": ""} and tm.match_end == 13
    assert ts.classify_text("  Bye now ").match_end == 9
    long_msg = "Hello x" + "y" * (T.MAX_MATCH_CHARS * 2)
    assert ts.classify_text(long_msg) is None  # does not fit the window: no match (round 7)
    assert ts.classify_text("") is None and ts.classify_text("   ") is None


def test_classify_never_raises_on_bad_input():
    ts = tiny(tpl(r"^a(?P<x>\d+)", extra='fields = { x = "int" }\ncont = "dotnet_stack"\n')).for_family(
        "X", sensitive=False
    )
    assert ts.classify(object()) is None
    assert ts.classify_text(None) is None  # type: ignore[arg-type]
    assert ts.classify_text("a1", cont=123) is not None  # broken continuation only loses its fields


def test_first_match_wins_and_exact_text_first():
    body = (
        tpl(r"^Same (?P<a>\w+)$", "X-001")
        + tpl(r"^Same thing$", "X-002")
        + tpl(text="Same thing", code="X-003")
    )
    ts = tiny(body).for_family("X", sensitive=False)
    assert ts.classify_text("Same thing").code == "X-003"  # exact texts are looked up first
    assert ts.classify_text("Same other").code == "X-001"


def test_family_templates_come_before_library():
    lib = {"_lib.toml": 'family = "_lib"\n' + tpl(r"^Hello (?P<x>.*)$", "LIB-001")}
    ts = tiny('extends = ["_lib"]\n' + tpl(r"^Hello world$", "X-001"), extra=lib).for_family(
        "X", sensitive=False
    )
    assert ts.classify_text("Hello world").code == "X-001"
    assert ts.classify_text("Hello there").code == "LIB-001"


def test_field_types():
    body = tpl(
        r"^i=(?P<i>-?\d*) h=(?P<h>0x[0-9a-f]+) g=(?P<g>[0-9A-F-]+) f=(?P<f>[\d.]+) l=(?P<l>[^;]*); "
        r"big=(?P<big>\d+) bad=(?P<bad>\S+)(?: opt=(?P<opt>\d+))?$",
        extra='fields = { i = "int", h = "hex", g = "guid", f = "float", l = "list", big = "int", bad = "float", '
        'opt = "int" }\n',
    )
    ts = tiny(body).for_family("X", sensitive=False)
    tm = ts.classify_text(
        "i=-12 h=0x1a8 g=ABC-DEF f=97.318 l=\"a\", 'b' ,,c; big=12345678901234567890 bad=1.2.3"
    )
    assert tm.fields == {
        "i": -12, "h": 0x1A8, "g": "abc-def", "f": 97.318, "l": ["a", "b", "c"],
        "big": "12345678901234567890", "bad": "1.2.3",
    }  # fmt: skip
    assert ts.classify_text("i= h=0x1 g=A f=1 l=; big=1 bad=x").fields["i"] == ""  # empty capture stays ""


def test_dispatch_tries_few_candidates(real):
    ts = real.for_family("AWFM", sensitive=False)
    assert len(ts) > 12
    for msg in ("Content being sent to CRS:", '"Ev": x message processing started...', "Unknown words here"):
        assert ts.candidates(msg) <= 6
    # prefix-less templates are only tried where their first character fits
    assert ts.candidates("changed crews request received.") < ts.candidates("Changed crews request received.")


def test_leading_alternatives_become_prefixes():
    ts = tiny(
        tpl(r"^(?:Creating new dal proxy\.|New dal proxy created\. Alias = (?P<alias>\S+))$")
    ).for_family("X", sensitive=False)
    t = ts.templates[0]
    assert t.alt_prefixes == ("Creating new dal proxy.", "New dal proxy created. Alias = ")
    assert ts.classify_text("New dal proxy created. Alias = a1").fields == {"alias": "a1"}
    assert ts.classify_text("Creating new dal proxy.").fields == {}


def test_memo_is_bounded(monkeypatch):
    monkeypatch.setattr(T, "MAX_DISPATCH_KEYS", 3)
    ts = tiny(tpl(r"^(?P<n>\d+) items$")).for_family("X", sensitive=False)
    for n in range(100, 140):
        assert ts.classify_text(f"{n} items").fields == {"n": str(n)}
    assert len(ts._memo) == 3


def test_accepted_regex_stays_fast_on_adversarial_input():
    # Quadratic in its window: accepted only with a declared window, which bounds the damage.
    with pytest.raises(UnsafeRegexError, match="declare a smaller window"):
        tiny(tpl(r"^a(?P<x>.*)b(?P<y>.*)c$"))
    ts = tiny(tpl(r"^a(?P<x>.*)b(?P<y>.*)c$", extra="window = 704\n")).for_family("X", sensitive=False)
    t0 = time.perf_counter()
    for _ in range(3):
        assert ts.classify_text("a" + "b" * 200_000) is None
    assert time.perf_counter() - t0 < 25  # catastrophe threshold only


# --------------------------------------------------------------------------- texts longer than the window


def _win_extra(window):
    return "" if window == T.MAX_MATCH_CHARS else f"window = {window}\n"


def _only(regex, window, **kw):
    return tiny(tpl(regex, extra=_win_extra(window), **kw)).for_family("X", sensitive=False)


_REVIEW_RX = r"^a(?P<x>[^b]*)b(?P<y>.*)c$"
_GREEDY_RX = r"^(?P<x>.*)b"  # round 7: the cut slice gave x="" for a 129-character text
_WINDOWS_UNDER_TEST = (128, T.MAX_MATCH_CHARS)


def _classify_cold_and_warm(ts, text):
    """Result on a fresh dispatch cache, then again with the key memoized: they must agree."""
    cold = ts.classify_text(text)
    assert ts._memo, "the dispatch key was not memoized"
    warm = ts.classify_text(text)
    assert (cold is None) == (warm is None)
    if cold is not None:
        assert (cold.code, cold.fields, cold.match_end) == (warm.code, warm.fields, warm.match_end)
    return cold


@pytest.mark.parametrize("window", _WINDOWS_UNDER_TEST)
def test_template_applies_only_to_messages_that_fit_its_window(window):
    ts = _only(_REVIEW_RX, window)
    fits = "a" + "x" * (window - 3) + "bc"  # exactly `window` characters
    assert len(fits) == window and _classify_cold_and_warm(ts, fits).fields == {"x": "x" * (window - 3), "y": ""}
    assert _classify_cold_and_warm(_only(_REVIEW_RX, window), fits + "EXTRA") is None
    assert ts.classify_text(fits + "c") is None
    # the quadratic reviewer pattern itself, at the largest window that fits the budget
    ts2 = _only(r"^a(?P<x>.*)b(?P<y>.*)c$", 128)
    assert ts2.classify_text("a" + "x" * 125 + "bc" + "EXTRA") is None
    assert ts2.classify_text("a" + "x" * 125 + "bc") is not None


@pytest.mark.parametrize("window", _WINDOWS_UNDER_TEST)
def test_greedy_pattern_never_matches_a_text_longer_than_its_window(window):
    # round 7: matching a cut slice gave x="" / end 1 where the full text gives a long capture
    text = "b" + "a" * (window - 1) + "b"  # window + 1 characters
    assert len(text) == window + 1
    assert _classify_cold_and_warm(_only(_GREEDY_RX, window), text) is None
    ts = _only(_GREEDY_RX, window)
    assert ts.classify_text(text[:-1] + "a") is None  # no `b` at the end either
    fits = "b" + "a" * (window - 2) + "b"
    tm = _classify_cold_and_warm(_only(_GREEDY_RX, window), fits)
    assert tm.fields == {"x": "b" + "a" * (window - 2)} and tm.match_end == window


@pytest.mark.parametrize("window", _WINDOWS_UNDER_TEST)
def test_prefix_template_does_not_match_a_text_longer_than_its_window(window):
    # documented: a template applies only to messages that fit; a long message falls through
    body = tpl(r"^Hello (?P<who>\w+)", "X-001", extra=_win_extra(window)) + tpl(
        r"^Hello (?P<who>\w+)", "X-002", extra="window = 4096\n" if window < 4096 else ""
    )
    ts = tiny(body).for_family("X", sensitive=False)
    long_msg = "Hello world " + "and some more words " * (window // 20 + 2)
    assert len(long_msg) > window
    tm = _classify_cold_and_warm(ts, long_msg)
    if window < T.MAX_MATCH_CHARS:  # the second candidate has a larger window and takes it
        assert tm.code == "X-002" and tm.fields == {"who": "world"} and tm.match_end == len("Hello world")
    else:
        assert tm is None
    exact = "Hello " + "w" * (window - 6)
    assert len(exact) == window and ts.classify_text(exact).code == "X-001"
    assert ts.classify_text("Hello world and more").code == "X-001"


@pytest.mark.parametrize("window", _WINDOWS_UNDER_TEST)
def test_capture_running_to_the_edge_is_never_truncated(window):
    ts = _only(r"^a(?P<x>.*)", window)
    assert ts.classify_text("a" + "x" * (window - 1)).fields == {"x": "x" * (window - 1)}  # exact length
    assert ts.classify_text("a" + "x" * window) is None  # one more: does not fit
    assert ts.classify_text("a" + "x" * (window * 2)) is None
    assert ts.classify_text("a" + "x" * window + "\nnext") is None
    assert ts.classify_text("a" + "x" * (window - 1) + "\nnext").fields == {"x": "x" * (window - 1)}


@pytest.mark.parametrize("window", _WINDOWS_UNDER_TEST)
def test_text_of_exactly_the_window_matches_and_the_strip_comes_first(window):
    ts = _only(r"^a(?P<x>.*)z$", window)
    assert ts.classify_text("a" + "x" * (window - 2) + "z").fields["x"] == "x" * (window - 2)
    assert ts.classify_text("a" + "x" * (window - 1) + "z") is None  # window + 1 characters
    assert ts.classify_text("  a" + "x" * (window - 2) + "z  ") is not None
    lookahead = _only(r"^a(?P<x>\w*)(?!q)", window)
    assert lookahead.classify_text("a" + "x" * (window - 1)) is not None
    assert lookahead.classify_text("a" + "x" * window) is None


@pytest.mark.parametrize("window", _WINDOWS_UNDER_TEST)
def test_unicode_window_counts_characters(window):
    ts = _only(r"^a(?P<x>.*)$", window)
    for ch in ("\u00e9", "\u4e2d", "\U0001f600"):
        ok = "a" + ch * (window - 1)
        assert ts.classify_text(ok).fields["x"] == ch * (window - 1)
        assert ts.classify_text(ok + ch) is None
        assert ts.classify_text(ok + "\n" + ch * window) is not None  # the line break ends the text


def test_cont_line_regex_ignores_a_longer_line():
    ts = tiny(tpl(r"^a$", extra='cont = { parser = "line_regex", regex = "^k=(?P<v>.*)$" }\n')).for_family(
        "X", sensitive=False
    )
    assert ts.classify_text("a", "k=" + "v" * 100).fields == {"v": "v" * 100}
    assert "v" not in ts.classify_text("a", "k=" + "v" * T.MAX_MATCH_CHARS).fields


# --------------------------------------------------------------------------- cost model limits

_CATASTROPHE = 0.25  # seconds: tolerant to a runner 5x slower than the local ~20-40 ms figures


def _ranges(count):
    return "[" + "".join(chr(0x10000 + 3 * i) + "-" + chr(0x10001 + 3 * i) for i in range(count)) + "]"


def _nested_captures(depth, core="a"):
    g = core
    for i in range(depth):
        g = f"(?P<g{i}>{g})"
    return g


_U = chr(0x102FD)
# Reviewer round 6/7 contrived shapes: big classes and deep captures, rejected by the whitelist
# limits (class <= 32 items, capture depth <= 3) at every window instead of being costed.
CONTRIVED = [
    *(f"^{_U}*(?!{_ranges(k)}*$)b$" for k in (33, 64, 128, 256)),
    *(f"^{_U}*(?!{_ranges(k)}*$)b$".replace("[", "[^", 1) for k in (33, 128)),
    *(f"^a*(?!(?:{_nested_captures(d)})*$)b$" for d in (4, 12, 24)),
    f"^{_U}*(?!{_nested_captures(24, _ranges(256))}*$)b$",  # the 108 ms CI shape (class AND depth)
]


@pytest.mark.parametrize("rx", CONTRIVED)
def test_contrived_expensive_shapes_are_rejected_at_every_window(rx):
    for window in (128, 256, 512, 720, 1020, 4096):
        with pytest.raises(UnsafeRegexError):
            T._compile_regex(rx, src="t", where="X-001", window=window)


def test_class_and_capture_limits_are_the_documented_values():
    assert (T.MAX_CLASS_ITEMS, T.MAX_GROUP_DEPTH, T.MAX_GROUPS) == (32, 3, 24)
    for rx in (_ranges(32), _nested_captures(3), "".join(f"(?P<v{i}>x)" for i in range(24))):
        T._compile_regex("^" + rx + "$", src="t", where="X-001", window=128)
    with pytest.raises(UnsafeRegexError, match="character class"):
        T._compile_regex("^" + _ranges(33) + "$", src="t", where="X-001", window=128)
    with pytest.raises(UnsafeRegexError, match="nested deeper"):
        T._compile_regex("^" + _nested_captures(4) + "$", src="t", where="X-001", window=128)
    with pytest.raises(InvalidRegexError, match="more than 24"):
        T._compile_regex("^" + "".join(f"(?P<v{i}>x)" for i in range(25)) + "$", src="t", where="X-001", window=128)


@pytest.mark.parametrize("ch", [chr(0x10000), chr(0x1005D)], ids=["first_range", "last_range"])
def test_pattern_at_the_limits_is_accepted_and_fast(ch):
    # 32-item class (ranges), depth-3 captures, a lookahead repeating them: the worst accepted shape.
    # The character is taken from the class (first or LAST range) so the quadratic case really runs.
    classes = _ranges(32)
    assert T.re.fullmatch(classes, ch)
    rx = f"^{ch}*(?!{_nested_captures(3, classes)}*$)b$"
    accepted = [w for w in (4096, 2048, 1024, 720, 512, 360, 256, 128) if _accepted(rx, w)]
    assert accepted, "the counter-case must be accepted at some window"
    window = accepted[0]
    pat = T.re.compile(rx)
    for n in (*(n for n in (64, 256, 1024) if n < window), window):  # escalating: abort at the first slow probe
        t0 = time.perf_counter()
        pat.match(ch * (n - 1) + "!")
        dt = time.perf_counter() - t0
        assert dt < _CATASTROPHE, (n, window, dt)


def _accepted(rx, window):
    try:
        T._compile_regex(rx, src="t", where="X-001", window=window)
    except TemplateError:
        return False
    return True


# --------------------------------------------------------------------------- continuation parsers


def test_cont_parsers_limits_and_shapes():
    ts = load_texts({"_common.toml": REAL_COMMON, "awfm.toml": REAL_WFM}).for_family("AWFM", sensitive=False)
    codes = "\n".join(f"Code{i}" for i in range(100))
    assert len(ts.classify_text("Detected errors:", codes).fields["codes"]) == 32
    dump = "\n".join(f"\tK{i} - {i}" for i in range(1646))
    assert ts.classify_text('New "M" map content:', dump).fields["items"] == 1646
    assert "rows" not in ts.classify_text("[Communication2] - By call count:", "[methodname]\n").fields
    stack = (
        "\tManaged Code Stack Trace:\n   at A.B() in F:\\PD\\1\\x.cs:line 1\n"
        "\tInner Exception System.IO.IOException: busy\n\t\t   at C.D()"
    )
    tm = ts.classify_text("Non Fatal Exception System.Exception: x", stack)
    assert tm.fields["exc_type"] == "System.Exception"  # regex field wins over the continuation
    assert tm.fields["inner_type"] == "System.IO.IOException" and tm.fields["frame"] == "A.B"
    assert ts.classify_text("Callback received:", "garbage").fields == {}


def test_line_regex_option_line():
    body = tpl(text="Head:", extra="cont = { parser = 'line_regex', regex = '^v=(?P<v>\\d+)$', line = 2 }\n"
               'fields = { v = "int" }\n')  # fmt: skip
    ts = tiny(body).for_family("X", sensitive=False)
    assert ts.classify_text("Head:", "first\nv=7").fields == {"v": 7}
    assert ts.classify_text("Head:", "v=7").fields == {}


# --------------------------------------------------------------------------- validation errors


@pytest.mark.parametrize(
    ("body", "error", "text"),
    [
        ("[[template]]\nclass = 'other'\ntext = 'a'\n", SchemaError, "missing required key 'code'"),
        (tpl(text="a", extra="colour = 1\n"), SchemaError, "unknown key(s) colour"),
        ("[[template]]\ncode = 'bad code'\nclass = 'other'\ntext = 'a'\n", SchemaError, "must look like"),
        ("[[template]]\ncode = 'X-1'\nclass = 'weird'\ntext = 'a'\n", SchemaError, "'class' must be one of"),
        (tpl(text="a", extra="sev = 9\n"), SchemaError, "'sev' must be between"),
        (tpl(text="a", extra="sev = true\n"), SchemaError, "not a boolean"),
        (tpl(text="a", extra="fts = 1\n"), SchemaError, "'fts' must be bool"),
        (tpl(text="a", extra="stream = 'both'\n"), SchemaError, "'stream' must be one of"),
        ("[[template]]\ncode = 'X-1'\nclass = 'other'\n", SchemaError, "exactly one of 'regex' or 'text'"),
        (tpl(regex="^a", text="a"), SchemaError, "exactly one of"),
        (tpl(text=" a"), SchemaError, "without surrounding spaces"),
        (tpl(regex="^(?P<a>x"), InvalidRegexError, "does not compile"),
        (tpl(regex="^(x)"), InvalidRegexError, "unnamed capture group"),
        (tpl(regex="^(?P<Bad>x)"), InvalidRegexError, "lowercase snake_case"),
        (tpl(regex="^" + "a" * 1001), InvalidRegexError, "longer than"),
        (tpl(regex="^(?:a+)+$"), UnsafeRegexError, "nested repeats"),
        (tpl(regex=r"^(?:\w+\s?)*$"), UnsafeRegexError, "nested repeats"),
        (tpl(regex=r"^(?:.*,)*x"), UnsafeRegexError, "nested repeats"),
        (tpl(regex="^(?:a|ab)*c"), UnsafeRegexError, "alternation"),
        (tpl(regex="^(?P<a>.*)-(?P<b>.*)-(?P<c>.*)$"), UnsafeRegexError, "too much possible backtracking"),
        (tpl(regex="^a", extra="window = 127\n"), SchemaError, "'window' must be between 128 and 4096"),
        (tpl(regex="^a", extra="window = 4097\n"), SchemaError, "'window' must be between"),
        (tpl(text="a", extra="window = 512\n"), SchemaError, "regex templates only"),
        (tpl(regex=r"^(?P<a>\w+)\s*(?P<b>\w*)\s*(?P<c>\w*)x"), UnsafeRegexError, "same text"),
        (tpl(regex=r"^(?P<a>x)(?P=a)"), UnsafeRegexError, "backreferences"),
        (tpl(regex=r"^(?P<a>x)?(?(a)y|z)"), UnsafeRegexError, "backreferences"),
        (tpl(text="a", extra="fields = { nope = 'int' }\n"), SchemaError, "not a regex group nor a cont output"),
        (tpl(regex="^(?P<a>x)", extra="fields = { a = 'date' }\n"), SchemaError, "field type of 'a'"),
        (tpl(text="a", extra="keys = ['nope']\n"), SchemaError, "key field 'nope'"),
        (tpl(regex="^(?P<a>x)", extra="keys = [{ field = 'a', type = 'Bad Type' }]\n"), SchemaError, "key type"),
        (tpl(regex="^(?P<a>x)", extra="keys = [1]\n"), SchemaError, "'keys' items"),
        (tpl(regex="^(?P<a>x)", extra="sev_field = 'a'\n"), SchemaError, "go together"),
        (tpl(regex="^(?P<a>x)", extra="sev_field = 'b'\nsev_map = { x = 2 }\n"), SchemaError, "not a field"),
        (tpl(regex="^(?P<a>x)", extra="sev_field = 'a'\nsev_map = { x = 7 }\n"), SchemaError, "1..5"),
        (tpl(text="a", extra="cont = 'magic'\n"), SchemaError, "unknown cont parser 'magic'"),
        (tpl(text="a", extra="cont = { parser = 'kv_tab', max_items = 3 }\n"), SchemaError, "has no option"),
        (tpl(text="a", extra="cont = { parser = 'codes_per_line', max_items = 0 }\n"), SchemaError, "between"),
        (tpl(text="a", extra="cont = { parser = 'codes_per_line', max_items = '3' }\n"), SchemaError, "must be int"),
        (tpl(text="a", extra="cont = { parser = 'line_regex' }\n"), SchemaError, "needs a 'regex'"),
        (tpl(text="a", extra="cont = { parser = 'line_regex', regex = '(?:a+)+' }\n"), UnsafeRegexError, "nested"),
        (tpl(text="a", extra="payload = { fmt = 'soap', start = 'msg', role = 'request' }\n"), SchemaError, "fmt"),
        (tpl(text="a", extra="payload = { fmt = 'xml', start = 'end', role = 'request' }\n"), SchemaError, "start"),
        (tpl(text="a", extra="payload = { fmt = 'xml', start = 'msg', role = 'x' }\n"), SchemaError, "role"),
        (tpl(text="a", extra="payload = { fmt = 'xml', start = 'msg', role = 'request', size = 1 }\n"),
         SchemaError, "unknown key(s) size"),
        (tpl(text="a", extra="payload = { fmt = 'xml', start = 'msg', role = 'fault', direction = 'up' }\n"),
         SchemaError, "direction"),
        (tpl(text="a", extra="payload = { fmt = 'xml', start = 'msg', role = 'fault', peer = 'a b' }\n"),
         SchemaError, "peer"),
        (tpl(text="a", extra="payload = { fmt = 'xml', start = 'msg', role = 'fault', strip_suffix = '' }\n"),
         SchemaError, "strip_suffix"),
        (tpl(text="a", extra="examples = [1]\n"), SchemaError, "an example must be"),
        (tpl(text="a", extra="counter_examples = [{ msg = 'b', fields = {} }]\n"), SchemaError, "unknown key(s)"),
        (tpl(text="a", extra="aliases = ['X-001']\n"), SchemaError, "alias"),
        ("[payload_defaults]\nby_root = { direction = 'in' }\n", SchemaError, "unknown payload role"),
        ("[payload_defaults]\nrequest = { peer = 'X' }\n", SchemaError, "missing required key 'direction'"),
        ("schema = 2\n", SchemaError, "unsupported schema 2"),
        ("unknown_top = 1\n", SchemaError, "unknown key(s) unknown_top"),
        ("template = 'x'\n", SchemaError, "'template' must be list"),
        ("[[operation]]\nstart = ['NOPE-1']\n", TemplateReferenceError, "unknown template 'NOPE-1'"),
        ("[[operation]]\nstart = [1]\n", SchemaError, "must be a code or a list of codes"),
        ('extends = ["_missing"]\n', TemplateReferenceError, "unknown library '_missing'"),
        ("this is = not toml\n", SchemaError, "invalid TOML"),
    ],
)  # fmt: skip
def test_validation_errors_are_typed_and_clear(body, error, text):
    with pytest.raises(error) as info:
        tiny(body)
    assert isinstance(info.value, TemplateError)
    assert text in str(info.value)
    assert "x.toml" in str(info.value)


def test_errors_name_the_template():
    with pytest.raises(UnsafeRegexError) as info:
        tiny(tpl(regex="^(?:a+)+$", code="ABC-042"))
    assert info.value.source == "x.toml" and info.value.where == "template ABC-042"


def test_duplicates_and_references_across_files():
    a = 'family = "A"\n' + tpl(text="a", code="Z-001")
    with pytest.raises(DuplicateError, match="Z-001"):
        load_texts({"a.toml": a, "b.toml": 'family = "B"\n' + tpl(text="b", code="Z-001")})
    with pytest.raises(DuplicateError, match="alias Z-001"):
        load_texts(
            {"a.toml": a, "b.toml": 'family = "B"\n' + tpl(text="b", code="Z-002", extra="aliases=['Z-001']")}
        )
    with pytest.raises(DuplicateError, match="family A"):
        load_texts({"a.toml": a, "b.toml": 'family = "A"\n'})
    with pytest.raises(DuplicateError, match="same text"):
        load_texts({"a.toml": a + tpl(text="a", code="Z-002")})
    with pytest.raises(TemplateReferenceError, match="not a library"):
        load_texts({"a.toml": a, "b.toml": 'family = "B"\nextends = ["A"]\n'})
    with pytest.raises(SchemaError, match="cannot extend"):
        load_texts({"_a.toml": 'family = "_a"\nextends = ["_b"]\n'})
    with pytest.raises(DuplicateError, match="twice"):
        load_texts({"_l.toml": 'family = "_l"\n', "b.toml": 'family = "B"\nextends = ["_l", "_l"]\n'})


def test_safe_patterns_are_accepted():
    for rx in (
        r"^\d+(?:,\d+)?$",
        r"^[\d,]+$",
        r"^(?:foo|bar) (?P<n>\d{1,3})\.\S{1,64}$",
        r"^(?:a|b)?c$",
        r'^"(?P<a>[^"]*)" "(?P<b>[^"]*)" (?P<c>.*)$',
        r"^(?:Subscriber\((?P<p>\w+)\) failed.*|\[ERROR\] - Unable: .*|Subscriber is disconnected from .*)$",
        r"^(?P<a>\d+) (?P<b>\d+) (?P<c>\d+) (?P<d>\d+)$",
        r"^hello (?P<w>\w+)",
    ):
        tiny(tpl(regex=rx))
    rx = r"^(?P<op>.+?) request received\. Message content wr\w+ in sensitive log\.$"  # quadratic
    tiny(tpl(regex=rx, extra="window = 992\n"))


def test_window_limits_what_a_template_sees():
    ts = tiny(tpl(r"^a(?P<x>.*)b(?P<y>.*)c$", extra="window = 128\n")).for_family("X", sensitive=False)
    assert ts.templates[0].window == 128
    assert ts.classify_text("a" + "x" * 100 + "bc").fields == {"x": "x" * 100, "y": ""}
    assert ts.classify_text("a" + "x" * 200 + "bc") is None  # "c$" lies beyond the window
    assert ts.classify_text("a1b2c\nnext") is not None  # a line break ends the matched text


def test_check_examples_reports_problems():
    body = (
        tpl(r"^a(?P<n>\d+)$", "X-001", extra="examples = ['a1', { msg = 'a2', fields = { n = '3' } }, 'zz']\n"
            "counter_examples = ['a5']\n")
        + tpl(r"^a\d$", "X-002", extra="examples = ['a7']\n")
    )  # fmt: skip
    problems = tiny(body).check_examples()
    joined = "\n".join(problems)
    assert "field 'n' of 'a2' is '2', expected '3'" in joined
    assert "example 'zz' classified as None" in joined
    assert "counter-example 'a5' matched" in joined
    assert "example 'a7' classified as X-001" in joined  # shadowed by an earlier template
    assert "example 'a1' of X-001 also matches X-002" in joined
    with pytest.raises(T.ExampleError):
        load_texts({"x.toml": 'family = "X"\n' + body}, check_examples=True)


def test_check_examples_ignores_a_template_whose_window_the_example_does_not_fit():
    long_example = "a" * 129
    body = tpl("^a+$", "X-001", extra="window = 128\n") + tpl(
        "^a+$", "X-002", extra=f"window = 256\nexamples = ['{long_example}']\n"
    )
    assert tiny(body).check_examples() == []
    load_texts({"x.toml": 'family = "X"\n' + body}, check_examples=True)
    # a real conflict (same window, both match) is still reported
    both = tpl("^a+$", "X-001", extra="window = 256\n") + tpl(
        "^a+$", "X-002", extra=f"window = 256\nexamples = ['{long_example}']\n"
    )
    assert any("also matches X-001" in p for p in tiny(both).check_examples())


def test_line_regex_discards_a_line_cut_by_the_continuation_cap(monkeypatch):
    body = tpl(text="Head:", extra="cont = { parser = 'line_regex', regex = '^k=(?P<v>.*)$', line = 2 }\n"
               'fields = { v = "str" }\n')  # fmt: skip
    ts = tiny(body).for_family("X", sensitive=False)
    monkeypatch.setattr(T, "MAX_CONT_CHARS", 100)
    value = "v" * 92
    complete = f"first\nk={value}"
    assert len(complete) == 100
    assert ts.classify_text("Head:", complete).fields == {"v": value}  # ends exactly at the cap
    assert ts.classify_text("Head:", complete + "\nmore").fields == {"v": value}  # cap before the line break
    assert ts.classify_text("Head:", complete + "yyyy").fields == {}  # the cap cuts the line: discarded


def test_a_single_line_longer_than_the_cap_yields_no_lines_but_real_empty_lines_survive(monkeypatch):
    body = tpl(text="Head:", extra="cont = { parser = 'line_regex', regex = '^(?P<v>.*)$', line = 1 }\n"
               'fields = { v = "str" }\n')  # fmt: skip
    ts = tiny(body).for_family("X", sensitive=False)
    monkeypatch.setattr(T, "MAX_CONT_CHARS", 100)
    # one single line longer than the cap: there is no complete line, so no empty capture is invented
    assert ts.classify_text("Head:", "a" * 101).fields == {}
    assert T._cont_lines("a" * 101, 8) == []
    # a REAL empty first line is still an empty line
    assert ts.classify_text("Head:", "\nnext").fields == {"v": ""}
    assert T._cont_lines("\nnext", 8) == ["", "next"]


# --------------------------------------------------------------------------- packaged files


def test_packaged_catalog_loads_and_skips_the_example():
    catalog = load_catalog(check_examples=True)
    assert all(fam.source != "_example.toml" for fam in catalog.families.values())


def test_example_file_covers_the_schema_and_passes_its_examples():
    data = resources.files("dmslog.adapters").joinpath("_example.toml").read_bytes()
    fam = parse_family(data, "_example.toml")
    cat = load_texts({"_example.toml": data}, check_examples=True)
    ts = cat.for_family(fam.family, sensitive=True)
    used = {
        "text": any(t.text for t in fam.templates),
        "regex": any(t.regex for t in fam.templates),
        "aliases": any(t.aliases for t in fam.templates),
        "fields": any(t.field_types for t in fam.templates),
        "keys": any(t.keys for t in fam.templates),
        "cont": any(t.cont for t in fam.templates),
        "payload": any(t.payload for t in fam.templates),
        "fts": any(not t.fts for t in fam.templates),
        "stream": any(t.stream != "any" for t in fam.templates),
        "sev_map": any(t.sev_map for t in fam.templates),
        "refs": any(t.refs_sensitive for t in fam.templates),
        "examples": all(t.examples for t in fam.templates),
        "counter": any(t.counter_examples for t in fam.templates),
        "defaults": bool(fam.payload_defaults),
        "rules": bool(ts.operations() and ts.sensitive_pairs()),
    }
    assert all(used.values()), used
    assert 3 <= len(fam.templates) <= 5


_C = "bcdefghijklmnopqrstuvwxyzABCDEFG"
_B = "|".join("(?!a*$)" + c for c in _C)
_E = "|".join("(?!a*(?!a*$)a$)" + c for c in _C)
R5_PATTERNS = [  # round 5 reviewer patterns: 0.85 s each on "a" * 4095 + "!" (the last one 27 ms)
    f"^a*(?!(?:{_B}))a$",
    f"^a*(?<!(?:{_B}))a$",
    f"^(?:{_E})$",
    f"^(?:(?:{_E}))?a$",
    r"^a*(?!a*$)b$",
]

REJECTED_REGEXES = [
    # nested repeats, finite or not, with or without a separator
    "^(?:a{1,64})+$",
    "^(?:a+)+$",
    "^(?:a*)*$",
    "^(?:a{1,64}){1,64}$",
    "^(?:(?:ab)+){2,}$",
    "^(?:a{2}){3}$",
    "^(?:(?:a{2}){3}){2}$",
    r"^(?:\d{1,3}\.){1,4}$",
    r"^(?:\d+,)*\d+$",
    r"^(?:, (?P<x>\d+))*$",
    r"^(?:\\[^{}\\:]{1,60}){1,12}$",
    "^(?:(?:a+)b)+$",
    "^(?:x(?:a+))+$",
    # case-insensitive separator (reviewer finding 1) and escape variants
    "^(?i:(?:a{1,64}A)+)$",
    r"^(?i:(?:a{1,64}\x41)+)$",
    "^(?i:(?:a|AA)+)$",
    "(?i)^(?:a{1,64}A)+$",
    # alternation under a repeat, however it is wrapped (reviewer finding 2)
    "^(?:a|aa)+$",
    "^(?:a|ab)+$",
    "^(?:(?:a|aa)?)+$",
    "^(?:(?:a|aa)?){1,64}$",
    "^(?:[ab]|aa)+$",
    "^(?:(?:[ab]|aa)?)+$",
    r"^(?:\w|aa)+$",
    r"^(?:(?:\w|aa)?)+$",
    r"^(?:(?:(?:a|aa)))+$",
    r"^(?:x(?:a|aa)y)*$",
    r"^(?:(?P<n>a|aa))+$",
    # optionals inside a repeat
    "^(?:a?)+$",
    "^(?:a?b?)+$",
    "^(?:a?a)+$",
    "^(?:(?:a)?)*$",
    # flags: only the absence of flags is supported
    "(?i)^abc",
    "(?s)^abc",
    "(?x)^abc",
    "^(?i:abc)$",
    "^(?-i:abc)$",
    "(?a)^abc",
    # bounds above 64, possessive, atomic, backreferences, conditionals
    r"^\d{1,65}$",
    r"^\d{65}$",
    r"^\d{65,}$",
    r"^\d{1,1000}$",
    r"^\d++$",
    r"^(?>\d+)$",
    r"^(?P<a>x)(?P=a)$",
    r"^(?P<a>x)?(?(a)y|z)$",
    # sequences of optionals / ambiguous alternations / variable repeats that share text
    "^" + "a?" * 30 + "$",
    "^" + "a?" * 30 + "a{30}$",
    "^" + "(?:a|aa)" * 30 + "$",
    "^" + "(?:[ab]|aa)" * 30 + "$",
    "^" + r"\w{1,64}" * 8 + "$",
    # round 3: lookaround bodies, alternatives holding groups, Unicode overlaps, bounded lazy units
    "^(?=" + "a?" * 30 + "$)a*$",
    "^(?!" + "a?" * 30 + "$)a*$",
    "^(?<!" + "(?:a|aa)" * 30 + ")a",
    "^" + "".join(f"(?:(?P<x{i}>[ab])|[ab][ab])" for i in range(24)) + "$",
    "^" + "(?:[\u0100-\u0200]|\u0100[\u0100-\u0200])" * 30 + "$",
    "^" + "(?:\\w|\u0100\\w)" * 30 + "$",
    "^" + r"[^\W_]{0,64}?" * 5 + "$",
    "^" + r"[^\W_]{0,64}" * 5 + "$",
    "^" + "a??" * 30 + "$",
    # two unbounded repeats that can both consume the same text, however far apart
    r"^(?P<a>.*)-(?P<b>.*)-(?P<c>\d+)$",
    r"^(?P<a>.+?) x (?P<b>.+?) y.*z",
    r"^(?P<a>[\u0100-\u0200]*)\u0150(?P<b>\w*)\u0150(?P<c>\w*)$",
    # round 4: a lookaround body is re-run on every attempt of the repeat before it
    "^a*(?!a*(?!a*(?!a*(?!a*(?!a*(?!a*$)a$)a$)a$)a$)a$)a$",  # reviewer pattern: 0.39 s on 'a'*40+'!'
    "^a*" + "(?!a*$)" * 20 + "a$",  # reviewer pattern: 0.53 s on a 4096-char line
    "^a*" + "(?=a*$)" * 20 + "a$",
    "^a*" + "(?!a*$)" * 3 + "a$",
    "^a*(?!a*(?!a*(?!a*$)a$)a$)a$",
    "^a*(?=a*(?=a*(?=a*$)a$)a$)a$",
    "^a*(?!a*(?=a*(?!a*(?=a*$)a$)a$)a$)a$",
    "^a*(?!a*(?!a*$)a$)a$",
    "^a+(?<!b)(?!a*(?!a*(?!a*$)a$)a$)a$",
    "^[ab]*" + "(?<!x)(?!a*$)" * 20 + "a$",
    "^(?:a*" + "(?!a*$)" * 20 + ")?a$",  # lookarounds inside an optional group
    "^(?:x|y*" + "(?!a*$)" * 20 + ")a$",
    "^a*(?=b*)b*(?!a*)c*(?=d*)" * 3 + "$",  # sequences of lookarounds between repeats
    "^a*(?=a*$)b*(?!a*$)c*(?=a*$)d*(?!a*$)e*(?=a*$)f*(?!a*$)$",
    "^a*" + "(?=" + "a?" * 12 + "a*)" * 6 + "$",
    # round 5: lookarounds run before the alternatives are told apart; quadratic shapes at 4096
    *R5_PATTERNS,
    r"^\w+(?<!x)(?!a*$) y",
    r"^(?!a*(?!b*$)c)\w+$",
    r"^(?P<a>.+?) - (?P<b>.+)\.$",
    # unnamed capture group
    "^(a)$",
]


@pytest.mark.parametrize("rx", REJECTED_REGEXES)
def test_unsafe_regexes_are_rejected(rx):
    with pytest.raises(TemplateError):
        T._compile_regex(rx, src="t", where="X-001")


# Reviewer findings of rounds 1-5, with the largest window at which each one fits the work budget
# (None: rejected at every window). Those accepted at a small window are probed at that window.
REVIEW_FINDINGS = [
    ("R1", "^(?:a{1,64})+$", None),
    ("R1", "^(?:a{1,64}){1,64}$", None),
    ("R1", r"^(?:\d{1,3}\.){1,4}$", None),
    ("R2", "^(?i:(?:a{1,64}A)+)$", None),
    ("R2", "^(?:(?:a|aa)?)+$", None),
    ("R3", "^(?=" + "a?" * 30 + "$)a*$", None),
    ("R3", "^" + "(?:[\u0100-\u0200]|\u0100[\u0100-\u0200])" * 30 + "$", None),
    ("R3", "^" + r"[^\W_]{0,64}?" * 5 + "$", None),
    ("R4", "^a*(?!a*(?!a*(?!a*(?!a*(?!a*(?!a*$)a$)a$)a$)a$)a$)a$", None),
    ("R4", "^a*" + "(?!a*$)" * 20 + "a$", 128),
    *(("R5", rx, 512 if rx.endswith("b$") else 128) for rx in R5_PATTERNS),
]
_WINDOWS = (128, 256, 512, 1024, 2048, 4096)


def _largest_window(rx):
    best = None
    for window in _WINDOWS:
        try:
            T._compile_regex(rx, src="t", where="X-001", window=window)
        except TemplateError:
            continue
        best = window
    return best


@pytest.mark.parametrize(("finding", "rx", "window"), REVIEW_FINDINGS)
def test_review_findings_are_rejected_or_bounded_by_their_window(finding, rx, window):
    assert _largest_window(rx) == window, finding
    if window is not None:
        pat = T.re.compile(rx)
        worst = 0.0
        for unit in ("a", "b", "ab"):
            text = (unit * window)[: window - 1] + "!"
            t0 = time.perf_counter()
            pat.match(text)
            worst = max(worst, time.perf_counter() - t0)
        assert worst < 0.25, (finding, worst)


ACCEPTED_REGEXES = [
    r"^\S{1,64}$",
    r"^\d{1,3}$",
    r"^\S{1,64} \S{1,64}$",
    r"^\d+(?:\.\d+)?$",
    r"^[\d,]+$",
    r"^(?:foo|bar|baz) (?P<n>\d+)$",
    r"^(?:a|b)?c$",
    r"^(?:ab)?(?:cd)?$",
    r"^(?:ab){2}$",
    r"^(?:\d{1,3}\.)$",
    r"^(?P<k>\w+)=(?P<v>[^;]*);$",
    r'^"(?P<a>[^"]*)" "(?P<b>[^"]*)" (?P<c>.*)$',
    r"^(?=<)",
    # idioms of the catalog: lazy fields, negative lookaheads, a fixed tail before "$"
    r"^\[(?!GetIncidents end\])(?P<op>\w+) end\] - (?P<detail>.*?) failed\.$",
    r"^(?P<op>[A-Z][\w ]+?) request received\.",
    r"^\[(?P<p>\w+)\] :(?! For \w+ service url is )(?: (?P<v>.*))?$",
    # exact Unicode classes: disjoint ranges and categories are told apart
    "^[\u0100-\u0200]+\u0201[\u0100-\u0200]+$",
    r"^\d+\u00b2\d+$",  # superscript two is not \d (it is str.isdigit())
    r"^\S+ \S+ \S+ \S+$",
    # lookaround idioms of the catalog, also after a repeat: few runs, shallow bodies
    r"^(?=.*foo)\d+ bar$",
    r"^\d+(?=:)",
    r"^\d+(?<=\d)(?! )",
    # bounded: each lookaround runs a bounded number of times (2 or 64), not once per position
    "^a{1,64}" + "(?!a{1,64}$)" * 4 + "a$",
    "^(?:a?)" + "(?!a*$)" * 20 + "a$",
    "^(?:a|aa)" + "(?!a*$)" * 20 + "a$",
]


@pytest.mark.parametrize("rx", ACCEPTED_REGEXES)
def test_flat_tokens_and_outer_alternation_stay_valid(rx):
    T._compile_regex(rx, src="t", where="X-001")


def test_packaged_catalog_loads_with_the_strict_validator():
    catalog = load_catalog(check_examples=True)
    assert len(list(catalog.templates())) >= 335


@pytest.mark.parametrize("name", ["CATEGORY_DIGIT", "CATEGORY_WORD", "CATEGORY_SPACE", "CATEGORY_NOT_WORD"])
def test_class_sets_agree_with_re(name):
    import bisect
    import re

    esc = {
        "CATEGORY_DIGIT": r"\d",
        "CATEGORY_WORD": r"\w",
        "CATEGORY_SPACE": r"\s",
        "CATEGORY_NOT_WORD": r"\W",
    }
    rx = re.compile(esc[name])
    s = T._categories()[name]
    for c in [*range(0, 0x3000), *range(0x3000, 0x110000, 37)]:
        i = bisect.bisect_right(s, (c, T._EOS))
        assert (i > 0 and s[i - 1][1] >= c) == bool(rx.fullmatch(chr(c))), hex(c)


def _attack_inputs(pattern: str, n: int) -> list[str]:
    """Inputs that pump every repeat of ``pattern`` with text it shares with what follows it.

    A walk over the parsed regex writes one representative text per alternative index; each
    unbounded repeat is filled with ``n`` characters: either a character from a short list that
    its class accepts, or the literal run that follows it (the classic " - x - x - x" attack).
    The text ends with a character no catalog regex expects, to force a full failure.
    """
    from re import _constants as C
    from re import _parser as P

    tree = P.parse(pattern).data
    chars = [" ", "a", "A", "0", ".", "-", ":", "\u0100"]

    def pick(s, prefer):
        for ch in (prefer, *chars):
            if ch and any(lo <= ord(ch) <= hi for lo, hi in s):
                return ch
        return chr(s[0][0]) if s else ""

    def walk(items, alt, prefer, pump_runs, after=""):
        out = []
        for k, (op, av) in enumerate(items):
            rest = items[k + 1 :]
            lits = "".join(chr(v) for _, v in itertools.takewhile(lambda x: x[0] is C.LITERAL, rest))
            follow = lits + after if len(lits) == len(rest) else lits  # literal text after this item
            atom = T._atom_set(op, av)
            if op is C.LITERAL:
                out.append(chr(av))
            elif atom is not None:
                out.append(pick(atom, prefer))
            elif op is C.SUBPATTERN:
                out.append(walk(av[3], alt, prefer, pump_runs, follow))
            elif op is C.BRANCH:
                out.append(walk(av[1][alt % len(av[1])], alt, prefer, pump_runs, follow))
            elif op in (C.MAX_REPEAT, C.MIN_REPEAT):
                lo, hi, body = av
                unit = walk(body, alt, prefer, pump_runs, follow)
                if hi != C.MAXREPEAT:
                    out.append(unit * hi)
                    continue
                first = T._atom_set(*body[0]) if len(body) == 1 else None
                run = follow[:40] if pump_runs and first else ""
                fill = run if run and all(pick(first, c) == c for c in run) else unit
                out.append(fill * max(1, n // max(1, len(fill))))
        return "".join(out)

    alts = max([len(av[1]) for op, av in _walk_all(tree) if op is C.BRANCH] or [1])
    out = set()
    for alt in range(min(alts, 16)):
        for prefer in ("", *chars):
            for pump_runs in (False, True):
                out.add(walk(tree, alt, prefer, pump_runs)[: T.MAX_MATCH_CHARS - 1] + "\x07")
    return sorted(out)


def _walk_all(items):
    from re import _constants as C

    for op, av in items:
        yield op, av
        if op is C.SUBPATTERN:
            yield from _walk_all(av[3])
        elif op is C.BRANCH:
            for b in av[1]:
                yield from _walk_all(b)
        elif op in (C.MAX_REPEAT, C.MIN_REPEAT):
            yield from _walk_all(av[2])
        elif op in (C.ASSERT, C.ASSERT_NOT):
            yield from _walk_all(av[1])


_ATTACK_SIZES = (64, 512, 1024, T.MAX_MATCH_CHARS)
_ATTACK_LIMIT = 0.25  # seconds per probe: catastrophe threshold (locally the worst shipped regex takes a few ms)


def _probe_catalog() -> None:
    """Child process body: probe every shipped regex at its own window (and the round 5 reviewer
    patterns at the largest window they are accepted at), printing each one before it is probed."""
    import sys

    catalog = load_catalog()
    windows = {t.regex.pattern: t.window for t in catalog.templates() if t.regex is not None}
    windows |= {
        t.cont.regex.pattern: T.MAX_MATCH_CHARS for t in catalog.templates() if t.cont and t.cont.regex
    }
    shipped = len(windows)
    windows |= {rx: w for f, rx, w in REVIEW_FINDINGS if w is not None}
    worst = (0.0, "")
    for pat, window in sorted(windows.items()):
        print("probing", window, pat, flush=True)
        T._compile_regex(pat, src="probe", where="", window=window)  # accepted at that window
        rx = T.re.compile(pat)
        for n in (*(n for n in _ATTACK_SIZES if n < window), window):
            for text in _attack_inputs(pat, n):
                text = text[: window - 1] + "\x07" if len(text) > window else text
                t0 = time.perf_counter()
                rx.match(text)
                dt = time.perf_counter() - t0
                worst = max(worst, (dt, pat))
                if dt > _ATTACK_LIMIT:
                    print(f"SLOW {dt:.3f}s n={n} {pat}", flush=True)
                    sys.exit(1)
    print(f"done {shipped} worst {worst[0] * 1000:.1f} ms {worst[1]}", flush=True)


def test_catalog_regexes_stay_fast_on_their_own_attack_inputs():
    """Defense in depth for the static rules: every shipped regex, pumped up to the match window.

    The probes run in a child process killed after a timeout (subprocess works the same on
    Windows and Linux), so even a regression that lets a catastrophic regex in cannot hang the
    suite: the failure names the regex being probed.
    """
    import subprocess
    import sys
    from pathlib import Path

    here = Path(__file__).resolve().parent
    code = "import sys; sys.path[:0] = sys.argv[1:3]; import test_templates; test_templates._probe_catalog()"
    args = [sys.executable, "-c", code, str(here.parent.parent / "src"), str(here)]
    try:
        done = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", timeout=120, check=False
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
        pytest.fail("timed out; last line: " + (out.strip().splitlines() or ["(none)"])[-1])
    last = (done.stdout.strip().splitlines() or ["(none)"])[-1]
    assert done.returncode == 0 and last.startswith("done "), (last, done.stderr[-2000:])
    assert int(last.split()[1]) >= 290


def _csv_template(options=""):
    cont = f'{{ parser = "stats_csv"{options} }}'
    return tiny(tpl(regex=r"^By count:$", extra=f"cont = {cont}\n"))


def test_stats_csv_keeps_every_row_up_to_the_continuation_cap():
    n = 1000
    body = "[h]\n" + "".join(f"M{i},{i},1,2\n" for i in range(n))
    got = _csv_template().for_family("X", sensitive=False).classify_text("By count:", body)
    assert got is not None and got.fields["n_rows"] == n and len(got.fields["rows"]) == n
    # the continuation cap still bounds it: a line the cap cuts is dropped, nothing past it is read
    big = "[h]\n" + "".join(f"Method{i},{i},1,2\n" for i in range(T.MAX_CONT_CHARS // 8))
    assert len(big) > T.MAX_CONT_CHARS
    got = _csv_template().for_family("X", sensitive=False).classify_text("By count:", big)
    assert got is not None and 0 < got.fields["n_rows"] < T.MAX_CONT_CHARS // 8


def test_stats_csv_max_rows_option_still_caps_the_rows_kept():
    body = "".join(f"M{i},{i},1,2\n" for i in range(50))
    got = _csv_template(", max_rows = 10").for_family("X", sensitive=False).classify_text("By count:", body)
    assert got is not None and got.fields["n_rows"] > 10 and len(got.fields["rows"]) == 10


def test_stats_csv_flags_rows_left_out():
    tset = _csv_template(", max_rows = 5").for_family("X", sensitive=False)
    cut = tset.classify_text("By count:", "".join(f"M{i},1,1,1\n" for i in range(9)))
    assert cut is not None and cut.fields["rows_cut"] is True and len(cut.fields["rows"]) == 5
    whole = _csv_template().for_family("X", sensitive=False).classify_text("By count:", "M1,1,1,1\n")
    assert whole is not None and "rows_cut" not in whole.fields
    big = "".join(f"Method{i},{i},1,2\n" for i in range(T.MAX_CONT_CHARS // 8))
    over = _csv_template().for_family("X", sensitive=False).classify_text("By count:", big)
    assert over is not None and over.fields["rows_cut"] is True


def test_stats_csv_single_line_above_the_cap_is_reported_cut():
    one_line = "M," + "9" * (T.MAX_CONT_CHARS + 10)
    got = _csv_template().for_family("X", sensitive=False).classify_text("By count:", one_line)
    assert got is not None
    assert got.fields["rows"] == [] and got.fields["n_rows"] == 0 and got.fields["rows_cut"] is True


@pytest.mark.parametrize("prefix", ["\n", "[methodname, call count, request total size, response total size]\n", "\n\n"])
def test_stats_csv_cut_is_reported_after_an_empty_line_or_header(prefix):
    cont = prefix + "M" * (T.MAX_CONT_CHARS + 10) + ",1,2,3\n"
    got = _csv_template().for_family("X", sensitive=False).classify_text("By count:", cont)
    assert got is not None
    assert got.fields["rows"] == [] and got.fields["n_rows"] == 0 and got.fields["rows_cut"] is True
