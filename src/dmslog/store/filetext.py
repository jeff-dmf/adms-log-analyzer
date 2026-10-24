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
* Hiding rules (the name of a credential is tested by ``is_credential_name``):
    1. ``Password=``, ``Pwd=`` and ``Secret=`` pairs (connection strings, anywhere in the text);
    2. start tags with a credential ``key=`` / ``name=`` attribute: the ``value`` attribute (``<add>``);
    3. simple-text elements with a credential name, or with a credential ``key`` / ``name`` attribute;
    4. XML attributes with a credential name;
    5. JSON string members with a credential name;
    6. ``name = value`` and ``name: value`` lines (``.ini``, ``.init``, ``.csc``, ``.sql``).
  Connection-string attributes and elements are not hidden whole, only their password pairs (rule 1).
  Hosts, accounts and addresses are not hidden.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
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
    for i, tok in enumerate(toks):
        if tok in _CRED_TOKENS or (len(tok) > 3 and tok.endswith(_CRED_SUFFIXES)):
            return True
        if i + 1 < len(toks) and (tok, toks[i + 1]) in _CRED_PAIRS:
            return True
    return False


_ATTR = r"""[\w:.\-]+\s*=\s*(?:"[^"]*"|'[^']*')"""
_ATTRS = rf"(?:\s+{_ATTR})*"
_ATTR_RE = re.compile(r"""(?P<name>[\w:.\-]+)(?P<eq>\s*=\s*)(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)')""")

# 1. Password pairs of connection strings: Password=...; Pwd=...; Secret=...
_PAIR_RE = re.compile(
    r"""(?<![A-Za-z0-9_])(?P<key>password|pwd|secret)(?P<eq>[ \t]*=[ \t]*)"""
    r"""(?P<val>"[^"\r\n]*"|'[^'\r\n]*'|[^;"'<>\r\n]*)""",
    re.IGNORECASE,
)
# 2. Start tags (add, setting, ...) whose key/name attribute is a credential: hide the value attribute.
_START_TAG_RE = re.compile(rf"<(?P<tag>[\w:.\-]+)(?P<attrs>{_ATTRS})(?P<end>\s*/?)>")
# 3. Elements with simple text: <tag attrs>text</tag>
_ELEMENT_RE = re.compile(
    rf"<(?P<tag>[\w:.\-]+)(?P<attrs>{_ATTRS})\s*>(?P<text>[^<>]*|<!\[CDATA\[.*?\]\]>)</(?P=tag)\s*>",
    re.DOTALL,
)
# 4. XML attributes
_ATTR_FULL_RE = re.compile(
    r"""(?<![\w:.\-])""" + r"""(?P<name>[\w:.\-]+)(?P<eq>\s*=\s*)(?P<q>["'])(?P<val>[^"'\r\n]*?)(?P=q)"""
)
# 5. JSON members
_JSON_RE = re.compile(
    r"""(?P<pre>"(?P<name>[^"\\\r\n]{1,100})"\s*:\s*")(?P<val>(?:[^"\\\r\n]|\\.)*)(?P<post>")"""
)
# 6. ini style lines (lines that do not start an XML tag)
_LINE_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<name>[A-Za-z_][\w.\- \t]{0,100}?)(?P<sep>[ \t]*[=:][ \t]*)(?P<val>[^\r\n]+)$",
    re.MULTILINE,
)
_KEY_ATTRS = ("key", "name")


class _Masker:
    """Applies the rules and counts the values hidden."""

    def __init__(self) -> None:
        self.count = 0

    def mask(self, value: str) -> str:
        if not value.strip() or value.strip() == MASK:
            return value  # nothing to hide, or already hidden
        self.count += 1
        return MASK

    def _attr_values(self, attrs: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for m in _ATTR_RE.finditer(attrs):
            val = m.group("dq") if m.group("dq") is not None else m.group("sq")
            out[m.group("name").lower()] = val
        return out

    def _has_credential_key(self, attrs: str) -> bool:
        values = self._attr_values(attrs)
        return any(is_credential_name(values[k]) for k in _KEY_ATTRS if k in values)

    # rule 1
    def pairs(self, text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            val = m.group("val")
            if val[:1] in "\"'" and len(val) >= 2:
                inner = self.mask(val[1:-1])
                return f"{m.group('key')}{m.group('eq')}{val[0]}{inner}{val[0]}"
            return f"{m.group('key')}{m.group('eq')}{self.mask(val)}"

        return _PAIR_RE.sub(repl, text)

    # rule 2
    def key_value_tags(self, text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            attrs = m.group("attrs")
            if not self._has_credential_key(attrs):
                return m.group(0)

            def attr_repl(a: re.Match[str]) -> str:
                if a.group("name").lower() != "value":
                    return a.group(0)
                quote = '"' if a.group("dq") is not None else "'"
                val = a.group("dq") if a.group("dq") is not None else a.group("sq")
                return f"{a.group('name')}{a.group('eq')}{quote}{self.mask(val)}{quote}"

            new_attrs = _ATTR_RE.sub(attr_repl, attrs)
            return f"<{m.group('tag')}{new_attrs}{m.group('end')}>"

        return _START_TAG_RE.sub(repl, text)

    # rule 3
    def elements(self, text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            tag = m.group("tag")
            if not (is_credential_name(tag) or self._has_credential_key(m.group("attrs"))):
                return m.group(0)
            body = m.group("text")
            new = self.mask(body)
            if new == body:
                return m.group(0)
            return f"<{tag}{m.group('attrs')}>{new}</{tag}>"

        return _ELEMENT_RE.sub(repl, text)

    # rule 4
    def attributes(self, text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            if not is_credential_name(m.group("name")):
                return m.group(0)
            q = m.group("q")
            return f"{m.group('name')}{m.group('eq')}{q}{self.mask(m.group('val'))}{q}"

        return _ATTR_FULL_RE.sub(repl, text)

    # rule 5
    def json_members(self, text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            if not is_credential_name(m.group("name")):
                return m.group(0)
            return f"{m.group('pre')}{self.mask(m.group('val'))}{m.group('post')}"

        return _JSON_RE.sub(repl, text)

    # rule 6
    def lines(self, text: str) -> str:
        def repl(m: re.Match[str]) -> str:
            if not is_credential_name(m.group("name")):
                return m.group(0)
            val = m.group("val")
            stripped = val.rstrip()
            head = f"{m.group('indent')}{m.group('name')}{m.group('sep')}"
            return f"{head}{self.mask(stripped)}{val[len(stripped) :]}"

        return _LINE_RE.sub(repl, text)


_RULES: tuple[Callable[[_Masker, str], str], ...] = (
    _Masker.pairs,
    _Masker.key_value_tags,
    _Masker.elements,
    _Masker.attributes,
    _Masker.json_members,
    _Masker.lines,
)


def hide_credentials(text: str) -> tuple[str, int]:
    """``text`` with credential values replaced by ``MASK``, and how many values were hidden."""
    masker = _Masker()
    for rule in _RULES:
        text = rule(masker, text)
    return text, masker.count


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
