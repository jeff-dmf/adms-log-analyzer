"""Every template ID quoted in docs/formato/*.md must exist in the catalog (task 2.4)."""

import re
from pathlib import Path

import pytest

from dmslog.parse.templates import load_catalog

DOCS = Path(__file__).resolve().parents[2] / "docs" / "formato"

# Shape of a template ID (WFM-080, GEN-STA-02, SMN-S016, ONT-016) plus other dash-separated tokens
# that the allow-list below rejects explicitly.
ID_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,5}(?:-[A-Z0-9]+)+\b")

# Documented tokens that are intentionally not catalog IDs. One reason per entry; anything else
# that does not resolve fails the test (never skipped silently).
ALLOWED_MISSING = {
    "AAAA-MM-DD": "date placeholder in a log-line pattern, not an ID",
    "UTF-8": "encoding name",
    "UTF-16": "encoding name",
    "UPDATE-FIELD-COMPLETE-INC": "literal prefix of a msgId value (UPDATE-FIELD-COMPLETE-INC<n>-<seq>)",
    "SMN-S": "prefix of the sensitive IDs (SMN-S...), written as 'SMN-S…/ONT-S020'",
    "GEN-CON": "group prefix of GEN-CON-01..03 in the generic-template table",
    "GEN-STA": "group prefix of GEN-STA-01..03 in the generic-template table",
    "GEN-STARTUP": "document row for the startup block; the catalog splits it into GEN-CFG-01/02",
    "AVL-013": "start of the range AVL-013..015 (map trio): covered by the generic GEN-MAP-* templates",
    "SMN-020": "start of the range SMN-020..022 (map trio): covered by the generic GEN-MAP-* templates",
}


def _doc_ids() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in sorted(DOCS.glob("*.md")):
        for tid in ID_RE.findall(path.read_text(encoding="utf-8-sig")):
            out.setdefault(tid, set()).add(path.name)
    return out


def test_docs_folder_has_ids():
    assert len(_doc_ids()) > 100


def test_documented_ids_exist():
    catalog = load_catalog()
    codes = {t.code for t in catalog.templates()}
    missing: list[str] = []
    for tid, files in sorted(_doc_ids().items()):
        if tid in ALLOWED_MISSING or catalog.resolve(tid) is not None:
            continue
        # a documented row split into letter-suffixed templates (WFM-062 -> WFM-062A, WFM-062B)
        if any(re.fullmatch(re.escape(tid) + r"[A-Z]", c) for c in codes):
            continue
        missing.append(f"{tid} ({', '.join(sorted(files))})")
    assert not missing, "documented IDs without a catalog code or alias: " + "; ".join(missing)


@pytest.mark.parametrize("tid", sorted(ALLOWED_MISSING))
def test_allowed_missing_is_still_needed(tid):
    """An allow-list entry must be documented and still unresolved; otherwise drop it."""
    assert tid in _doc_ids(), f"{tid} is no longer mentioned in docs/formato"
    resolved = load_catalog().resolve(tid)
    assert resolved is None, f"{tid} now exists in the catalog; remove it from ALLOWED_MISSING"
