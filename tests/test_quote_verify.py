import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.pipeline.quote_verify import find_quote, value_in_quote, verify_claims

DOC = "Recommended Operating Conditions: VIN 2.5 V to 5.5 V. Absolute Maximum Ratings: VIN 6.0 V."


class QuoteVerifyTests(unittest.TestCase):
    def test_quote_found_whitespace_normalized(self):
        self.assertTrue(find_quote("vin   2.5 V", DOC))

    def test_quote_absent(self):
        self.assertFalse(find_quote("VIN 9.9 V", DOC))

    def test_value_parses_from_quote(self):
        self.assertTrue(value_in_quote(5.5, "VIN 2.5 V to 5.5 V"))
        self.assertFalse(value_in_quote(6.1, "VIN 2.5 V to 5.5 V"))

    def test_unquoted_assertion_is_p0(self):
        v = verify_claims({"vin_max_v": {"value": 5.5, "quote": None}}, DOC)
        self.assertEqual(v[0]["reason_code"], "unquoted_assertion")
        self.assertEqual(v[0]["severity"], "P0")

    def test_fabricated_quote_is_p0(self):
        v = verify_claims({"vin_max_v": {"value": 9.9, "quote": "VIN up to 9.9 V guaranteed"}}, DOC)
        self.assertEqual(v[0]["reason_code"], "quote_not_in_doc")
        self.assertEqual(v[0]["severity"], "P0")

    def test_semantic_fabrication_rejected(self):
        self.assertFalse(find_quote("5.5 V storage temperature in Norway", DOC * 3))

    def test_value_not_in_quote_is_p0(self):
        v = verify_claims({"vin_max_v": {"value": 9.9, "quote": "VIN 2.5 V to 5.5 V"}}, DOC)
        self.assertEqual(v[0]["reason_code"], "value_not_in_quote")

    def test_good_claim_supported(self):
        v = verify_claims({"vin_max_v": {"value": 5.5, "quote": "VIN 2.5 V to 5.5 V",
                                         "table": "Recommended Operating",
                                         "column_header": "VIN", "row_header": "Operating"}}, DOC)
        self.assertEqual(v[0]["verdict"], "SUPPORTED")
        self.assertEqual(v[0]["severity"], "P2")

    def test_good_claim_legacy_without_headers_is_p1(self):
        v = verify_claims({"vin_max_v": {"value": 5.5, "quote": "VIN 2.5 V to 5.5 V",
                                         "table": "Recommended Operating"}}, DOC)
        self.assertEqual(v[0]["severity"], "P1")
        self.assertEqual(v[0]["reason_code"], "unbound_header")

    def test_wrong_table_title_flags_p1(self):
        v = verify_claims({"v_abs_max": {"value": 6.0, "quote": "VIN 6.0 V",
                                         "table": "Thermal Shutdown",
                                         "column_header": "VIN", "row_header": "Supply"}}, DOC)
        self.assertEqual(v[0]["severity"], "P1")
        self.assertIn(v[0]["reason_code"], ("table_title_unverified", "partially_bound_header"))

    def test_paraphrased_range_quote_matches(self):
        self.assertTrue(find_quote("VIN 2.5-5.5V", DOC))

    def test_wrong_number_never_matches(self):
        self.assertFalse(find_quote("VIN 2.5-7.7V", DOC))

    def test_paraphrased_tokens_match(self):
        self.assertTrue(find_quote("input range 2.5 V 5.5 V", DOC, field="vin_min_v"))

    def test_alias_does_not_rescue_wrong_field(self):
        self.assertFalse(find_quote("input range 2.5 V 5.5 V", DOC, field="temp_min_c"))

    def test_numbers_right_tokens_wrong_window_rejected(self):
        self.assertFalse(find_quote("5.5 V storage temperature in Norway", DOC * 3))

    def test_wrong_cell_qualifier_binding_rejects(self):
        doc = ("Electrical Characteristics: Symbol  Min  Typ  Max\n"
               "VGS(th)  1.0  1.5  2.5  V  DS=10V")
        v = verify_claims({"vgs_th": {"value": 1.5, "quote": "VGS(th)  1.0  1.5  2.5",
                                      "table": "Electrical Characteristics",
                                      "column_header": "Max", "row_header": "VGS(th)",
                                      "qualifier": "maximum"}}, doc)
        self.assertEqual(v[0]["severity"], "P0")
        self.assertEqual(v[0]["reason_code"], "wrong_cell_binding")

    def test_right_cell_qualifier_binding_passes(self):
        doc = ("Electrical Characteristics: Symbol  Min  Typ  Max\n"
               "VGS(th)  1.0  1.5  2.5  V  DS=10V")
        v = verify_claims({"vgs_th": {"value": 2.5, "quote": "VGS(th)  1.0  1.5  2.5",
                                      "table": "Electrical Characteristics",
                                      "column_header": "Max", "row_header": "VGS(th)",
                                      "qualifier": "maximum"}}, doc)
        self.assertEqual(v[0]["severity"], "P2")

    def test_missing_headers_flag_p1_not_p0(self):
        v = verify_claims({"vin_max_v": {"value": 5.5, "quote": "VIN 2.5 V to 5.5 V",
                                         "table": "Recommended Operating"}}, DOC)
        self.assertEqual(v[0]["severity"], "P1")
        self.assertEqual(v[0]["reason_code"], "unbound_header")

    def test_junction_temp_rejected_for_ambient_field(self):
        doc = "Absolute Maximum Ratings: TJ -40 to 150 C. Recommended Operating: TA -40 to 85 C."
        v = verify_claims({"temp_max_c": {"value": 150, "quote": "TJ -40 to 150 C",
                                          "table": "Absolute Maximum",
                                          "column_header": "TJ", "row_header": "Temp"}},
                          doc)
        self.assertEqual(v[0]["severity"], "P0")
        self.assertEqual(v[0]["reason_code"], "wrong_quantity_convention")

    def test_ambient_temp_passes(self):
        doc = "Absolute Maximum Ratings: TJ -40 to 150 C. Recommended Operating: TA -40 to 85 C."
        v = verify_claims({"temp_max_c": {"value": 85, "quote": "TA -40 to 85 C",
                                          "table": "Recommended Operating",
                                          "column_header": "TA", "row_header": "Operating"}}, doc)
        self.assertIn(v[0]["severity"], ("P2", "P1"))
        self.assertNotEqual(v[0]["reason_code"], "wrong_quantity_convention")

    def test_null_fields_skipped(self):
        self.assertEqual(verify_claims({"vin_min_v": {"value": None}}, DOC), [])


if __name__ == "__main__":
    unittest.main()
