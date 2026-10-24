#!/usr/bin/env python3
"""Append-merge push of fleet-state CHANGES.md to seven.

Replaces the wholesale scp that destroyed server-side appends (measured
2026-10-22/23: four DISPATCH notes wiped within hours). The lane law is
read-modify-write (m5 commit 041a2a9a); this extends it to the push path.

Sections (## <header> + body until the next header) present locally but NOT
in the remote file are appended verbatim, in order. Nothing is ever deleted
or rewritten remotely.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

LOCAL = Path("/Users/samkim/lane-out/fleet-state/CHANGES.md")
REMOTE = "samsonkim@100.89.168.75:staging/feeds/fleet-state/CHANGES.md"
HOST = "samsonkim@100.89.168.75"
RPATH = "staging/feeds/fleet-state/CHANGES.md"


def sections(text: str) -> list[tuple[str, str]]:
    """Split into (header, body) sections; header is the `## ...` line."""
    out: list[tuple[str, str]] = []
    cur_head = None
    cur: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if cur_head is not None:
                out.append((cur_head, "\n".join(cur)))
            cur_head, cur = line, [line]
        else:
            if cur_head is None:
                continue  # preamble noise
            cur.append(line)
    if cur_head is not None:
        out.append((cur_head, "\n".join(cur)))
    return out


def main() -> int:
    if not LOCAL.exists():
        print(f"no local {LOCAL}", file=sys.stderr)
        return 1
    local_secs = sections(LOCAL.read_text())
    r = subprocess.run(["ssh", HOST, f"cat {RPATH}"], capture_output=True,
                       text=True)
    remote_heads = {h for h, _ in sections(r.stdout or "")}
    missing = [b for h, b in local_secs if h not in remote_heads]
    if not missing:
        print("remote current; nothing to merge")
        return 0
    payload = "\n\n" + "\n\n".join(missing).strip() + "\n"
    ap = subprocess.run(["ssh", HOST, f"cat >> {RPATH}"], input=payload,
                        capture_output=True, text=True)
    if ap.returncode != 0:
        print(f"append failed: {ap.stderr}", file=sys.stderr)
        return 1
    print(f"merged {len(missing)} section(s); remote untouched otherwise")
    return 0


if __name__ == "__main__":
    sys.exit(main())
