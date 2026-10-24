#!/usr/bin/env python3
"""release_ops.py — full-release promote/rollback for the ExportStore root.

LAW: rollback restores the ENTIRE previous release — every feed file, the
fold manifest, the derived cube, the release identity record — verified
bit-exact by hash. Never just 'delete the new jsonl'.

Usage (isolated or production, owner-executed):
  release_ops.py snapshot  --root DIR --id NAME   # pre-promotion snapshot
  release_ops.py promote   --root DIR --rc RC_DIR # add feed + regen cube + new identity
  release_ops.py rollback  --root DIR --id NAME   # full restore, hash-verified
"""
import argparse, hashlib, json, shutil, sys, tarfile, tempfile, time
from pathlib import Path

IDENTITY = "RELEASE-IDENTITY.json"


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        while c := fh.read(1 << 20):
            h.update(c)
    return h.hexdigest()


def tree_hashes(root: Path) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.name.startswith(".rollback"):
            out[str(p.relative_to(root))] = sha(p)
    return out


def write_identity(root: Path, release_id: str, note: str):
    ident = {
        "release_id": release_id,
        "note": note,
        "code_head": "34b139aa",
        "cube_sha256": sha(root / "cube.json"),
        "feed_hashes": {p.name: sha(p) for p in sorted(root.glob("parts*.jsonl"))
                        if ".bak" not in p.name},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (root / IDENTITY).write_text(json.dumps(ident, indent=1, sort_keys=True))


def regen_cube(root: Path):
    """Cube over every parts_*.jsonl feed (same keying law as fold_lane_feed)."""
    from collections import defaultdict
    cells = {}
    for fpath in sorted(root.glob("parts_*.jsonl")):
        if ".bak" in fpath.name or ".tmp" in fpath.name:
            continue
        for line in fpath.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            vendor = row.get("vendor") or "unknown"
            name = fpath.name
            if name == "parts_lane.jsonl":
                key = (row.get("aisle") or "unclassified", "deterministic-lane", vendor)
            elif name.startswith("parts_a_pinout") or name.startswith("parts_z_pinout"):
                dim = ("pinout-staged" if row.get("join_status") == "unresolved"
                       else "pinout-gold")
                key = (row.get("aisle") or "mcu", dim, vendor)
            elif name == "parts_cr_core.jsonl":
                key = (row.get("aisle") or "unassigned", "cr-core", vendor)
            elif name == "parts_burn.jsonl":
                key = ("power/semiconductor", "burn", vendor)
            elif name == "parts_board.jsonl":
                key = ("boards", "design-containers", vendor)
            elif name == "parts_spine_rc1.jsonl":
                key = (row.get("aisle") or "spine-rc1", "spine-rc1", vendor)
            else:
                key = ("power/semiconductor", "catalog", vendor)
            cell = cells.setdefault(key, {"category": key[0], "aisle": key[1],
                                          "vendor": key[2], "parts": 0, "claims": 0,
                                          "laned": True, "curves": False})
            cell["parts"] += 1
            cell["claims"] += len(row.get("claims") or [])
    cube = {"dimensions": ["category", "aisle", "vendor"],
            "cells": sorted(cells.values(), key=lambda c: (c["category"], c["aisle"], c["vendor"])),
            "laned": True}
    tmp = root / "cube.json.tmp"
    tmp.write_text(json.dumps(cube, indent=1, sort_keys=True))
    tmp.replace(root / "cube.json")


def cmd_snapshot(args):
    root = Path(args.root)
    sid = f"RELEASE-SNAPSHOT-{args.id}.tar"
    pre = tree_hashes(root)
    with tarfile.open(root / f".rollback-{sid}", "w:gz") as tar:
        for p in sorted(root.rglob("*")):
            if p.is_file() and not p.name.startswith(".rollback"):
                tar.add(p, arcname=str(p.relative_to(root)))
    (root / f".rollback-{sid}.hashes.json").write_text(json.dumps(pre, indent=1, sort_keys=True))
    print(json.dumps({"snapshot": sid, "files": len(pre),
                      "snapshot_sha": sha(root / f'.rollback-{sid}'),
                      "identity_at_snapshot": json.loads((root / IDENTITY).read_text())["release_id"]
                      if (root / IDENTITY).exists() else None}, indent=1))


def cmd_promote(args):
    root, rc = Path(args.root), Path(args.rc)
    man = json.loads((rc / "release-manifest.json").read_text())
    feed = rc / "parts_spine_rc1.jsonl"
    if sha(feed) != man["feed_sha256"]:
        sys.exit("ABORT: RC feed hash does not match its manifest")
    shutil.copy2(feed, root / "parts_spine_rc1.jsonl")
    regen_cube(root)
    write_identity(root, man["release_id"], "promoted from staged RC")
    print(json.dumps({"promoted": man["release_id"], "cube_sha": sha(root / "cube.json"),
                      "feeds": len(list(root.glob('parts*.jsonl')))}, indent=1))


def cmd_rollback(args):
    root = Path(args.root)
    sid = f"RELEASE-SNAPSHOT-{args.id}.tar"
    tar_p = root / f".rollback-{sid}"
    hashes_p = root / f".rollback-{sid}.hashes.json"
    if not tar_p.exists() or not hashes_p.exists():
        sys.exit("ABORT: snapshot missing — fail closed, no partial restore")
    pre = json.loads(hashes_p.read_text())
    # remove every current non-rollback file, then restore the snapshot
    for p in sorted(root.rglob("*"), reverse=True):
        if p.is_file() and not p.name.startswith(".rollback"):
            p.unlink()
        elif p.is_dir() and not any(p.iterdir()) and not p.name.startswith(".rollback"):
            p.rmdir()
    with tarfile.open(tar_p) as tar:
        tar.extractall(root)
    # verify bit-exact restore against the pre-snapshot hash tree
    post = tree_hashes(root)
    missing = {k: v for k, v in pre.items() if k not in post or post[k] != v}
    extra = {k: v for k, v in post.items() if k not in pre}
    ok = not missing and not extra
    # restore the identity record from the snapshot's own identity? identity
    # files are excluded from the tar (they describe the release being replaced)
    # — rollback re-asserts it from the snapshot manifest sidecar:
    # identity restored bit-exact from the tar (it was snapshotted); audit
    # note goes to a side file, never into the release identity record
    (root / ".rollback-audit.json").write_text(json.dumps(
        {"rolled_back_to": args.id, "completed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, indent=1))
    print(json.dumps({"rollback": args.id, "files_restored": len(post),
                      "bit_exact": ok,
                      "missing_or_changed": len(missing), "extra": len(extra)}, indent=1))
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("snapshot"); p.add_argument("--root", required=True); p.add_argument("--id", required=True)
    p = sub.add_parser("promote"); p.add_argument("--root", required=True); p.add_argument("--rc", required=True)
    p = sub.add_parser("rollback"); p.add_argument("--root", required=True); p.add_argument("--id", required=True)
    args = ap.parse_args()
    {"snapshot": cmd_snapshot, "promote": cmd_promote, "rollback": cmd_rollback}[args.cmd](args)


if __name__ == "__main__":
    main()
