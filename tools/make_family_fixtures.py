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
MAX_RECORDS = 200
MIN_RECORDS = 10
STEP_MS = 50
GITATTRIBUTES = b"* -text\n"
COMMON = "_common"

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
    kind: str  # example | unknown | counter


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


def render_stream(family: str, records: list[Rec], pid: int) -> bytes:
    oas = family == "OASERR"
    lines: list[str] = []
    for i, r in enumerate(records):
        ts = _mf.fmt_ts(i * STEP_MS)
        if oas:
            lvl = "ERROR" if r.level in ("ERROR", "ALWAYS", "DEBUGLOG") else r.level
            lines.append(f"{ts} AlphaAdapter_{pid} [PID={pid}][TID=0x1a2b] - ({lvl}) {r.msg}")
        else:
            lines.append(_mf.header(i * STEP_MS, r.level, r.msg, pid=pid))
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
