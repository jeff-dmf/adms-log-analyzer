"""Alignment of two token sequences for the comparison of views (ARQUITETURA 10.9, ROADMAP 9.7).

:func:`align` returns the matched positions of a longest common subsequence (LCS) of two sequences of
hashable tokens (template codes, in :mod:`store.viewcompare`). It is pure, deterministic and bounded:

1. Tokens are interned to small integers in order of first appearance (A, then B), and positions whose
   token does not occur in the other sequence are dropped: they can never be matched, so the LCS of
   what remains is the LCS of the input, usually on much shorter sequences.
2. **Myers** (``method="myers"``): the O((N+M)D) difference algorithm in linear space (middle snake,
   divide and conquer), common prefix and suffix trimmed at each level. Near-linear when the sides are
   similar (small D); every step is charged to a work meter (``work``).
3. **Bit-parallel** (``method="bit_parallel"``, when Myers runs out of budget): Hirschberg's divide and
   conquer, each half row of LCS lengths computed with the bit-vector recurrence of Allison-Dix/Hyyro
   (``V' = (V + (V & M)) | (V & ~M)`` on Python integers of M bits, one step per token of A), so the cost
   is O(N * M / word) whatever D is. Charged to ``bit_work`` (rows times machine words). Both are exact:
   ``optimal=True``.
4. **Anchored** (both meters exhausted; not reachable within the request limits of the API): tokens that
   occur exactly once in both sides are anchors and the longest increasing chain of them is kept
   (patience diff); each gap is aligned exactly when it fits a small budget, otherwise in overlapping
   windows of :data:`WINDOW` tokens whose first half is committed. Charged to ``fallback_work``; a gap
   left when it runs out stays unmatched. A valid common subsequence, not necessarily the longest
   (``optimal=False``).

Ties between optimal alignments are broken by construction (Myers' middle snake; the smallest split
column in Hirschberg), so the result depends only on which tokens are equal. The meters count
operations, never time: the same input gives the same output on any machine. Memory is linear in N+M
(one bit vector per distinct token of B).
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from itertools import accumulate

# Work budgets (deterministic counters; times measured in ARQUITETURA 10.9).
ALIGN_WORK = 300_000  # Myers steps (about 0.06 s of CPython): enough when the sides barely differ
BIT_WORK = 20_000_000  # bit-parallel rows x 64-bit words: 20,000 x 20,000 tokens needs about 15 million
FALLBACK_WORK = 2_000_000
GAP_WORK = 200_000  # exact attempt on one gap of the anchored fallback
WINDOW = 192  # tokens per side in a window of the anchored fallback
SMALL = 4_096  # Hirschberg cells (rows x columns) solved directly by Myers


class _OverBudget(Exception):
    pass


class _Meter:
    __slots__ = ("left", "strict")

    def __init__(self, budget: int, strict: bool = True) -> None:
        self.left = budget
        self.strict = strict

    def spend(self, n: int) -> None:
        self.left -= n
        if self.left < 0 and self.strict:
            raise _OverBudget


@dataclass(frozen=True, slots=True)
class Alignment:
    pairs: tuple[tuple[int, int], ...]  # matched (i, j), strictly increasing in both
    optimal: bool
    method: str  # "myers" | "bit_parallel" | "anchored"
    work: int  # units spent by every stage that ran


def align(
    a: Sequence[Hashable],
    b: Sequence[Hashable],
    *,
    work: int | None = None,
    bit_work: int | None = None,
    fallback_work: int | None = None,
) -> Alignment:
    """Matched positions of a common subsequence of ``a`` and ``b`` (the longest when ``optimal``).

    Budgets default to :data:`ALIGN_WORK`, :data:`BIT_WORK` and :data:`FALLBACK_WORK` (read at call time)."""
    work = ALIGN_WORK if work is None else work
    bit_work = BIT_WORK if bit_work is None else bit_work
    fallback_work = FALLBACK_WORK if fallback_work is None else fallback_work
    codes: dict[Hashable, int] = {}
    ta = [codes.setdefault(t, len(codes)) for t in a]
    tb = [codes.setdefault(t, len(codes)) for t in b]
    in_a, in_b = set(ta), set(tb)
    ia = [i for i, t in enumerate(ta) if t in in_b]
    ib = [j for j, t in enumerate(tb) if t in in_a]
    fa = [ta[i] for i in ia]
    fb = [tb[j] for j in ib]
    out: list[tuple[int, int]] = []
    meter = _Meter(work)
    try:
        _lcs(fa, fb, 0, len(fa), 0, len(fb), out, meter)
        result = (True, "myers", work - meter.left)
    except _OverBudget:
        out = []
        bits = _Meter(bit_work)
        try:
            _Hirschberg(fa, fb, bits).run(out)
            result = (True, "bit_parallel", work + bit_work - bits.left)
        except _OverBudget:
            out = []
            fallback = _Meter(fallback_work, strict=False)
            _anchored(fa, fb, out, fallback)
            result = (False, "anchored", work + bit_work + fallback_work - fallback.left)
    pairs = tuple((ia[i], ib[j]) for i, j in out)
    return Alignment(pairs, *result)


def lcs_length(a: Sequence[Hashable], b: Sequence[Hashable]) -> int:
    """Length of the LCS by dynamic programming, O(N*M): reference for tests on small inputs only."""
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


# --------------------------------------------------------------------------- exact (Myers)


def _lcs(
    a: list[int], b: list[int], a0: int, a1: int, b0: int, b1: int, out: list[tuple[int, int]], meter: _Meter
) -> None:
    """Append to ``out``, in order, the pairs of an LCS of ``a[a0:a1]`` and ``b[b0:b1]``."""
    start = a0
    while a0 < a1 and b0 < b1 and a[a0] == b[b0]:
        out.append((a0, b0))
        a0 += 1
        b0 += 1
    end_a = a1
    while a1 > a0 and b1 > b0 and a[a1 - 1] == b[b1 - 1]:
        a1 -= 1
        b1 -= 1
    meter.spend(1 + (a0 - start) + (end_a - a1))
    if a0 < a1 and b0 < b1:
        split = _middle(a, b, a0, a1, b0, b1, meter)
        if split is not None:
            x, y = split
            if (x, y) == (a0, b0) or (x, y) == (a1, b1):  # cannot happen (D >= 2 here); never loop
                raise _OverBudget
            _lcs(a, b, a0, x, b0, y, out, meter)
            _lcs(a, b, x, a1, y, b1, out, meter)
    out.extend((a1 + k, b1 + k) for k in range(end_a - a1))


def _middle(
    a: list[int], b: list[int], a0: int, a1: int, b0: int, b1: int, meter: _Meter
) -> tuple[int, int] | None:
    """Split point of a shortest edit script of ``a[a0:a1]`` -> ``b[b0:b1]`` (Myers' middle snake, as in
    diff-match-patch's bisect), in absolute coordinates; None when the ranges share no token."""
    n, m = a1 - a0, b1 - b0
    max_d = (n + m + 1) // 2
    off = max_d
    size = 2 * max_d + 2  # room for the seeds at off + 1 even when max_d is 1
    v1 = [-1] * size
    v2 = [-1] * size
    v1[off + 1] = 0
    v2[off + 1] = 0
    delta = n - m
    front = delta % 2 != 0
    k1start = k1end = k2start = k2end = 0
    for d in range(max_d):
        steps = 0
        for k1 in range(-d + k1start, d + 1 - k1end, 2):
            k1o = off + k1
            if k1 == -d or (k1 != d and v1[k1o - 1] < v1[k1o + 1]):
                x1 = v1[k1o + 1]
            else:
                x1 = v1[k1o - 1] + 1
            y1 = x1 - k1
            s = x1
            while x1 < n and y1 < m and a[a0 + x1] == b[b0 + y1]:
                x1 += 1
                y1 += 1
            steps += 1 + x1 - s
            v1[k1o] = x1
            if x1 > n:
                k1end += 2
            elif y1 > m:
                k1start += 2
            elif front:
                k2o = off + delta - k1
                if 0 <= k2o < size and v2[k2o] != -1 and x1 >= n - v2[k2o]:
                    meter.spend(steps)
                    return a0 + x1, b0 + y1
        for k2 in range(-d + k2start, d + 1 - k2end, 2):
            k2o = off + k2
            if k2 == -d or (k2 != d and v2[k2o - 1] < v2[k2o + 1]):
                x2 = v2[k2o + 1]
            else:
                x2 = v2[k2o - 1] + 1
            y2 = x2 - k2
            s = x2
            while x2 < n and y2 < m and a[a1 - 1 - x2] == b[b1 - 1 - y2]:
                x2 += 1
                y2 += 1
            steps += 1 + x2 - s
            v2[k2o] = x2
            if x2 > n:
                k2end += 2
            elif y2 > m:
                k2start += 2
            elif not front:
                k1o = off + delta - k2
                if 0 <= k1o < size and v1[k1o] != -1:
                    x1 = v1[k1o]
                    if x1 >= n - x2:
                        meter.spend(steps)
                        return a0 + x1, b0 + off + x1 - k1o
        meter.spend(steps)
    return None


# --------------------------------------------------------------------------- exact (bit-parallel)

_ZEROS = bytes.maketrans(b"01", b"\x01\x00")  # bit character -> 1 when the bit is 0


class _Hirschberg:
    """Hirschberg's linear-space LCS with bit-parallel rows.

    ``fwd[t]`` has bit j set when ``b[j] == t`` and ``rev[t]`` the same for ``b`` reversed (bit
    ``m - 1 - j``), so any range of ``b`` is a shift and a mask."""

    def __init__(self, a: list[int], b: list[int], meter: _Meter) -> None:
        self.a, self.b, self.meter, self.m = a, b, meter, len(b)
        self.fwd: dict[int, int] = {}
        self.rev: dict[int, int] = {}
        m = len(b)
        for j, t in enumerate(b):
            self.fwd[t] = self.fwd.get(t, 0) | (1 << j)
            self.rev[t] = self.rev.get(t, 0) | (1 << (m - 1 - j))
        meter.spend(m)

    def run(self, out: list[tuple[int, int]]) -> None:
        self._solve(0, len(self.a), 0, self.m, out)

    def _zeros(self, rows: Sequence[int], masks: dict[int, int], shift: int, width: int) -> list[int]:
        """``z[j]`` = LCS of the tokens ``a[i] for i in rows`` and the first j columns of the masked range:
        the number of 0 bits among the j lowest bits of the bit vector after the rows."""
        self.meter.spend(len(rows) * (1 + width // 64) + width // 16)
        a = self.a
        full = (1 << width) - 1
        v = full
        cache: dict[int, int] = {}
        for i in rows:
            t = a[i]
            mt = cache.get(t)
            if mt is None:
                mt = cache[t] = (masks.get(t, 0) >> shift) & full
            if mt:
                u = v & mt
                v = (v + u) | (v - u)
        bits = format(v & full, f"0{width}b")[::-1].encode("ascii").translate(_ZEROS)
        return list(accumulate(bits, initial=0))

    def _solve(self, a0: int, a1: int, b0: int, b1: int, out: list[tuple[int, int]]) -> None:
        a, b = self.a, self.b
        while a0 < a1 and b0 < b1 and a[a0] == b[b0]:
            out.append((a0, b0))
            a0 += 1
            b0 += 1
        end = a1
        while a1 > a0 and b1 > b0 and a[a1 - 1] == b[b1 - 1]:
            a1 -= 1
            b1 -= 1
        n, m = a1 - a0, b1 - b0
        if n and m:
            if n * m <= SMALL or n == 1 or m == 1:  # tiny or one line: Myers, cost <= n * m
                _lcs(a, b, a0, a1, b0, b1, out, _Meter(0, strict=False))
                self.meter.spend(n * m)
            else:
                mid = (a0 + a1) // 2
                top = self._zeros(range(a0, mid), self.fwd, b0, m)
                bottom = self._zeros(range(a1 - 1, mid - 1, -1), self.rev, self.m - b1, m)
                best, split = -1, 0
                for j in range(m + 1):  # smallest column wins ties: deterministic
                    score = top[j] + bottom[m - j]
                    if score > best:
                        best, split = score, j
                self._solve(a0, mid, b0, b0 + split, out)
                self._solve(mid, a1, b0 + split, b1, out)
        out.extend((a1 + k, b1 + k) for k in range(end - a1))


# --------------------------------------------------------------------------- anchored fallback


def _anchored(a: list[int], b: list[int], out: list[tuple[int, int]], meter: _Meter) -> None:
    """Valid common subsequence within ``meter`` (never raises): anchors, then exact or windowed gaps."""
    stack = [(0, len(a), 0, len(b))]
    while stack:
        a0, a1, b0, b1 = stack.pop()
        while a0 < a1 and b0 < b1 and a[a0] == b[b0]:
            out.append((a0, b0))
            a0 += 1
            b0 += 1
        while a1 > a0 and b1 > b0 and a[a1 - 1] == b[b1 - 1]:
            a1 -= 1
            b1 -= 1
            out.append((a1, b1))
        meter.spend(1 + (a1 - a0) + (b1 - b0))
        if a0 >= a1 or b0 >= b1 or meter.left <= 0:
            continue
        sub = _Meter(min(GAP_WORK, meter.left))
        found: list[tuple[int, int]] = []
        try:
            _lcs(a, b, a0, a1, b0, b1, found, sub)
        except _OverBudget:
            meter.spend(min(GAP_WORK, meter.left))
        else:
            meter.spend(min(GAP_WORK, meter.left) - sub.left)
            out.extend(found)
            continue
        anchors = _unique_chain(a, b, a0, a1, b0, b1, meter)
        if not anchors:
            _windowed(a, b, a0, a1, b0, b1, out, meter)
            continue
        out.extend(anchors)
        bounds = [(a0 - 1, b0 - 1), *anchors, (a1, b1)]
        for (i, j), (k, m) in zip(bounds, bounds[1:], strict=False):
            if i + 1 < k and j + 1 < m:
                stack.append((i + 1, k, j + 1, m))
    out.sort()


def _unique_chain(
    a: list[int], b: list[int], a0: int, a1: int, b0: int, b1: int, meter: _Meter
) -> list[tuple[int, int]]:
    """Longest chain (increasing in both) of tokens that occur once in each range (patience diff)."""
    meter.spend((a1 - a0) + (b1 - b0))
    count_a: dict[int, int] = {}
    pos_a: dict[int, int] = {}
    for i in range(a0, a1):
        t = a[i]
        count_a[t] = count_a.get(t, 0) + 1
        pos_a[t] = i
    count_b: dict[int, int] = {}
    pos_b: dict[int, int] = {}
    for j in range(b0, b1):
        t = b[j]
        count_b[t] = count_b.get(t, 0) + 1
        pos_b[t] = j
    cand = sorted((pos_a[t], pos_b[t]) for t, c in count_a.items() if c == 1 and count_b.get(t) == 1)
    if not cand:
        return []
    meter.spend(len(cand) * 4)
    tails: list[int] = []  # smallest j ending a chain of each length
    tail_at: list[int] = []  # index in cand of that end
    prev = [-1] * len(cand)
    for k, (_, j) in enumerate(cand):
        p = bisect_left(tails, j)
        if p == len(tails):
            tails.append(j)
            tail_at.append(k)
        else:
            tails[p] = j
            tail_at[p] = k
        prev[k] = tail_at[p - 1] if p else -1
    chain = []
    k = tail_at[-1]
    while k != -1:
        chain.append(cand[k])
        k = prev[k]
    chain.reverse()
    return chain


def _windowed(
    a: list[int], b: list[int], a0: int, a1: int, b0: int, b1: int, out: list[tuple[int, int]], meter: _Meter
) -> None:
    """Exact LCS of overlapping windows of :data:`WINDOW` tokens, committing the pairs of the first half."""
    half = WINDOW // 2
    while a0 < a1 and b0 < b1 and meter.left > 0:
        ca, cb = min(a1, a0 + WINDOW), min(b1, b0 + WINDOW)
        found: list[tuple[int, int]] = []
        sub = _Meter(0, strict=False)  # one window is bounded by its size: charge what it used
        _lcs(a, b, a0, ca, b0, cb, found, sub)
        meter.spend(-sub.left)
        if ca == a1 and cb == b1:
            out.extend(found)
            return
        keep = []
        for i, j in found:
            if i >= a0 + half or j >= b0 + half:
                break
            keep.append((i, j))
        if keep:
            out.extend(keep)
            a0, b0 = keep[-1][0] + 1, keep[-1][1] + 1
        else:
            a0, b0 = min(a1, a0 + half), min(b1, b0 + half)
