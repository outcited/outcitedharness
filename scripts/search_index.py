"""Evidence-index CLI (PRD-SEARCH-01).

Build/refresh the search index incrementally from the catalog and the
topology wave. Idempotent: re-running with unchanged inputs is a no-op
(natural key = doc sha + grain + locator + extraction version).

Usage:
  scripts/search_index.py                          # default paths, write
  scripts/search_index.py --dry-run                # report only
  scripts/search_index.py --catalog X --db Y       # explicit paths
  scripts/search_index.py --release                # print pinned release
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.search import indexer, units  # noqa: E402

DEFAULT_CATALOG = "/Volumes/M5_4TB/extract-results/catalog.db"
DEFAULT_WAVE = "/Volumes/M5_4TB/extract-results/power-topology-v1.jsonl"
DEFAULT_DB = units.DEFAULT_DB


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--catalog", default=DEFAULT_CATALOG)
    parser.add_argument("--wave", default=DEFAULT_WAVE,
                        help="power-topology wave jsonl (optional)")
    parser.add_argument("--no-wave", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--release", action="store_true",
                        help="print the pinned release and exit")
    args = parser.parse_args()

    if args.release:
        con = units.connect(args.db)
        print(json.dumps(units.index_release(con), indent=2))
        return 0

    if not Path(args.catalog).exists():
        print(f"catalog not found: {args.catalog}", file=sys.stderr)
        return 2
    wave = None if args.no_wave else args.wave

    stats = indexer.index_catalog(
        units.connect(args.db), args.catalog,
        wave_path=wave, dry_run=args.dry_run)
    con = units.connect(args.db)
    release = units.index_release(con)
    print(json.dumps({"stats": stats, "release": release}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
