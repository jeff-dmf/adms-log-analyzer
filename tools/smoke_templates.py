"""Template coverage smoke on the real sample logs, plus the effect of per-template match windows.

Run with: PYTHONPATH=src python tools/smoke_templates.py [--samples DIR]
Or via pytest: python -m pytest tests/samples/test_smoke_templates.py

The real ERS samples hold sensitive customer data. This script reads them only by code and prints
counts, percentages and template codes; it never prints log text (not even in error messages).
It exits 0 with a message when the extracted sample folders are missing.

Output:
1. coverage table: per family and stream, records, % with a template, unknown signatures;
2. window effect table: for every template with a ``window``, the records matched with the declared
   window versus with the maximum window, the loss, where the lost records fall, and msg lengths;
3. count invariants stated in docs/formato/*.md, as PASS/FAIL lines.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ("enterprise", "sensitive", "enterprise2", "sensitive2")
MIN_WINDOW = 128


def _sample_roots() -> list[Path]:
    """Candidate folders holding ``_extracted``: env override, this checkout, the main checkout."""
    out: list[Path] = []
    env = os.environ.get("DMSLOG_SAMPLES")
    if env:
        out.append(Path(env))
    out.append(ROOT / "local-samples" / "_extracted")
    git = ROOT / ".git"
    if git.is_file():  # a git worktree: the main checkout is three levels above the gitdir
        try:
            text = git.read_text(encoding="utf-8").strip()
            if text.startswith("gitdir:"):
                gitdir = Path(text.split(":", 1)[1].strip())
                out.append(gitdir.parents[2] / "local-samples" / "_extracted")
        except (OSError, IndexError):
            pass
    return out


def find_samples(explicit: str | None = None) -> dict[str, Path]:
    """Map folder name -> existing sample folder."""
    roots = [Path(explicit)] if explicit else _sample_roots()
    for root in roots:
        found = {n: root / n for n in FOLDERS if (root / n).is_dir()}
        if found:
            return found
    return {}


def percentile(sorted_values: list[int], p: float) -> int:
    if not sorted_values:
        return 0
    idx = min(len(sorted_values) - 1, int(round(p * (len(sorted_values) - 1))))
    return sorted_values[idx]


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--samples", help="folder holding enterprise/, sensitive/, enterprise2/, sensitive2/")
    args = ap.parse_args(argv)

    samples = find_samples(args.samples)
    if not samples:
        print("smoke_templates: sample folders not found; skipping")
        return 0

    from dmslog.ingest.discover import discover
    from dmslog.ingest.pipeline import DETECT_BYTES, DETECT_LINES
    from dmslog.ingest.reader import SourceReader
    from dmslog.parse.envelope import detect_envelope
    from dmslog.parse.records import RecordGrouper
    from dmslog.parse.templates import MAX_MATCH_CHARS, Catalog, load_catalog
    from dmslog.store.writer import cached_signature

    cat = load_catalog()
    wide_cat = Catalog(
        {
            name: dataclasses.replace(
                fam, templates=tuple(dataclasses.replace(t, window=MAX_MATCH_CHARS) for t in fam.templates)
            )
            for name, fam in cat.families.items()
        }
    )
    windowed = [t.code for t in cat.templates() if t.window < MAX_MATCH_CHARS]
    windowed_set = set(windowed)
    window_of = {t.code: t.window for t in cat.templates()}

    # (family, sensitive) -> counters
    records: Counter = Counter()
    matched: Counter = Counter()
    unknown_sigs: dict[tuple[str, bool], set[str]] = defaultdict(set)
    # window effect, over all folders
    with_declared: Counter = Counter()
    with_max: Counter = Counter()
    lost_to: dict[str, Counter] = defaultdict(Counter)
    lengths: dict[str, list[int]] = defaultdict(list)
    # per folder code counts (normal streams only for the invariants)
    code_counts: dict[str, Counter] = defaultdict(Counter)
    failures: list[str] = []

    def feed_source(folder: str, info, reader: SourceReader) -> None:
        key = (info.family, info.sensitive)
        tset = cat.for_family(info.family, sensitive=info.sensitive)
        wide = wide_cat.for_family(info.family, sensitive=info.sensitive)
        grouper = None
        pending: list[bytes] = []
        pending_len = 0

        def handle(recs) -> None:
            for r in recs:
                records[key] += 1
                m = tset.classify(r)
                if m is not None:
                    matched[key] += 1
                    code_counts[folder][(info.sensitive, m.code)] += 1
                else:
                    sig = cached_signature(r.msg)
                    if sig:
                        unknown_sigs[key].add(sig)
                n = len(r.msg.strip())
                if n > MIN_WINDOW:
                    mw = wide.classify(r)
                else:
                    mw = m
                c1 = m.code if m is not None else None
                c2 = mw.code if mw is not None else None
                if c1 in windowed_set:
                    with_declared[c1] += 1
                    lengths[c1].append(len(r.msg))
                if c2 in windowed_set:
                    with_max[c2] += 1
                if c1 != c2 and c2 in windowed_set:
                    lost_to[c2][c1 or "(unknown)"] += 1

        for chunk in reader.iter_chunks(info, 0, 1024 * 1024):
            if grouper is None:
                pending.append(chunk)
                pending_len += len(chunk)
                if pending_len < DETECT_BYTES and sum(c.count(b"\n") for c in pending) < DETECT_LINES:
                    continue
                chunk = b"".join(pending)
                pending.clear()
                grouper = RecordGrouper(detect_envelope(chunk))
            handle(grouper.feed(chunk))
        if grouper is None:
            data = b"".join(pending)
            if not data:
                return
            grouper = RecordGrouper(detect_envelope(data))
            handle(grouper.feed(data))
        handle(grouper.finish(final=True))

    for folder, path in samples.items():
        try:
            infos = [i for i in discover(path) if i.default_scope and i.selectable]
        except Exception as exc:  # noqa: BLE001 - never echo paths or text
            failures.append(f"{folder}: discover failed ({type(exc).__name__})")
            continue
        with SourceReader() as reader:
            for idx, info in enumerate(infos):
                try:
                    feed_source(folder, info, reader)
                except Exception as exc:  # noqa: BLE001 - report only the type and the file index
                    failures.append(f"{folder}: source #{idx} failed ({type(exc).__name__})")

    print(f"folders read: {', '.join(samples)}")
    print()
    print("COVERAGE (default scope sources, all folders together)")
    print(f"{'family':<12}{'stream':<11}{'records':>12}{'with template':>15}{'unknown sigs':>14}")
    tot_r = tot_m = 0
    for key in sorted(records):
        fam, sens = key
        r, m = records[key], matched[key]
        tot_r += r
        tot_m += m
        pct = 100.0 * m / r if r else 100.0
        stream = "sensitive" if sens else "normal"
        print(f"{fam:<12}{stream:<11}{r:>12}{pct:>14.2f}%{len(unknown_sigs[key]):>14}")
    if tot_r:
        n_unknown = sum(map(len, unknown_sigs.values()))
        print(f"{'TOTAL':<23}{tot_r:>12}{100.0 * tot_m / tot_r:>14.2f}%{n_unknown:>14}")

    print()
    print(f"WINDOW EFFECT ({len(windowed)} templates with a window; max window {MAX_MATCH_CHARS})")
    head = f"{'code':<12}{'window':>7}{'declared':>10}{'max':>10}{'lost':>8}"
    print(head + f"  {'p50':>5}{'p99':>6}{'maxlen':>7}  lost falls to")
    for code in windowed:
        d, w = with_declared[code], with_max[code]
        ls = sorted(lengths[code])
        falls = ", ".join(f"{c}:{n}" for c, n in sorted(lost_to[code].items()))
        print(
            f"{code:<12}{window_of[code]:>7}{d:>10}{w:>10}{w - d:>8}  "
            f"{percentile(ls, 0.5):>5}{percentile(ls, 0.99):>6}{(ls[-1] if ls else 0):>7}  {falls}"
        )
    print(f"records lost because of a window (total): {sum(sum(c.values()) for c in lost_to.values())}")

    print()
    print("INVARIANTS (normal streams, per folder that has the stream)")
    resolve = cat.resolve

    def count(folder: str, code: str) -> int:
        canon = resolve(code) or code
        return code_counts[folder][(False, canon)]

    checks = [
        ("CRM-001 = CRM-003 + CRM-004", ["CRM-001"], ["CRM-003", "CRM-004"]),
        ("CRM-010 = CRM-015", ["CRM-010"], ["CRM-015"]),
        ("SMR-001 = SMR-005", ["SMR-001"], ["SMR-005"]),
        ("SMR-001 = SMR-004 + SMR-006 + SMR-007", ["SMR-001"], ["SMR-004", "SMR-006", "SMR-007"]),
        ("CLC-001 = CLC-009 + CLC-010", ["CLC-001"], ["CLC-009", "CLC-010"]),
    ]
    for label, lhs, rhs in checks:
        for folder in samples:
            left = sum(count(folder, c) for c in lhs)
            right = sum(count(folder, c) for c in rhs)
            if left == 0 and right == 0:
                continue  # the folder has no such stream
            status = "PASS" if left == right else "FAIL"
            print(f"{status:<5} {folder:<12} {label}  ({left} vs {right})")
    print("not checked: AMI flow counts (do not close 1:1), WFM-081 vs unclosed requests (needs TID join),")
    print("             sensitive/normal request_id pairing, ONT-011/012 same-TID (no count sum stated),")
    print("             WFM callback GID joins")

    if failures:
        print()
        print(f"{len(failures)} source error(s):")
        for f in failures:
            print(f"  {f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the smoke; an unexpected exception prints only its class name (no traceback, no message)."""
    try:
        return _main(argv)
    except Exception as exc:  # noqa: BLE001 - messages and tracebacks may hold sample names
        print(f"smoke_templates: unexpected {type(exc).__name__} (details withheld)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
