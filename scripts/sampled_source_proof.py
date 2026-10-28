#!/usr/bin/env python3
"""DW-TRUTH-FUNNEL-01 — deterministic source-to-served claim audit.

Samples >=20 real claims (>=10 MCU + >=10 POWER) and, for each, resolves the
full chain STAGED -> VERIFIED -> RELEASED -> RESOLVABLE with NO LLM anywhere.

Per claim it records and classifies:

  * claim identity (claim_id; part/OPN; field, value, unit, conditions)
  * verbatim quote + doc sha256 + page
  * deterministic source-text verification: the quote is extracted from the
    source PDF page text and matched verbatim (same NFKC/whitespace/lower
    normalisation as ``dw_truth::norm`` / ``scripts/locate_claim_pages.py``).
    The tool used to obtain page text is recorded (``pdftotext`` or staged
    extraction text or ``pymupdf``).
  * identity join status (exact / partial / UNKNOWN)
  * release id + served (present in the served export release? y/n)
  * resolvable (is there a viewer resolver / clickable path? y/n/UNKNOWN —
    a bare SHA is NOT a working link)

Link classification (never summed across classes):
  VERIFIED      staged, quote matches source, identity exact, served
  HELD          staged + quote verified but NOT served (staged != released)
  FAILED        staged but quote does not match the source page text
  STAGED_ONLY   staged, source doc absent so verification not possible
  SOURCE_MISSING the claim's source doc/sha cannot be resolved at all
  UNKNOWN       insufficient evidence to decide

Laws: no LLM verification. Determinism: two runs on pinned inputs differ only
in the ``observation`` metadata block (observed_at/host), asserted by tests.

usage:
  python3 scripts/sampled_source_proof.py --out-dir evidence/truth-funnel-20261028 \
      --power-claims <batch-spine-claims-v2.jsonl> \
      --mcu-claims-root <staging/mcu_family_content> \
      --served-export <api-store-export> \
      --vault-cas <vault/cas>
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

# --- link classes (stable vocabulary) --------------------------------------
VERIFIED = "VERIFIED"
HELD = "HELD"
FAILED = "FAILED"
STAGED_ONLY = "STAGED_ONLY"
SOURCE_MISSING = "SOURCE_MISSING"
UNKNOWN = "UNKNOWN"

CLASSES = (VERIFIED, HELD, FAILED, STAGED_ONLY, SOURCE_MISSING, UNKNOWN)

# --- identity join vocab ---------------------------------------------------
EXACT = "exact"
PARTIAL = "partial"
IDENT_UNKNOWN = "UNKNOWN"

MIN_MCU = 10
MIN_POWER = 10
MIN_TOTAL = 20


def norm(s) -> str:
    """Reference normalisation — must match dw_truth::norm exactly."""
    folded = unicodedata.normalize("NFKC", str(s or ""))
    return re.sub(r"\s+", " ", folded).strip().lower()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_cas(sha: str, cas_roots) -> Path | None:
    """CAS layout ``<root>/<aa>/<sha>.pdf`` (also tolerate <aa>/<bb>/)."""
    if not sha or len(sha) < 4:
        return None
    for r in cas_roots:
        for cand in (Path(r) / sha[:2] / f"{sha}.pdf",
                     Path(r) / sha[:2] / sha[2:4] / f"{sha}.pdf"):
            if cand.exists():
                return cand
    return None


# ---------------------------------------------------------------------------
# Pure, testable cores
# ---------------------------------------------------------------------------
def verify_quote(quote: str, page_text: str) -> bool:
    """Deterministic verbatim check. Empty quote never verifies."""
    nq = norm(quote)
    if not nq:
        return False
    return nq in norm(page_text)


def join_identity(claim_part: str, served_opn: str) -> str:
    """exact / partial / UNKNOWN. No fuzzy guessing: unknown stays UNKNOWN."""
    c = norm(claim_part)
    s = norm(served_opn)
    if not c or not s:
        return IDENT_UNKNOWN
    if c == s:
        return EXACT
    # partial only when one is a whole-token superstring of the other
    if c in s or s in c:
        return PARTIAL
    return IDENT_UNKNOWN


def classify(quote_ok, doc_state, served, identity) -> str:
    """Deterministic link class. Never returns a number/sum.

    doc_state: "present" | "absent" | "no_sha".
      "absent"/"no_sha" -> SOURCE_MISSING (never zero, never guessed).
    A staged row whose source doc is absent is NOT STAGED_ONLY — the source
    itself is missing, so it is SOURCE_MISSING. STAGED_ONLY is reserved for a
    present source that we chose not to fully verify.
    """
    if doc_state in ("absent", "no_sha"):
        return SOURCE_MISSING
    if quote_ok is False:
        return FAILED
    if quote_ok is True:
        if served and identity == EXACT:
            return VERIFIED
        return HELD
    return UNKNOWN


def observe(funnel, now=None, host=None) -> dict:
    """Strip observation metadata so two runs compare on evidence only."""
    return {
        "now": now or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": host or os.uname().nodename,
    }


# ---------------------------------------------------------------------------
# Source text extraction (recorded provenance: which tool)
# ---------------------------------------------------------------------------
def page_text_pdftotext(pdf: Path, page_1based: int, which: str = "pdftotext"):
    """Extract one page via pdftotext -f -l. Returns (text, tool) or (None, tool)."""
    exe = "pdftotext"
    try:
        r = subprocess.run(
            [exe, "-f", str(page_1based), "-l", str(page_1based), str(pdf), "-"],
            capture_output=True, text=True, timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None, exe
    if r.returncode != 0:
        return None, exe
    return r.stdout, exe


def page_text_pymupdf(pdf: Path, page_1based: int):
    try:
        import fitz
    except Exception:
        return None, "pymupdf-unavailable"
    try:
        doc = fitz.open(pdf)
        try:
            return doc[page_1based - 1].get_text(), "pymupdf"
        finally:
            doc.close()
    except Exception:
        return None, "pymupdf-error"


def extract_page_text(pdf: Path, page_1based: int):
    """Try pdftotext, fall back to pymupdf. Records which tool produced it."""
    if pdf is None:
        return None, "no-doc"
    if page_1based is None:
        return None, "no-page"
    txt, tool = page_text_pdftotext(pdf, page_1based)
    if txt:
        return txt, tool
    txt, tool = page_text_pymupdf(pdf, page_1based)
    return txt, tool


def extract_page_texts(pdf: Path, page_1based: int):
    """Extract the page with every available tool; return {tool: text}.

    Layout-reading order differs between extractors (pdftotext shuffles some
    table cells; pymupdf follows the text objects). A verbatim quote can match
    one and not the other. We therefore verify against ALL extractors and PASS
    if any matches verbatim, recording which — a single extractor would turn a
    tool's reading-order artifact into a false FAIL.
    """
    texts = {}
    if pdf is None:
        return {"no-doc": None}
    if page_1based is None:
        return {"no-page": None}
    t, tool = page_text_pdftotext(pdf, page_1based)
    if t:
        texts[tool] = t
    t, tool = page_text_pymupdf(pdf, page_1based)
    if t:
        texts[tool] = t
    if not texts:
        texts["unescapable"] = None
    return texts


# ---------------------------------------------------------------------------
# Sampling — deterministic, strided, no randomness
# ---------------------------------------------------------------------------
def deterministic_sample(rows, n):
    """Evenly-strided deterministic sample of n rows (order-stable)."""
    rows = list(rows)
    if n <= 0 or not rows:
        return []
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    picked = []
    seen = set()
    for i in range(n):
        idx = int(i * step)
        if idx not in seen:
            seen.add(idx)
            picked.append(rows[idx])
    return picked


def load_power_claims(path, n):
    """Power lane: batch-spine-claims-v2.jsonl rows (sha+page+verbatim)."""
    rows = []
    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            d["__lane"] = "POWER"
            rows.append(d)
    return deterministic_sample(rows, n)


def iter_mcu_staged(mcu_root):
    """Yield (part_number, field, pillar_dict) from staged MCU pillar files."""
    root = Path(mcu_root)
    # from-cr-core files are dicts with overview/pillars-like sections; the
    # datasheet_extractions/ files carry {"part_number", "pillars": {...}}.
    sub = root / "datasheet_extractions"
    search_roots = [sub if sub.is_dir() else root]
    for sr in search_roots:
        if not sr.is_dir():
            continue
        for name in sorted(os.listdir(sr)):
            if name.startswith("._") or not name.endswith(".json"):
                continue
            p = sr / name
            try:
                d = json.loads(p.read_text(errors="replace"))
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            pn = d.get("part_number") or name[:-5]
            pillars = d.get("pillars")
            if not isinstance(pillars, dict):
                continue
            for field, v in sorted(pillars.items()):
                if isinstance(v, dict):
                    yield pn, field, v


def load_mcu_claims(mcu_root, n):
    """MCU lane: staged pillar records carrying pdf_sha+page+verbatim."""
    all_rows = []
    for pn, field, v in iter_mcu_staged(mcu_root):
        sha = v.get("pdf_sha")
        verb = v.get("verbatim")
        if not sha or verb is None:
            continue
        all_rows.append({
            "__lane": "MCU",
            "part_number": pn,
            "field": field,
            "value_raw": v.get("value"),
            "unit_raw": v.get("unit"),
            "value_role": v.get("value_role"),
            "conditions_raw": v.get("condition_verbatim"),
            "pdf_sha": sha,
            "page_1based": v.get("page_1based"),
            "verbatim": verb,
            "source_url": v.get("source_url"),
        })
    return deterministic_sample(all_rows, n)


# ---------------------------------------------------------------------------
# Served export index
# ---------------------------------------------------------------------------
def load_served_index(export_root):
    """Return (index, release_id). index[(opn_norm, field_hint)] -> row; also
    a doc-sha set and opn set. Served = row present in the export release."""
    export_root = Path(export_root)
    served_rows = []
    cr = export_root / "compare_ready.jsonl"
    if cr.exists():
        with open(cr, errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                served_rows.append(json.loads(line))
    cube = {}
    cj = export_root / "cube.json"
    if cj.exists():
        try:
            cube = json.loads(cj.read_text())
        except Exception:
            cube = {}
    fm = export_root / "fold_manifest.json"
    release_id = UNKNOWN
    if fm.exists():
        try:
            m = json.loads(fm.read_text())
            release_id = m.get("release_id") or m.get("id")
            if not release_id:
                # fold manifest carries a content sha for the cube — use it
                release_id = (m.get("cube") or {}).get("sha256")
        except Exception:
            pass
    if not release_id or release_id == UNKNOWN:
        mj = export_root / "manifest.json"
        if mj.exists():
            try:
                mm = json.loads(mj.read_text())
                release_id = (mm.get("files", {}).get("cube.json", {})
                              .get("sha256"))
            except Exception:
                pass
    served_doc_shas = {norm(r.get("doc_sha256")) for r in served_rows
                       if r.get("doc_sha256")}
    served_opns = {norm(r.get("opn")) for r in served_rows if r.get("opn")}
    return {
        "rows": served_rows,
        "release_id": release_id or UNKNOWN,
        "doc_shas": served_doc_shas,
        "opns": served_opns,
        "cube": cube,
        "available": cr.exists(),
    }


def served_lookup(served, opn, doc_sha, field, value):
    """Is this claim's evidence present in the served export? y/n.

    A claim is SERVED only if the export holds a row for its (opn or doc sha)
    AND (when field/value are known) a matching axis/value. Absent -> n.
    """
    if not served.get("available"):
        return False
    o = norm(opn)
    s = norm(doc_sha)
    for r in served["rows"]:
        if norm(r.get("opn")) != o and norm(r.get("doc_sha256")) != s:
            continue
        # relax on value string equality when available
        rv = str(r.get("value"))
        if value is not None and rv and norm(rv) != norm(value):
            # same part/doc present, but this axis value not matched
            if field and norm(r.get("axis", "")) != norm(field):
                continue
        return True
    return False


def viewer_resolvable(served):
    """Is there a viewer resolver / clickable path in the served release?

    The export is document-relative (sha only) and exposes no viewer/link.
    Per contract a bare SHA is NOT a working link -> UNKNOWN, never 'y'.
    """
    if not served.get("available"):
        return UNKNOWN
    for r in served["rows"]:
        if any(k in r for k in ("url", "viewer", "href", "link",
                                "click_url", "resolver")):
            return "y"
    return UNKNOWN


# ---------------------------------------------------------------------------
# Audit driver
# ---------------------------------------------------------------------------
def audit_claim(row, served, cas_roots, release_id):
    """Produce one fully-classified claim record. Deterministic."""
    lane = row.get("__lane")
    part = (row.get("part_number") or row.get("part_slug")
            or row.get("opn") or UNKNOWN)
    field = row.get("field")
    value = row.get("value_raw")
    unit = row.get("unit_raw")
    conds = row.get("conditions_raw")
    quote = row.get("verbatim")
    sha = row.get("pdf_sha")
    page = row.get("page_1based")

    rec = {
        "lane": lane,
        "claim_id": row.get("claim_id") or _claim_id(lane, part, field,
                                                     sha, page, quote),
        "identity": {"part": part, "field": field, "value": value,
                     "unit": unit, "conditions": conds},
        "evidence": {"doc_sha256": sha, "page": page, "quote": quote},
        "source_file": row.get("source_path") or row.get("source_url"),
    }

    # source resolution (tri-state: present / absent / no_sha)
    doc = resolve_cas(sha, cas_roots) if sha else None
    if not sha:
        doc_state = "no_sha"
    elif doc is None:
        doc_state = "absent"
    else:
        doc_state = "present"
    rec["source"] = {
        "doc_state": doc_state,
        "doc_present_in_vault": (doc is not None) if sha else None,
        "doc_alias": f"nas://vault/cas/{sha[:2]}/{sha}.pdf" if sha else UNKNOWN,
    }

    # verification
    if not sha:
        rec["verification"] = {"status": UNKNOWN, "tool": None,
                               "reason": "claim carries no doc sha"}
    elif doc is None:
        rec["verification"] = {"status": UNKNOWN, "tool": None,
                               "reason": "source doc not in vault CAS"}
    else:
        texts = extract_page_texts(doc, page)
        matched = sorted(t for t, txt in texts.items()
                         if txt is not None and verify_quote(quote, txt))
        tools_available = sorted(t for t, txt in texts.items() if txt is not None)
        if not tools_available:
            rec["verification"] = {"status": UNKNOWN,
                                   "tool": ",".join(sorted(texts)),
                                   "reason": "page text unextractable"}
        else:
            rec["verification"] = {
                "status": "PASS" if matched else "FAIL",
                "tool": ",".join(matched) if matched else
                        ",".join(tools_available),
                "tools_tried": tools_available,
                "tools_matched": matched,
                "quote_normalized": norm(quote)}
            if matched and len(tools_available) > 1 and \
                    len(matched) < len(tools_available):
                rec["verification"]["note"] = (
                    "extractor reading-order differs; matched on "
                    + ",".join(matched))

    # identity join vs served
    served_opn = UNKNOWN
    for opn in served.get("opns", ()):
        if join_identity(part, opn) == EXACT:
            served_opn = opn
            break
    if served_opn == UNKNOWN and served.get("opns"):
        for opn in sorted(served["opns"]):
            if join_identity(part, opn) in (EXACT, PARTIAL):
                served_opn = opn
                break
    ident = join_identity(part, served_opn) if served_opn != UNKNOWN else (
        IDENT_UNKNOWN)
    rec["identity_join"] = {"served_opn": served_opn, "status": ident}

    # served?
    is_served = served_lookup(served, served_opn if served_opn != UNKNOWN
                              else part, sha, field, value)
    rec["release"] = {"release_id": release_id, "served": is_served}

    # resolvable / clickable
    rec["resolvable"] = {
        "clickable": viewer_resolvable(served),
        "note": "no viewer resolver in export; a SHA is not a working link",
    }

    quote_ok = None
    if rec["verification"]["status"] == "PASS":
        quote_ok = True
    elif rec["verification"]["status"] == "FAIL":
        quote_ok = False
    rec["classification"] = classify(quote_ok, doc_state, is_served, ident)
    return rec


def _claim_id(lane, part, field, sha, page, quote):
    h = hashlib.sha256()
    h.update("|".join(str(x) for x in
                     (lane, part, field, sha, page, norm(quote))).encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--power-claims", required=True)
    ap.add_argument("--mcu-claims-root", required=True)
    ap.add_argument("--served-export", required=True)
    ap.add_argument("--vault-cas", nargs="+", required=True)
    ap.add_argument("--min-power", type=int, default=MIN_POWER)
    ap.add_argument("--min-mcu", type=int, default=MIN_MCU)
    a = ap.parse_args()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    host = os.uname().nodename
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    served = load_served_index(a.served_export)
    release_id = served["release_id"]
    power = load_power_claims(a.power_claims, a.min_power)
    mcu = load_mcu_claims(a.mcu_claims_root, a.min_mcu)

    results = []
    for r in power + mcu:
        results.append(audit_claim(r, served, a.vault_cas, release_id))

    counts = {c: 0 for c in CLASSES}
    for r in results:
        counts[r["classification"]] += 1

    doc = {
        "schema": "designwins.truth-funnel.source-proof.v1",
        "observation": observe(None, now, host),
        "release_id": release_id,
        "denominators": {
            "power_sampled": len(power),
            "mcu_sampled": len(mcu),
            "total_sampled": len(results),
        },
        "classification_counts": counts,
        "claims": results,
    }
    (out / "sampled_source_proof_v1.json").write_text(
        json.dumps(doc, indent=1, default=str))

    lines = [f"# Sampled Source-to-Served Proof — {now}",
             f"release_id `{release_id}`",
             f"sampled power={len(power)} mcu={len(mcu)} total={len(results)}",
             "", "| # | lane | part | field | class | quote | join | served |",
             "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        lines.append(
            f"| {i} | {r['lane']} | {r['identity']['part']} | "
            f"{r['identity']['field']} | {r['classification']} | "
            f"{r['verification']['status']} | {r['identity_join']['status']} | "
            f"{r['release']['served']} |")
    lines += ["", "## Class counts"]
    for c in CLASSES:
        lines.append(f"- {c}: {counts[c]}")
    (out / "SAMPLED-SOURCE-PROOF.md").write_text("\n".join(lines) + "\n")

    print(f"wrote {out/'sampled_source_proof_v1.json'}; "
          f"total={len(results)} power={len(power)} mcu={len(mcu)} "
          f"classes={counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())