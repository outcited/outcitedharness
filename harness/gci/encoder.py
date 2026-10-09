from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from harness.gci.slicing import MAX_SLICE_CHARS


PRODUCTION_ENCODER_URL = "http://127.0.0.1:8800/v1/embeddings"
MAX_ENCODER_BATCH = 16
EMBED_DIM = 1024

# The encoder is pinned to localhost because GCI must talk to the embedder that
# holds the fine-tuned weights: a stock bge-m3 answers the same route with the
# same 1024 dims and different vectors, which silently invalidates the index.
# Relocating is an explicit, audited act — name the host:port in this env var
# (comma-separated) and nothing else changes. Empty by default, so the historic
# localhost-only policy is what you get unless someone deliberately widens it.
ENCODER_ALLOWLIST_ENV = "HARNESS_GCI_ENCODER_ALLOWLIST"
DEFAULT_ENCODER_HOSTS = frozenset({"127.0.0.1:8800", "localhost:8800"})
LOCALHOST_CANONICAL = "127.0.0.1:8800"


class EncoderPolicyError(RuntimeError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise EncoderPolicyError(f"encoder redirect refused: HTTP {code}")


def allowed_encoder_hosts() -> frozenset[str]:
    """host:port pairs the encoder may talk to. Exact match only, no wildcards."""
    hosts = set(DEFAULT_ENCODER_HOSTS)
    for entry in os.environ.get(ENCODER_ALLOWLIST_ENV, "").split(","):
        entry = entry.strip()
        if entry:
            hosts.add(entry)
    return frozenset(hosts)


def validate_encoder_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname or ""
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    if (
        parsed.scheme != "http"
        or netloc not in allowed_encoder_hosts()
        or parsed.path != "/v1/embeddings"
        or parsed.query
        or parsed.fragment
    ):
        allowed = ", ".join(sorted(allowed_encoder_hosts()))
        raise EncoderPolicyError(
            "GCI encoder must be http://<host>:<port>/v1/embeddings with "
            f"<host>:<port> in the allowlist (default {LOCALHOST_CANONICAL}; "
            f"currently allowed: {allowed}); got {url!r}"
        )
    canonical = LOCALHOST_CANONICAL if netloc == "localhost:8800" else netloc
    return f"http://{canonical}/v1/embeddings"


@dataclass(frozen=True)
class EncoderResponse:
    vectors: tuple[tuple[float, ...], ...]
    latency_ms: float


class StrictEncoder:
    def __init__(
        self,
        url: str = PRODUCTION_ENCODER_URL,
        *,
        timeout: float = 30.0,
        model: str = "bge-m3-cr-tapes-v1",
    ):
        self.url = validate_encoder_url(url)
        self.timeout = timeout
        self.model = model
        self._opener = urllib.request.build_opener(_NoRedirect())

    def embed(self, texts: list[str]) -> EncoderResponse:
        if not texts:
            return EncoderResponse((), 0.0)
        if len(texts) > MAX_ENCODER_BATCH:
            raise EncoderPolicyError(
                f"encoder batch {len(texts)} exceeds maximum {MAX_ENCODER_BATCH}"
            )
        if any(len(text) > MAX_SLICE_CHARS for text in texts):
            raise EncoderPolicyError(
                f"encoder input exceeds {MAX_SLICE_CHARS}-character GCI contract"
            )
        request = urllib.request.Request(
            self.url,
            data=json.dumps({"model": self.model, "input": texts}).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        started = time.perf_counter()
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
        except EncoderPolicyError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"encoder request failed: {type(exc).__name__}: {exc}") from exc
        latency = (time.perf_counter() - started) * 1000
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise RuntimeError("encoder returned unexpected row count")
        ordered = sorted(rows, key=lambda row: int(row.get("index", 0)))
        vectors = []
        for row in ordered:
            vector = row.get("embedding") if isinstance(row, dict) else None
            if not isinstance(vector, list) or len(vector) != EMBED_DIM:
                raise RuntimeError(f"encoder returned non-{EMBED_DIM}-dimensional vector")
            vectors.append(tuple(float(value) for value in vector))
        return EncoderResponse(tuple(vectors), latency)
