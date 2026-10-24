"""Read-only text of sources that are listed but not indexed (docs/notas/rt-r3-r4-desenho.md, 3.2).

``read_source_text`` reads one skipped source on demand (``.config``, ``.xml``, ``.csc``, ...) from
the zip or folder registered in the case and returns its text with the values of credentials hidden
by default.

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
* The file is opened once and read from that one handle (a zip is opened once too, never through a
  symlink). With directory handles and ``O_NOFOLLOW`` the kernel refuses a symlink on any component, even
  one swapped in while the source is being read; without them (Windows) every component is checked with
  ``lstat`` (symlink, reparse point) and the identity of the file is compared before and after the read.
"""

from __future__ import annotations

import functools
import json
import os
import re
import sqlite3
import stat
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from dmslog.ingest.discover import normalize_member_name

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
# Pieces of a start tag, parsed one attribute at a time (no cap on the attribute count).
_TAG_NAME_RE = re.compile(r"<(?P<tag>[\w:.\-]+)")
_ATTR_AT_RE = re.compile(r"""\s+(?P<name>[\w:.\-]+)(?:\s*=\s*(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'))?""")
_TAG_END_RE = re.compile(r"\s*(?P<self>/?)>")
# Start tags whose name looks like a credential (verified by is_credential_name).
_ELEMENT_HINT_RE = re.compile(
    r"<(?P<tag>[\w:.\-]*(?:pass|pwd|secret|token|credential|key)[\w:.\-]*)", re.IGNORECASE
)
_CLOSE_TAG_RE = re.compile(r"</(?P<tag>[\w:.\-]+)\s*>")
_PAIR_NAME_RE = re.compile(r"(?<![\w.\-])(?P<name>[\w.\-]+)[ \t]*=[ \t]*")
_UNQUOTED_RE = re.compile(r"""[^;"'<>\r\n]*""")
# Every quote starts a token: a string, or an unterminated one that runs to the end of its line, so the
# scan never restarts inside text it already examined (one pass, monotonic).
_JSON_STRING_RE = re.compile(r'"(?P<body>[^"\\\r\n]*(?:\\.[^"\\\r\n]*)*)(?P<close>")?')
_JSON_COLON_RE = re.compile(r"[ \t\r\n]*:[ \t\r\n]*")
_JSON_GAP_MAX = 64
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


def _attr_inner(m: re.Match[str]) -> Span | None:
    if m.group("dq") is not None:
        return m.span("dq")
    if m.group("sq") is not None:
        return m.span("sq")
    return None


def _tag_spans(text: str, lt: int, cdata: dict[int, int]) -> list[Span]:
    """Spans hidden by the start tag at ``lt``: its ``value`` attribute when ``key=`` / ``name=`` is a
    credential, and the text of the element when the tag or that key is a credential.

    The attributes are read one at a time, whatever their number. A tag that is not closed properly is
    still used up to where it could be read (hiding more, never less).
    """
    head = _TAG_NAME_RE.match(text, lt)
    if head is None:
        return []
    tag = head.group("tag")
    pos = head.end()
    cred_key = False
    values: list[Span] = []
    while (a := _ATTR_AT_RE.match(text, pos)) is not None:
        pos = a.end()
        inner = _attr_inner(a)
        if inner is None:
            continue
        name = a.group("name").lower()
        if name in _KEY_ATTRS and is_credential_name(text[inner[0] : inner[1]]):
            cred_key = True
        elif name == "value":
            values.append(inner)
    spans = values if cred_key else []
    end = _TAG_END_RE.match(text, pos)
    if end is not None and not end.group("self") and (cred_key or is_credential_name(tag)):
        span = _element_text_span(text, end.end(), tag, cdata)
        if span:
            spans.append(span)
    return spans


def _json_pair_spans(text: str) -> list[Span]:
    """Value spans of ``"key": "value"`` where the key, once its escapes are decoded, is a credential."""
    spans: list[Span] = []
    prev_end = -1
    prev_key: str | None = None
    for m in _JSON_STRING_RE.finditer(text):
        if m.group("close") is None:  # unterminated: it ran to the end of the line
            prev_key = None
            continue
        if prev_key is not None and m.start() - prev_end <= _JSON_GAP_MAX:
            if _JSON_COLON_RE.fullmatch(text, prev_end, m.start()) and is_credential_name(prev_key):
                spans.append(m.span("body"))
        prev_end = m.end()
        body = m.group("body")
        prev_key = _json_unescape(body) if len(body) <= 200 else None
    return spans


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
    cdata = _cdata_sections(text)
    seen_tags: set[int] = set()

    def tag_at(lt: int) -> None:
        if lt not in seen_tags:
            seen_tags.add(lt)
            spans.extend(_tag_spans(text, lt, cdata))

    # 2. XML attributes with a credential name (any quote kind inside, multiline values); a credential
    #    key=/name= attribute also leads to its start tag (<add key=".." value="..">)
    last_lt = -1
    scanned = 0
    for m in _ATTR_RE.finditer(text):
        name = m.group("name")
        inner = _attr_inner(m)
        if inner is None:
            continue
        if is_credential_name(name):
            spans.append(inner)
        elif name.lower() in _KEY_ATTRS and is_credential_name(text[inner[0] : inner[1]]):
            # nearest "<" before this attribute, searched incrementally (each stretch is scanned once)
            found = text.rfind("<", scanned, m.start())
            if found != -1:
                last_lt = found
            scanned = m.start()
            if last_lt != -1:
                tag_at(last_lt)
    # 3. start tags whose name is a credential: the text of the element
    for m in _ELEMENT_HINT_RE.finditer(text):
        if is_credential_name(m.group("tag")):
            tag_at(m.start())
    # 4. JSON string members (escapes in the key are decoded before the name is classified)
    spans.extend(_json_pair_spans(text))
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
    if "\\" not in text and not _HINT_RE.search(text):  # a backslash may hide the name (\\u0070assword)
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

READ_BLOCK = 64 * 1024
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)  # a FIFO swapped in must not block the open
_O_BINARY = getattr(os, "O_BINARY", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
# Walking the member path with directory handles and O_NOFOLLOW lets the kernel refuse any redirect, even
# one made between two steps. Without it (Windows) the path is checked with lstat before and after.
_HAVE_DIR_FD = bool(_O_NOFOLLOW and _O_DIRECTORY and os.open in os.supports_dir_fd)
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)

# Indirections the tests replace to simulate a swap between two steps.
_os_open = os.open


def _read_block(fh: BinaryIO, size: int) -> bytes:
    return fh.read(size)


def _unavailable() -> ContainerMissingError:
    return ContainerMissingError("the collection file is not available")


def _is_redirect_stat(st: os.stat_result) -> bool:
    return stat.S_ISLNK(st.st_mode) or bool(
        _REPARSE_FLAG and getattr(st, "st_file_attributes", 0) & _REPARSE_FLAG
    )


def _identity(st: os.stat_result) -> tuple[int, int]:
    return (st.st_dev, st.st_ino)


def _safe_parts(member: str) -> list[str]:
    parts = member.split("/")
    for part in parts:
        if part in ("", ".", "..") or "\\" in part or os.sep in part or (os.altsep and os.altsep in part):
            raise _unavailable()
    return parts


def _open_walk(root: Path, parts: list[str]) -> int:
    """Open ``root/parts`` through directory handles; no component may be a symlink."""
    fd = _os_open(str(root), os.O_RDONLY | _O_DIRECTORY | _O_CLOEXEC)
    try:
        for part in parts[:-1]:
            nfd = _os_open(part, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC, dir_fd=fd)
            os.close(fd)
            fd = nfd
        return _os_open(parts[-1], os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK | _O_CLOEXEC, dir_fd=fd)
    finally:
        os.close(fd)


def _check_components(root: Path, parts: list[str]) -> Path:
    """lstat every component under ``root``: none may be a symlink or a reparse point."""
    current = root
    for part in parts:
        current = current / part
        if _is_redirect_stat(os.lstat(current)):
            raise _unavailable()
    return current


def _open_regular(root: Path, parts: list[str]) -> tuple[int, Path | None, tuple[int, int]]:
    """Open ``root/parts`` once. Returns (fd, path to recheck after reading or None, identity)."""
    if _HAVE_DIR_FD:
        fd = _open_walk(root, parts)
        recheck: Path | None = None
    else:
        path = _check_components(root, parts)
        before = os.lstat(path)
        fd = _os_open(str(path), os.O_RDONLY | _O_NOFOLLOW | _O_BINARY | _O_NONBLOCK)
        recheck = path
        if _identity(os.fstat(fd)) != _identity(before):
            os.close(fd)
            raise _unavailable()
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise _unavailable()
    except BaseException:
        os.close(fd)
        raise
    return fd, recheck, _identity(st)


def _read_open_file(
    fd: int, recheck: Path | None, ident: tuple[int, int], reader: Callable[[BinaryIO], bytes]
) -> bytes:
    """Run ``reader`` on the handle opened once, then confirm the path still names the same file."""
    with os.fdopen(fd, "rb") as fh:
        data = reader(fh)
    if recheck is not None:
        after = os.lstat(recheck)
        if _is_redirect_stat(after) or _identity(after) != ident:
            raise _unavailable()
    return data


def _read_limited(fh: BinaryIO, max_bytes: int) -> bytes:
    data = bytearray()
    while block := _read_block(fh, READ_BLOCK):
        data += block
        if len(data) > max_bytes:
            raise FileTooLargeError(max_bytes)
    return bytes(data)


def _read_local(kind: str, cpath: str, member: str, max_bytes: int) -> bytes:
    """Bytes of a folder or loose-file source, read once through one handle, redirects refused."""
    if kind == "file":
        root, parts = Path(cpath).parent, [Path(cpath).name]
    else:
        root, parts = Path(cpath), _safe_parts(member)
    fd, recheck, ident = _open_regular(root, parts)
    return _read_open_file(fd, recheck, ident, lambda fh: _read_limited(fh, max_bytes))


def _read_zip_member(cpath: str, member: str, max_bytes: int) -> bytes:
    """Bytes of a zip member: the zip is opened once (never through a symlink) and the member is
    read from that handle, at most ``max_bytes + 1`` bytes of it."""
    path = Path(cpath)

    def reader(fh: BinaryIO) -> bytes:
        with zipfile.ZipFile(fh) as archive:
            names = {normalize_member_name(i.filename): i for i in archive.infolist() if not i.is_dir()}
            with archive.open(names[member]) as src:
                data = src.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise FileTooLargeError(max_bytes)
        return data

    fd, recheck, ident = _open_regular(path.parent, [path.name])
    return _read_open_file(fd, recheck, ident, reader)


def _decode(data: bytes) -> tuple[str, str, int]:
    """Text, encoding and decoded size of ``data``; never raises. A UTF-16 BOM selects UTF-16."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        codec = "utf-16-le" if data[:2] == b"\xff\xfe" else "utf-16-be"
        text = data[2:].decode(codec, errors="replace")
        return text, "utf-16", len(text.encode("utf-8"))
    body = data[3:] if data.startswith(b"\xef\xbb\xbf") else data
    try:
        return body.decode("utf-8"), "utf-8", len(body)
    except UnicodeDecodeError:
        return body.decode("latin-1"), "latin-1", len(body)


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


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
    ``ContainerMissingError`` (zip, folder, file or member gone, or redirected by a symlink).
    Writes nothing anywhere.
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

    try:
        if ckind == "ers_zip":
            data = _read_zip_member(cpath, normalize_member_name(member), limits.max_bytes)
        else:
            data = _read_local(ckind, cpath, member, limits.max_bytes)
    except FileTextError:
        raise
    except Exception as exc:  # missing file or member, ELOOP from a redirect, zipfile.BadZipFile, ...
        raise _unavailable() from exc

    text, encoding, total = _decode(data)
    # UTF-16 with a BOM is decoded first (its NUL bytes are gone): any NUL left in the text is binary;
    # without a BOM a NUL in the first 8 KiB means binary
    if encoding == "utf-16":
        binary = "\x00" in text
    else:
        binary = b"\x00" in (data[3:] if data.startswith(b"\xef\xbb\xbf") else data)[:BINARY_PROBE_BYTES]
    if binary:
        raise BinaryContentError("binary content")
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
