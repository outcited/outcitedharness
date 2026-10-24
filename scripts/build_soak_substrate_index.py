#!/usr/bin/env python3
"""Build the stem -> substrate index for the dw-soak Rust worker.

Resolves each cohort stem exactly the way burn_aisle.load_substrate does
(root loop order preserved: collected, runs, ASUSTOR dispatch) so the Rust
soak's substrate choice is identical to the Python factory's. Output is a
JSON {stem: path-or-null} consumed by `dw-soak enqueue --substrate-index`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SUBSTRATE_DIRS = [
    Path("/Volumes/M5_4TB/extract-results/collected"),
    Path("/Volumes/M5_4TB/extract-results/runs"),
    Path("/Users/samkim/mnt/yosemiteraid5-nfs/m5-opencode/dispatch/collected"),
]


def resolve(stem: str) -> str | None:
    for d in SUBSTRATE_DIRS:
        try:
            hits = sorted(d.rglob(f"{stem}.json"))
        except OSError:
            continue
        if hits:
            try:
                json.loads(hits[0].read_text())
                return str(hits[0])
            except Exception:
                return None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cohort", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    cohort = json.loads(args.cohort.read_text())
    stems = [Path(p).stem for p in cohort["pdfs"]]
    index: dict[str, str | None] = {}
    for i, stem in enumerate(stems):
        index[stem] = resolve(stem)
        if (i + 1) % 200 == 0:
            found = sum(1 for v in index.values() if v)
            print(f"{i + 1}/{len(stems)} resolved so far: {found}", flush=True)
    found = sum(1 for v in index.values() if v)
    args.out.write_text(json.dumps(index, ensure_ascii=False, indent=0) + "\n")
    print(json.dumps({"stems": len(stems), "substrate_found": found}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
