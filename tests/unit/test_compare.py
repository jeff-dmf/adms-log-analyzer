"""Line-level compare of two records (synthetic data)."""

from __future__ import annotations

from dmslog.store import bookmarks as bm


def rec(msg, cont=None):
    return {"msg": msg, "cont": cont}


def test_identical_records():
    out = bm.diff_records(rec("same", "a\nb"), rec("same", "a\nb"))
    assert out["identical"] is True and out["truncated"] is False
    assert out["ops"] == [
        {"tag": "equal", "a": ["same", "a", "b"], "b": ["same", "a", "b"], "a_start": 1, "b_start": 1}
    ]
    assert out["stats"] == {"equal": 3, "changed": 0, "removed": 0, "added": 0}


def test_change_insert_delete_counts():
    a = rec("head", "one\ntwo\nthree\nfour")
    b = rec("head", "one\nTWO\nthree\nfive\nsix")
    out = bm.diff_records(a, b)
    assert out["identical"] is False
    tags = [op["tag"] for op in out["ops"]]
    assert tags == ["equal", "replace", "equal", "replace"]
    assert out["stats"] == {"equal": 3, "changed": 3, "removed": 0, "added": 0}
    only_b = bm.diff_lines(["x"], ["x", "y"])
    assert only_b["stats"]["added"] == 1 and only_b["ops"][-1]["tag"] == "insert"
    only_a = bm.diff_lines(["x", "y"], ["x"])
    assert only_a["stats"]["removed"] == 1 and only_a["ops"][-1]["tag"] == "delete"


def test_missing_continuation_and_none_message():
    out = bm.diff_records({"msg": None, "cont": None}, rec("m", "c"))
    assert out["ops"][0]["a"] == [""] and out["stats"]["changed"] + out["stats"]["added"] >= 1


def test_bounded_lines_and_width():
    many = "\n".join(f"l{i}" for i in range(bm.COMPARE_MAX_LINES + 50))
    out = bm.diff_records(rec("m", many), rec("m", many))
    assert out["truncated"] is True and out["stats"]["equal"] == bm.COMPARE_MAX_LINES
    wide = bm.diff_records(rec("x" * (bm.COMPARE_MAX_LINE_CHARS + 10)), rec("x"))
    assert wide["truncated"] is True and len(wide["ops"][0]["a"][0]) == bm.COMPARE_MAX_LINE_CHARS


def test_worst_case_is_fast():
    a = rec("m", "\n".join(f"a{i}" for i in range(bm.COMPARE_MAX_LINES)))
    b = rec("m", "\n".join(f"b{i}" for i in range(bm.COMPARE_MAX_LINES)))
    out = bm.diff_records(a, b)
    assert out["stats"] == {"equal": 1, "changed": bm.COMPARE_MAX_LINES - 1, "removed": 0, "added": 0}


def test_adversarial_inputs_finish_quickly_with_a_correct_fallback():
    import time

    n = bm.COMPARE_MAX_LINES
    alt = ["x" if i % 2 else "y" for i in range(n)]
    shifted = ["y" if i % 2 else "x" for i in range(n)]
    same = ["same"] * n
    for a, b in ((alt, shifted), (same, same[:-1] + ["other"]), (alt, alt[::-1])):
        start = time.perf_counter()
        out = bm.diff_lines(a, b)
        assert time.perf_counter() - start < 1.0
        # whatever the algorithm, the ops must rebuild both sides
        assert [x for op in out["ops"] for x in op["a"]] == a
        assert [x for op in out["ops"] for x in op["b"]] == b
    assert bm.diff_lines(alt, shifted)["approximate"] is True
    assert bm.diff_lines(["a", "b"], ["a", "c"])["approximate"] is False
    wide = ["z" * bm.COMPARE_MAX_LINE_CHARS] * 200
    assert bm.diff_lines(wide, wide[:-1])["approximate"] is True  # too many characters


def test_positional_fallback_ops():
    out = bm.diff_lines(["a"] * 20_000, ["a"] * 19_999 + ["b"])
    assert out["approximate"] is True
    assert [op["tag"] for op in out["ops"]] == ["equal", "replace"]
    assert out["stats"]["changed"] == 1
