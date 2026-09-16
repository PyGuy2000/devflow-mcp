"""
Two-axis ADR taxonomy: Layer (what kind of thing) x Domain (what it serves).

Layer answers "is this a UI decision or an ETL decision". Domain answers
"which product does it serve". Both are needed: a single flat list buries six
products' infra decisions in one bucket, which is the thing that made the ADR
set unreadable in the first place.

Assignment precedence, highest first:
  1. Inline ``**Layer:**`` / ``**Domain:**`` fields in the ADR itself
  2. An entry in the overrides file (adr_categories.json), keyed by uid
  3. The keyword classifier below

Only (3) is guesswork, and everything it produces is tagged
``categorySource: "keyword"`` so the UI can show it as unconfirmed.

The Layer axis is fixed (below). The Domain axis is yours: ``adr_domains``,
``adr_project_domains`` and ``adr_domain_keywords`` in config.json define
it. With none configured every ADR lands in ``unassigned`` until an inline
field or an override says otherwise. ``adr_layer_keywords`` extends the
built-in layer keywords.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from devflow_config import load_config  # noqa: E402

_CONFIG = load_config()

# ── Layer vocabulary ───────────────────────────────────────────────────────────
#
# Fixed set. Adding a layer means adding a row to the Decisions grid, so this
# is deliberately short.

LAYERS = [
    "ui",
    "chatbot",
    "agentic",
    "etl",
    "datasets",
    "plugins-engines",
    "infra",
    "security",
    "ops",
]

LAYER_LABELS = {
    "ui": "UI",
    "chatbot": "Chatbot",
    "agentic": "Agentic",
    "etl": "ETL",
    "datasets": "Datasets",
    "plugins-engines": "Plugins / Engines",
    "infra": "Infra",
    "security": "Security",
    "ops": "Ops",
}

# Keyword -> layer. Matched case-insensitively as substrings. A hit in the
# title scores 3, a hit in the Decision section scores 2, elsewhere scores 1.
# Highest total wins; LAYERS order breaks ties.
LAYER_KEYWORDS = {
    "ui": [
        "dashboard", "frontend", "front-end", "cytoscape", "leaflet",
        "visualiz", "chart", "graph view", "html report", "react",
        "streamlit", "embed", "web page", "topology page", "hud",
        "user interface", "styling", "branding",
    ],
    "chatbot": [
        "telegram", "chatbot", "chat bot", "conversational", "slack",
        "chat interface", "notification batching", "bot command",
    ],
    "agentic": [
        "agent", "orchestrat", "claude", "llm", "prompt", "skill",
        "mcp", "harness", "subagent",
        "ollama", "inference", "model strategy", "autonomous",
        "self-improving", "reasoning", "tool call",
        "qwen", "sonnet", "haiku", "opus", "glm",
    ],
    "etl": [
        "pipeline", "ingest", "etl", "scrape", "scraping", "sync",
        "dagster", "cron", "feed", "extract", "sensor", "scheduler",
        "batch job", "backfill", "data flow", "collector", "crawl",
    ],
    "datasets": [
        "data model", "schema", "postgis", "dataset", "ontology",
        "registry", "warehouse", "retrieval", "vector search",
        "embedding", "knowledge base", "chromadb",
        "entitlement", "taxonomy", "directory structure", "data structure",
        "shapefile", "geospatial data",
    ],
    "plugins-engines": [
        "plugin", "engine", "bridge", "adapter", "template",
        "framework", "solver", "simulat", "module boundary",
        "extension point", "pluggable",
    ],
    "infra": [
        "k3s", "kubernetes", "k8s", "argocd", "gitops", "deploy",
        "docker", "container", "longhorn", "nas", "cluster", "gpu",
        "hardware", "proxmox", "tunnel", "dns", "pvc", "image tag",
        "ci/cd", "github actions", "ghcr", "tailscale", "subnet",
        "vm", "node", "storage", "workstation", "self-host", "forge",
        "webhook", "nfs", "postgres", "database server",
    ],
    "security": [
        "secret", "credential", "auth", "authelia", "sealed",
        "security", "hardening", "token", "api key", "permission",
        "rbac", "read-only role", "hygiene", "compromise", "rotate",
        "encrypt",
    ],
    "ops": [
        "monitor", "alert", "watchdog", "backup", "logging",
        "observability", "health check", "incident", "unattended-upgrade",
        "cost tracking", "usage tracking", "runbook", "uptime",
        "self-proving", "reconciliation", "lag",
    ],
}

# Extra layer keywords from config.json, merged into the built-in table.
for _layer, _kws in (_CONFIG.get("adr_layer_keywords") or {}).items():
    if _layer in LAYER_KEYWORDS and isinstance(_kws, list):
        LAYER_KEYWORDS[_layer] = LAYER_KEYWORDS[_layer] + [str(k).lower() for k in _kws]

# ── Domain vocabulary (from config.json) ───────────────────────────────────────

#: {"key": "Label"}. "unassigned" is always present and always last.
DOMAIN_LABELS = {
    **{str(k): str(v) for k, v in (_CONFIG.get("adr_domains") or {}).items() if k != "unassigned"},
    "unassigned": "Unassigned",
}

#: Default domain per repo directory name. The starting point; a keyword hit
#: in the title or the Decision section can pull an individual ADR elsewhere.
PROJECT_DOMAIN = {
    str(k): str(v) for k, v in (_CONFIG.get("adr_project_domains") or {}).items() if str(v) in DOMAIN_LABELS
}

#: Keyword -> domain. Only applied when the hit is strong (title match, or two
#: or more Decision-section hits), because a passing mention in a rollout list
#: should not reclassify an ADR.
DOMAIN_KEYWORDS = {
    str(k): [str(w).lower() for w in v]
    for k, v in (_CONFIG.get("adr_domain_keywords") or {}).items()
    if str(k) in DOMAIN_LABELS and isinstance(v, list)
}


def _score(text_title: str, text_decision: str, text_all: str, keywords: list) -> int:
    """Weighted substring score: title 3, Decision section 2, anywhere else 1."""
    total = 0
    for kw in keywords:
        if kw in text_title:
            total += 3
        elif kw in text_decision:
            total += 2
        elif kw in text_all:
            total += 1
    return total


def classify(title: str, sections: dict, body: str, project_dir_name: str) -> tuple:
    """Guess (layer, domain) from ADR text. Returns ("", "") only if nothing scores.

    project_dir_name seeds the domain; keyword hits can override it.
    """
    t = title.lower()
    decision = (sections.get("decision", "") or "").lower()
    everything = (title + " " + body).lower()

    # ── Layer ──
    layer_scores = {
        layer: _score(t, decision, everything, kws)
        for layer, kws in LAYER_KEYWORDS.items()
    }
    best_layer = ""
    best_score = 0
    for layer in LAYERS:  # LAYERS order breaks ties deterministically
        if layer_scores[layer] > best_score:
            best_layer = layer
            best_score = layer_scores[layer]

    # ── Domain ──
    #
    # The repo's default domain wins unless a keyword hits the TITLE or the
    # Decision section. Counting mentions anywhere in the body was far too
    # eager: an infrastructure ADR routinely lists every affected app, and a
    # rollout list is not a subject.
    domain = PROJECT_DOMAIN.get(project_dir_name, "unassigned")
    best_domain_score = 0
    for dom, kws in DOMAIN_KEYWORDS.items():
        title_hits = sum(1 for kw in kws if kw in t)
        decision_hits = sum(1 for kw in kws if kw in decision)
        if title_hits:
            score = 10 + title_hits
        elif decision_hits >= 2:
            score = decision_hits
        else:
            continue
        if score > best_domain_score:
            best_domain_score = score
            domain = dom

    return best_layer, domain
