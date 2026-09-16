#!/usr/bin/env bash
# The private install is this public repo plus an overlay. This script keeps
# the shared part identical in both directions and never moves the overlay.
#
#   SHARED   every file listed in FILES below: code, tests, hook, UI, example config.
#            Edit it HERE (the public repo), then `./sync.sh install`.
#   OVERLAY  private-only, never copied either way:
#              config.json           the live settings (paths, api_token)
#              devflow_state.json    the live tickets (+ backups/, *.lock, logs/)
#              adr_index.json        derived cache
#              projecthub_bridge.py  the private SQLite bridge
#              README.md             the private install's own notes
#              recovery-*/           incident records
#
# Every path that used to be a private code hunk (the ProjectHub DB, the ADR
# repo roots, the overrides file, the domain table) now lives in config.json,
# so there is nothing left to sanitize: the shared files are byte-identical.
#
#   ./sync.sh install   copy repo -> live
#   ./sync.sh pull      copy live -> repo   (shared files only; the overlay never comes back)
#   ./sync.sh diff      show what differs

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIVE="${DEVFLOW_LIVE_DIR:-$HOME/.config/devflow-mcp}"
FILES=(server.py state_store.py devflow_config.py http_api.py
       github_activity_sync.py adr_parse.py adr_categories.py adr_index.py adr_apply_review.py
       devflow.html config.example.json hooks/session-status.py hooks/hooks.json
       test_tools.py test_verifier.py test_stale_write_guard.py test_concurrency.py test_adr_config.py)

case "${1:-}" in
  install)
    mkdir -p "$LIVE/hooks"
    for f in "${FILES[@]}"; do
      [ -f "$REPO/$f" ] || continue
      cp "$REPO/$f" "$LIVE/$f"
    done
    echo "repo -> live. Restart to load: systemctl --user restart devflow-http.service"
    echo "MCP tool changes need a new Claude Code session (servers are per-session)."
    ;;
  pull)
    for f in "${FILES[@]}"; do
      [ -f "$LIVE/$f" ] || continue
      cp "$LIVE/$f" "$REPO/$f"
    done
    echo "live -> repo (shared files only). Review with: git diff"
    ;;
  diff)
    rc=0
    for f in "${FILES[@]}"; do
      if [ -f "$LIVE/$f" ] && [ -f "$REPO/$f" ]; then
        diff -u "$REPO/$f" "$LIVE/$f" && echo "  same: $f" || rc=1
      elif [ -f "$REPO/$f" ]; then
        echo "  missing in live: $f"; rc=1
      fi
    done
    exit $rc
    ;;
  *)
    echo "usage: $0 {install|pull|diff}" >&2; exit 2 ;;
esac
