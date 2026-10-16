"""Deterministic impact classification + decision-authority boundaries.

The YAML map is the only source of classification; this module loads and
validates it (schema/version present, rule ids unique, impacts are known
nodes) and matches events to rules. Matching is substring/kind-based and
fully deterministic — the LLM is never in this path.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

import yaml

from .schema import Assessment, Event, SourceRef, fp

# Human-only decision authority (HANDOFF-m5-20261012.md "Sam's calls, only"):
# ratify DECISIONS.md entries, cloud expense, freeze changes, alias folds
# crossing physical quantities, promotion/release execution.
SAM_ONLY_CATEGORIES = {
    "propose_promotion_freeze": "freeze change (HANDOFF-m5: Sam's calls only)",
    "propose_spend_review": "cloud expense (HANDOFF-m5: Sam's calls only)",
    "propose_release_nudge": "release execution (promotion packet owner)",
}


class ImpactMapError(Exception):
    pass


@dataclass
class Rule:
    rid: str
    feed: str
    any_substrings: list[str]
    kinds: list[str]
    severity: str
    impacts: list[str]
    action: str
    authority: str
    rationale: str


class ImpactMap:
    def __init__(self, data: dict):
        if data.get("schema") != 1:
            raise ImpactMapError("deps.yaml: unsupported schema")
        if "version" not in data:
            raise ImpactMapError("deps.yaml: missing version")
        self.version = data["version"]
        self.nodes: dict[str, dict] = data["nodes"]
        self.edges: dict[str, list[str]] = data["edges"]
        self.rules: list[Rule] = []
        seen_ids = set()
        for r in data.get("rules", []):
            rid = r.get("id")
            if not rid or rid in seen_ids:
                raise ImpactMapError(f"deps.yaml: bad/duplicate rule id {rid!r}")
            seen_ids.add(rid)
            for n in r.get("impacts", []):
                if n not in self.nodes:
                    raise ImpactMapError(f"rule {rid}: unknown impact node {n!r}")
            m = r.get("match", {})
            self.rules.append(Rule(
                rid=rid, feed=r.get("feed", "*"),
                any_substrings=m.get("any", []),
                kinds=m.get("kinds", []),
                severity=r["severity"], impacts=r["impacts"],
                action=r["action"], authority=r["authority"],
                rationale=r.get("rationale", "").strip()))
        self.rule_index = {r.rid: r for r in self.rules}

    @classmethod
    def load(cls, path: Path) -> "ImpactMap":
        return cls(yaml.safe_load(Path(path).read_text()))

    def downstream(self, node: str) -> set[str]:
        seen, stack = set(), [node]
        while stack:
            n = stack.pop()
            for child in self.edges.get(n, []):
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        return seen

    def match(self, ev: Event) -> list[Rule]:
        hits = []
        for r in self.rules:
            if r.feed not in ("*", ev.feed):
                continue
            if r.kinds and ev.kind not in r.kinds:
                continue
            if r.any_substrings and not any(
                    s.lower() in ev.summary.lower() for s in r.any_substrings):
                continue
            hits.append(r)
        return hits

    def assess(self, ev: Event, rule: Rule) -> Assessment:
        # impacts expand to full downstream closure; keep original list first
        closure = set(rule.impacts)
        for n in rule.impacts:
            closure |= self.downstream(n)
        return Assessment(
            rule_id=rule.rid,
            severity=rule.severity,
            impacts=sorted(closure),
            action=rule.action,
            authority=rule.authority,
            rationale=rule.rationale,
            evidence=list(ev.refs),
            event_kind=ev.kind,
            event_summary=ev.summary,
            trigger_key=fp("proposal", rule.rid, ev.fingerprint))


def authority_note(action: str) -> str | None:
    """Why this action is human-only, if it is. Fail closed: unknown
    actions with no authority mapping escalate by default upstream."""
    return SAM_ONLY_CATEGORIES.get(action)
