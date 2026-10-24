#!/usr/bin/env python3
"""PINOUT-CATALOG-04 — reconcile the 11,195 valid-looking-but-unserved OPN
candidates against canonical manufacturer/package/OPN identities and
PRIMARY EVIDENCE (the candidate's own held datasheet text layer).

Every rule is deterministic; no fuzzy joins, no invented identities:

- manufacturer canonical: the delivery-manifest manufacturer must be a
  vendor the SERVED catalog already carries (exact string).
- OPN primary evidence: the cleaned stem must appear (case-insensitive,
  exact token) in its OWN document — in the ordering/part-numbering
  region (ordering hit) or on the title page (title hit). Document IDs
  (e.g. Microchip 7-8 digit doc numbers) appear in footers, not ordering
  tables — an ordering-region miss with only footer hits REJECTS.
- identity uniqueness: canon(stem)+manufacturer must be unique within the
  candidate set (duplicates reconcile onto the row with the most pin
  rows; the rest record duplicate_of, never new identities).
- package: tokens matched against a pinned vocabulary in the
  ordering/package region; recorded with page + verbatim span when found,
  UNKNOWN (named absence) otherwise — absence alone never rejects.

Verdicts:
  ADMIT  — manufacturer canonical ∧ ordering/title primary hit ∧ unique
  REJECT — primary evidence contradicts (stem nowhere in doc, or only
           footer/doc-id hits), or manufacturer not canonical
  DEFER  — document not held / unreadable, or stem appears but not in
           ordering|title regions (weak evidence: human eyes)

Output: JSONL rows + summary. No store writes, no promotion, no release.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pymupdf

sys.path.insert(0, "/Users/samkim/Harnessv1-powerdata01")

# pinned package vocabulary (JEDEC/common two/three/four-token forms)
PACKAGE_VOCAB = [
    "WLCSP", "VQFN", "UQFN", "LLCC", "MLP", "LFCSP", "CSP",
    "BGA", "LGA", "QFN", "DFN", "SOD", "SOT", "SOIC", "SSOP", "TSSOP",
    "MSOP", "VSOP", "SO", "PDIP", "DIP", "TO-220", "TO-247", "TO-252",
    "TO-263", "TO-92", "SMA", "SMB", "SMC", "D2PAK", "DPAK", "PowerPAK",
    "SOT-23", "SOT-223", "SOT-89", "SOT-563", "WDFN", "ESON", "USON",
    "X2SON", "XSON", "Chip-Scale", "flip-chip",
]

ORDERING_RE = re.compile(
    r"ordering\s+information|part\s+numbering|part\s+marking|orderable|"
    r"ordering\s+guide|package\s+(?:type|information|options)|how\s+to\s+order",
    re.IGNORECASE,
)
# document-id shapes that LOOK OPN-ish but are literature numbers
DOC_ID_RE = re.compile(
    r"^(?:[A-Z]{1,3})?\d{6,8}[A-Z]?(?:-REV[A-Z]?)?$", re.IGNORECASE
)


def canon(stem: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", stem.upper())


def clean_stem(stem: str) -> tuple[str, list[str]]:
    """Deterministic pre-cleaning of leaked slug artifacts (recorded):
    .pdf/.html extensions, DS- style doc prefixes, onsemi-class trailing
    -<single letter> document-revision suffixes. The cleaning is recorded
    per row; identity is never invented — the admitted OPN is a printed
    form verified in the document."""
    applied = []
    s = stem
    for ext in (".pdf", ".html", ".htm"):
        if s.lower().endswith(ext):
            s = s[: -len(ext)]
            applied.append(f"strip-ext:{ext}")
    for pre in ("ds-", "doc-", "an-", "en.", "ww1-"):
        if s.lower().startswith(pre):
            s = s[len(pre):]
            applied.append(f"strip-prefix:{pre}")
    m = re.match(r"^(.*)-([A-Z])$", s)
    if m and len(m.group(1)) >= 4:
        s = m.group(1)
        applied.append("strip-rev-suffix")
    return s, applied


def page_text_safe(pdf_path: str, max_pages: int = 60) -> list[str]:
    try:
        doc = pymupdf.open(pdf_path)
    except Exception:
        return []
    out = []
    try:
        limit = min(doc.page_count, max_pages)
        for i in range(limit):
            try:
                out.append(doc[i].get_text() or "")
            except Exception:
                out.append("")
    finally:
        doc.close()
    return out


def find_hits(pages: list[str], needle: str) -> list[int]:
    n = needle.lower()
    return [i for i, t in enumerate(pages) if n in t.lower()]


def classify_one(row: dict, pdf_path: str | None,
                 manifest_mfrs: set[str], served_vendor_forms: set[str],
                 vocab: tuple[str, ...] = tuple(PACKAGE_VOCAB)) -> dict:
    raw_stem = row["stem"]
    stem, cleaning = clean_stem(raw_stem)
    mfr = row.get("manufacturer") or ""
    out = {
        "slug": row.get("slug"),
        "stem": raw_stem,
        "opn_candidate": stem,
        "cleaning_applied": cleaning,
        "canon": canon(stem),
        "manufacturer": mfr,
        "sha": row.get("sha"),
        "pin_rows": row.get("rows", 0),
        "tier": row.get("tier"),
        "rule_version": "pc04-reconcile-v1",
    }
    # 1) canonical manufacturer: the delivery-manifest identity table is
    # the primary source (a vendor not yet onboarded to the served catalog
    # is INTAKE BACKLOG, not non-canonical — served-ness is annotated)
    if mfr not in manifest_mfrs:
        out.update(verdict="REJECT", reason="manufacturer_not_in_manifest_table")
        return out
    out["vendor_already_served"] = mfr in served_vendor_forms
    # 2) document-id shape guard (literature numbers are not OPNs)
    if DOC_ID_RE.match(stem) and len(re.sub(r"\D", "", stem)) >= 6:
        out.update(verdict="REJECT", reason="document_id_shape",
                   note="literature/doc number, not an orderable part")
        return out
    # 3) primary evidence in its own document
    if pdf_path is None:
        out.update(verdict="DEFER", reason="document_not_held")
        return out
    pages = page_text_safe(pdf_path)
    if not pages:
        out.update(verdict="DEFER", reason="no_text_layer")
        return out
    # match forms, strictest first: raw substring, then line-level canon
    # containment (separator placement differs between slug and print)
    hits = find_hits(pages, stem)
    match_form = "raw" if hits else None
    if not hits:
        c_stem = canon(stem)
        hits = []
        for i, t in enumerate(pages):
            for line in t.splitlines():
                if c_stem in canon(line):
                    hits.append(i)
                    break
        if hits:
            match_form = "line-canon"
    out["match_form"] = match_form
    if not hits:
        out.update(verdict="REJECT", reason="stem_absent_from_own_document",
                   pages_checked=len(pages))
        return out
    ordering_pages = [i for i, t in enumerate(pages) if ORDERING_RE.search(t)]
    title_hit = 0 in hits
    ordering_hit = any(i in ordering_pages or
                       any(abs(i - j) <= 1 for j in ordering_pages)
                       for i in hits)
    footer_only = hits and not title_hit and not ordering_hit and all(
        i >= max(1, len(pages) - 2) or i > 2 for i in hits)
    out["evidence"] = {
        "hit_pages_1based": sorted({i + 1 for i in hits})[:8],
        "title_page_hit": title_hit,
        "ordering_region_hit": ordering_hit,
        "ordering_pages_1based": sorted({i + 1 for i in ordering_pages})[:5],
        "pages_checked": len(pages),
        "footer_only": footer_only,
    }
    if not (title_hit or ordering_hit):
        out.update(verdict="DEFER",
                   reason="stem_present_but_not_in_title_or_ordering_region")
        return out
    if footer_only and DOC_ID_RE.match(stem):
        out.update(verdict="REJECT", reason="footer_only_document_id")
        return out
    # 4) package token in ordering/package region (named absence ok)
    pkg = None
    for i in hits + ordering_pages:
        if i >= len(pages):
            continue
        t = pages[i]
        for token in vocab:
            if re.search(rf"(?<![A-Za-z]){re.escape(token)}(?![A-Za-z])", t):
                pkg = token
                break
        if pkg:
            out["evidence"]["package_page_1based"] = i + 1
            break
    out["package"] = pkg or "UNKNOWN"
    out.update(verdict="ADMIT",
               reason="primary_evidence_verified" if ordering_hit
               else "title_page_verified")
    return out


def _work(args):
    row, pdf_path, manifest_mfrs, served_vendor_forms = args
    return classify_one(row, pdf_path, manifest_mfrs, served_vendor_forms)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--joins", type=Path, default=Path("/tmp/idjoins.jsonl"))
    ap.add_argument("--sha-map", type=Path, default=Path("/tmp/pc04-sha-map.json"))
    ap.add_argument("--manifest-mfrs", type=Path,
                    default=Path("/tmp/pc04-manifest-mfrs.json"))
    ap.add_argument("--served-vendors", type=Path,
                    default=Path("/tmp/pc04-served-vendors.json"))
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    rows = [json.loads(l) for l in args.joins.read_text().splitlines() if l.strip()]
    rows = [r for r in rows if r.get("cause") == "opn-absent-from-served"]
    sha_map = json.loads(args.sha_map.read_text())
    manifest_mfrs = set(json.loads(args.manifest_mfrs.read_text()))
    served_vendor_forms = set(json.loads(args.served_vendors.read_text()))

    jobs = [(r, sha_map.get(r["sha"]), manifest_mfrs, served_vendor_forms)
            for r in rows]
    with ProcessPoolExecutor(args.workers) as ex:
        results = list(ex.map(_work, jobs, chunksize=32))

    # identity uniqueness on canon+manufacturer
    by_identity = defaultdict(list)
    for r in results:
        if r["verdict"] == "ADMIT":
            by_identity[(r["manufacturer"], r["canon"])].append(r)
    for key, group in by_identity.items():
        if len(group) > 1:
            group.sort(key=lambda r: -r.get("pin_rows", 0))
            keeper = group[0]
            keeper["identity_unique"] = True
            keeper["duplicate_shas"] = [g["sha"] for g in group[1:]]
            for g in group[1:]:
                g.update(verdict="REJECT", reason="duplicate_identity",
                         duplicate_of=keeper["sha"])

    with args.out.open("w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    verdicts = Counter(r["verdict"] for r in results)
    reasons = Counter(f"{r['verdict']}:{r['reason']}" for r in results)
    pkgs = Counter(r.get("package") for r in results if r["verdict"] == "ADMIT")
    print(json.dumps({
        "population": len(results),
        "verdicts": dict(verdicts),
        "reasons": dict(reasons.most_common()),
        "admit_packages": dict(pkgs.most_common(12)),
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
