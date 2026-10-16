"""The consumer loop — observation-only.

run_once(): poll every feed read-only, dedup, classify deterministically,
optionally enrich P0/ambiguous with the ops brain (advisory only), and
atomically journal + record proposals. Proposals are written to the
workspace outbox/escalations dirs with proposed:true headers — nothing is
ever delivered, no production path is touched.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .adapters import ADAPTERS
from .impact import ImpactMap
from .llm import OpsBrainClient
from .mailgen import build_escalation, build_message, write_proposal
from .schema import Assessment, Event, Proposal, StateMarker, utcnow
from .state import DirectorState

ENRICH_SEVERITIES = {"P0", "P1"}


@dataclass
class FeedConfig:
    args: dict = field(default_factory=dict)
    enabled: bool = True


@dataclass
class RunReport:
    events_seen: int = 0
    events_new: int = 0
    assessments: int = 0
    proposals_new: int = 0
    markers: int = 0
    llm: Optional[dict] = None
    started: float = field(default_factory=utcnow)
    finished: float = 0.0


class DirectorConsumer:
    def __init__(self, workspace: Path, deps_map: Path,
                 feeds: Optional[dict[str, FeedConfig]] = None,
                 llm: Optional[OpsBrainClient] = None,
                 mode: str = "observe"):
        if mode != "observe":
            raise ValueError("only observe mode is implemented (DIRECTOR-01)")
        self.mode = mode
        self.workspace = Path(workspace)
        self.state = DirectorState(self.workspace)
        self.map = ImpactMap.load(deps_map)
        self.feeds = feeds or {}
        self.llm = llm
        self.adapters = {}
        for name, cls in ADAPTERS.items():
            fc = self.feeds.get(name, FeedConfig())
            if fc.enabled:
                self.adapters[name] = cls(**fc.args)

    def _enrich(self, a: Assessment) -> Optional[dict]:
        if not self.llm or a.severity not in ENRICH_SEVERITIES:
            return None
        result = self.llm.assess(
            a.event_summary,
            f"rule={a.rule_id} severity={a.severity} "
            f"impacts={','.join(a.impacts)}")
        if result is None:
            return None
        if "unavailable" in result:
            self.state.journal_llm({"status": "unavailable",
                                    "reason": result["unavailable"]})
            return None
        self.state.journal_llm({"status": "ok", "source": result["source"]})
        # advisory only — verified stays false forever
        return {"source": result["source"], "text": result["text"],
                "verified": False, "advisory_only": True}

    def run_once(self) -> RunReport:
        report = RunReport()
        for feed, adapter in self.adapters.items():
            cursor = self.state.get_cursor(feed) or ""
            try:
                events, markers, new_cursor = adapter.poll(cursor)
            except Exception as e:  # noqa: BLE001 — fail to marker, keep looping
                markers = [StateMarker("feed_error", feed,
                                       f"{type(e).__name__}: {e}", utcnow())]
                events, new_cursor = [], cursor
            report.events_seen += len(events)
            report.markers += len(markers)

            fps = [e.fingerprint for e in events]
            already = self.state.seen(fps)
            fresh = [e for e in events if e.fingerprint not in already]
            report.events_new += len(fresh)

            assessments: list[Assessment] = []
            proposals: list[Proposal] = []
            for ev in fresh:
                for rule in self.map.match(ev):
                    a = self.map.assess(ev, rule)
                    a.model_assessment = self._enrich(a)
                    assessments.append(a)
                    msg = build_message(a)
                    esc = build_escalation(a)
                    for p in (msg, esc):
                        if p is not None:
                            proposals.append(p)
            report.assessments += len(assessments)

            new_props = self.state.ingest_batch(
                feed, new_cursor, fps, assessments, proposals, markers)
            report.proposals_new += len(new_props)
            for p in new_props:
                write_proposal(p, self.workspace)

        report.finished = utcnow()
        self.state.journal_run({
            "mode": self.mode,
            "map_version": self.map.version,
            "events_seen": report.events_seen,
            "events_new": report.events_new,
            "assessments": report.assessments,
            "proposals_new": report.proposals_new,
            "markers": report.markers,
            "elapsed_s": round(report.finished - report.started, 3)})
        return report

    def close(self) -> None:
        self.state.close()


def load_feeds(config_path: Path) -> dict[str, FeedConfig]:
    """feeds.yaml: {feed: {enabled: bool, args: {...}}}"""
    cfg = json.loads(Path(config_path).read_text()) \
        if config_path.suffix == ".json" else __import__("yaml").safe_load(
            Path(config_path).read_text())
    out = {}
    for name, spec in (cfg or {}).items():
        out[name] = FeedConfig(args=spec.get("args", {}),
                               enabled=spec.get("enabled", True))
    return out
