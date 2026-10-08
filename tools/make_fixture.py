"""Deterministic generator for the synthetic log fixtures and miniature ERS zips.

Everything is invented: hosts, ids, names, GUIDs. Nothing is derived from a real log.
Run ``python tools/make_fixture.py`` to (re)write ``tests/fixtures/logs/``;
``--check`` only compares and exits 1 on any difference.

Synthetic GUID rule: every GUID in a fixture must start with ``00000000-0000-4000-8000-``
(see ``synth_guid``). The leakage scan in ``tests/unit/test_fixtures.py`` rejects any other GUID.
Reserved documentation IPs only (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24), no e-mails.

Files are written as raw bytes, so BOM, CRLF and lone CR are preserved on Windows and Linux.
"""

from __future__ import annotations

import argparse
import random
import sys
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

SEED = 20301
BASE = datetime(2030, 1, 15, 10, 0, 0)
GUID_PREFIX = "00000000-0000-4000-8000-"
SYNTH_GUID = GUID_PREFIX + "000000000001"
BOM = b"\xef\xbb\xbf"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "logs"
ZIP_DATE = (2030, 1, 15, 10, 30, 0)
ZIP_ROOT = "SchneiderElectricData/OASyS/Servers/Log/dnaApp_mpdmz/DMS_INTEGRATION"
LOG_ROOT = "SchneiderElectricData/OASyS/Servers/Log"


def synth_guid(n: int) -> str:
    return f"{GUID_PREFIX}{n:012d}"


def fmt_ts(offset_ms: int) -> str:
    t = BASE + timedelta(milliseconds=offset_ms)
    return t.strftime("%Y-%m-%d %H:%M:%S.") + f"{t.microsecond // 1000:03d}"


def header(
    ts_ms: int, level: str, msg: str, *, pid: int = 1111, tid: int | None = 0x1A2B, ts: str | None = None
) -> str:
    tid_part = f"[TID=0x{tid:x}]" if tid is not None else ""
    return f"{ts or fmt_ts(ts_ms)} [PID={pid}]{tid_part} {level} - {msg}"


def render(
    lines: list[str], *, eol: str = "\n", bom: bool = False, final_newline: bool = True
) -> bytes:
    text = eol.join(lines) + (eol if final_newline else "")
    return (BOM if bom else b"") + text.encode("utf-8")


def _basic_lines(n: int = 12, pid: int = 1111, start_ms: int = 0) -> list[str]:
    msgs = [
        ("INFO", "Adapter Alpha started."),
        ("DEBUGLOG", "[Alpha] Processing request 1 for work item W-001."),
        ("DEBUGLOG", "[Alpha] Validation passed for work item W-001."),
        ("DEBUGLOG", "[Alpha] Sending response 1 for work item W-001."),
        ("WARNING", "[Alpha] Retry 1 of 3 for endpoint svc-one."),
        ("ERROR", "[Alpha] Request 2 failed: synthetic error code E-100."),
        ("DEBUGLOG", "[Alpha] Processing request 3 for work item W-003."),
        ("INFO", "[Alpha] Heartbeat ok."),
    ]
    out = []
    for i in range(n):
        level, text = msgs[i % len(msgs)]
        out.append(header(start_ms + i * 40, level, text, pid=pid))
    return out


# ---------------------------------------------------------------- individual fixtures


def fx_bom_crlf() -> bytes:
    return render(_basic_lines(), eol="\r\n", bom=True)


def fx_lf_only() -> bytes:
    return render(_basic_lines())


def fx_no_tid() -> bytes:
    lines = [
        header(0, "INFO", "PSAttach::Connect: synthetic attach to channel one", tid=None),
        header(5, "INFO", "arb_pubSubLookup: synthetic lookup done", tid=None),
        header(9, "INFO", "Message with TID follows."),
        header(12, "INFO", "PSAttach::Release: synthetic release", tid=None),
    ]
    return render(lines)


def fx_leading_tab() -> bytes:
    lines = [
        header(0, "ALWAYS", "\tManaged BLT: bobjSyntheticOne.dll"),
        header(3, "ALWAYS", "\tManaged BLT: bobjSyntheticTwo.dll"),
        header(6, "INFO", "Next record is a normal one."),
        header(9, "INFO", "Trailing space on purpose "),
        header(12, "INFO", "\tTab first, space last "),
    ]
    return render(lines)


def _stack_lines() -> list[str]:
    return [
        header(
            100,
            "DEBUGLOG",
            "Non Fatal Exception System.InvalidOperationException: Synthetic failure in widget 7",
        ),
        "\tManaged Code Stack Trace:",
        "   at Acme.Widgets.Runner.Execute(String name) in F:\\build\\acme\\Runner.cs:line 42",
        "   at Acme.Widgets.Host.Dispatch(Request r) in F:\\build\\acme\\Host.cs:line 118",
        "\tInner Exception System.TimeoutException: Synthetic timeout after 30 s",
        "\t\t   at Acme.Net.Client.Send(Byte[] data) in F:\\build\\acme\\Client.cs:line 77",
        "\t\t   at Acme.Net.Client.Call(String op) in F:\\build\\acme\\Client.cs:line 31",
    ]


def fx_continuations() -> bytes:
    lines = [header(0, "INFO", "Before the continuation blocks.")]
    lines += _stack_lines()
    lines += [
        header(200, "DEBUGLOG", "Request payload follows:"),
        "<Envelope>",
        "  <Body>",
        '    <Item id="A-1">',
        "      <Name>Synthetic item</Name>",
        "    </Item>",
        "  </Body>",
        "</Envelope>",
        header(300, "DEBUGLOG", "[Communication2] - Client-side statistics:"),
        "[TOTAL][Client] Call count: 10, Request bytes: 1000 (avg. 100.0), Response bytes: 2000 "
        "(avg. 200.0). Total operations: 10",
        "By call count:",
        "OpOne,6,600,1200",
        "OpTwo,4,400,800",
        header(400, "INFO", "After the continuation blocks."),
    ]
    return render(lines, eol="\r\n", bom=True)


def fx_big_dump() -> bytes:
    lines = [header(0, "DEBUGLOG", "Map dump follows:")]
    lines += [f"k{i:04d}=v{i % 7}" for i in range(1600)]
    lines.append(header(50, "DEBUGLOG", "Map dump finished."))
    return render(lines)


def fx_truncated() -> bytes:
    lines = _basic_lines(5)
    lines += [
        header(300, "DEBUGLOG", "Record cut in the middle of a continuation"),
        "first continuation line",
        "second continuation li",
    ]
    return render(lines, final_newline=False)


def fx_orphans() -> bytes:
    lines = [
        "orphan line one, written before any header",
        "\torphan line two with a tab",
        "",
    ]
    lines += _basic_lines(3)
    return render(lines)


def fx_ts_backstep_3ms() -> bytes:
    lines = [
        header(100, "INFO", "Record A."),
        header(110, "INFO", "Record B."),
        header(107, "INFO", "Record C, 3 ms before B."),
        header(120, "INFO", "Record D."),
    ]
    return render(lines)


def fx_ts_backstep_1h() -> bytes:
    lines = [
        header(0, "INFO", "Before the clock moves back.", ts="2030-10-27 02:59:58.000"),
        header(0, "INFO", "Still before.", ts="2030-10-27 02:59:59.900"),
        header(0, "INFO", "Clock moved back one hour.", ts="2030-10-27 02:00:00.100"),
        header(0, "INFO", "After the change.", ts="2030-10-27 02:00:01.000"),
    ]
    return render(lines)


def fx_ts_gap_1h() -> bytes:
    lines = [
        header(0, "INFO", "Before the clock jumps forward.", ts="2030-03-10 01:59:58.000"),
        header(0, "INFO", "Clock jumped forward one hour.", ts="2030-03-10 03:00:00.200"),
        header(0, "INFO", "After the jump.", ts="2030-03-10 03:00:01.000"),
    ]
    return render(lines)


def fx_rotation_old() -> bytes:
    """Older half (`.log.0`): operation 1 starts here and finishes in the `.log` file."""
    lines = [
        header(0, "INFO", "Adapter Alpha started."),
        header(100, "DEBUGLOG", "[Alpha] Processing request 1 for work item W-001."),
        header(200, "DEBUGLOG", "[Alpha] Validation passed for work item W-001."),
    ]
    return render(lines)


def fx_rotation_new() -> bytes:
    lines = [
        header(300, "DEBUGLOG", "[Alpha] Sending response 1 for work item W-001."),
        header(400, "INFO", "[Alpha] Heartbeat ok."),
    ]
    return render(lines)


def fx_oaserr() -> bytes:
    def oa(ms: int, proc: str, pid: int, level: str, msg: str) -> str:
        return f"{fmt_ts(ms)} {proc}_{pid} [PID={pid}][TID=0x1a2b] - ({level}) {msg}"

    lines = [
        oa(0, "AlphaAdapter", 1111, "ERROR", "Synthetic error while sending response."),
        "   at Acme.Widgets.Runner.Execute() in F:\\build\\acme\\Runner.cs:line 9",
        oa(50, "BetaAdapter", 2222, "WARNING", "Synthetic warning, queue length 5."),
        oa(90, "AlphaAdapter", 1111, "ERROR", "Second synthetic error."),
    ]
    return render(lines, eol="\r\n", bom=True)


def fx_plain() -> bytes:
    lines = [
        "Service synthetic-svc starting",
        "  limit cpu=80 mem=2048",
        "",
        "Process alpha.exe restarted by monitor",
    ]
    return render(lines, eol="\r\n")


def fx_utf8_cr() -> bytes:
    parts = [
        header(0, "INFO", "Multibyte: caf\u00e9, \u00e7\u00e3o, \u65e5\u672c\u8a9e, \U0001f600.").encode(),
        header(10, "INFO", "Lone CR inside\rthe same line, still one record.").encode(),
        header(20, "INFO", "Done.").encode(),
    ]
    return b"\n".join(parts) + b"\n"


def fx_script_literal() -> bytes:
    lines = [
        header(0, "INFO", "User text: <script>alert(1)</script> end."),
        header(10, "DEBUGLOG", "Payload follows:"),
        '<a href="x" onclick="alert(1)"><script>alert(1)</script></a>',
        header(20, "INFO", "Done."),
    ]
    return render(lines)


def fx_level_window() -> bytes:
    lines = [
        header(0, "ALWAYS", "Debug level changed from DebugLog (3) to Info (5)."),
        header(100, "INFO", "Inside the window."),
        header(200, "ALWAYS", "Debug level changed from Info (5) to DebugLog (3)."),
        header(300, "DEBUGLOG", "Back to the base level."),
    ]
    return render(lines)


def fx_stats_restart() -> bytes:
    def block(ms: int, calls: int) -> list[str]:
        return [
            header(ms, "DEBUGLOG", "[Communication2] - Client-side statistics:"),
            f"[TOTAL][Client] Call count: {calls}, Request bytes: {calls * 100} (avg. 100.0), "
            f"Response bytes: {calls * 200} (avg. 200.0). Total operations: {calls}",
            "By call count:",
            f"OpOne,{calls},{calls * 100},{calls * 200}",
        ]

    lines = block(0, 50) + block(3_600_000, 120) + [header(3_600_500, "INFO", "Process restarted.")]
    lines += block(7_200_000, 7)  # counters dropped: restart
    return render(lines)


def fx_startup() -> bytes:
    lines = [
        header(0, "ALWAYS", "Process starting, version 1.0"),
        header(1, "ALWAYS", "[Configuration]"),
        header(2, "ALWAYS", "\tManaged BLT: bobjSyntheticOne.dll"),
        header(3, "ALWAYS", "\tLogLevel = 3"),
        header(4, "ALWAYS", "\tMessageLogLevel = 5"),
        header(5, "INFO", "Startup finished."),
    ]
    return render(lines)


# name -> (relative path, builder)
FIXTURES: dict[str, tuple[str, object]] = {
    "bom_crlf": ("bom_crlf.log", fx_bom_crlf),
    "lf_only": ("lf_only.log", fx_lf_only),
    "no_tid": ("no_tid.log", fx_no_tid),
    "leading_tab": ("leading_tab.log", fx_leading_tab),
    "continuations": ("continuations.log", fx_continuations),
    "big_dump": ("big_dump.log", fx_big_dump),
    "truncated": ("truncated.log", fx_truncated),
    "orphans": ("orphans.log", fx_orphans),
    "ts_backstep_3ms": ("ts_backstep_3ms.log", fx_ts_backstep_3ms),
    "ts_backstep_1h": ("ts_backstep_1h.log", fx_ts_backstep_1h),
    "ts_gap_1h": ("ts_gap_1h.log", fx_ts_gap_1h),
    "rotation_old": ("rotation/WfmAdapter_1111.log.0", fx_rotation_old),
    "rotation_new": ("rotation/WfmAdapter_1111.log", fx_rotation_new),
    "oaserr": ("oaserr.log", fx_oaserr),
    "plain": ("plain.log", fx_plain),
    "utf8_cr": ("utf8_cr.log", fx_utf8_cr),
    "script_literal": ("script_literal.log", fx_script_literal),
    "level_window": ("level_window.log", fx_level_window),
    "stats_restart": ("stats_restart.log", fx_stats_restart),
    "startup": ("startup.log", fx_startup),
}


def build_all() -> dict[str, bytes]:
    """Relative POSIX path -> content for every committed fixture."""
    return {rel: builder() for rel, builder in FIXTURES.values()}  # type: ignore[operator]


# ---------------------------------------------------------------- performance log (not committed)


def generate_perf_log(target_bytes: int = 2_000_000, seed: int = SEED) -> bytes:
    """Synthetic adms-envelope log of about ``target_bytes`` (not committed; generated on demand)."""
    rng = random.Random(seed)
    levels = ["DEBUGLOG"] * 6 + ["INFO"] * 2 + ["WARNING", "ERROR"]
    out: list[bytes] = [BOM]
    size = len(BOM)
    ms = 0
    n = 0
    while size < target_bytes:
        ms += rng.randint(0, 40)
        n += 1
        item = rng.randint(1, 9999)
        kind = rng.random()
        if kind < 0.05:
            rec = [header(ms, "DEBUGLOG", f"Non Fatal Exception System.Exception: Synthetic {item}")]
            frame = f"   at Acme.Mod{item % 9}.Run() in F:\\b\\Run.cs:line {item % 400}"
            rec += ["\tManaged Code Stack Trace:", frame]
        elif kind < 0.08:
            rec = [header(ms, "DEBUGLOG", "Payload follows:")]
            rec += ["<Envelope>", f"  <Id>{item}</Id>", "</Envelope>"]
        else:
            lvl = rng.choice(levels)
            text = f"[Alpha] Processing request {n} for work item W-{item:05d}."
            rec = [header(ms, lvl, text, pid=1111 + n % 3)]
        for line in rec:
            b = (line + "\r\n").encode("utf-8")
            out.append(b)
            size += len(b)
    return b"".join(out)


# ---------------------------------------------------------------- miniature ERS zips

DEFAULT_ADAPTERS = ("WfmAdapter", "OntAdapter")


def _zip_write(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)


def ers_file_name(kind: str, guid: str, stamp: str, desc: str) -> str:
    label = {"enterprise": "Enterprise", "sensitive": "Sensitive_Enterprise"}[kind]
    return f"ERS_CL_{guid.upper()}_{stamp}_{label}_{desc}.zip"


def build_ers_zip(
    dest: Path | str,
    *,
    kind: str = "enterprise",
    guid: str = SYNTH_GUID,
    adapters: tuple[str, ...] | list[str] = DEFAULT_ADAPTERS,
    stamp: str = "20300115103000",
    desc: str = "synthetic",
) -> Path:
    """Create a tiny ERS-like zip in directory ``dest`` and return its path.

    ``kind='enterprise'``: adapter logs (+ a rotated ``.log.0`` for the first adapter), BLTHost,
    ProcessMonitor, out-of-scope logs (NMC, oasErrLog), fake binaries/.evtx and DMSIntegration XMLs.
    ``kind='sensitive'``: only the ``.sensitive.log`` counterparts, like a real Sensitive ERS.
    """
    if kind not in ("enterprise", "sensitive"):
        raise ValueError(f"unknown kind: {kind!r}")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / ers_file_name(kind, guid, stamp, desc)
    suffix = ".sensitive.log" if kind == "sensitive" else ".log"
    entries: dict[str, bytes] = {}
    for i, adapter in enumerate(adapters):
        pid = 1111 * (i + 1)
        content = render(_basic_lines(10, pid=pid, start_ms=i * 1000), eol="\r\n", bom=True)
        entries[f"{ZIP_ROOT}/{adapter}_{pid}{suffix}"] = content
        if kind == "enterprise" and i == 0:
            entries[f"{ZIP_ROOT}/{adapter}_{pid}.log.0"] = fx_rotation_old()
    blt = render(
        [header(0, "ALWAYS", "\tManaged BLT: bobjSyntheticOne.dll", pid=4444)], eol="\r\n", bom=True
    )
    entries[f"{LOG_ROOT}/BLTHost.DMS_INTEGRATION{suffix}"] = blt
    if kind == "enterprise":
        entries[f"{LOG_ROOT}/oasErrLog.log"] = fx_oaserr()
        entries[f"{LOG_ROOT}/NMC.log"] = fx_plain()
        entries[f"{LOG_ROOT}/ProcessMonitor.DMS_INTEGRATION.log"] = fx_plain()
        entries["SystemEvents/Application.evtx"] = b"ElfFile\x00" + bytes(range(64))
        entries["SchneiderElectricData/ADMS/temp/PerformanceCountersData/Synthetic.blg"] = (
            b"\x00\x01\x02\x03" * 32
        )
        for name in ("PluginConfiguration_Synthetic.xml", "FileTypeConfiguration_Synthetic.xml"):
            entries[f"SchneiderElectricData/ADMS/configuration/DMSIntegration/{name}"] = (
                b'<?xml version="1.0"?>\r\n<Configuration>\r\n  <Item key="a" value="1"/>\r\n'
                b"</Configuration>\r\n"
            )
    with zipfile.ZipFile(path, "w") as zf:
        for name in sorted(entries):
            _zip_write(zf, name, entries[name])
    return path


# ---------------------------------------------------------------- CLI


def write_all(out: Path) -> list[Path]:
    written = []
    for rel, data in build_all().items():
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(target)
    return written


def check_all(out: Path) -> list[str]:
    bad = []
    for rel, data in build_all().items():
        target = out / rel
        if not target.is_file() or target.read_bytes() != data:
            bad.append(rel)
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate synthetic log fixtures.")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    ap.add_argument("--check", action="store_true", help="compare only; exit 1 on difference")
    ap.add_argument("--perf", type=Path, help="also write the ~2 MB performance log to this path")
    args = ap.parse_args(argv)
    if args.check:
        bad = check_all(args.out)
        for rel in bad:
            print(f"differs: {rel}", file=sys.stderr)
        return 1 if bad else 0
    for p in write_all(args.out):
        print(f"wrote {p}")
    if args.perf:
        args.perf.write_bytes(generate_perf_log())
        print(f"wrote {args.perf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
