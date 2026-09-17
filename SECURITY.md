# Security

## Reporting a vulnerability

Report privately through GitHub's [security advisory form](https://github.com/PyGuy2000/devflow-mcp/security/advisories/new). Please do not open a public issue for something exploitable.

Expect an acknowledgement within a week. This is a one-person project, so a fix may take longer than that; the advisory thread will say where things stand.

## What this software does on your machine

Worth knowing before you install it, because an MCP server runs with your permissions.

**It runs the command you put in `verify_cmd`.** `verify_ticket` executes that string through a shell, in the project's `repo_path`, with a timeout. This is deliberate: the point of the field is to run your test command. It also means a ticket carries executable text. Treat `verify_cmd` the way you treat a line in a Makefile, and do not accept ticket data from anyone you would not let run a command on your machine.

**It writes one JSON file.** `devflow_state.json`, plus a rolling copy of the last 30 replaced states in `backups/`, a lock file, and logs. Nothing else is written outside the config directory.

**It runs `git`** to read the current revision of a project's repo, so a proof can be pinned to a commit. `github_activity_sync.py` additionally shells out to the `gh` CLI, and only when you run it.

**It sends nothing on its own.** No telemetry and no analytics. The only outbound traffic is whatever `verify_cmd` does and whatever `gh` does when you run the activity sync.

## The web UI

`http_api.py` binds `127.0.0.1` on port 7117. It is not reachable from another machine unless you put something in front of it, which you should not do.

Mutating routes are refused unless `api_token` is set in `config.json`. An empty token is the shipped default and leaves the UI read-only. The token is a local guard against a stray page or script, not an authentication system.

The server does not set `SO_REUSEADDR`, on purpose. A second instance fails to bind with a clear error instead of quietly sharing the port and racing the first one over the state file.

## The verification gate is not a security boundary

A ticket with a `verify_cmd` cannot reach `done` until that command has passed on the current revision. It is a workflow check, and it is honest about its limits:

- It proves a command exited 0. It does not prove the work is correct.
- `waiver="<reason>"` closes the ticket anyway and records the reason in the log. The escape hatch is deliberate and always available.
- A ticket with no `verify_cmd` has no gate at all.

Do not use it to stop a hostile actor. Use it to stop a forgetful one.

## Destructive writes

A write that would empty a non-empty store, cut the ticket count by more than half, or lower the monotonic `next_id` is refused. A deliberate reset needs `DEVFLOW_ALLOW_DESTRUCTIVE_WRITE=1`. Every replaced state is copied to `backups/` first, so even a permitted destructive write is one file copy away from undo.

This rule exists because two incidents removed real tickets. Both die at this check now instead of on disk.

## Supported versions

The latest release gets fixes. Older tags do not.
