"""Properties of the ad hoc extraction grammar (ROADMAP 9.8).

For random regexes of the grammar (and random strings rich in metacharacters) that the validator accepts:

* the reference backtracking matcher (``tests/unit/extract_sim.py``) gives the same match and groups as
  ``re`` on the emitted pattern and as ``re`` (ASCII) on the text the user typed: the reader of the text
  reads it the way ``re`` does, and the emission does not change the language;
* the steps the reference matcher counts never exceed ``Cost.steps`` (on random texts and on texts made
  of runs of the characters the pattern itself takes, the inputs that make a backtracking matcher work
  hardest);
* the emitted pattern is accepted again and writes itself back unchanged;
* the TOML strings of the promotion read back as the same text.

``DMSLOG_EXTRACTOR_EXAMPLES`` raises the number of examples locally (CI: 150).
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "unit"))
import extract_sim as sim  # noqa: E402

from dmslog.parse.extractor import ExtractorError, compile_extractor, contains, parse  # noqa: E402
from dmslog.parse.promote import toml_str  # noqa: E402

EXAMPLES = int(os.environ.get("DMSLOG_EXTRACTOR_EXAMPLES", "150"))
SETTINGS = settings(max_examples=EXAMPLES, deadline=None, suppress_health_check=[HealthCheck.too_slow])

ATOMS = [
    "a", "b", "0", " ", "-", "x", "\u00e9", r"\d", r"\w", r"\s", r"\D", r"\S", r"\W", ".", "[ab]", "[^a]",
    "[^ab0]", "[a-c0-1]", r"\.", r"[\-x]", r"\x41", r"\u00e9", r"[^\n]", r"\n", "[\\]a]", r"\{", "}", "]",
]  # fmt: skip
QUANTS = ["", "", "", "?", "*", "+", "{2}", "{1,3}", "{,2}", "{2,}", "{0,1}"]
ALPHABET = "ab0 -x\u00e9A1.\n]{}"

atom = st.tuples(st.sampled_from(ATOMS), st.sampled_from(QUANTS)).map("".join)


def _group(body: str, kind: int, opt: bool) -> str:
    head = "(?:" if kind == 0 else "(?P<@>"
    return head + body + ")" + ("?" if opt else "")


def _seq(children: list[str]) -> str:
    return "".join(children)


group = st.builds(_group, st.lists(atom, min_size=1, max_size=4).map(_seq), st.integers(0, 1), st.booleans())
items = st.one_of(atom, atom, group)  # groups one level deep; the outer named group makes two


def _name_groups(text: str) -> str:
    out, k = [], 0
    for part in text.split("@"):
        out.append(part)
        out.append(f"g{k}")
        k += 1
    return "".join(out[:-1])


patterns = st.tuples(
    st.booleans(),
    st.lists(items, min_size=1, max_size=5).map(_seq),
    st.sampled_from(["(?P<@>", ""]),
    st.lists(items, min_size=1, max_size=2).map(_seq),
).map(lambda t: _name_groups(("^" if t[0] else "") + (t[2] + t[1] + ")" if t[2] else t[1]) + t[3]))


def _accept(source: str):
    try:
        return compile_extractor(source)
    except ExtractorError:
        return None


def _runs(source: str, data) -> str:
    """A text of runs of characters that the atoms of the pattern take (worst inputs for backtracking)."""
    parsed = parse(source)
    sets = []

    def walk(its):
        for item in its:
            if item[0] in ("set", "rep"):
                sets.append(item[1] if item[0] == "set" else item[3])
            else:
                walk(item[2])

    walk(parsed.items)
    pool = [c for c in ALPHABET if any(contains(cs, ord(c)) for cs in sets)] or list(ALPHABET)
    chunks = data.draw(st.lists(st.tuples(st.sampled_from(pool), st.integers(1, 12)), max_size=6))
    return "".join(c * k for c, k in chunks)


def _check(ex, source: str, text: str) -> None:
    parsed = parse(source)
    got, steps = sim.search(parsed, text)
    assert got == sim.re_result(ex.regex, ex.anchored, ex.groups, text), (source, ex.pattern, text)
    typed = re.compile(source, re.ASCII)
    assert got == sim.re_result(typed, ex.anchored, ex.groups, text), (source, text)
    assert steps <= ex.cost.steps(len(text), ex.anchored), (source, text, steps)


@SETTINGS
@given(patterns, st.text(alphabet=ALPHABET, max_size=30), st.data())
def test_accepted_regexes_match_like_re_within_the_bound(source, text, data):
    ex = _accept(source)
    if ex is None:
        return
    _check(ex, source, text)
    _check(ex, source, _runs(source, data))
    again = compile_extractor(("^" if ex.anchored else "") + ex.pattern)
    assert again.pattern == ex.pattern and again.groups == ex.groups and again.window == ex.window


SEPS = "!#%&,;:=@~"
FIELDS = ["[^{rest}]+", "[^{rest}]*", "[^{rest}]{{1,3}}", r"\d+", r"\d{{0,2}}", "[ab]+"]


@st.composite
def structured(draw):
    """Separated fields (``k=v;`` shapes), some optional: deterministic by construction, often accepted."""
    n = draw(st.integers(1, 5))
    seps = draw(st.permutations(SEPS))[: n + 1]
    out = ["^" if draw(st.booleans()) else draw(st.sampled_from(["", "a", "x"]))]
    optional = 0
    for i in range(n):
        rest = re.escape("".join(seps[i + 1 :]))
        field = draw(st.sampled_from(FIELDS)).format(rest=rest)
        body = f"{re.escape(seps[i])}(?P<g{i}>{field})"
        if optional < 4 and draw(st.booleans()):
            optional += 1
            out.append(f"(?:{body})?")
        else:
            out.append(body)
    out.append(re.escape(seps[n]))
    return "".join(out)


@SETTINGS
@given(structured(), st.data())
def test_structured_regexes_match_like_re_within_the_bound(source, data):
    ex = _accept(source)
    if ex is None:
        return
    alphabet = SEPS + "ab1x"
    _check(ex, source, data.draw(st.text(alphabet=alphabet, max_size=40)))
    _check(ex, source, _runs(source, data))


META = '\\[](){}?*+^$|.-,:=!<>#P0123456789abdDwWsSxuU" \t\n'


@SETTINGS
@given(st.text(alphabet=META, min_size=1, max_size=24), st.text(alphabet=ALPHABET, max_size=24))
def test_strings_the_reader_accepts_mean_the_same_to_re(body, text):
    source = f"(?P<g>{body})"
    ex = _accept(source)
    if ex is None:
        return
    _check(ex, source, text)


@SETTINGS
@given(st.text(max_size=40))
def test_toml_strings_read_back(value):
    if any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        return
    assert tomllib.loads(f"x = {toml_str(value)}")["x"] == value


def test_the_generators_reach_accepted_regexes():
    accepted = 0
    for source in ("(?P<g0>a+)b", "^(?P<g0>[^ab0]*)a(?:b(?P<g1>0+))?x", r"(?P<g0>\d{1,3})\.(?P<g1>\w*)"):
        accepted += _accept(source) is not None
    assert accepted == 3
