"""Severity inference (ARQUITETURA 6.4).

Scale: 1 critical, 2 error, 3 warning, 4 info, 5 noise (hidden by default).

The envelope level is *not* a severity for most lines: almost every record is ``DEBUGLOG`` and the
verbosity levels (``ALWAYS``, ``INFO``, ``VERBOSE``) say how much the process logs, not how bad
the line is. Only ``FATAL``/``EXIT``/``ERROR`` and the ``(ERROR)`` prefix of ``ALWAYS`` lines
count as severity evidence; ``WARNING`` is treated like a text marker.

``infer_severity`` applies the steps of section 6.4 in order (the first that decides wins):
1. envelope level FATAL/EXIT -> 1, ERROR or ``(ERROR)`` prefix -> 2;
2. ``sev`` declared by the matched template (including ``sev_field``/``sev_map``, resolved by the
   engine into ``TemplateMatch.sev_hint``);
3. error codes of the record (``codes`` field), resolved through ``codes``;
4. text markers in ``msg`` and in the first continuation line;
5. known false positives skip the text markers;
6. otherwise 4 (info), or 5 for VERBOSE tracing lines.

``generic_severity`` (steps 1, 4 and 6 on the envelope level and ``msg`` only) stays importable.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

SEV_CRITICAL, SEV_ERROR, SEV_WARNING, SEV_INFO, SEV_NOISE = 1, 2, 3, 4, 5

# Resolves an error code to a severity 1..4, or None when unknown (error_config of the ERS first,
# then the vault pack; supplied by the caller).
CodeResolver = Callable[[str], int | None]

_ERROR_MARKERS = re.compile(
    r"Non Fatal Exception|Exception:|failed|unsuccessfully|\[ERROR\]|ERROR -|Errors occurred"
    r"|\[Validation Critical\]"
)
# A .NET exception type name anywhere in the line (`[System.ObjectDisposedException] ...`).
_DOTNET_EXCEPTION = re.compile(r"\b\w+Exception\b")
_WARNING_MARKERS = re.compile(r"WARNING|\[Validation Warning\]|not received within|timed out")
_DETECTED_ERRORS = re.compile(r"^Detected errors:")
# Lines that contain a marker word but are not errors (docs/formato; ARQUITETURA 6.4 step 5).
_FALSE_POSITIVES = re.compile(
    r"\[Configuration\]\s*\[LoadingError\]"
    r"|Validation error is detected during validation process of error configuration"
    r"|can be found (?:in )?the adapters? sensi\w+ log"
    r"|can be found adapters sensistive log"
)
_LEVEL_FATAL, _LEVEL_EXIT, _LEVEL_ERROR, _LEVEL_WARNING, _LEVEL_VERBOSE = 1, 2, 3, 6, 8


def _level_severity(level: int | None, msg: str) -> int | None:
    """Step 1."""
    if level is not None:
        if level <= _LEVEL_EXIT:
            return SEV_CRITICAL
        if level == _LEVEL_ERROR or msg.startswith("(ERROR)"):
            return SEV_ERROR
    return None


def _text_severity(level: int | None, msg: str, cont: str | None) -> int | None:
    """Steps 4 and 5."""
    if _FALSE_POSITIVES.search(msg):
        return None
    head = msg
    if cont:
        nl = cont.find("\n")
        head = f"{msg}\n{cont if nl < 0 else cont[:nl]}"
    if _ERROR_MARKERS.search(head) or _DOTNET_EXCEPTION.search(head):
        return SEV_ERROR
    if level == _LEVEL_WARNING or _WARNING_MARKERS.search(head):
        return SEV_WARNING
    return None


def generic_severity(level: int | None, msg: str) -> int:
    """Severity from the envelope level and ``msg`` only (steps 1, 4 and 6; no template)."""
    sev = _level_severity(level, msg)
    if sev is None:
        sev = _text_severity(level, msg, None)
    return SEV_INFO if sev is None else sev


def _code_severity(codes: Any, resolver: CodeResolver | None) -> int | None:
    """Step 3: the most severe resolved code (lowest number)."""
    if resolver is None or not isinstance(codes, (list, tuple)):
        return None
    found = [s for s in (resolver(str(c)) for c in codes) if s is not None]
    return min(found) if found else None


def infer_severity(match: Any, record: Any, codes: CodeResolver | None = None) -> int:
    """Severity (1..5) of ``record`` given its template ``match`` (None when unclassified).

    ``record`` needs ``level``, ``msg`` and ``cont``. ``codes`` resolves error codes found in
    ``match.fields['codes']``; a resolved code can only escalate a template severity (a template
    that says warning does not hide a Critical code).
    """
    msg = record.msg
    sev = _level_severity(record.level, msg)
    if sev is not None:
        return sev
    resolved = None
    if match is not None and match.fields:
        resolved = _code_severity(match.fields.get("codes"), codes)
    if match is not None and match.sev_hint is not None:  # step 2
        hint = match.sev_hint
        return hint if resolved is None or hint <= resolved else resolved
    if resolved is not None:  # step 3
        return resolved
    sev = _text_severity(record.level, msg, record.cont)
    if sev is not None:
        return sev
    if match is None and _DETECTED_ERRORS.match(msg):
        return SEV_WARNING
    if match is None and record.level == _LEVEL_VERBOSE:
        return SEV_NOISE
    return SEV_INFO
