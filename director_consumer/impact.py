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
    # 01A #5 structured matching (fleet deltas): service-token identity +
    # actual change type, not substring luck.
    change_kinds: list[str] = None                  # container_delta | probe_state
    service_tokens: list[str] = None                # exact service ids
    exclude_service_tokens: list[str] = None
    service_direction: str = None                   # removed | added | present
    # 01A incident collapse: one proposal per rule+box+window
    dedupe_window_h: float = 0.0

    def __post_init__(self) -> None:
        self.change_kinds = self.change_kinds or []
        self.service_tokens = self.service_tokens or []
        self.exclude_service_tokens = self.exclude_service_tokens or []


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
                rationale=r.get("rationale", "").strip(),
                change_kinds=m.get("change_kinds", []),
                service_tokens=m.get("service_tokens", []),
                exclude_service_tokens=m.get("exclude_service_tokens", []),
                service_direction=m.get("service_direction"),
                dedupe_window_h=float(r.get("dedupe_window_h", 0))))
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
            attrs = ev.attrs or {}
            # structured fleet-delta matching (01A #5)
            if r.change_kinds and attrs.get("change_kind") not in r.change_kinds:
                continue
            if r.service_tokens or r.exclude_service_tokens or r.service_direction:
                svcs = set(attrs.get("services", []))
                # probe nodes (e.g. spark_embedder) ARE the service identity
                if attrs.get("change_kind") == "probe_state" and attrs.get("box"):
                    svcs.add(attrs["box"])
                if r.exclude_service_tokens and (
                        svcs & set(r.exclude_service_tokens)):
                    continue
                if r.service_tokens:
                    present = set(r.service_tokens) & svcs
                    if not present:
                        continue
                    if r.service_direction:
                        token = sorted(present)[0]
                        frm = set(attrs.get("from", []))
                        to = set(attrs.get("to", []))
                        if r.service_direction == "removed" and not (
                                token in frm and token not in to):
                            continue
                        if r.service_direction == "added" and not (
                                token not in frm and token in to):
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
        # incident collapse: bucket the trigger key so repeated deltas for the
        # same rule+box inside the window produce ONE proposal
        tk_parts: list = ["proposal", rule.rid, ev.fingerprint]
        if rule.dedupe_window_h > 0:
            bucket = int(ev.refs[0].observed_ts //
                         (rule.dedupe_window_h * 3600)) if ev.refs else 0
            tk_parts = ["proposal", rule.rid,
                        ev.attrs.get("box") or ev.attrs.get("feed")
                        or ev.kind,
                        ",".join(sorted(ev.attrs.get("services", []))) or
                        ev.kind,
                        bucket]
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
            trigger_key=fp(*tk_parts))


def authority_note(action: str, auth_policy=None) -> str | None:
    """Why this action is human-only, if it is. Fail closed: unknown
    actions with no authority mapping escalate by default. With a project
    AuthorityPolicy (DIRECTOR-02 §2) the mapping is project data; the
    legacy constant remains as the engine default."""
    if auth_policy is not None:
        only, note = auth_policy.is_human_only(action)
        if only:
            return note
        return None
    return SAM_ONLY_CATEGORIES.get(action)
