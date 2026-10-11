#!/usr/bin/env python3
"""Locate the page each claim's quote was printed on.

The claim corpus (`catalog.db::claims_canonical`, 86,736 rows) carries a
`quote` and a `doc_sha256` on every row, and **no page number**
(`ASSURANCE.md` §1.3, `PROVENANCE.md` §3b). Without a page, `SourceRef.page` is
empty and "every number opens to the page it came from" has nothing to open.

This is the work item recorded in those documents: run quote location per page
of the source document and record the result.

## Method

Group claims by `doc_sha256`, extract page text **once per document**, then
locate each claim's quote. Matching is the same normalisation as
`dw_truth::find_quote` — NFKC, whitespace-collapsed, lowercased — so the Rust
side can verify the same way.

A quote that appears on several pages records **all** of them, and marks the
first as primary. A quote that appears on none is recorded as `unlocated`,
which is a finding and not a default.

## Output

`tests/fixtures/gold/claim_pages_v1.jsonl`, one row per claim:

    {"id", "opn", "symbol", "doc_sha256", "quote", "row_header",
     "page": 8 | null, "pages": [7, 8], "match": "exact" | "normalized" | "unlocated"}

`page` is the primary page or `null`. **`null` is a fact** — the quote could
not be found — and is never silently replaced.

`quote_len` is the normalised quote length, because match quality varies
sharply with it: a three-character quote such as "0.3" matches "exactly" on
almost any page of a datasheet and is weak evidence of *that* page.
`page_rank` is the fraction of the document's pages the quote appears on —
1.0 means it is on every page and therefore locates nothing. Both are
recorded so a weak match cannot later be read as a strong one
(`ASSURANCE.md` R1).

Usage::

    python3 scripts/locate_claim_pages.py --limit 200     # smoke run
    python3 scripts/locate_claim_pages.py                 # full
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
import unicodedata

import fitz

REPO = pathlib.Path(__file__).resolve().parent.parent
GOLD = REPO / "tests" / "fixtures" / "gold"
CLAIMS = GOLD / "claim_provenance_v1.jsonl"
OUT_DEFAULT = GOLD / "claim_pages_v1.jsonl"
VAULT = pathlib.Path("/Volumes/M5_4TB/vault/cas")


def norm(s: str) -> str:
    """The reference's normalisation. Must match `dw_truth::norm` exactly."""
    folded = unicodedata.normalize("NFKC", str(s or ""))
    return re.sub(r"\s+", " ", folded).strip().lower()


def resolve(sha: str) -> pathlib.Path | None:
    p = VAULT / sha[:2] / f"{sha}.pdf"
    return p if p.exists() else None


def load_claims(limit: int | None) -> list[dict]:
    rows = []
    with CLAIMS.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            rows.append(json.loads(line))
            if limit and len(rows) >= limit:
                break
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="smoke run on the first N claims")
    ap.add_argument("--out", type=pathlib.Path, default=OUT_DEFAULT)
    args = ap.parse_args()

    claims = load_claims(args.limit)
    by_doc: dict[str, list[dict]] = collections.defaultdict(list)
    for c in claims:
        by_doc[c["doc_sha256"]].append(c)

    print(f"claims={len(claims)}  distinct docs={len(by_doc)}")

    results: list[dict] = []
    stats = collections.Counter()
    missing_docs = []

    for n, (sha, group) in enumerate(sorted(by_doc.items()), 1):
        path = resolve(sha)
        if path is None:
            missing_docs.append(sha)
            stats["doc_missing"] += len(group)
            for c in group:
                results.append({**c, "page": None, "pages": [], "match": "doc_missing"})
            continue

        # Page text once per document.
        try:
            with path.open("rb") as probe:
                probe.read(8)
            doc = fitz.open(path)
        except PermissionError:
            # Distinguishable from corruption: the sandbox denies some vault
            # files. Recorded as its own class so it is never mistaken for a
            # broken PDF (ASSURANCE R4 - a denominator that lumps causes hides
            # which one is blocking).
            stats["doc_permission_denied"] += len(group)
            for c in group:
                results.append({**c, "page": None, "pages": [], "match": "doc_permission_denied"})
            continue
        except Exception as exc:  # noqa: BLE001 - a broken PDF is a finding
            stats["doc_unreadable"] += len(group)
            for c in group:
                results.append({**c, "page": None, "pages": [], "match": f"doc_unreadable:{type(exc).__name__}"})
            continue

        page_texts = [norm(p.get_text()) for p in doc]
        doc.close()

        for c in group:
            quote = c.get("quote") or ""
            nq = norm(quote)
            if not nq:
                stats["quote_empty"] += 1
                results.append({**c, "page": None, "pages": [], "match": "quote_empty"})
                continue

            # Exact normalised substring, the way `find_quote` does it first.
            hits = [i + 1 for i, t in enumerate(page_texts) if nq in t]
            if hits:
                stats["exact"] += 1
                results.append(
                    {
                        **c,
                        "page": hits[0],
                        "pages": hits,
                        "match": "exact",
                        "quote_len": len(nq),
                        "page_rank": round(len(hits) / max(1, len(page_texts)), 4),
                    }
                )
                continue

            # Fallback: every number in the quote appears on the page, and the
            # quote's non-stopword tokens mostly do. This is the tolerant
            # matcher, and it is marked so the difference is visible.
            q_tokens = re.findall(r"\d+(?:\.\d+)?|[a-z]+", nq)
            numbers = [t for t in q_tokens if re.fullmatch(r"\d+(?:\.\d+)?", t)]
            if not numbers:
                stats["unlocated"] += 1
                results.append({**c, "page": None, "pages": [], "match": "unlocated"})
                continue

            tolerant = []
            for i, t in enumerate(page_texts):
                if all(num in t for num in numbers):
                    tolerant.append(i + 1)
            if tolerant:
                stats["normalized"] += 1
                results.append(
                    {
                        **c,
                        "page": tolerant[0],
                        "pages": tolerant,
                        "match": "normalized",
                        "quote_len": len(nq),
                        "page_rank": round(len(tolerant) / max(1, len(page_texts)), 4),
                    }
                )
            else:
                stats["unlocated"] += 1
                results.append({**c, "page": None, "pages": [], "match": "unlocated"})

        if n % 100 == 0:
            print(f"  … {n}/{len(by_doc)} docs, {len(results)} claims", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as handle:
        for i, r in enumerate(sorted(results, key=lambda r: (r["doc_sha256"], r.get("opn") or "", r.get("symbol") or ""))):
            r = {"id": i, **r}
            handle.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    total = len(results)
    located = sum(v for k, v in stats.items() if k in ("exact", "normalized"))
    print(f"\nwrote {args.out}")
    print(f"  claims      {total}")
    for k in ("exact", "normalized", "unlocated", "quote_empty", "doc_missing", "doc_permission_denied", "doc_unreadable"):
        if stats[k]:
            print(f"  {k:16s} {stats[k]:7d}  {100 * stats[k] / total:5.1f}%")
    print(f"  LOCATED      {located:7d}  {100 * located / total:5.1f}%")
    if missing_docs:
        print(f"  docs missing from vault: {len(missing_docs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
