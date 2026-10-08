"""CURVE-05B tests: comparability matrix laws and the A/B/C discovery
validation gates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_comparability_matrix import (
    build,
    condition_cell,
    load_verified_bundle,
)
from scripts.run_abc_discovery import run

RESULTS = Path(__file__).parent.parent / "results/curve-05b"


@pytest.fixture(scope="module")
def matrix():
    return build()


@pytest.fixture(scope="module")
def abc():
    return run()


def test_bundle_is_sha_verified_and_fail_closed(tmp_path, monkeypatch):
    rows, manifest = load_verified_bundle()
    assert len(rows) == manifest["rows"]
    # tampered bundle must refuse to load
    import hashlib

    real = Path(
        "tests/fixtures/m4-handoff/curve_evidence_bundle.jsonl"
    ).read_bytes()
    tampered = tmp_path / "bundle.jsonl"
    tampered.write_text(real.decode()[:-20] + "0\n")
    monkeypatch.setattr(
        "scripts.build_comparability_matrix.BUNDLE", tampered
    )
    with pytest.raises(SystemExit):
        load_verified_bundle()


def test_matrix_conflicted_members_never_support_cells(matrix):
    for scenario in matrix["cross_family_scenarios"] + \
            matrix["single_family_scenarios"]:
        member_ids = {m["evidence_id"]
                      for members in scenario["series"].values()
                      for m in members}
        conflicted_ids = {m["evidence_id"]
                          for m in scenario["conflicted_members"]}
        assert not (member_ids & conflicted_ids)


def test_matrix_cross_family_cells_have_shared_support(matrix):
    cross = matrix["cross_family_scenarios"]
    assert cross, "at least one genuine cross-family cell exists"
    for scenario in cross:
        assert len(scenario["families"]) >= 2
        support = scenario["shared_support"]
        assert support and support["max"] > support["min"]


def test_matrix_context_keys_do_not_widen_cells(matrix):
    # device-rating keys are context, never cell identity
    for scenario in matrix["cross_family_scenarios"]:
        assert "iout_a" not in scenario["conditions"]


def test_abc_bundle_release_pinned(abc):
    manifest = json.loads(
        Path("tests/fixtures/m4-handoff/release_manifest.json").read_text()
    )
    assert abc["bundle_release"] == manifest["release_id"]
    assert abc["bundle_sha256"] == manifest["bundle_sha256"]


def test_abc_cases_carry_every_required_field(abc):
    required = ("case", "intent", "knife_activated", "A_cohort",
                "B_cohort", "C_evidence", "defensibility",
                "missing_evidence")
    for case in abc["cases"]:
        for field in required:
            assert case.get(field), f"{case['case']}: missing {field}"
        assert case["defensibility"]["verdict"]
        assert case["defensibility"]["caveats"]
        assert case["missing_evidence"]


def test_abc_no_hard_eliminations_from_curves(abc):
    for case in abc["cases"]:
        for verdict in case["A_cohort"].values():
            assert verdict in ("PASS", "FAIL", "UNKNOWN")


def test_abc_ranking_changes_cite_evidence(abc):
    def walk(node):
        if isinstance(node, dict):
            yield node
            for v in node.values():
                yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)

    rows = [json.loads(line) for line in Path(
        "tests/fixtures/m4-handoff/curve_evidence_bundle.jsonl"
    ).read_text().splitlines() if line.strip()]
    known_ids = {r["evidence_id"] for r in rows}
    cited = 0
    for case in abc["cases"]:
        for node in walk(case["C_evidence"]):
            if isinstance(node.get("value"), (int, float)) or \
                    node.get("status") == "ok":
                evidence_id = node.get("evidence_id") or \
                    node.get("curve_id")
                assert evidence_id in known_ids, \
                    f"uncited value in {case['case']}"
                assert node.get("guarantee") is False or \
                    "guarantee" not in node
                cited += 1
    assert cited >= 20, "every consequential number cites bundle evidence"


def test_abc_refusals_reasoned(abc):
    def walk(node):
        if isinstance(node, dict):
            yield node
            for v in node.values():
                yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)

    for case in abc["cases"]:
        for node in walk(case["C_evidence"]):
            if node.get("status") in ("not_comparable", "not_usable",
                                      "out_of_range"):
                assert node.get("reason"), \
                    f"unreasoned refusal in {case['case']}"


def test_cross_family_efficiency_not_overclaimed(abc):
    # the honest position: no cross-family efficiency ranking today
    case1 = next(c for c in abc["cases"] if c["case"].startswith("48V"))
    assert case1["C_evidence"]["winners"]["efficiency_3A"] == ["SiC462"]
    assert len(case1["C_evidence"]["winners"][
        "thermal_headroom_3A"]) >= 2
    assert case1["C_evidence"]["refused_conflicted_evidence"]
