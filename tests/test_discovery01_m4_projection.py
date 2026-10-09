"""CURVE-08B adapter projection regression tests (DISCOVERY-01 final
parity). The projection must map M4's 08B envelope onto M5's contract
explicitly, report every renamed/absent field, and never invent facts."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.search.m4_client import (  # noqa: E402
    M4_08B_CONTRACT,
    project_m4_08b,
)

LIVE_SHAPE = {
    "contract": M4_08B_CONTRACT,
    "contract_kind": "experimental_adapter_over_curve_engine",
    "engine": {"contract": "fae-curve-decisions-v1",
               "engine_revision": "p0-fixes-1"},
    "comparator_fingerprint": {
        "vendored_comparator": "curve-evidence-bundle-v2-3b5658b629cc",
        "engine_revision": "p0-fixes-1",
        "evidence_bundle_sha256": "35dbd73c"},
    "evidence_releases": {
        "evidence_release": "curve-evidence-bundle-v2-3b5658b629cc",
        "evidence_bundle_sha256": "35dbd73c"},
    "review_state": "experimental_pending_human_review",
    "candidate_identity": {
        "grain_disclosure": {"actual_grain": "cross_manufacturer",
                             "families": ["LM5161", "SiC46x"]},
        "ranked": [{"part": "SiC461", "family": "SiC46x",
                    "series": "VIN = 48 V, L = 10 uH",
                    "state": "preferred_under_conditions",
                    "evidence_id": "curve-x", "value": 93.94, "unit": "%"}],
        "unqualified_observations": [],
        "valued_candidates": [],
        "identity_basis": "engine_envelope_parts_and_series_only"},
    "eligibility": {
        "counts": {"eligible": 1777, "eliminated": 0, "unresolved": 0},
        "denominator": {"catalog_total": 5040},
        "eligible_parts": ["LM5161"],
        "eliminated_count": 0,
        "unresolved_count": 0,
        "eligibility_basis": "engine_qualification_only"},
    "canonical_envelope": {
        "qualification": {"eliminated": [{"part": "X", "reasons": []}],
                          "unresolved": [{"part": "Y"}]},
        "preference": {"ranking": []}},
}


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.answer, self.mismatches = project_m4_08b(
            json.loads(json.dumps(LIVE_SHAPE)))

    def test_projects_m5_schema_and_releases(self):
        self.assertEqual(self.answer["schema"],
                         "harness.electronics-decision-answer.v1")
        self.assertEqual(self.answer["evidence_release"],
                         "curve-evidence-bundle-v2-3b5658b629cc")
        self.assertEqual(self.answer["bundle_sha256"], "35dbd73c")
        self.assertEqual(self.answer["projected_from"], M4_08B_CONTRACT)

    def test_eligibility_and_comparison_mapped(self):
        self.assertEqual(self.answer["hard_eligibility"]["eligible"],
                         ["LM5161"])
        self.assertEqual(len(self.answer["hard_eligibility"]
                             ["ineligible"]), 1)
        entries = self.answer["comparison"]["condition_matched_entries"]
        self.assertEqual(entries[0]["part"], "SiC461")
        self.assertEqual(entries[0]["value"], 93.94)
        self.assertEqual(
            self.answer["comparison"]["comparison_level"]
            ["actual_grain"], "cross_manufacturer")

    def test_every_rename_is_reported_not_silenced(self):
        blob = " ".join(self.mismatches)
        for token in ("schema", "bundle_sha256", "hard_eligibility",
                      "comparison", "counts", "interpolation",
                      "approximate"):
            self.assertIn(token, blob)

    def test_no_facts_invented(self):
        entries = self.answer["comparison"]["condition_matched_entries"]
        self.assertNotIn("interpolation", entries[0],
                         "M4 publishes no interpolation disclosure; M5 "
                         "must not fabricate one")
        self.assertEqual(
            self.answer["comparison"]["approximate_scenario_entries"], [])
        self.assertEqual(self.answer["review_state"],
                         "experimental_pending_human_review")

    def test_non_08b_payloads_untouched_path(self):
        # a native M5 answer must not enter the projection
        native = {"schema": "harness.electronics-decision-answer.v1"}
        self.assertNotIn("canonical_envelope", native)


if __name__ == "__main__":
    unittest.main()
