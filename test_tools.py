"""
Tests for get_ticket, log_work, stale-active/WIP reporting, and
ready-to-unblock detection. Runs against a temp state file via the
DEVFLOW_STATE_FILE override; the ProjectHub bridge is disabled.

Run: python3 test_tools.py
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Point the state store at a temp file BEFORE importing server/state_store.
_tmpdir = tempfile.mkdtemp(prefix="devflow_test_")
os.environ["DEVFLOW_STATE_FILE"] = str(Path(_tmpdir) / "state.json")

import server  # noqa: E402

server._bridge = False  # disable ProjectHub side-effects (falsy, not None)

DAY_MS = 86_400_000
NOW_MS = int(time.time() * 1000)


def write_state(state):
    with open(os.environ["DEVFLOW_STATE_FILE"], "w") as f:
        json.dump(state, f)


def make_ticket(tid, title, status="backlog", blocked_by=None, blocks=None,
                created_days_ago=0, log_days_ago=None):
    created = NOW_MS - created_days_ago * DAY_MS
    log_t = NOW_MS - (log_days_ago if log_days_ago is not None else created_days_ago) * DAY_MS
    return {
        "id": tid,
        "title": title,
        "why": f"why {tid}",
        "desc": "",
        "projectId": "proj-test",
        "priority": "medium",
        "status": status,
        "blockedBy": blocked_by or [],
        "blocksTickets": blocks or [],
        "created": created,
        "log": [{"t": log_t, "msg": "Ticket created"}],
    }


def base_state(tickets):
    return {
        "projects": [{"id": "proj-test", "name": "test_project", "goal": "testing", "color": "#4f7cff"}],
        "tickets": tickets,
        "next_id": 100,
    }


passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


# ── get_ticket ────────────────────────────────────────────────────────────

print("get_ticket:")
write_state(base_state([
    make_ticket("T-001", "Blocker A", status="done", blocks=["T-003"]),
    make_ticket("T-002", "Blocker B", status="active", blocks=["T-003"]),
    make_ticket("T-003", "Blocked child", status="blocked", blocked_by=["T-001", "T-002"]),
]))

r = server.get_ticket("T-999")
check("unknown ticket returns error", "error" in r)

r = server.get_ticket("T-003")
check("returns project name", r.get("project") == "test_project")
check("resolves blockers to titles", {d["title"] for d in r["blocked_by"]} == {"Blocker A", "Blocker B"})
check("all_blockers_resolved false while B active", r["all_blockers_resolved"] is False)
check("log entries have readable time", "time" in r["ticket"]["log"][0])

r = server.get_ticket("T-001")
check("resolves blocks side", r["blocks"][0]["id"] == "T-003")

# ── log_work ──────────────────────────────────────────────────────────────

print("log_work:")
r = server.log_work("T-003", "   ")
check("empty note rejected", "error" in r)

r = server.log_work("T-999", "note")
check("unknown ticket rejected", "error" in r)

r = server.log_work("T-003", "Parser done; tests fail on edge case X. Resume at foo.py:42")
check("append succeeds", r.get("success") is True and r["log_entries"] == 2)

persisted = server.get_ticket("T-003")["ticket"]["log"]
check("note persisted to disk", persisted[-1]["msg"].startswith("Parser done"))

# ── ready-to-unblock on update_ticket_status ──────────────────────────────

print("update_ticket_status ready_to_unblock:")
r = server.update_ticket_status("T-002", "done")
check("done transition succeeds", r.get("success") is True)
check("reports newly unblocked ticket", [t["id"] for t in r.get("ready_to_unblock", [])] == ["T-003"])
check("hint present", "hint" in r)

r = server.update_ticket_status("T-003", "active")
check("non-done transition has no unblock list", "ready_to_unblock" not in r)

# a done transition that unblocks nothing
write_state(base_state([
    make_ticket("T-010", "Standalone", status="active"),
    make_ticket("T-011", "Still waiting", status="blocked", blocked_by=["T-010", "T-012"]),
    make_ticket("T-012", "Other blocker", status="backlog", blocks=["T-011"]),
]))
r = server.update_ticket_status("T-010", "done")
check("partial blockers -> not reported", "ready_to_unblock" not in r)

# ── get_status_report: stale actives, WIP, ready-to-unblock ───────────────

print("get_status_report:")
tickets = [
    make_ticket("T-020", "Fresh active", status="active", log_days_ago=1),
    make_ticket("T-021", "Stale active", status="active", created_days_ago=90, log_days_ago=40),
    make_ticket("T-022", "Blocker done", status="done", blocks=["T-023"]),
    make_ticket("T-023", "Ready one", status="blocked", blocked_by=["T-022"]),
    make_ticket("T-024", "External block", status="blocked"),  # empty blockedBy
]
write_state(base_state(tickets))
r = server.get_status_report()
check("stale active flagged", [t["id"] for t in r["stale_active"]] == ["T-021"])
check("stale days_idle computed", r["stale_active"][0]["days_idle"] == 40)
check("fresh active not flagged", all(t["id"] != "T-020" for t in r["stale_active"]))
check("ready_to_unblock lists T-023", [t["id"] for t in r["ready_to_unblock"]] == ["T-023"])
check("externally blocked excluded", all(t["id"] != "T-024" for t in r["ready_to_unblock"]))
check("no WIP warning at 2 active", r["wip_warning"] is None)

# push active count over the limit
many = tickets + [make_ticket(f"T-{30+i}", f"Extra {i}", status="active") for i in range(10)]
write_state(base_state(many))
r = server.get_status_report()
check("WIP warning above limit", r["wip_warning"] is not None and "12 active" in r["wip_warning"])

# log_work resets staleness
server.log_work("T-021", "picked this back up")
r = server.get_status_report()
check("log_work clears stale flag", all(t["id"] != "T-021" for t in r["stale_active"]))

# ── bridge: timer cap, cancel-on-demote, cancel-on-delete ─────────────────
#
# The ProjectHub bridge is intentionally not shipped in this repo (see
# README: "ProjectHub bridge") — it's tightly coupled to a schema this repo
# doesn't define. This section only runs if you've dropped your own
# projecthub_bridge.py alongside server.py; otherwise it's skipped, not
# failed, matching the bridge's own "silently disabled" behavior.

try:
    from projecthub_bridge import ProjectHubBridge
except ImportError:
    ProjectHubBridge = None

if ProjectHubBridge is None:
    print("bridge timers: SKIP (projecthub_bridge.py not present — this repo doesn't ship it)")
else:
    print("bridge timers:")
    import sqlite3

    bridge_db = str(Path(_tmpdir) / "projecthub.db")
    conn = sqlite3.connect(bridge_db)
    conn.executescript("""
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, description TEXT, classification TEXT,
            status TEXT, updated_at TEXT
        );
        CREATE TABLE time_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER, date TEXT, hours REAL, description TEXT,
            is_billable INTEGER, is_timer_entry INTEGER,
            timer_start TEXT, timer_end TEXT,
            created_at TEXT DEFAULT (datetime('now')), updated_at TEXT
        );
    """)
    conn.execute("INSERT INTO projects (name, status) VALUES ('test_project', 'active')")
    conn.commit()
    conn.close()

    bridge = ProjectHubBridge(bridge_db, max_timer_hours=8.0)

    def open_timer_row(desc):
        c = sqlite3.connect(bridge_db)
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT * FROM time_entries WHERE description = ? ORDER BY id DESC LIMIT 1", (desc,)
        ).fetchone()
        c.close()
        return row

    def backdate_timer(desc, days):
        c = sqlite3.connect(bridge_db)
        c.execute(
            "UPDATE time_entries SET timer_start = datetime('now', ?) WHERE description = ? AND timer_end IS NULL",
            (f"-{days} days", desc),
        )
        c.commit()
        c.close()

    # cap: a 3-day-old timer bills 8h, not 72h
    bridge.start_time_entry("test_project", "cap check")
    backdate_timer("cap check", 3)
    bridge.stop_time_entry("test_project", "cap check")
    row = open_timer_row("cap check")
    check("stop caps elapsed at max_timer_hours", row["hours"] == 8.0, f"hours={row['hours']}")

    # cancel: closes without billing
    bridge.start_time_entry("test_project", "cancel check")
    backdate_timer("cancel check", 3)
    bridge.cancel_time_entry("test_project", "cancel check")
    row = open_timer_row("cancel check")
    check("cancel closes timer", row["timer_end"] is not None)
    check("cancel bills nothing", row["hours"] == 0.01, f"hours={row['hours']}")

    # e2e: demotion and deletion cancel timers through server side-effects
    server._bridge = bridge
    write_state(base_state([]))

    t1 = server.add_ticket("Demote timer check", "why", "test_project", status="active")["ticket"]
    check("activation opens timer", open_timer_row("Demote timer check")["timer_end"] is None)
    server.update_ticket_status(t1["id"], "backlog")
    row = open_timer_row("Demote timer check")
    check("demotion cancels timer", row["timer_end"] is not None and row["hours"] == 0.01)

    t2 = server.add_ticket("Delete timer check", "why", "test_project", status="active")["ticket"]
    server.delete_ticket(t2["id"])
    row = open_timer_row("Delete timer check")
    check("deleting active ticket cancels timer", row["timer_end"] is not None and row["hours"] == 0.01)

    server._bridge = False

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
