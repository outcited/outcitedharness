"""CURVE-08E (DISCOVERY-01 B6): M4 ranks ONLY the population M5 supplies.

Owner-approved architecture: M5 owns candidate generation; M4 ranks the
explicit candidate set it is given. M4 may rank, score, compare and exclude
within that population under its own documented decision contract, but it
may NOT silently substitute its own evidence universe, and a candidate it
holds no evidence for must be reported unresolved rather than dropped or
replaced.

Backward compatibility lock: a body without ``candidate_scope`` is answered
exactly as before (the ``question`` dialect is untouched, so pinned parity
journeys and frozen baselines are byte-stable).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from harness.electronics.decision_api import (
    CANDIDATE_SCOPE_SCHEMA, handle_decision_request, service_state)

REPO = Path(__file__).resolve().parents[1]

QUESTION = {
    "requirements": {"vin_min": 48, "vin_max": 48, "iout_min": 0.5,
                     "vout": 5},
    "conditions": {"vin_v": 48, "vout_v": 5},
    "operating_point": {"x": 0.5, "unit": "A"},
    "phenomenon": "efficiency_vs_load",
}
SCOPE_SCHEMA = "harness.discovery01-candidate-scope.v1"


def _cand(opn, manufacturer="ti", canonical=None):
    return {"canonical_id": canonical or f"opn:{opn}", "opn": opn,
            "manufacturer": manufacturer, "grain": "opn",
            "eligibility_state": "sent_for_evaluation",
            "identity": {"mapped": False, "identity_source": "none"},
            "provenance": {"evidence_units": 0}}


def _scope(opns, **over):
    body = {
        "schema": CANDIDATE_SCOPE_SCHEMA,
        "population": "discovery_cohort",
        "crosswalk": {"schema": "harness.search-identity-crosswalk.v1",
                      "version": "m5-generated-identity-crosswalk.v1",
                      "sha256": "a" * 64,
                      "source": "generated:identity-store",
                      "coverage": {"total": len(opns)}},
        "releases": {"m5_evidence_release": "search-release-v1-test",
                     "m5_identity_snapshot": "identity-snapshot-v1-test"},
        "selection": {"category": "power", "constraints": {}},
        "candidates": [_cand(o) for o in opns],
        "count": len(opns),
        "sha256": "b" * 64,
    }
    body.update(over)
    return body


def _ask(question=None, scope=None):
    payload = {"question": question or QUESTION}
    if scope is not None:
        payload["candidate_scope"] = scope
    status, body = handle_decision_request(payload, repo_root=REPO)
    return status, body


def _ranked_parts(body):
    return [e["part"] for e in
            body["comparison"]["condition_matched_entries"]]


class BackwardCompatibilityTests(unittest.TestCase):
    def test_unscoped_body_is_answered_as_before(self):
        status, body = _ask()
        self.assertEqual(status, 200)
        self.assertNotIn("candidate_scope", body)
        self.assertEqual(body["counts"]["total_candidates"], 21)
        self.assertEqual(body["schema"],
                         "harness.electronics-decision-answer.v1")

    def test_unscoped_question_echo_is_unchanged_by_scoping(self):
        _, unscoped = _ask()
        _, scoped = _ask(scope=_scope(["LM5161"]))
        self.assertEqual(unscoped["question"], QUESTION)
        self.assertEqual(scoped["question"], QUESTION)

    def test_release_identity_is_identical_scoped_or_not(self):
        _, unscoped = _ask()
        _, scoped = _ask(scope=_scope(["LM5161"]))
        self.assertEqual(unscoped["evidence_release"],
                         scoped["evidence_release"])
        self.assertEqual(unscoped["bundle_sha256"], scoped["bundle_sha256"])


class ScopedRankingTests(unittest.TestCase):
    def test_m4_ranks_only_the_supplied_population(self):
        status, body = _ask(scope=_scope(["LM5161", "LM5005"]))
        self.assertEqual(status, 200)
        sc = body["candidate_scope"]
        self.assertEqual(sc["received"], 2)
        self.assertEqual(sc["matched"], 2)
        self.assertEqual(sc["unresolved"], 0)
        self.assertEqual(body["counts"]["total_candidates"], 2)
        self.assertTrue(set(_ranked_parts(body)) <= {"LM5161", "LM5005"})
        self.assertTrue(sc["ranking_strictly_in_cohort"])
        self.assertFalse(sc["m4_universe_substituted"])

    def test_no_extra_m4_candidate_can_appear(self):
        _, body = _ask(scope=_scope(["LM5161"]))
        universe = {c.device or c.opn
                    for c in service_state(REPO).candidates}
        presented = set(_ranked_parts(body))
        self.assertEqual(presented, {"LM5161"} & universe)
        self.assertLess(len(presented), len(universe))
        elig = body["hard_eligibility"]["eligible"]
        self.assertEqual(len(elig), 1)

    def test_empty_population_is_never_replaced_by_the_universe(self):
        status, body = _ask(scope=_scope([]))
        self.assertEqual(status, 200)
        self.assertEqual(body["counts"]["total_candidates"], 0)
        self.assertEqual(_ranked_parts(body), [])
        self.assertEqual(body["hard_eligibility"]["eligible"], [])
        self.assertEqual(body["candidate_scope"]["received"], 0)
        self.assertEqual(body["candidate_scope"]["matched"], 0)
        # release identity is still reported for an empty population
        self.assertTrue(body["evidence_release"])
        self.assertTrue(body["bundle_sha256"])

    def test_unresolvable_candidates_are_reported_not_dropped(self):
        _, body = _ask(scope=_scope(["LM5161", "IPB017N06N", "AIMBG120"]))
        sc = body["candidate_scope"]
        self.assertEqual(sc["received"], 3)
        self.assertEqual(sc["matched"], 1)
        self.assertEqual(sc["unresolved"], 2)
        reasons = {u["opn"]: u["reason"] for u in sc["unresolved_sample"]}
        self.assertEqual(reasons, {"IPB017N06N": "no_evidence_in_bundle",
                                   "AIMBG120": "no_evidence_in_bundle"})
        self.assertEqual(_ranked_parts(body), ["LM5161"])

    def test_unknown_identity_namespace_is_not_fuzzy_matched(self):
        """A near-miss name must stay unresolved: no fuzzy joins."""
        _, body = _ask(scope=_scope(["LM5161X", "lm5161"]))
        sc = body["candidate_scope"]
        # case-insensitive exact is allowed; invented suffixes are not
        unresolved = {u["opn"] for u in sc["unresolved_sample"]}
        self.assertIn("LM5161X", unresolved)
        self.assertEqual(sc["matched"], 1)

    def test_eligible_candidate_cannot_disappear_without_a_reason(self):
        _, body = _ask(scope=_scope(["LM5161", "LM5005", "TPS548C26"]))
        he = body["hard_eligibility"]
        accounted = (set(he["eligible"]) | {e["candidate"] for e in
                                            he["ineligible"]}
                     | {u["candidate"] for u in he["unknown"]})
        self.assertEqual(len(accounted), body["counts"]["total_candidates"])
        sc = body["candidate_scope"]
        self.assertEqual(sc["matched"],
                         sc["received"] - sc["unresolved"])

    def test_hard_exclusion_still_applies_within_the_population(self):
        _, body = _ask(question={**QUESTION,
                                 "requirements": {**QUESTION["requirements"],
                                                  "iout_min": 3.0}},
                       scope=_scope(["LM5161", "SiC464"]))
        inelig = {e["part"] for e in body["hard_eligibility"]["ineligible"]}
        self.assertTrue(inelig, "expected a hard exclusion at 3 A")
        for part in inelig:
            self.assertIn(part, {"LM5161", "SiC464"})
        rules = body["hard_eligibility"]["ineligible"][0]["rules"]
        self.assertTrue(all(r.get("rule") for r in rules))

    def test_unknown_ratings_are_retained_within_the_population(self):
        """Unknown retention is M4's eligibility law and must survive
        scoping. The universe's only unknown-retained candidate is a
        family-grain node (no OPN/device), transported here by its canonical
        id - which is exactly why the scope contract carries one."""
        universe = service_state(REPO).candidates
        _, unscoped = _ask()
        unknown_ids = [u["candidate"]
                       for u in unscoped["hard_eligibility"]["unknown"]]
        self.assertTrue(unknown_ids, "fixture expectation: one unknown")
        by_id = {c.candidate_id: c for c in universe}
        scope = _scope([])
        scope["candidates"] = [
            {"canonical_id": cid,
             "opn": by_id[cid].family or by_id[cid].device or cid,
             "manufacturer": "ti", "grain": by_id[cid].grain,
             "eligibility_state": "unknown_attributes_retained",
             "identity": {"mapped": False, "identity_source": "none"},
             "provenance": {}} for cid in unknown_ids]
        scope["count"] = len(scope["candidates"])
        _, body = _ask(scope=scope)
        unknown = body["hard_eligibility"]["unknown"]
        self.assertEqual([u["candidate"] for u in unknown], unknown_ids)
        for u in unknown:
            self.assertTrue(u["note"], "unknown must stay explicit")
            self.assertTrue(u["missing_ratings"])
        self.assertEqual(body["candidate_scope"]["matched"],
                         len(unknown_ids))
        self.assertEqual(_ranked_parts(body), [])

    def test_opn_grain_population_is_fully_accounted_for(self):
        universe = service_state(REPO).candidates
        opns = [c.device or c.opn for c in universe if c.grain == "opn"]
        _, body = _ask(scope=_scope(opns))
        sc = body["candidate_scope"]
        he = body["hard_eligibility"]
        self.assertEqual(sc["received"], len(opns))
        self.assertEqual(sc["unresolved"], 0)
        accounted = (set(he["eligible"])
                     | {e["candidate"] for e in he["ineligible"]}
                     | {u["candidate"] for u in he["unknown"]})
        self.assertEqual(len(accounted), sc["matched"])

    def test_scope_accounting_is_deterministic(self):
        _, a = _ask(scope=_scope(["LM5161", "LM5005", "NOPE"]))
        _, b = _ask(scope=_scope(["LM5161", "LM5005", "NOPE"]))
        self.assertEqual(json.dumps(a, sort_keys=True, default=str),
                         json.dumps(b, sort_keys=True, default=str))

    def test_order_of_supplied_candidates_does_not_change_the_ranking(self):
        _, a = _ask(scope=_scope(["LM5161", "LM5005"]))
        _, b = _ask(scope=_scope(["LM5005", "LM5161"]))
        self.assertEqual(_ranked_parts(a), _ranked_parts(b))

    def test_m5_releases_are_echoed_for_observability(self):
        _, body = _ask(scope=_scope(["LM5161"]))
        sc = body["candidate_scope"]
        self.assertEqual(sc["m5_releases"]["m5_evidence_release"],
                         "search-release-v1-test")
        self.assertEqual(sc["m4_release"]["evidence_release"],
                         body["evidence_release"])
        self.assertEqual(sc["m5_crosswalk"]["sha256"], "a" * 64)
        self.assertIn("never", sc["law"].lower())


class ScopeValidationTests(unittest.TestCase):
    def test_scope_must_be_an_object(self):
        with self.assertRaises(Exception) as ctx:
            _ask(scope="not-an-object")
        self.assertIn("candidate_scope", str(ctx.exception))

    def test_candidates_must_be_a_list(self):
        with self.assertRaises(Exception):
            _ask(scope={"schema": CANDIDATE_SCOPE_SCHEMA,
                        "population": "discovery_cohort",
                        "candidates": {"opn": "LM5161"}})

    def test_unknown_scope_schema_is_refused(self):
        with self.assertRaises(Exception):
            _ask(scope=_scope(["LM5161"], schema="made-up.v9"))

    def test_unknown_population_is_refused(self):
        with self.assertRaises(Exception):
            _ask(scope=_scope(["LM5161"], population="m4_universe"))

    def test_candidate_without_identity_is_refused(self):
        scope = _scope(["LM5161"])
        scope["candidates"][0] = {"manufacturer": "ti"}
        with self.assertRaises(Exception):
            _ask(scope=scope)

    def test_scope_schema_constant_matches_the_m5_contract(self):
        self.assertEqual(CANDIDATE_SCOPE_SCHEMA,
                         "harness.discovery01-candidate-scope.v1")


if __name__ == "__main__":
    unittest.main()
