"""B6 implementation support: generated identity crosswalk + explicit
candidate transport. Kept in its own module so the production identity
layer is inspectable and testable apart from session orchestration.

Owner-approved architecture (DISCOVERY-01 B6): M5 owns candidate
generation; M4 ranks ONLY the candidate set explicitly supplied by M5.
A pinned fixture is not an acceptable production identity layer, so the
mapping here is generated from the recorded identity store on every
request: deterministic, versioned, release-bound, evidence-backed, with
unmapped candidates left explicit and no fuzzy joins of any kind.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Iterable, Sequence

CANDIDATE_SCOPE_SCHEMA = "harness.discovery01-candidate-scope.v1"
IDENTITY_CROSSWALK_SCHEMA = "harness.search-identity-crosswalk.v1"
IDENTITY_CROSSWALK_VERSION = "m5-generated-identity-crosswalk.v1"

RANKING_POPULATIONS = ("discovery_cohort", "m4_bundle")
DEFAULT_RANKING_POPULATION = "discovery_cohort"

# Fail closed rather than silently truncating: a population this large must
# be narrowed by facets first, and the response says so.
MAX_CANDIDATE_SCOPE = int(os.environ.get("SEARCH_MAX_CANDIDATE_SCOPE",
                                         "20000"))

OUT_OF_COHORT_DISCLOSURE = (
    "ranking comes from M4's own evidence universe and is not "
    "facet-constrained by the M5 discovery cohort; retained for parity and "
    "migration only, labeled out_of_cohort, and production promotion stays "
    "blocked while it is the normal path")

_MATCH_RULE = ("exact canonical id, else exact OPN/device, else "
               "case-insensitive exact; never fuzzy, never name-similar")


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True,
                                     default=str).encode()).hexdigest()


def _row_get(row, key):
    """Read a column from a sqlite3.Row (or mapping) that may be absent."""
    if row is None:
        return None
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        return None


def _unknown_attributes(cand) -> bool:
    """True when the candidate carries unknown/absent typed values, so it
    travels as retained-unknown rather than as a confirmed fit."""
    attrs = getattr(cand, "attributes", None) or {}
    if not attrs:
        return True
    return any((v or {}).get("value") is None
               and (v or {}).get("kind") != "interval"
               for v in attrs.values())


def build_identity_crosswalk(identity_con, candidates: Sequence[Any], *,
                             identity_snapshot_id: str | None = None,
                             evidence_release: str | None = None,
                             category: str | None = None,
                             subcategory: str | None = None) -> dict:
    """Generate the M5 identity mapping for exactly these candidates.

    Sources: recorded identity-store relationships (family and series,
    with authority, source sha, locator rule and status) plus the catalog's
    resolved vendor. Nothing is inferred from names. A candidate with no
    recorded relationship stays unmapped and is counted; multiple distinct
    non-conflicting families are ambiguous and never collapsed.
    """
    by_child: dict[str, list] = {}
    if identity_con is not None:
        for row in identity_con.execute(
                "SELECT r.child_id, r.status, r.conflict_status,"
                " r.authority, r.source_sha256, r.source_locator,"
                " r.norm_rule, i.identity_id, i.kind, i.label"
                " FROM relationships r JOIN identities i"
                " ON i.identity_id = r.parent_id"
                " WHERE r.rel_type='contains'"
                " AND i.kind IN ('family','series')"):
            by_child.setdefault(row["child_id"], []).append(row)

    mappings: dict[str, dict] = {}
    coverage = {"total": 0, "mapped": 0, "unmapped": 0, "ambiguous": 0,
                "conflicting": 0, "with_series": 0,
                "verified_memberships": 0}
    for cand in candidates:
        cid = f"opn:{cand.opn}"
        coverage["total"] += 1
        rows = by_child.get(cid, [])
        fams = [r for r in rows if r["kind"] == "family"]
        clean = [r for r in fams if not r["conflict_status"]]
        series = [r for r in rows if r["kind"] == "series"
                  and not r["conflict_status"]]
        ambiguous = len(clean) > 1
        conflicting = bool(fams) and not clean
        fam = clean[0] if len(clean) == 1 else None
        ser = series[0] if len(series) == 1 else None
        if fam is not None:
            coverage["mapped"] += 1
            if fam["status"] == "verified":
                coverage["verified_memberships"] += 1
        elif ambiguous:
            coverage["ambiguous"] += 1
        elif conflicting:
            coverage["conflicting"] += 1
        else:
            coverage["unmapped"] += 1
        if ser is not None:
            coverage["with_series"] += 1
        mappings[cid] = {
            "canonical_id": cid,
            "opn": cand.opn,
            "manufacturer": (getattr(cand, "vendor_canonical", None)
                             or getattr(cand, "vendor_raw", None)),
            "grain": "opn",
            "identity": {
                "mapped": fam is not None,
                "family_id": fam["identity_id"] if fam is not None else None,
                "family_label": fam["label"] if fam is not None else None,
                "family_status": fam["status"] if fam is not None else None,
                "family_authority": fam["authority"] if fam is not None
                else None,
                "series_id": ser["identity_id"] if ser is not None else None,
                "series_label": ser["label"] if ser is not None else None,
                "series_status": ser["status"] if ser is not None else None,
                "ambiguous_families": sorted(r["identity_id"] for r in clean)
                if ambiguous else [],
                "conflicting": conflicting,
                "source_sha256": _row_get(fam if fam is not None else ser,
                                           "source_sha256"),
                "source_locator": _row_get(fam if fam is not None else ser,
                                           "source_locator"),
                "norm_rule": _row_get(fam if fam is not None else ser,
                                      "norm_rule"),
                "identity_source": ("identity-store:relationships"
                                    if rows else "none"),
            },
            "provenance": {
                "evidence_units": len(getattr(cand, "evidence_unit_ids", [])
                                      or []),
                "evidence_grade_units": getattr(cand, "evidence_grade_units",
                                                0),
                "discovery_only_units": getattr(cand, "discovery_only_units",
                                               0),
            },
        }

    generated_from = {
        "identity_snapshot": identity_snapshot_id,
        "m5_evidence_release": evidence_release,
        "category": category,
        "subcategory": subcategory,
        "identity_rows_considered": sum(len(v) for v in by_child.values()),
    }
    body = {"schema": IDENTITY_CROSSWALK_SCHEMA,
            "version": IDENTITY_CROSSWALK_VERSION,
            "generated_from": generated_from,
            "coverage": coverage,
            "mappings": mappings}
    body["sha256"] = _sha({k: v for k, v in body.items() if k != "sha256"})
    return body


def build_candidate_scope(candidates: Sequence[Any], crosswalk: dict, *,
                          category: str | None,
                          subcategory: str | None,
                          constraints: dict,
                          evidence_release: str | None,
                          identity_snapshot_id: str | None,
                          unknown_axes: dict | None = None) -> dict:
    """The explicit candidate handoff: exactly the discovery population,
    each entry self-identifying, identity-mapped and release-bound.

    M5 states no engineering verdict here — ``eligibility_state`` is a
    discovery-level statement (sent for evaluation, or retained with
    unknown attributes). Hard eligibility and ranking remain M4's.
    """
    unknown_axes = unknown_axes or {}
    entries = []
    for cand in candidates:
        cid = f"opn:{cand.opn}"
        mapping = crosswalk["mappings"].get(cid) or {}
        unplaced = sorted(axis for axis, opns in unknown_axes.items()
                          if cand.opn in opns)
        entry = {
            "canonical_id": cid,
            "opn": cand.opn,
            "manufacturer": mapping.get("manufacturer"),
            "grain": "opn",
            "eligibility_state": ("unknown_attributes_retained"
                                  if (unplaced or _unknown_attributes(cand))
                                  else "sent_for_evaluation"),
            "unknown_axes": unplaced,
            "identity": mapping.get("identity") or {"mapped": False,
                                                    "identity_source":
                                                    "none"},
            "provenance": mapping.get("provenance") or {},
        }
        entries.append(entry)
    scope = {
        "schema": CANDIDATE_SCOPE_SCHEMA,
        "population": "discovery_cohort",
        "crosswalk": {"schema": crosswalk["schema"],
                      "version": crosswalk["version"],
                      "sha256": crosswalk["sha256"],
                      "source": "generated:identity-store",
                      "coverage": crosswalk["coverage"]},
        "releases": {"m5_evidence_release": evidence_release,
                     "m5_identity_snapshot": identity_snapshot_id},
        "selection": {"category": category, "subcategory": subcategory,
                      "constraints": constraints,
                      "unknown_retention": "candidates with unknown or "
                                           "absent axis values are sent and "
                                           "never eliminated by M5",
                      "match_rule": _MATCH_RULE},
        "candidates": entries,
        "count": len(entries),
        "unknown_unplaced_included": sum(
            1 for e in entries if e["unknown_axes"]),
    }
    scope["sha256"] = _sha({k: v for k, v in scope.items()
                            if k != "sha256"})
    return scope


def scope_population_ids(scope: dict) -> set[str]:
    return {c["canonical_id"] for c in scope.get("candidates") or []}


def scope_population_parts(scope: dict) -> set[str]:
    return {c["opn"] for c in scope.get("candidates") or []}


def scoped_candidate_results(scope: dict, answer: dict) -> list[dict]:
    """Per-candidate view for the in-cohort path: every candidate M5 sent
    appears exactly once, with its M4 verdict or an explicit reason it was
    not ranked. Nothing may disappear silently."""
    he = answer.get("hard_eligibility") or {}
    comp = answer.get("comparison") or {}
    eligible = set(he.get("eligible") or [])
    inelig = {e.get("candidate"): e for e in he.get("ineligible") or []}
    unknown = {u.get("candidate"): u for u in he.get("unknown") or []}
    ranked_by_cid: dict[str, dict] = {}
    for e in comp.get("condition_matched_entries") or []:
        ranked_by_cid.setdefault(e.get("candidate"), e)
    approximate = {e.get("candidate") for e in
                   comp.get("approximate_scenario_entries") or []}
    availability = answer.get("evidence_availability") or {}

    out = []
    for entry in scope.get("candidates") or []:
        cid = entry["canonical_id"]
        identity = entry.get("identity") or {}
        row = {
            "m5_canonical_id": cid,
            "m4_candidate_id": None,
            "part": entry["opn"],
            "identity_class": "exact" if identity.get("mapped") else
            "unmapped",
            "grain": "opn",
            "in_ranking_population": True,
            "manufacturer": entry.get("manufacturer"),
            "family_label": identity.get("family_label"),
            "family_status": identity.get("family_status"),
            "discovery_eligibility_state": entry.get("eligibility_state"),
            "provenance": entry.get("provenance"),
        }
        if cid in inelig:
            rules = inelig[cid].get("rules") or []
            row.update({
                "engineering_eligibility": "ineligible",
                "ranked": False,
                "explanation": [
                    {"rule": r.get("rule"), "reason": r.get("reason"),
                     "required": r.get("required"), "rated": r.get("rated"),
                     "scope": r.get("scope"),
                     "evidence": r.get("evidence") or None}
                    for r in rules]})
        elif cid in unknown:
            row.update({
                "engineering_eligibility": "unknown",
                "ranked": False,
                "missing_ratings": unknown[cid].get("missing_ratings"),
                "note": unknown[cid].get("note")})
        elif cid in ranked_by_cid:
            e = ranked_by_cid[cid]
            row.update({
                "engineering_eligibility": "eligible",
                "ranked": True,
                "value": e.get("value"), "unit": e.get("unit"),
                "evidence_id": e.get("evidence_id"),
                "adjudication_state": e.get("adjudication_state"),
                "evidence_class": e.get("evidence_class"),
                "guarantee": e.get("guarantee")})
        elif cid in eligible:
            avail = (availability.get(cid) or {}).get("state")
            row.update({
                "engineering_eligibility": "eligible",
                "ranked": False,
                "reason_not_ranked": avail or "no_comparable_curve_evidence",
                "approximate_scenario_only": cid in approximate})
        else:
            row.update({
                "engineering_eligibility": "unresolved",
                "ranked": False,
                "reason_not_ranked": "no_evidence_in_m4_bundle",
                "note": "sent by M5 as part of the discovery population; "
                        "M4 holds no curve evidence for it, so it is "
                        "neither ranked nor eliminated"})
        out.append(row)
    return out


def out_of_population_entries(scope: dict, answer: dict) -> list[str]:
    """Ranked parts M5 never sent. M4 may not substitute its own universe;
    anything it returns outside the supplied population is a violation and
    is stripped from the presented ranking."""
    allowed_ids = scope_population_ids(scope)
    allowed_parts = scope_population_parts(scope)
    comp = answer.get("comparison") or {}
    bad = []
    for key in ("condition_matched_entries", "approximate_scenario_entries"):
        for e in comp.get(key) or []:
            cid, part = e.get("candidate"), e.get("part")
            if (cid and cid not in allowed_ids) or \
                    (part and part not in allowed_parts and
                     cid not in allowed_ids):
                bad.append(part or cid)
    seen, ordered = set(), []
    for b in bad:
        if b not in seen:
            seen.add(b)
            ordered.append(b)
    return ordered


def strip_out_of_population(answer: dict, scope: dict) -> dict:
    """Return a copy of the answer with out-of-population entries removed."""
    allowed_ids = scope_population_ids(scope)
    out = dict(answer)
    comp = dict(answer.get("comparison") or {})
    for key in ("condition_matched_entries", "approximate_scenario_entries"):
        comp[key] = [e for e in (comp.get(key) or [])
                     if e.get("candidate") in allowed_ids]
    out["comparison"] = comp
    return out


def ranking_disclosure(*, mode: str, scope: dict | None, answer: dict | None,
                       crosswalk: dict | None, discovery_candidate_count: int,
                       evidence_release: str | None,
                       identity_snapshot_id: str | None,
                       m4_release: str | None,
                       out_of_population: list[str],
                       population_too_large: dict | None = None) -> dict:
    """The response contract for B6: what population was sent, what came
    back, and whether the ranking was strictly in-cohort."""
    if mode == "m4_bundle":
        counts = (answer or {}).get("counts") or {}
        he = (answer or {}).get("hard_eligibility") or {}
        return {
            "mode": mode,
            "in_cohort": False,
            "out_of_cohort": True,
            "ranking_strictly_in_cohort": False,
            "promotion_blocked_while_normal_path": True,
            "disclosure": OUT_OF_COHORT_DISCLOSURE,
            "discovery_candidate_count": discovery_candidate_count,
            "candidates_sent": None,
            "m4_received": None,
            "m4_universe_candidates": counts.get("total_candidates"),
            "ranked": len(((answer or {}).get("comparison") or {}).get(
                "condition_matched_entries") or []),
            "excluded": len(he.get("ineligible") or []),
            "unknown_retained": len(he.get("unknown") or []),
            "unresolved_no_evidence": counts.get(
                "candidates_missing_curve_evidence"),
            "eligible_without_comparable_evidence": None,
            "unmapped": None,
            "assertion": None,
            "assertion_holds": None,
            "population_accounting": {
                "note": "the ranking population is M4's own evidence "
                        "universe, not the M5 discovery cohort; no "
                        "population identity assertion is possible"},
            "identity_mapping": {"source": "pinned fixture (transitional)",
                                 "sha256": None, "coverage": None},
            "out_of_population_entries": out_of_population,
            "releases": {"m5_evidence_release": evidence_release,
                         "m5_identity_snapshot": identity_snapshot_id,
                         "m4_evidence_release": m4_release},
        }

    sent_entries = (scope or {}).get("candidates") or []
    sent = len(sent_entries)
    state_counts: dict[str, int] = {}
    for e in sent_entries:
        k = e.get("eligibility_state") or "unset"
        state_counts[k] = state_counts.get(k, 0) + 1
    m4_scope = (answer or {}).get("candidate_scope") or {}
    results = scoped_candidate_results(scope or {}, answer or {})
    ranked = sum(1 for r in results if r.get("ranked"))
    excluded = sum(1 for r in results
                   if r["engineering_eligibility"] == "ineligible")
    unknown = sum(1 for r in results
                  if r["engineering_eligibility"] == "unknown")
    unresolved = sum(1 for r in results
                     if r["engineering_eligibility"] == "unresolved")
    eligible_unranked = sum(1 for r in results
                            if r["engineering_eligibility"] == "eligible"
                            and not r.get("ranked"))
    received = m4_scope.get("received")
    unplaced = (scope or {}).get("unknown_unplaced_included", 0)
    accounting_holds = (
        sent == ranked + excluded + unknown + unresolved + eligible_unranked
        and sent == discovery_candidate_count + unplaced)
    return {
        "mode": mode,
        "in_cohort": True,
        "out_of_cohort": False,
        "ranking_strictly_in_cohort": bool(
            m4_scope.get("ranking_strictly_in_cohort", True)
            and not out_of_population),
        "m4_universe_substituted": bool(
            m4_scope.get("m4_universe_substituted", False))
        or bool(out_of_population),
        "promotion_blocked_while_normal_path": False,
        "disclosure": "M4 ranked exactly the candidate population supplied "
                      "by M5 discovery; every supplied candidate is "
                      "accounted for as ranked, excluded, retained-unknown "
                      "or unresolved-no-evidence",
        "assertion": "ranking_population == discovery_candidate_population "
                     "modulo explicit hard exclusions, retained unknowns and "
                     "unresolved handling",
        "population_accounting": {
            "discovery_candidate_count": discovery_candidate_count,
            "unknown_unplaced_included": unplaced,
            "candidates_sent": sent,
            "identity": "candidates_sent == discovery_candidate_count + "
                        "unknown_unplaced_included"},
        "assertion_holds": bool(
            accounting_holds and received in (None, sent)
            and not out_of_population and population_too_large is None),
        "discovery_candidate_count": discovery_candidate_count,
        "candidates_sent": sent,
        "candidates_sha256": (scope or {}).get("sha256"),
        "candidates_sample": [e["canonical_id"] for e in sent_entries[:25]],
        "candidates_sample_truncated": max(0, sent - 25),
        "eligibility_state_counts": state_counts,
        "unknown_unplaced_included": (scope or {}).get(
            "unknown_unplaced_included", 0),
        "m4_received": received,
        "m4_matched": m4_scope.get("matched"),
        "ranked": ranked,
        "excluded": excluded,
        "unknown_retained": unknown,
        "unresolved_no_evidence": unresolved,
        "eligible_without_comparable_evidence": eligible_unranked,
        "unmapped": ((crosswalk or {}).get("coverage") or {}).get("unmapped"),
        "match_rule": m4_scope.get("match_rule") or _MATCH_RULE,
        "identity_mapping": {
            "source": "generated:identity-store",
            "schema": (crosswalk or {}).get("schema"),
            "version": (crosswalk or {}).get("version"),
            "sha256": (crosswalk or {}).get("sha256"),
            "coverage": (crosswalk or {}).get("coverage")},
        "out_of_population_entries": out_of_population,
        "population_too_large": population_too_large,
        "releases": {"m5_evidence_release": evidence_release,
                     "m5_identity_snapshot": identity_snapshot_id,
                     "m4_evidence_release": m4_release},
    }
