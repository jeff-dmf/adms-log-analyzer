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


SCRIPT = Path(__file__).resolve().parents[2] / "packaging" / "release_notes.py"


def _notes(tmp_path, changelog: str, version: str = "0.1.0"):
    import subprocess
    import sys

    path = tmp_path / "CHANGELOG.md"
    path.write_text(changelog, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), version, "--changelog", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_release_notes_single_section_stops_at_next_heading(tmp_path):
    log = "# Changelog\n\n## [0.2.0]\n\n- b\n\n## [0.1.0]\n\n- a\n- a2\n\n## [0.0.1]\n\n- z\n"
    result = _notes(tmp_path, log)
    assert result.returncode == 0
    assert result.stdout == "- a\n- a2\n"


def test_release_notes_stops_at_heading_without_brackets(tmp_path):
    result = _notes(tmp_path, "## [0.1.0]\n\n- a\n\n## Unreleased\n\n- not mine\n")
    assert result.returncode == 0
    assert result.stdout == "- a\n"


def test_release_notes_missing_section_fails(tmp_path):
    assert _notes(tmp_path, "## [0.2.0]\n\n- b\n").returncode == 1


def test_release_notes_duplicate_section_fails(tmp_path):
    assert _notes(tmp_path, "## [0.1.0]\n\n- a\n\n## [0.1.0]\n\n- again\n").returncode == 1


def test_release_notes_version_is_compared_literally(tmp_path):
    assert _notes(tmp_path, "## [0.1.0]\n\n- a\n", version="0.1.").returncode == 1
    assert _notes(tmp_path, "## [0.1.01]\n\n- a\n").returncode == 1


def test_release_notes_empty_section_fails(tmp_path):
    assert _notes(tmp_path, "## [0.1.0]\n\n## [0.0.1]\n\n- z\n").returncode == 1


def test_release_workflow_uses_the_notes_script():
    assert "packaging/release_notes.py" in _text()


def test_release_workflow_publishes_a_prerelease():
    assert any("--prerelease" in line for line in _code_lines())
