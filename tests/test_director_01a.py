"""DIRECTOR-01A — regression + failure-injection tests for each review item.

 1  journal head-cache after rollback          test_rollback_head_cache
 2  transactional outbox crash-heal            test_outbox_crash_heal + atomic name
 3  factory pagination cursor                  test_factory_pagination_no_loss
 4  fleet multiline sections + cursor bytes    test_fleet_multiline_and_exact_cursor
 5  structured canon matching                  test_canon_structured_matrix
 6  machine alias reconciliation               test_alias_normalization
 7  backfill suppression                        test_backfill_suppression
 8  watcher-health rule (sentinel stale)       test_watcher_stale_classifies
 9  (all of the above are the targeted tests)
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.adapters import (FleetChangesAdapter,
                                        FactoryEventsAdapter,
                                        parse_fleet_delta)
from director_consumer.consumer import DirectorConsumer, FeedConfig
from director_consumer.impact import ImpactMap
from director_consumer.mailgen import Proposal, write_proposal_atomic
from director_consumer.state import DirectorState

deps = Path(__file__).resolve().parents[1] / "director_consumer" / "deps.yaml"


# ---------------------------------------------------------- 01A #1 rollback

def test_rollback_head_cache(tmp_path):
    st = DirectorState(tmp_path)
    st.journal_run({"n": 1})
    ok1, _ = st.verify_chain()
    assert ok1

    from director_consumer.schema import Assessment, SourceRef, Proposal, StateMarker
    a = Assessment(rule_id="r", severity="P1", impacts=["x"], action="act",
                   authority="agent:m5", rationale="ra",
                   evidence=[SourceRef("f", "s", None, time.time())],
                   event_kind="k", event_summary="sum", trigger_key="tk1")
    good = Proposal("message", "tk-ok", "a.md", "body", "agent:m5")
    bad_marker = StateMarker("feed_error", "f", object(), time.time())

    st.ingest_batch("f", "c1", [], [a], [good], [])
    with pytest.raises(TypeError):
        # unserializable marker payload fails MID-transaction after journal
        # appends -> exercises the rollback path
        st.ingest_batch("f", "c2", [], [a], [], [bad_marker])
    # cursor must NOT have advanced, and the chain must still verify + extend
    assert st.get_cursor("f") == "c1"
    ok2, broken = st.verify_chain()
    assert ok2, f"chain broken after rollback at {broken}"
    st.ingest_batch("f", "c3", [], [], [Proposal("message", "tk2", "c.md",
                                                 "body", "agent:m5")], [])
    ok3, _ = st.verify_chain()
    assert ok3, "chain must extend cleanly after a rolled-back batch"
    st.close()


# ------------------------------------------------- 01A #2 transactional outbox

def test_outbox_crash_heal(tmp_path):
    st = DirectorState(tmp_path)
    from director_consumer.schema import Proposal
    p = Proposal("message", "tkX", "20261016T000000Z_a_b_status_x.md",
                 "BODY-1", "agent:m5")
    # commit the proposal row but DO NOT write the file (simulated crash)
    st.ingest_batch("f", "c", [], [], [p], [])
    assert st.unwritten_count() == 1
    assert list((tmp_path / "outbox").glob("*.md")) == []
    # heal: flush writes exactly once, deterministically named
    wrote = []
    healed = st.flush_pending_writes(lambda row: wrote.append(row) or
                                     write_proposal_atomic(
                                         Proposal(row["ptype"], row["trigger_key"],
                                                  row["filename"], row["body"],
                                                  row["authority"]), tmp_path))
    assert healed == 1 and st.unwritten_count() == 0
    files = list((tmp_path / "outbox").glob("*.md"))
    assert len(files) == 1 and "BODY-1" in files[0].read_text()
    # crash AFTER write but BEFORE marking: heal again -> same file, no dupes
    st.con.execute("UPDATE proposals SET written=0 WHERE trigger_key='tkX'")
    st.con.commit()
    healed2 = st.flush_pending_writes(lambda row:
                                      write_proposal_atomic(
                                          Proposal(row["ptype"], row["trigger_key"],
                                                   row["filename"], row["body"],
                                                   row["authority"]), tmp_path))
    assert healed2 == 1
    assert len(list((tmp_path / "outbox").glob("*.md"))) == 1, \
        "re-heal must land on the SAME deterministic filename"
    st.close()


def test_outbox_atomic_write(tmp_path):
    p1 = Proposal("escalation", "tk-1", "20261016T000000Z_a_sam_esc_x.md",
                  "hello", "sam")
    f1 = write_proposal_atomic(p1, tmp_path)
    f2 = write_proposal_atomic(p1, tmp_path)
    assert f1 == f2 and f1.exists() and "hello" in f1.read_text()
    assert not list((tmp_path / "escalations").glob("*.tmp"))


# ------------------------------------------------- 01A #3 factory pagination

def test_factory_pagination_no_loss(tmp_path):
    db = tmp_path / "factory.sqlite3"
    con = sqlite3.connect(db)
    con.executescript("""
CREATE TABLE factory_events (event_id TEXT PRIMARY KEY, event_type TEXT,
 subject_id TEXT, payload_json TEXT, payload_sha256 TEXT, created_at TEXT);""")
    for i in range(1, 1201):  # >2 pages of 500
        con.execute("INSERT INTO factory_events VALUES (?,?,?,?,?,?)",
                    (f"e{i}", "chunk_claimed", f"c{i}", "{}", "x",
                     f"2026-10-16T00:{i//60:02d}:{i%60:02d}"))
    con.commit()
    con.close()
    ad = FactoryEventsAdapter(db)
    total, cursor = 0, ""
    for _ in range(10):
        evs, _, cursor = ad.poll(cursor)
        if not evs:
            break
        total += len(evs)
    assert total == 1200, f"pagination lost events: saw {total}/1200"


# --------------------------------------- 01A #4 multiline + exact byte cursor

def test_fleet_multiline_and_exact_cursor(tmp_path):
    chg = tmp_path / "CHANGES.md"
    chg.write_text(
        "## 2026-10-16T07:00:00Z\n"
        "- asus1: ['NO_CONTAINERS'] -> ['tp4-worker']\n"
        "- dgx3: ['tp4-worker'] -> ['NO_CONTAINERS']\n"
        "- spark_embedder: [] -> ['UNREACHABLE']\n")
    ad = FleetChangesAdapter(chg, aliases={"e10b": "dgx1"})
    evs, _, cur = ad.poll("")
    assert len(evs) == 3, "every bullet must be its own event"
    assert evs[0].attrs["box"] == "asus1"
    assert evs[1].attrs["change_kind"] == "container_delta"
    assert evs[2].attrs["change_kind"] == "probe_state"
    assert evs[2].attrs["box"] == "spark_embedder"
    assert evs[2].attrs["services"] == [], \
        "probe states are states, not services; identity lives in box"
    # exact byte accounting (01B json cursor): o == bytes consumed
    import json as _json
    c = _json.loads(cur)
    assert c["o"] == chg.stat().st_size, "cursor must consume exactly the file"
    # append a new section -> ONLY the new section's bullets
    with chg.open("a") as fh:
        fh.write("\n## 2026-10-16T08:00:00Z\n- m5: ['a'] -> ['b']\n")
    evs2, _, cur_m5 = ad.poll(cur)
    assert [e.attrs["box"] for e in evs2] == ["m5"]
    # append a bullet to the LAST section without a new header -> not lost
    with chg.open("a") as fh:
        fh.write("- whitepine: ['c'] -> ['d']\n")
    evs_w, _, _ = ad.poll(cur_m5)
    assert any(e.attrs.get("box") == "whitepine" for e in evs_w), \
        "continuation bytes must not be silently discarded"


def test_parse_fleet_delta_shapes():
    d = parse_fleet_delta("- asus4: ['canon-qwen38-27b'] -> ['NO_CONTAINERS']")
    assert d["box"] == "asus4" and d["change_kind"] == "container_delta"
    assert d["from"] == ["canon-qwen38-27b"] and d["to"] == ["NO_CONTAINERS"]
    assert "canon-qwen38-27b" in d["services"]
    d2 = parse_fleet_delta("- asus4: ['canon-qwen38-27b'] -> ['SSH_FAIL']")
    assert d2["change_kind"] == "probe_state" and d2["to_state"] == "SSH_FAIL"
    d3 = parse_fleet_delta("- e10b: None -> ['canon-replica']")
    assert d3["change_kind"] == "container_delta"
    assert d3["services"] == ["canon-replica"]
    assert parse_fleet_delta("prose line without delta") is None


# ------------------------------------------- 01A #5 structured canon matching

def _one_feed(tmp_path, changes):
    chg = tmp_path / "CHANGES.md"
    chg.write_text(changes)
    return {"fleet_changes": FeedConfig(args={"path": chg})}


def test_canon_structured_matrix(tmp_path):
    ws = tmp_path / "ws"
    cases = {
        # (rule expected, severity, authority)
        "## 2026-10-16T10:00:00Z\n- asus4: ['canon-qwen38-27b'] -> ['NO_CONTAINERS']\n":
            ("canon-primary-outage", "P0", "sam"),
        "## 2026-10-16T10:01:00Z\n- asus4: ['canon-qwen38-27b'] -> ['SSH_FAIL']\n":
            ("canon-primary-unreachable", "P1", "agent:m5-opencode"),
        "## 2026-10-16T10:02:00Z\n- dgx1: ['canon-replica'] -> ['NO_CONTAINERS']\n":
            ("canon-replica-change", "P1", "agent:m5-opencode"),
        "## 2026-10-16T10:03:00Z\n- asus4: ['NO_CONTAINERS'] -> ['canon-qwen38-27b']\n":
            ("canon-primary-recovered", "P2", "agent:m5-opencode"),
    }
    for text, (want_rule, want_sev, want_auth) in cases.items():
        w = tmp_path / f"ws-{want_rule}"
        c = DirectorConsumer(w, deps, feeds=_one_feed(tmp_path, text))
        c.run_once()
        c.close()
        found = [json.loads(r[0]) for r in sqlite3.connect(
            w / "director.db").execute(
            "SELECT payload FROM journal WHERE kind='assessment'")]
        hit = [x for x in found if x["rule_id"] == want_rule]
        assert hit, f"{text!r}: expected {want_rule}, got {[x['rule_id'] for x in found]}"
        assert hit[0]["severity"] == want_sev and hit[0]["authority"] == want_auth
        # no P0/sam leak from the other canon rules on this same event
        sam_hits = [x for x in found if x["authority"] == "sam"
                    and x["rule_id"] != want_rule]
        assert not sam_hits or want_auth == "sam", \
            f"unexpected sam-authority classification: {sam_hits}"


# ------------------------------------------- 01A #6 alias reconciliation

def test_alias_normalization(tmp_path):
    ws = tmp_path / "ws"
    text = ("## 2026-10-16T11:00:00Z\n"
            "- e10b: ['canon-replica'] -> ['NO_CONTAINERS']\n")
    c = DirectorConsumer(ws, deps, feeds=_one_feed(tmp_path, text))
    c.run_once()
    c.close()
    rows = [json.loads(r[0]) for r in sqlite3.connect(
        ws / "director.db").execute(
        "SELECT payload FROM journal WHERE kind='assessment'")]
    hit = [x for x in rows if x["rule_id"] == "canon-replica-change"]
    assert hit, "alias e10b must still classify via the replica rule"
    # canonical identity: evidence summary keeps the raw line; the trigger
    # key is stable under the canonical box name
    w2 = tmp_path / "ws2"
    text2 = ("## 2026-10-16T11:00:30Z\n"
             "- dgx1: ['canon-replica'] -> ['NO_CONTAINERS']\n")
    c2 = DirectorConsumer(w2, deps, feeds=_one_feed(tmp_path, text2))
    c2.run_once()
    c2.close()
    p1 = sqlite3.connect(ws / "director.db").execute(
        "SELECT trigger_key FROM proposals LIMIT 1").fetchone()
    p2 = sqlite3.connect(w2 / "director.db").execute(
        "SELECT trigger_key FROM proposals LIMIT 1").fetchone()
    # same 6h window + same canonical box + same services => SAME incident
    assert p1 == p2, "e10b and dgx1 replicas must collapse to one incident"


# ------------------------------------------- 01A #7 backfill suppression

def test_backfill_suppression(tmp_path):
    ws = tmp_path / "ws"
    text = ("## 2026-10-09T00:00:00Z\n"  # a week old
            "- asus4: ['canon-qwen38-27b'] -> ['NO_CONTAINERS']\n")
    c = DirectorConsumer(ws, deps, feeds=_one_feed(tmp_path, text))
    r = c.run_once()
    c.close()
    assert r.historical_assessments == 1 and r.suppressed_backfill == 1
    assert r.proposals_new == 0, "historical events must not page"
    assert len(list((ws / "escalations").glob("*.md"))) == 0
    # but the assessment IS journaled with the historical marker
    rows = [json.loads(x[0]) for x in sqlite3.connect(
        ws / "director.db").execute(
        "SELECT payload FROM journal WHERE kind='assessment'")]
    assert rows and rows[0]["rationale"].startswith("[historical")

    # fresh event still pages
    ws2 = tmp_path / "ws2"
    text2 = ("## 2026-10-16T23:59:00Z\n"
             "- asus4: ['canon-qwen38-27b'] -> ['NO_CONTAINERS']\n")
    c2 = DirectorConsumer(ws2, deps, feeds=_one_feed(tmp_path, text2))
    r2 = c2.run_once()
    c2.close()
    assert r2.proposals_new >= 1, "fresh canon outage must still propose"


# ------------------------------------------- 01A #8 watcher-health rule

def test_watcher_stale_classifies(tmp_path):
    lkg = tmp_path / "last-known-good.json"
    old = time.time() - 200 * 3600
    lkg.write_text("{}")
    import os
    os.utime(lkg, (old, old))
    feeds = {"sentinel": FeedConfig(args={
        "alerts": tmp_path / "ALERTS.md", "lkg": lkg})}
    (tmp_path / "ALERTS.md").write_text("")
    ws = tmp_path / "ws"
    c = DirectorConsumer(ws, deps, feeds=feeds)
    r = c.run_once()
    c.close()
    rows = [json.loads(x[0]) for x in sqlite3.connect(
        ws / "director.db").execute(
        "SELECT payload FROM journal WHERE kind='assessment'")]
    hit = [x for x in rows if x["rule_id"] == "watcher-health-stale"]
    assert hit and hit[0]["severity"] == "P1"
    assert hit[0]["authority"] == "agent:m5-opencode"
    assert r.proposals_new >= 1
    # repeated runs inside the 6h window do not re-page
    c = DirectorConsumer(ws, deps, feeds=feeds)
    r2 = c.run_once()
    c.close()
    assert r2.proposals_new == 0, "watcher incident must collapse in-window"


# ------------------------------------------- map integrity

def test_deps_map_loads_and_rules_wellformed():
    m = ImpactMap.load(deps)
    assert m.version == "2026-10-16.2"
    ids = [r.rid for r in m.rules]
    assert len(ids) == len(set(ids))
    canon_rules = [r for r in m.rules if r.service_tokens]
    assert canon_rules, "structured service rules must exist"
    generic = [r for r in m.rules if r.rid == "fleet-infra-change"][0]
    assert "canon-qwen38-27b" in generic.exclude_service_tokens, \
        "generic rule must not double-classify canon events"
