"""Candidate-centric cohort narrowing (PRD-FACET-02 R1/R4).

The unit of counting is an ENGINEERING CANDIDATE — a distinct part/device at
the category's discovery grain — never an evidence unit. An evidence unit
(claim, section, curve) supports a candidate; it is not a candidate. This
module keeps those counts separate everywhere.

Grain honesty (R1): candidates are OPNs here because the catalog's
family/mpn_base columns are empty (0/1,882). The grain is reported on every
response and family-grain is NOT fabricated. Family evidence is never
expanded into OPN applicability: a candidate is attributed an axis value only
from claims whose ``opn`` equals the candidate.

Unknown handling (R4): a candidate with no value for an axis is UNKNOWN and
is never eliminated as technically unsuitable — it is reported as unknown and
excluded from a value bucket only by the arithmetic of that bucket.

Reduction ranking (R4) mirrors discovery.next_question: an axis is valuable
when choosing a value removes the most candidates. Numeric axes rank by the
median-screen elimination (candidates that would be cut by the median), the
same deterministic, model-free knife discovery already uses.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from harness.search import axes as axis_mod
from harness.search import taxonomy
from harness.search import vendors

COHORT_SCHEMA = "harness.search-cohort.v1"

# Deterministic numeric bucketing per axis (lower, upper, label); the last
# bucket is open-ended. Chosen so buckets are engineer-meaningful, not
# equal-width noise. Axes absent here bucket on their own distinct values.
_BUCKETS: dict[str, list[tuple[float | None, float | None, str]]] = {
    "vds_rating_v": [(None, 30, "<=30 V"), (30, 60, "30-60 V"),
                     (60, 100, "60-100 V"), (100, 200, "100-200 V"),
                     (200, 500, "200-500 V"), (500, 1000, "500-1000 V"),
                     (1000, None, ">1000 V")],
    "rds_on_ohm": [(None, 0.005, "<5 mOhm"), (0.005, 0.02, "5-20 mOhm"),
                   (0.02, 0.05, "20-50 mOhm"), (0.05, 0.2, "50-200 mOhm"),
                   (0.2, 1.0, "0.2-1 Ohm"), (1.0, None, ">=1 Ohm")],
    "id_continuous_a": [(None, 5, "<5 A"), (5, 20, "5-20 A"),
                        (20, 50, "20-50 A"), (50, 100, "50-100 A"),
                        (100, None, ">=100 A")],
    "tj_max_c": [(None, 125, "<125 C"), (125, 150, "125-150 C"),
                 (150, None, ">=150 C")],
    "thermal_resistance_c_per_w": [(None, 1, "<1 C/W"), (1, 5, "1-5 C/W"),
                                   (5, 20, "5-20 C/W"), (20, None, ">=20 C/W")],
    "output_voltage_v": [(None, 1.2, "<1.2 V"), (1.2, 3.3, "1.2-3.3 V"),
                         (3.3, 5, "3.3-5 V"), (5, 12, "5-12 V"),
                         (12, None, ">=12 V")],
    "supply_voltage_v": [(None, 1.2, "<1.2 V"), (1.2, 3.3, "1.2-3.3 V"),
                         (3.3, 5, "3.3-5 V"), (5, 12, "5-12 V"),
                         (12, None, ">=12 V")],
    "frequency_mhz": [(None, 20e6, "<20 MHz"), (20e6, 48e6, "20-48 MHz"),
                      (48e6, 80e6, "48-80 MHz"), (80e6, 150e6, "80-150 MHz"),
                      (150e6, None, ">=150 MHz")],
    "flash_kb": [(None, 32, "<32 KB"), (32, 128, "32-128 KB"),
                 (128, 512, "128-512 KB"), (512, None, ">=512 KB")],
    "sram_kb": [(None, 8, "<8 KB"), (8, 32, "8-32 KB"), (32, None, ">=32 KB")],
    "pitch_mm": [(None, 1.0, "<1.0 mm"), (1.0, 2.0, "1.0-2.0 mm"),
                 (2.0, 2.54, "2.0-2.54 mm"), (2.54, 5.08, "2.54-5.08 mm"),
                 (5.08, None, ">=5.08 mm")],
    "positions": [(None, 2, "2"), (2, 6, "3-6"), (6, 24, "7-24"),
                  (24, None, ">24")],
}


def bucket_for(axis_name: str, value: float | None) -> str:
    if value is None:
        return "(unknown)"
    buckets = _BUCKETS.get(axis_name)
    if not buckets:
        return f"{value:g}"
    for lo, hi, label in buckets:
        if (lo is None or value >= lo) and (hi is None or value < hi):
            return label
    return f"{value:g}"


@dataclass
class Candidate:
    opn: str
    vendor_raw: str | None = None
    vendor_canonical: str | None = None
    category: str | None = None
    subcategory: str | None = None
    attributes: dict[str, dict] = field(default_factory=dict)
    evidence_unit_ids: list[str] = field(default_factory=list)
    evidence_grade_units: int = 0
    discovery_only_units: int = 0

    def axis_value(self, axis_name: str) -> dict | None:
        return self.attributes.get(axis_name)


def _aisle_category(selector_aisle: str | None) -> tuple[str | None, str | None]:
    if not selector_aisle:
        return None, None
    key = selector_aisle.strip().lower()
    mapping = {
        "discrete-mosfets": ("power", "discrete-mosfet"),
        "gate-drivers": ("power", "gate-drivers"),
        "ldo": ("power", "ldo-regulators"),
        "ldo-regulators": ("power", "ldo-regulators"),
        "dc-dc": ("power", "switching-regulators"),
        "dc-dc-converters": ("power", "switching-regulators"),
        "ac-dc": ("power", "ac-dc"),
        "load-switches": ("power", "load-switches"),
        "battery-management": ("power", "battery-management"),
        "poe-ics": ("power", "poe-ics"),
        "supervisors-reset": ("power", "supervisors-reset"),
        "voltage-references": ("power", "voltage-references"),
        "power-management": ("power", "power-management"),
        "power": ("power", "power-management"),
        "microcontrollers": ("mcu", "microcontrollers"),
        "mcu": ("mcu", "microcontrollers"),
        "connectors": ("connectors", "connectors"),
        "connector": ("connectors", "connectors"),
    }
    if key in mapping:
        return mapping[key]
    cat, sub = taxonomy.classify_text(key)
    return cat, (sub or key)


def _claim_axes(claim_rows: list[sqlite3.Row],
                conditions: dict[str, dict]) -> dict[str, dict]:
    """Best typed value per axis from a candidate's claims (op semantics).

    range-op axes merge min/max qualified claims into an explicit interval
    (VDD "2.0 to 3.6 V" is an interval, never a point); rating axes keep
    the strongest single value with its qualifier and class.
    """
    chosen: dict[str, dict] = {}
    range_parts: dict[str, dict[str, dict]] = {}
    for row in claim_rows:
        axis = axis_mod.axis_for(row["symbol"])
        if axis is None:
            continue
        typed = axis_mod.type_claim_value(
            value=row["value"], unit=row["unit"], qualifier=row["qualifier"],
            condition_norm=row["condition_norm"],
            condition_typed=conditions.get(row["condition_norm"] or ""),
            axis=axis)
        if axis.op == "range" and isinstance(typed.get("value"),
                                             (int, float)):
            q = (row["qualifier"] or "").lower()
            if q.startswith("min"):
                range_parts.setdefault(axis.name, {})["min"] = typed
            elif q.startswith("max"):
                range_parts.setdefault(axis.name, {})["max"] = typed
            else:
                chosen.setdefault(axis.name, typed)
            continue
        prev = chosen.get(axis.name)
        if prev is None:
            chosen[axis.name] = typed
            continue
        pv, nv = prev.get("value"), typed.get("value")
        if not isinstance(pv, (int, float)) or not isinstance(nv, (int, float)):
            if prev["kind"] == "unknown" and typed["kind"] != "unknown":
                chosen[axis.name] = typed
            continue
        if axis.op == "min_rating" and nv > pv:
            chosen[axis.name] = typed
        elif axis.op == "max_rating" and nv < pv:
            chosen[axis.name] = typed
    for axis_name, parts in range_parts.items():
        if "min" in parts and "max" in parts:
            lo, hi = parts["min"], parts["max"]
            chosen[axis_name] = {
                "kind": "interval",
                "value": None,
                "value_min": lo["value"], "value_max": hi["value"],
                "unit": lo.get("unit") or hi.get("unit"),
                "condition": lo.get("condition") or hi.get("condition"),
                "condition_typed": lo.get("condition_typed"),
                "rating_class": "rated_limit",
                "class": "min-max",
            }
        elif parts:
            only = next(iter(parts.values()))
            chosen[axis_name] = only
    return chosen


def build_cohort(*, catalog_con: sqlite3.Connection,
                 search_con: sqlite3.Connection | None = None,
                 wave_path: str | None = None,
                 category: str | None = None,
                 subcategory: str | None = None,
                 aisle_map: dict[str, str] | None = None) -> dict:
    """Build the candidate set for a category/subcategory.

    aisle_map: opn(upper) -> selector_aisle. If absent, candidates are all
    parts (category reported as None unless search_con supplies it).
    """
    parts = {r["opn"]: r for r in catalog_con.execute(
        "SELECT opn, vendor, family, package FROM parts")}
    try:
        conditions = {r["condition_verbatim"]: json.loads(r["parsed"])
                      for r in catalog_con.execute(
                          "SELECT condition_verbatim, parsed"
                          " FROM conditions_typed")}
    except sqlite3.OperationalError:
        conditions = {}
    claims_by_opn: dict[str, list[sqlite3.Row]] = {}
    for row in catalog_con.execute(
            "SELECT opn, symbol, qualifier, condition_norm, value, unit,"
            " value_text FROM claims_canonical"):
        claims_by_opn.setdefault(row["opn"], []).append(row)

    aisle_map = aisle_map or {}
    candidates: dict[str, Candidate] = {}
    for opn, part in parts.items():
        cat = sub = None
        if aisle_map:
            cat, sub = _aisle_category(aisle_map.get(opn.upper()))
        if category and cat != category:
            continue
        if subcategory and sub != subcategory:
            continue
        vendor = vendors.resolve(part["vendor"])
        candidates[opn] = Candidate(
            opn=opn, vendor_raw=part["vendor"],
            vendor_canonical=vendor["canonical"], category=cat,
            subcategory=sub,
            attributes=_claim_axes(claims_by_opn.get(opn, []), conditions))

    if search_con is not None:
        _attach_evidence(search_con, candidates)

    grain = "opn"
    notes = ["family/mpn_base empty in catalog (0/1882) — candidate grain "
             "is OPN; family-grain not fabricated"]
    return {"schema": COHORT_SCHEMA, "grain": grain,
            "category": category, "subcategory": subcategory,
            "candidates": candidates, "notes": notes}


def _attach_evidence(search_con: sqlite3.Connection,
                     candidates: dict[str, Candidate]) -> None:
    for row in search_con.execute(
            "SELECT unit_id, applicability, doc_sha256, page, grain"
            " FROM units WHERE retired=0 AND grain IN ('claim','section')"):
        try:
            apps = json.loads(row["applicability"])
        except (json.JSONDecodeError, TypeError):
            apps = []
        graded = bool(row["doc_sha256"]) and not \
            str(row["doc_sha256"]).startswith("unhashed:") and \
            row["page"] is not None
        for app in apps:
            value = app.get("value")
            cand = candidates.get(value)
            if cand is None:
                continue
            cand.evidence_unit_ids.append(row["unit_id"])
            if graded:
                cand.evidence_grade_units += 1
            else:
                cand.discovery_only_units += 1


def bucket_bounds(axis_name: str, label: str) -> tuple:
    for lo, hi, lab in _BUCKETS.get(axis_name, []):
        if lab == label:
            return (lo, hi)
    return (None, None)


def value_in_bucket(axis_name: str, typed: dict | None, label: str) -> bool:
    """Point values match their bucket; intervals match EVERY bucket they
    overlap (an operating range of 2.0-3.6 V genuinely spans two buckets —
    overlap is the honest membership, not a guess)."""
    if not typed:
        return False
    if typed.get("kind") == "interval":
        lo, hi = bucket_bounds(axis_name, label)
        if lo is None and hi is None and label not in \
                [l for _, _, l in _BUCKETS.get(axis_name, [])]:
            return False
        vmin = typed.get("value_min")
        vmax = typed.get("value_max")
        if vmin is None or vmax is None:
            return False
        return (lo is None or vmax >= lo) and (hi is None or vmin < hi)
    value = typed.get("value")
    if value is None:
        return False
    return bucket_for(axis_name, value) == label


def _candidate_matches(cand: Candidate, axis_name: str, bucket: str) -> bool:
    return value_in_bucket(axis_name, cand.axis_value(axis_name), bucket)


def facets_for_cohort(cohort: dict, *, constraints: dict | None = None,
                      max_values: int = 25) -> dict:
    """Candidate-counted facets + reduction ranking for the current cohort."""
    constraints = dict(constraints or {})
    candidates = list(cohort["candidates"].values())
    cat = cohort.get("category")
    spec_axes = axis_mod.axes_for_category(cat)

    # apply already-chosen constraints. A candidate whose value is UNKNOWN
    # for the constrained axis is NOT eliminated as unsuitable — it is
    # unplaced: reported separately so the UI can show "unknown, needs
    # evidence" rather than "fails the requirement".
    unknown_excluded: dict[str, list[str]] = {}
    for axis_name, want in constraints.items():
        if axis_name == "vendor":
            candidates = [c for c in candidates
                          if c.vendor_canonical == want]
            continue
        if axis_name in ("category", "subcategory"):
            candidates = [c for c in candidates
                          if getattr(c, axis_name, None) == want]
            continue
        kept = []
        unplaced = []
        for c in candidates:
            typed = c.axis_value(axis_name)
            if not typed or (typed.get("value") is None
                             and typed.get("kind") != "interval"):
                unplaced.append(c.opn)
                continue
            if value_in_bucket(axis_name, typed, want):
                kept.append(c)
        candidates = kept
        if unplaced:
            unknown_excluded[axis_name] = unplaced

    cohort_size = len(candidates)
    evidence_units = sum(len(c.evidence_unit_ids) for c in candidates)
    graded = sum(c.evidence_grade_units for c in candidates)

    facets: list[dict] = []
    unknown_all: dict[str, int] = {}

    # categorical structural facets first (category/subcategory/vendor)
    for dim in ("subcategory", "vendor"):
        if dim in constraints:
            continue
        counts: dict[str, list[Candidate]] = {}
        for c in candidates:
            key = {"vendor": c.vendor_canonical,
                   "subcategory": c.subcategory}.get(dim)
            if key is None:
                continue
            counts.setdefault(key, []).append(c)
        values = sorted(
            ({"value": k, "candidates": len(v),
              "evidence_units": sum(len(x.evidence_unit_ids) for x in v),
              "unknown_candidates": 0,
              "reduction": cohort_size - len(v)}
             for k, v in counts.items()),
            key=lambda d: (-d["candidates"], d["value"]))[:max_values]
        if values:
            facets.append({"dimension": dim, "kind": "categorical",
                           "values": values})

    # numeric spec-axis facets (candidate counts per range, R1/R2)
    for axis in spec_axes:
        if axis.name in constraints:
            continue
        buckets: dict[str, list[Candidate]] = {}
        unknown = 0
        present = 0
        axis_buckets = [lab for _, _, lab in _BUCKETS.get(axis.name, [])]
        for c in candidates:
            typed = c.axis_value(axis.name)
            if not typed:
                unknown += 1
                continue
            if typed.get("kind") == "interval":
                present += 1
                for label in axis_buckets:
                    if value_in_bucket(axis.name, typed, label):
                        buckets.setdefault(label, []).append(c)
                continue
            if typed.get("value") is None:
                unknown += 1
                continue
            present += 1
            b = bucket_for(axis.name, typed["value"])
            buckets.setdefault(b, []).append(c)
        if present == 0 and unknown == 0:
            continue  # no underlying data supports this dimension (R2)
        unknown_all[axis.name] = unknown
        values = sorted(
            ({"value": k, "candidates": len(v),
              "evidence_units": sum(len(x.evidence_unit_ids) for x in v),
              "evidence_coverage": round(present / max(1, cohort_size), 4),
              "unknown_candidates": unknown,
              "reduction": cohort_size - len(v)}
             for k, v in buckets.items()),
            key=lambda d: (-d["candidates"], d["value"]))[:max_values]
        facets.append({
            "dimension": axis.name, "kind": "numeric", "op": axis.op,
            "unit": axis.unit, "values": values,
            "coverage": round(present / max(1, cohort_size), 4),
            "unknown_candidates": unknown,
        })

    # Reduction ranking (R4): an axis is informative when choosing a value
    # removes many candidates AND the axis actually has data. Weighting
    # reduction by coverage stops near-empty axes (high trivial reduction,
    # 1% coverage) from being recommended. Deterministic tie-break by name.
    def informative(f: dict) -> tuple:
        best = f["values"][0]["reduction"] if f["values"] else 0
        coverage = f.get("coverage", 1.0) if f["kind"] == "numeric" else 1.0
        return (-(best * coverage), f["dimension"])

    ranked = sorted(facets, key=informative)
    recommended = ranked[0]["dimension"] if ranked else None

    return {
        "schema": COHORT_SCHEMA,
        "category": cat, "subcategory": cohort.get("subcategory"),
        "grain": cohort["grain"],
        "candidate_count": cohort_size,
        "evidence_units": evidence_units,
        "evidence_grade_units": graded,
        "discovery_only_units": evidence_units - graded,
        "unknown_candidates": unknown_all,
        "unknown_excluded": unknown_excluded,
        "constraints": constraints,
        "facets": facets,
        "recommended_next": recommended,
        "notes": cohort.get("notes", []) + (
            ["candidates without a value for a constrained axis are "
             "reported under unknown_excluded — unplaced, never judged "
             "unsuitable"] if unknown_excluded else []),
    }