"""Property: random tree -> synthetic writer -> ``decode`` returns the same tree; arbitrary or mutated bytes
only ever raise ``NbfxError`` and finish inside the limits (issue #74).

``DMSLOG_NBFX_EXAMPLES`` raises the example count locally (default 50, kept low for CI memory)."""

from __future__ import annotations

import decimal
import os
import sys
from pathlib import Path
from uuid import UUID

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "support"))

import nbfx_writer as w  # noqa: E402

from dmslog.msgqueue.nbfx import Limits, NbfxError, Node, decode, to_xml  # noqa: E402

EXAMPLES = int(os.environ.get("DMSLOG_NBFX_EXAMPLES", "50"))
SETTINGS = settings(max_examples=EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])
SMALL = Limits(
    max_bytes=20_000, max_depth=16, max_nodes=2_000, max_text=5_000, max_total_text=20_000, max_attrs=16
)

NAMES = st.one_of(
    st.sampled_from(["a", "item", "Foo_1", "x.y", "payloadField", "n-1"]),
    st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,6}", fullmatch=True),
)
PREFIXES = st.one_of(st.just(""), st.sampled_from(["a", "i", "z", "ns", "p1"]))
TEXTS = st.text(max_size=12)
TICKS = st.integers(0, 3_155_378_975_999_999_999)


@st.composite
def decimals(draw):
    magnitude = draw(st.integers(0, 2**96 - 1))
    scale = draw(st.integers(0, 28))
    sign = 1 if magnitude and draw(st.booleans()) else 0
    digits = tuple(int(c) for c in str(magnitude))
    return decimal.Decimal((sign, digits, -scale))


VALUE_BY_KIND = {
    "int8": st.integers(-(2**7), 2**7 - 1),
    "int16": st.integers(-(2**15), 2**15 - 1),
    "int32": st.integers(-(2**31), 2**31 - 1),
    "int64": st.integers(-(2**63), 2**63 - 1),
    "uint64": st.integers(0, 2**64 - 1),
    "float": st.floats(width=32),
    "double": st.floats(width=64),
    "decimal": decimals(),
    "datetime": st.tuples(TICKS, st.integers(0, 3)),
    "timespan": st.integers(-(2**63), 2**63 - 1),
    "guid": st.integers(0, 2**128 - 1).map(lambda n: UUID(int=n)),
    "uniqueid": st.integers(0, 2**128 - 1).map(lambda n: UUID(int=n)),
    "bool": st.booleans(),
    "chars": TEXTS,
    "unicode": TEXTS,
    "bytes": st.binary(max_size=20),
}
CONSTANT_KINDS = ["zero", "one", "false", "true", "empty"]


def scalar_vals():
    typed = st.sampled_from(sorted(VALUE_BY_KIND)).flatmap(
        lambda kind: st.builds(
            w.Val,
            st.just(kind),
            VALUE_BY_KIND[kind],
            st.sampled_from([None, 8, 16, 32]) if kind in ("chars", "unicode", "bytes") else st.none(),
        )
    )
    return st.one_of(typed, st.builds(w.Val, st.sampled_from(CONSTANT_KINDS)))


VALS = st.one_of(scalar_vals(), st.lists(scalar_vals(), max_size=4).map(lambda items: w.Val("list", items)))
ARRAY_KINDS = [
    "int16",
    "int32",
    "int64",
    "float",
    "double",
    "decimal",
    "datetime",
    "timespan",
    "guid",
    "bool",
]


@st.composite
def arrays(draw):
    kind = draw(st.sampled_from(ARRAY_KINDS))
    return w.ArrayOf(
        draw(NAMES),
        kind,
        tuple(draw(st.lists(VALUE_BY_KIND[kind], max_size=4))),
        prefix=draw(PREFIXES),
        xmlns=tuple(draw(st.lists(st.tuples(PREFIXES, TEXTS), max_size=2))),
        attrs=tuple(draw(st.lists(st.tuples(PREFIXES, NAMES, VALS), max_size=2))),
    )


LEAVES = st.one_of(VALS, st.builds(w.Comment, TEXTS), arrays())


def element(children):
    return st.builds(
        w.E,
        NAMES,
        PREFIXES,
        st.lists(st.tuples(PREFIXES, TEXTS), max_size=3).map(tuple),
        st.lists(st.tuples(PREFIXES, NAMES, VALS), max_size=3).map(tuple),
        st.lists(children, max_size=4).map(tuple),
        st.booleans(),
        st.booleans(),
    )


TREES = st.recursive(element(LEAVES), lambda inner: element(st.one_of(LEAVES, inner)), max_leaves=12)


@SETTINGS
@given(TREES)
def test_round_trip(tree):
    blob = w.encode(tree)
    assert decode(blob) == w.expected_tree(tree)
    to_xml(decode(blob))  # serializing never fails for a decoded tree


def _blob_or_error(blob: bytes) -> Node | None:
    try:
        return decode(blob, SMALL)
    except NbfxError as err:
        assert err.code and isinstance(err.offset, int) and 0 <= err.offset <= len(blob)
        return None


@SETTINGS
@given(st.binary(max_size=200))
def test_random_bytes_after_a_valid_header_only_raise_nbfx_error(tail):
    _blob_or_error(w.HEADER + tail)
    _blob_or_error(w.HEADER + b"\x40\x01a" + tail)  # a valid root start, then noise


@SETTINGS
@given(st.binary(max_size=200))
def test_random_bytes_only_raise_nbfx_error(blob):
    _blob_or_error(blob)


@SETTINGS
@given(TREES, st.integers(0, 10_000), st.integers(0, 255), st.booleans())
def test_mutated_documents_only_raise_nbfx_error(tree, position, byte, truncate):
    blob = bytearray(w.encode(tree))
    index = position % len(blob)
    if truncate:
        del blob[index:]
    else:
        blob[index] = byte
    _blob_or_error(bytes(blob))
