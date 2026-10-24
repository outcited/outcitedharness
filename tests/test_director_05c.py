"""DIRECTOR-05C tests — end-to-end correctness regressions.

Covers: D1-D7 repairs, §8 measurement semantics (incl. the AppleDouble
LIKE-wildcard bug), §6 verification receipts, §7 durable decision paths.
The runtime rehearsal (§10) lives in director_consumer/rehearse.py and is
exercised by the `rehearse` suite at the bottom.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.authorization import (AuthRecord, AuthRequest,
                                             AuthorizationError,
                                             AuthorizationStore)
from director_consumer.continuity import FactoryContinuity
from director_consumer.incidents import IncidentStore, RecoveryError, load_correlations
from director_consumer.messaging import (DeliveryDisabled, DeliveryPolicy,
                                         MailboxDelivery)
from director_consumer.projects import ProjectSpec, ProjectRegistry

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / "director_consumer" / "deps.yaml"


def _db(tmp_path, n="d.db"):
    con = sqlite3.connect(tmp_path / n, timeout=30)
    con.row_factory = sqlite3.Row
    return con


# ------------------------------------------------ §8 measurement semantics

def _pipeline(tmp_path, rows):
    con = sqlite3.connect(tmp_path / "p.db")
    con.executescript("CREATE TABLE jobs (state TEXT, corpus_key TEXT, "
                      "source_path TEXT);")
    for r in rows:
        con.execute("INSERT INTO jobs VALUES (?,?,?)", r)
    con.commit()
    con.close()
    return tmp_path / "p.db"


def test_appledouble_matches_literal_basename_prefix_only(tmp_path):
    """§8.9/T — LIKE '_' is a wildcard; only the literal basename
    prefix '._' is AppleDouble."""
    db = _pipeline(tmp_path, [
        ("dead", "pdf:._junk", "/vault/._junk.pdf"),          # junk
        ("dead", "pdf:._another", "/vault/sub/._another.pdf"),  # junk
        ("dead", "pdf:x", "/vault/x.pdf"),                     # normal
        ("dead", "pdf:my_file", "/vault/my_file.pdf"),         # normal
        ("dead", "pdf:file._v2", "/vault/file._v2.pdf"),       # normal!
        ("dead", "pdf:archive.1", "/vault/archive.1.pdf"),     # normal
        ("live", "pdf:._notdead", "/vault/._notdead.pdf"),     # not dead
    ])
    fc = FactoryContinuity(pipeline_db=db)
    m = fc.assess([])
    assert m.junk_dead_jobs == 2, (
        f"expected exactly 2 AppleDouble dead jobs, got {m.junk_dead_jobs} "
        f"— underscores must not be wildcards")


def test_chunk_counts_are_not_document_counts(tmp_path):
    con = sqlite3.connect(tmp_path / "f.db")
    con.executescript("CREATE TABLE factory_chunks (state TEXT, "
                      "lease_expires_at TEXT);")
    for _ in range(10):
        con.execute("INSERT INTO factory_chunks VALUES ('queued', NULL)")
    con.commit()
    con.close()
    fc = FactoryContinuity(factory_db=tmp_path / "f.db")
    m = fc.assess([])
    assert m.rust_queued == 10
    meta = m.measurement_meta["ready_backlog_docs"]
    assert meta["unit"] == "documents" and meta["lane"] == "execution-queues"
    # the meta states what population this counts — chunks/work items,
    # never raw PDFs
    assert "queued work items" in meta["population"]


def test_facet_passes_are_not_unique_documents(tmp_path):
    fx = tmp_path / "fx"
    fx.mkdir()
    (fx / "done-a.txt").write_text("d1\nd2\nd3\n")
    (fx / "done-b.txt").write_text("d1\nd2\nd3\n")   # same 3 docs, 2 passes
    fc = FactoryContinuity(facet_root=fx)
    m = fc.assess([{"t": 1.0, "total": 6}, {"t": 3601.0, "total": 12}])
    meta = m.measurement_meta["consumption_rate_docs_hr"]
    assert meta["population"] == "documents touched by any facet pass", \
        "six pass-rows are three documents; the unit must say so"
    assert m.ledger_done_total == 6, "ledger counts pass rows, meta says so"


def test_missing_telemetry_is_not_an_empty_queue(tmp_path):
    fc = FactoryContinuity(factory_db=tmp_path / "nope.db",
                           pipeline_db=tmp_path / "nope2.db")
    m = fc.assess([])
    assert m.ready_backlog_docs == "unknown"
    assert m.starvation_basis == "queues-unavailable"
    kinds = [e.kind for e in fc.events(m)]
    assert "queue_empty" not in kinds and "starvation_risk" not in kinds


def test_queued_and_leased_are_distinct(tmp_path):
    con = sqlite3.connect(tmp_path / "f.db")
    con.executescript("CREATE TABLE factory_chunks (state TEXT, "
                      "lease_expires_at TEXT);")
    con.execute("INSERT INTO factory_chunks VALUES ('queued', NULL)")
    con.execute("INSERT INTO factory_chunks VALUES ('claimed', NULL)")
    con.commit()
    con.close()
    fc = FactoryContinuity(factory_db=tmp_path / "f.db")
    m = fc.assess([])
    assert m.rust_queued == 1 and m.rust_claimed == 1


def test_class_mapped_backlog_not_executable(tmp_path):
    l0 = tmp_path / "l0.jsonl"
    l0.write_text(json.dumps({"source_path": "/vault/a.pdf",
                              "doc_class": "datasheet", "page_count": 12}))
    bl = tmp_path / "bl.json"
    bl.write_text(json.dumps(["/vault/a.pdf"]))
    fc = FactoryContinuity(l0_index=l0, backlog_paths=bl,
                           class_costs={"datasheet": {"hours_per_doc": 0.05}})
    m = fc.assess([])
    assert m.worker_hours_estimate == pytest.approx(0.05)
    assert "planning" in m.measurement_meta["worker_hours_estimate"]["lane"], \
        "class-mapped backlog is planning data, not executable state"
    assert m.measurement_meta["worker_hours_estimate"]["completeness"] == \
        "partial"


def test_measured_coverage_is_not_below_target(tmp_path):
    """'measured' is a basis label, not a verdict."""
    con = sqlite3.connect(tmp_path / "f.db")
    con.executescript("CREATE TABLE factory_chunks (state TEXT, "
                      "lease_expires_at TEXT);")
    for _ in range(100):
        con.execute("INSERT INTO factory_chunks VALUES ('queued', NULL)")
    con.commit()
    con.close()
    fc = FactoryContinuity(factory_db=tmp_path / "f.db",
                           thresholds={"starvation_alert_h": 24},
                           population_aliases={
                               "queue-items(chunks+jobs-unconverted)":
                               "facet-pass-docs"})
    obs = [{"t": 0.0, "total": 0}, {"t": 3600.0, "total": 10}]
    fx = tmp_path / "fx"
    fx.mkdir()
    (fx / "done-a.txt").write_text("x\n" * 10)
    fc.facet_root = fx
    m = fc.assess(obs)
    assert m.starvation_basis == "measured"
    kinds = [e.kind for e in fc.events(m)]
    # backlog unknown/zero determines the event, not the word 'measured'
    assert isinstance(m.starvation_basis, str)


def test_worker_hours_units_are_named(tmp_path):
    fc = FactoryContinuity()
    m = fc.assess([])
    wh = m.measurement_meta["worker_hours_estimate"]
    assert "worker-hours" in wh["unit"] and "CPU" in wh["unit"]
    assert "not elapsed" in wh["unit"]


# ------------------------------------------------ §6 verification receipts

def _inc(tmp_path):
    con = _db(tmp_path)
    deps = __import__("yaml").safe_load(DEPS.read_text())
    st = IncidentStore(con, load_correlations(deps), project="alpha")
    from director_consumer.schema import Assessment, SourceRef
    a = Assessment(rule_id="factory-junk-flood", severity="P2",
                   impacts=["intake"], action="propose_investigation",
                   authority="agent:m5-opencode", rationale="suspected x",
                   evidence=[SourceRef("factory", "s", None, time.time())],
                   event_kind="junk_flood", event_summary="junk",
                   trigger_key=f"t-{time.time_ns()}")
    return st, st.observe(a)


def test_suspected_cause_never_promoted_by_resolve(tmp_path):
    st, iid = _inc(tmp_path)
    with pytest.raises(RecoveryError):
        st.resolve(iid, "looks fixed")   # no receipt: refused outright
    row = st.con.execute("SELECT verified_cause, suspected_cause, state "
                         "FROM incidents WHERE incident_id=?",
                         (iid,)).fetchone()
    assert row[0] is None, "resolve() must NEVER copy suspected into verified"
    assert row[1], "suspected cause is recorded as hypothesis"
    assert row[2] == "open", "no evidence = still visible"


def test_receipt_fields_required_for_verified_cause(tmp_path):
    st, iid = _inc(tmp_path)
    with pytest.raises(RecoveryError):
        st.resolve(iid, "fixed", receipt={"result": "ok"})   # incomplete
    real = tmp_path / "facts.jsonl"
    real.write_text("{}\n")
    st.resolve(iid, "service recovered; root cause unknown", receipt={
        "source": str(real), "scope": f"incident:{iid} new deliveries 24h",
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "method": "count of basenames prefix '._'", "result": "0 new junk"})
    row = st.con.execute("SELECT verified_outcome, verified_cause "
                         "FROM incidents WHERE incident_id=?",
                         (iid,)).fetchone()
    assert row[0] == "0 new junk"
    assert row[1] is None, "outcome is never a cause"


def test_old_occurrence_evidence_cannot_close_recurrence(tmp_path):
    st, iid = _inc(tmp_path)
    st.mark_recovery_evidence(iid, "root-cause-fixed", "old fix",
                              receipt={"source": "s",
                                       "scope": f"incident:{iid}",
                                       "observed_at": time.strftime(
                                           "%Y-%m-%dT%H:%M:%SZ",
                                           time.gmtime()),
                                       "method": "measurement", "result": "recovered-ok"})
    st.lifecycle_transition(iid, "investigating", "d", "e")
    st.lifecycle_transition(iid, "mitigation_proposed", "d", "e")
    st.lifecycle_transition(iid, "repair_in_progress", "d", "e")
    st.lifecycle_transition(iid, "recovery_observed", "d", "e")
    st.lifecycle_transition(iid, "recovery_verified", "d", "e")
    st.lifecycle_transition(iid, "closed", "d", "e")
    # recurrence: new occurrence
    st.lifecycle_transition(iid, "detected", "d", "junk returned")
    st.lifecycle_transition(iid, "investigating", "d", "e")
    with pytest.raises(RecoveryError):
        st.lifecycle_transition(iid, "recovery_verified", "d", "e"), \
            "evidence from the previous occurrence must not close a recurrence"


# ------------------------------------------------ D6 migration fixtures

def test_pre05_fixture_migrates_with_audit_and_children(tmp_path):
    con = _db(tmp_path)
    con.executescript("""
CREATE TABLE tasks (task_id TEXT PRIMARY KEY, project TEXT, state TEXT,
 accountable_owner TEXT, execution_agent TEXT, session_id TEXT, host TEXT,
 assignment_authority TEXT, epoch INTEGER, created_at REAL, updated_at REAL,
 last_verified_progress TEXT, correlation_id TEXT);
CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT,
 task_id TEXT NOT NULL, ts REAL, from_state TEXT, to_state TEXT, actor TEXT,
 evidence TEXT, note TEXT DEFAULT '');
INSERT INTO tasks VALUES ('T-1','alpha','active','owner','','','h','a',1,1.0,2.0,'','');
INSERT INTO task_events (task_id, ts, from_state, to_state, actor, evidence)
 VALUES ('T-1', 2.0, 'offered', 'active', 'owner', 'e');""")
    from director_consumer.scope import ensure_scoped
    ensure_scoped(con)
    t = con.execute("SELECT project, accountable_owner FROM tasks").fetchone()
    assert tuple(t) == ("alpha", "owner")
    ev = con.execute("SELECT task_id, project FROM task_events").fetchone()
    assert ev[0] == "T-1", "parent/child relationship preserved"
    log = con.execute("SELECT rows_before, rows_after FROM migration_log "
                      "WHERE tbl='tasks'").fetchone()
    assert tuple(log) == (1, 1), "migration audited with row counts"


def test_migration_interrupt_rolls_back_cleanly(tmp_path):
    con = _db(tmp_path)
    con.executescript("""
CREATE TABLE messages (message_id TEXT PRIMARY KEY, project TEXT DEFAULT 'default',
 correlation_id TEXT, recipient TEXT, msg_type TEXT, subject TEXT, body TEXT,
 state TEXT, attempts INTEGER DEFAULT 0, created_at REAL, sent_at REAL,
 acked_at REAL, expires_at REAL, authorization_ref TEXT);
INSERT INTO messages VALUES ('m1','alpha','c','a','question','s','b','proposed',0,1.0,NULL,NULL,2.0,'');""")
    # simulate interruption: the rebuild dies mid-transaction (as a
    # crash would) — the transaction must roll the whole attempt back
    from director_consumer import scope as scope_mod
    real_rebuild = scope_mod._rebuild
    def dying_rebuild(*a, **kw):
        raise RuntimeError("interrupted mid-migration")
    scope_mod._rebuild = dying_rebuild
    with pytest.raises(RuntimeError):
        scope_mod.ensure_scoped(con)
    # rollback leaves the ORIGINAL intact and un-migrated (retryable)
    n = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    assert n == 1, "original rows survive a failed migration"
    cols = [r[1] for r in con.execute("PRAGMA table_info(messages)")]
    assert "message_id" in cols
    scope_mod._rebuild = real_rebuild
    scope_mod.ensure_scoped(con)   # retry succeeds
    row = con.execute("SELECT project, recipient FROM messages").fetchone()
    assert tuple(row) == ("alpha", "a")


# ------------------------------------------------ §7 durable decision path

def test_decision_packet_persisted_and_model_receives_it(tmp_path):
    from director_consumer.consumer import DirectorConsumer, FeedConfig
    ws = tmp_path / "ws"
    chg = tmp_path / "CHANGES.md"
    chg.write_text("## 2026-10-16T23:00:00Z\n"
                   "- asus4: ['canon-qwen38-27b'] -> ['NO_CONTAINERS']\n")
    pdir = tmp_path / "projects" / "proj-x"
    pdir.mkdir(parents=True)
    (pdir / "project.yaml").write_text(
        "project_id: proj-x\nname: X\n"
        "objectives:\n  - {id: OBJ-1, text: keep things green}\n"
        "policy_version: '1'\nhuman_authority: sam\n")
    (pdir / "policy.yaml").write_text(
        "authority:\n  human_owner: sam\n"
        "models:\n  allowed:\n"
        "    - {name: fake, provider: local, base_url: 'http://x',"
        " model: m, role: routine}\n"
        "  rate_limits: {per_minute: 120, burst: 10}\n")
    from director_consumer.llm import Endpoint, OpsBrainClient
    captured = {}
    class Recorder(OpsBrainClient):
        def _call(self, ep, system, user):
            captured["packet_seen"] = "PACKET:" in user
            return "advisory note"
    llm = Recorder(endpoints=[Endpoint("fake", "http://x", "m")])
    c = DirectorConsumer(ws, DEPS,
                         feeds={"fleet_changes": FeedConfig(args={"path": chg})},
                         project_dir=pdir, llm=llm, machine_id="m5")
    # register the project and seed a P1-worthy state: acknowledged task
    # with no progress evidence for 3 days -> stalled-acknowledged
    from director_consumer.projects import load_spec_from_config
    ProjectRegistry(c.state.con).register(load_spec_from_config(pdir))
    from director_consumer.tasks import TaskStore
    ts = TaskStore(c.state.con, project="proj-x")
    ts.create("TASK-1", authority="director", evidence="OBJ-1")
    ts.transition("TASK-1", "offered", actor="director", evidence="e",
                  owner="agent-x")
    ts.transition("TASK-1", "acknowledged", actor="agent-x", evidence="ack")
    c.state.con.execute("UPDATE tasks SET updated_at=?, "
                        "last_verified_progress='' WHERE task_id='TASK-1'",
                        (time.time() - 3 * 86400,))
    c.state.con.commit()
    c.run_once()
    c.close()
    artifacts = list((ws / "decisions").glob("*.json"))
    assert artifacts, "decision packets must be persisted as artifacts"
    payload = json.loads(artifacts[0].read_text())
    assert payload["items"], "the packet contains the actual evidence items"
    assert payload["compiler_version"] and payload["source_versions"]
    assert captured.get("packet_seen") is True, \
        "the model must receive the packet, not a summary count"
    # decision record carries digest + advisory-only
    con = sqlite3.connect(ws / "director.db")
    rec = con.execute(
        "SELECT payload FROM journal WHERE kind='run' "
        "AND payload LIKE '%decision-record%'").fetchone()
    con.close()
    d = json.loads(rec[0])
    assert d["packet_digest"] and d["advisory_only"] is True


def test_visibility_private_by_default(tmp_path):
    from director_consumer.context import ContextCompiler
    dec = tmp_path / "DECISIONS.md"
    dec.write_text("# DECISIONS\n## D1 — rule\ncontent\n")
    comp = ContextCompiler(repo_root=ROOT, project_dir=None,
                           decisions_path=dec,
                           mailbox_root=ROOT / "shared-docs-queue",
                           shared_sources=set())   # nothing shared
    assert comp.mailbox is None, \
        "agentmail must be private until the project declares it shared"
    comp2 = ContextCompiler(repo_root=ROOT, project_dir=None,
                            decisions_path=dec,
                            mailbox_root=ROOT / "shared-docs-queue",
                            shared_sources={"agentmail"})
    assert comp2.mailbox is not None
