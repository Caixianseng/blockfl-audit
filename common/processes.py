"""Terminate only a Popen child created by the current process and its tree."""
from __future__ import annotations
import os
import subprocess
import psutil


def terminate_child_tree(process: subprocess.Popen, timeout: float = 15) -> list[int]:
    """Include the actual Python beneath a Windows venv redirector.

    The caller must own a still-live Popen object for a direct child. There is
    deliberately no arbitrary PID interface. psutil retains create-time checks
    on each Process instance before sending a termination signal.
    """
    if process.poll() is not None:
        return []
    try:
        parent = psutil.Process(process.pid)
        if parent.ppid() != os.getpid():
            raise ValueError("Refusing to terminate a process that is not our direct Popen child")
        owned = list(reversed(parent.children(recursive=True))) + [parent]
    except psutil.NoSuchProcess:
        return []
    for child in owned:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(owned, timeout=timeout)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=5)
    process.wait(timeout=5)
    return [p.pid for p in owned]
