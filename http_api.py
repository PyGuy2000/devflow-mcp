"""
DevFlow HTTP API — lightweight REST server for devflow.html.
Reads/writes the same devflow_state.json as the MCP server.

Run: python3 http_api.py
Serves on: http://localhost:7117
"""

import json
import os
import time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Concurrency-safe persistence (T-174): share the same locked, atomic store as
# server.py so the dashboard and MCP servers can't clobber each other.
from state_store import STATE_FILE, abort, mutate_state, next_ticket_id, read_state

_PRIORITIES = ("critical", "high", "medium", "low")
_STATUSES = ("backlog", "active", "blocked", "done")

from devflow_config import CONFIG_FILE as _CONFIG_FILE  # noqa: E402

_HTML_FILE = Path(__file__).resolve().parent / "devflow.html"


def _api_token() -> str:
    """Shared token required on mutating cross-origin routes.

    This server is CORS-open (Allow-Origin: *) for the dashboard, so an
    unauthenticated write endpoint would be drive-by-callable from any
    website the user has open. Fail-closed: no token configured → the
    ticket-create endpoint refuses. Read per-request so adding the token
    to config.json needs no restart.
    """
    try:
        with open(_CONFIG_FILE) as f:
            return (json.load(f).get("api_token") or "").strip()
    except (OSError, json.JSONDecodeError):
        return ""


def _resolve_project(state: dict, identifier: str):
    """Find a project by name or ID (mirror of server.py)."""
    for p in state["projects"]:
        if p["id"] == identifier or p["name"] == identifier:
            return p
    return None


def create_ticket(body: dict) -> tuple[dict, int]:
    """Create a ticket from an HTTP payload. Returns (response_json, status).

    Browser-facing twin of server.py's add_ticket, for an external admin page
    that turns an approved recommendation into a ticket. No blocked_by
    support — dependencies stay an MCP/agent concern.
    """
    title = (body.get("title") or "").strip()
    why = (body.get("why") or "").strip()
    project = (body.get("project") or "").strip()
    priority = body.get("priority") or "medium"
    status = body.get("status") or "backlog"
    desc = body.get("desc") or ""

    if not title or not why or not project:
        return {"error": "title, why and project are required"}, 400
    if priority not in _PRIORITIES:
        return {"error": f"Invalid priority '{priority}'"}, 400
    if status not in _STATUSES:
        return {"error": f"Invalid status '{status}'"}, 400

    def _apply(state):
        proj = _resolve_project(state, project)
        if not proj:
            # abort() returns the result WITHOUT writing (server.py pattern)
            abort({"error": f"Project '{project}' not found"})
        now_ms = int(time.time() * 1000)
        ticket = {
            "id": next_ticket_id(state),
            "title": title,
            "why": why,
            "desc": desc,
            "projectId": proj["id"],
            "priority": priority,
            "status": status,
            "blockedBy": [],
            "blocksTickets": [],
            "created": now_ms,
            "log": [{"t": now_ms, "msg": "Ticket created (via HTTP API)"}],
        }
        state["tickets"].append(ticket)
        return {"success": True, "ticket": ticket}

    result = mutate_state(_apply)
    if "error" in result:
        return result, 404
    return result, 201


def load_state() -> dict:
    return read_state()


class DevFlowHandler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-DevFlow-Token")

    def _json_response(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            html_file = _HTML_FILE
            if html_file.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                # UI is read from disk per request — never let a browser cache
                # mask an update (stale-page incident, T-582).
                self.send_header("Cache-Control", "no-store")
                self._cors()
                self.end_headers()
                self.wfile.write(html_file.read_bytes())
            else:
                self._json_response({"error": "devflow.html not found"}, 404)

        elif self.path == "/api/state":
            state = load_state()
            self._json_response(state)

        elif self.path == "/api/adrs":
            # The ADR index backing the Decisions view. A derived cache built
            # from every repo's decisions.md — read-only, never part of state.
            # Same no-store reasoning as the HTML route (T-582): a browser
            # holding a stale index would show decisions that moved.
            from adr_index import load_index
            index = load_index()
            if not index:
                self._json_response(
                    {"error": "adr_index.json not built — run adr_index.py"}, 404
                )
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self._cors()
            self.end_headers()
            self.wfile.write(json.dumps(index).encode())

        elif self.path == "/api/health":
            self._json_response({"status": "ok", "state_file": str(STATE_FILE)})

        else:
            self._json_response({"error": "Not found"}, 404)

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}

        if self.path == "/api/state":
            # Full state replacement (used by devflow.html saveState).
            # Routed through the locked, atomic store so it serializes with
            # MCP writers and never leaves a torn file.
            #
            # STALE-WRITE GUARD (2026-07-28): this endpoint clobbers the whole
            # file, so a browser tab holding an old snapshot can erase every
            # ticket written since it loaded. That happened — a ~2026-07-13
            # snapshot wiped T-366..T-497 (132 tickets). `next_id` is
            # monotonic: it only ever increases, and never decreases through
            # any legitimate path (deleting a ticket does not reclaim its id).
            # So an incoming next_id BELOW the server's proves the payload is
            # a stale snapshot. Reject it rather than write it.
            #
            # The comparison runs INSIDE the mutator so it reads the fresh
            # state under the exclusive lock — a pre-read could race an MCP
            # write landing between the check and the replace. abort()
            # returns the result without writing anything.
            def _replace(state):
                incoming = body.get("next_id")
                current = state.get("next_id", 1)
                if not isinstance(incoming, int):
                    abort({
                        "error": "stale state rejected",
                        "detail": "payload carried no integer next_id",
                        "server_next_id": current,
                        "client_next_id": incoming,
                    })
                if incoming < current:
                    abort({
                        "error": "stale state rejected",
                        "detail": (
                            f"client next_id {incoming} is behind server "
                            f"next_id {current} — this snapshot predates "
                            f"{current - incoming} ticket id(s) and would "
                            f"erase them"
                        ),
                        "server_next_id": current,
                        "client_next_id": incoming,
                    })
                state.clear()
                state.update(body)
                return {"success": True}

            result = mutate_state(_replace)
            if "error" in result:
                self._json_response(result, 409)
            else:
                self._json_response(result)

        elif self.path == "/api/ticket":
            # Create a ticket from an external page's approved recommendation.
            # Token-gated: this server is CORS-open, so the write must carry
            # the shared secret only trusted admin pages hold.
            import hmac
            token = _api_token()
            if not token:
                self._json_response(
                    {"error": "ticket creation disabled (no api_token in config.json)"}, 403
                )
                return
            supplied = self.headers.get("X-DevFlow-Token", "")
            if not hmac.compare_digest(supplied, token):
                self._json_response({"error": "invalid or missing token"}, 401)
                return
            data, status_code = create_ticket(body)
            self._json_response(data, status_code)

        elif self.path == "/api/adrs/refresh":
            # Rebuild the ADR index from disk. Token-gated like /api/ticket:
            # the server is CORS-open, and this walks 34 repos, so it must not
            # be triggerable by any page that happens to be open.
            import hmac
            token = _api_token()
            if not token:
                self._json_response(
                    {"error": "refresh disabled (no api_token in config.json)"}, 403
                )
                return
            if not hmac.compare_digest(self.headers.get("X-DevFlow-Token", ""), token):
                self._json_response({"error": "invalid or missing token"}, 401)
                return
            try:
                from adr_index import build, write_index
                index = build(verbose=False)
                write_index(index)
            except Exception as exc:  # surface the reason, don't 500 blindly
                self._json_response({"error": f"index build failed: {exc}"}, 500)
                return
            self._json_response({
                "success": True,
                "counts": index["counts"],
                "generated": index["generated"],
            })

        elif self.path == "/api/ticket/status":
            # Update a single ticket's status
            ticket_id = body.get("ticketId")
            new_status = body.get("status")
            if not ticket_id or not new_status:
                self._json_response({"error": "ticketId and status required"}, 400)
                return

            now_ms = int(time.time() * 1000)

            def _update(state):
                found = False
                updated_tickets = []
                for t in state["tickets"]:
                    if t["id"] == ticket_id:
                        found = True
                        old_status = t["status"]
                        t = {
                            **t,
                            "status": new_status,
                            "log": [*t["log"], {"t": now_ms, "msg": f"Status: {old_status} → {new_status}"}],
                        }
                    updated_tickets.append(t)
                state["tickets"] = updated_tickets
                return {"found": found}

            outcome = mutate_state(_update)

            if not outcome["found"]:
                self._json_response({"error": f"Ticket {ticket_id} not found"}, 404)
                return

            self._json_response({"success": True, "ticketId": ticket_id, "status": new_status})

        else:
            self._json_response({"error": "Not found"}, 404)

    def log_message(self, format, *args):
        # Suppress request logging
        pass


def main():
    port = 7117
    # Threading: a single browser keep-alive connection wedged the old
    # single-threaded HTTPServer — every later request (and the kanban itself)
    # hung behind it (T-582 incident, 2026-08-07).
    server = ThreadingHTTPServer(("127.0.0.1", port), DevFlowHandler)
    print(f"[DevFlow HTTP] Serving on http://localhost:{port}")
    print(f"[DevFlow HTTP] State file: {STATE_FILE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[DevFlow HTTP] Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
