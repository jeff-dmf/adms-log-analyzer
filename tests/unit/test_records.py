import io

import pytest

from dmslog.parse.envelope import ADMS, BOM, OASERR, PLAIN, Header
from dmslog.parse.records import RecordGrouper, iter_records, parse_bytes


def hdr(msg=b"m", *, sec=b"05", ms=b"000", pid=b"1111", tid=b"[TID=0x1a]", level=b"DEBUGLOG", eol=b"\r\n"):
    return (
        b"2026-01-02 03:04:"
        + sec
        + b"."
        + ms
        + b" [PID="
        + pid
        + b"]"
        + tid
        + b" "
        + level
        + b" - "
        + msg
        + eol
    )


def chunked(data, env, size, *, final=True, start_off=0, start_line=1):
    grouper = RecordGrouper(env, start_off=start_off, start_line=start_line)
    out = []
    for i in range(0, len(data), size):
        out += grouper.feed(data[i : i + size])
    return out + grouper.finish(final=final)


def assert_covers(records, data, start_off=0, start_line=1):
    off, line = start_off, start_line
    for r in records:
        assert (r.byte_off, r.line_no) == (off, line)
        assert r.nbytes > 0 and r.nlines >= 1
        off += r.nbytes
        line += r.nlines
    assert off == start_off + len(data)


def test_empty_input_has_no_records():
    assert parse_bytes(b"", ADMS) == []
    assert parse_bytes(b"", ADMS, final=False) == []


def test_crlf_records_with_continuations_and_blank_lines():
    data = BOM + hdr(b"Detected errors:") + b"CodeA\r\n\r\n" + hdr(b"next", sec=b"06")
    a, b = parse_bytes(data, ADMS)
    assert (a.msg, a.cont, a.nlines, a.partial, a.orphan) == ("Detected errors:", "CodeA\n", 3, False, False)
    assert a.byte_off == 0 and a.nbytes == len(data) - len(hdr(b"next"))
    assert (b.msg, b.cont, b.nlines, b.line_no) == ("next", None, 1, 4)
    assert_covers([a, b], data)


def test_lf_only_and_mixed_eol():
    data = hdr(b"a", eol=b"\n") + b"x\n" + b"y\r\n" + hdr(b"b", eol=b"\n")
    a, b = parse_bytes(data, ADMS)
    assert (a.msg, a.cont, b.msg) == ("a", "x\ny", "b")


def test_continuation_without_cr_keeps_inner_cr():
    data = hdr(b"a") + b"x\ry\r\n" + b"z\r\r\n"
    (a,) = parse_bytes(data, ADMS)
    assert a.cont == "x\ry\nz\r"


def test_message_spaces_and_leading_tab_preserved():
    data = hdr(b"Reason: ") + hdr(b"\t\t[Validation Info]: X") + hdr(b"  indented")
    records = parse_bytes(data, ADMS)
    assert [r.msg for r in records] == ["Reason: ", "\t\t[Validation Info]: X", "  indented"]
    assert all(r.cont is None for r in records)


def test_header_without_tid_is_not_a_continuation():
    data = hdr(b"a") + hdr(b"PSAttach::PubSubReceiveCallBack: x", tid=b"", level=b"INFO")
    a, b = parse_bytes(data, ADMS)
    assert a.cont is None
    assert (b.tid, b.pid, b.msg) == (None, 1111, "PSAttach::PubSubReceiveCallBack: x")


def test_orphan_lines_before_first_header():
    data = BOM + b"leftover\r\n\tmore\r\n" + hdr(b"a")
    orphan, a = parse_bytes(data, ADMS)
    assert orphan.orphan and (orphan.ts_ms, orphan.pid, orphan.level) == (None, None, None)
    assert (orphan.msg, orphan.cont, orphan.nlines) == ("leftover", "\tmore", 2)
    assert not a.orphan and a.line_no == 3


def test_file_without_any_header_is_one_orphan_record():
    data = b"a\nb\nc"
    (r,) = parse_bytes(data, ADMS)
    assert (r.orphan, r.msg, r.cont, r.nlines, r.partial) == (True, "a", "b\nc", 3, True)


def test_no_final_newline_marks_last_record_partial():
    data = hdr(b"a") + hdr(b"b", eol=b"")
    a, b = parse_bytes(data, ADMS)
    assert not a.partial and b.partial and b.msg == "b"
    a, b = parse_bytes(data + b"\r", ADMS)
    assert b.partial and b.msg == "b" and b.nbytes == len(hdr(b"b", eol=b"\r"))


def test_not_final_keeps_last_record_partial():
    data = hdr(b"a") + hdr(b"b")
    a, b = parse_bytes(data, ADMS, final=False)
    assert not a.partial and b.partial
    assert not parse_bytes(data, ADMS, final=True)[-1].partial


def test_timestamp_regression_keeps_file_order():
    data = hdr(b"a", ms=b"010") + hdr(b"b", ms=b"007") + hdr(b"c", sec=b"04")
    records = parse_bytes(data, ADMS)
    assert [r.msg for r in records] == ["a", "b", "c"]
    assert records[0].ts_ms - records[1].ts_ms == 3
    assert records[1].ts_ms - records[2].ts_ms == 1007


def test_invalid_utf8_is_replaced_but_offsets_are_bytes():
    data = hdr(b"caf\xc3\xa9 \xff") + b"\xe2\x82\r\n" + hdr(b"z")
    a, b = parse_bytes(data, ADMS)
    assert a.msg == "caf\u00e9 \ufffd"
    assert a.cont == "\ufffd"
    assert b.byte_off == len(data) - len(hdr(b"z"))


def test_bom_only_matters_on_its_own_line():
    data = BOM + hdr(b"a") + BOM + b"inside\r\n"
    (a,) = parse_bytes(data, ADMS)
    assert a.msg == "a" and a.cont == "\ufeffinside"


def test_large_dump_in_small_chunks():
    dump = b"".join(b'\t"Key %d" - VALUE %d\r\n' % (i, i) for i in range(1700))
    data = hdr(b"New map content:") + dump + b"\r\n" + hdr(b"after")
    whole = parse_bytes(data, ADMS)
    assert len(whole) == 2 and whole[0].nlines == 1702
    assert whole[0].cont.count("\n") == 1700
    for size in (1, 7, 4096):
        assert chunked(data, ADMS, size) == whole


def test_chunk_split_between_cr_and_lf():
    data = hdr(b"a") + b"x\r\n" + hdr(b"b")
    cut = data.index(b"x\r\n") + 2
    grouper = RecordGrouper(ADMS)
    out = grouper.feed(data[:cut]) + grouper.feed(data[cut:]) + grouper.finish(final=True)
    assert out == parse_bytes(data, ADMS)


def test_iter_records_from_stream_with_offset():
    head = hdr(b"a")
    data = hdr(b"b") + b"cont\n" + hdr(b"c")
    records = list(
        iter_records(io.BytesIO(data), ADMS, start_off=len(head), start_line=7, final=True, chunk_size=5)
    )
    assert [(r.msg, r.byte_off, r.line_no) for r in records] == [
        ("b", len(head), 7),
        ("c", len(head) + len(hdr(b"b")) + 5, 9),
    ]
    assert_covers(records, data, len(head), 7)


def test_resume_from_partial_record_offset():
    data = hdr(b"a") + hdr(b"b") + b"cont1\r\n"
    grown = data + b"cont2\r\n" + hdr(b"c")
    first = parse_bytes(data, ADMS, final=False)
    last = first[-1]
    assert last.partial and last.cont == "cont1"
    second = parse_bytes(grown[last.byte_off :], ADMS, start_off=last.byte_off, start_line=last.line_no)
    assert first[:-1] + second == parse_bytes(grown, ADMS)
    assert second[0].cont == "cont1\ncont2"


def test_oaserr_records_carry_process():
    data = (
        b"2026-01-02 03:04:05.006 SampleAdapter_1111 [PID=1111][TID=0x2b] - (ERROR) boom\r\n"
        b"   at X.Y()\r\n"
        b"2026-01-02 03:04:05.007 Other.Proc.2 [PID=2][TID=0x2c] - text\r\n"
    )
    a, b = parse_bytes(data, OASERR)
    assert (a.process, a.level, a.cont, b.process, b.level) == (
        "SampleAdapter",
        3,
        "   at X.Y()",
        "Other.Proc.2",
        None,
    )


def test_plain_one_record_per_line():
    data = BOM + b"one\r\n\r\ntwo"
    records = parse_bytes(data, PLAIN)
    assert [(r.msg, r.cont, r.line_no, r.ts_ms, r.orphan) for r in records] == [
        ("one", None, 1, None, False),
        ("", None, 2, None, False),
        ("two", None, 3, None, False),
    ]
    assert records[-1].partial


def test_generic_envelope_without_scan_matches_fast_path():
    class LineOnly:
        name = "adms-by-line"

        def match(self, line):
            return ADMS.match(line)

    data = (
        b"orphan\n" + hdr(b"a") + b"\tx\r\n" + hdr(b"b", tid=b"") + b"2026-02-30 bad\r\n" + hdr(b"c", eol=b"")
    )
    assert parse_bytes(data, LineOnly()) == parse_bytes(data, ADMS)
    assert chunked(data, LineOnly(), 3) == parse_bytes(data, ADMS)


def test_header_msg_start_beyond_line_is_clamped():
    class Wide:
        name = "wide"

        def match(self, line):
            return Header(None, None, None, None, 50)

    (r,) = parse_bytes(b"short\r\n", Wide())
    assert r.msg == ""


def test_feed_after_finish_raises():
    grouper = RecordGrouper(ADMS)
    grouper.finish(final=True)
    with pytest.raises(RuntimeError):
        grouper.feed(b"x")
    with pytest.raises(RuntimeError):
        grouper.finish(final=True)
