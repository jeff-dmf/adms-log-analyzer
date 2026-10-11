"""Build the knowledge pack (JSON) from the Obsidian vault.

Usage:
    python tools/kb_build.py --vault <vault dir> [--out <pack dir>] [--check] [--vault-commit auto|none|<sha>]

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
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dmslog.knowledge.pack_schema import PACK_FILES, SCHEMA_VERSION  # noqa: E402

GENERATOR_VERSION = "1.0.0"
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
LOG_RELEVANT_PARAMETERS = frozenset({
    "AuditingPeriod",
    "LogFileName",
    "LogLevel",
    "MessageLogLevel",
    "ModelUpdatersLogLevel",
    "NumOfRetries",
    "RetryTimeout",
    "SendTimeout",
    "WaitForCRSCallback",
})

ADAPTER_TYPES = {
    "soap/wsdl": "soap",
    "arquivos": "files",
    "rest/openapi": "rest",
    "multispeak": "multispeak",
}

_WIKILINK = re.compile(r"\[\[([^\]]*)\]\]")
_MDLINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
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


def clean_cell(cell: str) -> str:
    """Resolve escaped pipes, wiki/markdown links and inline-code marks of a cell."""

    def wiki(match: re.Match) -> str:
        inner = match.group(1).replace("\\|", "|")
        target, _, label = inner.partition("|")
        return label or target

    text = _WIKILINK.sub(wiki, cell.strip())
    text = _MDLINK.sub(r"\1", text)
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
                            f"table row has {len(cells)} cells, expected {len(headers)} "
                            f"(under '{heading}')"
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

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def read(self, path: Path) -> str:
        if path.suffix != ".md":
            raise BuildError(f"refusing to read a non-Markdown file: {path}")
        data = path.read_bytes()
        self.hashes[self.rel(path)] = hashlib.sha256(data).hexdigest()
        return data.decode("utf-8-sig").replace("\r\n", "\n")

    def find(self, filename: str):
        """Path of ``filename`` anywhere in the section (None when absent; error when ambiguous)."""
        if self._index is None:
            self._index = {}
            for path in sorted(self.section.rglob("*.md")):
                self._index.setdefault(path.name, []).append(path)
        found = self._index.get(filename, [])
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
            "Interface": "title", "Doc": "acronym", "Adapter": "adapter", "Tipo": "type_label",
            "Operações (WSDL)": "operations", "Configuração": "config",
            "Mapeamentos": "mappings", "Exemplos": "examples", "Arquivos originais": "files",
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
        operations = [] if null_if_dash(row["operations"]) is None else [
            item.strip() for item in row["operations"].split(",") if item.strip()
        ]
        adapters.append({
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
        })
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
        "Bloco": "block", "Code": "code", "ADMS Severity": "adms_severity", "DMS Reason": "dms_reason",
        "DMS Details": "dms_details", "CIM Reason": "cim_reason", "CIM Details": "cim_details",
    },
    ("Bloco", "ID", "ADMS Severity", "Log", "Code", "CIM Severity", "Reason", "Details"): {
        "Bloco": "block", "ID": "id", "ADMS Severity": "adms_severity", "Log": "log", "Code": "code",
        "CIM Severity": "cim_severity", "Reason": "cim_reason", "Details": "cim_details",
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
            "acronym": acronym, "adapter": adapter, "block": "", "id": None, "code": "",
            "adms_severity": "", "log": None, "dms_reason": None, "dms_details": None,
            "cim_severity": None, "cim_reason": None, "cim_details": None,
        }
        entry.update(record)
        rows.append(entry)
    rows.sort(key=lambda r: (r["acronym"], r["block"], r["id"] or "", r["code"], r["dms_reason"] or "",
                             r["cim_reason"] or "", r["adms_severity"]))
    return {"error_codes": rows}


def _operation_columns(headers, where):
    return _column_map(
        headers,
        {
            "WSDL": "wsdl", H_SERVICE: "service", H_OPERATION: "operation", "SOAPAction": "soap_action",
            "Entrada": "input", "Saída": "output", "Endpoint": "endpoint",
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
            H_TAB: "tab", H_SERVICE_BLOCK: "block", "Property Name": "name", "Description": "description",
            "Value Type": "value_type", "Default Value": "default", "Domain of Values": "domain",
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


def build_pack(vault_root, vault_commit="auto") -> dict[str, str]:
    """Build the pack in memory: ``{file name: text}`` (deterministic)."""
    vault_root = Path(vault_root)
    vault = Vault(vault_root)
    data = {
        "adapters.json": build_adapters(vault),
        "error_codes.json": build_error_codes(vault),
        "operations.json": build_operations(vault),
        "parameters.json": build_parameters(vault),
    }
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
    expected = set(PACK_FILES)
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
    parser.add_argument("--out", type=Path, default=None,
                        help="pack output directory (default: <data dir>/knowledge/pack)")
    parser.add_argument("--data-dir", type=Path, default=None,
                        help="dmslog data directory used for the default --out")
    parser.add_argument("--check", action="store_true", help="do not write; fail if --out differs")
    parser.add_argument("--vault-commit", default="auto", help="'auto' (read .git), 'none' or a commit id")
    args = parser.parse_args(argv)
    if args.out is None:
        args.out = default_out(args.data_dir)
    if not args.check and inside_repository(args.out):
        print(f"kb_build: error: refusing to write the pack inside the repository ({args.out}); "
              "it is derived from product documentation and must stay outside it", file=sys.stderr)
        return 2
    try:
        files = build_pack(args.vault, args.vault_commit)
    except (BuildError, OSError, UnicodeDecodeError) as exc:
        print(f"kb_build: error: {exc}", file=sys.stderr)
        return 2
    if args.check:
        problems = diff_pack(files, args.out)
        for problem in problems:
            print(f"kb_build: {problem}", file=sys.stderr)
        if problems:
            print("kb_build: pack is out of date; run kb_build without --check and review the diff",
                  file=sys.stderr)
            return 1
        print("kb_build: pack is up to date")
        return 0
    write_pack(files, args.out)
    print(f"kb_build: wrote {len(files)} files to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
