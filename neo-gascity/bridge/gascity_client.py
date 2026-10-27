"""Thin client for the Gas City supervisor REST API (v0).

Read operations plus one mutation (create_bead). Every mutation carries the
X-GC-Request anti-CSRF header and an Idempotency-Key (api.md). No retries
live here — retry policy belongs to handoff.py.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


class ApiError(Exception):
    def __init__(self, status: int, body: dict[str, Any] | str):
        self.status = status
        self.body = body
        self.code = body.get("code", "") if isinstance(body, dict) else ""
        super().__init__(f"HTTP {status} {self.code}: {str(body)[:200]}")


class GasCityClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8372", timeout: float = 20.0):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _req(self, method: str, path: str, body: dict | None = None,
             headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], Any]:
        req = urllib.request.Request(self.base + path, method=method)
        req.add_header("Accept", "application/json")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, data, timeout=self.timeout) as r:
                raw = r.read().decode() or "{}"
                return r.status, dict(r.headers), json.loads(raw)
        except urllib.error.HTTPError as e:
            raw = e.read().decode() or "{}"
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw
            raise ApiError(e.code, parsed) from None

    # ---- reads -------------------------------------------------------
    def health(self) -> dict:
        _, _, b = self._req("GET", "/health")
        return b

    def cities(self) -> dict:
        _, _, b = self._req("GET", "/v0/cities")
        return b

    def city_status(self, city: str) -> dict:
        _, _, b = self._req("GET", f"/v0/city/{city}/status")
        return b

    def agents(self, city: str) -> dict:
        _, _, b = self._req("GET", f"/v0/city/{city}/agents")
        return b

    def beads(self, city: str, label: str | None = None, status: str | None = None,
              limit: int = 100) -> tuple[dict, str | None]:
        q = f"?limit={limit}"
        if label:
            q += f"&label={urllib.request.quote(label)}"
        if status:
            q += f"&status={status}"
        _, h, b = self._req("GET", f"/v0/city/{city}/beads{q}")
        return b, h.get("X-GC-Index")

    def bead(self, city: str, bead_id: str) -> dict:
        _, _, b = self._req("GET", f"/v0/city/{city}/bead/{bead_id}")
        return b

    def sessions(self, city: str) -> dict:
        _, _, b = self._req("GET", f"/v0/city/{city}/sessions")
        return b

    def pending(self, city: str) -> dict:
        _, _, b = self._req("GET", f"/v0/city/{city}/pending")
        return b

    def events(self, city: str, after_seq: int | None = None, limit: int = 50) -> dict:
        q = f"?limit={limit}" + (f"&after_seq={after_seq}" if after_seq is not None else "")
        _, _, b = self._req("GET", f"/v0/city/{city}/events{q}")
        return b

    # ---- the one mutation --------------------------------------------
    def create_bead(self, city: str, payload: dict, idempotency_key: str) -> tuple[dict, str | None]:
        """Idempotent bead create. Returns (body, X-GC-Index)."""
        _, h, b = self._req("POST", f"/v0/city/{city}/beads", payload,
                            headers={"X-GC-Request": idempotency_key,
                                     "Idempotency-Key": idempotency_key})
        return b, h.get("X-GC-Index")


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
