"""Read-only adapters over the existing feeds.

Every adapter opens sources read-only (sqlite: mode=ro; files: plain reads)
and returns (events, markers, cursor_state). Cursors are opaque strings the
adapter understands; the state store persists them verbatim.
"""
from __future__ import annotations

import codecs
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


DELTA_RE = re.compile(
    r"^(?P<box>[A-Za-z0-9_.-]+):\s+(?P<from>\[[^\]]*\]|None)\s*->\s*"
    r"(?P<to>\[[^\]]*\]|None)\s*$")
# probe states that mean "connectivity unknown", never a service verdict
PROBE_STATES = {"SSH_FAIL", "UNREACHABLE", "TIMEOUT"}


def parse_fleet_delta(line: str) -> Optional[dict]:
    """'- asus4: ['a'] -> ['b']' -> structured {box, from, to, services,
    change_kind}. Accepts raw bullet lines or bare deltas. from/to are
    service-lists or probe-state strings."""
    line = line.strip()
    if line.startswith("-"):
        line = line[1:].strip()
    m = DELTA_RE.match(line)
    if not m:
        return None
    def services(v: str) -> tuple[list[str], Optional[str]]:
        if v == "None":
            return [], None
        inner = v.strip("[]")
        items = [s.strip().strip("'\"") for s in inner.split(",") if s.strip()]
        state = items[0] if len(items) == 1 and items[0] in PROBE_STATES else None
        return items, state
    f_items, f_state = services(m.group("from"))
    t_items, t_state = services(m.group("to"))
    all_items = (set(f_items) | set(t_items)) - PROBE_STATES
    return {
        "box": m.group("box"),
        "from": f_items, "to": t_items,
        "from_state": f_state, "to_state": t_state,
        "services": sorted(all_items),
        "change_kind": ("probe_state" if (f_state or t_state)
                        else "container_delta"),
    }


def load_default_aliases() -> dict[str, str]:
    """machine-aliases.yaml packaged next to this module (01A #6)."""
    import yaml
    p = Path(__file__).with_name("machine-aliases.yaml")
    if p.exists():
        try:
            data = yaml.safe_load(p.read_text()) or {}
            return dict(data.get("aliases", {}))
        except Exception:  # noqa: BLE001 — registry is best-effort, identity
            return {}      # falls through to raw box names
    return {}


class FleetChangesAdapter:
    """Parses fleet-state CHANGES.md — appended sections.

    01B Task 1: BYTE-EXACT cursor. The file is read as bytes; the cursor is
    {"o": byte_offset, "h": sha256(consumed_prefix)}. Rules:

    - offsets advance only through fully-decoded, newline-terminated bytes;
      an incomplete trailing UTF-8 sequence or a partial final line is left
      UNCONSUMED and re-read next poll (incomplete records are preserved,
      never skipped, never decoded mid-character)
    - truncation (offset > filesize) and replacement (prefix hash mismatch)
      both reset to offset 0 and re-emit — replay idempotency is preserved
      by the consumer's fingerprint dedup, so a re-read never duplicates
    - every bullet of every complete section becomes an event; continuation
      bullets (appended without a new header) become events too
    """

    feed = "fleet_changes"

    def __init__(self, path: Path, max_age_s: float = 7 * 86400,
                 aliases: Optional[dict[str, str]] = None):
        self.path = Path(path)
        self.max_age_s = max_age_s
        self.aliases = aliases if aliases is not None else load_default_aliases()

    def _canon(self, box: str) -> str:
        return self.aliases.get(box, self.aliases.get(box.lower(), box))

    @staticmethod
    def _decode_cursor(cursor: str) -> tuple[int, str]:
        """Accepts the 01B json cursor and the legacy plain-offset form."""
        if not cursor:
            return 0, ""
        try:
            obj = json.loads(cursor)
            if isinstance(obj, dict):
                return int(obj.get("o", 0)), str(obj.get("h", ""))
        except (json.JSONDecodeError, ValueError):
            pass
        try:
            return int(str(cursor).split(":", 1)[0]), ""
        except ValueError:
            return 0, ""

    def poll(self, cursor: str) -> tuple[list[Event], list[StateMarker], str]:
        marker, _ = _freshness_marker(self.feed, [self.path], self.max_age_s,
                                      "fleet-state CHANGES.md")
        if marker:
            return [], [marker], cursor
        data = self.path.read_bytes()
        offset, prev_hash = self._decode_cursor(cursor)
        # truncation or replacement -> full reread (dedup keeps idempotency)
        if offset > len(data) or (offset > 0 and (
                len(data) < offset or
                hashlib.sha256(data[:offset]).hexdigest() != prev_hash)):
            offset, prev_hash = 0, ""
        chunk = data[offset:]
        # hold back an incomplete trailing UTF-8 sequence
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        dec.decode(chunk, final=False)
        usable = chunk[: len(chunk) - len(dec.buffer)]
        # hold back a partial (unterminated) final line
        if usable and not usable.endswith(b"\n"):
            nl = usable.rfind(b"\n")
            usable = usable[: nl + 1] if nl != -1 else b""
        if not usable:
            return [], [], cursor  # nothing complete yet; keep position
        text = usable.decode("utf-8", errors="replace")
        new_offset = offset + len(usable)

        events: list[Event] = []
        consumed_chars = 0
        for section in re.split(r"(?m)^(?=## )", text):
            if not section.startswith("## "):
                # continuation bytes of the section consumed at cursor time
                bullets = [l.lstrip("-").strip() for l in section.splitlines()
                           if l.strip().startswith("- ")]
                for line in bullets:
                    ref = SourceRef(
                        self.feed,
                        f"changes#continuation@{new_offset}",
                        None, utcnow(), locator=f"{self.path}@{new_offset}")
                    attrs: dict[str, Any] = {"section_header": "(continuation)"}
                    parsed = parse_fleet_delta(line)
                    if parsed:
                        parsed["box"] = self._canon(parsed["box"])
                        attrs.update(parsed)
                    events.append(Event(
                        self.feed,
                        parsed["change_kind"] if parsed else "fleet_note",
                        line, [ref], attrs=attrs))
                consumed_chars += len(section)
                continue
            header = section.splitlines()[0][3:].strip()
            body_lines = [l.strip() for l in section.splitlines()[1:]]
            header_ts = header.split("—")[0].strip() or None
            h = hashlib.sha256(section.encode("utf-8", "surrogatepass")).hexdigest()[:16]
            bullets = [l.lstrip("-").strip() for l in body_lines
                       if l.startswith("- ")]
            lines_as_events = bullets or ([" ".join(
                l for l in body_lines if l)[:200]] if any(body_lines) else [])
            for line in lines_as_events:
                ref = SourceRef(self.feed, f"changes#{header}#{h}",
                                header_ts, utcnow(),
                                locator=f"{self.path}@{new_offset}")
                attrs = {"section_header": header}
                parsed = parse_fleet_delta(line)
                if parsed:
                    parsed["box"] = self._canon(parsed["box"])
                    attrs.update(parsed)
                events.append(Event(
                    self.feed,
                    parsed["change_kind"] if parsed else "fleet_note",
                    line, [ref], attrs=attrs))
            consumed_chars += len(section)
        new_cursor = json.dumps({
            "o": new_offset,
            "h": hashlib.sha256(data[:new_offset]).hexdigest()
            if new_offset else ""}, sort_keys=True)
        return events, [], new_cursor


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
            # 01A #3: the cursor may only advance through rows actually
            # returned. With LIMIT 500 in effect, MAX(rowid) would silently
            # skip every event beyond the page — cursor = last row returned.
            try:
                last = int(cursor or 0)
            except ValueError:
                last = 0
            rows = con.execute(
                "SELECT rowid AS rid, event_id, event_type, subject_id, "
                "payload_json, created_at FROM factory_events WHERE rowid > ? "
                "ORDER BY rowid LIMIT 500", (last,)).fetchall()
            new_cursor = str(rows[-1]["rid"]) if rows else str(last)
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
        return events, markers, new_cursor


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
