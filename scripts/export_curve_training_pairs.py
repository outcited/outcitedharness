#!/usr/bin/env python3
"""Export verified curve rows as vision SFT training pairs.

The factory law: no spend path produces a model answer without a training
pair. Every burn row carries the render sha (content-addressed image), the
deterministic prompt (curve_prompt), and the verified response — this
exporter mints them as harness.electronics-training-pair.v1 candidates:

  capability=typical_characteristics, modality=vision,
  image_sha256=[render sha], teacher=<engine identity>,
  source_claim_ids=[figure-row claim ids]

Only verdict=extracted rows become pairs; holds/rejects stay out (a held
answer never trains). DPO preference pairs come from dual-engine overlaps
(follow-up: same page through two engines, prefer the verified-better).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.electronics.models import (  # noqa: E402
    ModelIdentity,
    PairCapability,
    PairModality,
    TrainingPairCandidate,
)
from harness.electronics.claims import canonical_json  # noqa: E402


def _prompt_sha() -> str:
    from harness.electronics.cloud_vision import curve_prompt

    return hashlib.sha256(curve_prompt().encode()).hexdigest()


def _claim_id(row: dict) -> str:
    key = canonical_json(
        {
            "document_sha256": row.get("document_sha256"),
            "page_1based": row.get("page_1based"),
            "figure_index": row.get("figure_index"),
            "verdict": row.get("verdict"),
        }
    )
    return "claim-" + hashlib.sha256(key).hexdigest()[:32]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", type=Path, nargs="+", required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--engine",
        required=True,
        help="teacher identity for these rows (e.g. 'deepseek|deepseek-flash', "
        "'cloud|mimo-v2.6-flash', 'local|deepseek-v4.1-flash')",
    )
    args = ap.parse_args()

    provider, model = args.engine.split("|", 1)
    prompt_sha = _prompt_sha()
    pairs = 0
    skipped = 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a") as sink:
        for rows_path in args.rows:
            if not rows_path.exists():
                skipped += 1
                continue
            for line in rows_path.read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if row.get("verdict") != "extracted":
                    continue
                payload = row.get("payload") or {}
                response = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                if not response.strip():
                    continue
                pair_id = "pair-" + hashlib.sha256(
                    canonical_json(
                        {
                            "render": row.get("render_sha256"),
                            "figure": row.get("figure_index"),
                            "page": row.get("page_1based"),
                            "doc": row.get("document_sha256"),
                        }
                    )
                ).hexdigest()[:32]
                candidate = TrainingPairCandidate(
                    pair_id=pair_id,
                    capability=PairCapability.TYPICAL_CHARACTERISTICS,
                    modality=PairModality.VISION,
                    prompt="curve-digitization-v1:" + prompt_sha,
                    response=response,
                    source_claim_ids=[_claim_id(row)],
                    lineage_ids=[
                        row.get("document_sha256") or "",
                        f"page:{row.get('page_1based')}",
                        f"figure:{row.get('figure_index')}",
                        f"render:{row.get('render_sha256')}",
                    ],
                    image_uris=[str(row.get("render_sha256"))],
                    image_sha256=[row.get("render_sha256")],
                    teacher=ModelIdentity(
                        provider=provider,
                        model=model,
                        request_sha256=row.get("render_sha256"),
                    ),
                )
                sink.write(json.dumps(candidate.model_dump(by_alias=True), ensure_ascii=False, sort_keys=True) + "\n")
                pairs += 1
    print(json.dumps({"pairs": pairs, "files_missing": skipped, "out": str(args.out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
