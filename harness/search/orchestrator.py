"""Engineering-session orchestration (PRD-DISCOVERY-01 R1/R5/R7/R8).

M5 orchestrates discovery; M4 owns engineering decisions. This module:

  1. builds the M5 candidate cohort (FACET-02/03 machinery, reused — no
     parallel facet engine),
  2. crosswalks M5 canonical ids <-> M4 candidate ids (explicit classes:
     exact | ambiguous | unmapped; family-grain nodes are NEVER mapped to
     OPN candidates),
  3. forwards operating requirements to the M4 decision client (eligibility
     and curve mathematics stay M4's),
  4. resolves evidence references to source provenance (sha/page/locator/
     verification state — never filesystem paths, never upgraded states),
  5. returns the versioned envelope with cohort state, candidate results,
     comparisons, guidance, releases, and structured failures.

A failed M4 call produces a partial result with the cohort preserved and a
structured error — never a false "zero candidates" success.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from harness.search import cohort as cohort_mod
from harness.search import identity as identity_mod
from harness.search import m4_client
from harness.search import session as session_mod
from harness.search import units as units_store

ORCHESTRATION_SCHEMA = "harness.discovery01-engineering-session.v1"
DISCOVERY_POLICY = "discovery01-v1(facets=reduction-x-coverage-floor-0.30;" \
                   "eligibility=m4-only;curves=m4-only)"
CROSSWALK_FIXTURE = Path(__file__).resolve().parents[2] / \
    "tests/fixtures/discovery01/m4_identity_crosswalk_v1.json"

# M5 engineering-constraint vocabulary -> M4 requirement keys (canonical SI)
_CONSTRAINT_TO_M4 = {"vin_v": ("vin_min", "vin_max"), "vout_v": ("vout",),
                     "iout_a": ("iout_min",)}

# Explicit M4->M5 subcategory crosswalk (closed table, never fuzzy). Both
# vocabularies are reported in the response so the mapping is visible.
SUBCATEGORY_CROSSWALK = {
    "buck": "switching-regulators",
    "boost": "switching-regulators",
    "buck-boost": "switching-regulators",
    "flyback": "ac-dc",
    "ldo": "ldo-regulators",
    "load-switch": "load-switches",
    "gate-driver": "gate-drivers",
    "discrete-mosfet": "discrete-mosfet",
}


class SessionCache:
    """Bounded, release-keyed, deterministic-invalidation cache (R8).

    Key identity includes every release that can change the answer; errors
    are never cached; eviction is LRU with a hard bound.
    """

    def __init__(self, max_entries: int = 64):
        self.max_entries = max(1, max_entries)
        self._data: "OrderedDict[str, dict]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(**parts: Any) -> str:
        canonical = json.dumps(parts, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()[:24]

    def get(self, key: str) -> dict | None:
        if key in self._data:
            self._data.move_to_end(key)
            self.hits += 1
            return self._data[key]
        self.misses += 1
        return None

    def put(self, key: str, value: dict) -> None:
        self._data[key] = value
        self._data.move_to_end(key)
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)


def load_crosswalk(path: Path | None = None) -> dict:
    p = path or CROSSWALK_FIXTURE
    if not p.exists():
        raise m4_client.M4ServiceError(
            "no_frozen_answer", f"crosswalk fixture missing: {p}")
    return json.loads(p.read_text())


def crosswalk_map(crosswalk: dict, m5_opns: set[str]) -> dict:
    """Classify every M4 candidate against the M5 cohort.

    exact: M4 candidate's OPN/device string equals an M5 catalog OPN
           (case-normalized) at opn/device grain.
    ambiguous: one M5 OPN claimed by >1 M4 candidate.
    unmapped: family-grain nodes (never interchangeable with OPNs) and
           M4-only devices with no M5 catalog identity.
    No fuzzy/similar-name joins exist here by design.
    """
    norm = {o.upper(): o for o in m5_opns}
    by_m5: dict[str, list[dict]] = {}
    out = {"exact": {}, "ambiguous": {}, "unmapped": {}, "m4_only": {}}
    for row in crosswalk["candidates"]:
        cid = row["m4_candidate_id"]
        if row["grain"] == "family":
            out["unmapped"][cid] = {**row, "class": "family_grain_node"}
            continue
        token = (row["opn"] or row["device"] or "").upper()
        m5_opn = norm.get(token)
        if m5_opn is None:
            out["m4_only"][cid] = row
            continue
        by_m5.setdefault(m5_opn, []).append((cid, row))
    for m5_opn, matches in by_m5.items():
        if len(matches) == 1:
            cid, row = matches[0]
            out["exact"][cid] = {**row, "m5_canonical_id": f"opn:{m5_opn}"}
        else:
            for cid, row in matches:
                out["ambiguous"][cid] = {**row,
                                         "m5_canonical_id": f"opn:{m5_opn}"}
    return out


def build_m4_question(constraints: dict, comparison: dict | None) -> dict:
    """Canonical SI requirements -> the frozen M4 question contract."""
    requirements: dict[str, float] = {}
    for key, value in (constraints or {}).items():
        targets = _CONSTRAINT_TO_M4.get(key)
        if not targets or value is None:
            continue
        if key == "vin_v":
            requirements["vin_min"] = float(value)
            requirements["vin_max"] = float(value)
        elif key == "vout_v":
            requirements["vout"] = float(value)
        elif key == "iout_a":
            requirements["iout_min"] = float(value)
    conditions = {"vin_v": constraints.get("vin_v"),
                  "vout_v": constraints.get("vout_v")}
    conditions = {k: float(v) for k, v in conditions.items()
                  if v is not None}
    metric = (comparison or {}).get("metric", "efficiency")
    return {
        "requirements": requirements,
        "conditions": conditions,
        "operating_point": {"x": float(constraints.get("iout_a", 0.0)),
                            "unit": "A"},
        "phenomenon": ("efficiency_vs_load" if metric == "efficiency"
                       else f"{metric}_vs_load"),
    }


def resolve_evidence_refs(search_con: sqlite3.Connection,
                          answer: dict) -> dict:
    """R7: resolve M4 evidence references through the M5 index.

    Returns per-sha provenance views: document sha, page(s), locator kinds,
    verification states, evidence grades, human-review state (from M4's own
    fields when present). Never filesystem paths; never upgrades a
    verification state; discovery-only stays discovery-only.
    """
    shas: set[str] = set()
    evidence_ids: set[str] = set()
    for entry in answer.get("hard_eligibility", {}).get("ineligible", []):
        for rule in entry.get("rules", []):
            ev = rule.get("evidence") or {}
            if ev.get("datasheet_sha256"):
                shas.add(ev["datasheet_sha256"])
    comp = answer.get("comparison", {})
    for entry in comp.get("condition_matched_entries", []) + \
            comp.get("approximate_scenario_entries", []):
        if entry.get("evidence_id"):
            evidence_ids.add(entry["evidence_id"])
        for key in ("datasheet_sha256", "document_sha256"):
            if entry.get(key):
                shas.add(entry[key])
    resolved = {}
    for sha in shas:
        rows = search_con.execute(
            "SELECT page, locator, verification_state, retired FROM units"
            " WHERE doc_sha256=? AND retired=0 LIMIT 50", (sha,)).fetchall()
        pages = sorted({r["page"] for r in rows if r["page"] is not None})
        grades = set()
        for r in rows:
            loc = json.loads(r["locator"])
            grades.add(units_store.evidence_grade(
                doc_sha256=sha, page=r["page"], locator=loc))
        resolved[sha] = {
            "document_sha256": sha,
            "indexed_units": len(rows),
            "pages": pages[:12],
            "evidence_grades": sorted(grades),
            "verification_states": sorted(
                {r["verification_state"] for r in rows}),
            "note": "sha presence does not verify any individual quote; "
                    "per-quote states live on the units",
        }
    return {"documents": resolved,
            "curve_evidence_ids": sorted(evidence_ids),
            "resolver": "m5-search-index",
            "unresolved_evidence_ids": sorted(
                eid for eid in evidence_ids
                if not _evidence_id_known(search_con, eid))}


def _evidence_id_known(search_con: sqlite3.Connection,
                       evidence_id: str) -> bool:
    # exact PK lookup only — M4 bundle evidence ids are its own namespace;
    # anything not present as an M5 unit id is honestly reported unresolved
    # (no LIKE scans over 400K rows, no fuzzy joins).
    row = search_con.execute(
        "SELECT 1 FROM units WHERE unit_id=? LIMIT 1",
        (evidence_id,)).fetchone()
    return row is not None


def engineering_session(request: dict, *, catalog_con, search_con,
                        identity_con=None, aisle_map=None,
                        transport=None, cache: SessionCache | None = None,
                        crosswalk: dict | None = None) -> dict:
    """The orchestration endpoint logic (R5)."""
    t_start = time.time()
    timings: dict[str, float] = {}
    category = request.get("category") or "power"
    subcategory = request.get("subcategory")
    grain = request.get("discovery_grain", "opn")
    constraints = request.get("constraints") or {}
    comparison = request.get("comparison") or {}
    evidence_policy = request.get("evidence_policy",
                                  "machine_verified_advisory")
    expected = request.get("expected_releases") or {}

    # Release identities are computed CHEAPLY first (meta reads), so the
    # cache is consulted before any expensive cohort build.
    identity_snapshot_id = None
    if identity_con is not None:
        t0 = time.time()
        payload = identity_mod.build_identity_snapshot(
            catalog_con, identity_con, category=category)
        identity_snapshot_id = identity_mod.snapshot(
            identity_con, payload)["snapshot_id"]
        timings["identity_snapshot_ms"] = round((time.time() - t0) * 1000, 1)
    evidence_release = units_store.release_view(
        search_con, policy_identity=DISCOVERY_POLICY)["release"]

    cache_key = SessionCache.key(
        category=category, subcategory=subcategory, grain=grain,
        constraints=constraints, comparison=comparison,
        identity_snapshot=identity_snapshot_id,
        evidence_release=evidence_release,
        evidence_policy=evidence_policy,
        transport="http" if isinstance(transport, m4_client.HttpTransport)
        else "frozen")
    if cache is not None:
        hit = cache.get(cache_key)
        if hit is not None:
            out = dict(hit)
            out["cache"] = {"hit": True, "key": cache_key}
            out["total_ms"] = round((time.time() - t_start) * 1000, 1)
            return out

    t0 = time.time()
    m5_subcategory = SUBCATEGORY_CROSSWALK.get(subcategory, subcategory) \
        if subcategory else None
    cohort = cohort_mod.build_cohort(
        catalog_con=catalog_con, search_con=search_con,
        category=category, subcategory=m5_subcategory,
        aisle_map=aisle_map or {}, identity_con=identity_con)
    m5_constraints = {k: v for k, v in constraints.items()
                      if k in ("vendor", "family", "subcategory",
                               "vds_rating_v", "rds_on_ohm")}
    facets = cohort_mod.facets_for_cohort(cohort, constraints=m5_constraints)
    timings["cohort_build_ms"] = round((time.time() - t0) * 1000, 1)

    t0 = time.time()
    xwalk = crosswalk or load_crosswalk()
    mapping = crosswalk_map(xwalk, set(cohort["candidates"].keys()))
    timings["crosswalk_ms"] = round((time.time() - t0) * 1000, 1)

    releases = {
        "m5_evidence_release": evidence_release,
        "m5_identity_snapshot": identity_snapshot_id,
        "crosswalk_fixture_sha256": hashlib.sha256(
            json.dumps(xwalk, sort_keys=True).encode()).hexdigest()[:16],
    }

    question = build_m4_question(constraints, comparison)
    failures: list[dict] = []
    decision = None
    t0 = time.time()
    try:
        decision = m4_client.decide(question, transport)
    except m4_client.M4ServiceError as e:
        failures.append(e.structured())
    timings["m4_decision_ms"] = round((time.time() - t0) * 1000, 1)

    if decision is not None:
        answer = decision.answer
        releases["m4_decision_contract"] = decision.contract.get(
            "decision_schema")
        releases["m4_evidence_release"] = decision.contract.get(
            "m4_evidence_release")
        releases["m4_transport"] = decision.transport
        releases["comparison_fingerprint"] = hashlib.sha256(
            json.dumps(answer.get("comparison", {}), sort_keys=True,
                       default=str).encode()).hexdigest()[:16]
        exp_rel = expected.get("m4_evidence_release")
        if exp_rel and answer.get("evidence_release") != exp_rel:
            failures.append({
                "error": "release_mismatch",
                "expected": exp_rel,
                "actual": answer.get("evidence_release"),
                "action": "result marked partial; cohort preserved; "
                          "stale decision data never silently used"})

    t0 = time.time()
    evidence = resolve_evidence_refs(search_con, decision.answer) \
        if decision else {"documents": {}, "curve_evidence_ids": [],
                          "resolver": "m5-search-index",
                          "unresolved_evidence_ids": []}
    timings["evidence_resolution_ms"] = round((time.time() - t0) * 1000, 1)

    counts = (decision.answer.get("counts", {}) if decision else {})
    he = (decision.answer.get("hard_eligibility", {}) if decision
          else {})
    result = {
        "schema": ORCHESTRATION_SCHEMA,
        "session": {
            "state": session_mod.SessionState(
                category=category, subcategory=subcategory,
                discovery_grain=grain, constraints=constraints,
                canonical_units={"vin_v": "V", "vout_v": "V",
                                 "iout_a": "A"},
                releases=releases,
                cohort_count=facets["candidate_count"],
                unknown_counts=facets.get("unknown_candidates", {}),
                evidence_coverage={
                    f["dimension"]: f.get("coverage")
                    for f in facets["facets"]
                    if f.get("coverage") is not None},
                recommended_next=facets.get("recommended_next"),
            ).to_dict(),
        },
        "cohort_state": {
            "m5_candidate_count": facets["candidate_count"],
            "m5_distinct_families": facets.get("distinct_families", 0),
            "subcategory_vocabulary": {
                "request": subcategory,
                "m5_mapped": m5_subcategory,
                "crosswalk": "explicit closed table" if subcategory in
                SUBCATEGORY_CROSSWALK else "passthrough",
            },
            "m4_total_candidates": counts.get("total_candidates"),
            "m4_eligible": counts.get("hard_eligible_candidates"),
            "m4_ineligible": len(he.get("ineligible", [])),
            "m4_unknown_retained": len(he.get("unknown", [])),
            "identity_mapped_exact": len(mapping["exact"]),
            "identity_ambiguous": len(mapping["ambiguous"]),
            "identity_unmapped_m4_only": len(mapping["m4_only"]),
            "identity_unmapped_family_nodes": len(mapping["unmapped"]),
            "active_constraints": constraints,
            "grain_note": "M5 cohort counts OPNs; M4 counts its own "
                          "candidate set (opn/device/family grains declared "
                          "per candidate); grains are never mixed or "
                          "double-counted",
        },
        "candidate_results": _candidate_results(he, mapping),
        "comparisons": (decision.answer.get("comparison", {})
                        if decision else {}),
        "investigation_suggestions": _suggestions(counts, he) if decision
        else [],
        "discovery_guidance": {
            "available_facets": [
                {"dimension": f["dimension"],
                 "values": f["values"][:8],
                 "coverage": f.get("coverage"),
                 "unknown": f.get("unknown_candidates")}
                for f in facets["facets"]],
            "recommended_next": facets.get("recommended_next"),
            "recommendation_inputs": {
                "rule": "reduction x coverage (floor 0.30), deterministic",
                "cohort": facets["candidate_count"]},
        },
        "evidence": evidence,
        "releases": releases,
        "evidence_policy": evidence_policy,
        "failures": failures,
        "partial_result": bool(failures) or decision is None,
        "timings_ms": timings,
        "total_ms": round((time.time() - t_start) * 1000, 1),
        "cache": {"hit": False, "key": cache_key},
    }
    if cache is not None and not failures:
        cache.put(cache_key, result)
    return result


def _candidate_results(he: dict, mapping: dict) -> list[dict]:
    """Merged per-candidate view: M4 verdicts + crosswalk identity status.
    M5 adds identity/evidence context only — eligibility wording is M4's."""
    xwalk_by_cid = {}
    for cls in ("exact", "ambiguous", "unmapped", "m4_only"):
        for cid, row in mapping[cls].items():
            xwalk_by_cid[cid] = (cls, row)
    out = []
    for cid in he.get("eligible", []):
        cls, row = xwalk_by_cid.get(cid, ("unmapped", {}))
        out.append({"m4_candidate_id": cid,
                    "part": row.get("part"),
                    "m5_canonical_id": row.get("m5_canonical_id"),
                    "identity_class": cls,
                    "grain": row.get("grain"),
                    "engineering_eligibility": "eligible"})
    for entry in he.get("ineligible", []):
        cid = entry["candidate"]
        cls, row = xwalk_by_cid.get(cid, ("unmapped", {}))
        out.append({"m4_candidate_id": cid,
                    "part": entry.get("part"),
                    "m5_canonical_id": row.get("m5_canonical_id"),
                    "identity_class": cls,
                    "grain": entry.get("grain"),
                    "engineering_eligibility": "ineligible",
                    "explanation": [
                        {"rule": r.get("rule"), "reason": r.get("reason"),
                         "required": r.get("required"),
                         "rated": r.get("rated"),
                         "scope": r.get("scope"),
                         "evidence": r.get("evidence") or None}
                        for r in entry.get("rules", [])]})
    for entry in he.get("unknown", []):
        cid = entry["candidate"]
        cls, row = xwalk_by_cid.get(cid, ("unmapped", {}))
        out.append({"m4_candidate_id": cid,
                    "part": entry.get("part"),
                    "m5_canonical_id": row.get("m5_canonical_id"),
                    "identity_class": cls,
                    "engineering_eligibility": "unknown",
                    "missing_ratings": entry.get("missing_ratings"),
                    "note": entry.get("note")})
    return out


def _suggestions(counts: dict, he: dict) -> list[dict]:
    """Investigation suggestions derived from M4's own counts — never
    hardcoded (PRD journey C: 11 missing + 1 non-comparable + 1 unknown)."""
    out = []
    n = counts.get("candidates_missing_curve_evidence")
    if n:
        out.append({"kind": "missing_curve_evidence", "candidates": n,
                    "suggestion": "curve-evidence extraction or vendor "
                                  "datasheet acquisition for these parts"})
    n = counts.get("candidates_with_noncomparable_evidence")
    if n:
        out.append({"kind": "noncomparable_evidence", "candidates": n,
                    "suggestion": "evidence exists but conditions do not "
                                  "match; a matched-condition measurement "
                                  "or vendor curve would unlock"})
    n = len(he.get("unknown", []))
    if n:
        out.append({"kind": "ratings_unknown", "candidates": n,
                    "suggestion": "ratings missing — retained, never "
                                  "eliminated; acquisition would resolve"})
    n = counts.get("candidates_requiring_verification")
    if n:
        out.append({"kind": "verification_pending", "candidates": n,
                    "suggestion": "advisory evidence awaiting human review; "
                                  "approval is the owner's gate"})
    return out
