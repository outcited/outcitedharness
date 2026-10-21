"""DIRECTOR-CODE-INTEL-02 — doctrine + normalization-stage tests.

Proves (1) the operator's five truths are retrievable from the ACTUAL
project registry the context compiler reads, (2) the product DAG carries
a normalization stage between verification and identity-release, and
(3) blocker ranking APPLIES the doctrine: a normalization blocker ranks
above an identity-release blocker with identical attributes because it
blocks more of the chain, and a cloud-first proposal contradicts the
energy doctrine retrievably.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.product_graph import (Blocker, EDGES, STAGES,
                                             downstream_of, rank_blockers)

PROJECT_YAML = (Path(__file__).resolve().parents[1]
                / "projects" / "designwins" / "project.yaml")


@pytest.fixture(scope="module")
def registry():
    return yaml.safe_load(PROJECT_YAML.read_text())


def test_registry_carries_operational_doctrine(registry):
    d = registry.get("operational_doctrine") or {}
    for key in ("provenance_first", "normalization_prerequisite",
                "frontend_acceptance_honesty", "energy_economics",
                "dependency_chain_truth"):
        assert d.get(key), f"doctrine missing: {key}"
    assert "solar" in d["energy_economics"].lower()
    assert "bounded residual" in d["energy_economics"].lower()
    assert "cross-manufacturer" in d["normalization_prerequisite"].lower()


def test_registry_policy_version_bumped(registry):
    assert registry["policy_version"] >= "2026-10-20.2"


def test_doctrine_objectives_present(registry):
    ids = {o["id"] for o in registry["objectives"]}
    assert "PROVENANCE-TRUTH" in ids
    assert "ENGINEER-JOURNEY" in ids


def test_dag_has_normalization_stage_between_verification_and_identity():
    assert "normalization" in STAGES
    assert ("verification", "normalization") in EDGES
    assert ("normalization", "identity-release") in EDGES
    assert ("verification", "identity-release") not in EDGES


def test_dag_chain_matches_doctrine(registry):
    chain = [s.strip() for s in
             registry["operational_doctrine"]["dependency_chain_truth"]
             .replace("->", ">").split(">")]
    assert "normalization" in chain
    # every doctrine chain stage maps onto the DAG stages (modulo naming)
    dag = set(STAGES)
    named = {"PDF acquisition": "acquisition",
             "receiver manifests": "receiver-manifests",
             "admission": "m5-admission",
             "extraction": "extraction",
             "verification": "verification",
             "normalization": "normalization",
             "identity/release": "identity-release",
             "store/API": "store-api",
             "evidence wire": "evidence-wire",
             "frontend journeys": "fe-journeys",
             "engineer decision": "product-test"}
    for stage in chain:
        if stage in named:
            assert named[stage] in dag, f"doctrine stage not in DAG: {stage}"


def test_normalization_blocker_outranks_identity_blocker_identical_attrs():
    """The doctrine applied: identical severity/confidence/age, but the
    normalization blocker sits upstream of identity-release in the DAG,
    so it must rank strictly higher."""
    norm = Blocker(blocker_id="B-norm",
                   title="typed-axis normalization missing for 12K slugs",
                   stage="normalization", severity="P0", confidence=0.8,
                   age_h=48.0)
    ident = Blocker(blocker_id="B-ident",
                    title="OPN identity join stale for 378 pins",
                    stage="identity-release", severity="P0", confidence=0.8,
                    age_h=48.0)
    ranked = rank_blockers([ident, norm])
    assert ranked[0].blocker_id == "B-norm"
    assert len(downstream_of("normalization")) == \
        len(downstream_of("identity-release")) + 1


def test_extraction_blocker_outranks_normalization():
    ex = Blocker(blocker_id="B-extract", title="lane crashed",
                 stage="extraction", severity="P0", confidence=0.8,
                 age_h=48.0)
    norm = Blocker(blocker_id="B-norm", title="normalization gap",
                   stage="normalization", severity="P0", confidence=0.8,
                   age_h=48.0)
    ranked = rank_blockers([norm, ex])
    assert ranked[0].blocker_id == "B-extract"


def test_energy_doctrine_rejects_cloud_first_default(registry):
    """Retrieval-and-application, not storage: a proposal whose default
    is cloud-first must be flaggable using ONLY the registry text."""
    d = registry["operational_doctrine"]["energy_economics"].lower()
    proposal = {"name": "full-cloud power aisle run", "default": "cloud"}
    cloud_is_default = proposal["default"] == "cloud"
    requires_local_first = "never the default" in d
    contradiction = cloud_is_default and requires_local_first
    assert contradiction, "doctrine must flag cloud-first defaults"
