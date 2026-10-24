import hashlib
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from dmslog.knowledge.pack_schema import PACK_FILES, SCHEMA_VERSION, validate_pack

ROOT = Path(__file__).resolve().parents[2]
MINI_VAULT = ROOT / "tests" / "fixtures" / "mini_vault"
RT_CURATED = ROOT / "tests" / "fixtures" / "knowledge" / "rt_curated_fixture.toml"
RT_FILE = "rt_services.json"

_spec = importlib.util.spec_from_file_location("kb_build", ROOT / "tools" / "kb_build.py")
kb_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kb_build)


def build(tmp_path, name="pack", *extra, vault=MINI_VAULT, curated=RT_CURATED):
    out = tmp_path / name
    args = ["--vault", str(vault), "--out", str(out), "--vault-commit", "none", *extra]
    if "--no-rt" not in extra:
        args += ["--rt-curated", str(curated)]
    assert kb_build.main(args) == 0
    return out


def rt_rows(out):
    return {row["process"]: row for row in load(out, RT_FILE)["rt_services"]}


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
    for name in (*PACK_FILES, RT_FILE):
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
    args += ["--rt-curated", str(RT_CURATED)]
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
    args = ["--vault", str(vault), "--out", str(tmp_path / "o"), "--vault-commit", "none", "--no-rt"]
    assert kb_build.main(args) == 2


def test_vault_is_not_modified(tmp_path):
    before = {p: p.read_bytes() for p in MINI_VAULT.rglob("*") if p.is_file()}
    build(tmp_path)
    assert before == {p: p.read_bytes() for p in MINI_VAULT.rglob("*") if p.is_file()}


def test_default_out_is_the_data_dir_pack_where_dmslog_looks(tmp_path):
    data_dir = tmp_path / "data"
    args = ["--vault", str(MINI_VAULT), "--data-dir", str(data_dir), "--vault-commit", "none", "--no-rt"]
    assert kb_build.main(args) == 0
    from dmslog.knowledge.pack import SOURCE_DATA_DIR, STATE_INSTALLED, locate_pack, pack_candidates

    status, pack = locate_pack(pack_candidates(data_dir=data_dir, env={}))
    assert (status.state, status.source) == (STATE_INSTALLED, SOURCE_DATA_DIR)
    assert status.path == data_dir / "knowledge" / "pack" and pack.adapters


@pytest.mark.parametrize("relative", ["src/dmslog/knowledge/pack", "pack", "tests/fixtures/x"])
def test_refuses_to_write_inside_the_repository(relative, capsys):
    out = ROOT / relative
    existed = out.exists()
    args = ["--vault", str(MINI_VAULT), "--out", str(out), "--vault-commit", "none", "--no-rt"]
    assert kb_build.main(args) == 2
    assert "inside the repository" in capsys.readouterr().err
    assert out.exists() == existed


# ----- RT services ------------------------------------------------------------------------------------

OWN_NOTE = "01 Example Architecture/SARC 5.4.3 Example Services.md"


def test_rt_rows_are_sorted_and_cover_every_curated_service(tmp_path):
    rows = load(build(tmp_path), RT_FILE)["rt_services"]
    processes = [row["process"] for row in rows]
    assert processes == sorted(processes)
    assert set(processes) == {
        "DMS_ExampleScheduler",
        "DMS_ExampleLauncher",
        "DMS_ExampleHost",
        "DMS_ExampleLong",
        "DMS_ExampleEmpty",
        "ExampleAbsent",
        "ExampleNothing",
    }
    for row in rows:
        assert set(row) == {
            "process",
            "family",
            "title",
            "summary",
            "note",
            "section",
            "install_dir",
            "monitored",
            "counters",
            "usage_events",
            "references",
        }


def test_rt_service_with_everything(tmp_path):
    row = rt_rows(build(tmp_path))["DMS_ExampleScheduler"]
    assert row["family"] == "DMSJOB"
    assert row["title"] == "Example Scheduler"
    assert row["summary"] == (
        "Example Scheduler queues the example jobs and hands them to a launcher, e.g. when a slot is free."
    )
    assert (row["note"], row["section"]) == (OWN_NOTE, "5.4.3.4.1")
    assert row["install_dir"] == "Suite\\bin"
    assert row["monitored"] == ["cpu", "hdd"]
    assert row["counters"] == ["Active Slots", "Queue Depth"]
    assert row["usage_events"] == 2


def test_rt_references_follow_the_closed_list_and_exclude_the_own_note(tmp_path):
    references = rt_rows(build(tmp_path))["DMS_ExampleScheduler"]["references"]
    assert [r["doc"] for r in references] == ["SARC", "PRFI", "SCOM", "SCOM", "AVEL", "SMAN", "BCON"]
    assert all(r["note"] != OWN_NOTE and set(r) == {"doc", "note", "section", "mentions"} for r in references)
    sarc, prfi, scom_a, scom_b, avel, sman, bcon = references
    # whole-word matches only: the look-alike words of the SARC 5.2 fixture note do not count
    assert (sarc["section"], sarc["mentions"]) == ("5.2.1", 1)
    # process, process without prefix and the extra name all count
    assert (prfi["section"], prfi["mentions"]) == ("4.1", 3)
    assert (scom_a["section"], scom_b["section"]) == ("5.11", "5.6")
    assert avel["section"] == "2.1.1"
    assert sman["section"] is None  # no numbered heading above the first mention
    assert (bcon["section"], bcon["mentions"]) == ("1", 2)


def test_rt_documents_outside_the_closed_list_and_archive_are_not_read(tmp_path):
    vault = tmp_path / "vault"
    shutil.copytree(MINI_VAULT, vault)
    archived = vault / "archive" / "SARC 9 Archived.md"
    archived.parent.mkdir()
    archived.write_text("# 9. Archived\n\nExampleLauncher again.\n", encoding="utf-8")
    out = build(tmp_path, "pack", vault=vault)
    notes = {r["note"] for row in rt_rows(out).values() for r in row["references"]}
    assert not any("ZZZZ" in note or "archive" in note for note in notes)
    assert not any("ZZZZ" in rel or "archive" in rel for rel in load(out, "manifest.json")["files"])


def test_rt_pool_instances_are_found_by_the_pool_name(tmp_path):
    host = rt_rows(build(tmp_path))["DMS_ExampleHost"]
    assert host["install_dir"] == "Example\\Servers"
    assert [(r["doc"], r["mentions"]) for r in host["references"]] == [("PRFI", 3), ("AVEL", 1)]


def test_rt_summary_rules(tmp_path):
    rows = rt_rows(build(tmp_path))
    assert rows["DMS_ExampleLauncher"]["summary"] == "Example Launcher starts one example host per job!"
    assert rows["DMS_ExampleHost"]["summary"] == "Example Host runs the example functions and reports back?"
    long_summary = rows["DMS_ExampleLong"]["summary"]
    assert len(long_summary) <= 240 and long_summary.endswith(chr(0x2026))
    assert rows["DMS_ExampleEmpty"]["title"] == "Example Empty Section"
    assert rows["DMS_ExampleEmpty"]["summary"] is None


def test_rt_summary_none_leaves_out_every_sentence(tmp_path):
    rows = rt_rows(build(tmp_path, "pack", "--rt-summary", "none"))
    assert all(row["summary"] is None for row in rows.values())
    assert rows["DMS_ExampleScheduler"]["title"] == "Example Scheduler"


def test_rt_absent_service_has_only_references(tmp_path):
    rows = rt_rows(build(tmp_path))
    absent = rows["ExampleAbsent"]
    assert (absent["title"], absent["summary"], absent["note"], absent["section"]) == (None, None, None, None)
    assert [r["doc"] for r in absent["references"]] == ["SARC", "BCON"]
    nothing = rows["ExampleNothing"]  # no "vault" key at all
    assert nothing["note"] is None and nothing["references"] == []
    assert nothing["monitored"] == [] and nothing["counters"] == [] and nothing["usage_events"] == 0


def test_rt_missing_section_or_note_is_a_build_error(tmp_path, capsys):
    for old, new, message in (
        ('section = "5.4.3.4.1"', 'section = "9.9"', "section 9.9 not found"),
        ("SARC 5.4.3 Example Services.md", "SARC 0 Missing.md", "not found in the vault"),
    ):
        curated = tmp_path / "curated.toml"
        curated.write_text(RT_CURATED.read_text(encoding="utf-8").replace(old, new, 1), encoding="utf-8")
        args = ["--vault", str(MINI_VAULT), "--out", str(tmp_path / "o"), "--vault-commit", "none"]
        assert kb_build.main([*args, "--rt-curated", str(curated)]) == 2
        assert message in capsys.readouterr().err
        assert not (tmp_path / "o").exists()


def test_rt_invalid_curated_file_is_a_build_error(tmp_path, capsys):
    curated = tmp_path / "curated.toml"
    args = ["--vault", str(MINI_VAULT), "--out", str(tmp_path / "o"), "--vault-commit", "none"]
    for text in (
        '[[service]]\nprocess = "A"\nfamily = "NMC"\n[[service]]\nprocess = "A"\nfamily = "NMC"\n',
        '[[service]]\nprocess = "A"\nfamily = "NMC"\nvault = "x"\n',
        '[[service]]\nprocess = "A"\nfamily = "NMC"\nvault = { doc = "SARC" }\n',
        "not toml [",
    ):
        curated.write_text(text, encoding="utf-8")
        assert kb_build.main([*args, "--rt-curated", str(curated)]) == 2
    assert capsys.readouterr().err.count("kb_build: error") == 4
    assert kb_build.main([*args, "--rt-curated", str(tmp_path / "nope.toml")]) == 2


def test_rt_missing_support_notes_leave_fields_empty(tmp_path):
    vault = tmp_path / "vault"
    shutil.copytree(MINI_VAULT, vault)
    for path in (vault / "02 Example Operations").glob("*.md"):
        if path.name.split(" ")[0] in ("AVEL", "SCOM", "SMAN"):
            path.unlink()
    row = rt_rows(build(tmp_path, "pack", vault=vault))["DMS_ExampleScheduler"]
    assert (row["install_dir"], row["monitored"], row["counters"], row["usage_events"]) == (None, [], [], 0)
    assert row["title"] == "Example Scheduler"


def test_rt_ambiguous_note_name_is_a_build_error(tmp_path, capsys):
    vault = tmp_path / "vault"
    shutil.copytree(MINI_VAULT, vault)
    (vault / "other").mkdir()
    shutil.copy(vault / OWN_NOTE, vault / "other" / "SARC 5.4.3 Example Services.md")
    args = ["--vault", str(vault), "--out", str(tmp_path / "o"), "--vault-commit", "none"]
    assert kb_build.main([*args, "--rt-curated", str(RT_CURATED)]) == 2
    assert "ambiguous note name" in capsys.readouterr().err


def test_rt_manifest_lists_every_note_read_and_the_pack_validates(tmp_path):
    out = build(tmp_path)
    files = load(out, "manifest.json")["files"]
    for rel, digest in files.items():
        assert digest == hashlib.sha256((MINI_VAULT / rel).read_bytes()).hexdigest()
    assert OWN_NOTE in files
    assert "02 Example Operations/SMAN Usage Events.md" in files
    assert validate_pack(out) == []


def test_rt_coverage_line_has_only_numbers(tmp_path, capsys):
    build(tmp_path)
    printed = capsys.readouterr().out
    assert "rt: 5/7 services with a vault section, 4/7 with references" in printed
    assert "Example" not in printed.split("rt:", 1)[1]


def test_no_rt_skips_the_file_and_check_tolerates_it(tmp_path):
    plain = build(tmp_path, "plain", "--no-rt")
    assert not (plain / RT_FILE).exists()
    out = build(tmp_path)
    args = ["--vault", str(MINI_VAULT), "--out", str(out), "--vault-commit", "none", "--check"]
    assert kb_build.main([*args, "--rt-curated", str(RT_CURATED)]) == 0
    # the optional file next to a pack built without it is not an "unexpected file"
    assert kb_build.main([*args, "--no-rt"]) == 1  # the manifest differs, but only that
    assert kb_build.diff_pack(kb_build.build_pack(MINI_VAULT, "none", None), out) == [
        "manifest.json: differs from the freshly built pack"
    ]


def test_check_reports_a_divergent_or_missing_rt_file(tmp_path, capsys):
    out = build(tmp_path)
    args = ["--vault", str(MINI_VAULT), "--out", str(out), "--vault-commit", "none", "--check"]
    args += ["--rt-curated", str(RT_CURATED)]
    (out / RT_FILE).write_text('{"rt_services": []}\n', encoding="utf-8")
    assert kb_build.main(args) == 1
    assert RT_FILE in capsys.readouterr().err
    (out / RT_FILE).unlink()
    assert kb_build.main(args) == 1
    assert not (out / RT_FILE).exists()


def test_diff_pack_flags_unknown_json_but_not_the_optional_file(tmp_path):
    out = build(tmp_path)
    files = kb_build.build_pack(MINI_VAULT, "none", RT_CURATED)
    assert kb_build.diff_pack(files, out) == []
    (out / "stray.json").write_text("{}", encoding="utf-8")
    assert any("stray.json" in problem for problem in kb_build.diff_pack(files, out))


# ----- Review fixes: confinement, bounded work, first sentence, valid output --------------------------


def _symlink(link, target, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links cannot be created here")


def test_symlinked_note_outside_the_vault_is_never_read(tmp_path, capsys):
    vault = tmp_path / "vault"
    shutil.copytree(MINI_VAULT, vault)
    outside = tmp_path / "outside.md"
    outside.write_text("# 9. Outside\n\nExampleScheduler secret.\n", encoding="utf-8")
    _symlink(vault / "02 Example Operations" / "PRFI 9 Link.md", outside)
    out = build(tmp_path, "pack", vault=vault)
    assert not any("Link" in rel for rel in load(out, "manifest.json")["files"])
    assert not any("Link" in r["note"] for row in rt_rows(out).values() for r in row["references"])
    # a curated note that is itself a link is not found either
    curated = tmp_path / "curated.toml"
    curated.write_text(
        '[[service]]\nprocess = "DMS_X"\nfamily = "NMC"\nvault = { doc = "PRFI", note = "PRFI 9 Link.md", '
        'section = "9" }\n',
        encoding="utf-8",
    )
    args = ["--vault", str(vault), "--out", str(tmp_path / "o"), "--vault-commit", "none"]
    assert kb_build.main([*args, "--rt-curated", str(curated)]) == 2
    assert "not found in the vault" in capsys.readouterr().err


def test_symlinked_directory_is_not_followed_and_read_refuses_links(tmp_path):
    vault = tmp_path / "vault"
    shutil.copytree(MINI_VAULT, vault)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "SARC 8 Outside.md").write_text("# 8. Outside\n\nExampleScheduler.\n", encoding="utf-8")
    _symlink(vault / "linked", outside, directory=True)
    out = build(tmp_path, "pack", vault=vault)
    assert not any("Outside" in r["note"] for row in rt_rows(out).values() for r in row["references"])
    handle = kb_build.Vault(vault)
    with pytest.raises(kb_build.BuildError):
        handle.read(vault / "linked" / "SARC 8 Outside.md")
    with pytest.raises(kb_build.BuildError):
        handle.read(outside / "SARC 8 Outside.md")


def test_oversized_note_is_refused_before_decoding(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(kb_build, "MAX_NOTE_BYTES", 200)
    args = ["--vault", str(MINI_VAULT), "--out", str(tmp_path / "o"), "--vault-commit", "none", "--no-rt"]
    assert kb_build.main(args) == 2
    assert "larger than 200 bytes" in capsys.readouterr().err


@pytest.mark.parametrize("piece", ["[", "(", "]", "|", "**", "`", "[[", "](", "[a]("])
def test_malformed_markdown_is_cleaned_in_linear_time(piece):
    import time

    text = piece * (400000 // len(piece))  # a quadratic pass would take minutes here, linear well under 1 s
    started = time.perf_counter()
    kb_build.clean_cell(text)
    kb_build._plain(text)
    kb_build.first_sentence([text])
    assert time.perf_counter() - started < 10.0  # loose: the CI runners are slower than a workstation


def test_clean_cell_still_resolves_links():
    assert kb_build.clean_cell("[[A Index|Title]] and [text](http://x/y) [a] (b) [[n]]") == (
        "Title and text [a] (b) n"
    )
    assert kb_build.clean_cell("[x] [y](z") == "[x] [y](z"


def test_first_sentence_stops_at_the_first_sentence_end():
    first = kb_build.first_sentence
    assert first(["Example ends. another sentence is copied."]) == "Example ends."
    assert first(["Use it, e.g. now, or i.e. later. Then stop."]) == "Use it, e.g. now, or i.e. later."
    assert first(["Version 3.5 is used. Next."]) == "Version 3.5 is used."
    assert first(["No end here"]) == "No end here"
    assert first(["Is it? Yes."]) == "Is it?"


def test_first_sentence_skips_fenced_code_quotes_and_tables():
    lines = ["```", "code line. more code.", "```", "> quoted. text.", "| a | b |", "", "Real prose. Rest."]
    assert kb_build.first_sentence(lines) == "Real prose."
    assert kb_build.first_sentence(["~~~", "only code", "~~~"]) is None
    assert kb_build.first_sentence(["```", "never closed"]) is None


def test_first_sentence_limit_is_one_line_and_not_mid_word():
    text = " ".join(["word"] * 200)
    sentence = kb_build.first_sentence([text])
    assert len(sentence) <= 240 and sentence.endswith(chr(0x2026)) and "\n" not in sentence
    assert sentence[:-1].split(" ")[-1] == "word"
    assert len(kb_build.first_sentence(["x" * 5000])) <= 240


def test_rt_counter_longer_than_the_schema_limit_is_dropped_and_the_output_validates(tmp_path):
    vault = tmp_path / "vault"
    shutil.copytree(MINI_VAULT, vault)
    note = next(vault.rglob("SCOM 5.6*.md"))
    note.write_text(
        note.read_text(encoding="utf-8").replace("| Queue Depth |", f"| {'c' * 301} |"), encoding="utf-8"
    )
    out = build(tmp_path, "pack", vault=vault)
    assert rt_rows(out)["DMS_ExampleScheduler"]["counters"] == ["Active Slots"]
    assert validate_pack(out) == []


def test_invalid_generated_rt_output_is_a_build_error_and_nothing_is_written(tmp_path, capsys):
    curated = tmp_path / "curated.toml"
    curated.write_text('[[service]]\nprocess = "bad name!"\nfamily = "NMC"\n', encoding="utf-8")
    out = tmp_path / "o"
    args = ["--vault", str(MINI_VAULT), "--out", str(out), "--vault-commit", "none"]
    assert kb_build.main([*args, "--rt-curated", str(curated)]) == 2
    assert "is invalid" in capsys.readouterr().err
    assert not out.exists()
