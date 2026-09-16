"""One config loader for every DevFlow entry point.

server.py, http_api.py, adr_index.py, adr_categories.py, adr_apply_review.py
and hooks/session-status.py all read the same file. Keeping the loader here
means the ADR tooling can run without the ``mcp`` package (server.py imports
it) and every path a user might need to change lives in config.json, not in
code.

Location: ``<config dir>/config.json``. The config dir is ``$DEVFLOW_CONFIG_DIR``
or ``~/.config/devflow-mcp``. ``config.example.json`` beside this file
documents every key; the first session-start copies it into place.

Keys:

    projecthub_db_path      optional SQLite bridge; "" disables it
    auto_time_entries       bridge timers on ticket transitions
    default_classification  label for new bridge projects
    stale_active_days       an active ticket with no log for this long is stale
    wip_limit               active tickets above this raise wip_warning
    max_timer_hours         cap on hours a closing timer may book
    verify_timeout_sec      verify_cmd wall-clock limit
    api_token               required by the web UI's mutating routes; "" refuses them

    adr_repo_roots          directories scanned one level deep for repos with ADRs
                            (each project's repo_path is always scanned too)
    adr_exclude_dirs        repo directory names never indexed (stale worktrees, copies)
    adr_exclude_path_parts  path segments inside a repo that mark a copy (a docs preview, say)
    adr_overrides_file      per-uid layer/domain corrections; default <config dir>/adr_categories.json
    adr_domains             {"key": "Label"}; the Domain axis of the Decisions view
    adr_project_domains     {"repo dir name": "domain key"}; the default domain per repo
    adr_domain_keywords     {"domain key": ["keyword", ...]}; title/decision hits move an ADR
    adr_layer_keywords      {"layer": ["keyword", ...]}; merged into the built-in layer keywords
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from state_store import CONFIG_DIR

CONFIG_FILE = CONFIG_DIR / "config.json"
EXAMPLE_FILE = Path(__file__).resolve().parent / "config.example.json"

DEFAULT_CONFIG: dict = {
    "projecthub_db_path": "",
    "auto_time_entries": True,
    "default_classification": "personal",
    "stale_active_days": 14,
    "wip_limit": 10,
    "max_timer_hours": 8.0,
    "verify_timeout_sec": 600,
    "api_token": "",
    "adr_repo_roots": [],
    "adr_exclude_dirs": [],
    "adr_exclude_path_parts": [],
    "adr_overrides_file": "",
    "adr_domains": {},
    "adr_project_domains": {},
    "adr_domain_keywords": {},
    "adr_layer_keywords": {},
}


def load_config() -> dict:
    """The file merged over the defaults. A missing or unreadable file means defaults."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {**DEFAULT_CONFIG, **data}
        except (OSError, ValueError):
            pass
    return dict(DEFAULT_CONFIG)


def expand(path: str | os.PathLike | None) -> Path | None:
    """``~`` and ``$VAR`` expanded; None for an empty value."""
    if not path:
        return None
    return Path(os.path.expandvars(os.path.expanduser(str(path))))


def overrides_file(config: dict | None = None) -> Path:
    config = config or load_config()
    return expand(config.get("adr_overrides_file")) or (CONFIG_DIR / "adr_categories.json")


def ensure_config_dir() -> bool:
    """First run: create the config dir and seed config.json from the example.

    Returns True when the config file was created. The state file is not
    created here; the store writes it on the first mutation.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        return False
    if EXAMPLE_FILE.exists():
        shutil.copy(EXAMPLE_FILE, CONFIG_FILE)
    else:
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")
    return True
