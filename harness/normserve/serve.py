"""Serve: GET /api/v1/opn/<OPN> — stored JSON, no LLM at serve time.

stdlib http.server (no web framework in this venv). ~10ms target comes
from the opn index; this is a pure key lookup.
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .schema import SCHEMA_VERSION


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            parts = [p for p in path.split("/") if p]
            if (len(parts) == 4 and parts[0] == "api" and parts[1] == "v1"
                    and parts[2] == "opn"):
                opn = parts[3]
                claims = store.claims_for_opn(opn)
                body = json.dumps({
                    "opn": opn.upper(),
                    "schema_version": SCHEMA_VERSION,
                    "count": len(claims),
                    "claims": claims,
                }).encode()
                status = 200 if claims else 404
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                body = b'{"error": "not found", "routes": ["/api/v1/opn/<OPN>"]}'
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass

    return Handler


def serve(store, host="127.0.0.1", port=8801):
    server = ThreadingHTTPServer((host, port), make_handler(store))
    print(f"norm-serve listening on http://{host}:{port}/api/v1/opn/<OPN>")
    server.serve_forever()
