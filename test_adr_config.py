#!/usr/bin/env python3
"""ADR discovery, domains and overrides come from config.json, not from code.

Standalone script like the other tests. Builds a temp config dir with two
fixture repos and runs the index builder against it; then re-runs itself in
a subprocess with NO config to prove the defaults hold (every domain is
"unassigned", nothing is scanned that was not asked for).

    python3 test_adr_config.py
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="devflow-adr-test-")
CONFIG_DIR = Path(_tmp) / "config"
CONFIG_DIR.mkdir()
os.environ["DEVFLOW_CONFIG_DIR"] = str(CONFIG_DIR)

MODE = sys.argv[1] if len(sys.argv) > 1 else "configured"

ROOT = Path(_tmp) / "code"
(ROOT / "my_app" / "docs" / "project_notes").mkdir(parents=True)
(ROOT / "my_app" / "docs" / "project_notes" / "decisions.md").write_text(
    "## ADR-001: Invoices round half-up (2026-03-02) [ACCEPTED]\n\n**Status:** Accepted\n\n### Decision\nRound each invoice line.\n\n"
    "## ADR-002: Tax table source [PROPOSED]\n\n**Status:** Proposed\n\n### Decision\nA versioned tax table; depends on ADR-001. Tracked by T-1001.\n",
    encoding="utf-8",
)
(ROOT / "my_infra" / "docs" / "project_notes" / "decisions").mkdir(parents=True)
(ROOT / "my_infra" / "docs" / "project_notes" / "decisions" / "ADR-001-cluster.md").write_text(
    "## ADR-001: One cluster per site [ACCEPTED]\n\n**Status:** Accepted\n**Layer:** infra\n\n### Decision\nDeploy to the cluster.\n",
    encoding="utf-8",
)
(ROOT / "old-worktree" / "docs" / "project_notes").mkdir(parents=True)
(ROOT / "old-worktree" / "docs" / "project_notes" / "decisions.md").write_text("## ADR-009: stale copy\n", encoding="utf-8")
ELSEWHERE = Path(_tmp) / "elsewhere" / "registered_app"
(ELSEWHERE / "docs" / "project_notes").mkdir(parents=True)
(ELSEWHERE / "docs" / "project_notes" / "decisions.md").write_text("## ADR-001: Invoice rounding, registered by repo_path\n\n### Decision\nRound again.\n", encoding="utf-8")

OVERRIDES = Path(_tmp) / "overrides.json"

if MODE == "configured":
    (CONFIG_DIR / "config.json").write_text(json.dumps({
        "adr_repo_roots": [str(ROOT)],
        "adr_exclude_dirs": ["old-worktree"],
        "adr_overrides_file": str(OVERRIDES),
        "adr_domains": {"billing": "Billing", "platform": "Platform"},
        "adr_project_domains": {"my_app": "billing", "my_infra": "platform"},
        "adr_domain_keywords": {"billing": ["invoice"]},
        "adr_layer_keywords": {"datasets": ["tax table"]},
    }), encoding="utf-8")
    OVERRIDES.write_text(json.dumps({"proj-my_infra:ADR-001": {"domain": "billing"}}), encoding="utf-8")

# the state: two projects, one with a repo_path outside every root
(CONFIG_DIR / "devflow_state.json").write_text(json.dumps({
    "projects": [
        {"id": "proj-my_app", "name": "my_app", "goal": "g", "repoPath": ""},
        {"id": "proj-my_infra", "name": "my_infra", "goal": "g", "repoPath": ""},
        {"id": "proj-registered_app", "name": "registered_app", "goal": "g", "repoPath": str(ELSEWHERE)},
    ],
    "tickets": [{"id": "T-1001", "title": "x", "why": "y", "projectId": "proj-my_app", "status": "backlog", "priority": "low", "blockedBy": [], "blocksTickets": [], "created": 0, "log": []}],
    "next_id": 1002,
}), encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adr_categories  # noqa: E402
import adr_index  # noqa: E402
from devflow_config import load_config, overrides_file  # noqa: E402

passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


cfg = load_config()
repos = [p.name for p in adr_index.discover_repos(None, cfg)]

if MODE == "configured":
    print("configured:")
    check("roots scanned one level deep", "my_app" in repos and "my_infra" in repos, repos)
    check("excluded dir skipped", "old-worktree" not in repos, repos)
    check("project repo_path scanned even outside the roots", "registered_app" in repos, repos)
    check("overrides file from config", overrides_file(cfg) == OVERRIDES, overrides_file(cfg))
    check("domains from config", list(adr_categories.DOMAIN_LABELS) == ["billing", "platform", "unassigned"], adr_categories.DOMAIN_LABELS)
    check("project domains from config", adr_categories.PROJECT_DOMAIN == {"my_app": "billing", "my_infra": "platform"})
    check("layer keywords merged", "tax table" in adr_categories.LAYER_KEYWORDS["datasets"])
    index = adr_index.build(verbose=False)
    by_uid = {a["uid"]: a for a in index["adrs"]}
    check("four ADRs indexed", index["counts"]["adrs"] == 4, index["counts"])
    check("uid uses the project id", "proj-my_app:ADR-001" in by_uid and "proj-registered_app:ADR-001" in by_uid, sorted(by_uid))
    check("repo default domain", by_uid["proj-my_app:ADR-001"]["domain"] == "billing")
    check("override wins over repo default", by_uid["proj-my_infra:ADR-001"]["domain"] == "billing" and by_uid["proj-my_infra:ADR-001"]["categorySource"] == "override", by_uid["proj-my_infra:ADR-001"]["categorySource"])
    check("inline layer wins", by_uid["proj-my_infra:ADR-001"]["layer"] == "infra")
    check("keyword pulls an unassigned repo into a domain", by_uid["proj-registered_app:ADR-001"]["domain"] == "billing", by_uid["proj-registered_app:ADR-001"]["domain"])
    check("ticket edge links to the state", by_uid["proj-my_app:ADR-002"]["tickets"] == ["T-1001"])
    check("depends_on edge resolves", any(e["target"] == "proj-my_app:ADR-001" and e["resolved"] for e in by_uid["proj-my_app:ADR-002"]["edges"]))
    check("index written under the config dir", adr_index.write_index(index) == CONFIG_DIR / "adr_index.json")
    # the default run, in a fresh process with no config.json
    r = subprocess.run([sys.executable, __file__, "defaults"], capture_output=True, text=True, timeout=120)
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        failed += 1
        print(r.stderr)
    else:
        # Fold the child's tally in, so the printed total matches the PASS lines
        # above it. Without this the summary silently under-reports by however
        # many checks the defaults run makes.
        m = re.search(r"^(\d+) passed", r.stdout, re.M)
        if m:
            passed += int(m.group(1))
else:
    print("defaults (no config.json):")
    check("nothing scanned without roots except project repo paths", repos == ["registered_app"], repos)
    check("only the unassigned domain", list(adr_categories.DOMAIN_LABELS) == ["unassigned"], adr_categories.DOMAIN_LABELS)
    check("no project domains, no domain keywords", adr_categories.PROJECT_DOMAIN == {} and adr_categories.DOMAIN_KEYWORDS == {})
    check("overrides default beside the state", overrides_file(cfg) == CONFIG_DIR / "adr_categories.json")
    index = adr_index.build(verbose=False)
    check("one ADR, domain unassigned", index["counts"]["adrs"] == 1 and index["adrs"][0]["domain"] == "unassigned", index["counts"])

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
