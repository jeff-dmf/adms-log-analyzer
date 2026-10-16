"""Alignment of token sequences (store/align.py, ROADMAP 9.7): exact LCS within budgets, then anchored."""

from __future__ import annotations

import random

import pytest

from dmslog.store import align as AL
from dmslog.store.align import align, lcs_length


def check_valid(a, b, pairs):
    assert all(a[i] == b[j] for i, j in pairs)
    assert all(p[0] < q[0] and p[1] < q[1] for p, q in zip(pairs, pairs[1:], strict=False))


@pytest.mark.parametrize(
    ("a", "b", "length"),
    [
        ([], [], 0),
        ([1], [], 0),
        ([], [1], 0),
        ([1], [1], 1),
        ([1], [2], 0),
        ([1, 2], [2, 1], 1),
        ([1, 2, 3, 4], [1, 3, 4, 5], 3),
        ([1, 1, 1], [1, 1], 2),
        (list("ABCBDAB"), list("BDCABA"), 4),
        ([1, 2, 3], [4, 5, 6], 0),
        (list(range(50)), list(range(50)), 50),
        (list(range(50)), list(range(49, -1, -1)), 1),
    ],
)
def test_examples(a, b, length):
    out = align(a, b)
    check_valid(a, b, out.pairs)
    assert len(out.pairs) == length == lcs_length(a, b)
    assert out.optimal and out.method == "myers"


def test_matches_brute_force_on_random_small_inputs():
    rnd = random.Random(20261016)
    for _ in range(3000):
        alphabet = rnd.randint(1, 6)
        a = [rnd.randint(0, alphabet) for _ in range(rnd.randint(0, 16))]
        b = [rnd.randint(0, alphabet) for _ in range(rnd.randint(0, 16))]
        out = align(a, b)
        check_valid(a, b, out.pairs)
        assert len(out.pairs) == lcs_length(a, b), (a, b)
        assert out == align(a, b)  # deterministic


def test_fallback_is_valid_and_never_longer_than_the_lcs():
    rnd = random.Random(7)
    for _ in range(2000):
        a = [rnd.randint(0, 4) for _ in range(rnd.randint(0, 30))]
        b = [rnd.randint(0, 4) for _ in range(rnd.randint(0, 30))]
        out = align(
            a, b, work=rnd.randint(0, 60), bit_work=rnd.randint(0, 60), fallback_work=rnd.randint(0, 500)
        )
        check_valid(a, b, out.pairs)
        best = lcs_length(a, b)
        assert len(out.pairs) <= best
        assert not out.optimal or len(out.pairs) == best


def test_tokens_are_compared_only_for_equality():
    # relabelling tokens with any bijection gives the same pairs (ids vs codes, other template ids)
    rnd = random.Random(3)
    for _ in range(300):
        a = [rnd.randint(0, 5) for _ in range(rnd.randint(0, 40))]
        b = [rnd.randint(0, 5) for _ in range(rnd.randint(0, 40))]
        names = [f"T{k}" for k in range(6)]
        rnd.shuffle(names)
        assert align(a, b) == align([names[t] for t in a], [names[t] for t in b])


def test_windowed_fallback_resynchronises_after_a_long_insertion(monkeypatch):
    cycle = [1, 2, 3, 4, 5, 6]
    a = [cycle[k % 6] for k in range(3000)]
    b = a[:1000] + [7, 8, 9] * 300 + a[1000:]
    out = align(a, b, work=0, bit_work=0)
    check_valid(a, b, out.pairs)
    assert out.method == "anchored" and len(out.pairs) >= 0.95 * len(a)


@pytest.mark.parametrize("small", [0, 1, AL.SMALL])
def test_bit_parallel_stage_is_exact(monkeypatch, small):
    monkeypatch.setattr(AL, "SMALL", small)  # 0: Hirschberg down to single rows or columns
    rnd = random.Random(small)
    for _ in range(400):
        alphabet = rnd.randint(1, 7)
        a = [rnd.randint(0, alphabet) for _ in range(rnd.randint(0, 70))]
        b = [rnd.randint(0, alphabet) for _ in range(rnd.randint(0, 70))]
        out = align(a, b, work=0)
        check_valid(a, b, out.pairs)
        assert out.optimal and len(out.pairs) == lcs_length(a, b), (a, b)


def test_size_limit_worst_case_is_exact_within_the_budgets():
    """Two unrelated sides at the request limit (20,000 records each), the worst case for Myers: the
    bit-parallel stage answers exactly within its budget (memory is linear: one bit vector per token)."""
    rnd = random.Random(1)
    n = 20_000
    a = [rnd.randint(0, 5) for _ in range(n)]
    b = [rnd.randint(0, 5) for _ in range(n)]
    out = align(a, b)
    check_valid(a, b, out.pairs)
    assert out.method == "bit_parallel" and out.optimal
    assert out.work <= AL.ALIGN_WORK + AL.BIT_WORK
    assert out.work - AL.ALIGN_WORK <= 0.8 * AL.BIT_WORK  # margin left at the limit


def test_anchored_fallback_at_the_size_limit_stays_within_its_budget():
    rnd = random.Random(1)
    n = 20_000
    a = [rnd.randint(0, 5) for _ in range(n)]
    b = [rnd.randint(0, 5) for _ in range(n)]
    out = align(a, b, work=1000, bit_work=1000)
    check_valid(a, b, out.pairs)
    assert out.method == "anchored" and not out.optimal
    slack = 4 * (2 * n) + AL.GAP_WORK + 10 * AL.WINDOW**2
    assert out.work <= 2000 + AL.FALLBACK_WORK + slack


def test_anchored_fallback_keeps_most_of_similar_sides():
    rnd = random.Random(2)
    n = 20_000
    a = [rnd.randint(0, 30) for _ in range(n)]
    keep = [rnd.random() > 0.2 for _ in range(n)]  # 20% of the positions change in B
    b = [t if k else rnd.randint(0, 30) for t, k in zip(a, keep, strict=True)]
    out = align(a, b, work=0, bit_work=0)
    check_valid(a, b, out.pairs)
    assert out.method == "anchored" and len(out.pairs) >= 0.95 * sum(keep)  # unchanged positions are common


def test_identical_long_sequences_cost_linear_work():
    a = list(range(20_000))
    out = align(a, list(a))
    assert out.optimal and len(out.pairs) == 20_000 and out.work <= 3 * 20_000


@pytest.mark.parametrize("occurrences", [1, AL.DENSE])
def test_bit_parallel_memory_is_bounded_with_many_distinct_tokens(occurrences):
    """20,000 tokens per side (the route limit), each token repeated ``occurrences`` times, in different
    orders: Myers runs out and the bit-parallel stage runs. All distinct is the case that needed one
    M-bit integer per token (about 100 MB); exactly DENSE occurrences is the worst case of the bound
    (the most dense vectors). Both stay under 3 * M**2 / (8 * DENSE) bytes plus the lists."""
    import tracemalloc

    rnd = random.Random(occurrences)
    n = 20_000
    a = [k % (n // occurrences) for k in range(n)]
    rnd.shuffle(a)
    b = list(a)
    rnd.shuffle(b)
    tracemalloc.start()
    try:
        out = align(a, b, work=0)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    check_valid(a, b, out.pairs)
    assert out.method == "bit_parallel" and out.optimal
    assert peak < 3 * n * n // (8 * AL.DENSE) + 8 * 2**20, peak


def test_bit_parallel_masks_are_dense_only_for_frequent_tokens():
    rnd = random.Random(10)
    b = [rnd.randrange(3) for _ in range(500)] + list(range(100, 600))
    h = AL._Hirschberg(list(b), b, AL._Meter(10**9))
    assert set(h.fwd) == set(h.rev) == {0, 1, 2}
    assert all(len(h.pos[t]) < AL.DENSE for t in range(100, 600))
