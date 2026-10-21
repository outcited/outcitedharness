"""DIRECTOR-CODE-INTEL-02 — hermetic acceptance: doctrine through the real
context compiler + the four real DesignWins blockers ranked with the
doctrine-carrying DAG. No network, no live services: registry fixture +
rank_blockers only.

Blockers are the operator-named real ones (2026-10-20):
  Reader-v2 provenance recovery, S6 normalization-to-release gate,
  FE/cr-core decision API, factory receiver-to-M5 admission.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.context import ContextCompiler
from director_consumer.product_graph import (Blocker, STAGES,
                                             downstream_of, rank_blockers)
from director_consumer.projects import ProjectRegistry, load_spec_from_config

PROJECT_DIR = (Path(__file__).resolve().parents[1]
               / "projects" / "designwins")


@pytest.fixture()
def registry(tmp_path):
    import sqlite3
    con = sqlite3.connect(":memory:")
    reg = ProjectRegistry(con)
    spec = load_spec_from_config(PROJECT_DIR)
    reg.register(spec)
    return reg, spec


def test_doctrine_persists_in_registry_roundtrip(registry):
    reg, spec = registry
    back = reg.get(spec.project_id)
    assert set(back.operational_doctrine) >= {
        "provenance_first", "normalization_prerequisite",
        "frontend_acceptance_honesty", "energy_economics",
        "dependency_chain_truth"}
    assert back.policy_version == "2026-10-20.2"


def test_doctrine_reaches_compiled_packet(registry, tmp_path):
    reg, spec = registry
    compiler = ContextCompiler(repo_root=tmp_path,
                               project_dir=PROJECT_DIR)
    packet = compiler.compile("what blocks cross-manufacturer comparison?",
                              project_id=spec.project_id, registry=reg)
    assert "operational_doctrine" in packet.sections
    doctrine_items = [i for i in packet.items
                      if i.text.startswith("doctrine ")]
    assert {i.text.split(":")[0].removeprefix("doctrine ")
            for i in doctrine_items} >= {"energy_economics",
                                         "normalization_prerequisite"}
    assert all(i.authority == "operator doctrine"
               and i.source.startswith("project_registry:")
               for i in doctrine_items)


# ---------------------------------------------------------- 4 real blockers

REAL_BLOCKERS = [
    Blocker(
        blocker_id="REAL-S6-normalization-gate",
        title="S6: normalization-to-release gate — un-normalized values "
              "cannot cross into release comparison",
        stage="normalization", severity="P0", confidence=0.85, age_h=120.0,
        smallest_next_action="run typed-axis normalization over "
                             "pinout-staged feed; owner typing pending "
                             "(boundary has no normalization lane)"),
    Blocker(
        blocker_id="REAL-Reader2-provenance-recovery",
        title="Reader-v2 provenance recovery — quote/page/sha chain must "
              "resolve for handoff evidence",
        stage="verification", severity="P0", confidence=0.8, age_h=96.0,
        smallest_next_action="m5: re-run page attribution on claims with "
                             "page=null; report resolvable fraction"),
    Blocker(
        blocker_id="REAL-FE-cr-core-decision-api",
        title="FE/cr-core decision API contract — journeys call a decision "
              "experience that is not yet accepted end-to-end",
        stage="store-api", severity="P1", confidence=0.7, age_h=336.0,
        smallest_next_action="cr-core: pin decision-api contract version; "
                             "m4: wire one journey against it"),
    Blocker(
        blocker_id="REAL-factory-receiver-admission",
        title="factory receiver-to-M5 admission — delivered PDFs must land "
              "in admission queues with sha-verified manifests",
        stage="m5-admission", severity="P0", confidence=0.75, age_h=48.0,
        smallest_next_action="pdf-factory: re-emit receiver manifests; m5: "
                             "reconcile intake log against backlog"),
]


def test_all_four_stages_exist_in_dag():
    for b in REAL_BLOCKERS:
        assert b.stage in STAGES, f"{b.blocker_id}: stage {b.stage} missing"


def test_hermetic_four_blocker_ranking():
    ranked = rank_blockers(list(REAL_BLOCKERS))
    ids = [b.blocker_id for b in ranked]
    # doctrine-shaped expectations: upstream chain position dominates
    # when severities are comparable (three P0s ordered by downstream
    # reach: m5-admission > verification > normalization; the P1 store-api
    # blocker ranks last despite being oldest)
    assert ids == ["REAL-factory-receiver-admission",
                   "REAL-Reader2-provenance-recovery",
                   "REAL-S6-normalization-gate",
                   "REAL-FE-cr-core-decision-api"]
    for b in ranked:
        assert b.why_ranked.startswith("score=")
        assert len(downstream_of(b.stage)) >= 1


def test_normalization_owner_is_honestly_missing():
    """The ownership boundary has NO normalization lane — the doctrine-
    carrying DAG makes that gap visible instead of misattributing it."""
    reg = yaml.safe_load((PROJECT_DIR / "project.yaml").read_text())
    lanes = reg["ownership_boundary"]
    owning = [l for l, v in lanes.items()
              if "normalization" in (v.get("owns") or [])]
    assert owning == []   # honest gap: next owner action = Sam types it
    # and the S6 blocker's smallest_next_action says exactly that
    s6 = next(b for b in REAL_BLOCKERS if "S6" in b.blocker_id)
    assert "owner typing pending" in s6.smallest_next_action


def test_github_comment_is_not_ack_untouched_by_pr():
    """Invariant preserved: GitHub text never becomes authenticated ACK.
    acceptance.validate_acceptance remains the sole path (see 05B tests);
    PR #21 touches neither acceptance.py nor messaging.py."""
    import subprocess
    diff = subprocess.run(
        ["git", "diff", "--name-only",
         "origin/director/06-fleet-intelligence...HEAD"],
        capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[1]).stdout.split()
    assert not any(f in ("director_consumer/acceptance.py",
                         "director_consumer/messaging.py",
                         "director_consumer/authorization.py")
                   for f in diff), f"gate files touched: {diff}"
