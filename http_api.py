"""
DevFlow HTTP API — lightweight REST server for devflow.html.
Reads/writes the same devflow_state.json as the MCP server.

Run: python3 http_api.py
Serves on: http://localhost:7117
"""

import json
import os
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

STATE_FILE = Path(os.path.expanduser("~/.config/devflow-mcp/devflow_state.json"))

DEFAULT_STATE = {"projects": [], "tickets": [], "next_id": 1}


def load_state() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return {**DEFAULT_STATE, **json.load(f)}
    return dict(DEFAULT_STATE)


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


class DevFlowHandler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

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
            html_file = Path(os.path.expanduser("~/.config/devflow-mcp/devflow.html"))
            if html_file.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self._cors()
                self.end_headers()
                self.wfile.write(html_file.read_bytes())
            else:
                self._json_response({"error": "devflow.html not found"}, 404)

        elif self.path == "/api/state":
            state = load_state()
            self._json_response(state)

        elif self.path == "/api/health":
            self._json_response({"status": "ok", "state_file": str(STATE_FILE)})

        else:
            self._json_response({"error": "Not found"}, 404)

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(content_length)) if content_length else {}

        if self.path == "/api/state":
            # Full state replacement (used by devflow.html saveState)
            save_state(body)
            self._json_response({"success": True})

        elif self.path == "/api/ticket/status":
            # Update a single ticket's status
            ticket_id = body.get("ticketId")
            new_status = body.get("status")
            if not ticket_id or not new_status:
                self._json_response({"error": "ticketId and status required"}, 400)
                return

            state = load_state()
            now_ms = int(time.time() * 1000)
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

            if not found:
                self._json_response({"error": f"Ticket {ticket_id} not found"}, 404)
                return

            state["tickets"] = updated_tickets
            save_state(state)
            self._json_response({"success": True, "ticketId": ticket_id, "status": new_status})

        else:
            self._json_response({"error": "Not found"}, 404)

    def log_message(self, format, *args):
        # Suppress request logging
        pass


def main():
    port = 7117
    server = HTTPServer(("127.0.0.1", port), DevFlowHandler)
    print(f"[DevFlow HTTP] Serving on http://localhost:{port}")
    print(f"[DevFlow HTTP] State file: {STATE_FILE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[DevFlow HTTP] Shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
