"""NEO read-only supervision snapshot (Phase 2) + continuity rules (Phase 4).

One GET sweep over the supervisor API -> structured snapshot (JSON + MD).
No writes to Gas City, ever. NEO consumes the snapshot; it does not need
access to any assistant's private state.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from . import finding as F
from .gascity_client import GasCityClient, now_iso

STALE_EVENT_MIN = 30          # no city events for 30m => continuity alert
IDLE_READY_THRESHOLD = 5      # ready work with zero running agents => alert
PENDING_HUMAN_MIN = 60


def build_snapshot(client: GasCityClient, city: str) -> dict[str, Any]:
    snap: dict[str, Any] = {"schema": "dw.neo-status.v1", "city": city,
                            "captured_at": now_iso(), "source": "gas-city-supervisor"}
    snap["supervisor"] = client.health()
    st = client.city_status(city)
    snap["city"] = {
        "running": st.get("running"), "suspended": st.get("suspended"),
        "uptime_sec": st.get("uptime_sec"), "version": st.get("version"),
        "agents": st.get("agents"), "work": st.get("work"),
        "partial": st.get("partial"), "partial_errors": st.get("partial_errors"),
    }
    snap["agents"] = [
        {"name": a.get("qualified_name", a.get("name")), "running": a.get("running"),
         "suspended": a.get("suspended"), "scale": a.get("scale_label", "")}
        for a in (st.get("agent_details") or [])
    ]
    snap["sessions"] = [
        {"id": s.get("id", s.get("session_id")), "state": s.get("state", s.get("status"))}
        for s in (client.sessions(city).get("items") or [])
    ]
    snap["pending_interactions"] = client.pending(city).get("items") or []
    beads, index = client.beads(city, limit=200)
    snap["event_index"] = index
    items = beads.get("items") or []
    snap["beads_total_reported"] = beads.get("total")
    snap["findings"] = [b for b in items
                        if any(str(l).startswith(F.NEO_LABEL_PREFIX) for l in (b.get("labels") or []))]
    snap["blocked"] = [b.get("id") for b in items if _blocked(b)]
    snap["duplicates"] = _duplicates(items)
    ev = client.events(city, limit=20)
    seqs = [e.get("seq") for e in (ev.get("items") or []) if e.get("seq")]
    snap["last_event_seq"] = max(seqs) if seqs else None
    snap["recent_events"] = [
        {"seq": e.get("seq"), "type": e.get("type"), "subject": e.get("subject")}
        for e in (ev.get("items") or [])[-10:]
    ]
    return snap


def _blocked(b: dict) -> bool:
    return bool(b.get("blocked_by")) or "blocked" in (b.get("status") or "")


def _duplicates(items: list[dict]) -> list[dict]:
    """Same TASK_ID prefix in more than one bead => duplicate/superseded view."""
    counts: Counter[str] = Counter()
    ids: dict[str, list[str]] = {}
    for b in items:
        m = F.TASK_ID_RE.match((b.get("title") or "") + " —")
        if m:
            tid = m.group(1)
            counts[tid] += 1
            ids.setdefault(tid, []).append(b.get("id"))
    return [{"task_id": t, "beads": v} for t, v in ids.items() if counts[t] > 1]


# ---- Phase 4: continuity rules over a snapshot (pure, testable) --------

def evaluate(snap: dict[str, Any], now_epoch: int | None = None) -> list[dict]:
    alerts: list[dict] = []
    city = snap.get("city") or {}
    work = city.get("work") or {}
    agents = snap.get("agents") or []
    running = [a for a in agents if a.get("running")]
    if work.get("ready", 0) >= IDLE_READY_THRESHOLD and not running:
        alerts.append({"rule": "orchestrator-idle-with-ready-work",
                       "ready": work.get("ready"), "running_agents": 0})
    if not running and work.get("in_progress", 0) > 0:
        alerts.append({"rule": "in-progress-without-live-session",
                       "in_progress": work.get("in_progress")})
    if snap.get("pending_interactions"):
        alerts.append({"rule": "work-awaiting-human",
                       "count": len(snap["pending_interactions"])})
    if city.get("partial"):
        alerts.append({"rule": "city-status-partial", "errors": city.get("partial_errors")})
    if snap.get("duplicates"):
        alerts.append({"rule": "duplicate-findings", "items": snap["duplicates"]})
    for f in snap.get("findings") or []:
        if not _has_receipt(f):
            alerts.append({"rule": "finding-without-receipt",
                           "bead": f.get("id"), "title": (f.get("title") or "")[:60]})
    return alerts


def _has_receipt(bead: dict) -> bool:
    meta = bead.get("metadata") or {}
    return bool(meta.get("neo-accepted-receipt"))


def render_markdown(snap: dict, alerts: list[dict]) -> str:
    city = snap.get("city") or {}
    agents = snap.get("agents") or []
    agent_lines = [f"- {'RUN' if a['running'] else 'idle'} {a['name']} {a.get('scale') or ''}"
                   for a in agents] or ["- (none)"]
    alert_lines = [f"- **{a['rule']}** {json.dumps({k: v for k, v in a.items() if k != 'rule'})}"
                   for a in alerts] or ["- none"]
    lines = [
        f"# NEO status — {snap['city']} @ {snap['captured_at']}",
        f"- supervisor: {snap['supervisor'].get('status')} v{snap['supervisor'].get('version')} "
        f"uptime {snap['supervisor'].get('uptime_sec', 0) // 3600}h",
        f"- city running={city.get('running')} suspended={city.get('suspended')} "
        f"agents={city.get('agents')} work={city.get('work')}",
        f"- sessions: {len(snap.get('sessions') or [])} active; "
        f"pending human interactions: {len(snap.get('pending_interactions') or [])}",
        f"- NEO findings tracked: {len(snap.get('findings') or [])}; "
        f"duplicate groups: {len(snap.get('duplicates') or [])}",
        f"- event index: {snap.get('event_index')} last_seq={snap.get('last_event_seq')}",
        "",
        "## Agents",
        *agent_lines,
        "",
        "## Continuity alerts",
        *alert_lines,
    ]
    return "\n".join(lines) + "\n"


def snapshot_cli(city: str, base_url: str, out_dir: Path) -> dict:
    client = GasCityClient(base_url)
    snap = build_snapshot(client, city)
    alerts = evaluate(snap)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "neo-snapshot.json").write_text(json.dumps(snap, indent=2) + "\n")
    (out_dir / "neo-snapshot.md").write_text(render_markdown(snap, alerts))
    return {"snapshot": snap, "alerts": alerts}
