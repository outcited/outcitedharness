#!/usr/bin/env python3
"""Alias private filesystem paths out of a committed evidence artifact.

Public-artifact hygiene (DW-TRUTH-FUNNEL-01, issue #5): nothing committed or
posted may contain NAS paths, mount points, private URLs, or credentials.
Full paths stay in the local session log only; committed JSON/MD carry aliases
such as ``nas://m5-4tb/...``.

The real mount-path -> alias mapping is PRIVATE: it lives in a gitignored
local config (``scripts/evidence_path_aliases.local.json``), never in this
committed script. ``scripts/evidence_path_aliases.template.json`` documents the
key shape with dummy values.

This is a pure text transform: it preserves every byte except the matched
private path prefixes, so the aliased artifact remains deterministic and
diffable. It never touches the source file (read-only) — it writes an aliased
copy.

usage:
  python3 scripts/alias_evidence_paths.py IN OUT [IN OUT ...]

config:
  scripts/evidence_path_aliases.local.json (gitignored), or set
  EVIDENCE_PATH_ALIASES to point at another copy. JSON shape:
    {"aliases": [["./private/prefix", "public://alias"], ...]}
  Entries are applied in listed order (longest/most-specific prefix first).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "evidence_path_aliases.local.json"


class AliasConfigError(RuntimeError):
    pass


def load_aliases(path: Path | None = None) -> list[tuple[str, str]]:
    if path is None:
        env = os.environ.get("EVIDENCE_PATH_ALIASES")
        path = Path(env) if env else DEFAULT_CONFIG
    if not path.exists():
        raise AliasConfigError(
            f"private alias config not found: {path}\n"
            "This file is intentionally NOT committed (public-artifact hygiene). "
            "Copy scripts/evidence_path_aliases.template.json to "
            f"{DEFAULT_CONFIG} and fill in the real mount paths, or set "
            "EVIDENCE_PATH_ALIASES to an existing config."
        )
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise AliasConfigError(f"cannot read alias config {path}: {exc}") from exc
    pairs = data.get("aliases")
    if not isinstance(pairs, list) or not pairs:
        raise AliasConfigError(f"alias config {path} must define a non-empty 'aliases' list")
    aliases: list[tuple[str, str]] = []
    for i, pair in enumerate(pairs):
        if (not isinstance(pair, list) or len(pair) != 2
                or not all(isinstance(v, str) for v in pair)):
            raise AliasConfigError(
                f"alias config {path}: entry {i} must be a [source, alias] pair of strings"
            )
        aliases.append((pair[0], pair[1]))
    return aliases


def alias_text(text: str, aliases: list[tuple[str, str]]) -> str:
    for real, alias in aliases:
        text = text.replace(real, alias)
    return text


def alias_file(src: Path, dst: Path, aliases: list[tuple[str, str]]) -> None:
    dst.write_text(alias_text(src.read_text(errors="replace"), aliases))


def main(argv) -> int:
    if len(argv) < 3 or len(argv) % 2 == 0:
        print(__doc__)
        return 2
    try:
        aliases = load_aliases()
    except AliasConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for i in range(1, len(argv), 2):
        src, dst = Path(argv[i]), Path(argv[i + 1])
        alias_file(src, dst, aliases)
        print(f"aliased {src} -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))