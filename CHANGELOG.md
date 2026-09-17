# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- A CI workflow that runs all six test suites on Python 3.11 and 3.12, one suite per step so a red run names the file that broke.
- `CONTRIBUTING.md` and `SECURITY.md`.
- Brand images built by `scripts/make_brand_images.py`: a README banner, a social preview card, and a terminal card showing the verification gate, captured from a real run.

## [0.1.0] - 2026-09-16

First public release. 18 MCP tools over a single JSON state file.

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
