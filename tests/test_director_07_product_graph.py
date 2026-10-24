"""DIRECTOR-07 product graph — issue #7 (2026-10-20 amendments) acceptance.

Covers the amendment's proof requirements:
- contract-conflict detection from real-shaped observations (one cause,
  one record; symptoms never multiply causes)
- deterministic ranking that CHANGES with facts (upstream failure,
  recovery, contract mismatch), persisted why-ranked
- restart/tick stability: reconcile dedupes, phases never regress
- truth-class discipline: a GitHub comment NEVER promotes trusted ack;
  stale evidence degrades to UNKNOWN, never green
- observation vs execution separation: adding a repo to project.yaml
  grows OBSERVATION scope without code edits and WITHOUT expanding the
  gh_workloop execution allowlist
"""
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer import gh_workloop
from director_consumer.consumer import DirectorConsumer, RunReport
from director_consumer.dependency_inventory import (
    coverage_report, gap_matrix, lane_inventory, load_observation_scope,
    normalize_repo)
from director_consumer.product_graph import (
    TRUTH_GITHUB_OBSERVED, TRUTH_INDEPENDENTLY_TESTED,
    TRUTH_TRUSTED_MAILBOX_ACK,
    TRUTH_UNKNOWN, Blocker, ContractAssumption, Evidence,
    ProductGraphState, detect_contract_conflicts, paths_compatible,
    rank_blockers, record_phase_evidence, reconcile, stale_to_unknown)

NOW = 1792500000.0


# ------------------------------------------------- real-shaped fixtures
# Shapes mirror the actual sources (gathered read-only 2026-10-20):
# FE designwins-v2 branch agent/golden-path-01 src/lib/labs/decision-api.ts
# cr-core outcited.com #103 + route tree; see scripts/dw_contract_scan.py

def fe_assumption(now=str(int(NOW))):
    return ContractAssumption(
        lane="m4", endpoint_kind="decision-api", method="POST",
        path="{base}/v1/engineering/decisions",
        envelope="harness.electronics-decision-api.v1 (per cr-core #103)",
        release_pin="curve-evidence-bundle-v3-powerdata01-74cc003023f9",
        reachability="proven host-local against harness :8794 only",
        evidence=Evidence(
            locator="designwins-v2:agent/golden-path-01:"
                    "src/lib/labs/decision-api.ts",
            truth_class=TRUTH_GITHUB_OBSERVED, as_of=now))


def be_assumption(now=str(int(NOW))):
    return ContractAssumption(
        lane="cr-core", endpoint_kind="decision-api", method="POST",
        path="/api/designwins/v1/engineering/decisions",
        envelope="harness.electronics-decision-api.v1",
        release_pin="unknown",
        reachability="no authenticated FE-reachable facade confirmed",
        evidence=Evidence(
            locator="outcited.com:main:route-tree + issue #103 task 2",
            truth_class=TRUTH_GITHUB_OBSERVED, as_of=now))


IMPLEMENTED_NO_ENGINEERING = [
    "/api/designwins/v1/aisles",
    "/api/designwins/v1/aisles/[kim_slug]",
    "/api/designwins/v1/aisles/[kim_slug]/anchors",
]


# ------------------------------------------------- path compatibility

def test_paths_compatible_matrix():
    assert paths_compatible("{base}/v1/engineering/decisions",
                            "/v1/engineering/decisions")
    assert not paths_compatible("{base}/v1/engineering/decisions",
                                "/api/designwins/v1/engineering/decisions")
    assert paths_compatible("/api/designwins/v1/engineering/decisions",
                            "/api/designwins/v1/engineering/decisions")


# ------------------------------------------------- conflict detection

def test_conflict_detection_two_records_for_the_real_case():
    """The first acceptance test: FE composition vs cr-core asserted
    facade path -> path mismatch AND asserted-but-not-implemented."""
    conflicts = detect_contract_conflicts(
        [fe_assumption(), be_assumption()],
        IMPLEMENTED_NO_ENGINEERING, now=NOW)
    kinds = sorted(c.kind for c in conflicts)
    assert kinds == ["contract-conflict", "missing-implementation"]
    by_kind = {c.kind: c for c in conflicts}
    mm = by_kind["contract-conflict"]
    assert len(mm.sides) == 2
    lanes = {s["lane"] for s in mm.sides}
    assert lanes == {"m4", "cr-core"}
    for s in mm.sides:
        assert s["evidence"]["locator"]  # every side is source-pinned
    mi = by_kind["missing-implementation"]
    assert "/api/designwins/v1/engineering/decisions" in mi.title


def test_one_cause_one_record_despite_repeated_scans():
    """Symptoms multiply, causes do not: N repeated detections with the
    same facts produce ONE record per cause."""
    for _ in range(5):
        conflicts = detect_contract_conflicts(
            [fe_assumption(), be_assumption()],
            IMPLEMENTED_NO_ENGINEERING, now=NOW)
    assert len(conflicts) == 2
    fps = [c.cause_fingerprint for c in conflicts]
    assert len(set(fps)) == 2


def test_recovery_disappears_from_board():
    """When cr-core implements the route AND paths align, the conflict
    cause is gone — reconcile drops it (resolution evidence lives in
    phase tracking while the cause is present)."""
    conflicts = detect_contract_conflicts(
        [fe_assumption(),
         ContractAssumption(
             lane="cr-core", endpoint_kind="decision-api", method="POST",
             path="/v1/engineering/decisions",
             evidence=Evidence(locator="outcited.com:main:routes",
                               truth_class=TRUTH_GITHUB_OBSERVED,
                               as_of=str(int(NOW))))],
        ["/v1/engineering/decisions"], now=NOW)
    assert conflicts == []


# ------------------------------------------------- reconcile / restart

def test_reconcile_preserves_identity_and_dedupes():
    c1 = detect_contract_conflicts(
        [fe_assumption(), be_assumption()], IMPLEMENTED_NO_ENGINEERING,
        now=NOW)
    state1 = reconcile(None, c1, now=NOW)
    # second tick (restart in between: state round-trips through JSON)
    state1.save(Path("/tmp") if False else Path("."))  # no-op guard
    loaded = ProductGraphState()
    loaded.conflicts = json.loads(json.dumps(state1.conflicts))
    c2 = detect_contract_conflicts(
        [fe_assumption(), be_assumption()], IMPLEMENTED_NO_ENGINEERING,
        now=NOW + 3600)
    state2 = reconcile(loaded, c2, now=NOW + 3600)
    assert len(state2.conflicts) == len(state1.conflicts) == 2
    for a, b in zip(sorted(state1.conflicts, key=lambda x: x["conflict_id"]),
                    sorted(state2.conflicts, key=lambda x: x["conflict_id"])):
        assert a["conflict_id"] == b["conflict_id"]
        assert a["created_at"] == b["created_at"]   # identity preserved


def test_phase_never_regresses_and_comments_never_ack():
    conflicts = detect_contract_conflicts(
        [fe_assumption(), be_assumption()], IMPLEMENTED_NO_ENGINEERING,
        now=NOW)
    state = reconcile(None, conflicts, now=NOW)
    cid = state.conflicts[0]["conflict_id"]

    # a GitHub owner reply moves proposed -> owner-review ...
    ok = record_phase_evidence(
        state, cid, "owner-review",
        Evidence(locator="issue#comment", truth_class=TRUTH_GITHUB_OBSERVED),
        now=NOW)
    assert ok and state.conflicts[0]["phase"] == "owner-review"

    # ... but can NEVER promote to agreed-contract / trusted ack
    for phase in ("agreed-contract", "tests", "independently-verified"):
        assert not record_phase_evidence(
            state, cid, phase,
            Evidence(locator="issue#comment",
                     truth_class=TRUTH_GITHUB_OBSERVED), now=NOW)

    # only trusted-transport acks or independent tests cross the gate
    assert record_phase_evidence(
        state, cid, "agreed-contract",
        Evidence(locator="mailbox:ack-recorded",
                 truth_class=TRUTH_TRUSTED_MAILBOX_ACK), now=NOW)

    # no phase regression even with valid evidence class
    assert not record_phase_evidence(
        state, cid, "owner-review",
        Evidence(locator="x", truth_class=TRUTH_TRUSTED_MAILBOX_ACK),
        now=NOW)

    # unknown conflict id -> no-op
    assert not record_phase_evidence(
        state, "conflict:doesnotexist", "owner-review",
        Evidence(locator="x", truth_class=TRUTH_GITHUB_OBSERVED))


# ------------------------------------------------- ranking

def mk_blocker(stage, severity="P1", age_h=10.0, confidence=0.7,
               actionable=True, bid=None):
    return Blocker(blocker_id=bid or f"b:{stage}", title=f"blocker {stage}",
                   stage=stage, severity=severity, age_h=age_h,
                   confidence=confidence, safe_actionable=actionable,
                   smallest_next_action="do the thing")


def test_ranking_deterministic_and_prefers_upstream():
    up = mk_blocker("store-api")        # blocks evidence-wire->fe->test
    down = mk_blocker("fe-journeys")    # blocks only product-test
    r1 = rank_blockers([down, up])
    r2 = rank_blockers([down, up])
    assert [b.blocker_id for b in r1] == [b.blocker_id for b in r2]
    assert r1[0].blocker_id == "b:store-api"
    assert "downstream(" in r1[0].why_ranked


def test_ranking_changes_with_facts_failure_and_recovery():
    base = mk_blocker("store-api")
    failing = mk_blocker("store-api", severity="P0", age_h=100.0,
                         confidence=0.9)
    recovered = mk_blocker("store-api", severity="P2", age_h=1.0,
                           confidence=0.4, actionable=False)
    for b in (failing, base, recovered):
        b.compute_score()
    assert failing.score > base.score > recovered.score


def test_ranking_tiebreak_deterministic():
    a = mk_blocker("store-api", bid="b:a")
    b = mk_blocker("store-api", bid="b:b")
    out = rank_blockers([b, a])
    assert [x.blocker_id for x in out] == ["b:a", "b:b"]


# ------------------------------------------------- truth-class honesty

def test_stale_evidence_degrades_to_unknown_never_green():
    fresh = [{"truth_class": TRUTH_GITHUB_OBSERVED, "as_of": str(NOW)}]
    old = [{"truth_class": TRUTH_GITHUB_OBSERVED,
            "as_of": str(NOW - 48 * 3600)}]
    assert stale_to_unknown(list(fresh), NOW)[0]["truth_class"] == \
        TRUTH_GITHUB_OBSERVED
    assert stale_to_unknown(old, NOW)[0]["truth_class"] == TRUTH_UNKNOWN
    assert stale_to_unknown([{"truth_class": "HOST_REPORTED", "as_of": ""}],
                            NOW)[0]["truth_class"] == TRUTH_UNKNOWN


# ------------------------------------------------- inventory / scope

def repo_yaml(repos):
    return repos


@pytest.fixture
def project_yaml(tmp_path):
    p = tmp_path / "project.yaml"

    def write(repos):
        p.write_text("\n".join(
            ["project_id: designwins", "repositories:"] +
            [f"  - {r}" for r in repos]))
        return p
    return write


def test_scope_grows_by_config_not_code(project_yaml):
    """Adding a registered dependency to project.yaml makes it visible
    with zero code edits — the #7 acceptance for dynamic inventory."""
    p = project_yaml(["github.com/outcited/outcitedharness"])
    assert load_observation_scope(p) == ["outcited/outcitedharness"]
    project_yaml(["github.com/outcited/outcitedharness",
                  "github.com/outcited/outcited.com",
                  "github.com/outcited/MFGDosierfactory"])
    assert load_observation_scope(p) == [
        "outcited/outcitedharness", "outcited/outcited.com",
        "outcited/MFGDosierfactory"]


def test_normalize_repo_variants():
    assert normalize_repo("github.com/outcited/outcited.com") == \
        "outcited/outcited.com"
    assert normalize_repo("outcited/MFGDosierfactory") == \
        "outcited/MFGDosierfactory"
    assert normalize_repo("https://github.com/outcited/designwins-v2/") == \
        "outcited/designwins-v2"


def test_observation_vs_execution_separation(project_yaml):
    """cr-core/factory repos: readable read-only = NOT_INGESTED facts,
    never executable assignments; the gh_workloop allowlist is intact."""
    p = project_yaml(["github.com/outcited/outcitedharness",
                      "github.com/outcited/designwins-v2",
                      "github.com/outcited/outcited.com",
                      "github.com/outcited/MFGDosierfactory"])
    cov = coverage_report(p, readable={
        "outcited/outcitedharness": True,
        "outcited/designwins-v2": True,
        "outcited/outcited.com": True,
        "outcited/MFGDosierfactory": True})
    by = {c.repo: c for c in cov}
    assert by["outcited/outcitedharness"].status == "OBSERVED"
    assert by["outcited/outcitedharness"].execution_ingest is True
    assert by["outcited/outcited.com"].status == "NOT_INGESTED"
    assert by["outcited/outcited.com"].execution_ingest is False
    assert by["outcited/MFGDosierfactory"].status == "NOT_INGESTED"
    # the execution allowlist is unchanged — code-level enforcement holds
    assert "outcited/outcited.com" not in gh_workloop.ALLOWLISTED_REPOS
    with pytest.raises(PermissionError):
        gh_workloop.fetch_open_issues("outcited/outcited.com")


def test_unknown_access_is_an_explicit_gap(project_yaml):
    p = project_yaml(["github.com/outcited/outcitedharness",
                      "github.com/outcited/some-private-repo"])
    cov = coverage_report(p, readable={"outcited/outcitedharness": True,
                                       "outcited/some-private-repo": False})
    gm = gap_matrix(cov, hosts={"mac-mini": "unknown", "seven": "online"})
    assert gm["repos_unknown_access"] == ["outcited/some-private-repo"]
    assert gm["hosts_unreachable"] == ["mac-mini"]
    assert gm["repos_observed"] == 1


def test_lane_inventory_is_proposed_not_confirmed():
    lanes = {l["lane"]: l for l in lane_inventory()}
    assert set(lanes) == {"m5", "cr-core", "m4", "pdf-factory", "seven"}
    assert all(not l["confirmed"] for l in lanes.values())
    assert "evidence-correctness-at-handoff" in lanes["m5"]["owns"]


# ------------------------------------------------- consumer tick

def consumer_shell(workspace):
    c = DirectorConsumer.__new__(DirectorConsumer)
    c.workspace = workspace
    return c


def write_observations(workspace, implemented=IMPLEMENTED_NO_ENGINEERING):
    obs = {
        "as_of": NOW,
        "assumptions": [
            {"lane": "m4", "endpoint_kind": "decision-api",
             "method": "POST",
             "path": "{base}/v1/engineering/decisions",
             "envelope": "harness.electronics-decision-api.v1",
             "release_pin": "curve-evidence-bundle-v3-powerdata01-74cc0030",
             "reachability": "host-local :8794 only",
             "evidence": {"locator": "designwins-v2:agent/golden-path-01:"
                                     "src/lib/labs/decision-api.ts",
                          "truth_class": TRUTH_GITHUB_OBSERVED,
                          "as_of": str(int(NOW))}},
            {"lane": "cr-core", "endpoint_kind": "decision-api",
             "method": "POST",
             "path": "/api/designwins/v1/engineering/decisions",
             "envelope": "harness.electronics-decision-api.v1",
             "release_pin": "unknown",
             "reachability": "no FE-reachable facade confirmed",
             "evidence": {"locator": "outcited.com:main + #103",
                          "truth_class": TRUTH_GITHUB_OBSERVED,
                          "as_of": str(int(NOW))}},
        ],
        "implemented_paths": implemented,
    }
    (workspace / "product-graph-observations.json").write_text(
        json.dumps(obs))


def test_consumer_tick_two_cycles_no_duplicates(tmp_path):
    """Two unattended ticks + a simulated restart (new consumer, same
    workspace): stable records, no event multiplication."""
    write_observations(tmp_path)
    fired = []

    def record_feed(feed_self, feed, events, cursor, report, markers=None):
        fired.extend(events)

    c1 = consumer_shell(tmp_path)
    with patch.object(DirectorConsumer, "_derived_feed", record_feed):
        r1 = RunReport()
        c1.run_product_graph(r1)
        assert r1.product_graph_conflicts == 2
        assert r1.product_graph_new == 2
        assert len(fired) == 2

        # tick 2: same facts — no new conflicts, no new events
        r2 = RunReport()
        c1.run_product_graph(r2)
        assert r2.product_graph_conflicts == 2
        assert r2.product_graph_new == 0
        assert len(fired) == 2

    # restart: fresh consumer instance, same workspace state
    c2 = consumer_shell(tmp_path)
    with patch.object(DirectorConsumer, "_derived_feed", record_feed):
        r3 = RunReport()
        c2.run_product_graph(r3)
        assert r3.product_graph_conflicts == 2
        assert r3.product_graph_new == 0
        assert len(fired) == 2

    # upstream recovery: route implemented + paths aligned -> board clears
    write_observations(tmp_path, implemented=list(IMPLEMENTED_NO_ENGINEERING)
                       + ["/v1/engineering/decisions"])
    # (aligned assumption set)
    obs = json.loads(
        (tmp_path / "product-graph-observations.json").read_text())
    obs["assumptions"][1]["path"] = "/v1/engineering/decisions"
    (tmp_path / "product-graph-observations.json").write_text(
        json.dumps(obs))
    c3 = consumer_shell(tmp_path)
    with patch.object(DirectorConsumer, "_derived_feed", record_feed):
        r4 = RunReport()
        c3.run_product_graph(r4)
        assert r4.product_graph_conflicts == 0
        assert r4.product_graph_new == 0


def test_consumer_tick_no_observations_is_noop(tmp_path):
    c = consumer_shell(tmp_path)
    r = RunReport()
    c.run_product_graph(r)      # must not raise
    assert r.product_graph_conflicts == 0
