"""Discovery API v1 — the one front door above the catalog.

POST /designs                       {aisle?, name?, atoms?} -> design
POST /designs/{id}/requirements     {atoms: [...], replace?}  -> atoms
GET  /designs/{id}                  -> design + latest summary
POST /designs/{id}/discover         -> cohorts + constraint driver
GET  /designs/{id}/ledger           ?cohort=&limit= -> verdict rows
GET  /designs/{id}/next-question    -> knife ranking by expected reduction

Read-only over catalog.db (mode=ro); mutable state is the design only.
Every discover result is stamped with the corpus release it ran against
(spec section 45). Served by harness.discovery.api:main — stdlib, no
dependencies, same shape as the pipeline front door.
"""

from __future__ import annotations

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from harness.discovery import service

DESIGNS_DB = os.environ.get("DESIGNS_DB",
                            "/Volumes/M5_4TB/extract-results/designs.db")
CATALOG = os.environ.get("DISCOVERY_CATALOG",
                         "/Volumes/M5_4TB/extract-results/catalog.db")


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
        try:
            m = re.match(r"^/designs/([\w\-]+)$", route)
            if m:
                return self._json(200, service.get_design(
                    DESIGNS_DB, m.group(1)))
            m = re.match(r"^/designs/([\w\-]+)/ledger$", route)
            if m:
                qs = self.path_qs_parse()
                return self._json(200, service.ledger(
                    DESIGNS_DB, m.group(1),
                    cohort=qs.get("cohort"), limit=int(qs.get("limit", 50))))
            m = re.match(r"^/designs/([\w\-]+)/next-question$", route)
            if m:
                return self._json(200, service.next_question(
                    DESIGNS_DB, m.group(1), catalog_path=CATALOG))
            return self._json(404, {"error": "unknown route"})
        except KeyError as e:
            return self._json(404, {"error": str(e)})
        except ValueError as e:
            return self._json(422, {"error": str(e)})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._json(400, {"error": "bad json"})
        try:
            m = re.match(r"^/designs$", self.path)
            if m:
                design_id = service.create_design(
                    DESIGNS_DB, aisle=req.get("aisle", "power"),
                    name=req.get("name"))
                if req.get("atoms"):
                    service.add_requirements(
                        DESIGNS_DB, design_id, req["atoms"])
                return self._json(201, service.get_design(
                    DESIGNS_DB, design_id))
            m = re.match(r"^/designs/([\w\-]+)/requirements$", self.path)
            if m:
                atoms = service.add_requirements(
                    DESIGNS_DB, m.group(1), req.get("atoms") or [],
                    replace=bool(req.get("replace")))
                return self._json(200, {"design_id": m.group(1),
                                        "atoms": atoms})
            m = re.match(r"^/designs/([\w\-]+)/discover$", self.path)
            if m:
                return self._json(200, service.discover(
                    DESIGNS_DB, m.group(1), catalog_path=CATALOG))
            return self._json(404, {"error": "unknown route"})
        except KeyError as e:
            return self._json(404, {"error": str(e)})
        except ValueError as e:
            return self._json(422, {"error": str(e)})

    def path_qs_parse(self) -> dict:
        _, _, qs = self.path.partition("?")
        out = {}
        for pair in qs.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                out[k] = v
        return out


def main(listen=None):
    host = os.environ.get("DISCOVERY_HOST", "127.0.0.1")
    port = int(os.environ.get("DISCOVERY_PORT", "8790"))
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
