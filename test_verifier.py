"""
Tests for the verification gate: verify_ticket, the 'done' transition gate,
revision staleness, and the waiver escape hatch.

Builds a throwaway git repo so the staleness check exercises real revisions.
Runs against a temp state file via DEVFLOW_STATE_FILE; the bridge is disabled.

Run: python3 test_verifier.py
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

_tmpdir = tempfile.mkdtemp(prefix="devflow_verify_test_")
os.environ["DEVFLOW_STATE_FILE"] = str(Path(_tmpdir) / "state.json")

import server  # noqa: E402

server._bridge = False  # disable ProjectHub side-effects (falsy, not None)

NOW_MS = int(time.time() * 1000)
REPO = Path(_tmpdir) / "repo"
PLAIN = Path(_tmpdir) / "plaindir"  # a project directory that is not a checkout


def git(*args, cwd=REPO):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )


def setup_repo():
    REPO.mkdir(parents=True)
    PLAIN.mkdir(parents=True)
    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    (REPO / "marker.txt").write_text("v1\n")
    git("add", "-A")
    git("commit", "-qm", "initial")


def head():
    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=REPO, capture_output=True, text=True,
    ).stdout.strip()


def write_state(tickets, repo_path=None):
    state = {
        "projects": [{
            "id": "proj-test",
            "name": "test_project",
            "goal": "testing",
            "color": "#4f7cff",
            "repoPath": str(REPO) if repo_path is None else repo_path,
        }],
        "tickets": tickets,
        "next_id": 100,
    }
    with open(os.environ["DEVFLOW_STATE_FILE"], "w") as f:
        json.dump(state, f)


def make_ticket(tid, verify_cmd="", verified=None, status="active"):
    return {
        "id": tid,
        "title": f"Ticket {tid}",
        "why": "because",
        "desc": "",
        "projectId": "proj-test",
        "priority": "medium",
        "status": status,
        "blockedBy": [],
        "blocksTickets": [],
        "created": NOW_MS,
        "verifyCmd": verify_cmd,
        "verified": verified,
        "log": [{"t": NOW_MS, "msg": "Ticket created"}],
    }


def get(tid):
    with open(os.environ["DEVFLOW_STATE_FILE"]) as f:
        state = json.load(f)
    return next(t for t in state["tickets"] if t["id"] == tid)


passed = failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  PASS {name}")
    else:
        failed += 1
        print(f"  FAIL {name} {detail}")


setup_repo()

# ── Ungated tickets are untouched ─────────────────────────────────────────

print("no verify_cmd (existing behaviour):")
write_state([make_ticket("T-001")])
r = server.update_ticket_status("T-001", "done")
check("ticket without verify_cmd still closes", r.get("success") is True, r)

# ── The gate blocks a bare 'done' ─────────────────────────────────────────

print("gate with no proof:")
write_state([make_ticket("T-002", verify_cmd="true")])
r = server.update_ticket_status("T-002", "done")
check("refused without verification", "error" in r, r)
check("names the command", r.get("verify_cmd") == "true", r)
check("suggests verify_ticket", "verify_ticket" in r.get("hint", ""), r)
check("status unchanged on refusal", get("T-002")["status"] == "active")
check("non-done transitions unaffected", server.update_ticket_status("T-002", "blocked").get("success") is True)

# ── A failing run is recorded and still blocks ────────────────────────────

print("failing verification:")
write_state([make_ticket("T-003", verify_cmd="echo boom >&2; exit 3")])
r = server.verify_ticket("T-003")
check("reports failure", r.get("passed") is False and r.get("exit") == 3, r)
check("captures output tail", "boom" in r.get("tail", ""), r)
check("records the revision", r.get("rev") == head(), r)
check("proof stored on ticket", get("T-003")["verified"]["exit"] == 3)
check("logs the failure", "FAILED" in get("T-003")["log"][-1]["msg"])
r = server.update_ticket_status("T-003", "done")
check("done still refused after failure", "error" in r and "exit 3" in r["error"], r)

# ── A passing run opens the gate ──────────────────────────────────────────

print("passing verification:")
write_state([make_ticket("T-004", verify_cmd="echo 12 passed")])
r = server.verify_ticket("T-004")
check("reports success", r.get("passed") is True and r.get("exit") == 0, r)
check("duration recorded", isinstance(r.get("duration_sec"), float), r)
check("proof stored", get("T-004")["verified"]["rev"] == head())
r = server.update_ticket_status("T-004", "done")
check("done allowed with green proof", r.get("success") is True, r)

# ── Proof expires when HEAD moves ─────────────────────────────────────────

print("stale proof after a new commit:")
write_state([make_ticket("T-005", verify_cmd="echo ok")])
server.verify_ticket("T-005")
old_rev = get("T-005")["verified"]["rev"]
(REPO / "marker.txt").write_text("v2\n")
git("commit", "-qam", "one last cleanup")
new_rev = head()
check("HEAD actually moved", old_rev != new_rev)
r = server.update_ticket_status("T-005", "done")
check("stale proof refused", "error" in r and "stale" in r["error"], r)
check("error names both revisions", old_rev in r["error"] and new_rev in r["error"], r)
server.verify_ticket("T-005")
r = server.update_ticket_status("T-005", "done")
check("re-verification reopens the gate", r.get("success") is True, r)

# ── Waiver ────────────────────────────────────────────────────────────────

print("waiver:")
write_state([make_ticket("T-006", verify_cmd="exit 1")])
r = server.update_ticket_status("T-006", "done", waiver="needs a live k8s context")
check("waiver closes the ticket", r.get("success") is True, r)
check("waiver echoed in result", r.get("waived") == "needs a live k8s context", r)
check("waiver recorded in log", "WAIVED" in get("T-006")["log"][-1]["msg"])
check("reason preserved", "live k8s context" in get("T-006")["log"][-1]["msg"])

write_state([make_ticket("T-007")])
r = server.update_ticket_status("T-007", "done", waiver="unnecessary")
check("waiver on an ungated ticket is not logged as one", "WAIVED" not in get("T-007")["log"][-1]["msg"])

# ── Changing the command drops its proof ──────────────────────────────────

print("edit_ticket clears stale proof:")
write_state([make_ticket("T-008", verify_cmd="echo ok")])
server.verify_ticket("T-008")
check("green before edit", get("T-008")["verified"]["exit"] == 0)
server.edit_ticket("T-008", verify_cmd="pytest -q")
check("proof cleared on command change", get("T-008")["verified"] is None)
r = server.update_ticket_status("T-008", "done")
check("gate re-armed", "error" in r, r)

server.edit_ticket("T-008", verify_cmd="pytest -q", title="renamed")
check("re-setting the same command keeps proof slot untouched", get("T-008")["verified"] is None)
server.edit_ticket("T-008", verify_cmd="")
r = server.update_ticket_status("T-008", "done")
check("clearing verify_cmd removes the gate", r.get("success") is True, r)

# ── Misconfiguration ──────────────────────────────────────────────────────

print("misconfiguration:")
write_state([make_ticket("T-009")])
r = server.verify_ticket("T-009")
check("verify with no command errors", "error" in r and "no verify_cmd" in r["error"], r)

r = server.verify_ticket("T-404")
check("verify unknown ticket errors", "error" in r, r)

write_state([make_ticket("T-010", verify_cmd="echo ok")], repo_path="/nonexistent/path")
r = server.verify_ticket("T-010")
check("missing repoPath errors clearly", "error" in r and "repoPath" in r["error"], r)

# ── Non-git project directory ─────────────────────────────────────────────

print("non-git project directory:")
write_state([make_ticket("T-011", verify_cmd="echo ok")], repo_path=str(PLAIN))
r = server.verify_ticket("T-011")
check("runs outside a checkout", r.get("passed") is True, r)
check("revision is None", r.get("rev") is None, r)
r = server.update_ticket_status("T-011", "done")
check("no revision means no staleness check", r.get("success") is True, r)

# ── Status report surfacing ───────────────────────────────────────────────

print("status report:")
write_state([
    make_ticket("T-012", verify_cmd="pytest -q"),
    make_ticket("T-013", verify_cmd="pytest -q", verified={"t": NOW_MS, "cmd": "pytest -q", "exit": 0, "rev": head(), "tail": "ok"}),
    make_ticket("T-014"),
])
report = server.get_status_report()
ids = {n["id"] for n in report["needs_verification"]}
check("ungreen gated ticket listed", "T-012" in ids, ids)
check("green ticket omitted", "T-013" not in ids, ids)
check("ungated ticket omitted", "T-014" not in ids, ids)
check("command shown in report", report["needs_verification"][0]["verify_cmd"] == "pytest -q")

# ── Timeout ───────────────────────────────────────────────────────────────

print("timeout:")
server.load_config = lambda: {**server.DEFAULT_CONFIG, "verify_timeout_sec": 1}
write_state([make_ticket("T-015", verify_cmd="sleep 5")])
r = server.verify_ticket("T-015")
check("timeout reported as failure", r.get("passed") is False and r.get("exit") == 124, r)
check("timeout explained in tail", "timeout" in r.get("tail", "").lower(), r)

# ── edit_project: the only way to give an existing project a repoPath ─────
#
# A project created without repo_path cannot run any verify_cmd, and
# create_project refuses a duplicate name, so without this tool the gate was
# unreachable for every such project and the only fix was editing state by hand.

print("edit_project repoPath:")
write_state([make_ticket("T-030", verify_cmd="git rev-parse HEAD")], repo_path="")
r = server.verify_ticket("T-030")
check("no repoPath refuses the run", "repoPath" in r.get("error", ""), r)

r = server.edit_project("test_project", repo_path="relative/path")
check("relative path refused", "absolute" in r.get("error", ""), r)
r = server.edit_project("test_project", repo_path=str(REPO / "does-not-exist"))
check("missing directory refused", "not a directory" in r.get("error", ""), r)
r = server.edit_project("test_project")
check("no fields refused", "Nothing to update" in r.get("error", ""), r)
r = server.edit_project("no_such_project", repo_path=str(REPO))
check("unknown project refused", "not found" in r.get("error", ""), r)

r = server.edit_project("test_project", repo_path=str(REPO))
check("repoPath set", r.get("success") is True and r.get("updated") == ["repoPath"], r)
check("git revision reported back", r.get("git_rev") == head(), r)

r = server.verify_ticket("T-030")
check("the gate runs once the path is set", r.get("passed") is True, r)
check("proof pinned to the revision", r.get("rev") == head(), r)
check("the close is allowed", server.update_ticket_status("T-030", "done").get("success") is True)

r = server.edit_project("test_project", repo_path=str(PLAIN))
check("a non-checkout is allowed", r.get("success") is True, r)
check("but says proof cannot be pinned", "not a git checkout" in r.get("note", ""), r)

r = server.edit_project("test_project", goal="a new goal", color="#ff0000")
check("goal and colour update too", r.get("updated") == ["color", "goal"], r)

shutil.rmtree(_tmpdir, ignore_errors=True)

print(f"\n{passed} passed, {failed} failed")
raise SystemExit(1 if failed else 0)
