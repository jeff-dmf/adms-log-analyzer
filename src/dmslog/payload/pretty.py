"""Tolerant payload formatter for display.

Never raises and never executes or resolves anything: entities stay literal,
DOCTYPE declarations are shown as literal text (no DTD processing, no network),
HTML is returned raw. The XML tokenizer is our own, iterative and linear, so
hostile input (huge, deeply nested, malformed) cannot blow the stack or the
clock. Formatting only adds or removes whitespace between tags.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

MAX_DEPTH = 512
# Output budget relative to max_chars: indentation can multiply the size of
# hostile (deep and wide) input, so formatted output is bounded as well.
_OUTPUT_FACTOR = 5


@dataclass(frozen=True)
class PrettyResult:
    """Outcome of :func:`format_payload`.

    ``kind`` is one of xml|xml_seq|html|json|text. ``reason`` explains why the
    text is (partly) raw: truncated, malformed, too_large, too_deep, html_raw,
    text, empty, internal_error; it is None for a clean reformat.
    """

    text: str
    ok: bool
    kind: str
    reason: str | None
    truncated_display: bool = False


# Start tag: name, then attributes with quoted values (no '<' inside values).
# Possessive quantifiers everywhere (name included): no backtracking, so a
# failed match costs O(length) once instead of O(length^2).
_START_TAG = re.compile(r"""<([^\s/>"'<=]++)(?:[^>"'<]|"[^"<]*+"|'[^'<]*+')*+>""")
_TAG_PARTIAL = re.compile(r"""<[^\s/>"'<=]++(?:[^>"'<]|"[^"<]*+"|'[^'<]*+')*+""")

# Token: (kind, raw, name, start)
#   o open, s self-closing, c close, t text, d cdata, m comment,
#   p processing instruction, q doctype/declaration
_Token = tuple[str, str, str, int]


def _tokenize(s: str) -> tuple[list[_Token], str, int]:
    """Split ``s`` into tokens. Returns (tokens, status, pos).

    status is 'ok', 'truncated' (input ends inside a construct; pos is where
    it starts) or 'malformed' (pos is where the problem starts).
    """
    tokens: list[_Token] = []
    append = tokens.append
    find = s.find
    n = len(s)
    pos = 0
    while pos < n:
        lt = find("<", pos)
        if lt == -1:
            append(("t", s[pos:], "", pos))
            break
        if lt > pos:
            append(("t", s[pos:lt], "", pos))
        pos = lt
        if lt + 1 >= n:
            return tokens, "truncated", lt
        if s.startswith("<!--", lt):
            end = find("-->", lt + 4)
            if end == -1:
                return tokens, "truncated", lt
            append(("m", s[lt : end + 3], "", lt))
            pos = end + 3
        elif s.startswith("<![CDATA[", lt):
            end = find("]]>", lt + 9)
            if end == -1:
                return tokens, "truncated", lt
            append(("d", s[lt : end + 3], "", lt))
            pos = end + 3
        elif s.startswith("<?", lt):
            end = find("?>", lt + 2)
            if end == -1:
                return tokens, "truncated", lt
            append(("p", s[lt : end + 2], "", lt))
            pos = end + 2
        elif s.startswith("<!", lt):
            gt = find(">", lt + 2)
            br = find("[", lt + 2, gt if gt != -1 else n)
            if br != -1:
                rb = find("]", br)
                gt = find(">", rb) if rb != -1 else -1
            if gt == -1:
                return tokens, "truncated", lt
            append(("q", s[lt : gt + 1], "", lt))
            pos = gt + 1
        elif s.startswith("</", lt):
            gt = find(">", lt + 2)
            if gt == -1:
                status = "malformed" if find("<", lt + 2) != -1 else "truncated"
                return tokens, status, lt
            if find("<", lt + 2, gt) != -1:
                return tokens, "malformed", lt
            name = s[lt + 2 : gt].strip()
            if not name or any(c.isspace() for c in name):
                return tokens, "malformed", lt
            append(("c", s[lt : gt + 1], name, lt))
            pos = gt + 1
        else:
            ch = s[lt + 1]
            if not (ch.isalpha() or ch in "_:"):
                return tokens, "malformed", lt
            m = _START_TAG.match(s, lt)
            if m is None:
                part = _TAG_PARTIAL.match(s, lt)
                e = part.end() if part else lt + 1
                if e >= n:
                    return tokens, "truncated", lt
                c = s[e]
                if c in "\"'" and find(c, e + 1) == -1 and find("<", e + 1) == -1:
                    return tokens, "truncated", lt
                return tokens, "malformed", lt
            end = m.end()
            kind = "s" if s[end - 2] == "/" else "o"
            append((kind, s[lt:end], m.group(1), lt))
            pos = end
    return tokens, "ok", n


def tokenize(s: str) -> tuple[list[_Token], str, int]:
    """Public wrapper of the tolerant tokenizer: ``(tokens, status, pos)``, see :func:`_tokenize`.

    Token kinds: o open, s self-closing, c close, t text, d cdata, m comment, p processing
    instruction, q declaration. Nothing is parsed or resolved: entities stay literal.
    """
    return _tokenize(s)


def _indent(depth: int) -> str:
    return "  " * depth


def _format_xml(s: str, max_chars: int) -> PrettyResult:
    tokens, status, tok_pos = _tokenize(s)
    n = len(tokens)
    lines: list[str] = []
    stack: list[str] = []
    top_elements = 0
    budget = max_chars * _OUTPUT_FACTOR
    used = 0
    bad_at = -1  # offset where raw remainder starts (malformed)
    i = 0
    while i < n:
        kind, raw, name, start = tokens[i]
        depth = len(stack)
        if kind == "o":
            if depth >= MAX_DEPTH:
                return PrettyResult(s[:max_chars], False, "xml", "too_deep", len(s) > max_chars)
            j = i + 1
            while j < n and tokens[j][0] in ("t", "d"):
                j += 1
            if j < n and tokens[j][0] == "c" and tokens[j][2] == name:
                line = _indent(depth) + raw + "".join(t[1] for t in tokens[i + 1 : j]) + tokens[j][1]
                i = j + 1
            else:
                line = _indent(depth) + raw
                stack.append(name)
                i += 1
            if depth == 0:
                top_elements += 1
        elif kind == "s":
            line = _indent(depth) + raw
            if depth == 0:
                top_elements += 1
            i += 1
        elif kind == "c":
            if stack and stack[-1] == name:
                stack.pop()
                line = _indent(len(stack)) + raw
                i += 1
            else:
                bad_at = start
                break
        elif kind == "t":
            stripped = raw.strip()
            if not stripped:
                i += 1
                continue
            if depth == 0:
                bad_at = start
                break
            line = _indent(depth) + stripped
            i += 1
        elif kind == "d":
            if depth == 0:
                bad_at = start
                break
            line = _indent(depth) + raw
            i += 1
        else:  # comment, processing instruction, declaration
            line = _indent(depth) + raw
            i += 1
        used += len(line) + 1
        if used > budget:
            return PrettyResult(s[:max_chars], False, "xml", "too_large", True)
        lines.append(line)

    if bad_at == -1 and status == "malformed":
        bad_at = tok_pos
    kind_name = "xml_seq" if top_elements > 1 else "xml"

    def finish(text: str, ok: bool, kind: str, reason: str | None) -> PrettyResult:
        # One budget for every output path (prefix, raw tail, synthetic closers).
        if len(text) > budget:
            return PrettyResult(s[:max_chars], False, "xml", "too_large", True)
        return PrettyResult(text, ok, kind, reason)

    if bad_at != -1:
        body = "\n".join(lines)
        rest = s[bad_at:]
        return finish(body + "\n" + rest if body else rest, False, kind_name, "malformed")

    if status == "truncated":
        lines.append(_indent(len(stack)) + s[tok_pos:])
    truncated = status == "truncated" or bool(stack)
    if truncated:
        while stack:
            lines.append(_indent(len(stack) - 1) + f"</{stack.pop()}>")
        reason: str | None = "truncated"
    else:
        reason = None
    if top_elements == 0 and not truncated:
        return PrettyResult(s, False, "text", "empty" if not s.strip() else "text")
    return finish("\n".join(lines), True, kind_name, reason)


def _format_json(s: str, max_chars: int) -> PrettyResult:
    try:
        value = json.loads(s)
        text = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False)
    except (ValueError, RecursionError):
        return PrettyResult(s, False, "json", "malformed")
    if len(text) > max_chars * _OUTPUT_FACTOR:
        return PrettyResult(s[:max_chars], False, "json", "too_large", True)
    return PrettyResult(text, True, "json", None)


def format_payload(text: str, fmt: str, *, max_chars: int = 1_000_000) -> PrettyResult:
    """Format ``text`` for display according to ``fmt`` (xml|xml_seq|html|json|text).

    Never raises. Input is never executed and entities are never expanded.
    """
    try:
        if not isinstance(text, str):
            text = str(text)
        if fmt == "html":
            return PrettyResult(text[:max_chars], False, "html", "html_raw", len(text) > max_chars)
        if fmt not in ("xml", "xml_seq", "json"):
            return PrettyResult(text[:max_chars], False, "text", "text", len(text) > max_chars)
        kind = "json" if fmt == "json" else "xml"
        if len(text) > max_chars:
            return PrettyResult(text[:max_chars], False, kind, "too_large", True)
        norm = text.replace("\r\n", "\n").replace("\r", "\n")
        if fmt == "json":
            return _format_json(norm, max_chars)
        return _format_xml(norm, max_chars)
    except Exception:  # noqa: BLE001 - the formatter must never raise
        try:
            return PrettyResult(str(text)[:max_chars], False, "text", "internal_error")
        except Exception:  # noqa: BLE001
            return PrettyResult("", False, "text", "internal_error")
