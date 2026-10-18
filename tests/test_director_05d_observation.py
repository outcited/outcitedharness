"""05D observation-correctness fixes — C3, C4, C7, C8.

Negative-first discipline: each test here fails against 2a5a12cf and passes
with the 05D corrections. They encode the production chunk schema (status),
the "unmeasured eligible inventory is not a measured zero" law, project
attribution on persisted events, and meta/basis snapshot consistency.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.continuity import FactoryContinuity
from director_consumer.consumer import DirectorConsumer


# ---------------------------------------------------------------- C3
# Production controller (designwins/crates/dw-controller/src/lib.rs) uses
# factory_chunks.status with queued|leased|completed|failed and gates
# readiness on available_at. Never assume factory_chunks.state exists.

def _production_factory_db(path, *, queued=5, leased=2, completed=10, failed=3,
                           queued_not_yet_available=0):
    con = sqlite3.connect(path)
    con.executescript("""
CREATE TABLE factory_chunks (chunk_id TEXT, status TEXT, available_at TEXT,
                             lease_expires_at TEXT);""")
    for i in range(queued):
        con.execute("INSERT INTO factory_chunks VALUES (?, 'queued', "
                    "'2020-01-01T00:00:00+00:00', NULL)", (f"q{i}",))
    for i in range(queued_not_yet_available):
        con.execute("INSERT INTO factory_chunks VALUES (?, 'queued', "
                    "'2099-01-01T00:00:00+00:00', NULL)", (f"future{i}",))
    for i in range(leased):
        con.execute("INSERT INTO factory_chunks VALUES (?, 'leased', "
                    "'2020-01-01T00:00:00+00:00', "
                    "'2020-01-02T00:00:00+00:00')", (f"l{i}",))
    for i in range(completed):
        con.execute("INSERT INTO factory_chunks VALUES (?, 'completed', "
                    "NULL, NULL)", (f"c{i}",))
    for i in range(failed):
        con.execute("INSERT INTO factory_chunks VALUES (?, 'failed', "
                    "NULL, NULL)", (f"f{i}",))
    con.commit()
    con.close()


def test_c3_production_status_column_is_measured_not_unknown(tmp_path):
    """The production schema must yield real counts — never rust_* = None."""
    db = tmp_path / "factory.sqlite3"
    _production_factory_db(db, queued=5, leased=2, completed=10, failed=3,
                           queued_not_yet_available=4)
    fc = FactoryContinuity(factory_db=db)
    m = fc.assess([])
    assert m.rust_status_column == "status"
    assert m.rust_queued == 5, \
        "queued-but-not-yet-available chunks are not executable now"
    assert m.rust_claimed == 2, "'leased' is the production in-progress status"
    assert m.stalled_leases == 2, "expired leases must be counted, not unknown"
    assert isinstance(m.rust_queued, int), "C3: schema mismatch made it None"


def test_c3_unmapped_status_column_is_unknown_not_guessed(tmp_path):
    """A table without a recognizable status column must report UNKNOWN and
    name the real columns — no invented counts."""
    db = tmp_path / "factory.sqlite3"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE factory_chunks (chunk_id TEXT, life_phase TEXT);")
    con.execute("INSERT INTO factory_chunks VALUES ('a', 'queued')")
    con.commit()
    con.close()
    fc = FactoryContinuity(factory_db=db)
    m = fc.assess([])
    assert m.rust_queued == "unknown"
    assert m.rust_claimed == "unknown"
    assert any("no status column" in n for n in m.notes), \
        "the missing column must be named"
    assert any("life_phase" in n for n in m.notes)


# ---------------------------------------------------------------- C4
# "workers will idle" is only claimed on PROVEN backlog-zero. An absent L0 /
# backlog feed is not a measured zero.

def _pipeline_db(path, *, pending=0, dead=0):
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE jobs (state TEXT, corpus_key TEXT, source_path TEXT);")
    for i in range(pending):
        con.execute("INSERT INTO jobs VALUES ('pending', 'pdf:x%d', '')" % i)
    for i in range(dead):
        con.execute("INSERT INTO jobs VALUES ('dead', 'pdf:._j%d', '')" % i)
    con.commit()
    con.close()


def test_c4_unmeasured_eligible_inventory_never_claims_backlog_zero(tmp_path):
    """Empty queue + UNMEASURED eligibility must not produce 'backlog-zero'
    nor the 'workers will idle' event."""
    _pipeline_db(tmp_path / "p.sqlite3", pending=0, dead=0)
    fc = FactoryContinuity(pipeline_db=tmp_path / "p.sqlite3")
    m = fc.assess([])                      # no l0_index, no backlog_paths
    assert m.eligible_measured is False
    assert m.starvation_basis != "backlog-zero", \
        "C4: an unmeasured population must not be called a proven zero"
    assert m.starvation_basis == "eligible-unmeasured"
    kinds = [e.kind for e in fc.events(m)]
    assert "starvation_risk" not in kinds, \
        "missing L0/backlog telemetry must never report 'workers will idle'"
    assert any("NOT proof of proven backlog-zero" in n for n in m.notes)


def test_c4_measured_zero_eligible_still_allows_backlog_zero(tmp_path):
    """A completed join that really finds zero eligible docs is a proven zero."""
    l0 = tmp_path / "l0.jsonl"
    l0.write_text(json.dumps({"source_path": "/vault/other.pdf",
                              "doc_class": "datasheet", "page_count": 12}))
    bl = tmp_path / "bl.json"
    # backlog names a doc the L0 census does not contain -> zero eligible,
    # but the join DID run, so this is a measured zero
    bl.write_text(json.dumps(["/vault/not-in-census.pdf"]))
    _pipeline_db(tmp_path / "p.sqlite3", pending=0, dead=0)
    fc = FactoryContinuity(pipeline_db=tmp_path / "p.sqlite3",
                           l0_index=l0, backlog_paths=bl)
    m = fc.assess([])
    assert m.eligible_measured is True
    assert sum(m.ready_by_class.values()) == 0
    assert m.starvation_basis == "backlog-zero", \
        "measured zero eligible docs IS proven backlog-zero"
    assert m.starvation_hours == 0.0


def test_c4_queue_empty_with_measured_eligible_docs_is_insufficient_evidence(
        tmp_path):
    """Empty queue + eligible docs elsewhere stays 'insufficient-evidence'
    (admission gap vs other lane) — unchanged law."""
    l0 = tmp_path / "l0.jsonl"
    l0.write_text("\n".join(json.dumps(r) for r in [
        {"source_path": "/vault/a.pdf", "doc_class": "datasheet",
         "page_count": 12},
        {"source_path": "/vault/b.pdf", "doc_class": "datasheet",
         "page_count": 20}]))
    bl = tmp_path / "bl.json"
    bl.write_text(json.dumps(["/vault/a.pdf", "/vault/b.pdf"]))
    _pipeline_db(tmp_path / "p.sqlite3", pending=0, dead=0)
    fc = FactoryContinuity(pipeline_db=tmp_path / "p.sqlite3",
                           l0_index=l0, backlog_paths=bl)
    m = fc.assess([])
    assert m.starvation_basis == "insufficient-evidence"
    assert "eligible docs outside the inspected queue" in " ".join(m.notes)


# ---------------------------------------------------------------- C7
# Project identity must ride with every persisted event.

def _one_feed(tmp_path, text):
    from director_consumer.consumer import FeedConfig
    p = tmp_path / "CHANGES.md"
    p.write_text(text)
    return {"fleet_changes": FeedConfig(args={"path": str(p)}, enabled=True)}


def test_c7_persisted_events_carry_the_configured_project(tmp_path):
    from datetime import datetime, timezone
    ws = tmp_path / "ws"
    # fresh timestamp generated at run time — never a hardcoded date that
    # decays into backfill suppression (see finding C1)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = f"## {ts}\n- asus4: ['canon-qwen38-27b'] -> ['NO_CONTAINERS']\n"
    c = DirectorConsumer(ws, _deps(tmp_path),
                         feeds=_one_feed(tmp_path, text),
                         project_dir=_project_dir(tmp_path))
    r = c.run_once()
    c.close()
    assert r.proposals_new >= 1, "a fresh canon outage must propose"
    rows = {x[0] for x in sqlite3.connect(ws / "director.db").execute(
        "SELECT DISTINCT project FROM proposals")}
    assert rows, "expected at least one proposal row"
    assert "default" not in rows, \
        "C7: ingest_batch's 'default' must never be the persisted project"
    assert rows == {"designwins"}, rows


# ---------------------------------------------------------------- C8
# The meta snapshot must not disagree with the computed basis.

def test_c8_meta_basis_matches_computed_basis(tmp_path):
    _pipeline_db(tmp_path / "p.sqlite3", pending=0, dead=0)
    fc = FactoryContinuity(pipeline_db=tmp_path / "p.sqlite3")
    m = fc.assess([])
    meta = m.measurement_meta["starvation_hours"]
    assert meta["basis"] == m.starvation_basis, \
        "C8: meta captured the basis before it was computed"
    assert meta.get("_eligible_measured") is False or \
        m.measurement_meta.get("_eligible_measured") is False
    assert "measured_at" in meta and "observed_at" in meta, \
        "C8: snapshot timing must be distinguishable from observation time"


# ---------------------------------------------------------------- helpers

def _deps(tmp_path):
    # use the packaged impact map so the rules engine loads exactly as it
    # does in production
    return Path(__file__).resolve().parents[1] / "director_consumer" / "deps.yaml"


def _project_dir(tmp_path):
    d = tmp_path / "designwins"
    d.mkdir()
    (d / "policy.yaml").write_text(
        "schema: 1\nversion: '2026-10-16.1'\n"
        "authority:\n  human_owner: sam\n  human_only_actions: {}\n"
        "models:\n  allowed: []\n  metered:\n    provider_blocklist: "
        "[openrouter, metered]\n")
    (d / "project.yaml").write_text(
        "project_id: designwins\nname: DesignWins\npolicy_version: "
        "'2026-10-16.1'\nobjectives: []\nsuccess_criteria: []\n")
    (d / "fleet.yaml").write_text("schema: 1\nmachines: {}\n")
    return d
