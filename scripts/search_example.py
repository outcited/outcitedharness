"""Consumer integration example (PRD-SEARCH-01 deliverable 6).

Shows the full contract round-trip an engineer-facing consumer uses:

1. describe the problem in ordinary language,
2. read the interpretation HYPOTHESES (never confirmed requirements),
3. inspect precisely-located evidence with provenance, applicability
   (coverage_kind verbatim — family evidence is never widened), and
   verification state,
4. pin the evidence release for reproducibility.

Usage:
  scripts/search_example.py                      # served mode (:8791)
  scripts/search_example.py "your problem"       # custom query
  SEARCH_URL=http://host:8791 scripts/search_example.py
  scripts/search_example.py --offline           # direct library call
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_QUERY = "Low-power wireless industrial sensor"

OFFLINE_NOTICE = "serving not reachable — falling back to offline library"


def served_example(url: str, q: str, limit: int = 5) -> None:
    body = json.dumps({"query": q, "limit": limit}).encode()
    req = urllib.request.Request(
        url.rstrip("/") + "/v1/search", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        render(json.loads(resp.read()))


def offline_example(q: str, limit: int = 5) -> None:
    from harness.search import query as query_service
    from harness.search import units
    con = units.connect(os.environ.get(
        "SEARCH_DB", "/Volumes/M5_4TB/extract-results/search_index.db"))
    render(query_service.search(con, q, limit=limit))


def render(response: dict) -> None:
    print("schema        :", response["schema"])
    print("query         :", response["query"]["original"])
    print("qualification :", response["qualification"])
    print("release       :", response["provenance"]["release"])
    print()
    print("interpretations (hypotheses, not requirements):")
    for interp in response["query"]["interpretations"]:
        print(f"  - {interp['label']}  [confidence {interp['confidence']}]"
              f"  axes: {', '.join(interp['axes'][:3])}")
    print()
    print("evidence units:")
    for u in response["units"]:
        apps = "; ".join(
            f"{a['value']} (coverage={a['coverage_kind']})"
            for a in u["applicability"]) or "none"
        rev = (u["revision"] or {}).get("rev_code") or "?"
        print(f"  [{u['grain']}] {u['unit_id'][:12]} score={u['score']}")
        print(f"      {u['text_repr'][:110]}")
        print(f"      {u['vendor']} rev={rev} state={u['verification_state']}"
              f" p.{u['page']} applicability: {apps}")
        print(f"      why: {', '.join(u['rationale'])}")
    print()
    print(response["notice"])
    print(f"took {response['took_ms']} ms")


def main() -> int:
    args = [a for a in sys.argv[1:]]
    q = next((a for a in args if not a.startswith("-")), DEFAULT_QUERY)
    limit = int(os.environ.get("SEARCH_EXAMPLE_LIMIT", "5"))
    if "--offline" in args:
        offline_example(q, limit)
        return 0
    url = os.environ.get("SEARCH_URL", "http://127.0.0.1:8791")
    try:
        served_example(url, q, limit)
    except OSError:
        print(f"[{OFFLINE_NOTICE}: {url}]")
        offline_example(q, limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
