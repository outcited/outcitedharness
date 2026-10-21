"""Cross-repo product graph, contract-conflict detection, and critical-path
ranking — DW-SEVEN-PRODUCT-BOARD-01 (issue #7, 2026-10-20 amendments).

Seven is the dependency-resolution coordinator for DesignWins. This module
builds a read-only product dependency DAG (acquisition -> FE experience),
detects ONE cause behind many symptoms (contract conflicts, blocked edges,
stale sources), ranks blockers by product impact, and persists why.

Laws (from the #7 PRD, enforced here):
  * Truth classes are separate facts, never promoted: a GitHub comment is
    GITHUB_OBSERVED evidence and can NEVER become TRUSTED_MAILBOX_ACK.
  * Missing data is UNKNOWN, never green. Stale evidence degrades to
    UNKNOWN on reconcile.
  * One cause fingerprint = one conflict record. Symptoms multiply, causes
    do not. Restart preserves records without duplicates.
  * Every conflict is PROPOSED; phases advance only by recorded evidence
    (owner-review -> agreed-contract -> tests -> independently-verified).
    The Director cannot auto-advance a phase.
  * Ranking is deterministic: same inputs -> same order (tiebreak by id).
  * Nothing here writes to production, dispatches messages, or assigns
    owners. Proposed coordination actions are data, not sends.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

UNKNOWN = "unknown"

# Truth classes for evidence. Ordered by evidentiary weight for CONFIDENCE
# scoring only — a lower class is never "promoted" by repetition or by a
# higher-class fact about a DIFFERENT question.
TRUTH_UNKNOWN = "UNKNOWN"
TRUTH_GITHUB_OBSERVED = "GITHUB_OBSERVED"
TRUTH_HOST_REPORTED = "HOST_REPORTED"
TRUTH_TRUSTED_MAILBOX_ACK = "TRUSTED_MAILBOX_ACK"
TRUTH_INDEPENDENTLY_TESTED = "INDEPENDENTLY_TESTED"

TRUTH_WEIGHT = {
    TRUTH_UNKNOWN: 0.0,
    TRUTH_GITHUB_OBSERVED: 0.4,
    TRUTH_HOST_REPORTED: 0.7,
    TRUTH_TRUSTED_MAILBOX_ACK: 1.0,
    TRUTH_INDEPENDENTLY_TESTED: 1.0,
}

# Conflict lifecycle phases. The Director proposes; only recorded evidence
# from the named owners advances a phase. No auto-advance.
PHASES = ("proposed", "owner-review", "agreed-contract", "tests",
          "independently-verified")

# Product pipeline stages (stage_id -> display name). The DAG edges below
# define downstream impact for ranking: a blocker on an upstream stage
# blocks every stage reachable through edges.
STAGES = (
    "acquisition",          # PDF factory: crawl -> content-addressed corpus
    "receiver-manifests",   # SHA-verified receiver handoff
    "m5-admission",         # L0 admission into extraction queues
    "extraction",           # claims + quotes from PDFs (M5/Ryzen fleet)
    "verification",         # quote/page/SHA verification + adjudication
    "normalization",        # comparability law: units/conditions/typed axes
                            # — prerequisite for ANY cross-manufacturer
                            # comparison (operator truth 2026-10-20:
                            # no normalization, no comparison)
    "identity-release",     # OPN identity joins + release export/pinning
    "store-api",            # release-pinned store/facade/decision API (cr-core)
    "evidence-wire",        # evidence DTO + document resolver at handoff
    "fe-journeys",          # M4 engineer journeys + click-to-source
    "product-test",         # Sam's private two-scenario test drive
)

EDGES = (
    ("acquisition", "receiver-manifests"),
    ("receiver-manifests", "m5-admission"),
    ("m5-admission", "extraction"),
    ("extraction", "verification"),
    ("verification", "normalization"),
    ("normalization", "identity-release"),
    ("identity-release", "store-api"),
    ("store-api", "evidence-wire"),
    ("evidence-wire", "fe-journeys"),
    ("fe-journeys", "product-test"),
)

_DOWNSTREAM: dict[str, list[str]] = {}
for _a, _b in EDGES:
    _DOWNSTREAM.setdefault(_a, []).append(_b)


def downstream_of(stage: str) -> set[str]:
    """All stages reachable from `stage` (transitive)."""
    seen: set[str] = set()
    stack = list(_DOWNSTREAM.get(stage, []))
    while stack:
        s = stack.pop()
        if s in seen:
            continue
        seen.add(s)
        stack.extend(_DOWNSTREAM.get(s, []))
    return seen


# ---------------------------------------------------------------- evidence

@dataclass
class Evidence:
    locator: str                    # exact source: file:line, issue#comment, url
    truth_class: str = TRUTH_UNKNOWN
    as_of: str = ""                 # ISO timestamp of the SOURCE, not ingest
    detail: str = ""

    def to_dict(self) -> dict:
        return {"locator": self.locator, "truth_class": self.truth_class,
                "as_of": self.as_of, "detail": self.detail}


# ---------------------------------------------------------------- conflicts

@dataclass
class ContractAssumption:
    """One side's asserted interface contract, pinned to a real source."""
    lane: str                       # m5 | cr-core | m4 | pdf-factory | seven
    endpoint_kind: str              # e.g. "decision-api" | "document-resolver"
    method: str = ""
    path: str = ""                  # asserted path or path composition
    envelope: str = ""
    release_pin: str = UNKNOWN
    reachability: str = UNKNOWN     # asserted reachability context, if any
    evidence: Evidence = field(default_factory=Evidence)

    def to_dict(self) -> dict:
        return {"lane": self.lane, "endpoint_kind": self.endpoint_kind,
                "method": self.method, "path": self.path,
                "envelope": self.envelope, "release_pin": self.release_pin,
                "reachability": self.reachability,
                "evidence": self.evidence.to_dict()}


def _norm_path(p: str) -> str:
    """Normalized comparable path: lowercase, strip protocol+host, trailing /."""
    p = p.strip().lower()
    if "://" in p:
        p = p.split("://", 1)[1]
        p = "/" + "/".join(p.split("/")[1:])
    while p.startswith("/"):
        p = p[1:]
    return p.rstrip("/") or "/"


def paths_compatible(consumer_path: str, producer_path: str) -> bool:
    """A consumer composition like '{base}/v1/engineering/decisions' is
    compatible with a producer route only when the producer path IS the
    consumer tail (base maps to ''). A producer path that merely CONTAINS
    the tail under an extra prefix (e.g. '/api/designwins/...') requires
    an AGREED, documented base mapping — absent that, the two sides hold
    incompatible path assumptions and that is a conflict, not a match."""
    c, p = _norm_path(consumer_path), _norm_path(producer_path)
    if c == p:
        return True
    if c.startswith("{base}/"):
        tail = c[len("{base}/"):]
        return p == tail
    return False


def cause_fingerprint(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


@dataclass
class ConflictRecord:
    """ONE cause behind N symptoms. Deduped by cause_fingerprint."""
    conflict_id: str
    title: str
    kind: str = "contract-conflict"     # contract | missing-implementation | ...
    cause_fingerprint: str = ""
    sides: list[dict] = field(default_factory=list)   # ContractAssumption dicts
    affected: list[str] = field(default_factory=list)  # issues/PRs/lanes
    factual_gaps: list[str] = field(default_factory=list)
    proposed_resolution: str = ""
    phase: str = "proposed"
    phase_evidence: list[dict] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    stage: str = "store-api"            # where the cause lives in the DAG

    def to_dict(self) -> dict:
        return {"conflict_id": self.conflict_id, "title": self.title,
                "kind": self.kind,
                "cause_fingerprint": self.cause_fingerprint,
                "sides": self.sides, "affected": self.affected,
                "factual_gaps": self.factual_gaps,
                "proposed_resolution": self.proposed_resolution,
                "phase": self.phase, "phase_evidence": self.phase_evidence,
                "created_at": self.created_at, "updated_at": self.updated_at,
                "stage": self.stage}


def detect_contract_conflicts(
        assumptions: list[ContractAssumption],
        implemented_paths: Optional[list[str]] = None,
        now: float = None) -> list[ConflictRecord]:
    """Group assumptions by endpoint_kind; flag groups whose consumer and
    producer paths are incompatible, and endpoints whose producer path is
    asserted but not implemented. Returns ONE record per cause."""
    now = now or time.time()
    implemented = {_norm_path(p) for p in (implemented_paths or [])}
    records: list[ConflictRecord] = []

    by_kind: dict[str, list[ContractAssumption]] = {}
    for a in assumptions:
        by_kind.setdefault(a.endpoint_kind, []).append(a)

    for kind, sides in sorted(by_kind.items()):
        consumers = [a for a in sides if a.lane in ("m4", "m5")]
        producers = [a for a in sides if a.lane in ("cr-core", "m5")]
        for c in consumers:
            for p in producers:
                if c is p or not c.path or not p.path:
                    continue
                if not paths_compatible(c.path, p.path):
                    fp = cause_fingerprint("path-mismatch", kind,
                                           c.path, p.path)
                    records.append(ConflictRecord(
                        conflict_id=f"conflict:{fp}",
                        title=f"{kind}: consumer path {c.path!r} cannot "
                              f"reach producer path {p.path!r}",
                        kind="contract-conflict",
                        cause_fingerprint=fp,
                        sides=[c.to_dict(), p.to_dict()],
                        factual_gaps=[
                            "route-base mapping between "
                            "DECISION_API_URL and the facade prefix is "
                            "undocumented; no agreed version-pinned "
                            "contract exists"],
                        proposed_resolution=(
                            "owners (m4 + cr-core, m5 for evidence pins) "
                            "agree ONE version-pinned path contract incl. "
                            "base mapping, envelope, release pins; then "
                            "same-fixture contract tests on both sides"),
                        created_at=now, updated_at=now,
                        stage="evidence-wire"))
        # asserted-but-not-implemented producer endpoints
        for p in producers:
            if not p.path:
                continue
            norm = _norm_path(p.path)
            if p.path and "{base}" not in p.path and implemented and \
                    norm not in implemented:
                fp = cause_fingerprint("not-implemented", kind, p.path)
                records.append(ConflictRecord(
                    conflict_id=f"conflict:{fp}",
                    title=f"{kind}: asserted endpoint {p.path!r} not "
                          f"found in implemented routes",
                    kind="missing-implementation",
                    cause_fingerprint=fp,
                    sides=[p.to_dict()],
                    factual_gaps=[
                        "no implemented route matches the asserted path "
                        "(scanned producer repo route tree)"],
                    proposed_resolution=(
                        "producer lane implements the asserted endpoint or "
                        "corrects the asserted contract; scanner re-runs "
                        "on the producer repo tree each tick"),
                    created_at=now, updated_at=now,
                    stage="store-api"))
    # dedupe by fingerprint (one cause, one record)
    seen: dict[str, ConflictRecord] = {}
    for r in records:
        if r.cause_fingerprint not in seen:
            seen[r.cause_fingerprint] = r
    return list(seen.values())


# ---------------------------------------------------------------- blockers

SEVERITY_WEIGHT = {"P0": 1.0, "P1": 0.6, "P2": 0.3, UNKNOWN: 0.5}

# ranking weights: blocked downstream work, product impact, severity,
# age/staleness, confidence, safe actionability
W_DOWNSTREAM = 3.0
W_SEVERITY = 2.0
W_AGE = 1.0        # saturates at 7 days
W_CONFIDENCE = 1.5
W_ACTIONABLE = 0.5


@dataclass
class Blocker:
    blocker_id: str
    title: str
    stage: str
    severity: str = UNKNOWN
    symptoms: list[str] = field(default_factory=list)
    affected_agents: list[str] = field(default_factory=list)
    confidence: float = 0.0          # truth-class weighted, 0..1
    age_h: float = 0.0
    safe_actionable: bool = False
    smallest_next_action: str = ""
    why_ranked: str = ""
    score: float = 0.0
    conflict: Optional[dict] = None  # linked ConflictRecord dict

    def compute_score(self) -> float:
        down = len(downstream_of(self.stage))
        conf = max(0.0, min(1.0, self.confidence))
        sev = SEVERITY_WEIGHT.get(self.severity, 0.5)
        age = min(self.age_h / 168.0, 1.0)
        self.score = round(
            W_DOWNSTREAM * down + W_SEVERITY * sev + W_AGE * age +
            W_CONFIDENCE * conf + W_ACTIONABLE * float(self.safe_actionable),
            3)
        self.why_ranked = (
            f"score={self.score} = downstream({down} stages)*{W_DOWNSTREAM} "
            f"+ severity({self.severity})*{W_SEVERITY} + "
            f"age({self.age_h:.0f}h)*{W_AGE} + "
            f"confidence({conf:.2f})*{W_CONFIDENCE} + "
            f"actionable({self.safe_actionable})*{W_ACTIONABLE}")
        return self.score


def rank_blockers(blockers: list[Blocker]) -> list[Blocker]:
    """Deterministic: score desc, then blocker_id asc."""
    for b in blockers:
        b.compute_score()
    return sorted(blockers, key=lambda b: (-b.score, b.blocker_id))


# ---------------------------------------------------------------- state

STATE_FILENAME = "product-graph.json"
FRESHNESS_SLA_H = 24.0


@dataclass
class ProductGraphState:
    as_of: float = 0.0
    conflicts: list[dict] = field(default_factory=list)
    blockers: list[dict] = field(default_factory=list)
    stages: dict = field(default_factory=dict)   # stage -> status dict
    version: int = 1

    def save(self, workspace: Path) -> None:
        workspace = Path(workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        tmp = workspace / (STATE_FILENAME + ".tmp")
        tmp.write_text(json.dumps(self.__dict__, indent=1, sort_keys=True))
        tmp.replace(workspace / STATE_FILENAME)

    @classmethod
    def load(cls, workspace: Path) -> "ProductGraphState":
        p = Path(workspace) / STATE_FILENAME
        if not p.exists():
            return cls()
        try:
            d = json.loads(p.read_text())
        except Exception:                                    # noqa: BLE001
            return cls()
        st = cls()
        for k in ("as_of", "conflicts", "blockers", "stages", "version"):
            if k in d:
                setattr(st, k, d[k])
        return st


def reconcile(prev: Optional[ProductGraphState],
              conflicts: list[ConflictRecord],
              now: float = None) -> ProductGraphState:
    """Merge new detections into prior state: same fingerprint keeps
    identity/created_at/phase (no duplicates, no phase regressions);
    changed sides/affected update in place. Stale prior records whose
    cause is no longer detected are dropped (cause resolved or source
    gone) — resolution itself must come from owner evidence, which is
    tracked as phase, so a resolved cause disappears from the board and
    that disappearance is itself observable in the state file diff."""
    now = now or time.time()
    state = ProductGraphState(as_of=now)
    prev_by_fp = {c["cause_fingerprint"]: c
                  for c in (prev.conflicts if prev else [])}
    for c in conflicts:
        old = prev_by_fp.get(c.cause_fingerprint)
        if old:
            c.created_at = old.get("created_at", now)
            c.phase = old.get("phase", "proposed")
            c.phase_evidence = old.get("phase_evidence", [])
            # affected/sides refresh from current evidence
        state.conflicts.append(c.to_dict())
    return state


def record_phase_evidence(state: ProductGraphState, conflict_id: str,
                          phase: str, evidence: Evidence,
                          now: float = None) -> bool:
    """Advance a conflict's phase ONLY with recorded evidence. GitHub
    comments (GITHUB_OBSERVED) can move 'proposed' -> 'owner-review' (the
    owner replied somewhere) but NEVER to agreed-contract or beyond, and
    NEVER create TRUSTED_MAILBOX_ACK. Trust-class promotion is structurally
    impossible here: acks arrive only via the trusted transport caller."""
    now = now or time.time()
    allowed = {"owner-review": {TRUTH_GITHUB_OBSERVED,
                                TRUTH_HOST_REPORTED,
                                TRUTH_TRUSTED_MAILBOX_ACK,
                                TRUTH_INDEPENDENTLY_TESTED},
               "agreed-contract": {TRUTH_TRUSTED_MAILBOX_ACK,
                                   TRUTH_INDEPENDENTLY_TESTED},
               "tests": {TRUTH_TRUSTED_MAILBOX_ACK,
                         TRUTH_INDEPENDENTLY_TESTED},
               "independently-verified": {TRUTH_INDEPENDENTLY_TESTED}}
    if phase not in allowed:
        return False
    for c in state.conflicts:
        if c["conflict_id"] != conflict_id:
            continue
        if evidence.truth_class not in allowed[phase]:
            return False
        order = list(PHASES)
        if order.index(phase) < order.index(c.get("phase", "proposed")):
            return False    # no regression
        c["phase"] = phase
        c["phase_evidence"] = c.get("phase_evidence", []) + [
            evidence.to_dict()]
        c["updated_at"] = now
        return True
    return False


def confidence_of(evidences: list[Evidence]) -> float:
    """Max truth weight across the evidence set; 0 when only UNKNOWN."""
    if not evidences:
        return 0.0
    return max(TRUTH_WEIGHT.get(e.truth_class, 0.0) for e in evidences)


def stale_to_unknown(evidence: list[dict], now: float,
                     sla_h: float = FRESHNESS_SLA_H) -> list[dict]:
    """Evidence older than the SLA degrades to UNKNOWN truth class —
    missing/stale is never green."""
    for e in evidence:
        try:
            as_of = float(e.get("as_of") or 0)
        except (TypeError, ValueError):
            as_of = 0.0
        if not as_of or (now - as_of) > sla_h * 3600:
            e["truth_class"] = TRUTH_UNKNOWN
    return evidence
