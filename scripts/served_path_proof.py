#!/usr/bin/env python3
"""DW-TRUTH-FUNNEL-02 — served-path proof (issue #5 phase 2), corrected.

Deterministic, no LLM. For each claim in the cohort it records SEVEN
INDEPENDENT links and classifies each on its own ladder. Links are NEVER
summed; the overall report carries one count per link ("verified source",
"identity-joined", "folded", "bundled", "authorized", "deployed",
"clickable").

Owner-required corrections (2026-10-28, binding)
------------------------------------------------
C1 fold membership matches CLAIM identity, NOT doc SHA. A fold match requires
   manufacturer/OPN + field + value + conditions + source locator (page) to
   match a specific export row. On mismatch an explicit reason is recorded per
   field (opn_mismatch / field_mismatch / value_mismatch / conditions_mismatch
   / locator_mismatch / row_absent). Doc-sha-only overlap is reported as a
   separate, weaker signal and MUST NOT yield fold=VERIFIED.

C2 FE intersection is an INDEPENDENT comparison. An exact claim key
   (opn + axis + value + unit + doc_sha + page [+ quote hash]) is constructed
   from the EXPORT row and from the FE bundle row independently, then compared
   field-by-field. Unconstructible key (e.g. slug-shaped OPN, missing fields)
   -> bundle = UNKNOWN with the reason. Divergence -> explicit divergence
   record, never bundled=true.

C3 release fingerprint verifies ACTUAL BYTES: cube.json bytes are read and
   sha256'd, compared against the recorded/pinned cube sha AND the
   fold_manifest cube entry. Comparing a manifest field to itself is invalid.
   compare_ready.jsonl bytes are verified against a recorded digest if one
   exists; if none exists the absence is recorded (never invented).

C4 quote failures are investigated and CLASSIFIED, never erased. Every
   original failed record is preserved verbatim in a separate disposition
   artifact with new disposition fields alongside.

Cohort
------
The brief asks for >= 10 claims present in BOTH the export
``compare_ready.jsonl`` AND the FE main committed bundle
``data/labs/compare_ready_spine.jsonl``. The cohort is selected by an
INDEPENDENT claim-key comparison (C2), not by doc-sha presence. On the pinned
inputs the exact claim-key intersection is EMPTY; the maximal honest overlap is
FE bundle rows whose export row shares the claim *identity* (manufacturer/OPN
present) — still an independent per-row comparison, labelled ``selection`` in
every record and never passed off as an exact claim-key match.

Links (each independent; VERIFIED / HELD / FAILED / STAGED_ONLY /
SOURCE_MISSING / UNKNOWN, plus per-link vocabulary):
  1. identity   exact manufacturer+OPN joined against parts_catalog.jsonl
  2. source     doc sha256 + page + verbatim quote verified against vault PDF
  3. fold       claim-identity match against a specific export row (C1)
  4. bundled    independent claim-key comparison export-row vs FE-bundle row (C2)
  5. authorized is the bundle/fold part of an APPROVED release? A manifest is
                 NOT an approval record -> UNKNOWN when none exists
  6. deployed   is the bundle in the CURRENTLY DEPLOYED FE commit (deployments
                 API evidence)? Committed != deployed; inconclusive -> UNKNOWN
  7. resolve    does a browser-accessible authorized route resolve THIS claim's
                 source artifact (sha+page bound)? A static PNG not bound to the
                 claim is NOT a claim-level resolver -> unbound. Auth-gated or
                 missing -> UNKNOWN

Never infer serving from file presence, process state, or a green test.

usage:
  python3 scripts/served_path_proof.py \
      --export-root   <store>/api-store-export \
      --fe-bundle     <fe>/data/labs/compare_ready_spine.jsonl \
      --fe-repo       <fe>                         (for committed-bundle proof) \
      --parts-catalog <store>/api-store-export/parts_catalog.jsonl \
      --vault-cas     <nas>/vault/cas \
      --deployments   <deployments.json>           (gh api output, optional) \
      --evidence-dir  <fe>/public/labs/r2/evidence \
      --out-dir       evidence/served-path-proof-20261028
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

# --- per-link ladders (stable vocabulary) ----------------------------------
VERIFIED = "VERIFIED"
HELD = "HELD"
FAILED = "FAILED"
STAGED_ONLY = "STAGED_ONLY"
SOURCE_MISSING = "SOURCE_MISSING"
UNKNOWN = "UNKNOWN"
UNBOUND = "UNBOUND"          # resolve: static artifact exists but not claim-bound
MISSING = "MISSING"          # resolve: no artifact for this claim

IDENT_EXACT = "exact"
IDENT_PARTIAL = "partial"

# C1 fold-mismatch reason vocabulary (per field, explicit)
R_OPN_MISMATCH = "opn_mismatch"
R_FIELD_MISMATCH = "field_mismatch"
R_VALUE_MISMATCH = "value_mismatch"
R_CONDITIONS_MISMATCH = "conditions_mismatch"
R_LOCATOR_MISMATCH = "locator_mismatch"
R_ROW_ABSENT = "row_absent"

# C1 fold class vocabulary
FOLD_VERIFIED = "VERIFIED"            # full claim-identity match to an export row
FOLD_IDENTITY_PARTIAL = "HELD"        # identity present, some fields differ
FOLD_IDENTITY_MISMATCH = "FAILED"     # identity present, claim fields diverge
FOLD_ABSENT = "ABSENT"                # no export row for this identity
FOLD_DOC_SHA_ONLY = "DOC_SHA_ONLY"    # weaker signal: sha overlap, no claim match

# C2 bundle class vocabulary
BUNDLE_VERIFIED = "VERIFIED"          # independent claim-key match, field-for-field
BUNDLE_DIVERGENT = "DIVERGENT"        # key present both sides, fields differ
BUNDLE_UNKNOWN = "UNKNOWN"            # key not constructible on one/both sides
BUNDLE_ABSENT = "ABSENT"              # key constructible, not present in FE bundle

MIN_COHORT = 10


def norm(s) -> str:
    """Reference normalisation — must match dw_truth::norm exactly."""
    folded = unicodedata.normalize("NFKC", str(s or ""))
    return re.sub(r"\s+", " ", folded).strip().lower()


def norm_value(s) -> str:
    """Value folding: numeric values fold trailing zeros / signs so 40.0 == 40."""
    n = norm(s)
    if not n:
        return ""
    try:
        f = float(n)
        # integral values render without a decimal point; keeps 40.0 == 40
        return str(int(f)) if f == int(f) else repr(f)
    except (TypeError, ValueError):
        return n


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def quote_hash(quote: str) -> str:
    """Stable hash of the normalised quote; None for empty/absent quotes."""
    nq = norm(quote)
    if not nq:
        return None
    return sha256_bytes(nq.encode("utf-8"))[:16]


# ---------------------------------------------------------------------------
# C2 — exact claim key (independent construction from each side)
# ---------------------------------------------------------------------------
CLAIM_KEY_FIELDS = ("opn", "axis", "value", "unit", "doc_sha256", "page")


def _slug_shaped(opn: str) -> bool:
    """Slug-shaped OPN: a manufacturer-prefixed datasheet slug rather than a
    real orderable part number, e.g.
    'infineon-psoc-6-mcu-cyb0644xxzi-s2d44-datasheet-datasheet'.
    A real OPN has no 'datasheet' token and is not lower-kebab above N tokens."""
    n = norm(opn)
    if not n:
        return True
    if n.endswith("-datasheet") or " datasheet" in n:
        return True
    # >=4 kebab tokens AND a vendor word is a slug, not an orderable OPN
    if n.count("-") >= 3 and re.search(r"datasheet|mcu|psoc|programmable", n):
        return True
    return False


def build_claim_key(row: dict):
    """Construct an exact claim key from a row. Returns (key_dict | None,
    reason). Key is None (unconstructible) with an explicit reason when the
    row lacks a required field or carries a slug-shaped OPN.

    Independently applied to the EXPORT row and the FE bundle row (C2)."""
    if not row:
        return None, "row_missing"
    opn = row.get("opn")
    if not opn:
        return None, "opn_missing"
    if _slug_shaped(opn):
        return None, "opn_slug_shaped"
    axis = row.get("axis")
    if not axis:
        return None, "axis_missing"
    if row.get("value") in (None, ""):
        return None, "value_missing"
    if not row.get("unit"):
        return None, "unit_missing"
    sha = row.get("doc_sha256") or row.get("sha")
    if not sha:
        return None, "doc_sha_missing"
    page = row.get("page")
    if page is None or str(page) == "":
        return None, "page_missing"
    key = {
        "opn": norm(opn),
        "axis": norm(axis),
        "value": norm_value(row.get("value")),
        "unit": norm(row.get("unit")),
        "doc_sha256": str(sha),
        "page": str(int(page)) if str(page).lstrip("-").isdigit() else str(page),
        "quote_hash": quote_hash(row.get("quote")),
    }
    return key, None


def compare_claim_keys(export_key, fe_key):
    """Field-by-field comparison of two claim keys. Returns (status, detail).

    status in {'match','divergent','unconstructible'}. Divergence lists each
    field whose value differs."""
    if export_key is None or fe_key is None:
        return "unconstructible", {
            "export_unconstructible": export_key is None,
            "fe_unconstructible": fe_key is None,
        }
    diffs = {}
    for f in CLAIM_KEY_FIELDS + ("quote_hash",):
        ev = export_key.get(f)
        fv = fe_key.get(f)
        if ev != fv:
            diffs[f] = {"export": ev, "fe": fv}
    if diffs:
        return "divergent", {"field_diffs": diffs}
    return "match", {"matched_fields": list(CLAIM_KEY_FIELDS)}


# ---------------------------------------------------------------------------
# C1 — fold claim-identity comparison
# ---------------------------------------------------------------------------
def compare_fold_claim(claim, export_rows):
    """Compare a claim against export rows by CLAIM IDENTITY (C1).

    claim: dict with opn, axis/field, value, unit, conditions, page.
    export_rows: iterable of export row dicts.

    Returns (class, detail). class in {VERIFIED, HELD, FAILED, ABSENT,
    DOC_SHA_ONLY}. detail carries per-field mismatch reasons."""
    opn = norm(claim.get("opn"))
    sha = claim.get("doc_sha256")
    if not opn:
        return FOLD_ABSENT, {"reason": R_OPN_MISMATCH,
                             "note": "claim has no OPN to match on"}

    identity_rows = [r for r in export_rows if norm(r.get("opn")) == opn]
    if not identity_rows:
        # no export row for this identity at all
        sha_present = any(r.get("doc_sha256") == sha for r in export_rows)
        if sha_present:
            return FOLD_DOC_SHA_ONLY, {
                "reason": R_ROW_ABSENT,
                "note": ("no export row shares this claim's OPN; doc sha is "
                         "present in the export but that is sha-overlap only "
                         "and does NOT establish fold membership"),
            }
        return FOLD_ABSENT, {"reason": R_ROW_ABSENT}

    # identity present -> compare claim fields field-by-field
    want_axis = norm(claim.get("axis"))
    want_value = norm_value(claim.get("value"))
    want_unit = norm(claim.get("unit"))
    want_cond = norm(claim.get("conditions"))
    want_page = claim.get("page")

    best = None
    best_diffs = None
    for r in identity_rows:
        diffs = []
        if want_axis and norm(r.get("axis")) != want_axis:
            diffs.append(R_FIELD_MISMATCH)
        if want_value != "" and norm_value(r.get("value")) != want_value:
            diffs.append(R_VALUE_MISMATCH)
        if want_unit and norm(r.get("unit")) != want_unit:
            diffs.append(R_VALUE_MISMATCH)
        if want_cond and norm(r.get("condition_norm") or r.get("conditions")) != want_cond:
            diffs.append(R_CONDITIONS_MISMATCH)
        if want_page is not None and str(r.get("page")) != str(want_page):
            diffs.append(R_LOCATOR_MISMATCH)
        if not diffs:
            return FOLD_VERIFIED, {
                "matched_export_row_sha256": sha256_bytes(
                    json.dumps(r, sort_keys=True).encode("utf-8")),
                "matched_fields": ["opn", "axis", "value", "unit", "page"],
            }
        if best_diffs is None or len(diffs) < len(best_diffs):
            best, best_diffs = r, diffs

    cls = FOLD_IDENTITY_PARTIAL if len(best_diffs) == 1 else FOLD_IDENTITY_MISMATCH
    return cls, {
        "reasons": sorted(set(best_diffs)),
        "nearest_export_row_sha256": sha256_bytes(
            json.dumps(best, sort_keys=True).encode("utf-8")),
    }


def load_export_rows_by_opn(export_lines):
    """opn(normalised) -> list of export row dicts. Used by C1."""
    out: dict[str, list] = {}
    for l in export_lines:
        try:
            d = json.loads(l)
        except Exception:
            continue
        o = norm(d.get("opn"))
        if o:
            out.setdefault(o, []).append(d)
    return out


def fe_bundle_keys(fe_lines):
    """Independent claim-key map for the FE bundle (C2). Returns
    {key_tuple: raw_line} for constructible keys, and counts of unconstructible
    rows with reasons."""
    keys = {}
    unconstructible = {}
    for l in fe_lines:
        try:
            d = json.loads(l)
        except Exception:
            unconstructible["row_invalid_json"] = unconstructible.get("row_invalid_json", 0) + 1
            continue
        k, reason = build_claim_key(d)
        if k is None:
            unconstructible[reason] = unconstructible.get(reason, 0) + 1
            continue
        keys.setdefault(_key_tuple(k), l)
    return keys, unconstructible


def _key_tuple(k: dict):
    return tuple(k.get(f) for f in CLAIM_KEY_FIELDS + ("quote_hash",))


def export_claim_keys(export_lines):
    """opn -> {key_tuple: row} for constructible export claim keys."""
    out: dict[str, dict] = {}
    for l in export_lines:
        try:
            d = json.loads(l)
        except Exception:
            continue
        k, _ = build_claim_key(d)
        if k is None:
            continue
        out.setdefault(k["opn"], {})[_key_tuple(k)] = d
    return out


# ---------------------------------------------------------------------------
# Pure, testable cores (source / identity / bundling / deployment / resolve)
# ---------------------------------------------------------------------------
def verify_quote(quote: str, page_text: str) -> bool:
    """Deterministic verbatim check. Empty quote never verifies."""
    nq = norm(quote)
    if not nq:
        return False
    return nq in norm(page_text)


def verify_quote_normalized(quote: str, page_text: str) -> dict:
    """Non-weakening normalisation ladder used by C4 classification.

    Every level only *reorders / re-joins* the extraction's own characters —
    no token is deleted, substituted, or fuzzy-matched. Levels, in increasing
    strength (the weakest sufficient level is the one recorded):

      L1 ``pipe_as_space``      — the extraction's '|' column delimiters are
                                  treated as whitespace; the quote then reads
                                  as a contiguous run of the line-folded page
                                  text.
      L2 ``cell_containment``   — the quote split on '|' into cells; every cell
                                  is a contiguous substring of the line-folded
                                  page text (cells may live in different
                                  column runs / wrapped lines).
      L3 ``cell_token_containment`` — every cell's tokens all occur on the page
                                  (column-run reading order, wrapped-line joins,
                                  and repositioned footnote markers).
      L3b``cell_token_containment_footnote_folded`` — as L3 but with footnote
                                  markers (``1)`` / ``2)`` …) stripped from page
                                  and cell tokens before comparison (the
                                  extractor may attach or separate a footnote
                                  marker from the token it annotates).

    Returns detail incl. which level succeeded (if any). A pass at L1/L2/L3/L3b
    is consistent with true verbatim content under a documented reading-order
    normalisation and does NOT weaken verbatim-ness."""
    nq = norm(quote)
    pg = norm(page_text)
    if not nq:
        return {"strict_verbatim": False, "reason": "empty_quote"}
    if nq in pg:
        return {"strict_verbatim": True, "normalization": None}
    pipe_as_space = norm(re.sub(r"\|", " ", quote))
    if pipe_as_space and pipe_as_space in pg:
        return {"strict_verbatim": True, "level": "L1",
                "normalization": "pipe_as_space (column delimiter -> whitespace)",
                "normalized_quote": pipe_as_space}
    cells = [c for c in (norm(c) for c in quote.split("|")) if c]
    if cells and all(c in pg for c in cells):
        return {"strict_verbatim": True, "level": "L2",
                "normalization": ("cell_containment (each '|'-delimited cell is "
                                  "a contiguous run of the line-folded page "
                                  "text; cells may live in separate column "
                                  "runs / wrapped lines)"),
                "cells": cells}
    ptoks = set(pg.split())
    if cells and all(all(t in ptoks for t in c.split()) for c in cells):
        return {"strict_verbatim": True, "level": "L3",
                "normalization": ("cell_token_containment (every cell token "
                                  "occurs on the page; permits column-run "
                                  "reading order, wrapped-line joins and "
                                  "repositioned footnote markers)"),
                "cells": cells}
    fn = lambda t: re.sub(r"^[0-9]+\)|[0-9]+\)$", "", t)
    ptoks_fn = {fn(t) for t in ptoks} | ptoks
    if cells and all(all(t in ptoks_fn or fn(t) in ptoks_fn for t in c.split())
                     for c in cells):
        return {"strict_verbatim": True, "level": "L3b",
                "normalization": ("cell_token_containment_footnote_folded "
                                  "(as L3, with footnote markers 'N)' stripped "
                                  "from page and cell tokens before comparison "
                                  "— the extractor may attach or separate a "
                                  "footnote marker from the token it annotates)"),
                "cells": cells}
    # token-presence diagnostic (never sufficient for a pass)
    toks = [t for t in re.split(r"[|\s]+", nq) if len(t) > 1]
    present = [t for t in toks if t in pg]
    return {"strict_verbatim": False, "normalization": None,
            "tokens_total": len(toks), "tokens_present": len(present),
            "cells": cells or None}


def join_identity(part: str, opn: str) -> str:
    """exact / partial / UNKNOWN. No fuzzy guessing."""
    c = norm(part)
    s = norm(opn)
    if not c or not s:
        return UNKNOWN
    if c == s:
        return IDENT_EXACT
    if c in s or s in c:
        return IDENT_PARTIAL
    return UNKNOWN


def classify_source(quote_ok, doc_state) -> str:
    """source link ladder. doc_state: present|absent|no_sha."""
    if doc_state in ("absent", "no_sha"):
        return SOURCE_MISSING
    if quote_ok is False:
        return FAILED
    if quote_ok is True:
        return VERIFIED
    return UNKNOWN


def classify_identity(join_status, catalog_has_opn) -> str:
    """identity link ladder. Need exact OPN join AND a catalog record."""
    if join_status == IDENT_EXACT and catalog_has_opn:
        return VERIFIED
    if join_status == IDENT_PARTIAL and catalog_has_opn:
        return HELD
    if not catalog_has_opn:
        return SOURCE_MISSING
    return UNKNOWN


def classify_fold_legacy(present, fold_sha_match, cube_sha_match) -> str:
    """Legacy doc-sha fold ladder, retained for regression reference only.
    The live fold link uses ``compare_fold_claim`` (C1)."""
    if not present:
        return FOLD_ABSENT
    if fold_sha_match and cube_sha_match:
        return VERIFIED
    return FAILED


def classify_bundled(byte_exact, present_by_value) -> str:
    """Legacy bundle ladder, retained for regression reference only. The live
    bundle link uses ``compare_claim_keys`` (C2)."""
    if byte_exact:
        return VERIFIED
    if present_by_value:
        return HELD
    return BUNDLE_ABSENT


def classify_authorized(approval_record) -> str:
    """authorized link ladder. A manifest is NOT an approval."""
    if approval_record is True:
        return VERIFIED
    return UNKNOWN           # no approval record -> UNKNOWN, never "no"


def classify_deployed(deploy_state) -> str:
    """deployed link ladder. deploy_state: 'deployed'|'not_deployed'|'unknown'."""
    if deploy_state == "deployed":
        return VERIFIED
    if deploy_state == "not_deployed":
        return HELD          # committed but not deployed
    return UNKNOWN           # inconclusive evidence


def classify_resolve(resolve_state) -> str:
    """resolve link ladder.
    resolve_state: 'bound_and_served'|'bound_but_gated'|'unbound'|
                   'missing'|'unknown'."""
    return {
        "bound_and_served": VERIFIED,
        "bound_but_gated": UNKNOWN,
        "unbound": UNBOUND,
        "missing": MISSING,
        "unknown": UNKNOWN,
    }.get(resolve_state, UNKNOWN)


def observe(now=None, host=None) -> dict:
    return {
        "now": now or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": host or os.uname().nodename,
    }


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------
def read_jsonl(p: Path):
    out = []
    with open(p, errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.strip():
                out.append(line)
    return out


def load_fold_manifest(export_root: Path):
    fm = {}
    p = export_root / "fold_manifest.json"
    if p.exists():
        try:
            fm = json.loads(p.read_text())
        except Exception:
            fm = {}
    return fm


def load_catalog_index(catalog_path: Path):
    """part_number -> vendor. Used for the identity join."""
    idx = {}
    if not catalog_path or not Path(catalog_path).exists():
        return idx
    with open(catalog_path, errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            pn = d.get("part_number")
            if pn:
                idx[norm(pn)] = d.get("vendor") or UNKNOWN
    return idx


def resolve_cas(sha: str, cas_roots) -> Path | None:
    if not sha or len(sha) < 4:
        return None
    for r in cas_roots:
        for cand in (Path(r) / sha[:2] / f"{sha}.pdf",
                     Path(r) / sha[:2] / sha[2:4] / f"{sha}.pdf"):
            if cand.exists():
                return cand
    return None


def page_texts(pdf: Path, page_1based):
    """Extract the page with every available tool; {tool: text}."""
    texts = {}
    if pdf is None:
        return {"no-doc": None}
    if page_1based is None:
        return {"no-page": None}
    try:
        r = subprocess.run(
            ["pdftotext", "-f", str(page_1based), "-l", str(page_1based),
             str(pdf), "-"], capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and r.stdout:
            texts["pdftotext"] = r.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    try:
        import fitz
        doc = fitz.open(pdf)
        try:
            t = doc[page_1based - 1].get_text()
            if t:
                texts["pymupdf"] = t
        finally:
            doc.close()
    except Exception:
        pass
    if not texts:
        texts["unescapable"] = None
    return texts


# ---------------------------------------------------------------------------
# C3 — release fingerprint (verify ACTUAL bytes)
# ---------------------------------------------------------------------------
def verify_cube_fingerprint(export_root: Path, fold: dict) -> dict:
    """Read cube.json BYTES, compute sha256, compare against the recorded
    cube sha (fold_manifest's cube entry) AND a pinned cube sha if provided.

    Comparing a manifest field to itself is invalid: we hash the file and
    compare the *measured* sha against what the manifest claims."""
    p = Path(export_root) / "cube.json"
    manifest_cube_sha = (fold.get("cube") or {}).get("sha256")
    file_entry = (fold.get("files") or {}).get("cube.json") or {}
    file_entry_sha = file_entry.get("sha256")
    if not p.exists():
        return {"present": False, "measured_sha256": None,
                "manifest_cube_sha256": manifest_cube_sha,
                "manifest_file_entry_sha256": file_entry_sha,
                "match": False, "reason": "cube.json absent"}
    measured = sha256_file(p)
    checks = {
        "manifest_cube_sha256": manifest_cube_sha,
        "manifest_file_entry_sha256": file_entry_sha,
    }
    mismatches = [k for k, v in checks.items() if v and v != measured]
    match = measured == manifest_cube_sha and not mismatches
    return {
        "present": True,
        "measured_sha256": measured,
        "manifest_cube_sha256": manifest_cube_sha,
        "manifest_file_entry_sha256": file_entry_sha,
        "match": match,
        "mismatched_against": mismatches,
        "reason": None if match else "byte sha256 does not match manifest claim",
    }


def verify_recorded_digest(path: Path, recorded_sha) -> dict:
    """Verify a file's bytes against a recorded digest if one exists; if none
    exists, record that (never invent one)."""
    if recorded_sha is None:
        return {"recorded_sha256": None, "measured_sha256": None,
                "verified": False, "reason": "no recorded digest exists"}
    if not Path(path).exists():
        return {"recorded_sha256": recorded_sha, "measured_sha256": None,
                "verified": False, "reason": "file absent"}
    measured = sha256_file(path)
    return {"recorded_sha256": recorded_sha, "measured_sha256": measured,
            "verified": measured == recorded_sha}


# ---------------------------------------------------------------------------
# Cohort selection (C2 — independent claim-key comparison)
# ---------------------------------------------------------------------------
def byte_exact_intersection(export_lines, fe_lines):
    """Raw-line (byte-exact) intersection. Retained as a weak signal."""
    eset = set(export_lines)
    fset = set(fe_lines)
    return sorted(eset & fset)


def claim_key_intersection(export_lines, fe_lines):
    """Exact claim-key intersection (independent construction each side)."""
    exp_keys: dict[str, set] = {}
    for l in export_lines:
        try:
            d = json.loads(l)
        except Exception:
            continue
        k, _ = build_claim_key(d)
        if k is None:
            continue
        exp_keys.setdefault(k["opn"], set()).add(_key_tuple(k))
    fe_keys, _ = fe_bundle_keys(fe_lines)
    fe_kt = set(fe_keys.keys())
    out = set()
    for opn, ks in exp_keys.items():
        out |= (ks & fe_kt)
    return out


def export_identity_opns(export_lines):
    """Set of normalised OPNs present in the export (claim identity)."""
    opns = set()
    for l in export_lines:
        try:
            d = json.loads(l)
        except Exception:
            continue
        o = norm(d.get("opn"))
        if o:
            opns.add(o)
    return opns


def export_doc_shas(export_lines):
    """Set of doc sha256 values present in the export (weak signal only)."""
    shas = set()
    for l in export_lines:
        try:
            d = json.loads(l)
        except Exception:
            continue
        s = d.get("doc_sha256") or d.get("sha")
        if s:
            shas.add(s)
    return shas


def build_cohort(export_lines, fe_lines, exp_identity_opns, min_cohort,
                 exp_shas=None):
    """Return (cohort, meta). cohort = list of (fe_line, fe_dict, selection).

    Selection is by the exact claim-key intersection (C2 metric); when it is
    below the floor the declared fallback is FE bundle rows whose ``doc_sha256``
    is also present in the export. That fallback is only a *selection* device
    (it is labelled in every record and is never used to grant fold=VERIFIED or
    bundled=VERIFIED): the fold link (C1) and bundle link (C2) each do their own
    independent per-claim comparison regardless of how the row was selected."""
    inter = claim_key_intersection(export_lines, fe_lines)
    if len(inter) >= min_cohort:
        fe_keys, _ = fe_bundle_keys(fe_lines)
        rows = []
        for kt in sorted(inter):
            l = fe_keys.get(kt)
            if l is not None:
                rows.append((l, json.loads(l), "claim_key_exact"))
        meta = {"selection": "claim_key_exact", "claim_key_exact": len(inter),
                "byte_exact": len(byte_exact_intersection(export_lines, fe_lines)),
                "shortfall": False}
        return rows, meta

    exp_shas = exp_shas or set()
    picked = []
    for l in fe_lines:
        try:
            d = json.loads(l)
        except Exception:
            continue
        if d.get("doc_sha256") in exp_shas:
            picked.append((l, d))
    if len(picked) > min_cohort and min_cohort > 0:
        step = len(picked) / min_cohort
        out = []
        seen = set()
        for i in range(min_cohort):
            idx = int(i * step)
            if idx not in seen:
                seen.add(idx)
                out.append(picked[idx])
        picked = out
    # a claim-identity subset, reported separately as an even weaker signal
    identity_present = sum(1 for _, d in picked
                           if norm(d.get("opn")) in exp_identity_opns)
    rows = [(l, d, "doc_sha_overlap") for l, d in picked]
    meta = {
        "selection": "doc_sha_overlap",
        "claim_key_exact": len(inter),
        "byte_exact": len(byte_exact_intersection(export_lines, fe_lines)),
        "claim_identity_present": identity_present,
        "shortfall": True,
        "shortfall_reason": (
            "exact claim-key intersection (independent key built from export "
            "row and FE bundle row, then compared field-for-field) is below "
            f"floor ({len(inter)} < {min_cohort}); the two files are different "
            "release lineages. The fallback cohort is FE bundle rows whose "
            "doc_sha256 is present in the export — a *weak, declared selection* "
            "signal only. It does NOT establish fold membership (C1 requires a "
            "full claim-identity match) or bundle presence (C2 requires an "
            "independent claim-key comparison); both are computed per claim "
            "regardless of selection."),
    }
    return rows, meta


def load_export_index(export_lines):
    """opn -> opn (exact key presence) + doc sha set."""
    opns = {}
    for l in export_lines:
        try:
            d = json.loads(l)
        except Exception:
            continue
        o = d.get("opn")
        if o:
            opns.setdefault(o, o)
    return opns


# ---------------------------------------------------------------------------
# FE bundle byte-exact index + deployments + evidence
# ---------------------------------------------------------------------------
def fe_bundle_index(fe_lines):
    """raw line -> True (byte-exact set) and evidence-key map for value match."""
    exact = set(fe_lines)
    by_evidence = set()
    for l in fe_lines:
        try:
            d = json.loads(l)
        except Exception:
            continue
        by_evidence.add((norm(d.get("opn")), d.get("doc_sha256"),
                         str(d.get("page")), norm(d.get("axis")),
                         norm(d.get("value"))))
    return exact, by_evidence


def load_deployments(path: Path | None):
    """gh api repos/<r>/deployments output. Empty list -> no deployment
    evidence -> UNKNOWN. Returns (records, deployed_sha)."""
    if not path or not Path(path).exists():
        return [], None
    try:
        data = json.loads(Path(path).read_text())
    except Exception:
        return [], None
    if not isinstance(data, list) or not data:
        return (data if isinstance(data, list) else []), None
    recs = data
    dep_sha = None
    for r in recs:
        if isinstance(r, dict) and r.get("sha"):
            dep_sha = r["sha"]
            break
    return recs, dep_sha


def evidence_index(evidence_dir: Path | None):
    """Return {doc_sha256: set(page_int)} from <sha>-<page>.png files."""
    idx = {}
    if not evidence_dir or not Path(evidence_dir).is_dir():
        return idx
    for name in os.listdir(evidence_dir):
        if name.startswith("._") or not name.endswith(".png"):
            continue
        m = re.match(r"^([0-9a-f]{64})-(\d+)\.png$", name)
        if m:
            idx.setdefault(m.group(1), set()).add(int(m.group(2)))
    return idx


# ---------------------------------------------------------------------------
# Per-claim audit
# ---------------------------------------------------------------------------
def audit_claim(fe_line, fe_row, selection, ctx) -> dict:
    opn = fe_row.get("opn")
    sha = fe_row.get("doc_sha256") or fe_row.get("sha")
    page = fe_row.get("page")
    axis = fe_row.get("axis")
    value = fe_row.get("value")
    quote = fe_row.get("quote")

    rec = {
        "selection": selection,
        "fe_row_sha256": sha256_bytes(fe_line.encode("utf-8")),
        "claim": {"opn": opn, "axis": axis, "value": value,
                  "unit": fe_row.get("unit"), "page": page,
                  "doc_sha256": sha,
                  "conditions": fe_row.get("condition_norm") or fe_row.get("conditions"),
                  "quote": quote,
                  "quote_hash": quote_hash(quote)},
    }

    # 1. identity -----------------------------------------------------------
    catalog_has = norm(opn) in ctx["catalog"] if opn else False
    vendor = ctx["catalog"].get(norm(opn), UNKNOWN) if opn else UNKNOWN
    exp_opn = ctx["export_opns"].get(opn) if opn else None
    j = join_identity(opn, exp_opn) if exp_opn else UNKNOWN
    ident_class = classify_identity(j, catalog_has)
    rec["identity"] = {
        "opn": opn, "manufacturer": vendor, "in_catalog": catalog_has,
        "export_opn": exp_opn, "join": j, "class": ident_class,
    }

    # 2. source -------------------------------------------------------------
    doc = resolve_cas(sha, ctx["cas"]) if sha else None
    if not sha:
        doc_state = "no_sha"
    elif doc is None:
        doc_state = "absent"
    else:
        doc_state = "present"
    quote_ok = None
    tool = None
    diag = None
    if doc_state == "present":
        texts = page_texts(doc, page)
        matched = sorted(t for t, txt in texts.items()
                         if txt is not None and verify_quote(quote, txt))
        avail = sorted(t for t, txt in texts.items() if txt is not None)
        if not avail:
            quote_ok = None
        else:
            quote_ok = bool(matched)
            tool = ",".join(matched) if matched else ",".join(avail)
            if not matched:
                # strict verbatim failed; run the non-weakening normalisation
                # check and record the token-presence diagnostic.
                norm_detail = {}
                for t, txt in texts.items():
                    if txt is None:
                        continue
                    d = verify_quote_normalized(quote, txt)
                    if d.get("strict_verbatim"):
                        norm_detail = {"tool": t, **d}
                        break
                    norm_detail.setdefault("tools_tried", []).append(t)
                    norm_detail.setdefault("token_diags", {})[t] = {
                        "tokens_total": d.get("tokens_total"),
                        "tokens_present": d.get("tokens_present"),
                    }
                toks = [t for t in re.split(r"[|\s]+", norm(quote)) if len(t) > 1]
                joined = norm(" ".join(v for v in texts.values() if v))
                present = [t for t in toks if t in joined]
                diag = {
                    "quote_tokens_total": len(toks),
                    "quote_tokens_present_in_page_raw": len(present),
                    "normalization_recoverable": bool(norm_detail.get("strict_verbatim")),
                    "normalization_detail": norm_detail,
                    "note": ("strict verbatim failed; tokens present "
                             "individually — layout/reading-order mismatch, "
                             "not proof of absence")
                    if present else "strict verbatim failed; tokens absent",
                }
    src_class = classify_source(quote_ok, doc_state)
    rec["source"] = {
        "doc_state": doc_state, "quote_ok": quote_ok, "tool": tool,
        "diag": diag,
        "doc_alias": f"nas://vault/cas/{sha[:2]}/{sha}.pdf" if sha else None,
        "class": src_class,
    }

    # 3. fold (C1 — claim identity, NOT doc sha) ---------------------------
    fold = ctx["fold"]
    cube_sha = (fold.get("cube") or {}).get("sha256")
    fold_release = fold.get("fold")
    exp_rows = ctx["export_opns"].get(opn, []) if opn else []
    # note: ctx['export_rows_by_opn'] is the authoritative map
    exp_rows = ctx["export_rows_by_opn"].get(norm(opn), []) if opn else []
    fold_class, fold_detail = compare_fold_claim(
        {"opn": opn, "axis": axis, "value": value, "unit": fe_row.get("unit"),
         "conditions": fe_row.get("condition_norm") or fe_row.get("conditions"),
         "page": page, "doc_sha256": sha},
        exp_rows)
    rec["fold"] = {
        "fold": fold_release, "cube_sha256": cube_sha,
        "export_cube_sha256": ctx["cube_sha"],
        "identity_row_count": len(exp_rows),
        "class": fold_class,
        "detail": fold_detail,
        "weak_signals": {
            "doc_sha_in_export": sha in ctx["exp_shas"] if sha else False,
            "note": ("doc-sha overlap is a separate, weaker signal and does NOT "
                     "yield fold=VERIFIED"),
        },
    }

    # 4. bundled (C2 — independent claim-key comparison) -------------------
    byte_exact = fe_line in ctx["fe_exact"]
    fe_key, fe_reason = build_claim_key(fe_row)
    # export side: independent claim keys for this OPN, built from export rows
    export_key_map = ctx["export_claim_keys"].get(norm(opn), {}) if opn else {}
    rec["bundle"] = {
        "byte_exact": byte_exact,
        "fe_key": fe_key,
        "export_key_count": len(export_key_map),
        "note": ("independent claim-key comparison: the FE key is built from the "
                 "FE bundle row and each export key is built independently from "
                 "export rows, then compared field-by-field; a raw-line byte "
                 "match is a separate stronger signal"),
    }
    if fe_key is None:
        rec["bundle"]["class"] = BUNDLE_UNKNOWN
        rec["bundle"]["export_key"] = None
        rec["bundle"]["comparison"] = {"reason": fe_reason, "side": "fe_row"}
    elif not export_key_map:
        # no export row for this identity at all -> key not constructible there
        rec["bundle"]["class"] = BUNDLE_UNKNOWN
        rec["bundle"]["export_key"] = None
        rec["bundle"]["comparison"] = {"reason": R_ROW_ABSENT, "side": "export_row"}
    else:
        fe_tuple = _key_tuple(fe_key)
        if fe_tuple in export_key_map:
            rec["bundle"]["class"] = BUNDLE_VERIFIED
            rec["bundle"]["export_key"] = fe_key
            rec["bundle"]["comparison"] = {
                "matched_fields": list(CLAIM_KEY_FIELDS) + ["quote_hash"],
                "matched_export_row_sha256": sha256_bytes(
                    json.dumps(export_key_map[fe_tuple], sort_keys=True).encode("utf-8")),
            }
        else:
            # divergence: keys exist on both sides for this identity, none equal.
            # pick the nearest export key deterministically (most shared fields).
            fields = CLAIM_KEY_FIELDS + ("quote_hash",)
            nearest, best = None, -1
            for kt in sorted(export_key_map):
                d = sum(1 for i, f in enumerate(fields)
                        if kt[i] == fe_tuple[i])
                if d > best:
                    best, nearest = d, kt
            exp_row = export_key_map[nearest]
            exp_key, _ = build_claim_key(exp_row)
            _, detail = compare_claim_keys(exp_key, fe_key)
            rec["bundle"]["class"] = BUNDLE_DIVERGENT
            rec["bundle"]["export_key"] = exp_key
            rec["bundle"]["comparison"] = detail

    # 5. authorized ---------------------------------------------------------
    auth_class = classify_authorized(ctx["approval_record"])
    rec["authorized"] = {"approval_record": ctx["approval_record"],
                         "class": auth_class,
                         "note": "a manifest is not an approval"}

    # 6. deployed -----------------------------------------------------------
    dep_sha = ctx["deployed_sha"]
    fe_head = ctx["fe_commit"]
    if dep_sha is None:
        dep_state = "unknown"
    elif dep_sha == fe_head:
        dep_state = "deployed"
    else:
        dep_state = "not_deployed"
    dep_class = classify_deployed(dep_state)
    rec["deployed"] = {"deployed_sha": dep_sha, "fe_commit": fe_head,
                       "state": dep_state, "class": dep_class}

    # 7. resolve ------------------------------------------------------------
    ev = ctx["evidence"]
    bound = bool(sha and sha in ev and page is not None
                 and int(page) in ev.get(sha, set()))
    if not bound:
        resolve_state = "missing" if (sha not in ev) else "unbound"
    else:
        probe = ctx["resolve_probe"]
        if probe == "served":
            resolve_state = "bound_and_served"
        elif probe == "gated":
            resolve_state = "bound_but_gated"
        else:
            resolve_state = "unknown"
    rec["resolve"] = {
        "artifact_bound": bound,
        "artifact_href": (f"/labs/r2/evidence/{sha}-{page}.png"
                          if bound else None),
        "probe": ctx["resolve_probe"], "state": resolve_state,
        "class": classify_resolve(resolve_state),
    }

    return rec


# ---------------------------------------------------------------------------
# C4 — quote-failure classification (analysis only; never repairs claims)
# ---------------------------------------------------------------------------
QUOTE_DEFECT = "QUOTE_DEFECT"
EXTRACTION_ARTIFACT = "EXTRACTION_ARTIFACT"
NORMALIZATION_MISMATCH = "NORMALIZATION_MISMATCH"
LOCATOR_MISMATCH = "LOCATOR_MISMATCH"
DOC_VERSION_MISMATCH = "DOC_VERSION_MISMATCH"


def classify_quote_failure(quote, page_text, page_other_texts=None):
    """Classify a single failed quote. Returns (disposition, evidence).

    Order of tests (each documented, no weakening of verbatim-ness):
      1. strict verbatim on the recorded page (via norm) -> not a failure.
      2. non-weakening normalisation ladder L1/L2/L3 on the recorded page ->
         EXTRACTION_ARTIFACT (layout / reading-order / column interleaving /
         wrapped-line joins / repositioned footnote markers).
      3. verbatim on a *different* page -> LOCATOR_MISMATCH.
      4. unit/space/case folding only -> NORMALIZATION_MISMATCH.
      5. otherwise -> QUOTE_DEFECT if tokens absent; UNKNOWN if uncertain.
    """
    if page_text is None:
        return UNKNOWN, {"reason": "no page text available"}
    pg = norm(page_text)
    nq = norm(quote)
    if not nq:
        return QUOTE_DEFECT, {"reason": "empty_quote"}
    if nq in pg:
        return VERIFIED, {"reason": "strict verbatim holds on recorded page"}
    # 2. non-weakening normalisation ladder
    d = verify_quote_normalized(quote, page_text)
    if d.get("strict_verbatim") and d.get("level"):
        return EXTRACTION_ARTIFACT, {
            "normalization": d["normalization"],
            "level": d["level"],
            "cells": d.get("cells"),
        }
    # 3. locator mismatch: verbatim present on some other page
    if page_other_texts:
        for pgno, txt in page_other_texts.items():
            if txt is None:
                continue
            if nq in norm(txt):
                return LOCATOR_MISMATCH, {"found_on_page": pgno}
    # 4. normalization mismatch: unit/space/case folding
    unit_folded = norm(re.sub(r"[°º]\s*([cC]|f|k)\s*/?\s*([wW])", r"\1/\2", nq))
    if unit_folded != nq and unit_folded in pg:
        return NORMALIZATION_MISMATCH, {
            "fold": "unit/space/case folding",
            "folded_quote": unit_folded,
        }
    # token diagnostic
    toks = [t for t in re.split(r"[|\s]+", nq) if len(t) > 1]
    present = [t for t in toks if t in pg]
    if present and len(present) == len(toks):
        return UNKNOWN, {
            "reason": ("all tokens present individually but no documented "
                       "non-weakening normalisation reconstructs the quote; "
                       "classification uncertain"),
            "tokens_total": len(toks), "tokens_present": len(present),
        }
    return QUOTE_DEFECT, {
        "reason": "quote tokens absent from the document page text",
        "tokens_total": len(toks), "tokens_present": len(present),
    }


def build_quote_disposition(claims, ctx):
    """For each claim whose source link FAILED, produce a disposition record
    that PRESERVES the original failed record verbatim and adds new
    disposition fields alongside. Analysis only — no claim is repaired."""
    out = []
    for rec in claims:
        if rec["source"]["class"] != FAILED:
            continue
        sha = rec["claim"]["doc_sha256"]
        page = rec["claim"]["page"]
        doc = resolve_cas(sha, ctx["cas"]) if sha else None
        quote = rec["claim"].get("quote")
        page_text = None
        other_pages = {}
        if doc is not None and page is not None:
            texts = page_texts(doc, page)
            page_text = next((t for t in texts.values() if t), None)
            # sample neighbouring pages for a locator-mismatch check
            for pgno in _neighbour_pages(page):
                t = page_texts(doc, pgno)
                tv = next((v for v in t.values() if v), None)
                if tv:
                    other_pages[pgno] = tv
        disp, evidence = classify_quote_failure(quote, page_text, other_pages)
        out.append({
            "opn": rec["claim"]["opn"],
            "doc_sha256": sha,
            "recorded_page": page,
            "axis": rec["claim"]["axis"],
            "value": rec["claim"]["value"],
            "unit": rec["claim"]["unit"],
            "original_failed_record_verbatim": rec,
            "quote": quote,
            "quote_hash": quote_hash(quote),
            "disposition": disp,
            "evidence": evidence,
        })
    return out


def _neighbour_pages(page):
    try:
        p = int(page)
    except (TypeError, ValueError):
        return []
    return [p + 1, p + 2, p + 3, p - 1]


# ---------------------------------------------------------------------------
def run(export_root, fe_bundle, parts_catalog, cas_roots, deployments_json,
        evidence_dir, out_dir, fe_commit, fe_repo, min_cohort, resolve_probe,
        approval_record, still_pin=None, now=None, host=None):
    export_root = Path(export_root)
    fe_bundle = Path(fe_bundle)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    export_lines = read_jsonl(export_root / "compare_ready.jsonl")
    fe_lines = read_jsonl(fe_bundle)

    exp_shas = {json.loads(l)["doc_sha256"] for l in export_lines
                if _safe(l) and json.loads(l).get("doc_sha256")}
    exp_identity_opns = export_identity_opns(export_lines)
    cohort, meta = build_cohort(export_lines, fe_lines, exp_identity_opns,
                                min_cohort, exp_shas=exp_shas)

    fe_exact, fe_by_evidence = fe_bundle_index(fe_lines)
    catalog = load_catalog_index(Path(parts_catalog)) if parts_catalog else {}
    fold = load_fold_manifest(export_root)
    cube_sha = (fold.get("cube") or {}).get("sha256")
    dep_recs, dep_sha = load_deployments(deployments_json)
    ev = evidence_index(evidence_dir)

    # C3 — release fingerprint (verify actual bytes)
    cube_fp = verify_cube_fingerprint(export_root, fold)
    # compare_ready digest: recorded only if the fold manifest records one
    compare_ready_recorded = None
    fm_files = (fold.get("files") or {})
    if "compare_ready.jsonl" in fm_files:
        compare_ready_recorded = fm_files["compare_ready.jsonl"].get("sha256")
    cr_digest = verify_recorded_digest(export_root / "compare_ready.jsonl",
                                       compare_ready_recorded)

    # C1 requires per-OPN export row lists; C4 requires FE row by sha
    export_rows_by_opn = load_export_rows_by_opn(export_lines)
    fe_rows_by_sha = {}
    for l in fe_lines:
        try:
            d = json.loads(l)
        except Exception:
            continue
        s = d.get("doc_sha256") or d.get("sha")
        if s and s not in fe_rows_by_sha:
            fe_rows_by_sha[s] = d

    ctx = {
        "catalog": catalog,
        "export_opns": load_export_index(export_lines),
        "export_rows_by_opn": export_rows_by_opn,
        "export_claim_keys": export_claim_keys(export_lines),
        "exp_shas": exp_shas,
        "cas": [str(c) for c in cas_roots],
        "fold": fold,
        "cube_sha": cube_sha,
        "fe_exact": fe_exact,
        "fe_by_evidence": fe_by_evidence,
        "deployed_sha": dep_sha,
        "fe_commit": fe_commit,
        "evidence": ev,
        "resolve_probe": resolve_probe,
        "approval_record": approval_record,
        "fe_rows_by_sha": fe_rows_by_sha,
    }

    claims = [audit_claim(l, d, sel, ctx) for (l, d, sel) in cohort]

    # per-link counts, reported SEPARATELY, never summed
    link_keys = {
        "verified_source": ("source", VERIFIED),
        "identity_joined": ("identity", VERIFIED),
        "folded": ("fold", FOLD_VERIFIED),
        "bundled": ("bundle", BUNDLE_VERIFIED),
        "authorized": ("authorized", VERIFIED),
        "deployed": ("deployed", VERIFIED),
        "clickable": ("resolve", VERIFIED),
    }
    link_counts = {}
    for name, (grp, cls) in link_keys.items():
        link_counts[name] = sum(1 for c in claims if c[grp]["class"] == cls)
    distributions = {}
    for grp in ("source", "identity", "fold", "bundle", "authorized",
                "deployed", "resolve"):
        dist = {}
        for c in claims:
            dist[c[grp]["class"]] = dist.get(c[grp]["class"], 0) + 1
        distributions[grp] = dist

    # C4 — quote-failure disposition
    quote_disposition = build_quote_disposition(claims, ctx)

    doc = {
        "schema": "designwins.truth-funnel.served-path-proof.v1",
        "observation": observe(now, host),
        "inputs": {
            "export_compare_ready_sha256": sha256_file(
                export_root / "compare_ready.jsonl"),
            "export_fold_manifest_sha256": sha256_file(
                export_root / "fold_manifest.json"),
            "export_cube_sha256": cube_sha,
            "fe_bundle_sha256": sha256_file(fe_bundle),
            "fe_commit": fe_commit,
            "parts_catalog_present": bool(catalog),
            "deployments_records": len(dep_recs),
            "deployed_sha": dep_sha,
        },
        "release_fingerprint": {
            "cube": cube_fp,
            "compare_ready": cr_digest,
            "fold_manifest_cube_entry": cube_sha,
        },
        "cohort": {
            "selection": meta["selection"],
            "size": len(claims),
            "claim_key_exact_intersection": meta.get("claim_key_exact"),
            "byte_exact_intersection": meta.get("byte_exact"),
            "claim_identity_present": meta.get("claim_identity_present"),
            "shortfall": meta["shortfall"],
            "shortfall_reason": meta.get("shortfall_reason"),
        },
        "link_counts": link_counts,
        "link_distributions": distributions,
        "quote_disposition_counts": _count_dispositions(quote_disposition),
        "claims": claims,
    }
    (out / "served_path_proof_v1.json").write_text(
        json.dumps(doc, indent=1, default=str))

    # C4 artifact — separate, preserves originals verbatim
    qd = {
        "schema": "designwins.truth-funnel.quote-failure-disposition.v1",
        "observation": doc["observation"],
        "summary": _count_dispositions(quote_disposition),
        "failures": quote_disposition,
    }
    (out / "quote_failure_disposition_v1.json").write_text(
        json.dumps(qd, indent=1, default=str))
    (out / "QUOTE-FAILURE-DISPOSITION.md").write_text(
        _quote_disposition_md(qd))

    # markdown summary
    lines = _served_path_md(doc, meta, link_keys, link_counts, distributions,
                            claims)
    (out / "SERVED-PATH-PROOF.md").write_text("\n".join(lines) + "\n")

    print(f"wrote {out/'served_path_proof_v1.json'}; cohort={len(claims)} "
          f"selection={meta['selection']} claim_key_exact="
          f"{meta.get('claim_key_exact')} byte_exact={meta.get('byte_exact')} "
          f"counts={link_counts}")
    print(f"cube fingerprint match={cube_fp['match']}; quote failures="
          f"{len(quote_disposition)} -> {doc['quote_disposition_counts']}")
    return 0


def _safe(l):
    try:
        json.loads(l)
        return True
    except Exception:
        return False


def _count_dispositions(records):
    c = {}
    for r in records:
        c[r["disposition"]] = c.get(r["disposition"], 0) + 1
    return dict(sorted(c.items()))


def _served_path_md(doc, meta, link_keys, link_counts, distributions, claims):
    lines = ["# Served-Path Proof — " + doc["observation"]["now"], "",
             f"cohort selection: `{meta['selection']}` size={len(claims)}; "
             f"claim-key-exact intersection={meta.get('claim_key_exact')}; "
             f"byte-exact intersection={meta.get('byte_exact')}; "
             f"identity-present={meta.get('claim_identity_present')}",
             "",
             "## Release fingerprint (C3 — actual bytes)",
             "",
             f"- cube.json measured sha256 "
             f"`{(doc['release_fingerprint']['cube'].get('measured_sha256') or 'ABSENT')}` "
             f"→ match={doc['release_fingerprint']['cube']['match']}",
             f"- compare_ready recorded digest: "
             f"{'none exists (not invented)' if not doc['release_fingerprint']['compare_ready']['recorded_sha256'] else doc['release_fingerprint']['compare_ready']['recorded_sha256']}",
             "",
             "## Link counts (independent — never summed)", "",
             "| link | VERIFIED count |", "|---|---|"]
    for name in link_keys:
        lines.append(f"| {name} | {link_counts[name]} |")
    lines += ["", "## Per-link class distribution", ""]
    for grp, dist in distributions.items():
        lines.append(f"- {grp}: " +
                     ", ".join(f"{k}={v}" for k, v in sorted(dist.items())))
    lines += ["", "## Quote-failure disposition (C4)", ""]
    for k, v in doc["quote_disposition_counts"].items():
        lines.append(f"- {k}: {v}")
    lines += ["", "See QUOTE-FAILURE-DISPOSITION.md.", "",
              "| # | opn | axis | source | identity | fold | bundle | auth | deployed | resolve |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for i, c in enumerate(claims, 1):
        lines.append(
            f"| {i} | {c['claim']['opn']} | {c['claim']['axis']} | "
            f"{c['source']['class']} | {c['identity']['class']} | "
            f"{c['fold']['class']} | {c['bundle']['class']} | "
            f"{c['authorized']['class']} | {c['deployed']['class']} | "
            f"{c['resolve']['class']} |")
    return lines


def _quote_disposition_md(qd):
    lines = ["# Quote-Failure Disposition — " + qd["observation"]["now"], "",
             "System of record: GitHub issue `outcited/outcitedharness#5` "
             "(phase 2).", "",
             "This artifact is ANALYSIS ONLY. It preserves every original "
             "failed source record verbatim and adds disposition fields "
             "alongside. No stored claim was repaired.", "",
             "## Summary", ""]
    for k, v in qd["summary"].items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## Dispositions", "",
              "| # | opn | page | value | disposition | detail |",
              "|---|---|---|---|---|---|"]
    for i, f in enumerate(qd["failures"], 1):
        ev = f["evidence"]
        detail = ev.get("normalization") or ev.get("reason") or \
            ev.get("fold") or ""
        if ev.get("found_on_page"):
            detail = f"found on page {ev['found_on_page']}"
        lines.append(
            f"| {i} | {f['opn']} | {f['recorded_page']} | "
            f"{f['value']} {f['unit']} | {f['disposition']} | "
            f"{str(detail).replace('|', '/')} |")
    lines += ["", "## Per-failure evidence (originals preserved verbatim)", ""]
    for i, f in enumerate(qd["failures"], 1):
        lines += [f"### {i}. {f['opn']} (page {f['recorded_page']})", "",
                  f"- disposition: **{f['disposition']}**",
                  f"- quote: `{f['quote']}`",
                  f"- evidence: `{json.dumps(f['evidence'], sort_keys=True)}`",
                  ""]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--export-root", required=True)
    ap.add_argument("--fe-bundle", required=True)
    ap.add_argument("--parts-catalog", default=None)
    ap.add_argument("--vault-cas", nargs="+", required=True)
    ap.add_argument("--deployments", default=None,
                    help="gh api deployments JSON (empty list -> UNKNOWN)")
    ap.add_argument("--evidence-dir", default=None)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--fe-commit", default=None)
    ap.add_argument("--fe-repo", default=None)
    ap.add_argument("--min-cohort", type=int, default=MIN_COHORT)
    ap.add_argument("--resolve-probe", default="unknown",
                    choices=["unknown", "served", "gated"],
                    help="live probe result for the bound artifact "
                         "(auth-gated/missing -> UNKNOWN)")
    ap.add_argument("--approval-record", action="store_true",
                    help="an APPROVED release record exists (a manifest is not "
                         "one); default absent -> UNKNOWN")
    a = ap.parse_args(argv)
    return run(a.export_root, a.fe_bundle, a.parts_catalog, a.vault_cas,
               a.deployments, a.evidence_dir, a.out_dir, a.fe_commit, a.fe_repo,
               a.min_cohort, a.resolve_probe, bool(a.approval_record))


if __name__ == "__main__":
    raise SystemExit(main())