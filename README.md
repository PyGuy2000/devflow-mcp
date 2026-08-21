# DevFlow MCP

Solo workflow tracker for AI coding agents. Tracks tickets, priorities, dependencies, and blocked work across multiple projects via the Model Context Protocol.

Built for Claude Code. Works with any MCP-compatible client.

## What it does

DevFlow gives your AI agent a persistent to-do list and project portfolio. The agent can create tickets, track dependencies, check what's blocked, and get a status report at the start of every session.

State lives in a single JSON file. No database, no containers, no migrations.

All writes go through a lock-and-atomic-replace path (`state_store.py`), not an in-memory load/rewrite. That fix exists because the earlier pattern silently dropped tickets when more than one server process wrote at the same time — this repo's own worst bug. `test_concurrency.py` exercises it directly.

On top of that, every write is checked against the on-disk store before it's allowed: a write is refused if it would zero out a non-empty store, shrink the ticket count by more than half, or lower the monotonic `next_id`. Two real incidents made this rule — a stale browser tab replaying an old snapshot, and a second dashboard process starting up on the same machine. Both die at this check now instead of on disk. A deliberate reset (e.g. wiping a test install) needs `DEVFLOW_ALLOW_DESTRUCTIVE_WRITE=1`. Every replaced state is also copied to `backups/` first (last 30 kept), so even a permitted destructive write is one file-copy away from undo.

The web UI server (`http_api.py`) also refuses to double-launch: it doesn't set `SO_REUSEADDR`, so starting a second instance on the same port fails immediately with a clear error instead of silently binding — nothing touches the state file until a real request arrives.

## Tools

17 MCP tools organized in tiers:

### Daily work (Tier 1)
- `create_project` — register a project with a name and goal
- `add_ticket` — create a ticket with title, rationale ("why"), priority, and optional blockers
- `update_ticket_status` — move tickets between backlog, active, blocked, done; a `done` transition reports any blocked tickets whose blockers are now all resolved
- `list_tickets` — query tickets with project/status/priority filters
- `get_ticket` — full context for one ticket: fields, complete work log, dependencies resolved to titles/statuses
- `log_work` — append a timestamped work note to a ticket's log (progress, findings, session-handoff breadcrumbs)
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
- `get_status_report` — full portfolio view: active work, blocked tickets, ready-to-unblock tickets, stale actives, WIP warning, dependency trees, stale project alerts

### Ticket hygiene

The status report flags workflow rot:

- `stale_active` — active tickets with no log entry in `stale_active_days` (default 14). The fix is `log_work` (if you're actually working on it) or demote to backlog.
- `wip_warning` — set when active count exceeds `wip_limit` (default 10).
- `ready_to_unblock` — blocked tickets whose blockers are all done. Externally blocked tickets (empty `blockedBy`) are excluded.

Both thresholds are configurable in `config.json` (`stale_active_days`, `wip_limit`). The session-start hook shows the same signals: `[idle Nd]` markers on stale actives and a "Ready to unblock" section.

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

The UI shows all projects, tickets, and status filters. No authentication (localhost only). Includes a global search across all projects, including ticket work logs.

![DevFlow web UI](docs/images/devflow-web-ui.png)

## GitHub activity sync

`github_activity_sync.py` is a standalone script (not an MCP tool — run it as a cron job) that polls your GitHub repos via the `gh` CLI and writes commits/PRs into the ProjectHub database, matching each repo to a project by URL or name. It adds `last_commit_date`, `open_pr_count`, and `commits_this_week` columns so a portfolio view can show which projects have actually moved recently versus which are just sitting in the tracker.

```bash
python3 github_activity_sync.py --dry-run          # preview matches without writing
python3 github_activity_sync.py                    # full sync, all your repos
python3 github_activity_sync.py --repo you/one-repo # sync a single repo
```

Requires `gh auth login`. Only meaningful if you're using the ProjectHub bridge — like the bridge itself, it needs a `projecthub.db` to write into.

## ProjectHub bridge

DevFlow can optionally sync ticket state to an external SQLite database for time tracking and portfolio health. When a ticket moves to "active," a time entry starts. When it moves to "done," the timer stops and hours are calculated, capped at `max_timer_hours` (default 8) so a ticket left active for weeks can't book that whole span as work. Demoting an active ticket back to backlog or blocked — or deleting it — cancels the timer without billing the elapsed time.

The bridge also computes a health score (0-10) per project from activity recency, deadline proximity, documentation completeness, and dependency status.

The bridge is private and not included in this repository — it is tightly coupled to a specific project management schema. The core MCP server (`server.py`) works fully without it: if `projecthub_db_path` in `config.json` is empty or the file does not exist, the bridge is silently disabled.

Configure in `config.json`:

```json
{
  "projecthub_db_path": "/path/to/your/projecthub.db",
  "auto_time_entries": true,
  "default_classification": "personal",
  "stale_active_days": 14,
  "wip_limit": 10,
  "max_timer_hours": 8.0
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

DevFlow pairs well with the [`project-memory`](https://github.com/SpillwaveSolutions/project-memory) skill from SpillwaveSolutions. That skill creates 4 markdown files in each repo (`docs/project_notes/bugs.md`, `decisions.md`, `key_facts.md`, `issues.md`) and configures the AI to reference them.

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
  server.py                  # MCP server (17 tools, ~1,500 lines)
  state_store.py             # Lock + atomic-replace state persistence
  github_activity_sync.py    # Optional: cron script, syncs GitHub commits/PRs into ProjectHub
  http_api.py                # Optional web UI server
  devflow.html                # Web UI (dark theme, served by http_api.py)
  config.json                # Optional configuration
  devflow_state.json         # State file (gitignored — holds your tickets)
  test_concurrency.py        # Exercises the lock under concurrent writers
  test_stale_write_guard.py  # Exercises the HTTP API's stale-snapshot rejection (409)
  test_tools.py               # get_ticket, log_work, stale-active/WIP/ready-to-unblock
  hooks/
    session-status.py        # Session-start hook
```

## Running the tests

Each test file is a standalone script, not a pytest suite — running them together under one `pytest` collection can make one file's imports leak into another's, since `server`/`state_store` bind their state-file path once, at first import. Run them one at a time:

```bash
python3 test_tools.py
python3 test_stale_write_guard.py
python3 test_concurrency.py
```

All three build their own temp state file and clean up after themselves — none of them touch your real `devflow_state.json`.

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
