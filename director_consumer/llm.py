"""Ops-brain model router adapter.

Policy (DIRECTOR-BOOTSTRAP-PLAN §0): the LLM is an ENRICHMENT path only —
P0/ambiguous events may ask the brain for an assessment sketch, which is
stored as model_assessment with verified:false forever. Classification
itself is deterministic (impact.py) and NEVER depends on this module.

Endpoint loss degrades to None; the consumer keeps ingesting.
"""
from __future__ import annotations

import json
import time
import urllib.request
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    rate_per_min: float = 6.0     # steady calls/min (P0-only policy keeps this tiny)
    burst: int = 3
    tokens: float = 3.0
    last: float = field(default_factory=time.monotonic)

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens +
                          (now - self.last) * (self.rate_per_min / 60.0))
        self.last = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


@dataclass
class Endpoint:
    name: str
    base_url: str
    model: str


DEFAULT_ENDPOINTS = [
    Endpoint("asus2_ops_brain", "http://100.68.133.1:8900", "qwen3.8-27b-nvfp4"),
    Endpoint("dgx1_overflow", "http://100.81.201.24:8901", "qwen3.8-27b-fp8"),
]


class OpsBrainClient:
    """Primary -> overflow -> None. Never raises to the caller.

    DIRECTOR-02 §6: when a ModelPolicy is supplied, enforcement is in the
    router, not the prompt — endpoints must be allowlisted, metered
    providers are hard-refused, rate limits apply, and every attempt
    (allowed OR refused) is recorded to the usage ledger.
    """

    def __init__(self, endpoints=None, timeout_s: float = 10.0,
                 bucket: TokenBucket | None = None,
                 enabled: bool = True,
                 policy=None, purpose: str = "impact-enrichment",
                 project: str = "default"):
        self.timeout_s = timeout_s
        self.bucket = bucket if bucket is not None else TokenBucket()
        self.enabled = enabled
        self.policy = policy
        self.purpose = purpose
        self.project = project
        if policy is not None:
            self.endpoints = [
                Endpoint(name, spec.base_url, spec.model)
                for name, spec in policy.endpoints.items()
                if spec.role != "capacity-available-only" or endpoints]
        else:
            self.endpoints = endpoints if endpoints is not None \
                else DEFAULT_ENDPOINTS

    def _call(self, ep: Endpoint, system: str, user: str) -> str | None:
        body = {
            "model": ep.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0, "max_tokens": 300,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        req = urllib.request.Request(
            ep.base_url + "/v1/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        r = json.load(urllib.request.urlopen(req, timeout=self.timeout_s))
        content = r["choices"][0]["message"]["content"]
        return (content or "").strip() or None

    def assess(self, event_summary: str, classification: str) -> dict | None:
        """Returns {'source': endpoint, 'text': ..., 'verified': False} or
        {'unavailable': reason}. Inference is never promoted to fact."""
        if not self.enabled:
            return {"unavailable": "llm disabled"}
        if not self.bucket.allow():
            return {"unavailable": "token bucket empty"}
        reasons = []
        for ep in self.endpoints:
            if self.policy is not None:
                allowed, why = self.policy.check(ep.name, self.purpose)
                if not allowed:
                    reasons.append(f"{ep.name}: REFUSED ({why})")
                    self.policy.record(ep.name, False, 0.0, self.purpose,
                                       self.project, note=why)
                    continue
                if not self.policy.allow_rate():
                    reasons.append(f"{ep.name}: policy rate limit")
                    continue
            t0 = time.monotonic()
            try:
                text = self._call(
                    ep,
                    "You are an engineering impact reviewer. Be terse (<=80 "
                    "words). Your output is advisory only and never verified fact.",
                    f"Deterministic classification: {classification}\n"
                    f"Event: {event_summary}\n"
                    "One-paragraph impact note:")
                latency = (time.monotonic() - t0) * 1000
                if self.policy is not None:
                    self.policy.record(ep.name, True, latency, self.purpose,
                                       self.project)
                if text:
                    return {"source": ep.name, "text": text, "verified": False}
                reasons.append(f"{ep.name}: empty")
            except Exception as e:  # noqa: BLE001 — degrade, never raise
                latency = (time.monotonic() - t0) * 1000
                if self.policy is not None:
                    self.policy.record(ep.name, False, latency, self.purpose,
                                       self.project,
                                       note=type(e).__name__)
                reasons.append(f"{ep.name}: {type(e).__name__}")
        return {"unavailable": "; ".join(reasons)}
