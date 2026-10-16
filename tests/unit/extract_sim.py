"""Reference backtracking matcher of the ad hoc extraction grammar (ROADMAP 9.8), with a step counter.

It walks the parsed form of ``parse/extractor.py`` the way a backtracking engine such as ``re`` does
(leftmost start, greedy repeats that give back one character at a time, an optional group tried before
it is skipped) and counts one step per test of one character against one atom, including the failing
test at the end of a run or of the text. The tests check that its result equals ``re`` and that its
steps never exceed ``Cost.steps``. It is a model: it shows the bound holds for the strategy, the time
per step of ``re`` is measured separately.
"""

from __future__ import annotations

from typing import Any

from dmslog.parse.extractor import Parsed, contains


class _Steps:
    n = 0


def search(
    parsed: Parsed, text: str
) -> tuple[tuple[int, int, dict[str, tuple[int, int] | None]] | None, int]:
    """(match as (start, end, spans of the named groups) or None, steps)."""
    steps = _Steps()
    n = len(text)

    def seq(items, i, pos, caps, k):
        if i == len(items):
            return k(pos, caps)
        item = items[i]
        kind = item[0]
        if kind == "set":
            steps.n += 1
            if pos < n and contains(item[1], ord(text[pos])):
                return seq(items, i + 1, pos + 1, caps, k)
            return None
        if kind == "rep":
            _, lo, hi, cs, _ = item
            count = 0
            while hi is None or count < hi:
                steps.n += 1
                if pos + count < n and contains(cs, ord(text[pos + count])):
                    count += 1
                else:
                    break
            if count < lo:
                return None
            for c in range(count, lo - 1, -1):
                r = seq(items, i + 1, pos + c, caps, k)
                if r is not None:
                    return r
            return None
        name, body = item[1], item[2]

        def after(p, cp, start=pos):
            if name is not None:
                cp = {**cp, name: (start, p)}
            return seq(items, i + 1, p, cp, k)

        r = seq(body, 0, pos, caps, after)
        if r is not None or kind == "group":
            return r
        return seq(items, i + 1, pos, caps, k)  # optional group skipped: its groups stay unset

    starts = [0] if parsed.anchored else range(n + 1)
    for s in starts:
        r = seq(parsed.items, 0, s, {}, lambda p, cp, s=s: (s, p, cp))
        if r is not None:
            start, end, caps = r
            return (start, end, {g: caps.get(g) for g in parsed.groups}), steps.n
    return None, steps.n


def re_result(regex: Any, anchored: bool, groups, text: str):
    m = regex.match(text) if anchored else regex.search(text)
    if m is None:
        return None
    return (m.start(), m.end(), {g: (None if m.start(g) < 0 else m.span(g)) for g in groups})
