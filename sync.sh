#!/usr/bin/env bash
# Sync this git checkout to the live install, or pull the live install back.
#
# ~/.config/devflow-mcp is what actually runs (MCP server + HTTP API).
# ~/devflow-mcp is the public git repo. They are NOT identical by design:
# server.py carries two sanitized hunks here (a generic module docstring and
# an empty projecthub_db_path default) so no local path ships publicly.
#
# Before this script existed the two copies drifted silently and nobody could
# tell an intentional sanitization from a lost edit.
#
#   ./sync.sh install   copy repo -> live   (re-applies the real db path)
#   ./sync.sh pull      copy live -> repo   (re-applies the sanitization)
#   ./sync.sh diff      show what differs, ignoring the sanitized hunks

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIVE="$HOME/.config/devflow-mcp"
FILES=(server.py state_store.py http_api.py projecthub_bridge.py
       github_activity_sync.py adr_parse.py adr_categories.py adr_index.py adr_apply_review.py
       devflow.html)

PUBLIC_DOC='Claude Desktop, and any MCP-compatible client. Optionally syncs
side-effects to a ProjectHub SQLite database for portfolio tracking.'
LIVE_DOC='Claude Desktop, and Clutch bot. Syncs side-effects to ProjectHub'"'"'s
SQLite database for automatic portfolio tracking.'
LIVE_DB='    "projecthub_db_path": os.path.expanduser(
        "~/python/projects/python_project_tracker/data/projecthub.db"
    ),'
PUBLIC_DB='    "projecthub_db_path": "",'

retarget() {  # $1 = file, $2 = "live" | "public"
  python3 - "$1" "$2" <<'PY'
import os, pathlib, sys
path, mode = pathlib.Path(sys.argv[1]), sys.argv[2]
s = path.read_text()
pub_doc, live_doc = os.environ["PUBLIC_DOC"], os.environ["LIVE_DOC"]
pub_db, live_db = os.environ["PUBLIC_DB"], os.environ["LIVE_DB"]
if mode == "live":
    s = s.replace(pub_doc, live_doc).replace(pub_db, live_db)
else:
    s = s.replace(live_doc, pub_doc).replace(live_db, pub_db)
path.write_text(s)
PY
}
export PUBLIC_DOC LIVE_DOC LIVE_DB PUBLIC_DB

case "${1:-}" in
  install)
    for f in "${FILES[@]}"; do
      [ -f "$REPO/$f" ] || continue
      cp "$REPO/$f" "$LIVE/$f"
    done
    retarget "$LIVE/server.py" live
    echo "repo -> live. Restart to load: systemctl --user restart devflow-http.service"
    echo "MCP tool changes need a new Claude Code session (servers are per-session)."
    ;;
  pull)
    for f in "${FILES[@]}"; do
      [ -f "$LIVE/$f" ] || continue
      cp "$LIVE/$f" "$REPO/$f"
    done
    retarget "$REPO/server.py" public
    echo "live -> repo, sanitization re-applied. Review with: git diff"
    ;;
  diff)
    tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
    for f in "${FILES[@]}"; do
      [ -f "$LIVE/$f" ] && [ -f "$REPO/$f" ] || continue
      cp "$LIVE/$f" "$tmp/$f"
      [ "$f" = server.py ] && retarget "$tmp/$f" public
      diff -u "$REPO/$f" "$tmp/$f" && echo "  same: $f"
    done
    ;;
  *)
    echo "usage: $0 {install|pull|diff}" >&2; exit 2 ;;
esac
