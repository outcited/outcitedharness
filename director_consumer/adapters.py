"""Read-only adapters over the existing feeds.

Every adapter opens sources read-only (sqlite: mode=ro; files: plain reads)
and returns (events, markers, cursor_state). Cursors are opaque strings the
adapter understands; the state store persists them verbatim.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Optional

from .schema import Event, SourceRef, StateMarker, utcnow

SECTION_RE = re.compile(r"^## (.+)$")


def _ro(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro&immutable=0"
    con = sqlite3.connect(uri, uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def _mtime(p: Path) -> Optional[float]:
    try:
        return p.stat().st_mtime
    except OSError:
        return None


def _freshest_ts(paths: Iterable[Path]) -> Optional[float]:
    ts = [_mtime(p) for p in paths]
    ts = [t for t in ts if t is not None]
    return max(ts) if ts else None


def _freshness_marker(feed: str, paths: list[Path], max_age_s: float,
                      detail_prefix: str) -> tuple[Optional[StateMarker], Optional[float]]:
    """Fail closed: missing or stale source yields an explicit marker."""
    if not any(p.exists() for p in paths):
        return StateMarker("feed_missing", feed,
                           f"{detail_prefix}: no source at {paths}", utcnow()), None
    newest = _freshest_ts(paths)
    if newest is None:
        return StateMarker("feed_missing", feed, f"{detail_prefix}: stat failed", utcnow()), None
    age = utcnow() - newest
    if age > max_age_s:
        return StateMarker("feed_stale", feed,
                           f"{detail_prefix}: newest source {age/3600:.1f}h old "
                           f"(threshold {max_age_s/3600:.1f}h)", utcnow()), newest
    return None, newest


class FleetChangesAdapter:
    """Parses /Volumes/M5_4TB/fleet-state/CHANGES.md — appended sections.

    Cursor: 'offset:byte' into the file. Sections are the events; the
    section header timestamp (verbatim) is the original_ts.
    """

    feed = "fleet_changes"

    def __init__(self, path: Path, max_age_s: float = 7 * 86400):
        self.path = Path(path)
        self.max_age_s = max_age_s

    def poll(self, cursor: str) -> tuple[list[Event], list[StateMarker], str]:
        marker, _ = _freshness_marker(self.feed, [self.path], self.max_age_s,
                                      "fleet-state CHANGES.md")
        if marker:
            return [], [marker], cursor
        text = self.path.read_text(errors="replace")
        try:
            offset = int((cursor or "0").split(":", 1)[0])
        except ValueError:
            offset = 0
        if offset > len(text):
            offset = 0  # file truncated/replaced -> reread; dedup keeps us safe
        new_text = text[offset:]
        events: list[Event] = []
        pos = offset
        for section in re.split(r"(?m)^(?=## )", new_text):
            if not section.startswith("## "):
                continue
            header = section.splitlines()[0][3:].strip()
            body = "\n".join(section.splitlines()[1:]).strip()
            if not body:
                continue
            h = hashlib.sha256(section.encode()).hexdigest()[:16]
            ref = SourceRef(self.feed, f"changes#{header}#{h}",
                            header.split("—")[0].strip() or None, utcnow(),
                            locator=f"{self.path}#offset{pos}")
            # one event per bullet line (machine deltas), prose kept whole
            for line in body.splitlines():
                line = line.strip().lstrip("-").strip()
                if not line:
                    continue
                ev = Event(self.feed, "fleet_delta", line, [ref])
                ev.attrs["section_header"] = header
                events.append(ev)
                break  # section-level event; bullets folded into summary
            if not events or events[-1].refs[0] is not ref:
                ev = Event(self.feed, "fleet_delta", body[:200], [ref])
                ev.attrs["section_header"] = header
                events.append(ev)
            pos += len(section)
        return events, [], f"{len(text)}:{hashlib.sha256(text.encode()).hexdigest()[:8]}"


class SentinelAdapter:
    """ALERTS.md sections + last-known-good.json. Meaning-health feed."""

    feed = "sentinel"

    def __init__(self, alerts: Path, lkg: Path, max_age_s: float = 36 * 3600):
        self.alerts, self.lkg = Path(alerts), Path(lkg)
        self.max_age_s = max_age_s

    def poll(self, cursor: str) -> tuple[list[Event], list[StateMarker], str]:
        marker, _ = _freshness_marker(self.feed, [self.alerts.parent / "x"],
                                      1e18, "")  # dir-level existence below
        if not self.alerts.exists():
            return [], [StateMarker("feed_missing", self.feed,
                                    f"ALERTS.md missing: {self.alerts}", utcnow())], cursor
        # freshness on the alerts file itself (absence of alerts is healthy;
        # only lkg staleness marks the feed stale)
        lkg_marker, _ = _freshness_marker(self.feed, [self.lkg], self.max_age_s,
                                          "sentinel last-known-good")
        markers = [lkg_marker] if lkg_marker else []
        text = self.alerts.read_text(errors="replace")
        sig = hashlib.sha256(text.encode()).hexdigest()
        if (cursor or "").endswith(sig):
            return [], markers, sig
        events = []
        for section in re.split(r"(?m)^(?=## )", text):
            if not section.startswith("## "):
                continue
            header = section.splitlines()[0][3:].strip()
            body = "\n".join(section.splitlines()[1:]).strip()
            if not body:
                body = header
            h = hashlib.sha256(section.encode()).hexdigest()[:16]
            ref = SourceRef(self.feed, f"alert#{header}#{h}",
                            header.split("—")[0].strip() or None, utcnow(),
                            locator=str(self.alerts))
            ev = Event(self.feed, "sentinel_alert", body[:200], [ref])
            ev.attrs["header"] = header
            events.append(ev)
        return events, markers, sig


class FactoryEventsAdapter:
    """Immutable Rust event ledger (ra03-state/factory.sqlite3)."""

    feed = "factory_events"

    def __init__(self, db: Path, max_age_s: float = 7 * 86400):
        self.db = Path(db)
        self.max_age_s = max_age_s

    def poll(self, cursor: str) -> tuple[list[Event], list[StateMarker], str]:
        if not self.db.exists():
            return [], [StateMarker("feed_missing", self.feed,
                                    f"factory.sqlite3 missing: {self.db}", utcnow())], cursor
        try:
            con = _ro(self.db)
        except sqlite3.Error as e:
            return [], [StateMarker("feed_error", self.feed, str(e), utcnow())], cursor
        try:
            last = int(cursor or 0)
            rows = con.execute(
                "SELECT rowid AS rid, event_id, event_type, subject_id, "
                "payload_json, created_at FROM factory_events WHERE rowid > ? "
                "ORDER BY rowid LIMIT 500", (last,)).fetchall()
            newest_db = con.execute(
                "SELECT MAX(rowid) FROM factory_events").fetchone()[0] or last
        finally:
            con.close()
        marker, _ = _freshness_marker(self.feed, [self.db], self.max_age_s,
                                      "factory_events")
        markers = [marker] if marker else []
        events = []
        for r in rows:
            ref = SourceRef(self.feed, r["event_id"], r["created_at"], utcnow(),
                            locator=f"{self.db}:factory_events:rowid{r['rid']}")
            ev = Event(self.feed, r["event_type"],
                       f"{r['event_type']} {r['subject_id']}", [ref],
                       attrs={"payload": r["payload_json"], "subject": r["subject_id"]})
            events.append(ev)
        return events, markers, str(newest_db)


class FacetLedgersAdapter:
    """Per-facet wave ledgers (results/mbp-worker-20261014). Emits progress,
    parked-growth and missing-growth events. Cursor: per-file line counts."""

    feed = "facets"

    def __init__(self, root: Path, max_age_s: float = 7 * 86400):
        self.root = Path(root)
        self.max_age_s = max_age_s

    def _counts(self) -> dict[str, int]:
        out = {}
        if not self.root.exists():
            return out
        for f in sorted(self.root.glob("done-*.txt")) + \
                 [self.root / "parked.txt", self.root / "missing-docs.log"]:
            if f.exists():
                out[f.name] = sum(1 for _ in open(f, errors="replace"))
        return out

    def poll(self, cursor: str) -> tuple[list[Event], list[StateMarker], str]:
        if not self.root.exists():
            return [], [StateMarker("feed_missing", self.feed,
                                    f"facet ledger dir missing: {self.root}", utcnow())], cursor
        ledgers = sorted(self.root.glob("done-*.txt")) + \
                  [self.root / "parked.txt", self.root / "missing-docs.log"]
        ledgers = [f for f in ledgers if f.exists()]
        if not ledgers:
            return [], [StateMarker("feed_missing", self.feed,
                                    f"no ledgers under {self.root}", utcnow())], cursor
        marker, _ = _freshness_marker(self.feed, ledgers, self.max_age_s,
                                      "facet ledgers")
        markers = [marker] if marker else []
        try:
            prev = json.loads(cursor or "{}")
        except json.JSONDecodeError:
            prev = {}
        cur = self._counts()
        events = []
        for name, n in cur.items():
            p = prev.get(name, n)  # first sight is not a delta
            if n > p:
                kind = ("facet_parked_growth" if name == "parked.txt"
                        else "facet_missing_growth" if name == "missing-docs.log"
                        else "facet_progress")
                ref = SourceRef(self.feed, f"{name}#{n}", None, utcnow(),
                                locator=str(self.root / name))
                ev = Event(self.feed, kind,
                           f"{name}: {p} -> {n} lines", [ref],
                           attrs={"file": name, "from": p, "to": n})
                events.append(ev)
        return events, markers, json.dumps(cur, sort_keys=True)


class BudgetSpendAdapter:
    """budget_spend (day, tier, jobs) from pipeline-live.db — read-only."""

    feed = "budget"

    def __init__(self, db: Path, daily_job_ceiling: int = 5000):
        self.db = Path(db)
        self.ceiling = daily_job_ceiling

    def poll(self, cursor: str) -> tuple[list[Event], list[StateMarker], str]:
        if not self.db.exists():
            return [], [StateMarker("feed_missing", self.feed,
                                    f"pipeline-live.db missing: {self.db}", utcnow())], cursor
        try:
            con = _ro(self.db)
            try:
                rows = con.execute(
                    "SELECT day, tier, jobs FROM budget_spend "
                    "ORDER BY day DESC LIMIT 50").fetchall()
            finally:
                con.close()
        except sqlite3.Error as e:
            return [], [StateMarker("feed_error", self.feed, str(e), utcnow())], cursor
        sig = hashlib.sha256(json.dumps(
            [tuple(r) for r in rows], default=str).encode()).hexdigest()
        if cursor == sig:
            return [], [], sig
        events = []
        for day, tier, jobs in rows:
            if jobs > self.ceiling:
                ref = SourceRef(self.feed, f"budget/{day}/{tier}", day, utcnow(),
                                locator=f"{self.db}:budget_spend")
                events.append(Event(self.feed, "budget_anomaly",
                                    f"spend {jobs} jobs on {day}/{tier} "
                                    f"> ceiling {self.ceiling}", [ref],
                                    attrs={"day": day, "tier": tier, "jobs": jobs}))
        sig_state = sig
        return events, [], sig_state


ADAPTERS = {
    "fleet_changes": FleetChangesAdapter,
    "sentinel": SentinelAdapter,
    "factory_events": FactoryEventsAdapter,
    "facets": FacetLedgersAdapter,
    "budget": BudgetSpendAdapter,
}
