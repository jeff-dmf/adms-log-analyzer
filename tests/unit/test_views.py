"""Saved filters store: sanitizing, validation, limits and atomic persistence."""

import sqlite3

import pytest

from dmslog.store import db, views
from dmslog.store.filters import FilterError

FLT = {"and": [{"sev": {"le": 2}}, {"not": {"source": [1]}}]}


@pytest.fixture
def conn(tmp_path):
    c = db.create_case_db(tmp_path / "case.sqlite")
    yield c
    c.close()


def test_create_list_delete_roundtrip(conn):
    made = views.create_view(conn, "  Errors   only ", FLT, ["ts", "msg"])
    assert made["name"] == "Errors only" and made["filter"] == FLT and made["columns"] == ["ts", "msg"]
    assert made["created_at"].endswith("Z")
    assert views.list_views(conn) == [made]
    views.delete_view(conn, made["id"])
    assert views.list_views(conn) == []


def test_persisted_in_a_new_connection(conn, tmp_path):
    views.create_view(conn, "a", FLT)
    other = db.connect(tmp_path / "case.sqlite", readonly=True)
    try:
        (row,) = views.list_views(other)
        assert row["name"] == "a" and row["columns"] is None
    finally:
        other.close()


def test_name_is_sanitized():
    assert views.sanitize_name("a‮b\x00c\t d\n") == "abc d"
    with pytest.raises(views.ViewError) as exc:
        views.sanitize_name(" \x01 ")
    assert exc.value.code == "bad_name"
    for bad in (None, 5, "x" * (views.MAX_NAME_CHARS + 1)):
        with pytest.raises(views.ViewError):
            views.sanitize_name(bad)
    assert views.sanitize_name("x" * views.MAX_NAME_CHARS)


def test_duplicate_name_ignores_case_and_spacing(conn):
    views.create_view(conn, "Errors", FLT)
    with pytest.raises(views.ViewError) as exc:
        views.create_view(conn, " ERRORS ", FLT)
    assert exc.value.code == "duplicate_name"
    assert len(views.list_views(conn)) == 1


def test_invalid_filters_are_rejected(conn):
    for bad in (None, [], {}, "x"):
        with pytest.raises(views.ViewError) as exc:
            views.create_view(conn, "n", bad)
        assert exc.value.code == "bad_filter"
    with pytest.raises(FilterError):
        views.create_view(conn, "n", {"nope": 1})
    with pytest.raises(views.ViewError) as exc:
        views.create_view(conn, "n", FLT, "ts")
    assert exc.value.code == "bad_columns"
    assert views.list_views(conn) == []


def test_limit_of_views(conn, monkeypatch):
    monkeypatch.setattr(views, "MAX_VIEWS", 3)
    for i in range(3):
        views.create_view(conn, f"v{i}", FLT)
    with pytest.raises(views.ViewError) as exc:
        views.create_view(conn, "v3", FLT)
    assert exc.value.code == "too_many_views"


def test_delete_missing(conn):
    with pytest.raises(views.ViewError) as exc:
        views.delete_view(conn, 99)
    assert exc.value.code == "not_found"


def test_failed_insert_leaves_no_open_transaction(conn):
    with pytest.raises(views.ViewError):
        views.create_view(conn, "a", FLT, "bad")
    views.create_view(conn, "a", FLT)
    assert not conn.in_transaction
    with pytest.raises(sqlite3.Error):
        conn.execute("INSERT INTO saved_view(id) VALUES (NULL)")  # NOT NULL columns: schema intact
