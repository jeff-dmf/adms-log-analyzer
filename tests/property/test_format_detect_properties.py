"""Property tests for the generic envelopes and the fallback detection (ROADMAP 9.14).

Regression guarantee: a head that the built-in envelopes recognize (``fallbacks=False`` is not
``plain``) is detected exactly as before; the generic envelopes only ever take a would-be ``plain``.
"""

from datetime import timedelta

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from dmslog.parse.envelope import (  # noqa: E402
    ADMS,
    FALLBACK_ENVELOPES,
    OASERR,
    PLAIN,
    detect_envelope,
    get_envelope,
)
from dmslog.parse.formats import compile_envelopes, ensure_registered, packaged_envelopes  # noqa: E402
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


def detect_before_9_14(head: bytes, *, max_lines: int = 50):
    """Frozen copy of ``detect_envelope`` as it was before 9.14 (main at b9ddd67): an oracle that does not
    share code with the implementation under test. Registry order then: adms, oaserr (plain excluded)."""
    lines = head.split(b"\n", max_lines)[:max_lines]
    best = PLAIN
    best_count = 0
    for envelope in (ADMS, OASERR):
        count = sum(1 for line in lines if envelope.match(line) is not None)
        if count > best_count:
            best, best_count = envelope, count
    return best


@MAIN
@given(head)
def test_builtin_detection_never_changes(data):
    before = detect_before_9_14(data)
    after = detect_envelope(data)
    assert detect_envelope(data, fallbacks=False) is before
    if before is not PLAIN:
        assert after is before
    else:
        assert after is PLAIN or after.name in FALLBACK_ENVELOPES


@MAIN
@given(st.integers(1, 20), st.lists(st.tuples(generic_line, eol), max_size=30), st.randoms())
def test_ties_between_builtins_go_to_adms_as_before(n, extra, rnd):
    """Equal counts of adms and oaserr headers (plus any generic lines): adms, registered first, wins."""
    lines = [_adms((2026, 1, 2, 3, 4), b"", b"a")] * n + [_oaserr((2026, 1, 2, 3, 4), b"", b"b")] * n
    lines += [a for a, _ in extra]
    rnd.shuffle(lines)
    data = b"\n".join(lines[:50]) + b"\n"
    expected = detect_before_9_14(data)
    assert detect_envelope(data) is expected
    if len(lines) <= 50:
        assert expected is ADMS


@pytest.mark.parametrize(
    ("adms", "oaserr", "expected"),
    [(1, 1, "adms"), (2, 2, "adms"), (1, 2, "oaserr"), (3, 2, "adms"), (0, 1, "oaserr"), (0, 0, "plain")],
)
def test_explicit_builtin_ties_and_majorities(adms, oaserr, expected):
    data = b"".join(
        [_adms((2026, 1, 2, 3, 4), b"", b"a") + b"\n"] * adms
        + [_oaserr((2026, 1, 2, 3, 4), b"", b"b") + b"\n"] * oaserr
        + [_dmy((2026, 1, 2, 3, 4), b"INFO ", b"g") + b"\n"] * 3
    )
    assert detect_before_9_14(data).name == detect_envelope(data, fallbacks=False).name
    got = detect_envelope(data).name
    assert got == (expected if expected != "plain" else "dmy_tid_level")


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


_TS = r"^(?P<year>\d{4})-(?P<month>\d\d)-(?P<day>\d\d) (?P<hour>\d\d):(?P<minute>\d\d):(?P<second>\d\d)"
# Accepted headers with optional groups (each would be rejected if it were prefix-unstable).
ACCEPTED = compile_envelopes(
    {
        "envelope": [
            {
                "name": "o1",
                "header": _TS + r"(?:\.(?P<ms>\d{1,6}))? (?:\[(?P<tid>\d{1,8})\] )?(?P<level>[A-Z]{1,8}) \| ",
                "examples": ["2026-10-15 01:02:03 INFO | x"],
            },
            {
                "name": "o2",
                "header": _TS + r" (?:(?P<process>[A-Za-z]{1,20})_(?P<pid>\d{1,8}) )?- ",
                "examples": ["2026-10-15 01:02:03 - x", "2026-10-15 01:02:03 Svc_12 - x"],
            },
            {
                "name": "o3",
                "header": _TS + r" (?P<level>INFO|WARN|ERROR) ?: ",
                "examples": ["2026-10-15 01:02:03 INFO: x", "2026-10-15 01:02:03 WARN : x"],
            },
        ]
    }
)
header_alphabet = st.sampled_from(list(b"0123456789 .:_-|[]AINFORMTWSvcx\t")).map(lambda c: bytes([c]))
header_text = st.lists(header_alphabet, max_size=25).map(b"".join)


@MAIN
@given(st.integers(0, 2), header_text, header_text)
def test_accepted_headers_with_optional_groups_are_prefix_stable(which, body, suffix):
    env = ACCEPTED[which]
    line = b"2026-10-15 01:02:03" + body
    assert env.match(line + suffix) == env.match(line) or env.match(line) is None
