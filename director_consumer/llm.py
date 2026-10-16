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
    """Primary -> overflow -> None. Never raises to the caller."""

    def __init__(self, endpoints=None, timeout_s: float = 10.0,
                 bucket: TokenBucket | None = None,
                 enabled: bool = True):
        self.endpoints = endpoints if endpoints is not None else DEFAULT_ENDPOINTS
        self.timeout_s = timeout_s
        self.bucket = bucket or TokenBucket()
        self.enabled = enabled

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
            try:
                text = self._call(
                    ep,
                    "You are an engineering impact reviewer. Be terse (<=80 "
                    "words). Your output is advisory only and never verified fact.",
                    f"Deterministic classification: {classification}\n"
                    f"Event: {event_summary}\n"
                    "One-paragraph impact note:")
                if text:
                    return {"source": ep.name, "text": text, "verified": False}
                reasons.append(f"{ep.name}: empty")
            except Exception as e:  # noqa: BLE001 — degrade, never raise
                reasons.append(f"{ep.name}: {type(e).__name__}")
        return {"unavailable": "; ".join(reasons)}
