"""Property tests for the payload keys (issue #67): the tokenizer agrees with an ElementTree oracle.

Synthetic data only: random element trees over the vocabulary of the adapters' messages, with random
identifiers, namespace prefixes, comments and blanks; and random text that must never break the scan.
"""

import re
import xml.etree.ElementTree as ET

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from dmslog.parse import payload as pp  # noqa: E402

SETTINGS = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])

CONTAINERS = sorted(pp.MRID_PARENTS | {"Header", "Payload", "Equipments", "UsagePoint", "Zones", "Other"})
LEAVES = ["mRID", "MessageID", "CorrelationID", "name", "CrewId", "IncidentId", "ServiceAssignmentId"]
TEXT = st.text(alphabet="AB z09-_ &\t", min_size=0, max_size=12)
GID = st.one_of(
    st.integers(min_value=0, max_value=2**64 - 1).map(lambda n: f"0x{n:016x}"),
    st.integers(min_value=0, max_value=10**20 - 1).map(str),
    st.sampled_from(["", "x1", "0x", "-3"]),
)


NOTES = st.sampled_from([None, "a>b", "x'>y", '">"', "&gt;"])


def esc(value: str, refs: bool = False) -> str:
    """Escape for XML; with ``refs`` every letter or digit becomes a character reference (decimal with
    leading zeros or hex), which the parser must decode before validating the value."""
    plain = {"&": "&amp;", "<": "&lt;", '"': "&quot;"}
    return "".join(
        (
            (f"&#{ord(ch):07d};" if i % 2 else f"&#x{ord(ch):x};")
            if refs and ch.isalnum()
            else plain.get(ch, ch)
        )
        for i, ch in enumerate(value)
    )


@st.composite
def trees(draw, depth=0):
    """``(name, children | leaf text, attrs)``."""
    if depth >= 4 or draw(st.integers(0, 3)) == 0:
        kind = draw(st.sampled_from(["leaf", "leaf", "id", "property"]))
        if kind == "leaf":
            name = draw(st.sampled_from(LEAVES))
            value = draw(GID) if name in pp.GID_LEAVES else draw(TEXT)
            return (name, value, draw(NOTES))
        if kind == "id":
            return ("id", None, {"gid": draw(GID), "type": "1"})
        prop = draw(st.sampled_from([*pp.PROPERTY_MRIDS, "SM_OTHER"]))
        return ("Property", None, {"id": prop, "value": draw(TEXT)})
    name = draw(st.sampled_from([*CONTAINERS, "ResourceDescription"]))
    children = draw(st.lists(trees(depth=depth + 1), min_size=0, max_size=4))
    return (name, children, None)


def render(node, prefix: bool, comments: bool, refs: bool = False) -> str:
    name, body, attrs = node
    tag = f"p:{name}" if prefix else name
    if isinstance(body, str):  # a leaf, maybe with a note attribute holding '>' or quotes
        note = "" if attrs is None else f' note="{esc(attrs)}"'
        return f"<{tag}{note}>{esc(body, refs)}</{tag}>"
    if attrs is not None:
        inner = " ".join(f'{k}="{esc(v, refs and k != "id")}"' for k, v in attrs.items())
        return f"<{tag} {inner}/>"
    note = "<!-- <mRID>hidden</mRID> -->" if comments else ""
    return f"<{tag}>{note}\n  " + "\n  ".join(render(c, prefix, comments, refs) for c in body) + f"\n</{tag}>"


def ident(raw: str):
    value = raw.strip()
    if not value or len(value) > pp.MAX_ID_CHARS or any(ch < " " for ch in value):
        return None
    return value


def gid(raw: str):
    value = raw.strip()
    if re.fullmatch(r"0[xX][0-9a-fA-F]{1,16}", value):
        n = int(value[2:], 16)
    elif re.fullmatch(r"[0-9]{1,20}", value):
        n = int(value)
    else:
        return None
    return str(n) if n else None


def oracle(xml: str) -> list[tuple[str, str]]:
    root = ET.fromstring(xml)
    parent = {child: el for el in root.iter() for child in el}
    out: dict[tuple[str, str], None] = {}

    def local(el):
        return el.tag.rsplit("}", 1)[-1]

    for el in root.iter():
        name = local(el)
        up = local(parent[el]) if el in parent else None
        found: list[tuple[str, str | None]] = []
        if len(el) == 0 and el.text is not None and name not in ("id", "Property"):
            if name == "mRID" and up in pp.MRID_PARENTS:
                types = ("mrid", "vehicle") if up == "Vehicle" else ("mrid",)
                found = [(t, ident(el.text)) for t in types]
            elif name in ("MessageID", "CorrelationID") and up == "Header":
                found = [("msgid", ident(el.text))]
            elif name in pp.GID_LEAVES:
                found = [(t, gid(el.text)) for t in pp.GID_LEAVES[name]]
        elif name == "id" and up == "ResourceDescription":
            found = [("gid", gid(el.attrib.get("gid", "")))]
        elif name == "Property" and el.attrib.get("id") in pp.PROPERTY_MRIDS:
            found = [("mrid", ident(el.attrib.get("value", "")))]
        for key_type, value in found:
            if value is not None:
                out.setdefault((key_type, value), None)
    return list(out)


HIDDEN_PI = "<?p > <Crew><mRID>HIDDEN</mRID></Crew> ?>"  # ElementTree never sees this element


@SETTINGS
@given(tree=trees(), prefix=st.booleans(), comments=st.booleans(), refs=st.booleans(), pi=st.booleans())
def test_keys_agree_with_an_element_tree_oracle(tree, prefix, comments, refs, pi):
    body = render(("Root", [tree], None), prefix, comments, refs)
    xml = body.replace("<p:Root>", '<p:Root xmlns:p="urn:example">', 1) if prefix else body
    if pi:
        xml = HIDDEN_PI + xml
    expected = oracle(xml)[: pp.MAX_KEYS]
    assert list(pp.payload_keys(xml, 0, len(xml))) == expected


@SETTINGS
@given(text=st.text(alphabet="<>/!?-[]\"'= abcdmRIDgx0&;:\n", max_size=400))
def test_any_text_gives_valid_keys_and_never_raises(text):
    got = pp.payload_keys(text, 0, len(text))
    assert isinstance(got, tuple) and len(got) <= pp.MAX_KEYS
    assert len(set(got)) == len(got)
    for key_type, value in got:
        assert key_type in {"msgid", "mrid", "vehicle", "gid", "incident_gid"}
        assert value and value == value.strip() and len(value) <= pp.MAX_ID_CHARS
        assert "<" not in value and not any(ch < " " for ch in value)
