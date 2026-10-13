"""Read side of the collections of a case (decision U21, ARQUITETURA 5 and 10).

A case groups COLLECTIONS: one collection group per ERS capture (its Enterprise zip and its Sensitive zip,
added in any order), per installation ``Log`` folder or per loose file. Each group has its own server, GUID,
capture time and clock; the API and the UI name a group by its id (``collection.ers_group``, the id of its
first collection) and number them ``C1``, ``C2``... in that order.

Times of different groups come from different clocks (U6: shown as logged, no conversion, and
``clock_offset_ms`` stays 0 until task 9.15), so the merge by ``ts`` across groups is only an APPROXIMATE
order: ``ordering`` says so whenever the case has more than one group.

``GET /api/cases/<case>/collections``::

    {"case", "ordering": "exact" | "approximate", "collections": [<group>]}

    <group> = {"id", "group", "number", "tag", "kind", "guid", "capture", "server", "machine", "system",
               "label", "variants", "missing", "clock_offset_ms", "status", "sources", "listed",
               "records", "first_ts", "last_ts", "added_at", "members": [<member>]}
    <member> = {"id", "kind", "variant", "name", "label", "added_at", "status", "sources", "listed",
                "records", "first_ts", "last_ts"}

``kind`` is ``ers`` (a zip named like an ERS), ``zip`` (another zip), ``folder`` or ``file``. ``missing`` is
the half of an ERS capture the case does not hold yet (``enterprise`` or ``sensitive``), else null.
``server`` is the machine name when known, else the zone (``system``). ``status`` is ``indexing`` while a
source is pending or being indexed, ``error`` when a source failed, else ``ready``. ``name`` is the file or
folder name only (never the full local path).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .query import PAGE_TIMEOUT_S, read_guard

if TYPE_CHECKING:
    import sqlite3

_MEMBER_SQL = (
    "SELECT c.id, c.kind, c.path, c.ers_guid, c.ers_ts, c.ers_variant, coalesce(c.ers_group, c.id),"
    " c.machine, c.system, c.label, c.added_at, c.clock_offset_ms,"
    " count(s.id), coalesce(sum(s.status <> 'skipped'), 0),"
    " coalesce(sum(s.status IN ('pending', 'indexing')), 0), coalesce(sum(s.status = 'error'), 0),"
    " coalesce(sum(s.n_entries), 0), min(s.first_ts), max(s.last_ts)"
    " FROM collection c LEFT JOIN source s ON s.collection_id = c.id"
    " GROUP BY c.id ORDER BY coalesce(c.ers_group, c.id), c.id"
)


def _name(path: str) -> str:
    """Last component of a recorded path, whatever its separator (paths come from Windows and Linux)."""
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _status(pending: int, failed: int) -> str:
    return "indexing" if pending else ("error" if failed else "ready")


def _min(values: list[int | None]) -> int | None:
    found = [v for v in values if v is not None]
    return min(found) if found else None


def _max(values: list[int | None]) -> int | None:
    found = [v for v in values if v is not None]
    return max(found) if found else None


def list_collections(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The collection groups of the case with their members and counts, in group order."""
    with read_guard(conn, PAGE_TIMEOUT_S):
        rows = conn.execute(_MEMBER_SQL).fetchall()
    groups: dict[int, dict[str, Any]] = {}
    for (cid, kind, path, guid, stamp, variant, group, machine, system, label, added, offset, listed, chosen,
         pending, failed, records, first_ts, last_ts) in rows:  # fmt: skip
        is_ers = kind == "ers_zip" and bool(guid) and bool(stamp)
        member = {
            "id": cid, "kind": "ers" if is_ers else ("zip" if kind == "ers_zip" else kind),
            "variant": variant if is_ers else None, "name": _name(path), "label": label, "added_at": added,
            "status": _status(pending, failed), "sources": chosen, "listed": listed, "records": records,
            "first_ts": first_ts, "last_ts": last_ts,
        }  # fmt: skip
        info = groups.get(group)
        if info is None:
            info = groups[group] = {
                "id": group, "group": group, "number": 0, "tag": "", "kind": member["kind"],
                "guid": guid if is_ers else None, "capture": stamp if is_ers else None,
                "server": None, "machine": machine, "system": system, "label": label,
                "variants": [], "missing": None, "clock_offset_ms": offset or 0, "status": "ready",
                "sources": 0, "listed": 0, "records": 0, "first_ts": None, "last_ts": None,
                "added_at": added, "members": [],
            }  # fmt: skip
        info["members"].append(member)
        info["machine"] = info["machine"] or machine
        info["system"] = info["system"] or system
    out = list(groups.values())
    for number, info in enumerate(out, 1):
        members = info["members"]
        info["number"], info["tag"] = number, f"C{number}"
        info["server"] = info["machine"] or info["system"]
        info["variants"] = sorted(
            {m["variant"] for m in members if m["variant"]}, key=lambda v: v != "enterprise"
        )
        if info["kind"] == "ers":
            held = set(info["variants"])
            info["missing"] = next((v for v in ("enterprise", "sensitive") if v not in held), None)
        info["sources"] = sum(m["sources"] for m in members)
        info["listed"] = sum(m["listed"] for m in members)
        info["records"] = sum(m["records"] for m in members)
        info["first_ts"] = _min([m["first_ts"] for m in members])
        info["last_ts"] = _max([m["last_ts"] for m in members])
        statuses = {m["status"] for m in members}
        info["status"] = (
            "indexing" if "indexing" in statuses else ("error" if "error" in statuses else "ready")
        )
    return out


def ordering(groups: list[dict[str, Any]]) -> str:
    """``approximate`` when the case merges records of more than one clock (more than one group)."""
    return "approximate" if len(groups) > 1 else "exact"


def group_tags(conn: sqlite3.Connection) -> dict[int, str]:
    """``{group id: "C<n>"}`` in the numbering of :func:`list_collections`."""
    groups = [
        r[0] for r in conn.execute("SELECT DISTINCT coalesce(ers_group, id) FROM collection ORDER BY 1")
    ]
    return {g: f"C{n}" for n, g in enumerate(groups, 1)}


def group_exists(conn: sqlite3.Connection, group: int) -> bool:
    return (
        conn.execute("SELECT 1 FROM collection WHERE ers_group = ? LIMIT 1", (group,)).fetchone() is not None
    )
