"""Stateful property test of bookmarks over the real ingestion flow (ARQUITETURA 9.3 and 13).

Random sequences of file changes (append, close the last record, grow it, truncate, rewrite with the
same or other content, rotate, drop a rotated file, force a reindex), each followed by ``run_ingest``
with a random batch size, interleaved with bookmark operations (create, delete, export + import into
the same case and into a new one, a v3 -> v4 migration). After every step the stored bookmarks are
compared with a reference model that knows, per note, the identity of the bookmarked record (its
complete content, or the prefix of an open record) and its lineage:

(a) at most one bookmark per record; (b) a linked bookmark sits on a record of its lineage whose
content is its known identity; (c) no note is lost: the notes change only by explicit create/delete;
(d) export + import is idempotent and keeps the multiplicity; (e) a detached bookmark never links
again on its own; (f) a complete fingerprint never changes and an open one only gets stronger;
(g) the ``entry_id`` cache is what a full resolution (locator + fingerprint) gives; and, after each
ingestion, a linked bookmark whose record survived stays on it, and a record that comes back is
taken by the orphan of its identity that the model says wins: the one orphaned last (a bookmark whose
record was only parsed again within the step counts as orphaned in it), never one that lost its
record earlier; bookmarks orphaned only by an import or a migration rank last, the oldest first.

``DMSLOG_BOOKMARK_EXAMPLES`` raises the number of examples for a longer local run.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, precondition, rule  # noqa: E402

from dmslog.ingest.pipeline import PAYLOAD_VERSION_KEY, IngestOptions, run_ingest  # noqa: E402
from dmslog.store import bookmarks as bm  # noqa: E402
from dmslog.store import create_case_db  # noqa: E402
from dmslog.store.db import migrate  # noqa: E402

EXAMPLES = int(os.environ.get("DMSLOG_BOOKMARK_EXAMPLES", "150"))
STEPS = 30

LOGDIR = "dnaApp_zone1/SVC"
LOG = f"{LOGDIR}/WfmAdapter_1111.log"
LOG0 = f"{LOG}.0"
LOG1 = f"{LOG}.1"

# Few distinct contents, so that records of different files (same position, same text) share their
# identity, and two messages share a start (an open record cannot tell them apart yet).
MESSAGES = ("[Alpha] alpha", "[Alpha] beta", "[Alpha] alpha beta")
CONTINUATIONS = ("  cont one", "  cont two")
TAILS = ("x", " beta", "\n  cont one", "\n  cont two\n", "\n")


def header(index: int, msg: str) -> str:
    seconds, ms = divmod(index * 10, 1000)
    return f"2030-01-15 10:00:{seconds:02d}.{ms:03d} [PID=1111][TID=0x1a2b] INFO - {msg}"


def render(file_no: int, picks: list[tuple[int, bool]], *, final_newline: bool = True) -> str:
    """A file: a first line unique to the file (its rotation identity), then the picked records."""
    lines = [header(0, f"[Alpha] file {file_no:04d} start")]
    for i, (msg, cont) in enumerate(picks, start=1):
        lines.append(header(i, MESSAGES[msg]))
        if cont:
            lines.append(CONTINUATIONS[i % 2])
    return "\n".join(lines) + ("\n" if final_newline else "")


records = st.lists(st.tuples(st.integers(0, len(MESSAGES) - 1), st.booleans()), min_size=0, max_size=5)
# None: one batch per file; otherwise small reads and a commit every few records (intermediate batches).
batch_sizes = st.sampled_from([None, 40, 120])

Content = tuple[int, int, int, int | None, str]  # source_id, line_no, byte_off, ts, text


def ident_matches(ident: tuple | None, c: Content) -> bool:
    if ident is None:  # no identity yet (a bookmark from schema 3): any record
        return True
    if ident[0] == "f":
        return (c[2], c[3], c[4]) == ident[1:]
    if ident[0] == "p":
        return (c[2], c[3]) == ident[1:3] and c[4].startswith(ident[3])
    return bm.fp_matches(ident[1], c[2], c[3], c[4])  # opaque: only its fingerprint is known


def expected_fp(ident: tuple | None) -> str | None:
    if ident is None:
        return None
    if ident[0] == "f":
        return bm.fingerprint(ident[1], ident[2], ident[3])
    if ident[0] == "p":
        return bm.fingerprint(ident[1], ident[2], ident[3], open_record=True)
    return ident[1]


def stronger_or_same(old: str | None, new: str | None) -> bool:
    if old is None:
        return True
    if old.startswith("f:"):
        return new == old
    if new is None:
        return False
    if new.startswith("f:"):
        return True
    return int(new[1 : new.index(":")]) >= int(old[1 : old.index(":")])


@dataclass
class Model:
    lineage: int | None  # source id; None = detached
    ident: tuple | None  # ("f", off, ts, text) | ("p", off, ts, prefix) | ("o", fp) | None
    fp: str | None  # last stored fingerprint seen
    # Model clock of the file step in which an ingestion last took its record away; None when that
    # never happened since it was imported or migrated (it then ranks below any such step).
    orphaned: int | None = None


class Snapshot:
    """Everything the checks read: records, their content, sources and bookmarks."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.entries: dict[int, Content] = {}
        for eid, sid, line, off, ts, msg, cont in conn.execute(
            "SELECT e.id, e.source_id, e.line_no, e.byte_off, e.ts, e.msg, b.cont FROM entry e"
            " LEFT JOIN entry_body b ON b.entry_id = e.id"
        ):
            self.entries[eid] = (sid, line, off, ts, bm.record_text(msg, cont))
        self.at_line = {(c[0], c[1]): eid for eid, c in self.entries.items()}
        self.last = dict(conn.execute("SELECT source_id, max(id) FROM entry GROUP BY source_id"))
        self.members = dict(conn.execute("SELECT id, member FROM source"))
        sql = "SELECT note, id, member, line_no, entry_id, source_id, fp, uid FROM bookmark"
        self.rows = {r[0]: r for r in conn.execute(sql)}  # note -> the row above
        self.count = conn.execute("SELECT count(*) FROM bookmark").fetchone()[0]

    def content_keys(self) -> set[tuple[int, Content]]:
        return set(self.entries.items())


class BookmarkMachine(RuleBasedStateMachine):
    @initialize(first=records, batch=batch_sizes)
    def setup(self, first, batch) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="dmslog-bm-"))
        self.root = self.tmp / "Log"
        (self.root / LOGDIR).mkdir(parents=True)
        self.conn = create_case_db(self.tmp / "case" / "case.sqlite")
        self.files: dict[str, str] = {}
        self.file_no = 0
        self.notes = 0
        self.models: dict[str, Model] = {}
        self.cases = 0
        self.step = 0  # model clock: one tick per file step
        # Source rows dropped by an ingestion (their ids may be reused at once by a new source).
        self.conn.execute("CREATE TEMP TABLE dropped(id INTEGER)")
        self.conn.execute(
            "CREATE TEMP TRIGGER log_dropped AFTER DELETE ON main.source BEGIN"
            " INSERT INTO dropped(id) VALUES (OLD.id); END"
        )
        self.conn.commit()
        self.files[LOG] = self.new_file(first)
        self.write_files()
        self.ingest(batch)

    def teardown(self) -> None:
        if hasattr(self, "conn"):
            self.conn.close()
            shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ helpers

    def new_file(self, picks, final_newline: bool = True) -> str:
        self.file_no += 1
        return render(self.file_no, picks, final_newline=final_newline)

    def write_files(self) -> None:
        for name in (LOG, LOG0, LOG1):
            path = self.root / name
            if name in self.files:
                path.write_bytes(self.files[name].encode("utf-8"))
            elif path.exists():
                path.unlink()

    def ingest(self, batch, *, conn: sqlite3.Connection | None = None) -> None:
        options = (
            IngestOptions() if batch is None else IngestOptions(batch_bytes=batch, chunk_size=batch // 2)
        )
        run_ingest(conn or self.conn, self.root, options)

    def file_step(self, batch) -> None:
        """Write the files, ingest them, and check everything an ingestion may change."""
        before = Snapshot(self.conn)
        self.conn.execute("DELETE FROM dropped")
        self.conn.commit()
        self.write_files()
        self.step += 1
        self.ingest(batch)
        self.check(before, ingested=True)

    # ------------------------------------------------------------------ file changes

    @rule(picks=records, final_newline=st.booleans(), batch=batch_sizes)
    def append_records(self, picks, final_newline, batch) -> None:
        """New records after the last one: they close it (unless nothing is appended)."""
        text = self.files.get(LOG, "")
        start = text.count("\n") + 1 if text and not text.endswith("\n") else max(text.count("\n"), 1)
        lines = []
        for i, (msg, cont) in enumerate(picks, start=start):
            lines.append(header(i, MESSAGES[msg]))
            if cont:
                lines.append(CONTINUATIONS[i % 2])
        if text and not text.endswith("\n") and lines:
            text += "\n"
        self.files[LOG] = text + "\n".join(lines) + ("\n" if lines and final_newline else "")
        self.file_step(batch)

    @precondition(lambda self: LOG in self.files)
    @rule(tail=st.sampled_from(TAILS), batch=batch_sizes)
    def grow_last_record(self, tail, batch) -> None:
        """Text appended to the end: the last record grows (its first line or a continuation)."""
        self.files[LOG] += tail
        self.file_step(batch)

    @precondition(lambda self: LOG in self.files)
    @rule(cut=st.floats(0.3, 1.0), member=st.sampled_from([LOG, LOG0]), batch=batch_sizes)
    def truncate(self, cut, member, batch) -> None:
        if member not in self.files:
            return
        text = self.files[member]
        self.files[member] = text[: int(len(text) * cut)]
        self.file_step(batch)

    @precondition(lambda self: LOG in self.files)
    @rule(keep=st.integers(1, 14), newline=st.booleans(), member=st.sampled_from([LOG, LOG0]),
          batch=batch_sizes)  # fmt: skip
    def cut_lines(self, keep, newline, member, batch) -> None:
        """Truncation at a line boundary: the record before the cut may become the last one, intact."""
        if member not in self.files:
            return
        lines = self.files[member].split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        self.files[member] = "\n".join(lines[:keep]) + ("\n" if newline else "")
        self.file_step(batch)

    @precondition(lambda self: self.models)
    @rule(pick=st.integers(0, 10_000), newline=st.booleans(), batch=batch_sizes)
    def cut_after_a_bookmark(self, pick, newline, batch) -> None:
        """The records after a bookmarked one are cut away: it becomes the last record, intact."""
        rows = self.conn.execute(
            "SELECT s.member, e.line_no, e.nlines FROM bookmark b JOIN entry e ON e.id = b.entry_id"
            " JOIN source s ON s.id = e.source_id ORDER BY b.id"
        ).fetchall()
        if not rows:
            return
        member, line_no, nlines = rows[pick % len(rows)]
        if member not in self.files:
            return
        lines = self.files[member].split("\n")
        self.files[member] = "\n".join(lines[: line_no - 1 + nlines]) + ("\n" if newline else "")
        self.file_step(batch)

    @precondition(lambda self: LOG in self.files)
    @rule(line=st.integers(1, 12), member=st.sampled_from([LOG, LOG0]), batch=batch_sizes)
    def delete_line(self, line, member, batch) -> None:
        """A line removed: the records after it move to other lines and offsets."""
        if member not in self.files:
            return
        lines = self.files[member].split("\n")
        index = line % len(lines)
        if index == 0:
            return
        del lines[index]
        self.files[member] = "\n".join(lines)
        self.file_step(batch)

    @precondition(lambda self: LOG in self.files)
    @rule(
        line=st.integers(1, 12), msg=st.integers(0, len(MESSAGES) - 1), member=st.sampled_from([LOG, LOG0]),
        batch=batch_sizes,
    )  # fmt: skip
    def rewrite_line(self, line, msg, member, batch) -> None:
        """One line rewritten: same text, other text of the same length, or another length."""
        if member not in self.files:
            return
        lines = self.files[member].split("\n")
        index = line % len(lines)
        if index == 0 or not lines[index]:
            return  # keep the first line: it is the identity of the file across rotations
        if lines[index].startswith(" "):
            lines[index] = CONTINUATIONS[msg % 2]
        else:
            lines[index] = header(index, MESSAGES[msg])
        self.files[member] = "\n".join(lines)
        self.file_step(batch)

    @precondition(lambda self: LOG in self.files)
    @rule(member=st.sampled_from([LOG, LOG0, LOG1]), batch=batch_sizes)
    def force_reindex(self, member, batch) -> None:
        """Same content, parsed again from scratch."""
        row = self.conn.execute("SELECT id FROM source WHERE member = ?", (member,)).fetchone()
        if row is not None:
            self.conn.execute("DELETE FROM meta WHERE key = ?", (f"{PAYLOAD_VERSION_KEY}.{row[0]}",))
            self.conn.commit()
        self.file_step(batch)

    @precondition(lambda self: LOG in self.files)
    @rule(picks=records, twin=st.booleans(), batch=batch_sizes)
    def rotate(self, picks, twin, batch) -> None:
        """``.log.0`` -> ``.log.1``, ``.log`` -> ``.log.0`` and a new ``.log`` (maybe a twin of the old)."""
        if LOG0 in self.files:
            self.files[LOG1] = self.files.pop(LOG0)
        self.files[LOG0] = self.files.pop(LOG)
        self.files[LOG] = self.twin_of(self.files[LOG0]) if twin else self.new_file(picks)
        self.file_step(batch)

    @precondition(lambda self: LOG in self.files and LOG0 in self.files)
    @rule(picks=records, twin=st.booleans(), batch=batch_sizes)
    def drop_rotated(self, picks, twin, batch) -> None:
        """``.log.0`` disappears and ``.log`` rotates into its name: the old source is dropped."""
        self.files[LOG0] = self.files.pop(LOG)
        self.files[LOG] = self.twin_of(self.files[LOG0]) if twin else self.new_file(picks)
        self.file_step(batch)

    def twin_of(self, text: str) -> str:
        """Same records as ``text`` after a first line of its own (same length)."""
        first, _, rest = text.partition("\n")
        self.file_no += 1
        return header(0, f"[Alpha] file {self.file_no:04d} start") + ("\n" + rest if _ else "")

    # ------------------------------------------------------------------ bookmark operations

    @rule(pick=st.integers(0, 10_000))
    def create(self, pick) -> None:
        before = Snapshot(self.conn)
        if before.entries:
            ids = sorted(before.entries)
            self.create_on(before, ids[pick % len(ids)])

    @rule()
    def create_on_the_open_record(self) -> None:
        """A bookmark on the last record of ``.log``: an open identity that later growth must respect."""
        before = Snapshot(self.conn)
        sid = next((i for i, m in before.members.items() if m == LOG), None)
        if before.last.get(sid) is not None:
            self.create_on(before, before.last[sid])

    def create_on(self, before: Snapshot, eid: int) -> None:
        holder = [r for r in before.rows.values() if r[4] == eid]
        self.notes += 1
        note = f"note {self.notes}"
        if holder:
            with pytest.raises(bm.BookmarkError) as exc:
                bm.create_bookmark(self.conn, eid, "red", note)
            assert exc.value.code == "already_bookmarked"
            self.check(before)
            return
        made = bm.create_bookmark(self.conn, eid, "red", note)
        assert made["entry_id"] == eid
        c = before.entries[eid]
        last = before.last[c[0]] == eid
        ident = ("p", c[2], c[3], c[4].rstrip("�")) if last else ("f", c[2], c[3], c[4])
        self.models[note] = Model(lineage=c[0], ident=ident, fp=None)
        self.check(before)

    @precondition(lambda self: self.models)
    @rule(pick=st.integers(0, 10_000))
    def delete(self, pick) -> None:
        before = Snapshot(self.conn)
        note = sorted(self.models)[pick % len(self.models)]
        bm.delete_bookmark(self.conn, before.rows[note][1])
        del self.models[note]
        self.check(before)

    @precondition(lambda self: self.models)
    @rule()
    def export_import_same_case(self) -> None:
        before = Snapshot(self.conn)
        data = bm.export_bookmarks(self.conn)
        n = len(data["bookmarks"])
        counts = bm.import_bookmarks(self.conn, data)
        assert counts == {"imported": 0, "existing": n, "unmatched": 0, "invalid": 0}
        after = Snapshot(self.conn)
        assert after.rows == before.rows  # nothing changed at all
        self.check(before)

    @precondition(lambda self: self.models)
    @rule()
    def export_delete_import(self) -> None:
        before = Snapshot(self.conn)
        data = bm.export_bookmarks(self.conn)
        for note, row in before.rows.items():
            bm.delete_bookmark(self.conn, row[1])
            self.models[note].orphaned = None  # new rows: no ingestion has orphaned them yet
        n = len(data["bookmarks"])
        counts = bm.import_bookmarks(self.conn, data)
        assert counts == {"imported": n, "existing": 0, "unmatched": 0, "invalid": 0}
        after = Snapshot(self.conn)
        assert {r[0]: r[7] for r in after.rows.values()} == {r[0]: r[7] for r in before.rows.values()}  # uids
        for note, row in before.rows.items():
            if row[4] is not None:  # a linked bookmark is linked again to the same record
                assert after.rows[note][4] == row[4], note
        self.check(before, imported=True)

    @precondition(lambda self: self.models)
    @rule(batch=batch_sizes)
    def export_import_new_case(self, batch) -> None:
        data = bm.export_bookmarks(self.conn)
        self.cases += 1
        other = create_case_db(self.tmp / f"other{self.cases}" / "case.sqlite")
        try:
            self.ingest(batch, conn=other)
            members = {m for (m,) in other.execute("SELECT member FROM source")}
            counts = bm.import_bookmarks(other, data)
            expected = [i for i in data["bookmarks"] if i["detached"] or i["member"] in members]
            assert counts == {"imported": len(expected), "existing": 0,
                              "unmatched": len(data["bookmarks"]) - len(expected), "invalid": 0}  # fmt: skip
            again = bm.import_bookmarks(other, data)
            assert again["imported"] == 0 and again["existing"] == len(expected)
            snap = Snapshot(other)
            assert sorted(snap.rows) == sorted(i["note"] for i in expected)
            self.check_structure(snap, other)
            for note, row in snap.rows.items():
                if row[4] is not None:
                    assert ident_matches(self.models[note].ident, snap.entries[row[4]]), note
                if row[5] is None:
                    assert row[4] is None
        finally:
            other.close()

    @precondition(lambda self: self.models)
    @rule()
    def save_export(self) -> None:
        """Keep an export (a backup the user may import again later) with what the model knew then."""
        data = bm.export_bookmarks(self.conn)
        known = {note: Model(m.lineage, m.ident, m.fp) for note, m in self.models.items()}
        self.saved = (getattr(self, "saved", []) + [(data, known)])[-4:]

    @precondition(lambda self: getattr(self, "saved", None))
    @rule(pick=st.integers(0, 3))
    def import_saved_export(self, pick) -> None:
        """An older export imported again: bookmarks deleted since come back, the others are existing."""
        data, known = self.saved[pick % len(self.saved)]
        before = Snapshot(self.conn)
        counts = bm.import_bookmarks(self.conn, data)
        after = Snapshot(self.conn)
        back = [i for i in data["bookmarks"] if i["note"] not in self.models]
        missing = [i for i in back if i["note"] not in after.rows]
        assert counts == {"imported": len(back) - len(missing), "unmatched": len(missing),
                          "existing": len(data["bookmarks"]) - len(back), "invalid": 0}  # fmt: skip
        for item in missing:
            assert not item["detached"] and item["member"] not in after.members.values()
        for item in back:
            if item["note"] in after.rows:
                row = after.rows[item["note"]]
                assert row[7] == item["uid"] and (row[5] is None) == item["detached"]
                ident = known[item["note"]].ident
                self.models[item["note"]] = Model(lineage=row[5], ident=ident, fp=item["fp"], orphaned=None)
        self.check(before, imported=True)

    @precondition(lambda self: self.models)
    @rule()
    def migrate_from_v3(self) -> None:
        """The case goes back to schema 3 (no lineage, fingerprint nor uid) and is migrated again."""
        conn = self.conn
        conn.isolation_level = None
        conn.execute("DROP TRIGGER bookmark_source_gone")
        for index in ("ix_bookmark_source", "ix_bookmark_orphan", "ix_bookmark_open", "ux_bookmark_entry",
                      "ux_bookmark_uid"):  # fmt: skip
            conn.execute(f"DROP INDEX {index}")
        for column in ("fp", "source_id", "uid", "orphan_seq"):
            conn.execute(f"ALTER TABLE bookmark DROP COLUMN {column}")
        conn.execute("PRAGMA user_version=3")
        conn.isolation_level = ""
        migrate(conn)
        after = Snapshot(conn)
        self.check_structure(after, conn)
        self.saved = []  # exports taken before carry uids this case no longer has (schema 3 had none)
        # Schema 3 had no identities: the migration adopts what it finds; the model starts again from it.
        for note, row in after.rows.items():
            entry = after.entries.get(row[4]) if row[4] is not None else None
            self.models[note] = Model(lineage=row[5], ident=self.learn(row[6], entry), fp=row[6])

    # ------------------------------------------------------------------ checks

    @staticmethod
    def learn(fp: str | None, c: Content | None) -> tuple | None:
        """The identity a stored fingerprint stands for (the content is known when it is linked)."""
        if fp is None:
            return None
        if c is None:
            return ("o", fp)
        if fp.startswith("f:"):
            return ("f", c[2], c[3], c[4])
        return ("p", c[2], c[3], c[4][: int(fp[1 : fp.index(":")])])

    def check_structure(self, snap: Snapshot, conn: sqlite3.Connection) -> None:
        """(a) and (g) on any case: one bookmark per record; the cache is the resolution."""
        linked = [r[4] for r in snap.rows.values() if r[4] is not None]
        assert len(linked) == len(set(linked)), "two bookmarks on one record"
        assert snap.count == len(snap.rows), "duplicate notes"
        for note, (_n, _bid, member, line, eid, sid, fp, _uid) in snap.rows.items():
            if sid is not None:
                assert snap.members[sid] == member, (note, "the locator does not follow its lineage")
            if eid is None:
                continue
            c = snap.entries.get(eid)
            assert c is not None, (note, "dangling cache")
            assert sid is not None and c[0] == sid and c[1] == line, (note, "cache off its lineage or line")
            assert snap.at_line[(sid, line)] == eid
            assert bm.fp_matches(fp, c[2], c[3], c[4]), (note, "linked to a record its fingerprint rejects")
        # A full pass from scratch keeps every link and fingerprint the writer kept.
        conn.isolation_level = None
        conn.execute("BEGIN")
        try:
            bm.relink_all(conn)
            redone = {r[0]: r for r in conn.execute("SELECT note, entry_id, fp FROM bookmark")}
        finally:
            conn.execute("ROLLBACK")
            conn.isolation_level = ""
        for note, row in snap.rows.items():
            if row[4] is not None:
                assert redone[note][1:] == (row[4], row[6]), (note, "full pass disagrees with the cache")

    def check(self, before: Snapshot, *, ingested: bool = False, imported: bool = False) -> None:
        after = Snapshot(self.conn)
        assert sorted(after.rows) == sorted(self.models), "notes lost or invented"  # (c)
        self.check_structure(after, self.conn)  # (a), (g)
        old_keys = before.content_keys() if ingested else set()
        if ingested:
            self.check_winners(before, after, old_keys)
        for note, model in self.models.items():
            row = after.rows[note]
            eid, sid, fp = row[4], row[5], row[6]
            prev = before.rows.get(note)
            # (e) lineage: never changes, may only be dropped (by an ingestion: its source went away)
            if model.lineage is None:
                assert sid is None and eid is None, (note, "a detached bookmark came back")
            elif sid is None:
                dropped = {r[0] for r in self.conn.execute("SELECT id FROM dropped")} if ingested else set()
                assert model.lineage in dropped, (note, "detached without cause")
                assert eid is None
            else:
                assert sid == model.lineage, (note, "lineage changed")
            # (f) identities only get stronger
            if prev is not None:
                assert stronger_or_same(prev[6], fp), (note, prev[6], fp)
            c = after.entries.get(eid) if eid is not None else None
            if c is not None:  # (b) the record is the bookmarked one
                assert ident_matches(model.ident, c), (note, model.ident, c)
            if ingested and sid is not None and model.ident is not None:
                here = after.at_line.get((sid, row[3]))
                if here is not None and ident_matches(model.ident, after.entries[here]):
                    if prev is not None and prev[4] is not None:  # continuity: it stays on its record
                        assert eid == here, (note, "lost a record that is still there")
            # update the model: open identities become complete once a later record exists
            model.lineage = sid
            if c is not None:
                if model.ident is None or model.ident[0] == "o":
                    model.ident = self.learn(fp, c)
                elif model.ident[0] == "p":
                    last = after.last[c[0]] == eid
                    model.ident = ("p", c[2], c[3], c[4].rstrip("�")) if last else ("f", c[2], c[3], c[4])
                assert fp == expected_fp(model.ident), (note, "stored fingerprint", fp, model.ident)
            elif model.ident is not None and model.ident[0] != "o":
                assert fp == expected_fp(model.ident), (note, "orphan fingerprint", fp, model.ident)
            model.fp = fp

    def check_winners(self, before: Snapshot, after: Snapshot, old_keys: set[tuple[int, Content]]) -> None:
        """After an ingestion: each record that came back is held by the bookmark the model predicts.

        The model clock moves first: a bookmark whose record went away in this step was orphaned in
        it (even when it took a new parse of the record back). Among the bookmarks of the record's
        lineage and line whose identity it has, the one orphaned at the latest step wins; inside one
        step the order of the writer's calls decides, so any of them may; among bookmarks no ingestion
        has orphaned (imported or migrated) the oldest wins. The others stay orphans.
        """
        after_keys = after.content_keys()
        for note, model in self.models.items():
            prev = before.rows.get(note)
            if prev is not None and prev[4] is not None:
                if (prev[4], before.entries[prev[4]]) not in after_keys:
                    model.orphaned = self.step
        claims: dict[int, list[str]] = {}  # record that came back -> notes whose identity it has
        for note, model in self.models.items():
            row = after.rows[note]
            if row[5] is None or model.ident is None:
                continue
            here = after.at_line.get((row[5], row[3]))
            if here is None or (here, after.entries[here]) in old_keys:
                continue
            if ident_matches(model.ident, after.entries[here]):
                claims.setdefault(here, []).append(note)
        for here, notes in claims.items():
            rank = {n: -1 if self.models[n].orphaned is None else self.models[n].orphaned for n in notes}
            top = max(rank.values())
            group = sorted((n for n in notes if rank[n] == top), key=lambda n: after.rows[n][1])
            winners = group[:1] if top == -1 else group
            holders = [n for n in notes if after.rows[n][4] == here]
            assert holders, (notes, "its record came back but they all stayed orphans")
            assert holders[0] in winners, (here, "taken by", holders[0], "expected one of", winners, rank)

    @invariant()
    def one_bookmark_per_record(self) -> None:
        if hasattr(self, "conn"):
            rows = self.conn.execute(
                "SELECT entry_id FROM bookmark WHERE entry_id IS NOT NULL"
                " GROUP BY entry_id HAVING count(*) > 1"
            ).fetchall()
            assert rows == []


BookmarkMachine.TestCase.settings = settings(
    max_examples=EXAMPLES,
    stateful_step_count=STEPS,
    deadline=None,
    database=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large, HealthCheck.filter_too_much],
)
TestBookmarkMachine = BookmarkMachine.TestCase
