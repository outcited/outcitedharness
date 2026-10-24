#!/usr/bin/env python3
"""Python-side frozen-response replay for RUST-CUTOVER-01.

Runs the LIVE burn_aisle.burn_doc cascade over a cohort with every model
call served from the frozen response cache (the same cache dw-soak's shim
uses in replay mode). Monkeypatches:

- burn_aisle.call            -> soak_model_shim.call (record/replay cache)
- burn_aisle.load_substrate  -> local NVMe substrate copies (NAS-flake
  immune and identical to the Rust run's inputs)

Everything else — join, quick_ok, repair rounds, verify, page attribution —
is the untouched reference code. Output: one {stem}.json per doc, same
shape as the burn rows, into --out.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", type=Path, required=True,
                    help="dw-soak queue JSON (work items carry stem+substrate)")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    shim = _load("soak_model_shim", str(HERE / "soak_model_shim.py"))
    burn = shim.burn  # the live burn_aisle module, already imported by the shim

    mode = shim.SHIM_MODE
    if mode not in ("record", "replay"):
        print("set SOAK_SHIM_MODE=record|replay + SOAK_SHIM_CACHE", file=sys.stderr)
        return 2

    queue = json.loads(args.queue.read_text())
    work = queue["work"]
    if args.limit:
        work = work[: args.limit]

    # substrate resolution -> local paths carried by the queue
    def load_substrate_local(stem):
        for item in queue["work"]:
            if item["stem"] == stem and item.get("substrate"):
                try:
                    return json.loads(Path(item["substrate"]).read_text())
                except Exception:
                    return None
        return None

    burn.load_substrate = load_substrate_local
    burn.call = shim.call  # every model call (incl. inside propose) is cached

    args.out.mkdir(parents=True, exist_ok=True)
    con = None  # burn_doc never touches it in the current reference
    misses = []
    for i, item in enumerate(work):
        try:
            row = burn.burn_doc(item["stem"], item.get("pdf", ""), {}, con, None)
        except RuntimeError as exc:
            if "REPLAY_CACHE_MISS" in str(exc):
                misses.append(item["stem"])
                continue
            raise
        (args.out / f"{item['stem']}.json").write_text(
            json.dumps(row, ensure_ascii=False)
        )
        if (i + 1) % 25 == 0:
            print(f"{i + 1}/{len(work)}", flush=True)
    print(json.dumps({"done": len(work) - len(misses), "mode": mode,
                      "cache_misses": misses}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
