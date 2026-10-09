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

POST /v1/discovery/engineering-session is feature-flagged
(SEARCH_ENABLE_ENGINEERING_SESSION=1) and protected: it requires
``Authorization: Bearer $SEARCH_SESSION_TOKEN``. With no token configured
the route answers 503 auth_not_configured rather than serving decisions
unauthenticated (B4); loopback development requires the explicit
SEARCH_ALLOW_LOCAL_NOAUTH=1 opt-in together with a loopback bind and a
loopback client. Secrets come from the environment only.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from harness.search import cohort as cohort_mod
from harness.search import identity as identity_mod
from harness.search import indexer as indexer_mod
from harness.search import query as query_service
from harness.search import units as units_store

SEARCH_DB = os.environ.get(
    "SEARCH_DB", "/Volumes/M5_4TB/extract-results/search_index.db")
CATALOG = os.environ.get(
    "SEARCH_CATALOG", "/Volumes/M5_4TB/extract-results/catalog.db")
POWER_WAVE = os.environ.get(
    "SEARCH_POWER_WAVE",
    "/Volumes/M5_4TB/extract-results/power-topology-v1.jsonl")
IDENTITY_DB = os.environ.get(
    "SEARCH_IDENTITY_DB",
    "/Volumes/M5_4TB/extract-results/facet_identity.db")

# Pilot gate (release decision 2026-10-08): only categories whose candidate
# and taxonomy coverage passed review are served. MCU and connector facets
# stay disabled until their coverage gates are met — an honestly-empty
# cohort is correct, but the pilot must not offer aisles it cannot serve.
FACET_PILOT_CATEGORIES = frozenset(
    c.strip() for c in os.environ.get(
        "SEARCH_FACET_CATEGORIES", "power").split(",") if c.strip())

_ALLOWED_FILTERS = ("vendor", "family", "category", "grain",
                    "min_verification", "include_retired", "evidence_grade")

_EMBEDDER = None
_SESSION_CACHE = None

# --- authentication posture (B4: fail closed) -------------------------------
#
# The engineering-session route is a protected route: it returns engineering
# decisions with provenance. A missing secret must never open it, so the
# default posture when SEARCH_SESSION_TOKEN is unset is 503
# auth_not_configured. Loopback development stays possible only behind an
# explicit opt-in AND a loopback bind AND a loopback client — never by
# accident, and never by leaving a variable unset.
SESSION_TOKEN_ENV = "SEARCH_SESSION_TOKEN"
LOCAL_NOAUTH_ENV = "SEARCH_ALLOW_LOCAL_NOAUTH"
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_LOOPBACK_CLIENTS = ("127.0.0.1", "::1")

AUTH_ERRORS = {
    401: ("unauthorized", "bearer token required"),
    403: ("forbidden", "invalid bearer token"),
    503: ("auth_not_configured",
          f"the engineering-session route is protected but "
          f"{SESSION_TOKEN_ENV} is not set; auth fails closed rather than "
          f"serving decisions unauthenticated — configure a bearer token, "
          f"or for loopback development only set {LOCAL_NOAUTH_ENV}=1"),
}


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _bind_host() -> str:
    return os.environ.get("SEARCH_HOST", "127.0.0.1")


def _local_noauth_allowed(client_host: str | None) -> bool:
    """Explicit loopback development posture (never the default)."""
    if not _env_flag(LOCAL_NOAUTH_ENV):
        return False
    if _bind_host() not in _LOOPBACK_HOSTS:
        return False
    return client_host in _LOOPBACK_CLIENTS


def session_auth_status(headers, client_host: str | None = None):
    """Authorization decision for the protected session route.

    Returns ``(None, reason)`` when authorized, otherwise
    ``(http_status, code)``. Comparison is constant-time. A configured token
    always wins over the development opt-in.
    """
    token = (os.environ.get(SESSION_TOKEN_ENV) or "").strip()
    header = headers.get("Authorization") or ""
    if not token:
        if _local_noauth_allowed(client_host):
            return None, "local_development_noauth"
        return 503, "auth_not_configured"
    if not header:
        return 401, "unauthorized"
    if not hmac.compare_digest(header, f"Bearer {token}"):
        return 403, "forbidden"
    return None, "authorized"


def _session_enabled() -> bool:
    return os.environ.get("SEARCH_ENABLE_ENGINEERING_SESSION") == "1"


def _read_only_connect(db_path: str | None = None):
    """Read-only connection over the index (no schema side effects).

    ``units.connect`` runs the schema script and can persist release
    metadata, which is wrong for serving and probe routes: those must be
    side-effect free. If the database predates a table a route needs, the
    resulting OperationalError surfaces as the fail-closed 500 the route
    already produces.
    """
    import sqlite3
    con = sqlite3.connect(f"file:{db_path or SEARCH_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


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

    def _discovery_facets(self, qs: dict):
        import sqlite3
        category = qs.get("category")
        if not category:
            return self._json(422, {"error": "category is required"})
        if category not in FACET_PILOT_CATEGORIES:
            return self._json(403, {
                "error": "category_gated",
                "category": category,
                "allowed": sorted(FACET_PILOT_CATEGORIES),
                "notice": (f"category '{category}' is gated out of the "
                           "facet pilot: candidate/taxonomy coverage does "
                           "not yet satisfy the release gates (see "
                           "FACET02_PILOT_HANDOFF.md). Evidence search "
                           "(/v1/search) is unaffected."),
            })
        constraints = {}
        for key in qs:
            if key.startswith("c."):
                constraints[key[2:]] = qs[key]
        catalog = sqlite3.connect(
            f"file:{CATALOG}?mode=ro", uri=True)
        catalog.row_factory = sqlite3.Row
        search = sqlite3.connect(
            f"file:{SEARCH_DB}?mode=ro", uri=True)
        search.row_factory = sqlite3.Row
        icon = None
        try:
            if os.path.exists(IDENTITY_DB):
                icon = identity_mod.connect(IDENTITY_DB)
            aisle_map = indexer_mod.aisle_map_from_wave(POWER_WAVE)
            built = cohort_mod.build_cohort(
                catalog_con=catalog, search_con=search,
                category=category, subcategory=qs.get("subcategory"),
                aisle_map=aisle_map, identity_con=icon)
            result = cohort_mod.facets_for_cohort(
                built, constraints=constraints)
            result["evidence_policy"] = qs.get(
                "evidence_quality", "include_all")
            return self._json(200, result)
        finally:
            catalog.close()
            search.close()
            if icon is not None:
                icon.close()

    def _identity_snapshot(self, qs: dict):
        import sqlite3
        category = qs.get("category")
        if not category:
            return self._json(422, {"error": "category is required"})
        if category not in FACET_PILOT_CATEGORIES:
            return self._json(403, {
                "error": "category_gated", "category": category,
                "allowed": sorted(FACET_PILOT_CATEGORIES),
                "notice": "identity snapshots follow the facet pilot gate"})
        catalog = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True)
        catalog.row_factory = sqlite3.Row
        icon = identity_mod.connect(IDENTITY_DB)
        try:
            aisle_map = indexer_mod.aisle_map_from_wave(POWER_WAVE)
            category_of = {}
            for opn, aisle in aisle_map.items():
                cat, _sub = cohort_mod._aisle_category(aisle)
                if cat:
                    category_of[opn] = cat
            payload = identity_mod.build_identity_snapshot(
                catalog, icon, category=category, category_of=category_of)
            if qs.get("candidate"):
                want = f"opn:{qs['candidate']}"
                payload["candidates"] = [
                    c for c in payload["candidates"]
                    if c["canonical_id"] == want]
            snap = identity_mod.snapshot(icon, payload)
            return self._json(200, {
                "snapshot_id": snap["snapshot_id"],
                "schema": snap["payload"]["schema"],
                "counts": snap["counts"],
                "candidates": snap["payload"]["candidates"],
                "authority_note": snap["payload"]["authority_note"],
            })
        finally:
            catalog.close()
            icon.close()

    def do_GET(self):
        route = self.path.partition("?")[0]
        qs = self.path_qs_parse()
        if route == "/v1/discovery/facets":
            try:
                return self._discovery_facets(qs)
            except ValueError as e:
                return self._json(422, {"error": str(e)})
            except Exception as e:
                # fail closed: a broken cohort source must never
                # masquerade as an empty cohort
                return self._json(500, {
                    "error": "cohort_source_unavailable",
                    "detail": f"{type(e).__name__}: {e}"})
        if route == "/v1/identity/snapshot":
            try:
                return self._identity_snapshot(qs)
            except ValueError as e:
                return self._json(422, {"error": str(e)})
            except Exception as e:
                return self._json(500, {
                    "error": "identity_source_unavailable",
                    "detail": f"{type(e).__name__}: {e}"})
        # Read-only routes must not write (B5): an unauthenticated health
        # probe used to recompute the release fingerprint and persist it
        # under an EMPTY policy identity, clobbering the stored policy and
        # invalidating every other policy's cache.
        con = _read_only_connect()
        try:
            if route == "/v1/health":
                release = units_store.release_view(con)
                return self._json(200, {"ok": True, **release})
            if route == "/v1/release":
                return self._json(200, units_store.release_view(con))
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

    def path_qs_parse(self) -> dict:
        _, _, qs = self.path.partition("?")
        out = {}
        for pair in qs.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                out[k] = urllib.parse.unquote_plus(v)
        return out

    def _engineering_session(self):
        import sqlite3
        from harness.search import orchestrator
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})
        catalog = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True)
        catalog.row_factory = sqlite3.Row
        search = sqlite3.connect(f"file:{SEARCH_DB}?mode=ro", uri=True)
        search.row_factory = sqlite3.Row
        icon = identity_mod.connect(IDENTITY_DB) \
            if os.path.exists(IDENTITY_DB) else None
        m4_url = os.environ.get("SEARCH_M4_URL")
        global _SESSION_CACHE
        if _SESSION_CACHE is None:
            _SESSION_CACHE = orchestrator.SessionCache(max_entries=64)
        try:
            transport = (orchestrator.m4_client.make_transport(
                "http", base_url=m4_url) if m4_url else
                orchestrator.m4_client.make_transport("frozen"))
            result = orchestrator.engineering_session(
                req, catalog_con=catalog, search_con=search,
                identity_con=icon,
                aisle_map=indexer_mod.aisle_map_from_wave(POWER_WAVE),
                transport=transport, cache=_SESSION_CACHE)
            return self._json(200, result)
        except ValueError as e:
            return self._json(422, {"error": str(e)})
        except Exception as e:  # fail closed, never a false empty cohort
            return self._json(500, {
                "error": "orchestration_failed",
                "detail": f"{type(e).__name__}: {e}"})
        finally:
            catalog.close()
            search.close()
            if icon is not None:
                icon.close()

    def do_POST(self):
        route = self.path.partition("?")[0]
        if route == "/v1/discovery/engineering-session":
            # feature flag (R9/R10.20): off => route does not exist;
            # existing API behavior is untouched
            if not _session_enabled():
                return self._json(404, {"error": "unknown route"})
            # B4: authorization is decided BEFORE any store is opened or any
            # orchestration runs — an unauthorized request does no work.
            status, code = session_auth_status(
                self.headers, (self.client_address or (None,))[0])
            if status is not None:
                err, detail = AUTH_ERRORS[status]
                return self._json(status, {"error": err, "detail": detail,
                                           "code": code})
            return self._engineering_session()
        if route != "/v1/search":
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
    if host not in _LOOPBACK_HOSTS and \
            os.environ.get("SEARCH_ALLOW_REMOTE") != "1":
        raise SystemExit(
            "refusing to bind non-localhost by default: set "
            "SEARCH_ALLOW_REMOTE=1 only after authentication and network "
            "access policy are configured")
    # B4: exposing the protected session route beyond loopback additionally
    # requires a configured bearer — the loopback development opt-in does
    # not travel with the socket.
    if host not in _LOOPBACK_HOSTS and _session_enabled() and \
            not (os.environ.get(SESSION_TOKEN_ENV) or "").strip():
        raise SystemExit(
            f"refusing to bind non-localhost with the engineering-session "
            f"route enabled and no {SESSION_TOKEN_ENV}: auth fails closed; "
            f"configure a bearer token before cross-machine exposure")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
