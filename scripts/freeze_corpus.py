#!/usr/bin/env python3
"""Freeze a corpus: hash every member and write a pinned manifest.

Every parity run is measured against a frozen input set (PLAN Week 0 gate,
`docs/DECISIONS.md` D2). "Frozen" means content-addressed: the manifest records
a SHA-256 per member, so a run that silently picks up a different file is
immediately visible.

Output shape (``harness.dw-parity-corpus.v1``), matching
``dw_parity::CorpusManifest``::

    {
      "schema_version": "harness.dw-parity-corpus.v1",
      "release": "designwins-2026-W46-freeze-0",
      "generated_at": "2026-10-10T00:00:00Z",
      "root": "/abs/path/that/was/walked",
      "members": [ {"sha256": "...", "bytes": 1234, "path": "rel/from/root"}, ... ]
    }

Members are sorted by ``path`` so the manifest is byte-stable for a given
content set.

Usage::

    # freeze the in-repo gold corpus
    python3 scripts/freeze_corpus.py tests/fixtures/gold \\
        --release designwins-2026-W46-freeze-0 \\
        --out tests/fixtures/gold/corpus_freeze.json

    # verify an existing freeze still matches disk
    python3 scripts/freeze_corpus.py tests/fixtures/gold --verify tests/fixtures/gold/corpus_freeze.json

    # freeze the vault (needs the NAS mounted)
    python3 scripts/freeze_corpus.py /Volumes/M5_4TB/vault/cas --out ...
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "harness.dw-parity-corpus.v1"
CHUNK = 1024 * 1024


def sha256_file(path: Path) -> tuple[str, int]:
    """Return (hex digest, byte length) for a file."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def walk(root: Path, exclude: set[str]) -> list[Path]:
    """All files under root, sorted by relative posix path."""
    found: list[tuple[str, Path]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in exclude for part in Path(rel).parts):
            continue
        found.append((rel, path))
    found.sort(key=lambda kv: kv[0])
    return [p for _, p in found]


def build(root: Path, release: str, exclude: set[str]) -> dict:
    members = []
    for path in walk(root, exclude):
        digest, size = sha256_file(path)
        members.append(
            {
                "sha256": digest,
                "bytes": size,
                "path": path.relative_to(root).as_posix(),
            }
        )
    return {
        "schema_version": SCHEMA,
        "release": release,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": str(root.resolve()),
        "member_count": len(members),
        "total_bytes": sum(m["bytes"] for m in members),
        "members": members,
    }


def verify(root: Path, manifest_path: Path, exclude: set[str]) -> int:
    """Return 0 if the freeze still matches disk."""
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema_version") != SCHEMA:
        print(
            f"schema mismatch: got {manifest.get('schema_version')!r}, expected {SCHEMA!r}",
            file=sys.stderr,
        )
        return 2

    by_path = {m["path"]: m for m in manifest["members"]}
    on_disk = {p.relative_to(root).as_posix(): p for p in walk(root, exclude)}

    missing = sorted(set(by_path) - set(on_disk))
    added = sorted(set(on_disk) - set(by_path))
    changed = []
    for rel, expected in by_path.items():
        path = on_disk.get(rel)
        if path is None:
            continue
        digest, size = sha256_file(path)
        if digest != expected["sha256"] or size != expected["bytes"]:
            changed.append(rel)

    print(f"verify {manifest_path}")
    print(f"  frozen members : {len(by_path)}")
    print(f"  on disk        : {len(on_disk)}")
    print(f"  missing        : {len(missing)}")
    print(f"  added          : {len(added)}")
    print(f"  changed        : {len(changed)}")
    for label, items in (("missing", missing), ("added", added), ("changed", changed)):
        for rel in items[:10]:
            print(f"    {label}: {rel}")
        if len(items) > 10:
            print(f"    {label}: … and {len(items) - 10} more")

    ok = not missing and not added and not changed
    print(f"  result         : {'OK' if ok else 'DRIFT'}")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="directory to freeze")
    parser.add_argument("--release", default="designwins-freeze-0", help="release id")
    parser.add_argument("--out", type=Path, help="write the manifest here")
    parser.add_argument("--verify", type=Path, help="check disk against this manifest")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="directory name to skip (repeatable); defaults include __pycache__",
    )
    args = parser.parse_args()

    root: Path = args.root
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    exclude = {"__pycache__", ".git", ".DS_Store", "target", "node_modules"}
    exclude.update(args.exclude)

    # A manifest never freezes itself. Whatever we write (or are asked to
    # verify against) is excluded from the member set by filename.
    if args.out:
        exclude.add(args.out.name)
    if args.verify:
        exclude.add(args.verify.name)

    if args.verify:
        return verify(root, args.verify, exclude)

    manifest = build(root, args.release, exclude)
    text = json.dumps(manifest, indent=1, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
        print(
            f"wrote {args.out}  members={manifest['member_count']}  "
            f"bytes={manifest['total_bytes']}"
        )
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
