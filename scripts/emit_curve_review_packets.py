#!/usr/bin/env python3
"""Emit human review packets for the frozen curve pilot (CURVE-03 Phase 1).

For every referenced plot: a 300-DPI render of the page, the machine
reference (axes, series, points, conditions, numeric quality) as JSON,
and a checklist mirroring the PRD's qualification items (coordinates,
labels, conditions, applicability, orientation notes). A human reviewer
marks each packet signed (or flags errors) in ``signoff.json``; the pilot
scorer reports signed vs pending counts and never treats unsigned machine
references as human gold.

Usage:
  python scripts/emit_curve_review_packets.py \
      --pilot-dir tests/fixtures/gold/curve_evidence_pilot \
      --out results/curve-review
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TI_DIR = Path("/Volumes/M5_4TB/data/ti_power_datasheets")
DELIVERY_ROOT = Path(
    "/Volumes/M5_4TB/exports/corpus-deliveries/datasheet-tail-20261007"
)

CHECKLIST = [
    "axis_labels_verbatim",
    "axis_units_and_scale",
    "series_names_match_legend",
    "sampled_points_on_trace",
    "conditions_verbatim",
    "applicability_part_family",
    "orientation_note_resolved",
]


def _resolve_pdf(record: dict) -> Path | None:
    name = str(record.get("source_artifact") or "")
    candidate = TI_DIR / name
    if candidate.exists():
        return candidate
    sha = str(record.get("document_sha256") or "")
    if sha and DELIVERY_ROOT.exists():
        hit = DELIVERY_ROOT / "blobs" / sha[:2] / (sha[2:4]) / f"{sha}.pdf"
        if hit.exists():
            return hit
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pilot-dir", type=Path,
                    default=ROOT / "tests/fixtures/gold/curve_evidence_pilot")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "results/curve-review")
    args = ap.parse_args()

    files = sorted(
        p for p in args.pilot_dir.glob("*.json") if not p.name.startswith("_")
    )
    args.out.mkdir(parents=True, exist_ok=True)
    manifest = []
    signoff_path = args.out / "signoff.json"
    signoff = {}
    if signoff_path.exists():
        signoff = json.loads(signoff_path.read_text())
    packets = total = 0
    for path in files:
        record = json.loads(path.read_text())
        pdf = _resolve_pdf(record)
        if pdf is None:
            manifest.append({"file": path.name, "error": "pdf_not_found"})
            continue
        doc = pymupdf.open(pdf)
        try:
            page = doc[int(record.get("page_1based") or 1) - 1]
            pixmap = page.get_pixmap(
                matrix=pymupdf.Matrix(300 / 72.0, 300 / 72.0)
            )
            packet_dir = args.out / path.stem
            packet_dir.mkdir(parents=True, exist_ok=True)
            render = packet_dir / "page.png"
            pixmap.save(str(render))
            (packet_dir / "reference.json").write_text(
                json.dumps(record, indent=2, ensure_ascii=False) + "\n"
            )
            (packet_dir / "checklist.json").write_text(json.dumps({
                "document_sha256": record.get("document_sha256"),
                "page_1based": record.get("page_1based"),
                "items": CHECKLIST,
                "instructions": (
                    "Compare reference.json against page.png at each "
                    "checklist item. Mark true when verified; add "
                    "corrections as notes. Unsigned packets stay machine "
                    "references — never human gold."
                ),
            }, indent=2) + "\n")
            total += len(record.get("plots") or [])
            packets += 1
            key = path.stem
            if key not in signoff:
                signoff[key] = {"signed": False, "items": {}, "notes": None}
        finally:
            doc.close()
    signoff_path.write_text(json.dumps(signoff, indent=2) + "\n")
    (args.out / "packets.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print(json.dumps({
        "packets": packets, "plots": total, "out": str(args.out),
        "signoff_file": str(signoff_path),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
