"""Property tests for envelopes and record grouping (ARQUITETURA 6.2 and 13)."""

from datetime import timedelta

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from dmslog.parse.envelope import ADMS, BOM, LEVELS, OASERR, PLAIN  # noqa: E402
from dmslog.parse.records import Record, RecordGrouper, RecordTooLargeError, parse_bytes  # noqa: E402

MAIN = settings(
    max_examples=500,
    deadline=timedelta(seconds=2),
    database=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
LIGHT = settings(MAIN, max_examples=200)

ENVS = [ADMS, OASERR, PLAIN]

# --- generators ---------------------------------------------------------------------

digits = st.integers(min_value=0, max_value=9).map(lambda d: str(d).encode())
ts = st.builds(
    lambda y, mo, d, h, mi, s, ms: b"%04d-%02d-%02d %02d:%02d:%02d.%03d" % (y, mo, d, h, mi, s, ms),
    st.integers(1990, 2040),
    st.integers(1, 12),
    st.integers(1, 28),
    st.integers(0, 23),
    st.integers(0, 59),
    st.integers(0, 59),
    st.integers(0, 999),
)
pid = st.integers(0, 99999).map(lambda n: b"%d" % n)
tid = st.one_of(st.just(b""), st.integers(1, 0xFFFFF).map(lambda n: b"[TID=0x%x]" % n))
level = st.sampled_from(sorted(LEVELS))
text_chars = st.text(
    alphabet=st.characters(blacklist_categories=["Cs"], blacklist_characters="\r\n"), max_size=30
).map(lambda s: s.encode("utf-8"))
msg = st.one_of(text_chars, text_chars.map(lambda t: b"\t\t" + t), text_chars.map(lambda t: t + b" "))

adms_header = st.builds(
    lambda t, p, i, lv, m: t + b" [PID=" + p + b"]" + i + b" " + lv + b" - " + m, ts, pid, tid, level, msg
)
oaserr_header = st.builds(
    lambda t, proc, p, i, lv, m: t + b" " + proc + b"_" + p + b" [PID=" + p + b"]" + i + b" - " + lv + m,
    ts,
    st.sampled_from([b"SampleAdapter", b"X.Y", b"P"]),
    pid,
    tid,
    st.one_of(st.just(b""), level.map(lambda lv: b"(" + lv + b") ")),
    msg,
)
continuation = st.one_of(
    st.just(b""),
    text_chars.map(lambda t: b"\t" + t),
    text_chars.map(lambda t: b"   at " + t),
    ts.map(lambda t: t + b" not a header"),
    st.just(b"2026-02-30 03:04:05.000 [PID=1][TID=0x1] INFO - impossible date"),
    text_chars,
    st.binary(max_size=20).map(lambda b: b.replace(b"\n", b"")),
)
line = st.one_of(adms_header, oaserr_header, continuation)
eol = st.sampled_from([b"\r\n", b"\r\n", b"\n", b"\r"])


@st.composite
def log_bytes(draw):
    """Log-like bytes: header and continuation lines, mixed EOLs, optional BOM and final EOL."""
    lines = draw(st.lists(st.tuples(line, eol), max_size=40))
    data = b"".join(text + end for text, end in lines)
    if draw(st.booleans()):
        data = BOM + data
    if data and draw(st.booleans()):
        data = data.rstrip(b"\r\n")
    return data


any_input = st.one_of(log_bytes(), st.binary(max_size=400))


@st.composite
def cuts(draw, data):
    return sorted(draw(st.lists(st.integers(0, len(data)), max_size=12)))


# --- reference implementation (line by line, no buffering) ---------------------------


def split_lines(data):
    out, pos = [], 0
    while pos < len(data):
        nl = data.find(b"\n", pos)
        end = len(data) if nl < 0 else nl + 1
        out.append(data[pos:end])
        pos = end
    return out


def content(raw):
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    if raw.endswith(b"\r"):
        raw = raw[:-1]
    return raw


def reference(data, env, *, final=True, start_off=0, start_line=1):
    groups = []  # [header or None, [lines]]
    for raw in split_lines(data):
        h = env.match(raw)
        if h is not None or not groups:
            groups.append((h, [raw]))
        else:
            groups[-1][1].append(raw)
    records, off, line_no = [], start_off, start_line
    for i, (h, raws) in enumerate(groups):
        first = content(raws[0])
        if h is None:
            first_msg = first[3:] if first.startswith(BOM) else first
        else:
            first_msg = first[min(h.msg_start, len(first)) :]
        cont = None if len(raws) == 1 else "\n".join(content(r).decode("utf-8", "replace") for r in raws[1:])
        last = i == len(groups) - 1
        partial = last and (not final or not raws[-1].endswith(b"\n"))
        nbytes = sum(map(len, raws))
        if h is None:
            fields = (None, None, None, None)
        else:
            fields = (h.ts_ms, h.pid, h.tid, h.level)
        records.append(
            Record(
                off,
                line_no,
                len(raws),
                *fields,
                first_msg.decode("utf-8", "replace"),
                cont,
                partial,
                nbytes,
                None if h is None else h.process,
                h is None,
            )
        )
        off += nbytes
        line_no += len(raws)
    return records


def feed_in_pieces(data, env, points, *, final=True, start_off=0, start_line=1):
    grouper = RecordGrouper(env, start_off=start_off, start_line=start_line)
    out, prev = [], 0
    for point in [*points, len(data)]:
        out += grouper.feed(data[prev:point])
        prev = point
    return out + grouper.finish(final=final)


# --- properties --------------------------------------------------------------------


@MAIN
@given(st.data(), log_bytes(), st.sampled_from(ENVS), st.booleans())
def test_chunked_parse_equals_whole_parse(data, raw, env, final):
    points = data.draw(cuts(raw))
    assert feed_in_pieces(raw, env, points, final=final) == parse_bytes(raw, env, final=final)


@MAIN
@given(st.data(), st.binary(max_size=300), st.sampled_from(ENVS))
def test_chunked_parse_equals_whole_parse_on_arbitrary_bytes(data, raw, env):
    points = data.draw(cuts(raw))
    assert feed_in_pieces(raw, env, points) == parse_bytes(raw, env)


@MAIN
@given(any_input, st.sampled_from(ENVS), st.booleans())
def test_matches_line_by_line_reference(raw, env, final):
    assert parse_bytes(raw, env, final=final) == reference(raw, env, final=final)


@MAIN
@given(any_input, st.sampled_from(ENVS), st.integers(0, 10**9), st.integers(1, 10**6))
def test_records_reconstruct_input_byte_for_byte(raw, env, start_off, start_line):
    records = parse_bytes(raw, env, start_off=start_off, start_line=start_line)
    pieces = [raw[r.byte_off - start_off : r.byte_off - start_off + r.nbytes] for r in records]
    assert b"".join(pieces) == raw
    assert sum(r.nlines for r in records) == len(split_lines(raw))


@MAIN
@given(any_input, st.sampled_from(ENVS), st.booleans())
def test_offsets_and_line_numbers_are_contiguous_and_monotonic(raw, env, final):
    records = parse_bytes(raw, env, final=final, start_off=100, start_line=5)
    off, line_no = 100, 5
    for i, r in enumerate(records):
        assert r.byte_off == off and r.line_no == line_no
        assert r.nbytes >= 1 and r.nlines >= 1
        assert r.nlines == raw.count(b"\n", off - 100, off - 100 + r.nbytes) + (
            not raw[: off - 100 + r.nbytes].endswith(b"\n")
        )
        assert (r.cont is None) == (r.nlines == 1)
        assert "\n" not in r.msg
        assert r.partial == (i == len(records) - 1 and (not final or not raw.endswith(b"\n")))
        assert not r.orphan or i == 0
        off += r.nbytes
        line_no += r.nlines
    assert off == 100 + len(raw)


@MAIN
@given(st.data(), log_bytes(), st.sampled_from(ENVS))
def test_resume_from_last_record_offset_equals_whole_parse(data, raw, env):
    cut = data.draw(st.integers(0, len(raw)))
    first = parse_bytes(raw[:cut], env, final=False)
    if first:
        resume_off, resume_line, kept = first[-1].byte_off, first[-1].line_no, first[:-1]
    else:
        resume_off, resume_line, kept = 0, 1, []
    rest = parse_bytes(raw[resume_off:], env, start_off=resume_off, start_line=resume_line)
    assert kept + rest == parse_bytes(raw, env)


@MAIN
@given(st.data(), st.binary(max_size=500), st.sampled_from(ENVS), st.booleans())
def test_parser_never_raises_on_arbitrary_bytes(data, raw, env, final):
    points = data.draw(cuts(raw))
    feed_in_pieces(raw, env, points, final=final)


@LIGHT
@given(adms_header, eol)
def test_generated_adms_headers_match(text, end):
    h = ADMS.match(text + end)
    assert h is not None and h.ts_ms is not None and h.pid is not None
    assert OASERR.match(text + end) is None


@LIGHT
@given(oaserr_header, eol)
def test_generated_oaserr_headers_match(text, end):
    h = OASERR.match(text + end)
    assert h is not None and h.process
    assert ADMS.match(text + end) is None


@LIGHT
@given(continuation, eol)
def test_generated_continuations_never_match(text, end):
    assert ADMS.match(text + end) is None
    assert OASERR.match(text + end) is None


@LIGHT
@given(adms_header, st.binary(max_size=20))
def test_header_decision_depends_only_on_prefix(text, suffix):
    assert ADMS.match(text + suffix) == ADMS.match(text)


@MAIN
@given(st.data(), log_bytes(), st.sampled_from(ENVS), st.booleans())
def test_record_limit_is_independent_of_chunking(data, raw, env, final):
    points = data.draw(cuts(raw))
    whole = parse_bytes(raw, env, final=final)
    largest = max((r.nbytes for r in whole), default=0)
    longest_line = max((len(line) for line in raw.split(b"\n")), default=0) + 1

    def run(limit):
        grouper = RecordGrouper(env, max_record_bytes=limit)
        out, prev = [], 0
        for point in [*points, len(raw)]:
            out += grouper.feed(raw[prev:point])
            prev = point
        return out + grouper.finish(final=final)

    # Room for the record plus an unterminated line: nothing changes.
    assert run(max(largest + longest_line, 1)) == whole
    # A limit below the largest record always fails, whatever the chunking.
    if largest > 1:
        with pytest.raises(RecordTooLargeError):
            run(largest - 1)
