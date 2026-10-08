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
    parser.add_argument("--pipeline",
                        default="/Volumes/M5_4TB/extract-results/pipeline.db")
    parser.add_argument("--curves", default=None,
                        help="directory of curve-evidence fixtures to index")
    parser.add_argument("--sections", action="store_true",
                        help="index section-grain units from substrate "
                             "page text (pipeline.db results)")
    parser.add_argument("--sections-limit", type=int, default=None)
    parser.add_argument("--embed", default=None,
                        help="embeddings endpoint (http://host:8800/v1/"
                             "embeddings) — batch-attach unit vectors")
    parser.add_argument("--embed-model", default="bge-m3-cr-tapes-v1")
    parser.add_argument("--embed-batch", type=int, default=16)
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

    con = units.connect(args.db)
    stats = indexer.index_catalog(
        con, args.catalog, wave_path=wave, dry_run=args.dry_run)
    if args.curves:
        curve_stats = indexer.index_curves(con, args.curves,
                                           dry_run=args.dry_run)
        stats["curves"] = curve_stats
    if args.sections:
        stats["sections"] = indexer.index_sections(
            con, args.pipeline or
            "/Volumes/M5_4TB/extract-results/pipeline.db",
            dry_run=args.dry_run, limit_docs=args.sections_limit)
    if args.embed and not args.dry_run:
        stats["vectors"] = attach_vectors(con, args.embed, args.embed_model,
                                          batch=max(1, args.embed_batch))
    release = units.index_release(con)
    print(json.dumps({"stats": stats, "release": release}, indent=2))
    return 0


def attach_vectors(con, url: str, model: str, batch: int = 16) -> dict:
    """Idempotent vector attach: skips units already embedded with this
    model. 1024-dim BGE vectors, cosine at query time."""
    from harness.search.query import make_embedder
    embed = make_embedder(url, model)
    active = units.unit_count(con)
    rows = con.execute(
        "SELECT u.unit_id, u.text_repr FROM units u"
        " LEFT JOIN vectors v ON v.unit_id = u.unit_id AND v.model = ?"
        " WHERE v.unit_id IS NULL AND u.retired = 0"
        " ORDER BY u.unit_id", (model,)).fetchall()
    attached = 0
    for offset in range(0, len(rows), batch):
        chunk = rows[offset:offset + batch]
        vectors = embed([r["text_repr"] for r in chunk])
        for row, vector in zip(chunk, vectors):
            units.attach_vector(con, row["unit_id"], model, vector)
        attached += len(chunk)
    return {"model": model, "attached": attached,
            "skipped_already_embedded": active - attached}


if __name__ == "__main__":
    raise SystemExit(main())
