#!/usr/bin/env python3
"""Thin model-call shim for the dw-soak Rust worker (M5 Rust factory soak).

All model behavior — prompts, endpoint chain, parsing, repair protocol — is
imported from the LIVE scripts/burn_aisle.py in the main checkout (the exact
module the Python factory soak runs), so the Rust soak's model lane is
byte-identical to Python's. This process NEVER touches durable state: the
queue, leases, verification, page attribution and outputs all live in the
Rust controller (dw-controller). It is the "thin provider HTTP shell" the
dw-model handoff names — Python only because the HTTP endpoints already
have exactly one client implementation.

Protocol: argv[1] in {propose, repair}; one JSON payload on stdin; one JSON
result on stdout. Exit 0 even on model failure (the nulls carry the
fail-closed semantics); exit 2 on malformed use.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys

BURN_AISLE = "/Users/samkim/Harnessv1/scripts/burn_aisle.py"

_spec = importlib.util.spec_from_file_location("burn_aisle", BURN_AISLE)
burn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(burn)

# RUST-CUTOVER-01 frozen-response replay: the cache key is the exact
# request tuple (endpoint, model, canonical messages). Any engine that
# builds the same HTTP request gets the same frozen response — so a cache
# MISS under replay is itself a divergence signal (different prompt/text).
SHIM_MODE = os.environ.get("SOAK_SHIM_MODE", "live")  # live | record | replay
SHIM_CACHE = os.environ.get("SOAK_SHIM_CACHE", "")


def _canon_content(s: str) -> str:
    """Key-normalize a content string: decode JSON-style \\uXXXX escapes so
    Python's ensure_ascii dumps and Rust serde_json's raw UTF-8 hash the
    same. The endpoint decodes both to the same string; only the wire bytes
    differed (found by the LM5100B replay, RUST-CUTOVER-01)."""
    try:
        return s.encode("utf-8").decode("unicode_escape").encode(
            "latin-1", "backslashreplace").decode("utf-8") if "\\" in s else s
    except Exception:
        return s


def request_key(endpoint, model, messages):
    norm = []
    for m in messages:
        norm.append({
            "role": m.get("role", ""),
            "content": _canon_content(m.get("content", "")),
        })
    canon = json.dumps(norm, sort_keys=True, ensure_ascii=False)
    raw = f"{endpoint}\x00{model}\x00{canon}".encode()
    return hashlib.sha256(raw).hexdigest()


def _cache_load():
    entries = {}
    if SHIM_CACHE and os.path.exists(SHIM_CACHE):
        with open(SHIM_CACHE, encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    d = json.loads(line)
                    entries[d["key"]] = d["response"]
    return entries


def _cache_append(key, response):
    if SHIM_CACHE:
        with open(SHIM_CACHE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(
                {"key": key, "response": response}, ensure_ascii=False) + "\n")


def call(endpoint, model, messages, timeout=240):
    """burn_aisle.call with live/record/replay/fill semantics."""
    import os as _os
    _trace = _os.environ.get("SOAK_SHIM_TRACE")
    if _trace:
        with open(_trace, "a", encoding="utf-8") as _fh:
            _fh.write(json.dumps({"endpoint": endpoint, "model": model,
                                  "messages": messages}, ensure_ascii=False) + "\n")
    if SHIM_MODE in ("replay", "fill"):
        cache = _cache_load()
        key = request_key(endpoint, model, messages)
        entry = cache.get(key)
        if entry is None and SHIM_MODE == "replay":
            sys.stderr.write(f"REPLAY_CACHE_MISS {key[:16]} {model}\n")
            raise RuntimeError("REPLAY_CACHE_MISS")
        if entry is not None:
            if isinstance(entry, dict) and entry.get("exception"):
                raise RuntimeError("FROZEN_ENDPOINT_EXCEPTION")
            return entry
        # fill mode: cache miss -> call live and append
    try:
        response = burn.call(endpoint, model, messages, timeout=timeout)
    except Exception as exc:
        if SHIM_MODE in ("record", "fill"):
            _cache_append(request_key(endpoint, model, messages),
                          {"exception": True, "kind": type(exc).__name__})
        raise
    if SHIM_MODE in ("record", "fill"):
        _cache_append(request_key(endpoint, model, messages), response)
    return response


def propose(payload):
    part, text = payload["part"], payload["text"]
    tier = None
    claims = None
    chain = [(burn.STUDENT, "power-tables-student-v1")] + list(burn.LOCAL_CHAIN)
    for endpoint, model in chain:
        try:
            claims = call(
                endpoint,
                model,
                [
                    {"role": "system", "content": burn.STUDENT_PROMPT},
                    {
                        "role": "user",
                        "content": f"Part: {part}\n\nDatasheet pages:\n{text}",
                    },
                ],
            )
        except Exception:
            claims = None
        if isinstance(claims, list):
            tier = "local-v41" if endpoint == burn.V41 else (
                "student" if endpoint == burn.STUDENT else "cloud-v41"
            )
            break
    return {
        "claims": claims,
        "claims_raw": json.dumps(claims, ensure_ascii=False)
        if isinstance(claims, list)
        else None,
        "tier": tier,
    }


def repair(payload):
    bad = payload["bad"]
    text = payload["text"]
    # claims arrive as the RAW JSON text of the propose/repair response —
    # parsing HERE (Python) preserves the model's key order, so the repair
    # payload is byte-identical to burn_aisle's regardless of which engine
    # drives (RUST-CUTOVER-01: the sorted-map round trip through serde_json
    # reordered keys and diverged the repair prompt)
    claims = payload["claims_raw"] if "claims_raw" in payload else payload["claims"]
    if isinstance(claims, str):
        claims = json.loads(claims)
    try:
        fixes = call(
            burn.V41,
            "deepseek-v4.1-flash",
            [
                {
                    "role": "system",
                    "content": burn.REPAIR
                    % json.dumps(
                        [{"i": i, "reason": "quote_mismatch"} for i in bad]
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Claims:\n{json.dumps([claims[i] for i in bad])}"
                        f"\n\nText:\n{text}"
                    ),
                },
            ],
        )
    except Exception:
        fixes = None
    if isinstance(fixes, list):
        for f in fixes:
            if isinstance(f, dict) and "i" in f:
                claims[f["i"]] = {
                    **claims[f["i"]],
                    **{k: v for k, v in f.items() if k != "i"},
                }
    return {"claims": claims, "claims_raw": json.dumps(claims, ensure_ascii=False)}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in ("propose", "repair"):
        print("usage: soak_model_shim.py {propose|repair} < payload.json", file=sys.stderr)
        return 2
    payload = json.load(sys.stdin)
    result = propose(payload) if sys.argv[1] == "propose" else repair(payload)
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

