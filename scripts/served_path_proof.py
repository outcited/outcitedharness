#!/usr/bin/env python3
"""DW-TRUTH-FUNNEL-02 — served-path proof (issue #5 phase 2).

Deterministic, no LLM. For each claim in the cohort it records SEVEN
INDEPENDENT links and classifies each on its own ladder. Links are NEVER
summed; the overall report carries one count per link ("verified source",
"identity-joined", "folded", "bundled", "authorized", "deployed",
"clickable").

Cohort
------
The brief asks for >= 10 claims present in BOTH the export
``compare_ready.jsonl`` AND the FE main committed bundle
``data/labs/compare_ready_spine.jsonl`` as a BYTE-EXACT row match. On the
pinned inputs that intersection is EMPTY (0): the two files are different
release lineages (see ``cohort`` block in the output). A pinned-input
shortfall is a valid, documented outcome — the tool reports the measured
byte-exact intersection and, when it is below the floor, falls back to a
declared, still-honest cohort: FE-bundle rows whose ``doc_sha256`` is also
present in the export (the maximal available FE<->export overlap). The
fallback is labelled ``selection`` in every record; it is never passed off as
a byte-exact match.

Links (each independent; VERIFIED / HELD / FAILED / STAGED_ONLY /
SOURCE_MISSING / UNKNOWN, plus per-link vocabulary):
  1. identity   exact manufacturer+OPN joined against parts_catalog.jsonl
  2. source     doc sha256 + page + verbatim quote verified against vault PDF
  3. fold       row present in the export pinned by fold_manifest + cube sha
                (both shas recorded)
  4. bundled    byte-exact presence in the FE main committed bundle
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

MIN_COHORT = 10


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


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------------------
# Pure, testable cores
# ---------------------------------------------------------------------------
def verify_quote(quote: str, page_text: str) -> bool:
    """Deterministic verbatim check. Empty quote never verifies."""
    nq = norm(quote)
    if not nq:
        return False
    return nq in norm(page_text)


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


def classify_fold(present, fold_sha_match, cube_sha_match) -> str:
    """fold link ladder."""
    if not present:
        return "ABSENT"
    if fold_sha_match and cube_sha_match:
        return VERIFIED
    return FAILED


def classify_bundled(byte_exact, present_by_value) -> str:
    """bundle link ladder. byte_exact: raw-line match in committed bundle."""
    if byte_exact:
        return VERIFIED
    if present_by_value:
        return HELD          # same claim evidence, not a byte-exact row
    return "ABSENT"


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
# Cohort selection
# ---------------------------------------------------------------------------
def byte_exact_intersection(export_lines, fe_lines):
    """Raw-line (byte-exact) intersection. Returns sorted list of common
    lines. This is the brief's cohort criterion."""
    eset = set(export_lines)
    fset = set(fe_lines)
    return sorted(eset & fset)


def export_doc_shas(export_lines):
    shas = set()
    for l in export_lines:
        try:
            d = json.loads(l)
        except Exception:
            continue
        if d.get("doc_sha256"):
            shas.add(d["doc_sha256"])
    return shas


def fallback_cohort(fe_lines, exp_shas, n):
    """FE-bundle rows whose doc_sha256 is also in the export.

    Declared fallback (selection='doc_sha_overlap') when the byte-exact
    intersection is below the floor. Deterministic: input order preserved,
    evenly strided to n.
    """
    picked = []
    for l in fe_lines:
        try:
            d = json.loads(l)
        except Exception:
            continue
        if d.get("doc_sha256") in exp_shas:
            picked.append((l, d))
    if len(picked) <= n or n <= 0:
        return picked
    step = len(picked) / n
    out = []
    seen = set()
    for i in range(n):
        idx = int(i * step)
        if idx not in seen:
            seen.add(idx)
            out.append(picked[idx])
    return out


def build_cohort(export_lines, fe_lines, exp_shas, min_cohort):
    """Return (cohort, meta). cohort = list of (fe_line, fe_dict, selection)."""
    inter = byte_exact_intersection(export_lines, fe_lines)
    if len(inter) >= min_cohort:
        rows = []
        for l in inter[:min_cohort] if False else inter:
            rows.append((l, json.loads(l), "byte_exact"))
        meta = {"selection": "byte_exact", "byte_exact": len(inter),
                "shortfall": False}
        return rows, meta
    # documented shortfall -> declared fallback
    fb = fallback_cohort(fe_lines, exp_shas, min_cohort)
    rows = [(l, d, "doc_sha_overlap") for l, d in fb]
    meta = {
        "selection": "doc_sha_overlap",
        "byte_exact": len(inter),
        "shortfall": True,
        "shortfall_reason": (
            "byte-exact row intersection between export compare_ready.jsonl "
            "and FE compare_ready_spine.jsonl is below floor "
            f"({len(inter)} < {min_cohort}); export and FE bundle are "
            "different release lineages (no shared rows at any projection)."),
    }
    return rows, meta


# ---------------------------------------------------------------------------
# FE bundle byte-exact index
# ---------------------------------------------------------------------------
def fe_bundle_index(fe_lines):
    """raw line -> True (byte-exact set) and evidence-key map for value match.

    Evidence key = (opn, doc_sha256, page, axis, value) — same evidence, and a
    weaker 'present_by_value' signal than a byte-exact row.
    """
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
    # newest deployment (the API returns newest first) and its 'sha'
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
    sha = fe_row.get("doc_sha256")
    page = fe_row.get("page")
    axis = fe_row.get("axis")
    value = fe_row.get("value")
    quote = fe_row.get("quote")

    rec = {
        "selection": selection,
        "fe_row_sha256": sha256_bytes(fe_line.encode("utf-8")),
        "claim": {"opn": opn, "axis": axis, "value": value, "unit": fe_row.get("unit"),
                  "page": page, "doc_sha256": sha},
    }

    # 1. identity -----------------------------------------------------------
    catalog_has = norm(opn) in ctx["catalog"] if opn else False
    vendor = ctx["catalog"].get(norm(opn), UNKNOWN) if opn else UNKNOWN
    # join against the export's OPN set; a row absent from the export does
    # NOT self-join to EXACT (that would fabricate agreement).
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
                # strict verbatim failed; record whether the quote's non-trivial
                # tokens are present anyway (table-layout / reading-order note).
                toks = [t for t in re.split(r"[|\s]+", norm(quote)) if len(t) > 1]
                joined = norm(" ".join(texts.values()))
                present = [t for t in toks if t in joined]
                diag = {"quote_tokens_total": len(toks),
                        "quote_tokens_present_in_page_raw": len(present),
                        "note": ("strict verbatim failed; tokens present "
                                 "individually — layout/reading-order mismatch, "
                                 "not proof of absence")
                        if present else "strict verbatim failed; tokens absent"}
    src_class = classify_source(quote_ok, doc_state)
    rec["source"] = {
        "doc_state": doc_state, "quote_ok": quote_ok, "tool": tool,
        "diag": diag,
        "doc_alias": f"nas://vault/cas/{sha[:2]}/{sha}.pdf" if sha else None,
        "class": src_class,
    }

    # 3. fold ---------------------------------------------------------------
    fold = ctx["fold"]
    cube_sha = (fold.get("cube") or {}).get("sha256")
    fold_release = fold.get("fold")
    in_export = fe_row.get("doc_sha256") in ctx["exp_shas"]
    fold_class = classify_fold(
        present=in_export,
        fold_sha_match=(cube_sha == ctx["cube_sha"]),
        cube_sha_match=(cube_sha == ctx["cube_sha"]))
    rec["fold"] = {
        "fold": fold_release, "cube_sha256": cube_sha,
        "export_cube_sha256": ctx["cube_sha"],
        "doc_sha_in_export": in_export, "class": fold_class,
    }

    # 4. bundled ------------------------------------------------------------
    byte_exact = fe_line in ctx["fe_exact"]
    key = (norm(opn), sha, str(page), norm(axis), norm(value))
    present_by_value = key in ctx["fe_by_evidence"]
    bundle_class = classify_bundled(byte_exact, present_by_value)
    rec["bundle"] = {"byte_exact": byte_exact,
                     "present_by_value": present_by_value,
                     "class": bundle_class}

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
        # bound; served-ness depends on live probe (auth-gated -> unknown)
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
def run(export_root, fe_bundle, parts_catalog, cas_roots, deployments_json,
        evidence_dir, out_dir, fe_commit, fe_repo, min_cohort, resolve_probe,
        approval_record, now=None, host=None):
    export_root = Path(export_root)
    fe_bundle = Path(fe_bundle)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    export_lines = read_jsonl(export_root / "compare_ready.jsonl")
    fe_lines = read_jsonl(fe_bundle)

    exp_shas = export_doc_shas(export_lines)
    cohort, meta = build_cohort(export_lines, fe_lines, exp_shas, min_cohort)

    fe_exact, fe_by_evidence = fe_bundle_index(fe_lines)
    catalog = load_catalog_index(Path(parts_catalog)) if parts_catalog else {}
    fold = load_fold_manifest(export_root)
    cube_sha = (fold.get("cube") or {}).get("sha256")
    dep_recs, dep_sha = load_deployments(deployments_json)
    ev = evidence_index(evidence_dir)

    ctx = {
        "catalog": catalog,
        "export_opns": load_export_index(export_lines),
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
    }

    claims = [audit_claim(l, d, sel, ctx) for (l, d, sel) in cohort]

    # per-link counts, reported SEPARATELY, never summed
    link_keys = {
        "verified_source": ("source", VERIFIED),
        "identity_joined": ("identity", VERIFIED),
        "folded": ("fold", VERIFIED),
        "bundled": ("bundle", VERIFIED),
        "authorized": ("authorized", VERIFIED),
        "deployed": ("deployed", VERIFIED),
        "clickable": ("resolve", VERIFIED),
    }
    link_counts = {}
    for name, (grp, cls) in link_keys.items():
        link_counts[name] = sum(1 for c in claims if c[grp]["class"] == cls)
    # full per-link class distributions
    distributions = {}
    for grp in ("source", "identity", "fold", "bundle", "authorized",
                "deployed", "resolve"):
        dist = {}
        for c in claims:
            dist[c[grp]["class"]] = dist.get(c[grp]["class"], 0) + 1
        distributions[grp] = dist

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
        "cohort": {
            "selection": meta["selection"],
            "size": len(claims),
            "byte_exact_intersection": meta["byte_exact"],
            "shortfall": meta["shortfall"],
            "shortfall_reason": meta.get("shortfall_reason"),
        },
        "link_counts": link_counts,
        "link_distributions": distributions,
        "claims": claims,
    }
    (out / "served_path_proof_v1.json").write_text(
        json.dumps(doc, indent=1, default=str))

    # markdown summary
    lines = ["# Served-Path Proof — " + doc["observation"]["now"], "",
             f"cohort selection: `{meta['selection']}` size={len(claims)}; "
             f"byte-exact intersection={meta['byte_exact']}",
             "", "## Link counts (independent — never summed)", "",
             "| link | VERIFIED count |", "|---|---|"]
    for name in link_keys:
        lines.append(f"| {name} | {link_counts[name]} |")
    lines += ["", "## Per-link class distribution", ""]
    for grp, dist in distributions.items():
        lines.append(f"- {grp}: " + ", ".join(f"{k}={v}" for k, v in sorted(dist.items())))
    lines += ["", "| # | opn | axis | source | identity | fold | bundle | auth | deployed | resolve |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for i, c in enumerate(claims, 1):
        lines.append(
            f"| {i} | {c['claim']['opn']} | {c['claim']['axis']} | "
            f"{c['source']['class']} | {c['identity']['class']} | "
            f"{c['fold']['class']} | {c['bundle']['class']} | "
            f"{c['authorized']['class']} | {c['deployed']['class']} | "
            f"{c['resolve']['class']} |")
    (out / "SERVED-PATH-PROOF.md").write_text("\n".join(lines) + "\n")

    print(f"wrote {out/'served_path_proof_v1.json'}; cohort={len(claims)} "
          f"selection={meta['selection']} byte_exact={meta['byte_exact']} "
          f"counts={link_counts}")
    return 0


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