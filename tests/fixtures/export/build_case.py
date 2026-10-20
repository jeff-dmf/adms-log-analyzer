"""Builds the synthetic case used by the export tests. Every value below is fictitious.

``SENSITIVE`` lists, per redaction category, the values planted in the fixture; the 7.6-lite scan
asserts that none survives an anonymized export and that turning a category off lets its values
through.
"""

from __future__ import annotations

import json
from pathlib import Path

from dmslog.store.db import create_case_db
from dmslog.store.events import parse_log_time

GUID_A = "3f2a9c1e-7b4d-4e8a-9c21-5d6e7f8a9b0c"
GUID_B = "a1b2c3d4-0000-4aaa-8bbb-1234567890ab"

SENSITIVE: dict[str, list[str]] = {
    "guid": [GUID_A, GUID_B],
    "ticket": ["INC0045123", "PROB0077"],
    "gid": ["1234567890123456"],
    "mrid": ["CREW7788"],
    "plate": ["QRS4T56", "ABC-1234"],
    "host": ["dmsprod01.acme-grid.example.com", "ACMEHOST01"],
    "ip": ["10.20.30.40"],
    "user": ["jsilva", "dnaApp_acmeprod"],
    "url": ["https://esb.acme-grid.example.com:8443/Services/Wfm.svc"],
    "path": ["C:\\SchneiderElectricData\\acme\\Log\\x.log"],
    "email": ["maria.souza@acme-grid.example.com"],
    "phone": ["+55 21 99876-5432", "(21) 98765-4321"],
    "person": ["Maria Souza", "Rua das Acacias 45"],
}
USER_TERMS = ["ACME", "SUBALPHA"]
PAYLOAD_XML = (
    "<SendWorksRequest>\n"
    "  <Header><MessageID>" + GUID_B + "</MessageID></Header>\n"
    "  <Customer>\n"
    "    <Name>Maria Souza</Name>\n"
    "    <Address>Rua das Acacias 45</Address>\n"
    "    <Phone>(21) 98765-4321</Phone>\n"
    "  </Customer>\n"
    "</SendWorksRequest>"
)
BASE = "2030-10-15 10:00:00.000"


def _ts(offset_ms: int) -> int:
    return parse_log_time(BASE) + offset_ms


def build_export_case(path: Path) -> Path:
    """Create ``path`` (a case database) and return it."""
    conn = create_case_db(path)
    with conn:
        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [
                ("case_id", "export-fixture"),
                ("name", "ACME SUBALPHA zxqvaultn"),
                ("pack_version", "test-pack"),
            ],
        )
        conn.executemany(
            "INSERT INTO collection(id, kind, path, ers_guid, ers_ts, ers_variant, ers_group, machine,"
            " added_at) VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (
                    1,
                    "ers_zip",
                    "ers-enterprise.zip",
                    GUID_A,
                    "20301015095900",
                    "enterprise",
                    1,
                    "ACMEHOST01",
                    "2030-10-15",
                ),
                (
                    2,
                    "ers_zip",
                    "ers-sensitive.zip",
                    GUID_A,
                    "20301015095900",
                    "sensitive",
                    1,
                    "ACMEHOST01",
                    "2030-10-15",
                ),
            ],
        )
        conn.executemany(
            "INSERT INTO stream(id, collection_group, family, process, pid, account, sensitive)"
            " VALUES (?,?,?,?,?,?,?)",
            [
                (1, 1, "AWFM", "WfmAdapter", 4001, "dnaApp_acmeprod", 0),
                (2, 1, "AWFM", "WfmAdapter", 4001, "dnaApp_acmeprod", 1),
            ],
        )
        conn.executemany(
            "INSERT INTO source(id, collection_id, stream_id, member, file_name, envelope, status, n_entries)"
            " VALUES (?,?,?,?,?,?,?,?)",
            [
                (1, 1, 1, "Log/WfmAdapter_4001.log", "WfmAdapter_4001.log", "adms", "ready", 6),
                (
                    2,
                    2,
                    2,
                    "Log/WfmAdapter_4001.sensitive.log",
                    "WfmAdapter_4001.sensitive.log",
                    "adms",
                    "ready",
                    1,
                ),
            ],
        )
        conn.execute(
            "INSERT INTO template(id, code, family, class, sev, pattern, fts, def_version)"
            " VALUES (1, 'WFM-081', 'AWFM', 'error', 2, 'x', 1, 'v')"
        )
        stack = "   at Acme.Dms.Wfm.Send()\n   at System.Net.Http.HttpClient.Send()"
        entries = [
            # id, source, line, nlines, ts offset, tid, level, sev, tpl, msg, cont
            (1, 1, 10, 1, 0, 0x44C4, 7, 4, 0, f"Start SendWorks incident INC0045123 msgId {GUID_A}", None),
            (
                2,
                1,
                11,
                1,
                20,
                0x44C4,
                5,
                4,
                0,
                "Connecting to https://esb.acme-grid.example.com:8443/Services/Wfm.svc host: "
                "dmsprod01.acme-grid.example.com ip 10.20.30.40",
                None,
            ),
            (
                3,
                1,
                12,
                3,
                40,
                0x44C4,
                5,
                4,
                0,
                "Request by user: jsilva account dnaApp_acmeprod contact maria.souza@acme-grid.example.com "
                "phone +55 21 99876-5432 and (21) 98765-4321",
                stack,
            ),
            (
                4,
                1,
                15,
                1,
                60,
                0x44C4,
                5,
                4,
                0,
                "Crew mRID: CREW7788 GID: 1234567890123456 vehicle QRS4T56 and ABC-1234 problem PROB0077 "
                "log C:\\SchneiderElectricData\\acme\\Log\\x.log",
                None,
            ),
            (
                5,
                1,
                16,
                1,
                90,
                0x44C4,
                3,
                2,
                1,
                "SendWorks failed with code WFM-081: ACME SUBALPHA timeout on \\\\ACMEHOST01\\share",
                None,
            ),
            (6, 1, 17, 8, 120, 0x44C5, 7, 4, 0, "Content being sent to CRS", PAYLOAD_XML),
            (7, 2, 3, 9, 125, 0x44C5, 7, 4, 0, "Request received", PAYLOAD_XML),
        ]
        for entry_id, source, line, nlines, offset, tid, level, sev, tpl, msg, cont in entries:
            conn.execute(
                "INSERT INTO entry(id, source_id, line_no, byte_off, nlines, ts, pid, tid, level, sev,"
                " tpl_id, flags, fields, msg) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (entry_id, source, line, entry_id * 100, nlines, _ts(offset), 4001, tid, level, sev, tpl,
                 2 if cont else 0, None, msg),
            )  # fmt: skip
            if cont:
                conn.execute("INSERT INTO entry_body(entry_id, cont) VALUES (?, ?)", (entry_id, cont))
        message = (
            "System.TimeoutException: timeout calling https://esb.acme-grid.example.com:8443/Services/Wfm.svc"
            " for user: jsilva (INC0045123)"
        )
        detail = json.dumps({"error_codes": ["WFM-081"], "exception": message})
        conn.execute(
            "INSERT INTO event(id, stream_id, kind, name, key, tid, start_entry_id, end_entry_id, ts_start,"
            " ts_end, duration_ms, status, sev, n_entries, detail)"
            " VALUES (1, 1, 'operation', 'SendWorks', ?, 17604, 1, 5, ?, ?, 90, 'fail', 2, 5, ?)",
            (GUID_A, _ts(0), _ts(90), detail),
        )
        conn.execute("UPDATE entry SET event_id = 1 WHERE id BETWEEN 1 AND 5")
        # The payload span as the payload table records it (offsets into msg + newline + cont).
        start = len("Request received") + 1
        conn.execute(
            "INSERT INTO payload(entry_id, role, direction, peer, operation, fmt, char_off, char_len)"
            " VALUES (7, 'request', 'in', 'WFM', 'SendWorks', 'xml', ?, ?)",
            (start, len(PAYLOAD_XML)),
        )
        conn.execute(
            "INSERT INTO bookmark(collection_id, member, line_no, entry_id, note, created_at)"
            " VALUES (1, 'Log/WfmAdapter_4001.log', 16, 5, 'ACME called about INC0045123', '2030-10-15')"
        )
        conn.execute("INSERT INTO entry_fts(entry_fts) VALUES('rebuild')")
    conn.close()
    return path
