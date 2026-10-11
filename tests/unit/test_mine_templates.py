"""Tests for tools/mine_templates.py (draft templates from unknown signatures). Synthetic data only."""

import importlib.util
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
