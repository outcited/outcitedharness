#!/usr/bin/env python3
"""Fill the taxonomy truth table: nodes -> nodes_filled.jsonl.

The level above the OPN is the catalogue node
(`node:{category}:{brand}:{layer}:{key}`). This tool fills what we already
know deterministically, and names what we do not (lines unverified, brand
unjoined) — never inventing.

Sources (all measured 2026-10-11/12):
  tests/fixtures/gold/category_graph_v1.json   1,938 nodes (352 family + 1,586 series),
                                               7,334 OPN bindings (opn_closure)
  agent-inbox/claude-code-browser/*targets*.json  411 series + 346 family nodes
                                               with brand + source_url + depth_state
  catalog.db claims_canonical                  claims + located pages per OPN

Output: yosemiteraid5 Public/m5-opencode/api-store-export/nodes_filled.jsonl
(same seam as parts_*.jsonl — the dw-api loader reads it behind ApiStore).

Usage: python3 designwins/crates/dw-extract/tools/fill_taxonomy_nodes.py
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

REPO = Path("/Users/samkim/Harnessv1")
OUT = Path("/Users/samkim/mnt/yosemiteraid5-nfs/m5-opencode/api-store-export/nodes_filled.jsonl")
GRAPH = REPO / "tests" / "fixtures" / "gold" / "category_graph_v1.json"
CRAWL = Path("/Users/samkim/Volumes/Electronicdataassets/agent-inbox/claude-code-browser")
CATALOG = Path("/Users/samkim/mnt/yosemiteraid5-nfs/m5-opencode/scratch/catalog.db")


def norm(key: str) -> str:
    return key.strip().lower()


def main() -> int:
    g = json.loads(GRAPH.read_text())
    kinds = g.get("node_kinds", {})

    # node -> OPNs (invert the materialised closure: leaf + every ancestor
    # lists the OPN — that is the node's LINES).
    lines: dict[str, list[str]] = defaultdict(list)
    for opn, cl in g.get("opn_closure", {}).items():
        nodes = [cl.get("leaf")] + list(cl.get("ancestors") or [])
        for node in nodes:
            if node:
                lines[node].append(opn)
    for v in lines.values():
        v.sort()

    # crawler recon: node_id -> brand / source_url / depth_state / admitted.
    recon: dict[str, dict] = {}
    for fname in ("series_content_crawl_targets_2026-W43.json", "family_content_gap_targets_2026-W43.json"):
        path = CRAWL / fname
        if not path.exists():
            continue
        doc = json.loads(path.read_text())
        for t in doc.get("targets", []):
            nid = t.get("node_id", "")
            key = norm(nid.rsplit(":", 1)[-1])
            recon[key] = {
                "brand": t.get("brand"),
                "source_url": t.get("source_url"),
                "depth_state": t.get("depth_state"),
                "admitted_parts_count": t.get("admitted_parts_count") or t.get("released_parts"),
                "node_id": nid,
            }

    # catalog claims per OPN (power universe — overlaps mostly empty with the
    # MCU graph; measured, not assumed).
    db = sqlite3.connect(f"file:{CATALOG}?mode=ro&immutable=1", uri=True)
    claims_by_opn: dict[str, tuple[int, int]] = {}
    for opn, total, located in db.execute(
        "SELECT opn, count(*), sum(CASE WHEN json_extract(provenance,'$.quote') IS NOT NULL"
        " THEN 1 ELSE 0 END) FROM claims_canonical GROUP BY opn"
    ):
        claims_by_opn[opn] = (total, located)

    # Layer from the closure structure: leaves are family grain, ancestors
    # are series tokens (note: "series_tokens list most-specific-first").
    # Declared node_kinds is 352/1586 counts only; measured structure is
    # 345 leaves / 1569 ancestor-only / 24 bound-to-no-OPN ("unclassified",
    # kept honest — a node with no OPN closure is not silently series).
    leaf_nodes = set(g.get("opn_to_leaf", {}).values())
    ancestor_nodes: set[str] = set()
    for cl in g.get("opn_closure", {}).values():
        ancestor_nodes.update(cl.get("ancestors") or [])

    def layer_of(node: str) -> tuple[str, str]:
        r = recon.get(norm(node), {})
        if r.get("node_id"):
            layer = "series" if ":series:" in r["node_id"] else "family"
            return layer, "recon"
        if node in leaf_nodes:
            return "family", "closure"
        if node in ancestor_nodes:
            return "series", "closure"
        return "unclassified", "none"

    rows = []
    stats = {"nodes": 0, "with_lines": 0, "with_brand": 0, "claims_joined": 0}
    for node in sorted(g.get("nodes", [])):
        key = norm(node)
        r = recon.get(key, {})
        opns = lines.get(node, [])
        layer, kind_source = layer_of(node)
        c_total = sum(claims_by_opn.get(o, (0, 0))[0] for o in opns)
        c_loc = sum(claims_by_opn.get(o, (0, 0))[1] for o in opns)
        if opns:
            stats["with_lines"] += 1
        if r.get("brand"):
            stats["with_brand"] += 1
        if c_total:
            stats["claims_joined"] += 1
        rows.append(
            {
                "id": r.get("node_id") or f"node:unknown:unknown:{layer}:{key}",
                "brand": r.get("brand") or "unknown",
                "layer": layer,
                "kind_source": kind_source,
                "label": node,
                "released_parts": opns,
                "lines_verified": bool(opns),
                "depth_state": r.get("depth_state") or ("parts_available" if opns else "unknown"),
                "admitted_parts_count": r.get("admitted_parts_count") or len(opns),
                "source_url": r.get("source_url"),
                "claims": c_total,
                "claims_located": c_loc,
            }
        )
        stats["nodes"] += 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    OUT.write_text(body)
    print(f"nodes_filled.jsonl: {len(rows)} nodes, {len(body)} bytes")
    print(json.dumps(stats, indent=1))
    print("sha:", hashlib.sha256(body.encode()).hexdigest()[:16])
    print("out:", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
