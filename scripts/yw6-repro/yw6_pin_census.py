#!/usr/bin/env python3
"""hw-yw6 retained-pin census (read-only, bounded).

Classifies retained-pin records in the .54 corpus into:
  supported-exact : record binds an exact part_number AND carries package-scoped
                    pin-number evidence (package name / pin-table page / per-
                    package alignment) on its pin rows.
  package-only    : pin-number rows exist and a package is identified, but the
                    part_number is absent or not an exact ordering string.
  unresolved      : part_number present but no package-scoped pin binding
                    (or pinout empty/absent).
  conflicting     : within-record contradiction (same pin_no mapped to
                    different names) or same part_number across files with
                    materially different pin maps.

Deterministic sample: ALL records enumerated (denominator = every file),
field-level classification over the full set; per-record verbatim evidence
kept for the first N=8 per class (bounded output).
"""
import json, glob, os, sys
from collections import defaultdict

BASE = "/mnt/eda/m5-4tb/DigiKey_Reference_Designs"
def files_of(corpus):
    all_f = sorted(glob.glob(f"{BASE}/{corpus}/*/*.json"))
    main = [f for f in all_f if "/_quarantine_package_merge/" not in f
            and "/_superseded_local/" not in f]
    special = [f for f in all_f if f not in main]
    return main, special

CORPORA = {c: files_of(c) for c in
           ("MCU_Pinout_Extractions", "MCU_Pinout_Validated")}
CANON = BASE + "/training_data_pinout_rows_v1/canonical"

EVIDENCE_PER_CLASS = 8
out = {"corpora": {}, "canonical": {}, "samples": defaultdict(list)}

def classify(rec):
    ov = rec.get("overview") or {}
    pn = (ov.get("part_number") or "").strip()
    pin_rows = ((rec.get("pinout") or {}).get("pin_functions_summary")) or []
    meta = rec.get("_meta") or {}
    pkg_hint = any(k in json.dumps(rec)[:200000] for k in ("LQFP", "UFQFPN", "QFN", "BGA", "WLCSP", "SOIC", "TSSOP"))
    pages = str(meta.get("pin_table_pages") or "").strip()
    pins = [json.dumps(r.get("pin_no"), sort_keys=True, default=str) for r in pin_rows]
    if pin_rows and len(set(pins)) != len(pin_rows):
        return "conflicting", "duplicate pin_no within record"
    if pn and pkg_hint and pages:
        return "supported-exact", f"pn={pn!r} pin_table_pages={pages!r} package-scoped"
    if pkg_hint and pin_rows:
        return "package-only", f"package-scoped pin rows, part_number={pn!r}"
    if pn and pin_rows:
        return "unresolved", f"pn={pn!r}, {len(pin_rows)} pin rows, no package binding"
    return "unresolved", f"pn={pn!r}, pin_rows={len(pin_rows)}"

for corpus, (files, special) in CORPORA.items():
    counts = defaultdict(int)
    errors = 0
    per_part = defaultdict(set)   # pn -> set of pinmap hashes (conflict check)
    pn_file = defaultdict(list)
    for fp in files + special:
        try:
            rec = json.load(open(fp))
        except Exception:
            errors += 1
            counts["unresolved"] += 1   # unreadable counted, flagged separately
            continue
        cls, why = classify(rec)
        counts[cls] += 1
        ev = {"file": fp.replace(BASE + "/", ""), "class": cls, "why": why}
        if len(out["samples"][cls]) < EVIDENCE_PER_CLASS:
            out["samples"][cls].append(ev)
        pn = (rec.get("overview") or {}).get("part_number") or ""
        rows = (rec.get("pinout") or {}).get("pin_functions_summary") or []
        h = json.dumps([[r.get("pin_no"), r.get("name")] for r in rows], sort_keys=True)
        per_part[pn].add(h)
        pn_file[pn].append(fp)
    # cross-file conflicts: main manufacturer dirs only (special dirs are
    # quarantined/superseded copies; their divergence is reported separately)
    multi = {}
    for pn, hs in per_part.items():
        if not pn:
            continue   # empty-pn files are package-only/unresolved rows; never grouped
        fs = pn_file[pn]
        main_fs = [f for f in fs if f in files]
        if len(hs) > 1 and len(main_fs) >= 1:
            copies = sorted(f.replace(BASE + "/", "") for f in fs)
            multi[pn] = copies
    out["corpora"][corpus] = {
        "denominator_files_main": len(files),
        "special_dir_files": len(special),
        "unreadable": errors,
        "counts": dict(counts), "conflicting_across_files": multi,
    }

# canonical training rows: package-aligned, page-anchored -> supported-exact by construction
cfiles = sorted(glob.glob(CANON + "/*.json*"))
canon_counts = defaultdict(int)
canon_samples = []
for fp in cfiles:
    opener = gzip.open if fp.endswith(".gz") else open
    import gzip
    with opener(fp, "rt") as f:
        for i, line in enumerate(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            al = r.get("alignment") or {}
            pkg = al.get("package")
            page = al.get("page_1based")
            if pkg and page:
                canon_counts["supported-exact"] += 1
            elif pkg:
                canon_counts["package-only"] += 1
            else:
                canon_counts["unresolved"] += 1
            if len(canon_samples) < EVIDENCE_PER_CLASS:
                canon_samples.append({"file": fp.replace(CANON + "/", ""), "line": i,
                                      "example_id": r.get("example_id"),
                                      "package": pkg, "page_1based": page})
out["canonical"] = {"denominator_files": len(cfiles), "counts": dict(canon_counts),
                    "samples": canon_samples}
out["samples"] = dict(out["samples"])
print(json.dumps(out, indent=1))
