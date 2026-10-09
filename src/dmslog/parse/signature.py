"""Message signatures: group records without a template (ARQUITETURA 6.3 and 6.7).

``signature(msg)`` replaces the variable parts of a message with markers, so that lines
that differ only by IDs, numbers or quoted values share one signature (``unknown_sig``
table, unknown-templates panel, template mining). The function is pure, deterministic and
idempotent: ``signature(signature(x)) == signature(x)``.

Markers: ``<ts>`` timestamp, ``<guid>``, ``<url>``, ``<path>`` (Windows, UNC or POSIX),
``<ip>``, ``<hex>`` (``0x..`` or a long hex run with a digit), ``<s>`` quoted text,
``<n>`` number. Whitespace runs collapse to one space. No marker contains a digit, a
quote, a slash or a backslash, so a second pass finds nothing new.
"""

from __future__ import annotations

import re

MAX_INPUT_CHARS = 2048  # only the head of very long lines is looked at
MAX_SIGNATURE_CHARS = 240

_WS = re.compile(r"\s+")
_H = "[0-9a-fA-F]"
_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:[.,]\d+)?(?:Z|[+-]\d\d:?\d\d)?"), "<ts>"),
    (re.compile(r"\{?\b%s{8}-%s{4}-%s{4}-%s{4}-%s{12}\b\}?" % ((_H,) * 5)), "<guid>"),
    (re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]{1,15}://[^\s\"'<>]*"), "<url>"),
    (re.compile(r"(?:\b[A-Za-z]:\\|\\\\)[^\s\"'<>|]*"), "<path>"),
    (re.compile(r"(?<![\w<>.])/(?:[\w.$~-]+/)+[\w.$~-]*"), "<path>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{1,5})?\b"), "<ip>"),
    (re.compile(r"\b0[xX][0-9a-fA-F]+\b"), "<hex>"),
    (re.compile(r"\b(?=[0-9a-fA-F]*[a-fA-F])(?=[0-9a-fA-F]*\d)[0-9a-fA-F]{16,}\b"), "<hex>"),
    (re.compile(r"'[^']*'"), "<s>"),
    (re.compile(r'"[^"]*"'), "<s>"),
    (re.compile(r"\d+(?:\.\d+)*"), "<n>"),
)


def _once(text: str) -> str:
    text = _WS.sub(" ", text[:MAX_INPUT_CHARS]).strip()
    for rx, marker in _RULES:
        text = rx.sub(marker, text)
    text = _WS.sub(" ", text).strip()
    return text[:MAX_SIGNATURE_CHARS].rstrip()


def signature(msg: str) -> str:
    """Normalized signature of a message (first line of a record)."""
    out = _once(msg)
    # Truncation or a rule may expose a new candidate; a couple of extra passes reach the
    # fixed point (in practice the second pass changes nothing).
    for _ in range(3):
        again = _once(out)
        if again == out:
            break
        out = again
    return out
