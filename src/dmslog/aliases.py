"""Local aliases: map the real spelling of a name (as it appears in real logs and configs) to the
neutral token used in the repository.

The optional file ``<data dir>/aliases.toml`` never goes into git::

    version = 1
    [[alias]]
    public = "Custom"
    real = "<original text>"

Normalization real -> public happens at the ingest boundaries where names are read from real files
(file/process names, Registry file names and product names, Web Service operation names), before
matching against the catalog. Log message text and payloads are not rewritten. A missing file means
no aliases; an invalid file is ignored as a whole and reported through ``Aliases.error`` (the error
text never contains the values).
"""

from __future__ import annotations

import re
import threading
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import messages

ALIASES_FILENAME = "aliases.toml"
SCHEMA_VERSION = 1
_PUBLIC_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}", re.ASCII)
MAX_REAL_LENGTH = 128
_MAX_FILE_BYTES = 1024 * 1024


def _single_pass(rx: re.Pattern[str] | None, text: str, publics: tuple[str, ...]) -> str:
    """One pass over the ORIGINAL text; replaced output is never scanned again.

    The public token comes from the alternative that matched (its group), never from a second lookup
    of the matched text, so the matcher alone defines which spellings are equivalent.
    """
    if rx is None or not text:
        return text

    def repl(match: re.Match[str]) -> str:
        return publics[int(match.lastgroup[1:])]  # type: ignore[index]

    return rx.sub(repl, text)


def _compile(reals: list[str], flags: int) -> re.Pattern[str] | None:
    """Alternation in the given (longest first) order; alternative i is the named group ``g<i>``."""
    if not reals:
        return None
    return re.compile("|".join(f"(?P<g{i}>{re.escape(r)})" for i, r in enumerate(reals)), flags)


def same_spelling(a: str, b: str) -> bool:
    """True when the case-insensitive matcher treats `a` and `b` as the same real name."""
    return re.fullmatch(re.escape(a), b, re.IGNORECASE) is not None or (
        re.fullmatch(re.escape(b), a, re.IGNORECASE) is not None
    )


@dataclass(frozen=True)
class Aliases:
    """An immutable alias set. The empty set is a no-op."""

    pairs: tuple[tuple[str, str], ...] = ()  # (real, public), longest real first
    error: str | None = None  # why the file was ignored (None when loaded or absent)
    _exact: re.Pattern[str] | None = field(default=None, repr=False, compare=False)
    _fold: re.Pattern[str] | None = field(default=None, repr=False, compare=False)
    _publics: tuple[str, ...] = field(default=(), repr=False, compare=False)

    @classmethod
    def from_pairs(cls, pairs: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> Aliases:
        """Build from ``(real, public)`` pairs (no validation; for tests and callers that validated)."""
        ordered = tuple(sorted(dict.fromkeys(pairs), key=lambda p: (-len(p[0]), p[0])))
        reals = [r for r, _ in ordered]
        return cls(
            pairs=ordered,
            _exact=_compile(reals, 0),
            _fold=_compile(reals, re.IGNORECASE),
            _publics=tuple(p for _, p in ordered),
        )

    def __len__(self) -> int:
        return len(self.pairs)

    def __bool__(self) -> bool:
        return bool(self.pairs)

    def apply(self, text: str) -> str:
        """Replace each real spelling by its public token: one case-sensitive pass, longest first."""
        return _single_pass(self._exact, text, self._publics)

    def apply_name(self, name: str) -> str:
        """Like :meth:`apply` but a whole configured `real` also matches in any letter case.

        Only complete configured strings match (no partial-case mixtures) and the text is scanned once.
        """
        return _single_pass(self._fold, name, self._publics)

    def apply_path(self, path: str) -> str:
        """:meth:`apply_name` over a whole logical path (``/``-separated)."""
        return self.apply_name(path)

    def sanitize(self, text: str) -> str:
        """Normalize any text derived from names (errors, progress, warnings) before it is shown or stored."""
        return self.apply_name(text)


NO_ALIASES = Aliases()


def _invalid(detail: str) -> Aliases:
    return Aliases(error=detail)


def _printable(text: str) -> bool:
    """Only printable characters (space allowed): no control (C0, DEL, C1), format, line/paragraph
    separator (U+2028/2029), surrogate, private-use or unassigned ones."""
    return all(ch.isprintable() for ch in text)


def parse(text: str) -> Aliases:
    """Parse and validate the TOML text; an invalid file gives an empty set with ``error`` set.

    Diagnostics are fixed messages (plus an entry number): never file content.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        line = _line_of(exc)
        return _invalid(
            messages.ALIASES_ERR_TOML_LINE.format(line=line) if line else messages.ALIASES_ERR_TOML
        )
    if set(data) - {"version", "alias"}:
        return _invalid(messages.ALIASES_ERR_UNKNOWN_KEYS.format(where="file"))
    version = data.get("version")
    if type(version) is not int or version != SCHEMA_VERSION:
        return _invalid(messages.ALIASES_ERR_VERSION.format(expected=SCHEMA_VERSION))
    raw = data.get("alias", [])
    if not isinstance(raw, list) or not all(isinstance(x, dict) for x in raw):
        return _invalid(messages.ALIASES_ERR_NOT_TABLES)
    pairs: list[tuple[str, str]] = []
    seen: list[tuple[str, str]] = []  # (real, public) already accepted
    for i, entry in enumerate(raw, 1):
        where = f"alias #{i}"
        if set(entry) - {"public", "real"}:
            return _invalid(messages.ALIASES_ERR_UNKNOWN_KEYS.format(where=where))
        public, real = entry.get("public"), entry.get("real")
        if not isinstance(public, str) or not _PUBLIC_RE.fullmatch(public):
            return _invalid(messages.ALIASES_ERR_PUBLIC.format(where=where))
        if not isinstance(real, str) or not 1 <= len(real) <= MAX_REAL_LENGTH or not _printable(real):
            return _invalid(messages.ALIASES_ERR_REAL.format(where=where, limit=MAX_REAL_LENGTH))
        # conflicts are decided by the matcher's own equivalence, not by a second normalization
        if any(p != public and same_spelling(r, real) for r, p in seen):
            return _invalid(messages.ALIASES_ERR_CONFLICT.format(where=where))
        seen.append((real, public))
        pairs.append((real, public))
    return Aliases.from_pairs(pairs)


def _line_of(exc: tomllib.TOMLDecodeError) -> int:
    """Line number reported by the TOML parser (an int), or 0 when unavailable."""
    line = getattr(exc, "lineno", None)
    return line if type(line) is int else 0


def load(data_dir: str | Path) -> Aliases:
    """Load ``<data dir>/aliases.toml``; absent file = no aliases, invalid file = ignored with ``error``."""
    path = Path(data_dir) / ALIASES_FILENAME
    try:
        if not path.is_file():
            return NO_ALIASES
        if path.stat().st_size > _MAX_FILE_BYTES:
            return _invalid(messages.ALIASES_ERR_TOO_LARGE)
        text = path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return _invalid(messages.ALIASES_ERR_READ)
    return parse(text)


_CACHE: dict[Path, tuple[tuple[int, int] | None, Aliases]] = {}
_LOCK = threading.Lock()


def get(data_dir: str | Path) -> Aliases:
    """Cached :func:`load`: one read per process until the file's size or mtime changes."""
    key = Path(data_dir)
    try:
        st = (key / ALIASES_FILENAME).stat()
        stamp: tuple[int, int] | None = (st.st_mtime_ns, st.st_size)
    except OSError:
        stamp = None
    with _LOCK:
        cached = _CACHE.get(key)
        if cached is not None and cached[0] == stamp:
            return cached[1]
    loaded = load(key)
    with _LOCK:
        _CACHE[key] = (stamp, loaded)
    return loaded


def describe(aliases: Aliases) -> str:
    """One-line status for ``dmslog info`` (counts only, never values)."""
    if aliases.error:
        return messages.INFO_ALIASES_IGNORED.format(error=aliases.error)
    return messages.INFO_ALIASES_LOADED.format(count=len(aliases))
