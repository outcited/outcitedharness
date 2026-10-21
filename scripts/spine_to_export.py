#!/usr/bin/env python3
"""S6 bridge — normalized spine output → ExportStore staging format.

Takes the L3 normalized + L4 identity-bound rows and emits:
  1. parts_spine.jsonl     — part records with claims (ExportStore feed)
  2. compare_ready_spine.jsonl — per-axis comparable claims (the compare feed)
  3. spine_metrics.json    — stage counts with explicit grains

Written to a STAGING directory (not the live api-store-export) — promotion
is a separate, reviewed step.

usage: python3 spine_to_export.py --normalized .../normalized/ \
    --bound .../bound/ --staging /tmp/spine-staging
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

# maps our facet field names to the compare_ready axis vocabulary
AXIS_MAP = {
    "RthetaJA": "rtheta_ja_c_per_w",
    "RthetaJC": "rtheta_jc_c_per_w",
    "RthetaJB": "rtheta_jb_c_per_w",
    "PsiJT": "psi_jt_c_per_w",
    "PsiJB": "psi_jb_c_per_w",
    "ZthJA": "zth_ja_c_per_w",
    "flash_kb": "flash_kb",
    "sram_kb": "sram_kb",
    "clk_max_mhz": "clk_max_mhz",
    "n_timer": "n_timer",
    "n_gpio": "n_gpio",
    "n_adc": "n_adc",
    "n_dma": "n_dma",
    "n_spi": "n_spi",
    "package": "package",
}

OP_MAP = {
    "RthetaJA": "max_rating", "RthetaJC": "max_rating", "RthetaJB": "max_rating",
    "PsiJT": "max_rating", "PsiJB": "max_rating", "ZthJA": "max_rating",
    "flash_kb": "min_rating", "sram_kb": "min_rating", "clk_max_mhz": "min_rating",
    "n_timer": "min_rating", "n_gpio": "min_rating",
}

RATING_CLASS_MAP = {"max": "rated_limit", "min": "rated_limit", "typ": "typical"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--normalized", nargs="+", required=True)
    ap.add_argument("--bound", nargs="+", required=True)
    ap.add_argument("--staging", required=True)
    args = ap.parse_args()

    staging = Path(args.staging)
    staging.mkdir(parents=True, exist_ok=True)

    # Load bound rows into an index: sha → part_identity
    identity = {}
    for rp in args.bound:
        for line in open(rp, errors="replace"):
            try:
                o = json.loads(line)
            except Exception:
                continue
            sha = o.get("pdf_sha") or o.get("document_sha256")
            part = o.get("part_identity")
            if sha and part:
                identity[sha] = part

    # Process normalized rows → parts + compare_ready
    parts: dict[str, dict] = {}
    compare_rows: list[dict] = []
    stage_counts: Counter = Counter()
    condition_compatible: set[str] = set()  # parts with condition-aware comparisons

    for rp in args.normalized:
        for line in open(rp, errors="replace"):
            try:
                row = json.loads(line)
            except Exception:
                continue
            stage_counts["normalized_rows"] += 1

            # only verified rows
            v = (row.get("_verify") or {}).get("verdict")
            if v != "verified":
                continue
            stage_counts["verified"] += 1

            sha = row.get("pdf_sha") or row.get("document_sha256")
            part = identity.get(sha) or row.get("part_identity")
            if not part:
                continue
            stage_counts["identity_bound"] += 1

            # determine the axis
            metric = row.get("metric") or row.get("field")
            axis = AXIS_MAP.get(metric or "")
            if row.get("mode"):
                mode_canon = (row.get("_norm") or {}).get("mode_canon") or row["mode"]
                axis = f"mode_{mode_canon}"

            if not axis:
                continue
            stage_counts["axis_mapped"] += 1

            # normalized value
            norm = row.get("_norm") or {}
            canon_val = norm.get("canonical_value")
            if canon_val is None:
                continue
            stage_counts["normalized_value"] += 1

            # build the part record
            if part not in parts:
                parts[part] = {
                    "part_number": part,
                    "vendor": row.get("manufacturer") or "",
                    "aisle": "mcu" if "mcu" in str(row.get("doc_class", "")) else "power",
                    "claims": [],
                    "_spine": {"identity_source": row.get("identity_source")},
                }

            # DEDUPLICATION (2026-10-20 review): when a part has multiple
            # claims for the same field, keep the one from the earliest page
            # (title/summary page is authoritative; TOC pages carry section
            # numbers that masquerade as spec values — e.g., efr32mg24 TOC
            # page 6 yields flash_kb=7 from a chapter reference).
            page = row.get("page_1based") or row.get("page") or 9999
            # TOC entries carry dot leaders ("Flash Memory ....... 3") — never spec values
            quote_text = str(row.get("verbatim") or "")
            if "....." in quote_text or "......." in quote_text:
                continue  # skip TOC artifacts
            existing_claim = next((c for c in parts[part]["claims"] if c["field"] == axis), None)
            if existing_claim is not None:
                existing_page = existing_claim.get("source", {}).get("page", 9999)
                if page >= existing_page:
                    continue  # earlier page wins, skip this duplicate

            # build the claim
            role = row.get("value_role") or norm.get("rating_role") or "unknown"
            claim = {
                "field": axis,
                "value": canon_val,
                "unit": norm.get("unit_canon") or row.get("unit") or "",
                "rating_class": RATING_CLASS_MAP.get(role, "unknown"),
                "source": {
                    "doc_sha256": sha,
                    "page": row.get("page_1based") or row.get("page"),
                    "quote": row.get("verbatim"),
                },
                "role": role,
            }
            conditions = row.get("conditions_verbatim") or ""
            claim["conditions"] = conditions
            parts[part]["claims"].append(claim)
            stage_counts["claims_emitted"] += 1

            # compare_ready row
            cr = {
                "axis": axis,
                "op": OP_MAP.get(metric or "", "covers"),
                "opn": part,
                "doc_sha256": sha,
                "symbol": metric or row.get("mode", ""),
                "symbol_norm": (metric or row.get("mode", "")).lower().replace(" ", "_"),
                "rating_class": RATING_CLASS_MAP.get(role, "unknown"),
                "condition_norm": conditions,
                "value": str(row.get("value")),
                "unit": row.get("unit") or "",
                "quote": row.get("verbatim") or "",
                "page": row.get("page_1based") or row.get("page") or 1,
                "match": "exact",
                "value_si": canon_val,
                "unit_si": norm.get("unit_canon") or "",
                "kind": "spine_l3",
                # spine-specific fields
                "_spine": {
                    "verified": True,
                    "identity_source": row.get("identity_source"),
                    "canonical_unit": norm.get("unit_canon"),
                },
            }
            compare_rows.append(cr)

            # track condition-comparable pairs (same axis, same rating class,
            # both have conditions or both are unconditional)
            if conditions:
                condition_compatible.add(part)

    # write outputs
    parts_fh = open(staging / "parts_spine.jsonl", "w")
    for part in sorted(parts):
        parts_fh.write(json.dumps(parts[part], ensure_ascii=False) + "\n")
    parts_fh.close()

    cr_fh = open(staging / "compare_ready_spine.jsonl", "w")
    for cr in compare_rows:
        cr_fh.write(json.dumps(cr, ensure_ascii=False) + "\n")
    cr_fh.close()

    # count actual comparable pairs (same axis, same rating_class, both verified)
    axis_class_parts: dict[tuple, set] = defaultdict(set)
    for cr in compare_rows:
        key = (cr["axis"], cr["rating_class"])
        axis_class_parts[key].add(cr["opn"])

    comparable_pairs = sum(
        len(parts) * (len(parts) - 1) // 2
        for parts in axis_class_parts.values()
        if len(parts) >= 2
    )
    comparable_opns = set()
    for parts in axis_class_parts.values():
        if len(parts) >= 2:
            comparable_opns.update(parts)

    metrics = {
        "schema": "harness.spine-s6-metrics.v1",
        "generated": datetime.now(timezone.utc).isoformat(),
        "stages": {
            "raw_rows_unnormalized": stage_counts.get("normalized_rows", 0),
            "verified_claims": stage_counts.get("verified", 0),
            "normalized_claims": stage_counts.get("normalized_value", 0),
            "identity_bound": stage_counts.get("identity_bound", 0),
        },
        "comparable": {
            "parts_with_verified_data": len(parts),
            "parts_with_condition_aware_comparison": len(condition_compatible),
            "unique_opns_with_at_least_one_comparison": len(comparable_opns),
            "comparable_pairs_by_axis_and_class": comparable_pairs,
            "grain": "pairs computed per (axis, rating_class) where both parts have verified claims; NOT independently verified pairwise comparisons",
        },
        "axes": {
            f"{k[0]}:{k[1]}": len(v)
            for k, v in sorted(axis_class_parts.items(), key=lambda x: -len(x[1]))[:20]
        },
    }
    (staging / "spine_metrics.json").write_text(json.dumps(metrics, indent=1))

    print(json.dumps({
        "parts_staged": len(parts),
        "claims_staged": stage_counts.get("claims_emitted", 0),
        "compare_rows": len(compare_rows),
        "unique_opns_with_comparisons": len(comparable_opns),
        "comparable_pairs (axis×class grain)": comparable_pairs,
        "condition_aware_parts": len(condition_compatible),
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
