"""Cloud vision client (MiMo volume lane) — same contract as the local one.

The local client stays fail-closed on private URLs (evidence never leaves
the premises by accident). This client is the deliberate cloud lane: the
owner-supplied MiMo token-plan endpoint, free+unlimited, used for volume
vision work where the deterministic gates (grounding, anchor, gold scorer)
equalize model quality — a weak read holds at the gates, it can never ship
wrong. Responses go through the same JSON parse + schema validation as the
local lane; a cloud answer is never provisional-free.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Mapping

import httpx

from harness.electronics.local_model import (
    RESPONSE_SCHEMAS,
    parse_json_response,
    validate_response,
)

CLOUD_RESULT_SCHEMA = "harness.electronics-cloud-model-result.v1"

SYSTEM = (
    "You extract electronics facts from printed evidence only. Do not "
    "infer absent values. Preserve printed units verbatim. Return JSON only."
)


def _normalize_curve_points(payload: Any) -> None:
    """[x, y] pair form is accepted and rewritten to {x, y} objects so the
    single schema validator stays strict."""

    if not isinstance(payload, dict):
        return
    for series in payload.get("series") or []:
        if not isinstance(series, dict):
            continue
        points = series.get("points")
        if not isinstance(points, list):
            continue
        normalized = []
        for point in points:
            if isinstance(point, (list, tuple)) and len(point) == 2:
                normalized.append({"x": point[0], "y": point[1]})
            else:
                normalized.append(point)
        series["points"] = normalized


class CloudVisionClient:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout_s: float = 900,
        extra_body: dict | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("cloud vision requires an api key")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self.timeout_s = timeout_s
        self.extra_body = extra_body or {}

    def extract(
        self,
        *,
        capability: str,
        image_path: Path,
        prompt: str,
    ) -> dict[str, Any]:
        schema = RESPONSE_SCHEMAS[capability]
        payload = image_path.expanduser().resolve(strict=True).read_bytes()
        image_sha256 = hashlib.sha256(payload).hexdigest()
        request = {
            "model": self.model,
            # budget must hold thinking + answer: the provider burns hidden
            # reasoning tokens first and truncates the answer otherwise
            # (measured: 4095 reasoning tokens at max_tokens 4096, empty
            # content — same failure class as MODEL_SETTINGS records).
            "max_tokens": 16000,
            "reasoning_effort": "low",
            **self.extra_body,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,"
                                + base64.b64encode(payload).decode("ascii")
                            },
                        },
                    ],
                },
            ],
        }
        request_sha = hashlib.sha256(
            json.dumps(request, sort_keys=True).encode()
        ).hexdigest()
        started = time.perf_counter()
        response = None
        for attempt in range(4):
            with httpx.Client(timeout=self.timeout_s) as client:
                try:
                    response = client.post(
                        f"{self.base_url}/chat/completions",
                        json=request,
                        headers={"Authorization": f"Bearer {self._api_key}"},
                    )
                    response.raise_for_status()
                    break
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code
                    if status not in (429, 500, 502, 503) or attempt == 3:
                        raise
                    retry_after = float(
                        exc.response.headers.get("retry-after") or 0
                    ) or (2.0 ** attempt) * 5.0
                    time.sleep(min(retry_after, 60.0))
        if response is None:
            raise ValueError("cloud vision retries exhausted")
        latency_ms = (time.perf_counter() - started) * 1000
        response.raise_for_status()
        body = response.json()
        try:
            text = str(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("cloud vision response has no content") from exc
        parsed = parse_json_response(text)
        # providers that echo the schema document wrap the payload in
        # {"properties": ...}; unwrap deterministically before validation
        if isinstance(parsed, dict):
            inner = parsed.get("properties")
            if isinstance(inner, dict) and (
                "axes" in inner or "series" in inner or "facts" in inner
                or "modes" in inner or "pins" in inner
            ):
                parsed = inner
        _normalize_curve_points(parsed)
        validate_response(parsed, schema)
        return {
            "schema": CLOUD_RESULT_SCHEMA,
            "provider": "cloud",
            "model": self.model,
            "capability": capability,
            "request_sha256": request_sha,
            "response_sha256": hashlib.sha256(text.encode()).hexdigest(),
            "image_sha256": image_sha256,
            "latency_ms": latency_ms,
            "usage": body.get("usage"),
            "result": parsed,
        }


def curve_prompt() -> str:
    """The typical_characteristics contract, image-only (no page text).

    Keys are described in prose, not as a schema document: providers echo
    embedded schema JSON back as the answer wrapper (measured). The
    contract is enforced by validate_response, not by prompt furniture.
    """

    return (
        "Digitize this datasheet plot. Return ONLY a JSON object with keys: "
        "title (string or null), axes (x and y each with label, unit, min, "
        "max), series (list of objects with name, condition, points). "
        "Points are [x, y] pairs in printed axis units at visually "
        "identifiable features (endpoints, bends, crossings, labeled "
        "markers) only. Copy plot title, axis labels and units, and legend "
        "names verbatim from the image. Do not invent samples, do not "
        "extrapolate past the plotted range, do not read values from "
        "another plot. JSON only, no prose."
    )


__all__ = ["CLOUD_RESULT_SCHEMA", "CloudVisionClient", "curve_prompt"]
