"""Offline unit + negative-control tests for the NEO<->Gas City bridge.

Run: python3 -m unittest discover -s tests -v
No network: the supervisor is an in-process stub asserting every law
(CSRF header, Idempotency-Key, read-back-before-receipt, dedup).
"""
from __future__ import annotations

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bridge import finding as F
from bridge import handoff as H
from bridge import neo_status as NS
from bridge.gascity_client import ApiError, GasCityClient


def make_finding(**over):
    f = {
        "schema": "dw.finding.v1",
        "finding_id": "DW-NEO-DEMO-01",
        "task_id": "DW-NEO-DEMO-01 — demo",
        "source_url": "https://github.com/outcited/outcitedharness/issues/99",
        "repository": "outcited/outcitedharness",
        "severity": "major",
        "summary": "demo finding flows into staging queue",
        "reproduction": ["run demo", "observe bead"],
        "expected_behavior": "one bead, exactly once",
        "regression_tests": {"positive": ["test_accepts_once"],
                             "negative": ["test_refuses_unauthorized"]},
        "authorization_state": "authorized",
        "owner": "neo-bridge",
    }
    f.update(over)
    return f


def make_approval(**over):
    a = {
        "project": "designwins", "action": "gascity-ingest",
        "recipient": "designwins-staging", "task_ref": "DW-NEO-DEMO-01 — demo",
        "expires_at": int(time.time()) + 3600, "issuer": "sam",
        "max_actions": 1, "spend_scope": "none", "max_spend_usd": 0,
    }
    a.update(over)
    return a


class StubState:
    beads: dict[str, dict] = {}
    post_calls: list[dict] = []
    seq = 100
    fail_next_creates: int = 0


class StubSupervisor(BaseHTTPRequestHandler):
    def _send(self, code: int, body: dict):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-GC-Index", f"stub:{StubState.seq}")
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path
        if p.startswith("/health"):
            return self._send(200, {"status": "ok", "version": "stub-1.0",
                                    "uptime_sec": 5, "cities_total": 1,
                                    "cities_running": 1, "startup": {"ready": True}})
        if "/status" in p:
            return self._send(200, {
                "running": True, "suspended": False, "uptime_sec": 10,
                "version": "stub-1.0", "agents": {"total": 0, "running": 0},
                "work": {"open": 8, "ready": 6, "in_progress": 0},
                "agent_details": [], "partial": False, "partial_errors": []})
        if "/pending" in p:
            return self._send(200, {"items": [], "total": 0})
        if "/sessions" in p:
            return self._send(200, {"items": [], "total": 0})
        if "/events" in p:
            return self._send(200, {"items": [
                {"seq": StubState.seq, "type": "bead.created", "subject": "stub"}]})
        if "/bead/" in p:
            bid = p.rstrip("/").split("/")[-1]
            if bid in StubState.beads:
                return self._send(200, StubState.beads[bid])
            return self._send(404, {"code": "bead-not-found"})
        if "/beads" in p:
            label = None
            if "label=" in p:
                label = p.split("label=")[1].split("&")[0].replace("%3A", ":")
            items = [b for b in StubState.beads.values()
                     if label is None or label in (b.get("labels") or [])]
            return self._send(200, {"items": items, "total": len(items)})
        return self._send(404, {"code": "not-found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        # law: mutations require the anti-CSRF header (403 otherwise)
        if not self.headers.get("X-GC-Request"):
            return self._send(403, {"code": "csrf"})
        # law: our client must always send an Idempotency-Key
        idem = self.headers.get("Idempotency-Key", "")
        StubState.post_calls.append({"idem": idem, "body": body})
        if not idem.startswith("neo-"):
            return self._send(400, {"code": "bad-idempotency-key"})
        # replay of the same key must return the SAME bead, not create one
        for b in StubState.beads.values():
            if b.get("_idem") == idem:
                return self._send(200, b)
        if StubState.fail_next_creates > 0:
            StubState.fail_next_creates -= 1
            return self._send(503, {"code": "unavailable"})
        StubState.seq += 1
        bid = f"xx-{StubState.seq:04x}"
        bead = {"id": bid, "status": "open", "title": body.get("title"),
                "labels": body.get("labels"), "metadata": body.get("metadata"),
                "_idem": idem}
        StubState.beads[bid] = bead
        return self._send(200, bead)


class StubServer:
    def __enter__(self):
        StubState.beads, StubState.post_calls, StubState.seq = {}, [], 100
        StubState.fail_next_creates = 0
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubSupervisor)
        self.port = self.httpd.server_address[1]
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.t.start()
        self.client = GasCityClient(f"http://127.0.0.1:{self.port}")
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()


CITY = "designwins-staging"


class TestFindingContract(unittest.TestCase):
    def test_valid_finding_passes(self):
        self.assertEqual(F.validate(make_finding()), [])

    def test_bad_repo_is_a_gap(self):
        gaps = F.validate(make_finding(repository="evil/example"))
        self.assertTrue(any("allowlisted" in g for g in gaps))

    def test_missing_negative_controls_is_a_gap(self):
        gaps = F.validate(make_finding(regression_tests={"positive": ["t1"]}))
        self.assertTrue(any("negative" in g for g in gaps))

    def test_bad_finding_id(self):
        gaps = F.validate(make_finding(finding_id="X-1"))
        self.assertTrue(gaps)

    def test_digest_and_key_determinism(self):
        f = make_finding()
        self.assertEqual(F.digest(f), F.digest(make_finding()))
        k1 = F.idempotency_key(f, "auth-a")
        self.assertEqual(k1, F.idempotency_key(make_finding(), "auth-a"))
        self.assertNotEqual(k1, F.idempotency_key(f, "auth-b"))
        self.assertTrue(k1.startswith("neo-"))

    def test_bead_payload_shape(self):
        p = F.to_bead_payload(make_finding())
        self.assertEqual(p["title"], "DW-NEO-DEMO-01 — demo finding flows into staging queue")
        self.assertIn("neo-finding:DW-NEO-DEMO-01", p["labels"])
        self.assertIn("gh:outcitedharness", p["labels"])
        self.assertEqual(p["metadata"]["neo-authorization-state"], "authorized")
        self.assertIn("must fail pre-fix", p["description"])


class TestHandoffLaws(unittest.TestCase):
    def _run(self, finding, approval, tmp):
        ap = Path(tmp) / "approval.json"
        ap.write_text(json.dumps(approval))
        with StubServer() as s:
            r = H.submit(finding, ap, CITY, s.client, Path(tmp) / "receipts")
        return r, s

    def test_no_approval_file_refused(self):
        with TemporaryDirectory() as tmp:
            with StubServer() as s:
                r = H.submit(make_finding(), Path(tmp) / "missing.json",
                             CITY, s.client, Path(tmp) / "receipts")
            self.assertEqual(r["state"], "refused")
            self.assertEqual(r["refusal"]["reason"], "invalid-approval")
            self.assertEqual(StubState.post_calls, [])  # never touched the API

    def test_wrong_issuer_refused(self):
        with TemporaryDirectory() as tmp:
            r, _ = self._run(make_finding(), make_approval(issuer="neo"), tmp)
            self.assertEqual(r["refusal"]["reason"], "unauthorized-issuer")

    def test_expired_approval_refused(self):
        with TemporaryDirectory() as tmp:
            r, _ = self._run(make_finding(),
                             make_approval(expires_at=int(time.time()) - 10), tmp)
            self.assertEqual(r["refusal"]["reason"], "approval-expired")

    def test_task_mismatch_refused(self):
        with TemporaryDirectory() as tmp:
            r, _ = self._run(make_finding(),
                             make_approval(task_ref="OTHER-TASK-9"), tmp)
            self.assertEqual(r["refusal"]["reason"], "task-mismatch")

    def test_proposal_finding_never_dispatched(self):
        with TemporaryDirectory() as tmp:
            r, _ = self._run(make_finding(authorization_state="proposal"),
                             make_approval(), tmp)
            self.assertEqual(r["refusal"]["reason"], "finding-not-authorized")
            self.assertEqual(StubState.post_calls, [])

    def test_digest_mismatch_refused(self):
        with TemporaryDirectory() as tmp:
            r, _ = self._run(make_finding(),
                             make_approval(payload_digest="0" * 64), tmp)
            self.assertEqual(r["refusal"]["reason"], "digest-mismatch")

    def test_accepted_produces_receipt_with_readback(self):
        with TemporaryDirectory() as tmp:
            r, s = self._run(make_finding(), make_approval(), tmp)
            self.assertEqual(r["state"], "accepted")
            self.assertTrue(r["bead_id"])
            self.assertEqual(r["readback"]["id"], r["bead_id"])
            self.assertEqual(r["readback"]["status"], "open")
            self.assertIn("neo-finding:", r["readback"]["labels"][0])
            self.assertTrue(Path(r["receipt_path"]).exists())
            self.assertEqual(len(StubState.post_calls), 1)

    def test_resubmit_is_duplicate_no_new_post(self):
        with TemporaryDirectory() as tmp:
            ap = Path(tmp) / "a.json"
            ap.write_text(json.dumps(make_approval()))
            with StubServer() as s:
                r1 = H.submit(make_finding(), ap, CITY, s.client, Path(tmp) / "rec")
                r2 = H.submit(make_finding(), ap, CITY, s.client, Path(tmp) / "rec")
            self.assertEqual(r1["state"], "accepted")
            self.assertEqual(r2["state"], "duplicate")
            self.assertEqual(r2["bead_id"], r1["bead_id"])
            self.assertEqual(len(StubState.post_calls), 1)  # exactly one create

    def test_idempotency_key_replay_returns_same_bead(self):
        with TemporaryDirectory() as tmp:
            ap = Path(tmp) / "a.json"
            ap.write_text(json.dumps(make_approval()))
            with StubServer() as s:
                r1 = H.submit(make_finding(), ap, CITY, s.client, Path(tmp) / "rec")
                # simulate a lost response: same key POSTed directly again
                body, _ = s.client.create_bead(
                    CITY, F.to_bead_payload(make_finding()), r1["idempotency_key"])
                self.assertEqual(body["id"], r1["bead_id"])
                self.assertEqual(len(StubState.beads), 1)

    def test_5xx_retries_then_accepts(self):
        with TemporaryDirectory() as tmp:
            ap = Path(tmp) / "a.json"
            ap.write_text(json.dumps(make_approval()))
            with StubServer() as s:
                StubState.fail_next_creates = 2
                r = H.submit(make_finding(), ap, CITY, s.client, Path(tmp) / "rec")
                self.assertEqual(r["state"], "accepted")
                self.assertEqual(r["attempts"], 3)

    def test_4xx_is_api_error_no_retry(self):
        with TemporaryDirectory() as tmp:
            ap = Path(tmp) / "a.json"
            ap.write_text(json.dumps(make_approval()))
            with StubServer() as s:
                # force a 422 by violating the stub's key law once
                orig = s.client.create_bead
                s.client.create_bead = lambda c, p, k: (_ for _ in ()).throw(
                    ApiError(422, {"code": "validation-failed"}))
                r = H.submit(make_finding(), ap, CITY, s.client, Path(tmp) / "rec")
            self.assertEqual(r["state"], "api-error")
            self.assertEqual(r["attempts"], 1)
            self.assertEqual(r["error"]["code"], "validation-failed")

    def test_csrf_header_enforced_by_stub(self):
        # proves the stub (like the real API) rejects headerless mutations
        import urllib.request
        with StubServer() as s:
            req = urllib.request.Request(
                f"http://127.0.0.1:{self_port(s)}/v0/city/{CITY}/beads",
                method="POST", data=b"{}",
                headers={"Content-Type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=5)
                self.fail("expected 403")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 403)


def self_port(s):
    return s.port


class TestNeoStatus(unittest.TestCase):
    def test_snapshot_and_alerts(self):
        with TemporaryDirectory() as tmp:
            with StubServer() as s:
                ap = Path(tmp) / "a.json"
                ap.write_text(json.dumps(make_approval()))
                H.submit(make_finding(), ap, CITY, s.client, Path(tmp) / "rec")
                snap = NS.build_snapshot(s.client, CITY)
                self.assertEqual(len(snap["findings"]), 1)
                self.assertEqual(snap["supervisor"]["status"], "ok")
                # stub city: 1 ready, 0 running agents -> idle alert
                alerts = NS.evaluate(snap)
                rules = [a["rule"] for a in alerts]
                self.assertIn("orchestrator-idle-with-ready-work", rules)
                self.assertIn("finding-without-receipt", rules)
                md = NS.render_markdown(snap, alerts)
                self.assertIn("NEO status", md)
                self.assertIn("orchestrator-idle-with-ready-work", md)

    def test_duplicates_detected(self):
        with TemporaryDirectory() as tmp:
            with StubServer() as s:
                StubState.beads["a1"] = {"id": "a1", "title": "DW-TEST-1 — x", "labels": []}
                StubState.beads["a2"] = {"id": "a2", "title": "DW-TEST-1 — x", "labels": []}
                snap = NS.build_snapshot(s.client, CITY)
                self.assertEqual(snap["duplicates"],
                                 [{"task_id": "DW-TEST-1", "beads": ["a1", "a2"]}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
