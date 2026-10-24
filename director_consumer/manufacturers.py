"""Manufacturer acquisition telemetry consumer (§5).

Consumes the producer-side JSONL contract (DIRECTOR-03 spec, written by
the mac-mini factory owner). NEVER fabricates manufacturer observations:
when the feed is absent, the gap is reported and the previously-proposed
handoff stands. Detection is deterministic:

  - repeated failures for a manufacturer/endpoint in a window
  - auth / rate-limit / bot-block outcome classes
  - endpoint_version change (behavior regression candidate)
  - delivered==0 while failures>0 (acquisition down)
Endpoint failures trigger INVESTIGATION with history, never blind retries.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .schema import Event, SourceRef, StateMarker, utcnow

FAIL_OUTCOMES = {"auth", "rate-limited", "bot-blocked", "timeout"}
REPEAT_FAIL_THRESHOLD = 3


@dataclass
class MfrRecord:
    manufacturer: str
    method: str
    endpoint: str
    endpoint_version: str
    ts: str
    outcome: str
    docs_delivered: int = 0
    docs_failed: int = 0
    retry_info: str = ""
    evidence: str = ""


@dataclass
class MfrHealth:
    by_manufacturer: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


class ManufacturerTelemetry:
    def __init__(self, telemetry_path: Optional[Path] = None):
        self.path = Path(telemetry_path) if telemetry_path else None

    def read(self) -> tuple[list[MfrRecord], list[StateMarker]]:
        if self.path is None or not self.path.exists():
            return [], [StateMarker(
                "feed_missing", "manufacturers",
                f"manufacturer telemetry unavailable ({self.path}) — "
                f"producer-side adapter spec + handoff proposed to mac-mini; "
                f"no manufacturer data fabricated", utcnow())]
        records, markers = [], []
        for i, line in enumerate(open(self.path, errors="replace")):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                records.append(MfrRecord(
                    manufacturer=r.get("manufacturer", "unknown"),
                    method=r.get("method", "unknown"),
                    endpoint=r.get("endpoint", "unknown"),
                    endpoint_version=str(r.get("endpoint_version", "")),
                    ts=str(r.get("ts", "")), outcome=str(r.get("outcome", "")),
                    docs_delivered=int(r.get("documents_delivered", 0) or 0),
                    docs_failed=int(r.get("documents_failed", 0) or 0),
                    retry_info=str(r.get("notes", ""))[:120],
                    evidence=f"{self.path}:{i+1}"))
            except (TypeError, ValueError):
                continue
        return records, markers

    def assess(self, records: list[MfrRecord]) -> MfrHealth:
        h = MfrHealth()
        if not records:
            h.notes.append("manufacturer health UNKNOWN: no telemetry rows")
            return h
        for r in records:
            d = h.by_manufacturer.setdefault(r.manufacturer, {
                "attempts": 0, "failures": 0, "delivered": 0,
                "outcomes": {}, "endpoints": {}, "versions": set()})
            d["attempts"] += 1
            d["delivered"] += r.docs_delivered
            d["outcomes"][r.outcome] = d["outcomes"].get(r.outcome, 0) + 1
            if r.outcome in FAIL_OUTCOMES or r.outcome.startswith("http_"):
                d["failures"] += 1
            d["endpoints"][r.endpoint] = r.endpoint_version
            if r.endpoint_version:
                d["versions"].add(r.endpoint_version)
        for m, d in h.by_manufacturer.items():
            d["versions"] = sorted(d["versions"])
            if d["failures"] >= REPEAT_FAIL_THRESHOLD and d["delivered"] == 0:
                h.notes.append(
                    f"{m}: {d['failures']} failures / 0 delivered — "
                    f"endpoint regression suspected (verify, do not assume)")
            if len(d["versions"]) > 1:
                h.notes.append(
                    f"{m}: endpoint_version changed {d['versions']} — "
                    f"behavior regression candidate (needs verification)")
        return h

    def events(self, records: list[MfrRecord], h: MfrHealth) -> list[Event]:
        out = []
        for m, d in h.by_manufacturer.items():
            if d["failures"] >= REPEAT_FAIL_THRESHOLD and d["delivered"] == 0:
                ref = SourceRef("manufacturers", f"mfr:{m}", None, utcnow(),
                                locator=str(self.path))
                out.append(Event(
                    "manufacturers", "mfr_endpoint_failure",
                    f"{m}: {d['failures']} acquisition failures "
                    f"(0 delivered) outcomes={d['outcomes']} — investigate "
                    f"against reconnaissance history, no blind retry",
                    [ref], attrs={"feed": "manufacturers", "services": [],
                                  "manufacturer": m}))
        return out
