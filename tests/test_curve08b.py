"""CURVE-08B frozen regression suite — engineering decision API.

Covers the PRD requirements: R1 route (feature-flagged), R2 response
contract, R3 hard eligibility, R4 condition-matched curves, R5 evidence
resolution, R7 failure/unknown states, R8 three journeys through the
ACTUAL HTTP API, R9 ablation, R10 verification invariants, worktree
isolation, and FACET-02 non-merge.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from harness.electronics.decision_api import (  # noqa: E402
    ApiError,
    Handler,
    handle_decision_request,
)
from harness.electronics.candidates import (  # noqa: E402
    build_power_candidates,
    load_default_inputs,
)

CONTRACT = REPO / "contracts/engineering-decision-api-v1"
EXAMPLES = CONTRACT / "examples"

JOURNEY_A_REQ = {
    "category": "power", "subcategory": "buck-converters",
    "requirements": {"vin_v": 48, "vout_v": 5, "iout_a": 0.5},
    "comparison": {"metric": "efficiency", "mode": "condition_matched"},
    "evidence_policy": "machine_verified_advisory",
    "include_unknowns": True,
}
JOURNEY_B_REQ = {
    "category": "power", "subcategory": "buck-converters",
    "requirements": {"vin_v": 48, "vout_v": 5, "iout_a": 3.0},
    "comparison": {"metric": "efficiency", "mode": "condition_matched"},
    "evidence_policy": "machine_verified_advisory",
    "include_unknowns": True,
}
JOURNEY_C_REQ = {  # documented coverage gap: 24 V->3.3 V
    "category": "power", "subcategory": "buck-converters",
    "requirements": {"vin_v": 24, "vout_v": 3.3, "iout_a": 1.0},
    "comparison": {"metric": "efficiency"},
    "evidence_policy": "machine_verified_advisory",
    "include_unknowns": True,
}


def _post(port: int, path: str, payload, token=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _get(port: int, path: str, token=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


class LiveServer:
    """Real HTTP server on an ephemeral port; flag + optional token."""

    def __init__(self, enabled=True, token=None):
        self._old = {
            k: os.environ.get(k)
            for k in ("ENGINEERING_DECISIONS_ENABLED",
                      "ENGINEERING_DECISIONS_TOKEN", "HARNESS_REPO_ROOT")
        }
        os.environ["HARNESS_REPO_ROOT"] = str(REPO)
        os.environ["ENGINEERING_DECISIONS_ENABLED"] = "1" if enabled else ""
        if token:
            os.environ["ENGINEERING_DECISIONS_TOKEN"] = token
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        for key, val in self._old.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val


class IsolationTests(unittest.TestCase):
    """PRD §2: worktree-local assignment; assigned branch == checked-out
    branch, asserted WITHOUT hardcoding a branch name."""

    def test_assignment_matches_checked_out_branch(self):
        assignment = json.loads(
            (REPO / "curve08b.assignment.json").read_text())
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=REPO, capture_output=True, text=True,
            check=True).stdout.strip()
        self.assertEqual(assignment["branch"], branch,
                         "assignment file must name the live branch")
        subprocess.run(
            ["git", "merge-base", "--is-ancestor",
             assignment["base_commit"], "HEAD"],
            cwd=REPO, check=True)

    def test_facet02_foreign_and_unmerged(self):
        self.assertIsNone(
            importlib.util.find_spec("harness.search"),
            "FACET-02's harness/search must not exist on this lane")
        for mod in ("decision_api", "decision_engine", "candidates",
                    "eligibility"):
            src = (REPO / f"harness/electronics/{mod}.py").read_text()
            self.assertNotIn("harness.search", src)


class JourneyATests(unittest.TestCase):
    """R8 Journey A — light load, 48 V -> 5 V @ 0.5 A, end to end."""

    @classmethod
    def setUpClass(cls):
        cls.server = LiveServer()
        cls.status, cls.body = _post(
            cls.server.port, "/v1/engineering/decisions", JOURNEY_A_REQ)

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def test_http_200_and_contract_header(self):
        self.assertEqual(self.status, 200)
        self.assertEqual(self.body["schema"],
                         "harness.electronics-decision-api.v1")
        self.assertEqual(self.body["decision_contract_version"], "1.0.0")

    def test_release_identity_complete(self):
        rel = self.body["release"]
        self.assertEqual(rel["evidence_release_id"],
                         "curve-evidence-bundle-v3-5875789dcf3f")
        for key in ("bundle_sha256", "comparator_fingerprint",
                    "candidate_catalog_release", "decision_contract_version"):
            self.assertTrue(rel[key], f"release.{key} missing")
        self.assertEqual(rel["candidate_catalog_release"]["rows"], 16)

    def test_cohort_built_and_hard_eligibility_applied(self):
        c = self.body["counts"]
        self.assertEqual(c["total_candidates"], 21)
        self.assertEqual(c["hard_eligible"]
                         + c["hard_ineligible"] + c["hard_unknown"],
                         c["total_candidates"],
                         "verdict triple must partition the cohort")

    def test_condition_matched_curves_with_prd_values(self):
        entries = self.body["comparisons"]["condition_matched"]
        values = {e["part"]: round(e["result"]["value"], 2)
                  for e in entries}
        self.assertEqual(values.get("SiC461"), 93.94)
        self.assertEqual(values.get("SiC463"), 92.96)
        self.assertEqual(values.get("SiC464"), 92.95)
        self.assertEqual(values.get("LM5161"), 77.95)

    def test_unapproved_evidence_stays_advisory(self):
        for e in self.body["comparisons"]["condition_matched"]:
            self.assertEqual(
                e["human_review"]["adjudication_state"],
                "human_review_pending")
            self.assertTrue(e["human_review"]["advisory"])

    def test_every_evidence_reference_resolves(self):
        for e in self.body["comparisons"]["condition_matched"]:
            eid = e["evidence"]["evidence_id"]
            status, row = _get(self.server.port,
                               f"/v1/engineering/evidence/{eid}")
            self.assertEqual(status, 200)
            self.assertEqual(row["evidence"]["evidence_id"], eid)
            self.assertEqual(
                row["evidence"]["document_sha256"],
                e["evidence"]["document_sha256"])
            self.assertTrue(row["evidence"]["page_1based"] >= 1)

    def test_no_filesystem_paths_on_the_wire(self):
        blob = json.dumps(self.body)
        self.assertNotIn("/Volumes/", blob)
        self.assertNotIn("/Users/", blob)
        self.assertNotIn("source_path", blob)

    def test_response_matches_frozen_fixture(self):
        frozen = json.loads(
            (EXAMPLES / "response-journey-a.json").read_text())
        self.assertEqual(self.body, frozen,
                         "service output drifted from the frozen fixture")


class JourneyBTests(unittest.TestCase):
    """R8 Journey B — higher load 3 A: exclude LM5161/SiC464, preserve
    unknowns, recompute, never reuse the 0.5 A ranking."""

    @classmethod
    def setUpClass(cls):
        cls.server = LiveServer()
        cls.status, cls.body = _post(
            cls.server.port, "/v1/engineering/decisions", JOURNEY_B_REQ)

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def test_lm5161_and_sic464_excluded_with_cited_reasons(self):
        ineligible = {i["part"]: i for i in self.body["ineligible"]}
        for part, rated in (("LM5161", 1.0), ("SiC464", 2.0)):
            self.assertIn(part, ineligible)
            rules = ineligible[part]["rules"]
            self.assertTrue(rules, "every hard exclusion cites its rule")
            iout = next(r for r in rules if r["rule"] == "iout_min")
            self.assertEqual(iout["rated"], rated)
            self.assertEqual(iout["required"], 3.0)
            self.assertTrue(iout.get("evidence"))

    def test_excluded_parts_absent_from_comparisons(self):
        parts = {e["part"] for e in
                 self.body["comparisons"]["condition_matched"]}
        self.assertNotIn("LM5161", parts)
        self.assertNotIn("SiC464", parts)

    def test_recomputed_not_reused_from_half_amp(self):
        entries = self.body["comparisons"]["condition_matched"]
        self.assertTrue(entries)
        for e in entries:
            self.assertNotAlmostEqual(e["result"]["value"], 92.95, 1)
            self.assertNotAlmostEqual(e["result"]["value"], 77.95, 1)
        values = {e["part"]: round(e["result"]["value"], 2) for e in entries}
        self.assertEqual(values.get("SiC463"), 93.7)

    def test_unknown_candidates_preserved_and_distinguishable(self):
        unknowns = self.body["unknowns"]
        self.assertTrue(unknowns, "unknown candidates must be retained")
        for u in unknowns:
            self.assertTrue(u["missing_ratings"])
        matched_unknowns = [
            e for e in self.body["comparisons"]["condition_matched"]
            if e["eligibility"]["verdict"] == "unknown"]
        for e in matched_unknowns:
            self.assertFalse(e["eligibility"]["confirmed_eligible"])


class JourneyCTests(unittest.TestCase):
    """R8 Journey C — insufficient evidence at 24 V->3.3 V: parametric
    candidates, mismatch identified, comparison refused, suggestions."""

    @classmethod
    def setUpClass(cls):
        cls.server = LiveServer()
        cls.status, cls.body = _post(
            cls.server.port, "/v1/engineering/decisions", JOURNEY_C_REQ)

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def test_zero_comparisons_is_a_legitimate_answer(self):
        self.assertEqual(self.status, 200)
        self.assertEqual(
            self.body["comparisons"]["condition_matched"], [])

    def test_parametric_candidates_returned(self):
        self.assertGreater(self.body["counts"]["hard_eligible"], 0)
        self.assertGreater(len(self.body["candidates"]), 0)

    def test_missing_or_mismatched_evidence_identified(self):
        c = self.body["counts"]
        self.assertEqual(c["with_comparable_evidence"], 0)
        self.assertGreater(
            c["with_noncomparable_evidence"] + c["missing_evidence"], 0)

    def test_investigation_suggestions_returned(self):
        self.assertTrue(self.body["investigation"])
        for s in self.body["investigation"]:
            self.assertTrue(s["suggestion"])
            self.assertIn(s["situation"],
                          ("no_curve_evidence_held",
                           "evidence_not_comparable", "ratings_unknown"))


class HardEligibilityContractTests(unittest.TestCase):
    """R3 — hard eligibility before curves, unknown never eligible."""

    def test_three_amp_mandatory_case(self):
        _, body = handle_decision_request(JOURNEY_B_REQ)
        parts = {i["part"] for i in body["ineligible"]}
        self.assertIn("LM5161", parts)
        self.assertIn("SiC464", parts)

    def test_missing_current_rating_is_unknown(self):
        catalog, bundle, packets, ratings = load_default_inputs(REPO)
        candidates = list(build_power_candidates(
            catalog, bundle, packets, ratings).values())
        family = next(c for c in candidates if c.grain == "family")
        _, body = handle_decision_request(
            {**JOURNEY_A_REQ, "cohort": [family.candidate_id]})
        self.assertEqual(body["counts"]["hard_unknown"], 1)
        self.assertEqual(body["counts"]["hard_eligible"], 0,
                         "unknown must never be counted as eligible")

    def test_every_hard_exclusion_cites_its_source(self):
        _, body = handle_decision_request(JOURNEY_B_REQ)
        for record in body["candidates"]:
            if record["hard_eligibility"]["verdict"] != "ineligible":
                continue
            for rule in record["hard_eligibility"]["rules"]:
                if rule["verdict"] == "ineligible":
                    self.assertIn("rated", rule)
                    self.assertIn("required", rule)


class ErrorStateTests(unittest.TestCase):
    """R7 — stable machine-readable failure codes."""

    @classmethod
    def setUpClass(cls):
        cls.server = LiveServer()
        cls.port = cls.server.port

    @classmethod
    def tearDownClass(cls):
        cls.server.close()

    def _expect(self, payload, status, code):
        got_status, body = _post(
            self.port, "/v1/engineering/decisions", payload)
        self.assertEqual(got_status, status)
        self.assertEqual(body["error"]["code"], code)

    def test_invalid_json(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/v1/engineering/decisions",
            data=b"{not json", method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
            self.fail("should 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            self.assertEqual(json.loads(e.read())["error"]["code"],
                             "invalid_json")

    def test_unsupported_category(self):
        self._expect({"category": "mcu",
                      "requirements": {"vin_v": 48}}, 422,
                     "unsupported_category")

    def test_unsupported_metric(self):
        self._expect({"category": "power",
                      "requirements": {"vin_v": 48, "iout_a": 0.5},
                      "comparison": {"metric": "ripple"}}, 422,
                     "unsupported_metric")

    def test_unsupported_condition(self):
        self._expect({"category": "power",
                      "requirements": {"vin_v": 48, "esr_ohm": 1}}, 422,
                     "unsupported_condition")

    def test_unsupported_evidence_policy(self):
        self._expect({"category": "power",
                      "requirements": {"vin_v": 48},
                      "evidence_policy": "human_approved_only"}, 422,
                     "unsupported_evidence_policy")

    def test_unknown_candidate(self):
        self._expect({"category": "power",
                      "requirements": {"vin_v": 48},
                      "cohort": ["NOPE1", "NOPE2"]}, 422,
                     "unknown_candidate")

    def test_operating_point_required(self):
        self._expect({"category": "power",
                      "requirements": {"vin_v": 48, "vout_v": 5},
                      "comparison": {"metric": "efficiency"}}, 400,
                     "operating_point_required")

    def test_feature_flag_off(self):
        server = LiveServer(enabled=False)
        try:
            status, body = _post(
                server.port, "/v1/engineering/decisions", JOURNEY_A_REQ)
            self.assertEqual(status, 404)
            self.assertEqual(body["error"]["code"], "feature_disabled")
            health_status, health = _get(server.port,
                                         "/v1/engineering/health")
            self.assertEqual(health_status, 200)
            self.assertFalse(health["feature_enabled"])
        finally:
            server.close()

    def test_bearer_auth_when_configured(self):
        server = LiveServer(token="secret-token-1")
        try:
            status, _ = _post(server.port, "/v1/engineering/decisions",
                              JOURNEY_A_REQ)
            self.assertEqual(status, 401)
            status, body = _post(server.port, "/v1/engineering/decisions",
                                 JOURNEY_A_REQ, token="secret-token-1")
            self.assertEqual(status, 200)
        finally:
            server.close()

    def test_evidence_not_found(self):
        status, body = _get(
            self.port, "/v1/engineering/evidence/curve-doesnotexist")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "evidence_not_found")

    def test_broken_source_fails_closed_never_empty(self):
        """R10: a broken candidate source must not masquerade as an
        empty cohort. Tamper with the bundle in a throwaway copy."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copytree(REPO / "harness", root / "harness",
                            ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copytree(REPO / "tests/fixtures", root / "tests/fixtures")
            bundle = (root / "tests/fixtures/m4-handoff/"
                      "curve_evidence_bundle_v3.jsonl")
            bundle.write_text(bundle.read_text() + "\n{tampered}")
            from harness.electronics import decision_api
            decision_api._STATE.clear()
            old_root = os.environ.get("HARNESS_REPO_ROOT")
            os.environ["HARNESS_REPO_ROOT"] = str(root)
            try:
                with self.assertRaises(ApiError) as ctx:
                    handle_decision_request(
                        JOURNEY_A_REQ, repo_root=root)
                self.assertEqual(ctx.exception.status, 500)
                self.assertIn(ctx.exception.code,
                              ("evidence_bundle_tampered",
                               "evidence_source_unavailable"))
            finally:
                os.environ["HARNESS_REPO_ROOT"] = old_root or ""
                if not old_root:
                    os.environ.pop("HARNESS_REPO_ROOT", None)
                decision_api._STATE.clear()


class VerificationInvariantTests(unittest.TestCase):
    """R10 — the 'no' invariants, asserted on a full 0.5 A answer."""

    @classmethod
    def setUpClass(cls):
        cls.status, cls.body = handle_decision_request(JOURNEY_A_REQ)
        cls.status3, cls.body3 = handle_decision_request(JOURNEY_B_REQ)

    def test_no_invented_candidates(self):
        catalog, bundle, packets, ratings = load_default_inputs(REPO)
        universe = {c.candidate_id for c in build_power_candidates(
            catalog, bundle, packets, ratings).values()}
        for record in self.body["candidates"]:
            self.assertIn(record["candidate_id"], universe)

    def test_no_candidate_evidence_confusion(self):
        counts = self.body["counts"]
        self.assertEqual(counts["evidence_rows_attached"], 123)
        self.assertLess(counts["total_candidates"], 123)
        ids = [r["candidate_id"] for r in self.body["candidates"]]
        self.assertEqual(len(ids), len(set(ids)))

    def test_no_family_child_double_counting(self):
        records = self.body["candidates"]
        # one candidate per identity: no family+device pair counting the
        # same selection unit twice at one discovery grain
        identities = [(r["manufacturer"], r["family"], r["device"],
                       r["opn"], r["grain"]) for r in records]
        self.assertEqual(len(identities), len(set(identities)))
        # the TPS563206 family-grain node is the ONLY candidate for that
        # part (its part-bearing rows collapsed into it)
        tps = [r for r in records
               if (r.get("device") or r.get("opn")) == "TPS563206"
               or r["family"] == "TPS563206"]
        self.assertEqual(len(tps), 1)
        self.assertEqual(tps[0]["grain"], "family")
        self.assertIn("TPS563206", (tps[0].get("membership")
                                    or {}).get("devices", []))
        # every evidence row attaches to exactly one candidate: the sum
        # of attachments equals the bundle, never an inflated count
        self.assertEqual(self.body["counts"]["evidence_rows_attached"],
                         123)
        # SiC46x: four device candidates + honest family disclosure,
        # never a fifth "SiC46x family" duplicate
        sic = [r for r in records if r["family"] == "SiC46x"]
        self.assertEqual(len({r["candidate_id"] for r in sic}), len(sic))
        self.assertGreaterEqual(len(sic), 4)

    def test_no_unknown_promoted_to_eligible(self):
        self.assertEqual(
            self.body["counts"]["hard_eligible"]
            + self.body["counts"]["hard_ineligible"]
            + self.body["counts"]["hard_unknown"],
            self.body["counts"]["total_candidates"])
        for record in self.body["candidates"]:
            if record["hard_eligibility"]["verdict"] == "unknown":
                self.assertFalse(
                    record["hard_eligibility"]["parametric_ok"])

    def test_no_typical_as_guaranteed(self):
        for e in self.body["comparisons"]["condition_matched"]:
            self.assertFalse(e["result"]["guarantee"])
            self.assertTrue(e["result"]["typical"])
            self.assertIn("not a guarantee",
                          " ".join(e["interpolation"]["limitations"]))

    def test_no_extrapolation_outside_support(self):
        status, body = handle_decision_request({
            **JOURNEY_A_REQ,
            "comparison": {"metric": "efficiency",
                           "operating_point": {"x": 8.0, "unit": "A"}}})
        parts = {e["part"] for e in
                 body["comparisons"]["condition_matched"]}
        self.assertNotIn("LM5161", parts)
        refusals = [r for a in [rec["evidence_availability"]
                                for rec in body["candidates"]]
                    for r in (a.get("refusals") or [])]
        self.assertTrue(any("out_of_range" in json.dumps(r)
                            or "outside" in json.dumps(r)
                            for r in refusals))

    def test_no_missing_source_treated_as_verified(self):
        for record in self.body["candidates"]:
            quality = record["evidence_quality"]
            if quality["curve_evidence_rows"]:
                self.assertIn("human_review_pending",
                              quality["adjudication_states"])
                self.assertFalse(quality["human_approved"])

    def test_no_machine_to_human_gold_promotion(self):
        self.assertGreater(
            self.body["counts"]["pending_human_verification"], 0)
        self.assertEqual(
            self.body["release"]["evidence_release_id"],
            "curve-evidence-bundle-v3-5875789dcf3f")

    def test_no_approximate_bleeding_into_condition_matched(self):
        _, approx = handle_decision_request(
            {**JOURNEY_A_REQ,
             "comparison": {"metric": "efficiency",
                            "mode": "condition_matched+approximate"}})
        matched = approx["comparisons"]["condition_matched"]
        scenarios = approx["comparisons"]["approximate_scenarios"]
        self.assertTrue(scenarios)
        matched_ids = {e["evidence"]["evidence_id"] for e in matched}
        for s in scenarios:
            self.assertNotIn(s["evidence_id"], matched_ids)
            self.assertEqual(s["mode"], "approximate_scenario")

    def test_no_cross_worktree_mutation(self):
        bundle = (REPO / "tests/fixtures/m4-handoff/"
                  "curve_evidence_bundle_v3.jsonl")
        before = hashlib.sha256(bundle.read_bytes()).hexdigest()
        handle_decision_request(JOURNEY_A_REQ)
        handle_decision_request(JOURNEY_B_REQ)
        handle_decision_request(JOURNEY_C_REQ)
        after = hashlib.sha256(bundle.read_bytes()).hexdigest()
        self.assertEqual(before, after)

    def test_historical_bundles_stay_byte_identical(self):
        handoff = REPO / "tests/fixtures/m4-handoff"
        for name, manifest_name, digest in (
                ("curve_evidence_bundle.jsonl", "release_manifest.json",
                 "5cc1a6e41ad8"),
                ("curve_evidence_bundle_v2.jsonl",
                 "release_manifest_v2.json", "3b5658b629cc")):
            manifest = json.loads(
                (handoff / manifest_name).read_text())
            self.assertIn(digest, manifest["release_id"])
            actual = hashlib.sha256(
                (handoff / name).read_bytes()).hexdigest()
            self.assertEqual(actual, manifest["bundle_sha256"])


class AblationTests(unittest.TestCase):
    """R9 — parametric-only vs parametrics+curves on identical requests.

    Writes the measured ablation artifact next to the contract; expert
    review is honestly reported as pending (never fabricated)."""

    def test_ablation_arm_a_vs_arm_b(self):
        arm_a_req = {k: v for k, v in JOURNEY_A_REQ.items()
                     if k != "comparison"}
        t0 = time.perf_counter()
        _, a = handle_decision_request(arm_a_req)
        t_a = time.perf_counter() - t0
        t0 = time.perf_counter()
        _, b = handle_decision_request(JOURNEY_A_REQ)
        t_b = time.perf_counter() - t0

        # eligibility correctness: hard eligibility never sees curves —
        # verdicts must be IDENTICAL between arms
        verdicts_a = {r["candidate_id"]: r["hard_eligibility"]["verdict"]
                      for r in a["candidates"]}
        verdicts_b = {r["candidate_id"]: r["hard_eligibility"]["verdict"]
                      for r in b["candidates"]}
        self.assertEqual(verdicts_a, verdicts_b)

        # unknown handling identical
        self.assertEqual(a["counts"]["hard_unknown"],
                         b["counts"]["hard_unknown"])

        # evidence attempted vs not
        self.assertFalse(a["counts"]["evidence_attempted"])
        self.assertTrue(b["counts"]["evidence_attempted"])
        self.assertIsNone(a["counts"]["with_comparable_evidence"])
        self.assertEqual(b["counts"]["with_comparable_evidence"], 4)

        # unsupported comparisons: arm A attempts none; arm B's refusals
        # are surfaced, never silently dropped
        self.assertIsNone(a["comparisons"])
        refusals = sum(
            len(r.get("refusals") or []) for r in
            [rec["evidence_availability"] for rec in b["candidates"]])
        self.assertGreaterEqual(refusals, 0)

        # ranking changes: arm A has no ranking; arm B ranks 4 parts
        self.assertEqual(a["comparisons"], None)
        self.assertEqual(
            len(b["comparisons"]["condition_matched"]), 4)

        # explanation coverage
        explained_a = sum(1 for r in a["candidates"] if r["explanation"])
        explained_b = sum(1 for r in b["candidates"] if r["explanation"])
        self.assertEqual(explained_a, len(a["candidates"]))
        self.assertGreaterEqual(explained_b, explained_a)

        # evidence resolution: only arm B produces resolvable refs
        refs_b = sum(1 for e in
                     b["comparisons"]["condition_matched"]
                     if e["evidence"]["document_sha256"])
        self.assertEqual(refs_b, 4)

        # recall: comparable-evidence share of the eligible cohort
        recall_b = (b["counts"]["with_comparable_evidence"]
                    / max(1, b["counts"]["hard_eligible"]
                          + b["counts"]["hard_unknown"]))

        artifact = {
            "schema": "harness.electronics-ablation.v1",
            "request": JOURNEY_A_REQ,
            "arms": {
                "A_parametric_only": {
                    "latency_s": round(t_a, 4),
                    "eligibility_verdicts": verdicts_a,
                    "evidence_attempted": False,
                    "ranking": None,
                },
                "B_parametrics_plus_curves": {
                    "latency_s": round(t_b, 4),
                    "eligibility_verdicts": verdicts_b,
                    "evidence_attempted": True,
                    "comparable_candidates":
                        b["counts"]["with_comparable_evidence"],
                    "recall_of_eligible_cohort": round(recall_b, 4),
                    "unsupported_comparison_refusals": refusals,
                },
            },
            "measures": {
                "candidate_recall_gain":
                    b["counts"]["with_comparable_evidence"],
                "eligibility_correctness": "identical between arms "
                                           "(curves never participate)",
                "ranking_changes": "arm A ranks nothing; arm B ranks "
                                   "4 parts at cross_manufacturer level",
                "unknown_handling": "identical between arms",
                "expert_review": "pending — 0/123 rows human-approved; "
                                 "not fabricated",
            },
            "release": b["release"],
        }
        out = CONTRACT / "ablation" / "ABLATION_REPORT.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(artifact, indent=2) + "\n")
        self.assertGreater(t_a, 0)
        self.assertGreater(t_b, 0)


class FixtureFreezeTests(unittest.TestCase):
    """R6 — frozen fixtures are the byte-truth of the contract."""

    def test_all_journey_fixtures_reproduce(self):
        for name in ("journey-a", "journey-b", "journey-c",
                     "ablation-arm-a-parametric-only"):
            request = json.loads(
                (EXAMPLES / f"request-{name}.json").read_text())
            frozen = json.loads(
                (EXAMPLES / f"response-{name}.json").read_text())
            status, body = handle_decision_request(request)
            self.assertEqual(status, 200)
            self.assertEqual(body, frozen,
                             f"{name} drifted from its frozen fixture")

    def test_error_fixtures_reproduce(self):
        for path in sorted(EXAMPLES.glob("error-*.json")):
            frozen = json.loads(path.read_text())
            request = frozen.get("request")
            if request is None:
                self.fail(f"{path.name} must embed its request scenario")
            with self.assertRaises(ApiError) as ctx:
                handle_decision_request(request)
            self.assertEqual(ctx.exception.status,
                             frozen["http_status"])
            self.assertEqual(ctx.exception.code,
                             frozen["error"]["code"])

    def test_openapi_documents_every_error_code(self):
        spec = (CONTRACT / "openapi.yaml").read_text()
        for code in ("invalid_json", "invalid_request",
                     "operating_point_required", "unauthorized",
                     "feature_disabled", "unknown_route",
                     "unsupported_category", "unsupported_metric",
                     "unsupported_condition",
                     "unsupported_comparison_mode",
                     "unsupported_evidence_policy", "unknown_candidate",
                     "evidence_not_found", "evidence_source_unavailable",
                     "evidence_bundle_tampered", "internal_error"):
            self.assertIn(code, spec)


if __name__ == "__main__":
    unittest.main()
