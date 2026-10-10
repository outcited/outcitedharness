#!/usr/bin/env python3
"""Drain mail stranded in a FROZEN agent-inbox tree into the canonical NAS tree.

The 2026-10-08 migration decision (mail `agent-inbox-nas-migration-20261008`,
cursor-cr) made the NAS share the one canonical inbox and froze
/Volumes/M5_4TB/agent-inbox. Senders that were still pointed at the frozen tree
kept writing there, so mail exists that no recipient can see. This copies it
across and appends the INDEX.jsonl rows the protocol requires.

Copy, never move: the frozen tree stays as the historical record, matching the
2026-10-09 manual drain. Idempotent — a file already present at the destination
is skipped, and an INDEX row whose `path` is already indexed is not re-added.

Dry run by default; pass --apply to write.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

FROZEN = Path(os.environ.get("FROZEN_INBOX", "/Volumes/M5_4TB/agent-inbox"))
CANON = Path(
    os.environ.get(
        "CANON_INBOX",
        Path.home() / "Volumes/Electronicdataassets/agent-inbox",
    )
)
SKIP_NAMES = {"MOVED.md", "README.md", "INDEX.jsonl", ".DS_Store"}
DELIVERY_TAG = "manual-drain-" + datetime.now(timezone.utc).strftime("%Y%m%d")


def frontmatter(text):
    fields = {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return fields
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip('"')
    return fields


def ts_from_name(rel):
    head = Path(rel).name.split("_")[0]
    return head if len(head) == 16 and head.endswith("Z") else ""


def stranded(frozen, canon):
    have = {
        str(p.relative_to(canon))
        for p in canon.rglob("*")
        if p.is_file() and "@eaDir" not in p.parts
    }
    out = []
    for p in sorted(frozen.rglob("*")):
        if not p.is_file() or "@eaDir" in p.parts or p.name in SKIP_NAMES:
            continue
        rel = str(p.relative_to(frozen))
        if rel not in have:
            out.append(rel)
    return out


def index_rows(canon, rels, frozen):
    indexed = set()
    index = canon / "INDEX.jsonl"
    if index.is_file():
        for line in index.read_text().splitlines():
            try:
                indexed.add(json.loads(line).get("path"))
            except json.JSONDecodeError:
                continue
    rows = []
    for rel in rels:
        path = f"agent-inbox/{rel}"
        if path in indexed:
            continue
        fields = frontmatter((frozen / rel).read_text(errors="replace"))
        rows.append(
            {
                "ts": fields.get("created_at") or ts_from_name(rel),
                "from": fields.get("from", "unknown"),
                "to": fields.get("to", Path(rel).parent.name),
                "type": fields.get("type", "status"),
                "id": fields.get("id", Path(rel).stem),
                "subject": fields.get("subject", "")[:120],
                "path": path,
                "delivery": DELIVERY_TAG,
            }
        )
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--frozen", type=Path, default=FROZEN)
    ap.add_argument("--canon", type=Path, default=CANON)
    args = ap.parse_args(argv)

    if not args.frozen.is_dir():
        print(f"frozen tree not found: {args.frozen}", file=sys.stderr)
        return 2
    if not args.canon.is_dir():
        print(
            f"canonical inbox not mounted: {args.canon}\n"
            "  kickstart gui/$UID/com.harness.nasmount, then re-run",
            file=sys.stderr,
        )
        return 2

    rels = stranded(args.frozen, args.canon)
    rows = index_rows(args.canon, rels, args.frozen)
    verb = "DRAINED" if args.apply else "WOULD DRAIN"
    for rel in rels:
        print(f"{verb}: {rel}")
        if args.apply:
            dest = args.canon / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes((args.frozen / rel).read_bytes())
    if args.apply and rows:
        with (args.canon / "INDEX.jsonl").open("a") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")
    print(
        f"{len(rels)} file(s) {'copied' if args.apply else 'to copy'}, "
        f"{len(rows)} INDEX row(s) {'appended' if args.apply else 'to append'} "
        f"({DELIVERY_TAG})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
