"""Lifetime lock for CLI run loops on local Linux/macOS workspaces.

Ancillary ingesters may transact with SQLite; a second observer run loop
must not repeat external effects or overwrite derived briefing files.
Never unlink the lock file: waiters must keep referencing the same inode.
"""
import fcntl
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def workspace_writer(workspace: Path):
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    with (workspace / "director-run.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"Director run authority already held: {workspace}") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
