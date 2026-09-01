#!/usr/bin/env python3
"""
Acceptance test for T-174 — DevFlow state corruption under concurrent servers.

Proves the shared persistence helper (state_store.mutate_state) is safe when N
processes mutate the same state file at once, which is exactly what Claude Code
does: one stdio server.py per session, all bound to ~/.config/devflow-mcp/
devflow_state.json.

This drives the REAL persistence path (state_store.mutate_state + next_ticket_id)
— the single code path every MCP/HTTP mutation now funnels through — against an
ISOLATED temp state file (via the DEVFLOW_STATE_FILE env override). It never
touches the real devflow_state.json.

Asserts:
  (a) zero tickets lost          — final count == sum of all adds, IDs contiguous
  (b) next_id monotonic, no dups — no duplicate IDs, next_id == max(id) + 1
  (c) kill/restart mid-run       — nothing already committed is lost, no torn file

Run:  python3 test_concurrency.py
Exit: 0 on success, 1 on any failure. Repeatable.
"""

import json
import os
import signal
import sys
import tempfile
import time
from multiprocessing import get_context

# ── Isolate the store BEFORE importing state_store ──────────────────────────────
_TMPDIR = tempfile.mkdtemp(prefix="devflow_acctest_")
_STATE_PATH = os.path.join(_TMPDIR, "devflow_state.json")
os.environ["DEVFLOW_CONFIG_DIR"] = _TMPDIR
os.environ["DEVFLOW_STATE_FILE"] = _STATE_PATH

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from state_store import mutate_state, next_ticket_id, read_state  # noqa: E402

PROJECT_ID = "proj-test"


def _seed():
    """Reset the temp store to a single empty project."""
    state = {
        "projects": [{"id": PROJECT_ID, "name": "test", "goal": "acc test", "color": "#000"}],
        "tickets": [],
        "next_id": 1,
    }
    with open(_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)
    # clear any stale committed logs
    for fn in os.listdir(_TMPDIR):
        if fn.startswith("committed_"):
            os.unlink(os.path.join(_TMPDIR, fn))


def _add_ticket(worker_id, n):
    """Mirror server.add_ticket's persistence path: allocate ID + append, under lock."""
    def _apply(state):
        tid = next_ticket_id(state)
        now_ms = int(time.time() * 1000)
        ticket = {
            "id": tid,
            "title": f"w{worker_id}-{n}",
            "why": "acc test",
            "desc": "",
            "projectId": PROJECT_ID,
            "priority": "medium",
            "status": "backlog",
            "blockedBy": [],
            "blocksTickets": [],
            "created": now_ms,
            "log": [{"t": now_ms, "msg": "Ticket created"}],
        }
        state["tickets"] = [*state["tickets"], ticket]
        return tid

    return mutate_state(_apply)


def _worker(worker_id, count, per_iter_sleep=0.0):
    """Add `count` tickets, durably logging each committed ID right after it lands."""
    log_path = os.path.join(_TMPDIR, f"committed_w{worker_id}.log")
    with open(log_path, "w") as f:
        for n in range(count):
            tid = _add_ticket(worker_id, n)
            f.write(tid + "\n")
            f.flush()
            os.fsync(f.fileno())
            if per_iter_sleep:
                time.sleep(per_iter_sleep)


def _numeric(tid):
    return int(tid.split("-")[1])


def _read_committed_ids():
    ids = []
    for fn in os.listdir(_TMPDIR):
        if fn.startswith("committed_"):
            with open(os.path.join(_TMPDIR, fn)) as f:
                ids += [ln.strip() for ln in f if ln.strip()]
    return ids


# ── Tests ───────────────────────────────────────────────────────────────────────


def test_concurrent_no_loss(ctx, workers=5, per=25):
    print(f"\n[TEST 1] {workers} workers × {per} concurrent add-ticket = {workers * per} adds")
    _seed()
    procs = [ctx.Process(target=_worker, args=(w, per)) for w in range(workers)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()

    expected = workers * per
    state = read_state()
    ids = [t["id"] for t in state["tickets"]]
    nums = sorted(_numeric(i) for i in ids)

    ok = True

    if len(ids) != expected:
        print(f"  FAIL (a): expected {expected} tickets, found {len(ids)} — tickets LOST")
        ok = False
    else:
        print(f"  PASS (a): zero loss — {len(ids)} tickets == {expected} adds")

    dups = {i for i in ids if ids.count(i) > 1}
    if dups:
        print(f"  FAIL (b): duplicate IDs: {sorted(dups)}")
        ok = False
    elif nums != list(range(1, expected + 1)):
        print(f"  FAIL (b): IDs not contiguous/monotonic 1..{expected}")
        ok = False
    elif state["next_id"] != expected + 1:
        print(f"  FAIL (b): next_id={state['next_id']}, expected {expected + 1}")
        ok = False
    else:
        print(f"  PASS (b): no dups, IDs 1..{expected} contiguous, next_id={state['next_id']}")

    return ok


def test_kill_restart(ctx, survivors=4, per=60):
    print(f"\n[TEST 2] kill a worker mid-run + restart a replacement")
    _seed()

    # Long-ish runs so the kill reliably lands mid-flight.
    procs = [ctx.Process(target=_worker, args=(w, per, 0.004)) for w in range(survivors)]
    victim = ctx.Process(target=_worker, args=(99, per, 0.004))
    for p in procs:
        p.start()
    victim.start()

    time.sleep(0.08)  # let everyone get a few writes in
    os.kill(victim.pid, signal.SIGKILL)
    victim.join()
    killed_committed_at_death = len(_read_committed_ids())  # snapshot incl. victim partial

    # Restart a replacement worker that does its own batch.
    replacement = ctx.Process(target=_worker, args=(100, per, 0.0))
    replacement.start()

    for p in procs:
        p.join()
    replacement.join()

    state = read_state()
    ids = [t["id"] for t in state["tickets"]]
    id_set = set(ids)
    committed = _read_committed_ids()
    nums = sorted(_numeric(i) for i in ids)

    ok = True

    # File must be valid (already parsed) and have no torn/duplicate IDs.
    dups = {i for i in ids if ids.count(i) > 1}
    if dups:
        print(f"  FAIL: duplicate IDs after kill: {sorted(dups)}")
        ok = False
    else:
        print(f"  PASS: no duplicate IDs ({len(ids)} tickets total)")

    # Every ID a worker logged as committed must survive (incl. victim's partial).
    missing = [c for c in committed if c not in id_set]
    if missing:
        print(f"  FAIL (c): {len(missing)} committed tickets LOST: {missing[:10]}")
        ok = False
    else:
        print(f"  PASS (c): all {len(committed)} committed tickets present "
              f"(victim had logged {killed_committed_at_death} before SIGKILL)")

    # Monotonic counter: next_id past the highest issued ID, contiguous from 1.
    if nums and nums != list(range(1, len(nums) + 1)):
        print(f"  FAIL: IDs not contiguous 1..{len(nums)} (gaps/dupes)")
        ok = False
    elif state["next_id"] != (max(nums) + 1 if nums else 1):
        print(f"  FAIL: next_id={state['next_id']} != max+1={max(nums) + 1 if nums else 1}")
        ok = False
    else:
        print(f"  PASS: counter monotonic, next_id={state['next_id']}")

    return ok


def main():
    ctx = get_context("fork")
    print(f"DevFlow concurrency acceptance test (T-174)")
    print(f"Isolated store: {_STATE_PATH}")

    results = []
    # Run TEST 1 a few times to shake out races repeatably.
    for i in range(3):
        print(f"\n=== TEST 1 round {i + 1}/3 ===")
        results.append(test_concurrent_no_loss(ctx))
    results.append(test_kill_restart(ctx))

    print("\n" + "=" * 60)
    if all(results):
        print("RESULT: ALL ACCEPTANCE CRITERIA PASSED ✓")
        # cleanup temp dir on success
        import shutil
        shutil.rmtree(_TMPDIR, ignore_errors=True)
        sys.exit(0)
    else:
        print(f"RESULT: FAILURES ({results.count(False)}/{len(results)} runs failed) ✗")
        print(f"Temp store left for inspection: {_TMPDIR}")
        sys.exit(1)


if __name__ == "__main__":
    main()
