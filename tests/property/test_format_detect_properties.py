"""Property tests for the generic envelopes and the fallback detection (ROADMAP 9.14).

Regression guarantee: a head that the built-in envelopes recognize (``fallbacks=False`` is not
``plain``) is detected exactly as before; the generic envelopes only ever take a would-be ``plain``.
"""

from datetime import timedelta

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from dmslog.parse.envelope import FALLBACK_ENVELOPES, PLAIN, detect_envelope, get_envelope  # noqa: E402
from dmslog.parse.formats import ensure_registered, packaged_envelopes  # noqa: E402
from dmslog.parse.records import RecordGrouper, parse_bytes  # noqa: E402

MAIN = settings(
    max_examples=400,
    deadline=timedelta(seconds=2),
    database=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

ensure_registered()
GENERIC = [get_envelope(env.name) for env in packaged_envelopes()]

date = st.tuples(
    st.integers(1990, 2040), st.integers(1, 13), st.integers(1, 31), st.integers(0, 23), st.integers(0, 59)
)
text = st.text(
    alphabet=st.characters(blacklist_categories=["Cs"], blacklist_characters="\r\n"), max_size=30
).map(lambda s: s.encode("utf-8"))
level = st.sampled_from([b"DEBUG", b"INFO ", b"WARN ", b"ERROR", b"FATAL", b"Odd"])


def _dmy(d, lv, t):
    y, mo, day, h, mi = d
    return b"%02d.%02d.%04d %02d:%02d:07.123 [TID=%d] [%s] - %s" % (day, mo, y, h, mi, mi, lv, t)


def _ymd_level(d, lv, t):
    y, mo, day, h, mi = d
    return b"%04d.%02d.%02d %02d:%02d:07.001    %s: %s" % (y, mo, day, h, mi, lv.strip(), t)


def _ymd_colon(d, lv, t):
    y, mo, day, h, mi = d
    return b"%04d-%02d-%02d %02d:%02d:07.999: %s" % (y, mo, day, h, mi, t)


def _adms(d, lv, t):
    y, mo, day, h, mi = d
    return b"%04d-%02d-%02d %02d:%02d:07.000 [PID=1][TID=0x1] INFO - %s" % (y, mo, day, h, mi, t)


def _oaserr(d, lv, t):
    y, mo, day, h, mi = d
    return b"%04d-%02d-%02d %02d:%02d:07.000 P_7 [PID=7] - %s" % (y, mo, day, h, mi, t)


generic_line = st.builds(
    lambda f, d, lv, t: f(d, lv, t), st.sampled_from([_dmy, _ymd_level, _ymd_colon]), date, level, text
)
builtin_line = st.builds(
    lambda f, d, lv, t: f(d, lv, t), st.sampled_from([_adms, _oaserr]), date, level, text
)
noise_line = st.one_of(text, text.map(lambda t: b"   at " + t), st.just(b""), st.binary(max_size=40))
any_line = st.one_of(generic_line, builtin_line, noise_line)
eol = st.sampled_from([b"\n", b"\r\n"])
head = st.lists(st.tuples(any_line, eol), max_size=70).map(lambda ls: b"".join(a + b for a, b in ls))


@MAIN
@given(head)
def test_builtin_detection_never_changes(data):
    before = detect_envelope(data, fallbacks=False)
    after = detect_envelope(data)
    if before is not PLAIN:
        assert after is before
    else:
        assert after is PLAIN or after.name in FALLBACK_ENVELOPES


@MAIN
@given(st.binary(max_size=4096))
def test_detection_never_raises_on_arbitrary_bytes(data):
    env = detect_envelope(data)
    assert env is detect_envelope(data)  # deterministic


@MAIN
@given(st.lists(st.tuples(st.one_of(generic_line, noise_line), eol), max_size=40), st.data())
def test_generic_chunked_parse_equals_whole_parse_and_reconstructs(lines, data):
    raw = b"".join(a + b for a, b in lines)
    env = data.draw(st.sampled_from(GENERIC))
    whole = parse_bytes(raw, env)
    assert b"".join(raw[r.byte_off : r.byte_off + r.nbytes] for r in whole) == raw
    cuts = sorted(data.draw(st.lists(st.integers(0, len(raw)), max_size=6)))
    grouper = RecordGrouper(env)
    out, prev = [], 0
    for cut in [*cuts, len(raw)]:
        out += grouper.feed(raw[prev:cut])
        prev = cut
    out += grouper.finish(final=True)
    assert out == whole


@MAIN
@given(generic_line, text)
def test_generic_header_decision_depends_only_on_prefix(line, suffix):
    for env in GENERIC:
        assert env.match(line + suffix) == env.match(line)
