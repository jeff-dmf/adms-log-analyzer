"""Reconstruct the wire XML of a queued message from its decoded body.

The queue body is the .NET object serialized by the ``DataContractSerializer``; the adapter sends the same
object through the ``XmlSerializer``. :func:`reconstruct` rebuilds the second form from the first. The
result is a best effort, never the bytes that were sent: every guess is reported as a stable note code.

Names, order, namespaces, attributes and array layout come from, in this order: the shape learned from the
case's own payloads (``exemplar.py``), the static catalog (``catalog.toml``) and a heuristic. Values are
copied as decoded. All output text goes through ``payload.pretty.format_payload``.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from ..payload.pretty import PrettyResult, format_payload
from .exemplar import Shape
from .nbfx import Node, Text

XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
ARRAYS_NS = "http://schemas.microsoft.com/2003/10/Serialization/Arrays"
SCHEMA_NS = "http://iec.ch/TC57/2011/schema/message"
FIELD = "Field"
SPECIFIED = "FieldSpecified"
MAX_DEPTH = 200
CATALOG_RESOURCE = "catalog.toml"
MAX_NAME = 128  # longest element/attribute/prefix name accepted from data
MAX_OUTPUT_CHARS = 2_000_000  # budget of the serialized text, checked while serializing
_SAFE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.\-]*")
RECONSTRUCTION_LABEL = "Reconstruction, may differ from the bytes sent."

NOTE_CODES = (
    "any_content",
    "array_form_guessed",
    "attributes_unknown",
    "catalog_missing",
    "datetime_format",
    "datetime_local",
    "enum_as_stored",
    "invalid_chars",
    "invalid_name",
    "names_guessed",
    "order_alphabetical",
    "order_partial",
    "payload_namespace_unknown",
    "root_guessed",
    "too_deep",
)

_ITEM_ENUM = re.compile(r"Item\d+")
_BAD_XML_CHARS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")
_ENVELOPE_TYPES = ("Header", "Request", "Reply")


@dataclass(frozen=True)
class MessageEntry:
    type: str
    noun: str
    namespace: str
    payload_namespace: str | None = None
    attributes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Catalog:
    envelope_order: tuple[str, ...] = ()
    header_order: tuple[str, ...] = ()
    flat_arrays: frozenset[str] = frozenset()
    payload_order: tuple[str, ...] = ()
    payload_content_first: bool = False
    cim_names: tuple[str, ...] = ()
    enum_fields: frozenset[str] = frozenset()
    messages: tuple[MessageEntry, ...] = ()

    def message(self, clr_type: str) -> MessageEntry | None:
        for entry in self.messages:
            if entry.type == clr_type:
                return entry
        return None


def load_catalog(path: Path | None = None) -> Catalog:
    """Read ``catalog.toml`` (the bundled one by default)."""
    if path is None:  # packaged resource: also readable inside dmslog.pyz (zipimport)
        raw = resources.files("dmslog.msgqueue").joinpath(CATALOG_RESOURCE).read_text(encoding="utf-8")
    else:
        raw = path.read_text(encoding="utf-8")
    data = tomllib.loads(raw)
    if data.get("schema") != 1:
        raise ValueError("unsupported catalog schema")
    header = data.get("header", {})
    payload = data.get("payload", {})
    return Catalog(
        envelope_order=tuple(data.get("envelope", {}).get("order", ())),
        header_order=tuple(header.get("order", ())),
        flat_arrays=frozenset(header.get("flat_arrays", ())),
        payload_order=tuple(payload.get("order", ())),
        payload_content_first=bool(payload.get("content_first", False)),
        cim_names=tuple(data.get("cim", {}).get("names", ())),
        enum_fields=frozenset(data.get("enums", {}).get("fields", ())),
        messages=tuple(
            MessageEntry(
                m["type"],
                m["noun"],
                m["namespace"],
                m.get("payload_namespace"),
                tuple(m.get("attributes", ())),
            )
            for m in data.get("message", ())
        ),
    )


@dataclass(frozen=True)
class Reconstruction:
    root: Node  # tree in the wire format
    basis: str  # exemplar | catalog | none
    exemplars: int  # payloads of the case used as exemplar
    notes: tuple[str, ...]  # stable codes, sorted


def safe_name(name: str) -> bool:
    """True for a plain XML name (ASCII NCName, bounded): safe as an element/attribute/prefix name."""
    return len(name) <= MAX_NAME and _SAFE_NAME.fullmatch(name) is not None


def _fold(name: str) -> str:
    return name[:1].lower() + name[1:]


def _text_of(node: Node) -> str:
    return "".join(c.value for c in node.children if isinstance(c, Text))


def _elements(node: Node) -> list[Node]:
    return [c for c in node.children if isinstance(c, Node)]


def _find(node: Node, name: str) -> Node | None:
    for c in _elements(node):
        if c.name == name:
            return c
    return None


class _Builder:
    def __init__(self, catalog: Catalog, shape: Shape | None, entry: MessageEntry | None, msg_ns: str):
        self.catalog = catalog
        self.shape = shape
        self.entry = entry
        self.msg_ns = msg_ns
        self.notes: set[str] = set()

    # -- lookups ---------------------------------------------------------------------------------------

    def info(self, path: tuple[str, ...]):
        return self.shape.get(path) if self.shape is not None else None

    @staticmethod
    def _scope(scope: dict[str, str], node: Node) -> dict[str, str]:
        return {**scope, **dict(node.xmlns)} if node.xmlns else scope

    @staticmethod
    def _is_nil(node: Node, scope: dict[str, str]) -> bool:
        return any(
            name == "nil" and value.strip() in ("true", "1") and scope.get(prefix) == XSI_NS
            for prefix, name, value in node.attrs
        )

    def _structural_names(self, path: tuple[str, ...]) -> tuple[str, ...]:
        if path == ():
            return self.catalog.envelope_order
        if path == ("Header",):
            return self.catalog.header_order
        if path == ("Payload",):
            return self.catalog.payload_order
        return ()

    def _resolve_name(self, base: str, parent: tuple[str, ...]) -> str:
        """Wire name of the member ``base`` (the CLR field name without the ``Field`` suffix)."""
        info = self.info(parent)
        if info is not None:
            for known in (*info.children, *info.attrs):
                if _fold(known) == _fold(base):
                    return known
        for known in self._structural_names(parent):
            if _fold(known) == _fold(base):
                return known
        for known in self.catalog.cim_names:
            if _fold(known) == _fold(base):
                if known != base:
                    self.notes.add("names_guessed")
                return known
        if base[:1].islower():
            self.notes.add("names_guessed")
        return base

    # -- namespaces ------------------------------------------------------------------------------------

    @staticmethod
    def _child_role(parent: tuple[str, ...], name: str, catalog: Catalog) -> str:
        if parent == ():
            return "msg"
        if len(parent) == 1 and parent[0] in _ENVELOPE_TYPES:
            return "schema"
        if parent == ("Payload",):
            return "fixed" if name in catalog.payload_order else "content"
        return "inherit"

    def _namespace(self, path: tuple[str, ...], parent_ns: str, role: str) -> str:
        info = self.info(path)
        if info is not None:
            return info.namespace
        if role in ("msg", "fixed"):
            return self.msg_ns
        if role == "schema":
            return SCHEMA_NS
        if role == "content":
            if self.entry is not None and self.entry.payload_namespace:
                return self.entry.payload_namespace
            self.notes.add("payload_namespace_unknown")
            return ""
        return parent_ns

    # -- building --------------------------------------------------------------------------------------

    def build(
        self,
        src: Node,
        scope: dict[str, str],
        name: str,
        path: tuple[str, ...],
        ns: str,
        parent_ns: str | None,
        member: str,
        depth: int,
    ) -> Node:
        xmlns = (("", ns),) if ns != (parent_ns or "") else ()
        if not _elements(src):
            return Node(name, "", xmlns, (), self._leaf_children(src, path, member))
        if depth >= MAX_DEPTH:
            self.notes.add("too_deep")
            return Node(name, "", xmlns, (), ())
        kids, attrs = self._children(src, scope, path, ns, depth)
        return Node(name, "", xmlns, attrs, tuple(kids))

    def _leaf_children(self, src: Node, path: tuple[str, ...], member: str) -> tuple[Text, ...]:
        texts = [c for c in src.children if isinstance(c, Text)]
        if not texts:
            return ()
        value = "".join(t.value for t in texts)
        kind = texts[0].kind
        if _BAD_XML_CHARS.search(value):
            value = _BAD_XML_CHARS.sub("\ufffd", value)
            self.notes.add("invalid_chars")
        if kind == "datetime":
            if not value.endswith("Z"):
                self.notes.add("datetime_local")
            info = self.info(path)
            if info is not None and info.date_only and len(value) > 10:
                value = value[:10]
                self.notes.add("datetime_format")
        if kind == "chars" and (member in self.catalog.enum_fields or _ITEM_ENUM.fullmatch(value)):
            self.notes.add("enum_as_stored")
        return (Text(value, kind),)

    def _array_items(self, node: Node, scope: dict[str, str]) -> tuple[list[Node], bool] | None:
        items = _elements(node)
        if not items:
            return None
        if any(isinstance(c, Text) and c.value.strip() for c in node.children):
            return None
        if any(i.name.endswith(FIELD) for i in items) or len({i.name for i in items}) != 1:
            return None
        first = items[0]
        item_scope = self._scope(scope, first)
        primitive = item_scope.get(first.prefix, "") == ARRAYS_NS
        return items, primitive

    def _any_content(self, node: Node) -> list[Node]:
        self.notes.add("any_content")
        out = []
        for c in node.children:
            if isinstance(c, Node):
                if _tree_is_safe(c):
                    out.append(c)
                else:
                    self.notes.add("invalid_name")
        return out

    def _children(
        self, src: Node, scope: dict[str, str], path: tuple[str, ...], ns: str, depth: int
    ) -> tuple[list[Node], tuple[tuple[str, str, str], ...]]:
        kids = _elements(src)
        specified = {
            c.name[: -len("Specified")]: _text_of(c).strip().lower() in ("true", "1")
            for c in kids
            if c.name.endswith(SPECIFIED)
        }
        out: list[Node] = []
        for c in kids:
            if c.name.endswith(SPECIFIED):
                continue
            cscope = self._scope(scope, c)
            if self._is_nil(c, cscope) or specified.get(c.name) is False:
                continue
            is_member = c.name.endswith(FIELD) and len(c.name) > len(FIELD)
            base = c.name[: -len(FIELD)] if is_member else c.name
            if is_member and base in ("any", "anyAttr"):
                out.extend(self._any_content(c))
                continue
            name = self._resolve_name(base, path) if is_member else base
            if not safe_name(name):
                self.notes.add("invalid_name")
                continue
            out.extend(self._member(c, cscope, name, base, path, ns, depth))
        return self._split_attrs(self._order(out, path), path)

    def _member(
        self,
        c: Node,
        cscope: dict[str, str],
        name: str,
        base: str,
        path: tuple[str, ...],
        parent_ns: str,
        depth: int,
    ) -> list[Node]:
        cpath = (*path, name)
        role = self._child_role(path, name, self.catalog)
        ns = self._namespace(cpath, parent_ns, role)
        array = self._array_items(c, cscope)
        if array is None:
            return [self.build(c, cscope, name, cpath, ns, parent_ns, base, depth + 1)]
        items, primitive = array
        item_name = items[0].name
        items = [i for i in items if not self._is_nil(i, self._scope(cscope, i))]
        parent_info = self.info(path)
        own_info = self.info(cpath)
        if parent_info is not None and name in parent_info.flat:
            flat = True
        elif own_info is not None and own_info.item:
            flat, item_name = False, own_info.item
        elif name in self.catalog.flat_arrays or primitive:
            flat = True
        else:
            flat = False
            self.notes.add("array_form_guessed")
        if flat:
            return [
                self.build(i, self._scope(cscope, i), name, cpath, ns, parent_ns, base, depth + 1)
                for i in items
            ]
        if not safe_name(item_name):
            self.notes.add("invalid_name")
            return []
        ipath = (*cpath, item_name)
        ins = self._namespace(ipath, ns, "inherit")
        built = [
            self.build(i, self._scope(cscope, i), item_name, ipath, ins, ns, base, depth + 2) for i in items
        ]
        xmlns = (("", ns),) if ns != parent_ns else ()
        return [Node(name, "", xmlns, (), tuple(built))]

    # -- order and attributes -------------------------------------------------------------------------

    def _order(self, nodes: list[Node], path: tuple[str, ...]) -> list[Node]:
        if len({n.name for n in nodes}) < 2:
            return nodes
        info = self.info(path)
        ref: tuple[str, ...] | None = tuple(info.children) if info is not None and info.children else None
        content_first = False
        if ref is None:
            ref = self._structural_names(path) or None
            content_first = path == ("Payload",) and self.catalog.payload_content_first
        if ref is None:
            self.notes.add("order_alphabetical")
            return sorted(nodes, key=lambda n: n.name)
        pos = {name: i for i, name in enumerate(ref)}
        unknown = -1 if content_first else len(ref)
        if not content_first and any(n.name not in pos for n in nodes):
            if any(n.name in pos for n in nodes):
                self.notes.add("order_partial")
            else:
                self.notes.add("order_alphabetical")
                return sorted(nodes, key=lambda n: n.name)
        return sorted(nodes, key=lambda n: pos.get(n.name, unknown))

    def _split_attrs(
        self, nodes: list[Node], path: tuple[str, ...]
    ) -> tuple[list[Node], tuple[tuple[str, str, str], ...]]:
        info = self.info(path)
        if info is None or not info.attrs:
            return nodes, ()
        attrs: list[tuple[str, str, str]] = []
        rest: list[Node] = []
        for n in nodes:
            leaf = all(isinstance(c, Text) for c in n.children) and len(n.children) <= 1
            if leaf and n.name in info.attrs:
                attrs.append(("", n.name, n.children[0].value if n.children else ""))
            else:
                rest.append(n)
        return rest, tuple(attrs)


def _fallback_root(decoded: Node) -> str:
    name = decoded.name.removesuffix("Type")
    return name if safe_name(name) else "Message"


def _tree_is_safe(node: Node) -> bool:
    stack: list[Node] = [node]
    while stack:
        n = stack.pop()
        if not safe_name(n.name) or (n.prefix and not safe_name(n.prefix)):
            return False
        if any(p and not safe_name(p) for p, _ in n.xmlns):
            return False
        if any(not safe_name(a) or (p and not safe_name(p)) for p, a, _ in n.attrs):
            return False
        stack.extend(c for c in n.children if isinstance(c, Node))
    return True


def _root_name(decoded: Node, notes: set[str]) -> tuple[str, str | None]:
    header = _find(decoded, "headerField")
    verb = noun = ""
    if header is not None:
        v, n = _find(header, "verbField"), _find(header, "nounField")
        verb = _text_of(v).strip() if v is not None else ""
        noun = _text_of(n).strip() if n is not None else ""
    if verb and noun:
        name = verb[:1].upper() + verb[1:] + noun
        if safe_name(name):
            return name, noun
        notes.add("invalid_name")
        notes.add("root_guessed")
        return _fallback_root(decoded), None
    notes.add("root_guessed")
    if noun and not safe_name(noun):
        noun = ""
    return _fallback_root(decoded), noun or None


def reconstruct(decoded: Node, catalog: Catalog, shape: Shape | None) -> Reconstruction:
    """Rebuild the wire-format tree of ``decoded`` (the tree ``nbfx.decode`` returns)."""
    notes: set[str] = set()
    root_name, noun = _root_name(decoded, notes)
    entry = catalog.message(decoded.name)
    root_info = shape.get(()) if shape is not None else None
    if root_info is not None:
        msg_ns = root_info.namespace
    elif entry is not None:
        msg_ns = entry.namespace
    else:
        notes.add("catalog_missing")
        noun = noun or decoded.name.removesuffix("EventMessageType").removesuffix("Type")
        msg_ns = f"http://iec.ch/TC57/2011/{noun}Message" if noun else ""
    builder = _Builder(catalog, shape, entry, msg_ns)
    scope = dict(decoded.xmlns)
    root = builder.build(decoded, scope, root_name, (), msg_ns, None, "", 0)
    notes |= builder.notes
    exemplars = shape.count if shape is not None else 0
    if exemplars:
        basis = "exemplar"
    elif entry is not None:
        basis = "catalog"
    else:
        basis = "none"
    if entry is not None and entry.attributes and basis != "exemplar":
        notes.add("attributes_unknown")
    return Reconstruction(root, basis, exemplars, tuple(sorted(notes)))


# -- serialization -----------------------------------------------------------------------------------------


def _escape(text: str, quote: bool = False) -> str:
    text = _BAD_XML_CHARS.sub("\ufffd", text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text.replace('"', "&quot;") if quote else text


class OutputTooLarge(ValueError):
    """The serialized reconstruction would exceed the output budget."""


def serialize(root: Node, max_chars: int = MAX_OUTPUT_CHARS) -> str:
    """XML 1.0 text of a tree, escaped, without an XML declaration (iterative, linear).

    The size is counted while writing; ``OutputTooLarge`` is raised as soon as it passes ``max_chars``.
    """
    out: list[str] = []
    size = 0
    stack: list[object] = [root]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            piece = item
        elif isinstance(item, Text):
            piece = _escape(item.value)
        else:
            assert isinstance(item, Node)
            qname = f"{item.prefix}:{item.name}" if item.prefix else item.name
            parts = [qname]
            for prefix, uri in item.xmlns:
                parts.append(
                    f'xmlns:{prefix}="{_escape(uri, True)}"' if prefix else f'xmlns="{_escape(uri, True)}"'
                )
            for prefix, name, value in item.attrs:
                parts.append(f'{prefix + ":" if prefix else ""}{name}="{_escape(value, True)}"')
            if not item.children:
                piece = "<" + " ".join(parts) + "/>"
            else:
                piece = "<" + " ".join(parts) + ">"
                stack.append(f"</{qname}>")
                stack.extend(reversed(item.children))
        size += len(piece)
        if size > max_chars:
            raise OutputTooLarge
        out.append(piece)
    return "".join(out)


def format_reconstruction(rec: Reconstruction, max_chars: int = MAX_OUTPUT_CHARS) -> PrettyResult:
    """Indented, well-formedness checked XML of the reconstruction (always through ``format_payload``).

    Over the output budget the result is not ok (``reason = "too_large"``) and carries no text.
    """
    try:
        text = serialize(rec.root, max_chars)
    except OutputTooLarge:
        return PrettyResult("", False, "xml", "too_large", True)
    return format_payload(text, "xml")
