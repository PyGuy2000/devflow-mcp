#!/usr/bin/env python3
"""Theme rollup: every way a ticket joins a theme, and every flag, has a case.

Standalone script like the other tests. The rollup is pure, so the fixtures
are plain dicts; one last check runs compute() against a temp config dir to
prove the config key is read.

    python3 test_adr_themes.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="devflow-themes-test-")
CONFIG_DIR = Path(_tmp) / "config"
CONFIG_DIR.mkdir()
os.environ["DEVFLOW_CONFIG_DIR"] = str(CONFIG_DIR)

from adr_themes import brief, compute, rollup  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


DAY = 86_400_000
NOW = 1_800_000_000_000


def tk(tid, status="backlog", project="app", title="x", why="", desc="", age_days=1, blocked_by=()):
    return {"id": tid, "status": status, "projectId": f"proj-{project}", "title": title, "why": why,
            "desc": desc, "blockedBy": list(blocked_by), "created": NOW - age_days * DAY,
            "log": [{"t": NOW - age_days * DAY, "msg": "x"}]}


registry = {
    "title_match_projects": ["app"],
    "themes": [
        {"id": "TH-01", "title": "linked", "verdict": "open", "kind": "theme", "ticket_projects": [], "ticket_title_match": None},
        {"id": "TH-02", "title": "by project", "verdict": "open", "kind": "theme", "ticket_projects": ["side"], "ticket_title_match": None},
        {"id": "TH-03", "title": "by title", "verdict": "open", "kind": "theme", "ticket_projects": [], "ticket_title_match": r"\bpudl\b"},
        {"id": "TH-04", "title": "by tag", "verdict": "open", "kind": "theme", "ticket_projects": [], "ticket_title_match": None},
        {"id": "TH-05", "title": "answered", "verdict": "addressed", "kind": "theme", "ticket_projects": ["done_proj"], "ticket_title_match": None},
        {"id": "TH-06", "title": "ops", "verdict": "open", "kind": "ongoing", "ticket_projects": ["done_proj"], "ticket_title_match": None},
    ],
    "buckets": [{"id": "PRJ-x", "title": "bucket"}],
    "adrs": {"app:ADR-001": ["TH-01", "TH-04"], "app:ADR-002": ["PRJ-x"], "app:ADR-003": ["TH-99"]},
}
index = {"adrs": [
    {"uid": "app:ADR-001", "status": "accepted", "tickets": ["T-1"], "orphanTickets": []},
    {"uid": "app:ADR-002", "status": "accepted", "tickets": [], "orphanTickets": []},
    {"uid": "app:ADR-003", "status": "accepted", "tickets": [], "orphanTickets": []},
    {"uid": "app:ADR-004", "status": "proposed", "tickets": [], "orphanTickets": []},
]}
state = {"tickets": [
    tk("T-1", "blocked", title="EPIC", blocked_by=["T-2"]),
    tk("T-2", "active"),
    tk("T-3", "done", project="side", age_days=40),
    tk("T-4", "backlog", title="Land the PUDL table"),
    tk("T-5", "backlog", project="other", title="PUDL elsewhere"),
    tk("T-6", "done", project="other", why="Part of TH-04."),
    tk("T-7", "done", project="done_proj"),
]}

r = rollup(registry, index, state, now_ms=NOW)
rows = {x["id"]: x for x in r["themes"]}

print("joins:")
check("ADR link + one hop down the EPIC", rows["TH-01"]["tickets_total"] == 2 and rows["TH-01"]["open_ticket_ids"] == ["T-1", "T-2"], rows["TH-01"])
check("ticket_projects", rows["TH-02"]["tickets_total"] == 1)
check("title match only inside title_match_projects", rows["TH-03"]["open_ticket_ids"] == ["T-4"], rows["TH-03"])
check("theme id in why, plus the secondary ADR's links", sorted(rows["TH-04"]["open_ticket_ids"]) == ["T-1", "T-2"] and rows["TH-04"]["tickets_total"] == 3, rows["TH-04"])
check("secondary ADR counts in total, not primary", rows["TH-04"]["adrs_primary"] == 0 and rows["TH-04"]["adrs_total"] == 1)

print("flags:")
check("needs_verdict when every ticket is done and verdict open", "needs_verdict" in rows["TH-02"]["flags"], rows["TH-02"]["flags"])
check("quiet_30d after 30 days", "quiet_30d" in rows["TH-02"]["flags"] and rows["TH-02"]["quiet_days"] == 40)
check("no needs_verdict while a ticket is open", "needs_verdict" not in rows["TH-01"]["flags"])
check("no_adr when no ADR names it as primary", "no_adr" in rows["TH-03"]["flags"] and "no_adr" not in rows["TH-01"]["flags"])
check("a verdict silences every flag", rows["TH-05"]["flags"] == [], rows["TH-05"]["flags"])
check("an ongoing theme raises no flags", rows["TH-06"]["flags"] == [], rows["TH-06"]["flags"])
empty = rollup({"themes": [{"id": "TH-09", "title": "e", "verdict": "open"}], "adrs": {}}, index, {"tickets": []}, now_ms=NOW)
check("no_tickets on an empty theme", "no_tickets" in empty["themes"][0]["flags"])

print("registry checks:")
check("unsorted ADR uid reported", r["unsorted_adrs"] == ["app:ADR-004"], r["unsorted_adrs"])
check("unknown theme id reported, buckets are known", r["unknown_theme_ids"] == ["TH-99"], r["unknown_theme_ids"])
b = brief(r)
check("brief lists only flagged themes", {x["id"] for x in b["needs_verdict"]} == {"TH-02"} and b["counts"]["themes"] == 6, b)

print("config:")
missing = compute()
check("no themes file is an error, not a crash", "error" in missing and missing["themes"] == [], missing)
themes_path = Path(_tmp) / "elsewhere.json"
themes_path.write_text(json.dumps(registry))
(CONFIG_DIR / "config.json").write_text(json.dumps({"adr_themes_file": str(themes_path)}))
got = compute()
check("adr_themes_file is read", got.get("counts", {}).get("themes") == 6, got)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
