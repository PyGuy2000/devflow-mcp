#!/usr/bin/env python3
"""
ADR index builder — turns ~450 ADRs scattered across 34 repos into one JSON
file the DevFlow dashboard can render.

Why this exists: DevFlow tracks tickets well and decisions not at all. The
ADRs hold the actual development path, but they live in 34 separate
decisions.md files, every project restarts numbering at ADR-001 (so "ADR-013"
names three different decisions), and the only ADR-to-ticket link anywhere is
a string prefix in a ticket title.

What it produces: adr_index.json — every ADR with a collision-proof uid, a
Layer x Domain classification, resolved status, and typed edges to other ADRs
and to DevFlow tickets. It is a derived cache. Never hand-edit it; delete and
rebuild instead.

This module only ever READS devflow_state.json. It must never write it.

Usage:
    python3 adr_index.py                # build and write adr_index.json
    python3 adr_index.py --dry-run      # parse and report, write nothing
    python3 adr_index.py --review-csv out.csv   # dump keyword guesses to review
"""

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from adr_categories import DOMAIN_LABELS, LAYER_LABELS, LAYERS, classify
from adr_parse import load_decisions_per_file, parse_adrs
from state_store import CONFIG_DIR, read_state

INDEX_FILE = CONFIG_DIR / "adr_index.json"

# Where repos live. Each root is scanned one level deep, plus the root itself.
REPO_ROOTS = [
    Path.home() / "python" / "projects",
    Path.home(),
]

# Directories that contain ADRs but must not be indexed.
#   platform_kernel_os-adr122-123 — leftover git worktree, one ADR behind the
#     real repo, would double every platform_kernel_os ADR.
#   docs_preview — a byte-identical copy of generator_siting_engine's ADRs.
EXCLUDED_DIRS = {
    "platform_kernel_os-adr122-123",
}
EXCLUDED_PATH_PARTS = {"docs_preview", "node_modules", ".git", "venv", ".venv"}

# Overrides file. Git-tracked in homelab-gitops so category corrections are
# versioned alongside the decisions themselves.
OVERRIDES_FILE = (
    Path.home() / "homelab-gitops" / "docs" / "project_notes" / "adr_categories.json"
)

STALE_PROPOSAL_DAYS = 90


# ── Discovery ──────────────────────────────────────────────────────────────────

def _normalize(name: str) -> str:
    """Fold hyphen/underscore/case/.py so 'homelab-gitops' == 'proj-homelab_gitops'."""
    return re.sub(r"[^a-z0-9]", "", name.lower().removesuffix(".py"))


def discover_repos() -> list[Path]:
    """Find every repo with an ADR log.

    Driven by the filesystem, not by DevFlow's project list, for two reasons:
    every project's repoPath is currently empty, and nine repos with ADRs have
    no DevFlow project at all. Driving from disk finds both.
    """
    found = {}
    for root in REPO_ROOTS:
        if not root.is_dir():
            continue
        candidates = [root] + [p for p in root.iterdir() if p.is_dir()]
        for repo in candidates:
            if repo.name in EXCLUDED_DIRS or repo.name.startswith("."):
                continue
            notes = repo / "docs" / "project_notes"
            if (notes / "decisions.md").exists() or (notes / "decisions").is_dir():
                found[repo.resolve()] = True
    return sorted(found.keys())


def map_projects(state: dict) -> dict:
    """Normalized repo name -> DevFlow project id."""
    out = {}
    for p in state.get("projects", []):
        pid = p.get("id", "")
        # Prefer an explicit repoPath when one is ever set.
        repo_path = p.get("repoPath") or ""
        if repo_path:
            out[_normalize(Path(repo_path).name)] = pid
        name = p.get("name") or pid.removeprefix("proj-")
        out.setdefault(_normalize(name), pid)
        out.setdefault(_normalize(pid.removeprefix("proj-")), pid)
    return out


# ── Title / status parsing ─────────────────────────────────────────────────────

_TITLE_DATE = re.compile(r"\s*\((\d{4}-\d{2}-\d{2})\)\s*")
_TITLE_TAG = re.compile(r"\s*\[([A-Za-z ]+)\]\s*")

_STATUS_WORDS = [
    ("superseded", "superseded"),
    ("supersede", "superseded"),
    ("deprecated", "superseded"),
    ("rejected", "rejected"),
    ("proposed", "proposed"),
    ("draft", "proposed"),
    ("research", "proposed"),
    ("deferred", "deferred"),
    ("parked", "deferred"),
    ("implemented", "implemented"),
    ("complete", "implemented"),
    ("done", "implemented"),
    ("accepted", "accepted"),
]


def split_title(raw_title: str) -> tuple[str, str, str]:
    """Pull an inline (date) and [STATUS] tag out of an ADR heading.

    The upstream heading regex only captures a trailing ``(YYYY-MM-DD)``. The
    newest ADRs are written ``Title (2026-07-22) [ACCEPTED]``, which puts the
    date mid-string, so it was being dropped. This recovers both.

    Returns (clean_title, date, status_tag).
    """
    title = raw_title
    date = ""
    tag = ""

    m = _TITLE_DATE.search(title)
    if m:
        date = m.group(1)
        title = _TITLE_DATE.sub(" ", title, count=1)

    m = _TITLE_TAG.search(title)
    if m:
        tag = m.group(1).strip().lower()
        title = _TITLE_TAG.sub(" ", title, count=1)

    return re.sub(r"\s+", " ", title).strip(" -—"), date, tag


# Inline "**Status:** Implemented (2026-05-06) — ..." fields. parse_adr_sections
# only recognises a bold marker alone on its line, so inline fields like these
# are invisible to it. Scraping them separately here keeps section boundaries
# (and therefore scan_project's behaviour) untouched.
_INLINE_FIELD_RE = re.compile(r"^\*\*([A-Za-z ]{2,20}?)\*\*\s*:?\s*(\S.*)$", re.MULTILINE)


def inline_fields(body: str) -> dict:
    """Scrape ``**Name:** value`` pairs that sit on one line with their value."""
    out = {}
    for m in _INLINE_FIELD_RE.finditer(body):
        name = m.group(1).strip().rstrip(":").lower()
        out.setdefault(name, m.group(2).strip())
    return out


def resolve_status(tag: str, sections: dict, inline: dict) -> tuple[str, str]:
    """Return (status, source). Precedence: heading tag, **Status:** field, inferred."""
    if tag:
        for word, status in _STATUS_WORDS:
            if word in tag:
                return status, "title-tag"

    field = (sections.get("status", "") or inline.get("status", "") or "").lower()
    if field:
        for word, status in _STATUS_WORDS:
            if word in field:
                return status, "status-field"

    # No marker anywhere. 54 of 58 homelab-gitops ADRs are in this state, so
    # this is the common path, not the exception. Flagged so the UI can grey it.
    return "accepted", "inferred"


# ── Edge extraction ────────────────────────────────────────────────────────────

_TICKET_RE = re.compile(r"\bT-(\d{3,4})\b")
_QUALIFIED_ADR_RE = re.compile(r"\b([A-Za-z][\w.\-]{2,})\s+(ADR-\d+)\b")
_BARE_ADR_RE = re.compile(r"\b(ADR-\d+)\b")

_EDGE_VERBS = [
    (re.compile(r"supersed(?:e|es|ed)\s+by", re.I), "superseded_by"),
    (re.compile(r"supersed(?:e|es|ing)", re.I), "supersedes"),
    (re.compile(r"amend(?:s|ed|ing)?", re.I), "amends"),
    (re.compile(r"replac(?:e|es|ed)\s+by", re.I), "superseded_by"),
    (re.compile(r"replac(?:e|es|ing)", re.I), "supersedes"),
    (re.compile(r"depends?\s+on|blocked\s+(?:on|by)|requires", re.I), "depends_on"),
    (re.compile(r"builds?\s+on|extends?", re.I), "builds_on"),
]

_LOOKBACK = 80
_LOOKAHEAD = 130


def _edge_type(body: str, pos: int, end: int) -> str:
    """Type an ADR reference from the verb around it.

    Both directions matter. "supersedes ADR-030" puts the verb before the
    reference; "ADR-013 originally deferred CRM ... now superseded" puts it
    after. Looking backwards only missed the second form entirely.
    """
    before = body[max(0, pos - _LOOKBACK):pos]
    for pattern, kind in _EDGE_VERBS:
        if pattern.search(before):
            return kind

    after = body[end:end + _LOOKAHEAD]
    # Stop at a sentence or list-item boundary so a verb belonging to the next
    # thought does not get attached to this reference.
    after = re.split(r"\n\s*[-*]|\. ", after)[0]
    for pattern, kind in _EDGE_VERBS:
        if pattern.search(after):
            return kind

    return "relates_to"


def extract_edges(body: str, own_uid: str, own_project_key: str,
                  repo_keys: dict) -> list[dict]:
    """Find ADR->ADR references and type them.

    A reference qualified by a known repo name ("platform_kernel_os ADR-013")
    becomes a cross-project edge. Everything else resolves within the current
    project. This is the whole reason uids exist: without qualification,
    "ADR-013" is ambiguous across three repos.
    """
    edges = {}
    claimed = set()

    for m in _QUALIFIED_ADR_RE.finditer(body):
        qualifier, adr_id = m.group(1), m.group(2)
        target_key = repo_keys.get(_normalize(qualifier))
        if not target_key:
            continue
        target = f"{target_key}:{adr_id}"
        if target == own_uid:
            continue
        claimed.update(range(m.start(2), m.end(2)))
        edges[target] = {
            "type": _edge_type(body, m.start(), m.end()),
            "target": target,
            "crossProject": target_key != own_project_key,
        }

    for m in _BARE_ADR_RE.finditer(body):
        if m.start() in claimed:
            continue  # already handled as a qualified reference
        target = f"{own_project_key}:{m.group(1)}"
        if target == own_uid or target in edges:
            continue
        edges[target] = {
            "type": _edge_type(body, m.start(), m.end()),
            "target": target,
            "crossProject": False,
        }

    return sorted(edges.values(), key=lambda e: e["target"])


def extract_tickets(body: str) -> list[str]:
    seen = []
    for m in _TICKET_RE.finditer(body):
        tid = f"T-{m.group(1)}"
        if tid not in seen:
            seen.append(tid)
    return seen


# ── Inline category fields ─────────────────────────────────────────────────────

def inline_categories(sections: dict) -> tuple[str, str]:
    """Read **Layer:** / **Domain:** fields written into the ADR itself."""
    layer = (sections.get("layer", "") or "").strip().lower().split("\n")[0]
    domain = (sections.get("domain", "") or "").strip().lower().split("\n")[0]
    layer = layer if layer in LAYERS else ""
    domain = domain if domain in DOMAIN_LABELS else ""
    return layer, domain


# ── Build ──────────────────────────────────────────────────────────────────────

def load_overrides() -> dict:
    if OVERRIDES_FILE.exists():
        try:
            return json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"warning: overrides unreadable ({exc}); ignoring", file=sys.stderr)
    return {}


def build(verbose: bool = False) -> dict:
    state = read_state()
    project_map = map_projects(state)
    ticket_ids = {t["id"] for t in state.get("tickets", [])}
    ticket_by_id = {t["id"]: t for t in state.get("tickets", [])}
    overrides = load_overrides()

    repos = discover_repos()
    # Repo directory name -> project key used in uids. The project key is the
    # DevFlow project id when one matches, else a synthetic key from the dir.
    repo_keys = {}
    repo_meta = {}
    for repo in repos:
        norm = _normalize(repo.name)
        key = project_map.get(norm) or f"dir-{repo.name}"
        repo_keys[norm] = key
        repo_meta[key] = {
            "repoName": repo.name,
            "repoPath": str(repo),
            "devflowProject": project_map.get(norm),
        }

    adrs = []
    per_repo_counts = {}
    skipped_files = []

    for repo in repos:
        norm = _normalize(repo.name)
        key = repo_keys[norm]
        count = 0

        for content, source_path in load_decisions_per_file(repo):
            if any(part in source_path for part in EXCLUDED_PATH_PARTS):
                skipped_files.append(source_path)
                continue

            for adr in parse_adrs(content):
                title, title_date, tag = split_title(adr["title"])
                inline = inline_fields(adr["body"])
                date = adr["date"] or title_date
                if not date:
                    # Last resort: the first ISO date in an inline **Date:** or
                    # **Status:** field. 193 ADRs carry no date in the heading.
                    for field in ("date", "status", "decided"):
                        m = re.search(r"\d{4}-\d{2}-\d{2}", inline.get(field, ""))
                        if m:
                            date = m.group(0)
                            break
                status, status_source = resolve_status(tag, adr["sections"], inline)
                uid = f"{key}:{adr['id']}"

                # Layer and domain resolve independently, each through the same
                # ladder: inline field > overrides file > keyword guess. They
                # are tracked separately because an ADR often has one confirmed
                # and one guessed, and reporting the pair as "override" would
                # hide the guess from the unconfirmed count.
                inline_layer, inline_domain = inline_categories(adr["sections"])
                ov = overrides.get(uid) or {}
                if not isinstance(ov, dict):
                    ov = {}

                layer, layer_src = inline_layer, "inline"
                if not layer:
                    layer, layer_src = ov.get("layer", ""), "override"
                if not layer:
                    layer, layer_src = "", "keyword"

                domain, domain_src = inline_domain, "inline"
                if not domain:
                    domain, domain_src = ov.get("domain", ""), "override"
                if not domain:
                    domain, domain_src = "", "keyword"

                if not layer or not domain:
                    guess_layer, guess_domain = classify(
                        title, adr["sections"], adr["body"], repo.name
                    )
                    layer = layer or guess_layer or "infra"
                    domain = domain or guess_domain or "unassigned"

                # Weakest link wins: the pair is only as trustworthy as its
                # least-confirmed half.
                cat_source = (
                    "keyword" if "keyword" in (layer_src, domain_src)
                    else "override" if "override" in (layer_src, domain_src)
                    else "inline"
                )

                tickets = extract_tickets(adr["body"])
                adrs.append({
                    "uid": uid,
                    "project": key,
                    "repoName": repo.name,
                    "devflowProject": repo_meta[key]["devflowProject"],
                    "adrId": adr["id"],
                    "adrNum": int(adr["id"].split("-")[1]),
                    "title": title,
                    "date": date,
                    "status": status,
                    "statusSource": status_source,
                    "layer": layer,
                    "domain": domain,
                    "categorySource": cat_source,
                    "sourceFile": source_path,
                    "sections": adr["sections"],
                    "tickets": [t for t in tickets if t in ticket_ids],
                    "orphanTickets": [t for t in tickets if t not in ticket_ids],
                    "edges": extract_edges(adr["body"], uid, key, repo_keys),
                })
                count += 1

        per_repo_counts[repo.name] = count
        if verbose:
            print(f"  {repo.name:44} {count:4d}")

    known_uids = {a["uid"] for a in adrs}
    for a in adrs:
        for e in a["edges"]:
            e["resolved"] = e["target"] in known_uids

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "counts": {
            "adrs": len(adrs),
            "repos": len(repos),
            "byRepo": per_repo_counts,
        },
        "layers": LAYERS,
        "layerLabels": LAYER_LABELS,
        "domainLabels": DOMAIN_LABELS,
        "repos": repo_meta,
        "adrs": adrs,
        "health": health_report(adrs, repo_meta, state, ticket_by_id, skipped_files),
    }


# ── Health report ──────────────────────────────────────────────────────────────

def health_report(adrs, repo_meta, state, ticket_by_id, skipped_files) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=STALE_PROPOSAL_DAYS)).strftime("%Y-%m-%d")

    no_ticket = [
        {"uid": a["uid"], "title": a["title"], "status": a["status"]}
        for a in adrs
        if not a["tickets"] and a["status"] in ("proposed", "deferred")
    ]

    stale_proposals = [
        {"uid": a["uid"], "title": a["title"], "date": a["date"]}
        for a in adrs
        if a["status"] == "proposed" and a["date"] and a["date"] < cutoff
    ]

    orphan_refs = [
        {"uid": a["uid"], "tickets": a["orphanTickets"]}
        for a in adrs if a["orphanTickets"]
    ]

    unresolved_edges = [
        {"uid": a["uid"], "target": e["target"], "type": e["type"]}
        for a in adrs for e in a["edges"] if not e["resolved"]
    ]

    # Same ADR number used by more than one project. This is why uids exist.
    by_num = {}
    for a in adrs:
        by_num.setdefault(a["adrId"], []).append(a)
    collisions = [
        {
            "adrId": adr_id,
            "count": len(group),
            "entries": [
                {"uid": g["uid"], "repo": g["repoName"], "title": g["title"][:70]}
                for g in group
            ],
        }
        for adr_id, group in sorted(by_num.items())
        if len(group) > 1
    ]

    unlinked_repos = [
        {"repo": m["repoName"], "path": m["repoPath"]}
        for m in repo_meta.values() if not m["devflowProject"]
    ]

    indexed_projects = {m["devflowProject"] for m in repo_meta.values() if m["devflowProject"]}
    projects_without_adrs = [
        p["id"] for p in state.get("projects", []) if p["id"] not in indexed_projects
    ]

    # repoPath is empty on every project today, so the index has to resolve
    # repos by name. Emitting the mapping lets it be fixed once, by hand.
    suggested_repo_paths = {
        m["devflowProject"]: m["repoPath"]
        for m in repo_meta.values()
        if m["devflowProject"]
    }

    unconfirmed = sum(1 for a in adrs if a["categorySource"] == "keyword")

    return {
        "adrsWithoutTickets": no_ticket,
        "staleProposals": stale_proposals,
        "orphanTicketRefs": orphan_refs,
        "unresolvedEdges": unresolved_edges,
        "numberCollisions": collisions,
        "reposWithoutDevflowProject": unlinked_repos,
        "devflowProjectsWithoutAdrs": projects_without_adrs,
        "suggestedRepoPaths": suggested_repo_paths,
        "skippedFiles": skipped_files,
        "unconfirmedCategories": unconfirmed,
    }


# ── Write ──────────────────────────────────────────────────────────────────────

def write_index(index: dict) -> Path:
    """Atomic replace, same pattern as state_store: temp file in the same dir."""
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(INDEX_FILE.parent), prefix=".adr_index.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(index, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, INDEX_FILE)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return INDEX_FILE


def load_index() -> dict:
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    return {}


def write_review_csv(index: dict, path: str) -> int:
    rows = [a for a in index["adrs"] if a["categorySource"] == "keyword"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["uid", "repo", "adrId", "title", "guessed_layer",
                    "guessed_domain", "correct_layer", "correct_domain"])
        for a in rows:
            w.writerow([a["uid"], a["repoName"], a["adrId"], a["title"],
                        a["layer"], a["domain"], "", ""])
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="Build the ADR index for DevFlow.")
    ap.add_argument("--dry-run", action="store_true", help="parse and report, write nothing")
    ap.add_argument("--review-csv", metavar="PATH", help="dump keyword guesses for review")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args()

    verbose = not args.quiet
    if verbose:
        print("Scanning repos for ADRs...")
    index = build(verbose=verbose)

    c = index["counts"]
    h = index["health"]
    print(f"\n{c['adrs']} ADRs across {c['repos']} repos")
    print(f"  unconfirmed categories : {h['unconfirmedCategories']}")
    print(f"  number collisions      : {len(h['numberCollisions'])}")
    print(f"  orphan ticket refs     : {len(h['orphanTicketRefs'])}")
    print(f"  stale proposals        : {len(h['staleProposals'])}")
    print(f"  repos w/o DevFlow proj : {len(h['reposWithoutDevflowProject'])}")

    if args.review_csv:
        n = write_review_csv(index, args.review_csv)
        print(f"\nWrote {n} rows to {args.review_csv}")

    if args.dry_run:
        print("\n(dry run — nothing written)")
    else:
        print(f"\nWrote {write_index(index)}")


if __name__ == "__main__":
    main()
