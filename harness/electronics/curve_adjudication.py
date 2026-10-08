"""Immutable evidence-packet adjudication for curve evidence (CURVE-04 R1).

An evidence packet is the immutable review artifact for ONE digitized
curve series: full provenance, axes, coordinates, conditions, uncertainty,
extraction algorithm + version, machine-verification results, and the
render a human checks it against. Packets are content-addressed —
``packet_hash = sha256(canonical_json(packet))`` — so any material change
mints a new hash and prior decisions no longer apply to it.

Adjudication states:

    machine_verified -> human_review_pending -> human_approved
                                             -> human_rejected
                                             -> needs_rework

Machine verification NEVER promotes evidence to human-approved gold: only
an explicit decision in the append-only ledger by a named reviewer does,
and that decision references the exact packet hash it approved. The
effective state of a packet is derived by replaying ledger entries for its
hash — packets themselves are never mutated by review.

The ledger (JSONL, append-only) carries: packet_hash, state transition,
reviewer identity, timestamp, optional note. A decision on hash H is
invalid for hash H' — invalidation is structural, not procedural.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

from harness.electronics.curve_evidence import (
    CurveEvidence,
    unit_base,
)

ADJUDICATION_SCHEMA = "harness.electronics-curve-adjudication.v1"
STATES = (
    "machine_verified",
    "human_review_pending",
    "human_approved",
    "human_rejected",
    "needs_rework",
)
_TRANSITIONS = {
    "machine_verified": {"human_review_pending", "human_rejected"},
    "human_review_pending": {"human_approved", "human_rejected",
                             "needs_rework"},
    "needs_rework": {"human_review_pending", "human_rejected"},
    "human_approved": set(),
    "human_rejected": set(),
}
_HUMAN_STATES = {"human_approved", "human_rejected"}


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False, default=str,
    ).encode("utf-8")


def _render_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_packet(
    curve: CurveEvidence,
    *,
    render_path: Path | None = None,
    extraction_algorithm: str = "vector_reference",
    extraction_version: str = "extract_vector_curve_references.v1",
) -> dict[str, Any]:
    """One immutable packet per curve series. Render reference is
    content-hashed; the packet never embeds mutable pointers without
    hashes."""

    q = curve._numeric_quality if hasattr(curve, "_numeric_quality") \
        else (curve.uncertainty or {}).get("numeric_quality")
    packet: dict[str, Any] = {
        "schema": ADJUDICATION_SCHEMA,
        "packet_kind": "curve_series",
        "provenance": {
            "document_sha256": curve.document_sha256,
            "figure_revision": curve.figure_revision,
            "page_1based": curve.page_1based,
            "figure_index": curve.figure_index,
            "series_index": curve.series_index,
            "caption": curve.caption,
            "plot_bbox": list(curve.region_bbox) if curve.region_bbox
            else None,
            "series_identity": {
                "name": curve.series.get("name"),
                "legend_binding": "legend_swatch_color_match"
                if curve.series.get("name") else "single_trace",
            },
            "extraction_algorithm": extraction_algorithm,
            "extraction_version": extraction_version,
            "render": None,
        },
        "axes": {
            side: {
                "label": (curve.axes.get(side) or {}).get("label"),
                "unit": (curve.axes.get(side) or {}).get("unit"),
                "scale": (curve.axes.get(side) or {}).get("scale")
                or ("log10" if (side == "x" and curve.x_scale == "log10")
                    else "linear"),
                **{k: (curve.axes.get(side) or {}).get(k)
                   for k in ("min", "max")},
            }
            for side in ("x", "y")
        },
        "coordinates": [
            [round(p["x"], 6), round(p["y"], 6)] for p in curve.points
        ],
        "conditions": curve.conditions,
        "applicability": curve.applies_to,
        "uncertainty": curve.uncertainty,
        "relevance": [t["phenomenon"] for t in curve.relevance],
        "machine_verification": {
            "status": curve.verification.get("status"),
            "grounding": curve.verification.get("grounding"),
            "anchor_agreement": curve.verification.get("anchor_agreement"),
            "numeric_quality": q,
            "usable": curve.usable,
        },
    }
    if render_path is not None and Path(render_path).exists():
        packet["provenance"]["render"] = {
            "path": str(render_path),
            "sha256": _render_sha256(render_path),
            "note": "inspect this render alongside the coordinates",
        }
    packet["packet_hash"] = packet_content_hash(packet)
    return packet


def packet_content_hash(packet: Mapping[str, Any]) -> str:
    """Hash of the packet's content with any prior hash field removed —
    tampering detection is recompute-and-compare."""

    content = {k: v for k, v in dict(packet).items() if k != "packet_hash"}
    return "pkt-" + hashlib.sha256(canonical_json(content)).hexdigest()[:40]


def verify_packet_integrity(packet: Mapping[str, Any]) -> bool:
    return packet.get("packet_hash") == packet_content_hash(packet)


class AdjudicationLedger:
    """Append-only JSONL ledger. Effective state is derived by replay;
    entries are never edited or deleted."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def append(self, entry: Mapping[str, Any]) -> dict[str, Any]:
        state = entry.get("state")
        if state not in STATES:
            raise ValueError(f"unknown state {state!r}")
        packet_hash = entry.get("packet_hash")
        if not packet_hash:
            raise ValueError("ledger entries reference a packet hash")
        if state in _HUMAN_STATES or state == "needs_rework":
            reviewer = entry.get("reviewer")
            if not reviewer or not re.match(
                    r"^[A-Za-z0-9@._\- ]{2,64}$", str(reviewer)):
                raise ValueError(
                    "human decisions require a named reviewer identity"
                )
            current = self.state_of(packet_hash)["state"]
            if state not in _TRANSITIONS.get(current, set()):
                raise ValueError(
                    f"illegal transition {current} -> {state}"
                )
        record = {
            "ledger_schema": ADJUDICATION_SCHEMA,
            "time": time.time(),
            **{k: v for k, v in dict(entry).items() if k != "time"},
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(record, ensure_ascii=False, sort_keys=True,
                           default=str) + "\n"
            )
        return record

    def entries(self) -> list[dict[str, Any]]:
        out = []
        for line in self.path.read_text().splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out

    def state_of(self, packet_hash: str) -> dict[str, Any]:
        """Replay-derived state for one packet hash. Unknown hash -> no
        state (a packet without an emitted machine_verified entry is not
        in adjudication at all)."""

        state: str | None = None
        last: dict[str, Any] | None = None
        for entry in self.entries():
            if entry.get("packet_hash") != packet_hash:
                continue
            target = entry.get("state")
            if state is None:
                if target == "machine_verified":
                    state = target
                    last = entry
                continue
            if target in _TRANSITIONS.get(state, set()):
                state = target
                last = entry
        if state is None:
            return {"state": None, "entry": None}
        return {"state": state, "entry": last}

    def emit_machine_verified(self, packet: Mapping[str, Any],
                              machine_note: str = "") -> dict[str, Any]:
        return self.append({
            "packet_hash": packet["packet_hash"],
            "state": "machine_verified",
            "actor": packet["provenance"]["extraction_algorithm"],
            "note": machine_note,
        })

    def queue_review(self, packet_hash: str) -> dict[str, Any]:
        return self.append({
            "packet_hash": packet_hash, "state": "human_review_pending",
            "actor": "system",
        })

    def human_decision(
        self,
        packet_hash: str,
        decision: str,
        reviewer: str,
        note: str = "",
    ) -> dict[str, Any]:
        if not reviewer or not re.match(r"^[A-Za-z0-9@._\- ]{2,64}$",
                                        reviewer):
            raise ValueError("a named reviewer identity is required")
        if decision not in _HUMAN_STATES and decision != "needs_rework":
            raise ValueError(f"decision must be one of {STATES[1:]}")
        current = self.state_of(packet_hash)
        if current["state"] is None:
            raise ValueError(
                "packet has no machine_verified entry; nothing to review"
            )
        if decision not in _TRANSITIONS.get(current["state"], set()):
            raise ValueError(
                f"illegal transition {current['state']} -> {decision}"
            )
        return self.append({
            "packet_hash": packet_hash,
            "state": decision,
            "reviewer": reviewer,
            "note": note,
        })


def effective_states(
    packets: Sequence[Mapping[str, Any]],
    ledger: AdjudicationLedger,
) -> dict[str, dict[str, Any]]:
    return {
        p["packet_hash"]: ledger.state_of(p["packet_hash"])
        for p in packets
    }
