#!/usr/bin/env python3
"""PINOUT-IDENTITY-03 — deterministic identity join (slug -> canonical OPN).

Authority: the served export feeds (pinned SHAs) are the canonical identity
registry per the Store release owner. This joiner NEVER invents identities.

Rules (v1, evidence-backed, all deterministic):
  R1 stem-exact   cleaned manifest URL-stem == a served OPN               -> exact
  R2 canon-equal  uppercase/alphanumeric-normalized stem == normalized OPN -> alias

Guards (hard rejections — family/title docs never join):
  G1 stems containing datasheet/manual/errata/family/series/... tokens
  G2 stems that are not OPN-ish (charset/shape) after manufacturer-prefix
     and doc-id/locale suffix stripping
Every join row carries full provenance (original identity, manufacturer,
source sha, stem, rule, target OPN, tier). Unresolved rows are emitted with
status=unresolved and their bucket cause — never force-matched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

RULE_VERSION = "identity-join-v1"

TITLEY = re.compile(
    r"(datasheet|data-sheet|low-cost|high-speed|user|manual|errata|reference|"
    r"application|note|family|series|datasheet summary|product brief|"
    r"specification)", re.IGNORECASE)
OPNISH = re.compile(r"^[A-Z0-9][A-Z0-9\-/\.]{2,24}$", re.IGNORECASE)


def clean_stem(stem: str, manufacturer: str | None) -> str:
    """Deterministic URL-stem -> OPN-candidate cleaning (tested)."""
    s = stem
    mfr = (manufacturer or "").lower()
    if mfr and s.lower().startswith(mfr + "-"):
        s = s[len(mfr) + 1:]
    s = re.sub(r"[-_](en|de|zh|jp|fr)$", "", s, flags=re.I)
    s = re.sub(r"[-_]rev\d+(\.\d+)?$", "", s, flags=re.I)
    # doc-ids strip BEFORE the datasheet suffix ("MIC4600-Data-Sheet-20005584B")
    s = re.sub(r"[-_]\d{7,8}[A-Z]$", "", s, flags=re.I)
    s = re.sub(r"[-_](datasheet|data-sheet)$", "", s, flags=re.I)
    return s


def canon(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def joinable(stem: str) -> str | None:
    """Guard check: return None with cause if the stem must not join."""
    if TITLEY.search(stem):
        return "family-or-title-derived"
    if not OPNISH.match(stem):
        return "not-opn-shaped"
    return None


def load_served(export_dir: Path):
    served = {}
    feeds = {}
    for f in sorted(export_dir.glob("parts_*.jsonl")):
        feeds[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
        for line in f.open():
            opn = json.loads(line).get("part_number") or ""
            if opn:
                served[opn] = f.name
    return served, feeds


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--export-dir", type=Path,
                    default=Path("/Users/samkim/mnt/primary/m5-opencode/api-store-export"))
    ap.add_argument("--tiers", type=Path,
                    default=ROOT / "results/pin-tables-20261014/admission-tiers.jsonl")
    ap.add_argument("--queue", type=Path,
                    default=ROOT / "results/pin-tables-20261014/pin-batch-queue.jsonl")
    ap.add_argument("--manifests-glob", default="/Volumes/M5_4TB/exports/corpus-deliveries/*/corpus-delivery-manifest.jsonl")
    ap.add_argument("--out", type=Path,
                    default=ROOT / "results/pin-tables-20261014/identity-joins.jsonl")
    args = ap.parse_args()

    served, feeds = load_served(args.export_dir)
    canon_index = {}
    for opn in served:
        canon_index.setdefault(canon(opn), opn)

    ev = {}
    import glob as _glob
    for mf in _glob.glob(args.manifests_glob):
        for line in open(mf):
            try:
                r = json.loads(line)
            except Exception:
                continue
            sha = r.get("sha256")
            url = r.get("source_url") or ""
            stem = re.sub(r"\.(pdf|PDF)$", "", url.rstrip("/").rsplit("/", 1)[-1]).split("?")[0]
            if sha and sha not in ev:
                ev[sha] = {"stem": stem, "mfr": r.get("manufacturer")}

    slug_sha = defaultdict(list)
    for line in args.tiers.open():
        t = json.loads(line)
        if t.get("part_number"):
            slug_sha[t["part_number"]].append(t)

    stats = {"exact": 0, "alias": 0, "unresolved": 0, "no-evidence": 0}
    with args.out.open("w") as out:
        for slug, ts in slug_sha.items():
            sha = ts[0]["sha"]
            e = ev.get(sha)
            base = {"slug": slug, "sha": sha, "rows": ts[0]["rows"],
                    "tier": ts[0]["tier"],
                    "rule_version": RULE_VERSION}
            if e is None:
                stats["no-evidence"] += 1
                out.write(json.dumps({**base, "status": "unresolved",
                                      "cause": "no-manifest-evidence"}, sort_keys=True) + "\n")
                continue
            stem = clean_stem(e["stem"], e["mfr"])
            base.update({"manufacturer": e["mfr"], "stem": stem})
            guard = joinable(stem)
            if guard:
                stats["unresolved"] += 1
                out.write(json.dumps({**base, "status": "unresolved",
                                      "cause": guard}, sort_keys=True) + "\n")
                continue
            if stem in served:
                stats["exact"] += 1
                out.write(json.dumps({**base, "status": "exact",
                                      "canonical_opn": stem,
                                      "feed": served[stem]}, sort_keys=True) + "\n")
            elif canon(stem) in canon_index:
                tgt = canon_index[canon(stem)]
                stats["alias"] += 1
                out.write(json.dumps({**base, "status": "alias",
                                      "canonical_opn": tgt,
                                      "feed": served[tgt],
                                      "alias_rule": "canon(A-Z0-9)"}, sort_keys=True) + "\n")
            else:
                stats["unresolved"] += 1
                out.write(json.dumps({**base, "status": "unresolved",
                                      "cause": "opn-absent-from-served"}, sort_keys=True) + "\n")
    print(json.dumps({"feeds_pinned": feeds, "stats": stats}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())