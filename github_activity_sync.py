#!/usr/bin/env python3
"""
GitHub Activity Sync for ProjectHub.

Polls GitHub repos for commits and PRs, writes events to projecthub.db,
and updates summary columns on the projects table. Designed to run as a
cron job (every 30 minutes is a reasonable interval).

Usage:
    python github_activity_sync.py                       # full sync
    python github_activity_sync.py --verbose              # full sync with debug output
    python github_activity_sync.py --dry-run              # show what would be written
    python github_activity_sync.py --repo you/some-repo   # single repo
    python github_activity_sync.py --user someone-else    # override the GitHub user (default: gh's authenticated user)
"""

import json
import logging
import re
import sqlite3
import subprocess
import sys
from argparse import ArgumentParser
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

DB_PATH = Path.home() / ".config/devflow-mcp/projecthub.db"
LOG_PATH = Path.home() / ".config/devflow-mcp/logs/github_activity_sync.log"

logger = logging.getLogger("github_activity_sync")


def setup_logging(verbose: bool = False) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    if verbose:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger.addHandler(console)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)


# ── GitHub API via gh CLI ─────────────────────────────────────────────────


def gh_api(endpoint: str, paginate: bool = False) -> list | dict | None:
    """Call GitHub API via gh CLI. Returns parsed JSON or None on failure."""
    cmd = ["gh", "api", endpoint, "--cache", "5m"]
    if paginate:
        cmd.append("--paginate")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logger.warning("gh api %s failed: %s", endpoint, result.stderr.strip())
            return None
        return json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        logger.warning("gh api %s error: %s", endpoint, exc)
        return None


def check_gh_auth() -> bool:
    result = subprocess.run(
        ["gh", "auth", "status"], capture_output=True, text=True, timeout=10
    )
    return result.returncode == 0


def current_gh_user() -> Optional[str]:
    """Resolve the authenticated gh CLI user, so a GitHub username never has to be hardcoded."""
    data = gh_api("/user")
    return data.get("login") if isinstance(data, dict) else None


# ── Database ──────────────────────────────────────────────────────────────


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create github_activity table and add summary columns if missing."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS github_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            github_repo TEXT NOT NULL,
            event_type TEXT NOT NULL CHECK(event_type IN (
                'commit', 'pr_opened', 'pr_merged', 'pr_closed', 'release'
            )),
            event_id TEXT NOT NULL,
            title TEXT,
            author TEXT,
            event_date TEXT NOT NULL,
            branch TEXT,
            url TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL,
            UNIQUE(github_repo, event_type, event_id)
        );

        CREATE INDEX IF NOT EXISTS idx_github_activity_project
            ON github_activity(project_id);
        CREATE INDEX IF NOT EXISTS idx_github_activity_date
            ON github_activity(event_date);
    """)

    # Add summary columns to projects if missing
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)").fetchall()}
    migrations = [
        ("last_commit_date", "TEXT"),
        ("open_pr_count", "INTEGER DEFAULT 0"),
        ("commits_this_week", "INTEGER DEFAULT 0"),
    ]
    for col_name, col_type in migrations:
        if col_name not in cols:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {col_name} {col_type}")
            logger.info("Added column projects.%s", col_name)

    conn.commit()


# ── Repo ↔ Project Mapping ───────────────────────────────────────────────


def normalize_github_url(url: str) -> str:
    """Extract 'owner/repo' from a GitHub URL, lowercased."""
    if not url:
        return ""
    url = url.strip().rstrip("/")
    url = re.sub(r"\.git$", "", url)
    match = re.search(r"github\.com[/:]([^/]+/[^/]+)", url)
    return match.group(1).lower() if match else ""


def slugify(name: str) -> str:
    """Normalize a project name to a GitHub-style slug."""
    return name.lower().replace(" ", "-").replace("_", "-")


def build_repo_mapping(conn: sqlite3.Connection, github_user: str) -> dict[str, int]:
    """Map normalized 'owner/repo' → project_id."""
    rows = conn.execute(
        "SELECT id, name, git_repo_url FROM projects WHERE archived_at IS NULL"
    ).fetchall()

    owner = github_user.lower()
    mapping: dict[str, int] = {}
    for row in rows:
        # Primary: match by git_repo_url
        if row["git_repo_url"]:
            key = normalize_github_url(row["git_repo_url"])
            if key:
                mapping[key] = row["id"]

        name_lower = row["name"].lower()

        # Fallback 1: slugified name (spaces/underscores → hyphens)
        slug = slugify(row["name"])
        mapping.setdefault(f"{owner}/{slug}", row["id"])

        # Fallback 2: original name with underscores preserved (lowercased)
        underscored = name_lower.replace(" ", "_")
        mapping.setdefault(f"{owner}/{underscored}", row["id"])

        # Fallback 3: exact lowercased name (handles already-matching names)
        mapping.setdefault(f"{owner}/{name_lower}", row["id"])

    return mapping


# ── Fetch & Store ─────────────────────────────────────────────────────────


def fetch_commits(repo: str) -> list[dict]:
    """Fetch recent commits to the default branch."""
    data = gh_api(f"/repos/{repo}/commits?per_page=30")
    if not data or not isinstance(data, list):
        return []

    events = []
    for c in data:
        commit = c.get("commit", {})
        author_info = commit.get("author", {})
        events.append({
            "github_repo": repo,
            "event_type": "commit",
            "event_id": c.get("sha", "")[:12],
            "title": (commit.get("message") or "").split("\n")[0][:200],
            "author": author_info.get("name", ""),
            "event_date": author_info.get("date", ""),
            "branch": None,
            "url": c.get("html_url", ""),
        })
    return events


def fetch_prs(repo: str) -> list[dict]:
    """Fetch open + recently closed PRs."""
    events = []

    # Open PRs
    open_prs = gh_api(f"/repos/{repo}/pulls?state=open&per_page=30")
    if open_prs and isinstance(open_prs, list):
        for pr in open_prs:
            events.append({
                "github_repo": repo,
                "event_type": "pr_opened",
                "event_id": str(pr.get("number", "")),
                "title": (pr.get("title") or "")[:200],
                "author": (pr.get("user") or {}).get("login", ""),
                "event_date": pr.get("created_at", ""),
                "branch": (pr.get("head") or {}).get("ref", ""),
                "url": pr.get("html_url", ""),
            })

    # Recently closed/merged PRs
    closed_prs = gh_api(
        f"/repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=10"
    )
    if closed_prs and isinstance(closed_prs, list):
        for pr in closed_prs:
            event_type = "pr_merged" if pr.get("merged_at") else "pr_closed"
            events.append({
                "github_repo": repo,
                "event_type": event_type,
                "event_id": str(pr.get("number", "")),
                "title": (pr.get("title") or "")[:200],
                "author": (pr.get("user") or {}).get("login", ""),
                "event_date": pr.get("merged_at") or pr.get("closed_at", ""),
                "branch": (pr.get("head") or {}).get("ref", ""),
                "url": pr.get("html_url", ""),
            })

    return events


def store_events(
    conn: sqlite3.Connection,
    events: list[dict],
    repo_mapping: dict[str, int],
    dry_run: bool = False,
) -> int:
    """INSERT OR IGNORE events into github_activity. Returns count of new rows."""
    inserted = 0
    for event in events:
        repo_key = event["github_repo"].lower()
        project_id = repo_mapping.get(repo_key)

        if dry_run:
            status = "MATCHED" if project_id else "UNMATCHED"
            logger.debug(
                "  [DRY-RUN] %s %s %s %s → %s",
                event["event_type"],
                event["event_id"][:8],
                event["github_repo"],
                event["title"][:60],
                status,
            )
            inserted += 1
            continue

        cursor = conn.execute(
            """INSERT OR IGNORE INTO github_activity
               (project_id, github_repo, event_type, event_id,
                title, author, event_date, branch, url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                event["github_repo"],
                event["event_type"],
                event["event_id"],
                event["title"],
                event["author"],
                event["event_date"],
                event["branch"],
                event["url"],
            ),
        )
        if cursor.rowcount > 0:
            inserted += 1

    return inserted


def update_project_summaries(conn: sqlite3.Connection) -> None:
    """Recalculate summary columns on the projects table from github_activity."""
    conn.execute("""
        UPDATE projects SET
            last_commit_date = (
                SELECT MAX(event_date) FROM github_activity
                WHERE project_id = projects.id AND event_type = 'commit'
            ),
            open_pr_count = (
                SELECT COUNT(*) FROM github_activity
                WHERE project_id = projects.id AND event_type = 'pr_opened'
            ),
            commits_this_week = (
                SELECT COUNT(*) FROM github_activity
                WHERE project_id = projects.id AND event_type = 'commit'
                AND event_date >= datetime('now', '-7 days')
            ),
            updated_at = datetime('now')
        WHERE id IN (
            SELECT DISTINCT project_id FROM github_activity
            WHERE project_id IS NOT NULL
        )
    """)
    conn.commit()


# ── Repo Discovery ────────────────────────────────────────────────────────


def list_user_repos(single_repo: Optional[str] = None) -> list[str]:
    """Return list of repos to sync."""
    if single_repo:
        return [single_repo]

    data = gh_api("/user/repos?per_page=100&type=owner&sort=pushed", paginate=True)
    if not data or not isinstance(data, list):
        logger.error("Failed to list repos")
        return []

    repos = []
    for r in data:
        if r.get("fork"):
            continue
        if r.get("archived"):
            continue
        repos.append(r["full_name"])

    logger.info("Discovered %d repos (excluding forks/archived)", len(repos))
    return repos


# ── Main ──────────────────────────────────────────────────────────────────


def sync(
    db_path: Path = DB_PATH,
    github_user: Optional[str] = None,
    single_repo: Optional[str] = None,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    setup_logging(verbose)
    logger.info("=== GitHub Activity Sync starting ===")

    if not check_gh_auth():
        logger.error("gh CLI not authenticated — run 'gh auth login' first")
        sys.exit(1)

    if not db_path.exists():
        logger.error("ProjectHub DB not found: %s", db_path)
        sys.exit(1)

    if not github_user:
        github_user = current_gh_user()
        if not github_user:
            logger.error("Could not resolve GitHub user — pass --user explicitly")
            sys.exit(1)

    conn = connect(db_path)
    ensure_schema(conn)

    repo_mapping = build_repo_mapping(conn, github_user)
    logger.info("Loaded %d project mappings", len(repo_mapping))

    repos = list_user_repos(single_repo)
    if not repos:
        logger.warning("No repos to sync")
        conn.close()
        return

    total_new = 0
    unmatched_repos = []

    for repo in repos:
        logger.debug("Syncing %s ...", repo)
        events = fetch_commits(repo) + fetch_prs(repo)

        if not events:
            logger.debug("  No events for %s", repo)
            continue

        new = store_events(conn, events, repo_mapping, dry_run)
        total_new += new

        repo_key = repo.lower()
        if repo_key not in repo_mapping:
            unmatched_repos.append(repo)

        if not dry_run:
            conn.commit()

    if not dry_run:
        # Backfill project_id on orphaned events that now have a mapping
        for repo_key, project_id in repo_mapping.items():
            owner_repo = repo_key  # already lowercased
            conn.execute(
                """UPDATE github_activity
                   SET project_id = ?
                   WHERE project_id IS NULL
                   AND LOWER(github_repo) = ?""",
                (project_id, owner_repo),
            )
        conn.commit()

        update_project_summaries(conn)
        # Clean up stale pr_opened events for PRs that are now closed/merged
        conn.execute("""
            DELETE FROM github_activity
            WHERE event_type = 'pr_opened'
              AND (github_repo, event_id) IN (
                  SELECT github_repo, event_id FROM github_activity
                  WHERE event_type IN ('pr_merged', 'pr_closed')
              )
        """)
        conn.commit()

    conn.close()

    logger.info(
        "Sync complete: %d new events across %d repos", total_new, len(repos)
    )

    if unmatched_repos:
        logger.info("--- Unmatched repos (no ProjectHub project) ---")
        for repo in sorted(unmatched_repos):
            logger.info("  %s", repo)
        logger.info(
            "To link, set git_repo_url on the matching project in ProjectHub."
        )


def main() -> None:
    parser = ArgumentParser(description="Sync GitHub activity to ProjectHub")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--verbose", action="store_true", help="Debug output to console")
    parser.add_argument("--repo", type=str, help="Sync a single repo (owner/name)")
    parser.add_argument(
        "--user", type=str, default=None,
        help="GitHub username for repo-slug fallback matching (default: gh's authenticated user)",
    )
    parser.add_argument(
        "--db", type=str, default=str(DB_PATH), help="Path to projecthub.db"
    )
    args = parser.parse_args()

    sync(
        db_path=Path(args.db),
        github_user=args.user,
        single_repo=args.repo,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
