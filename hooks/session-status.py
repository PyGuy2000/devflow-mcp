#!/usr/bin/env python3
"""SessionStart hook: first-run setup and a brief DevFlow status summary.

Reads the state file directly (no MCP call). Prints to stdout, which Claude
Code adds to the session context. Exits 0 always; a session must never fail
because of a status line.

On the first run it creates the config dir and seeds config.json from
config.example.json. It also checks that the ``mcp`` package the server
needs is importable by the same ``python3`` and says how to install it when
it is not.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from devflow_config import CONFIG_FILE, ensure_config_dir, load_config  # noqa: E402
from state_store import STATE_FILE  # noqa: E402


def _consume_stdin() -> None:
    try:
        if not sys.stdin.isatty():
            sys.stdin.read()
    except (OSError, ValueError):
        pass


def setup_lines() -> list:
    lines = []
    try:
        if ensure_config_dir():
            lines.append(f"[DevFlow] first run: created {CONFIG_FILE} from config.example.json. The state file appears on the first write.")
    except OSError as exc:
        lines.append(f"[DevFlow] could not create {CONFIG_FILE.parent}: {exc}")
    try:
        import mcp  # noqa: F401
    except ImportError:
        lines.append(
            f"[DevFlow] the `mcp` Python package is not installed for {sys.executable}; the MCP server cannot start. "
            "Run: python3 -m pip install mcp"
        )
    return lines


def status_lines(state: dict, stale_days: int) -> list:
    tickets = state.get("tickets", [])
    if not tickets:
        return []
    active = [t for t in tickets if t.get("status") == "active"]
    blocked = [t for t in tickets if t.get("status") == "blocked"]
    backlog = [t for t in tickets if t.get("status") == "backlog"]
    done = [t for t in tickets if t.get("status") == "done"]
    projects = {p["id"]: p["name"] for p in state.get("projects", [])}
    now_ms = time.time() * 1000

    def days_idle(t):
        last = max((e["t"] for e in t.get("log", [])), default=t.get("created", now_ms))
        return int((now_ms - last) // 86_400_000)

    lines = ["[DevFlow] Quick Status", f"  {len(active)} active | {len(blocked)} blocked | {len(backlog)} backlog | {len(done)} done"]
    if active:
        lines += ["", "  Active:"]
        for t in active:
            proj = projects.get(t.get("projectId"), "?")
            unblocks = t.get("blocksTickets", [])
            suffix = f" (unblocks {', '.join(unblocks)})" if unblocks else ""
            idle = days_idle(t)
            if idle >= stale_days:
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
        lines += ["", "  Blocked:"]
        for t in blocked:
            proj = projects.get(t.get("projectId"), "?")
            waiting = ", ".join(t.get("blockedBy", []))
            lines.append(f"    {t['id']} [{proj}] {t['title']} <- waiting on {waiting}" if waiting else f"    {t['id']} [{proj}] {t['title']} <- external")
    ticket_map = {t["id"]: t for t in tickets}
    ready = [
        t for t in blocked
        if t.get("blockedBy") and all(ticket_map[d].get("status") == "done" for d in t["blockedBy"] if d in ticket_map)
    ]
    if ready:
        lines += ["", "  Ready to unblock (all blockers done):"]
        for t in ready:
            lines.append(f"    {t['id']} [{projects.get(t.get('projectId'), '?')}] {t['title']}")
    return lines


def main() -> int:
    _consume_stdin()
    out = setup_lines()
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE, encoding="utf-8") as f:
                state = json.load(f)
            out += status_lines(state, int(load_config().get("stale_active_days", 14)))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        out.append(f"[DevFlow] status unavailable: {exc}")
    if out:
        print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
