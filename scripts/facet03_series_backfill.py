"""Series-grain backfill v2 (autonomous roadmap item 1, 2026-10-09).

Extends the admitted family dimension one grain down using the SAME
evidence standard: the series designator must be printed inside an
already-verified membership quote (byte-verified sha + page + verbatim
line). No new sources, no name parsing of OPNs, no invention.

Case-sensitive all-caps designator patterns on purpose: "CoolMOS C7",
"CoolMOS CFD2", "CoolSiC G1", "OptiMOS 2" are printed designators; prose
words after the family token ("CoolMOS is a revolutionary...", "CoolMOS
Power Transistor") can never match an all-caps/numeric capture.

Hierarchy: family contains series; series contains OPN. Both written
status=proposed with their own source locators; packets await the owner
signature exactly like the family packets (promote_packet contract).
Zero or ambiguous designators => series stays UNKNOWN for that member.
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

from harness.search import identity  # noqa: E402

NORM_RULE = "series_designator_v1"
CONFIDENCE = 0.85

# Case-SENSITIVE: the family token as printed, optionally followed by
# printed product-class nouns ("CoolSiC MOSFET G1", "CoolMOS Power C7"),
# then an all-caps or numeric designator. Prose words (mixed/lower case)
# can never match: the capture is [A-Z0-9]-only and the interstitial set
# is a closed printed vocabulary.
_INTERSTITIAL = r"(?:\s+(?:MOSFET|Diode|Module|Automotive|Trench|Power|" \
                r"Switch|eTrench|Discrete|Transistor))*"
SERIES_EXTRACT = {
    "CoolMOS": re.compile(r"CoolMOS\s*[\u2122\u00aa]?" + _INTERSTITIAL +
                          r"\s+([A-Z][A-Z0-9]{1,5})\b"),
    "CoolSiC": re.compile(r"CoolSiC\s*[\u2122\u00aa]?" + _INTERSTITIAL +
                          r"\s+(G\d[A-Z0-9]{0,3}|[A-Z][A-Z0-9]{1,5})\b"),
    "OptiMOS": re.compile(r"OptiMOS\s*[\u2122\u00aa]?" + _INTERSTITIAL +
                          r"\s*[-\s]?(\d)\b"),
    "StrongIRFET": re.compile(r"StrongIRFET\s*[\u2122]?" + _INTERSTITIAL +
                              r"\s*[-\s]?(\d)\b"),
}
# tokens that are all-caps but NOT series designators (printed acronyms
# that can follow the family name)
_DESIGNATOR_STOP = {"MOSFET", "NPN", "PNP", "SIC", "GAN", "FET", "SMT",
                    "TOLL", "PG", "TO", "AEC"}


def extract_series(family_label: str, quote: str) -> tuple[str, str] | None:
    """(series_label, designator) printed in the quote, or None."""
    pattern = SERIES_EXTRACT.get(family_label)
    if not pattern or not quote:
        return None
    found = set()
    for m in pattern.finditer(quote):
        desig = m.group(1)
        if desig.upper() in _DESIGNATOR_STOP or len(desig) < 1:
            continue
        found.add(desig)
    if len(found) != 1:            # zero OR ambiguous => unknown, never guessed
        return None
    desig = found.pop()
    if family_label in ("OptiMOS", "StrongIRFET"):
        return f"{family_label} {desig}", desig
    return f"{family_label} {desig}", desig


def backfill_series(identity_con: sqlite3.Connection, *,
                    dry_run: bool = False) -> dict:
    rows = identity_con.execute(
        "SELECT r.parent_id, r.child_id, r.source_sha256, r.source_locator,"
        " i.label, i.manufacturer_id, i.status AS family_status"
        " FROM relationships r JOIN identities i ON i.identity_id=r.parent_id"
        " WHERE r.rel_type='contains' AND i.kind='family'"
        " AND r.status='verified' ORDER BY r.child_id").fetchall()
    report = {"verified_memberships_scanned": len(rows),
              "series_found": 0, "series_unknown": 0,
              "series_ambiguous_skipped": 0,
              "by_series": defaultdict(int), "proposed": 0}
    family_series_evidence: dict[tuple, dict] = {}
    for r in rows:
        loc = json.loads(r["source_locator"])
        quote = loc.get("quote") or ""
        result = extract_series(r["label"], quote)
        if result is None:
            # distinguish ambiguous (multiple designators) from absent
            pattern = SERIES_EXTRACT.get(r["label"])
            if pattern and len({m.group(1) for m in pattern.finditer(quote)
                                if m.group(1).upper() not in
                                _DESIGNATOR_STOP}) > 1:
                report["series_ambiguous_skipped"] += 1
            else:
                report["series_unknown"] += 1
            continue
        series_label, desig = result
        report["series_found"] += 1
        report["by_series"][f"{r['label']} {desig}"] += 1
        if dry_run:
            continue
        mfr = r["manufacturer_id"]
        series_id = identity.identity_id_for("series", series_label, mfr)
        fam_series_key = (r["parent_id"], series_id)
        if fam_series_key not in family_series_evidence:
            family_series_evidence[fam_series_key] = {
                "sha": r["source_sha256"], "loc": loc, "label": series_label}
            identity.ensure_identity(
                identity_con, "series", series_label, manufacturer_id=mfr,
                authority=f"printed designator in verified {r['label']} "
                          f"membership quote (p.{loc.get('page')})")
            identity.add_relationship(
                identity_con, r["parent_id"], series_id, "contains",
                authority=f"series designator printed: {quote[:120]}",
                source_sha256=r["source_sha256"], source_locator=loc,
                norm_rule=NORM_RULE, confidence=CONFIDENCE)
            report["proposed"] += 1
        identity.add_relationship(
            identity_con, series_id, r["child_id"], "contains",
            authority=f"designator in membership quote: {quote[:120]}",
            source_sha256=r["source_sha256"], source_locator=loc,
            norm_rule=NORM_RULE, confidence=CONFIDENCE)
        report["proposed"] += 1
    report["by_series"] = dict(sorted(report["by_series"].items(),
                                      key=lambda kv: -kv[1]))
    report["coverage"] = {
        "denominator_verified_memberships": report[
            "verified_memberships_scanned"],
        "memberships_with_series": report["series_found"],
        "series_coverage": round(
            report["series_found"] /
            max(1, report["verified_memberships_scanned"]), 4),
        "note": "proposed; owner packets required before any promotion"}
    return report


def write_packets(identity_con: sqlite3.Connection, out_dir: Path) -> dict:
    """Series admission packets, same schema/contract as family packets."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = identity_con.execute(
        "SELECT r.parent_id, r.child_id, r.source_sha256, r.source_locator,"
        " i.label, i.manufacturer_id FROM relationships r JOIN identities i"
        " ON i.identity_id=r.parent_id WHERE r.rel_type='contains' AND"
        " i.kind='series' AND r.status='proposed' ORDER BY i.label,"
        " r.child_id").fetchall()
    packets: dict[str, dict] = {}
    for r in rows:
        loc = json.loads(r["source_locator"])
        packet = packets.setdefault(r["parent_id"], {
            "schema": "harness.facet03-admission-packet.v1",
            "family": {"identity_id": r["parent_id"], "label": r["label"],
                       "manufacturer_id": r["manufacturer_id"],
                       "identity_authority": "printed series designator in "
                                             "verified membership quote"},
            "decision_requested": "approve or reject per member; promotion "
                                  "via promote_packet only",
            "status_of_all_memberships": "proposed",
            "members": [], "exceptions": [], "exception_count": 0})
        packet["members"].append({
            "parent_id": r["parent_id"], "child_id": r["child_id"],
            "opn": r["child_id"].split(":", 1)[1], "status": "proposed",
            "norm_rule": NORM_RULE, "confidence": CONFIDENCE,
            "source_sha256": r["source_sha256"], "page": loc.get("page"),
            "quoted_family_identifier": loc.get("quote")})
        if r["label"].split()[-1] not in (loc.get("quote") or ""):
            packet["exceptions"].append(
                {"child_id": r["child_id"],
                 "type": "designator_not_in_quote"})
            packet["exception_count"] += 1
    written = {}
    for sid, packet in sorted(packets.items()):
        packet["member_count"] = len(packet["members"])
        name = "series-" + sid.split(":")[-1] + "-review-v1.json"
        (out_dir / name).write_text(
            json.dumps(packet, indent=2, sort_keys=True) + "\n")
        written[sid] = {"members": packet["member_count"],
                        "exceptions": packet["exception_count"],
                        "file": name}
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity-db", default=identity.DEFAULT_DB)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--packets", action="store_true")
    args = parser.parse_args()
    icon = identity.connect(args.identity_db)
    report = backfill_series(icon, dry_run=args.dry_run)
    if args.packets and not args.dry_run:
        report["packets"] = write_packets(
            icon, Path("/Volumes/M5_4TB/extract-results/facet03-packets"
                       "/series"))
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
