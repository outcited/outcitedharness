"""CURVE-08 R1 — candidate-centric engineering model (power buck path).

A document is not a candidate. A datasheet section is not a candidate. A
curve is not a candidate. The candidate is the selection unit: for power
DC-DC that is the device/family the engineer chooses, with OPN-level
identity where the catalog carries it.

Identity model (contract-compatible with FACET-02's
``harness/search/cohort.py`` Candidate: same grain honesty, same typed
value kinds ``point | interval | inequality | unknown | text``, same
"unknown is never elimination" law). CURVE-08 keeps the model in the
electronics lane because the curve evidence and ratings live here; when
FACET-02 merges, ``to_facet_candidate()`` maps 1:1 onto their dataclass.

Candidate identity never depends on how many documents exist: it is
derived from canonical manufacturer + category + family/series + device +
OPN, with membership relationships recorded explicitly.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

CANDIDATE_SCHEMA = "harness.electronics-candidate.v1"

# typed value kinds shared with FACET-02 axes vocabulary
VALUE_KINDS = ("point", "interval", "inequality", "unknown", "text")

GRAINS = ("opn", "device", "series", "family", "category")

# source authority ranking (higher = stronger); reported, never hidden
AUTHORITIES = {
    "datasheet_header_quote": 3,
    "catalog_T3": 2,
    "curve_evidence": 2,
    "admission_pending": 1,
    "none": 0,
}


def typed_point(value: Optional[float], *, unit: str | None = None,
                condition: str | None = None, cls: str | None = None,
                evidence: Mapping[str, Any] | None = None) -> dict:
    if value is None:
        return {"kind": "unknown", "value": None, "condition": condition,
                "class": None, "evidence": dict(evidence or {})}
    return {"kind": "point", "value": float(value), "unit": unit,
            "condition": condition, "class": cls,
            "evidence": dict(evidence or {})}


def _canon(*parts: Any) -> str:
    blob = "|".join(str(p or "").lower().strip() for p in parts)
    return "cand-" + hashlib.sha256(blob.encode()).hexdigest()[:24]


@dataclass
class Candidate:
    """One engineering selection unit at its honest discovery grain."""

    manufacturer: Optional[str]
    category: str
    subcategory: Optional[str]
    family: Optional[str]
    series: Optional[str]
    device: Optional[str]
    opn: Optional[str]
    grain: str
    source_authority: str
    membership: dict = field(default_factory=dict)
    parametrics: dict = field(default_factory=dict)
    evidence_refs: list = field(default_factory=list)
    curve_evidence_ids: list = field(default_factory=list)
    adjudication_states: set = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.grain not in GRAINS:
            raise ValueError(f"unknown grain {self.grain!r}")

    @property
    def candidate_id(self) -> str:
        return _canon(self.manufacturer, self.category, self.family,
                      self.device or self.opn, self.grain)

    def axis_value(self, name: str) -> dict | None:
        return self.parametrics.get(name)

    def to_facet_candidate(self) -> dict:
        """1:1 mapping onto FACET-02 cohort.Candidate field names."""

        return {
            "opn": self.opn or self.device,
            "vendor_raw": self.manufacturer,
            "vendor_canonical": self.manufacturer,
            "category": self.category,
            "subcategory": self.subcategory,
            "attributes": self.parametrics,
            "evidence_unit_ids": self.curve_evidence_ids,
            "evidence_grade_units": sum(
                1 for e in self.evidence_refs
                if e.get("grade") == "evidence"),
            "discovery_only_units": sum(
                1 for e in self.evidence_refs
                if e.get("grade") == "discovery_only"),
        }


def _catalog_authority(record: Mapping[str, Any]) -> str:
    tier = str((record.get("provenance") or {}).get("provenance_tier")
               or record.get("provenance_tier") or "")
    return "catalog_T3" if tier else "catalog_T3"


def build_power_candidates(
    catalog_rows: Sequence[Mapping[str, Any]],
    bundle_rows: Sequence[Mapping[str, Any]],
    admission_packets: Sequence[Mapping[str, Any]] = (),
    device_ratings: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Candidate]:
    """Assemble power buck candidates from every authoritative source.

    Catalog supplies OPN-level parametrics; the frozen bundle supplies
    curve evidence references (device- and family-scoped); admission
    packets supply datasheet-quoted ratings for devices the catalog does
    not yet carry. Evidence rows attach to candidates; they never create
    extra candidates.
    """

    out: dict[str, Candidate] = {}

    def _get(cand: Candidate) -> Candidate:
        out[cand.candidate_id] = cand
        return cand

    # 1. catalog candidates (OPN grain where an OPN exists)
    for row in catalog_rows:
        if str(row.get("topology") or "").lower() not in (
                "buck", "dc-dc", "dcdc"):
            continue
        cand = Candidate(
            manufacturer=_vendor_canonical(row.get("domain")),
            category="power",
            subcategory="buck",
            family=row.get("family"),
            series=row.get("series"),
            device=row.get("part_number"),
            opn=row.get("part_number"),
            grain="opn" if row.get("part_number") else "device",
            source_authority=_catalog_authority(row),
            parametrics={
                "vin_min_v": typed_point(row.get("vin_min_v"), unit="V"),
                "vin_max_v": typed_point(row.get("vin_max_v"), unit="V"),
                "vout_min_v": typed_point(row.get("vout_min_v"), unit="V"),
                "vout_max_v": typed_point(row.get("vout_max_v"), unit="V"),
                "iout_max_a": typed_point(row.get("iout_max_a"), unit="A"),
                "temp_min_c": typed_point(row.get("temp_min_c"),
                                          unit="C"),
                "temp_max_c": typed_point(row.get("temp_max_c"),
                                          unit="C"),
            },
        )
        cand.evidence_refs.append({
            "kind": "catalog", "grade": "evidence",
            "record_id": row.get("id"),
            "authority": cand.source_authority,
        })
        _get(cand)

    # 2. datasheet-quoted ratings (stronger authority; upgrades or creates)
    ratings_by_part = {r["part"]: r for r in device_ratings}
    for packet in admission_packets:
        ident = packet.get("identity") or {}
        part = ident.get("device") or ident.get("series")
        rating = packet.get("ratings") or {}
        if not part:
            continue
        existing = next((c for c in out.values()
                         if c.device == part), None)
        cand = existing or Candidate(
            manufacturer=ident.get("manufacturer"),
            category="power", subcategory="buck",
            family=ident.get("canonical_family"),
            series=ident.get("series"), device=part, opn=None,
            grain="device",
            source_authority="admission_pending",
        )
        prov = ident.get("identity_provenance") or {}
        ev = {"kind": "admission_packet", "grade": "evidence",
              "authority": "datasheet_header_quote",
              "datasheet_sha256": prov.get("datasheet_sha256"),
              "quote": rating.get("evidence")}
        cand.parametrics["vin_min_v"] = typed_point(
            rating.get("vin_min_v"), unit="V", cls="rated", evidence=ev)
        cand.parametrics["vin_max_v"] = typed_point(
            rating.get("vin_max_v"), unit="V", cls="rated", evidence=ev)
        cand.parametrics["iout_max_a"] = typed_point(
            rating.get("iout_max_a"), unit="A", cls="rated", evidence=ev)
        # a fixed-output device's capability is the interval [v, v]
        if rating.get("vout_v") is not None:
            cand.parametrics["vout_min_v"] = typed_point(
                rating.get("vout_v"), unit="V", cls="fixed_output",
                evidence=ev)
            cand.parametrics["vout_max_v"] = typed_point(
                rating.get("vout_v"), unit="V", cls="fixed_output",
                evidence=ev)
        cand.source_authority = "datasheet_header_quote"
        cand.evidence_refs.append(ev)
        _get(cand)
    for part, rating in ratings_by_part.items():
        existing = next((c for c in out.values() if c.device == part), None)
        if existing is None:
            continue
        ev = {"kind": "device_rating", "grade": "evidence",
              "authority": "datasheet_header_quote", "quote":
              rating.get("quote")}
        existing.parametrics["iout_max_a"] = typed_point(
            rating.get("rated_iout_a"), unit="A", cls="rated", evidence=ev)

    # 3. attach curve evidence; family-scoped rows attach to a FAMILY
    #    candidate, never to a fabricated device
    for row in bundle_rows:
        app = row.get("applicability") or {}
        part = app.get("part")
        family = app.get("family_group") or app.get("family")
        ref = {
            "kind": "curve", "grade": "evidence",
            "evidence_id": row.get("evidence_id"),
            "authority": "curve_evidence",
            "adjudication_state": (row.get("adjudication") or {}).get(
                "state"),
            "phenomenon": row.get("phenomenon"),
        }
        target = next((c for c in out.values() if c.device == part), None)
        if target is None and family:
            target = out.get(_canon(app.get("manufacturer"), "power",
                                    family, None, "family"))
            if target is None:
                target = Candidate(
                    manufacturer=app.get("manufacturer"),
                    category="power", subcategory="buck",
                    family=family, series=None, device=None, opn=None,
                    grain="family",
                    source_authority="curve_evidence",
                    membership={"devices": sorted({
                        r2["applicability"]["part"]
                        for r2 in bundle_rows
                        if (r2.get("applicability") or {}).get(
                            "family_group") == family
                        and (r2.get("applicability") or {}).get("part")
                    })},
                )
                _get(target)
        if target is None:
            continue
        target.curve_evidence_ids.append(row.get("evidence_id"))
        target.evidence_refs.append(ref)
        state = (row.get("adjudication") or {}).get("state")
        if state:
            target.adjudication_states.add(state)
    return out


def _vendor_canonical(domain: str | None) -> str | None:
    if not domain:
        return None
    return re.sub(r"\.(com|net|org)$", "", str(domain).lower())


def candidate_counts(candidates: Sequence[Candidate],
                     eligibility: Mapping[str, dict] | None = None,
                     comparability: Mapping[str, str] | None = None
                     ) -> dict[str, int]:
    """The seven mandated counters. A candidate with 50 curves counts
    once; evidence rows are never counted as engineering choices."""

    elig = eligibility or {}
    comp = comparability or {}
    hard_eligible = [c for c in candidates
                     if elig.get(c.candidate_id, {}).get("verdict")
                     == "eligible"]
    parametric = [c for c in hard_eligible
                  if elig.get(c.candidate_id, {}).get("parametric_ok")]
    comparable = [c for c in candidates
                  if comp.get(c.candidate_id) == "comparable"]
    noncomparable = [c for c in candidates
                     if comp.get(c.candidate_id) == "non_comparable"]
    missing = [c for c in candidates
               if comp.get(c.candidate_id) == "missing"]
    verify = [c for c in candidates
              if c.adjudication_states
              and c.adjudication_states != {"human_approved"}
              or c.source_authority == "admission_pending"]
    return {
        "total_candidates": len(candidates),
        "hard_eligible_candidates": len(hard_eligible),
        "parametrically_compatible_candidates": len(parametric),
        "candidates_with_comparable_curve_evidence": len(comparable),
        "candidates_with_noncomparable_evidence": len(noncomparable),
        "candidates_missing_curve_evidence": len(missing),
        "candidates_requiring_verification": len(verify),
        "evidence_rows_attached": sum(
            len(c.curve_evidence_ids) for c in candidates),
    }


def load_default_inputs(repo_root: Path) -> tuple[list, list, list, list]:
    """Catalog rows + frozen bundle rows + admission packets + ratings."""

    catalog = []
    slice_path = (repo_root / "tests/fixtures/gold/curve_evidence_pilot/"
                  "_catalog_slice.json")
    if slice_path.exists():
        # frozen, provenance-stamped slice of the M4 power catalog; the
        # live catalog stays M4-owned and is injected by callers in prod
        slice_doc = json.loads(slice_path.read_text())
        catalog = list(slice_doc.get("rows") or [])
    bundle = []
    b = repo_root / "tests/fixtures/m4-handoff/curve_evidence_bundle_v3.jsonl"
    if b.exists():
        for line in b.read_text().splitlines():
            if line.strip():
                bundle.append(json.loads(line))
    manifest_path = (repo_root / "tests/fixtures/m4-handoff/"
                     "curve_evidence_bundle_v3_manifest.json")
    packets = json.loads(
        manifest_path.read_text()).get("catalog_admission_packets", []) \
        if manifest_path.exists() else []
    ratings_path = (repo_root / "tests/fixtures/gold/curve_evidence_pilot/"
                    "_device_ratings.json")
    ratings = json.loads(ratings_path.read_text()).get("ratings", []) \
        if ratings_path.exists() else []
    return catalog, bundle, packets, ratings


__all__ = [
    "CANDIDATE_SCHEMA", "Candidate", "GRAINS", "VALUE_KINDS",
    "build_power_candidates", "candidate_counts", "load_default_inputs",
    "typed_point",
]
