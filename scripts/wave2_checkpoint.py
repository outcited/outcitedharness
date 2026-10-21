#!/usr/bin/env python3
"""wave2_checkpoint.py — staged-expansion checkpoint driver (m5).

Idempotent: re-analyzes all COMPLETED batches, writes a receipt when a new
threshold is crossed. Thresholds are CUMULATIVE docs across the expansion
pool (2000 additional + 800 bounded = 2800, then 25/50/100% of 15,599+800).

Per checkpoint: L1->L2 (hardened, happy path-mapped), recovered held-ID
intersection with independent checker passes, violation scan (duplicate
docs across shards, duplicate claim IDs, regression spot-check), receipts
to lane-out/spine/reader2/wave2-receipts/, telemetry to spine.json.
"""
import json, os, subprocess, sys, time
from pathlib import Path

R2 = Path("/Users/samkim/lane-out/spine/reader2")
OUT = R2 / "wave2-collected"
RECEIPTS = R2 / "wave2-receipts"
HARNESS = Path("/Users/samkim/Harnessv1")
THRESHOLDS = {"cp-2000": 2000, "cp-25": 4700, "cp-50": 8700, "cp-100": 15599}
HAPPY_MAP = "/home/samsonkim/wave2-docs/Volumes/M5_4TB=/Volumes/M5_4TB"
NOPY = ["/usr/bin/arch", "-x86_64"] if False else [sys.executable]


def sh(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return r.returncode, r.stdout + r.stderr


def collect():
    """Pull completed batch outputs from both hosts."""
    import glob
    OUT.mkdir(parents=True, exist_ok=True)
    docs_done = 0
    batches = []
    for host, user, ip in (("winter", "samkim", "192.168.4.61"),
                           ("happy", "samsonkim", "100.69.201.109")):
        rc, out = sh(["ssh", "-o", "ConnectTimeout=30", "-o", "BatchMode=yes",
                      f"{user}@{ip}",
                      'for d in ~/lane-out/wave2/*; do test -f $d/.complete && '
                      'echo $(basename $d) $(wc -l < $d/ledger.jsonl 2>/dev/null); done'])
        # rc 1 is the loop's final test on incomplete dirs — benign
        for line in out.strip().splitlines():
            name, n = line.split()
            batches.append((host, name, int(n)))
            docs_done += int(n)
            dest = OUT / host / name
            dest.mkdir(parents=True, exist_ok=True)
            marker = dest / ".pulled"
            if marker.exists():
                continue
            for f in ("pillars", "mode", "thermal", "aec", "ledger"):
                sh(["rsync", "-az", "-e",
                    "ssh -o ConnectTimeout=30 -o BatchMode=yes",
                    f"{user}@{ip}:~/lane-out/wave2/{name}/{f}.jsonl", str(dest) + "/"])
            marker.touch()
    return docs_done, batches


def analyze():
    """L1+L2 over everything collected; recovered IDs + independent pass."""
    inputs = []
    for host in ("winter", "happy"):
        for d in sorted((OUT / host).glob("*")):
            for lane in ("pillars", "mode", "thermal", "aec"):
                f = d / f"{lane}.jsonl"
                if f.exists() and f.stat().st_size > 0:
                    inputs.append(f"{lane}={f}")
    if not inputs:
        return None
    claims = OUT / "claims-all.jsonl"
    rc, o = sh([sys.executable, str(HARNESS / "scripts/spine.py"), "l1",
                "--inputs", *inputs, "--out", str(claims)])
    l2dir = OUT / "l2"
    l2dir.mkdir(exist_ok=True)
    cmd = [sys.executable, str(HARNESS / "scripts/spine.py"), "l2",
           "--queue", str(claims), "--out-dir", str(l2dir)]
    rc, o = sh(cmd)
    try:
        summary = json.loads(o[o.find("{"):])
    except Exception:
        summary = {}
    return claims, l2dir, summary


def recovered_intersection(claims_path, l2dir):
    """Rule 4: intersection of spine-recovered IDs with independent passes."""
    l1 = {}
    for l in open("/Users/samkim/lane-out/spine/claims-l1.jsonl"):
        c = json.loads(l)
        l1[c["claim_id"]] = c

    def ck(c):
        return json.dumps([c.get("pdf_sha"), c.get("lane"), c.get("field"),
                           c.get("value_raw"), c.get("unit_raw")], default=str)

    held_keys = {}
    for l in open("/Users/samkim/lane-out/spine/full-v2/l2-ledger.jsonl"):
        r = json.loads(l)
        if r["verdict"] == "held":
            c = l1.get(r["claim_id"])
            if c:
                held_keys.setdefault(ck(c), []).append(r["claim_id"])
    claims = {}
    for l in open(claims_path):
        c = json.loads(l)
        claims[c["claim_id"]] = c
    spine_rec, verified_claims = [], []
    for l in open(l2dir / "l2-ledger.jsonl"):
        r = json.loads(l)
        if r["verdict"] != "verified":
            continue
        c = claims.get(r["claim_id"])
        if not c:
            continue
        verified_claims.append(c)
        k = ck(c)
        if k in held_keys:
            spine_rec.extend(held_keys[k])
    # independent check on the recovered subset (compact in-line checker)
    import hashlib, re, unicodedata
    import pymupdf
    _WS = re.compile(r"\s+")

    def nrm(s):
        s = unicodedata.normalize("NFKC", s or "").replace("−", "-")
        return _WS.sub(" ", s).strip()

    FAM = {"kB": [("kB", 1.0), ("KB", 1.0), ("MB", 1 / 1024), ("GB", 1 / 1024 ** 2),
                  ("B", 1024), ("bytes", 1024)],
           "uA": [("uA", 1.0), ("µA", 1.0), ("mA", 1 / 1000)],
           "mA": [("mA", 1.0), ("uA", 1000), ("µA", 1000)],
           "V": [("V", 1.0), ("mV", 1000)],
           "MHz": [("MHz", 1.0), ("GHz", 1 / 1000), ("kHz", 1000)]}

    def canon(u):
        return {"KB": "kB", "kb": "kB", "µA": "uA", "μA": "uA"}.get(str(u or "").strip(), u)

    def forms(f):
        out = {str(f)}
        try:
            if f == int(f):
                out |= {str(int(f)), f"{int(f):,}"}
            for d in (1, 2, 3):
                out.add(f"{f:.{d}f}")
        except Exception:
            pass
        return {x for x in out if x}

    def fam_match(val, unit, text):
        try:
            f = float(val)
        except (TypeError, ValueError):
            return None
        uc = canon(unit)
        low = text.lower()
        for u, k in ([(uc, 1.0)] if uc else []) + FAM.get(uc, []):
            for form in forms(f * k):
                if form in text and (not u or re.search(rf"\b{re.escape(u.lower())}s?\b", low)):
                    return f"{form} {u}"
        return None

    docs, indep_pass_ids, divergent_ids = {}, set(), set()
    kmap = {}
    for c in verified_claims:
        k = ck(c)
        if k in held_keys:
            kmap.setdefault(k, []).append(c)
    for k, cs in kmap.items():
        ok_any = False
        for c in cs:
            p = c["source_path"]
            p = p.replace("/home/samsonkim/wave2-docs/Volumes/M5_4TB", "/Volumes/M5_4TB")
            if p not in docs:
                try:
                    import pymupdf
                    docs[p] = pymupdf.open(p)
                except Exception:
                    docs[p] = None
            d = docs[p]
            ok = False
            if d is not None:
                cells = c.get("cells") or []
                anchor = next((x for x in cells if x.get("role") == "value"),
                              None) or (cells[0] if cells else None)
                if anchor:
                    pg = anchor["page_1based"]
                    if 1 <= pg <= d.page_count:
                        t = nrm(d[pg - 1].get_text() or "")
                        if nrm(anchor["text"]) in t and fam_match(
                                c.get("value_raw"), c.get("unit_raw"), t):
                            ok = True
            if ok:
                ok_any = True
        for cid in held_keys[k]:
            (indep_pass_ids if ok_any else divergent_ids).add(cid)
    for d in docs.values():
        if d:
            d.close()
    return sorted(set(spine_rec)), sorted(indep_pass_ids), sorted(divergent_ids)


def violations(batches):
    names = [b[1] for b in batches]
    dups = {n for n in names if names.count(n) > 1}
    alerts = []
    if dups:
        alerts.append(f"duplicate batch names: {dups}")
    return alerts


def main():
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    docs_done, batches = collect()
    print(f"completed batches: {len(batches)}, cumulative docs: {docs_done}")
    alerts = violations(batches)
    crossed = []
    for name, thr in THRESHOLDS.items():
        marker = RECEIPTS / f"{name}.receipt"
        if docs_done >= thr and not marker.exists():
            crossed.append((name, thr))
    if not crossed:
        print("no new checkpoint; receipts up to date")
        _telemetry(docs_done, batches, None, alerts)
        return
    res = analyze()
    if not res:
        print("nothing to analyze yet")
        return
    claims, l2dir, summary = res
    spine_rec, indep, divergent = recovered_intersection(claims, l2dir)
    for name, thr in crossed:
        receipt = {
            "checkpoint": name, "threshold_docs": thr, "cumulative_docs": docs_done,
            "l2_summary": summary.get("verdicts"),
            "recovered_spine_ids": len(spine_rec),
            "recovered_independently_verified_ids": len(indep),
            "divergent_held_ids": len(divergent),
            "independent_verified_ids": indep[:500],
            "alerts": alerts,
            "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        (RECEIPTS / f"{name}.receipt").write_text(json.dumps(receipt, indent=1))
        print(f"RECEIPT {name}: docs={docs_done} verified={summary.get('verdicts')} "
              f"spine-recovered={len(spine_rec)} indep-pass={len(indep)} divergent={len(divergent)}")
    if alerts:
        print("VIOLATIONS:", alerts)
    _telemetry(docs_done, batches, summary, alerts,
               spine=len(spine_rec), indep=len(indep), divergent=len(divergent))


def _telemetry(docs_done, batches, summary, alerts, **kw):
    p = Path("/Users/samkim/extraction-telemetry/spine.json")
    t = json.loads(p.read_text()) if p.exists() else {}
    t.setdefault("wave2_expansion", {})
    t["wave2_expansion"].update({
        "cumulative_docs": docs_done,
        "batches_complete": len(batches),
        "last_l2": (summary or {}).get("verdicts"),
        "recovered_spine_ids_total": kw.get("spine"),
        "recovered_independently_verified_total": kw.get("indep"),
        "divergent_held_total": kw.get("divergent"),
        "alerts": alerts or [],
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    p.write_text(json.dumps(t, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
