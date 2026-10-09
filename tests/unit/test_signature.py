"""Message signatures for records without a template (ARQUITETURA 6.3 and 6.7)."""

# ruff: noqa: E501 - real log lines and template regexes are kept on one line, as in the TOML files

import pytest

from dmslog.parse.signature import MAX_SIGNATURE_CHARS, signature


@pytest.mark.parametrize(
    ("msg", "expected"),
    [
        (
            'Changed crews request received. Request with ID "0b5c1f7e-1111-2222-3333-444455556666". Done.',
            "Changed crews request received. Request with ID <s>. Done.",
        ),
        ("Request 0B5C1F7E-1111-2222-3333-444455556666 sent", "Request <guid> sent"),
        ("Opened {0b5c1f7e-1111-2222-3333-444455556666}", "Opened <guid>"),
        ("IMS callback received for OMS_REQUEST GID: 288230376151711744 - Approved.",
         "IMS callback received for OMS_REQUEST GID: <n> - Approved."),
        ("[Communication2] - Call count: 11462391, Request bytes: 1115498604 (avg. 97.318)",
         "[Communication<n>] - Call count: <n>, Request bytes: <n> (avg. <n>)"),
        ("Removing messages older then 2026-10-07 02:30:11.123 started", "Removing messages older then <ts> started"),
        (r"File: /PROD/OutageReports/a.csv - is copied to location: \\share\dir\a.csv.",
         "File: <path> - is copied to location: <path>"),
        (r"at X in F:\PD\8308066\1\X.cs:line 83", "at X in <path> <n>"),
        ("Opening connection fullAddress = net.tcp://host01:12345/Svc ip 10.1.2.3:808",
         "Opening connection fullAddress = <url> ip <ip>"),
        ("Thread 0x1a8 hash deadbeef00112233aa", "Thread <hex> hash <hex>"),
        ("MRID 'ABC 123.", "MRID 'ABC <n>."),
        ("it's 5 o'clock", "it<s>clock"),
        ("\t\t[Validation Info]:   HashMissing  ", "[Validation Info]: HashMissing"),
        ("", ""),
    ],
)  # fmt: skip
def test_signature_examples(msg, expected):
    assert signature(msg) == expected


def test_signature_groups_variants_of_one_message():
    a = signature('Sending "IncidentsEventMessageType" message: 11111111-2222-3333-4444-555555555555 retry 1')
    b = signature('Sending "WorksEventMessageType" message: 99999999-8888-7777-6666-555555555555 retry 12')
    assert a == b


def test_signature_is_idempotent_and_bounded():
    for msg in ("x" * 5000, "a 1 " * 2000, "'" * 999, '"a" ' * 300, "0x" + "f" * 100):
        once = signature(msg)
        assert signature(once) == once
        assert len(once) <= MAX_SIGNATURE_CHARS


def test_signature_markers_have_no_digits_or_quotes():
    sig = signature("id 1 0x2 'q' \"r\" /a/b C:\\x 1.2.3.4 2026-01-01 10:00:00 http://h/p")
    assert not any(c.isdigit() for c in sig)
    assert "'" not in sig and '"' not in sig and "/" not in sig and "\\" not in sig
