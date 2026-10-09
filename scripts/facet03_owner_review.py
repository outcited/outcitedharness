"""Owner admission review harness (FACET-03 directive, review stage).

Independent re-verification of sampled family memberships FROM SOURCE:
hash the actual PDF bytes, open the document, confirm the family token on
the recorded page, and test that the source supports the PARENT-CHILD
claim (a family name appearing in a document does not by itself prove the
claimed OPN belongs to that family — the part identity must co-occur with
the family attribution).

Read-only: no promote(), no catalog writes, no identity-db writes, no
changes to unresolved provenance/quote states.

Sampling (reproducible, risk-stratified, deterministic):
  A  the previously-affected StrongIRFET truncation records (recomputed
     from the old capture rule — not remembered)
  B  duplicate-document cases (members sharing one source sha)
  C  series-ambiguity cases (family token printed with a series designator
     that the v1 vocabulary does not capture: CoolMOS C7, CoolSiC G1, ...)
  D  representative ordinary members (fixed stride over sorted child ids)

Verdicts:
  supported    sha matches + token on recorded page + part identity
               co-occurs with the family attribution in front matter
  ambiguous    token present but attribution context is comparative, or
               part identity not co-located with the token
  unsupported  sha mismatch, token absent, or attribution to another part
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import pymupdf  # noqa: E402

from facet03_resolve_provenance import (  # noqa: E402
    PAIRS_DIRS, norm_text, substrate_pages)

REVIEW_DIR = Path("/Volumes/M5_4TB/extract-results/facet03-packets/review")
VAULT_CAS = Path("/Volumes/M5_4TB/vault/cas")
IDENTITY_DB = "/Volumes/M5_4TB/extract-results/facet_identity.db"
PIPELINE = "/Volumes/M5_4TB/extract-results/pipeline.db"

# series designators printed after family tokens (v1 vocabulary captures
# the family only; these mark the series-ambiguity stratum)
SERIES_PATTERNS = {
    "CoolMOS": re.compile(r"CoolMOS\s*(?:\u2122|\u00aa)?\s*([A-Z]\d?\w*)", re.I),
    "CoolSiC": re.compile(r"CoolSiC\s*(?:\u2122|\u00aa)?\s*(G\d\w*)", re.I),
    "OptiMOS": re.compile(r"OptiMOS\s*(?:\u2122|\u00aa)?\s*(\d\w*)", re.I),
    "StrongIRFET": re.compile(r"StrongIRFET\s*(?:\u2122)?\s*(\d\w*)", re.I),
}
COMPARATIVE = re.compile(
    r"\b(unlike|compared (?:to|with)|versus|vs\.?|similar to|successor"
    r"(?:\s+to)?|alternative(?:\s+to)?|replaces)\b", re.I)
# Comparative markers only disqualify when they PRECEDE the family token
# within a short window — i.e. the token is the object of a comparison
# ("unlike CoolMOS..."). Datasheet figure titles ("... vs. Gate Voltage")
# routinely contain vs./versus after the token and must not flag.
_COMPARATIVE_PRECEDE_CHARS = 40


def sha256_file(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def locate_pdf(stem: str, sha: str) -> Path | None:
    for pairs_path in PAIRS_DIRS:
        cand = pairs_path.parent / "pdf" / f"{stem}.pdf"
        if cand.exists():
            return cand
    cas = VAULT_CAS / sha[:2] / f"{sha}.pdf"
    return cas if cas.exists() else None


def packet_hashes() -> dict:
    out = {}
    for path in sorted(REVIEW_DIR.glob("family-*-review-v1.json")):
        out[path.name] = {
            "sha256": sha256_file(path),
            "members": len(json.loads(path.read_text())["members"])}
    return out


def old_style_affected(icon, member: dict) -> bool:
    """Recompute the pre-fix capture: first-300-chars of the normalized hit
    page, token beyond char 200 => previously affected."""
    pipe = sqlite3.connect(f"file:{PIPELINE}?mode=ro", uri=True)
    pipe.row_factory = sqlite3.Row
    try:
        pages = substrate_pages(pipe, member["source_sha256"])
    finally:
        pipe.close()
    label = member["parent_id"].rsplit(":", 1)[-1]
    for _page_no, text in pages[:2]:
        m = re.search(rf"\b{re.escape(label)}\b", text, re.I)
        if m:
            old_quote = text[:300]
            return label.lower() not in old_quote[:200].lower()
    return False


def build_strata(icon) -> dict[str, dict[str, list[dict]]]:
    fams = {}
    rows = icon.execute(
        "SELECT r.parent_id, r.child_id, r.source_sha256, r.source_locator,"
        " i.label FROM relationships r JOIN identities i"
        " ON i.identity_id=r.parent_id WHERE r.rel_type='contains'"
        " AND i.kind='family' ORDER BY i.label, r.child_id").fetchall()
    by_sha: dict[str, list] = {}
    for r in rows:
        by_sha.setdefault(r["source_sha256"], []).append(r)
    per_family: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        per_family.setdefault(r["label"], []).append(r)

    for label, members in per_family.items():
        strata: dict[str, list[dict]] = {"A_affected": [], "B_duplicate_doc": [],
                                         "C_series_ambiguous": [],
                                         "D_ordinary": []}
        dup_shas = {s for s, group in by_sha.items() if len(group) > 1}
        for r in members:
            loc = json.loads(r["source_locator"])
            entry = {"parent_id": r["parent_id"], "child_id": r["child_id"],
                     "opn": r["child_id"].split(":", 1)[1],
                     "source_sha256": r["source_sha256"],
                     "page": loc.get("page"), "quote": loc.get("quote")}
            if r["source_sha256"] in dup_shas:
                strata["B_duplicate_doc"].append(entry)
            else:
                m = SERIES_PATTERNS.get(label)
                if m and m.search(entry["quote"] or ""):
                    strata["C_series_ambiguous"].append(entry)
        # A: recomputed truncation-affected (StrongIRFET historically)
        if label == "StrongIRFET":
            for entry in strata["B_duplicate_doc"] + \
                    strata["C_series_ambiguous"] + \
                    [{"parent_id": x["parent_id"], "child_id": x["child_id"],
                      "opn": x["child_id"].split(":", 1)[1],
                      "source_sha256": x["source_sha256"],
                      "page": json.loads(x["source_locator"]).get("page"),
                      "quote": json.loads(x["source_locator"]).get("quote")}
                     for x in members]:
                if old_style_affected(icon, entry):
                    if entry not in strata["A_affected"]:
                        strata["A_affected"].append(entry)
        # D: fixed stride over sorted ids, excluding already-stratified
        chosen = {e["child_id"] for s in strata.values() for e in s}
        ordinary = [r for r in members if r["child_id"] not in chosen]
        stride = max(1, len(ordinary) // 7)
        for i in range(0, len(ordinary), stride):
            r = ordinary[i]
            loc = json.loads(r["source_locator"])
            strata["D_ordinary"].append({
                "parent_id": r["parent_id"], "child_id": r["child_id"],
                "opn": r["child_id"].split(":", 1)[1],
                "source_sha256": r["source_sha256"],
                "page": loc.get("page"), "quote": loc.get("quote")})
            if len(strata["D_ordinary"]) >= 7:
                break
        # caps: B and C sampled deterministically if huge
        for key in ("B_duplicate_doc", "C_series_ambiguous"):
            strata[key] = strata[key][:8]
        fams[label] = strata
    return fams


def verify_member(member: dict) -> dict:
    """Independent source verification for one sampled member."""
    label = member["parent_id"].rsplit(":", 1)[-1]
    opn = member["opn"]
    result = {"child_id": member["child_id"], "family": label,
              "page_recorded": member["page"], "checks": {}}
    stem_guess = None
    pdf = None
    # find the pdf: pairs dirs by *-{OPN}.pdf, else cas by sha
    for pairs_path in PAIRS_DIRS:
        cand = pairs_path.parent / "pdf" / \
            f"{pairs_path.parent.name.rsplit('-', 1)[0]}-{opn}.pdf"
        if cand.exists():
            pdf = cand
            break
    if pdf is None:
        pdf = locate_pdf("", member["source_sha256"]) or \
            VAULT_CAS / member["source_sha256"][:2] / \
            f"{member['source_sha256']}.pdf"
    actual_sha = sha256_file(pdf) if pdf and pdf.exists() else None
    result["checks"]["sha256_matches_record"] = \
        (actual_sha == member["source_sha256"])
    if not result["checks"]["sha256_matches_record"]:
        result["verdict"] = "unsupported"
        result["reason"] = f"source bytes hash {str(actual_sha)[:12]} != " \
                           f"recorded {member['source_sha256'][:12]}"
        return result
    try:
        doc = pymupdf.open(str(pdf))
    except Exception as e:  # noqa: BLE001
        result["verdict"] = "unsupported"
        result["reason"] = f"pdf open failed: {e}"
        return result
    try:
        pages_text = {}
        for pno in range(min(3, doc.page_count)):
            pages_text[pno + 1] = norm_text(doc[pno].get_text())
        recorded = member["page"]
        token_pages = [p for p, t in pages_text.items()
                       if re.search(rf"\b{re.escape(label)}\b", t, re.I)]
        result["checks"]["token_on_recorded_page"] = \
            recorded in token_pages if recorded else bool(token_pages)
        result["token_pages_found"] = token_pages
        # part identity co-occurrence: the OPN (or its stem) must be named
        # in the same front matter as the family attribution
        opn_pat = re.compile(re.escape(opn), re.I)
        opn_pages = [p for p, t in pages_text.items() if opn_pat.search(t)]
        result["checks"]["opn_named_in_front_matter"] = bool(opn_pages)
        co_occur = sorted(set(token_pages) & set(opn_pages))
        result["checks"]["family_and_opn_co_occur"] = bool(co_occur)
        # attribution context on the token page
        context = ""
        comparative = False
        if token_pages:
            t = pages_text[token_pages[0]]
            m = re.search(rf"\b{re.escape(label)}\b", t, re.I)
            if m:
                context = t[max(0, m.start() - 120):m.end() + 160]
                preceding = t[max(0, m.start() - _COMPARATIVE_PRECEDE_CHARS):
                              m.start()]
                comparative = bool(COMPARATIVE.search(preceding))
        result["context"] = context
        result["checks"]["comparative_context"] = comparative
        series = SERIES_PATTERNS.get(label)
        sm = series.search(context) if (series and context) else None
        result["series_designator_printed"] = sm.group(0) if sm else None
        if (result["checks"]["token_on_recorded_page"]
                and result["checks"]["family_and_opn_co_occur"]
                and not comparative):
            result["verdict"] = "supported"
        elif comparative or (token_pages and not co_occur):
            result["verdict"] = "ambiguous"
            result["reason"] = ("comparative mention" if comparative
                                else "family token not co-located with OPN")
        else:
            result["verdict"] = "unsupported"
            result["reason"] = "token not on recorded page" if not \
                token_pages else "part identity absent from front matter"
    finally:
        doc.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REVIEW_DIR /
                                             "owner-review-results-v1.json"))
    args = parser.parse_args()
    icon = sqlite3.connect(f"file:{IDENTITY_DB}?mode=ro", uri=True)
    icon.row_factory = sqlite3.Row
    out = {"schema": "harness.facet03-owner-review.v1",
           "packet_hashes": packet_hashes(), "families": {}}
    strata = build_strata(icon)
    for label in sorted(strata):
        family_out = {"strata": {}, "results": [], "summary": {}}
        counts = {"sampled": 0, "supported": 0, "ambiguous": 0,
                  "unsupported": 0, "series_noted": 0}
        for stratum, members in sorted(strata[label].items()):
            family_out["strata"][stratum] = [m["child_id"] for m in members]
            for member in members:
                verdict = verify_member(member)
                verdict["stratum"] = stratum
                family_out["results"].append(verdict)
                counts["sampled"] += 1
                counts[verdict["verdict"]] += 1
                if verdict.get("series_designator_printed"):
                    counts["series_noted"] += 1
        family_out["summary"] = counts
        out["families"][label] = family_out
    Path(args.out).write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "packets": {k: v["sha256"][:16] for k, v in
                    out["packet_hashes"].items()},
        "members_total": sum(v["members"] for v in
                             out["packet_hashes"].values()),
        "summaries": {f: d["summary"] for f, d in out["families"].items()},
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
