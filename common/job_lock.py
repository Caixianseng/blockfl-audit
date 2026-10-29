"""Crash-released OS mutex for all operations that mutate this demo.

The lock file is never unlinked. JSON is informational metadata, not the lock.
An owning batch can delegate to its actual descendants using an unguessable
lease; a copied/forged environment alone does not grant access. Process-local
reentry also covers audit worker threads, so a pipeline does not deadlock.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import secrets
import threading
from typing import Iterator

import psutil

ROOT = Path(__file__).resolve().parents[1]
LOCK_DIR = ROOT / "results"
_GUARD = threading.RLock()
_STATES: dict[Path, dict] = {}
_LEASE_ENV = "BLOCKFL_JOB_LEASE"


class JobBusyError(RuntimeError):
    """Another process owns the demo's mutation lease."""


class JobLeaseLostError(RuntimeError):
    """A delegated worker lost its live parent lease."""


class _Mutex:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("a+b")
        self.held = False

    def acquire(self) -> bool:
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            raise
        self.held = True
        if os.fstat(self.handle.fileno()).st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        return True

    def close(self):
        if self.held:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.held = False
        self.handle.close()


def _paths(lock_dir=None):
    directory = Path(lock_dir if lock_dir is not None else LOCK_DIR).resolve()
    return directory / ".job.lock", directory / ".job-status.json"


def _metadata(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_metadata(path: Path, data: dict):
    temporary = path.with_name(path.name + f".{os.getpid()}.{threading.get_ident()}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _public(meta: dict) -> dict:
    return {key: value for key, value in meta.items() if key != "lease_sha256"}


def _lock_is_held(path: Path) -> bool:
    probe = _Mutex(path)
    try:
        return not probe.acquire()
    finally:
        probe.close()


def _valid_delegation(lock_path: Path, metadata_path: Path, token: str) -> dict | None:
    if not token:
        return None
    meta = _metadata(metadata_path)
    expected_hash=meta.get("lease_sha256", "")
    if not meta.get("active") or not isinstance(expected_hash,str) or not secrets.compare_digest(expected_hash, hashlib.sha256(token.encode()).hexdigest()):
        return None
    try:
        owner = psutil.Process(int(meta["pid"]))
        if abs(owner.create_time() - float(meta["created"])) > 0.01:
            return None
        # Siblings, unrelated CLIs, and PID reuse cannot inherit this lease.
        ancestors = {p.pid: p.create_time() for p in psutil.Process().parents()}
        if owner.pid not in ancestors or abs(ancestors[owner.pid] - owner.create_time()) > 0.01:
            return None
    except (psutil.Error, KeyError, TypeError, ValueError):
        return None
    if not _lock_is_held(lock_path):
        return None
    # Owner clears active metadata before unlocking; re-read after probing to
    # reject a lease observed across release/reacquisition by another job.
    if _metadata(metadata_path) != meta:
        return None
    return meta


def job_status(*, lock_dir=None) -> dict | None:
    """Return authoritative busy state without trusting stale JSON metadata."""
    lock_path, metadata_path = _paths(lock_dir)
    with _GUARD:
        state = _STATES.get(lock_path)
        if state and state["pid"] == os.getpid() and state["depth"]:
            return {**_public(state["meta"]), "busy": True}
    if not lock_path.exists() or not _lock_is_held(lock_path):
        return None
    meta = _metadata(metadata_path)
    return {**_public(meta), "busy": True, "operation": meta.get("operation", "task-starting")}


def delegated_env(base_env: dict | None = None, *, lock_dir=None) -> dict:
    """Pass a held parent lease to subprocesses; caller keeps its context open."""
    lock_path, _ = _paths(lock_dir)
    with _GUARD:
        state = _STATES.get(lock_path)
        if not state or state["pid"] != os.getpid() or not state["depth"]:
            raise RuntimeError("A job lock must be held before delegating")
        env = dict(os.environ if base_env is None else base_env)
        env[_LEASE_ENV] = state["token"]
        return env


@contextmanager
def job_lock(operation: str, *, lock_dir=None) -> Iterator[dict]:
    """Fail fast on conflict; reenter safely within an owning process.

    ``lock_dir`` is only an explicit API/testing parameter. An environment
    variable cannot redirect the production mutex to a different file.
    """
    lock_path, metadata_path = _paths(lock_dir)
    with _GUARD:
        state = _STATES.get(lock_path)
        if state and state["pid"] != os.getpid():
            _STATES.pop(lock_path, None)  # Defensive handling for POSIX fork.
            state = None
        if state and state["depth"]:
            if state["delegated"] and _valid_delegation(lock_path, metadata_path, state["token"]) is None:
                raise JobLeaseLostError("The parent job lease is no longer active")
            state["depth"] += 1
        else:
            token = os.environ.get(_LEASE_ENV, "")
            delegated = _valid_delegation(lock_path, metadata_path, token)
            mutex = None
            if delegated:
                meta = delegated
            else:
                mutex = _Mutex(lock_path)
                if not mutex.acquire():
                    mutex.close()
                    busy = _metadata(metadata_path)
                    raise JobBusyError(f"Another operation is active: {busy.get('operation', 'task-starting')} (PID {busy.get('pid', 'unknown')})")
                token = secrets.token_urlsafe(32)
                meta = {"active": True, "pid": os.getpid(), "created": psutil.Process().create_time(),
                        "operation": str(operation), "started_at": datetime.now(timezone.utc).isoformat(),
                        "lease_sha256": hashlib.sha256(token.encode()).hexdigest()}
                try:
                    _write_metadata(metadata_path, meta)
                except BaseException:
                    mutex.close()
                    raise
            state = {"pid": os.getpid(), "depth": 1, "delegated": bool(delegated), "token": token,
                     "mutex": mutex, "meta": meta}
            _STATES[lock_path] = state
    try:
        yield _public(state["meta"])
    finally:
        with _GUARD:
            state["depth"] -= 1
            if state["depth"] == 0:
                try:
                    if not state["delegated"]:
                        _write_metadata(metadata_path, {"active": False, "released_at": datetime.now(timezone.utc).isoformat()})
                finally:
                    if state["mutex"] is not None:
                        state["mutex"].close()
                    _STATES.pop(lock_path, None)
