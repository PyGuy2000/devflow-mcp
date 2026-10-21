#!/usr/bin/env python3
"""Theme status: which open questions a run of ADRs and tickets was for, and where each landed.

A theme is authored in the themes file (config key ``adr_themes_file``): its
title, a ``done_when`` test, the owner's ``verdict``, which ADR uids belong to
it, and how tickets join it. Everything else is derived here on every call and
never written back: ADR counts, open and total tickets, last activity, flags.

A ticket joins a theme by any of:
  * an ADR link (the ADR index's ``tickets`` / ``orphanTickets`` for a member ADR)
  * one hop down an EPIC: a linked ticket's ``blockedBy`` children
  * belonging to a project listed in the theme's ``ticket_projects``
  * a title matching ``ticket_title_match`` (only inside ``title_match_projects``)
  * naming the theme id (``TH-07``) in its ``why`` or ``desc``

A theme closes only on a verdict. Every ticket done with the verdict still
open raises ``needs_verdict``; nothing closes by itself.

    python3 adr_themes.py            # table
    python3 adr_themes.py --brief    # JSON for /portfolio: the flagged themes only
    python3 adr_themes.py --json     # everything
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter

from devflow_config import load_config, themes_file

QUIET_DAYS = 30
DAY_MS = 86_400_000


def load_themes(config: dict | None = None) -> dict:
    path = themes_file(config)
    if not path or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"_error": f"themes file {path} is unreadable: {e}"}
    return data if isinstance(data, dict) else {"_error": f"themes file {path} is not a JSON object"}


def _last_ms(ticket: dict) -> int:
    stamps = [e.get("t") for e in ticket.get("log") or [] if isinstance(e, dict) and e.get("t")]
    return max(stamps) if stamps else int(ticket.get("created") or 0)


def _project(ticket: dict) -> str:
    return (ticket.get("projectId") or "").removeprefix("proj-")


def rollup(registry: dict, index: dict, state: dict, now_ms: int | None = None) -> dict:
    """Theme rows with derived status. Pure: callers pass what they loaded."""
    now_ms = now_ms or int(time.time() * 1000)
    tickets = {t["id"]: t for t in state.get("tickets", [])}
    themes = registry.get("themes", [])
    membership = registry.get("adrs", {})
    match_projects = set(registry.get("title_match_projects", []))

    adrs_by_uid: dict[str, list[dict]] = {}
    for a in index.get("adrs", []):
        adrs_by_uid.setdefault(a["uid"], []).append(a)

    rows = []
    bad_patterns = []
    for th in themes:
        tid = th["id"]
        uids = [u for u, ths in membership.items() if tid in ths]
        primary = [u for u in uids if membership[u][0] == tid]
        adr_status = Counter(a["status"] for u in uids for a in adrs_by_uid.get(u, [])[:1])

        members: set[str] = set()
        for u in uids:
            for a in adrs_by_uid.get(u, []):
                for t in a.get("tickets", []) + a.get("orphanTickets", []):
                    members.add(t)
                    members.update(tickets.get(t, {}).get("blockedBy", []))
        projects = set(th.get("ticket_projects", []))
        rx = None
        if th.get("ticket_title_match"):
            try:
                rx = re.compile(th["ticket_title_match"], re.I)
            except re.error as e:
                # One bad pattern loses that theme's title matches, not the whole rollup.
                bad_patterns.append({"id": tid, "error": str(e)})
        tag = re.compile(rf"\b{re.escape(tid)}\b")
        for t in tickets.values():
            p = _project(t)
            if p in projects:
                members.add(t["id"])
            elif rx and p in match_projects and rx.search(t.get("title") or ""):
                members.add(t["id"])
            elif tag.search((t.get("why") or "") + " " + (t.get("desc") or "")):
                members.add(t["id"])

        found = [tickets[i] for i in members if i in tickets]
        by_status = Counter(t["status"] for t in found)
        open_n = sum(n for s, n in by_status.items() if s != "done")
        last = max((_last_ms(t) for t in found), default=0)
        quiet_days = (now_ms - last) // DAY_MS if last else None

        verdict = th.get("verdict", "open")
        kind = th.get("kind", "theme")
        live = verdict == "open" and kind == "theme"
        flags = []
        if live and found and open_n == 0:
            flags.append("needs_verdict")
        if live and quiet_days is not None and quiet_days >= QUIET_DAYS:
            flags.append("quiet_30d")
        if live and not primary:
            flags.append("no_adr")
        if live and not found:
            flags.append("no_tickets")

        rows.append({
            "id": tid,
            "title": th["title"],
            "kind": kind,
            "verdict": verdict,
            "verdict_date": th.get("verdict_date"),
            "done_when": th.get("done_when", ""),
            "adrs_primary": len(primary),
            "adrs_total": len(uids),
            "adr_status": dict(adr_status),
            "tickets_open": open_n,
            "tickets_total": len(found),
            "tickets_by_status": dict(by_status),
            "open_ticket_ids": sorted((t["id"] for t in found if t["status"] != "done"),
                                      key=lambda i: int(re.sub(r"\D", "", i) or 0)),
            "last_activity": time.strftime("%Y-%m-%d", time.gmtime(last / 1000)) if last else None,
            "quiet_days": quiet_days,
            "flags": flags,
        })

    known = {th["id"] for th in themes} | {b["id"] for b in registry.get("buckets", [])}
    unsorted = sorted(u for u in adrs_by_uid if u not in membership)
    unknown = sorted({t for ths in membership.values() for t in ths} - known)
    return {
        "themes": rows,
        "unsorted_adrs": unsorted,
        "unknown_theme_ids": unknown,
        "bad_patterns": bad_patterns,
        "counts": {
            "themes": len(rows),
            "open": sum(r["verdict"] == "open" for r in rows),
            "needs_verdict": sum("needs_verdict" in r["flags"] for r in rows),
            "quiet_30d": sum("quiet_30d" in r["flags"] for r in rows),
            "no_adr": sum("no_adr" in r["flags"] for r in rows),
        },
    }


def compute(config: dict | None = None) -> dict:
    """Load the themes file, the ADR index and ticket state, and roll them up."""
    from adr_index import load_index
    from state_store import read_state

    config = config or load_config()
    registry = load_themes(config)
    if not registry:
        return {"error": f"no themes file at {themes_file(config)} (config key adr_themes_file)", "themes": []}
    if "_error" in registry:
        return {"error": registry["_error"], "themes": []}
    return rollup(registry, load_index(), read_state())


def brief(result: dict) -> dict:
    """The flagged themes only, small enough for a session-start brief."""
    rows = result.get("themes", [])

    def pick(flag):
        return [{"id": r["id"], "title": r["title"], "open": r["tickets_open"], "total": r["tickets_total"],
                 "last": r["last_activity"], "quiet_days": r["quiet_days"]}
                for r in rows if flag in r["flags"]]

    return {
        "counts": result.get("counts", {}),
        "needs_verdict": pick("needs_verdict"),
        "quiet_30d": sorted(pick("quiet_30d"), key=lambda r: -(r["quiet_days"] or 0)),
        "no_adr": pick("no_adr"),
        "unsorted_adrs": result.get("unsorted_adrs", []),
        "unknown_theme_ids": result.get("unknown_theme_ids", []),
        "bad_patterns": result.get("bad_patterns", []),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Derived status for the theme registry.")
    ap.add_argument("--brief", action="store_true", help="JSON: flagged themes only")
    ap.add_argument("--json", action="store_true", help="JSON: every theme")
    args = ap.parse_args()

    result = compute()
    if "error" in result:
        print(result["error"], file=sys.stderr)
        return 1
    if args.brief:
        print(json.dumps(brief(result), indent=1))
    elif args.json:
        print(json.dumps(result, indent=1))
    else:
        for r in result["themes"]:
            print(f"{r['id']}  {r['verdict']:9} adrs={r['adrs_primary']:>3}/{r['adrs_total']:<3} "
                  f"open/total={r['tickets_open']:>3}/{r['tickets_total']:<3} last={r['last_activity'] or '-':10} "
                  f"{','.join(r['flags']):30} {r['title']}")
        if result["unsorted_adrs"]:
            print(f"\nunsorted ADRs ({len(result['unsorted_adrs'])}): {', '.join(result['unsorted_adrs'][:10])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
