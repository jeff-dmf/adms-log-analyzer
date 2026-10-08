"""Source discovery and ingestion pipeline (milestone M1).

The `discover` function lives in `dmslog.ingest.discover` (not re-exported here, so the
submodule name stays importable).
"""

from dmslog.ingest.discover import SourceInfo
from dmslog.ingest.ers import CollectionConflictError, ErsIdentity, parse_ers_name, plan_collection

__all__ = [
    "CollectionConflictError",
    "ErsIdentity",
    "SourceInfo",
    "parse_ers_name",
    "plan_collection",
]
