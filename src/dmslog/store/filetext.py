"""Read-only text of sources that are listed but not indexed (docs/notas/rt-r3-r4-desenho.md, 3.2).

``read_source_text`` reads one skipped source on demand (``.config``, ``.xml``, ``.csc``, ...) from
the zip or folder registered in the case. All or nothing per file: when the text mentions a credential
(password, pwd, secret, token, apikey, ...) anywhere, in any syntax, the text is withheld (``text == ""``,
``withheld``) unless the caller asks to ``reveal`` it. There are no regions, parsers or delimiters to get
wrong: the mention is looked for in a normalised view (escapes decoded, lower case, everything that is not
a letter or a digit removed), so punctuation, blanks, quotes or escapes between the letters change nothing.

* Nothing is written: no database row, no FTS entry, no cache, no export. The connection is only
  used for ``SELECT``.
* The file content is untrusted data; it is only scanned and returned as text.
* Only extensions of a closed list are viewable, up to ``MAX_TEXT_BYTES`` decoded bytes; a NUL byte in
  the first 8 KiB (in the text, for UTF-16 with a BOM) means a binary file.
* The file is opened once and read from that one handle (a zip is opened once too, never through a
  symlink). With directory handles and ``O_NOFOLLOW`` the kernel refuses a symlink on any component, even
  one swapped in while the source is being read; without them (Windows) every component is checked with
  ``lstat`` (symlink, reparse point), and the path of the open handle (asked to the operating system) must be
  the authorised one before and after the read.
"""

from __future__ import annotations

import functools
import html
import html.entities
import os
import re
import sqlite3
import stat
import sys
import unicodedata
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from dmslog.ingest.discover import normalize_member_name

MAX_TEXT_BYTES = 4 * 1024 * 1024
BINARY_PROBE_BYTES = 8 * 1024

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
    hidden: int  # credential mentions found (reported whether or not the text is withheld)
    revealed: bool
    text: str  # the text; "" when ``withheld``
    withheld: bool = False  # True: the file mentions a credential and ``reveal`` was not asked for


# ---------------------------------------------------------------------- hiding rules

# ---------------------------------------------------------------------- credential detection

# The core words of a credential. They are looked for in normalised views of the text (escapes and
# character references decoded, decomposed, lower case, every character that is not an ASCII letter or digit
# removed), so no delimiter, escape, quote, blank, line break, punctuation, accent or invisible character
# between the letters can hide a mention.
_CORE_WORDS_RE = re.compile(
    r"password|passwd|pwd|passphrase|secret|token|apikey|privatekey|sharedkey|clientsecret|credential"
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_BACKSLASH_ESC_RE = re.compile(
    r"\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})|\\U([0-9a-fA-F]{8})|\\([nrtbf\"'/\\])"
)
_LEADING_ZEROS_RE = re.compile(r"(&#[xX]?)0+(?=[0-9A-Fa-f])")
_HUGE_REF_RE = re.compile(r"&#(?:[xX][0-9A-Fa-f]{9,}|[0-9]{9,});?")  # beyond any code point (and int())
_ENTITY_TOKEN_RE = re.compile(r"&[#A-Za-z0-9]+;?")  # anything that looks like a character reference
_ENTITY_CLOSED_RE = re.compile(
    r"&[#A-Za-z0-9]+;"
)  # only the ones closed by ";" (a prefix may be glued before)
_DECODE_ROUNDS = 8  # escapes of escapes (&amp;#112;, \\u0070) are decoded this many levels deep


def _decode_backslash(m: re.Match[str]) -> str:
    hexa = m.group(1) or m.group(2) or m.group(3)
    if hexa is None:
        return " "  # \n, \", ...: a separator, never a letter
    code = int(hexa, 16)
    return chr(code) if code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF else " "


@functools.lru_cache(maxsize=4)  # the chains share their first steps; cleared when the search ends
def _backslash_step(text: str) -> str:
    return _BACKSLASH_ESC_RE.sub(_decode_backslash, text.replace("\\\\", "\\"))


@functools.lru_cache(maxsize=4)
def _html_step(text: str) -> str:
    text = text.replace("&amp;", "&") if "&amp;" in text else text  # what unescape does for it, at C speed
    if "&" not in text:
        return text
    if "&#" in text:
        text = _HUGE_REF_RE.sub(" ", _LEADING_ZEROS_RE.sub(r"\1", text))
    return html.unescape(text)


_CLOSED_REF_RE = re.compile(r"&(#[0-9]+;|#[xX][0-9A-Fa-f]+;|[A-Za-z][A-Za-z0-9]*;)")


def _strict_ref(m: re.Match[str]) -> str:
    token = m.group(0)
    if token[1] == "#":
        return html.unescape(token)
    return html.entities.html5.get(token[1:], token)  # an unknown "&name;" stays as it is


@functools.lru_cache(maxsize=4)
def _strict_html_step(text: str) -> str:
    """Only the references closed by ";" (``html.unescape`` also reads ``&not`` out of ``&notit;``)."""
    text = text.replace("&amp;", "&") if "&amp;" in text else text
    if "&#" in text:
        text = _HUGE_REF_RE.sub(" ", _LEADING_ZEROS_RE.sub(r"\1", text))
    return _CLOSED_REF_RE.sub(_strict_ref, text)


# The chains of decoding steps; each level of each chain is searched (see credential_mentions)
_CHAINS: tuple[Callable[[str], str], ...] = (
    _backslash_step,  # backslash escapes only: a decoder must not destroy a name (&notoken)
    lambda t: _html_step(_backslash_step(t)),
    lambda t: _backslash_step(_html_step(t)),
    lambda t: _strict_html_step(_backslash_step(t)),
    lambda t: _backslash_step(_strict_html_step(t)),
)


def _count_core_words(text: str) -> int:
    # NFKD turns an accented letter into letter + combining mark; the mark (like every other non-ASCII or
    # invisible character) is dropped by the filter, the letter stays
    if not text.isascii():
        text = unicodedata.normalize("NFKD", text)
    text = text.lower()
    return len(_CORE_WORDS_RE.findall(_NON_ALNUM_RE.sub("", text)))


def credential_mentions(text: str) -> int:
    """How many times a credential core word (password, pwd, secret, token, apikey, ...) is mentioned.

    Any mention counts, in any syntax and however it is spelled (backslash escapes, character references
    of any form, case, accents, invisible characters, punctuation or blanks between the letters). The text
    is decoded step by step (up to ``_DECODE_ROUNDS`` levels) by five chains: backslash escapes only;
    backslash escapes then HTML references, and the reverse; the same with only the references closed by
    ``;`` (``html.unescape`` also reads legacy ones such as ``&not`` out of ``&notit;``). At every level three
    views are searched: the text as it is, the text without any reference-like token (``&...;?``), which
    joins the letters around an unknown reference, and the text without the references closed by ``;``
    (letters glued before a reference stay). The largest count wins, because a decoder can also destroy a
    name (``&notoken`` becomes ``\u00actoken``). The caller withholds the whole file when it is not zero.
    """
    best = 0
    seen: set[str] = set()

    def search(view: str) -> None:
        nonlocal best
        if view not in seen:
            seen.add(view)
            best = max(best, _count_core_words(view))

    def search_level(level: str) -> None:
        search(level)
        if "&" in level:
            search(_ENTITY_TOKEN_RE.sub("", level))
            search(_ENTITY_CLOSED_RE.sub("", level))

    try:
        _search_chains(text, search_level)
    finally:
        _backslash_step.cache_clear()
        _html_step.cache_clear()
        _strict_html_step.cache_clear()
    return best


def _search_chains(text: str, search_level: Callable[[str], None]) -> None:
    search_level(text)
    for step in _CHAINS:
        level = text
        for _ in range(_DECODE_ROUNDS):
            if "\\" not in level and "&" not in level:
                break
            nxt = step(level)
            if nxt == level:
                break
            level = nxt
            search_level(level)


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
_os_lstat = os.lstat


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
        if _is_redirect_stat(_os_lstat(current)):
            raise _unavailable()
    return current


def _handle_path(fd: int) -> str | None:
    """The real path the open handle ``fd`` points to, from the operating system (None: unknown)."""
    try:
        if sys.platform.startswith("linux"):
            return os.readlink(f"/proc/self/fd/{fd}")
        if sys.platform == "darwin":
            import fcntl

            raw = fcntl.fcntl(fd, fcntl.F_GETPATH, b"\0" * 1024)  # type: ignore[attr-defined]
            return os.fsdecode(raw.split(b"\0", 1)[0])
        if sys.platform == "win32":
            import ctypes
            import msvcrt

            handle = msvcrt.get_osfhandle(fd)  # type: ignore[attr-defined]
            buf = ctypes.create_unicode_buffer(32768)
            size = ctypes.windll.kernel32.GetFinalPathNameByHandleW(handle, buf, len(buf), 0)  # type: ignore[attr-defined]
            if size == 0 or size >= len(buf):
                return None
            path = buf.value
            if path.startswith("\\\\?\\UNC\\"):
                return "\\\\" + path[8:]
            return path[4:] if path.startswith("\\\\?\\") else path
    except (OSError, ValueError, ImportError, AttributeError):
        return None
    return None


def _norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _open_regular(root: Path, parts: list[str]) -> tuple[int, Callable[[int], None] | None]:
    """Open ``root/parts`` once. Returns the handle and, when the path cannot be walked with directory
    handles, a function that checks the handle again after the read."""
    verify: Callable[[int], None] | None = None
    if _HAVE_DIR_FD:
        fd = _open_walk(root, parts)
    else:
        real_root = os.path.realpath(root)
        expected = _norm(os.path.join(real_root, *parts))
        path = _check_components(root, parts)
        before = _os_lstat(path)
        fd = _os_open(str(path), os.O_RDONLY | _O_NOFOLLOW | _O_BINARY | _O_NONBLOCK)

        def check(handle: int) -> None:
            # the handle must be the file at the authorised place (a folder swapped for a link after the
            # components were checked still ends up outside), and the path must still name it
            actual = _handle_path(handle)
            if actual is None or _norm(actual) != expected:
                raise _unavailable()
            after = _os_lstat(path)
            if _is_redirect_stat(after) or _identity(after) != _identity(before):
                raise _unavailable()

        try:
            check(fd)
            if _identity(os.fstat(fd)) != _identity(before):
                raise _unavailable()
        except BaseException:
            os.close(fd)
            raise
        verify = check
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise _unavailable()
    except BaseException:
        os.close(fd)
        raise
    return fd, verify


def _read_open_file(
    fd: int, verify: Callable[[int], None] | None, reader: Callable[[BinaryIO], bytes]
) -> bytes:
    """Run ``reader`` on the handle opened once, then confirm the handle still is the authorised file."""
    with os.fdopen(fd, "rb") as fh:
        data = reader(fh)
        if verify is not None:
            verify(fh.fileno())
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
    fd, verify = _open_regular(root, parts)
    return _read_open_file(fd, verify, lambda fh: _read_limited(fh, max_bytes))


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

    fd, verify = _open_regular(path.parent, [path.name])
    return _read_open_file(fd, verify, reader)


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
    hidden = credential_mentions(text)
    withheld = hidden > 0 and not reveal
    return FileText(
        source_id=source_id,
        name=file_name,
        member=member,
        kind=ext[1:],
        size=total,
        encoding=encoding,
        lines=len(text.splitlines()),
        hidden=hidden,
        revealed=reveal,
        text="" if withheld else text,
        withheld=withheld,
    )
