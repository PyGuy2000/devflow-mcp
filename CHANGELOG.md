# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- A CI workflow. It runs all six test suites, one suite per step so a red run names the file that broke. See the matrix note below for what it runs them against.
- `edit_project`, to change an existing project's goal, colour or `repo_path`. Without it a project created with no `repo_path` could never run a ticket's `verify_cmd`, because the verifier had no working directory and `create_project` refuses a duplicate name. The only fix was editing the state file by hand.
- `CONTRIBUTING.md` and `SECURITY.md`.
- Brand images built by `scripts/make_brand_images.py`: a README banner, a social preview card, and a terminal card showing the verification gate, captured from a real run.

### Changed

- `server.py` runs on `mcp` 1.x and 2.x. The 2.0 release renamed `FastMCP` to `MCPServer`; the import now accepts either name, and the decorator and `run()` signatures are the same across both. An existing install stays on 1.x until its operator chooses to move.
- CI is a cross product: Python 3.11 and 3.12 against `mcp<2` and `mcp>=2`, four jobs. Both axes are in the matrix proper. Under `include` the second entry would overwrite the first and one major would never be tested.

### Fixed

- The README's tool badge said 19 and the text said 18; the real count is 22, and the 0.1.0 tag had 21. Nobody had counted. The test badge, which counted print lines and moved three times in one session, is replaced by the suite count.
- `test_adr_config.py` reported fewer passes than it ran. It re-runs itself in a second process to test the no-config defaults, folded that run's failures into its own tally but not its passes, so the summary said 16 where 21 checks had passed. Failures were always counted, so nothing was hidden.
- The documented install no longer breaks on a new machine. `pip install mcp` resolves to 2.x, which `server.py` could not import, so a fresh install failed before any tool ran. Fixed first with a `mcp<2` pin, then properly by supporting both majors. The first CI run found this; every machine here already had 1.x installed.

## [0.1.0] - 2026-09-16

First public release. 21 MCP tools over a single JSON state file.

### Added

- Projects, tickets with a mandatory `why`, priorities, and dependencies between tickets.
- A verification gate. A ticket carrying a `verify_cmd` cannot reach `done` until that command has passed on the current revision. A stale proof from an older commit is refused. `waiver="<reason>"` closes it anyway and records the reason.
- A status report at session start: active work, blocked tickets, tickets whose blockers are now resolved, stale actives, and a work-in-progress warning.
- An ADR index that reads decision records out of configured repos and links them to tickets.
- A local web UI on `127.0.0.1:7117`, read-only until `api_token` is set.
- Lock-and-atomic-replace persistence in `state_store.py`, with a guard that refuses a write that would empty the store, halve the ticket count, or lower `next_id`. Both rules came from incidents that removed real tickets.
- A GitHub activity sync that shells out to the `gh` CLI on demand.
- Packaged as a Claude Code plugin, installed as a dependency of [K-mem](https://github.com/PyGuy2000/k-mem).

[Unreleased]: https://github.com/PyGuy2000/devflow-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/PyGuy2000/devflow-mcp/releases/tag/v0.1.0
