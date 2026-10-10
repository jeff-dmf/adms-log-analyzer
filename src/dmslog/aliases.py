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
_PUBLIC_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
MAX_REAL_LENGTH = 128
_MAX_FILE_BYTES = 1024 * 1024


@dataclass(frozen=True)
class Aliases:
    """An immutable alias set. The empty set is a no-op."""

    pairs: tuple[tuple[str, str], ...] = ()  # (real, public), longest real first
    error: str | None = None  # why the file was ignored (None when loaded or absent)
    _folded: tuple[tuple[re.Pattern[str], str], ...] = field(default=(), repr=False, compare=False)

    @classmethod
    def from_pairs(cls, pairs: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> Aliases:
        """Build from ``(real, public)`` pairs (no validation; for tests and callers that validated)."""
        ordered = tuple(sorted(dict.fromkeys(pairs), key=lambda p: (-len(p[0]), p[0])))
        folded = tuple((re.compile(re.escape(real), re.IGNORECASE), public) for real, public in ordered)
        return cls(pairs=ordered, _folded=folded)

    def __len__(self) -> int:
        return len(self.pairs)

    def __bool__(self) -> bool:
        return bool(self.pairs)

    def apply(self, text: str) -> str:
        """Replace every real spelling by its public token (case-sensitive, longest first)."""
        for real, public in self.pairs:
            if real in text:
                text = text.replace(real, public)
        return text

    def apply_name(self, name: str) -> str:
        """Like :meth:`apply` for file/process/product names: also matches regardless of case."""
        if not self.pairs:
            return name
        name = self.apply(name)
        for pattern, public in self._folded:
            name = pattern.sub(lambda _m, p=public: p, name)
        return name

    def apply_path(self, path: str) -> str:
        """:meth:`apply_name` over a whole logical path (``/``-separated)."""
        return self.apply_name(path)


NO_ALIASES = Aliases()


def _invalid(detail: str) -> Aliases:
    return Aliases(error=detail)


def parse(text: str) -> Aliases:
    """Parse and validate the TOML text; an invalid file gives an empty set with ``error`` set."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return _invalid(messages.ALIASES_ERR_TOML.format(error=exc))
    extra = set(data) - {"version", "alias"}
    if extra:
        return _invalid(messages.ALIASES_ERR_UNKNOWN_KEYS.format(where="file", keys=", ".join(sorted(extra))))
    if data.get("version") != SCHEMA_VERSION or isinstance(data.get("version"), bool):
        return _invalid(messages.ALIASES_ERR_VERSION.format(expected=SCHEMA_VERSION))
    raw = data.get("alias", [])
    if not isinstance(raw, list) or not all(isinstance(x, dict) for x in raw):
        return _invalid(messages.ALIASES_ERR_NOT_TABLES)
    pairs: list[tuple[str, str]] = []
    seen: dict[str, str] = {}
    for i, entry in enumerate(raw, 1):
        where = f"alias #{i}"
        extra = set(entry) - {"public", "real"}
        if extra:
            return _invalid(
                messages.ALIASES_ERR_UNKNOWN_KEYS.format(where=where, keys=", ".join(sorted(extra)))
            )
        public, real = entry.get("public"), entry.get("real")
        if not isinstance(public, str) or not _PUBLIC_RE.fullmatch(public):
            return _invalid(messages.ALIASES_ERR_PUBLIC.format(where=where))
        if (
            not isinstance(real, str)
            or not 1 <= len(real) <= MAX_REAL_LENGTH
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in real)
        ):
            return _invalid(messages.ALIASES_ERR_REAL.format(where=where, limit=MAX_REAL_LENGTH))
        if seen.setdefault(real, public) != public:
            return _invalid(messages.ALIASES_ERR_CONFLICT.format(where=where))
        pairs.append((real, public))
    return Aliases.from_pairs(pairs)


def load(data_dir: str | Path) -> Aliases:
    """Load ``<data dir>/aliases.toml``; absent file = no aliases, invalid file = ignored with ``error``."""
    path = Path(data_dir) / ALIASES_FILENAME
    try:
        if not path.is_file():
            return NO_ALIASES
        if path.stat().st_size > _MAX_FILE_BYTES:
            return _invalid(messages.ALIASES_ERR_TOO_LARGE)
        text = path.read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        return _invalid(messages.ALIASES_ERR_READ.format(error=type(exc).__name__))
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
