"""Log envelope, record and template parsing (milestones M1-M2)."""

from .envelope import ENVELOPES, LEVELS, Envelope, Header, detect_envelope, get_envelope, register_envelope
from .records import Record, RecordGrouper, iter_records, parse_bytes
from .signature import signature
from .templates import (
    Catalog,
    PayloadSpec,
    TemplateDef,
    TemplateError,
    TemplateMatch,
    TemplateSet,
    load_catalog,
    load_dir,
    load_texts,
)

__all__ = [
    "ENVELOPES",
    "LEVELS",
    "Catalog",
    "Envelope",
    "Header",
    "PayloadSpec",
    "Record",
    "RecordGrouper",
    "TemplateDef",
    "TemplateError",
    "TemplateMatch",
    "TemplateSet",
    "detect_envelope",
    "get_envelope",
    "iter_records",
    "load_catalog",
    "load_dir",
    "load_texts",
    "parse_bytes",
    "register_envelope",
    "signature",
]
