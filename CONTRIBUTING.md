# Contributing

Thanks for looking. This is a small project that tracks work for AI coding agents, and it has a strong opinion about proof, so a few things are worth saying up front.

## Getting set up

```
git clone https://github.com/PyGuy2000/devflow-mcp.git
cd devflow-mcp
python3 -m pip install mcp pytest
```

Python 3.11 is the floor. CI runs 3.11 and 3.12. The only runtime dependency is `mcp`; everything else is standard library, and it should stay that way.

## Running the tests

Each test file is a standalone script, not a pytest suite. Running them together under one collection lets one file's imports leak into another's, because `server` and `state_store` bind their state-file path once, at first import. Run them one at a time:

```
python3 test_concurrency.py
python3 test_stale_write_guard.py
python3 test_tools.py
python3 test_verifier.py
python3 test_adr_config.py
python3 test_mcp_stdio.py
```

Each builds its own temp state file and cleans up after itself. None of them touch your real `devflow_state.json`. CI runs all six as separate steps so a red run names the suite that broke.

## The state file is the thing to be careful with

Two real incidents shaped this code, and both are now tests.

A stale browser tab replayed an old snapshot and erased 132 tickets. A second server process started on the same machine and writes from both interleaved. So every write goes through a lock-and-atomic-replace path in `state_store.py`, and every write is checked against the on-disk store before it lands. A write is refused if it would empty a non-empty store, halve the ticket count, or lower the monotonic `next_id`.

If you change anything that writes state, `test_concurrency.py` and `test_stale_write_guard.py` must still pass, and a new guard needs a test that fails without it.

## A gate ships with the case that trips it

`test_verifier.py` is the pattern. Every refusal the verification gate can issue has a test that produces it: no proof, a failed command, a proof from an older revision, a waived close. A guard with no failing case is not a guard.

## Before you open a pull request

```
python3 test_concurrency.py && python3 test_stale_write_guard.py && \
python3 test_tools.py && python3 test_verifier.py && \
python3 test_adr_config.py && python3 test_mcp_stdio.py
claude plugin validate . --strict
```

`claude plugin validate` passes some manifests the runtime refuses, so a change to `.claude-plugin/plugin.json`, `.mcp.json` or `hooks/hooks.json` needs a real install to prove it. Install the plugin from a clean checkout and open a session before you call it done.

Never declare `hooks/hooks.json` in the plugin manifest. Claude Code discovers that file on its own, and declaring it as well makes the runtime report a duplicate and load nothing. Validation does not catch this.

## Writing

Documentation and commit bodies get the same care as code. Be specific, lead with the answer, and skip the decorative vocabulary. Say what a change does and what it fixes, with the evidence.

## Reporting a bug

Open an issue with your Python version, your operating system, and what you expected. If it involves lost or duplicated tickets, say how many server processes were running and whether a browser tab was open on the web UI. Those two questions have explained every state bug so far.

For anything with a security dimension, read [SECURITY.md](SECURITY.md) first.
