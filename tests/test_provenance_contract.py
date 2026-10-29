import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from provenance_contract import (
    qa_claim, qa_payload, unit_consistent, correct_rating_class,
    is_thermal_field, has_package_key, has_doc_variant,
)
import unittest

class TestUnitDimensionalConsistency(unittest.TestCase):
    def test_3e6_microamp_is_3_picoamp_off_by_meg(self):
        self.assertFalse(unit_consistent(3e-6, "µa", 3.0, "µA"),
                         "3e-6 µa = 3 pA ≠ 3.0 µA (off by 10^6)")

    def test_3e6_amp_is_3_microamp_correct(self):
        self.assertTrue(unit_consistent(3e-6, "A", 3.0, "µA"),
                        "3e-6 A = 3.0 µA ✓")

    def test_unitless_matches_unitless(self):
        self.assertIsNone(unit_consistent("foo", None, "foo", None))

    def test_dimension_mismatch_never_consistent(self):
        self.assertFalse(unit_consistent(40, "V", 43.2, "°C/W"))

class TestThermalRating(unittest.TestCase):
    def test_thermal_is_typical_not_rated(self):
        self.assertEqual(correct_rating_class("rtheta_ja_c_per_w", None, 43.2), "typical")
        self.assertEqual(correct_rating_class("psi_jb_c_per_w", None, 16.4), "typical")
        self.assertEqual(correct_rating_class("rtheta_jc_c_per_w", None, 52.1), "typical")

    def test_shutdown_max_is_rated_limit(self):
        self.assertEqual(correct_rating_class("mode_shutdown", "max", 3.0), "rated_limit")

    def test_non_thermal_non_mode_stays_unknown(self):
        self.assertEqual(correct_rating_class("something_else", None, 1.0), "typical")

class TestQaGate(unittest.TestCase):
    def test_thermal_rated_limit_fabrication_caught(self):
        claim = {"field": "rtheta_jc_c_per_w", "value": "52.1", "unit": "c/w",
                 "role": "min", "rating_class": "rated_limit", "package": "",
                 "doc_variant": "", "source": {"doc_sha256": "60fab"}}
        printed = {"value": 52.1, "unit": "°C/W", "role": None, "multi_package": True}
        defects = qa_claim(claim, printed)
        types = {d["defect"] for d in defects}
        assert "thermal_rated_limit_fabrication" in types, types
        assert "package_coordinates_missing" in types
        assert "doc_variant_binding_missing" in types

    def test_per_package_value_as_min_caught(self):
        claim = {"field": "psi_jb_c_per_w", "value": "16.4", "unit": "c/w",
                 "role": "min", "rating_class": "rated_limit", "package": "",
                 "doc_variant": "", "source": {"doc_sha256": "ae39"}}
        printed = {"value": 16.4, "unit": "°C/W", "role": None, "multi_package": True}
        defects = qa_claim(claim, printed)
        types = {d["defect"] for d in defects}
        assert "thermal_rated_limit_fabrication" in types
        assert "per_package_value_as_rated_limit" in types

    def test_unit_entailment_failure_caught(self):
        claim = {"field": "mode_shutdown", "value": 3e-6, "unit": "µa",
                 "role": "max", "rating_class": "rated_limit", "package": "",
                 "doc_variant": "", "source": {"doc_sha256": "59a9"}}
        printed = {"value": 3.0, "unit": "µA", "role": "max", "multi_package": False}
        defects = qa_claim(claim, printed)
        types = [d["defect"] for d in defects]
        assert "unit_dimension_mismatch" in types, f"expected unit_dimension_mismatch, got {types}"

    def test_correct_thermal_row_no_defects(self):
        claim = {"field": "rtheta_ja_c_per_w", "value": "43.2", "unit": "°C/W",
                 "role": "typ", "rating_class": "typical",
                 "package": "DDA (HSOIC)", "doc_variant": "base",
                 "source": {"doc_sha256": "59a9"}}
        printed = {"value": 43.2, "unit": "°C/W", "role": None, "multi_package": False}
        defects = qa_claim(claim, printed)
        assert len(defects) == 0, f"clean row flagged: {defects}"

    def test_unrelated_row_not_flagged(self):
        claim = {"field": "rtheta_ja_c_per_w", "value": "36.5", "unit": "°C/W",
                 "role": "typ", "rating_class": "typical",
                 "package": "DPR (WSON)", "doc_variant": "-Q1",
                 "source": {"doc_sha256": "60fab"}}
        printed = {"value": 36.5, "unit": "°C/W", "role": None, "multi_package": False}
        defects = qa_claim(claim, printed)
        assert len(defects) == 0, f"near-miss flagged: {defects}"

    def test_verified_thermal_3e6_microamp_rejected(self):
        """The pipeline's own 3e-6 µa must not pass."""
        claim = {"field": "rtheta_ja_c_per_w", "value": "3e-6", "unit": "µa",
                 "role": "typ", "rating_class": "typical",
                 "package": "DDA (HSOIC)", "doc_variant": "base",
                 "source": {"doc_sha256": "test"}}
        printed = {"value": 3.0, "unit": "µA", "role": None, "multi_package": False}
        assert unit_consistent(3e-6, "µa", 3.0, "µA") is False

if __name__ == "__main__":
    unittest.main()
