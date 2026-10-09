"""Tests for dmslog.parse.severity.infer_severity (ARQUITETURA 6.4), synthetic cases only."""

from types import SimpleNamespace

import pytest

from dmslog.parse.severity import generic_severity, infer_severity

ERROR, ALWAYS, DEBUGLOG, WARNING, INFO, VERBOSE = 3, 4, 5, 6, 7, 8


def rec(msg, level=DEBUGLOG, cont=None):
    return SimpleNamespace(msg=msg, level=level, cont=cont)


def tm(sev_hint=None, **fields):
    return SimpleNamespace(sev_hint=sev_hint, fields=fields)


@pytest.mark.parametrize(
    ("level", "msg", "expected"),
    [
        (1, "anything", 1),
        (2, "anything", 1),
        (ERROR, "plain", 2),
        (ALWAYS, "(ERROR) [Communication2] - boom", 2),
    ],
)
def test_step1_envelope_level_wins_over_template(level, msg, expected):
    assert infer_severity(tm(5), rec(msg, level)) == expected


def test_step2_template_sev_wins_over_text_markers():
    assert infer_severity(tm(5), rec("request failed", DEBUGLOG)) == 5
    assert infer_severity(tm(4), rec("Non Fatal Exception X: y")) == 4


def test_step3_codes_use_the_most_severe_resolved_code():
    table = {"CodeA": 3, "CodeB": 1, "CodeC": None}
    resolver = table.get
    assert infer_severity(tm(None, codes=["CodeA", "CodeB", "CodeC"]), rec("Detected errors:"), resolver) == 1
    assert infer_severity(tm(None, codes=["CodeC"]), rec("Detected errors:"), resolver) == 4
    # a code can escalate a template severity but never hide it
    assert infer_severity(tm(3, codes=["CodeB"]), rec("Detected errors:"), resolver) == 1
    assert infer_severity(tm(3, codes=["CodeA", "Other"]), rec("Detected errors:"), resolver) == 3
    assert infer_severity(tm(2, codes=["CodeA"]), rec("Detected errors:"), resolver) == 2


@pytest.mark.parametrize(
    "msg",
    [
        "Non Fatal Exception System.IO.IOException: x",
        "Processing failed for the message",
        "Upload finished unsuccessfully",
        "[ERROR] - Unable to initialize subscriber",
        "ERROR - Processing message failed",
        "Errors occurred while processing",
        "[Validation Critical]: SomeCode",
        "[System.ObjectDisposedException] Cannot access a disposed object",
    ],
)
def test_step4_error_markers(msg):
    assert infer_severity(None, rec(msg)) == 2
    assert generic_severity(DEBUGLOG, msg) == 2


@pytest.mark.parametrize(
    "msg", ["WARNING: x", "[Validation Warning]: Code", "Reply not received within 5 s", "call timed out"]
)
def test_step4_warning_markers(msg):
    assert infer_severity(None, rec(msg)) == 3


def test_warning_level_and_first_continuation_line():
    assert infer_severity(None, rec("something", WARNING)) == 3
    assert infer_severity(None, rec("Callback", cont="System.TimeoutException: x\n   at A.B()")) == 2
    assert infer_severity(None, rec("Callback", cont="fine\nfailed later")) == 4  # only the first line counts


@pytest.mark.parametrize(
    "msg",
    [
        "[Configuration][LoadingError] [Param] does not exist",
        "[ERROR] - Validation error is detected during validation process of error configuration",
        "A detailed description of these errors can be found in the adapters sensitive log.",
        "can be found adapters sensistive log",
    ],
)
def test_step5_false_positives_are_not_errors(msg):
    assert infer_severity(None, rec(msg)) == 4


def test_level_names_that_are_not_severity():
    for level in (ALWAYS, DEBUGLOG, INFO):
        assert infer_severity(None, rec("Started processing", level)) == 4
    assert infer_severity(None, rec("trace line", VERBOSE)) == 5


def test_unclassified_detected_errors_is_a_warning():
    assert infer_severity(None, rec("Detected errors:")) == 3


def test_default_is_info():
    assert infer_severity(None, rec("Services started.")) == 4
    assert infer_severity(tm(None), rec("Services started.")) == 4
