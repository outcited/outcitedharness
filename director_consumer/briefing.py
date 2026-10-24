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
        lc = m.get("lane_counts") or {}
        risk.append(f"- queue by lane (never summed): {lc}")
        risk.append(f"- consumption: {m.get('consumption_rate_docs_hr')} "
                    f"docs/hr · corpus file-counter growth: "
                    f"{m.get('acquisition_rate_docs_day')} docs/day "
                    f"(inventory growth, not admitted documents)")
        risk.append(f"- starvation basis: {m.get('starvation_basis')} · "
                    f"hours: {m.get('starvation_hours')} · "
                    f"junk dead jobs: {m.get('junk_dead_jobs')} · "
                    f"stalled leases: {m.get('stalled_leases')}")
        risk.append("- NOTE: an empty inspected queue is NOT proof that all "
                    "queues are empty, and a zero counter is NOT proof of an "
                    "admission failure or of starvation")
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

    # GitHub issue definitions are an OBSERVATION. In particular, a manual
    # GitHub reply can never be rendered as an authenticated AgentMail ACK.
    github_work = []
    gh_snapshot = ws / "github-workloop-latest.json"
    if gh_snapshot.exists():
        try:
            g = json.loads(gh_snapshot.read_text())
            age_s = max(0, time.time() - float(g.get("observed_at", 0)))
            github_work.append(
                f"- observed {age_s/60:.0f} min ago; project="
                f"{g.get('project_id', '?')}; "
                f"source errors={len(g.get('errors', []))}")
            for repo, info in sorted(g.get("repositories", {}).items()):
                github_work.append(
                    f"- {repo}: source={info.get('source_state', 'UNKNOWN')}; "
                    f"issues={info.get('issues_observed', 'UNKNOWN')}; "
                    f"new={info.get('new', 0)}; "
                    f"duplicates={info.get('duplicates', 0)}; "
                    f"untrusted={info.get('untrusted', 0)}")
            for task in g.get("tasks", [])[:12]:
                github_work.append(
                    f"- {task.get('task_id')}: definition="
                    f"{task.get('definition_state')} DB={task.get('task_state')} "
                    f"trusted-mail-ack="
                    f"{task.get('transport_ack', {}).get('state', 'UNKNOWN')}")
            if g.get("errors"):
                github_work.append(
                    "- SOURCE ERROR(s) — issue count UNKNOWN, no empty-queue "
                    "inference; see github-workloop-latest.json")
        except (ValueError, OSError, TypeError, AttributeError):
            github_work.append("- UNKNOWN: snapshot invalid/unreadable")
    else:
        github_work.append("- NOT CONFIGURED: no GitHub work-order polling snapshot")

    # coordination state (04): tasks / messages / incident lifecycles
    coord = []
    try:
        for st_, n in con.execute(
                "SELECT state, COUNT(*) FROM tasks GROUP BY state"):
            coord.append(f"- tasks [{st_}]: {n}")
        for st_, n in con.execute(
                "SELECT state, COUNT(*) FROM messages GROUP BY state"):
            coord.append(f"- messages [{st_}]: {n}")
        for cid, sev, lc in con.execute(
                "SELECT correlation_id, severity, lifecycle_state "
                "FROM incidents WHERE state='open' ORDER BY severity"):
            coord.append(f"- incident {cid} ({sev}) lifecycle={lc}")
    except sqlite3.Error:
        coord.append("- coordination tables unavailable")

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

## GitHub work-order ingestion (not delivery)
{chr(10).join(github_work)}

## Coordination state
{chr(10).join(coord) or "- nothing open"}

## Model usage
{chr(10).join(usage)}

## What should happen next
{chr(10).join(nxt) or "- no open proposals"}

(Observation mode: everything above is derived from journaled evidence with
source references in director.db. Nothing has been dispatched or delivered.)
"""
    out = bdir / f"BRIEFING-{ts}.md"
    out.write_text(body)
    # consolidated director briefing (all projects in this workspace)
    try:
        projs = con.execute("SELECT project_id, name, lifecycle FROM "
                            "project_registry").fetchall()
        finds = con.execute("SELECT project, rule, subject, severity FROM "
                            "reconciler_findings WHERE state='open' "
                            "ORDER BY severity").fetchall()
        rows = ["## Projects"]
        rows += [f"- {p[0]} ({p[2]}): {p[1]}" for p in projs] or ["- none"]
        rows.append("## Open findings (all projects)")
        rows += [f"- [{f[3]}] {f[0]}:{f[1]} — {f[2][:60]}" for f in finds] \
            or ["- none"]
        cons = bdir / f"BRIEFING-consolidated-{ts}.md"
        cons.write_text(
            f"# Director consolidated briefing — {ts}\n---\nproposed: true\n"
            f"mode: observe\ndelivered: false\n\n" + "\n".join(rows) +
            "\n\n(derived from director.db; nothing dispatched)\n")
    except Exception:  # noqa: BLE001
        pass
    return out
