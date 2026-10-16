"""Property: the ad hoc extraction (ROADMAP 9.8) is a function of the records, not of their ids.

Random records over five sources (a rotation chain, the same member in the two halves of an ERS, a folder),
with small time steps, ties and records without time, short texts and continuation lines, are written to
two cases with different insertion orders of collections and sources and a different interleaving of the
records (other ids, as another indexing order would give). For random regexes, targets, types and limits:

* the rows (by locator), their values, the cut marks and the summary are equal in both cases;
* they equal a brute-force model: all records sorted in Python by the documented order (time with the
  records without time first, rotation, file name, member, ERS half, path, file order), the first ``limit``
  taken, and the regex applied to each through ``Extractor.apply`` on the whole record;
* the drill-down leaf of every value selects, over the same records, exactly the records with that value.

``DMSLOG_EXTRACT_EXAMPLES`` raises the number of examples locally (CI: 40).
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
from extract_helpers import COLLECTIONS, SOURCES, build, locator  # noqa: E402

from dmslog.store import query as Q  # noqa: E402
from dmslog.store.extract import as_json, extract, extractor  # noqa: E402

EXAMPLES = int(os.environ.get("DMSLOG_EXTRACT_EXAMPLES", "40"))
SETTINGS = settings(max_examples=EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])

WORDS = ["id=7", "id=42", "id=", "x", " id=9 ", "\tid=1\t", "code:E1", "code:", "\u00e9", "  "]
REGEXES = [
    (r"id=(?P<n>\d*)", {"n": "int"}),
    (r"id=(?P<n>\d+)", {}),
    (r"^(?P<w>[^ ]+)", {}),
    (r"code:(?P<c>[A-Z]\d+)(?:;(?P<d>\d+))?", {}),
    (r"(?P<w>[a-z]+)=", {}),
    (r"\n(?P<line>[^\n]+)", {}),
]

record = st.tuples(
    st.integers(0, len(SOURCES) - 1),
    st.one_of(st.none(), st.integers(0, 4)),  # ts: few values, so ties are frequent
    st.lists(st.sampled_from(WORDS), min_size=1, max_size=3).map("".join),
    st.one_of(st.none(), st.lists(st.sampled_from(WORDS + ["code:E7;3"]), max_size=3).map("\n".join)),
)


def _model(records, ex, limit):
    rot = {k: s[2] for k, s in enumerate(SOURCES)}
    line: dict[int, int] = {}
    keyed = []
    for rec in records:
        src = rec[0]
        line[src] = line.get(src, 0) + 1
        c, member, rotation, _ = SOURCES[src]
        path, variant = COLLECTIONS[c]
        key = (
            rec[1] is not None, rec[1] or 0, rotation is None, -(rotation or 0), member.rsplit("/", 1)[-1],
            member, variant or "", path, line[src],
        )  # fmt: skip
        keyed.append((key, (path, member, line[src]), rec))
    assert len(rot) == len(SOURCES)
    keyed.sort(key=lambda item: item[0])
    out = []
    for _, loc, rec in keyed[:limit]:
        values, cut = ex.apply(rec[2], rec[3])
        out.append((loc, values, cut))
    return out, len(keyed) > limit


def _rows(conn, result):
    return [(locator(conn, r["id"]), r["values"], r["cut"]) for r in result.rows]


@SETTINGS
@given(
    st.lists(record, max_size=30),
    st.permutations(range(len(SOURCES))),
    st.permutations(range(len(COLLECTIONS))),
    st.sampled_from(REGEXES),
    st.sampled_from(["msg", "text"]),
    st.integers(1, 35),
)
def test_extraction_is_a_function_of_the_records(
    records, source_order, collection_order, spec, target, limit
):
    source, types = spec
    ex = extractor(source, target, types)
    with tempfile.TemporaryDirectory(prefix="dmslog-extract-") as folder:
        a = build(Path(folder) / "a.sqlite", records)
        b = build(Path(folder) / "b.sqlite", records, source_order=list(source_order),
                  collection_order=list(collection_order))  # fmt: skip
        try:
            ra = extract(a, ex, None, limit=limit)
            rb = extract(b, ex, None, limit=limit)
            assert _rows(a, ra) == _rows(b, rb)
            ja, jb = as_json(ra), as_json(rb)
            ja.pop("rows"), jb.pop("rows")
            assert ja == jb
            expected, truncated = _model(records, ex, limit)
            matched = [(loc, [values[g] for g in ex.groups], cut) for loc, values, cut in expected if values]
            converted = [(loc, list(v), cut) for loc, v, cut in matched]
            got = [(loc, values, cut) for loc, values, cut in _rows(a, ra)]
            assert got == converted
            assert ra.truncated == truncated and ra.scanned == min(limit, len(records))
            assert ra.cut == sum(cut for _, _, cut in expected)
            if not ra.truncated:  # the leaves select over every record, the summary covers them all
                for column in ra.columns:
                    for v in column["values"]:
                        if v["leaf"] is not None:
                            assert Q.count(b, v["leaf"]).n == v["count"], v
        finally:
            a.close()
            b.close()
