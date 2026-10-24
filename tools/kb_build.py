"""Build the knowledge pack (JSON) from the Obsidian vault.

Usage:
    python tools/kb_build.py --vault <vault dir> [--out <pack dir>] [--check] [--vault-commit auto|none|<sha>]
        [--no-rt] [--rt-curated <rt_services.toml>] [--rt-summary first-sentence|none]

Reads only ``.md`` files from the vault (read-only, nothing is executed) and writes deterministic JSON
(sorted keys and rows, indent=2, LF, UTF-8, trailing newline, no timestamps). With ``--check`` nothing is
written: the pack is built in memory and the command fails when ``--out`` differs from it.

The pack is derived from product documentation, so it never goes into the repository (ARQUITETURA D10).
``--out`` defaults to ``<data dir>/knowledge/pack`` (``%LOCALAPPDATA%\\dmslog`` on Windows,
``$XDG_DATA_HOME/dmslog`` elsewhere), exactly where ``dmslog`` looks for it; ``--data-dir`` changes the
data directory the same way as in ``dmslog``. Writing anywhere inside this repository is refused.

See docs/ARQUITETURA.md section 7.1 and src/dmslog/knowledge/PACK_SCHEMA.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import tomllib
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dmslog.knowledge.pack_schema import (  # noqa: E402
    OPTIONAL_PACK_FILES,
    PACK_FILES,
    RT_MAX_COUNTERS,
    RT_MAX_REFERENCES,
    RT_MAX_SUMMARY,
    RT_MAX_TEXT,
    RT_SERVICES_FILE,
    SCHEMA_VERSION,
    validate_rt_services,
)

MAX_NOTE_BYTES = 4 * 1024 * 1024  # a note larger than this is refused before it is decoded
MAX_PARAGRAPH_CHARS = 4096  # candidate paragraph of the RT summary, cut before any regex

GENERATOR_VERSION = "1.1.0"
NOTICE = (
    "This pack is derived from Schneider Electric documentation (ADMS GridOps/ADMS). "
    "The repository must stay private; do not publish this pack, nor artifacts that embed it."
)

# Vault names, written with escapes so this file stays ASCII.
SECTION_DIR = "12 Interfaces online"
OVERVIEW_NOTE = "Interfaces online.md"
CATALOG_DIR = "Catálogos"
ERROR_CATALOG = "Catálogo de códigos de erro.md"
OPERATION_CATALOG = "Catálogo de operações.md"
PARAMETER_CATALOG = "Catálogo de parâmetros de configuração.md"
H_BLOCK = "Bloco"
H_SERVICE = "Serviço"
H_OPERATION = "Operação"
H_TAB = "Aba"
H_SERVICE_BLOCK = "Serviço/bloco"

# Parameters that matter for log and timing analysis (ARQUITETURA 7.1).
LOG_RELEVANT_PARAMETERS = frozenset(
    {
        "AuditingPeriod",
        "LogFileName",
        "LogLevel",
        "MessageLogLevel",
        "ModelUpdatersLogLevel",
        "NumOfRetries",
        "RetryTimeout",
        "SendTimeout",
        "WaitForCRSCallback",
    }
)

ADAPTER_TYPES = {
    "soap/wsdl": "soap",
    "arquivos": "files",
    "rest/openapi": "rest",
    "multispeak": "multispeak",
}

_HEADING = re.compile(r"^##\s+(.+?)\s+—\s+(.+?)\s+\(\[\[([A-Za-z0-9]+) Index(?:\\?\|[^\]]*)?\]\]\)\s*$")
_PROCESS_PREFIX = re.compile(r"\b([A-Z][A-Za-z]*Adapter)_")


class BuildError(Exception):
    """The vault does not have the expected shape."""


# ----- Markdown parsing -------------------------------------------------------------------------------


def split_row(line: str) -> list[str]:
    """Split a table row on unescaped pipes that are outside ``[[...]]`` and backtick spans."""
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    cells: list[str] = []
    current: list[str] = []
    depth = 0
    in_code = False
    i = 0
    while i < len(text):
        char = text[i]
        if char == "\\" and i + 1 < len(text) and text[i + 1] == "|":
            current.append("\\|")
            i += 2
            continue
        if char == "`":
            in_code = not in_code
        elif text.startswith("[[", i) and not in_code:
            depth += 1
            current.append("[[")
            i += 2
            continue
        elif text.startswith("]]", i) and depth and not in_code:
            depth -= 1
            current.append("]]")
            i += 2
            continue
        if char == "|" and depth == 0 and not in_code:
            cells.append("".join(current))
            current = []
        else:
            current.append(char)
        i += 1
    cells.append("".join(current))
    return cells


def _wiki_links(text: str) -> str:
    """``[[target|label]]`` -> label (or target); one linear scan (no regex backtracking on bad markdown)."""
    out: list[str] = []
    pos = 0
    close = -2  # cached index of the next "]" at or after the scan point
    while True:
        start = text.find("[[", pos)
        if start < 0:
            break
        if close < start + 2:
            close = text.find("]", start + 2)
            if close < 0:
                break
        if text.startswith("]]", close):
            inner = text[start + 2 : close].replace("\\|", "|")
            target, _, label = inner.partition("|")
            out.append(text[pos:start])
            out.append(label or target)
            pos = close + 2
        else:
            out.append(text[pos : start + 1])
            pos = start + 1
    out.append(text[pos:])
    return "".join(out)


def _md_links(text: str) -> str:
    """``[label](url)`` -> label; one linear scan."""
    out: list[str] = []
    pos = 0
    close = -2  # cached next "]"
    paren = -2  # cached next ")"
    while True:
        start = text.find("[", pos)
        if start < 0:
            break
        if close < start + 1:
            close = text.find("]", start + 1)
            if close < 0:
                break
        if text.startswith("(", close + 1):
            if paren < close + 2:
                paren = text.find(")", close + 2)
            if paren < 0:
                break  # no ")" left: no link can match any more
            out.append(text[pos:start])
            out.append(text[start + 1 : close])
            pos = paren + 1
            continue
        out.append(text[pos : start + 1])
        pos = start + 1
    out.append(text[pos:])
    return "".join(out)


def clean_cell(cell: str) -> str:
    """Resolve escaped pipes, wiki/markdown links and inline-code marks of a cell."""

    text = _md_links(_wiki_links(cell.strip()))
    text = text.replace("\\|", "|")
    if len(text) >= 2 and text.startswith("`") and text.endswith("`"):
        text = text[1:-1]
    return text.strip()


def _is_alignment_row(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells)


def parse_tables(text: str):
    """Yield ``(heading, headers, rows)`` for every table; ``heading`` is the last ``##`` line before it."""
    heading = None
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            heading = line.strip()
        if line.lstrip().startswith("|") and i + 1 < len(lines) and lines[i + 1].lstrip().startswith("|"):
            header_cells = split_row(line)
            if _is_alignment_row(split_row(lines[i + 1])):
                headers = [clean_cell(cell) for cell in header_cells]
                rows = []
                i += 2
                while i < len(lines) and lines[i].lstrip().startswith("|"):
                    cells = split_row(lines[i])
                    if len(cells) != len(headers):
                        raise BuildError(
                            f"table row has {len(cells)} cells, expected {len(headers)} (under '{heading}')"
                        )
                    rows.append([clean_cell(cell) for cell in cells])
                    i += 1
                yield heading, headers, rows
                continue
        i += 1


def parse_frontmatter(text: str) -> dict:
    """Minimal YAML subset: ``key: value`` scalars and ``key:`` followed by ``- item`` lists."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    data: dict = {}
    key = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        item = re.match(r"^\s+-\s+(.*)$", line)
        if item and key is not None:
            data.setdefault(key, []).append(item.group(1).strip().strip('"'))
            continue
        pair = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if pair:
            key = pair.group(1)
            value = pair.group(2).strip().strip('"')
            data[key] = value if value else []
    return data


def parse_heading(heading: str):
    """``## <Adapter> - <Title> ([[ACR Index|ACR]])`` -> ``(adapter, title, acronym)``."""
    match = _HEADING.match(heading or "")
    if not match:
        raise BuildError(f"unexpected section heading: {heading!r}")
    return match.group(1), match.group(2), match.group(3)


def null_if_dash(value: str):
    return None if value in ("", "—", "-") else value


# ----- Vault access -----------------------------------------------------------------------------------


class Vault:
    """Read-only view of the vault that records the sha256 of every file read."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.section = self.root / SECTION_DIR
        if not self.section.is_dir():
            raise BuildError(f"vault section not found: {self.section}")
        self.hashes: dict[str, str] = {}
        self._index = None
        self._root_index = None

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def _check_inside(self, path: Path) -> None:
        """Refuse symlinks and any path that does not resolve inside the resolved vault root."""
        try:
            parts = Path(path).relative_to(self.root).parts
        except ValueError as exc:
            raise BuildError(f"refusing to read a file outside the vault: {path}") from exc
        current = self.root
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise BuildError(f"refusing to follow a symbolic link: {current}")
        real_root = Path(os.path.realpath(self.root))
        real = Path(os.path.realpath(path))
        if real != real_root and not real.is_relative_to(real_root):
            raise BuildError(f"refusing to read a file outside the vault: {path}")

    def _walk_notes(self, base: Path):
        """Sorted ``.md`` files below ``base``; symbolic links (files and directories) are never followed."""
        found = []
        for folder, dirnames, filenames in os.walk(base, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not (Path(folder) / d).is_symlink())
            for name in filenames:
                path = Path(folder) / name
                if name.endswith(".md") and not path.is_symlink():
                    found.append(path)
        return sorted(found)

    def read(self, path: Path) -> str:
        if path.suffix != ".md":
            raise BuildError(f"refusing to read a non-Markdown file: {path}")
        self._check_inside(path)
        with open(path, "rb") as handle:
            data = handle.read(MAX_NOTE_BYTES + 1)
        if len(data) > MAX_NOTE_BYTES:
            raise BuildError(f"note larger than {MAX_NOTE_BYTES} bytes: {path}")
        self.hashes[self.rel(path)] = hashlib.sha256(data).hexdigest()
        return data.decode("utf-8-sig").replace("\r\n", "\n")

    def find(self, filename: str):
        """Path of ``filename`` anywhere in the section (None when absent; error when ambiguous)."""
        if self._index is None:
            self._index = {}
            for path in self._walk_notes(self.section):
                self._index.setdefault(path.name, []).append(path)
        found = self._index.get(filename, [])
        if len(found) > 1:
            raise BuildError(f"ambiguous note name {filename!r}: {[self.rel(p) for p in found]}")
        return found[0] if found else None

    def root_index(self) -> dict[str, list[Path]]:
        """Every ``.md`` note of the vault by file name (``archive`` folders are left out)."""
        if self._root_index is None:
            self._root_index = {}
            for path in self._walk_notes(self.root):
                if "archive" in path.relative_to(self.root).parts[:-1]:
                    continue
                self._root_index.setdefault(path.name, []).append(path)
        return self._root_index

    def find_any(self, filename: str):
        """Path of ``filename`` anywhere in the vault (None when absent; error when ambiguous)."""
        found = self.root_index().get(filename, [])
        if len(found) > 1:
            raise BuildError(f"ambiguous note name {filename!r}: {[self.rel(p) for p in found]}")
        return found[0] if found else None


def find_vault_commit(vault_root: Path):
    """HEAD commit of the git repository that contains the vault, read from ``.git`` files (no git run)."""
    for base in [vault_root.resolve(), *vault_root.resolve().parents]:
        git = base / ".git"
        if git.is_dir():
            try:
                head = (git / "HEAD").read_text(encoding="utf-8").strip()
                if not head.startswith("ref:"):
                    return head or None
                ref = head.split(":", 1)[1].strip()
                ref_file = git / ref
                if ref_file.is_file():
                    return ref_file.read_text(encoding="utf-8").strip()
                packed = git / "packed-refs"
                if packed.is_file():
                    for line in packed.read_text(encoding="utf-8").splitlines():
                        if line.endswith(" " + ref):
                            return line.split(" ", 1)[0]
            except OSError:
                return None
            return None
    return None


# ----- Extraction -------------------------------------------------------------------------------------


def _column_map(headers, mapping, where):
    unknown = [header for header in headers if header not in mapping]
    missing = [header for header in mapping if header not in headers]
    if unknown or missing:
        raise BuildError(f"{where}: unexpected table header (unknown={unknown}, missing={missing})")
    return [mapping[header] for header in headers]


def build_overview(vault: Vault):
    text = vault.read(vault.section / OVERVIEW_NOTE)
    tables = list(parse_tables(text))
    if len(tables) != 1:
        raise BuildError(f"{OVERVIEW_NOTE}: expected exactly one table, found {len(tables)}")
    _, headers, rows = tables[0]
    keys = _column_map(
        headers,
        {
            "Interface": "title",
            "Doc": "acronym",
            "Adapter": "adapter",
            "Tipo": "type_label",
            "Operações (WSDL)": "operations",
            "Configuração": "config",
            "Mapeamentos": "mappings",
            "Exemplos": "examples",
            "Arquivos originais": "files",
            "Versão": "version",
        },
        OVERVIEW_NOTE,
    )
    return [dict(zip(keys, row, strict=True)) for row in rows]


def build_adapters(vault: Vault):
    adapters = []
    for row in build_overview(vault):
        acronym = row["acronym"]
        index_path = vault.find(f"{acronym} Index.md")
        meta = parse_frontmatter(vault.read(index_path)) if index_path else {}
        config_path = vault.find(f"{acronym} Configuration.md")
        process_names: list[str] = []
        if config_path:
            process_names = sorted(set(_PROCESS_PREFIX.findall(vault.read(config_path))))
        type_label = null_if_dash(row["type_label"])
        operations = (
            []
            if null_if_dash(row["operations"]) is None
            else [item.strip() for item in row["operations"].split(",") if item.strip()]
        )
        adapters.append(
            {
                "acronym": acronym,
                "title": row["title"],
                "adapter": null_if_dash(row["adapter"]),
                "type": ADAPTER_TYPES.get(type_label.lower()) if type_label else None,
                "type_label": type_label,
                "operations": sorted(operations),
                "config_note": vault.rel(config_path) if config_path else None,
                "document_title": meta.get("documento") or None,
                "document_version": null_if_dash(row["version"]),
                "aliases": sorted(meta.get("aliases", [])),
                "index_note": vault.rel(index_path) if index_path else None,
                "process_names": process_names,
            }
        )
    adapters.sort(key=lambda item: item["acronym"])
    process_map: dict[str, str] = {}
    for adapter in adapters:
        for process in adapter["process_names"]:
            if process_map.setdefault(process, adapter["acronym"]) != adapter["acronym"]:
                raise BuildError(f"process {process} appears in more than one adapter")
    return {"adapters": adapters, "process_map": dict(sorted(process_map.items()))}


def _catalog_rows(vault: Vault, filename: str, mapping_for_headers):
    """Yield ``(acronym, adapter, record_dict)`` for every table row of a per-adapter catalog."""
    text = vault.read(vault.section / CATALOG_DIR / filename)
    for heading, headers, rows in parse_tables(text):
        adapter, _title, acronym = parse_heading(heading)
        keys = mapping_for_headers(headers, filename)
        for row in rows:
            yield acronym, adapter, dict(zip(keys, row, strict=True))


_ERROR_SHAPES = {
    ("Bloco", "Code", "ADMS Severity", "DMS Reason", "DMS Details", "CIM Reason", "CIM Details"): {
        "Bloco": "block",
        "Code": "code",
        "ADMS Severity": "adms_severity",
        "DMS Reason": "dms_reason",
        "DMS Details": "dms_details",
        "CIM Reason": "cim_reason",
        "CIM Details": "cim_details",
    },
    ("Bloco", "ID", "ADMS Severity", "Log", "Code", "CIM Severity", "Reason", "Details"): {
        "Bloco": "block",
        "ID": "id",
        "ADMS Severity": "adms_severity",
        "Log": "log",
        "Code": "code",
        "CIM Severity": "cim_severity",
        "Reason": "cim_reason",
        "Details": "cim_details",
    },
}


def _error_columns(headers, where):
    mapping = _ERROR_SHAPES.get(tuple(headers))
    if mapping is None:
        raise BuildError(f"{where}: unknown error table header {headers}")
    return [mapping[header] for header in headers]


def build_error_codes(vault: Vault):
    rows = []
    for acronym, adapter, record in _catalog_rows(vault, ERROR_CATALOG, _error_columns):
        entry = {
            "acronym": acronym,
            "adapter": adapter,
            "block": "",
            "id": None,
            "code": "",
            "adms_severity": "",
            "log": None,
            "dms_reason": None,
            "dms_details": None,
            "cim_severity": None,
            "cim_reason": None,
            "cim_details": None,
        }
        entry.update(record)
        rows.append(entry)
    rows.sort(
        key=lambda r: (
            r["acronym"],
            r["block"],
            r["id"] or "",
            r["code"],
            r["dms_reason"] or "",
            r["cim_reason"] or "",
            r["adms_severity"],
        )
    )
    return {"error_codes": rows}


def _operation_columns(headers, where):
    return _column_map(
        headers,
        {
            "WSDL": "wsdl",
            H_SERVICE: "service",
            H_OPERATION: "operation",
            "SOAPAction": "soap_action",
            "Entrada": "input",
            "Saída": "output",
            "Endpoint": "endpoint",
        },
        where,
    )


def operation_direction(service: str):
    """Heuristic from the service name: ``Receive*`` is hosted by ADMS (inbound), ``Send*`` is outbound."""
    if service.startswith("Receive"):
        return "inbound"
    if service.startswith("Send"):
        return "outbound"
    return None


def build_operations(vault: Vault):
    rows = []
    for acronym, adapter, record in _catalog_rows(vault, OPERATION_CATALOG, _operation_columns):
        record.update(acronym=acronym, adapter=adapter, direction=operation_direction(record["service"]))
        rows.append(record)
    rows.sort(key=lambda r: (r["acronym"], r["service"], r["operation"], r["wsdl"], r["endpoint"]))
    return {"operations": rows}


def _parameter_columns(headers, where):
    return _column_map(
        headers,
        {
            H_TAB: "tab",
            H_SERVICE_BLOCK: "block",
            "Property Name": "name",
            "Description": "description",
            "Value Type": "value_type",
            "Default Value": "default",
            "Domain of Values": "domain",
        },
        where,
    )


def build_parameters(vault: Vault):
    rows = []
    for acronym, adapter, record in _catalog_rows(vault, PARAMETER_CATALOG, _parameter_columns):
        record.update(acronym=acronym, adapter=adapter)
        record["log_relevant"] = record["name"] in LOG_RELEVANT_PARAMETERS
        rows.append(record)
    rows.sort(key=lambda r: (r["acronym"], r["tab"], r["block"], r["name"]))
    return {"parameters": rows}


# ----- RT services (rt_services.json) -----------------------------------------------------------------

DEFAULT_RT_CURATED = (
    Path(__file__).resolve().parents[1].joinpath("src", "dmslog", "knowledge", "curated", "rt_services.toml")
)
RT_SUMMARY_MODES = ("first-sentence", "none")
# Closed list of documents searched for references, in priority order (ARQUITETURA 7.1).
RT_REFERENCE_DOCS = (
    "SARC",
    "PAIN",
    "PRFI",
    "SCOM",
    "AVEL",
    "SMAN",
    "BCON",
    "MNTN",
    "INST",
    "SWFS",
    "SQEO",
    "NDEX",
)
# Supporting notes (structure only; a missing note leaves the fields empty).
EXECUTABLES_NOTE = "AVEL 2.1 Exclusion List for Executables.md"
COUNTERS_NOTE = "SCOM 5.6 EcoStruxure GridOps Specific Performance Counters.md"
MONITORING_NOTE = "SCOM 5.11 EcoStruxure GridOps Process Performance Counter.md"
USAGE_EVENTS_NOTE = "SMAN Usage Events.md"
H_APPLICATION_KEY = "ApplicationKey"

_NUMBERED_HEADING = re.compile(r"^(#+)\s+(\d+(?:\.\d+)*)\.?\s+(.*?)\s*$")
_BULLETS = re.compile(r"[•\s]+")
_PROGRAM_FILES = re.compile(r"^[A-Za-z]:\\Program Files(?: \(x86\))?\\[^\\]+\\")
_DRIVE = re.compile(r"^[A-Za-z]:\\")
_NOT_PROSE = re.compile(r"^(?:[-+•]\s|\*(?!\*)|\d+[.)]\s|!\[|>|<|\||#)")
_FENCE = re.compile(r"^(?:```|~~~)")
_ABBREVIATIONS = frozenset({"e.g", "i.e", "etc", "vs", "cf", "fig", "approx"})
_SENTENCE_MARK = re.compile(r"[.!?]")
_MAX_LINE_CHARS = 1000  # longer lines are not scanned for headings


def _plain(text: str) -> str:
    """Single-line plain text: links resolved, emphasis and code marks removed, spaces collapsed."""
    text = clean_cell(text).replace("**", "").replace("`", "")
    text = "".join(" " if unicodedata.category(ch) in ("Cc", "Zl", "Zp") else ch for ch in text)
    return " ".join(text.split())


def _heading(line: str):
    return _NUMBERED_HEADING.match(line) if len(line) <= _MAX_LINE_CHARS else None


def _normalize(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _body(text: str) -> str:
    """The note without its front matter."""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for index, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                return "\n".join(lines[index + 1 :])
    return text


def find_section(text: str, section: str):
    """``(title, lines below the heading)`` of the heading numbered ``section`` (None when absent)."""
    lines = _body(text).splitlines()
    for index, line in enumerate(lines):
        match = _heading(line)
        if not match or match.group(2) != section:
            continue
        level = len(match.group(1))
        below = []
        for following in lines[index + 1 :]:
            other = re.match(r"^(#+)\s", following[:_MAX_LINE_CHARS])
            if other and len(other.group(1)) <= level:
                break
            below.append(following)
        return _plain(match.group(3))[:RT_MAX_TEXT], below
    return None


def _sentence_end(text: str):
    """Index just after the first sentence end of ``text`` (None: the sentence runs to the end)."""
    for mark in _SENTENCE_MARK.finditer(text):
        end = mark.end()
        if end == len(text):
            return None
        if not text[end].isspace():
            continue  # "3.5", "a.b", "file.txt"
        word = text[:end].rsplit(" ", 1)[-1].rstrip(".!?").lower()
        if mark.group() == "." and (word in _ABBREVIATIONS or (len(word) == 1 and word.isalpha())):
            continue
        return end
    return None


def first_sentence(lines: list[str], limit: int = RT_MAX_SUMMARY):
    """First sentence of the first prose paragraph (None when there is no prose).

    Fenced code blocks, quotes, tables, lists, figures and captions are skipped; the candidate paragraph
    is cut at ``MAX_PARAGRAPH_CHARS`` before any pattern runs and the result is one line of at most
    ``limit`` characters.
    """
    paragraph: list[str] = []
    size = 0
    fenced = False
    for line in lines:
        stripped = line.strip()
        if _FENCE.match(stripped):
            fenced = not fenced
            if paragraph:
                break
            continue
        if fenced:
            continue
        if not stripped or _NOT_PROSE.match(stripped):
            if paragraph:
                break
            continue
        paragraph.append(stripped)
        size += len(stripped) + 1
        if size >= MAX_PARAGRAPH_CHARS:
            break
    text = _plain(" ".join(paragraph)[:MAX_PARAGRAPH_CHARS])
    if not text:
        return None
    end = _sentence_end(text)
    sentence = text[:end].strip() if end else text
    if len(sentence) > limit:
        head = sentence[: limit - 1]
        if " " in head and not sentence[limit - 1].isspace():
            head = head.rsplit(" ", 1)[0]
        sentence = head.rstrip(" ,;:") + "…"
    return sentence


def load_rt_curated(path: Path) -> list[dict]:
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise BuildError(f"cannot read the curated RT services file {path}: {exc}") from exc
    services = data.get("service", [])
    seen: set[str] = set()
    for entry in services:
        process, family = entry.get("process"), entry.get("family")
        if not isinstance(process, str) or not isinstance(family, str):
            raise BuildError(f"{path}: every service needs a 'process' and a 'family'")
        if process in seen:
            raise BuildError(f"{path}: duplicate process {process}")
        seen.add(process)
        vault = entry.get("vault", "absent")
        if isinstance(vault, dict):
            if not all(isinstance(vault.get(key), str) and vault[key] for key in ("doc", "note", "section")):
                raise BuildError(f"{path}: {process}: 'vault' needs doc, note and section")
        elif vault != "absent":
            raise BuildError(f"{path}: {process}: 'vault' must be a table or \"absent\"")
    return services


def _candidates(entry: dict) -> list[str]:
    """Identifiers searched for: the process, the process without ``DMS_``/``OMS_`` and the ``names``."""
    process = entry["process"]
    found = [process, re.sub(r"^(?:DMS|OMS)_", "", process), *entry.get("names", [])]
    return sorted({name for name in found if name}, key=lambda name: (-len(name), name))


def _word_regex(names: list[str]) -> re.Pattern:
    alternatives = "|".join(re.escape(name) for name in names)
    return re.compile(rf"(?<![A-Za-z0-9_])(?:{alternatives})(?![A-Za-z0-9])")


def _read_support(vault: Vault, filename: str):
    path = vault.find_any(filename)
    return vault.read(path) if path else None


def _install_dirs(text: str | None) -> list[tuple[str, set[str]]]:
    """``(folder, executable names)`` for every heading of the executables note."""
    result: list[tuple[str, set[str]]] = []
    if not text:
        return result
    current = None
    names: set[str] = set()
    for line in _body(text).splitlines():
        heading = re.match(r"^##+\s+[\d.]*\s*(.*?)\s*$", line[:_MAX_LINE_CHARS])
        if heading:
            if current is not None:
                result.append((current, names))
            title = _plain(heading.group(1))
            title = _PROGRAM_FILES.sub("", title) if _PROGRAM_FILES.match(title) else _DRIVE.sub("", title)
            current, names = title[:RT_MAX_TEXT], set()
        elif current is not None:
            names.update(token for token in _BULLETS.split(line) if token.endswith(".exe"))
    if current is not None:
        result.append((current, names))
    return result


def _monitoring(text: str | None) -> list[tuple[str, set[str]]]:
    """``(cpu|ram|hdd, process names)`` for every table of the process monitoring note."""
    result = []
    if not text:
        return result
    for _heading, headers, rows in parse_tables(_body(text)):
        kind = headers[0].replace("*", "").strip().lower() if headers else ""
        if kind not in ("cpu", "ram", "hdd") or len(headers) < 2:
            continue
        names: set[str] = set()
        for row in rows:
            names.update(token for token in _BULLETS.split(row[1]) if token)
        result.append((kind, names))
    return result


def _counter_blocks(text: str | None) -> list[tuple[str, list[str]]]:
    """``(normalized block name, counter names)`` for every counter table."""
    result = []
    if not text:
        return result
    for _heading, headers, rows in parse_tables(_body(text)):
        block = _normalize(headers[0]) if headers else ""
        if len(headers) < 2 or not block:
            continue
        result.append((block, [name for name in (_plain(row[0]) for row in rows) if name]))
    return result


def _usage_events(text: str | None) -> list[str]:
    """Normalized ``ApplicationKey`` of every usage event row."""
    keys: list[str] = []
    if not text:
        return keys
    for _heading, headers, rows in parse_tables(_body(text)):
        if H_APPLICATION_KEY in headers:
            column = headers.index(H_APPLICATION_KEY)
            keys.extend(_normalize(row[column]) for row in rows)
    return keys


def _reference_notes(vault: Vault):
    """``(doc, relative path, body)`` of the notes of the closed document list, in priority order."""
    found = []
    for priority, doc in enumerate(RT_REFERENCE_DOCS):
        for name, paths in vault.root_index().items():
            if name.startswith(doc + " "):
                found.extend((priority, vault.rel(path), doc, path) for path in paths)
    found.sort(key=lambda item: item[:2])
    return [(doc, rel, _body(vault.read(path))) for _priority, rel, doc, path in found]


def _references(notes, entry: dict, own_note: str | None):
    pattern = _word_regex(_candidates(entry))
    found = []
    for doc, rel, body in notes:
        if rel == own_note:
            continue
        matches = list(pattern.finditer(body))
        if not matches:
            continue
        section = None
        for line in reversed(body[: matches[0].start()].splitlines()):
            heading = _heading(line)
            if heading:
                section = heading.group(2)
                break
        found.append((RT_REFERENCE_DOCS.index(doc), -len(matches), rel, doc, section))
    found.sort(key=lambda item: item[:3])
    return [
        {"doc": doc, "note": rel, "section": section, "mentions": -negative}
        for _priority, negative, rel, doc, section in found[:RT_MAX_REFERENCES]
    ]


def build_rt_services(vault: Vault, services: list[dict], summary_mode: str = "first-sentence"):
    """The rows of ``rt_services.json`` from the curated services and the vault (sorted by process)."""
    install_dirs = _install_dirs(_read_support(vault, EXECUTABLES_NOTE))
    monitoring = _monitoring(_read_support(vault, MONITORING_NOTE))
    counter_blocks = _counter_blocks(_read_support(vault, COUNTERS_NOTE))
    usage_keys = _usage_events(_read_support(vault, USAGE_EVENTS_NOTE))
    sections: dict[str, tuple[str, str, str | None]] = {}
    cache: dict[tuple[str, str], tuple[str, list[str]]] = {}
    for entry in services:
        ref = entry.get("vault", "absent")
        if not isinstance(ref, dict):
            continue
        path = vault.find_any(ref["note"])
        if path is None:
            raise BuildError(f"{entry['process']}: note {ref['note']!r} not found in the vault")
        key = (ref["note"], ref["section"])
        if key not in cache:
            found = find_section(vault.read(path), ref["section"])
            if found is None:
                raise BuildError(f"{entry['process']}: section {ref['section']} not found in {ref['note']!r}")
            cache[key] = found
        title, below = cache[key]
        summary = first_sentence(below) if summary_mode == "first-sentence" else None
        sections[entry["process"]] = (title, vault.rel(path), summary)
    notes = _reference_notes(vault)
    rows = []
    for entry in sorted(services, key=lambda item: item["process"]):
        process = entry["process"]
        names = _candidates(entry)
        normalized = {_normalize(name) for name in names}
        title, note, summary = sections.get(process, (None, None, None))
        section = entry["vault"]["section"] if note else None
        counters = sorted(
            {
                c
                for block, items in counter_blocks
                if block in normalized
                for c in items
                if len(c) <= RT_MAX_TEXT
            }
        )
        rows.append(
            {
                "process": process,
                "family": entry["family"],
                "title": title,
                "summary": summary,
                "note": note,
                "section": section,
                "install_dir": next(
                    (folder for folder, exes in install_dirs if any(f"{name}.exe" in exes for name in names)),
                    None,
                ),
                "monitored": sorted({kind for kind, procs in monitoring if procs & set(names)}),
                "counters": counters[:RT_MAX_COUNTERS],
                "usage_events": sum(1 for key in usage_keys if key in normalized),
                "references": _references(notes, entry, note),
            }
        )
    return rows


def rt_coverage(files: dict[str, str]) -> str | None:
    """The coverage line of the RT services (numbers only); None when the pack has no such file."""
    if RT_SERVICES_FILE not in files:
        return None
    rows = json.loads(files[RT_SERVICES_FILE])["rt_services"]
    total = len(rows)
    with_section = sum(1 for row in rows if row["note"] is not None)
    with_references = sum(1 for row in rows if row["references"])
    return (
        f"rt: {with_section}/{total} services with a vault section, {with_references}/{total} with references"
    )


def _validate_rt_output(value: dict) -> None:
    """The loader drops the whole RT block when the file is invalid, so never produce an invalid one."""
    with tempfile.TemporaryDirectory() as folder:
        Path(folder, RT_SERVICES_FILE).write_text(dump(value), encoding="utf-8", newline="\n")
        errors = validate_rt_services(folder)
    if errors:
        raise BuildError("generated " + RT_SERVICES_FILE + " is invalid: " + "; ".join(errors[:5]))


def build_pack(
    vault_root, vault_commit="auto", rt_curated=DEFAULT_RT_CURATED, rt_summary="first-sentence"
) -> dict[str, str]:
    """Build the pack in memory: ``{file name: text}`` (deterministic).

    ``rt_curated`` is the curated RT services file (None: no ``rt_services.json``; the default file is
    skipped when it does not exist); ``rt_summary`` is ``first-sentence`` or ``none`` (no vault text).
    """
    vault_root = Path(vault_root)
    vault = Vault(vault_root)
    if rt_summary not in RT_SUMMARY_MODES:
        raise BuildError(f"unknown RT summary mode {rt_summary!r}")
    data = {
        "adapters.json": build_adapters(vault),
        "error_codes.json": build_error_codes(vault),
        "operations.json": build_operations(vault),
        "parameters.json": build_parameters(vault),
    }
    if rt_curated is not None and (Path(rt_curated) != DEFAULT_RT_CURATED or Path(rt_curated).is_file()):
        services = load_rt_curated(rt_curated)
        data[RT_SERVICES_FILE] = {"rt_services": build_rt_services(vault, services, rt_summary)}
        _validate_rt_output(data[RT_SERVICES_FILE])
    if vault_commit == "auto":
        commit = find_vault_commit(vault_root)
    elif vault_commit == "none":
        commit = None
    else:
        commit = vault_commit
    data["manifest.json"] = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "vault_commit": commit,
        "notice": NOTICE,
        "files": dict(sorted(vault.hashes.items())),
    }
    return {name: dump(value) for name, value in data.items()}


def dump(value) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


# ----- CLI --------------------------------------------------------------------------------------------


def write_pack(files: dict[str, str], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        with open(out / name, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)


def diff_pack(files: dict[str, str], out: Path) -> list[str]:
    problems = []
    for name, text in files.items():
        path = out / name
        if not path.is_file():
            problems.append(f"{name}: missing in {out}")
        elif path.read_bytes() != text.encode("utf-8"):
            problems.append(f"{name}: differs from the freshly built pack")
    expected = set(PACK_FILES) | set(OPTIONAL_PACK_FILES)
    for path in sorted(out.glob("*.json")) if out.is_dir() else []:
        if path.name not in expected:
            problems.append(f"{path.name}: unexpected file in {out}")
    return problems


REPO_ROOT = Path(__file__).resolve().parents[1]


def inside_repository(path: Path) -> bool:
    """True when ``path`` is this repository or below it (the pack must never land there)."""
    return path.resolve().is_relative_to(REPO_ROOT)


def default_out(data_dir: Path | None = None) -> Path:
    from dmslog.paths import resolve_data_dir

    return resolve_data_dir(data_dir).joinpath("knowledge", "pack")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the knowledge pack from the vault (read-only).")
    parser.add_argument("--vault", required=True, type=Path, help="vault root directory")
    parser.add_argument(
        "--out", type=Path, default=None, help="pack output directory (default: <data dir>/knowledge/pack)"
    )
    parser.add_argument(
        "--data-dir", type=Path, default=None, help="dmslog data directory used for the default --out"
    )
    parser.add_argument("--check", action="store_true", help="do not write; fail if --out differs")
    parser.add_argument("--vault-commit", default="auto", help="'auto' (read .git), 'none' or a commit id")
    parser.add_argument("--no-rt", action="store_true", help="do not generate rt_services.json")
    parser.add_argument(
        "--rt-curated",
        type=Path,
        default=DEFAULT_RT_CURATED,
        help="curated RT services file (default: the one in src/dmslog/knowledge/curated)",
    )
    parser.add_argument(
        "--rt-summary",
        choices=RT_SUMMARY_MODES,
        default="first-sentence",
        help="'none' leaves the first sentence of each vault section out of rt_services.json",
    )
    args = parser.parse_args(argv)
    if args.out is None:
        args.out = default_out(args.data_dir)
    if not args.check and inside_repository(args.out):
        print(
            f"kb_build: error: refusing to write the pack inside the repository ({args.out}); "
            "it is derived from product documentation and must stay outside it",
            file=sys.stderr,
        )
        return 2
    try:
        files = build_pack(
            args.vault, args.vault_commit, None if args.no_rt else args.rt_curated, args.rt_summary
        )
    except (BuildError, OSError, UnicodeDecodeError) as exc:
        print(f"kb_build: error: {exc}", file=sys.stderr)
        return 2
    if args.check:
        problems = diff_pack(files, args.out)
        for problem in problems:
            print(f"kb_build: {problem}", file=sys.stderr)
        if problems:
            print(
                "kb_build: pack is out of date; run kb_build without --check and review the diff",
                file=sys.stderr,
            )
            return 1
        print("kb_build: pack is up to date")
        return 0
    write_pack(files, args.out)
    print(f"kb_build: wrote {len(files)} files to {args.out}")
    coverage = rt_coverage(files)
    if coverage:
        print(f"kb_build: {coverage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
