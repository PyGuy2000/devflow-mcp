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
"""

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
        "hermes", "clutch", "openclaw", "mcp", "harness", "subagent",
        "ollama", "inference", "model strategy", "autonomous",
        "self-improving", "reasoning", "tool call", "humanizer",
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
        "embedding", "knowledge base", "kbvault", "chromadb",
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

# ── Domain vocabulary ──────────────────────────────────────────────────────────

DOMAIN_LABELS = {
    "homelab": "Homelab",
    "platform-kernel": "Platform Kernel",
    "kbvault": "KBVault",
    "clutch-openclaw": "Clutch / OpenClaw",
    "alberta-market": "Alberta Market",
    "nerc": "NERC",
    "pediatrica": "Pediatrica",
    "consulting": "Consulting",
    "personal-automation": "Personal Automation",
    "harness": "Dev Harness",
    "modelling": "Modelling",
    "unassigned": "Unassigned",
}

# Default domain per repo directory name. This is the starting point; the
# keyword overrides below can pull an individual ADR into a different domain
# (e.g. a KBVault ADR living in the platform_kernel_os repo).
PROJECT_DOMAIN = {
    "homelab-gitops": "homelab",
    "platform_kernel_os": "platform-kernel",
    "etl_api_pipeline": "alberta-market",
    "AB_Electricity_Sector_Stats": "alberta-market",
    "NEW_AESO_API": "alberta-market",
    "alberta_substation_satellite_pipeline": "alberta-market",
    "avoided_cost_model": "modelling",
    "virtual_power_plant": "modelling",
    "energy_pathway_orchestrator": "modelling",
    "generator_siting_engine": "modelling",
    "Digital_Twin_Turbine": "modelling",
    "telemetry_simulation": "modelling",
    "energy_mgmt_toolkit": "modelling",
    "vppa_hedge": "modelling",
    "finstmt": "modelling",
    "casino_simulator": "modelling",
    "synth-data-ml": "modelling",
    "nerc_compliance_dashboard": "nerc",
    "pediatrica_dashboard": "pediatrica",
    "consulting_os": "consulting",
    "community_investment_program": "consulting",
    "devflow-mcp": "harness",
    "python_project_tracker": "harness",
    "agent_framework_gem": "harness",
    "agent_framework_audit": "harness",
    "cognitive_scaffolding": "harness",
    "loop_demo": "harness",
    "medium_vault": "harness",
    "my_solar_rag": "kbvault",
    "sec_energy_analyzer.py": "kbvault",
    "geospatial-dashboard": "alberta-market",
    "hud_globe2": "alberta-market",
    "utility_dashboard": "personal-automation",
    "mom_assisted_living": "personal-automation",
    "ai-resume-builder": "personal-automation",
}

# Keyword -> domain. Only applied when the hit is strong (title match, or two
# or more body hits), because a passing mention of "Telegram" in an infra ADR
# should not reclassify it into Clutch.
DOMAIN_KEYWORDS = {
    "kbvault": ["kbvault", "knowledge base service", "private knowledge base"],
    "clutch-openclaw": ["clutch", "openclaw"],
    "harness": [
        "claude code", "devflow", "mcp server", "settings file",
        "subagent", "slash command", "claude skill",
    ],
    "alberta-market": ["aeso", "alberta", "pool price", "market intelligence"],
    "nerc": ["nerc"],
    "pediatrica": ["pediatrica"],
    "consulting": ["client project", "invoice", "consulting"],
    "personal-automation": [
        "gmail", "google calendar", "hubspot", "crm", "chore",
        "meal plan", "family calendar", "job market", "email automation",
        "job alert",
    ],
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
    # eager: homelab-gitops ADRs routinely list every affected app, so
    # "Immutable Image Tags" got pulled into the Pediatrica domain purely
    # because Pediatrica appeared twice in a rollout list.
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
