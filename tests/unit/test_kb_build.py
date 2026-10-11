import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from dmslog.knowledge.pack_schema import PACK_FILES, SCHEMA_VERSION, validate_pack

ROOT = Path(__file__).resolve().parents[2]
MINI_VAULT = ROOT / "tests" / "fixtures" / "mini_vault"

_spec = importlib.util.spec_from_file_location("kb_build", ROOT / "tools" / "kb_build.py")
kb_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kb_build)


def build(tmp_path, name="pack"):
    out = tmp_path / name
    assert kb_build.main(["--vault", str(MINI_VAULT), "--out", str(out), "--vault-commit", "none"]) == 0
    return out


def load(out, name):
    return json.loads((out / name).read_text(encoding="utf-8"))


def test_split_row_handles_escaped_pipes_wikilinks_and_code():
    cells = kb_build.split_row(r"| [[A Index\|Title]] | x \| y | [[B|Cfg]] | `a|b` | |")
    assert [kb_build.clean_cell(c) for c in cells] == ["Title", "x | y", "Cfg", "a|b", ""]


def test_parse_tables_skips_alignment_row_and_tracks_heading():
    text = "## Section One\n\n| A | B |\n|:---|---:|\n| 1 | 2 |\n| 3 | 4 |\n\ntext\n"
    [(heading, headers, rows)] = list(kb_build.parse_tables(text))
    assert heading == "## Section One"
    assert headers == ["A", "B"]
    assert rows == [["1", "2"], ["3", "4"]]


def test_parse_tables_rejects_ragged_rows():
    with pytest.raises(kb_build.BuildError):
        list(kb_build.parse_tables("| A | B |\n|---|---|\n| 1 | 2 | 3 |\n"))


def test_parse_frontmatter_scalars_and_lists():
    text = '---\ndoc: AAAA\nversao: "1.0 (X)"\naliases:\n  - "One"\n  - "Two"\n---\nbody'
    meta = kb_build.parse_frontmatter(text)
    assert meta == {"doc": "AAAA", "versao": "1.0 (X)", "aliases": ["One", "Two"]}


def test_build_contents(tmp_path):
    out = build(tmp_path)
    adapters = load(out, "adapters.json")
    by_acronym = {a["acronym"]: a for a in adapters["adapters"]}
    assert sorted(by_acronym) == ["AAAA", "ABBB"]
    alpha = by_acronym["AAAA"]
    assert alpha["type"] == "soap"
    assert alpha["operations"] == ["CreatedExampleItems", "SendExampleItems"]
    assert alpha["document_version"] == "1.0 (January 2020)"
    assert alpha["aliases"] == ["Example Adapter", "Example Alpha Interface"]
    assert alpha["process_names"] == ["ExampleAdapter"]
    assert adapters["process_map"] == {"ExampleAdapter": "AAAA"}
    beta = by_acronym["ABBB"]
    assert beta["type"] == "files"
    assert beta["operations"] == []
    assert beta["config_note"] is None

    errors = load(out, "error_codes.json")["error_codes"]
    assert len(errors) == 3
    beta_error = next(e for e in errors if e["acronym"] == "ABBB")
    assert beta_error["id"] == "InvalidMessage"
    assert beta_error["cim_details"] == "Value A | B is not valid."
    alpha_error = next(e for e in errors if e["code"] == "1.2")
    assert alpha_error["dms_reason"] == "IdMissing"
    assert alpha_error["id"] is None

    operations = load(out, "operations.json")["operations"]
    assert [(o["service"], o["direction"]) for o in operations] == [
        ("ReceiveExampleItems", "inbound"),
        ("SendExampleItems", "outbound"),
    ]
    assert operations[0]["wsdl"] == "ReceiveExampleItemsService"

    parameters = {(p["acronym"], p["name"]): p for p in load(out, "parameters.json")["parameters"]}
    assert parameters[("AAAA", "ErrorIdsDelimiter")]["default"] == "|"
    assert parameters[("AAAA", "LogLevel")]["log_relevant"] is True
    assert parameters[("AAAA", "SendTimeout")]["log_relevant"] is True
    assert parameters[("AAAA", "ExampleOnly")]["log_relevant"] is False


def test_output_is_deterministic_and_well_formed(tmp_path):
    first = build(tmp_path, "one")
    second = build(tmp_path, "two")
    for name in PACK_FILES:
        raw = (first / name).read_bytes()
        assert raw == (second / name).read_bytes()
        assert b"\r" not in raw
        assert raw.endswith(b"}\n")
        json.loads(raw.decode("utf-8"))


def test_manifest_hashes_and_metadata(tmp_path):
    out = build(tmp_path)
    manifest = load(out, "manifest.json")
    assert manifest["schema_version"] == SCHEMA_VERSION
    assert manifest["vault_commit"] is None
    assert "private" in manifest["notice"]
    assert set(manifest) == {"schema_version", "generator_version", "vault_commit", "notice", "files"}
    assert manifest["files"]
    for rel, digest in manifest["files"].items():
        assert rel.endswith(".md")
        assert digest == hashlib.sha256((MINI_VAULT / rel).read_bytes()).hexdigest()
    assert "12 Interfaces online/Interfaces online.md" in manifest["files"]
    assert any(rel.endswith("AAAA Configuration.md") for rel in manifest["files"])


def test_check_mode(tmp_path, capsys):
    out = build(tmp_path)
    args = ["--vault", str(MINI_VAULT), "--out", str(out), "--vault-commit", "none", "--check"]
    assert kb_build.main(args) == 0
    (out / "operations.json").write_text("{}\n", encoding="utf-8")
    assert kb_build.main(args) == 1
    assert "operations.json" in capsys.readouterr().err
    (out / "operations.json").unlink()
    assert kb_build.main(args) == 1
    # --check never writes
    assert not (out / "operations.json").exists()


def test_generated_pack_passes_schema_validation(tmp_path):
    assert validate_pack(build(tmp_path)) == []


def test_schema_validation_reports_problems(tmp_path):
    out = build(tmp_path)
    parameters = load(out, "parameters.json")
    parameters["parameters"][0]["log_relevant"] = "yes"
    del parameters["parameters"][1]["name"]
    (out / "parameters.json").write_text(json.dumps(parameters), encoding="utf-8")
    manifest = load(out, "manifest.json")
    manifest["files"]["x.md"] = "not-a-digest"
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (out / "adapters.json").unlink()
    errors = validate_pack(out)
    assert any("log_relevant" in e for e in errors)
    assert any("missing field 'name'" in e for e in errors)
    assert any("sha256" in e for e in errors)
    assert any(e.startswith("adapters.json") for e in errors)


def test_validate_pack_on_missing_directory(tmp_path):
    assert validate_pack(tmp_path / "nope")


def test_build_fails_on_unknown_table_shape(tmp_path):
    vault = tmp_path / "vault"
    shutil.copytree(MINI_VAULT, vault)
    path = next(p for p in vault.rglob("*.md") if p.name.startswith("Cat") and "opera" in p.name)
    path.write_text(path.read_text(encoding="utf-8").replace("| Endpoint |", "| Other |"), encoding="utf-8")
    assert kb_build.main(["--vault", str(vault), "--out", str(tmp_path / "o"), "--vault-commit", "none"]) == 2


def test_vault_is_not_modified(tmp_path):
    before = {p: p.read_bytes() for p in MINI_VAULT.rglob("*") if p.is_file()}
    build(tmp_path)
    assert before == {p: p.read_bytes() for p in MINI_VAULT.rglob("*") if p.is_file()}


def test_default_out_is_the_data_dir_pack_where_dmslog_looks(tmp_path):
    data_dir = tmp_path / "data"
    args = ["--vault", str(MINI_VAULT), "--data-dir", str(data_dir), "--vault-commit", "none"]
    assert kb_build.main(args) == 0
    from dmslog.knowledge.pack import SOURCE_DATA_DIR, STATE_INSTALLED, locate_pack, pack_candidates

    status, pack = locate_pack(pack_candidates(data_dir=data_dir, env={}))
    assert (status.state, status.source) == (STATE_INSTALLED, SOURCE_DATA_DIR)
    assert status.path == data_dir / "knowledge" / "pack" and pack.adapters


@pytest.mark.parametrize("relative", ["src/dmslog/knowledge/pack", "pack", "tests/fixtures/x"])
def test_refuses_to_write_inside_the_repository(relative, capsys):
    out = ROOT / relative
    existed = out.exists()
    assert kb_build.main(["--vault", str(MINI_VAULT), "--out", str(out), "--vault-commit", "none"]) == 2
    assert "inside the repository" in capsys.readouterr().err
    assert out.exists() == existed
