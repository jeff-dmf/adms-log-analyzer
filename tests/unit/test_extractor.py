"""Ad hoc extraction regex (ROADMAP 9.8): grammar, determinism, cost bound, emission and runtime probes."""

from __future__ import annotations

import re
import time

import extract_sim as sim
import pytest

from dmslog.parse.extractor import (
    DIGIT,
    MAX_STEPS,
    MIN_WINDOW,
    TARGET_WINDOWS,
    Cost,
    ExtractorError,
    check_determinism,
    compile_extractor,
    cost_of,
    parse,
    suggest_type,
    window_for,
)

ACCEPTED = [
    r"GID: (?P<gid>\d+)",
    r'Request with ID "(?P<id>[^"]+)"',
    r"(?P<op>\w+) request received",
    r'ExceptionType: "(?P<exc>[^"]+)" Reason: (?P<reason>.*)',
    r"count=(?P<n>\d+)(?:, errors=(?P<e>\d+))?",
    r"^(?P<a>\S+) (?P<b>\S+)",
    r"(?P<k>\w+)=(?P<v>\w*)",
    r"(?P<ip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})",
    r"took (?P<ms>\d{1,9}) ?ms",
    r"(?P<x>[\u00e9\U0001F600a-c]+)!",
    r"\[(?P<lvl>[A-Z]{3,8})\]",
    r"(?:(?P<a>x)y)?z",
    r"(?P<name>[^\]\\]+)\]",
    r"a\{(?P<b>[0-9]{2,}),",
    r"(?P<t>\t+)\n",
    r"^(?P<all>.+)",
    r"id=(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
]

REFUSED = {
    r"[A-Z]\w+(?P<x>Exception)": "can also match what follows",
    r"(?P<a>.*)x": "can also match what follows",
    r"(?P<a>\d*)": "empty text",
    r"(?P<a>x?)": "empty text",
    r"(a)": "unnamed capture group",
    r"x+": "at least one named group",
    r"(?P<A>x)": "lowercase",
    r"(?P<a>x)(?P<a>y)": "used twice",
    r"x$(?P<a>y)": "'$' is not supported",
    r"(?P<a>x)\Z": "anchors",
    r"\b(?P<a>x)": "anchors",
    r"(?P<a>x)|y": "alternation",
    r"(?P<a>x|y)": "alternation",
    r"(?P<a>x){2}": "only repeat of a group",
    r"(?P<a>x){0,1}": "only repeat of a group",
    r"(?P<a>x)??": "lazy",
    r"(?P<a>x)*": "only repeat of a group",
    r"(?P<a>a*?)b": "lazy",
    r"(?P<a>a++)b": "lazy, possessive",
    r"(?P<a>a{2}{3})": "lazy, possessive",
    r"a{65}(?P<b>c)": "from 1 to 64",
    r"a{0}(?P<b>c)": "from 1 to 64",
    r"a{99999999999}(?P<b>c)": "at most 64",
    r"a{65,}(?P<b>c)": "at most 64",
    r"a{3,2}(?P<b>c)": "0 <= m <= n",
    r"a{,}(?P<b>c)": "at least one bound",
    r"a{}(?P<b>c)": "must be escaped",
    r"a{x}(?P<b>c)": "must be escaped",
    r"{2}(?P<b>c)": "nothing to repeat",
    r"(?P<b>c){": "must be escaped",
    r"(?i)(?P<a>x)": "no flags",
    r"(?a:(?P<a>x))": "no flags",
    r"(?>(?P<a>x))": "no flags or atomic",
    r"(?P<a>x)(?P=a)": "back-references",
    r"(?P<a>x)\1": "back-references",
    r"(?P<a>x)\0": "back-references",
    r"(?=x)(?P<a>x)": "look-arounds",
    r"(?<!x)(?P<a>x)": "look-arounds",
    r"(?#c)(?P<a>x)": "comments",
    r"(?(1)a|b)(?P<a>x)": "conditionals",
    r"(?<a>x)": "only (?:...)",
    r"[]a](?P<b>c)": "empty class",
    r"[^]a](?P<b>c)": "empty class",
    r"[[a]](?P<b>c)": "nested sets",
    r"[a--b](?P<b>c)": "set operations",
    r"[a&&b](?P<b>c)": "set operations",
    r"[a-\d](?P<b>c)": "between two characters",
    r"[\w-z](?P<b>c)": "between two characters",
    r"[z-a](?P<b>c)": "low to high",
    r"[\b](?P<b>c)": "backspace",
    r"[^\x00-\U0010ffff](?P<b>c)": "matches no character",
    r"\p{L}(?P<b>c)": "not allowed",
    r"\N{DIGIT ONE}(?P<b>c)": "not allowed",
    "\\\u00e9(?P<b>c)": "only ASCII punctuation",
    r"\x4(?P<b>c)": "exactly 2 hex digits",
    r"\U00110000(?P<b>c)": "above U+10FFFF",
    "(?P<b>c) ": "escapes",
    "(?P<b>c)\x01": "escapes",
    "(?P<b>c\n)": "escapes",
    r"(?P<b>c)\\": None,  # accepted: an escaped backslash
    "(?P<b>c)\\": "trailing backslash",
    r"(?P<b>c": "missing ')'",
    r"(?P<b>c))": "unbalanced",
    r"(?:)(?P<b>c)": "empty group",
    r"(?:(?:(?P<b>c)))": "deeper than 2",
    r"(?:(?:a)?b)?(?P<c>d)": "may not be nested",
    r"(?:a?)?(?P<c>d)": "must not be able to match the empty text",
    r"(?P<c>d)(?:ab)?a": "can start like what follows",
    r"a^(?P<b>c)": "only allowed at the start",
    r"[a(?P<b>c)": "unterminated",
    "".join(f"(?P<g{i}>c)" for i in range(17)): "at most 16",
    "(?:a)?(?:b)?(?:c)?(?:d)?(?:e)?(?P<f>g)": "at most 4 optional",
    "[" + "a" * 33 + "](?P<b>c)": "at most 32 items",
    "(?P<b>c)" + "d" * 600: "longer than 512",
    # a failing continuation after the repeat tries 200 optional characters: too much work per give-back
    "(?P<a>a+)" + "".join(chr(0x100 + i) + "?" for i in range(200)) + "z": "step budget",
}


@pytest.mark.parametrize("source", ACCEPTED)
def test_accepted_regexes_compile_and_emit_the_same_language(source):
    ex = compile_extractor(source)
    assert MIN_WINDOW <= ex.window <= TARGET_WINDOWS["msg"]
    assert ex.steps <= MAX_STEPS
    assert ex.groups == tuple(re.compile(source).groupindex)
    # the emitted pattern is in the grammar and writes itself back unchanged
    again = compile_extractor(("^" if ex.anchored else "") + ex.pattern)
    assert again.pattern == ex.pattern and again.window == ex.window


@pytest.mark.parametrize(("source", "needle"), list(REFUSED.items()), ids=range(len(REFUSED)))
def test_refused_regexes_say_why(source, needle):
    if needle is None:
        compile_extractor(source)
        return
    with pytest.raises(ExtractorError) as info:
        compile_extractor(source)
    assert needle in str(info.value), str(info.value)


def test_errors_carry_the_offset_of_the_problem():
    with pytest.raises(ExtractorError) as info:
        compile_extractor(r"abc(?P<a>.*)x")
    assert info.value.offset == 9 and info.value.code == "bad_regex"
    with pytest.raises(ExtractorError) as info:
        compile_extractor(r"abc[z-a](?P<b>c)")
    assert info.value.offset == 4


def test_escapes_have_ascii_meaning_and_classes_are_spelled_out():
    ex = compile_extractor(r"(?P<d>\d+)(?P<w>\W)(?P<s>\s)")
    assert ex.pattern == r"(?P<d>[0-9]+)(?P<w>[^0-9A-Z_a-z])(?P<s>[\x09-\x0d ])"
    assert ex.run("٣٤ 12! ") == {"d": "12", "w": "!", "s": " "}  # Arabic-Indic digits are not \d
    assert compile_extractor(r"(?P<x>[^\]])").pattern == r"(?P<x>[^\]])"
    assert compile_extractor(r"a(?P<x>.)").pattern == "a(?P<x>.)"
    assert compile_extractor(r"(?:ab)(?P<x>c)").pattern == "ab(?P<x>c)"  # plain groups are inlined


def test_target_text_sees_the_continuation_and_msg_is_trimmed():
    msg = compile_extractor(r"^(?P<a>[a-z]+)")
    assert msg.apply("  \tabc  ", "x\ny") == ({"a": "abc"}, False)
    text = compile_extractor(r"code=(?P<c>\d+)", target="text")
    assert text.apply("first line", "  more\n  code=42") == ({"c": "42"}, False)
    assert msg.apply("first line", "code=42")[0] == {"a": "first"}


def test_records_longer_than_the_window_are_searched_in_part_and_marked():
    ex = compile_extractor(r"x(?P<n>\d+)")
    w = ex.window
    values, cut = ex.apply("x12" + "a" * w)
    assert values == {"n": "12"} and cut
    values, cut = ex.apply("a" * w + "x12")
    assert values is None and cut
    # exactly the window: not cut; trailing spaces and TABs do not count for msg
    values, cut = ex.apply("x1" + "a" * (w - 2) + " \t ")
    assert values == {"n": "1"} and not cut


def test_types_convert_like_the_templates():
    ex = compile_extractor(
        r"n=(?P<n>-?\d+) h=(?P<h>0x[0-9A-F]+) g=(?P<g>[0-9A-F-]+) f=(?P<f>[0-9.]+) e=(?P<e>[a-z]*);",
        types={"n": "int", "h": "hex", "g": "guid", "f": "float", "e": "int"},
    )
    assert ex.run("n=-5 h=0x1F g=ABC-D f=1.5 e=;") == {"n": -5, "h": 31, "g": "abc-d", "f": 1.5, "e": ""}
    big = "9" * 19
    # values that do not convert stay text (a 19-digit integer, a float with two dots)
    assert ex.run(f"n={big} h=0x1 g=A f=1.2.3 e=;") == {"n": big, "h": 1, "g": "a", "f": "1.2.3", "e": ""}
    with pytest.raises(ExtractorError, match="not a group"):
        compile_extractor(r"(?P<a>x)", types={"b": "int"})
    with pytest.raises(ExtractorError, match="must be one of"):
        compile_extractor(r"(?P<a>x)", types={"a": "list"})


def test_suggested_types():
    assert suggest_type(["1", "-20", ""]) == "int"
    assert suggest_type(["0x1f", "0X2"]) == "hex"
    assert suggest_type(["0b5c0000-0000-4000-8000-000000000000"]) == "guid"
    assert suggest_type(["1.5", "2"]) == "float"
    assert suggest_type(["1.5", "x"]) == "str"
    assert suggest_type([]) == "str"
    assert suggest_type(["9" * 19]) == "float"  # too long for an int, still a finite number


def test_cost_model_numbers():
    cost = cost_of(parse(r"[0-9]+(?P<x>[a-z])"))
    assert cost == Cost(atoms=2, optional=0, reach=1, longest=None)
    # one start of an anchored regex; every start of a search
    assert cost.steps(10, True) == 2 * (10 + 2)
    assert cost.steps(10, False) == 2 * (55 + 2 * 11)
    bounded = cost_of(parse(r"(?P<x>a{1,3})b"))
    assert bounded.longest == 4
    assert bounded.steps(10, False) == 2 * ((4 * 5 // 2 + 4 * 6) + 2 * 11)
    # reach: the atoms a failing continuation tries (nullable ones are skipped)
    assert cost_of(parse(r"(?P<x>a+)b?c?d")).reach == 3
    assert cost_of(parse(r"(?P<x>a+)(?:bc)?d")).reach == 2
    assert cost_of(parse(r"(?:(?P<x>a+))?b")).reach == 1
    assert window_for(cost, False, 10**6) == 2045 == compile_extractor(r"[0-9]+(?P<x>[a-z])").window
    assert window_for(Cost(1, 0, 0, None), True, 100, max_steps=0) == -1


def test_anchored_regexes_get_the_whole_window():
    assert compile_extractor(r"^(?P<a>[^ ]+) (?P<b>.*)").window == TARGET_WINDOWS["msg"]
    assert compile_extractor(r"^(?P<a>[^ ]+) (?P<b>.*)", target="text").window == TARGET_WINDOWS["text"]


def test_determinism_errors_name_an_overlapping_character():
    with pytest.raises(ExtractorError, match="'5'"):
        check_determinism(parse(r"(?P<a>[0-9a-z]+)[5-9]"))
    assert parse(r"(?P<a>\d)").items[0][2][0][1] == DIGIT


def _probe(source: str, text: str) -> float:
    ex = compile_extractor(source)
    subject = text[: ex.window]
    best = 1.0
    for _ in range(3):
        start = time.perf_counter()
        ex.run(subject)
        best = min(best, time.perf_counter() - start)
    return best


# Accepted forms at their own window with the input that makes each one work hardest. The bound is in
# steps; the time is measured (locally 1-12 ms) and the test only fails at a catastrophe threshold, so a
# slow runner does not fail it.
PROBES = [
    (r"[0-9]+(?P<x>[a-z])", "1" * 5000),
    (r"(?P<k>\w+)=(?P<v>\w*)", "a" * 5000),
    (r"(?P<n>[^x,;]+)(?:,e(?P<e>[^x;]+))?(?:;f(?P<f>[^x]+))?x", ";f" + "1" * 5000),
    (r"(?P<n>[Ā-Ȁ̀-Ѐa-zA-Z0-9" + "".join(f"\\x{c:02x}" for c in range(0xC0, 0xD8)) + "]+)!", "Ő" * 5000),
    (r"(?:(?P<a>[^!]+))!", "a" * 5000),
    (r"(?P<a>[^!,]{0,64}),(?P<b>[^!]{0,64})!", "a," * 5000),
    (
        r"^(?P<a>[^!?.,;]+)(?:!(?P<b>[^?.,;]+))?(?:\?(?P<c>[^.,;]+))?(?:\.(?P<d>[^,;]+))?(?:,(?P<e>[^;]+))?;",
        "a!b?c.d,e" * 600,
    ),
]


@pytest.mark.parametrize(("source", "text"), PROBES, ids=range(len(PROBES)))
def test_attack_probes_stay_far_from_a_catastrophe(source, text):
    assert _probe(source, text) < 0.25


def test_the_simulator_agrees_with_re_on_the_probes():
    for source, text in PROBES[:3]:
        ex = compile_extractor(source)
        subject = text[:300]
        parsed = parse(source)
        got, steps = sim.search(parsed, subject)
        assert got == sim.re_result(ex.regex, ex.anchored, ex.groups, subject)
        assert steps <= ex.cost.steps(len(subject), ex.anchored)


def test_classes_tested_range_by_range_weigh_more():
    # re tests a class's ranges above U+FFFF one by one: 32 of them cost ~7 plain tests (review of PR #204)
    astral = "".join(chr(0x20000 + 0x400 * i) + "-" + chr(0x20010 + 0x400 * i) for i in range(32))
    heavy = compile_extractor(f"[{astral}]+(?P<x>!)")
    assert heavy.cost.weight == 9 and heavy.window < 700
    assert compile_extractor(f"[^{astral[:-3]}!]+(?P<x>!)").cost.weight == 9
    bmp = "".join(chr(0x400 + 8 * i) + "-" + chr(0x403 + 8 * i) for i in range(32))
    assert compile_extractor(f"[{bmp}]+(?P<x>!)").cost.weight == 1
    assert compile_extractor(r"(?P<x>[^\U00020000!]+)!").cost.weight == 2
    assert compile_extractor(r"(?P<x>.+)").cost.weight == 1
    assert _probe(f"[{astral}]+(?P<x>!)", chr(0x20000 + 0x400 * 31) * 5000) < 0.25
