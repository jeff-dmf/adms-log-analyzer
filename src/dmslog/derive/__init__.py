"""Derived events: operations, statistics, lifecycle, correlation (milestones M4-M5).

``build_derivers`` is the registry of the derivers of a family; ``derive/engine.py`` runs them over a
stream stored in SQLite (``derive_stream`` / ``derive_pending``; import it from there, not from here).
"""

from __future__ import annotations

import importlib
from typing import Any

from dmslog.derive.base import Deriver

# (module, class) of the optional derivers, in a fixed order. A module that does not exist yet is
# skipped; any other import error (a broken module) surfaces.
_OPTIONAL = (
    ("stats", "StatsDeriver"),
    ("lifecycle", "LifecycleDeriver"),
    ("exceptions", "ExceptionDeriver"),
    ("retries", "RetryDeriver"),
)


def build_derivers(
    family: str, rules: Any = None, *, truncated: bool = False, stream_start: bool = True
) -> list[Deriver]:
    """The derivers for ``family`` (``rules``: its ``TemplateSet``, may be None).

    ``truncated``: the stream was cut by the ERS, so operations without an end become ``truncated``
    instead of ``open`` (passed to the operations deriver only). ``stream_start``: False when deriving from
    a checkpoint after the start of the chain (the lifecycle deriver then does not open a level window for
    the part before the first level change).
    """
    from dmslog.derive.operations import OperationsDeriver

    derivers: list[Deriver] = [OperationsDeriver(family, rules, truncated=truncated)]
    for module_name, class_name in _OPTIONAL:
        full = f"dmslog.derive.{module_name}"
        try:
            module = importlib.import_module(full)
        except ModuleNotFoundError as exc:
            if exc.name != full:
                raise
            continue
        cls = getattr(module, class_name)
        derivers.append(
            cls(family, rules, stream_start=False)
            if not stream_start and class_name == "LifecycleDeriver"
            else cls(family, rules)
        )
    return derivers


__all__ = ["build_derivers"]
