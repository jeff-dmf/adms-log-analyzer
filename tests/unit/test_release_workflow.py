"""Static checks of the release workflow: it must never ship the knowledge pack (ARQUITETURA D10)."""
from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _code_lines() -> list[str]:
    return [line for line in _text().splitlines() if not line.lstrip().startswith("#")]


def test_release_workflow_never_passes_the_pack_flag():
    assert not any("--pack" in line for line in _code_lines())


def test_release_workflow_runs_the_pack_guard_on_the_artifacts():
    text = _text()
    assert "packaging/check_no_pack.py --artifact" in re.sub(r"\s+", " ", text)


def test_release_workflow_actions_are_pinned_by_sha():
    uses = [m for m in re.findall(r"uses:\s*(\S+)", _text())]
    assert uses
    assert all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", u) for u in uses), uses


def test_release_workflow_write_permission_only_in_publish_job():
    text = _text()
    assert text.count("contents: write") == 1
    assert text.index("contents: write") > text.index("\n  publish:")
    assert "if: github.event_name == 'push' && github.ref_type == 'tag'" in text
