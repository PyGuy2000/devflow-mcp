"""
DevFlow MCP Server — Solo workflow tracker with ProjectHub bridge.

Provides ticket/project management tools accessible from Claude Code,
Claude Desktop, and any MCP-compatible client. Optionally syncs
side-effects to a ProjectHub SQLite database for portfolio tracking.
"""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

# ── Configuration ──────────────────────────────────────────────────────────────

CONFIG_DIR = Path(os.path.expanduser("~/.config/devflow-mcp"))
STATE_FILE = CONFIG_DIR / "devflow_state.json"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "projecthub_db_path": "",
    "auto_time_entries": True,
    "default_classification": "personal",
}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return {**DEFAULT_CONFIG, **json.load(f)}
    return dict(DEFAULT_CONFIG)


# ── State Management ───────────────────────────────────────────────────────────

DEFAULT_STATE = {
    "projects": [],
    "tickets": [],
    "next_id": 1,
}


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return {**DEFAULT_STATE, **json.load(f)}
    return dict(DEFAULT_STATE)


def save_state(state: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def next_ticket_id(state: dict) -> str:
    tid = f"T-{state['next_id']:03d}"
    state["next_id"] = state["next_id"] + 1
    return tid


# ── ProjectHub Bridge (imported lazily) ────────────────────────────────────────

_bridge = None


def get_bridge():
    global _bridge
    if _bridge is None:
        from projecthub_bridge import ProjectHubBridge

        config = load_config()
        db_path = config["projecthub_db_path"]
        if Path(db_path).exists():
            _bridge = ProjectHubBridge(db_path)
        else:
            _bridge = None
    return _bridge


# ── MCP Server ─────────────────────────────────────────────────────────────────

mcp = FastMCP("devflow")


# ── Tier 1: Daily Work Tools ──────────────────────────────────────────────────


@mcp.tool()
def create_project(name: str, goal: str, color: str = "#4f7cff") -> dict:
    """
    Register a new project in DevFlow.

    Args:
        name: Project name (short, snake_case preferred).
        goal: What this project is ultimately for.
        color: Hex color for the project badge. Defaults to blue.
    """
    state = load_state()

    # Check for duplicate name
    for p in state["projects"]:
        if p["name"] == name:
            return {"error": f"Project '{name}' already exists", "project": p}

    project = {
        "id": f"proj-{name.lower().replace(' ', '_')}",
        "name": name,
        "goal": goal,
        "color": color,
    }
    state["projects"] = [*state["projects"], project]
    save_state(state)

    # Bridge: upsert into ProjectHub
    bridge = get_bridge()
    if bridge:
        bridge.upsert_project(name, goal)

    return {"success": True, "project": project}


@mcp.tool()
def add_ticket(
    title: str,
    why: str,
    project: str,
    priority: str = "medium",
    blocked_by: Optional[list[str]] = None,
    status: str = "backlog",
    desc: str = "",
) -> dict:
    """
    Add a ticket to a project.

    Args:
        title: Short task title.
        why: Why this ticket exists and what it unblocks (mandatory rationale).
        project: Project name or project ID to attach this ticket to.
        priority: critical, high, medium, or low. Defaults to medium.
        blocked_by: List of ticket IDs (e.g. ["T-001", "T-003"]) that block this ticket.
        status: Initial status: backlog, active, blocked, or done. Defaults to backlog.
        desc: Technical details and implementation approach (optional).
    """
    state = load_state()

    # Resolve project
    proj = _resolve_project(state, project)
    if not proj:
        return {"error": f"Project '{project}' not found. Create it first."}

    if priority not in ("critical", "high", "medium", "low"):
        return {"error": f"Invalid priority '{priority}'. Use critical/high/medium/low."}

    if status not in ("backlog", "active", "blocked", "done"):
        return {"error": f"Invalid status '{status}'. Use backlog/active/blocked/done."}

    blocked_by = blocked_by or []
    ticket_id = next_ticket_id(state)
    now_ms = int(time.time() * 1000)

    ticket = {
        "id": ticket_id,
        "title": title,
        "why": why,
        "desc": desc,
        "projectId": proj["id"],
        "priority": priority,
        "status": status,
        "blockedBy": blocked_by,
        "blocksTickets": [],
        "created": now_ms,
        "log": [{"t": now_ms, "msg": "Ticket created"}],
    }

    # Update reverse dependencies
    updated_tickets = []
    for t in state["tickets"]:
        if t["id"] in blocked_by:
            t = {**t, "blocksTickets": [*t["blocksTickets"], ticket_id]}
        updated_tickets.append(t)

    state["tickets"] = [*updated_tickets, ticket]
    save_state(state)

    # Bridge: if status is active, start time tracking
    if status == "active":
        _bridge_ticket_active(ticket, proj, state)

    return {"success": True, "ticket": ticket}


@mcp.tool()
def update_ticket_status(ticket_id: str, new_status: str) -> dict:
    """
    Move a ticket between statuses.

    Args:
        ticket_id: The ticket ID (e.g. "T-001").
        new_status: Target status: backlog, active, blocked, or done.
    """
    if new_status not in ("backlog", "active", "blocked", "done"):
        return {"error": f"Invalid status '{new_status}'. Use backlog/active/blocked/done."}

    state = load_state()

    ticket = None
    for t in state["tickets"]:
        if t["id"] == ticket_id:
            ticket = t
            break

    if not ticket:
        return {"error": f"Ticket '{ticket_id}' not found."}

    old_status = ticket["status"]
    if old_status == new_status:
        return {"info": f"Ticket {ticket_id} is already '{new_status}'.", "ticket": ticket}

    now_ms = int(time.time() * 1000)
    log_entry = {"t": now_ms, "msg": f"Status: {old_status} → {new_status}"}

    updated_ticket = {
        **ticket,
        "status": new_status,
        "log": [*ticket["log"], log_entry],
    }

    state["tickets"] = [
        updated_ticket if t["id"] == ticket_id else t for t in state["tickets"]
    ]
    save_state(state)

    # Bridge side-effects
    proj = _find_project_by_id(state, updated_ticket["projectId"])

    if new_status == "active" and old_status != "active":
        _bridge_ticket_active(updated_ticket, proj, state)
    elif new_status == "done" and old_status != "done":
        _bridge_ticket_done(updated_ticket, proj, state)
    elif new_status == "blocked":
        _bridge_ticket_blocked(updated_ticket, proj, state)

    return {"success": True, "ticket": updated_ticket, "transition": f"{old_status} → {new_status}"}


@mcp.tool()
def list_tickets(
    project: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
) -> dict:
    """
    List tickets, optionally filtered.

    Args:
        project: Filter by project name or ID.
        status: Filter by status (backlog, active, blocked, done).
        priority: Filter by priority (critical, high, medium, low).
    """
    state = load_state()
    tickets = state["tickets"]

    if project:
        proj = _resolve_project(state, project)
        if proj:
            tickets = [t for t in tickets if t["projectId"] == proj["id"]]
        else:
            return {"error": f"Project '{project}' not found.", "tickets": []}

    if status:
        tickets = [t for t in tickets if t["status"] == status]

    if priority:
        tickets = [t for t in tickets if t["priority"] == priority]

    # Sort: critical > high > medium > low, then by created
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    tickets = sorted(tickets, key=lambda t: (priority_order.get(t["priority"], 9), t["created"]))

    return {"count": len(tickets), "tickets": tickets}


@mcp.tool()
def edit_ticket(
    ticket_id: str,
    title: Optional[str] = None,
    why: Optional[str] = None,
    desc: Optional[str] = None,
    priority: Optional[str] = None,
    project: Optional[str] = None,
) -> dict:
    """
    Edit a ticket's fields. Only provided fields are updated.

    Args:
        ticket_id: The ticket ID (e.g. "T-001").
        title: New title.
        why: New rationale.
        desc: New technical description.
        priority: New priority (critical, high, medium, low).
        project: Move ticket to a different project (name or ID).
    """
    state = load_state()

    ticket = None
    for t in state["tickets"]:
        if t["id"] == ticket_id:
            ticket = t
            break

    if not ticket:
        return {"error": f"Ticket '{ticket_id}' not found."}

    if priority and priority not in ("critical", "high", "medium", "low"):
        return {"error": f"Invalid priority '{priority}'."}

    updates = {}
    if title is not None:
        updates["title"] = title
    if why is not None:
        updates["why"] = why
    if desc is not None:
        updates["desc"] = desc
    if priority is not None:
        updates["priority"] = priority

    if project is not None:
        proj = _resolve_project(state, project)
        if not proj:
            return {"error": f"Project '{project}' not found."}
        updates["projectId"] = proj["id"]

    now_ms = int(time.time() * 1000)
    changed_fields = ", ".join(updates.keys())
    log_entry = {"t": now_ms, "msg": f"Edited: {changed_fields}"}

    updated_ticket = {**ticket, **updates, "log": [*ticket["log"], log_entry]}

    state["tickets"] = [
        updated_ticket if t["id"] == ticket_id else t for t in state["tickets"]
    ]
    save_state(state)

    return {"success": True, "ticket": updated_ticket}


@mcp.tool()
def delete_ticket(ticket_id: str) -> dict:
    """
    Delete a ticket and clean up its dependency references.

    Args:
        ticket_id: The ticket ID to delete (e.g. "T-001").
    """
    state = load_state()

    ticket = None
    for t in state["tickets"]:
        if t["id"] == ticket_id:
            ticket = t
            break

    if not ticket:
        return {"error": f"Ticket '{ticket_id}' not found."}

    # Remove from other tickets' blockedBy and blocksTickets
    cleaned_tickets = []
    for t in state["tickets"]:
        if t["id"] == ticket_id:
            continue
        t = {
            **t,
            "blockedBy": [bid for bid in t["blockedBy"] if bid != ticket_id],
            "blocksTickets": [bid for bid in t["blocksTickets"] if bid != ticket_id],
        }
        cleaned_tickets.append(t)

    state["tickets"] = cleaned_tickets
    save_state(state)

    return {"success": True, "deleted": ticket_id, "title": ticket["title"]}


@mcp.tool()
def get_project(project: str) -> dict:
    """
    Get a project and all its tickets.

    Args:
        project: Project name or ID.
    """
    state = load_state()

    proj = _resolve_project(state, project)
    if not proj:
        return {"error": f"Project '{project}' not found."}

    tickets = [t for t in state["tickets"] if t["projectId"] == proj["id"]]
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    tickets = sorted(tickets, key=lambda t: (priority_order.get(t["priority"], 9), t["created"]))

    status_counts = {}
    for t in tickets:
        status_counts[t["status"]] = status_counts.get(t["status"], 0) + 1

    return {
        "project": proj,
        "ticket_count": len(tickets),
        "status_summary": status_counts,
        "tickets": tickets,
    }


@mcp.tool()
def add_dependency(ticket_id: str, blocked_by: str) -> dict:
    """
    Add a blocking dependency to a ticket.

    Args:
        ticket_id: The ticket that becomes blocked (e.g. "T-005").
        blocked_by: The ticket that blocks it (e.g. "T-002").
    """
    state = load_state()
    ticket_map = {t["id"]: t for t in state["tickets"]}

    if ticket_id not in ticket_map:
        return {"error": f"Ticket '{ticket_id}' not found."}
    if blocked_by not in ticket_map:
        return {"error": f"Ticket '{blocked_by}' not found."}
    if ticket_id == blocked_by:
        return {"error": "A ticket cannot block itself."}

    ticket = ticket_map[ticket_id]
    blocker = ticket_map[blocked_by]

    if blocked_by in ticket["blockedBy"]:
        return {"info": f"{ticket_id} is already blocked by {blocked_by}."}

    now_ms = int(time.time() * 1000)

    updated_ticket = {
        **ticket,
        "blockedBy": [*ticket["blockedBy"], blocked_by],
        "log": [*ticket["log"], {"t": now_ms, "msg": f"Added dependency: blocked by {blocked_by}"}],
    }
    updated_blocker = {
        **blocker,
        "blocksTickets": [*blocker["blocksTickets"], ticket_id],
    }

    state["tickets"] = [
        updated_ticket if t["id"] == ticket_id
        else updated_blocker if t["id"] == blocked_by
        else t
        for t in state["tickets"]
    ]
    save_state(state)

    # Bridge: cross-project blocking
    if updated_ticket["projectId"] != updated_blocker["projectId"]:
        proj = _find_project_by_id(state, updated_ticket["projectId"])
        _bridge_ticket_blocked(updated_ticket, proj, state)

    return {"success": True, "ticket_id": ticket_id, "now_blocked_by": updated_ticket["blockedBy"]}


@mcp.tool()
def remove_dependency(ticket_id: str, blocked_by: str) -> dict:
    """
    Remove a blocking dependency from a ticket.

    Args:
        ticket_id: The ticket to unblock (e.g. "T-005").
        blocked_by: The blocker ticket to remove (e.g. "T-002").
    """
    state = load_state()
    ticket_map = {t["id"]: t for t in state["tickets"]}

    if ticket_id not in ticket_map:
        return {"error": f"Ticket '{ticket_id}' not found."}

    ticket = ticket_map[ticket_id]

    if blocked_by not in ticket["blockedBy"]:
        return {"info": f"{ticket_id} is not blocked by {blocked_by}."}

    now_ms = int(time.time() * 1000)

    updated_ticket = {
        **ticket,
        "blockedBy": [bid for bid in ticket["blockedBy"] if bid != blocked_by],
        "log": [*ticket["log"], {"t": now_ms, "msg": f"Removed dependency: {blocked_by}"}],
    }

    # Update blocker's blocksTickets
    blocker = ticket_map.get(blocked_by)
    updated_blocker = None
    if blocker:
        updated_blocker = {
            **blocker,
            "blocksTickets": [bid for bid in blocker["blocksTickets"] if bid != ticket_id],
        }

    state["tickets"] = [
        updated_ticket if t["id"] == ticket_id
        else updated_blocker if updated_blocker and t["id"] == blocked_by
        else t
        for t in state["tickets"]
    ]
    save_state(state)

    return {"success": True, "ticket_id": ticket_id, "blocked_by": updated_ticket["blockedBy"]}


@mcp.tool()
def archive_project(project: str) -> dict:
    """
    Archive a project — removes it from DevFlow and marks it completed in ProjectHub.

    Args:
        project: Project name or ID to archive.
    """
    state = load_state()

    proj = _resolve_project(state, project)
    if not proj:
        return {"error": f"Project '{project}' not found."}

    # Count open tickets
    open_tickets = [
        t for t in state["tickets"]
        if t["projectId"] == proj["id"] and t["status"] != "done"
    ]
    if open_tickets:
        return {
            "error": f"Cannot archive — {len(open_tickets)} tickets still open.",
            "open_tickets": [{"id": t["id"], "title": t["title"], "status": t["status"]} for t in open_tickets],
        }

    # Remove project and its tickets from DevFlow
    state["projects"] = [p for p in state["projects"] if p["id"] != proj["id"]]
    state["tickets"] = [t for t in state["tickets"] if t["projectId"] != proj["id"]]
    save_state(state)

    # Bridge: mark completed in ProjectHub
    bridge = get_bridge()
    if bridge:
        conn = bridge._connect()
        try:
            now = bridge._now()
            conn.execute(
                "UPDATE projects SET status = 'completed', archived_at = ?, updated_at = ? WHERE name = ?",
                (now, now, proj["name"]),
            )
            conn.commit()
        finally:
            conn.close()

    return {"success": True, "archived": proj["name"]}


@mcp.tool()
def search_tickets(query: str) -> dict:
    """
    Search tickets by keyword across all projects. Searches title, why, and desc fields.

    Args:
        query: Search term (case-insensitive).
    """
    state = load_state()
    query_lower = query.lower()

    matches = []
    for t in state["tickets"]:
        searchable = f"{t['title']} {t['why']} {t.get('desc', '')}".lower()
        if query_lower in searchable:
            matches.append({
                "id": t["id"],
                "title": t["title"],
                "project": _project_name_by_id(state, t["projectId"]),
                "status": t["status"],
                "priority": t["priority"],
            })

    return {"query": query, "count": len(matches), "results": matches}


@mcp.tool()
def get_blocked() -> dict:
    """
    Show all blocked tickets with their dependency chains.
    Returns blocked tickets and what they're waiting on.
    """
    state = load_state()

    blocked = [t for t in state["tickets"] if t["status"] == "blocked"]
    ticket_map = {t["id"]: t for t in state["tickets"]}

    result = []
    for t in blocked:
        deps = []
        for dep_id in t["blockedBy"]:
            dep = ticket_map.get(dep_id)
            if dep:
                deps.append({
                    "id": dep["id"],
                    "title": dep["title"],
                    "status": dep["status"],
                    "resolved": dep["status"] == "done",
                })
        result.append({
            "id": t["id"],
            "title": t["title"],
            "why": t["why"],
            "project": _project_name_by_id(state, t["projectId"]),
            "waiting_on": deps,
            "all_deps_resolved": all(d["resolved"] for d in deps),
        })

    return {"blocked_count": len(result), "blocked_tickets": result}


@mcp.tool()
def get_status_report() -> dict:
    """
    Get a full status report: blocked tickets, active work, dependency chains,
    and portfolio health alerts. The "what should I work on next" summary.
    """
    state = load_state()
    ticket_map = {t["id"]: t for t in state["tickets"]}

    # Blocked tickets
    blocked = []
    for t in state["tickets"]:
        if t["status"] == "blocked":
            deps = [
                {"id": d, "title": ticket_map[d]["title"], "status": ticket_map[d]["status"]}
                for d in t["blockedBy"]
                if d in ticket_map
            ]
            blocked.append({
                "id": t["id"],
                "title": t["title"],
                "why": t["why"],
                "project": _project_name_by_id(state, t["projectId"]),
                "waiting_on": deps,
            })

    # Active work
    active = [
        {
            "id": t["id"],
            "title": t["title"],
            "project": _project_name_by_id(state, t["projectId"]),
            "priority": t["priority"],
            "unblocks": [
                {"id": b, "title": ticket_map[b]["title"]}
                for b in t["blocksTickets"]
                if b in ticket_map
            ],
        }
        for t in state["tickets"]
        if t["status"] == "active"
    ]

    # Dependency chains (roots → leaves)
    chains = _build_dependency_chains(state)

    # Summary stats
    status_counts = {"backlog": 0, "active": 0, "blocked": 0, "done": 0}
    for t in state["tickets"]:
        status_counts[t["status"]] = status_counts.get(t["status"], 0) + 1

    # Portfolio health (from bridge if available)
    portfolio_alerts = []
    bridge = get_bridge()
    if bridge:
        portfolio_alerts = bridge.get_stale_projects()

    return {
        "summary": status_counts,
        "project_count": len(state["projects"]),
        "blocked": blocked,
        "active": active,
        "dependency_chains": chains,
        "portfolio_alerts": portfolio_alerts,
    }


# ── Tier 4: Manifest Ingestion ─────────────────────────────────────────────────


@mcp.tool()
def ingest_manifest(path: str) -> dict:
    """
    Ingest a devflow.json manifest from a project directory.
    Creates the project if it doesn't exist, then creates/updates tickets.
    Skips tickets whose titles already exist in the project.

    Args:
        path: Absolute path to the devflow.json file.
    """
    from pathlib import Path as P

    manifest_path = P(path)
    if not manifest_path.exists():
        return {"error": f"Manifest not found: {path}"}

    try:
        with open(manifest_path) as f:
            manifest = json.load(f)
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON in manifest: {e}"}

    project_name = manifest.get("project")
    goal = manifest.get("goal", "")
    tickets_data = manifest.get("tickets", [])

    if not project_name:
        return {"error": "Manifest missing 'project' field"}

    state = load_state()

    # Create project if it doesn't exist
    proj = _resolve_project(state, project_name)
    if not proj:
        project_id = f"proj-{project_name.lower().replace(' ', '_')}"
        proj = {
            "id": project_id,
            "name": project_name,
            "goal": goal,
            "color": manifest.get("color", "#4f7cff"),
        }
        state["projects"] = [*state["projects"], proj]

        # Bridge: upsert into ProjectHub
        bridge = get_bridge()
        if bridge:
            bridge.upsert_project(project_name, goal)

    # Get existing ticket titles for this project to avoid duplicates
    existing_titles = {
        t["title"]
        for t in state["tickets"]
        if t["projectId"] == proj["id"]
    }

    created = []
    skipped = []

    for td in tickets_data:
        title = td.get("title", "")
        if not title:
            continue

        if title in existing_titles:
            skipped.append(title)
            continue

        ticket_id = next_ticket_id(state)
        now_ms = int(time.time() * 1000)

        blocked_by = td.get("blockedBy", [])
        ticket = {
            "id": ticket_id,
            "title": title,
            "why": td.get("why", ""),
            "desc": td.get("desc", ""),
            "projectId": proj["id"],
            "priority": td.get("priority", "medium"),
            "status": td.get("status", "backlog"),
            "blockedBy": blocked_by,
            "blocksTickets": [],
            "created": now_ms,
            "log": [{"t": now_ms, "msg": f"Ticket created from manifest: {path}"}],
        }

        # Update reverse dependencies
        updated_tickets = []
        for t in state["tickets"]:
            if t["id"] in blocked_by:
                t = {**t, "blocksTickets": [*t["blocksTickets"], ticket_id]}
            updated_tickets.append(t)
        state["tickets"] = [*updated_tickets, ticket]

        created.append({"id": ticket_id, "title": title})

    save_state(state)

    return {
        "success": True,
        "project": proj["name"],
        "created": len(created),
        "skipped": len(skipped),
        "tickets_created": created,
        "tickets_skipped": skipped,
    }


# ── Tier 5: Project Scanning & Backfill ───────────────────────────────────────


@mcp.tool()
def scan_project(
    path: str,
    project: Optional[str] = None,
    dry_run: bool = True,
    include_completed: bool = False,
) -> dict:
    """
    Scan a project's docs/project_notes/decisions.md for ADRs with outstanding
    work, cross-reference against existing DevFlow tickets, and report (or
    create) missing tickets.

    Args:
        path: Absolute path to the project root directory (must contain docs/project_notes/decisions.md).
        project: DevFlow project name or ID. If omitted, inferred from the directory name.
        dry_run: If True (default), report proposed tickets without creating them. Set False to create.
        include_completed: If True, also create tickets for implemented ADRs (status=done). Gives full ADR visibility in the kanban.
    """
    project_dir = Path(path)
    decisions_file = project_dir / "docs" / "project_notes" / "decisions.md"

    if not decisions_file.exists():
        return {"error": f"No decisions.md found at {decisions_file}"}

    # Resolve project
    project_name = project or project_dir.name
    state = load_state()
    proj = _resolve_project(state, project_name)

    if not proj and not dry_run:
        return {
            "error": f"Project '{project_name}' not found in DevFlow. Create it first or use dry_run=True.",
        }

    # Parse ADRs
    with open(decisions_file) as f:
        content = f.read()

    adrs = _parse_adrs(content)

    # Classify each ADR as outstanding or completed
    outstanding_ids = {adr["id"] for adr in adrs if _has_outstanding_work(adr)}

    # Cross-reference against existing tickets
    existing_titles = set()
    if proj:
        existing_titles = {
            t["title"]
            for t in state["tickets"]
            if t["projectId"] == proj["id"]
        }

    # Check ALL projects' tickets for ADR references — an ADR tracked under
    # a different DevFlow project still counts as covered.
    existing_adr_refs = set()
    for t in state["tickets"]:
        for adr in adrs:
            if adr["id"] in t["title"]:
                existing_adr_refs.add(adr["id"])

    # Determine which ADRs to process
    adrs_to_process = adrs if include_completed else [a for a in adrs if a["id"] in outstanding_ids]

    missing = []
    already_tracked = []

    for adr in adrs_to_process:
        proposed_title = f"{adr['id']}: {adr['title']}"
        is_outstanding = adr["id"] in outstanding_ids

        # Check if already tracked by exact title or ADR ID reference
        if proposed_title in existing_titles or adr["id"] in existing_adr_refs:
            already_tracked.append({
                "adr": adr["id"],
                "title": adr["title"],
                "reason": "Existing ticket references this ADR",
            })
            continue

        why = _extract_why(adr)
        desc = _extract_outstanding_work(adr) if is_outstanding else adr["sections"].get("decision", adr["sections"].get("context", ""))
        # Truncate long completed ADR descriptions
        if not is_outstanding and len(desc) > 500:
            desc = desc[:497] + "..."

        missing.append({
            "adr": adr["id"],
            "title": proposed_title,
            "why": why,
            "desc": desc,
            "date": adr.get("date", ""),
            "status": "backlog" if is_outstanding else "done",
        })

    # Create tickets if not dry_run
    created = []
    if not dry_run and proj and missing:
        for item in missing:
            ticket_id = next_ticket_id(state)
            now_ms = int(time.time() * 1000)

            status = item["status"]
            log_msg = f"Auto-created by scan_project from {decisions_file}"
            log_entries = [{"t": now_ms, "msg": log_msg}]
            if status == "done":
                log_entries.append({"t": now_ms, "msg": "Status: backlog → done (implemented ADR)"})

            ticket = {
                "id": ticket_id,
                "title": item["title"],
                "why": item["why"],
                "desc": item["desc"],
                "projectId": proj["id"],
                "priority": "medium",
                "status": status,
                "blockedBy": [],
                "blocksTickets": [],
                "created": now_ms,
                "log": log_entries,
            }
            state["tickets"] = [*state["tickets"], ticket]
            created.append({
                "id": ticket_id,
                "adr": item["adr"],
                "title": item["title"],
                "status": status,
            })

        save_state(state)

        # Bridge: upsert project in ProjectHub
        bridge = get_bridge()
        if bridge and proj:
            bridge.upsert_project(proj["name"], proj.get("goal", ""))

    return {
        "project": project_name,
        "decisions_file": str(decisions_file),
        "total_adrs": len(adrs),
        "adrs_with_outstanding_work": len(outstanding_ids),
        "already_tracked": len(already_tracked),
        "missing_tickets": len(missing),
        "dry_run": dry_run,
        "include_completed": include_completed,
        "proposed_tickets": missing if dry_run else [],
        "created_tickets": created,
        "already_tracked_details": already_tracked,
    }


def _parse_adrs(content: str) -> list[dict]:
    """Parse ADR entries from a decisions.md file."""
    adr_pattern = re.compile(
        r"^#{2,3} (ADR-\d+|ADR-XXX): (.+?)(?:\s*\((\d{4}-\d{2}-\d{2})\))?\s*$",
        re.MULTILINE,
    )

    matches = list(adr_pattern.finditer(content))
    adrs = []

    for i, match in enumerate(matches):
        adr_id = match.group(1)
        title = match.group(2).strip()
        date = match.group(3) or ""

        # Skip the template entry
        if adr_id == "ADR-XXX":
            continue

        # Extract body until next ADR or end of file
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[start:end].strip()

        # Parse sections
        sections = _parse_adr_sections(body)

        # Fall back to **Date**: field if header didn't have a date
        if not date and "date" in sections:
            date_match = re.search(r"\d{4}-\d{2}-\d{2}", sections["date"])
            if date_match:
                date = date_match.group(0)

        adrs.append({
            "id": adr_id,
            "title": title,
            "date": date,
            "body": body,
            "sections": sections,
        })

    return adrs


def _parse_adr_sections(body: str) -> dict[str, str]:
    """Split an ADR body into named sections. Handles both **Bold:** and ### Header styles."""
    # Match **Bold:** markers OR ### subsection headers
    section_pattern = re.compile(
        r"(?:^\*\*(.+?):?\*\*\s*$|^### (.+?)\s*$)",
        re.MULTILINE,
    )
    matches = list(section_pattern.finditer(body))
    sections = {}

    for i, match in enumerate(matches):
        # group(1) is **Bold**, group(2) is ### Header
        name = (match.group(1) or match.group(2)).strip().rstrip(":")
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[name.lower()] = body[start:end].strip()

    return sections


def _has_outstanding_work(adr: dict) -> bool:
    """Heuristic: does this ADR have work that isn't done yet?"""
    body_lower = adr["body"].lower()
    sections = adr["sections"]

    # Check for explicit status markers (e.g. "**Status:** Research / Future")
    status = sections.get("status", "").lower()
    if any(s in status for s in ["research", "future", "draft", "proposed", "pending"]):
        return True

    # Check for explicit phase/rollout plans with incomplete phases
    phase_sections = [
        "migration sequence", "ml transition path", "transition path",
        "automated workflow", "rollout",
    ]
    for name in phase_sections:
        if name in sections or name in body_lower:
            return True

    # Check ALL section content for future-tense markers
    check_text = " ".join(sections.values()).lower()

    future_markers = [
        "will be", "will need", "needs to", "must be",
        "blocked on", "blocked until", "waiting on", "waiting for",
        "deferred", "planned for", "not yet", "todo", "to-do",
        "phase ", "step ", "next:", "later:",
        "when ready", "when complete", "once deployed",
        "requires ", "should be added", "needs migration",
        "future", "not yet implemented",
    ]

    future_count = sum(1 for marker in future_markers if marker in check_text)

    # ADR likely has outstanding work if it has multiple future-tense markers
    if future_count >= 2:
        return True

    # Recent ADRs (last 30 days) with any future language are likely incomplete
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    if adr.get("date", "") >= cutoff and future_count >= 1:
        return True

    return False


def _extract_why(adr: dict) -> str:
    """Build a 'why' string from the ADR context section."""
    context = adr["sections"].get("context", "")
    if not context:
        return f"Outstanding work from {adr['id']}: {adr['title']}"

    # Take the first substantive bullet or sentence
    lines = [
        re.sub(r"^[-*]\s+", "", line.strip())
        for line in context.split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]

    if lines:
        # Use first 1-2 lines, truncated
        why = ". ".join(lines[:2])
        if len(why) > 300:
            why = why[:297] + "..."
        return why

    return f"Outstanding work from {adr['id']}: {adr['title']}"


def _extract_outstanding_work(adr: dict) -> str:
    """Build a description of what remains to be done."""
    parts = []
    future_keywords = [
        "will ", "needs ", "must ", "blocked", "requires ",
        "should ", "next ", "phase ", "deferred", "not yet",
        "future", "planned", "pending",
    ]

    # Check for phased rollout sections
    phase_keys = [
        "migration sequence", "ml transition path", "transition path",
        "automated workflow", "rollout",
    ]
    for key in phase_keys:
        content = adr["sections"].get(key, "")
        if content:
            parts.append(f"{key.title()}:\n{content.strip()}")

    # Scan all sections for future-tense lines
    for section_name, section_text in adr["sections"].items():
        if section_name in phase_keys:
            continue  # already handled above
        future_lines = []
        for line in section_text.split("\n"):
            line_lower = line.strip().lower()
            if any(m in line_lower for m in future_keywords):
                future_lines.append(line.strip())
        if future_lines:
            parts.append(f"Outstanding from {section_name}:\n" + "\n".join(future_lines))

    if not parts:
        return f"Review {adr['id']} for outstanding implementation work."

    return "\n\n".join(parts)


# ── Helper Functions ───────────────────────────────────────────────────────────


def _resolve_project(state: dict, identifier: str) -> Optional[dict]:
    """Find a project by name or ID."""
    for p in state["projects"]:
        if p["id"] == identifier or p["name"] == identifier:
            return p
    return None


def _find_project_by_id(state: dict, project_id: str) -> Optional[dict]:
    for p in state["projects"]:
        if p["id"] == project_id:
            return p
    return None


def _project_name_by_id(state: dict, project_id: str) -> str:
    proj = _find_project_by_id(state, project_id)
    return proj["name"] if proj else project_id


def _build_dependency_chains(state: dict) -> list[dict]:
    """Build dependency chain trees from root tickets."""
    ticket_map = {t["id"]: t for t in state["tickets"]}

    # Roots: tickets that block others, are not done, and have no blockedBy
    roots = [
        t
        for t in state["tickets"]
        if t["blocksTickets"] and not t["blockedBy"] and t["status"] != "done"
    ]

    def build_tree(tid: str, visited: set) -> Optional[dict]:
        if tid in visited or tid not in ticket_map:
            return None
        visited = {*visited, tid}
        t = ticket_map[tid]
        children = [
            build_tree(child_id, visited)
            for child_id in t["blocksTickets"]
            if child_id in ticket_map
        ]
        return {
            "id": t["id"],
            "title": t["title"],
            "status": t["status"],
            "children": [c for c in children if c is not None],
        }

    return [
        tree
        for root in roots
        if (tree := build_tree(root["id"], set())) is not None
    ]


# ── Bridge Side-Effects ────────────────────────────────────────────────────────


def _bridge_ticket_active(ticket: dict, proj: Optional[dict], state: dict) -> None:
    """Side-effect: ticket moved to active → start time entry in ProjectHub."""
    bridge = get_bridge()
    if not bridge or not proj:
        return
    config = load_config()
    if config.get("auto_time_entries"):
        bridge.start_time_entry(proj["name"], ticket["title"])


def _bridge_ticket_done(ticket: dict, proj: Optional[dict], state: dict) -> None:
    """Side-effect: ticket done → close time entry, update progress."""
    bridge = get_bridge()
    if not bridge or not proj:
        return
    config = load_config()
    if config.get("auto_time_entries"):
        bridge.stop_time_entry(proj["name"], ticket["title"])

    # Update progress: done_count / total_count for this project
    project_tickets = [t for t in state["tickets"] if t["projectId"] == proj["id"]]
    if project_tickets:
        done_count = sum(1 for t in project_tickets if t["status"] == "done")
        total = len(project_tickets)
        progress = int((done_count / total) * 100)
        bridge.update_project_progress(proj["name"], progress)


def _bridge_ticket_blocked(ticket: dict, proj: Optional[dict], state: dict) -> None:
    """Side-effect: cross-project blocking → create project dependency."""
    bridge = get_bridge()
    if not bridge or not proj:
        return

    ticket_map = {t["id"]: t for t in state["tickets"]}
    for dep_id in ticket.get("blockedBy", []):
        dep = ticket_map.get(dep_id)
        if dep and dep["projectId"] != ticket["projectId"]:
            blocker_proj = _find_project_by_id(state, dep["projectId"])
            if blocker_proj:
                bridge.add_project_dependency(
                    source_project=proj["name"],
                    target_project=blocker_proj["name"],
                )


# ── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
