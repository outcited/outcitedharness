"""Operational briefing (§10.10) — what changed, what is at risk, what next.

Derived exclusively from the director journal, fleet registry cache, and
continuity measurements. Written to <workspace>/briefings/ each run. Never
delivered; proposed-artifact convention applies.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path


def write_briefing(consumer) -> Path:
    ws = consumer.workspace
    bdir = ws / "briefings"
    bdir.mkdir(exist_ok=True)
    con = consumer.state.con

    # what changed: recent assessments, newest first
    rows = con.execute(
        "SELECT ts, payload FROM journal WHERE kind='assessment' "
        "ORDER BY id DESC LIMIT 25").fetchall()
    changed = []
    for ts, payload in rows:
        a = json.loads(payload)
        changed.append(f"- [{a.get('severity','?')}] {a.get('rule_id','?')} · "
                       f"{a.get('event_summary','')[:110]} · "
                       f"action={a.get('action','?')} · "
                       f"authority={a.get('authority','?')}")

    # what is at risk: continuity + P0/P1
    risk = []
    meas_file = ws / "continuity-latest.json"
    if meas_file.exists():
        m = json.loads(meas_file.read_text())
        risk.append(f"- ready backlog: {m.get('ready_backlog_docs')} docs "
                    f"(coverage target {m.get('coverage_target_h')}h)")
        risk.append(f"- consumption: {m.get('consumption_rate_docs_hr')} "
                    f"docs/hr · acquisition: "
                    f"{m.get('acquisition_rate_docs_day')} docs/day")
        risk.append(f"- starvation: {m.get('starvation_hours')}h · "
                    f"junk dead jobs: {m.get('junk_dead_jobs')} · "
                    f"stalled leases: {m.get('stalled_leases')}")
        for n in m.get("notes", []):
            risk.append(f"- UNKNOWN: {n}")
    p01 = con.execute(
        "SELECT payload FROM journal WHERE kind='assessment' "
        "ORDER BY id DESC LIMIT 50").fetchall()
    for (payload,) in p01:
        a = json.loads(payload)
        if a.get("severity") in ("P0", "P1"):
            risk.append(f"- {a.get('severity')} {a.get('rule_id')}: "
                        f"{a.get('event_summary','')[:100]}")

    # next actions: unprocessed proposals awaiting human/agent handling
    props = con.execute(
        "SELECT ptype, filename, authority FROM proposals "
        "ORDER BY created_at DESC LIMIT 15").fetchall()
    nxt = [f"- {ptype} → {authority}: {fname}" for ptype, fname, authority
           in props]

    # source health
    markers = con.execute(
        "SELECT payload FROM journal WHERE kind='marker' "
        "ORDER BY id DESC LIMIT 8").fetchall()
    health = []
    for (payload,) in markers:
        m = json.loads(payload)
        health.append(f"- {m.get('kind')} {m.get('feed')}: "
                      f"{str(m.get('detail',''))[:90]}")

    # model usage summary
    try:
        u = con.execute(
            "SELECT endpoint, COUNT(*), SUM(ok) FROM model_usage "
            "GROUP BY endpoint").fetchall()
        usage = [f"- {ep}: {n} calls, {ok or 0} ok" for ep, n, ok in u] or \
            ["- no model calls this session (deterministic-only)"]
    except sqlite3.Error:
        usage = ["- usage table unavailable"]

    fleet_file = ws / "fleet-state.json"
    fleet_lines = []
    if fleet_file.exists():
        fs = json.loads(fleet_file.read_text())
        for mid, h in sorted(fs.items()):
            fleet_lines.append(
                f"- {mid}: connectivity={h.get('connectivity')} "
                f"verdict={h.get('verdict')} roles={','.join(h.get('roles', []))}"
                + (" [MIGRATING]" if h.get("status") == "migrating" else ""))

    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    body = f"""# Engineering Director briefing — {ts}
---
proposed: true
mode: observe
delivered: false
project: {getattr(consumer, 'project_name', 'default')}
map_version: {consumer.map.version}
---

## What changed
{chr(10).join(changed) or "- nothing classified this window"}

## What is at risk
{chr(10).join(risk) or "- no known risks in current measurements"}

## Fleet
{chr(10).join(fleet_lines) or "- fleet not observed this session"}

## Source health
{chr(10).join(health) or "- all feeds fresh"}

## Model usage
{chr(10).join(usage)}

## What should happen next
{chr(10).join(nxt) or "- no open proposals"}

(Observation mode: everything above is derived from journaled evidence with
source references in director.db. Nothing has been dispatched or delivered.)
"""
    out = bdir / f"BRIEFING-{ts}.md"
    out.write_text(body)
    return out
