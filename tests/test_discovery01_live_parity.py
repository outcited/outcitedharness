"""Live-parity pytest wrapper — skips loudly unless a REAL M4 service is
reachable. A skip is never a pass: the frozen-fixture suite lives in
test_discovery01.py and is reported separately. If M4_DECISION_URL is set
but unreachable, the tests FAIL (misconfiguration must not masquerade as
"live parity pending")."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

M4_URL = os.environ.get("M4_DECISION_URL")


def _live() -> bool:
    if not M4_URL:
        return False
    req = urllib.request.Request(
        M4_URL.rstrip("/") + "/v1/engineering/decisions",
        data=json.dumps({"question": {}}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=8)
        return True
    except urllib.error.HTTPError:
        return True          # an error status still proves liveness
    except Exception:  # noqa: BLE001
        return False


def pytest_configure(config):
    pass  # hook lives in conftest space only; liveness is enforced in-test


live_required = pytest.mark.skipif(
    not M4_URL,
    reason="M4_DECISION_URL not set — live parity PENDING (frozen "
           "contract tests in test_discovery01.py are the reporting "
           "baseline; this skip is not a pass)")


@live_required
def test_live_parity_full_run():
    import subprocess
    import sys
    from pathlib import Path
    if not _live():
        pytest.fail(
            f"M4_DECISION_URL={M4_URL} is set but unreachable — "
            "misconfiguration must fail loudly, never skip or fall back "
            "to fixtures")
    script = Path(__file__).resolve().parents[1] / \
        "scripts/discovery01_live_parity.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--url", M4_URL],
        capture_output=True, text=True, timeout=600)
    report = json.loads(proc.stdout)
    failed = [c for c in report.get("checks", []) if not c["pass"]]
    assert report.get("verdict") == "PASS", json.dumps(
        {"verdict": report.get("verdict"), "failed_checks": failed},
        indent=1)
    # anti-mock: every journey must have run over http
    for name, j in report.get("journeys", {}).items():
        assert j.get("live_transport") is True, name
