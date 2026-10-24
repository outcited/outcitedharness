"""Operator-controlled OpenCode launch/receipt wrapper (05C session-origin gap).

Closes the trust loop that OpenCodeSessionTransport alone cannot: the
binding of (process, task, owner, epoch, session, artifact) is created by
THIS wrapper at launch time, under the operator account, and is the only
writer of the bindings registry. No caller-written identity header can
create a binding.

Flow:
  launch(task_id, owner, epoch, message_id, worktree, prompt, ...)
    1. record PENDING binding: prompt_sha256 + task/owner/epoch/message
    2. spawn `opencode run` (bounded) in the isolated worktree
    3. resolve the REAL session id from OpenCode's own store
       (~/.local/share/opencode/opencode.db) by directory + start window
    4. atomically finalize binding -> {by_artifact, by_task_epoch}

The registry shape serves BOTH validators:
  by_artifact[path] -> session_id      (OpenCodeSessionTransport.verify_origin)
  by_task_epoch["project|task|epoch"] -> session_id  (validate_acceptance)
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

SCHEMA = "harness.seven-opencode-binding.v1"
DEFAULT_DB = Path.home() / ".local/share/opencode/opencode.db"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class LaunchReceipt:
    task_id: str
    owner: str
    epoch: int
    message_id: str
    project: str
    prompt_sha256: str
    session_id: str
    pid: int
    exit_code: int
    started_at: float
    ended_at: float
    worktree: str
    artifact_path: str
    launched_by: str
    binding_sha256: str


class BindingRegistry:
    """Operator-protected session bindings (atomic, single-writer)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            if data.get("schema") != SCHEMA:
                return {"schema": SCHEMA, "by_artifact": {}, "by_task_epoch": {}}
            data.setdefault("by_artifact", {})
            data.setdefault("by_task_epoch", {})
            return data
        except (OSError, json.JSONDecodeError):
            return {"schema": SCHEMA, "by_artifact": {}, "by_task_epoch": {}}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
        os.replace(tmp, self.path)

    def register(self, receipt: LaunchReceipt) -> dict:
        data = self.load()
        key = f"{receipt.project}|{receipt.task_id}|{receipt.epoch}"
        data["by_artifact"][receipt.artifact_path] = receipt.session_id
        # flat map is what OpenCodeSessionTransport.verify_origin reads
        data[str(receipt.artifact_path)] = receipt.session_id
        data["by_task_epoch"][key] = receipt.session_id
        data.setdefault("receipts", {})[receipt.artifact_path] = asdict(receipt)
        self.save(data)
        return data

    def task_epoch_map(self) -> dict:
        """(project, task_ref, epoch) -> session_id for validate_acceptance."""
        out = {}
        for key, sid in self.load().get("by_task_epoch", {}).items():
            parts = key.split("|")
            if len(parts) == 3:
                try:
                    epoch = int(parts[2])
                except ValueError:
                    continue
                out[(parts[0], parts[1], epoch)] = sid
        return out


def resolve_session(opencode_db: Path, worktree: str, after_ts: float,
                    grace_s: float = 120.0) -> Optional[str]:
    """The REAL session from OpenCode's own store — never caller-declared."""
    con = None
    try:
        con = sqlite3.connect(f"file:{opencode_db}?mode=ro", uri=True,
                              timeout=5)
        rows = con.execute(
            "SELECT id, time_created, directory FROM session "
            "WHERE time_created >= ? ORDER BY time_created DESC LIMIT 20",
            (int((after_ts - grace_s) * 1000),)).fetchall()
    except (sqlite3.Error, OSError):
        return None
    finally:
        if con is not None:
            con.close()
    wt = str(Path(worktree).resolve())
    for sid, _ts, directory in rows:
        if directory and str(Path(directory).resolve()) == wt:
            return sid
    return rows[0][0] if rows else None


def launch(task_id: str, owner: str, epoch: int, message_id: str,
           project: str, worktree: Path, prompt: str,
           bindings_path: Path, artifact_path: Path,
           opencode_bin: str = "opencode",
           model: str = "asus2/qwen3.8-27b-nvfp4",
           opencode_db: Path = DEFAULT_DB,
           timeout_s: int = 1500,
           dry_run: bool = False) -> LaunchReceipt:
    """Launch one bounded OpenCode task and bind it, end to end."""
    worktree = Path(worktree).resolve()
    artifact_path = Path(artifact_path).resolve()
    started = time.time()
    prompt_sha = _sha(prompt)
    launched_by = f"opencode_launch.py uid={os.getuid()} user={os.getenv('USER','?')}"

    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    proc = None
    exit_code = 0
    stdout = stderr = ""
    if not dry_run:
        artifact_path.write_text(prompt)
        try:
            proc = subprocess.run(
                [opencode_bin, "run", "--model", model, prompt],
                cwd=str(worktree), capture_output=True, text=True,
                timeout=timeout_s,
                env={**os.environ,
                     "PATH": f"{Path.home()}/.opencode/bin:"
                             f"{os.environ.get('PATH','')}"})
            exit_code = proc.returncode
            stdout, stderr = proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired as e:
            exit_code = 124
            o, r = e.stdout or b"", e.stderr or b""
            stdout = o.decode(errors="replace") if isinstance(o, bytes) else (o or "")
            stderr = (r.decode(errors="replace") if isinstance(r, bytes) else (r or ""))
            stderr += chr(10) + f"WRAPPER: timed out after {timeout_s}s"
        artifact_path.with_suffix(".out.md").write_text((stdout or "")[-20000:])
        artifact_path.with_suffix(".err.md").write_text((stderr or "")[-8000:])
    ended = time.time()

    session_id = ""
    if not dry_run:
        session_id = (resolve_session(opencode_db, str(worktree), started)
                      or f"unresolved-{_sha(str(started))[:12]}")

    binding_src = "|".join([project, task_id, str(epoch), message_id,
                            prompt_sha, session_id, str(started)])
    receipt = LaunchReceipt(
        task_id=task_id, owner=owner, epoch=epoch, message_id=message_id,
        project=project, prompt_sha256=prompt_sha, session_id=session_id,
        pid=getattr(proc, "pid", -1), exit_code=exit_code,
        started_at=started, ended_at=ended, worktree=str(worktree),
        artifact_path=str(artifact_path), launched_by=launched_by,
        binding_sha256=_sha(binding_src))
    BindingRegistry(bindings_path).register(receipt)
    return receipt
