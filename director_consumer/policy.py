"""Model resource governance + authority policy — loaded per project.

The router enforces, in CODE (never prompts):
  - allowlist: only endpoints named in policy.models.allowed are callable
  - metered providers (openrouter, anything provider==metered) are REFUSED
    without a task-specific authorization_ref recorded by the human owner
  - rate limits per policy
  - every call journaled (model_usage table) with provider/purpose/project

Authority policy replaces the hardcoded SAM_ONLY_CATEGORIES: unknown actions
fail CLOSED (escalate) exactly like before, but the mapping is project data.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

METERED_BLOCK_PROVIDERS = {"openrouter", "metered"}


class PolicyError(Exception):
    pass


@dataclass
class EndpointSpec:
    name: str
    provider: str
    base_url: str
    model: str
    role: str = ""
    authorization_ref: str = ""   # required for any metered endpoint


@dataclass
class ModelPolicy:
    endpoints: dict[str, EndpointSpec] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    per_minute: float = 6.0
    burst: int = 3
    tokens: float = 3.0
    last: float = field(default_factory=time.monotonic)
    usage_sink: Optional[object] = None   # state.record_model_usage(dict)

    @classmethod
    def load(cls, path: Path) -> "ModelPolicy":
        data = yaml.safe_load(Path(path).read_text()) or {}
        m = data.get("models", {})
        pol = cls()
        for e in m.get("allowed", []):
            spec = EndpointSpec(**{k: e[k] for k in
                                   ("name", "provider", "base_url", "model")},
                                role=e.get("role", ""),
                                authorization_ref=e.get("authorization_ref", ""))
            pol.endpoints[spec.name] = spec
        rl = m.get("rate_limits", {})
        pol.per_minute = float(rl.get("per_minute", 6))
        pol.burst = int(rl.get("burst", 3))
        pol.tokens = float(pol.burst)
        pol.order = list(pol.endpoints)
        return pol

    # ---- enforcement ----

    def check(self, name: str, purpose: str = "") -> tuple[bool, str]:
        """Hard gate. Returns (allowed, reason). Metered NEVER passes
        without an authorization_ref; unknown names never pass. Provider
        blocklist is checked before the allowlist so a misconfigured
        allowlist entry still cannot spend."""
        ep = self.endpoints.get(name)
        blocked = ((ep is not None and
                    ep.provider.lower() in METERED_BLOCK_PROVIDERS) or
                   name.lower() in METERED_BLOCK_PROVIDERS or
                   any(b in name.lower() for b in METERED_BLOCK_PROVIDERS))
        if blocked:
            if ep is not None and ep.authorization_ref:
                return True, f"metered authorized by {ep.authorization_ref}"
            return False, (f"METERED REFUSED: {name} — no task-specific "
                           f"spend authorization (director cannot authorize "
                           f"spending)")
        if ep is None:
            return False, f"endpoint {name!r} not in project allowlist"
        return True, "ok"

    def allow_rate(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens +
                          (now - self.last) * (self.per_minute / 60.0))
        self.last = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def record(self, name: str, ok: bool, latency_ms: float,
               purpose: str, project: str, note: str = "") -> None:
        if self.usage_sink is not None:
            ep = self.endpoints.get(name)
            self.usage_sink({
                "endpoint": name,
                "provider": ep.provider if ep else "unknown",
                "model": ep.model if ep else "",
                "ok": ok, "latency_ms": round(latency_ms, 1),
                "purpose": purpose, "project": project, "note": note})


@dataclass
class AuthorityPolicy:
    human_owner: str = "sam"
    human_only_actions: dict[str, str] = field(default_factory=dict)
    agent_authorities: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "AuthorityPolicy":
        data = yaml.safe_load(Path(path).read_text()) or {}
        a = data.get("authority", {})
        return cls(human_owner=a.get("human_owner", "sam"),
                   human_only_actions=dict(a.get("human_only_actions", {})),
                   agent_authorities=dict(a.get("agent_authorities", {})))

    def is_human_only(self, action: str) -> tuple[bool, str]:
        """Unknown action fails CLOSED: treated as human-only."""
        if action in self.human_only_actions:
            return True, self.human_only_actions[action]
        return True, "unmapped action — fail closed (project policy)"


def load_project(project_dir: Optional[Path]):
    """Resolution order (DIRECTOR-02-BOUNDARIES): project dir -> engine
    defaults. Returns (ModelPolicy, AuthorityPolicy)."""
    project_dir = Path(project_dir) if project_dir else None
    defaults = Path(__file__).parent
    def pick(name: str) -> Optional[Path]:
        if project_dir and (project_dir / name).exists():
            return project_dir / name
        if (defaults / name).exists():
            return defaults / name
        return None
    p = pick("policy.yaml")
    if p:
        return ModelPolicy.load(p), AuthorityPolicy.load(p)
    return ModelPolicy(), AuthorityPolicy()
