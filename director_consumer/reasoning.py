"""Validated structured reasoning over ASUS2 — DIRECTOR-06 §6.

ASUS2's qwen3.8-27b-nvfp4 is the Director's reasoning model. A prior probe
returned reasoning-style preamble and hit max_tokens instead of complying, so
consequential assessments now REQUIRE a validated structured response.

Contract for every consequential assessment:
  current_situation, relevant_retrieved_evidence, alternative_interpretations,
  proposed_recommendation, expected_effect, uncertainties,
  what_would_falsify, supporting_source_references

Rejected: invalid JSON, unsupported citations, model preambles outside the
envelope, and any model claim of verification. Bounded retry; falls back to a
deterministic (non-model) assessment. Every attempt is accounted in the model
usage ledger. Output is ADVISORY only and never becomes fact.

Exact source references must be checkable: a citation that does not resolve to
a known source_id is reported as unsupported, not passed through.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

REQUIRED_KEYS = (
    "current_situation", "relevant_retrieved_evidence",
    "alternative_interpretations", "proposed_recommendation",
    "expected_effect", "uncertainties", "what_would_falsify",
    "supporting_source_references",
)

# Words a model must never use to upgrade its own output to fact.
FORBIDDEN_CLAIMS = (
    "is verified", "has been verified", "i verified", "confirmed fact",
    "proven fact", "definitely", "certainly true", "as verified fact",
)

PREAMBLE_RE = re.compile(
    r"^\s*(sure|certainly|of course|here'?s|i'?ll|let me|as an ai|"
    r"the user asks|we need to|assistant:)\b", re.I)


@dataclass
class Assessment:
    ok: bool = False
    reason: str = ""
    payload: dict = field(default_factory=dict)
    attempts: int = 0
    latency_ms: float = 0.0
    fallback_used: bool = False
    unsupported_citations: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    advisory_note: str = ("advisory only — model output is never verified "
                          "fact and never becomes authority")


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None
    return d if isinstance(d, dict) else None


def validate(text: str, known_sources: Optional[set[str]] = None,
             require_falsifier: bool = True) -> Assessment:
    """Pure validation — no model call. Used by tests and by the retry loop."""
    a = Assessment()
    stripped = (text or "").strip()
    if not stripped:
        a.reason = "empty model response"
        return a
    if PREAMBLE_RE.match(stripped):
        a.rejected.append("preamble outside the JSON envelope")
    for bad in FORBIDDEN_CLAIMS:
        if bad in stripped.lower():
            a.rejected.append(f"model claims verification: {bad!r}")
    d = _extract_json(stripped)
    if d is None:
        a.rejected.append("no parseable JSON object")
        a.reason = "invalid JSON"
        return a
    missing = [k for k in REQUIRED_KEYS if k not in d]
    if missing:
        a.rejected.append(f"missing required keys: {missing}")
    if require_falsifier and not str(d.get("what_would_falsify", "")).strip():
        a.rejected.append("what_would_falsify is empty — unfalsifiable advice")
    refs = d.get("supporting_source_references")
    if isinstance(refs, str):
        refs = [refs]
    if not isinstance(refs, list) or not refs:
        a.rejected.append("no supporting_source_references")
        refs = []
    if known_sources is not None:
        for r in refs:
            rid = str(r)
            if not any(rid == k or rid in k or k in rid for k in known_sources):
                a.unsupported_citations.append(rid)
        if a.unsupported_citations:
            a.rejected.append(
                f"unsupported citations: {a.unsupported_citations}")
    if a.rejected:
        a.reason = "; ".join(a.rejected)
        return a
    a.ok = True
    a.payload = d
    a.reason = "validated"
    return a


SYSTEM_PROMPT = (
    "You are the Engineering Director's reasoning assistant. You are ADVISORY "
    "ONLY: never state a conclusion as verified fact and never claim to have "
    "verified anything. Respond with STRICT JSON and no prose outside it, with "
    "EXACTLY these keys: current_situation, relevant_retrieved_evidence, "
    "alternative_interpretations, proposed_recommendation, expected_effect, "
    "uncertainties, what_would_falsify, supporting_source_references. "
    "supporting_source_references must be source identifiers taken only from "
    "the evidence provided to you — never invented. what_would_falsify must "
    "name an observation that would prove the recommendation wrong."
)


def user_prompt(situation: str, evidence: list[dict]) -> str:
    ev = []
    for e in evidence[:12]:
        ev.append({
            "source_id": e.get("source_id", ""),
            "locator": e.get("locator", ""),
            "evidence_class": e.get("evidence_class", "unknown"),
            "title": e.get("title", ""),
            "excerpt": (e.get("body") or e.get("excerpt") or "")[:600],
        })
    return ("SITUATION:\n%s\n\nRETRIEVED EVIDENCE (the only citable sources):\n"
            "%s\n\nAnswer in the required JSON envelope."
            % (situation, json.dumps(ev, indent=1)))


def run_structured(client, situation: str, evidence: list[dict],
                   known_sources: set[str], max_retries: int = 2,
                   purpose: str = "decision-packet",
                   project: str = "designwins") -> Assessment:
    """Bounded retry with fallback to a deterministic assessment."""
    a = Assessment()
    t0 = time.time()
    prompt = user_prompt(situation, evidence)
    last_reason = ""
    for attempt in range(1, max_retries + 1):
        a.attempts = attempt
        try:
            text = client._call(client.endpoints[0], SYSTEM_PROMPT, prompt)
        except Exception as e:                              # noqa: BLE001
            last_reason = f"model call raised {type(e).__name__}: {e}"
            text = None
        res = validate(text, known_sources=known_sources)
        res.attempts = attempt
        if res.ok:
            res.latency_ms = (time.time() - t0) * 1000
            res.advisory_note = a.advisory_note
            return res
        last_reason = res.reason
        a.rejected = res.rejected
        a.unsupported_citations = res.unsupported_citations
    # deterministic fallback — no model, no invented content
    a.fallback_used = True
    a.ok = False
    a.latency_ms = (time.time() - t0) * 1000
    a.payload = {
        "current_situation": situation[:400],
        "relevant_retrieved_evidence": [
            {"source_id": e.get("source_id"), "locator": e.get("locator"),
             "evidence_class": e.get("evidence_class")}
            for e in evidence[:8]],
        "alternative_interpretations": ["model unavailable or invalid — see "
                                        "retrieved evidence directly"],
        "proposed_recommendation": "none — deterministic fallback engaged",
        "expected_effect": "unknown",
        "uncertainties": ["no validated model assessment was obtained"],
        "what_would_falsify": "n/a (no recommendation made)",
        "supporting_source_references": [
            e.get("source_id") for e in evidence[:8] if e.get("source_id")],
    }
    a.reason = (f"fallback after {a.attempts} attempts: {last_reason}"
                if last_reason else "fallback")
    return a


def deterministic_summary(evidence: list[dict]) -> dict:
    """The no-model path. Facts and gaps only — never a recommendation."""
    return {
        "current_situation": "unassessed (deterministic fallback)",
        "facts": [{"source_id": e.get("source_id"),
                   "evidence_class": e.get("evidence_class"),
                   "title": e.get("title")} for e in evidence[:10]],
        "gaps": ["no validated model assessment"],
        "proposed_recommendation": "none",
        "advisory_note": ("deterministic fallback presents evidence without "
                          "interpretation"),
    }
