"""CURVE-07E regression + gate tests (R1-R9).

All assertions run against frozen fixtures and the v3 bundle manifest.
v1/v2 bundles must remain byte-identical (immutability), ambiguous
traces must refuse, cross-manufacturer claims require distinct canonical
families with aligned conditions and physics checks.
"""

from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.discovery.curves import load_reference_curves
from harness.electronics.curve_evidence import (
    compare_at_operating_point,
    query_operating_point,
)

PILOT = REPO / "tests/fixtures/gold/curve_evidence_pilot"
HANDOFF = REPO / "tests/fixtures/m4-handoff"

RATINGS = {"LM5161": 1.0, "SiC461": 10.0, "SiC462": 6.0, "SiC463": 4.0,
           "SiC464": 2.0}


def _curves():
    out = []
    for f in ("lm5161_p7.json", "vishay_sic46x_p14.json",
              "vishay_sic46x_p15.json", "vishay_sic46x_p16.json",
              "vishay_sic46x_p17.json"):
        out.extend(load_reference_curves([PILOT / f]))
    return out


class ImmutabilityTests(unittest.TestCase):
    def test_v1_v2_bundles_untouched(self):
        expected = {
            "curve_evidence_bundle.jsonl":
                "5cc1a6e41ad8",  # v1 release digest (manifest-pinned)
            "curve_evidence_bundle_v2.jsonl":
                "3b5658b629cc",
        }
        for name, digest in expected.items():
            manifest_name = ("release_manifest.json" if name.endswith(
                "bundle.jsonl") else "release_manifest_v2.json")
            manifest = json.loads((HANDOFF / manifest_name).read_text())
            self.assertIn(digest, manifest["release_id"])
            actual = hashlib.sha256(
                (HANDOFF / name).read_bytes()).hexdigest()
            self.assertEqual(actual, manifest["bundle_sha256"],
                             f"{name} must stay byte-identical")


class LegendBindingTests(unittest.TestCase):
    def test_ambiguous_unnamed_traces_refuse(self):
        amb = [c for c in _curves()
               if c.conditions.get("legend_ambiguity")]
        self.assertTrue(amb, "ambiguous traces must be flagged")
        for c in amb:
            r = query_operating_point(
                c, 0.5, required_conditions={"vin_v": 48.0,
                                             "vout_v": 5.0})
            self.assertEqual(r["status"], "not_comparable")
            self.assertEqual(r["reason"], "legend_ambiguity")

    def test_bound_traces_carry_legend_conditions(self):
        bound = [c for c in _curves()
                 if (c.series.get("name") or "").startswith("VIN = 48 V")]
        self.assertTrue(bound)
        for c in bound:
            self.assertEqual(c.conditions["keys"]["vin_v"], 48.0)

    def test_physics_vin_monotonicity_on_bound_lm5161(self):
        curves = [c for c in _curves()
                  if c.applies_to.get("part") == "LM5161"
                  and (c.conditions.get("keys") or {}).get("vin_v")
                  in (36.0, 48.0, 60.0)
                  and (c.conditions.get("keys") or {}).get("vout_v") == 5.0]
        by_vin = {(c.conditions["keys"]["vin_v"]): c for c in curves}
        self.assertEqual(set(by_vin), {36.0, 48.0, 60.0})
        for x in (0.1, 0.5, 0.9):
            vals = {}
            for vin, c in by_vin.items():
                r = query_operating_point(
                    c, x, required_conditions={"vin_v": vin,
                                               "vout_v": 5.0})
                if r["status"] == "ok":
                    vals[vin] = r["value"]
            ordered = [vals[v] for v in sorted(vals)]
            self.assertTrue(
                all(ordered[i] >= ordered[i + 1] - 1.5
                    for i in range(len(ordered) - 1)),
                f"efficiency must not rise with VIN at {x} A: {vals}")


class CrossManufacturerTests(unittest.TestCase):
    def test_cross_manufacturer_cell_exists_and_is_canonical(self):
        manifest = json.loads(
            (HANDOFF / "curve_evidence_bundle_v3_manifest.json").read_text())
        cross = [c for c in manifest["coverage_matrix"]
                 if c["comparison_levels"]
                 ["cross_manufacturer_comparisons"] >= 2]
        self.assertTrue(cross, "R2 requires a real cross-manufacturer cell")
        for cell in cross:
            self.assertGreaterEqual(len(cell["families"]), 2)
            self.assertEqual(cell["phenomenon"], "efficiency_vs_load")
        self.assertTrue(any(c["vin_v"] == 48.0 for c in cross),
                        "the 48 V -> 5 V cell must be cross-manufacturer")

    def test_comparison_at_half_amp_is_defensible(self):
        eligible = [
            c for c in _curves()
            if c.y_kind == "efficiency"
            and (c.conditions.get("keys") or {}).get("vin_v") == 48.0
            and (c.conditions.get("keys") or {}).get("vout_v") == 5.0
            and RATINGS.get(c.applies_to.get("part"), 99) >= 0.5
        ]
        res = compare_at_operating_point(
            eligible, 0.5,
            required_conditions={"vin_v": 48.0, "vout_v": 5.0})
        parts = {e["part"] for e in res["entries"]}
        self.assertIn("LM5161", parts)
        self.assertTrue(parts & {"SiC461", "SiC463", "SiC464"})
        vals = {e["part"]: e["value"] for e in res["entries"]}
        self.assertLess(vals["LM5161"],
                        max(v for p, v in vals.items()
                            if p.startswith("SiC")) - 5.0,
                        "cross-manufacturer delta must exceed uncertainty "
                        "by a wide margin")
        for e in res["entries"]:
            self.assertFalse(e.get("guarantee"))
            self.assertTrue(e["citation"]["document_sha256"])

    def test_hard_ratings_gate_before_curves(self):
        eligible = [
            c for c in _curves()
            if c.y_kind == "efficiency"
            and (c.conditions.get("keys") or {}).get("vin_v") == 48.0
            and RATINGS.get(c.applies_to.get("part"), 99) >= 3.0
        ]
        parts = {c.applies_to["part"] for c in eligible}
        self.assertNotIn("LM5161", parts, "1 A part cannot join a 3 A set")
        self.assertNotIn("SiC464", parts, "2 A part cannot join a 3 A set")


class CoverageMatrixTests(unittest.TestCase):
    def test_five_levels_never_blended(self):
        manifest = json.loads(
            (HANDOFF / "curve_evidence_bundle_v3_manifest.json").read_text())
        for cell in manifest["coverage_matrix"]:
            levels = cell["comparison_levels"]
            self.assertEqual(
                set(levels),
                {"same_configuration_observations",
                 "within_device_configurations",
                 "within_family_device_comparisons",
                 "cross_family_comparisons",
                 "cross_manufacturer_comparisons"})
            if levels["cross_manufacturer_comparisons"]:
                self.assertGreaterEqual(
                    levels["cross_family_comparisons"], 2)

    def test_admission_packets_present(self):
        manifest = json.loads(
            (HANDOFF / "curve_evidence_bundle_v3_manifest.json").read_text())
        packets = manifest["catalog_admission_packets"]
        self.assertEqual(len(packets), 4)
        for p in packets:
            self.assertIn("identity_provenance", p["identity"])
            self.assertTrue(p["identity"]["identity_provenance"]
                            ["datasheet_sha256"])
            self.assertIn("request", p)


class EvidenceRequestTests(unittest.TestCase):
    def test_gap_request_outcomes_recorded(self):
        manifest = json.loads(
            (HANDOFF / "curve_evidence_bundle_v3_manifest.json").read_text())
        outcomes = {o["request"]: o["status"]
                    for o in manifest["evidence_request_outcomes"]
                    ["outcomes"]}
        self.assertEqual(outcomes["second family efficiency curve at "
                                  "48 V -> 5 V"], "satisfied")
        self.assertEqual(outcomes["catalog admission for Vishay SiC46x"],
                         "prepared_not_applied")
        self.assertEqual(outcomes["human adjudication of curve evidence"],
                         "blocked_pending_human")


if __name__ == "__main__":
    unittest.main()
