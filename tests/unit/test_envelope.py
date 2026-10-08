from datetime import UTC, datetime

import pytest

from dmslog.parse.envelope import (
    ADMS,
    BOM,
    ENVELOPES,
    LEVELS,
    OASERR,
    PLAIN,
    Header,
    detect_envelope,
    get_envelope,
    parse_ts,
    register_envelope,
)


def _ms(*parts):
    return int(datetime(*parts, tzinfo=UTC).timestamp() * 1000)


def test_adms_header_with_tid():
    line = b"2026-01-02 03:04:05.678 [PID=1111][TID=0x1a8] DEBUGLOG - Hello world\r\n"
    h = ADMS.match(line)
    assert h == Header(_ms(2026, 1, 2, 3, 4, 5) + 678, 1111, 0x1A8, LEVELS[b"DEBUGLOG"], len(line) - 13)
    assert line[h.msg_start :] == b"Hello world\r\n"


def test_adms_header_without_tid():
    h = ADMS.match(b"2026-01-02 03:04:05.000 [PID=22] INFO - PSAttach::Callback: x")
    assert h is not None
    assert (h.pid, h.tid, h.level) == (22, None, LEVELS[b"INFO"])


def test_adms_bom_is_optional_and_skipped():
    line = b"2026-01-02 03:04:05.000 [PID=22][TID=0xA] ERROR - m"
    plain_header = ADMS.match(line)
    bom_header = ADMS.match(BOM + line)
    assert bom_header.msg_start == plain_header.msg_start + 3
    assert bom_header.tid == 10


@pytest.mark.parametrize("name,value", sorted(LEVELS.items(), key=lambda item: item[1]))
def test_adms_every_level(name, value):
    h = ADMS.match(b"2026-01-02 03:04:05.000 [PID=1][TID=0x1] " + name + b" - x")
    assert h.level == value


def test_level_scale_is_fatal_to_verbose():
    assert LEVELS[b"FATAL"] == 1 and LEVELS[b"VERBOSE"] == 8
    assert sorted(LEVELS.values()) == list(range(1, 9))


def test_tab_after_separator_is_message_text():
    line = b"2026-01-02 03:04:05.000 [PID=1][TID=0x1] DEBUGLOG - \t\t[Validation Info]: X\r\n"
    h = ADMS.match(line)
    assert h is not None
    assert line[h.msg_start : h.msg_start + 2] == b"\t\t"


def test_message_may_end_with_space():
    line = b"2026-01-02 03:04:05.000 [PID=1][TID=0x1] DEBUGLOG - Reason: "
    h = ADMS.match(line)
    assert line[h.msg_start :] == b"Reason: "


@pytest.mark.parametrize(
    "line",
    [
        b"",
        b"\r\n",
        b"\tManaged Code Stack Trace:\r\n",
        b"   at Some.Method() in F:\\build\\File.cs:line 12\r\n",
        b"2026-01-02 garbage\r\n",
        b"2026-01-02 03:04:05.000 [PID=1][TID=0x1] debuglog - lowercase level",
        b"2026-01-02 03:04:05.000 [PID=1][TID=0x1] TRACE - unknown level",
        b"2026-01-02 03:04:05.000 [PID=1][TID=0x1] INFO -missing space",
        b"2026-01-02 03:04:05.00 [PID=1][TID=0x1] INFO - two digit ms",
        b"2026-02-30 03:04:05.000 [PID=1][TID=0x1] INFO - impossible date",
        b"2026-01-02 25:04:05.000 [PID=1][TID=0x1] INFO - impossible hour",
        b"2026-01-02 03:04:05.000 [PID=12345678901][TID=0x1] INFO - pid too long",
        b"2026-01-02 03:04:05.000 [PID=1][TID=1a] INFO - tid without 0x",
        b" 2026-01-02 03:04:05.000 [PID=1][TID=0x1] INFO - leading space",
        b"\t2026-01-02 03:04:05.000 [PID=1][TID=0x1] INFO - leading tab",
        b"2026-01-02 03:04:05.000 Proc_1 [PID=1][TID=0x1] - (ERROR) oaserr line",
    ],
)
def test_adms_continuations_do_not_match(line):
    assert ADMS.match(line) is None


def test_ts_cache_and_millis():
    assert parse_ts(b"1970-01-01 00:00:00", b"001") == 1
    assert parse_ts(b"2026-03-01 00:00:00", b"999") == _ms(2026, 3, 1) + 999
    assert parse_ts(b"0000-01-01 00:00:00", b"000") is None
    # cached values stay correct
    assert parse_ts(b"2026-03-01 00:00:00", b"000") == _ms(2026, 3, 1)


def test_oaserr_header_with_level():
    line = b"2026-01-02 03:04:05.006 SampleAdapter_1111 [PID=1111][TID=0x2b] - (ERROR) [Ctx] failed\r\n"
    h = OASERR.match(line)
    assert (h.ts_ms, h.pid, h.tid, h.level, h.process) == (
        _ms(2026, 1, 2, 3, 4, 5) + 6,
        1111,
        0x2B,
        LEVELS[b"ERROR"],
        "SampleAdapter",
    )
    assert line[h.msg_start :] == b"[Ctx] failed\r\n"


def test_oaserr_header_without_level_and_other_process_tokens():
    h = OASERR.match(b"2026-01-02 03:04:05.006 Some.Proc.2222 [PID=2222][TID=0x9] - Free text")
    assert (h.level, h.process) == (None, "Some.Proc.2222")
    h = OASERR.match(b"2026-01-02 03:04:05.006 X_NoPid [PID=3][TID=0x9] - Path: C:\\x")
    assert (h.level, h.process, h.pid) == (None, "X_NoPid", 3)
    h = OASERR.match(BOM + b"2026-01-02 03:04:05.006 P_7 [PID=7] - (FATAL) m")
    assert (h.level, h.tid, h.process) == (LEVELS[b"FATAL"], None, "P")


def test_oaserr_does_not_match_adms_lines():
    assert OASERR.match(b"2026-01-02 03:04:05.000 [PID=1][TID=0x1] INFO - m") is None


def test_plain_every_line_is_a_header():
    assert PLAIN.match(b"anything") == Header(None, None, None, None, 0)
    assert PLAIN.match(BOM + b"x").msg_start == 3
    assert PLAIN.match(b"") is not None


@pytest.mark.parametrize("env", [ADMS, OASERR, PLAIN])
def test_scan_agrees_with_match(env):
    lines = [
        b"2026-01-02 03:04:05.000 [PID=1][TID=0x1] INFO - a\r\n",
        b"\tcont\r\n",
        b"\r\n",
        b"2026-01-02 03:04:05.006 P_7 [PID=7][TID=0x9] - (ERROR) b\n",
        b"2026-02-30 03:04:05.000 [PID=1][TID=0x1] INFO - bad date\n",
        BOM + b"2026-01-02 03:04:05.000 [PID=1] VERBOSE - c\n",
        b"tail without newline",
    ]
    buf = bytearray(b"".join(lines))
    expected = []
    pos = 0
    for line in lines:
        h = env.match(line)
        if h is not None:
            expected.append((pos, h))
        pos += len(line)
    assert list(env.scan(buf, 0, len(buf))) == expected


def test_detect_envelope_adms_oaserr_plain():
    adms = b"2026-01-02 03:04:05.000 [PID=1][TID=0x1] INFO - a\r\n\tx\r\n" * 3
    oaserr = b"2026-01-02 03:04:05.006 P_7 [PID=7][TID=0x9] - (ERROR) b\r\n   at y\r\n" * 3
    assert detect_envelope(BOM + adms) is ADMS
    assert detect_envelope(oaserr) is OASERR
    assert detect_envelope(b"<xml/>\nkey=value\n") is PLAIN
    assert detect_envelope(b"") is PLAIN


def test_detect_envelope_majority_and_line_limit():
    one_adms = b"2026-01-02 03:04:05.000 [PID=1][TID=0x1] INFO - a\n"
    two_oaserr = b"2026-01-02 03:04:05.006 P_7 [PID=7][TID=0x9] - b\n" * 2
    assert detect_envelope(one_adms + two_oaserr) is OASERR
    # a header beyond the line limit is not seen
    assert detect_envelope(b"dump\n" * 60 + one_adms) is PLAIN
    assert detect_envelope(b"dump\n" * 40 + one_adms) is ADMS


def test_register_custom_envelope():
    class Hash:
        name = "test-hash"

        def match(self, line):
            return Header(None, None, None, None, 1) if line.startswith(b"#") else None

    try:
        register_envelope(Hash())
        assert get_envelope("test-hash").name == "test-hash"
        assert detect_envelope(b"#a\n#b\n").name == "test-hash"
    finally:
        ENVELOPES.pop("test-hash", None)
    assert set(ENVELOPES) == {"adms", "oaserr", "plain"}
