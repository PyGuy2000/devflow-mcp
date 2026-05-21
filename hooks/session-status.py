"""
SessionStart hook — prints a brief DevFlow status summary.
Called by Claude Code on every session start via settings.json hook.
Reads directly from devflow_state.json (no MCP call needed).
"""

import json
import sys
from pathlib import Path

STATE_FILE = Path.home() / ".config" / "devflow-mcp" / "devflow_state.json"


def main():
    # Pass through stdin (required by hook protocol)
    stdin_data = sys.stdin.read()

    if not STATE_FILE.exists():
        print(stdin_data, end="")
        return

    with open(STATE_FILE) as f:
        state = json.load(f)

    tickets = state.get("tickets", [])
    if not tickets:
        print(stdin_data, end="")
        return

    active = [t for t in tickets if t["status"] == "active"]
    blocked = [t for t in tickets if t["status"] == "blocked"]
    backlog = [t for t in tickets if t["status"] == "backlog"]
    done = [t for t in tickets if t["status"] == "done"]

    # Build project name lookup
    projects = {p["id"]: p["name"] for p in state.get("projects", [])}

    lines = []
    lines.append("[DevFlow] Quick Status")
    lines.append(f"  {len(active)} active | {len(blocked)} blocked | {len(backlog)} backlog | {len(done)} done")

    if active:
        lines.append("")
        lines.append("  Active:")
        for t in active:
            proj = projects.get(t["projectId"], "?")
            unblocks = t.get("blocksTickets", [])
            suffix = f" (unblocks {', '.join(unblocks)})" if unblocks else ""
            lines.append(f"    {t['id']} [{proj}] {t['title']}{suffix}")

    if blocked:
        lines.append("")
        lines.append("  Blocked:")
        for t in blocked:
            proj = projects.get(t["projectId"], "?")
            waiting = ", ".join(t.get("blockedBy", []))
            lines.append(f"    {t['id']} [{proj}] {t['title']} <- waiting on {waiting}" if waiting else f"    {t['id']} [{proj}] {t['title']} <- external")

    summary = "\n".join(lines)

    # Emit as user-context block that Claude sees
    print(stdin_data, end="")
    print(f"\n{summary}", file=sys.stderr)


if __name__ == "__main__":
    main()
