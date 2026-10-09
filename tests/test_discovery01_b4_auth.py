"""B4 regression — authentication must fail closed on both hops.

Assessment evidence (results/discovery01-production-readiness/
security_posture.json): the M5 engineering-session route served HTTP 200
with no credentials at all; M4's ``auth_status()`` returned "authorized"
whenever ``ENGINEERING_DECISIONS_TOKEN`` was unset (fail open); M4 had no
non-local bind guard equivalent to M5's ``SEARCH_ALLOW_REMOTE``; and the
bearer comparison was a plain ``!=`` (not constant-time).

These tests pin the corrected posture:
  * a missing secret never opens a protected route (503, not 200);
  * missing bearer -> 401, wrong bearer -> 403, correct bearer -> allowed;
  * non-local bind is refused without explicit authorization, and refused
    outright for the session route when no session token is configured;
  * loopback development remains possible only behind an explicit env
    opt-in AND a loopback bind AND a loopback client;
  * secrets stay environment-only and comparison is constant-time.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from harness.search import api

BODY = json.dumps({"category": "power"}).encode()
SESSION_ROUTE = "/v1/discovery/engineering-session"


class _Headers:
    def __init__(self, authorization=None):
        self._h = {"Authorization": authorization} if authorization else {}

    def get(self, key, default=None):
        return self._h.get(key, default)


@pytest.fixture
def server(monkeypatch, tmp_path):
    """Hermetic HTTP server: flag on, no real databases reachable."""
    monkeypatch.setattr(api, "SEARCH_DB", str(tmp_path / "absent.db"))
    monkeypatch.setattr(api, "CATALOG", str(tmp_path / "absent.db"))
    monkeypatch.setenv("SEARCH_ENABLE_ENGINEERING_SESSION", "1")
    monkeypatch.delenv("SEARCH_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("SEARCH_ALLOW_LOCAL_NOAUTH", raising=False)
    monkeypatch.setenv("SEARCH_HOST", "127.0.0.1")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), api.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _post(base, token=None, route=SESSION_ROUTE):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + route, data=BODY, headers=headers,
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:  # noqa: BLE001
            return e.code, {}


# --- a missing secret never opens the route ---------------------------------

def test_b4_session_route_is_not_open_without_a_configured_token(server):
    status, body = _post(server)
    assert status == 503, f"protected route served {status} with no token"
    assert body.get("error") == "auth_not_configured"
    assert "fail" in (body.get("detail") or "").lower()


def test_b4_auth_helper_fails_closed_when_token_absent(monkeypatch):
    monkeypatch.delenv("SEARCH_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("SEARCH_ALLOW_LOCAL_NOAUTH", raising=False)
    status, code = api.session_auth_status(_Headers(), "127.0.0.1")
    assert status == 503 and code == "auth_not_configured"


def test_b4_blank_token_is_treated_as_absent(monkeypatch):
    monkeypatch.setenv("SEARCH_SESSION_TOKEN", "   ")
    status, code = api.session_auth_status(_Headers(), "127.0.0.1")
    assert status == 503 and code == "auth_not_configured"


# --- bearer contract --------------------------------------------------------

def test_b4_missing_bearer_is_401(server, monkeypatch):
    monkeypatch.setenv("SEARCH_SESSION_TOKEN", "s3cret")
    status, body = _post(server)
    assert status == 401
    assert body.get("error") == "unauthorized"


def test_b4_wrong_bearer_is_403(server, monkeypatch):
    monkeypatch.setenv("SEARCH_SESSION_TOKEN", "s3cret")
    status, body = _post(server, token="wrong")
    assert status == 403
    assert body.get("error") == "forbidden"


def test_b4_scheme_mismatch_is_403(server, monkeypatch):
    monkeypatch.setenv("SEARCH_SESSION_TOKEN", "s3cret")
    req = urllib.request.Request(
        server + SESSION_ROUTE, data=BODY,
        headers={"Content-Type": "application/json",
                 "Authorization": "Basic s3cret"}, method="POST")
    try:
        urllib.request.urlopen(req, timeout=30)
        raise AssertionError("expected 403 for a non-bearer scheme")
    except urllib.error.HTTPError as e:
        assert e.code == 403


def test_b4_correct_bearer_is_authorized(monkeypatch):
    monkeypatch.setenv("SEARCH_SESSION_TOKEN", "s3cret")
    status, code = api.session_auth_status(_Headers("Bearer s3cret"),
                                           "127.0.0.1")
    assert status is None and code == "authorized"


def test_b4_authorization_is_checked_before_any_database_work(server,
                                                             monkeypatch):
    """An unauthorized request must not open stores or run orchestration."""
    monkeypatch.setenv("SEARCH_SESSION_TOKEN", "s3cret")
    status, _ = _post(server)
    assert status == 401
    # the hermetic fixture points at a nonexistent db: had the route reached
    # orchestration it would have answered 500 orchestration_failed


# --- loopback development posture is explicit, not default ------------------

def test_b4_local_noauth_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("SEARCH_SESSION_TOKEN", raising=False)
    monkeypatch.delenv("SEARCH_ALLOW_LOCAL_NOAUTH", raising=False)
    assert api.session_auth_status(_Headers(), "127.0.0.1")[0] == 503

    monkeypatch.setenv("SEARCH_ALLOW_LOCAL_NOAUTH", "1")
    monkeypatch.setenv("SEARCH_HOST", "127.0.0.1")
    status, code = api.session_auth_status(_Headers(), "127.0.0.1")
    assert status is None and code == "local_development_noauth"


def test_b4_local_noauth_refuses_non_loopback_bind(monkeypatch):
    monkeypatch.delenv("SEARCH_SESSION_TOKEN", raising=False)
    monkeypatch.setenv("SEARCH_ALLOW_LOCAL_NOAUTH", "1")
    monkeypatch.setenv("SEARCH_HOST", "0.0.0.0")
    assert api.session_auth_status(_Headers(), "127.0.0.1")[0] == 503


def test_b4_local_noauth_refuses_non_loopback_client(monkeypatch):
    monkeypatch.delenv("SEARCH_SESSION_TOKEN", raising=False)
    monkeypatch.setenv("SEARCH_ALLOW_LOCAL_NOAUTH", "1")
    monkeypatch.setenv("SEARCH_HOST", "127.0.0.1")
    assert api.session_auth_status(_Headers(), "10.0.0.7")[0] == 503


def test_b4_local_noauth_refuses_unknown_client(monkeypatch):
    monkeypatch.delenv("SEARCH_SESSION_TOKEN", raising=False)
    monkeypatch.setenv("SEARCH_ALLOW_LOCAL_NOAUTH", "1")
    monkeypatch.setenv("SEARCH_HOST", "127.0.0.1")
    assert api.session_auth_status(_Headers(), None)[0] == 503


def test_b4_token_always_wins_over_dev_opt_in(monkeypatch):
    monkeypatch.setenv("SEARCH_SESSION_TOKEN", "s3cret")
    monkeypatch.setenv("SEARCH_ALLOW_LOCAL_NOAUTH", "1")
    monkeypatch.setenv("SEARCH_HOST", "127.0.0.1")
    assert api.session_auth_status(_Headers(), "127.0.0.1")[0] == 401


# --- bind guards ------------------------------------------------------------

def _main_guard(monkeypatch, host, **env):
    monkeypatch.setattr(api, "ThreadingHTTPServer",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("server must not bind")))
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    monkeypatch.setenv("SEARCH_HOST", host)
    with pytest.raises(SystemExit) as e:
        api.main()
    return str(e.value)


def test_b4_non_local_bind_refused_without_explicit_authorization(monkeypatch):
    msg = _main_guard(monkeypatch, "0.0.0.0", SEARCH_ALLOW_REMOTE=None,
                      SEARCH_SESSION_TOKEN=None,
                      SEARCH_ENABLE_ENGINEERING_SESSION=None)
    assert "refusing to bind non-localhost" in msg


def test_b4_non_local_bind_refused_without_session_token(monkeypatch):
    msg = _main_guard(monkeypatch, "0.0.0.0", SEARCH_ALLOW_REMOTE="1",
                      SEARCH_ENABLE_ENGINEERING_SESSION="1",
                      SEARCH_SESSION_TOKEN=None)
    assert "SEARCH_SESSION_TOKEN" in msg


def test_b4_non_local_bind_allowed_with_token_and_authorization(monkeypatch):
    bound = {}

    class _FakeServer:
        def __init__(self, addr, handler):
            bound["addr"] = addr

        def serve_forever(self):
            bound["served"] = True

    monkeypatch.setattr(api, "ThreadingHTTPServer", _FakeServer)
    monkeypatch.setenv("SEARCH_HOST", "0.0.0.0")
    monkeypatch.setenv("SEARCH_ALLOW_REMOTE", "1")
    monkeypatch.setenv("SEARCH_ENABLE_ENGINEERING_SESSION", "1")
    monkeypatch.setenv("SEARCH_SESSION_TOKEN", "s3cret")
    api.main()
    assert bound.get("served") is True


# --- hygiene ----------------------------------------------------------------

def test_b4_comparison_is_constant_time():
    src = Path(api.__file__).read_text()
    assert "hmac.compare_digest" in src
    assert 'header != f"Bearer' not in src


def test_b4_secrets_remain_environment_only():
    root = Path(api.__file__).resolve().parents[2]
    literals = []
    for path in (root / "harness").rglob("*.py"):
        text = path.read_text()
        for needle in ("disc01-final-verify", "s3cret"):
            if needle in text:
                literals.append(f"{path.name}:{needle}")
    assert literals == []
    src = Path(api.__file__).read_text()
    assert "SEARCH_SESSION_TOKEN" in src      # env-only source of truth


def test_b4_flag_off_still_removes_the_route(server, monkeypatch):
    """Rollback behaviour must survive the auth change."""
    monkeypatch.setenv("SEARCH_SESSION_TOKEN", "s3cret")
    monkeypatch.delenv("SEARCH_ENABLE_ENGINEERING_SESSION", raising=False)
    status, _ = _post(server, token="s3cret")
    assert status == 404
