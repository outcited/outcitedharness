#!/usr/bin/env python3
"""Stage consolidated extraction lanes into per-part API files.

Reads the consolidator output + AEC raw rows, routes each axis entry by
its source lane into the per-part staging schema (thermal / vout_iq /
pillars / power_modes / aec), merges into existing staging files without
clobbering other keys (cr-core schema fields stay untouched), and creates
minimal files for net-new parts.

Staging layout: /Volumes/M5_4TB/staging/datasheet_extractions/
  mcu_family_content/datasheet_extractions/<PN>.json
  power_family_content/datasheet_extractions/<PN>.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

STAGING = Path("/Volumes/M5_4TB/staging/datasheet_extractions")
MCU_DIR = STAGING / "mcu_family_content" / "datasheet_extractions"
PWR_DIR = STAGING / "power_family_content" / "datasheet_extractions"

LANE_TO_SECTION = {
    "pillars_fullcorpus": "pillars",
    "thermal": "thermal",
    "thermal_fullcorpus": "thermal",
    "vout_iq": "vout_iq",
    "power_modes_mcu": "power_modes",
    "power_modes_ti": "power_modes",
}


def apply_sections(data: dict, sections: dict) -> None:
    """Merge lane sections into a per-part record without clobbering others.

    `pillars` is a full replace (stale v1 artifacts like line-wrap package
    names must not survive a corrected re-run); every other section merges
    key-by-key."""
    for section, axes in sections.items():
        if section == "pillars":
            data[section] = axes
            continue
        cur = data.get(section)
        if not isinstance(cur, dict):
            cur = {}
        cur.update(axes)
        data[section] = cur


def existing_file(pn: str) -> Path | None:
    for d in (MCU_DIR, PWR_DIR):
        f = d / f"{pn}.json"
        if f.exists():
            return f
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--consolidated", type=Path, required=True)
    ap.add_argument("--aec", type=Path, default=None,
                    help="aec-rows-enriched.jsonl (grade/qualified per part)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="write merged per-part files into this local dir instead of"
                         " in-place staging (staging is read-only for this uid since"
                         " 2026-10-12); rsync the dir up separately")
    args = ap.parse_args()

    tally: Counter[str] = Counter()
    updates: dict[str, dict] = {}  # pn -> {section: {axis: entry}}

    for line in args.consolidated.open():
        if not line.strip():
            continue
        rec = json.loads(line)
        pn = rec.get("part_number")
        if not pn:
            continue
        for axis, entry in (rec.get("axes") or {}).items():
            section = LANE_TO_SECTION.get(entry.get("lane") or "")
            if not section:
                continue
            upd = updates.setdefault(pn, {})
            sec = upd.setdefault(section, {})
            prev = sec.get(axis)
            if prev is None:
                sec[axis] = entry
                tally[f"axis:{section}"] += 1

    # AEC raw rows -> aec section
    if args.aec and args.aec.exists():
        for line in args.aec.open():
            if not line.strip():
                continue
            r = json.loads(line)
            pn = r.get("part_number")
            if not pn or r.get("verdict") != "printed_row":
                continue
            entry = {
                "value": r.get("grade"),
                "qualified_statement": r.get("qualified_statement"),
                "standard": r.get("standard_verbatim"),
                "verbatim": r.get("context_verbatim"),
                "page_1based": r.get("page_1based"),
                "pdf_sha": r.get("pdf_sha") or r.get("document_sha256"),
                "source_url": r.get("source_url"),
                "method": r.get("method"),
                "lane": "aec_fullcorpus",
            }
            sec = updates.setdefault(pn, {}).setdefault("aec", {})
            key = f"{entry['standard'] or 'aec'}_grade"
            if key not in sec or (entry["value"] is not None and sec[key]["value"] is None):
                sec[key] = entry
            tally["axis:aec"] += 1

    if args.dry_run:
        print(json.dumps({"parts_touched": len(updates), **dict(sorted(tally.items()))}, indent=1))
        return 0

    created = merged = 0
    for pn, sections in updates.items():
        f = existing_file(pn)
        if args.out_dir is not None:
            # local-delta mode: read existing from staging (RO NFS), write the
            # merged file into the delta dir keyed by where it must land
            target_dir = MCU_DIR
            data = {"part_number": pn}
            if f:
                target_dir = f.parent
                try:
                    data = json.loads(f.read_text())
                except Exception:
                    data = {"part_number": pn}
            out_root = args.out_dir / target_dir.parent.name / "datasheet_extractions"
            out_root.mkdir(parents=True, exist_ok=True)
            apply_sections(data, sections)
            (out_root / f"{pn}.json").write_text(
                json.dumps(data, ensure_ascii=False, sort_keys=True))
            merged += 1
            continue
        if f:
            try:
                data = json.loads(f.read_text())
            except Exception:
                data = {"part_number": pn}
            apply_sections(data, sections)
            data.setdefault("part_number", pn)
            f.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True))
            merged += 1
        else:
            data = {"part_number": pn, **sections}
            MCU_DIR.mkdir(parents=True, exist_ok=True)
            (MCU_DIR / f"{pn}.json").write_text(
                json.dumps(data, ensure_ascii=False, sort_keys=True))
            created += 1

    print(json.dumps({
        "parts_touched": len(updates),
        "files_merged_into_existing": merged,
        "files_created": created,
        **dict(sorted(tally.items())),
    }, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
