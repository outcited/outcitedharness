"""Progressive engineering discovery session state (PRD-DISCOVERY-01 R2).

Deterministic, serializable, reproducible: the same state + the same
releases produce equivalent results. Every narrowing step records cohort
before/after, eliminated candidates WITH reasons, unknowns kept visible,
the facet and range chosen, evidence coverage, and the next recommended
question.

Unknown is never collapsed into unsuitable: the step record separates
eliminated (hard-requirement failed, with rule + rated + required) from
unplaced (value unknown) from service failures.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

SESSION_SCHEMA = "harness.discovery01-session.v1"


@dataclass
class SessionState:
    category: str
    subcategory: str | None = None
    discovery_grain: str = "opn"
    constraints: dict[str, Any] = field(default_factory=dict)
    canonical_units: dict[str, str] = field(default_factory=dict)
    releases: dict[str, str] = field(default_factory=dict)
    steps: list[dict] = field(default_factory=list)
    cohort_count: int | None = None
    unknown_counts: dict[str, int] = field(default_factory=dict)
    evidence_coverage: dict[str, float] = field(default_factory=dict)
    recommended_next: str | None = None

    def state_id(self) -> str:
        canonical = json.dumps(self.to_dict(), sort_keys=True,
                               default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "schema": SESSION_SCHEMA,
            "category": self.category,
            "subcategory": self.subcategory,
            "discovery_grain": self.discovery_grain,
            "constraints": self.constraints,
            "canonical_units": self.canonical_units,
            "releases": self.releases,
            "cohort_count": self.cohort_count,
            "unknown_counts": self.unknown_counts,
            "evidence_coverage": self.evidence_coverage,
            "recommended_next": self.recommended_next,
            "steps": self.steps,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SessionState":
        if d.get("schema") != SESSION_SCHEMA:
            raise ValueError(
                f"session contract {d.get('schema')!r} != {SESSION_SCHEMA}")
        known = {f for f in cls.__dataclass_fields__}  # noqa: C416
        return cls(**{k: v for k, v in d.items() if k in known})

    def record_step(self, *, facet: str, value: Any, cohort_before: int,
                    cohort_after: int, eliminated: list[dict],
                    unplaced_unknown: dict[str, list[str]],
                    evidence_coverage: dict[str, float],
                    recommended_next: str | None) -> None:
        self.steps.append({
            "facet": facet, "value": value,
            "cohort_before": cohort_before, "cohort_after": cohort_after,
            "eliminated": eliminated,
            "unknown_unplaced": {k: len(v) for k, v in
                                 unplaced_unknown.items()},
            "evidence_coverage": evidence_coverage,
            "recommended_next": recommended_next,
        })
        self.cohort_count = cohort_after
        self.recommended_next = recommended_next
