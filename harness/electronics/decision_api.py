"""CURVE-08B — Engineering Decision API (feature-flagged, localhost only).

Wraps the CURVE-08 decision engine (never re-implements it) in a
deterministic, versioned HTTP contract M5 can build an experience on:

  POST /v1/engineering/decisions        -> decision answer (PRD R1-R5, R7)
  GET  /v1/engineering/evidence/{id}    -> frozen evidence row resolver (R5)
  GET  /v1/engineering/health           -> flag + release identity

Conventions follow harness/discovery/api.py and harness/pipeline/api.py:
stdlib http.server, no new dependencies, JSON in/out, stable
machine-readable error codes, ``main()`` entry. This is the first
``/v1`` route in the harness; existing front doors use resource paths
(``/designs``, ``/jobs``) — the versioned prefix is a deliberate PRD
mandate, documented in CURVE_08B_ARCHITECTURE.md.

Feature flag: the decisions route answers only when
ENGINEERING_DECISIONS_ENABLED is truthy ("1"/"true"/"yes"); otherwise
404 ``feature_disabled``. Optional bearer auth via
ENGINEERING_DECISIONS_TOKEN. Default bind 127.0.0.1:8793.

Fail-closed law (R10): a missing or tampered evidence bundle is a 500
``evidence_source_unavailable`` / ``evidence_bundle_tampered`` — it can
NEVER masquerade as an empty cohort.

Determinism law: responses carry no wall-clock time and no random ids;
``request_id`` is a hash of the canonical request + release ids, so
frozen fixtures are byte-stable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from harness.electronics.candidates import (
    Candidate,
    build_power_candidates,
    load_default_inputs,
)
from harness.electronics.decision_engine import answer_question
from harness.electronics.eligibility import evaluate_cohort

DECISION_API_SCHEMA = "harness.electronics-decision-api.v1"
DECISION_API_VERSION = "1.0.0"
EVIDENCE_REF_SCHEMA = "harness.electronics-evidence-ref.v1"

# physical requirement key -> engine rule keys (a single-point VIN is a
# span demand: rated max >= V AND rated min <= V — same law as the store)
_REQUIREMENT_MAP: dict[str, list[str]] = {
    "vin_v": ["vin_min", "vin_max"],
    "vout_v": ["vout"],
    "iout_a": ["iout_min"],
    "iout_max_a": ["iout_min"],
    "temp_max_c": ["temp_max"],
    "temp_min_c": ["temp_min"],
}
_NATIVE_RULES = {"vin_min", "vin_max", "iout_min", "vout", "temp_max",
                 "temp_min"}
_METRICS = {"efficiency": "efficiency_vs_load"}
_MODES = {"condition_matched", "approximate", "condition_matched+approximate"}
_EVIDENCE_POLICIES = {"machine_verified_advisory"}

_CATEGORIES = {"power"}
_SUBCATEGORY_ALIASES = {
    "buck": "buck", "buck-converters": "buck", "buck_converter": "buck",
    "dc-dc": "buck", "dcdc": "buck", "step-down": "buck",
}

# fields of a bundle-row source locator that may cross the wire; anything
# else (notably any filesystem path) is stripped by projection
_SOURCE_FIELDS = ("document_sha256", "document_revision", "page_1based",
                  "figure_index", "caption", "source_artifact",
                  "manufacturer")


class ApiError(Exception):
    """Stable machine-readable failure (PRD R7)."""

    def __init__(self, status: int, code: str, detail: str = "",
                 extra: Optional[Mapping[str, Any]] = None):
        super().__init__(f"{code}: {detail}")
        self.status = status
        self.code = code
        self.detail = detail
        self.extra = dict(extra or {})

    def body(self) -> dict:
        err: dict[str, Any] = {"code": self.code}
        if self.detail:
            err["detail"] = self.detail
        if self.extra:
            err.update(self.extra)
        return {"error": err}


# --- service state (immutable after load; SHA-verified, fail closed) ------

class ServiceState:
    """Frozen inputs + verified identity. Loaded once per repo root."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        bundle_path = (repo_root / "tests/fixtures/m4-handoff/"
                       "curve_evidence_bundle_v3.jsonl")
        manifest_path = (repo_root / "tests/fixtures/m4-handoff/"
                         "curve_evidence_bundle_v3_manifest.json")
        if not bundle_path.exists() or not manifest_path.exists():
            raise ApiError(500, "evidence_source_unavailable",
                           "evidence bundle or manifest missing under the "
                           "repository; failing closed — never an empty "
                           "cohort")
        manifest = json.loads(manifest_path.read_text())
        digest = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
        if digest != manifest.get("bundle_sha256"):
            raise ApiError(500, "evidence_bundle_tampered",
                           "bundle sha256 does not match the manifest pin; "
                           "failing closed")
        self.manifest = manifest
        self.rows = [json.loads(line) for line in
                     bundle_path.read_text().splitlines() if line.strip()]
        self.rows_by_id = {r["evidence_id"]: r for r in self.rows}
        catalog, bundle, packets, ratings = load_default_inputs(repo_root)
        self.catalog_doc = catalog
        self.catalog_by_id = {row.get("id"): row for row in catalog}
        self.candidates = list(build_power_candidates(
            catalog, bundle, packets, ratings).values())
        self.by_id = {c.candidate_id: c for c in self.candidates}
        self.by_part = {c.device or c.opn: c for c in self.candidates
                        if (c.device or c.opn)}
        slice_doc = json.loads(
            (repo_root / "tests/fixtures/gold/curve_evidence_pilot/"
             "_catalog_slice.json").read_text())
        self.catalog_release = {
            "source": slice_doc.get("source"),
            "source_sha256": slice_doc.get("source_sha256"),
            "frozen_at": slice_doc.get("frozen_at"),
            "rows": len(slice_doc.get("rows") or []),
        }

    def release_identity(self) -> dict:
        return {
            "evidence_release_id": self.manifest["release_id"],
            "candidate_catalog_release": dict(self.catalog_release),
            "comparator_fingerprint":
                self.manifest.get("comparator_fingerprint"),
            "bundle_sha256": self.manifest["bundle_sha256"],
            "decision_contract_version": DECISION_API_VERSION,
            "decision_schema": DECISION_API_SCHEMA,
        }


_STATE: dict[Path, ServiceState] = {}


def service_state(repo_root: Path) -> ServiceState:
    st = _STATE.get(repo_root)
    if st is None:
        st = ServiceState(repo_root)
        _STATE[repo_root] = st
    return st


# --- request validation + normalization -----------------------------------

def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ApiError(400, "invalid_request",
                       f"{name} must be a number, got {value!r}")
    return float(value)


def normalize_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the wire request and produce the engine question plus
    service options. Raises ApiError with stable codes on every R7
    failure state that is a request-side error."""

    if not isinstance(payload, dict):
        raise ApiError(400, "invalid_request", "body must be a JSON object")

    category = str(payload.get("category") or "").strip().lower()
    if not category:
        raise ApiError(400, "invalid_request", "category is required")
    if category not in _CATEGORIES:
        raise ApiError(422, "unsupported_category",
                       f"category {category!r} is not served; supported: "
                       f"{sorted(_CATEGORIES)}")
    sub_raw = str(payload.get("subcategory") or "buck").strip().lower()
    subcategory = _SUBCATEGORY_ALIASES.get(sub_raw)
    if subcategory is None:
        raise ApiError(422, "unsupported_category",
                       f"subcategory {sub_raw!r} is not served for "
                       f"{category}; supported: buck (buck-converters, "
                       "dc-dc, step-down)")

    requirements_in = payload.get("requirements")
    if not isinstance(requirements_in, dict) or not requirements_in:
        raise ApiError(400, "invalid_request",
                       "requirements object with at least one numeric "
                       "requirement is required")
    rules: dict[str, float] = {}
    conditions: dict[str, float] = {}
    for key, value in requirements_in.items():
        if value is None:
            continue
        v = _number(value, f"requirements.{key}")
        if key in _REQUIREMENT_MAP:
            for rule in _REQUIREMENT_MAP[key]:
                rules[rule] = v
            if key in ("vin_v", "vout_v"):
                conditions[key] = v
        elif key in _NATIVE_RULES:
            rules[key] = v
            if key == "vout":
                conditions["vout_v"] = v
            elif key in ("vin_min", "vin_max") and "vin_v" not in conditions:
                conditions["vin_v"] = v
        else:
            raise ApiError(422, "unsupported_condition",
                           f"requirement key {key!r} is not an understood "
                           "engineering condition; physical keys: "
                           "vin_v, vout_v, iout_a, temp_max_c; rule keys: "
                           "vin_min, vin_max, iout_min, vout, temp_max")

    comparison = payload.get("comparison")
    approximate = False
    metric = None
    operating_point = None
    parametric_only = comparison is None
    if comparison is not None:
        if not isinstance(comparison, dict):
            raise ApiError(400, "invalid_request",
                           "comparison must be an object")
        metric_raw = str(comparison.get("metric") or "").strip().lower()
        if metric_raw not in _METRICS:
            raise ApiError(422, "unsupported_metric",
                           f"comparison metric {metric_raw!r} unsupported; "
                           f"supported: {sorted(_METRICS)}")
        metric = _METRICS[metric_raw]
        mode = str(comparison.get("mode") or "condition_matched").strip()
        if mode not in _MODES:
            raise ApiError(422, "unsupported_comparison_mode",
                           f"comparison mode {mode!r} unsupported; "
                           f"supported: {sorted(_MODES)}")
        approximate = "approximate" in mode
        op = comparison.get("operating_point")
        if op is None:
            x = rules.get("iout_min")
            if x is None:
                raise ApiError(400, "operating_point_required",
                               "comparison requested but no load point: "
                               "pass iout_a in requirements or "
                               "comparison.operating_point")
            operating_point = {"x": x, "unit": "A"}
        else:
            if not isinstance(op, dict) or "x" not in op:
                raise ApiError(400, "invalid_request",
                               "comparison.operating_point needs {x, unit}")
            operating_point = {"x": _number(op["x"], "operating_point.x"),
                               "unit": op.get("unit") or "A"}
        extra_conditions = comparison.get("conditions")
        if isinstance(extra_conditions, dict):
            for k, v in extra_conditions.items():
                if v is not None:
                    conditions[k] = _number(v, f"comparison.conditions.{k}")

    policy = payload.get("evidence_policy", "machine_verified_advisory")
    if policy not in _EVIDENCE_POLICIES:
        raise ApiError(422, "unsupported_evidence_policy",
                       f"evidence_policy {policy!r} unsupported; every "
                       "served value is machine-verified advisory (0/123 "
                       "human-approved); supported: "
                       f"{sorted(_EVIDENCE_POLICIES)}")

    return {
        "category": category,
        "subcategory": subcategory,
        "rules": rules,
        "conditions": conditions,
        "metric": metric,
        "approximate": approximate,
        "operating_point": operating_point,
        "parametric_only": parametric_only,
        "evidence_policy": policy,
        "include_unknowns": bool(payload.get("include_unknowns", True)),
        "cohort": payload.get("cohort"),
    }


def _select_cohort(state: ServiceState,
                   cohort: Any) -> list[Candidate]:
    if not cohort:
        return state.candidates
    if not isinstance(cohort, list):
        raise ApiError(400, "invalid_request",
                       "cohort must be a list of part names or candidate "
                       "ids")
    chosen, unknown = [], []
    for name in cohort:
        cand = state.by_id.get(str(name)) or state.by_part.get(str(name))
        if cand is None:
            unknown.append(str(name))
        elif cand not in chosen:
            chosen.append(cand)
    if unknown:
        raise ApiError(422, "unknown_candidate",
                       "cohort names candidates that do not exist in the "
                       "frozen candidate universe",
                       extra={"unknown": unknown})
    return chosen


# --- projection (engine answer -> wire contract, paths stripped) ----------

def _cite_rule_sources(candidates: Sequence[Candidate],
                       eligibility: Mapping[str, dict],
                       state: "ServiceState") -> None:
    """PRD R3: every hard exclusion cites its source. Rules whose rated
    value came from the catalog slice carry no inline quote; enrich them
    in place with the catalog provenance (record id + datasheet url) so
    no exclusion is ever source-less on the wire."""

    for cand in candidates:
        verdict = eligibility.get(cand.candidate_id)
        if not verdict:
            continue
        catalog_refs = [e for e in cand.evidence_refs
                        if e.get("kind") == "catalog"]
        for rule in verdict.get("rules", []):
            if rule.get("evidence"):
                continue
            ref = catalog_refs[0] if catalog_refs else None
            row = state.catalog_by_id.get(
                (ref or {}).get("record_id")) if ref else None
            rule["evidence"] = {
                "kind": "catalog_slice",
                "grade": "evidence",
                "authority": cand.source_authority,
                "record_id": (ref or {}).get("record_id"),
                "datasheet_url": (row or {}).get("datasheet_url"),
                "note": "rating from the frozen provenance-stamped "
                        "catalog slice (no datasheet quote captured "
                        "for this axis yet)",
            }


def _explain(verdict: Mapping[str, Any]) -> str:
    if verdict["verdict"] == "ineligible":
        return "; ".join(r.get("reason") or r.get("text") or r["rule"]
                         for r in verdict["rules"]
                         if r["verdict"] == "ineligible")
    unknown = [r["rule"] for r in verdict["rules"]
               if r["verdict"] == "unknown"]
    if unknown:
        return ("electrically unconfirmed — missing ratings: "
                + ", ".join(unknown) + " (unknown is never elimination)")
    return (f"all {len(verdict['rules'])} stated hard rules satisfied by "
            "ratings evidence")


def _sanitized_source(source: Mapping[str, Any]) -> dict:
    out = {k: source.get(k) for k in _SOURCE_FIELDS}
    out["locator"] = {
        "page_1based": (source.get("locator") or {}).get("page_1based"),
        "figure_index": (source.get("locator") or {}).get("figure_index"),
        "series_index": (source.get("locator") or {}).get("series_index"),
    }
    return {k: v for k, v in out.items() if v is not None or k == "locator"}


def _evidence_ref(row: Mapping[str, Any]) -> dict:
    """Versioned evidence-reference contract (PRD R5): sha, revision,
    page, figure/series locator, series identity, supporting points,
    original conditions, status. Locators are only ever what the frozen
    row carries — none are manufactured."""

    src = row.get("source") or {}
    curve = row.get("curve") or {}
    cond = row.get("conditions") or {}
    return {
        "schema": EVIDENCE_REF_SCHEMA,
        "evidence_id": row.get("evidence_id"),
        "document_sha256": src.get("document_sha256"),
        "document_revision": src.get("document_revision"),
        "page_1based": src.get("page_1based"),
        "figure_index": src.get("figure_index"),
        "series_index": (src.get("locator") or {}).get("series_index"),
        "caption": src.get("caption"),
        "source_artifact": src.get("source_artifact"),
        "series_name": curve.get("series_name"),
        "phenomenon": row.get("phenomenon"),
        "supporting_points": curve.get("points") or [],
        "original_conditions": {
            "keys": cond.get("keys") or {},
            "verbatim": cond.get("verbatim") or [],
        },
        "evidence_class": row.get("evidence_class"),
        "adjudication_state": (row.get("adjudication") or {}).get("state"),
        "advisory": True,
        "guarantee": False,
    }


def _candidate_record(cand: Candidate, verdict: Mapping[str, Any],
                      avail: Optional[Mapping[str, Any]]) -> dict:
    missing_ratings = [r["rule"] for r in verdict.get("rules", [])
                       if r["verdict"] == "unknown"]
    states = sorted(cand.adjudication_states)
    return {
        "candidate_id": cand.candidate_id,
        "manufacturer": cand.manufacturer,
        "category": cand.category,
        "subcategory": cand.subcategory,
        "family": cand.family,
        "series": cand.series,
        "device": cand.device,
        "opn": cand.opn,
        "grain": cand.grain,
        "hard_eligibility": {
            "verdict": verdict["verdict"],
            "rules": verdict.get("rules", []),
            "parametric_ok": verdict.get("parametric_ok"),
        },
        "explanation": _explain(verdict),
        "missing_ratings": missing_ratings,
        "evidence_quality": {
            "source_authority": cand.source_authority,
            "adjudication_states": states,
            "human_approved": states == ["human_approved"],
            "curve_evidence_rows": len(cand.curve_evidence_ids),
            "evidence_refs": [
                {k: v for k, v in ref.items()
                 if k not in ("evidence",)}
                for ref in cand.evidence_refs
            ],
        },
        "evidence_availability": (
            {"state": "not_evaluated",
             "reason": "ineligible candidates never consult curves"}
            if verdict["verdict"] == "ineligible" else
            {"state": (avail or {}).get("state", "missing"),
             "rows": (avail or {}).get("rows", 0),
             "refusals": (avail or {}).get("refusals", [])}
        ),
        "membership": cand.membership or None,
    }


def _comparison_record(entry: Mapping[str, Any],
                       level: Mapping[str, Any]) -> dict:
    row_source = entry.get("source") or {}
    interp = entry.get("interpolation") or {}
    return {
        "candidate_id": entry["candidate"],
        "part": entry.get("part"),
        "family": entry.get("family"),
        "manufacturer": entry.get("manufacturer"),
        "series": entry.get("series"),
        "result": {
            "value": entry.get("value"),
            "unit": entry.get("unit"),
            "metric": "efficiency",
            "guarantee": False,
            "typical": True,
        },
        "operating_conditions": {
            "x": interp.get("operating_point"),
            "matched": entry.get("matched_conditions"),
        },
        "eligibility": {
            "verdict": entry.get("eligibility_verdict"),
            "confirmed_eligible":
                entry.get("eligibility_verdict") == "eligible",
        },
        "human_review": {
            "adjudication_state": entry.get("adjudication_state"),
            "advisory": True,
        },
        "evidence": {
            "schema": EVIDENCE_REF_SCHEMA,
            "evidence_id": entry.get("evidence_id"),
            **_sanitized_source(row_source),
            "series_name": entry.get("series"),
            "phenomenon": "efficiency_vs_load",
            "matched_conditions": entry.get("matched_conditions"),
            "evidence_class": entry.get("evidence_class"),
            "adjudication_state": entry.get("adjudication_state"),
            "advisory": True,
            "guarantee": False,
            "note": "supporting points for THIS query ride "
                    "interpolation.supporting_points",
        },
        "interpolation": {
            "method": interp.get("method"),
            "supporting_points": interp.get("supporting_points"),
            "uncertainty": interp.get("uncertainty"),
            "limitations": interp.get("limitations"),
            "source_curve_id": entry.get("evidence_id"),
        },
        "comparability": {
            "level_achieved": level.get("achieved_level"),
            "levels_present": level.get("levels_present"),
            "families": level.get("families"),
            "manufacturers": level.get("manufacturers"),
            "devices": level.get("devices"),
        },
        "thermal": entry.get("thermal"),
    }


_SUGGESTION_BY_REASON = {
    "legend_ambiguity":
        "held curve refused: trace legend ambiguous — needs cold human "
        "review before it can support any comparison",
    "out_of_range":
        "operating point sits outside the digitized support of the held "
        "curve; acquiring a wider-load figure or an approved engineering "
        "model is required — extrapolation is refused",
    "condition_mismatch":
        "held curves were measured at different conditions than "
        "requested; acquire curves at the requested conditions or "
        "restate the question at held conditions",
    "no operating point supplied":
        "no load point supplied for a curve query",
}


def _investigation_suggestions(eligible_candidates, availability,
                               unknowns) -> list[dict]:
    out = []
    for cand in eligible_candidates:
        avail = availability.get(cand.candidate_id) or {}
        if avail.get("state") == "comparable":
            continue
        if avail.get("state") == "missing":
            out.append({
                "candidate_id": cand.candidate_id,
                "part": cand.device or cand.opn,
                "situation": "no_curve_evidence_held",
                "suggestion": (
                    "parametrically eligible but no digitized efficiency "
                    "curve is held for this part; acquisition target for "
                    "the evidence factory"),
            })
        else:
            for refusal in avail.get("refusals", [])[:2]:
                reason = refusal.get("reason") or ""
                key = next((k for k in _SUGGESTION_BY_REASON
                            if k in reason), None)
                out.append({
                    "candidate_id": cand.candidate_id,
                    "part": cand.device or cand.opn,
                    "situation": "evidence_not_comparable",
                    "evidence_id": refusal.get("evidence_id"),
                    "suggestion": _SUGGESTION_BY_REASON.get(
                        key, f"held evidence refused: {reason}"),
                })
    for u in unknowns:
        out.append({
            "candidate_id": u.get("candidate_id") or u.get("candidate"),
            "part": u.get("part"),
            "situation": "ratings_unknown",
            "suggestion": (
                "missing ratings keep this candidate unknown — it is "
                "retained for investigation, never eliminated; a "
                "datasheet ratings pass would resolve it"),
        })
    return out


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def handle_decision_request(payload: Mapping[str, Any],
                            repo_root: Optional[Path] = None
                            ) -> tuple[int, dict]:
    """Pure service entry: validated request -> (http_status, body)."""

    repo_root = Path(repo_root or Path(__file__).resolve().parents[2])

    # CURVE-08C (R5): M5 orchestration dialect. Bodies of the shape
    # {"question": {...}} are answered by the canonical CURVE-08 producer
    # answer_question() — the exact function M5's frozen journey fixtures
    # (m4_frozen_journeys_v1.json, sha 095ebd9c...) were generated from.
    # Pure dialect routing: zero new engineering logic, zero engine edits;
    # the native decision-api.v1 path below is unchanged.
    question = payload.get("question") if isinstance(payload, dict) else None
    if isinstance(question, dict) and question:
        return 200, answer_question(question, repo_root=repo_root)

    state = service_state(repo_root)
    norm = normalize_request(payload)

    cohort = _select_cohort(state, norm["cohort"])
    eligibility = evaluate_cohort(cohort, norm["rules"])
    _cite_rule_sources(cohort, eligibility, state)

    request_id = hashlib.sha256(_canonical(
        {"request": payload, "release": state.release_identity()}
    ).encode()).hexdigest()[:16]

    base = {
        "schema": DECISION_API_SCHEMA,
        "decision_contract_version": DECISION_API_VERSION,
        "request_id": request_id,
        "release": state.release_identity(),
        "question": {
            "category": norm["category"],
            "subcategory": norm["subcategory"],
            "requirements": norm["rules"],
            "comparison_conditions": norm["conditions"],
            "metric": "efficiency" if norm["metric"] else None,
            "operating_point": norm["operating_point"],
            "evidence_policy": norm["evidence_policy"],
            "evidence_mode": ("parametric_only" if norm["parametric_only"]
                              else "parametrics_plus_curves"),
            "include_unknowns": norm["include_unknowns"],
        },
    }

    if norm["parametric_only"]:
        # R9 arm A: identity + hard eligibility only; evidence is not
        # attempted, so evidence counts are reported as not-attempted.
        records = [_candidate_record(c, eligibility[c.candidate_id], None)
                   for c in cohort
                   if norm["include_unknowns"]
                   or eligibility[c.candidate_id]["verdict"] != "unknown"]
        hard_unknown = sum(1 for v in eligibility.values()
                           if v["verdict"] == "unknown")
        counts = {
            "total_candidates": len(cohort),
            "hard_eligible": sum(1 for v in eligibility.values()
                                 if v["verdict"] == "eligible"),
            "hard_ineligible": sum(1 for v in eligibility.values()
                                   if v["verdict"] == "ineligible"),
            "hard_unknown": hard_unknown,
            "with_comparable_evidence": None,
            "with_noncomparable_evidence": None,
            "missing_evidence": None,
            "pending_human_verification": None,
            "evidence_rows_attached": sum(
                len(c.curve_evidence_ids) for c in cohort),
            "partitions": _PARTITIONS_NOTE,
            "evidence_attempted": False,
        }
        return 200, {
            **base,
            "counts": counts,
            "candidates": records,
            "ineligible": [
                {"candidate_id": c.candidate_id,
                 "part": c.device or c.opn,
                 "grain": c.grain,
                 "rules": [r for r in eligibility[c.candidate_id]["rules"]
                           if r["verdict"] == "ineligible"]}
                for c in cohort
                if eligibility[c.candidate_id]["verdict"] == "ineligible"],
            "unknowns": [
                {"candidate_id": c.candidate_id, "part": c.device or c.opn,
                 "grain": c.grain,
                 "missing_ratings": [
                     r["rule"] for r in eligibility[c.candidate_id]["rules"]
                     if r["verdict"] == "unknown"]}
                for c in cohort
                if eligibility[c.candidate_id]["verdict"] == "unknown"],
            "comparisons": None,
            "investigation": [],
            "laws": _LAWS,
        }

    question = {
        "requirements": norm["rules"],
        "conditions": norm["conditions"] or None,
        "operating_point": norm["operating_point"],
        "phenomenon": norm["metric"],
        "approximate": norm["approximate"],
    }
    answer = answer_question(
        question, repo_root=repo_root, candidates=cohort, rows=state.rows)

    level = answer["comparison"]["comparison_level"]
    matched = [_comparison_record(e, level) for e in
               answer["comparison"]["condition_matched_entries"]]
    approximate_scenarios = answer["comparison"].get(
        "approximate_scenario_entries") or []

    availability = answer["evidence_availability"]
    engine_counts = answer["counts"]

    ineligible_records = [
        {"candidate_id": c.candidate_id,
         "part": c.device or c.opn,
         "family": c.family,
         "grain": c.grain,
         "rules": [r for r in eligibility[c.candidate_id]["rules"]
                   if r["verdict"] == "ineligible"]}
        for c in cohort
        if eligibility[c.candidate_id]["verdict"] == "ineligible"]
    unknown_records = [
        {"candidate_id": c.candidate_id,
         "part": c.device or c.opn,
         "family": c.family,
         "grain": c.grain,
         "missing_ratings": [
             r["rule"] for r in eligibility[c.candidate_id]["rules"]
             if r["verdict"] == "unknown"],
         "note": "electrically unconfirmed; retained, never eliminated"}
        for c in cohort
        if eligibility[c.candidate_id]["verdict"] == "unknown"]

    keep = lambda c: (norm["include_unknowns"] or
                      eligibility[c.candidate_id]["verdict"] != "unknown")
    records = [_candidate_record(
        c, eligibility[c.candidate_id],
        availability.get(c.candidate_id)) for c in cohort if keep(c)]

    pending = sum(
        1 for c in cohort
        if (c.adjudication_states
            and c.adjudication_states != {"human_approved"})
        or c.source_authority == "admission_pending")
    counts = {
        "total_candidates": engine_counts["total_candidates"],
        "hard_eligible": engine_counts["hard_eligible_candidates"],
        "hard_ineligible": len(answer["hard_eligibility"]["ineligible"]),
        "hard_unknown": len(unknown_records),
        "with_comparable_evidence":
            engine_counts["candidates_with_comparable_curve_evidence"],
        "with_noncomparable_evidence":
            engine_counts["candidates_with_noncomparable_evidence"],
        "missing_evidence": engine_counts["candidates_missing_curve_evidence"],
        "pending_human_verification": pending,
        "evidence_rows_attached": engine_counts["evidence_rows_attached"],
        "partitions": _PARTITIONS_NOTE,
        "evidence_attempted": True,
    }

    investigation = _investigation_suggestions(
        [c for c in cohort
         if eligibility[c.candidate_id]["verdict"] == "eligible"],
        availability, unknown_records)

    return 200, {
        **base,
        "counts": counts,
        "candidates": records,
        "ineligible": ineligible_records,
        "unknowns": unknown_records,
        "comparisons": {
            "condition_matched": matched,
            "comparison_level": level,
            "approximate_scenarios": approximate_scenarios,
            "ranking_note": "entries are ordered by measured value; "
                            "unknown-eligibility entries are labeled and "
                            "never counted as confirmed eligible",
        },
        "investigation": investigation,
        "laws": _LAWS,
    }


_PARTITIONS_NOTE = (
    "mutually exclusive: hard_eligible | hard_ineligible | hard_unknown "
    "(one verdict per candidate at one grain). Overlapping evidence "
    "status counters (with_comparable_evidence, "
    "with_noncomparable_evidence, missing_evidence, "
    "pending_human_verification) describe evidence attachment and review "
    "state and MAY coincide with any verdict; a candidate can be "
    "comparable and still pending human review"
)

_LAWS = [
    "hard eligibility precedes every curve comparison; curves never "
    "eliminate and never rescue",
    "missing ratings are unknown, never failure",
    "typical values never guarantee performance",
    "interpolation only inside the digitized domain; extrapolation "
    "refused with reason",
    "five comparison levels reported, never merged",
    "approximate scenarios labeled separately from condition-matched "
    "evidence",
    "candidate identity is independent of document count; evidence rows "
    "never inflate candidate counts",
    "every value is machine-verified advisory until a human approves it",
    "missing or tampered evidence fails closed; it never masquerades as "
    "an empty cohort",
]


# --- HTTP front door (stdlib; feature-flagged; localhost default) ---------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False,
                          default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        return self.auth_status() is None

    def auth_status(self) -> Optional[int]:
        """CURVE-08C remediation #5: distinguish 401 (no bearer presented)
        from 403 (a bearer was presented but is invalid). Returns None when
        authorized. No token configured => auth disabled (localhost/flag
        posture unchanged)."""
        token = os.environ.get("ENGINEERING_DECISIONS_TOKEN")
        if not token:
            return None
        header = self.headers.get("Authorization") or ""
        if not header:
            return 401
        if header != f"Bearer {token}":
            return 403
        return None

    def do_GET(self):
        route = self.path.partition("?")[0]
        try:
            if route == "/v1/engineering/health":
                repo_root = Path(os.environ.get(
                    "HARNESS_REPO_ROOT",
                    str(Path(__file__).resolve().parents[2])))
                try:
                    state = service_state(repo_root)
                    ident = state.release_identity()
                    ident["bundle_rows"] = len(state.rows)
                except ApiError as e:
                    return self._json(200, {
                        "service": DECISION_API_SCHEMA,
                        "version": DECISION_API_VERSION,
                        "feature_enabled": _flag(),
                        "evidence": {"state": "unavailable",
                                     "code": e.code, "detail": e.detail}})
                return self._json(200, {
                    "service": DECISION_API_SCHEMA,
                    "version": DECISION_API_VERSION,
                    "feature_enabled": _flag(),
                    "evidence": {"state": "ok", **ident}})
            m = re.match(r"^/v1/engineering/evidence/([\w\-]+)$", route)
            if m:
                status = self.auth_status()
                if status is not None:
                    return self._json(status, {"error": {
                        "code": ("unauthorized" if status == 401
                                 else "forbidden"),
                        "detail": ("bearer token required" if status == 401
                                   else "invalid bearer token")}})
                repo_root = Path(os.environ.get(
                    "HARNESS_REPO_ROOT",
                    str(Path(__file__).resolve().parents[2])))
                state = service_state(repo_root)
                row = state.rows_by_id.get(m.group(1))
                if row is None:
                    return self._json(404, {"error": {
                        "code": "evidence_not_found",
                        "detail": "no frozen evidence row with that id"}})
                return self._json(200, {
                    "schema": EVIDENCE_REF_SCHEMA,
                    "evidence": _evidence_ref(row),
                    "supported_region": row.get("supported_region"),
                    "binding": row.get("binding"),
                    "operating_point_query": row.get(
                        "operating_point_query"),
                })
            return self._json(404, {"error": {"code": "unknown_route"}})
        except ApiError as e:
            return self._json(e.status, e.body())
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"error": {
                "code": "internal_error", "detail": str(e)}})

    def do_POST(self):
        auth = self.auth_status()
        if auth is not None:
            return self._json(auth, {"error": {
                "code": ("unauthorized" if auth == 401 else "forbidden"),
                "detail": ("bearer token required" if auth == 401
                           else "invalid bearer token")}})
        if not _flag():
            return self._json(404, {"error": {
                "code": "feature_disabled",
                "detail": "ENGINEERING_DECISIONS_ENABLED is not set; the "
                          "decisions route stays dark"}})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(n) or b"null")
        except json.JSONDecodeError:
            return self._json(400, {"error": {
                "code": "invalid_json",
                "detail": "request body is not valid JSON"}})
        try:
            status, body = handle_decision_request(payload)
            return self._json(status, body)
        except ApiError as e:
            return self._json(e.status, e.body())
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"error": {
                "code": "internal_error", "detail": str(e)}})


def _flag() -> bool:
    return os.environ.get(
        "ENGINEERING_DECISIONS_ENABLED", "").strip().lower() in (
        "1", "true", "yes")


def main(listen=None):
    host = os.environ.get("ENGINEERING_DECISIONS_HOST", "127.0.0.1")
    port = int(os.environ.get("ENGINEERING_DECISIONS_PORT", "8793"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
