"""Tests for tools/mine_templates.py (draft templates from unknown signatures). Synthetic data only."""

import importlib.util
import re
import sqlite3
import sys
import tomllib
from pathlib import Path

import pytest

from dmslog.parse.signature import signature
from dmslog.store.db import create_case_db

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("mine_templates", ROOT / "tools" / "mine_templates.py")
mt = importlib.util.module_from_spec(_spec)
sys.modules["mine_templates"] = mt
_spec.loader.exec_module(mt)

GUID_A = "00000000-0000-4000-8000-000000000001"
GUID_B = "00000000-0000-4000-8000-000000000002"

WIDGET = [
    *[f"[Sync start] - Request {GUID_A[:-1]}{i} for item W-{i} from client {i}." for i in range(5)],
    *[f"Widget {i} moved to 10.0.0.{i}:80 at '2030-01-15' by \"unit {i}\"" for i in range(3)],
    "Widget cache flushed",
    "Widget cache flushed",
    "Heartbeat lost for peer 'alpha'",
]
OTHER = ["Gizmo spun up 1", "Gizmo spun up 2", "Gizmo spun up 3"]


def build_case(path: Path, streams: dict[str, list[str]]) -> Path:
    conn = create_case_db(path)
    conn.execute("INSERT INTO collection(id, kind, path, added_at) VALUES (1, 'folder', 'x', 'now')")
    entry_id = 0
    sigs: dict[tuple[str, str], list[int]] = {}
    for sid, (family, msgs) in enumerate(streams.items(), 1):
        conn.execute(
            "INSERT INTO stream(id, collection_group, family, sensitive) VALUES (?,1,?,0)",
            (sid, family),
        )
        conn.execute(
            "INSERT INTO source(id, collection_id, stream_id, member, file_name, envelope) "
            "VALUES (?,1,?,?,?,'adms')",
            (sid, sid, f"{family}.log", f"{family}.log"),
        )
        for line, msg in enumerate(msgs, 1):
            entry_id += 1
            conn.execute(
                "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, sev, tpl_id, msg) "
                "VALUES (?,?,?,?,1,4,0,?)",
                (entry_id, sid, line, line * 100, msg),
            )
            hit = sigs.setdefault((family, signature(msg)), [0, entry_id])
            hit[0] += 1
    for (family, sig), (n, first) in sigs.items():
        conn.execute("INSERT INTO unknown_sig VALUES (?,?,?,?)", (family, sig, n, first))
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def case_db(tmp_path):
    return build_case(tmp_path / "case.sqlite", {"WIDGET": WIDGET})


def mine(*args: str) -> int:
    return mt.run(list(args))


def test_help_is_english(capsys):
    with pytest.raises(SystemExit) as exc:
        mine("--help")
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--min-count" in out and "anonymize" in out


def test_groups_counts_and_order(case_db, capsys):
    assert mine("--case-db", str(case_db), "--family", "WIDGET", "--min-count", "1") == 0
    out = capsys.readouterr().out
    doc = tomllib.loads(out)
    assert doc["family"] == "WIDGET" and doc["schema"] == 1
    assert len(doc["template"]) == 4  # 4 signatures
    assert "# 5 records, first entry id 1" in out
    positions = [out.index(f"# {n} records") for n in (5, 3, 2, 1)]
    assert positions == sorted(positions)


def test_min_count_and_limit(case_db, capsys):
    assert mine("--case-db", str(case_db), "--family", "WIDGET", "--min-count", "3") == 0
    assert len(tomllib.loads(capsys.readouterr().out)["template"]) == 2
    assert mine("--case-db", str(case_db), "--family", "WIDGET", "--min-count", "1", "--limit", "1") == 0
    assert len(tomllib.loads(capsys.readouterr().out)["template"]) == 1


def test_output_loads_and_regexes_match_examples(case_db, capsys):
    assert mine("--case-db", str(case_db), "--family", "WIDGET", "--min-count", "1") == 0
    out = capsys.readouterr().out
    catalog = mt.load_draft(out, check_examples=True)  # also checks every example
    templates = catalog.families["WIDGET"].templates
    assert len(templates) == 4
    by_example = {t.examples[0].msg: t for t in templates}
    assert len(by_example) == 4
    sync = next(t for m, t in by_example.items() if m.startswith("[Sync start]"))
    assert len(sync.examples) == 3  # extra distinct examples came from the unknown records
    tset = catalog.for_family("WIDGET", sensitive=False)
    match = tset.classify_text(f"[Sync start] - Request {GUID_B} for item W-9 from client 77.", "")
    assert match is not None and match.code == sync.code
    assert match.fields["client"] == 77
    assert match.fields["request"] == GUID_B
    assert any(t.text == "Widget cache flushed" for t in templates)  # no markers: exact text


def test_header_warning_present(case_db, capsys):
    mine("--case-db", str(case_db), "--family", "WIDGET")
    assert "Anonymize them before committing" in capsys.readouterr().out


def test_unsafe_or_odd_signatures_never_break_the_output(tmp_path, capsys):
    odd = ["x " + "'a' " * 30 + str(i) for i in range(3)] + [f"tab\there {i}" for i in range(3)]
    db = build_case(tmp_path / "c.sqlite", {"ODD": odd})
    assert mine("--case-db", str(db), "--family", "ODD", "--min-count", "1") == 0
    out = capsys.readouterr().out
    doc = tomllib.loads(out)  # still valid TOML, whatever was skipped
    mt.load_draft(out, check_examples=True)
    assert doc["family"] == "ODD"


def test_truncated_signature_still_matches(tmp_path, capsys):
    long_msgs = [f"Long {i} " + "word " * 80 for i in range(3)]
    db = build_case(tmp_path / "c.sqlite", {"LONG": long_msgs})
    assert mine("--case-db", str(db), "--family", "LONG") == 0
    out = capsys.readouterr().out
    catalog = mt.load_draft(out, check_examples=True)
    assert catalog.families["LONG"].templates[0].regex.match(long_msgs[0].strip())


def test_family_required_when_ambiguous(tmp_path, capsys):
    db = build_case(tmp_path / "c.sqlite", {"WIDGET": WIDGET, "GIZMO": OTHER})
    assert mine("--case-db", str(db)) == 2
    err = capsys.readouterr().err
    assert "GIZMO" in err and "WIDGET" in err
    assert mine("--case-db", str(db), "--family", "GIZMO") == 0
    out = capsys.readouterr().out
    assert tomllib.loads(out)["family"] == "GIZMO"
    assert "Widget" not in out


def test_single_family_is_inferred(tmp_path, capsys):
    db = build_case(tmp_path / "c.sqlite", {"GIZMO": OTHER})
    assert mine("--case-db", str(db)) == 0
    assert tomllib.loads(capsys.readouterr().out)["family"] == "GIZMO"


def test_case_without_unknowns(tmp_path, capsys):
    db = tmp_path / "empty.sqlite"
    create_case_db(db).close()
    assert mine("--case-db", str(db)) == 1
    assert "no unknown records" in capsys.readouterr().err


def test_min_count_above_everything(case_db, capsys):
    assert mine("--case-db", str(case_db), "--family", "WIDGET", "--min-count", "99") == 1
    assert "--min-count" in capsys.readouterr().err


def test_unknown_family_and_bad_inputs(case_db, tmp_path, capsys):
    assert mine("--case-db", str(case_db), "--family", "NOPE") == 1
    assert mine("--case-db", str(tmp_path / "missing.sqlite")) == 1
    junk = tmp_path / "junk.sqlite"
    junk.write_bytes(b"not a database at all")
    assert mine("--case-db", str(junk)) == 1
    assert mine("--case-db", str(case_db), "--limit", "0") == 2
    capsys.readouterr()


def test_case_name_with_data_dir(tmp_path, capsys):
    build_case(tmp_path / "data" / "cases" / "my-case" / "case.sqlite", {"GIZMO": OTHER})
    assert mine("--case", "my-case", "--data-dir", str(tmp_path / "data")) == 0
    assert tomllib.loads(capsys.readouterr().out)["family"] == "GIZMO"


def test_writes_only_the_out_file_and_keeps_the_case_untouched(case_db, tmp_path, capsys):
    before = case_db.read_bytes()
    out = tmp_path / "sub" / "widget.toml"
    out.parent.mkdir()
    snapshot = {p for p in tmp_path.rglob("*")}
    assert mine("--case-db", str(case_db), "--family", "WIDGET", "--out", str(out)) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert tomllib.loads(out.read_text(encoding="utf-8"))["family"] == "WIDGET"
    assert {p for p in tmp_path.rglob("*")} == snapshot | {out}
    assert case_db.read_bytes() == before
    conn = sqlite3.connect(case_db)
    assert conn.execute("SELECT count(*) FROM unknown_sig").fetchone()[0] == 4
    conn.close()


def test_toml_helpers_escape_control_characters():
    assert tomllib.loads(f"a = {mt.toml_str(chr(1) + chr(127) + 'q' + chr(34) + chr(92))}")["a"] == (
        chr(1) + chr(127) + "q" + chr(34) + chr(92)
    )
    assert tomllib.loads(f"a = {mt.toml_regex(r'^a' + chr(39) * 3 + '$')}")["a"] == "^a'''$"


# --- review fixes: no side effects next to the case, hostile text in comments, valid output ----


def _wal_case(tmp_path: Path) -> Path:
    """A copy of a WAL-mode case whose last transaction is still in the -wal file."""
    (tmp_path / "src").mkdir()
    (tmp_path / "dst").mkdir()
    db = build_case(tmp_path / "src" / "case.sqlite", {"GIZMO": OTHER})
    conn = sqlite3.connect(db)
    assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("INSERT INTO unknown_sig VALUES ('GIZMO', 'Late <n>', 9, 1)")
    conn.commit()
    wal = Path(f"{db}-wal")
    assert wal.stat().st_size > 0
    dst = tmp_path / "dst" / "case.sqlite"
    dst.write_bytes(db.read_bytes())
    Path(f"{dst}-wal").write_bytes(wal.read_bytes())
    conn.close()
    return dst


def test_pending_wal_is_refused_without_touching_the_directory(tmp_path, capsys):
    dst = _wal_case(tmp_path)
    before = {p.name: p.read_bytes() for p in dst.parent.iterdir()}
    assert mine("--case-db", str(dst), "--family", "GIZMO") == 1
    assert "write-ahead log" in capsys.readouterr().err
    assert {p.name: p.read_bytes() for p in dst.parent.iterdir()} == before  # no -shm, nothing changed
    assert not Path(f"{dst}-shm").exists()


def test_nonempty_shm_alone_is_refused(case_db, capsys):
    Path(f"{case_db}-shm").write_bytes(b"x" * 32)
    assert mine("--case-db", str(case_db), "--family", "WIDGET") == 1
    assert "write-ahead log" in capsys.readouterr().err


def test_empty_side_files_do_not_block_and_create_nothing(case_db, capsys):
    Path(f"{case_db}-wal").write_bytes(b"")
    before = sorted(p.name for p in case_db.parent.iterdir())
    assert mine("--case-db", str(case_db), "--family", "WIDGET") == 0
    capsys.readouterr()
    assert sorted(p.name for p in case_db.parent.iterdir()) == before


HOSTILE = [
    "\n[evil]\nx=1",
    "a\x00b",
    "a\rb\r\n[evil]",
    "a injected = true #",
    "esc\x1b[31m red",
    "\ninjected = true\n#",
    "quote\" and ' and \\ and \\n and '''",
    "x" * 6000,
    "\x7f\x85﻿‮ rtl",
]
EXPECTED_TOP = {"schema", "family", "extends", "template"}
EXPECTED_KEYS = {"code", "class", "sev", "title", "text", "regex", "window", "fields", "examples"}


def _assert_clean(out: str, family: str) -> None:
    doc = tomllib.loads(out)
    assert set(doc) - {"template"} == EXPECTED_TOP - {"template"} and doc["family"] == family
    for tpl in doc.get("template", []):
        assert set(tpl) <= EXPECTED_KEYS
    assert "injected" not in out.replace("\\n", "").split("# ")[0]
    mt.load_draft(out, check_examples=True)
    for line in out.splitlines():
        assert "\x00" not in line and " " not in line and " " not in line


@pytest.mark.parametrize("hostile", HOSTILE)
def test_hostile_messages_never_inject_toml(tmp_path, capsys, hostile):
    msgs = [f"Gadget {i} said {hostile} done" for i in range(3)] + [hostile + " tail"] * 2
    db = build_case(tmp_path / "c.sqlite", {"GADGET": msgs})
    assert mine("--case-db", str(db), "--family", "GADGET", "--min-count", "1") == 0
    _assert_clean(capsys.readouterr().out, "GADGET")


@pytest.mark.parametrize("hostile", HOSTILE)
def test_hostile_signature_row_never_injects_toml(tmp_path, capsys, hostile):
    db = build_case(tmp_path / "c.sqlite", {"GADGET": OTHER})
    conn = sqlite3.connect(db)
    conn.execute("UPDATE unknown_sig SET signature = ?", (hostile,))
    conn.commit()
    conn.close()
    rc = mine("--case-db", str(db), "--family", "GADGET", "--min-count", "1")
    out = capsys.readouterr().out
    assert rc == 0
    _assert_clean(out, "GADGET")


@pytest.mark.parametrize("hostile", HOSTILE)
def test_hostile_family_and_file_name(tmp_path, capsys, hostile):
    db = build_case(tmp_path / "c.sqlite", {hostile: OTHER})
    rc = mine("--case-db", str(db), "--family", hostile, "--min-count", "1")
    out = capsys.readouterr().out
    if rc == 0:
        _assert_clean(out, hostile)
    else:
        assert rc == 1 and out == ""  # refused instead of emitting an invalid file
    named = tmp_path / ("w\n[evil]\nx=1.sqlite" if sys.platform != "win32" else "w.sqlite")
    named.write_bytes(db.read_bytes())
    assert mine("--case-db", str(named), "--family", "GADGET") in (0, 1)  # family absent: error only
    capsys.readouterr()


def test_hostile_file_name_stays_in_one_comment(tmp_path, capsys):
    if sys.platform == "win32":
        pytest.skip("newline in file names")
    db = build_case(tmp_path / "tmp.sqlite", {"GIZMO": OTHER})
    named = tmp_path / "w\n[evil]\nx=1.sqlite"
    named.write_bytes(db.read_bytes())
    assert mine("--case-db", str(named), "--family", "GIZMO") == 0
    out = capsys.readouterr().out
    assert "[evil]" in out and "evil" not in tomllib.loads(out)
    _assert_clean(out, "GIZMO")


def test_comment_text_is_a_single_clean_line():
    text = mt.comment_text("a\nb\rc\x00d e\x1bf\ud800g" + "z" * 1000)
    assert "\n" not in text and "\r" not in text and "\x00" not in text and " " not in text
    assert "\\n" in text and "\\u0000" in text and "more chars" in text


def test_overlapping_templates_are_resolved_not_emitted_invalid(tmp_path, capsys):
    msgs = ["Value 0x1", "Value 0x1", "Value 0x2", "Value 11", "Value 12"]
    db = build_case(tmp_path / "c.sqlite", {"OVER": msgs})
    assert mine("--case-db", str(db), "--family", "OVER", "--min-count", "1") == 0
    out = capsys.readouterr().out
    mt.load_draft(out, check_examples=True)  # the whole file loads
    assert "conflicts with" in out
    assert "WARNING: the whole file" not in out


def test_overlap_skips_the_less_frequent_candidate(tmp_path, capsys):
    msgs = ["Value 11"] * 5 + ["Value 0x1"] * 2
    db = build_case(tmp_path / "c.sqlite", {"OVER": msgs})
    assert mine("--case-db", str(db), "--family", "OVER", "--min-count", "1") == 0
    out = capsys.readouterr().out
    catalog = mt.load_draft(out, check_examples=True)
    kept = catalog.families["OVER"].templates
    assert len(kept) == 1 and kept[0].examples[0].msg == "Value 11"
    assert "# SKIPPED OVER-002: conflicts with OVER-001" in out


def test_examples_longer_than_the_window_are_skipped_not_emitted(tmp_path, capsys):
    long_msgs = ["Huge " + "w" * 5000 + f" {i}" for i in range(3)]
    db = build_case(tmp_path / "c.sqlite", {"HUGE": long_msgs})
    assert mine("--case-db", str(db), "--family", "HUGE", "--min-count", "1") == 0
    out = capsys.readouterr().out
    mt.load_draft(out, check_examples=True)
    assert "SKIPPED HUGE-001" in out and "window" in out


def test_unresolvable_draft_exits_with_an_error(tmp_path, capsys, monkeypatch):
    db = build_case(tmp_path / "c.sqlite", {"GIZMO": OTHER})
    real = mt.load_draft

    def broken(text, *, check_examples=False):
        raise mt.TemplateError("synthetic failure")

    monkeypatch.setattr(mt, "load_draft", broken)
    out_file = tmp_path / "o.toml"
    assert mine("--case-db", str(db), "--family", "GIZMO", "--out", str(out_file)) == 1
    assert "synthetic failure" in capsys.readouterr().err and not out_file.exists()
    monkeypatch.setattr(mt, "load_draft", real)


# --- hostile numeric/text columns and the stderr summary ---------------------------------------

HOSTILE_VALUES = [
    "0\npayload_defaults = {}\n#",
    "0\x00",
    "9" * 5000,
    1.5,
    b"\x00\xff\nx = 1",
]


def _sig_case(tmp_path: Path, column: str, value) -> Path:
    db = build_case(tmp_path / "c.sqlite", {"GADGET": OTHER + ["Other thing 1", "Other thing 1"]})
    conn = sqlite3.connect(db)
    conn.execute(f"UPDATE unknown_sig SET {column} = ? WHERE signature LIKE 'Gizmo%'", (value,))
    conn.commit()
    conn.close()
    return db


@pytest.mark.parametrize("column", ["n", "first_entry_id", "signature"])
@pytest.mark.parametrize("value", HOSTILE_VALUES)
def test_hostile_numeric_columns_never_reach_the_output(tmp_path, capsys, column, value):
    db = _sig_case(tmp_path, column, value)
    rc = mine("--case-db", str(db), "--family", "GADGET", "--min-count", "1")
    cap = capsys.readouterr()
    assert rc == 0  # the other, valid group is still there: never a regression to an error
    doc = tomllib.loads(cap.out)
    assert set(doc) == EXPECTED_TOP and "payload_defaults" not in doc
    for tpl in doc["template"]:
        assert set(tpl) <= EXPECTED_KEYS
    mt.load_draft(cap.out, check_examples=True)
    for line in cap.out.splitlines():
        assert "payload_defaults" not in line or line.startswith("#")
    # the valid group stays an ACTIVE template (not commented out)
    assert any("Other thing" in (t.get("text") or t.get("regex") or "") for t in doc["template"])


@pytest.mark.parametrize("column", ["n", "first_entry_id"])
@pytest.mark.parametrize("value", [v for v in HOSTILE_VALUES if v != "0\x00"])  # that one is stored as 0
def test_invalid_integer_rows_are_skipped_with_a_warning(tmp_path, capsys, column, value):
    db = _sig_case(tmp_path, column, value)
    assert mine("--case-db", str(db), "--family", "GADGET", "--min-count", "1") == 0
    cap = capsys.readouterr()
    doc = tomllib.loads(cap.out)
    assert len(doc["template"]) == 1 and "ignored because of invalid data" in cap.err
    assert column in cap.err and "payload_defaults" not in cap.err


def test_all_rows_invalid_is_an_error_with_the_warning(tmp_path, capsys):
    db = build_case(tmp_path / "c.sqlite", {"GIZMO": OTHER})
    conn = sqlite3.connect(db)
    conn.execute("UPDATE unknown_sig SET first_entry_id = '0\npayload_defaults = {}\n#'")
    conn.commit()
    conn.close()
    assert mine("--case-db", str(db), "--family", "GIZMO", "--min-count", "1") == 1
    cap = capsys.readouterr()
    assert cap.out == "" and "invalid data" in cap.err


def test_dropped_candidates_are_summarized_on_stderr(tmp_path, capsys):
    msgs = ["Value 0x1", "Value 0x1", "Value 0x2", "Value 11", "Value 12"]
    db = build_case(tmp_path / "c.sqlite", {"OVER": msgs})
    out_file = tmp_path / "o.toml"
    assert mine("--case-db", str(db), "--family", "OVER", "--min-count", "1", "--out", str(out_file)) == 0
    err = capsys.readouterr().err
    assert "2 signature groups, 1 active templates, 1 commented out as SKIPPED" in err
    assert "SKIPPED OVER-002" in err and "conflicts with OVER-001" in err
    assert "wrote 1 active draft templates" in err and "1 SKIPPED" in err
    catalog = mt.load_draft(out_file.read_text(encoding="utf-8"), check_examples=True)
    assert len(catalog.families["OVER"].templates) == 1


def test_no_summary_when_nothing_was_dropped(case_db, capsys):
    assert mine("--case-db", str(case_db), "--family", "WIDGET", "--min-count", "1") == 0
    assert "summary" not in capsys.readouterr().err


CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
ESCAPED = "\x1b[31mRED\x1b[0m\nFORGED"


def _assert_stderr_clean(err: str, *raw: str) -> None:
    assert not CTRL.search(err), repr(err)
    for value in raw:
        assert value not in err


def test_family_with_escapes_is_sanitized_on_stderr(case_db, capsys):
    assert mine("--case-db", str(case_db), "--family", ESCAPED) == 1
    err = capsys.readouterr().err
    _assert_stderr_clean(err, ESCAPED, "\x1b")
    assert "RED" in err


def test_case_path_and_out_path_with_escapes_are_sanitized(tmp_path, capsys):
    missing = tmp_path / "no\x1b[2Jthere\nforged.sqlite"
    assert mine("--case-db", str(missing)) == 1
    _assert_stderr_clean(capsys.readouterr().err, "\x1b", str(missing))


def test_out_path_with_escapes_is_sanitized(case_db, tmp_path, capsys):
    out = tmp_path / "sub\x1b[2J\nforged" / "o.toml"  # parent missing: the write fails
    assert mine("--case-db", str(case_db), "--family", "WIDGET", "--min-count", "1", "--out", str(out)) == 1
    _assert_stderr_clean(capsys.readouterr().err, "\x1b", str(out))
    ok = tmp_path / "ok\x1b[2J\nforged.toml"
    assert mine("--case-db", str(case_db), "--family", "WIDGET", "--min-count", "1", "--out", str(ok)) == 0
    _assert_stderr_clean(capsys.readouterr().err, "\x1b", str(ok))


def test_exception_messages_are_sanitized(tmp_path, capsys):
    bad = tmp_path / "bad\x1b[2J\nforged.sqlite"
    bad.write_bytes(b"not a database" * 100)
    assert mine("--case-db", str(bad)) == 1
    _assert_stderr_clean(capsys.readouterr().err, "\x1b", str(bad))


def test_usage_errors_do_not_echo_raw_values(case_db, capsys):
    with pytest.raises(SystemExit):
        mine("--case-db", str(case_db), "--limit", "x\x1b[2J")
    _assert_stderr_clean(capsys.readouterr().err, "\x1b")


def test_stderr_summary_never_shows_examples(tmp_path, capsys):
    canary = "CANARY-EXAMPLE-123"
    msgs = [f"{canary} 0x1", f"{canary} 0x1", f"{canary} 0x2", f"{canary} 11"]
    db = build_case(tmp_path / "c.sqlite", {"OVER": msgs})
    out_file = tmp_path / "o.toml"
    assert mine("--case-db", str(db), "--family", "OVER", "--min-count", "1", "--out", str(out_file)) == 0
    err = capsys.readouterr().err
    assert "SKIPPED" in err and "CANARY" not in err
    assert "conflicts with OVER-001" in err


def test_unmatched_example_reason_is_generic(tmp_path, capsys, monkeypatch):
    canary = "CANARY-EXAMPLE-123"
    db = build_case(tmp_path / "c.sqlite", {"GIZMO": [f"{canary} a"] * 2 + [f"{canary} b"]})
    monkeypatch.setattr(mt, "build_regex", lambda *a, **k: ("^NEVER-MATCHES$", {}))
    out_file = tmp_path / "o.toml"
    assert mine("--case-db", str(db), "--family", "GIZMO", "--min-count", "1", "--out", str(out_file)) == 0
    err = capsys.readouterr().err
    assert "no regex matched its examples" in err and "CANARY" not in err
    assert "SKIPPED" in out_file.read_text(encoding="utf-8")  # the detail stays in the output file


def test_rejected_catalog_reason_in_summary_is_generic(tmp_path, capsys, monkeypatch):
    db = build_case(tmp_path / "c.sqlite", {"GIZMO": ["Gizmo spun up 1"] * 3})
    monkeypatch.setattr(mt, "_usable", lambda *a: (mt.REASON_CATALOG, "detail CANARY-EXAMPLE-123 \x1b"))
    assert mine("--case-db", str(db), "--family", "GIZMO", "--min-count", "1") == 0
    cap = capsys.readouterr()
    assert "catalog rejected the draft" in cap.err and "CANARY" not in cap.err and "\x1b" not in cap.err
    assert not CTRL.search(cap.err)


def _entry_case(tmp_path: Path, column: str, value) -> Path:
    db = build_case(tmp_path / "c.sqlite", {"GADGET": OTHER + ["Other thing 1", "Other thing 1"]})
    conn = sqlite3.connect(db)
    conn.execute(
        f"UPDATE entry SET {column} = ? WHERE id = (SELECT first_entry_id FROM unknown_sig "
        "WHERE signature LIKE 'Gizmo%')",
        (value,),
    )
    conn.commit()
    conn.close()
    return db


BAD_ENTRY_VALUES = [("flags", "text\x1b[2J"), ("flags", 1.5), ("msg", b"\xff\x00")]


@pytest.mark.parametrize(("column", "value"), BAD_ENTRY_VALUES)
def test_invalid_entry_values_reject_the_row_with_a_column_only_warning(tmp_path, capsys, column, value):
    db = _entry_case(tmp_path, column, value)
    assert mine("--case-db", str(db), "--family", "GADGET", "--min-count", "1") == 0
    cap = capsys.readouterr()
    assert "ignored because of invalid data" in cap.err
    assert f"entry row rejected: invalid value in column {column}" in cap.err
    _assert_stderr_clean(cap.err, "\x1b", "text")
    # the rejected row gave no example: no active template quotes it from the bad row alone
    doc = tomllib.loads(cap.out)
    assert any("Other thing" in (t.get("text") or t.get("regex") or "") for t in doc["template"])


def test_invalid_row_does_not_use_up_the_limit(tmp_path, capsys):
    db = _sig_case(tmp_path, "n", "bad")  # sorts before every integer
    assert mine("--case-db", str(db), "--family", "GADGET", "--min-count", "1", "--limit", "1") == 0
    cap = capsys.readouterr()
    doc = tomllib.loads(cap.out)
    (tpl,) = doc["template"]
    assert "Other thing" in (tpl.get("text") or tpl["regex"])
    assert "column n" in cap.err


def test_zero_count_row_is_rejected_with_a_warning(tmp_path, capsys):
    db = _sig_case(tmp_path, "n", 0)
    assert mine("--case-db", str(db), "--family", "GADGET", "--min-count", "1") == 0
    cap = capsys.readouterr()
    assert "unknown_sig row rejected: invalid value in column n" in cap.err
    assert len(tomllib.loads(cap.out)["template"]) == 1
