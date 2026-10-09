"""M4 engineering-decision client (PRD-DISCOVERY-01 R4).

Typed client for M4's decision authority. M5 orchestrates, filters, and
displays; it NEVER computes engineering eligibility or curve mathematics
itself. Two transports:

  frozen — pinned contract fixtures regenerated from the M4 evidence bundle
           (scripts/discovery01_freeze_m4_journeys.py). Contract-level
           integration only; explicitly reported as fixture, not live.
  http   — POST {base}/v1/engineering/decisions when the M4 experimental
           service (CURVE-08B, port 8793) is actually reachable. Contract
           version is verified against the response schema; mismatches are
           structured errors, never silent acceptance.

A failed M4 call raises M4ServiceError with a structured reason — it can
never surface as a valid empty-cohort engineering result.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CLIENT_SCHEMA = "harness.discovery01-m4-client.v1"
DECISION_SCHEMA_EXPECTED = "harness.electronics-decision-answer.v1"
M4_08B_CONTRACT = "fae-engineering-decisions-v1"


def project_m4_08b(payload: dict) -> tuple[dict, list[str]]:
    """Explicit, loss-reporting projection of M4's CURVE-08B adapter
    envelope onto M5's decision-answer contract. M4's schema is NOT
    adopted: every renamed or absent field is recorded in the returned
    mismatch list, and facts M4 does not publish stay absent (never
    invented)."""

    mismatches: list[str] = []
    canon = payload.get("canonical_envelope") or {}
    releases = payload.get("evidence_releases") or {}
    identity = payload.get("candidate_identity") or {}
    elig = payload.get("eligibility") or {}
    qual = canon.get("qualification") or {}
    pref = canon.get("preference") or {}

    if "schema" not in payload:
        mismatches.append("schema: M4 publishes 'contract' only; M5 "
                          "projects its own schema id")
    if "bundle_sha256" not in payload:
        mismatches.append("bundle_sha256: nested under "
                          "evidence_releases.evidence_bundle_sha256")
    if "hard_eligibility" not in payload:
        mismatches.append("hard_eligibility: M4 publishes eligibility "
                          "with counts/parts; rules only inside "
                          "canonical_envelope")
    if "comparison" not in payload:
        mismatches.append("comparison: M4 publishes candidate_identity."
                          "ranked; interpolation disclosure absent")
    if "counts" not in payload:
        mismatches.append("counts: M4 eligibility.counts lacks the "
                          "seven-way candidate/evidence separation")
    ranked = identity.get("ranked") or []
    for entry in ranked:
        if "interpolation" not in entry:
            mismatches.append(
                "comparison.condition_matched_entries[].interpolation: "
                "not published by the 08B projection")
            break
    projected = {
        "schema": DECISION_SCHEMA_EXPECTED,
        "projected_from": M4_08B_CONTRACT,
        "evidence_release": releases.get("evidence_release"),
        "bundle_sha256": releases.get("evidence_bundle_sha256"),
        "review_state": payload.get("review_state"),
        "hard_eligibility": {
            "eligible": elig.get("eligible_parts") or [],
            "ineligible": qual.get("eliminated") or [],
            "unknown": qual.get("unresolved") or [],
        },
        "counts": {
            **(elig.get("counts") or {}),
            "m5_seven_way_counters": None,
        },
        "comparison": {
            "condition_matched_entries": ranked,
            "approximate_scenario_entries": [],
            "comparison_level": identity.get("grain_disclosure"),
        },
        "canonical_envelope": canon,
        "m4_adapter": {
            "contract_kind": payload.get("contract_kind"),
            "comparator_fingerprint": payload.get("comparator_fingerprint"),
            "identity_basis": identity.get("identity_basis"),
            "eligibility_basis": elig.get("eligibility_basis"),
        },
    }
    if not pref.get("approximate_scenario_entries") and             "approximate" not in canon:
        mismatches.append("comparison.approximate_scenario_entries: M4 "
                          "engine publishes no scenario mode")
    return projected, sorted(set(mismatches))
DEFAULT_TIMEOUT_S = 10.0
MAX_RETRIES = 1          # bounded; only for connection-level failures

FIXTURE_DIR = Path(__file__).resolve().parents[2] / \
    "tests/fixtures/discovery01"

_REQUIRED_QUESTION_KEYS = ("requirements", "conditions", "operating_point",
                           "phenomenon")


class M4ServiceError(RuntimeError):
    """Structured M4 failure. `reason` is machine-readable."""

    def __init__(self, reason: str, detail: str = "",
                 http_status: int | None = None):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason          # connection_refused | timeout |
        # http_error | contract_mismatch | unsupported_category |
        # release_mismatch | no_frozen_answer | invalid_request | m4_internal
        self.detail = detail
        self.http_status = http_status

    def structured(self) -> dict:
        return {"error": "m4_service_error", "reason": self.reason,
                "detail": self.detail, "http_status": self.http_status}


def validate_question(question: dict) -> None:
    if not isinstance(question, dict):
        raise M4ServiceError("invalid_request", "question must be an object")
    missing = [k for k in _REQUIRED_QUESTION_KEYS if k not in question]
    if missing:
        raise M4ServiceError("invalid_request",
                             f"question missing keys: {missing}")
    req = question["requirements"]
    for key in ("vin_min", "vout", "iout_min"):
        if key in req and not isinstance(req[key], (int, float)):
            raise M4ServiceError("invalid_request",
                                 f"requirements.{key} must be numeric SI")
    op = question["operating_point"]
    if not isinstance(op.get("x"), (int, float)) or not op.get("unit"):
        raise M4ServiceError("invalid_request",
                             "operating_point needs numeric x + unit")


@dataclass
class DecisionResult:
    answer: dict
    transport: str                    # "frozen" | "http"
    contract: dict                    # schema/release identities
    latency_ms: float


class FrozenTransport:
    """Contract-level integration against pinned fixtures."""

    def __init__(self, fixture_path: Path | None = None):
        path = fixture_path or FIXTURE_DIR / "m4_frozen_journeys_v1.json"
        if not path.exists():
            raise M4ServiceError("no_frozen_answer",
                                 f"fixture missing: {path}")
        self.fixture = json.loads(path.read_text())
        self.provenance = self.fixture["provenance"]
        self._by_question = {
            json.dumps(j["question"], sort_keys=True): j
            for j in self.fixture["journeys"].values()}

    def decide(self, question: dict) -> DecisionResult:
        import time
        t0 = time.time()
        key = json.dumps(question, sort_keys=True)
        journey = self._by_question.get(key)
        if journey is None:
            raise M4ServiceError(
                "no_frozen_answer",
                "question does not match a pinned journey; frozen "
                "transport never improvises answers")
        answer = journey["answer"]
        if answer.get("schema") != DECISION_SCHEMA_EXPECTED:
            raise M4ServiceError(
                "contract_mismatch",
                f"fixture schema {answer.get('schema')!r} != "
                f"{DECISION_SCHEMA_EXPECTED!r}")
        return DecisionResult(
            answer=answer, transport="frozen",
            contract={
                "decision_schema": answer.get("schema"),
                "m4_evidence_release": answer.get("evidence_release"),
                "m4_bundle_sha256": answer.get("bundle_sha256"),
                "m4_commit": self.provenance.get("m4_commit"),
                "fixture": "m4_frozen_journeys_v1.json",
                "fixture_sha256": self.provenance.get("bundle_sha256"),
            },
            latency_ms=(time.time() - t0) * 1000.0)


class HttpTransport:
    """Live integration against the M4 experimental service."""

    def __init__(self, base_url: str, timeout: float = DEFAULT_TIMEOUT_S,
                 expected_contract: str = DECISION_SCHEMA_EXPECTED):
        self.url = base_url.rstrip("/") + "/v1/engineering/decisions"
        self.timeout = timeout
        self.expected_contract = expected_contract

    @staticmethod
    def _headers() -> dict:
        headers = {"Content-Type": "application/json"}
        token = os.environ.get("M4_BEARER_TOKEN", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def decide(self, question: dict) -> DecisionResult:
        import time
        body = json.dumps({"question": question}).encode()
        last: M4ServiceError | None = None
        for attempt in range(MAX_RETRIES + 1):
            t0 = time.time()
            req = urllib.request.Request(
                self.url, data=body,
                headers=self._headers(), method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    payload = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                detail = ""
                try:
                    detail = e.read().decode()[:300]
                except Exception:  # noqa: BLE001
                    pass
                if e.code == 401 or e.code == 403:
                    raise M4ServiceError("auth_failure", detail, e.code)
                if e.code == 422 or e.code == 400:
                    raise M4ServiceError("invalid_request", detail, e.code)
                if e.code == 404:
                    raise M4ServiceError("unsupported_category", detail,
                                         e.code)
                if e.code >= 500:
                    last = M4ServiceError("m4_internal", detail, e.code)
                else:
                    raise M4ServiceError("http_error", detail, e.code)
            except urllib.error.URLError as e:
                reason = str(e.reason)
                last = M4ServiceError(
                    "connection_refused" if "refused" in reason.lower()
                    else "timeout" if "timed out" in reason.lower()
                    else "connection_error", reason)
            except TimeoutError as e:
                last = M4ServiceError("timeout", str(e))
            if attempt < MAX_RETRIES:
                continue
        if last is not None:
            raise last
        answer = payload.get("answer", payload)
        latency = (time.time() - t0) * 1000.0
        mismatches: list[str] = []
        if answer.get("contract") == M4_08B_CONTRACT and \
                "canonical_envelope" in answer:
            answer, mismatches = project_m4_08b(answer)
        schema = answer.get("schema")
        if schema != self.expected_contract:
            raise M4ServiceError(
                "contract_mismatch",
                f"live schema {schema!r} != expected "
                f"{self.expected_contract!r} — release mismatch is never "
                "silently accepted")
        return DecisionResult(
            answer=answer, transport="http",
            contract={
                "decision_schema": schema,
                "m4_evidence_release": answer.get("evidence_release"),
                "m4_bundle_sha256": answer.get("bundle_sha256"),
                "m4_adapter_contract": M4_08B_CONTRACT if mismatches
                else None,
                "field_mismatches": mismatches,
            },
            latency_ms=latency)


def make_transport(mode: str = "frozen", base_url: str | None = None,
                   fixture_path: Path | None = None):
    if mode == "http":
        if not base_url:
            raise M4ServiceError("invalid_request",
                                 "http transport requires base_url")
        return HttpTransport(base_url)
    if mode == "frozen":
        return FrozenTransport(fixture_path)
    raise M4ServiceError("invalid_request", f"unknown transport {mode!r}")


def decide(question: dict, transport) -> DecisionResult:
    validate_question(question)
    return transport.decide(question)
