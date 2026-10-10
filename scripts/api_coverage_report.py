#!/usr/bin/env python3
"""Hourly API inventory/coverage report (Sam directive 2026-10-09).

Snapshots both APIs' UX-offer surface, computes deltas vs the previous
hour, writes JSON snapshot + rolling markdown report, and mails Sam a
concise growth summary. The point: SEE the front end gain more to offer.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "results/api-coverage"
SNAP_DIR = OUT_DIR / "snapshots"
REPORT = OUT_DIR / "LATEST.md"
SEND_MESSAGE = Path("/Volumes/M5_4TB/repos/outcited-ai-sandbox-20260822/"
                    "scripts/orchestration/send-message.ts")
AUTH = {"Authorization": "Bearer crk_m5_extraction_v1"}


def _get(url: str, headers: dict | None = None, timeout: int = 30):
    try:
        req = urllib.request.Request(url, headers=headers or {})
        return json.load(urllib.request.urlopen(req, timeout=timeout))
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def snapshot() -> dict:
    cov = _get("http://localhost:8819/v1/coverage", AUTH)
    health = _get("http://localhost:8819/health", AUTH)
    rows = _get("http://localhost:8792/healthz")
    return {
        "ts": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "parts_api": {**(cov if "_error" not in cov else {}),
                      "_health": health},
        "rows_api": rows,
    }


def prev_snapshot(cur_ts: str) -> dict | None:
    if not SNAP_DIR.exists():
        return None
    snaps = sorted(f for f in SNAP_DIR.glob("*.json") if f.stem < cur_ts)
    if not snaps:
        return None
    try:
        return json.loads(snaps[-1].read_text())
    except Exception:
        return None


def _delta(cur: dict, old: dict | None, path: list[str]) -> dict:
    if not old:
        return {}
    c, o = cur, old
    for k in path:
        c = (c or {}).get(k) if isinstance(c, dict) else None
        o = (o or {}).get(k) if isinstance(o, dict) else None
    out = {}
    if isinstance(c, dict) and isinstance(o, dict):
        for key in c:
            if isinstance(c[key], (int, float)) and isinstance(o.get(key), (int, float)):
                d = c[key] - o[key]
                if d:
                    out[key] = d
    elif isinstance(c, (int, float)) and isinstance(o, (int, float)):
        if c - o:
            out["_"] = c - o
    return out


def fmt_deltas(d: dict, limit: int = 8) -> str:
    if not d:
        return "no change"
    items = sorted(d.items(), key=lambda x: -abs(x[1]))[:limit]
    return ", ".join(f"{k} {'+' if v > 0 else ''}{v:,}" for k, v in items)


def main() -> int:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    snap = snapshot()
    ts = snap["ts"]
    prev = prev_snapshot(ts)
    (SNAP_DIR / f"{ts}.json").write_text(json.dumps(snap, indent=1))

    pa = snap.get("parts_api", {})
    ra = snap.get("rows_api", {})
    facets = pa.get("facets") or {}
    pillars = pa.get("pillar_fields") or {}

    parts_now = pa.get("parts_served") or 0
    parts_note = ""
    if prev:
        prev_parts = (prev.get("parts_api") or {}).get("parts_served") or parts_now
        d = parts_now - prev_parts
        if d:
            parts_note = f" ({'+' if d > 0 else ''}{d:,} since last)"

    lines = [
        f"# API coverage — {ts}",
        "",
        f"**Parts served: {parts_now:,}**{parts_note}",
        f"Errata devices: {pa.get('errata_devices', '?'):,} | "
        f"curve index entries: {pa.get('curve_index_entries', 0):,} | "
        f"family-covered parts: {pa.get('family_covered_parts', 0):,}",
        "",
        "## Rows API (v1.1)",
        f"curve rows: {ra.get('curve_rows', '?'):,} | errata rows: {ra.get('errata_rows', '?'):,} | "
        f"curve docs: {ra.get('curve_docs', '?'):,} | manifest joins: {ra.get('manifest_parts', '?'):,}",
        "",
        "## Facet coverage (parts with each renderable facet)",
    ]
    for k, v in facets.items():
        lines.append(f"- {k}: {v:,}")
    lines += ["", "## Pillar fields (comparison facets)", ]
    for k, v in list(pillars.items())[:20]:
        lines.append(f"- {k}: {v:,}")

    # deltas vs previous hour
    if prev:
        lines += ["", "## Δ since previous snapshot"]
        fd = _delta(pa, prev.get("parts_api"), ["facets"])
        pd = _delta(pa, prev.get("parts_api"), ["pillar_fields"])
        rd = _delta(ra, prev.get("rows_api"), [])
        lines.append(f"- facets: {fmt_deltas(fd)}")
        lines.append(f"- pillar fields: {fmt_deltas(pd)}")
        lines.append(f"- rows API: {fmt_deltas(rd)}")
        parts_d = (pa.get("parts_served") or 0) - ((prev.get("parts_api") or {}).get("parts_served") or 0)
        lines.append(f"- parts served: {parts_d:+,}")

    REPORT.write_text("\n".join(lines) + "\n")
    print(f"snapshot {ts} written; report at {REPORT}")

    # mail Sam (concise)
    if SEND_MESSAGE.exists() and "--no-mail" not in sys.argv:
        body = "\n".join(lines[:12])
        if prev:
            body += "\n\nΔ hour: " + fmt_deltas(
                _delta(pa, prev.get("parts_api"), ["facets"]), 6)
        bf = Path(f"/tmp/coverage-mail-{ts}.md")
        bf.write_text(body)
        subprocess.run([
            "/opt/homebrew/bin/npx", "tsx", str(SEND_MESSAGE),
            "--from=m5-opencode", "--to=samson", "--type=status",
            f"--id=api-coverage-{ts}",
            "--subject=API coverage hour: "
            f"{pa.get('parts_served', '?'):,} parts, "
            f"{ra.get('curve_rows', '?'):,} curve rows",
            f"--body-file={bf}",
        ], capture_output=True, timeout=120, cwd=str(SEND_MESSAGE.parent))
        print("mailed to samson")
    return 0


if __name__ == "__main__":
    sys.exit(main())
