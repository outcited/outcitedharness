"""CURVE-08D (B4): M4 decisions auth must fail closed.

The production-readiness assessment of DISCOVERY-01
(results/discovery01-production-readiness/security_posture.json) recorded
that this service authorized every request when
ENGINEERING_DECISIONS_TOKEN was unset, had no non-local bind guard
equivalent to M5's SEARCH_ALLOW_REMOTE, and compared bearers with a plain
``!=``. A missing secret must never open a protected route.

Contract pinned here:
  * no token configured -> 503 auth_not_configured (never "authorized");
  * missing bearer -> 401, wrong bearer -> 403, correct bearer -> allowed;
  * loopback development without a token requires an explicit
    ENGINEERING_DECISIONS_ALLOW_LOCAL_NOAUTH=1 opt-in AND a loopback bind
    AND a loopback client;
  * main() refuses a non-local bind without
    ENGINEERING_DECISIONS_ALLOW_REMOTE=1, and refuses to serve an enabled
    decisions route with no token outside that explicit loopback posture;
  * bearer comparison is constant-time; secrets stay environment-only.

The pre-existing lock ``test_no_token_configured_disables_auth`` asserted
the fail-open behaviour and is inverted by this remediation (see
tests/test_curve08c_parity_repair.py).
"""

from __future__ import annotations

import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import harness.electronics.decision_api as svc

REPO = Path(__file__).resolve().parents[1]
TOKEN_KEYS = ("ENGINEERING_DECISIONS_TOKEN", "ENGINEERING_DECISIONS_ENABLED",
              "ENGINEERING_DECISIONS_ALLOW_LOCAL_NOAUTH",
              "ENGINEERING_DECISIONS_ALLOW_REMOTE",
              "ENGINEERING_DECISIONS_HOST", "HARNESS_REPO_ROOT")


class _Headers:
    def __init__(self, authorization=None):
        self._h = {"Authorization": authorization} if authorization else {}

    def get(self, key, default=None):
        return self._h.get(key, default)


def _handler(header=None, client=("127.0.0.1", 5555)):
    h = svc.Handler.__new__(svc.Handler)      # no socket needed
    h.headers = _Headers(header)
    h.client_address = client
    return h


class _EnvCase(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in TOKEN_KEYS}
        os.environ["HARNESS_REPO_ROOT"] = str(REPO)
        os.environ["ENGINEERING_DECISIONS_ENABLED"] = "1"
        os.environ.pop("ENGINEERING_DECISIONS_ALLOW_LOCAL_NOAUTH", None)
        os.environ.pop("ENGINEERING_DECISIONS_ALLOW_REMOTE", None)
        os.environ.pop("ENGINEERING_DECISIONS_HOST", None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class FailClosedTests(_EnvCase):
    def test_no_token_configured_is_503_not_authorized(self):
        os.environ.pop("ENGINEERING_DECISIONS_TOKEN", None)
        self.assertEqual(_handler().auth_status(), 503)

    def test_blank_token_is_503(self):
        os.environ["ENGINEERING_DECISIONS_TOKEN"] = "   "
        self.assertEqual(_handler().auth_status(), 503)

    def test_no_token_is_503_even_with_a_bearer_presented(self):
        os.environ.pop("ENGINEERING_DECISIONS_TOKEN", None)
        self.assertEqual(_handler("Bearer anything").auth_status(), 503)

    def test_missing_bearer_is_401(self):
        os.environ["ENGINEERING_DECISIONS_TOKEN"] = "k1"
        self.assertEqual(_handler().auth_status(), 401)
        self.assertEqual(_handler("").auth_status(), 401)

    def test_invalid_bearer_is_403(self):
        os.environ["ENGINEERING_DECISIONS_TOKEN"] = "k1"
        self.assertEqual(_handler("Bearer wrong").auth_status(), 403)
        self.assertEqual(_handler("Basic k1").auth_status(), 403)

    def test_valid_bearer_is_authorized(self):
        os.environ["ENGINEERING_DECISIONS_TOKEN"] = "k1"
        self.assertIsNone(_handler("Bearer k1").auth_status())


class LoopbackDevelopmentTests(_EnvCase):
    def test_opt_in_plus_loopback_bind_plus_loopback_client_allows(self):
        os.environ.pop("ENGINEERING_DECISIONS_TOKEN", None)
        os.environ["ENGINEERING_DECISIONS_ALLOW_LOCAL_NOAUTH"] = "1"
        os.environ["ENGINEERING_DECISIONS_HOST"] = "127.0.0.1"
        self.assertIsNone(_handler().auth_status())

    def test_without_opt_in_it_stays_closed(self):
        os.environ.pop("ENGINEERING_DECISIONS_TOKEN", None)
        os.environ["ENGINEERING_DECISIONS_HOST"] = "127.0.0.1"
        self.assertEqual(_handler().auth_status(), 503)

    def test_opt_in_does_not_apply_to_a_non_loopback_bind(self):
        os.environ.pop("ENGINEERING_DECISIONS_TOKEN", None)
        os.environ["ENGINEERING_DECISIONS_ALLOW_LOCAL_NOAUTH"] = "1"
        os.environ["ENGINEERING_DECISIONS_HOST"] = "0.0.0.0"
        self.assertEqual(_handler().auth_status(), 503)

    def test_opt_in_does_not_apply_to_a_non_loopback_client(self):
        os.environ.pop("ENGINEERING_DECISIONS_TOKEN", None)
        os.environ["ENGINEERING_DECISIONS_ALLOW_LOCAL_NOAUTH"] = "1"
        os.environ["ENGINEERING_DECISIONS_HOST"] = "127.0.0.1"
        self.assertEqual(
            _handler(client=("10.0.0.7", 1)).auth_status(), 503)

    def test_opt_in_does_not_apply_when_client_is_unknown(self):
        os.environ.pop("ENGINEERING_DECISIONS_TOKEN", None)
        os.environ["ENGINEERING_DECISIONS_ALLOW_LOCAL_NOAUTH"] = "1"
        h = svc.Handler.__new__(svc.Handler)
        h.headers = _Headers()
        self.assertEqual(h.auth_status(), 503)

    def test_configured_token_always_wins_over_opt_in(self):
        os.environ["ENGINEERING_DECISIONS_TOKEN"] = "k1"
        os.environ["ENGINEERING_DECISIONS_ALLOW_LOCAL_NOAUTH"] = "1"
        os.environ["ENGINEERING_DECISIONS_HOST"] = "127.0.0.1"
        self.assertEqual(_handler().auth_status(), 401)
        self.assertEqual(_handler("Bearer nope").auth_status(), 403)
        self.assertIsNone(_handler("Bearer k1").auth_status())


class StartupGuardTests(_EnvCase):
    """main() must refuse unsafe postures before binding. Hermetic: the
    server class is replaced, so a missing guard fails the assertion
    instead of blocking on serve_forever()."""

    def _run_main(self):
        """Returns (systemexit_message_or_None, bound_dict)."""
        bound = {}

        class _Fake(ThreadingHTTPServer):
            def __init__(self, addr, handler):
                bound["addr"] = addr

            def serve_forever(self):
                bound["served"] = True

        saved = svc.ThreadingHTTPServer
        svc.ThreadingHTTPServer = _Fake
        try:
            svc.main()
        except SystemExit as e:
            return str(e), bound
        finally:
            svc.ThreadingHTTPServer = saved
        return None, bound

    def test_non_local_bind_refused_without_explicit_authorization(self):
        os.environ["ENGINEERING_DECISIONS_HOST"] = "0.0.0.0"
        os.environ["ENGINEERING_DECISIONS_TOKEN"] = "k1"
        msg, bound = self._run_main()
        self.assertIsNotNone(msg, "non-local bind was not refused")
        self.assertIn("refusing to bind non-localhost", msg)
        self.assertEqual(bound, {})

    def test_enabled_route_without_token_refused_at_startup(self):
        os.environ.pop("ENGINEERING_DECISIONS_TOKEN", None)
        os.environ["ENGINEERING_DECISIONS_HOST"] = "127.0.0.1"
        msg, bound = self._run_main()
        self.assertIsNotNone(msg, "an enabled route with no token started")
        self.assertIn("ENGINEERING_DECISIONS_TOKEN", msg)
        self.assertEqual(bound, {})

    def test_loopback_dev_opt_in_is_allowed_to_start(self):
        os.environ.pop("ENGINEERING_DECISIONS_TOKEN", None)
        os.environ["ENGINEERING_DECISIONS_HOST"] = "127.0.0.1"
        os.environ["ENGINEERING_DECISIONS_ALLOW_LOCAL_NOAUTH"] = "1"
        msg, bound = self._run_main()
        self.assertIsNone(msg)
        self.assertTrue(bound.get("served"))

    def test_disabled_route_without_token_may_still_start(self):
        """Health/dark-route posture is unchanged when the flag is off."""
        os.environ.pop("ENGINEERING_DECISIONS_TOKEN", None)
        os.environ["ENGINEERING_DECISIONS_ENABLED"] = ""
        msg, bound = self._run_main()
        self.assertIsNone(msg)
        self.assertTrue(bound.get("served"))


class LiveHttpTests(_EnvCase):
    """End-to-end over a real socket on an ephemeral loopback port."""

    def _server(self, token=None, noauth=False):
        if token:
            os.environ["ENGINEERING_DECISIONS_TOKEN"] = token
        else:
            os.environ.pop("ENGINEERING_DECISIONS_TOKEN", None)
        if noauth:
            os.environ["ENGINEERING_DECISIONS_ALLOW_LOCAL_NOAUTH"] = "1"
        os.environ["ENGINEERING_DECISIONS_HOST"] = "127.0.0.1"
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), svc.Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return f"http://127.0.0.1:{httpd.server_address[1]}"

    def _post(self, base, token=None, path="/v1/engineering/decisions"):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(
            base + path, data=json.dumps({"question": {}}).encode(),
            headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except Exception:  # noqa: BLE001
                return e.code, {}

    def test_unconfigured_token_serves_503_not_a_decision(self):
        base = self._server()
        status, body = self._post(base)
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "auth_not_configured")

    def test_bearer_contract_over_http(self):
        base = self._server(token="live-token")
        self.assertEqual(self._post(base)[0], 401)
        self.assertEqual(self._post(base, token="wrong")[0], 403)
        status, _ = self._post(base, token="live-token")
        self.assertNotIn(status, (401, 403, 503))

    def test_evidence_route_is_protected_too(self):
        base = self._server(token="live-token")
        status, _ = self._post(base, path="/v1/engineering/evidence/x")
        self.assertIn(status, (401, 405))
        req = urllib.request.Request(
            base + "/v1/engineering/evidence/curve-nonexistent")
        try:
            urllib.request.urlopen(req, timeout=30)
            self.fail("evidence route must require a bearer")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 401)

    def test_loopback_dev_posture_serves_decisions(self):
        base = self._server(noauth=True)
        status, _ = self._post(base, token=None)
        self.assertNotIn(status, (401, 403, 503))


class HygieneTests(unittest.TestCase):
    def test_comparison_is_constant_time(self):
        src = (REPO / "harness/electronics/decision_api.py").read_text()
        self.assertIn("hmac.compare_digest", src)
        self.assertNotIn('header != f"Bearer', src)

    def test_secrets_are_environment_only(self):
        src = (REPO / "harness/electronics/decision_api.py").read_text()
        self.assertIn('os.environ.get("ENGINEERING_DECISIONS_TOKEN")', src)
        for literal in ("live-token", "secret-token-1"):
            self.assertNotIn(literal, src)


if __name__ == "__main__":
    unittest.main()
