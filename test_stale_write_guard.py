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
import socket
import subprocess
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


TOKEN = "test-token"


def post(port, payload, path="/api/state", headers=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-DevFlow-Token": TOKEN}
        if headers is None else headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def check_write_gate(port):
    """Every write route refuses a caller with no token, and changes nothing."""
    good = {"projects": [], "tickets": [{"id": "T-495", "status": "backlog", "log": []}], "next_id": 900}
    status_body = {"ticketId": "T-495", "status": "done"}
    routes = [
        ("/api/state", good),
        ("/api/ticket/status", status_body),
        ("/api/ticket", {"title": "t", "why": "w", "project": "proj"}),
        ("/api/adrs/refresh", {}),
    ]
    json_only = {"Content-Type": "application/json"}

    print("6. no token, wrong token, non-ASCII token: refused on every write route")
    before = disk()
    for path, body in routes:
        st, _ = post(port, body, path, json_only)
        check(f"{path} no token -> 401", st == 401, f"got {st}")
        st, _ = post(port, body, path, {**json_only, "X-DevFlow-Token": "wrong"})
        check(f"{path} wrong token -> 401", st == 401, f"got {st}")
        st, _ = post(port, body, path, {**json_only, "X-DevFlow-Token": "t\u00e9st"})
        check(f"{path} non-ASCII token -> 401", st == 401, f"got {st}")
    check("disk unchanged", disk() == before)

    print("7. a text/plain POST, the kind a web page can send with no preflight")
    st, _ = post(port, good, "/api/state", {"Content-Type": "text/plain"})
    check("refused 401", st == 401, f"got {st}")
    check("disk unchanged", disk() == before)

    print("8. no token configured: every write route fails closed")
    configured = http_api._api_token
    http_api._api_token = lambda: ""
    for path, body in routes:
        st, _ = post(port, body, path)
        check(f"{path} -> 403", st == 403, f"got {st}")
        st, _ = post(port, body, path, {**json_only, "X-DevFlow-Token": ""})
        check(f"{path} empty token -> 403", st == 403, f"got {st}")
    http_api._api_token = configured
    check("disk unchanged", disk() == before)

    print("9. the right token still writes")
    seed_status()
    st, _ = post(port, status_body, "/api/ticket/status")
    check("status route accepted", st == 200, f"got {st}")
    check("status written", disk()["tickets"][0]["status"] == "done")


def seed_status():
    def _seed(state):
        state["tickets"] = [{**t, "status": "backlog", "log": []} for t in state["tickets"]]
        return {}
    mutate_state(_seed)


def check_second_server_leaves_state_alone():
    """A second http_api.py on a taken port exits and never creates the state file.

    On 2026-08-20 a duplicate server's startup wrote a fresh state over 615
    tickets before it died on the taken port. main() must bind before any state
    access; this holds it there.
    """
    print("10. second http_api.py on a taken port: exits non-zero, no state file")
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        holder.bind(("127.0.0.1", 7117))
        holder.listen(1)
    except OSError:
        pass  # already taken by something else, which is just as good
    try:
        state_file = os.path.join(tempfile.mkdtemp(), "devflow_state.json")
        env = {**os.environ, "DEVFLOW_STATE_FILE": state_file}
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "http_api.py")
        try:
            proc = subprocess.run([sys.executable, script], env=env,
                                  capture_output=True, text=True, timeout=20)
            code, extra = proc.returncode, proc.stderr.strip()[-120:]
        except subprocess.TimeoutExpired:
            code, extra = None, "timed out after 20s"
        check("exits non-zero", code not in (None, 0), f"exit {code}; {extra}")
        check("state file still does not exist", not os.path.exists(state_file))
    finally:
        holder.close()


def main():
    http_api._api_token = lambda: TOKEN
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

    check_write_gate(port)
    check_second_server_leaves_state_alone()

    srv.shutdown()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
