"""Guards of the real-time (RT) template files (issue: RT template mining). The repository is public: the RT
templates are written from message shapes with markers and synthetic examples, never from real log lines."""

from __future__ import annotations

import ipaddress
import re
from importlib import resources

import pytest

from dmslog.parse.templates import load_catalog

RT_PREFIXES = ("DFH", "DJB", "DCR", "DMD", "DOM", "RTL", "PSE", "ARB", "NMC", "ERS")
RT_FILES = (
    "_dmsrt.toml",
    "dmsrt.toml",
    "dmsfh.toml",
    "dmsjob.toml",
    "dmscore.toml",
    "dmsmodel.toml",
    "dmsoms.toml",
    "psengine.toml",
    "arbip.toml",
    "nmc.toml",
    "ers.toml",
)
# optional ERS logs: not DMS_* services, they do not extend the RT library
STANDALONE = {"PSENGINE", "ARBIP", "NMC", "ERS"}
GUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
SYNTHETIC_GUID_RE = re.compile(
    r"^(?:0{8}-0{4}-4000-8000-0{9}[0-9a-f]{3}|([0-9a-f])\1{7}-\1{4}-\1{4}-\1{4}-\1{12})$", re.I
)
IPV4_RE = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
# only the three documentation ranges (RFC 5737): a private range such as 10.0.0.0/24 may hold a real address
DOC_NETS = [ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")]
LONG_DIGITS_RE = re.compile(r"(?<![0-9a-fA-Fx])\d{10,}(?![0-9a-fA-F])")


def rt_files() -> list[str]:
    root = resources.files("dmslog.adapters")
    return [n for n in RT_FILES if root.joinpath(n).is_file()]


@pytest.fixture(scope="module")
def catalog():
    return load_catalog(check_examples=True)


def rt_families(catalog):
    sources = {f"{n}" for n in rt_files()}
    return [f for f in catalog.families.values() if f.source.rsplit("/", 1)[-1] in sources]


def leaks_in(name: str, text: str) -> list[str]:
    """What looks like a real value in the text of an RT template file."""
    found = []
    if not text.isascii():
        found.append(f"{name}: only ASCII")
    for line_no, line in enumerate(text.splitlines(), 1):
        for guid in GUID_RE.findall(line):
            if not SYNTHETIC_GUID_RE.match(guid):
                found.append(f"{name}:{line_no}: a GUID that is not synthetic")
        masked = GUID_RE.sub("GUID", line)  # a synthetic GUID has long digit runs of its own
        for ip in IPV4_RE.findall(masked):
            octets = [int(o) for o in ip.split(".")]
            if all(o <= 255 for o in octets) and not any(ipaddress.ip_address(ip) in net for net in DOC_NETS):
                found.append(f"{name}:{line_no}: real-looking IP")
        if LONG_DIGITS_RE.search(masked):
            found.append(f"{name}:{line_no}: a number of 10 or more digits")
    return found


def test_the_rt_files_are_ascii_and_free_of_real_looking_values():
    root = resources.files("dmslog.adapters")
    for name in rt_files():
        assert leaks_in(name, root.joinpath(name).read_text(encoding="utf-8")) == []


def test_the_leak_guard_accepts_synthetic_values_and_refuses_the_rest():
    assert (
        leaks_in("t", 'x = "00000000-0000-4000-8000-000000000001" y = "11111111-1111-1111-1111-111111111111"')
        == []
    )
    assert leaks_in("t", "peer 192.0.2.7 and 198.51.100.9 and 203.0.113.250") == []
    assert leaks_in("t", "peer 10.0.0.42")  # a private range is not a documentation range
    assert leaks_in("t", "peer 172.16.5.4") and leaks_in("t", "peer 8.8.8.8")
    assert leaks_in("t", "id 7d1f3a2e-9c4b-4e55-8a31-0f2b6c7d8e90")
    assert leaks_in("t", "number 1234567890")
    assert leaks_in("t", "caf\u00e9")


def test_every_rt_template_has_the_conventional_fields(catalog):
    for family in rt_families(catalog):
        for t in family.templates:
            where = f"{family.family} {t.code}"
            assert t.code.startswith(RT_PREFIXES), f"{where}: code prefix"
            assert t.sev is not None, f"{where}: sev"
            assert t.title, f"{where}: title"
            assert t.examples, f"{where}: at least one example"
            assert t.counter_examples, f"{where}: at least one counter-example"


def test_the_rt_families_extend_the_rt_library_first(catalog):
    for family in rt_families(catalog):
        if family.family.startswith("_") or family.family in STANDALONE:
            continue
        assert tuple(family.extends[:2]) == ("_dmsrt", "_common"), family.family


@pytest.mark.parametrize(
    ("family", "msg", "code", "sev"),
    [
        (
            "DMSJOB",
            "[Scheduler] JobID: 7 received its status. Job status: DoneWithSuccess, DoneWithSuccess.",
            "DJB-007",
            4,
        ),
        (
            "DMSJOB",
            "[Scheduler] JobID: 7 received its status. Job status: DoneWithSuccess, DoneWithWarning.",
            "DJB-144",
            3,
        ),
        (
            "DMSJOB",
            "[Scheduler] JobID: 7 received its status. Job status: DoneWithError, DoneWithSuccess.",
            "DJB-145",
            2,
        ),
        (
            "DMSJOB",
            "[Scheduler] JobID: 7 received its status. Job status: DoneWithWarning, DoneWithError.",
            "DJB-145",
            2,
        ),
        ("DMSJOB", "Sending job status. JobID: 7, status DoneWithSuccess, DoneWithError.", "DJB-147", 2),
        (
            "DMSJOB",
            "Sending job status to JS. JobID: 7, Status: DoneWithSuccess, DoneWithWarning",
            "DJB-148",
            3,
        ),
        ("DMSJOB", "Sending job status to JS. JobID: 7, Status: DoneWithError, DoneWithError", "DJB-149", 2),
    ],
)
def test_status_list_severity_is_the_worst_status(catalog, family, msg, code, sev):
    tm = catalog.for_family(family, sensitive=False).classify_text(msg)
    assert tm is not None
    assert (tm.code, tm.sev_hint) == (code, sev)


def test_function_host_job_fails_on_every_explicit_failure_code(catalog):
    op = next(o for o in catalog.families["DMSFH"].operations if o["name"] == "FunctionHostJob")
    for code in ("DFH-190", "DFH-195"):
        assert code in op["fail"]


DISCONNECTED = (
    "[NDSSubscriber] OnSubscriberDisconnected: Subscriber disconnected, onUserDemand = %s. Reason: "
)


@pytest.mark.parametrize(
    ("family", "msg", "sev"),
    [
        ("DMSCORE", "[ASC] Prepare phase in AnnualScheduleCache failed.", None),
        ("DMSOMS", "[Archive] Query execution failed.", None),
        ("DMSOMS", "[Archive] : Archived state set.", 5),
        ("DMSOMS", "Outage Id: 4A00B00C00D00E01. Command failed to execute.", None),
        (
            "DMSCORE",
            DISCONNECTED % "True" + "x",
            4,
        ),
        (
            "DMSCORE",
            DISCONNECTED % "False" + "x",
            2,
        ),
        ("DMSOMS", "[WarnExecutionBeforeStartDateCommand] Callback populated with 0 errors.", 4),
        ("DMSOMS", "[WarnExecutionBeforeStartDateCommand] Callback populated with 2 errors.", 3),
    ],
)
def test_failures_are_not_hidden_as_noise(catalog, family, msg, sev):
    tm = catalog.for_family(family, sensitive=False).classify_text(msg)
    got = (
        None
        if tm is None or tm.code.startswith(("DCR-037", "DOM-073", "DOM-059", "DOM-110")) and sev is None
        else tm.sev_hint
    )
    assert got == sev


def test_plugin_not_found_without_default_is_not_noise(catalog):
    tset = catalog.for_family("DMSRT", sensitive=False)
    miss = tset.classify_text(
        "Plugin not found for Example.Service.Missing and no default implementation supplied."
    )
    assert (miss.code, miss.sev_hint) == ("RTL-085", 3)
    assert tset.classify_text("Plugin not found for Example.Service.ModelConverter.").code == "RTL-076"


def test_permissions_reset_failures_are_not_noise(catalog):
    tset = catalog.for_family("ERS", sensitive=False)
    ok = tset.classify_text("icacls: Successfully processed 2 files; Failed processing 0 files")
    bad = tset.classify_text("icacls: Successfully processed 2 files; Failed processing 7 files")
    assert (ok.code, ok.sev_hint) == ("ERS-028", 5)
    assert (bad.code, bad.sev_hint) == ("ERS-029", 3)
