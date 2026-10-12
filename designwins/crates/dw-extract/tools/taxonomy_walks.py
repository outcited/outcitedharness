#!/usr/bin/env python3
"""The three deterministic taxonomy walks (the always-on check).

Walk DOWN  node -> source_url -> vendor page -> listed parts (the LINES)
Walk UP    OPN -> vendor page -> family/series breadcrumb -> node resolution
COMPARE    ours vs live -> diff queue with evidence

Law (PLAN §9): a caught class never becomes model-judgment again. These
walks are deterministic — token extraction and set comparison, no model.
The crawler/Hermes lane classifies pages; THIS verifies their proposals and
our table against what the vendor prints today.

Bounded by --limit; results append to the diff queue with evidence (url,
retrieved_at, content_type). Type-gate lesson carried: vendor pages are
HTML/XLSX/AVIF — sniff content_type, never trust the extension.

Usage:
  python3 .../taxonomy_walks.py down  --limit 5
  python3 .../taxonomy_walks.py compare --limit 5
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OUT = Path("/Users/samkim/mnt/yosemiteraid5/Public/m5-opencode/api-store-export/taxonomy_walks.jsonl")
NODES = Path("/Users/samkim/mnt/yosemiteraid5/Public/m5-opencode/api-store-export/nodes_filled.jsonl")
CRAWL = Path("/Users/samkim/Volumes/Electronicdataassets/agent-inbox/claude-code-browser")

OPN_TOKEN = re.compile(r"\b[A-Z][A-Z0-9]{2,12}[A-Z0-9x]{0,4}\b")
SKIP_TOKENS = {"HTML", "HTTP", "PDF", "NULL", "NONE", "API", "JSON", "CPU", "GPIO", "USB", "ADC", "DAC", "SPI", "I2C", "CAN", "MCU"}


def sniff_is_html(content_type: str, body: bytes) -> bool:
    """Type gate: content_type is honest; extensions lie (the AVIF trap)."""
    if "html" in (content_type or "").lower():
        return True
    return body[:20].lstrip().lower().startswith((b"<!doctype", b"<html"))


def fetch(url: str) -> tuple[int, str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "m5-taxonomy-walk/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310 (evidence fetch)
        return resp.status, resp.headers.get("content-type", ""), resp.read()


def parts_in_html(body: bytes) -> list[str]:
    text = body.decode("utf-8", errors="ignore")
    found = {m.group(0) for m in OPN_TOKEN.finditer(text) if m.group(0) not in SKIP_TOKENS}
    return sorted(found)


def walk_down(limit: int) -> list[dict]:
    """node -> live vendor page -> the parts it lists."""
    rows = [json.loads(l) for l in NODES.open()]
    with_url = [r for r in rows if r.get("source_url")]
    diffs = []
    for row in with_url[:limit]:
        url = row["source_url"]
        entry = {
            "walk": "down",
            "node_id": row["id"],
            "url": url,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            status, ctype, body = fetch(url)
            entry["status"] = status
            entry["content_type"] = ctype
            entry["is_html"] = sniff_is_html(ctype, body)
            live = parts_in_html(body) if entry["is_html"] else []
            ours = set(row.get("released_parts") or [])
            entry["live_tokens"] = len(live)
            entry["lines_match"] = bool(ours) and any(p in live for p in ours)
            entry["missing_live"] = sorted(ours - set(live))[:20]
            entry["class"] = "lines_match" if entry["lines_match"] else "no_line_overlap"
        except Exception as e:  # noqa: BLE001 — the walk records failures as data
            entry["class"] = "fetch_failed"
            entry["error"] = str(e)[:200]
        diffs.append(entry)
    return diffs


def compare(limit: int) -> list[dict]:
    """ours (nodes_filled) vs the crawler recon index: what each side knows."""
    rows = [json.loads(l) for l in NODES.open()]
    recon_keys = set()
    for fname in ("series_content_crawl_targets_2026-W43.json", "family_content_gap_targets_2026-W43.json"):
        doc = json.loads((CRAWL / fname).read_text())
        for t in doc.get("targets", []):
            recon_keys.add(t.get("node_id", "").rsplit(":", 1)[-1].lower())
    diffs = []
    for row in rows[:limit]:
        key = row["label"].lower()
        ours_lines = row.get("released_parts") or []
        entry = {
            "walk": "compare",
            "node_id": row["id"],
            "label": row["label"],
            "in_recon_index": key in recon_keys,
            "ours_lines": len(ours_lines),
        }
        if not row.get("brand") or row["brand"] == "unknown":
            entry["class"] = "brand_unjoined"
        elif not ours_lines:
            entry["class"] = "no_lines_ours"
        elif not entry["in_recon_index"]:
            entry["class"] = "ours_has_nodes_recon_dropped"
        else:
            entry["class"] = "agree"
        diffs.append(entry)
    return diffs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("walk", choices=["down", "up", "compare"])
    ap.add_argument("--limit", type=int, default=5)
    args = ap.parse_args()
    if args.walk == "up":
        print("walk-up needs the OPN->vendor-URL map from the crawler lane; queue it there")
        return 2
    fn = {"down": walk_down, "compare": compare}[args.walk]
    diffs = fn(args.limit)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a") as fh:
        for d in diffs:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    by_class: dict[str, int] = {}
    for d in diffs:
        by_class[d["class"]] = by_class.get(d["class"], 0) + 1
    print(json.dumps({"walk": args.walk, "n": len(diffs), "by_class": by_class}, indent=1))
    for d in diffs[:3]:
        print(json.dumps({k: d[k] for k in list(d)[:8]})[:220])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
