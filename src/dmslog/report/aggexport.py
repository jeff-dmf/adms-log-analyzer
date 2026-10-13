"""CSV of an aggregation result (store/aggregate.py; ARQUITETURA 10.6).

Same CSV rules as the view export (``report/viewexport.py``: RFC 4180, CRLF, formula guard on text,
optional BOM). Group values are written as the viewer shows them: the readable title of a template,
the file name of a source, the ``C<n>`` tag of a collection, severity and level names, bucket starts
as log time; measures and lookups as they are.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .. import messages
from ..parse.envelope import LEVEL_NAMES
from ..store.aggregate import Aggregate, Dim, Measure
from ..store.filters import SEV_NAMES
from .viewexport import BOM, csv_row, format_ts

_SEV_TEXT = {v: k for k, v in SEV_NAMES.items()}


def _dim_header(dim: Dim) -> str:
    if dim.kind == "bucket":
        return messages.AGGREGATE_CSV_DIMS["bucket"].format(width=dim.arg)
    if dim.kind in ("field", "key"):
        return dim.id
    return messages.AGGREGATE_CSV_DIMS.get(dim.arg, dim.id)


def _measure_header(m: Measure) -> str:
    if m.fn == "count":
        return messages.AGGREGATE_CSV_MEASURES["count"]
    if m.dim is not None:
        arg = _dim_header(m.dim)
    else:
        arg = messages.AGGREGATE_CSV_VALUES.get(m.value or "", m.value or "")
    return messages.AGGREGATE_CSV_MEASURES[m.fn].format(arg=arg)


def csv_header(result: Aggregate) -> list[str]:
    req = result.request
    header = [_dim_header(d) for d in req.dims] + [_measure_header(m) for m in req.measures]
    return header + [messages.AGGREGATE_CSV_LOOKUPS[name] for name in result.lookup.get("columns", [])]


def _dim_cell(dim: Dim, value: Any, label: str | None) -> Any:
    if value is None:
        return None
    if dim.kind == "bucket":
        return format_ts(value)
    if label is not None:
        return label
    if dim.kind == "column" and dim.arg == "sev":
        return _SEV_TEXT.get(value, value)
    if dim.kind == "column" and dim.arg == "level":
        return LEVEL_NAMES.get(value, value).lower() if isinstance(value, int) else value
    return value


def stream_csv(result: Aggregate, bom: bool = False) -> Iterator[str]:
    """The header line and one line per returned group."""
    if bom:
        yield BOM
    yield csv_row(csv_header(result))
    dims = result.request.dims
    n = len(dims)
    for row in result.rows:
        values = row["values"]
        cells = [_dim_cell(d, values[i], row["labels"][i]) for i, d in enumerate(dims)]
        yield csv_row(cells + list(values[n:]))
