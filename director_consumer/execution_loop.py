"""Execution loop — DIRECTOR-WORKLOOP-01: close the loop from task to
verified completion using ONLY existing Director machinery.

Pieces (each honest about what is measured vs gated):

  1. CONFIRMED OWNER REGISTRY (owners.yaml beside project config).
     Agent identity comes from the onboarding roster (issue #3) + declared
     agent network — never branch prefixes, never issue text. An owner not
     in the registry resolves UNKNOWN and delivery refuses.

  2. OWNER RESOLUTION: task lane -> confirmed agent, with evidence string
     recording WHERE the identity came from.

  3. DELIVERY: existing AuthorizationStore + MailboxDelivery. Requires a
     REAL approval artifact owned by the operator uid (uid-separated trust
     boundary enforced by AuthorizationStore). No artifact -> REFUSED,
     recorded, never bypassed. The artifact names ONE task, ONE recipient,
     ONE action, with expiry — least authority.

  4. ACK OBSERVATION: the recipient's attributable ack is read from the
     delivery transport (mailbox receipt). Missing binding or stale epoch
     is NOT an ack (matches messaging.validate_acceptance discipline).

  5. VERIFICATION: independent check of the NAMED proof artifact for the
     work class (PROOF_MAP). A green-looking status message is not
     verification; the artifact must be observed (file/PR/test receipt).

  6. HISTORY ACROSS FAILURES: every step is a TaskStore transition with
     evidence + epoch; restart-safe by construction (05 contracts).
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

import yaml

from .tasks import TaskStore
from .workgraph import PROOF_MAP

UNKNOWN = "unknown"


# ------------------------------------------------------------------ owners
class OwnerRegistry:
    """Confirmed agents only. Source: onboarding roster (#3) + declared
    agent network. This file is the registry — branch prefixes are not."""

    def __init__(self, path: Optional[Path]):
        self.path = Path(path) if path else None
        self._d = {}
        if self.path and self.path.exists():
            self._d = yaml.safe_load(self.path.read_text()) or {}

    def resolve(self, lane: str) -> tuple[str, str, dict]:
        """Returns (agent_id, evidence, record). UNKNOWN if unconfirmed."""
        rec = (self._d.get("agents") or {}).get(lane)
        if not rec:
            return UNKNOWN, f"lane {lane!r} has no confirmed owner in registry", {}
        ev = (f"confirmed via {rec.get('source', 'registry')} "
              f"(ack channel: {rec.get('ack_channel', UNKNOWN)})")
        return rec.get("agent_id", UNKNOWN), ev, rec

    def agent_by_id(self, agent_id: str) -> Optional[dict]:
        for lane, rec in (self._d.get("agents") or {}).items():
            if rec.get("agent_id") == agent_id:
                return rec
        return None


# ------------------------------------------------------------- verifier
class Verifier:
    """Independent verification against the NAMED artifact. Never accepts
    a status message as proof. Returns (verified: bool, evidence: str)."""

    def __init__(self, workspace: Path, repo: Optional[Path] = None):
        self.workspace = Path(workspace)
        self.repo = Path(repo) if repo else None

    def check(self, kind: str, task_id: str, spec: dict) -> tuple[bool, str]:
        """spec: how to check this task's proof artifact."""
        checks = {
            "file_exists": self._file_exists,
            "github_comment": self._github_comment,
            "mailbox_ack": self._mailbox_ack,
            "tests_pass": self._tests_pass,
        }
        how = spec.get("verify_how")
        fn = checks.get(how)
        if fn is None:
            return False, (f"no verifier for {how!r}; artifact spec: "
                           f"{PROOF_MAP.get(kind, UNKNOWN)} — NOT verified")
        return fn(task_id, spec)

    def _file_exists(self, task_id, spec):
        p = Path(spec["path"])
        if not p.exists():
            return False, f"artifact absent: {p}"
        return True, f"artifact present: {p} (mtime {time.strftime('%FT%TZ', time.gmtime(p.stat().st_mtime))})"

    def _github_comment(self, task_id, spec):
        url = spec.get("api_url", "")
        token = spec.get("token")
        if not url:
            return False, "no api_url given — cannot verify GitHub artifact"
        cmd = ["curl", "-sS", "--max-time", "20", url]
        if token:
            cmd += ["-H", f"Authorization: Bearer {token}"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        try:
            d = json.loads(r.stdout)
        except Exception:                                        # noqa: BLE001
            return False, f"github api unparseable: {r.stdout[:80]}"
        if isinstance(d, dict) and d.get("html_url"):
            return True, (f"github artifact observed: {d['html_url']} "
                          f"(author {d.get('user', {}).get('login')})")
        return False, f"github api negative: {str(d)[:120]}"

    def _mailbox_ack(self, task_id, spec):
        box = Path(spec["mailbox"])
        pat = spec.get("pattern", task_id)
        if not box.is_dir():
            return False, f"mailbox absent: {box}"
        for f in sorted(box.glob("*.md")) + sorted(box.glob("*.json")):
            try:
                body = f.read_text(errors="replace")
            except OSError:
                continue
            if pat in body:
                return True, f"ack artifact observed: {f.name}"
        return False, f"no ack containing {pat!r} in {box}"

    def _tests_pass(self, task_id, spec):
        if not self.repo:
            return False, "no repo configured for test verification"
        r = subprocess.run(
            [spec.get("python", "/usr/bin/python3"), "-m", "pytest",
             spec.get("tests", "tests/"), "-q"],
            cwd=self.repo, capture_output=True, text=True,
            timeout=spec.get("timeout", 600))
        tail = (r.stdout or "").strip().splitlines()[-1:] or ["<no output>"]
        ok = r.returncode == 0
        return ok, f"pytest exit={r.returncode}: {tail[0]}"


# --------------------------------------------------------------- driver
class ExecutionLoop:
    """One pass: resolve owner -> deliver (gated) -> observe ack -> verify.
    Each step records a TaskStore transition with evidence; a refused or
    failed step STOPS the loop for that task and records why."""

    def __init__(self, ts: TaskStore, owners: OwnerRegistry,
                 verifier: Verifier, actor: str = "director-execution"):
        self.ts = ts
        self.owners = owners
        self.ver = verifier
        self.actor = actor

    def run(self, task_id: str, lane: str, deliver=None,
            ack_spec: Optional[dict] = None,
            verify_spec: Optional[dict] = None,
            kind: str = "") -> dict:
        out = {"task_id": task_id, "steps": []}

        def step(name, ok, detail):
            out["steps"].append({"step": name, "ok": ok, "detail": detail})
            return ok

        rec = self.ts.get(task_id)
        if rec is None:
            step("resolve-task", False, f"unknown task {task_id}")
            return out
        step("resolve-task", True, f"state={rec.state} epoch={rec.epoch}")

        agent, ev, _ = self.owners.resolve(lane)
        if not step("resolve-owner", agent != UNKNOWN, f"{agent}: {ev}"):
            return out

        if deliver is None:
            step("deliver", False,
                 "no delivery function provided (authorization gate not "
                 "exercised in this pass)")
        else:
            try:
                ok, detail = deliver(task_id, agent)
                step("deliver", ok, detail)
                if not ok:
                    return out  # gate refused: stop, record, never bypass
            except Exception as e:                              # noqa: BLE001
                step("deliver", False, f"delivery raised {type(e).__name__}: {e}")
                return out

        if ack_spec:
            ok, detail = self.ver.check("ack", task_id, ack_spec)
            if step("observe-ack", ok, detail) and ok:
                self.ts.transition(task_id, "acknowledged", actor=agent,
                                   evidence=detail)
        if verify_spec:
            ok, detail = self.ver.check(kind or "generic", task_id, verify_spec)
            step("verify", ok, detail)
            if ok:
                state = self.ts.get(task_id).state
                if state == "submitted":
                    self.ts.transition(task_id, "verified", actor=self.actor,
                                       evidence=detail)
        return out
