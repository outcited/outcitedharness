"""Core value types. Every assessment carries verbatim source references."""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


def utcnow() -> float:
    return time.time()


def fp(*parts: Any) -> str:
    """Stable fingerprint for dedup (sha256 of canonical json)."""
    blob = json.dumps(list(parts), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class SourceRef:
    """Preserves the original identifier and timestamp, verbatim."""

    feed: str                    # fleet_changes | sentinel | factory_events | facets | budget
    source_id: str               # e.g. factory event_id, CHANGES section header, file:line
    original_ts: Optional[str]   # source-carried timestamp when present
    observed_ts: float
    locator: str = ""            # extra locator (file path+line, uri, table rowid)


@dataclass
class Event:
    feed: str
    kind: str                    # adapter-specific event kind
    summary: str                 # one-line human summary, verbatim-ish from source
    refs: list[SourceRef] = field(default_factory=list)
    attrs: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.fingerprint:
            self.fingerprint = fp(self.feed, self.kind, self.summary,
                                  *[r.source_id for r in self.refs])


@dataclass
class StateMarker:
    """Explicit stale/missing/error/contradiction representation."""

    kind: str                    # feed_stale | feed_missing | feed_error | contradiction
    feed: str
    detail: str
    observed_ts: float


@dataclass
class Assessment:
    """Deterministic classification of one event against the impact map."""

    rule_id: str
    severity: str                # P0..P3
    impacts: list[str]           # dependency-map node ids
    action: str                  # proposed action id (never executed)
    authority: str               # "sam" | "agent:<box>" | "none"
    rationale: str
    evidence: list[SourceRef]
    event_kind: str
    event_summary: str
    trigger_key: str             # dedup key for the derived proposal
    model_assessment: Optional[dict] = None  # NEVER treated as fact

    def to_json(self) -> dict:
        d = asdict(self)
        return d


@dataclass
class Proposal:
    """A proposed outbound artifact. In observe mode nothing is delivered."""

    ptype: str                   # message | escalation | none
    trigger_key: str             # unique per (rule, event) — replay-safe
    filename: str
    body: str
    authority: str


def event_from_row(feed: str, kind: str, summary: str, refs: list[SourceRef],
                   attrs: Optional[dict] = None) -> Event:
    return Event(feed=feed, kind=kind, summary=summary, refs=refs,
                 attrs=attrs or {})
