"""Agent runtime contract (§5) — runtime-agnostic identity + lifecycle.

OpenCode is the first adapter; the interface is generic. Interactive
human-owned sessions are NEVER touched; managed execution is simulated
only (spawning disabled). A terminal tab is not an identity; a disconnected
terminal is not completion; a session title is not authority.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

from .sessions import SessionDiscovery, SessionRecord

UNKNOWN = "unknown"


@dataclass
class AgentIdentity:
    agent_id: str
    runtime: str = "opencode"
    host: str = UNKNOWN
    mode: str = UNKNOWN            # interactive | managed | unknown
    session_id: str = ""
    project_id: str = ""
    task_id: str = ""
    capabilities: list = field(default_factory=list)
    ownership_epoch: int = 0
    heartbeat_ts: float = 0.0
    progress_evidence: str = ""
    allowed_tools: list = field(default_factory=list)
    model_policy_ref: str = ""
    budget: dict = field(default_factory=dict)
    worktree: str = UNKNOWN
    repo: str = UNKNOWN
    recovery_state: str = UNKNOWN


class AgentAdapter(Protocol):
    def discover(self) -> list[AgentIdentity]: ...
    def heartbeat(self, agent_id: str, evidence: str) -> None: ...


class OpenCodeAdapter:
    """Read-only discovery from OpenCode metadata + declared registry.
    mode: managed only when the session has a parent (subagent) — root
    sessions are interactive-by-default and never touched."""

    def __init__(self, session_discovery: SessionDiscovery,
                 declared_agents: Optional[dict] = None):
        self.sd = session_discovery
        self.declared = declared_agents or {}

    def discover(self) -> list[AgentIdentity]:
        out = []
        for s in self.sd.discover():
            kind = self.declared.get(s.agent, {}).get("kind", UNKNOWN)
            mode = ("managed" if s.parent_id else
                    "interactive" if kind == "human-interactive" else
                    "managed" if kind in ("autonomous-worker",
                                          "director-managed-future")
                    else UNKNOWN)
            out.append(AgentIdentity(
                agent_id=s.agent or UNKNOWN, runtime="opencode",
                host=s.machine, mode=mode, session_id=s.session_id,
                project_id=self.declared.get(s.agent, {}).get("project", ""),
                task_id=(s.task_ids[0] if s.task_ids else ""),
                ownership_epoch=0, heartbeat_ts=s.last_activity_ts,
                progress_evidence=s.task_state,
                worktree=s.repo_worktree, repo=s.repo_worktree,
                recovery_state=s.process_health))
        return out

    def heartbeat(self, agent_id: str, evidence: str) -> None:
        raise NotImplementedError("read-only adapter (observe mode)")


class SimulatedAgent:
    """Managed-execution lifecycle for tests and the future spawn path.
    Spawning is DISABLED platform-wide; this is the conformance model."""

    def __init__(self, agent_id: str, project_id: str, task_id: str,
                 host: str = "sim-host", epoch: int = 1):
        self.identity = AgentIdentity(
            agent_id=agent_id, runtime="simulated", host=host, mode="managed",
            session_id=f"ses-sim-{agent_id}", project_id=project_id,
            task_id=task_id, ownership_epoch=epoch,
            allowed_tools=["read", "write-worktree"],
            model_policy_ref="projects/<pid>/policy.yaml",
            budget={"max_model_calls": 50, "max_spend_usd": 0.0})
        self.events: list[tuple[str, str]] = []

    def acknowledge(self, evidence: str) -> None:
        self.events.append(("acknowledged", evidence))
        self.identity.progress_evidence = evidence

    def progress(self, evidence: str) -> None:
        self.events.append(("progress", evidence))
        self.identity.heartbeat_ts = time.time()
        self.identity.progress_evidence = evidence

    def submit(self, evidence: str) -> None:
        self.events.append(("submitted", evidence))
        self.identity.recovery_state = "submitted"

    def disconnect(self) -> None:
        self.events.append(("disconnected", "terminal lost"))
        self.identity.recovery_state = "disconnected"

    def verify_or_fail(self, ok: bool, evidence: str) -> str:
        self.events.append(("verified" if ok else "failed-verification",
                            evidence))
        self.identity.recovery_state = (
            "verified" if ok else "failed-verification")
        return self.identity.recovery_state
