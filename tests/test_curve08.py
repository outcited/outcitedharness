"""CURVE-08 frozen regression suite — candidate model, hard eligibility,
condition-matched comparison, interpolation disclosure, five-level
preservation, and the 48 V -> 5 V verification targets."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.electronics.candidates import (  # noqa: E402
    build_power_candidates,
    candidate_counts,
    load_default_inputs,
)
from harness.electronics.decision_engine import answer_question  # noqa: E402
from harness.electronics.eligibility import (  # noqa: E402
    evaluate_candidate,
    thermal_feasibility_note,
)

Q_48_5 = {
    "requirements": {"vin_min": 48.0, "vin_max": 48.0, "iout_min": 0.5,
                     "vout": 5.0},
    "conditions": {"vin_v": 48.0, "vout_v": 5.0},
    "operating_point": {"x": 0.5, "unit": "A"},
    "phenomenon": "efficiency_vs_load",
}
Q_48_5_3A = {**Q_48_5,
             "requirements": {**Q_48_5["requirements"], "iout_min": 3.0}}


def _candidates():
    catalog, bundle, packets, ratings = load_default_inputs(REPO)
    return list(build_power_candidates(catalog, bundle, packets,
                                       ratings).values()), bundle


class CandidateModelTests(unittest.TestCase):
    def test_evidence_rows_never_become_candidates(self):
        candidates, bundle = _candidates()
        counts = candidate_counts(candidates)
        self.assertEqual(counts["evidence_rows_attached"], len(bundle))
        self.assertLess(counts["total_candidates"], len(bundle),
                        "123 evidence rows must collapse to few candidates")
        sic = [c for c in candidates if c.family == "SiC46x"]
        self.assertTrue(sic)
        self.assertTrue(all(c.grain == "device" for c in sic),
                        "every SiC46x row names its device")
        family_grain = [c for c in candidates if c.grain == "family"]
        self.assertTrue(family_grain,
                        "family-scoped rows attach to a family candidate")
        for c in family_grain:
            self.assertIsNone(c.device)
            self.assertTrue(c.membership.get("devices"))

    def test_facet02_contract_mapping(self):
        candidates, _ = _candidates()
        facet = candidates[0].to_facet_candidate()
        for key in ("opn", "vendor_raw", "vendor_canonical", "category",
                    "subcategory", "attributes", "evidence_unit_ids",
                    "evidence_grade_units", "discovery_only_units"):
            self.assertIn(key, facet)

    def test_candidate_identity_independent_of_document_count(self):
        candidates, bundle = _candidates()
        half = build_power_candidates(
            *([load_default_inputs(REPO)[0], bundle[:40]] +
              list(load_default_inputs(REPO)[2:])))
        ids_full = {c.candidate_id for c in candidates
                    if c.device == "SiC462"}
        ids_half = {c.candidate_id for c in half.values()
                    if c.device == "SiC462"}
        self.assertEqual(ids_full, ids_half)


class EligibilityTests(unittest.TestCase):
    def test_lm5161_cannot_join_three_amp_shortlist(self):
        candidates, _ = _candidates()
        lm = next(c for c in candidates if c.device == "LM5161")
        verdict = evaluate_candidate(lm, {"iout_min": 3.0})
        self.assertEqual(verdict["verdict"], "ineligible")
        rule = verdict["rules"][0]
        self.assertEqual(rule["rule"], "iout_min")
        self.assertEqual(rule["rated"], 1.0)
        self.assertIn("evidence", rule)

    def test_sic464_cannot_join_three_amp_shortlist(self):
        candidates, _ = _candidates()
        sic = next(c for c in candidates if c.device == "SiC464")
        verdict = evaluate_candidate(sic, {"iout_min": 3.0})
        self.assertEqual(verdict["verdict"], "ineligible")
        self.assertEqual(verdict["rules"][0]["rated"], 2.0)

    def test_missing_ratings_unknown_not_failure(self):
        candidates, _ = _candidates()
        family = next(c for c in candidates if c.grain == "family")
        verdict = evaluate_candidate(
            family, {"vin_min": 48.0, "iout_min": 0.5})
        self.assertEqual(verdict["verdict"], "unknown")

    def test_thermal_feasibility_never_confirmed_by_curves(self):
        candidates, _ = _candidates()
        sic = next(c for c in candidates if c.device == "SiC462")
        note = thermal_feasibility_note(sic)
        self.assertIn(note["thermal_feasibility"],
                      ("unconfirmed", "curve_evidence_advisory"))
        self.assertNotEqual(note["thermal_feasibility"], "confirmed")

    def test_hard_eligibility_ignores_curves(self):
        catalog, bundle, packets, ratings = load_default_inputs(REPO)
        with_curves = build_power_candidates(catalog, bundle, packets,
                                             ratings)
        without = build_power_candidates(catalog, [], packets, ratings)
        a = evaluate_candidate(with_curves["cand-" + next(
            c.candidate_id[5:] for c in with_curves.values()
            if c.device == "LM5161")], {"iout_min": 3.0})
        b = evaluate_candidate(next(c for c in without.values()
                                    if c.device == "LM5161"),
                               {"iout_min": 3.0})
        self.assertEqual(a["verdict"], b["verdict"])


class DecisionEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.answer = answer_question(Q_48_5)
        cls.answer_3a = answer_question(Q_48_5_3A)

    def test_verification_targets_regenerated_not_hardcoded(self):
        values = {e["part"]: round(e["value"], 2)
                  for e in self.answer["comparison"]
                  ["condition_matched_entries"]}
        self.assertEqual(values.get("LM5161"), 77.95)
        self.assertEqual(values.get("SiC461"), 93.94)
        self.assertEqual(values.get("SiC463"), 92.96)
        self.assertEqual(values.get("SiC464"), 92.95)

    def test_every_entry_discloses_interpolation_and_state(self):
        for e in self.answer["comparison"]["condition_matched_entries"]:
            interp = e["interpolation"]
            self.assertEqual(len(interp["supporting_points"]), 2)
            self.assertTrue(interp["method"])
            self.assertIn("typical printed-curve value",
                          interp["limitations"][0])
            self.assertFalse(e["guarantee"])
            self.assertEqual(e["adjudication_state"],
                             "human_review_pending")
            self.assertTrue(e["source"]["document_sha256"])

    def test_five_level_classification_preserved(self):
        level = self.answer["comparison"]["comparison_level"]
        self.assertEqual(level["achieved_level"], "cross_manufacturer")
        self.assertIn("SiC46x", level["families"])
        self.assertIn("LM5161", level["families"])

    def test_within_family_question_not_overclaimed(self):
        ans = answer_question({
            "requirements": {"vin_min": 24.0, "vin_max": 24.0,
                             "iout_min": 0.5, "vout": 5.0},
            "conditions": {"vin_v": 24.0, "vout_v": 5.0},
            "operating_point": {"x": 0.5, "unit": "A"},
            "phenomenon": "efficiency_vs_load"})
        level = ans["comparison"]["comparison_level"]
        self.assertIn(level["achieved_level"],
                      ("cross_family", "within_family"))
        self.assertNotEqual(level["achieved_level"], "cross_manufacturer")

    def test_three_amp_hardware_gates(self):
        parts = {e["part"] for e in self.answer_3a["hard_eligibility"]
                 ["ineligible"]}
        self.assertIn("LM5161", parts)
        self.assertIn("SiC464", parts)
        ranked = {e["part"] for e in self.answer_3a["comparison"]
                  ["condition_matched_entries"]}
        self.assertNotIn("LM5161", ranked)
        self.assertNotIn("SiC464", ranked)

    def test_no_extrapolation_beyond_support(self):
        ans = answer_question({**Q_48_5,
                               "operating_point": {"x": 8.0, "unit": "A"}})
        parts = {e["part"] for e in ans["comparison"]
                 ["condition_matched_entries"]}
        self.assertNotIn("LM5161", parts, "LM5161 support ends at 0.99 A")
        avail = [a for a in ans["evidence_availability"].values()
                 if a["state"] == "non_comparable"]
        self.assertTrue(avail)

    def test_approximate_mode_labeled_separately(self):
        exact = self.answer["comparison"]["condition_matched_entries"]
        approx = answer_question({**Q_48_5, "approximate": True})
        scenario = approx["comparison"]["approximate_scenario_entries"]
        self.assertTrue(scenario,
                        "scenario mode must surface mismatched evidence")
        for s in scenario:
            self.assertEqual(s["mode"], "approximate_scenario")
            self.assertIn("NOT condition-matched", s["note"])
            self.assertTrue(s.get("mismatch") or s.get("missing")
                            or s.get("reason"))
        matched = approx["comparison"]["condition_matched_entries"]
        self.assertEqual({e["evidence_id"] for e in matched},
                         {e["evidence_id"] for e in exact},
                         "approximate mode must not alter matched evidence")

    def test_counts_separate_evidence_from_choices(self):
        counts = self.answer["counts"]
        self.assertEqual(counts["evidence_rows_attached"], 123)
        self.assertLess(counts["total_candidates"], 123)
        self.assertGreater(counts["candidates_missing_curve_evidence"], 0)
        self.assertGreater(counts["candidates_requiring_verification"], 0)


if __name__ == "__main__":
    unittest.main()
