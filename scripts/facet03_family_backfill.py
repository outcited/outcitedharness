"""Family identity backfill (PRD-FACET-03 R2) — printed evidence only.

A family relationship is an engineering identity claim requiring evidence.
The only authority this pipeline accepts is the manufacturer's own printed
front matter: a family name (curated, published vocabulary below) appearing
in the first pages of the part's resolved datasheet, captured with page and
verbatim line.

- No part-name-prefix inference exists in this code (the norm_rule set has
  no string-similarity rule; identity.add_relationship refuses unknown
  rules).
- Every relationship is written status=proposed with {source sha, page,
  quote}. Nothing is verified here; promotion requires an owner-approved
  admission packet (written to facet03-packets/).
- Multiple distinct families printed for one document => both recorded and
  marked conflicting (distinguishable, never merged).
- Zero hits => the part's family stays UNKNOWN (no relationship row).

Vocabulary v1 (Infineon only — the families already evidenced in repo gold
fixtures plus published marketing families; TI/Rohm/Espressif have no
printed-family authority in scope for v1 and are reported unknown):

    CoolMOS, CoolSiC, StrongIRFET, OptiMOS, OptiREG, TrenchStop

Coverage denominator = distinct candidates (OPNs), per PRD.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from harness.search import identity, vendors  # noqa: E402
from facet03_resolve_provenance import (  # noqa: E402
    CATALOG, PACKET_DIR, PIPELINE, substrate_pages)

FAMILY_VOCAB_V1 = {
    "infineon": ("CoolMOS", "CoolSiC", "StrongIRFET", "OptiMOS",
                 "OptiREG", "TrenchStop"),
}
NORM_RULE = "front_matter_vocab_v1"
FRONT_MATTER_PAGES = 2
CONFIDENCE = 0.9


def _opn_of(stem: str, known_opns: set[str]) -> str | None:
    """The stem's part token, accepted only if it is a catalog OPN
    (membership starts from the recorded candidate, not from parsing)."""
    token = stem.split("-", 1)[-1].upper()
    if token in known_opns:
        return token
    upper = stem.upper()
    for opn in known_opns:
        if upper.endswith(opn):
            return opn
    return None


def scan_front_matter(pages: list[tuple[int, str]],
                      vocab: tuple[str, ...]) -> dict[str, tuple[int, str]]:
    """family token -> (page, match-centered verbatim excerpt).

    The substrate text is whitespace-normalized per page, so the capture is
    a window around the actual match (120 chars before, 200 after) — the
    quoted identifier always CONTAINS the family token it evidences.
    """
    hits: dict[str, tuple[int, str]] = {}
    for page_no, text in pages[:FRONT_MATTER_PAGES]:
        for token in vocab:
            if token in hits:
                continue
            m = re.search(rf"\b{re.escape(token)}\b", text, re.I)
            if m:
                start = max(0, m.start() - 120)
                excerpt = text[start:m.end() + 200].strip()
                hits[token] = (page_no, excerpt[:340])
    return hits


def backfill(identity_con: sqlite3.Connection,
             pipeline_con: sqlite3.Connection,
             catalog_con: sqlite3.Connection, *,
             dry_run: bool = False) -> dict:
    known_opns = {r[0].upper() for r in
                  catalog_con.execute("SELECT opn FROM parts")}
    vendor_of = {r[0].upper(): (r[1] or "") for r in
                 catalog_con.execute("SELECT opn, vendor FROM parts")}
    rows = identity_con.execute(
        "SELECT stem, resolved_sha256 FROM provenance_map WHERE"
        " resolved_sha256 IS NOT NULL AND bytes_verified=1 AND"
        " quote_verified_claims > 0").fetchall()

    report = {"stems_scanned": 0, "opn_matched": 0, "family_hits": 0,
              "relationships_proposed": 0, "conflicts": 0,
              "no_evidence": 0, "by_family": defaultdict(int),
              "by_vendor_scanned": defaultdict(int)}
    packets: dict[str, dict] = {}

    for row in rows:
        stem, sha = row["stem"], row["resolved_sha256"]
        report["stems_scanned"] += 1
        opn = _opn_of(stem, known_opns)
        vendor_raw = vendor_of.get(opn or "", "")
        vendor = vendors.resolve(vendor_raw)["canonical"]
        report["by_vendor_scanned"][vendor or "(unresolved)"] += 1
        if opn is None:
            continue
        report["opn_matched"] += 1
        vocab = FAMILY_VOCAB_V1.get(vendor or "")
        if not vocab:
            report["no_evidence"] += 1
            continue
        pages = substrate_pages(pipeline_con, sha)
        hits = scan_front_matter(pages, vocab)
        if not hits:
            report["no_evidence"] += 1
            continue
        report["family_hits"] += 1
        mfr_id = f"mfr:{vendor}"
        child_id = f"opn:{opn}"
        proposed = []
        for token, (page_no, line) in sorted(hits.items()):
            family_id = identity.identity_id_for("family", token, mfr_id)
            if dry_run:
                proposed.append((family_id, token, page_no, line))
                continue
            identity.ensure_identity(
                identity_con, "family", token, manufacturer_id=mfr_id,
                authority=f"printed front matter, {vendor} datasheet "
                          f"page {page_no}")
            identity.add_relationship(
                identity_con, family_id, child_id, "contains",
                authority=f"front matter line: {line[:120]}",
                source_sha256=sha,
                source_locator={"page": page_no, "quote": line},
                norm_rule=NORM_RULE, confidence=CONFIDENCE)
            proposed.append((family_id, token, page_no, line))
            report["relationships_proposed"] += 1
            report["by_family"][f"{vendor}:{token}"] += 1
            packet = packets.setdefault(family_id, {
                "family_id": family_id, "label": token,
                "manufacturer_id": mfr_id, "status": "proposed",
                "norm_rule": NORM_RULE,
                "requires": "owner approval to promote to verified",
                "members": []})
            packet["members"].append(
                {"opn": opn, "source_sha256": sha, "page": page_no,
                 "quote": line})
        if len({p[0] for p in proposed}) > 1 and not dry_run:
            ids = sorted({p[0] for p in proposed})
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    identity.mark_conflict(identity_con, ids[i], ids[j],
                                           child_id, "contains")
            report["conflicts"] += 1

    distinct_opns = len(known_opns)
    covered = sum(1 for r in identity_con.execute(
        "SELECT DISTINCT child_id FROM relationships WHERE"
        " rel_type='contains' AND conflict_status IS NULL")) \
        if not dry_run else 0
    report["by_family"] = dict(sorted(report["by_family"].items()))
    report["by_vendor_scanned"] = dict(
        sorted(report["by_vendor_scanned"].items()))
    report["coverage"] = {
        "denominator_distinct_opns": distinct_opns,
        "opns_with_proposed_family": covered,
        "family_coverage": round(covered / max(1, distinct_opns), 4),
        "note": "proposed (printed-evidence) coverage; verified coverage "
                "is 0 until owner admission — never silently promoted",
    }
    if not dry_run and packets:
        PACKET_DIR.mkdir(parents=True, exist_ok=True)
        out = PACKET_DIR / "family-packets-v1.jsonl"
        with out.open("w") as handle:
            for family_id in sorted(packets):
                handle.write(json.dumps(packets[family_id],
                                        sort_keys=True) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity-db", default=identity.DEFAULT_DB)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    icon = identity.connect(args.identity_db)
    pipe = sqlite3.connect(f"file:{PIPELINE}?mode=ro", uri=True)
    pipe.row_factory = sqlite3.Row
    cat = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True)
    cat.row_factory = sqlite3.Row
    try:
        report = backfill(icon, pipe, cat, dry_run=args.dry_run)
    finally:
        pipe.close()
        cat.close()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
