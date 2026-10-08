#!/usr/bin/env python3
"""Adjudication workflow CLI for curve evidence packets (CURVE-04 R1).

  emit     build immutable packets for the frozen pilot, render each page,
           stamp machine_verified + queue human review
  status   list packets with replay-derived adjudication states
  approve|reject|rework   record a named human decision on a packet hash

Decisions reference the packet hash; any material change to a packet mints
a new hash and prior approvals do not carry over. Nothing here can promote
machine output to human-approved gold by itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.discovery.curves import load_reference_curves  # noqa: E402
from harness.electronics.curve_adjudication import (  # noqa: E402
    AdjudicationLedger,
    build_packet,
    effective_states,
)

PILOT = ROOT / "tests/fixtures/gold/curve_evidence_pilot"
OUT = ROOT / "results/curve-adjudication"
TI_DIR = Path("/Volumes/M5_4TB/data/ti_power_datasheets")
DELIVERY = Path(
    "/Volumes/M5_4TB/exports/corpus-deliveries/datasheet-tail-20261007"
)


def _pdf_for(record: dict) -> Path | None:
    sha = str(record.get("document_sha256") or "")
    if sha and DELIVERY.exists():
        hit = DELIVERY / "blobs" / sha[:2] / sha[2:4] / f"{sha}.pdf"
        if hit.exists():
            return hit
    candidate = TI_DIR / str(record.get("source_artifact") or "")
    if candidate.exists():
        return candidate
    return None


def emit(args: argparse.Namespace) -> int:
    files = sorted(
        p for p in Path(args.pilot_dir).glob("*.json")
        if not p.name.startswith("_")
    )
    (OUT / "packets").mkdir(parents=True, exist_ok=True)
    renders = OUT / "renders"
    renders.mkdir(parents=True, exist_ok=True)
    ledger = AdjudicationLedger(OUT / "ledger.jsonl")
    packets = []
    for path in files:
        record = json.loads(path.read_text())
        pdf = _pdf_for(record)
        render = None
        if pdf is not None and args.render:
            doc = pymupdf.open(pdf)
            page = doc[int(record.get("page_1based") or 1) - 1]
            out_png = renders / f"{path.stem}.png"
            page.get_pixmap(
                matrix=pymupdf.Matrix(args.dpi / 72.0, args.dpi / 72.0)
            ).save(str(out_png))
            doc.close()
            render = out_png
        for plot in record.get("plots") or []:
            from harness.electronics.curve_evidence import (
                CurveEvidence,
            )
            for index in range(len(plot.get("series") or [])):
                curve = CurveEvidence.from_reference_plot(
                    record, plot, index
                )
                packet = build_packet(
                    curve, render_path=render,
                    extraction_version=str(
                        record.get("extraction_version")
                        or "extract_vector_curve_references.v1"
                    ),
                )
                packet["_source_file"] = path.name
                packets.append(packet)
                ledger.emit_machine_verified(packet)
                ledger.queue_review(packet["packet_hash"])
    manifest = {
        "schema": "harness.electronics-curve-adjudication-manifest.v1",
        "packets": [
            {k: v for k, v in p.items() if not k.startswith("_")}
            for p in packets
        ],
        "sources": sorted({p["_source_file"] for p in packets}),
        "render_dpi": args.render and args.dpi,
        "count": len(packets),
    }
    (OUT / "packets/manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, default=str)
        + "\n"
    )
    print(json.dumps({"packets": len(packets), "out": str(OUT)}))
    return 0


def status(args: argparse.Namespace) -> int:
    manifest = json.loads(
        (OUT / "packets/manifest.json").read_text()
    ) if (OUT / "packets/manifest.json").exists() else {"packets": []}
    ledger = AdjudicationLedger(OUT / "ledger.jsonl")
    states = effective_states(manifest["packets"], ledger)
    counts: dict[str, int] = {}
    rows = []
    for packet in manifest["packets"]:
        state = states[packet["packet_hash"]]["state"] or "not_emitted"
        counts[state] = counts.get(state, 0) + 1
        rows.append({
            "packet_hash": packet["packet_hash"],
            "caption": packet["provenance"]["caption"],
            "series": packet["provenance"]["series_identity"]["name"],
            "state": state,
        })
    if args.hash:
        rows = [r for r in rows if r["packet_hash"] == args.hash]
    print(json.dumps({"counts": counts,
                      "rows": rows if args.hash or args.verbose else None},
                     indent=2, default=str))
    return 0


def decide(args: argparse.Namespace) -> int:
    ledger = AdjudicationLedger(OUT / "ledger.jsonl")
    entry = ledger.human_decision(
        args.hash, args.decision, args.reviewer, args.note or ""
    )
    print(json.dumps({
        "recorded": True,
        "packet_hash": entry["packet_hash"],
        "state": entry["state"],
        "reviewer": entry["reviewer"],
        "effective": ledger.state_of(args.hash),
    }, default=str))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_emit = sub.add_parser("emit")
    p_emit.add_argument("--pilot-dir", type=Path, default=PILOT)
    p_emit.add_argument("--dpi", type=int, default=300)
    p_emit.add_argument("--no-render", dest="render", action="store_false")
    p_emit.set_defaults(render=True)
    p_emit.set_defaults(func=emit)

    p_status = sub.add_parser("status")
    p_status.add_argument("--hash")
    p_status.add_argument("--verbose", action="store_true")
    p_status.set_defaults(func=status)

    for verb in ("approve", "reject", "rework"):
        p_dec = sub.add_parser(verb)
        p_dec.add_argument("--hash", required=True)
        p_dec.add_argument("--reviewer", required=True)
        p_dec.add_argument("--note", default="")
        p_dec.set_defaults(
            func=decide,
            decision={"approve": "human_approved",
                      "reject": "human_rejected",
                      "rework": "needs_rework"}[verb],
        )

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
