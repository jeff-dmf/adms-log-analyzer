"""Diagnostics built from fixed text plus normalized components, never from exception text.

``str(exc)`` of OS, zip, XML and codec errors can embed file names (often escaped, for example doubled
backslashes) that the alias matcher cannot recognize. So a diagnostic is built from:

* a fixed English description chosen by the exception CLASS (and integers such as line/column/errno),
* components (path, member, file name) that the caller passes through the alias matcher once.

Exceptions defined by this app declare how they may be shown: ``diag_reason`` and ``diag_component``
(fixed text plus one raw name) or ``diag_safe`` (the message holds only numbers and fixed words).
"""

from __future__ import annotations

import os
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
import zlib

from .aliases import Aliases


def component(aliases: Aliases | None, text: str) -> str:
    """Public spelling of ONE raw component (file, path, tag, name) of a diagnostic."""
    return aliases.apply_name(text) if aliases else text


def _fixed(exc: BaseException) -> str:
    """Fixed English description for the exception class (no text taken from the exception)."""
    if isinstance(exc, ET.ParseError):
        position = getattr(exc, "position", None)
        if isinstance(position, tuple) and len(position) == 2 and all(type(n) is int for n in position):
            return f"invalid XML at line {position[0]}, column {position[1]}"
        return "invalid XML"
    if isinstance(exc, RecursionError):
        return "document nesting too deep"
    if isinstance(exc, MemoryError):
        return "out of memory"
    if isinstance(exc, zipfile.BadZipFile):
        return "bad zip file or member"
    if isinstance(exc, zlib.error):
        return "corrupt compressed data"
    if isinstance(exc, EOFError):
        return "unexpected end of data"
    if isinstance(exc, NotImplementedError):
        return "unsupported zip feature"
    if isinstance(exc, KeyError):
        return "member not found"
    if isinstance(exc, UnicodeError):
        return "invalid text encoding"
    if isinstance(exc, LookupError):
        return "unknown encoding"
    if isinstance(exc, FileNotFoundError):
        return "file not found"
    if isinstance(exc, PermissionError):
        return "permission denied"
    if isinstance(exc, IsADirectoryError):
        return "is a directory"
    if isinstance(exc, NotADirectoryError):
        return "not a directory"
    if isinstance(exc, OSError):
        return f"I/O error (errno {exc.errno})" if type(exc.errno) is int else "I/O error"
    if isinstance(exc, sqlite3.Error):
        return "database error"
    if isinstance(exc, ValueError):
        return "invalid value"
    return type(exc).__name__


def describe_error(exc: BaseException, aliases: Aliases | None = None, *, with_filename: bool = True) -> str:
    """Safe one-line description of an exception: fixed text plus normalized components only."""
    reason = getattr(exc, "diag_reason", None)
    if isinstance(reason, str):
        name = getattr(exc, "diag_component", "")
        return f"{reason}: {component(aliases, name)}" if name else reason
    if getattr(exc, "diag_safe", False):
        return str(exc)
    text = _fixed(exc)
    name = getattr(exc, "filename", None) if with_filename and isinstance(exc, OSError) else None
    if isinstance(name, (str, os.PathLike)) and os.fspath(name):
        text = f"{text}: {component(aliases, os.fspath(name))}"
    return text


def error_label(exc: BaseException, aliases: Aliases | None = None) -> str:
    """``ClassName: <describe_error>`` (just the class name when the description is the class name)."""
    detail = describe_error(exc, aliases)
    name = type(exc).__name__
    return name if detail == name else f"{name}: {detail}"
