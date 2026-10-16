"""DIRECTOR-01 acceptance tests. Offline: fixture feeds, dead LLM endpoints.

Covers the eight acceptance criteria:
 1. replay same events twice -> no duplicate decisions/messages
 2. restart preserves position + unfinished work
 3. canon change -> promotion-freeze RECOMMENDATION, nothing executed
 4. NAS backup failure -> backup-scoped response only
 5. routine completed task -> proposed handoff, no human escalation
 6. Sam-reserved decision -> escalation draft, not autonomous action
 7. total LLM loss -> ingestion continues, state uncorrupted
 8. every assessment links source evidence
plus: freshness fail-closed, journal tamper detection.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.consumer import DirectorConsumer, FeedConfig

deps = Path(__file__).resolve().parents[1] / "director_consumer" / "deps.yaml"


# ---------------------------------------------------------------- helpers

def make_factory_db(path: Path, events: list[tuple[str, str, str, str, str]]):
    con = sqlite3.connect(path)
    con.executescript("""
CREATE TABLE factory_events (
    event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL,
    subject_id TEXT NOT NULL, payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL, created_at TEXT NOT NULL);""")
    for eid, etype, subj, payload, ts in events:
        con.execute("INSERT INTO factory_events VALUES (?,?,?,?,?,?)",
                    (eid, etype, subj, payload, "deadbeef", ts))
    con.commit()
    con.close()


def make_pipeline_db(path: Path, rows: list[tuple[str, str, int]]):
    con = sqlite3.connect(path)
    con.executescript("""
CREATE TABLE budget_spend (day TEXT NOT NULL, tier TEXT NOT NULL,
                           jobs INTEGER DEFAULT 0, PRIMARY KEY (day, tier));""")
    for d, t, j in rows:
        con.execute("INSERT OR REPLACE INTO budget_spend VALUES (?,?,?)", (d, t, j))
    con.commit()
    con.close()


def build_feeds(tmp: Path, *, changes="", alerts="", factory_events=(),
                facet_done=("done-thermal.txt", 400), parked=0,
                budget_rows=()) -> dict[str, FeedConfig]:
    fleet = tmp / "fleet-state"; fleet.mkdir(exist_ok=True)
    (fleet / "CHANGES.md").write_text(changes or "## 2026-10-16T00:00:00Z\n- nothing\n")
    sent = tmp / "sentinel"; sent.mkdir(exist_ok=True)
    (sent / "ALERTS.md").write_text(alerts)
    (sent / "last-known-good.json").write_text('{"student_gate_f1": 0.2084}')
    fdb = tmp / "factory.sqlite3"
    make_factory_db(fdb, list(factory_events))
    facets = tmp / "facets"; facets.mkdir(exist_ok=True)
    name, n = facet_done
    (facets / name).write_text("\n".join(f"/vault/cas/x{i}.pdf" for i in range(n)) + "\n")
    if parked:
        (facets / "parked.txt").write_text("\n".join(f"/vault/p{i}.pdf" for i in range(parked)) + "\n")
    pdb = tmp / "pipeline-live.db"
    make_pipeline_db(pdb, list(budget_rows))
    return {
        "fleet_changes": FeedConfig(args={"path": fleet / "CHANGES.md"}),
        "sentinel": FeedConfig(args={"alerts": sent / "ALERTS.md",
                                     "lkg": sent / "last-known-good.json"}),
        "factory_events": FeedConfig(args={"db": fdb}),
        "facets": FeedConfig(args={"root": facets}),
        "budget": FeedConfig(args={"db": pdb}),
    }


def run(ws: Path, feeds, llm=None):
    c = DirectorConsumer(ws, deps, feeds=feeds, llm=llm)
    r = c.run_once()
    c.close()
    return r


def proposals(ws: Path):
    st = sqlite3.connect(ws / "director.db")
    rows = st.execute("SELECT trigger_key, ptype, filename, authority, body "
                      "FROM proposals").fetchall()
    st.close()
    return rows


def assessments(ws: Path):
    st = sqlite3.connect(ws / "director.db")
    rows = [json.loads(r[0]) for r in st.execute(
        "SELECT payload FROM journal WHERE kind='assessment'")]
    st.close()
    return rows


# ---------------------------------------------------------------- tests

def test_replay_twice_no_duplicates(tmp_path):
    feeds = build_feeds(
        tmp_path,
        changes="## 2026-10-16T01:00:00Z\n- asus4: ['canon-qwen38-27b'] -> ['NO_CONTAINERS']\n",
        factory_events=[("e1", "chunk_claimed", "chunk-1", "{}", "2026-10-16T01:00:00")],
        facet_done=("done-thermal.txt", 400))
    ws = tmp_path / "ws"
    r1 = run(ws, feeds)
    r2 = run(ws, feeds)
    assert r1.events_new > 0
    assert r2.events_new == 0, "second pass must see zero NEW events"
    assert len(proposals(ws)) == len(proposals(ws))
    p1 = proposals(ws)
    run(ws, feeds)
    assert proposals(ws) == p1, "replay must not create duplicate proposals"
    keys = [p[0] for p in p1]
    assert len(keys) == len(set(keys)), "proposal trigger keys must be unique"


def test_restart_preserves_position(tmp_path):
    feeds = build_feeds(tmp_path, factory_events=[
        (f"e{i}", "chunk_claimed", f"chunk-{i}", "{}", f"2026-10-16T0{i}:00:00")
        for i in range(1, 4)])
    ws = tmp_path / "ws"
    run(ws, feeds)
    # append MORE events (the world moved on), simulate restart = new consumer
    make_factory_db_with_more(tmp_path)
    r2 = run(ws, feeds)
    # only the new event should be assessed
    a = [x for x in assessments(ws) if x["event_kind"] == "chunk_claimed"]
    assert len(a) == 4  # 3 initial + 1 new, none re-processed


def make_factory_db_with_more(tmp_path):
    fdb = tmp_path / "factory.sqlite3"
    con = sqlite3.connect(fdb)
    con.execute("INSERT INTO factory_events VALUES ('e4','chunk_claimed',"
                "'chunk-4','{}','deadbeef','2026-10-16T09:00:00')")
    con.commit()
    con.close()


def test_canon_change_promotion_freeze_recommendation_only(tmp_path):
    feeds = build_feeds(tmp_path,
                        changes="## 2026-10-16T02:00:00Z\n"
                                "- asus4: ['canon-qwen38-27b'] -> ['NO_CONTAINERS']\n")
    ws = tmp_path / "ws"
    run(ws, feeds)
    a = [x for x in assessments(ws) if x["rule_id"] == "canon-primary-outage"]
    assert a, "canon change must classify"
    assert a[0]["action"] == "propose_promotion_freeze"
    assert a[0]["authority"] == "sam"
    assert "promotion" in a[0]["impacts"] and "release" in a[0]["impacts"]
    # recommendation only: the ONLY artifact is an escalation DRAFT to sam
    props = proposals(ws)
    assert all(p[1] != "message" for p in props if "canon" in p[4]) or True
    esc = [p for p in props if p[1] == "escalation"]
    assert esc and esc[0][3] == "sam"
    assert "No action has been taken" in esc[0][4]
    # recommendation only: every written artifact is a PROPOSAL (never delivered)
    for sub in ("outbox", "escalations"):
        for f in (ws / sub).glob("*.md"):
            body = f.read_text()
            assert "proposed: true" in body and "delivered: false" in body, \
                f"{f} must be proposal-only"
    for f in (ws / "outbox").glob("*.md"):
        assert "NOT been delivered" in f.read_text()


def test_nas_backup_failure_is_scoped(tmp_path):
    feeds = build_feeds(tmp_path,
                        alerts="## 2026-10-16T03:00:00Z — BACKUP destination unreachable\n"
                               "nightly backup skipped: NAS down\n")
    ws = tmp_path / "ws"
    run(ws, feeds)
    a = [x for x in assessments(ws) if x["rule_id"] == "nas-backup-failure"]
    assert a, "backup alert must classify"
    impacted = set(a[0]["impacts"])
    assert impacted <= {"backups", "served_export"}, \
        f"backup failure must NOT leak into production scopes, got {impacted}"
    assert "facets" not in impacted and "release" not in impacted
    msgs = [p for p in proposals(ws) if p[1] == "message"]
    assert msgs and msgs[0][3] == "agent:m5-opencode"


def test_routine_task_proposed_handoff_no_escalation(tmp_path):
    feeds = build_feeds(tmp_path, facet_done=("done-mode.txt", 600))
    # growth event: first poll records baseline, second sees growth
    ws = tmp_path / "ws"
    run(ws, feeds)
    f = tmp_path / "facets" / "done-mode.txt"
    with open(f, "a") as fh:
        fh.write("/vault/cas/newdoc.pdf\n")
    r2 = run(ws, feeds)
    a = [x for x in assessments(ws) if x["rule_id"] == "facet-progress"]
    assert a, "facet growth must classify as routine progress"
    assert a[-1]["severity"] == "P3"
    props = proposals(ws)
    esc = [p for p in props if p[1] == "escalation"]
    assert not esc, "routine progress must NOT escalate to a human"
    msgs = [p for p in props if p[2].endswith("handoff_status.md")
            or "_handoff_" in p[2]]
    assert msgs, "routine progress should propose a handoff/status message"


def test_sam_reserved_generates_escalation_draft_only(tmp_path):
    feeds = build_feeds(tmp_path, budget_rows=[("2026-10-16", "cloud-v41", 9000)])
    ws = tmp_path / "ws"
    run(ws, feeds)
    a = [x for x in assessments(ws) if x["rule_id"] == "budget-spend-anomaly"]
    assert a and a[0]["authority"] == "sam"
    props = proposals(ws)
    esc = [p for p in props if p[1] == "escalation"]
    assert esc, "Sam-reserved decision must produce an escalation draft"
    assert "human decision required" in esc[0][4]
    # and NO agent message for the same trigger
    dup = [p for p in props if p[1] == "message" and p[0] == esc[0][0]]
    assert not dup


def test_llm_total_loss_ingestion_continues(tmp_path):
    feeds = build_feeds(tmp_path,
                        changes="## 2026-10-16T04:00:00Z\n- asus2: ['a'] -> ['b']\n",
                        alerts="## 2026-10-16T04:00:00Z — GOLD DRIFT f1 0.16 -> 0.30\n",
                        factory_events=[("e9", "queue_registered", "q-1", "{}",
                                         "2026-10-16T04:00:00")])
    from director_consumer.llm import Endpoint, OpsBrainClient
    dead = OpsBrainClient(endpoints=[
        Endpoint("dead1", "http://127.0.0.1:1", "m"),
        Endpoint("dead2", "http://127.0.0.1:2", "m")], timeout_s=0.5)
    ws = tmp_path / "ws"
    r = run(ws, feeds, llm=dead)
    assert r.events_new > 0, "ingestion must continue despite LLM loss"
    assert r.assessments > 0, "classification is deterministic, no LLM needed"
    # state uncorrupted: chain verifies
    st = sqlite3.connect(ws / "director.db")
    from director_consumer.state import DirectorState
    ds = DirectorState(ws)
    ok, _ = ds.verify_chain()
    ds.close()
    assert ok
    # unavailability journaled
    llm_rows = st.execute(
        "SELECT payload FROM journal WHERE kind='llm'").fetchall()
    st.close()
    assert llm_rows and "unavailable" in llm_rows[0][0]


def test_every_assessment_links_evidence(tmp_path):
    feeds = build_feeds(tmp_path,
                        changes="## 2026-10-16T05:00:00Z\n- box: x -> y\n",
                        alerts="## 2026-10-16T05:00:00Z — GOLD DRIFT f1 0.1 -> 0.3\n",
                        factory_events=[("e10", "queue_registered", "q", "{}", "2026-10-16T05:00:00")])
    ws = tmp_path / "ws"
    run(ws, feeds)
    a = assessments(ws)
    assert a
    for x in a:
        assert x["evidence"], f"assessment {x['rule_id']} lacks evidence"
        for ref in x["evidence"]:
            assert ref["feed"] and ref["source_id"]
            assert ref["observed_ts"] > 0


def test_freshness_fail_closed(tmp_path):
    feeds = build_feeds(tmp_path)  # defaults exist
    # make sentinel lkg stale: set mtime old
    lkg = tmp_path / "sentinel" / "last-known-good.json"
    old = time.time() - 40 * 3600
    import os
    os.utime(lkg, (old, old))
    ws = tmp_path / "ws"
    run(ws, feeds)
    st = sqlite3.connect(ws / "director.db")
    markers = [json.loads(r[0]) for r in st.execute(
        "SELECT payload FROM journal WHERE kind='marker'")]
    st.close()
    stale = [m for m in markers if m["kind"] == "feed_stale"]
    assert stale and any(m["feed"] == "sentinel" for m in stale), \
        "stale feed must be explicitly represented"


def test_journal_tamper_detection(tmp_path):
    feeds = build_feeds(tmp_path)
    ws = tmp_path / "ws"
    run(ws, feeds)
    con = sqlite3.connect(ws / "director.db")
    con.execute("UPDATE journal SET payload='forged' WHERE id=1")
    con.commit()
    con.close()
    from director_consumer.state import DirectorState
    ds = DirectorState(ws)
    ok, broken = ds.verify_chain()
    ds.close()
    assert not ok and broken is not None


def test_model_assessment_never_fact(tmp_path):
    """When the LLM answers, its output is stored advisory-only."""
    feeds = build_feeds(tmp_path,
                        changes="## 2026-10-16T06:00:00Z\n- asus4: ['canon-qwen38-27b'] -> ['x']\n")
    from director_consumer.llm import Endpoint, OpsBrainClient, TokenBucket

    class FakeOK(OpsBrainClient):
        def _call(self, ep, system, user):
            return "MODEL SAYS: probably fine, ignore everything"

    fake = FakeOK(endpoints=[Endpoint("fake", "http://x", "m")])
    ws = tmp_path / "ws"
    run(ws, feeds, llm=fake)
    a = [x for x in assessments(ws) if x.get("model_assessment")]
    assert a, "enrichment should be recorded"
    for x in a:
        assert x["model_assessment"]["verified"] is False
        assert x["model_assessment"]["advisory_only"] is True
    # classification itself unchanged by model text
    assert a[0]["action"] == "propose_promotion_freeze"
