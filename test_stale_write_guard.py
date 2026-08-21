"""Regression test for the POST /api/state stale-write guard.

Background (2026-07-28): POST /api/state replaces the WHOLE state file. A
browser tab holding an old snapshot POSTed it and erased T-366..T-497 — 132
tickets. Two things let that happen:

  1. devflow.html called saveState() from render(), so every re-render (including
     view-only ones: filters, sort, page init) pushed the tab's whole in-memory
     state to the server. Fixed by persisting from the mutation functions only.
  2. The server accepted any payload. Fixed by the guard under test here.

`next_id` is monotonic — it only ever increases, and no legitimate path lowers
it (deleting a ticket does not reclaim its id). So an incoming next_id below the
server's proves the payload is a stale snapshot.

Run: python3 test_stale_write_guard.py
"""

import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer

# Point the shared store at a throwaway file BEFORE importing it.
_TMPDIR = tempfile.mkdtemp()
os.environ["DEVFLOW_STATE_FILE"] = os.path.join(_TMPDIR, "devflow_state.json")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import http_api  # noqa: E402
from state_store import STATE_FILE, mutate_state  # noqa: E402

FAILURES = []


def check(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  ({extra})" if extra else ""))
    if not cond:
        FAILURES.append(name)


def seed(n_tickets=5, next_id=500):
    def _seed(state):
        state.clear()
        state.update({
            "projects": [{"id": "p1", "name": "proj", "goal": "g", "color": "#fff"}],
            "tickets": [{"id": f"T-{i:03d}"} for i in range(next_id - n_tickets, next_id)],
            "next_id": next_id,
        })
        return {}
    mutate_state(_seed)


def disk():
    with open(STATE_FILE) as f:
        return json.load(f)


def post(port, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/state",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def main():
    seed()
    srv = HTTPServer(("127.0.0.1", 0), http_api.DevFlowHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    print("1. stale snapshot (client next_id=366 < server 500) — the real clobber")
    st, resp = post(port, {"projects": [], "tickets": [{"id": "T-001"}], "next_id": 366})
    check("rejected 409", st == 409, f"got {st}")
    check("error is stale-state", resp.get("error") == "stale state rejected")
    check("disk keeps its 5 tickets", len(disk()["tickets"]) == 5)
    check("disk keeps next_id=500", disk()["next_id"] == 500)

    print("2. current snapshot (client next_id == server) — a normal status edit")
    st, _ = post(port, {"projects": [], "tickets": [{"id": f"T-{i:03d}"} for i in range(495, 500)], "next_id": 500})
    check("accepted 200", st == 200, f"got {st}")
    check("disk still 5 tickets", len(disk()["tickets"]) == 5)

    print("3. advanced snapshot (client created a ticket, next_id=501)")
    st, _ = post(port, {"projects": [], "tickets": [{"id": f"T-{i:03d}"} for i in range(495, 501)], "next_id": 501})
    check("accepted 200", st == 200, f"got {st}")
    check("disk now 6 tickets", len(disk()["tickets"]) == 6)
    check("disk next_id now 501", disk()["next_id"] == 501)

    print("4. payload with no next_id — fail closed")
    st, _ = post(port, {"projects": [], "tickets": []})
    check("rejected 409", st == 409, f"got {st}")
    check("disk untouched", len(disk()["tickets"]) == 6)

    print("5. payload with a non-integer next_id — fail closed")
    st, _ = post(port, {"projects": [], "tickets": [], "next_id": "501"})
    check("rejected 409", st == 409, f"got {st}")
    check("disk untouched", len(disk()["tickets"]) == 6)

    srv.shutdown()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
