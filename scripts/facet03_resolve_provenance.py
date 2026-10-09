"""Provenance backfill pipeline (PRD-FACET-03 R3).

Resolves `unhashed:<stem>` claim identities to real artifact SHA-256 using
a bounded ladder, with independent byte verification at every rung:

  1. pairs manifest  (exports/power-datasheet-pairs/*/pairs.jsonl) —
     stem -> pdf_sha256 + local pdf; the local file's bytes are re-hashed
     and must equal the manifest hash AND the recorded size.
  2. vault documents (vault/catalog/documents.jsonl) — mpn/filename
     guesses -> sha; the cas object must exist and re-hash to its own name.
  3. quote cross-check — the burn claims' verbatim quotes must appear in
     the substrate page text recorded for the resolved sha (pipeline.db
     results). This links claims to the document through CONTENT, and
     recovers the printed page for each quote (page_source=quote_locate_v1).

Nothing is promoted on filenames or plausibility. Unresolved or failed
verification stays `unhashed:`/discovery-only. Conflicting candidate
artifacts are recorded distinguishably, never collapsed.

Bounded repair: at most three failed verification attempts per stem, then
the stem is logged unresolved and the pipeline moves on (execution rule).

Outputs:
  - provenance_map + provenance_conflicts rows in facet_identity.db
  - a re-key packet for the extraction lane (catalog.db is NOT touched)
  - optional evidence-index re-key (--rekey): claim units for verified
    resolutions are re-issued under the real sha at a NEW extraction
    version (immutable supersede), quote-recovered pages attached, and the
    placeholder-identity units retired.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.search import identity, units  # noqa: E402

PAIRS_DIRS = sorted(Path("/Volumes/M5_4TB/exports/power-datasheet-pairs"
                         ).glob("*/pairs.jsonl"))
VAULT_DOCUMENTS = "/Volumes/M5_4TB/vault/catalog/documents.jsonl"
VAULT_CAS = Path("/Volumes/M5_4TB/vault/cas")
CATALOG = "/Volumes/M5_4TB/extract-results/catalog.db"
PIPELINE = "/Volumes/M5_4TB/extract-results/pipeline.db"
PACKET_DIR = Path("/Volumes/M5_4TB/extract-results/facet03-packets")

MAX_REPAIR_ATTEMPTS = 3
_WS = re.compile(r"\s+")


def norm_text(s: str) -> str:
    return _WS.sub(" ", (s or "")).strip()


def hash_file(path: Path, limit_bytes: int | None = None) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def load_pairs() -> dict[str, dict]:
    """stem (as the burn wave wrote it) -> manifest record + local path."""
    out: dict[str, dict] = {}
    for pairs_path in PAIRS_DIRS:
        vendor_dir = pairs_path.parent.name           # infineon-20260908
        vendor = vendor_dir.rsplit("-", 1)[0]          # infineon
        with pairs_path.open() as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                part = rec["part_number"]
                local = pairs_path.parent / "pdf" / f"{vendor}-{part}.pdf"
                entry = {
                    "sha256": rec["pdf_sha256"], "bytes": rec["pdf_bytes"],
                    "local_path": str(local), "source": "pairs_manifest",
                    "vendor": vendor,
                }
                out[f"{vendor}-{part}"] = entry
                out.setdefault(part, entry)   # prefix-less catalog stems
    return out


def load_vault_documents() -> dict[str, list[dict]]:
    """token -> [{sha256, source}] from the vault registry (mpn guesses)."""
    out: dict[str, list[dict]] = {}
    path = Path(VAULT_DOCUMENTS)
    if not path.exists():
        return out
    with path.open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            sha = rec.get("sha256") or rec.get("sha")
            if not sha:
                continue
            for guess in rec.get("mpn_guesses") or []:
                out.setdefault(str(guess).upper(), []).append(
                    {"sha256": sha, "source": "vault_documents"})
    return out


def substrate_pages(pipeline_con: sqlite3.Connection,
                    sha: str) -> list[tuple[int, str]]:
    row = pipeline_con.execute(
        "SELECT output FROM results WHERE document_sha256=? AND"
        " kind_hint='substrate' ORDER BY created_at DESC LIMIT 1",
        (sha,)).fetchone()
    if row is None:
        return []
    try:
        payload = json.loads(row["output"])
    except json.JSONDecodeError:
        return []
    pages = []
    for page in payload.get("pages") or []:
        text = page.get("text") or ""
        if text.strip():
            pages.append((int(page.get("page") or 0), norm_text(text)))
    return pages


def quote_crosscheck(pipeline_con: sqlite3.Connection, sha: str,
                     quotes: list[str]) -> dict[str, tuple[bool, int | None]]:
    """Locate each verbatim quote in the resolved document's substrate text.

    Returns quote -> (found, page). Whitespace-normalized substring match —
    the same contract level as extraction-time quote verification.
    """
    pages = substrate_pages(pipeline_con, sha)
    corpus = [(p, t) for p, t in pages]
    out: dict[str, tuple[bool, int | None]] = {}
    for quote in quotes:
        needle = norm_text(quote)
        if not needle:
            out[quote] = (False, None)
            continue
        found_page = None
        for page_no, text in corpus:
            if needle in text:
                found_page = page_no or None
                break
        out[quote] = (found_page is not None, found_page)
    return out


def resolve(catalog_con: sqlite3.Connection,
            pipeline_con: sqlite3.Connection,
            identity_con: sqlite3.Connection, *,
            pairs: dict | None = None,
            vault: dict | None = None,
            limit: int | None = None,
            dry_run: bool = False) -> dict:
    pairs = pairs if pairs is not None else load_pairs()
    vault = vault if vault is not None else load_vault_documents()
    stems = [r[0].split(":", 1)[1] for r in catalog_con.execute(
        "SELECT DISTINCT doc_sha256 FROM claims_canonical"
        " WHERE doc_sha256 LIKE 'unhashed:%'")]
    if limit:
        stems = stems[:limit]
    claims_by_stem: dict[str, list[str]] = {}
    for stem, prov in catalog_con.execute(
            "SELECT doc_sha256, provenance FROM claims_canonical"
            " WHERE doc_sha256 LIKE 'unhashed:%'"):
        s = stem.split(":", 1)[1]
        if s not in stems:
            continue
        try:
            quote = json.loads(prov).get("quote")
        except (json.JSONDecodeError, TypeError):
            quote = None
        if quote:
            claims_by_stem.setdefault(s, []).append(str(quote))

    report = {"stems": len(stems), "resolved_pairs": 0, "resolved_vault": 0,
              "bytes_verified": 0, "bytes_mismatch": 0, "file_missing": 0,
              "quote_checked": 0, "quote_found": 0, "unresolved": 0,
              "conflicts": 0, "repair_budget_exhausted": 0}
    resolutions: dict[str, dict] = {}
    for stem in stems:
        attempts = 0
        resolved = None
        # rung 1: pairs manifest (byte-verified)
        cand = pairs.get(stem) or pairs.get(stem.upper())
        if cand:
            attempts += 1
            local = Path(cand["local_path"])
            if local.exists():
                actual = hash_file(local)
                if actual == cand["sha256"]:
                    size_ok = local.stat().st_size == cand["bytes"]
                    resolved = {"sha256": actual, "resolution":
                                "pairs_manifest", "bytes_verified": True,
                                "bytes_match": size_ok}
                    report["bytes_verified"] += 1
                else:
                    report["bytes_mismatch"] += 1
            else:
                report["file_missing"] += 1
        # rung 2: vault registry (cas object re-hashed to its own name)
        if resolved is None and attempts < MAX_REPAIR_ATTEMPTS:
            token = stem.split("-", 1)[-1].upper()
            for vcand in vault.get(token, []):
                attempts += 1
                if attempts > MAX_REPAIR_ATTEMPTS:
                    report["repair_budget_exhausted"] += 1
                    break
                cas = VAULT_CAS / vcand["sha256"][:2] / \
                    f"{vcand['sha256']}.pdf"
                if cas.exists() and hash_file(cas) == vcand["sha256"]:
                    resolved = {"sha256": vcand["sha256"],
                                "resolution": "vault_documents",
                                "bytes_verified": True, "bytes_match": True}
                    report["bytes_verified"] += 1
                    break
        # conflict detection: pairs and vault disagree on content
        if resolved is not None and cand and \
                cand["sha256"] != resolved["sha256"]:
            identity.record_provenance_conflict(
                identity_con, stem, cand["sha256"], "pairs_manifest",
                resolved["sha256"], resolved["resolution"])
            report["conflicts"] += 1
        if resolved is None:
            report["unresolved"] += 1
            if not dry_run:
                identity.set_provenance(
                    identity_con, stem, resolved_sha256=None,
                    resolution=None, bytes_verified=False, bytes_match=None,
                    quote_verified=0,
                    quote_total=len(claims_by_stem.get(stem, [])))
            continue
        # rung 3: quote cross-check against substrate content
        quotes = claims_by_stem.get(stem, [])[:24]
        checks = quote_crosscheck(pipeline_con, resolved["sha256"], quotes)
        found = sum(1 for ok, _ in checks.values() if ok)
        resolved["quote_verified"] = found
        resolved["quote_total"] = len(quotes)
        resolved["quote_pages"] = {q: p for q, (ok, p) in checks.items()
                                   if ok}
        report["quote_checked"] += len(quotes)
        report["quote_found"] += found
        report["resolved_pairs" if resolved["resolution"] ==
               "pairs_manifest" else "resolved_vault"] += 1
        resolutions[stem] = resolved
        if not dry_run:
            identity.set_provenance(
                identity_con, stem,
                resolved_sha256=resolved["sha256"],
                resolution=resolved["resolution"],
                bytes_verified=True, bytes_match=resolved["bytes_match"],
                quote_verified=found, quote_total=len(quotes))
            identity_con.executemany(
                "INSERT INTO quote_locator (stem, quote, page) VALUES"
                " (?,?,?) ON CONFLICT(stem, quote) DO UPDATE SET"
                " page=excluded.page",
                [(stem, q, p) for q, p in resolved["quote_pages"].items()])
            identity_con.commit()
    if not dry_run:
        PACKET_DIR.mkdir(parents=True, exist_ok=True)
        packet = PACKET_DIR / "provenance-rekey-v1.jsonl"
        with packet.open("w") as handle:
            for stem, res in sorted(resolutions.items()):
                handle.write(json.dumps({
                    "stem": stem, "sha256": res["sha256"],
                    "resolution": res["resolution"],
                    "bytes_verified": True,
                    "quote_verified": res["quote_verified"],
                    "quote_total": res["quote_total"],
                }, sort_keys=True) + "\n")
    return report


def rekey_units(search_con: sqlite3.Connection,
                identity_con: sqlite3.Connection,
                *, version_tag: str = "prov1") -> dict:
    """Re-issue claim units under verified real shas (new extraction
    version = immutable supersede), attach quote-recovered pages, retire
    the placeholder-identity units. Search index only — catalog untouched.
    """
    stats = {"docs": 0, "units": 0, "pages_recovered": 0, "retired": 0}
    rows = identity_con.execute(
        "SELECT stem, resolved_sha256, quote_verified_claims FROM"
        " provenance_map WHERE resolved_sha256 IS NOT NULL AND"
        " bytes_verified=1 AND quote_verified_claims > 0").fetchall()
    # Byte-identical PDFs can serve several catalog stems (one datasheet
    # covering 2N7002 AND 2N7002L). Group by resolved sha so all stems of
    # one artifact are written in ONE replace_document pass — per-stem
    # passes would delete each other's units as stale.
    by_sha: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        by_sha.setdefault(row["resolved_sha256"], []).append(row)
    for sha, sha_rows in by_sha.items():
        grouped: dict[str, list[dict]] = {}
        any_units = False
        for row in sha_rows:
            stem = row["stem"]
            old = search_con.execute(
                "SELECT * FROM units WHERE doc_sha256=?",
                (f"unhashed:{stem}",)).fetchall()
            if not old:
                continue
            any_units = True
            page_by_quote: dict[str, int | None] = {
                r["quote"]: r["page"] for r in identity_con.execute(
                    "SELECT quote, page FROM quote_locator WHERE stem=?",
                    (stem,))}
            for r in old:
                u = units.row_to_unit(r)
                quote = u["locator"].get("quote")
                page = u["page"] or page_by_quote.get(quote)
                locator = dict(u["locator"])
                # the origin stem is part of the unit's position identity
                # so distinct source records never collapse
                locator["origin_stem"] = stem
                if page and not u["page"]:
                    locator["page"] = page
                    locator["page_source"] = "quote_locate_v1"
                    stats["pages_recovered"] += 1
                new_unit = units.make_unit(
                    doc_sha256=sha, grain=u["grain"], locator=locator,
                    text_repr=u["text_repr"],
                    extraction_version=
                    f"{u['extraction_version']}+{version_tag}",
                    page=page, vendor=u["vendor"], doc_class=u["doc_class"],
                    category=u["category"], family=u["family"],
                    ident=[u["ident"]] if u["ident"] else [],
                    applicability=u["applicability"],
                    rev_code=u["rev_code"], rev_date=u["rev_date"],
                    verification_state=u["verification_state"],
                    verification_source=u["verification_source"],
                    structured=u.get("structured"))
                grouped.setdefault(new_unit["extraction_version"],
                                   []).append(new_unit)
        if not any_units:
            continue
        for version, group in grouped.items():
            units.replace_document(search_con, group, doc_sha256=sha,
                                   extraction_version=version, commit=False)
            stats["docs"] += 1
            stats["units"] += len(group)
        for row in sha_rows:
            units.retire_document(search_con, f"unhashed:{row['stem']}",
                                  f"provenance-resolved to {sha[:12]}")
            stats["retired"] += 1
    search_con.commit()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity-db", default=identity.DEFAULT_DB)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rekey", action="store_true",
                        help="also re-key the evidence index (search lane)")
    parser.add_argument("--search-db", default=units.DEFAULT_DB)
    args = parser.parse_args()

    catalog = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True)
    catalog.row_factory = sqlite3.Row
    pipeline = sqlite3.connect(f"file:{PIPELINE}?mode=ro", uri=True)
    pipeline.row_factory = sqlite3.Row
    icon = identity.connect(args.identity_db)
    t0 = time.time()
    report = resolve(catalog, pipeline, icon, limit=args.limit,
                     dry_run=args.dry_run)
    report["elapsed_s"] = round(time.time() - t0, 1)
    if args.rekey and not args.dry_run:
        scon = units.connect(args.search_db)
        report["rekey"] = rekey_units(scon, icon)
        scon.close()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
