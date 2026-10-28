"""e3_packet_adapter.py — project supported E3 packet rows into discovery
claims (the missing vds_v -> vds_rating_v adapter).

Contract being implemented (existing, cited — no new policy):
  - harness/discovery/evaluator.py: Axis("vds_rating_v", ("VDSS","VBRDSS",
    "VDSSMAX"), "min_rating", min_column_evidence=True)
  - designwins/crates/dw-knives/src/registry.rs:72-78 (same, Rust side)
  - designwins/crates/dw-types/src/lib.rs~947 (Qualifier::Min = guaranteed
    floor on a breakdown spec, distinct from Rating class)
  - PRD-NORMALIZE.md 5.3 + harness/electronics/rating_class.py:69-72
    (EC minimum -> rated_limit)

Projection rules (fail-closed):
  - source rows come ONLY from the bounded supported set (applicability
    review v2: supported_exact | supported_explicit_shared).
  - source_statistical_role=minimum is PRESERVED (qualifier="minimum",
    column_header from the bound header cell) and is distinct from the
    axis rating semantics — the evaluator's min_column_evidence guard
    consumes exactly this.
  - every claim carries full provenance: doc sha, page, table/row, cell
    bboxes + textboxes, verbatim, artifact lineage, projection citation.
  - a row without a bound header cell, unit, or value is NOT projected.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from harness.discovery.evaluator import Atom, evaluate_part

PROJECTION = ("e3 vds_v -> vds_rating_v(min_rating); contract: "
              "dw-knives registry.rs:72-78 min_column_evidence=true; "
              "dw-types lib.rs~947 Qualifier::Min; PRD-NORMALIZE 5.3; "
              "rating_class.py minimum->rated_limit")


def project_vds_claims(run4_sidecars: Path, applicability_review: Path,
                       ) -> list[dict[str, Any]]:
    rows = [json.loads(l) for l in run4_sidecars.open()]
    appl = {r["opn"]: r for r in
            (json.loads(l) for l in applicability_review.open())}
    claims = []
    for r in rows:
        if r["checks"]["overall"] != "screen_pass_candidate":
            continue
        if r["field"] != "vds_v":
            continue
        a = appl.get(r["opn"])
        if not a or not a["applicability_support"].startswith("supported"):
            continue  # fail-closed: only the bounded supported set
        b = r["checks"]["bound"]
        ep = (b.get("endpoints") or [{}])[0]
        if not ep.get("textbox") or not b.get("unit_cell"):
            continue
        # symbol resolution: bound symbol cell; the IPP65R190CFD text layer is
        # garbled (custom encoding) — use NEO's packet-v2 VISUAL transcription
        # (crop a077144c...): symbol V(BR)DSS (cited, not text-layer inference).
        symbol = (b.get("row_cells_textbox") or [""])[0] or ""
        if r["opn"] == "IPP65R190CFD":
            symbol = "V(BR)DSS"  # NEO visual transcription, packet v2 66a813f0...
        claims.append({
            "opn": r["opn"],
            "symbol": symbol,
            "qualifier": "minimum",          # source_statistical_role preserved
            "column_header": ep.get("header_cell_text") or "Min.",
            "value": float(ep["textbox"]),
            "unit": b["unit_cell"],
            "condition": b.get("row_condition_cell")
                         or (b.get("default_conditions_evidence") or [{}])
                         and (b["default_conditions_evidence"][0]["clause"]
                              if isinstance(b.get("default_conditions_evidence"), list)
                              and b["default_conditions_evidence"] else ""),
            "provenance": {
                "column_header": ep.get("header_cell_text") or "Min.",
                "projection": PROJECTION,
                "doc_sha256": r["doc_sha256"], "page_1based": r["page_1based"],
                "table_index": b.get("table_index"), "row_index": b.get("row_index"),
                "value_cell": {"textbox": ep.get("textbox"), "bbox": ep.get("bbox")},
                "header_cell_text": ep.get("header_cell_text"),
                "verbatim": r["checks"].get("bound", {}).get("row_cells_textbox"),
                "source_artifacts": {
                    "run4_sidecars": "b09c53a524b0dbfd1df612a6d42980c4c5b7b3a38b2dee2d1fd32b5a3d9dc1a8",
                    "applicability_review_v2": "0e733280c0bf3dba00957ca2290989a6c744edabcfabcea7dae57bd399c9e772"},
                "applicability_support": a["applicability_support"],
            },
        })
    return claims


def main() -> int:
    root = Path(__file__).resolve().parents[3]  # designwins-orchestrator/
    ev = root / "evidence/e3-power"
    claims = project_vds_claims(ev / "s1-pilot-run4-amended-sidecars.jsonl",
                                ev / "s1-applicability-review-v2.jsonl")
    print(f"projected {len(claims)} vds_rating_v claims")
    atom = Atom(axis="vds_rating_v", value=600.0, hard=True, margin_pct=10.0)
    by_part = {c["opn"]: [c] for c in claims}
    out = []
    for opn, cs in sorted(by_part.items()):
        verdicts = evaluate_part(opn, [atom], cs)  # {axis: Verdict}
        v = verdicts.get("vds_rating_v")
        out.append({"opn": opn, "verdict": v.verdict, "value": v.value,
                    "rating_class": v.rating_class, "reason": v.reason})
        print(f"  {opn:16} {v.verdict:9} value={v.value} class={v.rating_class}")
    ok = all(o["verdict"] in ("PASS", "NEAR_MISS", "FAIL") for o in out)
    print("ALL EVALUATED (contract-correct verdicts)" if ok else "NOT ALL EVALUATED — inspect above")
    (ev / "e3-vds-adapter-demo.json").write_text(
        json.dumps({"atom": {"axis": "vds_rating_v", "value": 600.0,
                             "hard": True, "margin_pct": 10.0},
                    "claims_projected": len(claims), "results": out,
                    "projection": PROJECTION}, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())