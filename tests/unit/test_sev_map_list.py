"""sev_map over a list field: the most severe (lowest) mapped value wins (RT design note, C5)."""

import pytest

from dmslog.parse.templates import load_texts

SEV_MAP = "{ DoneWithSuccess = 4, Warning = 3, DoneWithError = 2 }"


def _set(sev=5, field_type="list", sev_map=SEV_MAP):
    body = (
        'family = "X"\n[[template]]\ncode = "X-001"\nclass = "other"\n'
        f"sev = {sev}\n"
        "regex = '^Status:\\s*(?P<status>.*)$'\n"
        f'fields = {{ status = "{field_type}" }}\n'
        f'sev_field = "status"\nsev_map = {sev_map}\n'
    )
    return load_texts({"x.toml": body}).for_family("X", sensitive=False)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Status: DoneWithSuccess", 4),
        ("Status: DoneWithSuccess, DoneWithSuccess", 4),
        ("Status: DoneWithSuccess, Warning", 3),
        ("Status: DoneWithSuccess, DoneWithError", 2),
        ("Status: DoneWithError, Warning, DoneWithSuccess", 2),
        ("Status: Unknown, DoneWithSuccess", 4),
        ("Status: Unknown, Other", 5),
        ("Status: ", 5),
    ],
)
def test_list_field_takes_most_severe_mapped_value(text, expected):
    tm = _set().classify_text(text)
    assert tm is not None
    assert tm.sev_hint == expected


def test_scalar_field_still_resolves():
    ts = _set(field_type="str")
    assert ts.classify_text("Status: Warning").sev_hint == 3
    assert ts.classify_text("Status: Nope").sev_hint == 5


def test_numeric_keys_match_int_scalar_and_list_values():
    ts = _set(field_type="int", sev_map='{ "1" = 2, "7" = 4 }')
    assert ts.classify_text("Status: 7").sev_hint == 4
    assert ts.classify_text("Status: 3").sev_hint == 5
    tl = _set(field_type="list", sev_map='{ "1" = 2, "7" = 4 }')
    assert tl.classify_text("Status: 7, 1").sev_hint == 2
    assert tl.classify_text("Status: 7, 9").sev_hint == 4
