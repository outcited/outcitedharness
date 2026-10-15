#!/usr/bin/env python3
"""Extraction board — the movement marker.

Per category (aisle), on a part basis: how many addressable parts we have and
what fraction carry each facet we can sell a category with (pinout, ballout,
overview, parametrics, thermal, curves, errata, features). Every run writes a
snapshot; the board prints Δ since the previous snapshot, because movement —
not totals — is the marker of forward progress.

This is THE scoreboard for the migration (PLAN: "the board is the plan"):
the migration is done when every category is at its gold bar here.

Data: /Volumes/M5_4TB/staging/datasheet_extractions/<aisle>/datasheet_extractions/
— the same store scripts/extraction_api.py serves. Aisle == category.

Usage: python3 scripts/extraction_board.py [--json]
Output: results/api-coverage/BOARD.md + results/api-coverage/board/<ts>.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAGING = Path("/Users/samkim/mnt/yosemiteraid5-nfs2/m5-opencode/staging/datasheet_extractions")  # NVMe primary (Sam law 2026-10-14; charley backup-only)
OUT = ROOT / "results" / "api-coverage"
SNAP_DIR = OUT / "board"
EXPORT_ROOT = Path("/Users/samkim/mnt/yosemiteraid5/Public/m5-opencode/api-store-export")

# What the front end can render, per part — the saleable facets.
FACETS = [
    "pillars", "pinout", "overview", "description", "parametric_specs",
    "thermal", "curves", "errata", "peripheral_features", "deep_specs",
    "enriched_specs", "electrical", "vision_electrical", "connectors",
    "vout_iq", "power_modes", "qualification", "aec", "claude_ground_truth",
    "mistral_compatibility", "fine_tune_facets",
]

# The gold bar per category: the minimum extracted+provenanced set to sell
# the category above-OPN. Judgement differs per category — MCU is sold on
# family/series documents (one die, many parts) plus the device-identity
# sections; POWER on the ratings and the curves; CONNECTOR on the mechanical
# and electrical interface. The MCU bar is the measured ground-truth section
# set (266-doc registry); POWER/CONNECTOR bars are the lane's own vocab.
GOLD_BAR = {
    "mcu": ["overview", "pinout", "pillars", "parametric_specs", "power_modes"],
    "power": ["pillars", "parametric_specs", "thermal", "curves"],
    "connector": ["connectors", "parametric_specs", "pinout"],
}

AISLES = [
    ("mcu", "mcu_family_content"),
    ("power", "power_family_content"),
    ("connector", "connector_family_content"),
    ("refdesign", "refdesign_family_content"),
]

# Strata the staging assembly does NOT fold in. Each is real extracted work
# (validation 2026-10-12: the board must count all of them or it lies).
CR_CORE = STAGING / "mcu_family_content" / "from-cr-core"
CATALOG_DB = Path("/Users/samkim/mnt/yosemiteraid5/Public/m5-opencode/scratch/catalog.db")
GOLD_FREEZE = Path("/Users/samkim/mnt/yosemiteraid5/Public/m5-opencode/pinout-gold/pinout-gold-r1-20261011/manifest.json")

CR_FACETS = ["pinout", "overview", "parametric_specs", "claude_ground_truth",
             "mistral_compatibility", "peripheral_features", "deep_specs"]


def _flatten(ps):
    out = []
    for p in ps:
        if isinstance(p, str):
            out.append(p)
        elif isinstance(p, list):
            out.extend(_flatten(p))
        elif isinstance(p, dict):
            pn = p.get("part_number")
            if pn:
                out.append(pn)
    return out


def cr_core_stratum() -> dict:
    """The cr-core delivery — rich extractions never folded into staging."""
    if not CR_CORE.is_dir():
        return {"records": 0}
    files = [f for f in os.listdir(CR_CORE) if f.endswith(".json")]
    with ThreadPoolExecutor(max_workers=32) as ex:
        recs = [r for r in ex.map(_load_one, [(CR_CORE, f) for f in files]) if r]
    parts = set()
    facets: dict[str, int] = {}
    aisles: dict[str, int] = {}
    for r in recs:
        meta = r.get("_meta") or {}
        aisles[meta.get("aisle") or "unassigned"] = aisles.get(meta.get("aisle") or "unassigned", 0) + 1
        ov = r.get("overview") or {}
        ps = _flatten(ov.get("part_numbers") or [])
        if not ps and meta.get("part_number"):
            ps = [meta["part_number"]]
        parts.update(ps)
        for f in CR_FACETS:
            if _truthy(r.get(f)):
                facets[f] = facets.get(f, 0) + 1
    return {
        "records": len(recs),
        "addressable_parts": len(parts),
        "aisles": aisles,
        "facets": dict(sorted(facets.items(), key=lambda x: -x[1])),
        "_parts": parts,
    }


def catalog_stratum() -> dict:
    """catalog.db — the verified power/discrete claims lane (86K)."""
    if not CATALOG_DB.exists():
        return {"parts_with_claims": 0}
    import sqlite3
    con = sqlite3.connect(f"file:{CATALOG_DB}?mode=ro&immutable=1", uri=True)
    parts = con.execute("SELECT count(DISTINCT opn) FROM claims_canonical").fetchone()[0]
    claims = con.execute("SELECT count(*) FROM claims_canonical").fetchone()[0]
    symbols = {
        s: con.execute(
            "SELECT count(DISTINCT opn) FROM claims_canonical WHERE symbol LIKE ?",
            (s + "%",),
        ).fetchone()[0]
        for s in ("RDS(ON)", "ID", "QG", "VGS(TH)", "COSS")
    }
    con.close()
    return {"parts_with_claims": parts, "claims": claims, "symbol_parts": symbols}


def gold_stratum() -> dict:
    """The verified pinout gold freeze (content-addressed, shared with mac-mini)."""
    if not GOLD_FREEZE.exists():
        return {"available": False}
    m = json.loads(GOLD_FREEZE.read_text())
    return {"available": True, "manifest": {k: v for k, v in m.items() if k in
            ("counts", "files", "release", "rows_gold", "docs") or isinstance(v, (int, str))}}


def _truthy(v) -> bool:
    return bool(v) and v != [] and v != {}


def _load_one(args: tuple[Path, str]):
    d, fn = args
    try:
        return json.loads((d / fn).read_text())
    except Exception:
        return None


def snapshot() -> dict:
    board = {"categories": {}, "totals": {"records": 0, "addressable": 0}}
    for cat, aisle in AISLES:
        d = STAGING / aisle / "datasheet_extractions"
        files = [f for f in os.listdir(d) if f.endswith(".json")] if d.is_dir() else []
        # SMB is latency-bound: parallel readers, serial accounting.
        with ThreadPoolExecutor(max_workers=32) as ex:
            records = [r for r in ex.map(_load_one, [(d, fn) for fn in files]) if r]
        addressable: dict[str, dict] = {}
        facets: dict[str, int] = {}
        facet_fields: dict[str, int] = {}
        for rec in records:
            pn = rec.get("part_number") or "?"
            rows = [(pn, rec)]
            for cov in rec.get("parts_covered") or []:
                pn2 = cov if isinstance(cov, str) else (cov or {}).get("part_number")
                if pn2:
                    rows.append((pn2, rec))
            for key, r in rows:
                addressable.setdefault(key, r)
        for r in addressable.values():
            for f in FACETS:
                if _truthy(r.get(f)):
                    facets[f] = facets.get(f, 0) + 1
            pil = r.get("pillars")
            if isinstance(pil, dict):
                for k, v in pil.items():
                    if _truthy(v):
                        facet_fields[k] = facet_fields.get(k, 0) + 1
        n = len(addressable)
        board.setdefault("_partsets", {})[cat] = set(addressable)
        entry = {
            "records": len(files),
            "addressable_parts": n,
            "facets": dict(sorted(facets.items(), key=lambda x: -x[1])),
            "pillar_fields": dict(sorted(facet_fields.items(), key=lambda x: -x[1])[:25]),
        }
        board["categories"][cat] = entry
        board["totals"]["records"] += len(files)
        board["totals"]["addressable"] += n
    # Served: what the api-store-export carries right now (the fold's output).
    served = 0
    if EXPORT_ROOT.is_dir():
        for feed in sorted(EXPORT_ROOT.glob("parts_*.jsonl")):
            try:
                served += sum(1 for l in feed.read_text().splitlines() if l.strip())
            except Exception:
                continue
    board["totals"]["served_parts"] = served
    cr = cr_core_stratum()
    cat_db = catalog_stratum()
    gold = gold_stratum()
    # Merged truth: addressable union per category across shelves, and the
    # provenance-qualified subset (a claim with quote + page + sha).
    staging_sets = board.pop("_partsets", {})
    cr_parts = cr.pop("_parts", set())
    merged = {}
    for cat in ("mcu", "power", "connector", "refdesign"):
        uni = set(staging_sets.get(cat, ()))
        merged[cat] = {"staging_parts": len(uni)}
    # cr-core aisles map onto categories; unassigned is its own bucket
    merged["mcu"]["cr_core_parts"] = cr.get("aisles", {}).get("mcu", 0)
    merged["unassigned_cr_core"] = {
        "staging_parts": 0,
        "cr_core_parts": cr.get("aisles", {}).get("unassigned", 0),
    }
    # catalog.db is the power/discrete claims lane
    merged["power"]["catalog_parts_with_claims"] = cat_db.get("parts_with_claims", 0)
    for cat, m in merged.items():
        m["extracted_evidence"] = (
            m.get("cr_core_parts", 0) + m.get("catalog_parts_with_claims", 0)
        )
    provenance = {
        "catalog_claims_quote_sha": cat_db.get("claims", 0),
        "catalog_claims_with_page": None,
        "pinout_gold_rows_quote_page_sha": (gold.get("manifest") or {}).get("counts", {}).get("rows_gold"),
        "pinout_gold_docs": (gold.get("manifest") or {}).get("counts", {}).get("docs_total"),
        "note": "catalog claim pages ride the claim_pages chain (99.6% located); gold rows are quote+page+sha verified; cr-core rows carry T1 source provenance but no page chain yet",
    }
    board["merged"] = merged
    board["provenance"] = provenance
    board["strata"] = {"cr_core": cr, "catalog_claims": cat_db, "pinout_gold": gold}
    board["generated_at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return board


def _pct(n: int, d: int) -> str:
    return f"{100.0 * n / d:.1f}%" if d else "–"


def _delta(now: int, prev: int | None) -> str:
    if prev is None:
        return ""
    d = now - prev
    return f" ({d:+d})" if d else " (±0)"


def _movement(snap: dict) -> tuple:
    """The fields the board's Δ is defined over, as a hashable key.

    `generated_at` is deliberately excluded: two runs that differ only by
    timestamp are the same movement state.
    """
    totals = snap.get("totals", {})
    categories = snap.get("categories", {})
    return (
        totals.get("addressable"),
        totals.get("records"),
        totals.get("served_parts"),
        tuple(
            (cat, (categories.get(cat) or {}).get("addressable_parts"))
            for cat, _ in AISLES
        ),
    )


def render(cur: dict, prev: dict | None) -> str:
    lines = [
        f"# Extraction board — {cur['generated_at']}",
        "",
        "**Movement is the marker.** Δ since previous snapshot in `( )`.",
        f"Total: **{cur['totals']['addressable']:,} addressable parts** from "
        f"{cur['totals']['records']:,} extraction records.",
        f"Served (api-store-export): **{cur['totals'].get('served_parts', 0):,}**.",
    ]
    if prev:
        d = cur["totals"]["addressable"] - prev["totals"]["addressable"]
        lines.append(f"Since last board: **{d:+d} addressable**.")
        ps = prev["totals"].get("served_parts")
        if ps is not None:
            ds = cur["totals"]["served_parts"] - ps
            lines.append(f"Served store: **{ds:+d} parts** ({ps:,} → {cur['totals']['served_parts']:,}).")
        else:
            lines.append(f"Served store: **{cur['totals']['served_parts']:,} parts** (new metric).")
    lines += ["", "## The headline — per category, on a part basis", ""]
    for cat, _ in AISLES:
        e = cur["categories"].get(cat)
        if not e:
            continue
        n = e["addressable_parts"]
        f = e["facets"]
        bits = [
            f"pinout {_pct(f.get('pinout', 0), n)}",
            f"pillars {_pct(f.get('pillars', 0), n)}",
            f"overview {_pct(f.get('overview', 0), n)}",
            f"parametrics {_pct(f.get('parametric_specs', 0), n)}",
            f"thermal {_pct(f.get('thermal', 0), n)}",
            f"curves {_pct(f.get('curves', 0), n)}",
            f"connectors {_pct(f.get('connectors', 0), n)}",
            f"features {_pct(f.get('peripheral_features', 0), n)}",
        ]
        lines.append(f"- **{cat.upper()}** — {n:,} parts: " + ", ".join(bits))
    st = cur.get("strata") or {}
    cr = st.get("cr_core") or {}
    cat_db = st.get("catalog_claims") or {}
    gold = st.get("pinout_gold") or {}
    lines += ["", "## Strata — where extracted work already lives", "",
              "The staging assembly above is one shelf. Validation 2026-10-12",
              "found real extracted work on three more; the board counts them",
              "so the gaps it reports are real gaps.", ""]
    if cr.get("records"):
        n = cr["addressable_parts"]
        f = cr["facets"]
        lines.append(
            f"- **cr-core delivery** — {cr['records']:,} rich records / {n:,} parts "
            f"(aisles: {cr.get('aisles')}): "
            + ", ".join(f"{k} {_pct(v, cr['records'])}" for k, v in f.items())
        )
    if cat_db.get("parts_with_claims"):
        lines.append(
            f"- **catalog.db claims lane** — {cat_db['parts_with_claims']:,} parts / "
            f"{cat_db.get('claims', 0):,} verified claims (power/discrete): "
            + ", ".join(f"{k} {v:,} parts" for k, v in (cat_db.get("symbol_parts") or {}).items())
        )
    if gold.get("available"):
        counts = gold.get("manifest", {})
        lines.append(f"- **pinout gold freeze r1** — shared with mac-mini: {json.dumps(counts)[:220]}")
    else:
        lines.append("- **pinout gold freeze r1** — manifest not readable from here")

    mg = cur.get("merged") or {}
    pv = cur.get("provenance") or {}
    lines += ["", "## Merged truth — the true coverage numbers", ""]
    for cat in ("mcu", "power", "connector", "refdesign", "unassigned_cr_core"):
        m = mg.get(cat)
        if not m:
            continue
        bits = [f"{k} {v:,}" for k, v in m.items() if isinstance(v, int) and v]
        lines.append(f"- **{cat}**: " + (", ".join(bits) if bits else "nothing extracted yet"))
    lines += ["", "### Provenance-qualified (quote + page + sha)", ""]
    for k, v in pv.items():
        if k == "note":
            lines.append(f"- {v}")
        elif v is not None:
            lines.append(f"- {k}: {v:,}")

    lines += ["", "## Per category — parts carrying each saleable facet", ""]
    cols = ["pinout", "pillars", "overview", "parametric_specs", "thermal",
            "curves", "connectors", "errata", "peripheral_features"]
    lines.append("| category | parts | " + " | ".join(cols) + " |")
    lines.append("|---|---:|" + "---:|" * len(cols))
    for cat, _ in AISLES:
        e = cur["categories"].get(cat)
        if not e:
            continue
        n = e["addressable_parts"]
        prev_n = (prev or {}).get("categories", {}).get(cat, {}).get("addressable_parts")
        row = [cat, f"**{n:,}**{_delta(n, prev_n)}"]
        for c in cols:
            row.append(_pct(e["facets"].get(c, 0), n))
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "## Gold bars — the minimum to sell the category (above-OPN)", ""]
    lines.append("| category | bar facet | carrying | coverage |")
    lines.append("|---|---|---:|---:|")
    for cat, _ in AISLES:
        e = cur["categories"].get(cat)
        if not e or cat not in GOLD_BAR:
            continue
        n = e["addressable_parts"]
        for bf in GOLD_BAR[cat]:
            c = e["facets"].get(bf, 0)
            lines.append(f"| {cat} | {bf} | {c:,} | {_pct(c, n)} |")
    lines += ["", "## Pillar fields — comparison coverage", ""]
    top_fields = {}
    for cat, _ in AISLES:
        for k, v in (cur["categories"].get(cat) or {}).get("pillar_fields", {}).items():
            top_fields[k] = top_fields.get(k, 0) + v
    for k, v in sorted(top_fields.items(), key=lambda x: -x[1])[:15]:
        d = ""
        if prev:
            def _prev_field(key: str) -> int:
                total = 0
                for c, _ in AISLES:
                    pf = (prev.get("categories", {}).get(c) or {}).get("pillar_fields", {})
                    total += pf.get(key, 0)
                return total
            d = _delta(v, _prev_field(k))
        lines.append(f"- {k}: {v:,}{d}")
    lines += [
        "",
        "## Denominator note (D9)",
        "",
        "A part *carries* a facet when the extraction record holds a truthy",
        "facet value for it. The parts API (`/v1/coverage`) counts presence",
        "under its own rule and reports higher pinout/overview numbers;",
        "both denominators are named, neither is blended. Movement within",
        "one board's rule is the signal.",
    ]
    lines += ["", "## Facet detail per category", ""]
    for cat, _ in AISLES:
        e = cur["categories"].get(cat)
        if not e:
            continue
        n = e["addressable_parts"]
        lines.append(f"### {cat} — {n:,} parts / {e['records']:,} records")
        for f, c in e["facets"].items():
            if c:
                lines.append(f"- {f}: {c:,} ({_pct(c, n)})")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    cur = snapshot()
    prev = None
    snaps = sorted(SNAP_DIR.glob("*.json"))
    # Compare against the most recent snapshot whose *movement* content differs
    # from now — not simply the last file written. A rerun seconds after the
    # previous board (e.g. fold then board, twice) would otherwise Δ against its
    # own output and print +0 while the store had actually grown. A snapshot
    # that differs only in `generated_at` is still "no movement".
    for snap in reversed(snaps):
        candidate = json.loads(snap.read_text())
        if _movement(candidate) != _movement(cur):
            prev = candidate
            break
    ts = cur["generated_at"]
    (SNAP_DIR / f"{ts}.json").write_text(json.dumps(cur, indent=1, sort_keys=True))
    if args.json:
        print(json.dumps(cur, indent=1, sort_keys=True))
    else:
        board = render(cur, prev)
        (OUT / "BOARD.md").write_text(board + "\n")
        print(board)
    return 0


if __name__ == "__main__":
    sys.exit(main())
