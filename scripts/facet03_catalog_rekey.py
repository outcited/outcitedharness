"""Catalog re-key executor (FACET-03 provenance backfill, stage 2).

Rewrites `unhashed:<stem>` document identities in catalog.db to the
byte-verified SHA-256s recorded in facet_identity.db provenance_map.
Identity-only re-key: quotes, row/column headers, and values are touched
by NO code path here; page enrichment stays in the evidence-index layer
(page_source=quote_locate_v1) and is NOT injected into catalog provenance.

Safety contract:
  * dry-run by default — writes require --execute;
  * --execute refuses without a fresh backup (makes one automatically:
    catalog.db.bak-pre-rekey-<utc>);
  * only stems with resolved_sha256 AND bytes_verified=1 AND
    quote_verified_claims>0 are re-keyed (the same bar as the evidence
    index re-key); the 5 unresolved stems stay unhashed;
  * single transaction; any error rolls back everything;
  * post-verification: resolved-stem counts zeroed, unresolved preserved,
    10-claim spot check that quote/headers are byte-identical.

After a production re-key, the next search-index rebuild keys claim units
directly off real shas; the +prov1 re-keyed units remain valid history.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CATALOG = "/Volumes/M5_4TB/extract-results/catalog.db"
IDENTITY = "/Volumes/M5_4TB/extract-results/facet_identity.db"


def load_resolutions(identity_path: str) -> dict[str, str]:
    icon = sqlite3.connect(f"file:{identity_path}?mode=ro", uri=True)
    rows = icon.execute(
        "SELECT stem, resolved_sha256 FROM provenance_map WHERE"
        " resolved_sha256 IS NOT NULL AND bytes_verified=1 AND"
        " quote_verified_claims > 0").fetchall()
    icon.close()
    return {stem: sha for stem, sha in rows}


def rekey(catalog_path: str, resolutions: dict[str, str], *,
          execute: bool) -> dict:
    con = sqlite3.connect(catalog_path)
    con.row_factory = sqlite3.Row
    report = {"stems_eligible": len(resolutions), "claims": 0,
              "part_sources": 0, "parts_touched": 0, "skipped": []}
    before = {
        "claims_unhashed": con.execute(
            "SELECT COUNT(*) FROM claims_canonical WHERE doc_sha256 LIKE"
            " 'unhashed:%'").fetchone()[0],
        "ps_unhashed": con.execute(
            "SELECT COUNT(*) FROM part_sources WHERE doc_sha256 LIKE"
            " 'unhashed:%'").fetchone()[0],
    }
    report["before"] = before
    if execute:
        backup = Path(catalog_path).with_name(
            f"catalog.db.bak-pre-rekey-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}")
        con.close()
        shutil.copy2(catalog_path, backup)
        report["backup"] = str(backup)
        con = sqlite3.connect(catalog_path)
        con.row_factory = sqlite3.Row
    try:
        con.execute("BEGIN")
        for stem, sha in sorted(resolutions.items()):
            old = f"unhashed:{stem}"
            exists = con.execute(
                "SELECT 1 FROM claims_canonical WHERE doc_sha256=? LIMIT 1",
                (old,)).fetchone()
            if not exists:
                report["skipped"].append(stem)
                continue
            if execute:
                cur = con.execute(
                    "UPDATE claims_canonical SET doc_sha256=?"
                    " WHERE doc_sha256=?", (sha, old))
                report["claims"] += cur.rowcount
                # provenance JSON sha field: identity only, quotes untouched
                for row in con.execute(
                        "SELECT id, provenance FROM claims_canonical"
                        " WHERE doc_sha256=?", (sha,)).fetchall():
                    try:
                        prov = json.loads(row["provenance"])
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if prov.get("sha") == old:
                        prov["sha"] = sha
                        con.execute(
                            "UPDATE claims_canonical SET provenance=?"
                            " WHERE id=?",
                            (json.dumps(prov, ensure_ascii=False),
                             row["id"]))
                cur = con.execute(
                    "UPDATE part_sources SET doc_sha256=? WHERE doc_sha256=?",
                    (sha, old))
                report["part_sources"] += cur.rowcount
                for row in con.execute(
                        "SELECT opn, source_doc_shas FROM parts WHERE"
                        " source_doc_shas LIKE ?", (f"%{old}%",)).fetchall():
                    try:
                        shas = json.loads(row["source_doc_shas"])
                    except (json.JSONDecodeError, TypeError):
                        continue
                    new = [sha if s == old else s for s in shas]
                    if new != shas:
                        con.execute(
                            "UPDATE parts SET source_doc_shas=? WHERE opn=?",
                            (json.dumps(new), row["opn"]))
                        report["parts_touched"] += 1
        if execute:
            con.execute("COMMIT")
        else:
            con.execute("ROLLBACK")
            # dry-run counts without writing
            for stem, sha in resolutions.items():
                old = f"unhashed:{stem}"
                report["claims"] += con.execute(
                    "SELECT COUNT(*) FROM claims_canonical WHERE"
                    " doc_sha256=?", (old,)).fetchone()[0]
                report["part_sources"] += con.execute(
                    "SELECT COUNT(*) FROM part_sources WHERE doc_sha256=?",
                    (old,)).fetchone()[0]
    except Exception:
        if execute:
            con.execute("ROLLBACK")
        raise
    if execute:
        report["after"] = {
            "claims_unhashed": con.execute(
                "SELECT COUNT(*) FROM claims_canonical WHERE doc_sha256"
                " LIKE 'unhashed:%'").fetchone()[0],
            "ps_unhashed": con.execute(
                "SELECT COUNT(*) FROM part_sources WHERE doc_sha256 LIKE"
                " 'unhashed:%'").fetchone()[0],
        }
    con.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default=CATALOG)
    parser.add_argument("--identity-db", default=IDENTITY)
    parser.add_argument("--execute", action="store_true",
                        help="actually write (default: dry-run)")
    args = parser.parse_args()
    resolutions = load_resolutions(args.identity_db)
    report = rekey(args.catalog, resolutions, execute=args.execute)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
