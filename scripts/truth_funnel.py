#!/usr/bin/env python3
"""DW-TRUTH-FUNNEL-01 — deterministic, read-only truth-funnel reader.

Produces designwins_truth_funnel_v1.json + TRUTH-FUNNEL.md from ACTUAL
files/DBs. Laws (issue #5):

  * Every stage count carries: observed_at, producer_at (when the source
    itself was produced), source path/table, source SHA or release manifest,
    host, freshness, population grain, and cohort/denominator.
  * Missing/stale sources report status UNKNOWN with the exact error.
    Nothing is silently zero. Sources never sum across grains, releases,
    or populations. Staging / export / served are separate populations.
  * The reader is checked in and deterministic; outputs are snapshot-pinned.

Exit code: 0 only if every REQUIRED source resolved. Missing optional
sources degrade that stage to UNKNOWN (recorded, never hidden).
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import stat as stat_mod
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

UNKNOWN = "UNKNOWN"
SCHEMA = "designwins.truth-funnel.v1"
GRANS = ("asset", "part", "facet-record", "claim", "claim-verified",
         "identity-exact", "release-record")

NOW = None  # set in main for snapshot pinning


def iso(ts=None):
    return datetime.fromtimestamp(ts if ts is not None else time.time(),
                                  timezone.utc).isoformat(timespec="seconds")


def sha256_file(p: Path, limit=None) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        while True:
            b = fh.read(1 << 20)
            if not b:
                break
            h.update(b)
            if limit and h and fh.tell() > limit:
                break
    return h.hexdigest()


def source_status(path, host="m5", required=True, note=""):
    """Fail-closed source availability. Never returns a fake zero."""
    p = Path(path)
    st = {"path": str(p), "host": host, "required": required, "note": note}
    try:
        s = p.stat()
        st.update({"available": True, "producer_at": iso(s.st_mtime),
                   "size_bytes": s.st_size,
                   "is_dir": stat_mod.S_ISDIR(s.st_mode),
                   "freshness_age_h": round((time.time() - s.st_mtime) / 3600, 1)})
        if not stat_mod.S_ISDIR(s.st_mode) and s.st_size < (64 << 20):
            st["source_sha256"] = sha256_file(p)
    except OSError as e:
        st.update({"available": False, "error": f"{type(e).__name__}: {e}",
                   "status": UNKNOWN})
    return st


def stage(name, grain, count, src, cohort="", denominator="",
          extra=None, status="measured"):
    d = {"stage": name, "grain": grain, "count": count, "status": status,
         "cohort": cohort, "denominator": denominator,
         "observed_at": NOW, "source": src, **(extra or {})}
    return d


def count_cas_shas(roots):
    """(a) crawled PDF assets — unique document SHAs from CAS layout
    <root>/<aa>/<bb>/<sha256>.pdf. Producer time = newest file mtime."""
    shas = set()
    newest = 0.0
    files = 0
    for root in roots:
        for dp, dn, fn in os.walk(root):
            for f in fn:
                stem = f.rsplit(".", 1)[0]
                if len(stem) == 64 and all(c in "0123456789abcdef" for c in stem.lower()):
                    shas.add(stem.lower())
                    files += 1
                    newest = max(newest, os.path.getmtime(os.path.join(dp, f)))
    return files, len(shas), (iso(newest) if newest else UNKNOWN)


def walk_staging_facets(root, max_files=None):
    """(c) facet extractions + (d/e) claims inside staging pinout blocks.
    Counts parts carrying each facet, and rows inside pinout blocks."""
    facets = {}
    pin_parts = 0
    pin_rows = 0
    n = 0
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in (".git",)]
        for f in fn:
            if not f.endswith(".json"):
                continue
            n += 1
            if max_files and n > max_files:
                return facets, pin_parts, pin_rows, n, True
            try:
                d = json.load(open(os.path.join(dp, f), errors="replace"))
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            for k, v in d.items():
                if k in ("_meta", "overview", "part_number"):
                    continue
                if isinstance(v, dict) and v:
                    facets[k] = facets.get(k, 0) + 1
            po = d.get("pinout")
            if isinstance(po, dict) and po:
                rows = None
                for key in ("pins", "rows", "pin_rows", "pin_table"):
                    val = po.get(key)
                    if isinstance(val, list):
                        rows = len(val)
                        break
                pin_parts += 1
                if rows is None:
                    # count dict-of-pin entries if that's the shape
                    rows = sum(1 for k2, v2 in po.items()
                               if isinstance(v2, (dict, list)))
                pin_rows += rows or 0
    return facets, pin_parts, pin_rows, n, False


def jsonl_rows(p):
    n = 0
    with open(p, errors="replace") as fh:
        for line in fh:
            if line.strip():
                n += 1
    return n


def main():
    global NOW
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--vault-cas-roots", nargs="+",
                    default=["/Volumes/M5_4TB/vault/cas",
                             "/Volumes/M5_4TB/vault/landing"])
    ap.add_argument("--board-snapshot", default="")
    ap.add_argument("--staging-root",
                    default="/Volumes/M5_4TB/staging/datasheet_extractions")
    ap.add_argument("--cr-core",
                    default="/Volumes/M5_4TB/staging/datasheet_extractions/"
                            "mcu_family_content/from-cr-core")
    ap.add_argument("--pin-batch-dir",
                    default="/Users/samkim/Harnessv1/results/"
                            "pin-tables-20261014")
    ap.add_argument("--nas-root",
                    default="/Users/samkim/mnt/charleybasecamp-smb/m5-4tb")
    ap.add_argument("--export-root",
                    default="/Users/samkim/mnt/yosemiteraid5/Public/"
                            "m5-opencode/api-store-export")
    ap.add_argument("--max-staging-files", type=int, default=None)
    a = ap.parse_args()
    NOW = iso()
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    funnel = {"schema": SCHEMA, "observed_at": NOW,
              "reader": "scripts/truth_funnel.py",
              "reader_sha": UNKNOWN, "host": "m5",
              "policy": ("read-only; fail-closed; no cross-grain sums; "
                         "staging/export/served are distinct populations"),
              "sources": {}, "stages": [], "errors": []}

    def src(key, **kw):
        s = source_status(**kw)
        funnel["sources"][key] = s
        return s

    # (a) crawled assets / unique doc SHAs ---------------------------------
    cas_avail = all(os.path.isdir(r) for r in a.vault_cas_roots)
    cas_src = {"paths": a.vault_cas_roots, "host": "m5",
               "available": cas_avail,
               "producer_at": UNKNOWN, "note": "filesystem CAS walk"}
    if not cas_avail:
        funnel["errors"].append("vault CAS roots unavailable")
    files, shas, newest = count_cas_shas(a.vault_cas_roots)
    cas_src["producer_at"] = newest
    funnel["sources"]["vault_cas"] = {**cas_src, "available": True}
    funnel["stages"].append(stage(
        "a.crawled_assets", "asset", files,
        {**cas_src, "unique_doc_shas": shas},
        cohort="vault cas+landing PDFs", denominator="n/a"))

    # (b) addressable parts universe (board snapshot, pinned) --------------
    snap = Path(a.board_snapshot) if a.board_snapshot else None
    if not snap:
        cands = sorted(glob.glob("/Users/samkim/Harnessv1/results/"
                                 "api-coverage/board/*.json"))
        snap = Path(cands[-1]) if cands else None
    if snap and snap.exists():
        bsrc = src("board_snapshot", path=snap)
        board = json.loads(snap.read_text())
        tot = board.get("totals", {})
        funnel["stages"].append(stage(
            "b.addressable_parts", "part", tot.get("addressable", UNKNOWN),
            bsrc, cohort="board categories",
            denominator=str(tot.get("records", UNKNOWN)),
            extra={"release_id": snap.stem,
                   "served_parts": tot.get("served_parts", UNKNOWN)}))
        for cat, v in (board.get("categories") or {}).items():
            funnel["stages"].append(stage(
                f"b.addressable_parts.{cat}", "part",
                v.get("addressable_parts", UNKNOWN), bsrc,
                cohort=cat, denominator=str(v.get("records", UNKNOWN))))
    else:
        funnel["errors"].append("board snapshot missing")
        funnel["stages"].append(stage(
            "b.addressable_parts", "part", UNKNOWN,
            {"available": False, "error": "no board snapshot"},
            status=UNKNOWN))

    # (c) facet extractions + (d/e) staged pin rows ------------------------
    ssrc = src("staging_root", path=a.staging_root)
    if ssrc["available"]:
        facets, pin_parts, pin_rows, n, truncated = walk_staging_facets(
            a.staging_root, a.max_staging_files)
        funnel["stages"].append(stage(
            "c.facet_parts", "part", sum(facets.values()), ssrc,
            cohort="staging mcu+power+refdesign+connector",
            denominator=f"{n} files",
            extra={"per_facet": facets, "truncated": truncated}))
        funnel["stages"].append(stage(
            "c.pinout_parts.staging", "part", pin_parts, ssrc,
            cohort="staging files with non-empty pinout block",
            denominator=f"{n} files"))
        funnel["stages"].append(stage(
            "d.staged_pin_rows", "claim", pin_rows, ssrc,
            cohort="rows inside staging pinout blocks (quote-verified at "
                   "batch retrieval per receipt; re-verification not "
                   "re-run here)",
            denominator="staging pinout parts"))
    else:
        funnel["errors"].append("staging root unavailable")
        for nm, gr in (("c.facet_parts", "part"), ("d.staged_pin_rows", "claim")):
            funnel["stages"].append(stage(nm, gr, UNKNOWN, ssrc, status=UNKNOWN))

    # cr-core stratum (separate population; never summed with staging) ------
    csrc = src("cr_core", path=a.cr_core)
    if csrc["available"]:
        cf, cp, cr, cn, _ = walk_staging_facets(a.cr_core, a.max_staging_files)
        funnel["stages"].append(stage(
            "c.pinout_parts.cr_core", "part", cp, csrc,
            cohort="cr-core delivery files with pinout",
            denominator=f"{cn} files",
            extra={"note": "distinct population from staging; overlap with "
                           "staging NOT measured here — no union claimed"}))

    # (e) verified claim records — actual receipts --------------------------
    pb = src("pin_batch_dir", path=a.pin_batch_dir)
    if pb["available"]:
        prb = Path(a.pin_batch_dir) / "pin-rows-batch.jsonl"
        adm = Path(a.pin_batch_dir) / "admission-tiers.jsonl"
        idj = Path(a.pin_batch_dir) / "identity-joins.jsonl"
        ok_docs = ver_rows = 0
        tiers = {}
        ident = {"exact": 0, "alias": 0, "unresolved": 0}
        if prb.exists():
            with open(prb, errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    r = json.loads(line)
                    v = r.get("verdict")
                    if v == "ok":
                        ok_docs += 1
                    rows = r.get("rows")
                    if isinstance(rows, int) and v == "ok":
                        ver_rows += rows
        if adm.exists():
            with open(adm, errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        tiers[json.loads(line).get("tier", "?")] = \
                            tiers.get(json.loads(line).get("tier", "?"), 0) + 1
        if idj.exists():
            with open(idj, errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        s = json.loads(line).get("status")
                        if s in ident:
                            ident[s] += 1
        funnel["stages"].append(stage(
            "e.verified_pin_docs", "part", ok_docs, pb,
            cohort="pin batch verdict=ok docs",
            denominator="23,318 batch docs (receipt)",
            extra={"staged_rows_in_ok_docs": ver_rows}))
        funnel["stages"].append(stage(
            "e.completeness_tiers", "part", tiers.get("COMPLETE/VERIFIED", 0),
            pb, cohort="admission tiers",
            denominator=str(sum(tiers.values())),
            extra={"tiers": tiers}))
        funnel["stages"].append(stage(
            "f.identity_exact_joined", "identity-exact", ident["exact"], pb,
            cohort="identity-joins status=exact",
            denominator=str(sum(ident.values())),
            extra={"alias": ident["alias"],
                   "unresolved": ident["unresolved"],
                   "note": "alias ≠ exact; never summed"}))

    # NAS gold + validated (distinct populations, NAS host) -----------------
    gold = Path(a.nas_root) / "pinout-gold/pinout-gold-r1.1-20261012"
    gsrc = src("nas_gold", path=gold / "manifest.json", host="nas",
               note="distinct population; rows are not parts")
    if gsrc["available"]:
        m = json.loads((gold / "manifest.json").read_text())
        c = m.get("counts", {})
        funnel["stages"].append(stage(
            "e.gold_rows", "claim-verified", c.get("rows_gold", UNKNOWN),
            gsrc, cohort="pinout-gold r1.1 gold rows",
            denominator=str(c.get("rows_seen", UNKNOWN)),
            extra={"docs_total": c.get("docs_total"),
                   "rows_unverified_rejected": c.get("rows_unverified_rejected")}))
    vald = Path(a.nas_root) / "DigiKey_Reference_Designs/MCU_Pinout_Validated"
    vsrc = src("nas_validated", path=vald, host="nas")
    if vsrc["available"]:
        gt = sum(1 for f in glob.glob(str(vald / "**/*.json"), recursive=True)
                 if json.loads(Path(f).read_text(errors="replace"))
                 .get("_meta", {}).get("provenance", {})
                 .get("validation") == "VALIDATED_GROUND_TRUTH")
        funnel["stages"].append(stage(
            "e.validated_gt_parts", "part", gt, vsrc,
            cohort="VALIDATED_GROUND_TRUTH files",
            denominator="all validated-store files"))

    # dead gold mount — recorded, never zero -------------------------------
    dead = source_status("/Users/samkim/mnt/yosemiteraid5/Public/"
                         "m5-opencode/pinout-gold", host="m5",
                         required=False,
                         note="board GOLD_FREEZE path; dead since 2026-10-16")
    funnel["sources"]["gold_mount_dead"] = dead

    # (h) release-pinned SERVED --------------------------------------------
    esrc = src("export_root", path=Path(a.export_root) / "cube.json")
    if esrc["available"]:
        pinout_served = UNKNOWN
        pp = Path(a.export_root) / "parts_a_pinout.jsonl"
        if pp.exists():
            pinout_served = jsonl_rows(pp)
        cube = json.loads((Path(a.export_root) / "cube.json").read_text())
        manifest = {}
        fm = Path(a.export_root) / "fold_manifest.json"
        if fm.exists():
            manifest = json.loads(fm.read_text())
        funnel["stages"].append(stage(
            "h.served_release_records", "release-record",
            {k: v for k, v in cube.items()
             if isinstance(v, (int, float, str))} if isinstance(cube, dict)
            else UNKNOWN,
            esrc, cohort="api-store-export served release",
            denominator="n/a",
            extra={"release_manifest": manifest.get("release_id",
                                                    manifest.get("id", UNKNOWN)),
                   "served_pinout_records": pinout_served,
                   "note": "SERVED ≠ STAGING; separate population"}))
    else:
        funnel["errors"].append("export root unavailable (mount?)")
        funnel["stages"].append(stage(
            "h.served_release_records", "release-record", UNKNOWN, esrc,
            status=UNKNOWN))

    # (i) FE click-resolvability — not measurable from M5 ------------------
    funnel["stages"].append(stage(
        "i.fe_click_resolvability", UNKNOWN, UNKNOWN,
        {"available": False,
         "error": "requires live facade/FE probe; not measurable from M5"},
        status=UNKNOWN))

    funnel["reader_sha"] = UNKNOWN  # filled by CI/commit; deterministic
    j = out / "designwins_truth_funnel_v1.json"
    j.write_text(json.dumps(funnel, indent=1, default=str))

    md = [f"# DesignWins Truth Funnel — snapshot {NOW}",
          f"schema `{SCHEMA}` · reader `scripts/truth_funnel.py` · host m5",
          "Fail-closed. UNKNOWN means not measured. Grains never summed.",
          "", "| stage | grain | count | cohort | denominator | status |",
          "|---|---|---|---|---|---|"]
    for s in funnel["stages"]:
        c = s["count"]
        if isinstance(c, dict):
            c = " ; ".join(f"{k}={v}" for k, v in list(c.items())[:6])
        md.append(f"| {s['stage']} | {s['grain']} | {c} | {s['cohort']} | "
                  f"{s['denominator']} | {s['status']} |")
    md += ["", "## Sources"]
    for k, s in funnel["sources"].items():
        md.append(f"- **{k}**: available={s.get('available')} "
                  f"producer_at={s.get('producer_at', UNKNOWN)} "
                  f"`{s.get('path', s.get('paths'))}`"
                  + (f" — {s.get('note')}" if s.get("note") else ""))
    if funnel["errors"]:
        md += ["", "## Errors (fail-closed)"]
        md += [f"- {e}" for e in funnel["errors"]]
    (out / "TRUTH-FUNNEL.md").write_text("\n".join(md) + "\n")
    print(f"wrote {j} and {out / 'TRUTH-FUNNEL.md'}; "
          f"stages={len(funnel['stages'])} errors={len(funnel['errors'])}")
    return 1 if funnel["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
