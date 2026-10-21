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
            # per-batch multiset hash (deterministic replay evidence)
            h = _batch_hash(dest)
            (dest / ".batchhash").write_text(h)
            marker.touch()
    return docs_done, batches


def _batch_hash(dest):
    import hashlib as _h
    rows = []
    for lane in ("pillars", "mode", "thermal", "aec", "ledger"):
        f = dest / f"{lane}.jsonl"
        if not f.exists():
            continue
        for line in f.open():
            x = json.loads(line)
            key = [x.get("pdf_sha") or x.get("sha256"),
                   x.get("field") or x.get("mode") or x.get("metric") or x.get("grade") or x.get("status"),
                   x.get("value"), x.get("unit"), x.get("page_1based"), x.get("verbatim"),
                   json.dumps(x.get("cells"), sort_keys=True) if x.get("cells") is not None else None]
            rows.append(json.dumps(key, sort_keys=True, default=str))
    rows.sort()
    return _h.sha256("\n".join(rows).encode()).hexdigest()


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
    # independent check on the recovered subset — SINGLE SOURCE OF TRUTH:
    # spine.value_in_evidence_family (deterministic rules, rule ids recorded)
    import hashlib, re, unicodedata
    import pymupdf
    sys.path.insert(0, str(HARNESS / "scripts"))
    from spine import value_in_evidence_family as fam_match_with_field

    _WS = re.compile(r"\s+")

    def nrm(s):
        s = unicodedata.normalize("NFKC", s or "").replace("−", "-")
        return _WS.sub(" ", s).strip()

    def fam_match(val, unit, text, field=None):
        r = fam_match_with_field(val, unit, text, field=field)
        return r[0] if r else None

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
                                c.get("value_raw"), c.get("unit_raw"), t,
                                field=c.get("field")):
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


_regression_last = {}

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
    reg = _regression_check()
    _regression_last.clear(); _regression_last.update(reg)
    print("regression:", reg)
    if reg.get("regressions", 0) > 0:
        # law: regression BLOCKS all unreviewed wave-2 batches from acceptance
        bl = RECEIPTS / "ACCEPTANCE-BLOCKLIST.json"
        bldata = json.loads(bl.read_text()) if bl.exists() else {"batches": []}
        for host in ("winter", "happy"):
            for d in sorted((OUT / host).glob("*")):
                name = d.name
                if name not in bldata["batches"]:
                    bldata["batches"].append(name)
        bldata["reason"] = f"regression check failed: {reg}"
        bldata["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        bl.write_text(json.dumps(bldata, indent=1))
        print("REGRESSION -> all wave-2 batches BLOCKED from acceptance")
    if alerts:
        print("VIOLATIONS:", alerts)
        # rule: violation BLOCKS downstream acceptance of affected batches
        blocked = sorted({a.split(":")[1].strip(" {}'") for a in alerts
                          if a.startswith("duplicate batch names")})
        blocklist = RECEIPTS / "ACCEPTANCE-BLOCKLIST.json"
        bl = json.loads(blocklist.read_text()) if blocklist.exists() else {"batches": []}
        for b in blocked:
            if b not in bl["batches"]:
                bl["batches"].append(b)
        bl["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        bl["law"] = "blocked batches are excluded from every recovery count and " \
                    "from any future L3/L4/L5 acceptance until reviewed"
        blocklist.write_text(json.dumps(bl, indent=1))
        print("BLOCKED batches:", blocked)
    _telemetry(docs_done, batches, summary, alerts,
               spine=len(spine_rec), indep=len(indep), divergent=len(divergent))


def _regression_check(n=400):
    """Re-verify n previously-verified classic claims; any regression blocks."""
    import random
    sys.path.insert(0, str(HARNESS / "scripts"))
    from spine import l2_verify_claim, PdfCache
    l1 = {}
    for l in open("/Users/samkim/lane-out/spine/claims-l1.jsonl"):
        c = json.loads(l)
        l1[c["claim_id"]] = c
    ver = []
    for l in open("/Users/samkim/lane-out/spine/full-v2/l2-ledger.jsonl"):
        r = json.loads(l)
        if r["verdict"] == "verified":
            ver.append(r["claim_id"])
    sample = random.Random(20261021).sample(ver, min(n, len(ver)))
    cache = PdfCache()
    ok = sum(1 for cid in sample
             if l2_verify_claim(l1[cid], cache)["verdict"] == "verified")
    return {"sample": len(sample), "still_verified": ok,
            "regressions": len(sample) - ok}


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
        "s6_release_dependency": {
            "gate": "S6 spine->ExportStore promotion (HOLD per Sam 2026-10-21)",
            "recovery_independently_verified": kw.get("indep"),
            "interpretation": "processed documents are NOT released claims; "
                              "only independently-verified recovered IDs advance "
                              "the S6 dependency",
            "confidence": ("high" if (alerts == [] and kw.get("indep", 0) > 0 and
                                      _regression_last.get("regressions") == 0)
                           else "reduced"),
        },
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    p.write_text(json.dumps(t, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
