"""Worst-case performance bench for ``dmslog ingest`` (ROADMAP 8.1, ARCHITECTURE 9.3 and 9.3a).

Two modes:

* **Synthetic** (default): generates a deterministic synthetic ERS of a target size (14 adapter
  families at most, Info or Verbose profile, a BLTHost sensitive stream, multi-line records, one adapter
  with very large XML payloads in Verbose, Wfm map dumps), then indexes it with ``dmslog ingest`` in a
  subprocess (temporary ``--data-dir``) and measures it.
* **Existing ERS** (``--ers PATH``): measures a real ERS zip or Log folder. The input is only read,
  never copied or modified.

Measured: wall time of the ingest subprocess, the phase times it reports (derivation, correlation,
payload linking), peak RSS of the subprocess, database size, records, MB/s and records/s. Results are
printed as a table and optionally written as JSON (``--json PATH``) with PASS/FAIL against the M8
targets (RSS < 1 GB by default; time and database limits are optional).

Peak RSS: POSIX uses ``os.wait4`` (``ru_maxrss`` of exactly the subprocess); Windows polls
``GetProcessMemoryInfo`` through ``ctypes`` (stdlib only); elsewhere it is reported as ``n/a``.

Everything created is removed at the end unless ``--keep`` is given. ``--out`` must be a new or empty
directory outside this repository. No network, no telemetry, standard library only.

Examples::

    python tools/bench.py --quick --out /tmp/bench
    python tools/bench.py --size-mb 2600 --adapters 14 --profile verbose --zip --out /data/bench
    python tools/bench.py --ers /path/to/ERS_CL_xxx.zip --out /tmp/bench --json result.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if importlib.util.find_spec("dmslog") is None:  # run from a checkout without installing
    sys.path.insert(0, str(ROOT / "src"))

import dmslog  # noqa: E402


def _load_tool(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


mf = _load_tool("make_fixture")
mff = _load_tool("make_family_fixtures")

SCHEMA_VERSION = 1
MB = 1_000_000
DEFAULT_MAX_RSS_MB = 1024.0
QUICK_SIZE_MB = 0.6
QUICK_ADAPTERS = 3
ADAPTER_FAMILIES = (
    "AWFM", "AONT", "AAVL", "AOSR", "ASMN", "ASSS", "ACRM",
    "AAMI", "ASMR", "AWDI", "ACLC", "AFUP", "AFMO", "AEDP",
)  # fmt: skip
MAX_ADAPTERS = len(ADAPTER_FAMILIES)
HEAVY_FAMILY = "AWFM"  # the adapter that carries the very large XML payloads in Verbose
DUMP_FAMILY = "AWFM"  # and the multi-hundred-line map dumps
SENSITIVE_WEIGHT = 0.15  # size of a sensitive stream relative to its normal stream
BLT_SENSITIVE_WEIGHT = 0.5
EXTRA_WEIGHT = 0.1  # ProcessMonitor, oasErrLog, BLTHost normal
ROTATED_SHARE = 0.6  # share of an adapter stream that lands in the rotated ``.log.0``
FLUSH_BYTES = 1 << 20
LOG_DIR = "SchneiderElectricData/OASyS/Servers/Log"
APP_DIR = f"{LOG_DIR}/dnaApp_zone1/DMS_INTEGRATION"
PROFILES = ("info", "verbose")
ZIP_MAX_RATIO = 200  # the ingest zip-bomb guard (ARCHITECTURE 9.2)


class BenchError(Exception):
    """A usage or environment problem (printed without a traceback)."""


# ---------------------------------------------------------------------------------------- generator
_DEVICE_PREFIXES = ("\\\\?\\", "\\\\.\\", "\\??\\")  # \\?\  \\.\  \??\ with '/' read as '\\'


def is_device_path(path: str | os.PathLike[str]) -> bool:
    """True for a Windows device or extended-length path (``\\\\?\\``, ``\\\\.\\``, ``\\??\\``).

    Case-insensitive, with ``/`` read as ``\\``. These forms have too many equivalent spellings
    (``UNC\\``, ``Volume{GUID}\\``, ...) to compare safely, so they are refused (fail closed).
    """
    text = os.fspath(path).replace("/", "\\").upper()
    return text.startswith(_DEVICE_PREFIXES)


def canonical(path: str | os.PathLike[str], pathmod=os.path) -> str:
    """One canonical text form for every path comparison: absolute, symlinks resolved, case-folded
    per platform. ``pathmod`` is ``ntpath`` in tests. Device paths never get here (``is_device_path``)."""
    return pathmod.normcase(pathmod.realpath(pathmod.abspath(os.fspath(path))))


def _textually_inside(child: str, parent: str, pathmod) -> bool:
    try:
        return pathmod.commonpath([child, parent]) == parent
    except ValueError:  # different drives, or mixed absolute and relative
        return False


def _same_file(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except (OSError, ValueError):
        return False


def _is_inside(child: Path | str, parent: Path | str, pathmod=os.path) -> bool:
    """True when ``child`` is ``parent`` or below it.

    Text is compared in canonical form. On top of that, ``child`` and each of its existing ancestors
    are checked with ``os.path.samefile``, which also sees hard links, junctions and aliases that the
    text comparison cannot (a destination that does not exist yet is covered by its nearest existing
    ancestor).
    """
    if _textually_inside(canonical(child, pathmod), canonical(parent, pathmod), pathmod):
        return True
    if pathmod is not os.path:
        return False
    current = os.path.abspath(os.fspath(child))
    parent_text = os.path.abspath(os.fspath(parent))
    while True:
        if os.path.exists(current) and _same_file(current, parent_text):
            return True
        up = os.path.dirname(current)
        if up == current:
            return False
        current = up


def expand_path(path: Path) -> Path:
    """Expand ``~`` and make the path absolute (symlinks are kept). Called ONCE per input path."""
    try:
        return Path(os.path.abspath(path.expanduser()))
    except RuntimeError as exc:  # no home directory could be determined
        raise BenchError(f"cannot expand '~' in a path argument: {exc}") from None


def check_output_paths(out: Path, ers: Path | None, json_path: Path | None) -> None:
    """Refuse output locations that overlap the input or each other, before anything is written.

    The paths must already be expanded (``expand_path``); nothing is expanded again here. Rules:
    ``--out`` must not be, contain or lie inside the ERS; ``--json`` must not be the ERS, lie inside an
    ERS folder, lie inside ``--out`` (cleanup would delete it) or inside the repository; ``--out`` and
    ``--json`` themselves must not be symbolic links. Messages carry no paths on purpose.
    """
    for label, path in (("--out", out), ("--json", json_path)):
        if path is not None and os.path.islink(path):
            raise BenchError(f"{label} must not be a symbolic link")
    if ers is not None and (_is_inside(out, ers) or _is_inside(ers, out)):
        raise BenchError("--out must not overlap the --ers input")
    if json_path is None:
        return
    if json_path.is_dir():
        raise BenchError("--json must be a file path, not a directory")
    if ers is not None and _is_inside(json_path, ers):  # the ERS zip itself, or inside an ERS folder
        raise BenchError("--json must not overwrite or be inside the --ers input")
    if _is_inside(json_path, out):
        raise BenchError("--json must be outside --out (the cleanup would delete it)")
    if _is_inside(json_path, ROOT):
        raise BenchError("--json must be outside the repository")


def check_out_dir(out: Path) -> Path:
    """Validate ``--out`` (already expanded): outside the repository, and either new or empty."""
    out = out.resolve()
    if _is_inside(out, ROOT):
        raise BenchError("--out must be outside the repository")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise BenchError("--out must be a new or empty directory")
    return out


class _Plan:
    """One log file to generate: family, sensitive flag, relative path(s) and byte budget."""

    def __init__(self, family: str, sensitive: bool, pid: int, weight: float) -> None:
        self.family = family
        self.sensitive = sensitive
        self.pid = pid
        self.weight = weight
        layout, proc = mff.PROCESSES[family]
        sens = ".sensitive" if sensitive else ""
        if layout == "adapter":
            self.rel = f"{APP_DIR}/{proc}_{pid}{sens}.log"
            self.rotates = True
        elif layout == "single":
            self.rel = f"{LOG_DIR}/{proc}{sens}.log"
            self.rotates = False
        else:
            self.rel = f"{LOG_DIR}/{proc}.DMS_INTEGRATION{sens}.log"
            self.rotates = False


def build_plans(adapters: int) -> list[_Plan]:
    """Streams of the synthetic ERS: N adapters (normal + sensitive), BLTHost and the common logs."""
    plans: list[_Plan] = []
    for n, family in enumerate(ADAPTER_FAMILIES[:adapters]):
        pid = 1000 + n * 11
        plans.append(_Plan(family, False, pid, 1.0))
        plans.append(_Plan(family, True, pid, SENSITIVE_WEIGHT))
    plans.append(_Plan("BLTHOST", False, 2000, EXTRA_WEIGHT))
    plans.append(_Plan("BLTHOST", True, 2000, BLT_SENSITIVE_WEIGHT))
    plans.append(_Plan("SERVICE", False, 2100, EXTRA_WEIGHT))
    plans.append(_Plan("OASERR", False, 2200, EXTRA_WEIGHT))
    return plans


class _Pools:
    """Catalog-derived record pools (examples and payload scenarios), per (family, sensitive)."""

    def __init__(self) -> None:
        self.catalog = mff.load_catalog()
        self._cache: dict[tuple[str, bool], tuple[list, list]] = {}

    def get(self, family: str, sensitive: bool) -> tuple[list, list]:
        """(regular records, VERBOSE-level records) of a stream; never empty."""
        key = (family, sensitive)
        if key not in self._cache:
            recs = mff.stream_records(self.catalog, family, sensitive)
            recs += mff.scenario_records(self.catalog, family, sensitive)
            verbose = [r for r in recs if r.level == "VERBOSE"]
            regular = [r for r in recs if r.level != "VERBOSE"]
            if not regular:  # tiny streams: fall back to a generic record
                regular = [mff.Rec("Synthetic bench record.", None, "INFO", None, "bench")]
            self._cache[key] = (regular, verbose)
        return self._cache[key]


def _payload_record(counter: int, target_chars: int) -> tuple[str, str]:
    """A well-formed XML payload (IEC 61968 envelope) of about ``target_chars`` characters.

    Every item is unique (``counter``), so the zip compression ratio stays realistic.
    """
    body = ["  <Payload>"]
    size = 0
    while size < target_chars:
        counter += 1
        for line in mff.resource_description(counter):
            body.append("    " + line)
            size += len(line) + 5
    body.append("  </Payload>")
    msg_id = mff._g(counter % 999_999)
    lines = [*mff._hdr("changed", "Works", msg_id, msg_id), *body, "</ChangedWorks>."]
    return f'Message: <ChangedWorks xmlns="{mff.NS_WORKS}">', "\n".join(lines)


def _dump_record(rng: random.Random, counter: int) -> tuple[str, str]:
    """A multi-hundred-line map dump (Wfm dumps reach 1,646 lines)."""
    n = rng.choice((200, 600, 1600))
    lines = [f"  key{counter + i} = value{(counter + i) * 7 % 100_000}" for i in range(n)]
    return "Dump of the synthetic map:", "\n".join(lines)


def _render(plan: _Plan, rec, ts_ms: int, msg: str | None = None, cont: str | None = None) -> str:
    msg = rec.msg if msg is None else msg
    cont = rec.cont if cont is None else cont
    ts = mf.fmt_ts(ts_ms)
    if plan.family == "OASERR":
        lvl = "ERROR" if rec.level in ("ERROR", "ALWAYS", "DEBUGLOG") else rec.level
        head = f"{ts} AlphaAdapter_{plan.pid} [PID={plan.pid}][TID=0x1a2b] - ({lvl}) {msg}"
    else:
        head = mf.header(ts_ms, rec.level, msg, pid=plan.pid, tid=rec.tid)
    return head + "\r\n" + (cont.replace("\n", "\r\n") + "\r\n" if cont else "")


def stream_chunks(
    plan: _Plan, budget: int, profile: str, seed: int, pools: _Pools, counter_base: int
) -> Iterator[bytes]:
    """Yield UTF-8 chunks of one stream until ``budget`` bytes (BOM included) were produced."""
    rng = random.Random(f"{seed}:{plan.rel}")
    regular, verbose = pools.get(plan.family, plan.sensitive)
    verbose_mode = profile == "verbose"
    heavy = verbose_mode and plan.family == HEAVY_FAMILY and not plan.sensitive
    p_verbose = 0.55 if (verbose_mode and verbose and not plan.sensitive) else 0.0
    p_payload = (0.03 if heavy else 0.0) if not plan.sensitive else (0.04 if verbose_mode else 0.01)
    p_dump = 0.002 if plan.family == DUMP_FAMILY and not plan.sensitive else 0.0
    sizes = (4_000, 20_000, 66_000, 164_000) if heavy else (1_500, 6_000, 20_000)
    size_weights = (5, 3, 2, 1) if heavy else (5, 3, 1)
    counter = counter_base
    ts_ms = 0
    size = len(mf.BOM)
    buf: list[str] = []
    buf_len = 0
    yield mf.BOM
    while size + buf_len < budget:
        ts_ms += rng.randint(0, 40)
        r = rng.random()
        if r < p_payload:
            counter += 1000
            msg, cont = _payload_record(counter, rng.choices(sizes, size_weights)[0])
            rec = mff.Rec(msg, cont, "INFO", None, "bench", 42)
            text = _render(plan, rec, ts_ms)
        elif r < p_payload + p_dump:
            counter += 2000
            msg, cont = _dump_record(rng, counter)
            text = _render(plan, mff.Rec(msg, cont, "DEBUGLOG", None, "bench"), ts_ms)
        elif r < p_payload + p_dump + p_verbose:
            text = _render(plan, rng.choice(verbose), ts_ms)
        else:
            text = _render(plan, rng.choice(regular), ts_ms)
        counter += 1
        buf.append(text)
        buf_len += len(text)
        if buf_len >= FLUSH_BYTES:
            data = "".join(buf).encode("utf-8")
            size += len(data)
            yield data
            buf, buf_len = [], 0
    if buf:
        yield "".join(buf).encode("utf-8")


def _write_chunks(path: Path, chunks: Iterator[bytes]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with open(path, "wb") as fh:
        for chunk in chunks:
            fh.write(chunk)
            total += len(chunk)
    return total


def generate_folder(root: Path, size_bytes: int, adapters: int, profile: str, seed: int) -> dict[str, int]:
    """Write the synthetic ERS Log tree under ``root``. Returns member path -> size in bytes."""
    plans = build_plans(adapters)
    total_weight = sum(p.weight for p in plans)
    pools = _Pools()
    sizes: dict[str, int] = {}
    for n, plan in enumerate(plans):
        budget = max(int(size_bytes * plan.weight / total_weight), 2_000)
        base = n * 10_000_000
        if plan.rotates:
            first = int(budget * ROTATED_SHARE)
            sizes.update(_split_rotated(root, plan, first, budget - first, profile, seed, pools, base))
        else:
            chunks = stream_chunks(plan, budget, profile, seed, pools, base)
            sizes[plan.rel] = _write_chunks(root / plan.rel, chunks)
    return sizes


def _split_rotated(
    root: Path, plan: _Plan, first: int, rest: int, profile: str, seed: int, pools: _Pools, base: int
) -> dict[str, int]:
    """An adapter stream split as ``.log.0`` (older) and ``.log`` (current), as the rotation leaves it."""
    out: dict[str, int] = {}
    out[plan.rel + ".0"] = _write_chunks(
        root / (plan.rel + ".0"), stream_chunks(plan, first, profile, seed, pools, base)
    )
    out[plan.rel] = _write_chunks(
        root / plan.rel, stream_chunks(plan, max(rest, 2_000), profile, seed + 1, pools, base + 5_000_000)
    )
    return out


def zip_folder(root: Path, zip_path: Path) -> None:
    """Zip ``root`` (sorted, fixed timestamps: deterministic), streaming each file in 1 MiB blocks."""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            info = zipfile.ZipInfo(path.relative_to(root).as_posix(), date_time=mf.ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(path, "rb") as src, zf.open(info, "w", force_zip64=True) as dst:
                shutil.copyfileobj(src, dst, 1 << 20)


# ---------------------------------------------------------------------------------------- measuring
class PeakRssSampler:
    """Reads the peak working set (bytes) of a process; ``None`` when the reading failed."""

    def sample(self) -> int | None:
        raise NotImplementedError

    def close(self) -> None:
        """Release whatever the sampler opened (idempotent)."""


def _declare_win_api(kernel32, psapi):
    """Declare the complete ctypes signatures of the Win32 calls and return the counters type.

    Without ``argtypes``/``restype`` ctypes converts a 64-bit ``HANDLE`` to a C ``int`` and truncates it.
    """
    import ctypes
    from ctypes import wintypes

    class ProcessMemoryCountersEx(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]  # fmt: skip

    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCountersEx), wintypes.DWORD,
    ]  # fmt: skip
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    return ProcessMemoryCountersEx


class WindowsPeakSampler(PeakRssSampler):
    """``PeakWorkingSetSize`` through ``GetProcessMemoryInfo`` (Windows only, stdlib ``ctypes``).

    Uses the handle ``subprocess.Popen`` already owns (no PID-reuse race, and it stays valid after the
    process ends, so the final reading is possible); falls back to ``OpenProcess`` otherwise.
    """

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    PROCESS_VM_READ = 0x0010

    def __init__(self, proc: subprocess.Popen) -> None:
        import ctypes

        if sys.platform != "win32":
            raise OSError("WindowsPeakSampler needs Windows")
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        self._psapi = ctypes.WinDLL("psapi", use_last_error=True)  # type: ignore[attr-defined]
        self._counters_type = _declare_win_api(self._kernel32, self._psapi)
        self._owned = False
        popen_handle = getattr(proc, "_handle", None)
        if popen_handle is not None:
            self._handle = int(popen_handle)
        else:
            self._handle = (
                self._kernel32.OpenProcess(
                    self.PROCESS_QUERY_LIMITED_INFORMATION | self.PROCESS_VM_READ, False, proc.pid
                )
                or None
            )
            self._owned = self._handle is not None

    def sample(self) -> int | None:
        import ctypes

        if not self._handle:
            return None
        counters = self._counters_type()
        counters.cb = ctypes.sizeof(counters)
        if not self._psapi.GetProcessMemoryInfo(self._handle, ctypes.byref(counters), counters.cb):
            return None
        return int(counters.PeakWorkingSetSize) or None

    def close(self) -> None:
        if self._owned and self._handle:
            self._kernel32.CloseHandle(self._handle)
        self._owned, self._handle = False, None


def monitor_peak(proc, sampler: PeakRssSampler, interval: float = 0.05) -> tuple[int | None, list[str]]:
    """Poll ``sampler`` until ``proc`` ends and return (peak bytes or None, warnings).

    One last reading is taken after the end is detected (the counter is monotonic, and the process
    handle is still open). If no reading succeeded the peak is ``None`` with a warning, never 0.
    """
    peak: int | None = None
    failures = 0
    while True:
        finished = proc.poll() is not None
        value = sampler.sample()
        if value:
            peak = value if peak is None else max(peak, value)
        else:
            failures += 1
        if finished:
            break
        time.sleep(interval)
    warnings: list[str] = []
    if peak is None:
        warnings.append(f"peak RSS unavailable: the memory query failed ({failures} attempts)")
    return peak, warnings


def _run_posix(cmd: list[str], env: dict[str, str], stdout) -> tuple[int, float | None, str, list[str]]:
    proc = subprocess.Popen(cmd, env=env, stdout=stdout, stderr=subprocess.STDOUT)
    _, status, usage = os.wait4(proc.pid, 0)
    proc.returncode = os.waitstatus_to_exitcode(status)
    scale = 1024 if sys.platform != "darwin" else 1  # ru_maxrss: KiB on Linux, bytes on macOS
    return proc.returncode, usage.ru_maxrss * scale / MB, "wait4/ru_maxrss", []


def _run_windows(cmd: list[str], env: dict[str, str], stdout) -> tuple[int, float | None, str, list[str]]:
    proc = subprocess.Popen(cmd, env=env, stdout=stdout, stderr=subprocess.STDOUT)
    method = "GetProcessMemoryInfo/PeakWorkingSetSize"
    try:
        sampler = WindowsPeakSampler(proc)
    except (OSError, AttributeError, ImportError) as exc:
        proc.wait()
        return proc.returncode, None, "n/a (ctypes unavailable)", [f"peak RSS unavailable: {exc}"]
    try:
        peak, warnings = monitor_peak(proc, sampler)
    finally:
        sampler.close()
    return proc.returncode, (None if peak is None else peak / MB), method, warnings


def run_subprocess(
    cmd: list[str], env: dict[str, str], log_path: Path
) -> tuple[int, float | None, str, float, list[str]]:
    """Run ``cmd`` with stdout/stderr in ``log_path``.

    Returns (code, peak RSS MB or None, method, secs, warnings).
    """
    t0 = time.perf_counter()
    with open(log_path, "wb") as log:
        if hasattr(os, "wait4"):
            code, rss, method, warnings = _run_posix(cmd, env, log)
        elif sys.platform == "win32":
            code, rss, method, warnings = _run_windows(cmd, env, log)
        else:
            proc = subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
            code, rss, method, warnings = proc.returncode, None, "n/a (unsupported platform)", []
    return code, rss, method, time.perf_counter() - t0, warnings


_PATTERNS = {
    "sources_failed": re.compile(r"\((\d+) indexed, \d+ unchanged, \d+ not selected, (\d+) failed\)"),
    "records": re.compile(r"Records: (\d+)\s+Lines: (\d+)\s+Data: (\d+(?:\.\d+)?) MB"),
    "ingest_seconds": re.compile(r"^Time: (\d+(?:\.\d+)?) s", re.M),
    "derive": re.compile(r"Derived events: (\d+) \(derivation took (\d+(?:\.\d+)?) s\)"),
    "correlate": re.compile(r"Links: (\d+) \(correlation took (\d+(?:\.\d+)?) s\)"),
    "payloads": re.compile(
        r"Payloads: (\d+) \((\d+) linked to an operation, linking took (\d+(?:\.\d+)?) s\)"
    ),
}


def parse_ingest_output(text: str) -> dict[str, float | int | None]:
    """Extract the figures ``dmslog ingest`` prints. Missing items are ``None`` (and warned about)."""
    out: dict[str, float | int | None] = dict.fromkeys(
        (
            "records", "lines", "data_mb", "ingest_seconds", "events", "derive_seconds", "links",
            "correlate_seconds", "payloads", "payload_seconds", "sources_failed",
        ),
        None,
    )  # fmt: skip

    def grab(key: str, fields: dict[str, tuple[int, type]]) -> None:
        if m := _PATTERNS[key].search(text):
            try:
                converted = {name: kind(m[group]) for name, (group, kind) in fields.items()}
            except ValueError:  # malformed number: leave the items None (reported as missing)
                return
            out.update(converted)

    grab("records", {"records": (1, int), "lines": (2, int), "data_mb": (3, float)})
    grab("ingest_seconds", {"ingest_seconds": (1, float)})
    grab("derive", {"events": (1, int), "derive_seconds": (2, float)})
    grab("correlate", {"links": (1, int), "correlate_seconds": (2, float)})
    grab("payloads", {"payloads": (1, int), "payload_seconds": (3, float)})
    grab("sources_failed", {"sources_failed": (2, int)})
    return out


def db_size_bytes(data_dir: Path) -> int:
    """Total size of the case databases (``case.sqlite`` plus WAL/SHM leftovers)."""
    total = 0
    for path in data_dir.rglob("case.sqlite*"):
        if path.is_file():
            total += path.stat().st_size
    return total


def zip_uncompressed_bytes(path: Path) -> int | None:
    try:
        with zipfile.ZipFile(path) as zf:
            return sum(i.file_size for i in zf.infolist())
    except (OSError, zipfile.BadZipFile):
        return None


def measure(target: Path, data_dir: Path, work: Path, all_logs: bool) -> dict[str, object]:
    """Run ``dmslog ingest`` on ``target`` and collect the measurements."""
    cmd = [sys.executable, "-m", "dmslog", "--data-dir", str(data_dir), "ingest", str(target)]
    if all_logs:
        cmd.append("--all-logs")
    env = dict(os.environ)
    package_parent = str(Path(dmslog.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (package_parent, env.get("PYTHONPATH", "")) if p)
    env["PYTHONIOENCODING"] = "utf-8"
    log_path = work / "ingest.out"
    code, rss, method, wall, rss_warnings = run_subprocess(cmd, env, log_path)
    text = log_path.read_text(encoding="utf-8", errors="replace")
    parsed = parse_ingest_output(text)
    warnings = [f"missing in ingest output: {k}" for k, v in parsed.items() if v is None and code == 0]
    warnings.extend(rss_warnings)
    if code != 0:
        warnings.append(f"ingest exited with code {code}: " + " | ".join(text.strip().splitlines()[-3:]))
    return {
        "command": cmd[2:],
        "returncode": code,
        "wall_seconds": wall,
        "peak_rss_mb": rss,
        "rss_method": method,
        "db_bytes": db_size_bytes(data_dir),
        "parsed": parsed,
        "warnings": warnings,
    }


def build_report(
    mode: str, params: dict[str, object], inputs: dict[str, object], run: dict[str, object],
    targets_cfg: dict[str, float | None], generate_seconds: float | None,
) -> dict[str, object]:  # fmt: skip
    """Combine inputs and measurements into the JSON document, with PASS/FAIL per target."""
    parsed = run["parsed"]
    assert isinstance(parsed, dict)
    wall = float(run["wall_seconds"])  # type: ignore[arg-type]
    data_mb = inputs.get("uncompressed_mb")
    if not isinstance(data_mb, (int, float)):
        data_mb = parsed.get("data_mb")
    records = parsed.get("records")
    result = {
        "wall_seconds": round(wall, 3),
        "ingest_seconds_reported": parsed.get("ingest_seconds"),
        "derive_seconds": parsed.get("derive_seconds"),
        "correlate_seconds": parsed.get("correlate_seconds"),
        "payload_seconds": parsed.get("payload_seconds"),
        "records": records,
        "lines": parsed.get("lines"),
        "events": parsed.get("events"),
        "links": parsed.get("links"),
        "payloads": parsed.get("payloads"),
        "sources_failed": parsed.get("sources_failed"),
        "data_mb": data_mb,
        "mb_per_s": round(data_mb / wall, 2) if data_mb and wall > 0 else None,
        "records_per_s": round(records / wall) if records and wall > 0 else None,
        "peak_rss_mb": None if run["peak_rss_mb"] is None else round(float(run["peak_rss_mb"]), 1),  # type: ignore[arg-type]
        "rss_method": run["rss_method"],
        "db_mb": round(int(run["db_bytes"]) / MB, 1),  # type: ignore[call-overload]
        "returncode": run["returncode"],
    }
    raw_values = {  # the targets compare the raw measurements; rounding is for display only
        "max_rss_mb": None if run["peak_rss_mb"] is None else float(run["peak_rss_mb"]),  # type: ignore[arg-type]
        "max_seconds": wall,
        "max_db_mb": int(run["db_bytes"]) / MB,  # type: ignore[call-overload]
    }
    targets: dict[str, dict[str, object]] = {}
    for name, limit in targets_cfg.items():
        if limit is None:
            continue
        raw = raw_values[name]
        status = "N/A" if raw is None else ("PASS" if raw <= limit else "FAIL")  # at the limit passes
        targets[name] = {
            "limit": limit, "value": None if raw is None else round(raw, 3), "value_raw": raw,
            "status": status,
        }  # fmt: skip
    failed = run["returncode"] != 0 or bool(parsed.get("sources_failed"))
    overall = "FAIL" if failed or any(t["status"] == "FAIL" for t in targets.values()) else "PASS"
    return {
        "tool": "bench",
        "schema": SCHEMA_VERSION,
        "mode": mode,
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "dmslog_version": getattr(dmslog, "__version__", None),
        "params": params,
        "input": inputs,
        "generate_seconds": None if generate_seconds is None else round(generate_seconds, 2),
        "result": result,
        "targets": targets,
        "warnings": run["warnings"],
        "status": overall,
    }


def format_table(report: dict[str, object]) -> str:
    """Human-readable summary of ``report``."""
    res: dict = report["result"]  # type: ignore[assignment]
    inp: dict = report["input"]  # type: ignore[assignment]

    def fmt(value, unit: str = "", digits: int = 1) -> str:
        if value is None:
            return "n/a"
        if isinstance(value, float):
            return f"{value:,.{digits}f}{unit}"
        return f"{value:,}{unit}"

    rows = [
        ("Mode", str(report["mode"])),
        ("Input", str(inp.get("path"))),
        ("Input size on disk", fmt(inp.get("disk_mb"), " MB")),
        ("Uncompressed logs", fmt(res["data_mb"], " MB")),
        ("Wall time (ingest)", fmt(res["wall_seconds"], " s")),
        ("  derivation", fmt(res["derive_seconds"], " s")),
        ("  correlation", fmt(res["correlate_seconds"], " s")),
        ("  payload linking", fmt(res["payload_seconds"], " s")),
        ("Throughput", f"{fmt(res['mb_per_s'], ' MB/s')}  {fmt(res['records_per_s'], ' records/s')}"),
        ("Records / events", f"{fmt(res['records'])} / {fmt(res['events'])}"),
        ("Peak RSS", f"{fmt(res['peak_rss_mb'], ' MB')}  [{res['rss_method']}]"),
        ("Database", fmt(res["db_mb"], " MB")),
    ]
    width = max(len(k) for k, _ in rows)
    lines = [f"{k:<{width}}  {v}" for k, v in rows]
    targets: dict = report["targets"]  # type: ignore[assignment]
    if targets:
        lines.append("")
        for name, t in targets.items():
            lines.append(
                f"[{t['status']}] {name}: {fmt(t['value'], digits=2)} (limit {fmt(t['limit'], digits=2)})"
            )
    for warning in report["warnings"]:  # type: ignore[attr-defined]
        lines.append(f"warning: {warning}")
    lines.append(f"\nOverall: {report['status']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="bench.py",
        description=(
            "Worst-case performance bench for 'dmslog ingest': generates a synthetic ERS (or uses "
            "--ers) and measures wall time, peak RSS, database size and throughput."
        ),
    )
    ap.add_argument("--out", type=str, required=True,
                    help="Work directory (new or empty, outside the repository); removed at the end "
                    "unless --keep. Windows \\\\?\\ and \\\\.\\ paths are refused.")  # fmt: skip
    ap.add_argument("--ers", type=str, metavar="PATH",
                    help="Measure an existing ERS zip or Log folder (read only, never copied) instead "
                    "of generating one. Windows \\\\?\\ and \\\\.\\ paths are refused.")  # fmt: skip
    ap.add_argument("--size-mb", type=float, default=200.0,
                    help="Target uncompressed size of the synthetic logs in MB (default: 200).")  # fmt: skip
    ap.add_argument("--adapters", type=int, default=MAX_ADAPTERS,
                    help=f"Adapter families, 1 to {MAX_ADAPTERS} (default: {MAX_ADAPTERS}).")  # fmt: skip
    ap.add_argument("--profile", choices=PROFILES, default="info",
                    help="Log level of the window: info, or verbose (Communication2 trace, very large "
                    "XML payloads in one adapter).")  # fmt: skip
    ap.add_argument("--seed", type=int, default=20301, help="Random seed (default: 20301).")
    ap.add_argument("--zip", action="store_true", help="Zip the synthetic ERS and ingest the zip.")
    ap.add_argument("--all-logs", action="store_true", help="Pass --all-logs to 'dmslog ingest'.")
    ap.add_argument("--quick", action="store_true",
                    help=f"Tiny run for tests and CI ({QUICK_SIZE_MB} MB, {QUICK_ADAPTERS} adapters, "
                    "verbose, zipped).")  # fmt: skip
    ap.add_argument("--json", type=str, metavar="PATH",
                    help="Also write the result as JSON. Windows \\\\?\\ and \\\\.\\ paths are "
                    "refused.")  # fmt: skip
    ap.add_argument("--keep", action="store_true", help="Keep the generated ERS and the data directory.")
    ap.add_argument("--max-rss-mb", type=float, default=DEFAULT_MAX_RSS_MB,
                    help=f"RSS target in MB; 0 disables (default: {DEFAULT_MAX_RSS_MB:g}). Targets "
                    "compare the raw measurement and pass when it is <= the limit.")  # fmt: skip
    ap.add_argument("--max-seconds", type=float, default=0.0,
                    help="Wall-time target in seconds; 0 disables (default).")  # fmt: skip
    ap.add_argument("--max-db-mb", type=float, default=0.0,
                    help="Database size target in MB; 0 disables (default).")  # fmt: skip
    return ap


def _limit(value: float) -> float | None:
    return value if value > 0 else None


def run(args: argparse.Namespace) -> dict[str, object]:
    if args.quick:
        args.size_mb, args.adapters, args.profile, args.zip = QUICK_SIZE_MB, QUICK_ADAPTERS, "verbose", True
    if not 1 <= args.adapters <= MAX_ADAPTERS:
        raise BenchError(f"--adapters must be between 1 and {MAX_ADAPTERS}")
    if args.size_mb <= 0:
        raise BenchError("--size-mb must be positive")
    for label, given in (("--out", args.out), ("--ers", args.ers), ("--json", args.json)):
        if given is not None and is_device_path(given):  # raw text, before Path() or any other normalization
            raise BenchError(
                f"{label} must not use a Windows device or extended-length path (\\\\?\\ or \\\\.\\ prefix)"
            )
    args.out = expand_path(Path(args.out))  # expanded once; these exact paths are used from here on
    args.ers = None if args.ers is None else expand_path(Path(args.ers))
    args.json = None if args.json is None else expand_path(Path(args.json))
    if args.ers is not None and not args.ers.exists():
        raise BenchError("--ers path not found")
    check_output_paths(args.out, args.ers, args.json)
    out = check_out_dir(args.out)
    created = not out.exists()
    out.mkdir(parents=True, exist_ok=True)
    targets_cfg = {
        "max_rss_mb": _limit(args.max_rss_mb),
        "max_seconds": _limit(args.max_seconds),
        "max_db_mb": _limit(args.max_db_mb),
    }
    data_dir = out / "data"
    work = out / "work"
    work.mkdir()
    data_dir.mkdir()
    try:
        generate_seconds = None
        if args.ers is not None:
            target = args.ers.resolve()
            mode, params = "ers", {"all_logs": args.all_logs}
            disk = target.stat().st_size if target.is_file() else None
            unc = zip_uncompressed_bytes(target) if target.is_file() else None
            inputs: dict[str, object] = {
                "path": str(target),
                "disk_mb": None if disk is None else round(disk / MB, 1),
                "uncompressed_mb": None if unc is None else round(unc / MB, 1),
            }
        else:
            mode = "synthetic"
            params = {
                "size_mb": args.size_mb, "adapters": args.adapters, "profile": args.profile,
                "seed": args.seed, "zip": args.zip, "quick": args.quick, "all_logs": args.all_logs,
            }  # fmt: skip
            t0 = time.perf_counter()
            tree = out / "ers"
            sizes = generate_folder(tree, int(args.size_mb * MB), args.adapters, args.profile, args.seed)
            target = tree
            total = sum(sizes.values())
            disk = total
            if args.zip:
                target = out / mf.ers_file_name("enterprise", mf.SYNTH_GUID, "20300115103000", "bench")
                zip_folder(tree, target)
                shutil.rmtree(tree)
                disk = target.stat().st_size
            generate_seconds = time.perf_counter() - t0
            inputs = {
                "path": str(target),
                "files": len(sizes),
                "disk_mb": round(disk / MB, 1),
                "uncompressed_mb": round(total / MB, 1),
                "compression_ratio": round(total / disk, 1) if args.zip and disk else None,
            }
        report = build_report(mode, params, inputs, measure(target, data_dir, work, args.all_logs),
                              targets_cfg, generate_seconds)  # fmt: skip
        ratio = inputs.get("compression_ratio")
        if isinstance(ratio, (int, float)) and ratio > ZIP_MAX_RATIO * 0.75:
            report["warnings"].append(  # type: ignore[attr-defined]
                f"compression ratio {ratio} is close to the ingest zip-bomb limit ({ZIP_MAX_RATIO}x)"
            )
        if args.keep:
            report["kept"] = str(out)
        return report
    finally:
        if not args.keep:
            shutil.rmtree(out) if created else _empty_dir(out)


def _empty_dir(path: Path) -> None:
    for child in path.iterdir():
        shutil.rmtree(child) if child.is_dir() else child.unlink()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run(args)
    except BenchError as exc:
        print(f"bench: error: {exc}", file=sys.stderr)
        return 2
    print(format_table(report))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        with open(args.json, "w", encoding="utf-8", newline="") as fh:
            json.dump(report, fh, indent=2)
            fh.write("\n")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
