"""Property tests for the template engine and signatures (ARQUITETURA 6.3, 6.7 and 13)."""

# ruff: noqa: E501 - real log lines and template regexes are kept on one line, as in the TOML files

from datetime import timedelta

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from dmslog.parse import templates as T  # noqa: E402
from dmslog.parse.signature import signature  # noqa: E402

MAIN = settings(
    max_examples=400,
    deadline=timedelta(seconds=2),
    database=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

LIB = r"""
family = "_common"
[[template]]
code = "GEN-STA-02"
aliases = ["WFM-090", "ONT-030"]
class = "statistics"
regex = '^\[Communication(?P<chan>\d+)\] - \[TOTAL\]\[(?P<side>Client|Service)\] Call count: (?P<calls>\d+)'
fields = { chan = "int", calls = "int" }
[[template]]
code = "GEN-STA-03"
class = "statistics"
regex = '^\[Communication(?P<chan>\d+)\] - By (?P<order>call count|Request Message Size Total):$'
cont = "stats_csv"
[[template]]
code = "GEN-ERR-01"
aliases = ["WFM-050"]
class = "validation"
text = "Detected errors:"
cont = "codes_per_line"
keys = [{ field = "codes", type = "errcode" }]
[[template]]
code = "GEN-EXC-01"
class = "error"
regex = '^Non Fatal Exception (?P<exc_type>[\w.]+): (?P<exc_msg>.*)$'
cont = "dotnet_stack"
[[template]]
code = "GEN-MAP-01"
class = "startup"
regex = '^"(?P<map>\w+)" map (?P<action>closed|is now open|cleared)\.$'
[[template]]
code = "GEN-MAP-02"
class = "other"
regex = '^"(?P<key>[^"]*)" entry added to the "(?P<map>\w+)" map\.$'
[[template]]
code = "GEN-PLD-01"
class = "req_resp"
stream = "sensitive"
regex = '^Message(?: content)?: (?=<)'
payload = { fmt = "xml", start = "match_end", strip_suffix = ".", role = "by_root" }
[[template]]
code = "GEN-CON-01"
class = "other"
regex = '^(?:Connecting to|Successfully connected to) (?P<service>\w+)\.$'
"""

FAM = r"""
family = "AWFM"
extends = ["_common"]
[[template]]
code = "WFM-002"
class = "other"
regex = '^"(?P<map>\w+)" map closed\.$'
[[template]]
code = "WFM-010"
class = "start"
regex = '^(?P<op>[A-Z][\w ]+?) request received\. Request with ID "(?P<request_id>[0-9a-fA-F-]{36})"\.'
fields = { request_id = "guid" }
keys = ["request_id"]
[[template]]
code = "WFM-030"
class = "req_resp"
text = "Callback received:"
cont = { parser = "line_regex", regex = '^(?P<cb_type>\w+) GID: (?P<gid>\d+)' }
fields = { gid = "int" }
[[template]]
code = "WFM-111"
class = "other"
regex = '^(?P<n>\d+) (?P<entity>crews|vehicles) added to insert Delta operation\.$'
fields = { n = "int" }
[[template]]
code = "WFM-123"
class = "req_resp"
regex = '^Request IDs returned by (?P<target>CRS|IMS) are: "(?P<gids>[^"]*)"\.$'
fields = { gids = "list" }
keys = [{ field = "gids", type = "gid" }]
[[template]]
code = "WFM-070"
class = "error"
regex = '^(?:Subscriber\((?P<pubsub>\w+)\) failed to check-in.*|\[ERROR\] - Unable to initialize subscriber: .*)$'
[[template]]
code = "SSS-001"
class = "other"
text = "Adapter is on ACTIVE site."
"""

FILES = {"_common.toml": LIB, "awfm.toml": FAM}
CATALOG = T.load_texts(FILES)
SETS = [CATALOG.for_family("AWFM", sensitive=s) for s in (False, True)] + [
    CATALOG.for_family("UNKNOWN", sensitive=False)
]

SEEDS = [
    "[Communication2] - [TOTAL][Client] Call count: 12",
    "[Communication2] - By call count:",
    "Detected errors:",
    "Non Fatal Exception System.Exception: boom",
    '"OMS_CREW" map closed.',
    '"OMS_CREW" map is now open.',
    '"ABC 1" entry added to the "OMS_CREW" map.',
    "Message content: <ChangedCrews>",
    "Connecting to CRSBusinessService.",
    'Changed crews request received. Request with ID "0b5c1f7e-1111-2222-3333-444455556666".',
    "Callback received:",
    "5 crews added to insert Delta operation.",
    'Request IDs returned by CRS are: "1,22,333".',
    "Subscriber(CrewManagementPubSub) failed to check-in x",
    "[ERROR] - Unable to initialize subscriber: y",
    " Adapter is on ACTIVE site. ",
]
NOISE = st.text(alphabet=st.characters(codec="utf-8", exclude_categories=("Cs",)), max_size=40)


@st.composite
def messages(draw):
    """Known messages, mutated: insertions, deletions, replacements, padding; plus random text."""
    if draw(st.integers(0, 4)) == 0:
        return draw(st.text(max_size=200))
    msg = draw(st.sampled_from(SEEDS))
    for _ in range(draw(st.integers(0, 3))):
        pos = draw(st.integers(0, len(msg)))
        kind = draw(st.integers(0, 2))
        piece = draw(NOISE)
        if kind == 0:
            msg = msg[:pos] + piece + msg[pos:]
        elif kind == 1:
            msg = msg[:pos] + msg[pos + len(piece) :]
        else:
            msg = msg[:pos] + piece + msg[pos + 1 :]
    pad = draw(st.sampled_from(["", " ", "\t\t", " \t "]))
    return pad + msg + draw(st.sampled_from(["", " ", "\r"]))


conts = st.one_of(st.none(), st.text(max_size=300), st.sampled_from(["A1\nB2", "X GID: 12", "  at A.B()\n"]))


def naive(tset, msg):
    """Reference classifier: exact texts first, then every regex template in declaration order."""
    s = msg.strip()
    if "\n" in s:
        s = s[: s.index("\n")]
    if not s:
        return None
    for t in tset.templates:
        if t.text is not None and t.text == s:
            return t.code
    for t in tset.templates:
        if t.regex is not None and len(s) <= t.window and t.regex.match(s):
            return t.code
    return None


@MAIN
@given(messages(), conts)
def test_classify_never_raises_and_matches_the_naive_scan(msg, cont):
    for tset in SETS:
        tm = tset.classify_text(msg, cont)
        assert (tm.code if tm else None) == naive(tset, msg)
        if tm is not None:
            assert 0 < tm.match_end <= len(msg)
            assert isinstance(tm.fields, dict) and all(
                isinstance(k, str) and isinstance(v, str) for k, v in tm.keys
            )
            assert tm.tpl_id == CATALOG.tpl_id(tm.code)


@MAIN
@given(messages(), conts)
def test_classify_is_deterministic(msg, cont):
    tset = SETS[0]
    assert tset.classify_text(msg, cont) == tset.classify_text(msg, cont)


@settings(MAIN, max_examples=300)
@given(st.permutations(sorted(FILES)), st.lists(messages(), min_size=1, max_size=5))
def test_classification_does_not_depend_on_load_order(order, msgs):
    other = T.load_texts({name: FILES[name] for name in order})
    tset = other.for_family("AWFM", sensitive=False)
    for msg in msgs:
        assert tset.classify_text(msg) == SETS[0].classify_text(msg)


@settings(MAIN, max_examples=300)
@given(st.sampled_from([(a, t.code) for t in CATALOG.templates() for a in t.aliases]))
def test_aliases_resolve_to_the_canonical_code(pair):
    alias, code = pair
    assert CATALOG.resolve(alias) == code
    assert CATALOG.template(alias) is CATALOG.template(code)
    assert CATALOG.tpl_id(alias) == CATALOG.tpl_id(code)


OVERLAPPING = ["a", "\\w", ".", "[a-z]", "[^,]", "\\S", "(?:ab)", "[ab]"]
UNBOUNDED = ["+", "*", "{1,}", "{2,}", "+?", "*?"]


@settings(MAIN, max_examples=300)
@given(
    st.sampled_from(OVERLAPPING),
    st.sampled_from(UNBOUNDED),
    st.sampled_from(UNBOUNDED),
    st.sampled_from(["", "\\s?", "b?", "(?:x)?"]),
    st.sampled_from(["^", "^x", "^(?P<g>"]),
)
def test_nested_ambiguous_repeats_are_rejected(atom, q1, q2, tail, head):
    rx = f"{head}(?:{atom}{q1}{tail}){q2}"
    if "(" in head:
        rx += ")"
    with pytest.raises(T.UnsafeRegexError):
        T._compile_regex(rx, src="p", where="t")


regex_pieces = st.sampled_from(
    ["a", "b", "\\d", "\\w", ".", "[^\"]", "(?:", ")", "(?P<f1>", "(?P<f2>", "|", "*", "+", "?", "{2}", "{1,}",
     "\\.", " ", "\"", "^", "$", "(", "[", "\\1", "(?=", "(?i)"]
)  # fmt: skip


@settings(MAIN, max_examples=400)
@given(st.lists(regex_pieces, max_size=14).map("".join))
def test_regex_validation_only_raises_typed_errors(rx):
    try:
        compiled, _ = T._compile_regex(rx, src="p", where="t")
    except T.TemplateError:
        return
    compiled.match("a" * 50 + "b")  # accepted patterns compile and run


@MAIN
@given(st.one_of(st.text(max_size=400), messages()))
def test_signature_is_idempotent_and_deterministic(msg):
    sig = signature(msg)
    assert signature(sig) == sig
    assert signature(msg) == sig
    assert len(sig) <= 240
    assert not any(c.isdigit() for c in sig if c.isascii())


@MAIN
@given(st.sampled_from(SEEDS), st.integers(0, 10**12), st.uuids())
def test_signature_ignores_ids_and_numbers(seed, number, uid):
    a = signature(f"{seed} id {number} guid {uid} '{number}'")
    b = signature(f"{seed} id {number + 7} guid {str(uid).upper()} '{number + 1}'")
    assert a == b


_ATOMS = st.sampled_from(
    ["a", "A", "b", "ab", "\\x41", "\\d", "\\w", "\\S", "\\s", ".", "[ab]", "[^a]", ",", " ",
     "\u0100", "[\u0100-\u0200]", "[^\\W_]", "\\u0101", "[a\u0100]"]
)  # fmt: skip
_QUANT = st.sampled_from(
    ["", "", "?", "*", "+", "{2}", "{3}", "{1,3}", "{2,}", "{1,64}", "{0,8}", "{0,64}", "+?", "*?", "??",
     "{0,64}?", "{1,3}?"]
)  # fmt: skip
_GROUP_QUANT = st.sampled_from(["", "", "", "?", "??", "*", "+", "{2}", "{0,3}"])  # mostly not nested


def _node(children):
    seq = st.lists(children, min_size=1, max_size=3).map("".join)
    group = st.tuples(seq, _GROUP_QUANT).map(lambda t: "(?:" + t[0] + ")" + t[1])
    named = st.tuples(seq, _GROUP_QUANT).map(lambda t: "(?P<g>" + t[0] + ")" + t[1])
    alt = st.tuples(st.lists(children, min_size=2, max_size=3), _GROUP_QUANT).map(
        lambda t: "(?:" + "|".join(t[0]) + ")" + t[1]
    )
    look = st.tuples(st.sampled_from(["(?=", "(?!", "(?<=", "(?<!"]), seq).map(lambda t: t[0] + t[1] + ")")
    return st.one_of(group, named, alt, look)


# Bound by construction (ARQUITETURA 6.3): every regex the validator accepts at a window must, in
# practice, stay far from a catastrophe (0.25 s; ~20-40 ms locally) on texts of that window. The fuzz builds the shapes the review rounds
# used against the static analysis: alternations of lookarounds, nested lookarounds, lookarounds
# after repeats, optional-wrapped alternations, Unicode classes, lazy and chained bounded repeats.
_LOOK_OPEN = st.sampled_from(["(?=", "(?!", "(?<=", "(?<!"])
_REP_ATOM = st.tuples(st.sampled_from(["a", "[ab]", "\\w", ".", "b", "\u0100", "[^\\W_]", " "]), _QUANT).map(
    "".join
)


def _fuzz_node(children):
    seq = st.lists(children, min_size=1, max_size=3).map("".join)
    group = st.tuples(seq, st.sampled_from(["", "", "?", "??"])).map(lambda t: "(?:" + t[0] + ")" + t[1])
    named = seq.map(lambda b: "(?P<g>" + b + ")")
    alt = st.tuples(st.lists(seq, min_size=2, max_size=4), st.sampled_from(["", "?", "??"])).map(
        lambda t: "(?:" + "|".join(t[0]) + ")" + t[1]
    )
    look = st.tuples(_LOOK_OPEN, seq).map(lambda t: t[0] + t[1] + ")")
    # round 5: alternatives that each run a lookaround before their first character
    look_alt = st.tuples(
        _LOOK_OPEN, st.lists(st.tuples(_LOOK_OPEN, seq, st.sampled_from("bcdxyz")), min_size=2, max_size=32)
    ).map(lambda t: t[0] + "(?:" + "|".join(o + b + ")" + c for o, b, c in t[1]) + "))")
    return st.one_of(group, named, alt, look, look_alt)


_FUZZ = st.lists(st.recursive(_REP_ATOM, _fuzz_node, max_leaves=10), min_size=1, max_size=8).map("".join)
_FUZZ_WINDOWS = st.sampled_from([128, 512, T.MAX_MATCH_CHARS, T.MAX_MATCH_CHARS])
# Sizes grow by 2 characters up to 32 (an exponential regex: x4 per step), then by 1.25 (degree d:
# x1.25 ** d per step): a regex let through by a bug stops near _ABORT instead of hanging.
_PROBE_SIZES = (*range(2, 32, 2), *(int(32 * 1.25**k) for k in range(19)))
_ABORT = 0.25  # seconds: CATASTROPHE threshold (5x slower runner than the local ~20-40 ms): fails at once
_BUDGET = 0.25  # seconds: catastrophe threshold, not a speed claim (docs: ~20-40 ms locally)
_UNITS = ("a", "b", "ab", "A", " ", "a ", "\u0100", "a\u0100", "\u0100\u0101", "x", "0")
FUZZ_STATS = {"accepted": [], "worst": (0.0, "", 0)}


def _units(rx: str) -> list[str]:
    """Pump units: generic ones and the regex's own literal characters, alone and in pairs."""
    lits = sorted({c for c in rx if c.isalnum() or c in " ,."} - set("PgwWsSdD"))[:6]
    return [*_UNITS, *lits, *(a + b for a in lits[:3] for b in lits[:3] if a != b)]


def _probe(rx: str, window: int) -> float:
    """Worst time of ``rx`` on pumped texts of growing size up to ``window``; fails at once on a
    probe slower than _ABORT. Returns the worst time at the window itself."""
    import re
    import time

    pat = re.compile(rx)
    worst = 0.0
    for n in (*(n for n in _PROBE_SIZES if n < window), window):
        for unit in _units(rx):
            for text in ((unit * n)[: n - 1] + "!", (unit * n)[:n]):
                t0 = time.perf_counter()
                pat.match(text)
                dt = time.perf_counter() - t0
                assert dt < _ABORT, (rx, window, unit, n, dt)
                if n == window:
                    worst = max(worst, dt)
    return worst


def _assert_fast(rx: str, window: int = T.MAX_MATCH_CHARS) -> None:
    worst = _probe(rx, window)
    assert worst < _BUDGET, (rx, window, worst)


@settings(MAIN, max_examples=2500, deadline=timedelta(seconds=10))
@given(_FUZZ, st.sampled_from(["$", "x", "", "a$"]), _FUZZ_WINDOWS)
def test_accepted_regexes_stay_within_budget_at_their_window(body, tail, window):
    rx = "^" + body + tail
    try:
        T._compile_regex(rx, src="p", where="t", window=window)
    except T.InvalidRegexError:
        return
    worst = _probe(rx, window)
    FUZZ_STATS["accepted"].append(rx)
    FUZZ_STATS["worst"] = max(FUZZ_STATS["worst"], (worst, rx, window))
    assert worst < _BUDGET, (rx, window, worst)


def test_the_fuzz_accepts_a_fair_share_of_regexes():
    """Guard against a fuzz that only produces rejected regexes (and so proves nothing)."""
    accepted = FUZZ_STATS["accepted"]
    if not accepted:
        pytest.skip("run together with test_accepted_regexes_stay_within_budget_at_their_window")
    print(f"accepted {len(accepted)}; worst {FUZZ_STATS['worst'][0] * 1000:.1f} ms", FUZZ_STATS["worst"][1:])
    assert len(accepted) >= 800, len(accepted)
    assert any("?:" in rx and "|" in rx for rx in accepted)
    assert sum("(?=" in rx or "(?!" in rx or "(?<" in rx.replace("(?P<", "") for rx in accepted) >= 50
    assert any(rx.count("(?!") + rx.count("(?=") >= 2 for rx in accepted)
    assert any("?" in rx.replace("(?", "") for rx in accepted)


@pytest.mark.parametrize(
    "rx",
    [
        r"^(?:a|aa)$",
        r"^\S{1,64} \S{1,64}$",
        r"^a?a?a?a?a?$",
        r"^(?=a?a?a?$)a*$",
        r"^[\u0100-\u0200]+\u0100?$",
    ],
)
def test_fuzz_probe_passes_on_safe_regexes(rx):
    T._compile_regex(rx, src="p", where="t")
    _assert_fast(rx)


def test_a_quadratic_regex_is_bounded_by_its_declared_window():
    rx = r"^(?P<a>.+?) - (?P<b>.+)\.$"
    with pytest.raises(T.UnsafeRegexError):
        T._compile_regex(rx, src="p", where="t")
    T._compile_regex(rx, src="p", where="t", window=704)
    _assert_fast(rx, 704)


def test_fuzz_probe_detects_a_slow_regex():
    """The probe itself works: an exponential regex (never accepted) fails it, quickly."""
    import time

    t0 = time.perf_counter()
    with pytest.raises(AssertionError):
        _assert_fast("^(?:a+)+$")  # 2 ** n: aborts on the first probe slower than _ABORT
    assert time.perf_counter() - t0 < 5
