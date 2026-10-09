"""Effective installation configuration of a case: layers, effective value, observed value, payload.

Reads ``config_param`` (every Registry layer plus the per-operation ``WebServiceConfiguration``
settings, written at ingestion) and ``config_observed`` (the ``[Configuration]`` startup lines the
adapter logged), see docs/ARQUITETURA.md 5, 7.2 and 7.2.1.

Assumptions (documented because the architecture leaves them open):

* Level order for the payload rule (6.6): ``DebugLog < Warning < Info < Verbose``. ``Debug`` is read
  as ``DebugLog`` and ``Information`` as ``Info``. ``MessageLogLevel = None`` means "never". Any other
  value (``Error``, ``Off``, a typo) gives ``payload_expected = None`` (unknown) instead of a guess.
* The level in force is the value the process logged at startup (``LogLevel`` in
  ``config_observed``) when present, otherwise the effective Registry value.
  Time windows from ``Debug level changed`` lines (``level_window``) are not considered here.
* Observed vs resolved: names are compared normalized (no ``_``, case-insensitive) plus the alias
  table below (the one in the header of ``adapters/_common.toml``); path values are compared after
  ``%OASINSTALLDATADIR%``/drive/duplicate separators are normalized, case-insensitively.
"""

from __future__ import annotations

import re
import sqlite3
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dmslog.knowledge.installation import ProductConfig

SCOPE_REGISTRY = "registry"
SCOPE_WS_PREFIX = "ws:"
WS_SETTINGS = ("MessageLogLevel", "AuditingPeriod", "NumOfRetries", "RetryTimeout")

# normalized Registry name -> normalized name logged by the adapter (see _common.toml header).
# "<Operation>_ErrorConfigurationPath" needs no entry: removing "_" already makes both names equal.
NAME_ALIASES = {
    "webserviceconfiguration": "pathtowebserviceconfiguration",
    "addressingmode": "addressingmodestring",
    "remotehousekeepingtype": "remotehousekeepingtypestring",
}

LEVEL_ORDER = {"debuglog": 3, "warning": 4, "info": 5, "verbose": 6}  # ARQUITETURA 6.6
_LEVEL_SYNONYMS = {"debug": "debuglog", "information": "info"}
_NEVER = "none"


def normalize_name(name: str) -> str:
    """Name used to compare Registry names with logged names: no ``_``, lower case."""
    return name.replace("_", "").casefold()


def logged_name(registry_name: str) -> str:
    """Normalized name under which the adapter logs a Registry name (alias applied)."""
    norm = normalize_name(registry_name)
    return NAME_ALIASES.get(norm, norm)


def product_for_process(process: str | None, products: set[str] | list[str]) -> str | None:
    """Registry product that configures a log process (``WfmAdapter`` -> ``AdapterWfm``).

    Exact (case-insensitive) match first (``CustomAdapterChangeIncidentDevice``), then
    ``<X>Adapter`` -> ``Adapter<X>``.
    """
    if not process:
        return None
    lower = {p.casefold(): p for p in products}
    hit = lower.get(process.casefold())
    if hit:
        return hit
    if process.casefold().endswith("adapter") and len(process) > 7:
        return lower.get(("Adapter" + process[:-7]).casefold())
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, tuple):
        return "\n".join(value)
    return str(value)


def config_param_rows(config: ProductConfig) -> list[tuple[Any, ...]]:
    """Rows for ``IndexWriter.replace_config_params`` from one resolved product."""
    rows: list[tuple[Any, ...]] = []
    for p in config.params:
        rows.append(
            (p.product, SCOPE_REGISTRY, p.key_path, p.name, _text(p.value), p.source_file, p.layer, p.label,
             int(p.effective), p.status)
        )  # fmt: skip
    ws_param = config.effective("WebServiceConfiguration")
    if ws_param is not None and config.web_service_path:
        for op in config.operations:
            values = {
                "MessageLogLevel": op.message_log_level,
                "AuditingPeriod": op.auditing_period,
                "NumOfRetries": op.num_of_retries,
                "RetryTimeout": op.retry_timeout,
            }
            for name, value in values.items():
                if value is None:
                    continue
                rows.append(
                    (config.product, SCOPE_WS_PREFIX + op.operation, "", name, value, config.web_service_path,
                     ws_param.layer, ws_param.label, 1, "only")
                )  # fmt: skip
    return rows


_TIMESPAN = re.compile(r"(?:(\d+)\.)?(\d+):(\d{1,2}):(\d{1,2})(?:\.(\d+))?\Z")


def _timespan(text: str) -> float | None:
    """Seconds of a .NET TimeSpan (``0:00:30``, ``00:00:30``, ``1.02:00:00``), else None."""
    m = _TIMESPAN.match(text.strip())
    if m is None:
        return None
    days, hours, minutes, seconds, frac = m.groups()
    total = int(days or 0) * 86400 + int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    return total + (float("0." + frac) if frac else 0.0)


def _same_value(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return a == b
    from dmslog.knowledge.installation import normalize_install_path

    if "\\" in a or "/" in a or "\\" in b or "/" in b:
        pa, pb = normalize_install_path(a), normalize_install_path(b)
        if pa is not None and pb is not None:
            return pa.casefold() == pb.casefold()
    ta, tb = _timespan(a), _timespan(b)
    if ta is not None and tb is not None:
        return ta == tb
    return a.strip().casefold() == b.strip().casefold()


def _level_rank(value: str | None) -> int | None:
    if value is None:
        return None
    key = value.strip().casefold()
    return LEVEL_ORDER.get(_LEVEL_SYNONYMS.get(key, key))


def payload_expected(log_level: str | None, message_log_level: str | None) -> bool | None:
    """Should the operation's payload be logged? True/False, or None when it cannot be told."""
    if message_log_level is None:
        return None
    if message_log_level.strip().casefold() == _NEVER:
        return False
    need, have = _level_rank(message_log_level), _level_rank(log_level)
    if need is None or have is None:
        return None
    return have >= need


def _default_group(conn: sqlite3.Connection, product: str) -> int | None:
    row = conn.execute(
        "SELECT collection_group FROM config_param WHERE product = ? ORDER BY collection_group LIMIT 1",
        (product,),
    ).fetchone()
    return None if row is None else int(row[0])


def config_products(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Products that have stored configuration: ``[{group, product, keys}]``."""
    rows = conn.execute(
        "SELECT collection_group, product, count(*) FROM config_param WHERE scope = ?"
        " GROUP BY collection_group, product ORDER BY collection_group, product",
        (SCOPE_REGISTRY,),
    )
    return [{"group": r[0], "product": r[1], "keys": r[2]} for r in rows]


def _observed(conn: sqlite3.Connection, group: int, product: str) -> dict[str, dict[str, Any]]:
    """Last observed value per normalized logged name, over the streams of ``product``."""
    streams = []
    for sid, process in conn.execute(
        "SELECT id, process FROM stream WHERE collection_group = ? AND sensitive = 0", (group,)
    ):
        if product_for_process(process, {product}) == product:
            streams.append(sid)
    out: dict[str, dict[str, Any]] = {}
    if not streams:
        return out
    marks = ",".join("?" * len(streams))
    rows = conn.execute(
        f"SELECT o.stream_id, o.name, o.value, o.is_default, o.entry_id, e.ts"
        f" FROM config_observed o LEFT JOIN entry e ON e.id = o.entry_id"
        f" WHERE o.stream_id IN ({marks}) ORDER BY e.ts, o.entry_id",
        streams,
    )
    for stream_id, name, value, is_default, entry_id, _ts in rows:
        # Rows come in (ts, entry_id) order: the most recent observation wins. Measured on the
        # sample ERS: a logged name is never reported as both Registry-typed and [Default], so no
        # startup boundary is needed and no type preference applies.
        out[normalize_name(name)] = {
            "name": name, "value": value, "is_default": bool(is_default), "entry_id": entry_id,
            "stream_id": stream_id,
        }  # fmt: skip
    return out


def config_for(conn: sqlite3.Connection, product: str, group: int | None = None) -> dict[str, Any] | None:
    """Configuration of one product: layers, effective value, observed value, divergence, payload.

    Returns ``None`` when the case has no stored configuration for ``product`` (in ``group``, or in
    the first group that has it). Shape::

        {"product", "group", "keys": [{"scope", "key_path", "name", "layers": [...], "effective",
         "effective_layer", "status", "observed", "diverges"}], "log_level": {"value", "source"},
         "operations": [{"operation", "message_log_level", "payload_expected", "settings"}]}

    ``diverges`` is true only when both an effective and an observed value exist and differ.
    """
    if group is None:
        group = _default_group(conn, product)
        if group is None:
            return None
    rows = conn.execute(
        "SELECT scope, key_path, name, value, source_file, layer, layer_label, effective, status"
        " FROM config_param WHERE collection_group = ? AND product = ?"
        " ORDER BY scope, key_path, name, layer, source_file",
        (group, product),
    ).fetchall()
    if not rows:
        return None
    observed = _observed(conn, group, product)
    keys: dict[tuple[str, str, str], dict[str, Any]] = {}
    ops: dict[str, dict[str, Any]] = {}
    for scope, key_path, name, value, source_file, layer, label, effective, status in rows:
        if scope.startswith(SCOPE_WS_PREFIX):
            op = ops.setdefault(scope[len(SCOPE_WS_PREFIX) :], {"settings": {}})
            op["settings"][name] = value
            op["source_file"], op["layer"], op["layer_label"] = source_file, layer, label
            continue
        entry = keys.setdefault(
            (scope, key_path, name),
            {"scope": scope, "key_path": key_path, "name": name, "layers": [], "effective": None,
             "effective_layer": None, "status": status, "observed": None, "diverges": False},
        )  # fmt: skip
        entry["layers"].append(
            {"layer": layer, "layer_label": label or "", "source_file": source_file, "value": value,
             "status": status, "effective": bool(effective)}
        )  # fmt: skip
        if effective:
            entry["effective"], entry["effective_layer"], entry["status"] = value, layer, status
        elif entry["effective"] is None:
            entry["status"] = status  # overridden/ambiguous only: keeps the last status seen
    for entry in keys.values():
        if entry["scope"] != SCOPE_REGISTRY:
            continue
        seen = observed.get(logged_name(entry["name"]))
        if seen is not None:
            entry["observed"] = {k: seen[k] for k in ("name", "value", "is_default", "entry_id")}
            entry["diverges"] = entry["effective"] is not None and not _same_value(
                entry["effective"], seen["value"]
            )
    level_entry = keys.get((SCOPE_REGISTRY, "", "LogLevel"))
    level_seen = observed.get(normalize_name("LogLevel"))
    log_level: dict[str, Any] = {"value": None, "source": None}
    if level_seen is not None:
        log_level = {"value": level_seen["value"], "source": "observed"}
    elif level_entry is not None and level_entry["effective"] is not None:
        log_level = {"value": level_entry["effective"], "source": "registry"}
    operations = []
    for op_name in sorted(ops):
        info = ops[op_name]
        message_level = info["settings"].get("MessageLogLevel")
        operations.append(
            {"operation": op_name, "message_log_level": message_level,
             "payload_expected": payload_expected(log_level["value"], message_level),
             "settings": info["settings"], "source_file": info["source_file"], "layer": info["layer"],
             "layer_label": info["layer_label"] or ""}
        )  # fmt: skip
    return {
        "product": product, "group": group, "keys": list(keys.values()), "log_level": log_level,
        "operations": operations,
    }  # fmt: skip
