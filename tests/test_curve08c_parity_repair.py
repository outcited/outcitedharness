"""CURVE-08C regression locks — the question dialect + native byte-truth.

Locks the parity repair that took DISCOVERY-01 live parity from FAIL 13/18
(adapter, v2) to PASS 21/21 (real engine, v3):

1. {"question": {...}} bodies are answered by the canonical CURVE-08
   producer answer_question() verbatim — schema
   harness.electronics-decision-answer.v1, release v3, frozen values
   regenerated from evidence (never hardcoded in serving logic).
2. The native decision-api.v1 path stays byte-identical to its frozen
   fixtures (the 08B contract is unchanged by the repair).
3. Journey B hard ineligibility (LM5161 1 A, SiC464 2 A) and unknown
   retention through the question dialect.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location(
    "decision_api_08c", REPO / "harness" / "electronics" / "decision_api.py")
mod = importlib.util.module_from_spec(_spec)
sys.modules["decision_api_08c"] = mod
_spec.loader.exec_module(mod)

QUESTION_A = {
    "conditions": {"vin_v": 48.0, "vout_v": 5.0},
    "operating_point": {"unit": "A", "x": 0.5},
    "phenomenon": "efficiency_vs_load",
    "requirements": {"iout_min": 0.5, "vin_max": 48.0,
                     "vin_min": 48.0, "vout": 5.0},
}
QUESTION_B = {
    "conditions": {"vin_v": 48.0, "vout_v": 5.0},
    "operating_point": {"unit": "A", "x": 3.0},
    "phenomenon": "efficiency_vs_load",
    "requirements": {"iout_min": 3.0, "vin_max": 48.0,
                     "vin_min": 48.0, "vout": 5.0},
}

PIN_SCHEMA = "harness.electronics-decision-answer.v1"
PIN_RELEASE = "curve-evidence-bundle-v3-5875789dcf3f"
PIN_BUNDLE_SHA = "0c047e516c10c489134efe226d12e2422651665c4e394379108e9ec10ea4b16f"


class QuestionDialectTests(unittest.TestCase):
    def test_journey_a_frozen_values_regenerated(self):
        status, body = mod.handle_decision_request({"question": QUESTION_A})
        self.assertEqual(status, 200)
        self.assertEqual(body["schema"], PIN_SCHEMA)
        self.assertEqual(body["evidence_release"], PIN_RELEASE)
        self.assertEqual(body["bundle_sha256"], PIN_BUNDLE_SHA)
        vals = {e["part"]: round(e["value"], 2) for e in
                body["comparison"]["condition_matched_entries"]}
        self.assertEqual(vals, {"SiC461": 93.94, "SiC463": 92.96,
                                "SiC464": 92.95, "LM5161": 77.95})
        level = body["comparison"]["comparison_level"]
        self.assertEqual(level.get("achieved_level"), "cross_manufacturer")

    def test_journey_b_hard_ineligibility_with_rules(self):
        status, body = mod.handle_decision_request({"question": QUESTION_B})
        self.assertEqual(status, 200)
        inel = {e["part"]: e for e in body["hard_eligibility"]["ineligible"]}
        self.assertIn("LM5161", inel)
        self.assertIn("SiC464", inel)
        lm = inel["LM5161"]["rules"][0]
        self.assertEqual(lm["required"], 3.0)
        self.assertEqual(lm["rated"], 1.0)
        sc = inel["SiC464"]["rules"][0]
        self.assertEqual(sc["rated"], 2.0)
        self.assertTrue(body["hard_eligibility"]["unknown"])

    def test_journey_b_ranking_excludes_ineligible(self):
        _, body = mod.handle_decision_request({"question": QUESTION_B})
        parts = {e["part"] for e in
                 body["comparison"]["condition_matched_entries"]}
        self.assertNotIn("LM5161", parts & {"LM5161"})
        self.assertNotIn("SiC464", parts & {"SiC464"})

    def test_interpolation_disclosure_present(self):
        _, body = mod.handle_decision_request({"question": QUESTION_A})
        for e in body["comparison"]["condition_matched_entries"]:
            self.assertIn("interpolation", json.dumps(e).lower())
            self.assertTrue(e.get("evidence_id"))


class NativePathUnchangedTests(unittest.TestCase):
    def test_native_fixture_byte_truth(self):
        req = json.loads((REPO / "contracts" / "engineering-decision-api-v1"
                          / "examples" / "request-journey-a.json")
                         .read_text())
        frozen = json.loads(
            (REPO / "contracts" / "engineering-decision-api-v1" / "examples"
             / "response-journey-a.json").read_text())
        status, live = mod.handle_decision_request(req)
        self.assertEqual(status, 200)
        self.assertEqual(
            json.dumps(live, sort_keys=True, default=str),
            json.dumps(frozen, sort_keys=True, default=str))


if __name__ == "__main__":
    unittest.main()


class AuthContractTests(unittest.TestCase):
    """PRD remediation #5: 401 = missing bearer, 403 = present-but-invalid.

    Exercises the real Handler.auth_status() logic (env token set).
    Hermetic: saves/restores ENGINEERING_DECISIONS_TOKEN so the env never
    leaks into sibling suites (order-independence is part of the lock)."""

    def setUp(self):
        self._saved = os.environ.get("ENGINEERING_DECISIONS_TOKEN")
        os.environ["ENGINEERING_DECISIONS_TOKEN"] = "k1"

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ENGINEERING_DECISIONS_TOKEN", None)
        else:
            os.environ["ENGINEERING_DECISIONS_TOKEN"] = self._saved

    def _handler_status(self, header_value):
        import harness.electronics.decision_api as svc
        h = svc.Handler.__new__(svc.Handler)  # no server socket needed

        class H:  # minimal header stub
            def get(self, k, default=None):
                return {"Authorization": header_value}.get(k, default)
        h.headers = H()
        return h.auth_status()

    def test_missing_bearer_is_401(self):
        self.assertEqual(self._handler_status(None), 401)
        self.assertEqual(self._handler_status(""), 401)

    def test_invalid_bearer_is_403(self):
        self.assertEqual(self._handler_status("Bearer wrong"), 403)
        self.assertEqual(self._handler_status("Basic k1"), 403)

    def test_valid_bearer_passes(self):
        self.assertIsNone(self._handler_status("Bearer k1"))

    def test_no_token_configured_disables_auth(self):
        os.environ.pop("ENGINEERING_DECISIONS_TOKEN", None)
        import harness.electronics.decision_api as svc  # noqa: F401
        h = svc.Handler.__new__(svc.Handler)

        class H:
            def get(self, k, default=None):
                return None
        h.headers = H()
        self.assertIsNone(h.auth_status())
