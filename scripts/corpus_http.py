#!/usr/bin/env python3
"""Read-only threaded file server for the corpus — seven's fetch path.

Serves the m5-4tb tree over the LAN so fleet boxes without a NAS mount can
stream PDFs (burn_pass --map /Volumes/M5_4TB=http://<m5>:8811). Read-only
GET; no upload; binds the LAN interface only.
"""

import argparse
import functools
import http.server
import socketserver


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silent: the burn writes its own ledger


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--bind", default="192.168.4.59")
    ap.add_argument("--port", type=int, default=8811)
    args = ap.parse_args()
    handler = functools.partial(Handler, directory=args.root)
    with ThreadingHTTPServer((args.bind, args.port), handler) as srv:
        print(f"serving {args.root} on {args.bind}:{args.port}", flush=True)
        srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
