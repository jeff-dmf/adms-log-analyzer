"""Read-only text of sources that are listed but not indexed (docs/notas/rt-r3-r4-desenho.md, 3.2).

``read_source_text`` reads one skipped source on demand (``.config``, ``.xml``, ``.csc``, ...) from
the zip or folder registered in the case, through ``SourceReader``, and returns its text with the
values of credentials hidden by default.

* Nothing is written: no database row, no FTS entry, no cache, no export. The connection is only
  used for ``SELECT``.
* The file content is untrusted data. There is no XML parser (no DTD, no entities): the hiding rules
  are regular expressions over the text.
* Only extensions of a closed list are viewable, up to ``MAX_TEXT_BYTES`` decoded bytes; a NUL byte in
  the first 8 KiB means a binary file.
* Hiding rules (the name of a credential is tested by ``is_credential_name``); each only collects spans
  of the original text, which are merged and replaced in one linear pass:
    1. ``name=value`` pairs anywhere (connection strings), bare or quoted (doubled quotes are escapes);
    2. XML attributes with a credential name (any quote kind inside, multiline values);
    3. start tags with a credential ``key=`` / ``name=`` attribute: the ``value`` attribute (``<add>``);
    4. elements with simple text (text and CDATA, whitespace around it) with a credential name or a
       credential ``key`` / ``name`` attribute;
    5. JSON string members with a credential name (escapes in the key are decoded first);
    6. ``name = value`` and ``name: value`` lines (``.ini``, ``.init``, ``.csc``, ``.sql``).
  Connection-string attributes and elements are not hidden whole, only their password pairs (rule 1).
  Hosts, accounts and addresses are not hidden.
* Files of folder and loose-file collections are read only when the path, resolved, is the registered
  one: a symlink (or junction) on the file or any folder of the member path is refused.
"""

from __future__ import annotations

import functools
import json
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from dmslog.ingest.discover import SourceInfo
from dmslog.ingest.reader import ReadLimitError, ReadLimits, SourceReader

MAX_TEXT_BYTES = 4 * 1024 * 1024
BINARY_PROBE_BYTES = 8 * 1024
MASK = "\u2022" * 4

# Closed list of viewable extensions (lowercase, with the dot).
TEXT_EXTENSIONS = frozenset(
    {
        ".xml",
        ".xsd",
        ".xsl",
        ".xslt",
        ".xmlold",
        ".config",
        ".csc",
        ".sql",
        ".init",
        ".ini",
        ".html",
        ".htm",
        ".old",
        ".example",
        ".csv",
        ".json",
    }
)
# Reasons stored in ``source.error`` of skipped sources that can be viewed (pipeline.SKIP_NOT_INDEXABLE).
VIEWABLE_REASON = "not_indexable"


# ---------------------------------------------------------------------- errors


class FileTextError(Exception):
    """Base of the errors of ``read_source_text``. ``code`` is the API error code (409, 413, ...)."""

    diag_safe = True  # fixed words and numbers only
    code = "file_text_error"
    status = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)


class SourceNotFoundError(FileTextError):
    code = "source_not_found"
    status = 404


class NotViewableError(FileTextError):
    """Indexed source, or a source whose extension is outside the closed list."""

    code = "not_viewable"
    status = 409


class FileTooLargeError(FileTextError):
    code = "file_too_large"
    status = 413

    def __init__(self, limit: int) -> None:
        super().__init__(f"file larger than {limit} bytes")
        self.limit = limit


class BinaryContentError(FileTextError):
    code = "binary_content"
    status = 422


class ContainerMissingError(FileTextError):
    """The zip, folder or file of the collection (or the member inside it) is not available."""

    code = "container_missing"
    status = 410


# ---------------------------------------------------------------------- result and limits


@dataclass(frozen=True)
class FileTextLimits:
    max_bytes: int = MAX_TEXT_BYTES
    read_limits: ReadLimits | None = None


@dataclass(frozen=True)
class FileText:
    source_id: int
    name: str
    member: str
    kind: str  # lowercase extension without the dot ("config", "xml")
    size: int  # bytes of the decoded stream (UTF-8 for UTF-16 files)
    encoding: str  # "utf-8" | "utf-16" | "latin-1"
    lines: int
    hidden: int  # values hidden by default (also reported when ``revealed``)
    revealed: bool
    text: str  # original text when ``revealed``, else with the values masked


# ---------------------------------------------------------------------- hiding rules

_SPLIT_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_CRED_TOKENS = frozenset(
    {
        "pass",
        "password",
        "passwords",
        "passwd",
        "passphrase",
        "pwd",
        "secret",
        "secrets",
        "token",
        "tokens",
        "apikey",
        "credential",
        "credentials",
        "privatekey",
        "sharedkey",
        "clientsecret",
    }
)
_CRED_SUFFIXES = ("password", "passwd", "pwd", "secret", "apikey", "privatekey", "sharedkey")
_CRED_PAIRS = frozenset({("api", "key"), ("private", "key"), ("shared", "key"), ("client", "secret")})
_CONN_NAMES = frozenset({"connectionstring", "connectionstrings", "connstr", "connectionstringname"})


def _tokens(name: str) -> list[str]:
    return [t for t in _NON_ALNUM.split(_SPLIT_CAMEL.sub("_", name).lower()) if t]


def _is_connection_string_name(name: str) -> bool:
    toks = _tokens(name)
    joined = "".join(toks)
    return joined in _CONN_NAMES or ("connection" in toks and "string" in toks)


@functools.lru_cache(maxsize=8192)
def is_credential_name(name: str) -> bool:
    """True when ``name`` (an attribute, element, key or setting name) names a credential.

    Names are split at case changes and punctuation; ``pass`` / ``password`` / ``pwd`` / ``secret`` /
    ``token`` / ``credential`` and key pairs match as whole words (``DbPassword`` and ``api-key`` do,
    ``compass``, ``bypass`` and ``tokenizer`` do not). Connection-string names are not credential names
    here: only the password pairs inside them are hidden.
    """
    if _is_connection_string_name(name):
        return False
    toks = _tokens(name)
    if "".join(toks) in _CRED_TOKENS:
        return True
    for i, tok in enumerate(toks):
        if tok in _CRED_TOKENS or (len(tok) > 3 and tok.endswith(_CRED_SUFFIXES)):
            return True
        if i + 1 < len(toks) and (tok, toks[i + 1]) in _CRED_PAIRS:
            return True
    return False


_ATTR_RE = re.compile(
    r"""(?<![\w:.\-])(?P<name>[\w:.\-]+)\s*=\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)')""",
)
# A start tag with at most 64 well-formed attributes (the bound keeps hostile input linear).
_OPEN_TAG_RE = re.compile(
    r"""<(?P<tag>[\w:.\-]+)(?P<attrs>(?:\s+[\w:.\-]+\s*=\s*(?:"[^"]*"|'[^']*')){0,64})\s*(?P<end>/?)>""",
)
_CLOSE_TAG_RE = re.compile(r"</(?P<tag>[\w:.\-]+)\s*>")
_PAIR_NAME_RE = re.compile(r"(?<![\w.\-])(?P<name>[\w.\-]+)[ \t]*=[ \t]*")
_UNQUOTED_RE = re.compile(r"""[^;"'<>\r\n]*""")
_JSON_RE = re.compile(
    r'"(?P<key>(?:[^"\\\r\n]|\\.)*)"[ \t\r\n]*:[ \t\r\n]*"(?P<val>(?:[^"\\\r\n]|\\.)*)"',
)
_LINE_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<name>[A-Za-z_][\w.\- \t]{0,100}?)(?P<sep>[ \t]*[=:][ \t]*)(?P<val>[^\r\n]+)$",
    re.MULTILINE,
)
_HINT_RE = re.compile(r"pass|pwd|secret|token|credential|key", re.IGNORECASE)
_KEY_ATTRS = ("key", "name")
_CDATA_OPEN = "<![CDATA["
Span = tuple[int, int]


def _cdata_sections(text: str) -> dict[int, int]:
    """Start -> end of every CDATA section, in one pass (-1 for an unterminated one: it runs to the end)."""
    out: dict[int, int] = {}
    pos = 0
    while (i := text.find(_CDATA_OPEN, pos)) != -1:
        k = text.find("]]>", i + len(_CDATA_OPEN))
        if k == -1:
            out[i] = -1
            break
        out[i] = k + 3
        pos = k + 3
    return out


def _pair_value_span(text: str, start: int) -> Span | None:
    """Value after ``name=``: quoted (doubled quotes are escapes) up to the real closing quote, or bare."""
    n = len(text)
    if start >= n:
        return None
    q = text[start]
    if q in "\"'":
        i = start + 1
        while True:
            k = text.find(q, i)
            if k == -1:  # unterminated: hide up to the end of the line
                nl = text.find("\n", start)
                return (start + 1, n if nl == -1 else nl)
            if k + 1 < n and text[k + 1] == q:
                i = k + 2
                continue
            return (start + 1, k)
    m = _UNQUOTED_RE.match(text, start)
    return (start, m.end()) if m else None


def _element_text_span(text: str, start: int, tag: str, cdata: dict[int, int]) -> Span | None:
    """Span of the text of ``<tag>...</tag>`` (text and CDATA only, whitespace around it excluded)."""
    i = start
    while True:
        j = text.find("<", i)
        if j == -1:
            return None
        if text.startswith(_CDATA_OPEN, j):
            end = cdata.get(j, -1)
            if end == -1:
                return None
            i = end
            continue
        close = _CLOSE_TAG_RE.match(text, j)
        if close is None or close.group("tag") != tag:
            return None
        region = text[start:j]
        lead = len(region) - len(region.lstrip())
        trail = len(region) - len(region.rstrip())
        return (start + lead, j - trail)


def _json_unescape(raw: str) -> str:
    if "\\" not in raw:
        return raw
    try:
        return json.loads(f'"{raw}"')
    except ValueError:
        return raw


def _collect_spans(text: str) -> list[Span]:
    spans: list[Span] = []
    # 1. name=value pairs (connection strings, anywhere): any credential name, quoted or bare
    covered = 0  # pairs that start inside a value already hidden are skipped (keeps the pass linear)
    for m in _PAIR_NAME_RE.finditer(text):
        if m.start() >= covered and is_credential_name(m.group("name")):
            span = _pair_value_span(text, m.end())
            if span:
                spans.append(span)
                covered = max(covered, span[1])
    # 2. XML attributes with a credential name (any quote kind inside, multiline values)
    for m in _ATTR_RE.finditer(text):
        if is_credential_name(m.group("name")):
            spans.append(m.span("dq") if m.group("dq") is not None else m.span("sq"))
    # 3. start tags: <add key=".." value=".."> and elements with simple text
    cdata = _cdata_sections(text)
    for m in _OPEN_TAG_RE.finditer(text):
        attrs = m.group("attrs")
        cred_key = False
        value_span: Span | None = None
        for a in _ATTR_RE.finditer(text, m.start("attrs"), m.end("attrs")):
            name = a.group("name").lower()
            inner = a.span("dq") if a.group("dq") is not None else a.span("sq")
            if name in _KEY_ATTRS and is_credential_name(text[inner[0] : inner[1]]):
                cred_key = True
            elif name == "value":
                value_span = inner
        if cred_key and value_span:
            spans.append(value_span)
        if not m.group("end") and attrs is not None and (cred_key or is_credential_name(m.group("tag"))):
            span = _element_text_span(text, m.end(), m.group("tag"), cdata)
            if span:
                spans.append(span)
    # 4. JSON string members (escapes in the key are decoded before the name is classified)
    for m in _JSON_RE.finditer(text):
        if is_credential_name(_json_unescape(m.group("key"))):
            spans.append(m.span("val"))
    # 5. "name = value" / "name: value" lines
    for m in _LINE_RE.finditer(text):
        if is_credential_name(m.group("name")):
            start, end = m.span("val")
            val = m.group("val")
            spans.append((start, end - (len(val) - len(val.rstrip()))))
    return spans


_NONBLANK_RE = re.compile(r"\S")


def _worth_hiding(text: str, a: int, b: int) -> bool:
    """False for an empty or blank span, or one that is already the mask (no slicing of long spans)."""
    if b <= a:
        return False
    m = _NONBLANK_RE.search(text, a, b)
    if m is None:
        return False
    return not (b - a <= 2 * len(MASK) and text[a:b].strip() == MASK)


def hide_credentials(text: str) -> tuple[str, int]:
    """``text`` with credential values replaced by ``MASK``, and how many values were hidden.

    Every rule only collects spans of the original text; overlapping spans are merged and replaced in
    one pass, so a value found by two rules is counted once and the cost stays linear.
    """
    if not _HINT_RE.search(text):
        return text, 0
    spans = sorted((a, b) for a, b in _collect_spans(text) if _worth_hiding(text, a, b))
    if not spans:
        return text, 0
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out: list[str] = []
    pos = 0
    for a, b in merged:
        out.append(text[pos:a])
        out.append(MASK)
        pos = b
    out.append(text[pos:])
    return "".join(out), len(merged)


# ---------------------------------------------------------------------- reading


def _decode(data: bytes, utf16: bool) -> tuple[str, str]:
    """Decode ``data`` (UTF-8 stream; BOM-less UTF-8, or the transcoding of a UTF-16 file) without failing."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return data.decode("utf-8"), "utf-16" if utf16 else "utf-8"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _info_from_rows(kind: str, coll_path: str, member: str, file_name: str, size: int) -> SourceInfo:
    parts = member.split("/")
    if kind == "folder" and (".." in parts or Path(member).is_absolute()):
        raise ContainerMissingError("invalid member path")
    return SourceInfo(
        container=Path(coll_path),
        container_kind="zip" if kind == "ers_zip" else kind,
        member=member,
        file_name=file_name,
        family="",
        envelope="plain",
        indexable=False,
        scope="optional",
        default_scope=False,
        process=None,
        pid=None,
        account=None,
        service=None,
        sensitive=False,
        rotation=None,
        size=size,
        compressed_size=None,
        zip_crc=None,
        mtime=None,
    )


def _is_redirect(path: Path) -> bool:
    isjunction = getattr(os.path, "isjunction", None)
    return path.is_symlink() or bool(isjunction and isjunction(path))


def _check_not_redirected(kind: str, root: Path, member: str) -> None:
    """Refuse folder and file sources whose path was redirected (symlink, junction) after discovery."""
    if kind == "ers_zip":
        return
    unavailable = ContainerMissingError("the collection file is not available")
    try:
        if kind == "file":
            if _is_redirect(root) or not root.is_file():
                raise unavailable
            return
        parts = member.split("/")
        current = root
        for part in parts:
            current = current / part
            if _is_redirect(current):
                raise unavailable
        real_root = root.resolve(strict=True)
        expected = real_root.joinpath(*parts)
        real = current.resolve(strict=True)
        if os.path.normcase(str(real)) != os.path.normcase(str(expected)) or not real.is_file():
            raise unavailable
    except OSError as exc:
        raise unavailable from exc


def read_source_text(
    conn: sqlite3.Connection,
    source_id: int,
    *,
    reveal: bool = False,
    limits: FileTextLimits | None = None,
) -> FileText:
    """Text of a skipped, non-indexed source, with credential values hidden unless ``reveal``.

    Raises ``SourceNotFoundError`` (no such source), ``NotViewableError`` (an indexed or skipped log,
    or an extension outside ``TEXT_EXTENSIONS``), ``FileTooLargeError``, ``BinaryContentError`` and
    ``ContainerMissingError`` (zip, folder, file or member gone). Writes nothing anywhere.
    """
    limits = limits or FileTextLimits()
    row = conn.execute(
        "SELECT s.member, s.file_name, s.size, s.status, s.error, c.kind, c.path "
        "FROM source s JOIN collection c ON c.id = s.collection_id WHERE s.id = ?",
        (source_id,),
    ).fetchone()
    if row is None:
        raise SourceNotFoundError("source not found")
    member, file_name, size, status, error, ckind, cpath = row
    ext = Path(file_name).suffix.lower()
    if status != "skipped" or error != VIEWABLE_REASON or ext not in TEXT_EXTENSIONS:
        raise NotViewableError("this source cannot be viewed as text")
    if size is not None and size > limits.max_bytes:
        raise FileTooLargeError(limits.max_bytes)

    info = _info_from_rows(ckind, cpath, member, file_name, size or 0)
    _check_not_redirected(ckind, Path(cpath), member)
    chunks: list[bytes] = []
    total = 0
    try:
        with SourceReader(limits.read_limits) as reader:
            utf16 = reader.is_utf16(info)
            with closing(reader.iter_chunks(info, 0)) as stream:
                for chunk in stream:
                    total += len(chunk)
                    if total > limits.max_bytes:
                        raise FileTooLargeError(limits.max_bytes)
                    chunks.append(chunk)
    except FileTextError:
        raise
    except ReadLimitError as exc:
        raise FileTooLargeError(limits.max_bytes) from exc
    except Exception as exc:  # missing file or member, zipfile.BadZipFile: a gone or replaced container
        raise ContainerMissingError("the collection file is not available") from exc

    data = b"".join(chunks)
    probe = data[3:] if data.startswith(b"\xef\xbb\xbf") else data
    # UTF-16 with a BOM was already decoded: its NUL bytes are gone, so any NUL left is binary
    if b"\x00" in (probe if utf16 else probe[:BINARY_PROBE_BYTES]):
        raise BinaryContentError("binary content")
    text, encoding = _decode(data, utf16)
    text = _normalize_newlines(text)
    masked, hidden = hide_credentials(text)
    shown = text if reveal else masked
    return FileText(
        source_id=source_id,
        name=file_name,
        member=member,
        kind=ext[1:],
        size=total,
        encoding=encoding,
        lines=len(shown.splitlines()),
        hidden=hidden,
        revealed=reveal,
        text=shown,
    )
