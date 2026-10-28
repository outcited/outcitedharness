#!/usr/bin/env python3
"""Alias private filesystem paths out of a committed evidence artifact.

Public-artifact hygiene (DW-TRUTH-FUNNEL-01, issue #5): nothing committed or
posted may contain NAS paths, mount points, private URLs, or credentials.
Full paths stay in the local session log only; committed JSON/MD carry aliases
such as ``nas://m5-4tb/...``.

This is a pure text transform: it preserves every byte except the matched
private path prefixes, so the aliased artifact remains deterministic and
diffable. It never touches the source file (read-only) — it writes an aliased
copy.

usage:
  python3 scripts/alias_evidence_paths.py IN OUT [IN OUT ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

# Longest-prefix-first so nested roots resolve to the most specific alias.
ALIASES = [
    ("/Volumes/Electronicdataassets/m5-4tb", "nas://m5-4tb"),
    ("/Users/samkim/mnt/charleybasecamp-smb/m5-4tb", "nas://m5-4tb"),
    ("/Volumes/M5_4TB", "nas://m5-4tb"),
    ("/Users/samkim/mnt/yosemiteraid5/Public/m5-opencode/api-store-export",
     "store://api-store-export"),
    ("/Users/samkim/mnt/yosemiteraid5/Public/m5-opencode", "store://m5-opencode"),
    ("/Users/samkim/Harnessv1/results", "local://Harnessv1/results"),
    ("/Users/samkim/lane-out", "local://lane-out"),
    ("/Users/samkim", "local://home"),
    ("/Volumes/BIGGEYBIGS", "nas://biggeybigs"),
]


def alias_text(text: str) -> str:
    for real, alias in ALIASES:
        text = text.replace(real, alias)
    return text


def alias_file(src: Path, dst: Path) -> None:
    dst.write_text(alias_text(src.read_text(errors="replace")))


def main(argv) -> int:
    if len(argv) < 3 or len(argv) % 2 == 0:
        print(__doc__)
        return 2
    for i in range(1, len(argv), 2):
        src, dst = Path(argv[i]), Path(argv[i + 1])
        alias_file(src, dst)
        print(f"aliased {src} -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))