# DevFlow MCP

Solo workflow tracker for AI coding agents. Tracks tickets, priorities, dependencies, and blocked work across multiple projects via the Model Context Protocol.

Built for Claude Code. Works with any MCP-compatible client.

## What it does

DevFlow gives your AI agent a persistent to-do list and project portfolio. The agent can create tickets, track dependencies, check what's blocked, and get a status report at the start of every session.

State lives in a single JSON file. No database, no containers, no migrations.

## Tools

15 MCP tools organized in tiers:

### Daily work (Tier 1)
- `create_project` — register a project with a name and goal
- `add_ticket` — create a ticket with title, rationale ("why"), priority, and optional blockers
- `update_ticket_status` — move tickets between backlog, active, blocked, done
- `list_tickets` — query tickets with project/status/priority filters
- `edit_ticket` — modify ticket fields
- `delete_ticket` — remove a ticket and clean up dependency references

### Project queries (Tier 2)
- `get_project` — get a project and all its tickets
- `add_dependency` — link tickets (T-005 is blocked by T-002)
- `remove_dependency` — unlink tickets
- `archive_project` — archive a completed project (all tickets must be done)

### Portfolio (Tier 3)
- `search_tickets` — keyword search across all projects
- `get_blocked` — show all blocked tickets with dependency chains
- `get_status_report` — full portfolio view: active work, blocked tickets, dependency trees, stale project alerts

### Ingestion (Tier 4)
- `ingest_manifest` — import tickets from a `devflow.json` file

### Scanning (Tier 5)
- `scan_project` — read a project's `docs/project_notes/decisions.md`, parse ADRs, cross-reference against existing tickets, and create missing ones

## The "why" field

Every ticket requires a `why` field: why the work exists and what it unblocks. The AI reads these tickets. A clear rationale produces better implementation decisions than a task title alone.

```json
{
  "id": "T-042",
  "title": "Add persistent volume to data directory",
  "why": "Processing history lost on pod restart. Blocks reliable onboarding.",
  "priority": "high",
  "status": "done",
  "blockedBy": [],
  "blocksTickets": ["T-043"]
}
```

## ADR scanning

The `scan_project` tool reads a project's Architectural Decision Records and backfills missing DevFlow tickets. It handles two ADR header formats (`##` and `###`), extracts dates from headers or body fields, and uses heuristics to classify each ADR as outstanding or completed.

```python
# Dry run: see what's missing without creating tickets
scan_project(path="/home/user/my-project", dry_run=True)

# Create backlog tickets for outstanding ADRs
scan_project(path="/home/user/my-project", project="my_project", dry_run=False)

# Full sync: also create done tickets for implemented ADRs
scan_project(path="/home/user/my-project", project="my_project", dry_run=False, include_completed=True)
```

Outstanding work heuristics:
- Explicit status markers: "Status: Research", "Status: Proposed", "Status: Draft"
- Phase/rollout plans: "Migration Sequence", "ML Transition Path"
- Future-tense language: "will be", "blocked on", "deferred", "not yet implemented"
- Rolling 30-day window: recent ADRs with any future-tense marker are flagged

Cross-project deduplication: if any ticket in any project already references an ADR ID in its title, that ADR is skipped.

## Installation

```bash
git clone https://github.com/PyGuy2000/devflow-mcp.git ~/.config/devflow-mcp
pip install mcp
```

Add to Claude Code settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "devflow": {
      "command": "python3",
      "args": ["/path/to/devflow-mcp/server.py"]
    }
  }
}
```

Replace `/path/to/` with the actual path (e.g., `~/.config/devflow-mcp/server.py`).

## Session-start hook

The `hooks/session-status.py` script prints a quick status summary when Claude Code starts:

```
DevFlow: 6 active, 5 blocked, 23 backlog, 21 done
  Active:
    T-088 [high] Add email bridge to CRM sync (unblocks: T-091)
    T-092 [medium] Build monitoring dashboard page
```

To enable it, add a SessionStart hook in your Claude Code settings.

## Web UI

An optional HTTP server (`http_api.py`) serves a dark-themed dashboard on port 7117:

```bash
python3 http_api.py
# Open http://localhost:7117
```

The UI shows all projects, tickets, and status filters. No authentication (localhost only).

![DevFlow web UI](docs/images/devflow-web-ui.png)

## ProjectHub bridge

DevFlow can optionally sync ticket state to an external SQLite database for time tracking and portfolio health. When a ticket moves to "active," a time entry starts. When it moves to "done," the timer stops and hours are calculated.

The bridge is private and not included in this repository — it is tightly coupled to a specific project management schema. The core MCP server (`server.py`) works fully without it: if `projecthub_db_path` in `config.json` is empty or the file does not exist, the bridge is silently disabled.

Configure in `config.json`:

```json
{
  "projecthub_db_path": "/path/to/your/projecthub.db",
  "auto_time_entries": true,
  "default_classification": "personal"
}
```

## Manifest ingestion

Projects can declare their tickets in a `devflow.json` file:

```json
{
  "project": "my_project",
  "goal": "Build a data pipeline",
  "tickets": [
    {
      "title": "Set up ingestion service",
      "why": "No automated data flow exists yet",
      "priority": "high",
      "status": "backlog"
    }
  ]
}
```

Ingest with `ingest_manifest(path="/path/to/devflow.json")`. Duplicate titles are skipped.

## Works with project-memory

DevFlow pairs well with the `project-memory` skill from the [Everything Claude Code (ECC)](https://github.com/anthropics/ecc) project. That skill creates 4 markdown files in each repo (`docs/project_notes/bugs.md`, `decisions.md`, `key_facts.md`, `issues.md`) and configures the AI to reference them.

The `scan_project` tool reads `decisions.md` directly, closing the loop between project memory and DevFlow.

Typical `CLAUDE.md` rules for integration:

```
When writing a new ADR in decisions.md:
- Create a DevFlow ticket for the implementation work

When a DevFlow ticket is marked done:
- Log the completion in issues.md

Periodically:
- Run scan_project to backfill any ADRs that were missed
```

## File structure

```
devflow-mcp/
  server.py              # MCP server (15 tools, ~1,200 lines)
  http_api.py            # Optional web UI server (~130 lines)
  devflow.html           # Web UI (dark theme, served by http_api.py)
  config.json            # Optional configuration
  devflow_state.json     # State file (gitignored — holds your tickets)
  hooks/
    session-status.py    # Session-start hook
```

## Requirements

- Python 3.11+
- `mcp` package (FastMCP)
- No other dependencies

## Limitations

- Solo tool. No multi-user support, no authentication, no sync.
- JSON state file. Works for hundreds of tickets across dozens of projects. Would need a database for larger scale.
- ADR scanning heuristics are tuned for the `project-memory` format. Other ADR formats may need adjustments to the parser.
- MCP protocol support required. Works with Claude Code and Claude Desktop.

## License

MIT
