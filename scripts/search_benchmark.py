"""Frozen-benchmark scorer for engineering-evidence retrieval (PRD eval).

Modes:
  --fixture            build a throwaway index from a fixture corpus jsonl
                       (used by CI; gold entries flagged "fixture": true)
  --live               run against the built search index (SEARCH_DB)
  --catalog PATH       also audit returned quotes against catalog provenance
                       (the "zero invented quotations" gate)

Benchmark entry shape (tests/fixtures/gold/search_benchmark_v1.jsonl):
  {"id", "aisle": mcu|power|connectors, "query", "kind", "fixture": bool,
   "expected": {"anchors": [opn/family/vendor tokens], "grains": [...],
                "page": int (optional)},
   "must_not": {"anchors": [...]} }

Metrics (PRD): Recall@10, nDCG@10 (binary relevance, anchors unordered),
source-locator accuracy (entries with an expected page), incorrect
family/OPN applicability (must_not violations), p50/p95 latency, invented
quotes (live+catalog only). Unsupported-conclusion entries carry empty
anchors: they are excluded from recall, included in quote audit + latency.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.search import indexer, query as query_service  # noqa: E402
from harness.search import units as units_store  # noqa: E402

BENCHMARK = Path(__file__).resolve().parents[1] / \
    "tests/fixtures/gold/search_benchmark_v1.jsonl"


def load_benchmark(path: Path, aisle: str | None = None,
                   fixture: bool | None = None) -> list[dict]:
    entries = []
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            entries.append(json.loads(line))
    if aisle:
        entries = [e for e in entries if e["aisle"] == aisle]
    if fixture is not None:
        entries = [e for e in entries if bool(e.get("fixture")) == fixture]
    return entries


def _unit_anchors(unit: dict) -> set[str]:
    """Every anchor-ish token a hit could legitimately match on."""
    tokens = set()
    for key in ("vendor", "family", "text_repr", "ident"):
        value = unit.get(key) or ""
        tokens.add(str(value).upper())
    for app in unit.get("applicability", []):
        tokens.add(str(app.get("value", "")).upper())
    return {t for t in tokens if t}


def _matches(unit: dict, anchor: str) -> bool:
    needle = anchor.upper()
    for token in _unit_anchors(unit):
        if needle in token or token in needle and len(token) > 3:
            return True
    return False


def run(entries: list[dict], con, limit: int = 10) -> dict:
    per_entry = []
    recalls, ndcgs = [], []
    locator_hits, locator_total = 0, 0
    must_not_violations = 0
    unsupported_entries = 0
    latencies = []
    for entry in entries:
        started = time.time()
        response = query_service.search(
            con, entry["query"], limit=limit, with_interpretations=False)
        latencies.append((time.time() - started) * 1000.0)
        hits = response["units"]
        anchors = entry.get("expected", {}).get("anchors") or []
        if not anchors:
            unsupported_entries += 1
            per_entry.append({"id": entry["id"], "kind": entry["kind"],
                              "recall": None, "note": "no-anchor entry"})
            continue
        expected_page = entry.get("expected", {}).get("page")
        if expected_page is not None:
            locator_total += 1
            for hit in hits:
                if any(_matches(hit, a) for a in anchors):
                    if hit.get("page") == expected_page:
                        locator_hits += 1
                    break
        top = hits[:limit]
        matched = set()
        hit_flags = []
        for hit in top:
            hit_anchors = {a for a in anchors if _matches(hit, a)}
            new = hit_anchors - matched
            matched |= hit_anchors
            hit_flags.append(bool(new))
        recall = len(matched) / max(1, len(anchors))
        recalls.append(min(recall, 1.0))
        dcg = sum(1.0 / math.log2(i + 2) for i, flag in
                  enumerate(hit_flags) if flag)
        ideal = sum(1.0 / math.log2(i + 2)
                    for i in range(min(len(anchors), limit)))
        ndcgs.append(dcg / ideal if ideal else 0.0)
        banned = (entry.get("must_not") or {}).get("anchors") or []
        for hit in hits:
            if any(_matches(hit, b) for b in banned):
                must_not_violations += 1
                break
        per_entry.append({"id": entry["id"], "kind": entry["kind"],
                          "recall": round(recall, 3)})
    latencies.sort()

    def pct(p):
        if not latencies:
            return 0.0
        return round(latencies[min(len(latencies) - 1,
                                   int(p * len(latencies)))], 2)

    return {
        "entries": len(entries),
        "scored": len(recalls),
        "unsupported_or_audit_only": unsupported_entries,
        "recall_at_10": round(statistics.fmean(recalls), 4) if recalls else None,
        "ndcg_at_10": round(statistics.fmean(ndcgs), 4) if ndcgs else None,
        "locator_accuracy": (round(locator_hits / locator_total, 4)
                             if locator_total else None),
        "locator_scored": locator_total,
        "must_not_violations": must_not_violations,
        "latency_p50_ms": pct(0.50),
        "latency_p95_ms": pct(0.95),
        "per_entry": per_entry,
    }


def audit_quotes(entries: list[dict], con, catalog_path: str,
                 limit: int = 10) -> dict:
    """Every quoted fragment the engine returned must exist in the catalog's
    recorded provenance — the zero-invented-quotations gate."""
    catalog = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True)
    known_quotes = set()
    for (prov,) in catalog.execute(
            "SELECT provenance FROM claims_canonical"):
        try:
            quote = json.loads(prov).get("quote")
        except json.JSONDecodeError:
            continue
        if quote:
            known_quotes.add(str(quote).strip())
    catalog.close()
    checked = invented = 0
    for entry in entries:
        response = query_service.search(
            con, entry["query"], limit=limit, with_interpretations=False)
        for hit in response["units"]:
            quote = (hit.get("locator") or {}).get("quote")
            if not quote:
                continue
            checked += 1
            if str(quote).strip() not in known_quotes:
                invented += 1
    return {"quotes_checked": checked, "invented": invented}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", default=str(BENCHMARK))
    parser.add_argument("--aisle", choices=("mcu", "power", "connectors"))
    parser.add_argument("--fixture", action="store_true",
                        help="score only fixture-flagged entries")
    parser.add_argument("--fixture-corpus",
                        default=str(BENCHMARK.parent / "search_fixture_corpus.jsonl"))
    parser.add_argument("--live", action="store_true",
                        help="score against SEARCH_DB (all entries)")
    parser.add_argument("--catalog",
                        default="/Volumes/M5_4TB/extract-results/catalog.db")
    parser.add_argument("--no-quote-audit", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if not args.live and not args.fixture:
        args.fixture = True

    entries = load_benchmark(Path(args.benchmark), aisle=args.aisle,
                             fixture=True if args.fixture else None)
    if not entries:
        print("no benchmark entries selected", file=sys.stderr)
        return 2

    con = units_store.connect(":memory:")
    if args.fixture:
        corpus_path = Path(args.fixture_corpus)
        if not corpus_path.exists():
            print(f"fixture corpus missing: {corpus_path}", file=sys.stderr)
            return 2
        fixture_con = indexer.catalog_from_corpus_jsonl(corpus_path)
        merged = list(indexer.claim_units(fixture_con))
        merged += list(indexer.family_units(fixture_con))
        grouped: dict[tuple[str, str], list[dict]] = {}
        for unit in merged:
            grouped.setdefault(
                (unit["doc_sha256"], unit["extraction_version"]),
                []).append(unit)
        for (doc, version), group in grouped.items():
            units_store.replace_document(con, group, doc_sha256=doc,
                                         extraction_version=version)
        fixture_con.close()
    else:
        con.close()
        con = units_store.connect()

    report = run(entries, con, limit=args.limit)
    output = {k: v for k, v in report.items()
              if k != "per_entry" or args.verbose}
    if not args.no_quote_audit and not args.fixture:
        output["quote_audit"] = audit_quotes(entries, con, args.catalog,
                                             limit=args.limit)
    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
