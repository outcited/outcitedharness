#!/usr/bin/env python3
"""RUST-CUTOVER-01 cliff experiment: controlled model-replay evidence for
the 47-vs-163 claim-doc gap.

For each sampled stem (cliff docs where exactly one engine got claims, plus
both-claim controls), fire K independent propose calls with the document's
full text against the SAME endpoint/chain the factory uses, and count how
many invocations return claims. High flip rates at the cliff + stable
controls = the gap is per-invocation model-lane variance, not engine
behavior. No verify, no repair — this measures the lane, nothing else.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", type=Path, required=True,
                    help="JSON {stem: substrate_path} x K")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    spec = importlib.util.spec_from_file_location(
        "shim", str(HERE / "soak_model_shim.py"))
    shim = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shim)
    assert shim.SHIM_MODE == "live", "run with SOAK_SHIM_MODE unset (live)"

    plan = json.loads(args.plan.read_text())
    results = {}
    for i, (stem, sub_path) in enumerate(sorted(plan.items())):
        sub = json.loads(Path(sub_path).read_text())
        texts = [p.get("text", "") or "" for p in sub.get("pages", [])]
        text = "\n".join(texts)[:28000]
        part = sub.get("filename", stem).rsplit(".", 1)[0]
        outcomes = []
        for k in range(args.repeats):
            try:
                out = shim.propose({"part": part, "text": text})
                claims = out["claims"]
                outcomes.append({
                    "k": k,
                    "got_claims": isinstance(claims, list) and len(claims) > 0,
                    "n": len(claims) if isinstance(claims, list) else 0,
                    "tier": out.get("tier"),
                })
            except Exception as exc:
                outcomes.append({"k": k, "error": str(exc)[:80]})
            time.sleep(2)
        hits = sum(1 for o in outcomes if o.get("got_claims"))
        results[stem] = {"hits": hits, "repeats": args.repeats,
                         "outcomes": outcomes}
        print(f"{stem}: {hits}/{args.repeats}", flush=True)

    stable = [s for s, r in results.items()
              if s.startswith("CTL_") or "ctl" in s.lower()]
    args.out.write_text(json.dumps(results, indent=1, ensure_ascii=False))
    print(json.dumps({
        "stems": len(results),
        "cliff_flip_summary": {
            s: f"{r['hits']}/{r['repeats']}" for s, r in results.items()}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
