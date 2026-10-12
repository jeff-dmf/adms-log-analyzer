# ruff: noqa: E501
"""Copy/export of the view: lists, original lines, CSV (RFC 4180), events CSV and the streamed route."""

from __future__ import annotations

import csv
import http.client
import io
import json
import tracemalloc

import pytest
from query_helpers import build_case, entry_rows
from test_api import client, server  # noqa: F401  (fixtures)

from dmslog.report import viewexport as ve
from dmslog.store.db import connect, create_case_db
from dmslog.store.events import EventFilter

BOM_BYTES = b"\xef\xbb\xbf"


def parse_csv(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text, newline="")))


# -- csv cells -----------------------------------------------------------------------------------


def test_csv_cell_quotes_per_rfc4180():
    assert ve.csv_cell("plain") == "plain"
    assert ve.csv_cell("a,b") == '"a,b"'
    assert ve.csv_cell('say "hi"') == '"say ""hi"""'
    assert ve.csv_cell("two\nlines") == '"two\nlines"'
    assert ve.csv_cell("cr\rlf") == '"cr\rlf"'
    assert ve.csv_cell(None) == "" and ve.csv_cell(12) == "12" and ve.csv_cell(1.5) == "1.5"
    assert ve.csv_cell("Jos\u00e9 \u4e2d\u6587 \U0001f600") == "Jos\u00e9 \u4e2d\u6587 \U0001f600"
    assert ve.csv_row(["a", "b,c", None]) == 'a,"b,c",\r\n'


@pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r"])
def test_formula_injection_gets_an_apostrophe(lead):
    cell = ve.csv_cell(lead + "SUM(A1)")
    assert cell.lstrip('"').startswith("'" + lead)
    assert ve.guard_formula(lead + "x") == "'" + lead + "x"


def test_formula_guard_leaves_safe_text_and_numbers_alone():
    assert ve.guard_formula("normal =x") == "normal =x"
    assert ve.csv_cell(-5) == "-5"
    assert ve.csv_cell("=1+1,2") == '"\'=1+1,2"'


# -- lists ---------------------------------------------------------------------------------------


def test_format_list_separators_quotes_and_unique():
    values = ["a", "b", "a", "c'd"]
    assert ve.format_list(values) == "a\nb\na\nc'd"
    assert ve.format_list(values, "comma") == "a,b,a,c'd"
    assert ve.format_list(values, "comma", "single", unique=True) == "'a','b','c''d'"
    assert ve.format_list(['x"y'], "newline", "double") == '"x""y"'
    assert ve.format_list([], "comma") == ""


def test_parse_request_validates():
    with pytest.raises(ve.ViewExportError):
        ve.parse_request({"kind": "nope"})
    with pytest.raises(ve.ViewExportError):
        ve.parse_request({"kind": "values"})  # neither column nor key
    with pytest.raises(ve.ViewExportError):
        ve.parse_request({"kind": "values", "column": "tid", "key": "xkey"})
    with pytest.raises(ve.ViewExportError):
        ve.parse_request({"kind": "values", "column": "fields.a b"})
    with pytest.raises(ve.ViewExportError):
        ve.parse_request({"kind": "view_csv", "columns": ["ts", "id; DROP TABLE entry"]})
    for bad in (0, -1, ve.MAX_ROWS_CAP + 1, True, "5"):
        with pytest.raises(ve.ViewExportError):
            ve.parse_request({"kind": "lines", "max_rows": bad})
    with pytest.raises(ve.ViewExportError):
        ve.parse_request({"kind": "values", "column": "tid", "separator": "tab"})
    req = ve.parse_request({"kind": "view_csv", "columns": ["ts", "ts", "fields.op"], "bom": True})
    assert req.columns == ("ts", "fields.op") and req.max_rows == ve.DEFAULT_MAX_ROWS and req.bom


# -- over a synthetic case -----------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    build_case(tmp_path / "case.sqlite")
    connection = connect(tmp_path / "case.sqlite", readonly=True)
    yield connection
    connection.close()


def collect(chunks):
    return "".join(chunks)


def test_values_of_a_field_column_over_the_filter(conn):
    req = ve.parse_request(
        {
            "kind": "values",
            "column": "fields.op",
            "unique": True,
            "separator": "comma",
            "filter": {"source": [1]},
        }
    )
    values = collect(ve.stream_values(conn, req)).split(",")
    assert sorted(values) == sorted({"timeout", "connected", "GetIncidents", "SendWorks", "retry", "crew"})


def test_values_without_unique_keep_every_row_and_skip_empty(conn):
    req = ve.parse_request({"kind": "values", "column": "tid", "filter": {"source": [1]}})
    lines = collect(ve.stream_values(conn, req)).split("\n")
    expected = [r for r in entry_rows() if r["source_id"] == 1 and r["tid"] is not None]
    assert len(lines) == len(expected) and all(v.startswith("0x") for v in lines)


def test_values_of_a_key_type(conn, tmp_path):
    rw = connect(tmp_path / "case.sqlite")
    with rw:
        rw.executemany(
            "INSERT INTO entry_key(key_type, value, entry_id) VALUES (?,?,?)",
            [("xkey", "G1", 10), ("xkey", "G2", 11), ("xkey", "G1", 12), ("msgid", "M1", 10)],
        )
    rw.close()
    req = ve.parse_request(
        {"kind": "values", "key": "xkey", "unique": True, "quote": "single", "separator": "comma"}
    )
    assert collect(ve.stream_values(conn, req)) == "'G1','G2'"
    req = ve.parse_request({"kind": "values", "key": "xkey"})
    assert collect(ve.stream_values(conn, req)) == "G1\nG2\nG1"


def test_lines_keep_the_continuation_and_the_header(conn):
    req = ve.parse_request({"kind": "lines", "filter": {"source": [1]}, "max_rows": 40})
    text = collect(ve.stream_lines(conn, req))
    first = next(r for r in entry_rows() if r["source_id"] == 1 and r["cont"])
    assert first["msg"] + "\n" + first["cont"] in text
    assert text.endswith("\n") and "[PID=4001]" in text


def test_lines_cut_a_huge_record_with_a_notice(tmp_path):
    path = tmp_path / "big.sqlite"
    rows = build_case(path, rows=None)
    rw = connect(path)
    with rw:
        rw.execute("UPDATE entry_body SET cont = ? WHERE entry_id = (SELECT min(entry_id) FROM entry_body)",
                   ("x" * (ve.MAX_RECORD_CHARS + 50),))  # fmt: skip
    rw.close()
    ro = connect(path, readonly=True)
    text = collect(ve.stream_lines(ro, ve.parse_request({"kind": "lines"})))
    ro.close()
    assert "[... 50 more characters of this record are not exported]" in text
    assert len(rows) > 0


def test_view_csv_is_rfc4180_with_fields_and_template(conn):
    req = ve.parse_request(
        {
            "kind": "view_csv",
            "columns": ["ts", "tid", "level", "template", "source", "msg", "fields.op"],
            "filter": {"source": [1]},
            "max_rows": 30,
        }  # fmt: skip
    )
    text = collect(ve.stream_view_csv(conn, req))
    assert text.endswith("\r\n")
    table = parse_csv(text)
    assert table[0] == ["Record", "Time", "TID", "Level", "Template", "Source", "Message", "op"]
    assert len(table) == 31 and all(len(r) == 8 for r in table)
    assert table[1][5] == "a.log" and table[1][1].startswith("1970-01-01 00:16:")
    assert {r[4] for r in table[1:]} <= {"", "TST-001", "TST-002"}
    assert all(r[7] for r in table[1:])


def test_view_csv_escapes_formulas_commas_quotes_and_newlines(tmp_path):
    rows = [
        dict(source_id=1, ts=1000, tid=None, pid=1, level=3, sev=3, tpl_id=0, flags=0, cont=None,
             fields=json.dumps({"op": "=cmd|' /C calc'!A0"}), msg=m)
        for m in ['=HYPERLINK("http://x","y")', "a,b", 'q"uote', "+55 11", "plain \u00e9\u4e2d"]
    ]  # fmt: skip
    path = tmp_path / "f.sqlite"
    build_case(path, rows)
    ro = connect(path, readonly=True)
    req = ve.parse_request({"kind": "view_csv", "columns": ["msg", "fields.op"], "filter": {"source": [1]}})
    text = collect(ve.stream_view_csv(ro, req))
    ro.close()
    table = parse_csv(text)
    assert [r[1] for r in table[1:]] == [
        "'=HYPERLINK(\"http://x\",\"y\")", "a,b", 'q"uote', "'+55 11", "plain \u00e9\u4e2d",
    ]  # fmt: skip
    assert all(r[2].startswith("'=") for r in table[1:])
    assert text.count("\r\n") == 6


def test_bom_is_optional(conn):
    plain = collect(ve.stream_view_csv(conn, ve.parse_request({"kind": "view_csv", "columns": ["ts"]})))
    with_bom = collect(
        ve.stream_view_csv(conn, ve.parse_request({"kind": "view_csv", "columns": ["ts"], "bom": True}))
    )
    assert not plain.startswith(ve.BOM) and with_bom == ve.BOM + plain


def test_max_rows_and_totals(conn):
    total = len(entry_rows())
    assert ve.entry_total(conn, None, 10) == 11
    assert ve.entry_total(conn, None, total + 5) == total
    req = ve.parse_request({"kind": "view_csv", "columns": ["ts"], "max_rows": 7})
    assert len(parse_csv(collect(ve.stream_view_csv(conn, req)))) == 8


def test_events_csv(conn, tmp_path):
    rw = connect(tmp_path / "case.sqlite")
    with rw:
        rw.execute("UPDATE event SET key = 'K-1', start_entry_id = 10, duration_ms = 250, ts_end = ts_start + 250,"
                   " detail = '{\"op_key\": \"=OPK\"}' WHERE id = 1")  # fmt: skip
        rw.execute("UPDATE event SET start_entry_id = 15 WHERE id = 2")
    rw.close()
    req = ve.parse_request({"kind": "events_csv"})
    table = parse_csv(collect(ve.stream_events_csv(conn, EventFilter(kinds=("operation",)), req)))
    assert table[0][:5] == ["Event", "Operation", "Kind", "Status", "Duration (ms)"]
    assert len(table) == 3
    one = dict(zip(table[0], table[1], strict=True))
    assert one["Operation"] == "GetIncidents" and one["Status"] == "fail" and one["Duration (ms)"] == "250"
    assert one["Key"] == "K-1" and one["Operation key"] == "'=OPK" and one["Source"] == "a.log"
    assert one["Start"] == "1970-01-01 00:16:40.000"
    only_ok = parse_csv(collect(ve.stream_events_csv(conn, EventFilter(statuses=("ok",)), req)))
    assert len(only_ok) == 2 and only_ok[1][1] == "SendWorks"


def test_events_csv_pages_through_every_event(tmp_path):
    path = tmp_path / "ev.sqlite"
    conn = create_case_db(path)
    with conn:
        conn.execute(
            "INSERT INTO stream(id, collection_group, family, process, pid, sensitive) VALUES (1,1,'AWFM','P',1,0)"
        )
        conn.executemany(
            "INSERT INTO event(stream_id, kind, name, ts_start, duration_ms, status, sev) VALUES (1,'operation','Op',?,?,'ok',4)",
            [(1_000_000 + i // 3, i) for i in range(2500)],
        )  # fmt: skip
    conn.close()
    ro = connect(path, readonly=True)
    req = ve.parse_request({"kind": "events_csv", "max_rows": 2400})
    table = parse_csv(collect(ve.stream_events_csv(ro, EventFilter(kinds=("operation",)), req)))
    assert len(table) == 2401 and len({r[0] for r in table[1:]}) == 2400
    assert ve.event_total(ro, EventFilter(kinds=("operation",)), 2400) == 2401
    ro.close()


def test_streaming_keeps_memory_flat(tmp_path):
    path = tmp_path / "many.sqlite"
    conn = create_case_db(path)
    n = 60_000
    with conn:
        conn.execute(
            "INSERT INTO collection(id, kind, path, added_at) VALUES (1,'ers_zip','c.zip','2026-01-01')"
        )
        conn.execute(
            "INSERT INTO stream(id, collection_group, family, process, pid, sensitive) VALUES (1,1,'AWFM','P',1,0)"
        )
        conn.execute(
            "INSERT INTO source(id, collection_id, stream_id, member, file_name, envelope) VALUES (1,1,1,'Log/a.log','a.log','adms')"
        )
        conn.executemany(
            "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, pid, tid, level, sev, tpl_id, flags, fields, msg)"
            " VALUES (?,1,?,?,1,?,1,1,3,3,0,0,NULL,?)",
            ((i, i, i * 10, 1_000_000 + i, f"synthetic record {i} " + "y" * 60) for i in range(1, n + 1)),
        )  # fmt: skip
    conn.close()
    ro = connect(path, readonly=True)
    req = ve.parse_request({"kind": "view_csv", "columns": ["ts", "source", "msg"], "max_rows": n})
    tracemalloc.start()
    size = rows = 0
    for chunk in ve.stream_view_csv(ro, req):
        size += len(chunk)
        rows += chunk.count("\r\n")
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    ro.close()
    assert rows == n + 1 and size > 5_000_000
    assert peak < size // 8  # constant-size working set, not the whole export


# -- the route -----------------------------------------------------------------------------------


@pytest.fixture
def demo(server, tmp_path):  # noqa: F811
    folder = server.api.store.root / "Demo"
    folder.mkdir(parents=True)
    rows = build_case(folder / "case.sqlite")
    return rows


def post_raw(client, body):  # noqa: F811
    conn = http.client.HTTPConnection("127.0.0.1", client.server.port, timeout=15)
    try:
        conn.request(
            "POST",
            "/api/export/view",
            body=json.dumps(body).encode("utf-8"),
            headers={"Cookie": client.cookie, "Origin": f"http://127.0.0.1:{client.server.port}",
                     "Content-Type": "application/json"},
        )  # fmt: skip
        response = conn.getresponse()
        return response.status, response.read(), {k.lower(): v for k, v in response.getheaders()}
    finally:
        conn.close()


def test_route_streams_csv_with_truncation_headers(client, demo):  # noqa: F811
    body = {"case": "Demo", "kind": "view_csv", "columns": ["ts", "msg"], "max_rows": 50, "bom": True}
    status, data, headers = post_raw(client, body)
    assert status == 200 and headers["content-type"] == "text/csv; charset=utf-8"
    assert headers["transfer-encoding"] == "chunked" and headers["cache-control"] == "no-store"
    assert headers["x-dmslog-truncated"] == "1" and headers["x-dmslog-rows"] == "50"
    assert data.startswith(BOM_BYTES)
    table = parse_csv(data.decode("utf-8-sig"))
    assert len(table) == 51 and table[0] == ["Record", "Time", "Message"]
    body["max_rows"] = 100_000
    status, data, headers = post_raw(client, body)
    assert headers["x-dmslog-truncated"] == "0" and headers["x-dmslog-rows"] == str(len(demo))
    assert len(parse_csv(data.decode("utf-8-sig"))) == len(demo) + 1


def test_route_values_lines_and_events(client, demo):  # noqa: F811
    status, data, headers = post_raw(
        client, {"case": "Demo", "kind": "values", "column": "pid", "unique": True, "separator": "comma"}
    )
    assert status == 200 and headers["content-type"] == "text/plain; charset=utf-8"
    assert sorted(data.decode().split(",")) == ["4001", "4002"]
    status, data, _ = post_raw(client, {"case": "Demo", "kind": "lines", "filter": {"source": [3]}})
    assert data.decode() == "".join(f"plain line {k}\n" for k in range(5))
    status, data, headers = post_raw(
        client, {"case": "Demo", "kind": "events_csv", "events": {"status": ["fail"], "kind": "operation"}}
    )
    table = parse_csv(data.decode())
    assert status == 200 and len(table) == 2 and table[1][1] == "GetIncidents"
    assert headers["x-dmslog-rows"] == "1"


def test_route_errors(client, demo):  # noqa: F811
    assert post_raw(client, {"case": "Demo", "kind": "zip"})[0] == 400
    assert post_raw(client, {"case": "Demo", "kind": "lines", "max_rows": 0})[0] == 400
    assert post_raw(client, {"case": "Demo", "kind": "lines", "filter": {"bogus": 1}})[0] == 400
    assert post_raw(client, {"case": "Demo", "kind": "lines", "filter": {"substr": "x"}})[0] == 400
    assert post_raw(client, {"case": "Demo", "kind": "events_csv", "events": {"from": "garbage"}})[0] == 400
    assert post_raw(client, {"case": "Nope", "kind": "lines"})[0] in (400, 404)
    status, data, _ = post_raw(client, {"case": "Demo", "kind": "lines", "filter": {"bogus": 1}})
    assert json.loads(data)["error"]
