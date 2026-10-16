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
from .mailgen import build_escalation, build_message, write_proposal_atomic
from .schema import Assessment, Event, Proposal, SourceRef, StateMarker, utcnow
from .state import DirectorState

ENRICH_SEVERITIES = {"P0", "P1"}
DEFAULT_BACKFILL_MAX_AGE_H = 24.0


@dataclass
class FeedConfig:
    args: dict = field(default_factory=dict)
    enabled: bool = True


@dataclass
class RunReport:
    events_seen: int = 0
    events_new: int = 0
    assessments: int = 0
    historical_assessments: int = 0
    proposals_new: int = 0
    suppressed_backfill: int = 0
    healed_writes: int = 0
    markers: int = 0
    llm: Optional[dict] = None
    started: float = field(default_factory=utcnow)
    finished: float = 0.0


class DirectorConsumer:
    def __init__(self, workspace: Path, deps_map: Path,
                 feeds: Optional[dict[str, FeedConfig]] = None,
                 llm: Optional[OpsBrainClient] = None,
                 mode: str = "observe",
                 backfill_max_age_h: float = DEFAULT_BACKFILL_MAX_AGE_H):
        if mode != "observe":
            raise ValueError("only observe mode is implemented (DIRECTOR-01)")
        self.mode = mode
        self.backfill_max_age_s = backfill_max_age_h * 3600.0
        self.workspace = Path(workspace)
        self.state = DirectorState(self.workspace)
        self.map = ImpactMap.load(deps_map)
        self.feeds = feeds or {}
        self.llm = llm
        self.adapters = {}
        for name, fc in self.feeds.items():
            if fc.enabled and name in ADAPTERS:
                self.adapters[name] = ADAPTERS[name](**fc.args)

    def _is_historical(self, ev: Event) -> bool:
        """01A #7: old events are journaled but never presented as fresh
        incidents. Uses the source-carried timestamp when present; events
        without one are treated as fresh (never suppressed silently)."""
        if not ev.refs or not ev.refs[0].original_ts:
            return False
        try:
            from datetime import datetime, timezone
            ts = datetime.fromisoformat(
                ev.refs[0].original_ts.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return (utcnow() - ts.timestamp()) > self.backfill_max_age_s
        except ValueError:
            return False

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

            # 01A #8: stale/missing feeds are not just markers — a stale
            # WATCHER feed becomes a classified event (watcher-health rule).
            watcher_events = []
            for m in markers:
                if m.kind in ("feed_stale", "feed_missing", "feed_error"):
                    ref = SourceRef("director", f"{m.kind}:{m.feed}", None,
                                    m.observed_ts, locator="")
                    watcher_events.append(Event(
                        "director", "watcher_stale" if m.feed == "sentinel"
                        else f"source_{m.kind.split('_', 1)[1]}",
                        f"{m.feed}: {m.detail}", [ref],
                        attrs={"feed": m.feed, "marker_kind": m.kind}))
            fresh = watcher_events + fresh

            assessments: list[Assessment] = []
            proposals: list[Proposal] = []
            for ev in fresh:
                historical = self._is_historical(ev)
                for rule in self.map.match(ev):
                    a = self.map.assess(ev, rule)
                    if historical:
                        # 01A #7: journal the assessment, suppress paging
                        a.rationale = (f"[historical — source ts older than "
                                       f"{self.backfill_max_age_s/3600:.0f}h; "
                                       f"no proposal] " + a.rationale)
                        assessments.append(a)
                        report.historical_assessments += 1
                        report.suppressed_backfill += 1
                        continue
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
        # transactional outbox: heal any proposal committed but unwritten
        report.healed_writes = self.state.flush_pending_writes(
            lambda row: write_proposal_atomic(
                Proposal(ptype=row["ptype"], trigger_key=row["trigger_key"],
                         filename=row["filename"], body=row["body"],
                         authority=row["authority"]), self.workspace))

        report.finished = utcnow()
        self.state.journal_run({
            "mode": self.mode,
            "map_version": self.map.version,
            "events_seen": report.events_seen,
            "events_new": report.events_new,
            "assessments": report.assessments,
            "historical_assessments": report.historical_assessments,
            "suppressed_backfill": report.suppressed_backfill,
            "proposals_new": report.proposals_new,
            "healed_writes": report.healed_writes,
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
