"""
Shared, concurrency-safe persistence for DevFlow state.

Both server.py (Claude Code spawns one stdio MCP process per session) and
http_api.py mutate the same devflow_state.json. Without coordination, each
long-lived process holds a drifting in-memory copy and clobbers the others on
save (last-stale-writer-wins) -> silent ticket loss and next_id reverting.
This was T-174: 16 orphaned server.py instances lost tickets and reverted the
on-disk counter to next_id=173.

Every mutation MUST go through mutate_state(), which:
  1. takes an exclusive OS-level lock on a sidecar .lock file (fcntl.flock),
  2. re-reads the current state from disk inside the lock (never trusts an
     in-memory copy for writes),
  3. applies the caller's mutation to that fresh state,
  4. writes to a temp file and os.replace()s it into place (atomic),
  5. releases the lock.

Readers use read_state(), a plain read. Because writes always land via an
atomic os.replace(), a reader sees a complete old-or-new file, never a torn
write, so reads need no lock. Writes always re-read under the lock, so any
in-memory copy held by the caller is irrelevant to correctness.

The schema is unchanged: {"projects": [...], "tickets": [...], "next_id": N}.

Paths can be overridden for testing via the DEVFLOW_STATE_FILE env var (the
lock file is always <state-file>.lock alongside it).
"""

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

import fcntl

# ── Paths (env-overridable for tests) ───────────────────────────────────────────

CONFIG_DIR = Path(
    os.environ.get("DEVFLOW_CONFIG_DIR", os.path.expanduser("~/.config/devflow-mcp"))
)

if os.environ.get("DEVFLOW_STATE_FILE"):
    STATE_FILE = Path(os.environ["DEVFLOW_STATE_FILE"])
else:
    STATE_FILE = CONFIG_DIR / "devflow_state.json"

LOCK_FILE = Path(str(STATE_FILE) + ".lock")

DEFAULT_STATE = {
    "projects": [],
    "tickets": [],
    "next_id": 1,
}


# ── Abort sentinel ──────────────────────────────────────────────────────────────


class Abort(Exception):
    """Raise inside a mutator (via abort()) to return a result WITHOUT writing.

    Used for validation/error paths (e.g. "project not found") that must not
    persist any change.
    """

    def __init__(self, result):
        super().__init__("state mutation aborted")
        self.result = result


def abort(result):
    """Abort the current mutation, returning `result` to the caller, no write."""
    raise Abort(result)


# ── Core read / write primitives ────────────────────────────────────────────────


def _read_unlocked() -> dict:
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return {**DEFAULT_STATE, **json.load(f)}
    return dict(DEFAULT_STATE)


def read_state() -> dict:
    """Read the current state from disk.

    Safe without a lock: writes land via atomic os.replace(), so a reader
    always sees a complete file. Callers MUST NOT persist a read_state() result
    directly — all writes go through mutate_state(), which re-reads under lock.
    """
    return _read_unlocked()


# Backwards-compatible alias for the original module-level name.
load_state = read_state


class DestructiveWriteRefused(RuntimeError):
    """Raised when a write would wipe or rewind a non-empty store.

    Two real incidents made this guard (both erased the live board):
      2026-07-28 — a stale browser tab POSTed an old snapshot (132 tickets lost);
      2026-08-20 — a duplicate server's startup wrote a fresh-init state over
                   615 tickets before dying on the taken port.
    The rule: against an existing store, a write may not drop to zero tickets,
    shrink the ticket count by more than half, or lower the monotonic next_id.
    A deliberate reset must set DEVFLOW_ALLOW_DESTRUCTIVE_WRITE=1.
    """


def _refuse_destructive(new_state: dict) -> None:
    if os.environ.get("DEVFLOW_ALLOW_DESTRUCTIVE_WRITE") == "1":
        return
    if not STATE_FILE.exists():
        return
    try:
        with open(STATE_FILE) as f:
            current = json.load(f)
    except (OSError, ValueError):
        return  # unreadable current file: the atomic replace is the repair path
    cur_n = len(current.get("tickets", []))
    new_n = len(new_state.get("tickets", []))
    cur_id = current.get("next_id", 1)
    new_id = new_state.get("next_id", 1)
    if cur_n > 0 and (new_n == 0 or new_n < cur_n / 2 or new_id < cur_id):
        raise DestructiveWriteRefused(
            f"refusing write: on-disk store has {cur_n} tickets (next_id {cur_id}), "
            f"incoming state has {new_n} (next_id {new_id}). If this reset is "
            "deliberate, set DEVFLOW_ALLOW_DESTRUCTIVE_WRITE=1."
        )


_BACKUP_KEEP = 30


def _rotate_backup() -> None:
    """Copy the current state into backups/ before replacing it. Best-effort —
    a backup failure must never block a legitimate write."""
    try:
        if not STATE_FILE.exists():
            return
        import shutil
        import time as _time

        bdir = STATE_FILE.parent / "backups"
        bdir.mkdir(parents=True, exist_ok=True)
        stamp = _time.strftime("%Y%m%d-%H%M%S")
        shutil.copy2(STATE_FILE, bdir / f"devflow_state.{stamp}.json")
        old = sorted(bdir.glob("devflow_state.*.json"))
        for p in old[:-_BACKUP_KEEP]:
            p.unlink(missing_ok=True)
    except OSError:
        pass


def _atomic_write(state: dict) -> None:
    """Write state to a temp file in the same dir, fsync, then os.replace().

    Guarded: refuses wipes/rewinds of a non-empty store (DestructiveWriteRefused)
    and rotates a timestamped backup of the outgoing file first."""
    _refuse_destructive(state)
    _rotate_backup()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(STATE_FILE.parent), prefix=".devflow_state.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextmanager
def _exclusive_lock():
    """Hold an exclusive flock on the sidecar lock file for the duration."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# ── The one persistence helper every mutation uses ──────────────────────────────


def mutate_state(mutator):
    """Apply a single mutation safely under N concurrent processes.

    Acquires an exclusive lock, RE-READS state from disk inside the lock,
    passes that fresh state to `mutator(state)` (which mutates it in place and
    returns a result), then writes atomically and releases the lock.

    The mutator may call abort(result) to return without writing (used for
    validation failures). Any exception other than Abort propagates and no
    write occurs.
    """
    with _exclusive_lock():
        state = _read_unlocked()
        try:
            result = mutator(state)
        except Abort as a:
            return a.result
        _atomic_write(state)
        return result


# ── ID allocation (must be called inside a mutator, on freshly-read state) ───────


def next_ticket_id(state: dict) -> str:
    tid = f"T-{state['next_id']:03d}"
    state["next_id"] = state["next_id"] + 1
    return tid
