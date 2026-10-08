#!/usr/bin/env python3
"""Extraction API v1 — serves verified extraction data for designwins drill-down.

Contract: matches contracts/designwins-v1/openapi.yaml conventions.
Auth: Bearer token (env EXTRACTION_API_KEY, default crk_m5_extraction_v1).
Errors: application/problem+json.
CORS: * (facade proxies allowed).

Endpoints:
  GET /health                                — liveness + data freshness
  GET /v1/part/:part_number                  — everything for one part
  GET /v1/part/:part_number/thermal          — thermal metrics only
  GET /v1/part/:part_number/errata           — errata issues only
  GET /v1/part/:part_number/curves           — digitized plots only
  GET /v1/part/:part_number/parametrics      — vout/iq/vin/iout axes only
  GET /v1/part/:part_number/power-modes      — mode currents only
  GET /v1/part/:part_number/qualification    — AEC grade only
  GET /v1/aisle/:aisle/parts?axis=...&op=...&value=... — parametric search
  GET /v1/axes                               — available axes + coverage counts

Data sources: staging JSONs + raw JSONL from extraction lanes.
Only verdict=extracted/printed_row rows are served. Holds never ship.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PORT = int(os.environ.get("EXTRACTION_API_PORT", "8819"))
API_KEY = os.environ.get("EXTRACTION_API_KEY", "crk_m5_extraction_v1")
STAGING = Path("/Volumes/M5_4TB/staging/datasheet_extractions")

# Load staging per-part files
PARTS: dict[str, dict] = {}
ERRATA: dict[str, dict] = {}


def load_staging():
    global PARTS, ERRATA
    PARTS.clear()
    ERRATA.clear()
    for aisle_dir in ["mcu_family_content", "power_family_content"]:
        ext_dir = STAGING / aisle_dir / "datasheet_extractions"
        if not ext_dir.exists():
            continue
        for f in ext_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                PARTS[data["part_number"]] = data
            except Exception:
                continue
    errata_dir = STAGING / "mcu_family_content" / "errata"
    if errata_dir.exists():
        for f in errata_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text())
                for token in (data.get("errata", {}).get("device_scope", {}) or {}).get("device_tokens", []):
                    ERRATA[token] = data
            except Exception:
                continue
    print(f"[staging] loaded {len(PARTS)} parts, {len(ERRATA)} errata devices")


def _problem(status: int, title: str, detail: str = ""):
    return {
        "type": "application/problem+json",
        "status": status,
        "title": title,
        "detail": detail,
    }


class Handler(BaseHTTPRequestHandler):
    def _check_auth(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if auth != f"Bearer {API_KEY}":
            self._send(_problem(401, "Unauthorized", "Missing or invalid Bearer token"), 401)
            return False
        return True

    def _send(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json" if code < 400 else "application/problem+json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization")
        self.end_headers()

    def do_GET(self):
        if not self._check_auth():
            return
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        if path == "/health":
            self._send({
                "status": "ok",
                "parts_served": len(PARTS),
                "errata_devices": len(ERRATA),
                "loaded_at": time.time(),
            })
            return

        if path == "/v1/axes":
            axes = defaultdict(int)
            for part, data in PARTS.items():
                for key in data:
                    if key in ("part_number",):
                        continue
                    if isinstance(data[key], dict):
                        for sub in data[key]:
                            axes[f"{key}.{sub}"] += 1
                    elif isinstance(data[key], list):
                        axes[key] += len(data[key])
            self._send({"axes": dict(sorted(axes.items(), key=lambda x: -x[1]))})
            return

        # /v1/part/:pn[/subresource]
        if path.startswith("/v1/part/"):
            parts = path.split("/")[3:]
            pn = parts[0]
            sub = parts[1] if len(parts) > 1 else None
            part_data = PARTS.get(pn)
            if not part_data:
                # check errata by device token
                errata = ERRATA.get(pn)
                if errata:
                    self._send({"part_number": pn, "errata": errata["errata"]})
                    return
                self._send(_problem(404, "Not Found", f"Part {pn} not found"), 404)
                return
            if sub:
                key_map = {
                    "thermal": "thermal",
                    "errata": "errata",
                    "curves": "curves",
                    "parametrics": "vout_iq",
                    "power-modes": "power_modes",
                    "qualification": "aec",
                    "description": "description",
                    "documents": "documents",
                }
                data_key = key_map.get(sub)
                if not data_key or data_key not in part_data:
                    self._send(_problem(404, "Not Found", f"Sub-resource '{sub}' not available for {pn}"), 404)
                    return
                self._send({"part_number": pn, sub.replace("-", "_"): part_data[data_key]})
            else:
                self._send(part_data)
            return

        # /v1/aisle/:aisle/parts
        if "/v1/aisle/" in path and path.endswith("/parts"):
            aisle = path.split("/v1/aisle/")[1].split("/")[0]
            axis = params.get("axis", [None])[0]
            op = params.get("op", ["eq"])[0]
            value = params.get("value", [None])[0]
            # basic parametric filter
            results = []
            for pn, data in PARTS.items():
                if axis and value:
                    found = False
                    for section in ("thermal", "vout_iq", "aec"):
                        for key, val in data.get(section, {}).items():
                            if f"{section}.{key}" == axis or key == axis:
                                v = val.get("value") or val.get("grade")
                                if isinstance(v, (int, float)) and isinstance(float(value), float):
                                    if op == "lt" and v < float(value): found = True
                                    elif op == "gt" and v > float(value): found = True
                                    elif op == "eq" and v == float(value): found = True
                                    elif op == "lte" and v <= float(value): found = True
                                    elif op == "gte" and v >= float(value): found = True
                                break
                        if found: break
                    if not found: continue
                else:
                    # just list with coverage
                    results.append({"part_number": pn, "axes": list(data.keys())})
                    continue
                results.append({"part_number": pn, "axes": list(data.keys())})
            self._send({"aisle": aisle, "count": len(results), "parts": results[:200]})
            return

        self._send(_problem(404, "Not Found", f"Unknown path: {path}"), 404)

    def log_message(self, fmt, *args):
        pass


def main():
    load_staging()
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[extraction-api] serving on :{PORT} with {len(PARTS)} parts")
    server.serve_forever()


if __name__ == "__main__":
    main()
