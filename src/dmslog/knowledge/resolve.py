"""Knowledge API: vault pack + installation (ERS) configuration (ARQUITETURA 7.1-7.2).

Precedence: the ERS-effective ``ErrorConfiguration`` wins over the vault pack. A code that the ERS
defines and the vault does not know is returned with ``out_of_vault=True`` (never ``None``).

``curated/hints.toml`` (optional; absent file = no hints) has one table per hint::

    [[hint]]
    code = "WFM-080"                    # template code
    text = "Short explanation."
    params = ["WaitForCRSCallback"]     # related parameters (optional)
    note = "12 Interfaces online/..."   # vault note to open (optional)
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from dmslog.knowledge.installation import InstallationConfig
from dmslog.knowledge.pack import AdapterInfo, Pack, VaultError, VaultParam, load_pack
from dmslog.store.config import product_for_process

_SEVERITY_NUMBERS = {"fatal": 1, "critical": 1, "error": 2, "warning": 3, "warn": 3, "info": 4}


def severity_number(label: str | None) -> int | None:
    """ADMS severity text (``FATAL``, ``Error``, ``WARNING``...) to the 1..4 scale; None if unknown."""
    if not label:
        return None
    return _SEVERITY_NUMBERS.get(label.strip().lower())


@dataclass(frozen=True, slots=True)
class ErrorInfo:
    family: str
    code: str
    operation: str | None
    adms_severity: str | None
    severity: int | None  # 1..4, None when the label is not recognised
    reason: str | None
    details: str | None
    source: str  # "ers" or "vault"
    out_of_vault: bool  # defined by the ERS, absent from the vault
    config_path: str | None = None  # ERS error-configuration file, when source == "ers"


@dataclass(frozen=True, slots=True)
class ParamValue:
    product: str
    name: str
    value: str | tuple[str, ...] | None
    source: str  # "registry" or "vault"
    layer: int | None = None
    source_file: str | None = None
    default: str | None = None
    description: str | None = None
    log_relevant: bool | None = None


@dataclass(frozen=True, slots=True)
class Hint:
    code: str
    text: str
    params: tuple[str, ...] = ()
    note: str | None = None


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


class Knowledge:
    """Read-only lookups over the pack and the ERS installation configuration.

    ``installations`` is one ``InstallationConfig`` (group 0) or a mapping ``group -> config``.
    """

    def __init__(
        self,
        pack: Pack | None = None,
        installations: InstallationConfig | Mapping[int, InstallationConfig] | None = None,
        hints_path: Path | str | None = None,
    ) -> None:
        self._pack = pack
        if isinstance(installations, InstallationConfig):
            installations = {0: installations}
        self._installations: dict[int, InstallationConfig] = dict(installations or {})
        self._hints_path = Path(hints_path) if hints_path is not None else None
        self._hints: dict[str, Hint] | None = None

    @property
    def pack(self) -> Pack:
        if self._pack is None:
            self._pack = load_pack()
        return self._pack

    # -- adapters --------------------------------------------------------------------------------

    def adapter_for_process(self, process: str) -> AdapterInfo | None:
        pack = self.pack
        acronym = pack.process_map.get(process)
        if acronym is None:
            wanted = _norm(process)
            for item in pack.adapters:
                if any(_norm(n) == wanted for n in item.process_names):
                    acronym = item.acronym
                    break
        return pack.adapter(acronym) if acronym else None

    def vault_note(self, family: str) -> str | None:
        adapter = self.pack.adapter(family)
        if adapter is None:
            return None
        return adapter.index_note or adapter.config_note

    # -- errors ----------------------------------------------------------------------------------

    def error(self, family: str, code: str, operation: str | None = None) -> ErrorInfo | None:
        vault = self._vault_error(family, code, operation)
        ers = self._ers_error(family, code, operation)
        if ers is not None:
            entry, path = ers
            reason = entry.reason or (vault.dms_reason or vault.cim_reason if vault else None)
            details = entry.details or (vault.dms_details or vault.cim_details if vault else None)
            label = entry.adms_severity or (vault.adms_severity if vault else None)
            return ErrorInfo(
                family,
                code,
                operation,
                label,
                severity_number(label),
                reason,
                details,
                "ers",
                vault is None,
                path,
            )
        if vault is None:
            return None
        return ErrorInfo(
            family,
            code,
            operation,
            vault.adms_severity,
            severity_number(vault.adms_severity),
            vault.dms_reason or vault.cim_reason,
            vault.dms_details or vault.cim_details,
            "vault",
            False,
        )

    def _vault_error(self, family: str, code: str, operation: str | None) -> VaultError | None:
        best: tuple[int, VaultError] | None = None
        for row in self.pack.errors:
            if row.acronym != family or code not in (row.code, row.id):
                continue
            rank = (
                0 if operation is not None and row.block == operation else (1 if row.block == "Common" else 2)
            )
            if best is None or rank < best[0]:
                best = (rank, row)
        return best[1] if best else None

    def _family_processes(self, family: str) -> set[str]:
        """Process names bound to a family: the adapter's process names plus the process map."""
        names: set[str] = set()
        adapter = self.pack.adapter(family)
        if adapter is not None:
            names.update(adapter.process_names)
        names.update(proc for proc, acronym in self.pack.process_map.items() if acronym == family)
        return {_norm(n) for n in names if n}

    def _product_bound_to(self, family: str, product: str) -> bool:
        """Explicit binding: the product is the Registry product of a process of the family."""
        wanted = _norm(product)
        return any(
            product_for_process(process, {wanted}) is not None for process in self._family_processes(family)
        )

    def _ers_error(self, family: str, code: str, operation: str | None):
        found = []  # (rank, entry, path)
        for config in self._installations.values():
            for product in config.products:
                if not self._product_bound_to(family, product.product):
                    continue  # a product of another family never answers for this family
                for conf in product.error_configurations:
                    op_match = operation is not None and conf.param_name.startswith(f"{operation}_")
                    if operation is not None and not op_match and conf.interface != operation:
                        continue
                    for entry in conf.entries:
                        if code in (entry.code, entry.adms_id):
                            found.append((0, entry, conf.path))
        if not found:
            return None
        found.sort(key=lambda item: item[0])
        return found[0][1], found[0][2]

    # -- parameters ------------------------------------------------------------------------------

    def param(self, group: int, product: str, name: str, scope: str = "registry") -> ParamValue | None:
        """``scope='registry'``: effective ERS value of ``product``/``name`` in installation ``group``
        (vault description and default attached when known); ``scope='vault'``: pack entry only."""
        if scope not in ("registry", "vault"):
            raise ValueError(f"unknown scope: {scope!r}")
        vault = self._vault_param(product, name)
        if scope == "vault":
            return None if vault is None else self._from_vault(product, vault)
        config = self._installations.get(group)
        item = config.product(product) if config else None
        effective = item.effective(name) if item else None
        if effective is None:
            return None
        return ParamValue(
            product,
            name,
            effective.value,
            "registry",
            effective.layer,
            effective.source_file,
            vault.default if vault else None,
            vault.description if vault else None,
            vault.log_relevant if vault else None,
        )

    @staticmethod
    def _from_vault(product: str, row: VaultParam) -> ParamValue:
        return ParamValue(
            product,
            row.name,
            row.default,
            "vault",
            None,
            None,
            row.default,
            row.description,
            row.log_relevant,
        )

    def _vault_param(self, product: str, name: str) -> VaultParam | None:
        rows = [r for r in self.pack.params if r.name == name]
        wanted = _norm(product)
        for row in rows:
            if _norm(row.acronym) == wanted or _norm(row.adapter) in wanted or wanted in _norm(row.adapter):
                return row
        return rows[0] if rows else None

    # -- hints -----------------------------------------------------------------------------------

    def hint(self, tpl_code: str) -> Hint | None:
        if self._hints is None:
            self._hints = self._load_hints()
        return self._hints.get(tpl_code)

    def _load_hints(self) -> dict[str, Hint]:
        try:
            if self._hints_path is not None:
                raw = self._hints_path.read_bytes()
            else:
                raw = (resources.files("dmslog.knowledge") / "curated" / "hints.toml").read_bytes()
        except (OSError, FileNotFoundError):
            return {}
        data = tomllib.loads(raw.decode("utf-8-sig"))
        hints: dict[str, Hint] = {}
        for row in data.get("hint", []):
            hints[row["code"]] = Hint(row["code"], row["text"], tuple(row.get("params", ())), row.get("note"))
        return hints
