"""Configurable anonymization of exported text (ARQUITETURA 10.1 and 12, decision U10; ROADMAP 7.4).

A :class:`Redactor` replaces sensitive values by stable markers (``<HOST-1>``, ``<GUID-3>``): the
same value always gets the same marker inside one redactor, numbered per category in order of first
appearance, so the text stays correlatable without showing the value.

What is replaced is set by a :class:`Profile`, stored as ``<data dir>/redaction.toml`` (outside the
cases and the repository):

* ``mode``: ``"strict"`` (default) adds the structural safety net below; ``"standard"`` is the
  pattern layer alone (more readable, recognizes only the shapes and labels it knows);
* ``categories``: GUIDs, GIDs, INC/PROB numbers, mRIDs, vehicle plates, hosts, IPs, users, URLs,
  paths, e-mails, phones and person names/addresses. Default: every category on;
* ``terms``: words typed by the user (customer name, substation acronyms). ``word`` terms match
  whole words ignoring case; ``literal`` terms match the exact characters anywhere;
* ``exceptions``: values that are never replaced (operation names, error codes), wherever they occur;
* ``payload_names``: XML element/attribute names and JSON keys whose value is replaced inside
  payloads, which are always treated as sensitive.

The pattern layer (both modes):

* Every rule runs on the ORIGINAL text and on its decoded views (percent, ``\\u``, character
  references; JSON string escapes); spans are mapped back to the original characters.
* Overlapping spans are united (the span that starts first, then the higher priority, names the
  category), so a marker is never re-scanned and a value is never left half visible. A URL, path or
  e-mail that continues in the raw text is extended to the end of its token BEFORE the union, so a
  span it reaches is absorbed instead of dropped.
* Labelled values (``user:``, ``GID=``, ``mRID``...): a quoted value runs to its closing quote whatever
  its length, a bracketed one to its closing bracket; never closed, it runs to the end of the text. A
  bare name goes on over blank runs, capitalized words and lowercase particles; a GID over digit
  groups.
* Payload field rules (XML elements/attributes, JSON keys) run whatever the first character is, and
  again inside XML text holding JSON and inside escaped JSON strings holding markup (3 levels).
* Exceptions: a USER exception protects every occurrence of its text from every rule. An AUTOMATIC
  exception (catalog names and codes the export validated) only protects a value found by a shape rule
  (host, IP, plate... shapes) or a user term, never a labelled value (``customerName=``, ``user:``), a
  known value, a payload field, a template field or a strict span. Containment is answered by a binary
  search over the sorted protected intervals (no quadratic scan).

The structural safety net (strict mode) is an ALLOWLIST: patterns only know the shapes they were
written for, so the net keeps only what is PROVEN harmless and replaces everything else whole:

* Records with a template (:mod:`dmslog.report.template_shape`): the regex is matched again on the
  first line. Its literal text stays. A field stays only when proven: the regex allows only a finite
  set of literals (``True|False``), or :data:`PROVEN_FIELDS` names a validator that accepts the value
  (membership in a closed catalog set: operation names, log levels, catalog/case codes, booleans,
  explicit enumerations; or a number of at most 6 digits per run in a field named as a count, size or
  time, or declared int/float in the catalog; a numeric regex alone proves nothing). Identifier-named
  fields never stay. Every other
  field, uncaptured part and the text after the match becomes ``<VALUE-n>`` (or the marker of the
  identifier category its name announces). A template that is unknown or no longer matches makes the
  whole line one value. Continuation lines the template parses as data (dumps, key/value blocks,
  statistics, code lists) are replaced piece by piece, keeping only numbers in statistics and
  catalog/case codes in code lists; stack traces go through the free-text net.
* Free text (records without a template, stack traces, notes, exception messages, header and payload
  metadata, the profile name): a piece (text between blanks and ``= , ; | " ' < > ( ) [ ] { }``) stays
  only when it is a catalog or common word, a catalog name or code, a compound of catalog words
  (``System.TimeoutException``, ``WFM-081``), a date or time of day, a number of up to 6 digits
  glued to or followed by a unit (``120 ms``), or punctuation. A bare number, a capital letter, any
  other word is replaced.
  The value after an identifier label is replaced whole, punctuation included, whatever it looks like:
  it runs to ``, ; |``, the end of the line, another label or a stop word ("for", "after"...), never
  stopping because a word looks public (``customer monitor will``). A label is a word naming an
  identifier followed by ``:``/``=`` (``customerName=``, ``request_user_id:``, ``\\"user\\":``), or one of
  a few identifier nouns followed by a blank and a word that is not a stop word (``customer Monitor``).
  A free text that holds markup or JSON gets the payload net below instead of the allowlist.
* Payloads and structured text: a FAIL-CLOSED GATE first. The text is cut in regions (an XML element
  with all its content, a comment, a processing instruction, or a JSON value); a region keeps a
  skeleton only if it passes a strict well-formedness check (XML: balanced and properly nested,
  valid whole names, attributes separated by blanks with quoted values, no '<' or bad '&' in values
  or text, no DOCTYPE or other declaration, XML blanks only, AND acceptance by the stdlib expat
  parser, the authority, with namespaces checked and no entity expansion; JSON: the ``json`` module's
  strict rules, checked by a linear exception-free scanner and confirmed by the ``json`` module on
  the span). Cutting consumes COMPLETE strings ("..." and '...' with backslash escapes; an apostrophe
  inside a word is not a quote): nothing inside a string starts a region, and an unterminated string
  makes the rest of the text one value, and so does a string closed right against a word (ambiguous
  quoting). Arrays starting with numbers or literals are JSON too, and ANY failed JSON candidate,
  even one that only might be JSON (``[0x]``, ``[1/3]``), leaves a structure open: the rest of the
  text is one value (the state carries across lines, so nothing after it is trusted). The content
  of every quoted string between regions is one value too: in a structured text, string content
  never reaches the free-text allowlist. Every recovery (a failed JSON candidate, an ambiguous or
  unterminated string, an invalid region, a stray '<'/'>') goes through ONE "rest of the text is
  data" path, a record's first line and continuation are read as ONE text (``record_spans``), and a
  final guard replaces whole any piece bound for the allowlist that still holds a quote, a brace or a bracket.
  Lone surrogates never crash the gate (expat refuses the region) and never reach the output. The
  JSON scanner refuses whatever the ``json`` module refuses (integers past Python's digit limit,
  nesting past 500 levels). JSON inside an
  XML text node or attribute value keeps a skeleton only when that exact text is valid JSON. The
  first region that fails, or whose end is not found (a truncated payload), is ONE value up to the
  end of the text, and so is everything after a stray '<' or '>' between regions. Markup written
  escaped in free text (``&lt;Name&gt;``) goes through the same gate on the decoded text. Inside a
  validated region the skeleton is DEFAULT-DENY at the character level. The only text that survives
  is markup syntax (``< > </ /> = <!-- --> <![CDATA[ ]]> <? ?> <!``, the quotes of attribute values and
  of JSON strings, JSON ``{ } [ ] , :``), element/attribute names and JSON keys in the ALLOWED NAME SET
  (XML built-ins, the payload field names, and digit-free names made only of catalog words, under an
  optional short namespace prefix), the JSON literals true/false/null in a JSON value position, and
  blanks. Everything else (text nodes, attribute values, comments, CDATA, processing instructions,
  doctype, strings, numbers, punctuation, entities) is replaced; each maximal run is one value. A
  name only counts in its structural position and as a WHOLE valid token (``<Name&#65;/>``,
  ``123Name=`` are data, consumed whole): an element name when the tag is closed by ``>``/``/>`` and
  is self-closing or properly paired with a complete tag in structural context (tags inside comments,
  CDATA, PIs, attribute values, JSON strings or quoted plain text never pair); an attribute name when
  ``=`` follows; a JSON key when ``:`` follows. Orphan words in tags, broken or unpaired tags
  (``value <Name> x``) and unquoted values holding quotes or entities (to the end of the tag) are
  data. Blanks are ONE set shared by the detection and the scanner: every Unicode blank (``\\v``,
  ``\\f``, NBSP, U+2000-200A, U+2028/9, U+3000) and U+180E, U+200B-200D, U+2060, BOM.
* Whole-value replacements keep their full extent, punctuation included; punctuation left over
  between a strict span and another marker (``(INC1)``) joins that marker, keyed on the value alone.
* Event keys are identifiers and are replaced whole. Every header text that comes from the profile or
  the case (label, machine, profile name, pack, collection kind) goes through the redactor; the ERS
  time and variant are shown only in their known shapes.
* Automatic exceptions (catalog names, validated codes) only correct a SHAPE rule or a user term; they
  never protect a labelled value, a payload field, a template field or anything the allowlist
  replaces. User exceptions protect every occurrence of their text.
* The strict spans fill the gaps the other rules leave: a GUID inside a labelled value keeps its
  ``<GUID-n>`` marker. A category switched off takes its labels and template fields with it.

Maintenance of :data:`PROVEN_FIELDS`: a new template needs nothing (its variable fields are replaced
by default). An entry is added only with a validator that proves the value harmless; "*" (any
template) only for membership and numeric validators; identifier-named fields never get one.

Known limits (strict): free text without markup keeps catalog/common words, so a person whose name is
such a word ("Will", "Grant") and that has no label, template field or profile term stays; numbers
glued to a unit and dates stay in free text; element/attribute names and JSON keys made only of
catalog words stay (a map keyed by such a word shows it), and so do short namespace prefixes; JSON
syntax characters (``{ } [ ] , :``) inside XML text stay; the field rules (payload categories) follow
formats nested down to 3 levels (the default-deny net has no depth). Standard mode: the pattern
layer alone; free text with no label, field or term is not recognized, a lowercase name after a
label keeps its second word, a label word without ``:``/``=`` is not a label.

Engineering rules:

* Every regular expression is bounded (no unbounded repeats, no nested repeats, see
  ``tests/unit/test_redact.py``); lookups that regexes cannot do (is this really an IP?) are plain
  Python checks on the match. All passes are linear in the text (see the 425k-character tests).
* Memory is bounded: at most :data:`MAX_DISTINCT` distinct values per redactor; after that new
  values get the generic marker ``<LABEL-*>`` and are counted in ``overflow``. A text longer than
  :data:`MAX_TEXT_CHARS` is replaced as a whole.
* Payload structure is read by a small tolerant scanner, not a parser: truncated or malformed XML
  and JSON still get their element rules, and the regex rules always run on the whole text.
* The value map (marker -> original) stays in this process and, on request, in a file inside the
  data directory (:func:`write_value_map`); it is never part of the exported text.
"""

from __future__ import annotations

import bisect
import ipaddress
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import tomllib
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

PROFILE_FILENAME = "redaction.toml"
MAP_FOLDER = "redaction-maps"
PROFILE_VERSION = 1
DEFAULT_PROFILE_NAME = "default"
MODES = ("strict", "standard")
DEFAULT_MODE = "strict"

CATEGORIES = (
    "guid", "gid", "ticket", "mrid", "plate", "host", "ip", "user", "url", "path", "email", "phone", "person",
)  # fmt: skip
LABELS = {
    "guid": "GUID", "gid": "GID", "ticket": "TICKET", "mrid": "MRID", "plate": "PLATE", "host": "HOST",
    "ip": "IP", "user": "USER", "url": "URL", "path": "PATH", "email": "EMAIL", "phone": "PHONE",
    "person": "PERSON", "term": "TERM", "value": "VALUE",
}  # fmt: skip
TERM = "term"  # not a switchable category: user terms are always applied when present
VALUE = "value"  # not a switchable category: what the strict nets replace without knowing its kind

DEFAULT_PAYLOAD_NAMES = (
    "Name", "FirstName", "LastName", "FullName", "CustomerName", "ContactName", "Phone", "PhoneNumber",
    "Telephone", "Mobile", "Email", "Address", "StreetAddress", "Street", "StreetDetail", "City", "Town",
    "Neighborhood", "PostalCode", "ZipCode", "Document", "Cpf", "Cnpj", "Comments", "Remarks", "Notes",
)  # fmt: skip

MAX_TERMS = 200
MAX_EXCEPTIONS = 1000
MAX_PAYLOAD_NAMES = 200
MIN_TERM_CHARS = 2
MAX_TERM_CHARS = 128
MAX_EXCEPTION_CHARS = 256
MAX_DISTINCT = 50_000  # distinct values per redactor
MAX_STORED_VALUE_CHARS = 4096  # longest original kept in the local value map
MAX_TEXT_CHARS = 16_000_000  # a longer text is replaced as a whole (fail closed)
MAX_XML_DEPTH = 200
MAX_NESTING = 3  # JSON inside XML text inside JSON strings...: levels followed by the structured rules
_TOKEN_CATEGORIES = frozenset({"url", "path", "email"})
_TOKEN_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_%\\/.~+@:=&?#-")
MAX_JSON_DEPTH = 200

# Tie-break between spans that start at the same character (lower wins).
_PRIO = {
    TERM: 0, "person_element": 0, "url": 1, "email": 2, "path": 3, "guid": 4, "ticket": 5, "mrid": 6,
    "gid": 7, "plate": 8, "ip": 9, "phone": 10, "user": 11, "host": 12, "person": 13, VALUE: 50,
}  # fmt: skip

_NAME_OK = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}\Z")
_ELEMENT_NAME_OK = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]{0,63}\Z")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class ProfileError(ValueError):
    """A redaction profile is invalid. ``problem`` and ``field`` never contain user values."""

    def __init__(self, problem: str, field: str = "") -> None:
        super().__init__(f"{problem}: {field}")
        self.problem = problem
        self.field = field


# --------------------------------------------------------------------------- profile


@dataclass(frozen=True, slots=True)
class Profile:
    name: str = DEFAULT_PROFILE_NAME
    categories: Mapping[str, bool] = field(default_factory=lambda: {c: True for c in CATEGORIES})
    terms_word: tuple[str, ...] = ()
    terms_literal: tuple[str, ...] = ()
    exceptions: tuple[str, ...] = ()
    payload_names: tuple[str, ...] = DEFAULT_PAYLOAD_NAMES
    mode: str = DEFAULT_MODE  # "strict" (structural nets on) or "standard" (pattern rules only)

    def enabled(self, category: str) -> bool:
        return bool(self.categories.get(category, True))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "categories": {c: self.enabled(c) for c in CATEGORIES},
            "terms": {"word": list(self.terms_word), "literal": list(self.terms_literal)},
            "exceptions": list(self.exceptions),
            "payload_names": list(self.payload_names),
            "mode": self.mode,
        }


def default_profile() -> Profile:
    return Profile()


def _string_list(raw: Any, field_name: str, limit: int, min_chars: int, max_chars: int) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ProfileError("bad_list", field_name)
    out: list[str] = []
    for item in raw:
        value = item.strip()
        if not min_chars <= len(value) <= max_chars or _CONTROL.search(value):
            raise ProfileError("bad_entry", field_name)
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ProfileError("bad_entry", field_name) from None
        if value not in out:
            out.append(value)
    if len(out) > limit:
        raise ProfileError("too_many", field_name)
    return tuple(out)


def profile_from_dict(data: Any) -> Profile:
    """Validate a profile received as JSON or read from TOML; missing parts take the defaults."""
    if not isinstance(data, Mapping):
        raise ProfileError("not_object", "profile")
    name = data.get("name", DEFAULT_PROFILE_NAME)
    if not isinstance(name, str) or not _NAME_OK.match(name):
        raise ProfileError("bad_name", "name")
    flags = {c: True for c in CATEGORIES}
    raw_categories = data.get("categories")
    if raw_categories is not None:
        if not isinstance(raw_categories, Mapping):
            raise ProfileError("bad_section", "categories")
        for key, value in raw_categories.items():
            if key not in flags:
                raise ProfileError("unknown_category", "categories")
            if not isinstance(value, bool):
                raise ProfileError("bad_flag", f"categories.{key}")
            flags[key] = value
    raw_terms = data.get("terms")
    if raw_terms is not None and not isinstance(raw_terms, Mapping):
        raise ProfileError("bad_section", "terms")
    raw_terms = raw_terms or {}
    words = _string_list(raw_terms.get("word"), "terms.word", MAX_TERMS, MIN_TERM_CHARS, MAX_TERM_CHARS)
    literals = _string_list(
        raw_terms.get("literal"), "terms.literal", MAX_TERMS, MIN_TERM_CHARS, MAX_TERM_CHARS
    )
    exceptions = _string_list(data.get("exceptions"), "exceptions", MAX_EXCEPTIONS, 1, MAX_EXCEPTION_CHARS)
    if "payload_names" in data and data["payload_names"] is not None:
        names = _string_list(data["payload_names"], "payload_names", MAX_PAYLOAD_NAMES, 1, 64)
        if any(not _ELEMENT_NAME_OK.match(n) for n in names):
            raise ProfileError("bad_element_name", "payload_names")
    else:
        names = DEFAULT_PAYLOAD_NAMES
    mode = data.get("mode", DEFAULT_MODE)
    if mode is None:
        mode = DEFAULT_MODE
    if not isinstance(mode, str) or mode not in MODES:
        raise ProfileError("bad_mode", "mode")
    return Profile(name, flags, words, literals, exceptions, names, mode)


def _toml_string(value: str) -> str:
    # A JSON string is a valid TOML basic string (same escapes); non-ASCII stays literal.
    return json.dumps(value, ensure_ascii=False)


def _toml_list(values: Iterable[str]) -> str:
    return "[" + ", ".join(_toml_string(v) for v in values) + "]"


def dump_profile_toml(profile: Profile) -> str:
    """The profile as TOML text (the file lives in the data directory, outside the repository)."""
    lines = [
        "# dmslog redaction profile. Local to this machine: do not commit or share this file.",
        f"version = {PROFILE_VERSION}",
        f"name = {_toml_string(profile.name)}",
        f"mode = {_toml_string(profile.mode)}",
        f"exceptions = {_toml_list(profile.exceptions)}",
        f"payload_names = {_toml_list(profile.payload_names)}",
        "",
        "[categories]",
        *(f"{c} = {'true' if profile.enabled(c) else 'false'}" for c in CATEGORIES),
        "",
        "[terms]",
        f"word = {_toml_list(profile.terms_word)}",
        f"literal = {_toml_list(profile.terms_literal)}",
        "",
    ]
    return "\n".join(lines)


def profile_path(data_dir: str | os.PathLike[str]) -> Path:
    return Path(data_dir) / PROFILE_FILENAME


def load_profile(data_dir: str | os.PathLike[str]) -> Profile:
    """The stored profile, or the default one (everything on) when there is no file.

    Raises :class:`ProfileError` when the file exists but is unreadable or invalid: exports must not
    silently fall back to weaker settings.
    """
    path = profile_path(data_dir)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return default_profile()
    except (OSError, UnicodeDecodeError):
        raise ProfileError("unreadable", PROFILE_FILENAME) from None
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        raise ProfileError("invalid_toml", PROFILE_FILENAME) from None
    version = data.get("version", PROFILE_VERSION)
    if not isinstance(version, int) or isinstance(version, bool) or version > PROFILE_VERSION:
        raise ProfileError("bad_version", "version")
    return profile_from_dict(data)


_SAVE_LOCK = threading.Lock()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        try:
            tmp.chmod(0o600)  # POSIX only; a no-op where permissions do not apply
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def save_profile(data_dir: str | os.PathLike[str], profile: Profile) -> None:
    """Write ``<data dir>/redaction.toml`` atomically (UTF-8, LF)."""
    with _SAVE_LOCK:
        _atomic_write(profile_path(data_dir), dump_profile_toml(profile).encode("utf-8"))


def write_value_map(
    data_dir: str | os.PathLike[str], case: str, redactor: Redactor, stamp: str | None = None
) -> Path | None:
    """Save the marker -> original map of one export under ``<data dir>/redaction-maps/``.

    Returns the file written, or None when nothing was replaced. The file name is opaque (random):
    it carries neither the case nor any value; the case and the time are stored inside the file. The
    file is for the user to revert a marker; it must never be shared or exported.
    """
    values = redactor.value_map()
    if not values:
        return None
    when = stamp or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = Path(data_dir) / MAP_FOLDER / f"{secrets.token_hex(12)}.map.json"
    body = json.dumps({"case": case, "created": when, "values": values}, ensure_ascii=False, indent=1)
    _atomic_write(path, body.encode("utf-8", errors="backslashreplace"))  # lone surrogates as \\udxxx
    return path


# --------------------------------------------------------------------------- patterns

# Two kinds of rules. Fixed-size shapes (GUID, IPs, phones, plates) are one bounded regex. Values of
# unknown length (URLs, paths, e-mails, host names, labelled values) are found in two steps: a
# bounded PREFIX regex says where the value starts, then a TAIL class runs to the end of the token
# with no length limit, so a long value is never cut and left half visible. Tails are single
# character classes (no nested repeats); prefixes start behind a look-behind of their own class so
# the work stays linear. Python checks (a valid IP, a known top-level domain) run on the result.

_LABEL_TAIL_CHARS = r"[^\s\"'\],;<>()]*"
# ONE set of separators for the structure net: every Unicode blank (``\\s`` = ``str.isspace``) and these
# invisible characters. The detection regex and the scanner (``_is_blank``) are both built from it.
_ZERO_WIDTH_CHARS = "\u180e\u200b\u200c\u200d\u2060\ufeff"
_BLANK_CLASS = "[\\s" + _ZERO_WIDTH_CHARS + "]"
_RX = {
    "guid": re.compile(
        r"(?<![0-9A-Za-z])(?:[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
        r"|[0-9A-Fa-f]{32})(?![0-9A-Za-z])"
    ),
    "ticket": re.compile(
        r"(?<![A-Za-z0-9])(?:INC|PROB|PRB|CHG|RITM)[ _-]{0,2}[0-9]+(?![0-9A-Za-z])", re.IGNORECASE
    ),
    "plate": re.compile(r"(?<![A-Za-z0-9])[A-Z]{3}-?[0-9][A-Z0-9][0-9]{2}(?![A-Za-z0-9])", re.IGNORECASE),
    "mrid_label": re.compile(r"(?<![A-Za-z0-9])m[_-]?rid(?:\(s\))?[ \t\r\n]{0,8}[:=]", re.IGNORECASE),
    "mrid_quoted": re.compile(r"(?<![A-Za-z0-9])m[_-]?rid[ \t]{1,3}(?=[\"'])", re.IGNORECASE),
    "mrid_tail": re.compile(r"[A-Za-z0-9_.-]*"),
    "mrid_xml": re.compile(r"<(?:[A-Za-z0-9_.-]{1,32}:)?mrid>", re.IGNORECASE),
    "xml_value_tail": re.compile(r"[^<>]*"),
    "gid": re.compile(
        r"(?<![A-Za-z0-9])gid"
        r"(?:[ \t\r\n]{0,8}[:=]|[ \t]{1,3}(?=[\"'`\[])|[ \t]{1,3}(?=[0-9]{6}))",
        re.IGNORECASE,
    ),
    "gid_xml": re.compile(r"<(?:[A-Za-z0-9_.-]{1,32}:)?(?:gid|globalid)>(?=[0-9])", re.IGNORECASE),
    "digits_tail": re.compile(r"[0-9]*"),
    "host_label": re.compile(
        r"(?<![A-Za-z0-9])(?:host(?:name)?|server|machine|computer|node|endpoint)[ \t\r\n]{0,8}[:=]",
        re.IGNORECASE,
    ),
    "host_tail": re.compile(r"[A-Za-z0-9._-]*"),
    "host_fqdn": re.compile(r"(?<![A-Za-z0-9@.-])(?=[A-Za-z0-9])"),
    "fqdn_tail": re.compile(r"[A-Za-z0-9.-]+"),
    "host_unc": re.compile(r"\\\\(?=[A-Za-z0-9])"),
    "ipv4": re.compile(
        r"(?<![0-9A-Za-z.])[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}(?![0-9A-Za-z]|\.[0-9])"
    ),
    "ipv6": re.compile(r"(?<![0-9A-Za-z:.])[0-9A-Fa-f:]{2,45}(?![0-9A-Za-z.])"),
    "user_label": re.compile(
        r"(?<![A-Za-z0-9])(?:user(?:[ _]?name|[ _]?id)?|login|logon|account|usuario|usu.rio)"
        r"[ \t\r\n]{0,8}[:=]",
        re.IGNORECASE,
    ),
    "label_tail": re.compile(_LABEL_TAIL_CHARS),
    "user_quoted": re.compile(r"(?<![A-Za-z0-9])user[ \t]{1,3}(?=[\"'])", re.IGNORECASE),
    "quoted_tail": re.compile(r"[^\"'\s]*"),
    "user_domain": re.compile(r"(?<![A-Za-z0-9_\\-])[A-Za-z][A-Za-z0-9_-]+\\(?=[A-Za-z0-9_.$-])"),
    "user_domain_tail": re.compile(r"[A-Za-z0-9_.$-]*"),
    "url": re.compile(
        r"(?<![A-Za-z0-9+.-])(?:https?|ftps?|sftp|wss?|net\.tcp|net\.pipe|file|ldaps?|tcp|amqps?|mqtts?|smtps?)://",
        re.IGNORECASE,
    ),
    "url_tail": re.compile(r"[^\s<>\"'`\\^{}|]*"),
    "email": re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\."),
    "email_tail": re.compile(r"[A-Za-z0-9.-]*"),
    "path_win": re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"),
    "path_tail": re.compile(r"[^\s\"'<>|*?]*"),
    "path_win_quoted": re.compile(r"(?<=[\"'])[A-Za-z]:[\\/]"),
    "path_quoted_tail": re.compile(r"[^\"'<>|*?\r\n]*"),
    "path_win_element": re.compile(r"(?<=>)[A-Za-z]:[\\/]"),
    "path_element_tail": re.compile(r"[^<>\r\n|*?\"]*"),
    "path_unc": re.compile(r"\\\\[A-Za-z0-9][A-Za-z0-9._$-]*\\"),
    "path_posix": re.compile(r"(?<![A-Za-z0-9_<:.,)%&/\\-])/[A-Za-z0-9._@+~-]+/"),
    "posix_tail": re.compile(r"[A-Za-z0-9._@+~/-]*"),
    "phone": re.compile(
        r"(?<![0-9A-Za-z+])(?:\+[0-9]{1,3}[ .-]?\(?[0-9]{2,3}\)?[ .-]?[0-9]{4,5}[ .-]?[0-9]{4}"
        r"|\([0-9]{2}\) ?9?[0-9]{4}[ -]?[0-9]{4}"
        r"|(?:[0-9]{2}[ -])?9[0-9]{4}-[0-9]{4}"
        r"|[0-9]{2}[ -][0-9]{4}-[0-9]{4})(?![0-9A-Za-z])"
    ),
    "person_label": re.compile(
        r"(?<![A-Za-z0-9])(?:customer|contact|caller|nome|cliente|contato|solicitante|address|endere.o"
        r"|street|logradouro|bairro|city|cidade|munic.pio|cep)(?:[ _]?name)?"
        r"[ \t\r\n]{0,8}[:=]",
        re.IGNORECASE,
    ),
    "person_tail": re.compile(r"[^\r\n\"'<>;|]*"),
    "xml_name": re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]*"),
    "json_break": re.compile(r"[\\\"]"),
    "quote_stop_dq": re.compile(r'[\\"]'),
    "quote_stop_sq": re.compile(r"[\\']"),
    "quote_stop_bq": re.compile(r"[\\`]"),
    "name_particle": re.compile(
        r"(?:da|de|do|das|dos|di|del|della|du|van|von|der|den|la|le|e|y)[ \t]{1,8}(?=[^\W\d_])", re.IGNORECASE
    ),
    # strict nets
    "strict_label": re.compile(
        r"(?<![\w])([^\W\d_][\w.-]{0,47})(?:\\?[\"']?[ \t]{0,8}[:=]|[ \t]{1,3}(?=\\?[\"'`\[]))"
    ),
    "strict_word_label": None,  # filled below from the label words
    "chunk": re.compile(r"\S+"),
    "zero_runs": re.compile(rb"\x00+"),
    # markup, or JSON with any JSON whitespace (no length cap: '{   \n  "k"  \n : 1}' is JSON too)
    "open_tag": re.compile(r"<([A-Za-z_][A-Za-z0-9_.:-]*)"),
    "close_tag": re.compile(r"</([A-Za-z_][A-Za-z0-9_.:-]*)"),
    # where a region of markup or JSON may start (the gate validates it from there)
    # region cutting: candidates, XML start, JSON starts (strong: surely JSON; weak: maybe JSON)
    "cut": re.compile(r"[\"'<{\[]"),
    "json_string_stop": re.compile(r'["\\\x00-\x1f]'),
    "hex4": re.compile(r"[0-9A-Fa-f]{4}"),
    "json_char": re.compile(r'[{}\[\]"]'),
    # group 1: the digits of an integer (no fraction, no exponent), checked against Python's digit limit
    "json_number": re.compile(
        r"-?(?:(0|[1-9][0-9]*)(?![.eE])|(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)(?![0-9A-Za-z_.])"
    ),
    "json_literal": re.compile(r"(?:true|false|null)(?![0-9A-Za-z_])"),
    "xml_start": re.compile(r"<[A-Za-z_!?]"),
    "json_strong": re.compile(r"[{\[]" + _BLANK_CLASS + r"*[\"{\[\]}]"),
    "json_weak": re.compile(r"[{\[]" + _BLANK_CLASS + r"*[-0-9tfn]"),
    "xml_ref": re.compile(r"&(?:[A-Za-z_][A-Za-z0-9_.-]{0,63}|#[0-9]{1,7}|#x[0-9A-Fa-f]{1,6});"),
    "xml_text_stop": re.compile(r"[<&]"),
    "angle": re.compile(r"[<>]"),
    "allowlist_guard": re.compile(r"[\"{}\[\]]|(?<![A-Za-z0-9])'|'(?![A-Za-z0-9])"),
    "surrogate": re.compile("[\ud800-\udfff]"),
    "structured_hint": re.compile(
        # every start the cutter may take for JSON (strong or weak, round 13), so a broken JSON-like text
        # always goes through the gate and never straight to the free-text allowlist
        r"<[A-Za-z_!?/]|\\?\"" + _BLANK_CLASS + r"*:|[{\[]" + _BLANK_CLASS + r"*[\"'{\[\]}\-0-9tfn]"
    ),
    "blank": re.compile(_BLANK_CLASS),
    "ns_prefix": re.compile(r"[A-Za-z]{1,8}[0-9]{0,2}"),
    "plain_name": re.compile(r"[A-Za-z_][A-Za-z_.-]{0,63}"),
    "piece": re.compile(r"[^\s=,;|\"'<>(){}\[\]]+"),
    "cell": re.compile(r"[^\s,;|=]+"),
    "clock": re.compile(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:[.,][0-9]{1,7})?)?)?"
        r"|[0-9]{1,2}:[0-9]{2}(?::[0-9]{2}(?:[.,][0-9]{1,7})?)?"
    ),
    "compound": re.compile(r"[A-Za-z0-9_.:/-]+"),
    "digit_runs": re.compile(r"[0-9]+"),
    # escapes that can hide letters inside words the token rule keeps (%41 and &#65; look like numbers)
    "hidden_escape": re.compile(r"%[0-9A-Fa-f]{2}|&#[xX]?[0-9A-Fa-f]{1,7};"),
    "word": re.compile(r"\w+"),
    "label_parts": re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[^\W\d_A-Za-z]+"),
    "unit": re.compile(r"[0-9]{1,6}(?:ms|s|sec|secs|min|mins|h|hr|hrs|kb|mb|gb|b|bytes|x)\Z"),
    "nest_trigger": re.compile(r"[<{\[]|&lt;|&#0*60;|&#[xX]0*3[cC];|\\u003[cC]|%3[cC]", re.IGNORECASE),
}

_KNOWN_TLDS = frozenset(
    "com net org edu gov int mil local lan corp intra internal intranet br io info biz".split()
)
_TRIM_PUNCT = ".,;:!?)]}>'\""
_TRIM_LABEL = ".-"


Span = tuple[int, int]
SpanFinder = Callable[[str], Iterator[Span]]
Check = Callable[[str, int, int], bool]


def _finder(rx: re.Pattern[str], check: Check | None = None) -> SpanFinder:
    """Spans of a fixed-size shape: the whole match."""

    def find(text: str) -> Iterator[Span]:
        for match in rx.finditer(text):
            start, end = match.span()
            if check is None or check(text, start, end):
                yield start, end

    return find


def _scan(
    prefix: re.Pattern[str],
    tail: re.Pattern[str],
    *,
    from_start: bool,
    trim: str = "",
    check: Check | None = None,
) -> SpanFinder:
    """Spans of a value of unknown length: ``prefix`` finds where it starts, ``tail`` runs to the end of
    the token. The value starts at the prefix (``from_start``) or right after it, and ends where the
    tail ends, whatever its length; the next search resumes after it, so the work stays linear."""

    def find(text: str) -> Iterator[Span]:
        pos = 0
        while True:
            match = prefix.search(text, pos)
            if match is None:
                return
            end = tail.match(text, match.end()).end()
            start = match.start() if from_start else match.end()
            stop = end
            while stop > start and text[stop - 1] in trim:
                stop -= 1
            if stop > start and (check is None or check(text, start, stop)):
                yield start, stop
            pos = max(end, match.start() + 1)

    return find


_QUOTE_STOPS = {'"': "quote_stop_dq", "'": "quote_stop_sq", "`": "quote_stop_bq"}


def _closing_quote(text: str, j: int, quote: str) -> int:
    """Index of the quote that closes a value opened just before ``j`` (backslash escapes honoured),
    or ``len(text)`` when it is never closed. No length limit: the value runs to its real end."""
    stop = _RX[_QUOTE_STOPS[quote]]
    size = len(text)
    while True:
        hit = stop.search(text, j)
        if hit is None:
            return size
        if hit.group() == quote:
            return hit.start()
        j = hit.start() + 2
        if j >= size:
            return size


def _value_extent(text: str, i: int) -> tuple[int, int, bool] | None:
    """Where a quoted or bracketed value that starts at ``i`` lies: (start, end, closed). A value that
    is never closed runs to the end of the text (fail closed: never to the end of the line only)."""
    size = len(text)
    char = text[i] if i < size else ""
    if char in ('"', "'", "`") and char:
        close = _closing_quote(text, i + 1, char)
        return i + 1, close, close < size
    if char == "[":
        close = text.find("]", i + 1)
        return (i + 1, size, False) if close < 0 else (i + 1, close, True)
    return None


def _labelled(
    prefix: re.Pattern[str],
    bare: re.Pattern[str],
    *,
    trim: str = "",
    quote_only: bool = False,
    capitalized_words: bool = False,
    digit_groups: bool = False,
) -> SpanFinder:
    """Spans of the value after a label (``user:``, ``mRID =``...). Blanks, even line breaks, may
    separate the label from the value. A quoted value runs to its closing quote (backslash escapes
    honoured, any length) and a bracketed one (``[a, b]``) to its closing bracket; a value that is
    never closed runs to the end of the text. A bare value is what ``bare`` accepts; with
    ``capitalized_words`` a name goes on over blanks with capitalized words and lowercase particles
    (``Ana  da Silva``)."""

    def find(text: str) -> Iterator[Span]:
        size = len(text)
        pos = 0
        while True:
            match = prefix.search(text, pos)
            if match is None:
                return
            i = match.end()
            while i < size and text[i] in " \t\r\n":
                i += 1
            extent = _value_extent(text, i)
            if extent is not None:
                start, end, closed = extent
                after = end + 1 if closed else end
            elif quote_only:
                pos = max(match.end(), match.start() + 1)
                continue
            else:
                start, end = i, bare.match(text, i).end()
                if capitalized_words and start < end and text[start].isupper():
                    end = _name_end(text, end, bare)
                while (
                    digit_groups
                    and end > start
                    and text[end : end + 1] in (" ", "-")
                    and text[end + 1 : end + 2].isdigit()
                ):
                    end = bare.match(text, end + 1).end()  # "123456 789012": one value in groups
                after = end
            stop = end
            while stop > start and text[stop - 1] in trim:
                stop -= 1
            if stop > start:
                yield start, stop
            pos = max(after, match.end(), match.start() + 1)

    find.labelled = True  # type: ignore[attr-defined]  # a labelled value is an identifier by its label
    return find


def _name_end(text: str, end: int, bare: re.Pattern[str]) -> int:
    """End of a capitalized name that starts before ``end``: it goes on over runs of blanks (not line
    breaks) with words that start with a capital, and over lowercase particles (``da``, ``van``)
    followed by such a word."""
    size = len(text)
    while True:
        k = end
        while k < size and text[k] in " \t\u00a0":
            k += 1
        if k == end or k >= size:
            return end
        if not text[k].isupper():
            particle = _RX["name_particle"].match(text, k)
            if particle is None or not text[particle.end()].isupper():
                return end
            k = particle.end()
        nxt = bare.match(text, k).end()
        if nxt <= k:
            return end
        end = nxt


def _ipv4_ok(text: str, start: int, end: int) -> bool:
    parts = text[start:end].split(".")
    if len(parts) != 4 or any(not p.isdigit() or int(p) > 255 for p in parts):
        return False
    return "version" not in text[max(0, start - 16) : start].casefold()


def _ipv6_ok(text: str, start: int, end: int) -> bool:
    candidate = text[start:end].rstrip(":")
    if candidate.count(":") < 2:
        return False
    try:
        ipaddress.IPv6Address(candidate)
    except ValueError:
        return False
    return True


_NAMESPACE_ROOTS = frozenset({"system", "microsoft", "mscorlib", "windows"})


def _fqdn_ok(text: str, start: int, end: int) -> bool:
    labels = text[start:end].split(".")
    if len(labels) < 2 or not all(labels) or labels[-1].casefold() not in _KNOWN_TLDS:
        return False
    return labels[0].casefold() not in _NAMESPACE_ROOTS  # System.Net, Microsoft.Com...: .NET namespaces


def _email_ok(text: str, start: int, end: int) -> bool:
    last = text[start:end].rsplit(".", 1)[-1]
    return len(last) >= 2 and last.isalpha()


def _unc_host_ok(text: str, start: int, end: int) -> bool:
    return text[end : end + 1] == "\\"


def _domain_user_ok(text: str, start: int, end: int) -> bool:
    following = text[end : end + 1]
    return not (following.isalnum() or following == "\\")


def _ipv6_finder(text: str) -> Iterator[Span]:
    for match in _RX["ipv6"].finditer(text):
        start, end = match.span()
        while end > start and text[end - 1] == ":":
            end -= 1
        if end > start and _ipv6_ok(text, start, end):
            yield start, end


_FINDERS: dict[str, list[SpanFinder]] = {
    "guid": [_finder(_RX["guid"])],
    "ticket": [_finder(_RX["ticket"])],
    "mrid": [
        _labelled(_RX["mrid_label"], _RX["mrid_tail"], trim=_TRIM_LABEL),
        _labelled(_RX["mrid_quoted"], _RX["mrid_tail"], trim=_TRIM_LABEL, quote_only=True),
        _scan(_RX["mrid_xml"], _RX["xml_value_tail"], from_start=False),
    ],
    "gid": [
        _labelled(_RX["gid"], _RX["digits_tail"], digit_groups=True),
        _scan(_RX["gid_xml"], _RX["digits_tail"], from_start=False),
    ],
    "plate": [_finder(_RX["plate"])],
    "host": [
        _labelled(_RX["host_label"], _RX["host_tail"], trim=_TRIM_LABEL),
        _scan(_RX["host_fqdn"], _RX["fqdn_tail"], from_start=True, trim=".-", check=_fqdn_ok),
        _scan(_RX["host_unc"], _RX["host_tail"], from_start=False, check=_unc_host_ok),
    ],
    "ip": [_finder(_RX["ipv4"], _ipv4_ok), _ipv6_finder],
    "user": [
        _labelled(_RX["user_label"], _RX["label_tail"], trim=_TRIM_PUNCT, capitalized_words=True),
        _labelled(_RX["user_quoted"], _RX["quoted_tail"], quote_only=True),
        _scan(
            _RX["user_domain"],
            _RX["user_domain_tail"],
            from_start=True,
            trim=".-",
            check=_domain_user_ok,
        ),
    ],
    "url": [_scan(_RX["url"], _RX["url_tail"], from_start=True, trim=_TRIM_PUNCT)],
    "path": [
        _scan(_RX["path_win"], _RX["path_tail"], from_start=True, trim=_TRIM_PUNCT),
        _scan(_RX["path_win_quoted"], _RX["path_quoted_tail"], from_start=True),
        _scan(_RX["path_win_element"], _RX["path_element_tail"], from_start=True),
        _scan(_RX["path_unc"], _RX["path_tail"], from_start=True, trim=_TRIM_PUNCT),
        _scan(_RX["path_posix"], _RX["posix_tail"], from_start=True, trim=_TRIM_PUNCT),
    ],
    "email": [_scan(_RX["email"], _RX["email_tail"], from_start=True, trim=".-", check=_email_ok)],
    "phone": [_finder(_RX["phone"])],
    "person": [_labelled(_RX["person_label"], _RX["person_tail"], trim=" \t" + _TRIM_PUNCT)],
}


# --------------------------------------------------------------------------- escaped text

# Sensitive values can be written percent-encoded (%40), as JSON/Java escapes (@) or as XML/HTML
# character references (&#64;). The rules run on a DECODED view of the text; the spans found there
# are mapped back to the original characters, which are what gets replaced.

_ESCAPES = re.compile(
    r"(?:%[0-9A-Fa-f]{2})+"
    r"|\\u[dD][89abAB][0-9A-Fa-f]{2}\\u[dD][c-fC-F][0-9A-Fa-f]{2}"
    r"|\\u[0-9A-Fa-f]{4}"
    r"|&#[xX][0-9A-Fa-f]{1,6};"
    r"|&#[0-9]{1,7};"
    r"|&(?:amp|lt|gt|quot|apos);"
)
_ESCAPES_JSON = re.compile(_ESCAPES.pattern + r'|\\[/"\\bfnrt]')  # the JSON string escapes too
_JSON_ESCAPED = {"/": "/", '"': '"', "\\": "\\", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_NAMED = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&apos;": "'"}
MAX_DECODE_PASSES = 3


def _decode_token(token: str) -> list[tuple[str, int]]:
    """(decoded character, characters of ``token`` it stands for) pairs; the token itself when it
    cannot be decoded."""
    if token[0] == "%":
        raw = bytes(int(token[i + 1 : i + 3], 16) for i in range(0, len(token), 3))
        try:
            chars = raw.decode("utf-8")
        except UnicodeDecodeError:
            return [(chr(b), 3) if b < 0x80 else (token[3 * k : 3 * k + 3], 0) for k, b in enumerate(raw)]
        return [(c, 3 * len(c.encode("utf-8"))) for c in chars]
    if token[0] == "\\":
        if len(token) == 2:
            return [(_JSON_ESCAPED[token[1]], 2)]
        if len(token) == 12:
            high, low = int(token[2:6], 16), int(token[8:12], 16)
            return [(chr(0x10000 + ((high - 0xD800) << 10) + (low - 0xDC00)), 12)]
        code = int(token[2:], 16)
        return [(chr(code), 6)] if not 0xD800 <= code <= 0xDFFF else [(token, 0)]
    if token in _NAMED:
        return [(_NAMED[token], len(token))]
    code = int(token[3:-1], 16) if token[2] in "xX" else int(token[2:-1])
    if code == 0 or code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
        return [(token, 0)]
    return [(chr(code), len(token))]


def _decode_pass(text: str, escapes: re.Pattern[str]) -> tuple[str, list[int], list[int]] | None:
    """One decoding pass: (decoded text, start of each decoded character in ``text``, end of each)."""
    if escapes.search(text) is None:
        return None
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    pos = 0
    for match in escapes.finditer(text):
        begin, finish = match.span()
        if begin > pos:
            out.append(text[pos:begin])
            starts.extend(range(pos, begin))
            ends.extend(range(pos + 1, begin + 1))
        cursor = begin
        for char, width in _decode_token(match.group()):
            if width == 0:  # kept as written: the whole original token, character by character
                for k, original in enumerate(char):
                    out.append(original)
                    starts.append(cursor + k)
                    ends.append(cursor + k + 1)
                cursor += len(char)
            else:
                out.append(char)
                starts.append(cursor)
                ends.append(cursor + width)
                cursor += width
        pos = finish
    if pos < len(text):
        out.append(text[pos:])
        starts.extend(range(pos, len(text)))
        ends.extend(range(pos + 1, len(text) + 1))
    return "".join(out), starts, ends


def decode_view(text: str, json_strings: bool = False) -> tuple[str, list[int], list[int]] | None:
    """The decoded view of ``text`` with maps back to its characters, or None when nothing is escaped.
    ``json_strings`` also undoes the JSON string escapes (``\\/``, ``\\"``, ``\\n``...)."""
    escapes = _ESCAPES_JSON if json_strings else _ESCAPES
    view: tuple[str, list[int], list[int]] | None = None
    current = text
    for _ in range(MAX_DECODE_PASSES):
        step = _decode_pass(current, escapes)
        if step is None:
            break
        decoded, starts, ends = step
        if view is None:
            view = step
        else:
            _, first_starts, first_ends = view
            view = (decoded, [first_starts[s] for s in starts], [first_ends[e - 1] for e in ends])
        current = decoded
    return view


def _decode_quick(text: str, json_strings: bool = True) -> str:
    """``text`` with its escapes decoded as :func:`decode_view` does, without position maps."""
    escapes = _ESCAPES_JSON if json_strings else _ESCAPES
    for _ in range(MAX_DECODE_PASSES):
        if escapes.search(text) is None:
            break
        text = escapes.sub(lambda m: "".join(c for c, _w in _decode_token(m.group())), text)
    return text


def decoded(text: str) -> str:
    """``text`` with its escapes decoded (the value used as the key of a marker)."""
    return _decode_quick(text, json_strings=False)


# --------------------------------------------------------------------------- payload scanner


def _local_name(name: str) -> str:
    return name.rsplit(":", 1)[-1].casefold()


def _text_span(text: str, start: int, end: int) -> Span | None:
    """The part of ``text[start:end]`` without surrounding whitespace, or None when empty."""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end) if end > start else None


# Built-in field names of the payloads and their category: the VALUE of such an element, attribute or
# JSON key (all descendant text included) is replaced whatever its shape, because the field says what
# it is. The profile's ``payload_names`` add person fields on top of these.
STRUCTURED_FIELDS = {
    "gid": ("gid", "globalid", "dmsgid"),
    "mrid": ("mrid",),
    "ticket": ("incident", "incidentid", "incidentnumber", "inc", "problem", "problemid", "prob", "ticket"),
    "plate": ("plate", "placa", "licenseplate", "vehicleplate"),
    "user": ("user", "username", "userid", "login", "account", "accountname"),
    "host": ("host", "hostname", "server", "machine", "computername"),
    "ip": ("ip", "ipaddress"),
    "url": ("url", "uri"),
    "email": ("email", "emailaddress", "mail"),
    "phone": ("phone", "phonenumber", "telephone", "mobile", "cellphone"),
}
FieldMap = Mapping[str, str]  # casefolded local name -> category


def field_map(profile: Profile) -> dict[str, str]:
    """name -> category for the structured rules of a profile (only categories that are on)."""
    mapping: dict[str, str] = {}
    if profile.enabled("person"):
        mapping.update(dict.fromkeys((n.casefold() for n in profile.payload_names), "person"))
    for category, names in STRUCTURED_FIELDS.items():
        if profile.enabled(category):
            mapping.update(dict.fromkeys(names, category))
    return mapping


def xml_name_spans(text: str, fields: FieldMap) -> Iterator[tuple[int, int, str]]:
    """(start, end, category) of the text and the attribute values of the elements whose local name is
    in ``fields``, with the category the field maps to.

    A linear, quote-aware scanner over the markup: no DTD, no entity resolution, no well-formedness
    requirement. Sensitivity is inherited: everything inside such an element (children, their text and
    attributes, comments) is replaced with the category of the outermost field. It fails closed: a tag
    or an attribute value that is never closed is replaced up to the end, and so is everything after
    the depth limit.
    """
    size = len(text)
    pos = 0
    stack: list[tuple[str, str | None]] = []
    while pos < size:
        lt = text.find("<", pos)
        inside = stack[-1][1] if stack else None
        if inside:
            span = _text_span(text, pos, size if lt < 0 else lt)
            if span:
                yield (*span, inside)
        if lt < 0:
            return
        for opener, closer in (("<!--", "-->"), ("<![CDATA[", "]]>"), ("<?", "?>")):
            if text.startswith(opener, lt):
                close = text.find(closer, lt + len(opener))
                if inside:
                    span = _text_span(text, lt + len(opener), size if close < 0 else close)
                    if span:
                        yield (*span, inside)
                pos = size if close < 0 else close + len(closer)
                break
        else:
            pos = yield from _xml_tag(text, lt, fields, stack)


def _xml_tag(
    text: str, lt: int, fields: FieldMap, stack: list[tuple[str, str | None]]
) -> Iterator[tuple[int, int, str]]:
    """Handle the tag at ``lt``: yields the spans to replace and returns the next position."""
    size = len(text)
    if text.startswith("<!", lt):  # DOCTYPE and friends
        gt = text.find(">", lt)
        return size if gt < 0 else gt + 1
    if text.startswith("</", lt):
        name = _RX["xml_name"].match(text, lt + 2)
        if name:
            local = _local_name(name.group())
            for index in range(len(stack) - 1, -1, -1):
                if stack[index][0] == local:
                    del stack[index:]
                    break
        gt = text.find(">", lt)
        return size if gt < 0 else gt + 1
    opened = _RX["xml_name"].match(text, lt + 1)
    if opened is None:  # "a < b": not a tag
        return lt + 1
    local = _local_name(opened.group())
    category = (stack[-1][1] if stack else None) or fields.get(local)
    i = opened.end()
    self_closing = False
    while i < size:
        char = text[i]
        if char == ">":
            i += 1
            break
        if char == "/" and text.startswith("/>", i):
            self_closing = True
            i += 2
            break
        attribute = _RX["xml_name"].match(text, i) if not (char.isspace() or char == "/") else None
        if attribute is None:
            i += 1
            continue
        value_category = category or fields.get(_local_name(attribute.group()))
        i = attribute.end()
        j = i
        while j < size and text[j].isspace():
            j += 1
        if j >= size or text[j] != "=":
            continue
        j += 1
        while j < size and text[j].isspace():
            j += 1
        if j < size and text[j] in "\"'":
            close = text.find(text[j], j + 1)
            end = size if close < 0 else close
            if value_category and end > j + 1:
                yield j + 1, end, value_category
            i = size if close < 0 else close + 1
        else:  # unquoted value
            k = j
            while k < size and not text[k].isspace() and text[k] != ">" and not text.startswith("/>", k):
                k += 1
            if value_category and k > j:
                yield j, k, value_category
            i = k
    if not self_closing:
        if len(stack) >= MAX_XML_DEPTH:
            yield i, size, category or "person"  # too deep to follow: the rest is replaced
            return size
        stack.append((local, category))
    return i


def _json_name(raw: str) -> str:
    """A JSON property name with its escapes decoded (``Name`` for ``\\u004eame``), case folded."""
    try:
        return json.loads('"' + raw + '"', strict=False).casefold()
    except ValueError:
        return decoded(raw).casefold()


def json_key_spans(text: str, fields: FieldMap) -> Iterator[tuple[int, int, str]]:
    """(start, end, category) of every string and scalar (numbers, booleans and null as text) inside the
    values of the JSON keys in ``fields``.

    A linear tokenizer: names are matched after decoding their escapes, and the rule covers the whole
    value (arrays and objects included, keys inside it too). A string that is never closed is
    replaced up to the end, and so is everything after the depth limit.
    """
    size = len(text)
    i = 0
    # frames: [is_object, category of the whole container or None, expecting_key, category of the current key]
    stack: list[list[Any]] = []

    def value_category(top: list[Any] | None) -> str | None:
        if top is None:
            return None
        return top[1] or (top[3] if top[0] and not top[2] else None)

    while i < size:
        char = text[i]
        top = stack[-1] if stack else None
        if char in " \t\r\n:":
            i += 1
        elif char == ",":
            if top is not None and top[0]:
                top[2], top[3] = True, None
            i += 1
        elif char == '"':
            j = i + 1
            while True:
                hit = _RX["json_break"].search(text, j)
                if hit is None:
                    close = size
                    break
                if hit.group() == '"':
                    close = hit.start()
                    break
                j = hit.start() + 2
            close = min(close, size)
            if top is not None and top[0] and top[2]:
                top[2] = False
                top[3] = fields.get(_json_name(text[i + 1 : close]))
                if top[1] and close > i + 1:
                    yield i + 1, close, top[1]
            else:
                category = value_category(top)
                if category and close > i + 1:
                    yield i + 1, close, category
            i = close + 1
        elif char in "{[":
            if len(stack) >= MAX_JSON_DEPTH:
                yield i, size, value_category(top) or "person"
                return
            stack.append([char == "{", value_category(top), char == "{", None])
            i += 1
        elif char in "}]":
            if stack:
                stack.pop()
            i += 1
        else:
            j = i
            while j < size and text[j] not in ',:}] \t\r\n"{[':
                j += 1
            j = max(j, i + 1)
            category = value_category(top)
            if category:
                yield i, j, category
            i = j


# --------------------------------------------------------------------------- nested formats


_ZERO_WIDTH = frozenset(_ZERO_WIDTH_CHARS)


def _is_blank(char: str) -> bool:
    """Any Unicode blank (``str.isspace``: \\v, \\f, NBSP, U+2000-200A, U+2028/9, U+3000...) and the
    zero-width characters: what may separate tokens of a structure."""
    return char.isspace() or char in _ZERO_WIDTH


_XML_SPACE = " \t\r\n"  # the only blanks XML allows inside markup


def _xml_name_at(text: str, k: int) -> int:
    """End of a WHOLE valid XML name at ``k`` (up to a blank or ``< > = / " '``), or -1."""
    end = _token_end(text, k)
    return end if end > k and _RX["xml_name"].fullmatch(text, k, end) else -1


def _xml_region_end(text: str, i: int) -> int | None:
    """Strict check of ONE XML item at ``i`` (an element with all its content, a comment or a processing
    instruction): the position after it, or None when anything is malformed or ambiguous. Refused:
    DOCTYPE and every other ``<!`` declaration, invalid or unmatched names, tags not closed by ``>``,
    attributes not separated by blanks or without a quoted value, '<' or a bad '&' in attribute values
    or text, '--' inside a comment, CDATA outside an element, blanks other than XML's. Linear."""
    size = len(text)
    stack: list[str] = []
    k = i
    while k < size:
        if k > i and not stack:
            return k
        if text[k] != "<":
            if not stack:
                return None
            if text[k] == "&":
                ref = _RX["xml_ref"].match(text, k)
                if ref is None:
                    return None
                k = ref.end()
                continue
            stop = _RX["xml_text_stop"].search(text, k)
            k = size if stop is None else stop.start()
            continue
        if text.startswith("<!--", k):
            close = text.find("-->", k + 4)
            if close < 0 or text.find("--", k + 4, close) >= 0:
                return None
            k = close + 3
            if not stack:
                return k
            continue
        if text.startswith("<![CDATA[", k):
            close = text.find("]]>", k + 9)
            if close < 0 or not stack:
                return None
            k = close + 3
            continue
        if text.startswith("<?", k):
            name_end = _xml_name_at(text, k + 2)
            close = text.find("?>", k + 2)
            if name_end < 0 or close < 0:
                return None
            if text[k + 2 : name_end].casefold().startswith("xml") and k != 0:
                return None  # the XML declaration only at the very start (reserved target)
            k = close + 2
            if not stack:
                return k
            continue
        if text.startswith("<!", k):
            return None  # DOCTYPE, ENTITY and other declarations: never trusted
        if text.startswith("</", k):
            name_end = _xml_name_at(text, k + 2)
            if name_end < 0 or not stack or stack[-1] != text[k + 2 : name_end]:
                return None
            j = name_end
            while j < size and text[j] in _XML_SPACE:
                j += 1
            if j >= size or text[j] != ">":
                return None
            stack.pop()
            k = j + 1
            continue
        name_end = _xml_name_at(text, k + 1)
        if name_end < 0:
            return None
        j = name_end
        while True:
            blanks = j
            while j < size and text[j] in _XML_SPACE:
                j += 1
            if j >= size:
                return None
            if text[j] == ">":
                stack.append(text[k + 1 : name_end])
                k = j + 1
                break
            if text.startswith("/>", j):
                k = j + 2
                break
            if j == blanks:  # an attribute must follow a blank
                return None
            attr_end = _xml_name_at(text, j)
            if attr_end < 0:
                return None
            j = attr_end
            while j < size and text[j] in _XML_SPACE:
                j += 1
            if j >= size or text[j] != "=":
                return None
            j += 1
            while j < size and text[j] in _XML_SPACE:
                j += 1
            if j >= size or text[j] not in "\"'":
                return None
            close = text.find(text[j], j + 1)
            if close < 0:
                return None
            value_start = j + 1
            if text.find("<", value_start, close) >= 0:
                return None
            amp = text.find("&", value_start, close)
            while amp >= 0:
                ref = _RX["xml_ref"].match(text, amp, close)
                if ref is None:
                    return None
                amp = text.find("&", ref.end(), close)
            j = close + 1
        if not stack:
            return k
    return k if k > i and not stack else None


MAX_XML_REGION_CHARS = 4_000_000  # a larger XML region is not validated: it is one value


def _expat_accepts(text: str, start: int, end: int) -> bool:
    """The AUTHORITY on XML well-formedness: the stdlib expat parser must accept the region as a
    fragment (inside a synthetic root, so comments, PIs and mixed content are fine; the XML declaration
    only at the very start, followed by a synthetic root). Namespaces are checked; parameter entities
    are never parsed and the strict check already refused every DOCTYPE, so no entity can expand.
    Undefined entities, duplicate attributes, bad character references, '--' in comments, unbound
    prefixes: all refused. Linear in the region."""
    if end - start > MAX_XML_REGION_CHARS:
        return False
    from xml.parsers import expat

    region = text[start:end]
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    declaration = start == 0 and region.startswith("<?xml")
    document = region + "<r/>" if declaration else "<r>" + region + "</r>"
    try:
        parser.Parse(document, True)
    except (expat.ExpatError, UnicodeError, ValueError):  # a lone surrogate cannot even be encoded
        return False
    return True


MAX_JSON_DEPTH_STRICT = 500  # deeper JSON is not validated: it is one value (json.loads goes deeper)
_INT_DIGITS_LIMIT = getattr(sys, "get_int_max_str_digits", lambda: 0)()  # 0: no limit
_JSON_BLANKS = " \t\r\n"
_JSON_ESCAPES = frozenset('"\\/bfnrt')


def _json_string_end(text: str, i: int) -> tuple[int, bool]:
    """``text[i]`` is '"': (position after the closing quote, True), or (failure position, False). Strict:
    no control characters, only the JSON escapes (``\\uXXXX`` with four hex digits)."""
    size = len(text)
    k = i + 1
    stop = _RX["json_string_stop"]
    while True:
        hit = stop.search(text, k)
        if hit is None:
            return size, False
        k = hit.start()
        char = text[k]
        if char == '"':
            return k + 1, True
        if char != "\\":
            return k, False  # a control character
        nxt = text[k + 1 : k + 2]
        if nxt == "u":
            if _RX["hex4"].fullmatch(text, k + 2, k + 6) is None:
                return k, False
            k += 6
        elif nxt and nxt in _JSON_ESCAPES:
            k += 2
        else:
            return k, False


def _json_scan(text: str, i: int) -> tuple[int, bool]:
    """A strict, linear, exception-free JSON validator (RFC 8259 grammar, like the ``json`` module in
    strict mode without NaN/Infinity) for the value at ``i``: (position after it, True) or (the position
    where it fails, False). The failure position lets the caller resume there, so failed candidates
    never make the work quadratic; a failing string reports its OPENING quote, so the caller never
    resumes inside a string."""
    size = len(text)
    stack: list[str] = []  # '{' or '['
    expect = "value"  # value | key | colon | comma_or_close
    k = i
    while True:
        if expect == "comma_or_close" and not stack:
            return k, True
        while k < size and text[k] in _JSON_BLANKS and (stack or expect != "value" or k > i):
            k += 1
        if k >= size:
            return k, False
        char = text[k]
        if expect == "colon":
            if char != ":":
                return k, False
            k += 1
            expect = "value"
            continue
        if expect == "comma_or_close":
            top = stack[-1]
            if char == ",":
                k += 1
                expect = "key" if top == "{" else "value"
                continue
            if char == ("}" if top == "{" else "]"):
                stack.pop()
                k += 1
                continue
            return k, False
        if expect == "key":
            if char == "}" and _last_nonblank(text, i, k) == "{":  # the empty object
                stack.pop()
                k += 1
                expect = "comma_or_close"
                continue
            if char != '"':
                return k, False
            end, ok = _json_string_end(text, k)
            if not ok:
                return k, False  # at the opening quote: a caller resuming there reads the WHOLE string
            k = end
            expect = "colon"
            continue
        # a value
        if char in "{[":
            if len(stack) >= MAX_JSON_DEPTH_STRICT:
                return k, False
            stack.append(char)
            k += 1
            if char == "{":
                expect = "key"
                continue
            j = k
            while j < size and text[j] in _JSON_BLANKS:
                j += 1
            if j < size and text[j] == "]":
                stack.pop()
                k = j + 1
                expect = "comma_or_close"
            continue
        if char == '"':
            end, ok = _json_string_end(text, k)
            if not ok:
                return k, False  # at the opening quote (see above)
            k = end
        elif char in "-0123456789":
            number = _RX["json_number"].match(text, k)
            if number is None or (
                number.group(1) is not None and _INT_DIGITS_LIMIT and len(number.group(1)) > _INT_DIGITS_LIMIT
            ):
                return k, False  # the json module refuses integers past Python's digit limit too
            k = number.end()
        else:
            literal = _RX["json_literal"].match(text, k)
            if literal is None:
                return k, False
            k = literal.end()
        expect = "comma_or_close"
        if not stack:
            return k, True


def _last_nonblank(text: str, start: int, k: int) -> str:
    j = k - 1
    while j >= start and text[j] in _JSON_BLANKS:
        j -= 1
    return text[j] if j >= start else ""


def _json_region_end(text: str, i: int) -> int | None:
    """Strict JSON for the value at ``i``: the position after it, or None (see :func:`_json_scan`)."""
    end, ok = _json_scan(text, i)
    return end if ok else None


def _json_module_accepts(text: str, start: int, end: int) -> bool:
    """Second opinion of the ``json`` module on a span :func:`_json_scan` accepted (a bounded slice:
    no rescan of the whole text)."""

    def refuse(_constant: str) -> Any:
        raise ValueError("not JSON")

    try:
        json.loads(text[start:end], strict=True, parse_constant=refuse)
    except (ValueError, RecursionError):
        return False
    return True


def _embedded_json_values(region: str) -> Iterator[Span]:
    """In a validated XML region: every text node (or attribute value, comment, CDATA) that holds JSON
    syntax (``{ } [ ] "``) keeps a skeleton only when that exact text is valid JSON; otherwise it is
    replaced whole (``<Body>{"a":"b":0}</Body>``)."""
    for start, end, _category in xml_name_spans(region, _ANY_NAME):  # type: ignore[arg-type]
        segment = region[start:end]
        if not _RX["json_char"].search(segment):
            continue
        stop, ok = _json_scan(segment, 0)
        if not (ok and stop == len(segment) and _json_module_accepts(segment, 0, stop)):
            yield start, end


class _Finder:
    """``str.find`` with a memory: the next occurrence of a token found from position p is still the
    answer for any later position up to it, so scanning many unclosed tags stays linear."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.size = len(text)
        self.cache: dict[str, tuple[int, int]] = {}

    def find(self, token: str, start: int) -> int:
        cached = self.cache.get(token)
        if cached is not None and cached[0] <= start <= cached[1]:
            return cached[1]
        found = self.text.find(token, start)
        found = self.size if found < 0 else found
        self.cache[token] = (start, found)
        return found


class _Tag:
    """A complete tag found in structural context (see Redactor._structure_spans)."""

    __slots__ = ("kind", "name", "name_span", "syntax", "paired")

    def __init__(self, kind: str, name: str) -> None:
        self.kind = kind  # open | close | empty
        self.name = name
        self.name_span: Span | None = None
        self.syntax: list[Span] = []
        self.paired = False


def _token_end(text: str, start: int) -> int:
    """End of the token at ``start``: everything up to a blank or one of ``< > = / " '``."""
    size = len(text)
    k = start
    while k < size:
        char = text[k]
        if char in "<>=/\"'" or _is_blank(char):
            break
        k += 1
    return k


class _AnyName:
    """A field map that maps every name: lists the text of every element, attribute and value."""

    def get(self, _name: str, _default: Any = None) -> str:
        return VALUE


_ANY_NAME = _AnyName()


def _json_strings(text: str) -> Iterator[Span]:
    """(start, end) of the content of every JSON-style double-quoted string (keys too)."""
    size = len(text)
    i = 0
    while i < size:
        quote = text.find('"', i)
        if quote < 0:
            return
        j = quote + 1
        while True:
            hit = _RX["json_break"].search(text, j)
            if hit is None:
                close = size
                break
            if hit.group() == '"':
                close = hit.start()
                break
            j = hit.start() + 2
        if close > quote + 1:
            yield quote + 1, close
        i = close + 1


def structured_spans(text: str, fields: FieldMap, depth: int = 0) -> Iterator[tuple[int, int, str]]:
    """The XML and JSON field rules on ``text`` whatever its first character, and again inside every
    XML text or attribute value and every JSON string that holds markup (JSON inside XML, XML inside
    a JSON string, escaped or not), down to :data:`MAX_NESTING` levels."""
    yield from xml_name_spans(text, fields)
    yield from json_key_spans(text, fields)
    if depth >= MAX_NESTING:
        return
    size = len(text)
    # A segment needs its own pass when the pass on the whole text cannot have read it: it is escaped
    # (\" &lt; %3C...), or it is XML text/attribute/CDATA holding JSON or markup (the JSON tokenizer on
    # the whole text may have paired the quotes of the XML differently).
    segments = [(a, b, True) for a, b, _c in xml_name_spans(text, _ANY_NAME)]  # type: ignore[arg-type]
    segments.extend((a, b, False) for a, b in _json_strings(text))
    trigger = _RX["nest_trigger"]
    for start, end, from_xml in segments:
        if end - start >= size:
            continue
        segment = text[start:end]
        escaped = _ESCAPES_JSON.search(segment) is not None
        if not escaped and not (from_xml and trigger.search(segment)):
            continue
        view = decode_view(segment, json_strings=True) if escaped else None
        inner, starts, ends = (segment, None, None) if view is None else view
        if view is not None and trigger.search(inner) is None:
            continue
        for a, b, category in structured_spans(inner, fields, depth + 1):
            if starts is not None and ends is not None:
                a, b = starts[a], ends[b - 1]
            yield start + a, start + b, category


# --------------------------------------------------------------------------- strict nets

# Label words that announce an identifier, with the category that must be on for the label to count
# (VALUE: always). A label is a word followed by ':' or '=' (or a blank and a quote); it counts when the
# whole word or one of its parts (snake_case, camelCase, dotted) is in this table.
_LABEL_WORDS: dict[str, str] = {}
for _category, _words in {
    "user": "user users username userid login logon account accountname owner operator usuario usu\u00e1rio "
    "technician tecnico t\u00e9cnico employee",
    "person": "name names firstname lastname fullname nome customer customername cliente contact contato "
    "caller solicitante address endereco endere\u00e7o street rua logradouro bairro city cidade municipio "
    "munic\u00edpio cep zip zipcode postalcode crew description descricao descri\u00e7\u00e3o "
    "comment comments remarks "
    "notes note document cpf cnpj location site substation subestacao subesta\u00e7\u00e3o premise",
    "phone": "phone telephone tel mobile celular telefone fone",
    "email": "email mail",
    "host": "host hostname server machine computer node endpoint",
    "ip": "ip ipaddress",
    "url": "url uri",
    "path": "path file filename folder directory dir",
    "gid": "gid gids globalid",
    "mrid": "mrid",
    "guid": "guid uuid",
    "ticket": "ticket incident inc problem prob",
    "plate": "plate placa vehicle",
    VALUE: "id ids uid key token password passwd pwd secret client msgid messageid correlationid meter "
    "feeder alimentador",
}.items():
    _LABEL_WORDS.update(dict.fromkeys(_words.split(), _category))
# Template field names: the same words, plus a few that only name identifiers as fields.
_FIELD_WORDS = dict(_LABEL_WORDS)
_FIELD_WORDS.update(
    dict.fromkeys(
        "src dst dest target remote partner subscriber alias handle socket conn contract ident works "
        "devices".split(),
        VALUE,
    )
)
# Template fields whose values may stay: (template code or "*", field name) -> validator. Every other
# variable part of a templated line is replaced whole. MAINTENANCE RULE: a new template needs no entry
# (its variable fields are replaced by default). An entry is added only with a validator that PROVES
# the value harmless: membership in a closed set ("operation": catalog operation names, families and
# template codes; "level": log level names; "code": catalog and case error codes; "bool") or a
# numeric shape for a field whose name says it is a count, a size or a time ("number": digits and
# . : , only, every digit run at most 6 long). "*" (any template) is allowed only for these
# validators; a field whose regex is a finite alternation of literals needs no entry (the regex
# proves it). Identifier-named fields (user, host, gid, id...) never get an entry.
PROVEN_FIELDS: dict[tuple[str, str], str] = {}
for _validator, _names in {
    "operation": "op ops operation msg_op method action verb",
    "level": "lvl level from_level to_level vsev",
    "code": "code err errcode error_code",
    "bool": "ready supported skip_unreachable",
    "number": "n count total done inserts updates deletes inserted updated deleted identical errors calls "
    "chunks messages assigned deassigned from_n to_n n_assignments size size2 size_in size_out bytes "
    "req_bytes resp_bytes req_avg resp_avg ms elapsed duration timeout period idle secs seconds port chan "
    "line proc_pid pid max min left retries attempt attempts",
}.items():
    PROVEN_FIELDS.update(dict.fromkeys((("*", n) for n in _names.split()), _validator))
del _validator, _names
_SERVICE_STATES = "enum:Running|Stopped|Starting|Stopping|Paused|Pending|Unknown|Active|Standby|Failed"
PROVEN_FIELDS.update({("SVC-001", "from_state"): _SERVICE_STATES, ("SVC-001", "to_state"): _SERVICE_STATES})
_EDGE_PUNCT = "\"'()[]{}<>,;:.!?`"
_UNITS = frozenset(
    "ms s sec secs second seconds min mins minute minutes h hr hrs hour hours b kb mb gb bytes %".split()
)
_NUMBER_CHARS = frozenset("0123456789.:,")
_XML_NAMES = frozenset("xml xmlns xsi xsd version encoding standalone nil type schemalocation".split())
_STOP_WORDS = frozenset(
    "the a an is was are were has have had not no for from to in on at of and or with by as be been being "
    "this that it its after before since until while when because but then into via using during within "
    "without over under about again than so if".split()
)
_KEEP_PUNCT = frozenset(" -:,.;/|()[]")
_FIELD_SPLIT = re.compile(r"[_0-9]+")
# Label words that announce a value even without punctuation ("for user jsilva", "customer Monitor"):
# nouns that name a person, an account, a machine or an identifier, not words common in log prose.
_WORD_LABELS = frozenset(
    "user username userid account accountname usuario customer customername cliente contact contato caller "
    "solicitante crew technician tecnico employee host hostname server machine computer gid globalid mrid "
    "guid uuid msgid messageid correlationid ticket incident plate placa email phone telephone mobile "
    "celular telefone ip ipaddress url uri token password passwd pwd secret".split()
)
_RX["strict_word_label"] = re.compile(
    r"(?<![\w.@/\\-])("
    + "|".join(re.escape(w) for w in sorted(_WORD_LABELS, key=lambda w: (-len(w), w)))
    + r")[ \t]{1,3}(?=[^\s:=])",
    re.IGNORECASE,
)
del _category, _words


def _container(intervals: list[Span]) -> Callable[[int, int], bool]:
    """``inside(start, end)``: is [start, end) inside ONE of ``intervals``? Sorted starts and the running
    maximum of the ends answer it with one binary search (no quadratic scan)."""
    if not intervals:
        return lambda _s, _e: False
    intervals.sort()
    starts = [a for a, _b in intervals]
    reach: list[int] = []
    top = -1
    for _a, b in intervals:
        top = max(top, b)
        reach.append(top)

    def inside(start: int, end: int) -> bool:
        index = bisect.bisect_right(starts, start) - 1
        return index >= 0 and reach[index] >= end

    return inside


def _complement(covered: list[Span], start: int, end: int) -> Iterator[Span]:
    """The parts of [start, end) outside the union of ``covered``."""
    pos = start
    for a, b in sorted(covered):
        if a > pos:
            yield pos, min(a, end)
        pos = max(pos, b)
        if pos >= end:
            return
    if pos < end:
        yield pos, end


def _union(intervals: Iterable[Span]) -> list[Span]:
    merged: list[list[int]] = []
    for a, b in sorted(intervals):
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def _subtract(spans: Iterable[tuple[int, int, str]], holes: list[Span]) -> Iterator[tuple[int, int, str]]:
    """``spans`` minus the union of ``holes`` (pieces that remain, with their category)."""
    union = _union(holes)
    starts = [a for a, _b in union]
    for start, end, category in spans:
        pos = start
        index = max(0, bisect.bisect_right(starts, start) - 1)
        while index < len(union) and union[index][0] < end:
            a, b = union[index]
            if b > pos:
                if a > pos:
                    yield pos, a, category
                pos = max(pos, b)
            index += 1
        if pos < end:
            yield pos, end, category


def _fill_gaps(
    text: str, nets: list[tuple[int, int, str]], protected: list[Span], taken: list[Span]
) -> Iterator[tuple[int, int, str]]:
    """Strict spans without the user-protected parts and without what other spans already cover. A
    piece keeps its full extent, punctuation included (``password: 12345!!!``, ``!@#SecretQ$%!``);
    only blanks at its edges are left out, and a piece of blanks only is dropped. The last value says
    the piece is punctuation left over around another marker (``(`` and ``)`` of ``user: x (INC1)``):
    it is replaced together with the marker it touches."""
    word = _RX["word"]
    for start, end, category in _subtract(_subtract(nets, protected) if protected else iter(nets), taken):
        piece = text[start:end]
        core = piece.strip()
        if core:
            lead = len(piece) - len(piece.lstrip())
            yield start + lead, start + lead + len(core), category, word.search(core) is None


def _payload_markup(text: str) -> list[Span]:
    """Element and attribute names of well-formed tags: the parts of a payload the strict token rule
    leaves alone (JSON keys are not exempt: a map keyed by names would show them). A tag counts only
    when it is closed by '>' before any other '<'."""
    size = len(text)
    spans: list[Span] = []
    name_rx = _RX["xml_name"]
    pos = 0
    while True:
        lt = text.find("<", pos)
        if lt < 0:
            break
        skip = 2 if text.startswith(("</", "<?"), lt) else 1
        name = name_rx.match(text, lt + skip)
        if name is None:
            pos = lt + 1
            continue
        found = [name.span()]
        i = name.end()
        closed = False
        while i < size:
            char = text[i]
            if char == ">":
                closed = True
                i += 1
                break
            if char == "<":
                break
            if char in "\"'":
                close = text.find(char, i + 1)
                i = size if close < 0 else close + 1
                continue
            if char == "=":  # an unquoted value is data
                i += 1
                while i < size and text[i] in " \t\r\n":
                    i += 1
                if i < size and text[i] not in "\"'":
                    while i < size and not text[i].isspace() and text[i] not in "<>":
                        i += 1
                continue
            attribute = name_rx.match(text, i) if not (char.isspace() or char in "/?") else None
            if attribute is not None:
                found.append(attribute.span())
                i = attribute.end()
                continue
            i += 1
        if closed:
            spans.extend(found)
        pos = max(i, lt + 1)
    return spans


# --------------------------------------------------------------------------- the redactor


def _literal_regex(values: Iterable[str], *, word: bool) -> re.Pattern[str] | None:
    ordered = sorted(dict.fromkeys(values), key=lambda v: (-len(v), v))
    if not ordered:
        return None
    body = "|".join(re.escape(v) for v in ordered)
    if word:
        return re.compile(rf"(?<![0-9A-Za-z_])(?:{body})(?![0-9A-Za-z_])", re.IGNORECASE)
    return re.compile(body)


def _normalize(category: str, value: str) -> str:
    if category in ("guid", "host", "email", "user", "person", TERM, VALUE):
        return " ".join(value.casefold().split())
    if category == "ticket":
        return re.sub(r"[\s_-]+", "", value).upper()
    if category == "plate":
        return value.replace("-", "").upper()
    if category == "phone":
        return re.sub(r"[^0-9]", "", value)
    if category == "ip":
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            return value
    return value


_STRUCTURED_PRIO = _PRIO["person_element"]
_ATTACH = 99  # priority of leftover punctuation: it joins the marker it touches
_TOKEN_RUN = re.compile("[" + re.escape("".join(sorted(_TOKEN_CHARS))) + "]+")
RecordSpans = list[tuple[int, int, str]]


class Redactor:
    """Applies a :class:`Profile` to texts; one instance per export (markers are per instance)."""

    def __init__(
        self,
        profile: Profile | None = None,
        *,
        exceptions: Iterable[str] = (),
        known: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        self.profile = profile or default_profile()
        # Exceptions of the user (the profile) win everywhere, wherever they occur. Automatic ones
        # (catalog names and codes the export validated) only protect a value found by the generic
        # rules, never a structured field, a template field or a labelled value.
        self._user_exceptions = {e.casefold() for e in self.profile.exceptions if e}
        self._auto_exceptions = {e.casefold() for e in exceptions if e} - self._user_exceptions
        self._user_rx = _literal_regex(self.profile.exceptions, word=True)
        self._finders: list[tuple[str, int, SpanFinder]] = []
        for category in CATEGORIES:
            if category != "person" and self.profile.enabled(category):
                for finder in _FINDERS[category]:
                    self._finders.append((category, _PRIO[category], finder))
        if self.profile.enabled("person"):
            for finder in _FINDERS["person"]:
                self._finders.append(("person", _PRIO["person"], finder))
        # automatic exceptions may only correct a SHAPE rule (a catalog name that looks like a host) or
        # a user term that names a catalog item; a labelled value or a known value is never protected
        self._shape_finders = {id(f) for _c, _p, f in self._finders if not getattr(f, "labelled", False)}
        self._literal_rules: list[tuple[str, int, re.Pattern[str]]] = []
        word_rx = _literal_regex(self.profile.terms_word, word=True)
        literal_rx = _literal_regex(self.profile.terms_literal, word=False)
        for rx in (word_rx, literal_rx):
            if rx is not None:
                self._literal_rules.append((TERM, _PRIO[TERM], rx))
        for category, values in (known or {}).items():
            if category in CATEGORIES and self.profile.enabled(category):
                rx = _literal_regex((v for v in values if len(v) >= MIN_TERM_CHARS), word=True)
                if rx is not None:
                    self._literal_rules.append((category, _PRIO[category], rx))
        self._fields: dict[str, str] = field_map(self.profile)
        self._markers: dict[tuple[str, str], str] = {}
        self._originals: dict[str, str] = {}
        self._distinct: dict[str, int] = {}
        self._replaced: dict[str, int] = {}
        self.overflow = 0
        # strict nets (the structural safety net; see the module docstring)
        self.strict = self.profile.mode == "strict" and bool(self._finders or self._fields)
        self._names: frozenset[str] = frozenset()
        self._levels: frozenset[str] = frozenset()
        self._codes: frozenset[str] = frozenset()
        self._vocab: frozenset[str] = frozenset()
        self._label_extra: dict[str, str] = {}
        if self.strict:
            from . import template_shape

            self._names = template_shape.catalog_names()
            self._levels = template_shape.log_levels()
            self._codes = template_shape.product_codes()
            auto_words = {w.casefold() for e in exceptions for w in _RX["word"].findall(e)}
            self._vocab = template_shape.vocabulary() | self._names | auto_words | frozenset(_LABEL_WORDS)
            for name, category in self._fields.items():
                self._label_extra.setdefault(name, category)

    # -- public

    @property
    def active(self) -> bool:
        return bool(self._finders or self._literal_rules or self._fields)

    @property
    def mode(self) -> str:
        return self.profile.mode

    def redact(
        self,
        text: str,
        *,
        payload: bool = False,
        spans: RecordSpans | None = None,
        identifier: bool = False,
    ) -> str:
        """``text`` with every sensitive value replaced. ``payload`` adds the payload rules.

        Strict mode only: ``spans`` are the structural spans of the text computed by
        :meth:`record_spans` (they replace the free-text net; with ``payload`` they are united with the
        payload's own net, so a character either replaces stays replaced); ``identifier`` says the
        whole text is an identifier (an event key) and is replaced as a whole."""
        if not text or not self.active:
            return text
        if len(text) > MAX_TEXT_CHARS:
            return "<REDACTED-TOO-LONG>"
        out = self._redact_text(text, payload, spans, identifier)
        # a lone surrogate (\\ud800) cannot be encoded: it never reaches the output (no crash downstream)
        return _RX["surrogate"].sub("?", out) if _RX["surrogate"].search(out) else out

    def record_spans(self, text: str, msg_len: int, template: str | None = None) -> RecordSpans:
        """Strict mode: the spans to replace in a record (``text`` = first line, then the continuation
        lines after ``msg_len``), from its template. Empty in standard mode.

        * template that matches the first line: its literal text and its PROVEN fields stay, every
          other variable part is replaced whole;
        * template that is unknown or no longer matches: the whole first line is replaced;
        * continuation lines the template parses as data: replaced piece by piece unless proven;
        * no template, stack traces and other continuation lines: the free-text net, always run on the
          WHOLE record so that a structure left open or unsafe on the first line carries over."""
        if not self.strict or not text:
            return []
        msg_len = max(0, min(msg_len, len(text)))
        out: RecordSpans = []
        if template:
            from . import template_shape

            shape = template_shape.shape(template)
            lay = template_shape.layout(shape, text[:msg_len]) if shape is not None else None
            if lay is None:  # unknown or no longer matching: the whole line is variable
                piece = text[:msg_len].strip()
                if piece:
                    lead = len(text[:msg_len]) - len(text[:msg_len].lstrip())
                    out.append((lead, lead + len(piece), VALUE))
            else:
                self._layout_spans(text, shape, lay, msg_len, out)
            parser = shape.cont_parser if shape is not None else None
            if msg_len < len(text) and parser is not None and parser != "dotnet_stack":
                # the template parses its continuation as data (map dumps, key/value blocks, statistics,
                # code lists): every piece of it is replaced unless proven, and the structural state of
                # the WHOLE record comes first (a parser only allows what that state leaves safe)
                out.extend((a, b, VALUE) for a, b in self._continuation_spans(text, msg_len, parser))
                out.extend(self.structural_spans(text, msg_len))
                return out
            if msg_len < len(text):
                # the free-text net reads the WHOLE record (one structural state across the line break:
                # a first line that ends unsafe makes the continuation data) and keeps what falls in
                # the continuation; the template decides the first line
                for a, b, category in self._free_text_spans(text, 0, len(text)):
                    if b > msg_len:
                        out.append((max(a, msg_len), b, category))
            return out
        # no template: the first line and the continuation are ONE free text (one structural state)
        out.extend(self._free_text_spans(text, 0, len(text)))
        return out

    def structural_spans(self, text: str, start: int = 0) -> RecordSpans:
        """Strict mode: only the STRUCTURAL decisions of the gate on the whole ``text`` (rest-of-text
        values, stray delimiters, string contents, region skeletons), clipped to ``start`` on; no
        free-text allowlist. Empty when the text holds no structure."""
        if not self.strict or not _RX["structured_hint"].search(text):
            return []
        return [
            (max(a, start), b, VALUE)
            for a, b in self._structure_spans(text, payload=False, free=False)
            if b > start
        ]

    def value_map(self) -> dict[str, str]:
        """marker -> first original value (local use only; never export it)."""
        return dict(self._originals)

    def legend(self) -> list[dict[str, Any]]:
        """Per category: label, distinct values and replacements. Never contains the values."""
        rows = []
        for category in (*CATEGORIES, TERM, VALUE):
            replaced = self._replaced.get(category, 0)
            if replaced:
                rows.append(
                    {
                        "category": category,
                        "label": LABELS[category],
                        "distinct": self._distinct.get(category, 0),
                        "replaced": replaced,
                    }
                )
        return rows

    # -- strict nets

    def _free_safe(self, core: str) -> bool:
        """May free text keep this blank-free piece (edge punctuation already removed)? Only when it is
        PROVEN public: no word character at all, a catalog or common word, a catalog name or code, a
        compound of catalog words (``System.TimeoutException``, ``WFM-081``, ``Communication2``), a
        date or time of day, or a number glued to a unit (``120ms``). A bare number, a capital letter or
        any other word is not."""
        if not _RX["word"].search(core):
            return True
        if ("%" in core or "&#" in core) and _RX["hidden_escape"].search(core):
            plain = _decode_quick(core)
            return plain != core and self._free_safe(plain.strip(_EDGE_PUNCT))
        low = core.casefold()
        if low in self._vocab or low in self._auto_exceptions or low in self._codes:
            return len(core) > 1 or core == "a"
        if _RX["unit"].match(low) or _RX["clock"].fullmatch(core):
            return True
        if any(len(d) > 4 for d in _RX["digit_runs"].findall(core)):
            return False
        parts = _RX["label_parts"].findall(core)
        alpha = [p for p in parts if not p.isdigit()]
        if not alpha or not _RX["compound"].fullmatch(core):
            return False
        return all(len(p) > 1 and p.casefold() in self._vocab for p in alpha)

    def _free_runs(self, text: str, start: int, end: int) -> Iterator[Span]:
        """The free-text allowlist on ``text[start:end]``: spans of every piece :meth:`_free_safe` does not
        keep (a number is kept only right before a unit: ``120 ms``). Pieces separated only by blanks
        share one span."""
        pending: list[int] | None = None
        chunks = list(_RX["piece"].finditer(text, start, end))
        for index, chunk in enumerate(chunks):
            raw = chunk.group()
            core = raw.strip(_EDGE_PUNCT)
            if not core:
                continue
            a = chunk.start() + (len(raw) - len(raw.lstrip(_EDGE_PUNCT)))
            b = a + len(core)
            safe = self._free_safe(core)
            if not safe and core.isdigit() and index + 1 < len(chunks):
                unit = chunks[index + 1].group().strip(_EDGE_PUNCT).casefold()
                safe = unit in _UNITS and len(core) <= 6
            if safe:
                if pending is not None:
                    yield pending[0], pending[1]
                    pending = None
                continue
            if pending is not None and not text[pending[1] : a].strip(" \t"):
                pending[1] = b
                continue
            if pending is not None:
                yield pending[0], pending[1]
            pending = [a, b]
        if pending is not None:
            yield pending[0], pending[1]

    def _label_category(self, word: str) -> str | None:
        """The category a label word announces (VALUE for generic identifiers), or None."""
        low = word.casefold()
        found = _LABEL_WORDS.get(low) or self._label_extra.get(low)
        if found is None:
            for part in re.split(r"[._-]+", word):
                for piece in (part, *_RX["label_parts"].findall(part)):
                    found = _LABEL_WORDS.get(piece.casefold())
                    if found is not None:
                        break
                if found is not None:
                    break
        if found is None or (found != VALUE and not self.profile.enabled(found)):
            return None  # a category switched off takes its labels with it
        return found

    def _bare_value_end(self, text: str, start: int, limit: int) -> int:
        """End of a bare labelled value, punctuation included: its first chunk (to a blank or ``,;|``)
        and the next chunks on the same line, up to ``,;|``, another label or a stop word."""
        chunk_rx = _RX["chunk"]

        def chunk_end(i: int) -> int:
            match = chunk_rx.match(text, i, limit)
            if match is None:
                return i
            end = match.end()
            for stop in ",;|":
                cut = text.find(stop, i, end)
                if cut >= 0:
                    end = min(end, cut)
            return end

        end = chunk_end(start)
        while end < limit:
            k = end
            while k < limit and text[k] in " \t":
                k += 1
            if k == end or k >= limit or text[k] in ",;|":
                break
            nxt = chunk_end(k)
            if nxt <= k or _RX["strict_label"].match(text, k, limit) is not None:
                break
            core = text[k:nxt].strip(_EDGE_PUNCT).casefold()
            if core in _LABEL_WORDS or core in _STOP_WORDS:  # "user: x account y", "user: x from h"
                break
            end = nxt  # a word that looks public does not end a value ("customer monitor will")
        return end

    def _free_text_spans(self, text: str, start: int, end: int) -> RecordSpans:
        """The free-text net on ``text[start:end]`` (labels also on its decoded view, mapped back)."""
        piece = text[start:end]
        out: RecordSpans = [(start + a, start + b, c) for a, b, c in self._free_spans(piece)]
        view = decode_view(piece)
        if view is not None:
            source, starts, ends = view
            out.extend((start + starts[a], start + ends[b - 1], c) for a, b, c in self._decoded_spans(source))
        return out

    def _decoded_spans(self, source: str, payload: bool = False) -> RecordSpans:
        """The net on a DECODED view: labels, and, in free text whose markup was written escaped
        (``&lt;Name&gt;``, ``%3C``, ``\\u003c``), the gated structure net on the decoded markup."""
        out = self._label_values(source)
        if not payload and _RX["structured_hint"].search(source):
            out.extend((a, b, VALUE) for a, b in self._structure_spans(source, payload=False))
        return out

    def _free_spans(self, text: str, payload: bool = False) -> RecordSpans:
        """Free-text net: labelled values replaced whole, and the allowlist (:meth:`_free_runs`) on the
        rest. A text that holds markup or JSON gets the default-deny structure net instead (so does a
        payload, from the caller)."""
        out = self._label_values(text)
        if payload:
            return out
        if _RX["structured_hint"].search(text):  # markup or JSON inside free text: the gated structure net
            out.extend((a, b, VALUE) for a, b in self._structure_spans(text, payload=False))
            return out
        out.extend((x, y, VALUE) for x, y in self._free_runs(text, 0, len(text)))
        return out

    def _label_values(self, text: str) -> RecordSpans:
        """The value after an identifier label, replaced whole whatever it looks like (a user exception
        excepted, through the user protection). A label is a word that names an identifier followed by
        ':' or '=' (or a blank and a quote), or followed by a blank and a word that is not a stop word."""
        size = len(text)
        out: RecordSpans = []
        label_rx = _RX["strict_label"]
        pos = 0
        while pos < size:
            match = label_rx.search(text, pos)
            if match is None:
                break
            category = self._label_category(match.group(1))
            if category is None:
                pos = match.end()
                continue
            i = match.end()
            while i < size and text[i] in " \t\r\n":
                i += 1
            if i >= size:
                break
            extent = _value_extent(text, i)
            if extent is not None:
                value_start, value_end, closed = extent
                after = value_end + 1 if closed else value_end
            else:
                value_start, value_end = i, self._bare_value_end(text, i, size)
                after = value_end
            if text[value_start:value_end].strip():
                out.append((value_start, value_end, category))
            pos = max(after, match.end(), match.start() + 1)
        for match in _RX["strict_word_label"].finditer(text):
            category = self._label_category(match.group(1))
            if category is None:
                continue
            i = match.end()
            first = _RX["chunk"].match(text, i)
            if first is None or _RX["strict_label"].match(text, i) is not None:  # "crew GID: ...": that label
                continue
            core = first.group().strip(_EDGE_PUNCT).casefold()
            if not core or core in _STOP_WORDS or core in _LABEL_WORDS:
                continue
            value_end = self._bare_value_end(text, i, size)
            if value_end > i:
                out.append((i, value_end, category))
        return out

    def _field_category(self, name: str) -> str | None:
        """The identifier category a template field name announces (VALUE for generic ids), or None."""
        for part in (p for p in _FIELD_SPLIT.split(name.casefold()) if p):
            category = _FIELD_WORDS.get(part)
            if category is not None:
                return category if category == VALUE or self.profile.enabled(category) else VALUE
        return None

    def _proven(self, validator: str, value: str) -> bool:
        low = value.strip().casefold()
        if not low:
            return True
        if validator == "number":
            return set(low) <= _NUMBER_CHARS and all(len(d) <= 6 for d in _RX["digit_runs"].findall(low))
        if validator == "operation":
            return low in self._names
        if validator == "level":
            return low in self._levels
        if validator == "code":
            return low in self._names or low in self._auto_exceptions or low in self._codes
        if validator == "bool":
            return low in ("true", "false", "yes", "no")
        if validator.startswith("enum:"):
            return low in {v.casefold() for v in validator[5:].split("|")}
        return False

    def _field_kept(self, shape: Any, name: str, value: str) -> bool:
        """Does the template PROVE this field value harmless? (see :data:`PROVEN_FIELDS`)"""
        proof = (shape.proofs or {}).get(name)
        if isinstance(proof, frozenset):
            return value in proof
        if self._field_category(name) is not None:
            return False
        validator = PROVEN_FIELDS.get((shape.code, name)) or PROVEN_FIELDS.get(("*", name))
        if validator is None and (shape.field_types or {}).get(name) in ("int", "float"):
            validator = "number"  # a declared numeric type (a numeric regex alone proves nothing)
        return validator is not None and self._proven(validator, value)

    def _layout_spans(self, text: str, shape: Any, lay: Any, msg_len: int, out: RecordSpans) -> None:
        """A templated first line: static text stays, proven fields stay, every other variable part
        (fields, uncaptured parts, the text after the match) is replaced whole."""
        covered: list[Span] = [(a, b) for a, b in lay.static if b <= msg_len]
        for a, b, name in lay.fields:
            if b > msg_len:
                continue
            if self._field_kept(shape, name, text[a:b]):
                covered.append((a, b))
        replaced: list[Span] = []
        for a, b, name in lay.fields:
            if b <= msg_len and (a, b) not in covered:
                category = self._field_category(name) or VALUE
                out.append((a, b, category))
                replaced.append((a, b))
        for a, b in _complement(covered + replaced, 0, msg_len):
            piece = text[a:b].strip()
            if not piece or (len(piece) <= 2 and set(piece) <= _KEEP_PUNCT):
                continue  # blanks and the template's own separators ("-", ": ")
            lead = len(text[a:b]) - len(text[a:b].lstrip())
            out.append((a + lead, a + lead + len(piece), VALUE))

    def _continuation_spans(self, text: str, start: int, parser: str) -> Iterator[Span]:
        """Continuation lines a template parses as data: every piece is replaced, except numbers and
        catalog names in statistics and catalog/case codes in code lists."""
        pending: list[int] | None = None
        for chunk in _RX["cell"].finditer(text, start):
            piece = chunk.group()
            keep = not _RX["word"].search(piece)
            if parser == "stats_csv":
                keep = keep or self._proven("number", piece) or self._proven("operation", piece)
            elif parser == "codes_per_line":
                keep = keep or self._proven("code", piece)
            if keep:
                if pending is not None:
                    yield pending[0], pending[1]
                    pending = None
                continue
            if pending is not None and not text[pending[1] : chunk.start()].strip(" \t"):
                pending[1] = chunk.end()
                continue
            if pending is not None:
                yield pending[0], pending[1]
            pending = [chunk.start(), chunk.end()]
        if pending is not None:
            yield pending[0], pending[1]

    def _allowed_name(self, name: str) -> bool:
        """Is an element/attribute name or a JSON key in the ALLOWED NAME SET? That set is: the XML
        built-ins, the payload field names of the profile and of the structured rules, and names made
        only of catalog words (letters, with ``_ . -`` or camelCase between words; no digit at all),
        under an optional short namespace prefix (``ns0:``)."""
        if not name or len(name) > 64:
            return False
        prefix, _sep, local = name.rpartition(":")
        if prefix.casefold() == "xmlns":  # a namespace declaration names a prefix
            return bool(_RX["ns_prefix"].fullmatch(local))
        if prefix and not _RX["ns_prefix"].fullmatch(prefix):
            return False
        low = local.casefold()
        if low in _XML_NAMES or low in self._fields:
            return True
        if not _RX["plain_name"].fullmatch(local):
            return False
        parts = _RX["label_parts"].findall(local)
        return bool(parts) and all(len(p) > 1 and p.casefold() in self._vocab for p in parts)

    def _structure_spans(self, text: str, payload: bool = True, free: bool = True) -> list[Span]:
        """FAIL-CLOSED gate in front of the structural skeleton. The text is cut in regions: an XML item
        (element with all its content, comment or processing instruction) or a JSON value. Cutting keeps
        the quote context: a '<', '{' or '[' inside "quoted" plain text never starts a region. A region
        keeps its skeleton (:meth:`_skeleton`) ONLY if it is well-formed: XML must pass this module's
        strict check (:func:`_xml_region_end`) AND the stdlib expat parser, the authority, with no DTD,
        no entity declaration and namespace checks (:func:`_expat_accepts`); JSON must pass the ``json``
        module's strict decoder. A failed XML region, or a JSON one that starts like JSON for sure
        (``{"``, ``[{``...), is ONE value up to the end of the text, and so is everything after the
        first stray '<' or '>' between regions (the scan stops there). Text between regions is a payload
        value (``payload``) or free text (allowlist). Each character is validated at most once (regions
        are disjoint), so the work stays linear."""
        size = len(text)
        spans: list[Span] = []

        def whole(a: int, b: int) -> None:
            piece = text[a:b]
            core = piece.strip()
            if core:
                lead = len(piece) - len(piece.lstrip())
                spans.append((a + lead, a + lead + len(core)))

        def outside(a: int, b: int) -> bool:
            """Text between regions; False when it ends the scan (a stray '<' or '>')."""
            stray = _RX["angle"].search(text, a, b)
            if payload:
                whole(a, size if stray else b)
                return stray is None
            free_end = stray.start() if stray else b
            # defence in depth: the allowlist only ever sees text with no quote, brace or bracket (an
            # apostrophe inside a word excepted); anything else here is replaced whole (fail closed)
            if _RX["allowlist_guard"].search(text, a, free_end):
                whole(a, free_end)
            elif free:  # free=False: only the structural decisions, no allowlist (see record_spans)
                spans.extend(self._free_runs(text, a, free_end))
            if stray:
                whole(stray.start(), size)  # everything after it, to the end of the text
                return False
            return True

        def to_end(k: int) -> None:
            """THE single path for "the rest of the text is data": the text before ``k``, then one value
            from ``k`` to the end. Every recovery that cannot resume safely ends the scan here."""
            if outside(pos, k):
                whole(k, size)

        pos = scan = 0
        while True:
            found = _RX["cut"].search(text, scan)
            if found is None:
                outside(pos, size)
                break
            k = found.start()
            char = text[k]
            if char in "\"'":
                if char == "'" and 0 < k < size - 1 and text[k - 1].isalnum() and text[k + 1].isalnum():
                    scan = k + 1  # an apostrophe inside a word ("don't"), not a quote
                    continue
                # a COMPLETE string of the text between regions (backslash escapes honoured): nothing in
                # it starts a region, and its CONTENT is one value (never the free-text allowlist); an
                # unterminated one makes the rest of the text data
                close = _closing_quote(text, k + 1, char)
                after = text[close + 1 : close + 2]
                if close >= size or after.isalnum() or after == "_":
                    # unterminated, or closed right against a word ('"[0,{"status"'): the quoting is
                    # ambiguous, so the rest of the text is data
                    to_end(k)
                    break
                if not outside(pos, k):
                    break
                whole(k + 1, close)
                pos = scan = close + 1
                continue
            if char == "<":
                if _RX["xml_start"].match(text, k) is None:
                    scan = k + 1  # "a < b": a stray '<', judged as text
                    continue
                end = _xml_region_end(text, k)
                if end is not None and not _expat_accepts(text, k, end):
                    end = None
            else:
                strong = _RX["json_strong"].match(text, k) is not None
                if not strong and _RX["json_weak"].match(text, k) is None:
                    scan = k + 1
                    continue
                stop, ok = _json_scan(text, k)
                end = stop if ok and _json_module_accepts(text, k, stop) else None
            if end is None:  # malformed or ambiguous: one value to the end of the text
                to_end(k)
                break
            if not outside(pos, k):
                break
            # the skeleton keeps its own extra caution (quoted text never pairs tags) on validated input
            region = text[k:end]
            spans.extend((k + a, k + b) for a, b in self._skeleton(region))
            if char == "<":
                spans.extend((k + a, k + b) for a, b in _embedded_json_values(region))
            pos = scan = end
        return spans

    def _skeleton(self, text: str) -> list[Span]:
        """DEFAULT-DENY net for payloads and structured text (XML, JSON, both, broken or not). The only
        text that survives is: markup syntax (``< > </ /> = <!-- --> <![CDATA[ ]]> <? ?> <!``, quotes
        of attribute values and JSON strings, JSON ``{ } [ ] , :``), element/attribute names and JSON
        keys in the allowed name set (:meth:`_allowed_name`) that stand in their structural position,
        the JSON literals true/false/null in a JSON value position, and blanks. Every other character
        (text nodes, attribute values, comments, CDATA, processing instructions, doctype, strings,
        numbers, punctuation) is replaced; each maximal run of replaced characters (blanks inside a line
        included) is one span.

        Two passes, both linear: the first reads the text once, marks the unconditional syntax and
        collects the COMPLETE tags found in structural context (never inside a comment, CDATA, PI,
        doctype, attribute value, JSON string or quoted plain text: after an odd number of '"', or of
        "'" that are not apostrophes inside a word, every '<' of plain text is data); the second
        pairs opening and closing tags with a stack (proper nesting only) and keeps an element name
        only for a self-closing tag or a paired one."""
        size = len(text)
        keep = bytearray(size)  # 1 = this character survives
        finder = _Finder(text)
        tags: list[_Tag] = []
        depth = 0  # JSON nesting outside tags: , : and literals count as syntax only inside JSON
        quoted = False  # inside "..." of plain text: a '<' there is data, never a tag
        squoted = False  # inside '...' of plain text (an apostrophe between two letters does not count)

        def mark(a: int, b: int) -> None:
            keep[a:b] = b"\x01" * (b - a)

        i = 0
        while i < size:
            char = text[i]
            if _is_blank(char):
                keep[i] = 1
                i += 1
                continue
            if char == "<" and not quoted and not squoted:
                for opener, closer in (("<!--", "-->"), ("<![CDATA[", "]]>")):
                    if text.startswith(opener, i):
                        mark(i, i + len(opener))
                        close = finder.find(closer, i + len(opener))
                        mark(close, min(size, close + len(closer)))
                        i = close + len(closer)
                        break
                else:
                    tag, i = self._tag(text, i, keep, mark, finder)
                    if tag is not None:
                        tags.append(tag)
                continue
            if char in "{[":
                depth += 1
                keep[i] = 1
            elif char in "}]":
                depth = max(0, depth - 1)
                keep[i] = 1
            elif char in ",:" and depth:
                keep[i] = 1
            elif char == '"' and depth:
                close = _closing_quote(text, i + 1, '"')
                keep[i] = 1
                if close < size:
                    keep[close] = 1
                    j = close + 1
                    while j < size and _is_blank(text[j]):
                        j += 1
                    if j < size and text[j] == ":" and self._allowed_name(text[i + 1 : close]):
                        mark(i + 1, close)
                i = close + 1
                continue
            elif depth and text.startswith(("true", "false", "null"), i):
                word = _RX["word"].match(text, i)
                end = word.end() if word else i + 1
                back = i - 1
                while back >= 0 and _is_blank(text[back]):
                    back -= 1
                ahead = end
                while ahead < size and _is_blank(text[ahead]):
                    ahead += 1
                before = text[back] if back >= 0 else ""
                after = text[ahead] if ahead < size else ""
                if text[i:end] in ("true", "false", "null") and before in (":", "[", ",") and after in ",}]":
                    mark(i, end)
                i = end
                continue
            elif char == '"':
                quoted = not quoted
            elif char == "'" and not (0 < i < size - 1 and text[i - 1].isalnum() and text[i + 1].isalnum()):
                squoted = not squoted
            i += 1
        # second pass: pair the complete tags (stack), then keep what they prove
        stack: list[_Tag] = []
        where: dict[str, list[int]] = {}  # name -> stack indices of the open tags with that name
        for tag in tags:
            if tag.kind == "open":
                where.setdefault(tag.name, []).append(len(stack))
                stack.append(tag)
            elif tag.kind == "close" and where.get(tag.name):
                index = where[tag.name][-1]
                if index == len(stack) - 1:  # properly nested: a pair
                    stack[index].paired = tag.paired = True
                while len(stack) > index:  # each frame is popped once: linear overall
                    where[stack.pop().name].pop()
        for tag in tags:  # only now is every pairing known
            for start, end in tag.syntax:
                mark(start, end)
            if tag.name_span is not None and (tag.kind == "empty" or tag.paired):
                mark(*tag.name_span)
        spans: list[Span] = []
        for run in _RX["zero_runs"].finditer(keep):  # maximal runs of replaced characters
            start, end = run.span()
            if spans and not text[spans[-1][1] : start].strip(" \t"):
                spans[-1] = (spans[-1][0], end)  # blanks inside a line join two runs into one value
            else:
                spans.append((start, end))
        return spans

    def _tag(self, text: str, i: int, keep: bytearray, mark: Any, finder: _Finder) -> tuple[_Tag | None, int]:
        """Read the tag (or the stray '<') at ``i``: the complete tag found, if any, and the next position.

        Default-deny for malformed shapes: a tag counts only when it is CLOSED by '>' or '/>' (a broken
        tag is data); a name is a WHOLE token (everything up to a blank, ``< > = / " '``) that is a
        valid XML name, else the token is data and is consumed whole (``<Monitor&#65;/>``,
        ``123Monitor=``); an attribute name only when '=' follows it (an orphan word in a tag is
        data). An unquoted value runs to a blank, or, when it holds a quote or an '&', to the end of
        the tag. The element name is kept later, only for a self-closing or a paired tag."""
        size = len(text)
        if text.startswith(("<?", "<!"), i):  # processing instruction, doctype: their content is data
            keep[i] = keep[i + 1] = 1
            closer = "?>" if text[i + 1] == "?" else ">"
            close = finder.find(closer, i + 2)
            mark(close, min(size, close + len(closer)))
            return None, close + len(closer)
        j = i + 1
        closing = j < size and text[j] == "/"
        if closing:
            j += 1
        token_end = _token_end(text, j)
        token = text[j:token_end]
        if not token or not _RX["xml_name"].fullmatch(token):
            # "a < b", "<Monitor&#65;/>", "<123x>": not a tag; the token is data, consumed whole
            return None, max(token_end, i + 1)
        tag = _Tag("close" if closing else "open", token)
        tag.syntax.append((i, j))
        if self._allowed_name(token):
            tag.name_span = (j, token_end)
        k = token_end
        while k < size:
            char = text[k]
            if char == ">" or text.startswith("/>", k):
                end = k + (1 if char == ">" else 2)
                if char == "/" and not closing:
                    tag.kind = "empty"
                tag.syntax.append((k, end))
                return tag, end
            if char == "<":
                return None, k  # broken tag: nothing of it is kept; the next one starts here
            if _is_blank(char):
                keep[k] = 1
                k += 1
                continue
            if char == "=":
                tag.syntax.append((k, k + 1))
                k += 1
                while k < size and _is_blank(text[k]):
                    keep[k] = 1
                    k += 1
                if k < size and text[k] in "\"'":
                    close = finder.find(text[k], k + 1)
                    if close >= size:
                        return None, size  # never closed: the rest is the value (replaced)
                    tag.syntax.extend(((k, k + 1), (close, close + 1)))
                    k = close + 1
                    continue
                while k < size and not _is_blank(text[k]) and text[k] not in "<>\"'&":
                    k += 1
                if k < size and text[k] in "\"'&":  # a quote or an entity: the value runs to the tag end
                    k = min(finder.find(">", k), finder.find("<", k))
                continue
            if char in "\"'":  # a quoted text without '=' (malformed): data, quotes included
                close = finder.find(char, k + 1)
                if close >= size:
                    return None, size
                k = close + 1
                continue
            end = _token_end(text, k)
            if end == k:  # a lone '/' or another delimiter that is not markup here: data
                k += 1
                continue
            look = end
            while look < size and _is_blank(text[look]):
                look += 1
            name = text[k:end]
            if (
                look < size
                and text[look] == "="
                and _RX["xml_name"].fullmatch(name)
                and self._allowed_name(name)
            ):
                tag.syntax.append((k, end))
            k = end  # the whole token is consumed: recognition never restarts inside it
        return None, k

    # -- internals

    def _redact_text(self, text: str, payload: bool, extra: RecordSpans | None, identifier: bool) -> str:
        # Every rule runs on the raw text AND on its decoded views (escapes undone, and also JSON string
        # escapes such as \\/); each match is mapped back to the original characters. Then:
        #   1. protection: a generic span inside an exception is dropped (user or automatic exception);
        #      structured, template and strict spans are dropped only inside a USER exception;
        #   2. a URL, path or e-mail that continues in the raw text is extended to its end;
        #   3. the intervals are united (the span that starts first, then the higher priority, names
        #      the category), so a value is never left half visible.
        views: list[tuple[str, list[int] | None, list[int] | None]] = [(text, None, None)]
        plain = decode_view(text)
        if plain is not None:
            views.append(plain)
        jsonish = decode_view(text, json_strings=True)
        if jsonish is not None and (plain is None or jsonish[0] != plain[0]):
            views.append(jsonish)
        strict = self.strict
        jsonish_source = (
            views[-1][0] if jsonish is not None and len(views) > 1 and views[-1] is jsonish else None
        )
        generic: list[tuple[int, int, int, str]] = []  # (start, priority, end, category)
        kept: list[tuple[int, int, int, str]] = []  # structured spans
        nets: list[tuple[int, int, str]] = []  # strict spans: they fill the gaps the other rules leave
        user_protected: list[Span] = []
        auto_protected: list[Span] = []
        user_exc, auto_exc = self._user_exceptions, self._auto_exceptions
        for number, (source, starts, ends) in enumerate(views):

            def back(
                a: int, b: int, starts: list[int] | None = starts, ends: list[int] | None = ends
            ) -> Span:
                return (a, b) if starts is None or ends is None else (starts[a], ends[b - 1])

            if self._user_rx is not None:
                user_protected.extend(back(*m.span()) for m in self._user_rx.finditer(source))
            if (payload or strict) and self._fields:
                for a, b, category in structured_spans(source, self._fields):
                    if b <= a:
                        continue
                    seen = source[a:b].strip().casefold()
                    ra, rb = back(a, b)
                    if user_exc and (seen in user_exc or text[ra:rb].strip().casefold() in user_exc):
                        user_protected.append((ra, rb))
                        continue
                    kept.append((ra, _STRUCTURED_PRIO, rb, category))
            rules: list[tuple[str, int, Iterable[Span], bool]] = [
                (category, prio, finder(source), id(finder) in self._shape_finders)
                for category, prio, finder in self._finders
            ]
            rules.extend(
                (category, prio, ((m.start(), m.end()) for m in rx.finditer(source)), category == TERM)
                for category, prio, rx in self._literal_rules
            )
            for category, prio, found, shaped in rules:
                for a, b in found:
                    seen = source[a:b].casefold()
                    ra, rb = back(a, b)
                    if user_exc and (seen in user_exc or text[ra:rb].casefold() in user_exc):
                        user_protected.append((ra, rb))
                    elif shaped and auto_exc and (seen in auto_exc or text[ra:rb].casefold() in auto_exc):
                        auto_protected.append((ra, rb))
                    else:
                        generic.append((ra, prio, rb, category))
            # the label regex reads JSON-escaped labels (\"user\":) on the raw text: the view that only
            # undoes JSON string escapes adds nothing to the free-text net
            # (a payload keeps its own net even with the record's spans: the two are united)
            if strict and (extra is None or payload) and not (number and source is jsonish_source):
                found = (
                    self._free_spans(source, payload) if number == 0 else self._decoded_spans(source, payload)
                )
                for a, b, category in found:
                    ra, rb = back(a, b)
                    nets.append((ra, rb, category))
        if strict:
            for a, b, category in extra or ():
                nets.append((a, b, category))
            if payload:
                nets.extend((a, b, VALUE) for a, b in self._structure_spans(text))
            if identifier:
                first = len(text) - len(text.lstrip())
                last = len(text.rstrip())
                if last > first:
                    nets.append((first, last, VALUE))
        in_user = _container(user_protected)
        in_auto = _container(auto_protected)
        spans = [s for s in generic if not in_user(s[0], s[2]) and not in_auto(s[0], s[2])]
        spans.extend(s for s in kept if s[2] > s[0] and not in_user(s[0], s[2]))
        spans = self._extend_tokens(text, spans)
        if nets:
            # the strict spans lose what a user exception protects and what the other rules already
            # replace (those markers keep their own category), and are trimmed to their words
            taken = [(s[0], s[2]) for s in spans]
            wordy = _container([(a, b) for a, b, _c in nets if _RX["word"].search(text, a, b)])
            spans.extend(
                (a, _ATTACH if leftover and wordy(a, b) else _PRIO.get(category, _PRIO[VALUE]), b, category)
                for a, b, category, leftover in _fill_gaps(text, nets, user_protected, taken)
            )
        if not spans:
            return text
        spans.sort(key=lambda s: (s[0], s[1], -s[2]))
        # [start, end, category, made only of leftover punctuation, start and end of the value itself]
        merged: list[list[Any]] = []
        for start, prio, end, category in spans:
            attach = prio == _ATTACH
            if merged and (start < merged[-1][1] or (start == merged[-1][1] and (attach or merged[-1][3]))):
                last = merged[-1]
                if last[3] and not attach:  # leftover "(" before a value: the value names the marker
                    last[2], last[3], last[4], last[5] = category, False, start, end
                elif not attach and end > last[5]:
                    last[5] = end
                last[1] = max(last[1], end)
            else:
                merged.append([start, end, category, attach, start, end])
        out: list[str] = []
        pos = 0
        for start, end, category, _attach, core_start, core_end in merged:
            out.append(text[pos:start])
            # the marker is keyed on the value without the punctuation it absorbed (same value, same marker)
            out.append(self._marker(category, decoded(text[core_start:core_end])))
            pos = end
        out.append(text[pos:])
        return "".join(out)

    @staticmethod
    def _extend_tokens(text: str, spans: list[tuple[int, int, int, str]]) -> list[tuple[int, int, int, str]]:
        """A URL, path or e-mail span that continues in the raw text runs to the end of its token, before
        the intervals are united (so a span that the extension reaches is not dropped)."""
        if not any(s[3] in _TOKEN_CATEGORIES for s in spans):
            return spans
        run_starts: list[int] = []
        run_ends: list[int] = []
        for match in _TOKEN_RUN.finditer(text):
            run_starts.append(match.start())
            run_ends.append(match.end())
        out = []
        for start, prio, end, category in spans:
            if category in _TOKEN_CATEGORIES and end < len(text):
                index = bisect.bisect_right(run_starts, end) - 1
                if index >= 0 and run_ends[index] > end:
                    end = run_ends[index]
            out.append((start, prio, end, category))
        return out

    def _marker(self, category: str, value: str) -> str:
        key = (category, _normalize(category, value))
        marker = self._markers.get(key)
        self._replaced[category] = self._replaced.get(category, 0) + 1
        if marker is not None:
            return marker
        label = LABELS[category]
        if len(self._markers) >= MAX_DISTINCT:
            self.overflow += 1
            return f"<{label}-*>"
        number = self._distinct.get(category, 0) + 1
        self._distinct[category] = number
        marker = f"<{label}-{number}>"
        self._markers[key] = marker
        self._originals[marker] = value[:MAX_STORED_VALUE_CHARS]
        return marker
