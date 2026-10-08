"""Search API v1 — the one front door over the evidence index (PRD R4).

GET  /v1/health           -> {ok, release, unit_count}
GET  /v1/release          -> pinned index release details
GET  /v1/units/{id}       -> one full evidence unit
POST /v1/search           {query, limit?, filters?, interpretations?}
                          -> harness.search-response.v1 envelope

Every response carries the evidence release it ran against (reproducible;
see units.index_release). The envelope states qualification: none —
relevance is not engineering qualification, interpretations are
hypotheses, and applicability is reported exactly as extracted (family
evidence is never widened to OPN evidence).

Read-only over search_index.db. Served by harness.search.api:main —
stdlib only, same shape as the discovery front door (:8791).
"""

from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from harness.search import query as query_service
from harness.search import units as units_store

SEARCH_DB = os.environ.get(
    "SEARCH_DB", "/Volumes/M5_4TB/extract-results/search_index.db")

_ALLOWED_FILTERS = ("vendor", "family", "category", "grain",
                    "min_verification", "include_retired", "evidence_grade")

_EMBEDDER = None


def _embed():
    """Optional semantic path: activates only when SEARCH_EMBED_URL is
    configured (serving-qualified fleet embedders) and the index carries
    vectors. FTS-only otherwise — deterministic tier, no fleet cost."""
    global _EMBEDDER
    url = os.environ.get("SEARCH_EMBED_URL")
    if not url:
        return None
    if _EMBEDDER is None:
        from harness.search.query import make_embedder
        _EMBEDDER = make_embedder(
            url, os.environ.get("SEARCH_EMBED_MODEL",
                                "bge-m3-cr-tapes-v1"))
    return _EMBEDDER


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        route = self.path.partition("?")[0]
        con = units_store.connect(SEARCH_DB)
        try:
            if route == "/v1/health":
                release = units_store.index_release(con)
                return self._json(200, {"ok": True, **release})
            if route == "/v1/release":
                return self._json(200, units_store.index_release(con))
            m = re.match(r"^/v1/units/([0-9a-f]{32})$", route)
            if m:
                unit = units_store.get_unit(con, m.group(1))
                if not unit:
                    return self._json(404, {"error": "unknown unit"})
                return self._json(200, {
                    "schema": "harness.search-unit.v1", "unit": unit,
                    "release": units_store.index_release(con)["release"],
                    "qualification": "none"})
            return self._json(404, {"error": "unknown route"})
        finally:
            con.close()

    def do_POST(self):
        if self.path.partition("?")[0] != "/v1/search":
            return self._json(404, {"error": "unknown route"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})
        query = (req.get("query") or "").strip()
        if not query:
            return self._json(422, {"error": "query is required"})
        filters = {k: req["filters"][k] for k in _ALLOWED_FILTERS
                   if req.get("filters", {}).get(k) is not None}
        con = units_store.connect(SEARCH_DB)
        try:
            response = query_service.search(
                con, query,
                limit=int(req.get("limit", 10)),
                filters=filters,
                embed=_embed(),
                with_interpretations=bool(req.get("interpretations", True)))
            return self._json(200, response)
        except ValueError as e:
            return self._json(422, {"error": str(e)})
        finally:
            con.close()


def main(listen=None):
    host = os.environ.get("SEARCH_HOST", "127.0.0.1")
    port = int(os.environ.get("SEARCH_PORT", "8791"))
    # Localhost-only until auth, network policy, and cross-machine clients
    # are explicitly configured (review directive 2026-10-08).
    if host not in ("127.0.0.1", "localhost") and \
            os.environ.get("SEARCH_ALLOW_REMOTE") != "1":
        raise SystemExit(
            "refusing to bind non-localhost by default: set "
            "SEARCH_ALLOW_REMOTE=1 only after authentication and network "
            "access policy are configured")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
