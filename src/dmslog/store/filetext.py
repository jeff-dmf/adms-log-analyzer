"""Read-only text of sources that are listed but not indexed (docs/notas/rt-r3-r4-desenho.md, 3.2).

``read_source_text`` reads one skipped source on demand (``.config``, ``.xml``, ``.csc``, ...) from
the zip or folder registered in the case and returns its text with the values of credentials hidden
by default.

* Nothing is written: no database row, no FTS entry, no cache, no export. The connection is only
  used for ``SELECT``.
* The file content is untrusted data. There is no XML or JSON parser (no DTD, no entities): hiding is
  fail closed. Every occurrence of a credential name (``is_credential_name``: password, pwd, secret, token,
  api/private/shared key, client secret, credential; camelCase and punctuation split; also when spelled with
  ``\\uXXXX`` or ``&#..;`` escapes) hides what follows it, up to the end of its region, whatever the syntax:
    - at least to the end of the logical line, after any separators, quotes and blank lines between the
      name and its value;
    - a value that opens a quote: to the line of the closing quote (doubled quotes are escapes);
    - inside a start tag: to the end of the tag and, for an element, to its closing tag or the end of the
      next non-empty line (whichever is later);
    - a CDATA section the region reaches is taken whole (an open one: to the end of the text).
  Precision is traded for safety (``PasswordPolicy`` is hidden); lines without a credential name stay
  readable. Hosts, accounts and addresses are not hidden.
* Only extensions of a closed list are viewable, up to ``MAX_TEXT_BYTES`` decoded bytes; a NUL byte in
  the first 8 KiB means a binary file.
* The file is opened once and read from that one handle (a zip is opened once too, never through a
  symlink). With directory handles and ``O_NOFOLLOW`` the kernel refuses a symlink on any component, even
  one swapped in while the source is being read; without them (Windows) every component is checked with
  ``lstat`` (symlink, reparse point), and the path of the open handle (asked to the operating system) must be
  the authorised one before and after the read.
"""

from __future__ import annotations

import bisect
import functools
import os
import re
import sqlite3
import stat
import sys
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


def _tokens(name: str) -> list[str]:
    return [t for t in _NON_ALNUM.split(_SPLIT_CAMEL.sub("_", name).lower()) if t]


@functools.lru_cache(maxsize=8192)
def is_credential_name(name: str) -> bool:
    """True when ``name`` (an attribute, element, key or setting name) names a credential.

    Names are split at case changes and punctuation; ``pass`` / ``password`` / ``pwd`` / ``secret`` /
    ``token`` / ``credential`` and key pairs match as whole words (``DbPassword`` and ``api-key`` do,
    ``compass``, ``bypass`` and ``tokenizer`` do not). Connection-string names are not credential names
    here: only the password pairs inside them are hidden.
    """
    toks = _tokens(name)
    if "".join(toks) in _CRED_TOKENS:
        return True
    for i, tok in enumerate(toks):
        if tok in _CRED_TOKENS or (len(tok) > 3 and tok.endswith(_CRED_SUFFIXES)):
            return True
        if i + 1 < len(toks) and (tok, toks[i + 1]) in _CRED_PAIRS:
            return True
    return False


# A run of name characters that contains a credential word (verified by is_credential_name); one word
# before it is included so that "Shared Key" or "api key" are seen as a pair.
_NAME_RE = re.compile(
    r"(?<![\w.\-])(?:[\w.\-]+[ \t]+)?(?P<name>[\w.\-]*(?:pass|pwd|secret|token|credential|key)[\w.\-]*)",
    re.IGNORECASE,
)
_HINT_RE = re.compile(r"pass|pwd|secret|token|credential|key", re.IGNORECASE)
_SEP_RE = re.compile(r"""[\s=:"']*""")  # between a name and its value (any amount)
_BREAK_RE = re.compile(r"[\r\n]")
_BLANK_RE = re.compile(r"[\r\n \t]*")
_TAG_NAME_RE = re.compile(r"<(?P<tag>[\w:.\-]+)")
_CLOSE_TAG_RE = re.compile(r"</(?P<tag>[\w:.\-]+)\s*>")
_CDATA_OPEN = "<![CDATA["
# Escapes that can hide the letters of a name: \uXXXX, \xHH, &#xHH; and &#NNN;
_ESC_RE = re.compile(r"\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})|&#[xX]([0-9a-fA-F]{1,6});|&#([0-9]{1,7});")
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


class _DecodedView:
    """The text with the escapes that can spell a name decoded, and the way back to the original."""

    def __init__(self, text: str) -> None:
        parts: list[str] = []
        self.dstarts: list[int] = []
        self.ostarts: list[int] = []
        self.olens: list[int] = []  # 0 = plain segment (one to one), else the length of one escape
        pos = dpos = 0
        for m in _ESC_RE.finditer(text):
            if m.start() > pos:
                self._add(parts, dpos, pos, 0, text[pos : m.start()])
                dpos += m.start() - pos
            code = int(next(g for g in m.groups() if g is not None), 16 if m.group(4) is None else 10)
            char = chr(code) if code <= 0x10FFFF else "\ufffd"
            self._add(parts, dpos, m.start(), m.end() - m.start(), char)
            dpos += 1
            pos = m.end()
        if pos < len(text):
            self._add(parts, dpos, pos, 0, text[pos:])
        self.text = "".join(parts)
        self.changed = bool(self.olens) and any(self.olens)

    def _add(self, parts: list[str], dpos: int, opos: int, olen: int, piece: str) -> None:
        parts.append(piece)
        self.dstarts.append(dpos)
        self.ostarts.append(opos)
        self.olens.append(olen)

    def to_original(self, a: int, b: int) -> Span:
        """Original span of the decoded span [a, b) (a decoded escape counts whole)."""
        i = bisect.bisect_right(self.dstarts, a) - 1
        start = self.ostarts[i] + (a - self.dstarts[i] if not self.olens[i] else 0)
        j = bisect.bisect_right(self.dstarts, b - 1) - 1
        end = self.ostarts[j] + (b - self.dstarts[j] if not self.olens[j] else self.olens[j])
        return start, end


class _Scan:
    """Region finder over one text. Searches are memoised so that, in a single pass, no stretch of the
    text is scanned twice for the same purpose (hostile input stays linear)."""

    def __init__(self, text: str) -> None:
        self.t = text
        self.n = len(text)
        self._nl = (-1, -1)
        self._gt = (-1, -1)
        self._scan = 0
        self._last_lt = -1
        self._last_gt = -1
        self._cdata: dict[int, int] | None = None
        self._cstarts: list[int] = []
        self._closes: dict[str, list[int]] | None = None

    def line_end(self, i: int) -> int:
        lo, res = self._nl
        if lo != -1 and lo <= i <= res:
            return res
        m = _BREAK_RE.search(self.t, i)
        res = m.start() if m else self.n
        self._nl = (i, res)
        return res

    def next_gt(self, i: int) -> int:
        lo, res = self._gt
        if lo != -1 and lo <= i and (res == -1 or i <= res):
            return res
        res = self.t.find(">", i)
        self._gt = (i, res)
        return res

    def next_nonempty_line_end(self, i: int) -> int:
        j = _BLANK_RE.match(self.t, i).end()  # type: ignore[union-attr]
        return self.n if j >= self.n else self.line_end(j)

    def close_quote(self, q: str, start: int) -> int:
        """Index of the quote that really closes a value (a doubled quote is an escape), or -1."""
        i = start
        while (k := self.t.find(q, i)) != -1:
            if k + 1 < self.n and self.t[k + 1] == q:
                i = k + 2
                continue
            return k
        return -1

    def in_tag(self, ne: int) -> int:
        """Index of the "<" of the start tag that contains position ``ne``, or -1."""
        f = self.t.rfind("<", self._scan, ne)
        if f != -1:
            self._last_lt = f
        f = self.t.rfind(">", self._scan, ne)
        if f != -1:
            self._last_gt = f
        self._scan = ne
        return self._last_lt if self._last_lt > self._last_gt else -1

    def close_tag_start(self, tag: str, after: int) -> int:
        if self._closes is None:
            self._closes = {}
            for m in _CLOSE_TAG_RE.finditer(self.t):
                self._closes.setdefault(m.group("tag").lower(), []).append(m.start())
        starts = self._closes.get(tag.lower())
        if not starts:
            return -1
        k = bisect.bisect_left(starts, after)
        return starts[k] if k < len(starts) else -1

    def extend_over_cdata(self, ns: int, end: int) -> int:
        if self._cdata is None:
            self._cdata = _cdata_sections(self.t) if _CDATA_OPEN in self.t else {}
            self._cstarts = sorted(self._cdata)
        k = bisect.bisect_left(self._cstarts, ns)
        while k < len(self._cstarts) and self._cstarts[k] < end:
            e = self._cdata[self._cstarts[k]]
            end = max(end, self.n if e == -1 else e)
            k += 1
        return end

    def region(self, ns: int, ne: int) -> Span | None:
        """What to hide for a credential name at [ns, ne): from the first character of its value (after
        any separators, quotes and blanks) to the end of the region. Fail closed: the region runs to the
        end of the logical line at least; a value that opens a quote runs to the closing quote's line;
        inside a start tag it runs to the end of the tag and, for an element, to its closing tag or the end
        of the next non-empty line (whichever is later); a CDATA section it reaches is taken whole."""
        t = self.t
        p = _SEP_RE.match(t, ne).end()  # type: ignore[union-attr]
        lt = self.in_tag(ne)
        end = self.line_end(p) if p < self.n else self.n
        if p > ne and t[p - 1] in "\"'":
            c = self.close_quote(t[p - 1], p)
            if c != -1:
                end = max(end, self.line_end(c))
        start = p
        if lt != -1:
            gt = self.next_gt(ne)
            if gt == -1:
                end = self.n
            else:
                end = max(end, self.line_end(gt))
                if t[gt - 1] != "/":  # an element with content: up to its closing tag, or two lines
                    head = _TAG_NAME_RE.match(t, lt)
                    if head:
                        close = self.close_tag_start(head.group("tag"), gt)
                        end = max(end, close, self.next_nonempty_line_end(self.line_end(gt)))
                head = _TAG_NAME_RE.match(t, lt)
                if head and ns != head.start("tag"):  # an attribute: its value may come before the name
                    start = head.end()
                elif p == gt:  # "<Password>": keep the ">" visible
                    start = gt + 1
        end = self.extend_over_cdata(ns, end)
        return (start, end) if end > start else None


_NONBLANK_RE = re.compile(r"\S")


def _worth_hiding(text: str, a: int, b: int) -> bool:
    """False for an empty or blank span, or one that is already the mask (no slicing of long spans)."""
    if b <= a:
        return False
    if _NONBLANK_RE.search(text, a, b) is None:
        return False
    return not (b - a <= 2 * len(MASK) and text[a:b].strip() == MASK)


def hide_credentials(text: str) -> tuple[str, int]:
    """``text`` with credential values replaced by ``MASK``, and how many values were hidden.

    Fail closed: every occurrence of a credential name hides what follows it up to the end of its region
    (see ``_Scan.region``), whatever the syntax around it; precision is traded for safety (``PasswordPolicy``
    is hidden too) but lines without a credential name stay readable. Names spelled with escapes
    (``\\u0070assword``, ``&#112;assword``) are found in a decoded view. One pass, linear in the text.
    """
    escaped = "\\" in text or "&#" in text
    if not escaped and not _HINT_RE.search(text):
        return text, 0
    view = _DecodedView(text) if escaped else None
    work = view.text if view is not None and view.changed else text
    if work is not text and not _HINT_RE.search(work):
        return text, 0
    if work is text and not _HINT_RE.search(work):
        return text, 0
    scan = _Scan(work)
    regions: list[Span] = []
    covered = 0
    for m in _NAME_RE.finditer(work):
        ns = m.start("name")
        if ns < covered:
            continue
        if not (is_credential_name(m.group("name")) or is_credential_name(m.group(0))):
            continue
        r = scan.region(ns, m.end())
        if r is not None:
            regions.append(r)
            covered = r[1]
    if view is not None and view.changed:
        regions = [view.to_original(a, b) for a, b in regions]
    spans = [(a, b) for a, b in regions if _worth_hiding(text, a, b)]
    if not spans:
        return text, 0
    out: list[str] = []
    pos = 0
    for a, b in spans:
        out.append(text[pos:a])
        out.append(MASK)
        pos = b
    out.append(text[pos:])
    return "".join(out), len(spans)


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
