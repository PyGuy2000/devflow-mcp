"""
SessionStart hook — prints a brief DevFlow status summary.
Called by Claude Code on every session start via settings.json hook.
Reads directly from devflow_state.json (no MCP call needed).
"""

import json
import sys
import time
from pathlib import Path

STATE_FILE = Path.home() / ".config" / "devflow-mcp" / "devflow_state.json"
STALE_ACTIVE_DAYS = 14


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

    now_ms = time.time() * 1000

    def days_idle(t):
        last = max((e["t"] for e in t.get("log", [])), default=t.get("created", now_ms))
        return int((now_ms - last) // 86_400_000)

    if active:
        lines.append("")
        lines.append("  Active:")
        for t in active:
            proj = projects.get(t["projectId"], "?")
            unblocks = t.get("blocksTickets", [])
            suffix = f" (unblocks {', '.join(unblocks)})" if unblocks else ""
            idle = days_idle(t)
            if idle >= STALE_ACTIVE_DAYS:
                suffix += f" [idle {idle}d]"
            if t.get("verifyCmd"):
                v = t.get("verified")
                if not v:
                    suffix += " [unverified]"
                elif v.get("exit") != 0:
                    suffix += f" [verify failing: exit {v['exit']}]"
                else:
                    suffix += f" [verified {v.get('rev', '?')}]"
            lines.append(f"    {t['id']} [{proj}] {t['title']}{suffix}")

    if blocked:
        lines.append("")
        lines.append("  Blocked:")
        for t in blocked:
            proj = projects.get(t["projectId"], "?")
            waiting = ", ".join(t.get("blockedBy", []))
            lines.append(f"    {t['id']} [{proj}] {t['title']} <- waiting on {waiting}" if waiting else f"    {t['id']} [{proj}] {t['title']} <- external")

    # Blocked tickets whose blockers are all done
    ticket_map = {t["id"]: t for t in tickets}
    ready = [
        t for t in blocked
        if t.get("blockedBy")
        and all(
            ticket_map[d]["status"] == "done"
            for d in t["blockedBy"]
            if d in ticket_map
        )
    ]
    if ready:
        lines.append("")
        lines.append("  Ready to unblock (all blockers done):")
        for t in ready:
            proj = projects.get(t["projectId"], "?")
            lines.append(f"    {t['id']} [{proj}] {t['title']}")

    summary = "\n".join(lines)

    # Emit as user-context block that Claude sees
    print(stdin_data, end="")
    print(f"\n{summary}", file=sys.stderr)


if __name__ == "__main__":
    main()
