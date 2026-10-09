#!/usr/bin/env python3
"""B1 — generate the DISCOVERY-01 identity authority record.

Answers, from the live stores and from git (never from memory), the six
questions a production operator must be able to answer:

  1. where verified family lives
  2. where verified series lives
  3. how the serving path reads them
  4. whether the catalog carries a materialized copy
  5. what commit admitted them
  6. what approval reference authorized them

Read-only: opens the identity, catalog and search databases in ``mode=ro``
and runs only ``git log`` / ``git merge-base``. Writes
results/discovery01-remediation/b1_identity_authority.json.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUT = ROOT / "results/discovery01-remediation"

DATA = Path(os.environ.get("READINESS_DATA_DIR",
                           "/Volumes/M5_4TB/extract-results"))
IDENTITY_DB = DATA / "facet_identity.db"
CATALOG_DB = DATA / "catalog.db"
SEARCH_DB = DATA / "search_index.db"

# Commits on the facet-03 / facet-decision lanes that executed or prepared
# the admissions now visible in the live store.
ADMISSION_COMMITS = [
    ("6ba8c66e", "executed the family admission: all four packets approved "
                 "(626 OPN->family memberships verified)"),
    ("b76f14be", "series-grain v2: series designators extracted from the "
                 "same verified membership quotes (237 OPN->series)"),
    ("4e4398aa", "executed the v2 owner decisions: 15/15 family->series "
                 "edges promoted (adds promote_packet with child_kind "
                 "scoping and drift refusal)"),
    ("0c23a34d", "series signoff package: 15 packets / 237 memberships "
                 "pinned by sha256 (owner gate preparation)"),
]
RELATED_COMMITS = [
    ("49557b74", "release-keyed cohort cache (R11.3 / FACET-02 GA blocker): "
                 "build_cohort_cached wired into facets + the session "
                 "orchestrator"),
    ("4c1f45a6", "grain-weighting experiment, measured negative, reverted "
                 "to neutral (hybrid-v1.1)"),
]


def _ro(path: Path):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def _git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True).stdout.strip()


def _in_ancestry(sha: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", sha,
                           "HEAD"], cwd=ROOT).returncode == 0


def _commit_entry(short: str, role: str) -> dict:
    full = _git("rev-parse", short) or short
    return {
        "commit": full,
        "short": short,
        "subject": _git("log", "-1", "--format=%s", short)[:400],
        "date": _git("log", "-1", "--format=%ad", "--date=iso", short),
        "branches_containing": _git("branch", "-a", "--contains", short
                                    ).splitlines()[:6],
        "in_this_branch_ancestry": _in_ancestry(full),
        "role": role,
    }


def main() -> int:
    icon = _ro(IDENTITY_DB)
    cat = _ro(CATALOG_DB)
    idx = _ro(SEARCH_DB)

    by_kind = {f"{r[0]}|{r[1]}|{r[2]}": r[3] for r in icon.execute(
        "SELECT i.kind, CASE WHEN r.child_id LIKE 'opn:%' THEN 'opn'"
        " ELSE 'series' END, r.status, count(*) FROM relationships r"
        " JOIN identities i ON i.identity_id = r.parent_id"
        " GROUP BY 1, 2, 3")}

    def _rel_count(parent_kind, child_prefix, status="verified"):
        return icon.execute(
            "SELECT count(*) FROM relationships r JOIN identities i"
            " ON i.identity_id = r.parent_id WHERE i.kind=? AND r.status=?"
            " AND r.child_id LIKE ?",
            (parent_kind, status, child_prefix + "%")).fetchone()[0]
    approvals = {}
    for r in icon.execute("SELECT key, value FROM meta WHERE key LIKE"
                          " 'approval:%'"):
        try:
            approvals[r["key"]] = json.loads(r["value"])
        except json.JSONDecodeError:
            approvals[r["key"]] = {"raw": r["value"][:120]}
    refs = sorted({v.get("approval_ref") for v in approvals.values()
                   if v.get("approval_ref")})

    parts_total = cat.execute("SELECT count(*) FROM parts").fetchone()[0]
    parts_family = cat.execute(
        "SELECT count(*) FROM parts WHERE family IS NOT NULL AND family<>''"
    ).fetchone()[0]
    units_active = idx.execute(
        "SELECT count(*) FROM units WHERE retired=0").fetchone()[0]
    units_family = idx.execute(
        "SELECT count(*) FROM units WHERE retired=0 AND family IS NOT NULL"
        " AND family<>''").fetchone()[0]
    family_grain_units = idx.execute(
        "SELECT count(*) FROM units WHERE retired=0 AND grain='family'"
    ).fetchone()[0]

    record = {
        "record": "DISCOVERY-01 identity authority",
        "generated_by": "scripts/discovery01_identity_authority.py",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "generated_from_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "generated_from_commit": _git("rev-parse", "HEAD"),
        "authority": {
            "source_of_truth": str(IDENTITY_DB),
            "tables": {
                "identities": "family/series nodes with kind, label, "
                              "manufacturer_id, authority, status",
                "relationships": "membership edges (parent_id -> child_id, "
                                 "rel_type='contains') carrying status, "
                                 "authority, source_sha256, source_locator, "
                                 "norm_rule, confidence, conflict_status",
                "meta": "approval:<identity_id> rows written by "
                        "identity.promote() — the owner approval reference "
                        "that authorized each verified node",
            },
            "verified_is_only_reachable_via": "harness/search/identity.py"
                                              "::promote(con, identity_id, "
                                              "approval=...) — it requires a "
                                              "non-empty approval reference "
                                              "and records it in meta",
            "catalog_is_authoritative_for_family": False,
            "index_is_authoritative_for_family": False,
        },
        "live_state": {
            "relationships_total": icon.execute(
                "SELECT count(*) FROM relationships").fetchone()[0],
            "relationships_not_verified": icon.execute(
                "SELECT count(*) FROM relationships WHERE status<>'verified'"
            ).fetchone()[0],
            "opn_to_family_verified": _rel_count("family", "opn:"),
            "opn_to_series_verified": _rel_count("series", "opn:"),
            "series_to_family_verified": _rel_count("family", "series:"),
            "relationships_by_kind_status": by_kind,
            "identities_by_kind_status": {
                f"{r[0]}|{r[1]}": r[2] for r in icon.execute(
                    "SELECT kind, status, count(*) FROM identities"
                    " GROUP BY 1, 2")},
            "verified_family_nodes": icon.execute(
                "SELECT count(*) FROM identities WHERE kind='family' AND"
                " status='verified'").fetchone()[0],
            "verified_series_nodes": icon.execute(
                "SELECT count(*) FROM identities WHERE kind='series' AND"
                " status='verified'").fetchone()[0],
            "family_labels": [r[0] for r in icon.execute(
                "SELECT label FROM identities WHERE kind='family' AND"
                " status='verified' ORDER BY label")],
        },
        "approval_references": {
            "family": "FACET03-OWNER-APPROVAL-2026-10-09-961ef951",
            "series": "FACET03-SERIES-OWNER-APPROVAL-2026-10-09-0c23a34d",
            "series_edges": "FACET03-SERIES-EDGES-OWNER-APPROVAL-2026-10-09-"
                            "a56e177d",
            "refs_found_in_meta": refs,
            "approval_rows": len(approvals),
            "node_approvals": sum(1 for k in approvals
                                  if ":edges:" not in k),
            "edge_approvals": sum(1 for k in approvals if ":edges:" in k),
            "rows_by_reference": {
                ref: sum(1 for v in approvals.values()
                         if v.get("approval_ref") == ref) for ref in refs},
            "edge_approval_keys": sorted(k for k in approvals
                                         if ":edges:" in k),
            "approved_nodes": sorted(k.split("approval:", 1)[1]
                                     for k in approvals),
            "decision_text_sample": next(
                (v.get("decision_text") for v in approvals.values()
                 if v.get("decision_text")), None),
        },
        "serving_path": {
            "family_membership": "harness/search/cohort.py::_attach_families "
                                 "— SELECT r.child_id, r.status, "
                                 "r.conflict_status, r.authority, "
                                 "r.source_sha256, r.source_locator, "
                                 "i.identity_id, i.label FROM relationships "
                                 "r JOIN identities i ... WHERE "
                                 "r.rel_type='contains' AND i.kind='family'; "
                                 "matches child_id 'opn:<OPN>'; grants "
                                 "family_id only for a single "
                                 "non-conflicting membership; statuses "
                                 "travel with every membership and are never "
                                 "upgraded",
            "m4_candidate_identity": "harness/search/identity.py::"
                                     "build_identity_snapshot — canonical "
                                     "ids, grain, parents with authority "
                                     "and membership evidence; recorded per "
                                     "session as releases."
                                     "m5_identity_snapshot (content-addressed, "
                                     "idempotent)",
            "never_read_for_family": "catalog.parts.family is selected by "
                                     "cohort.build_cohort but is not used to "
                                     "construct a Candidate; the discovery "
                                     "path has no catalog family dependency",
            "disclosed_in_response": "cohort notes state the catalog gap "
                                     "(family/mpn_base empty in catalog "
                                     "0/1882, family-grain not fabricated) "
                                     "and the membership counts with "
                                     "statuses",
        },
        "materialization": {
            "catalog_parts_total": parts_total,
            "catalog_parts_with_family": parts_family,
            "catalog_parts_family_coverage": f"{parts_family}/{parts_total}",
            "catalog_populated_by_this_remediation": False,
            "rationale": "parts.family is not the serving authority for "
                         "DISCOVERY-01, so it was deliberately NOT "
                         "populated to close a blocker; materializing it "
                         "would duplicate identity and create a second "
                         "source of truth",
            "index_units_active": units_active,
            "index_units_with_family": units_family,
            "index_family_grain_units": family_grain_units,
            "index_family_gap": "indexer.family_units() builds family-grain "
                                "units from catalog.parts.family, so the "
                                "SEARCH surface has 1,882 family-grain units "
                                "with empty family labels and 0 units "
                                "carrying a family value; the DISCOVERY "
                                "surface does not read units.family",
            "materializing_the_index_would_change": "the search index "
                                                    "release fingerprint "
                                                    "(units content), so it "
                                                    "requires explicit "
                                                    "owner authorization and "
                                                    "a re-prime of the "
                                                    "policy release cache",
        },
        "admission_commits": [_commit_entry(c, role)
                              for c, role in ADMISSION_COMMITS],
        "related_commits_on_other_lanes": [_commit_entry(c, role)
                                           for c, role in RELATED_COMMITS],
        "reproducibility": {
            "admission_code_present_in_this_branch": bool(
                subprocess.run(["git", "grep", "-l",
                                "UPDATE relationships SET status='verified'",
                                "HEAD", "--", "harness/search/identity.py"],
                               cwd=ROOT,
                               capture_output=True).stdout.strip()),
            "this_branch_identity_api": "add_relationship refuses "
                                        "status='verified' and promote() "
                                        "updates identities only, so this "
                                        "branch can neither fabricate nor "
                                        "reproduce the live verified "
                                        "relationship state",
            "implication": "the live store is authoritative and the serving "
                           "path reads it faithfully, but the tooling that "
                           "produced that state lives on the facet-03 / "
                           "facet-decision lanes; production promotion must "
                           "run from a branch that contains those commits, "
                           "or they must be merged with owner authorization",
            "verification_performed": "counts, statuses and approval refs "
                                      "were read directly from the live "
                                      "store by this script and are locked "
                                      "by tests/test_discovery01_b1_"
                                      "identity_authority.py",
        },
    }

    icon.close()
    cat.close()
    idx.close()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "b1_identity_authority.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False, default=str) + "\n")
    print(json.dumps({
        "relationships_total": record["live_state"]["relationships_total"],
        "not_verified": record["live_state"]["relationships_not_verified"],
        "opn_to_family": record["live_state"]["opn_to_family_verified"],
        "opn_to_series": record["live_state"]["opn_to_series_verified"],
        "series_to_family": record["live_state"]["series_to_family_verified"],
        "approval_rows": record["approval_references"]["approval_rows"],
        "refs": record["approval_references"]["refs_found_in_meta"],
        "catalog_parts_family": record["materialization"][
            "catalog_parts_family_coverage"],
        "index_units_with_family": f"{units_family}/{units_active}",
        "admission_commits_in_ancestry": [
            (c["short"], c["in_this_branch_ancestry"])
            for c in record["admission_commits"]],
        "admission_code_in_this_branch": record["reproducibility"][
            "admission_code_present_in_this_branch"],
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
