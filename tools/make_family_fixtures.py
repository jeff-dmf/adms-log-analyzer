"""Deterministic generator of the per-family template fixtures (M2 task 2.6).

For every template of the packaged catalog (``src/dmslog/adapters/*.toml``) the logs written to
``tests/fixtures/families/`` hold at least one record that the engine classifies to that template.
The records come from the templates' own positive ``examples`` (already synthetic), wrapped in the
real line envelope (BOM + CRLF, ``YYYY-MM-DD HH:MM:SS.mmm [PID=n][TID=0xN] LEVEL - msg``; the
oasErrLog envelope for OASERR), with deterministic timestamps and continuation lines. Each stream
also gets a few records that must stay unknown (tpl 0) and a few false-positive-looking lines
(counter-examples that no template of the stream matches).

Run ``python tools/make_family_fixtures.py`` to (re)write the files; ``--check`` only compares and
exits 1 on any difference. Files are raw bytes, so BOM and CRLF survive on Windows and Linux.

Layout (relative to the output folder)::

    dnaApp_zone1/SVC/<Process>_<pid>.log            normal stream of an adapter family
    dnaApp_zone1/SVC/<Process>_<pid>.sensitive.log  its sensitive stream (families with such templates)
    dnaApp_zone1/SVC/BLTHost.SVC.log, ProcessMonitor.SVC.log, oasErrLog.log
    dnaApp_zone1/SVC/GenericProc_9001[.sensitive].log   the shared ``_common`` templates (family PROCESS)

The Wfm, Ont and Smn streams also end with a block of Info/Verbose **payload scenarios** (tasks 6.2 and
6.4; ``scenario_records``): announcements and payloads on the logging threads, requests/responses/faults,
delta and CRS/WMS/IMS content, Smn publications, an HTML error body, PSAttach lines without TID, a
timestamp that goes back 2 s in the Smn sensitive stream and two concurrent operations whose responses
carry no ID. They sit at an absolute time (``SCENARIO_T0``) shared by the normal and sensitive files so
that announce and payload have the same millisecond. A record above 65,536 characters is not committed:
``large_publication_log`` builds it in memory for the tests.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if importlib.util.find_spec("dmslog") is None:  # run from a checkout without installing
    sys.path.insert(0, str(ROOT / "src"))

from dmslog.parse.templates import Catalog, load_catalog  # noqa: E402

_spec = importlib.util.spec_from_file_location("make_fixture", Path(__file__).with_name("make_fixture.py"))
_mf = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
sys.modules.setdefault("make_fixture", _mf)
_spec.loader.exec_module(_mf)  # type: ignore[union-attr]

DEFAULT_OUT = ROOT / "tests" / "fixtures" / "families"
FOLDER = "dnaApp_zone1/SVC"
RT_FOLDER = "dnaApp_zone1/DMS_REALTIME"  # service folder of the RT servers (any DMS_* but DMS_INTEGRATION)
MAX_RECORDS = 200
MIN_RECORDS = 10
STEP_MS = 50
GITATTRIBUTES = b"* -text\n"
COMMON = "_common"
DEFAULT_TID = 0x1A2B
SCENARIO_T0 = 60_000  # ms after BASE: after the template examples of every stream

# family -> (file layout, process name). ``adapter``: <Process>_<pid>[.sensitive].log
PROCESSES: dict[str, tuple[str, str]] = {
    "AWFM": ("adapter", "WfmAdapter"),
    "AONT": ("adapter", "OntAdapter"),
    "AAVL": ("adapter", "AVLAdapter"),
    "AOSR": ("adapter", "OsrAdapter"),
    "ASMN": ("adapter", "SmnAdapter"),
    "ASSS": ("adapter", "SiteSwitchAdapter"),
    "ACRM": ("adapter", "CrmAdapter"),
    "AAMI": ("adapter", "AmiAdapter"),
    "ASMR": ("adapter", "SmrAdapter"),
    "AWDI": ("adapter", "WDIAdapter"),
    "ACLC": ("adapter", "ClaimCheckAdapter"),
    "AFUP": ("adapter", "FileUploadAdapter"),
    "AFMO": ("adapter", "FileMonitorAdapter"),
    "BLTHOST": ("blthost", "BLTHost"),
    "SERVICE": ("service", "ProcessMonitor"),
    "OASERR": ("single", "oasErrLog"),
    COMMON: ("adapter", "GenericProc"),  # family PROCESS: only the generic library applies
    # Last, so that the pids of the earlier fixtures do not change.
    "AEDP": ("adapter", "CustomAdapterChangeIncidentDevice"),
}
# Family the discovery rules give to each file (for the tests).
EXPECTED_FAMILY = {fam: ("PROCESS" if fam == COMMON else fam) for fam in PROCESSES}

UNKNOWN_MARKERS = ("Zzz unmatched synthetic record", "Qqq synthetic noise without template")
UNKNOWN_TEXTS = (
    "Zzz unmatched synthetic record alpha",
    "Zzz unmatched synthetic record beta",
    "Qqq synthetic noise without template",
)
MAX_COUNTER = 3

_GUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_LONG_DIGITS_RE = re.compile(r"\d{9,}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def sanitize(text: str) -> str:
    """Make a template example safe for the leak scan (9+ digit runs, GUIDs, e-mails)."""

    def guid(m: re.Match[str]) -> str:
        g = m.group(0).lower()
        if not g.startswith(_mf.GUID_PREFIX):
            n = int(g.replace("-", "")[-6:], 16) % 1_000_000
            return _mf.GUID_PREFIX + f"0000a0{n:06d}"
        tail = g[len(_mf.GUID_PREFIX) :]
        if _LONG_DIGITS_RE.search(tail):  # the leak scan rejects 9+ digit runs, even in synthetic GUIDs
            tail = tail[:4] + "a" + tail[5:]
        return _mf.GUID_PREFIX + tail

    out = _GUID_RE.sub(guid, text)
    out = _LONG_DIGITS_RE.sub(lambda m: m.group(0)[:8], out)
    return _EMAIL_RE.sub("user.example", out)


@dataclass(frozen=True)
class Rec:
    msg: str
    cont: str | None  # continuation lines, '\n' separated, no trailing newline
    level: str
    code: str | None  # intended template code; None = must stay unknown
    kind: str  # example | unknown | counter | scenario
    tid: int | None = DEFAULT_TID  # None: header without [TID=...]
    at_ms: int | None = None  # absolute offset from the fixture BASE; None = position * STEP_MS


def _level_for(t) -> str:
    if t.cls == "startup":
        return "ALWAYS"
    if t.cls == "req_resp":
        return "DEBUGLOG"
    if t.sev is not None and t.sev <= 2:
        return "ERROR"
    if t.sev == 3:
        return "WARNING"
    if t.sev == 4:
        return "INFO"
    return "DEBUGLOG"


def _clean_cont(cont: str | None) -> str | None:
    if not cont:
        return None
    lines = sanitize(cont).replace("\r\n", "\n").split("\n")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) if lines else None


def stream_records(catalog: Catalog, family: str, sensitive: bool) -> list[Rec]:
    """Records of one stream: template examples, unknown lines and false-positive-looking lines."""
    tset = catalog.for_family("PROCESS" if family == COMMON else family, sensitive=sensitive)
    own = [t for t in catalog.templates() if t.family == family]
    if sensitive:
        wanted = [t for t in own if t.stream == "sensitive"]
    else:
        wanted = [t for t in own if t.stream in ("any", "normal")]
    firsts: list[Rec] = []
    extras: list[Rec] = []
    for t in sorted(wanted, key=lambda x: x.code):
        for ex in t.examples:
            msg = sanitize(ex.msg)
            cont = _clean_cont(ex.cont)
            tm = tset.classify_text(msg, cont)
            if tm is None or tm.code != t.code:
                continue  # the sanitized text no longer matches: skipped (reported by the tests)
            rec = Rec(msg, cont, _level_for(t), t.code, "example")
            (firsts if not any(r.code == t.code for r in firsts) else extras).append(rec)
    unknown = [Rec(m, None, "INFO", None, "unknown") for m in UNKNOWN_TEXTS]
    counters: list[Rec] = []
    seen: set[tuple[str, str | None]] = set()
    for t in sorted(own, key=lambda x: x.code):
        for ex in t.counter_examples:
            msg, cont = sanitize(ex.msg), _clean_cont(ex.cont)
            if (msg, cont) in seen or tset.classify_text(msg, cont) is not None:
                continue
            seen.add((msg, cont))
            counters.append(Rec(msg, cont, "INFO", None, "counter"))
            if len(counters) >= MAX_COUNTER:
                break
        if len(counters) >= MAX_COUNTER:
            break
    room = MAX_RECORDS - len(firsts) - len(unknown) - len(counters)
    records = firsts + extras[: max(room, 0)] + counters + unknown
    # tiny streams are padded with repeated examples up to MIN_RECORDS (still deterministic)
    pool = firsts + extras
    k = 0
    while pool and len(records) < MIN_RECORDS:
        records.insert(len(firsts) + k, pool[k % len(pool)])
        k += 1
    # deterministic interleave: unknown and counter lines spread through the file
    return _interleave(records)


def _interleave(records: list[Rec]) -> list[Rec]:
    base = [r for r in records if r.kind == "example"]
    odd = [r for r in records if r.kind != "example"]
    if not odd:
        return base
    out: list[Rec] = []
    gap = max(len(base) // (len(odd) + 1), 1)
    it = iter(odd)
    for i, r in enumerate(base):
        out.append(r)
        if (i + 1) % gap == 0:
            nxt = next(it, None)
            if nxt is not None:
                out.append(nxt)
    out.extend(it)
    return out


# ------------------------------------------------------------------------------------------ scenarios
# Synthetic Info/Verbose payload scenarios (docs/formato/payloads.md). Everything here is invented:
# GUIDs use the synthetic prefix, GIDs have 8 digits, names are placeholders. Threads: 0x10..0x12
# operation threads, 0x2a..0x2d logging threads (the payload never leaves on the operation thread).

NS_MSG = "http://iec.ch/TC57/2011/schema/message"
NS_CREWS = "http://iec.ch/TC57/2011/CrewsMessage"
NS_INC = "http://iec.ch/TC57/2011/IncidentsMessage"
NS_PLANS = "http://iec.ch/TC57/2011/SwitchingPlansMessage"
NS_WORKS = "http://iec.ch/TC57/2011/WorksMessage"


def _g(n: int) -> str:
    """Synthetic GUID (no run of 9+ digits, as the leak scan requires)."""
    return f"{_mf.GUID_PREFIX}0000a0{n:06d}"


def _sc(at: int, tid: int | None, level: str, msg: str, code: str, cont: str | None = None) -> Rec:
    return Rec(sanitize(msg), _clean_cont(cont), level, code, "scenario", tid, SCENARIO_T0 + at)


def _lines(*parts: str) -> str:
    return "\n".join(parts)


def _hdr(verb: str, noun: str, msg_id: str | None = None, corr_id: str | None = None) -> list[str]:
    """IEC 61968 message header, one element per line (the `Header` of a request or response)."""
    out = [
        "  <Header>",
        f'    <Verb xmlns="{NS_MSG}">{verb}</Verb>',
        f'    <Noun xmlns="{NS_MSG}">{noun}</Noun>',
    ]
    out.append(f'    <Source xmlns="{NS_MSG}">ADMS</Source>')
    if msg_id is not None:
        out.append(f'    <MessageID xmlns="{NS_MSG}">{msg_id}</MessageID>')
    if corr_id is not None:
        out.append(f'    <CorrelationID xmlns="{NS_MSG}">{corr_id}</CorrelationID>')
    out.append("  </Header>")
    return out


def _payload(root_open: str, body: list[str], root_name: str, suffix: str = ".") -> tuple[str, str]:
    """(first-line remainder, continuation) of an XML payload: the adapter appends `suffix` to the root."""
    return root_open, "\n".join([*body, f"</{root_name}>{suffix}"])


def wfm_request_xml(msg_id: str) -> tuple[str, str]:
    body = [
        *_hdr("changed", "Crews", msg_id, msg_id),
        "  <Payload>",
        '    <Crews xmlns="http://iec.ch/TC57/2007/Crews#">',
        "      <Crew>",
        "        <mRID>CREW0001</mRID>",
        "        <availability>Dispon\u00edvel</availability>",
        "        <fcConnectionStatus>Online</fcConnectionStatus>",
        "      </Crew>",
        "    </Crews>",
        "  </Payload>",
    ]
    return _payload(f'<ChangedCrews xmlns="{NS_CREWS}">', body, "ChangedCrews")


def wfm_response_xml(msg_id: str, corr_id: str) -> tuple[str, str]:
    body = [
        *_hdr("reply", "Crews", msg_id, corr_id),
        "  <Reply>",
        f'    <Result xmlns="{NS_MSG}">OK</Result>',
        "  </Reply>",
    ]
    return _payload(f'<CrewsResponseMessage xmlns="{NS_CREWS}">', body, "CrewsResponseMessage")


def wfm_fault_xml() -> tuple[str, str]:
    body = [
        "  <faultcode>s:Client</faultcode>",
        '  <faultstring xml:lang="en-US">Synthetic failure while processing the request.</faultstring>',
        "  <detail>",
        f'    <CrewsFaultMessage xmlns="{NS_CREWS}">',
        "      <Reply>",
        f'        <Result xmlns="{NS_MSG}">FAILED</Result>',
        "        <Error>",
        "          <code>5.3</code>",
        "          <level>FATAL</level>",
        "          <reason>Synthetic reason</reason>",
        "        </Error>",
        "      </Reply>",
        "    </CrewsFaultMessage>",
        "  </detail>",
    ]
    return _payload('<s:Fault xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">', body, "s:Fault")


def ont_request_xml(msg_id: str, inc: str, comment_lines: list[str]) -> tuple[str, str]:
    """Incident request whose `comment` element may span several physical lines (and holds `&amp;`).

    The lines after the first one of the text are continuation lines with neither indentation nor `<`.
    """
    text = "\n".join(comment_lines)
    body = [
        *_hdr("changed", "Incidents", msg_id, msg_id),
        "  <Payload>",
        "    <Incidents>",
        "      <IncidentRecord>",
        f"        <mRID>{inc}</mRID>",
        *f"        <comment>{text}</comment>".split("\n"),
        "      </IncidentRecord>",
        "    </Incidents>",
        "  </Payload>",
    ]
    return _payload(f'<ChangedIncidents xmlns="{NS_INC}">', body, "ChangedIncidents")


def reply_xml(prefix_ns: str, noun: str, ns: str) -> tuple[str, str]:
    """Reply of the SAP PI: no MessageID and no CorrelationID."""
    root = f"{prefix_ns}:{noun}ResponseMessage"
    body = [
        f"  <{prefix_ns}:Header>",
        f'    <ns9:Verb xmlns:ns9="{NS_MSG}">reply</ns9:Verb>',
        f'    <ns9:Noun xmlns:ns9="{NS_MSG}">{noun}</ns9:Noun>',
        f"  </{prefix_ns}:Header>",
        f"  <{prefix_ns}:Reply>",
        f'    <ns9:Result xmlns:ns9="{NS_MSG}">OK</ns9:Result>',
        f"  </{prefix_ns}:Reply>",
    ]
    return _payload(f'<{root} xmlns:{prefix_ns}="{ns}">', body, root)


def smn_request_xml(msg_id: str, noun: str, ns: str, mrid: str) -> tuple[str, str]:
    body = [
        *_hdr("changed", noun, msg_id, msg_id),
        "  <Payload>",
        f"    <{noun}>",
        f"      <mRID>{mrid}</mRID>",
        f"    </{noun}>",
        "  </Payload>",
    ]
    return _payload(f'<Changed{noun} xmlns="{ns}">', body, f"Changed{noun}")


def resource_description(i: int, *, crlf_value: bool = False) -> list[str]:
    """One ``ResourceDescription`` of an Smn publication (a sequence of siblings, no single root)."""
    value = "Line one&#xD;&#xA;line two" if crlf_value else f"SP {1000000 + i}"
    return [
        f'<ResourceDescription xmlns="urn:example:dms" gid="{28823000 + i}">',
        f'  <Property id="NAME" value="{value}" />',
        f'  <Property id="TYPE" value="Example &amp; synthetic {i}" />',
        "</ResourceDescription>",
    ]


def _wfm_scenario(sensitive: bool) -> list[Rec]:
    g1, g2, resp = _g(101), _g(102), "SYNT_0000001"
    req_msg, req_cont = wfm_request_xml(g1)
    rsp_msg, rsp_cont = wfm_response_xml(resp, g1)
    flt_msg, flt_cont = wfm_fault_xml()
    if sensitive:
        return [
            _sc(
                0,
                0x10,
                "INFO",
                f'Changed crews request received. Request with ID "{g1}" content:',
                "WFM-S010",
                "Identifier: CREW0001\nAvailability: Dispon\u00edvel",
            ),
            _sc(5, 0x2A, "INFO", f"Message content: {req_msg}", "GEN-PLD-01", req_cont),
            _sc(
                42,
                0x10,
                "INFO",
                '<delta id="7">',
                "WFM-S112",
                _lines(
                    '  <operation type="insert">',
                    "    <ResourceDescription>",
                    '      <id gid="28823001" type="1"/>',
                    "    </ResourceDescription>",
                    "  </operation>",
                    "</delta>",
                ),
            ),
            _sc(
                66,
                0x10,
                "INFO",
                "Content being sent to IMS:",
                "WFM-S121",
                _lines(
                    '<ArrayOfIncidentCrewAssigment xmlns:xsd="http://www.w3.org/2001/XMLSchema">',
                    "  <IncidentCrewAssigment>",
                    "    <IncidentId>28823010</IncidentId>",
                    "    <CrewId>28823011</CrewId>",
                    "  </IncidentCrewAssigment>",
                    "</ArrayOfIncidentCrewAssigment>",
                ),
            ),
            _sc(101, 0x2B, "INFO", f"Message content: {rsp_msg}", "GEN-PLD-01", rsp_cont),
            _sc(206, 0x2C, "INFO", f"Message content: {flt_msg}", "GEN-PLD-01", flt_cont),
            _sc(
                300,
                0x10,
                "INFO",
                "Creating client proxy object for service URL: net.tcp://example.invalid:9000/CrsService",
                "GEN-PRX-S01",
            ),
        ]
    err = (
        'ERROR - Processing "ChangedCrewsEvent" message failed. ExceptionType: "System.TimeoutException" '
        "Reason: Synthetic timeout. StackTrace: at Example.Wfm.Run() in "
        "F:\\PD\\1234567\\1\\Example\\Run.cs:line 40"
    )
    return [
        _sc(
            0,
            0x10,
            "DEBUGLOG",
            f'Changed crews request received. Request with ID "{g1}". Message content written in sensitive '
            "log.",
            "WFM-010",
        ),
        _sc(
            5,
            0x2A,
            "DEBUGLOG",
            f"Message with MessageID: '{g1}' will be logged in sensitive log.",
            "GEN-MSG-01",
        ),
        _sc(
            6,
            0x10,
            "INFO",
            "Received valid request message according to XML schema definition.",
            "GEN-XSD-01",
        ),
        _sc(
            20,
            0x10,
            "VERBOSE",
            "[Communication2] - TCP client connection id = 3 sending message msgId = 7, msgType = Request, "
            "msgSize = 120.",
            "GEN-C2T-02",
        ),
        _sc(
            21,
            0x10,
            "VERBOSE",
            "[Communication2] - Tcp socket id = 5 send called with 120 bytes count.",
            "GEN-C2T-01",
        ),
        _sc(40, 0x10, "INFO", "Generating Delta...", "WFM-110"),
        _sc(41, 0x10, "INFO", "2 crews added to insert Delta operation.", "WFM-111"),
        _sc(42, 0x10, "INFO", "Writting delta to log sensitive.", "WFM-112"),
        _sc(43, 0x10, "INFO", "[CrsDataManager] Applying delta.", "WFM-113"),
        _sc(
            50,
            0x10,
            "INFO",
            "Content being sent to CRS:",
            "WFM-120",
            _lines(
                '<ArrayOfAssignmentStatusInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema">',
                "  <AssignmentStatusInfo>",
                "    <CrewId>28823011</CrewId>",
                "    <AssignmentStatusId>28823012</AssignmentStatusId>",
                "  </AssignmentStatusInfo>",
                "</ArrayOfAssignmentStatusInfo>",
            ),
        ),
        _sc(52, 0x10, "INFO", "Calling ICRSBusinessContract.OrderedChangeAssignmentStatuses.", "WFM-122"),
        _sc(53, 0x10, "INFO", 'Request IDs returned by CRS are: "28823020".', "WFM-123"),
        _sc(
            60,
            0x10,
            "INFO",
            "Content being sent to WMS:",
            "WFM-120W",
            '<ArrayOfDocumentCrewAssigment xmlns:xsd="http://www.w3.org/2001/XMLSchema" />',
        ),
        _sc(
            66,
            0x10,
            "INFO",
            "Content being sent to IMS. Content details written in sensitive log.",
            "WFM-121",
        ),
        _sc(
            70,
            None,
            "INFO",
            "PSAttach::PubSubReceiveCallBack: Received callback for config value 'ExampleKey' (no change "
            "from 'a' to 'a')",
            "GEN-PSA-01",
        ),
        _sc(
            71,
            None,
            "INFO",
            "arb_pubSubLookup: Received update on topic: example.sys.svc.arb.host.states.syncstate with "
            "status 0",
            "GEN-PSA-02",
        ),
        _sc(
            100, 0x10, "DEBUGLOG", f'Changed crews request with ID "{g1}" successfully processed.', "WFM-012"
        ),
        _sc(
            101,
            0x2B,
            "DEBUGLOG",
            f"Message with MessageID: '{resp}' will be logged in sensitive log.",
            "GEN-MSG-01",
        ),
        _sc(
            200,
            0x11,
            "DEBUGLOG",
            f'Changed vehicles request received. Request with ID "{g2}". Message content written in '
            "sensitive log.",
            "WFM-010",
        ),
        _sc(
            205, 0x2C, "DEBUGLOG", "Message with MessageID: '' will be logged in sensitive log.", "GEN-MSG-01"
        ),
        _sc(
            206,
            0x11,
            "ERROR",
            err,
            "WFM-081",
            "   at Example.Wfm.Process() in F:\\PD\\1234567\\1\\Example\\Process.cs:line 12",
        ),
    ]


def _ont_scenario(sensitive: bool) -> list[Rec]:
    g1, g2 = _g(201), _g(202)
    r1_msg, r1_cont = ont_request_xml(
        g1,
        "INC 1001",
        ["Falta de energia &amp; manuten\u00e7\u00e3o", "segunda linha do coment\u00e1rio", "terceira linha"],
    )
    r2_msg, r2_cont = ont_request_xml(g2, "INC 1002", ["Sem coment\u00e1rio adicional"])
    p_msg, p_cont = reply_xml("ns1", "Incidents", NS_INC)
    if sensitive:
        return [
            _sc(20, 0x2A, "INFO", f"Message: {r1_msg}", "GEN-PLD-01", r1_cont),
            _sc(20, 0x2B, "INFO", f"Message: {r2_msg}", "GEN-PLD-01", r2_cont),
            # two concurrent operations, two identical replies without any ID, a few ms apart
            _sc(60, 0x2C, "INFO", f"Message: {p_msg}", "GEN-PLD-01", p_cont),
            _sc(62, 0x2D, "INFO", f"Message: {p_msg}", "GEN-PLD-01", p_cont),
            _sc(
                80,
                0x32,
                "INFO",
                'Error: Sending "IncidentsEventMessageType" message failed. "System.TimeoutException" has '
                "occurred.",
                "ONT-S020",
                "Reason: The request channel timed out.\nStack trace:\n   at Example.Client.Send(Message "
                "m) in F:\\PD\\1234567\\1\\Example\\Client.cs:line 77",
            ),
            _sc(
                90,
                0x32,
                "INFO",
                "Creating client proxy object for service URL: http://sap-pi.example.invalid:50001/other",
                "GEN-PRX-S01",
            ),
        ]
    return [
        _sc(0, 0x31, "DEBUGLOG", "[SendIncidents] Sending following Incident: INC 1001.", "ONT-010"),
        _sc(2, 0x32, "DEBUGLOG", "[SendIncidents] Sending following Incident: INC 1002.", "ONT-010"),
        _sc(8, 0x31, "INFO", "[SendIncidents] - Processing of 1 Incident Resources started.", "ONT-110"),
        _sc(
            10,
            0x31,
            "DEBUGLOG",
            f'Sending "IncidentsEventMessageType" message: {g1} with verb: Changed...',
            "ONT-011",
        ),
        _sc(
            11,
            0x32,
            "DEBUGLOG",
            f'Sending "IncidentsEventMessageType" message: {g2} with verb: Changed...',
            "ONT-011",
        ),
        _sc(
            12,
            0x31,
            "INFO",
            "[CachedDataSynchronizer] - Saving 'IncidentsEventMessageType' message with GID: '28823050', "
            f"Identifier: 'INC 1001' and MessageId: '{g1}' to smart cache.",
            "ONT-111A",
        ),
        _sc(
            15,
            0x31,
            "INFO",
            "Sending request message is valid according to XML schema definition.",
            "GEN-XSD-01",
        ),
        _sc(
            20,
            0x2A,
            "DEBUGLOG",
            f"Message with MessageID: '{g1}' will be logged in sensitive log.",
            "GEN-MSG-01",
        ),
        _sc(
            20,
            0x2B,
            "DEBUGLOG",
            f"Message with MessageID: '{g2}' will be logged in sensitive log.",
            "GEN-MSG-01",
        ),
        _sc(
            60, 0x2C, "DEBUGLOG", "Message with MessageID: '' will be logged in sensitive log.", "GEN-MSG-01"
        ),
        _sc(
            62, 0x2D, "DEBUGLOG", "Message with MessageID: '' will be logged in sensitive log.", "GEN-MSG-01"
        ),
        _sc(
            60,
            0x31,
            "DEBUGLOG",
            f'Sending "IncidentsEventMessageType" message: {g1} with verb: Changed finished successfully.',
            "ONT-012",
        ),
        _sc(
            61,
            0x31,
            "INFO",
            f"Request message with MessageId '{g1}' successfully sent. Reply result 'OK' is returned in "
            "response message.",
            "ONT-014",
        ),
        _sc(
            62,
            0x32,
            "DEBUGLOG",
            f'Sending "IncidentsEventMessageType" message: {g2} with verb: Changed finished successfully.',
            "ONT-012",
        ),
        _sc(
            63,
            0x32,
            "INFO",
            f"Request message with MessageId '{g2}' successfully sent. Reply result 'OK' is returned in "
            "response message.",
            "ONT-014",
        ),
        _sc(64, 0x31, "DEBUGLOG", "[SendIncidents] Sending Incident finished successfully", "ONT-013"),
        _sc(65, 0x32, "DEBUGLOG", "[SendIncidents] Sending Incident finished successfully", "ONT-013"),
        _sc(
            70,
            None,
            "INFO",
            "PSAttach::PubSubReceiveCallBack: Received callback for config value 'ExampleKey' (no change "
            "from 'a' to 'a')",
            "GEN-PSA-01",
        ),
    ]


def _smn_scenario(sensitive: bool) -> list[Rec]:
    g1, g2 = _g(301), _g(302)
    w_msg, w_cont = smn_request_xml(g1, "Works", NS_WORKS, "WR 3000001")
    p_msg, p_cont = smn_request_xml(g2, "SwitchingPlans", NS_PLANS, "SP 1000001")
    rw_msg, rw_cont = reply_xml("ns0", "Works", NS_WORKS)
    rp_msg, rp_cont = reply_xml("ns0", "SwitchingPlans", NS_PLANS)
    pub = _lines(
        "DmsType: SwitchingPlan",
        "Operation: Add",
        *resource_description(1, crlf_value=True),
        *resource_description(2),
    )
    html = "\n".join(
        [
            "<html>",
            "<head><title>Service Unavailable</title></head>",
            "<body>",
            "<h1>Service Unavailable</h1>",
            "</body>",
            "</html>",
        ]
    )
    if sensitive:
        return [
            _sc(15, 0x2A, "INFO", f"Message: {w_msg}", "GEN-PLD-01", w_cont),
            _sc(15, 0x2B, "INFO", f"Message: {p_msg}", "GEN-PLD-01", p_cont),
            _sc(50, 0x2C, "INFO", f"Message: {rw_msg}", "GEN-PLD-01", rw_cont),
            _sc(52, 0x2D, "INFO", f"Message: {rp_msg}", "GEN-PLD-01", rp_cont),
            _sc(2100, 0x43, "INFO", "[WmsSubscriber] - Data received from publication:", "SMN-S110", pub),
            # asynchronous logging thread: the timestamp goes back 2 s relative to the previous record
            _sc(
                100,
                0x44,
                "INFO",
                'Error: Sending "WorksEventMessageType" message failed. "ProtocolException" has occurred. '
                "DmsReason: The content type text/html of the response message does not match the content "
                "type of the binding.",
                "SMN-S019",
                html,
            ),
        ]
    return [
        _sc(0, 0x41, "DEBUGLOG", "[SendWorks] Sending following Work Request: WR 3000001.", "SMN-005"),
        _sc(
            2,
            0x42,
            "DEBUGLOG",
            "[SendSwitchingPlans] Sending following Switching Plan: SP 1000001.",
            "SMN-006",
        ),
        _sc(
            10,
            0x41,
            "DEBUGLOG",
            f'Sending "WorksEventMessageType" message: {g1} with verb: Changed...',
            "SMN-007",
        ),
        _sc(
            11,
            0x42,
            "DEBUGLOG",
            f'Sending "SwitchingPlansEventMessageType" message: {g2} with verb: Changed...',
            "SMN-007",
        ),
        _sc(
            15,
            0x2A,
            "DEBUGLOG",
            f"Message with MessageID: '{g1}' will be logged in sensitive log.",
            "GEN-MSG-01",
        ),
        _sc(
            15,
            0x2B,
            "DEBUGLOG",
            f"Message with MessageID: '{g2}' will be logged in sensitive log.",
            "GEN-MSG-01",
        ),
        _sc(
            50, 0x2C, "DEBUGLOG", "Message with MessageID: '' will be logged in sensitive log.", "GEN-MSG-01"
        ),
        _sc(
            52, 0x2D, "DEBUGLOG", "Message with MessageID: '' will be logged in sensitive log.", "GEN-MSG-01"
        ),
        _sc(
            50,
            0x41,
            "DEBUGLOG",
            f'Sending "WorksEventMessageType" message: {g1} with verb: Changed finished successfully.',
            "SMN-008",
        ),
        _sc(
            52,
            0x42,
            "DEBUGLOG",
            f'Sending "SwitchingPlansEventMessageType" message: {g2} with verb: Changed finished '
            "successfully.",
            "SMN-008",
        ),
        _sc(53, 0x41, "DEBUGLOG", "[SendWorks] Sending Work Request finished successfully.", "SMN-009"),
        _sc(
            54,
            0x42,
            "DEBUGLOG",
            "[SendSwitchingPlans] Sending Switching Plan finished successfully.",
            "SMN-009",
        ),
        _sc(
            2100,
            0x43,
            "INFO",
            "[WmsSubscriber] - Data received from publication. Details are written in sensitive log.",
            "SMN-110",
        ),
        _sc(2101, 0x43, "INFO", "[BusinessLogicResolver] - Processing received publication data.", "SMN-111"),
        _sc(
            2102,
            0x43,
            "INFO",
            "[Subscriber] - Publication info:",
            "GEN-SUB-03",
            "Sub-process - Example\nCacheOperation - Add\nDmsType - SwitchingPlan",
        ),
        _sc(
            2200,
            0x45,
            "INFO",
            "[MessageValidator] Message: Could not find schema information for the element "
            "'ns0:SystemError'.",
            "GEN-XSD-02",
        ),
        _sc(
            2300,
            None,
            "INFO",
            "arb_pubSubLookup: Received update on topic: example.sys.svc.arb.host.states.syncstate with "
            "status 0",
            "GEN-PSA-02",
        ),
    ]


_SCENARIOS = {"AWFM": _wfm_scenario, "AONT": _ont_scenario, "ASMN": _smn_scenario}
SCENARIO_FAMILIES = tuple(_SCENARIOS)


def scenario_records(catalog: Catalog, family: str, sensitive: bool) -> list[Rec]:
    """Payload scenario records of one stream (empty for the families without scenarios).

    Every record is checked against the catalog: it must classify as the template it was written for.
    """
    build = _SCENARIOS.get(family)
    if build is None:
        return []
    tset = catalog.for_family(family, sensitive=sensitive)
    recs = build(sensitive)
    for r in recs:
        tm = tset.classify_text(r.msg, r.cont)
        got = tm.code if tm is not None else None
        if got != r.code:
            raise AssertionError(
                f"scenario record of {family} classified as {got}, expected {r.code}: {r.msg[:80]}"
            )
    return recs


def large_publication_log(min_chars: int = 66_000, *, pid: int = 4242) -> tuple[bytes, int]:
    """An Smn sensitive log whose publication record is above 65,536 characters (not committed).

    Returns (file bytes, characters of the publication continuation). The record sits between two
    small ones so that a test can check where it starts and ends.
    """
    items: list[str] = []
    size = 0
    i = 0
    while size < min_chars:
        i += 1
        block = "\n".join(resource_description(i, crlf_value=(i % 50 == 0)))
        items.append(block)
        size += len(block) + 1
    cont = "\n".join(["DmsType: SwitchingPlan", "Operation: Add", *items])
    url = "Creating client proxy object for service URL: net.tcp://example.invalid:9000/Svc"
    lines = [
        _mf.header(0, "INFO", url, pid=pid, tid=0x43),
        _mf.header(10, "INFO", "[WmsSubscriber] - Data received from publication:", pid=pid, tid=0x43),
        *cont.split("\n"),
        _mf.header(20, "INFO", url, pid=pid, tid=0x43),
    ]
    return _mf.render(lines, eol="\r\n", bom=True), len(cont)


def render_stream(family: str, records: list[Rec], pid: int) -> bytes:
    oas = family == "OASERR"
    lines: list[str] = []
    for i, r in enumerate(records):
        at = i * STEP_MS if r.at_ms is None else r.at_ms
        ts = _mf.fmt_ts(at)
        if oas:
            lvl = "ERROR" if r.level in ("ERROR", "ALWAYS", "DEBUGLOG") else r.level
            lines.append(f"{ts} AlphaAdapter_{pid} [PID={pid}][TID=0x1a2b] - ({lvl}) {r.msg}")
        else:
            lines.append(_mf.header(at, r.level, r.msg, pid=pid, tid=r.tid))
        if r.cont:
            lines.extend(r.cont.split("\n"))
    return _mf.render(lines, eol="\r\n", bom=True)


def _file_name(family: str, pid: int, sensitive: bool) -> str:
    layout, proc = PROCESSES[family]
    sens = ".sensitive" if sensitive else ""
    if layout == "adapter":
        return f"{FOLDER}/{proc}_{pid}{sens}.log"
    if layout == "single":
        return f"{FOLDER}/{proc}{sens}.log"
    if layout == "rt":  # <Process>_<pid>[.sensitive].log in a real-time service folder
        return f"{RT_FOLDER}/{proc}_{pid}{sens}.log"
    if layout == "rtfh":  # DMS_FunctionHost_<n>: the number is the pool instance, there is no pid in the name
        return f"{RT_FOLDER}/{proc}{sens}.log"
    return f"{FOLDER}/{proc}.SVC{sens}.log"


def build_all(catalog: Catalog | None = None) -> dict[str, bytes]:
    """Relative POSIX path -> content for every committed fixture file."""
    catalog = catalog or load_catalog()
    out: dict[str, bytes] = {".gitattributes": GITATTRIBUTES}
    for n, family in enumerate(PROCESSES):
        pid = 1000 + n * 11
        for sensitive in (False, True):
            recs = stream_records(catalog, family, sensitive)
            if sensitive and not any(r.kind == "example" for r in recs):
                continue
            recs = recs + scenario_records(catalog, family, sensitive)
            out[_file_name(family, pid, sensitive)] = render_stream(family, recs, pid)
    return out


def write_all(out: Path) -> list[Path]:
    written = []
    for rel, data in build_all().items():
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(target)
    return written


def check_all(out: Path) -> list[str]:
    expected = build_all()
    bad = []
    for rel, data in expected.items():
        target = out / rel
        if not target.is_file() or target.read_bytes() != data:
            bad.append(rel)
    if out.is_dir():
        for p in sorted(out.rglob("*")):
            rel = p.relative_to(out).as_posix()
            if p.is_file() and rel not in expected:
                bad.append(f"unexpected: {rel}")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate the per-family template fixtures.")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    ap.add_argument("--check", action="store_true", help="compare only; exit 1 on difference")
    args = ap.parse_args(argv)
    if args.check:
        bad = check_all(args.out)
        for rel in bad:
            print(f"differs: {rel}", file=sys.stderr)
        return 1 if bad else 0
    for p in write_all(args.out):
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
