"""Offline unit + negative-control tests for the NEO<->Gas City bridge.

Run: python3 -m unittest discover -s tests -v
No network: the supervisor is an in-process stub asserting every law
(CSRF header, Idempotency-Key, read-back-before-receipt, event proof,
digest binding, single-use ledger, trust-root placement).

Covers review #6448553783:
  finding 1 — authorization integrity (authenticity/scope/single-use/forge)
  finding 2 — proof-gated acceptance receipts (read-back + event evidence)
  finding 3 — publisher stability + sanitization (see test_publisher.py)
"""
from __future__ import annotations

import json
import os
import stat
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

CITY = "designwins-staging"


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


def make_approval(finding=None, **over):
    finding = finding or make_finding()
    a = {
        "schema": "dw.approval.gascity-ingest.v1",
        "environment": "staging",
        "project": "designwins", "action": "gascity-ingest",
        "recipient": CITY, "task_ref": finding["task_id"],
        "issuer": "staging-fixture",
        "authorization_ref": "unit-test fixture",
        "expires_at": int(time.time()) + 3600, "max_actions": 1,
        "spend_scope": "none", "max_spend_usd": 0,
        "payload_digest": F.digest(finding),
    }
    a.update(over)
    return a


class TrustDir:
    """A 0700 trust root containing one approval artifact."""

    def __init__(self, tmp: str, approval: dict, name: str = "ap.json"):
        self.root = Path(tmp) / "trust-staging"
        self.root.mkdir(mode=0o700)
        self.path = self.root / name
        self.path.write_text(json.dumps(approval))
        os.chmod(self.path, 0o600)

    @property
    def roots(self):
        return [self.root]


class StubState:
    beads: dict[str, dict] = {}
    post_calls: list[dict] = []
    seq = 100
    fail_next_creates: int = 0
    events_extra: list[dict] = []            # injected into event listing
    suppress_create_events: bool = False     # hide bead.created (negative ctl)
    drop_bead_labels_in_events: bool = False # event without matching label
    fail_bead_get: bool = False              # read-back 404 (negative ctl)
    strip_metadata_on_read: bool = False     # metadata lost on read-back
    event_metadata_digest: str | None = None  # corrupt event metadata digest


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

    def _events(self):
        items = list(StubState.events_extra)
        if not StubState.suppress_create_events:
            for b in StubState.beads.values():
                labels = [] if StubState.drop_bead_labels_in_events else (b.get("labels") or [])
                meta = dict(b.get("metadata") or {})
                if StubState.event_metadata_digest and meta:
                    meta["neo-digest"] = StubState.event_metadata_digest
                items.append({"seq": b["_seq"], "type": "bead.created",
                              "ts": "stub", "subject": b["id"],
                              "payload": {"bead": {"id": b["id"], "labels": labels,
                                                   "metadata": meta}}})
        return sorted(items, key=lambda e: e.get("seq", 0))

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
            return self._send(200, {"items": [
                {"kind": "tool_approval", "session_id": "s1", "request_id": "r1",
                 "prompt": "PRIVATE: should I run rm -rf?"}], "total": 1})
        if "/sessions" in p:
            return self._send(200, {"items": [{"id": "s1", "state": "active"}], "total": 1})
        if "/events" in p:
            return self._send(200, {"items": self._events()})
        if "/bead/" in p:
            bid = p.rstrip("/").split("/")[-1]
            if StubState.fail_bead_get or bid not in StubState.beads:
                return self._send(404, {"code": "bead-not-found"})
            b = dict(StubState.beads[bid])
            if StubState.strip_metadata_on_read:
                b.pop("metadata", None)
            return self._send(200, b)
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
        if not self.headers.get("X-GC-Request"):
            return self._send(403, {"code": "csrf"})
        idem = self.headers.get("Idempotency-Key", "")
        StubState.post_calls.append({"idem": idem, "body": body})
        if not idem.startswith("neo-"):
            return self._send(400, {"code": "bad-idempotency-key"})
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
                "_idem": idem, "_seq": StubState.seq, "assignee": None}
        StubState.beads[bid] = bead
        return self._send(200, bead)


class StubServer:
    def __enter__(self):
        StubState.beads, StubState.post_calls, StubState.seq = {}, [], 100
        StubState.fail_next_creates = 0
        StubState.events_extra = []
        StubState.suppress_create_events = False
        StubState.drop_bead_labels_in_events = False
        StubState.fail_bead_get = False
        StubState.strip_metadata_on_read = False
        StubState.event_metadata_digest = None
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubSupervisor)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.client = GasCityClient(f"http://127.0.0.1:{self.port}")
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()


def submit(finding, approval, tmp, client, extra_trust=()):
    td = TrustDir(tmp if isinstance(tmp, str) else str(tmp), approval)
    return H.submit(finding, td.path, CITY, client,
                    Path(tmp) / "receipts", list(extra_trust) + td.roots)


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
        self.assertTrue(F.validate(make_finding(finding_id="X-1")))

    def test_digest_and_key_determinism(self):
        f = make_finding()
        self.assertEqual(F.digest(f), F.digest(make_finding()))
        self.assertEqual(F.idempotency_key(f, "a"), F.idempotency_key(make_finding(), "a"))
        self.assertNotEqual(F.idempotency_key(f, "a"), F.idempotency_key(f, "b"))

    def test_bead_payload_shape(self):
        p = F.to_bead_payload(make_finding())
        self.assertEqual(p["title"], "DW-NEO-DEMO-01 — demo finding flows into staging queue")
        self.assertIn("neo-finding:DW-NEO-DEMO-01", p["labels"])
        self.assertEqual(p["metadata"]["neo-digest"], F.digest(make_finding()))


class TestAuthorizationIntegrity(unittest.TestCase):
    """Review finding 1 — every forge/scope/single-use refusal."""

    def _refusal(self, finding=None, approval_over=None, trust_mode="ok"):
        with TemporaryDirectory() as tmp:
            approval = make_approval(finding, **(approval_over or {}))
            with StubServer() as s:
                if trust_mode == "outside":
                    ap = Path(tmp) / "elsewhere.json"
                    ap.write_text(json.dumps(approval))
                    roots = [Path(tmp) / "trust-not-used"]
                    (Path(tmp) / "trust-not-used").mkdir()
                    r = H.submit(finding or make_finding(), ap, CITY, s.client,
                                 Path(tmp) / "rec", roots)
                elif trust_mode == "world-writable":
                    td = TrustDir(tmp, approval)
                    os.chmod(td.root, 0o777)
                    r = H.submit(finding or make_finding(), td.path, CITY, s.client,
                                 Path(tmp) / "rec", td.roots)
                else:
                    r = submit(finding or make_finding(), approval, tmp, s.client)
                return r, len(StubState.post_calls)

    def test_forged_issuer_string_refused(self):
        r, posts = self._refusal(approval_over={"issuer": "sam"})
        self.assertEqual(r["state"], "refused")
        self.assertIn(r["refusal"]["reason"],
                      ("unauthorized-issuer", "invalid-approval"))
        self.assertEqual(posts, 0)

    def test_missing_digest_refused(self):
        r, posts = self._refusal(approval_over={"payload_digest": None})
        self.assertEqual(r["refusal"]["reason"], "invalid-approval")
        self.assertEqual(posts, 0)

    def test_digest_mismatch_refused(self):
        r, posts = self._refusal(approval_over={"payload_digest": "0" * 64})
        self.assertEqual(r["refusal"]["reason"], "digest-mismatch")
        self.assertEqual(posts, 0)

    def test_wrong_project_refused(self):
        r, posts = self._refusal(approval_over={"project": "other"})
        self.assertIn(r["refusal"]["reason"], ("unauthorized-project", "invalid-approval"))
        self.assertEqual(posts, 0)

    def test_wrong_action_refused(self):
        r, posts = self._refusal(approval_over={"action": "gascity-dispatch"})
        self.assertIn(r["refusal"]["reason"],
                      ("unauthorized-action", "invalid-approval"))
        self.assertEqual(posts, 0)

    def test_nonzero_spend_refused(self):
        r, posts = self._refusal(approval_over={"max_spend_usd": 5})
        self.assertIn(r["refusal"]["reason"],
                      ("spend-not-authorized", "invalid-approval"))
        self.assertEqual(posts, 0)

    def test_max_actions_gt_1_refused(self):
        r, posts = self._refusal(approval_over={"max_actions": 2})
        self.assertEqual(r["refusal"]["reason"], "invalid-approval")
        self.assertEqual(posts, 0)

    def test_expired_refused(self):
        r, posts = self._refusal(approval_over={"expires_at": int(time.time()) - 10})
        self.assertEqual(r["refusal"]["reason"], "approval-expired")
        self.assertEqual(posts, 0)

    def test_wrong_city_refused(self):
        r, posts = self._refusal(approval_over={"recipient": "designwins-staging2"})
        self.assertEqual(r["refusal"]["reason"], "unauthorized-recipient")
        self.assertEqual(posts, 0)

    def test_task_mismatch_refused(self):
        r, posts = self._refusal(approval_over={"task_ref": "OTHER-9"})
        self.assertEqual(r["refusal"]["reason"], "task-mismatch")
        self.assertEqual(posts, 0)

    def test_staging_artifact_cannot_target_production_city(self):
        with TemporaryDirectory() as tmp:
            approval = make_approval(recipient="designwins-orchestrator")
            td = TrustDir(tmp, approval)
            with StubServer() as s:
                r = H.submit(make_finding(), td.path, "designwins-orchestrator",
                             s.client, Path(tmp) / "rec", td.roots)
            self.assertIn(r["refusal"]["reason"],
                          ("environment-mismatch", "invalid-approval"))
            self.assertEqual(StubState.post_calls, [])

    def test_untrusted_path_refused(self):
        r, posts = self._refusal(trust_mode="outside")
        self.assertEqual(r["refusal"]["reason"], "untrusted-path")
        self.assertEqual(posts, 0)

    def test_world_writable_trust_root_refused(self):
        r, posts = self._refusal(trust_mode="world-writable")
        self.assertEqual(r["refusal"]["reason"], "untrusted-trust-dir")
        self.assertEqual(posts, 0)

    def test_missing_artifact_file_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "trust"
            root.mkdir(mode=0o700)
            with StubServer() as s:
                r = H.submit(make_finding(), root / "gone.json", CITY, s.client,
                             Path(tmp) / "rec", [root])
            self.assertEqual(r["refusal"]["reason"], "invalid-approval")
            self.assertEqual(StubState.post_calls, [])

    def test_github_finding_cannot_authorize_itself(self):
        # a fully-valid finding with authorization_state=authorized but NO
        # artifact anywhere -> refused; nothing on GitHub is an authorization
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "trust"
            root.mkdir(mode=0o700)
            with StubServer() as s:
                r = H.submit(make_finding(), root / "absent.json", CITY, s.client,
                             Path(tmp) / "rec", [root])
            self.assertEqual(r["state"], "refused")
            self.assertEqual(StubState.post_calls, [])

    def test_proposal_finding_never_dispatched(self):
        r, posts = self._refusal(finding=make_finding(authorization_state="proposal"))
        self.assertEqual(r["refusal"]["reason"], "finding-not-authorized")
        self.assertEqual(posts, 0)

    def test_reused_artifact_cannot_authorize_second_finding(self):
        # same artifact replayed against a DIFFERENT (altered) finding must
        # refuse on digest binding — no second task can be created
        with TemporaryDirectory() as tmp:
            f1 = make_finding()
            f2 = make_finding(finding_id="DW-NEO-DEMO-02", task_id="DW-NEO-DEMO-02 — two")
            td = TrustDir(tmp, make_approval(f1))
            with StubServer() as s:
                r1 = H.submit(f1, td.path, CITY, s.client, Path(tmp) / "rec", td.roots)
                r2 = H.submit(f2, td.path, CITY, s.client, Path(tmp) / "rec", td.roots)
            self.assertEqual(r1["state"], "accepted")
            self.assertEqual(r2["state"], "refused")
            self.assertIn(r2["refusal"]["reason"],
                          ("digest-mismatch", "task-mismatch", "approval-reused"))
            self.assertEqual(len(StubState.post_calls), 1)


class TestAcceptanceReceipts(unittest.TestCase):
    """Review finding 2 — proof-gated acceptance."""

    def test_accepted_with_full_proof(self):
        with TemporaryDirectory() as tmp:
            r, _ = None, 0
            with StubServer() as s:
                r = submit(make_finding(), make_approval(), tmp, s.client)
            self.assertEqual(r["state"], "accepted")
            self.assertEqual(r["stage"], "BEAD_ACCEPTED")
            self.assertTrue(r["bead_id"])
            self.assertEqual(r["readback"]["metadata"]["neo-digest"],
                             F.digest(make_finding()))
            self.assertEqual(r["event_proof"]["subject"], r["bead_id"])
            self.assertTrue(r["event_proof"]["label_match"])

    def test_missing_event_downgrades_to_persisted_only(self):
        with TemporaryDirectory() as tmp:
            with StubServer() as s:
                StubState.suppress_create_events = True
                r = submit(make_finding(), make_approval(), tmp, s.client)
            self.assertEqual(r["state"], "accepted-persisted")
            self.assertEqual(r["stage"], "BEAD_ACCEPTED_PERSISTED_ONLY")
            self.assertIn("persisted-only", r["note"])

    def test_unrelated_event_is_not_proof(self):
        with TemporaryDirectory() as tmp:
            with StubServer() as s:
                StubState.suppress_create_events = True
                StubState.events_extra = [
                    {"seq": 99, "type": "bead.created", "ts": "stub",
                     "subject": "xx-unrelated",
                     "payload": {"bead": {"id": "xx-unrelated", "labels": []}}}]
                r = submit(make_finding(), make_approval(), tmp, s.client)
            self.assertEqual(r["state"], "accepted-persisted")  # weaker, honest

    def test_failed_readback_never_accepted(self):
        with TemporaryDirectory() as tmp:
            with StubServer() as s:
                StubState.fail_bead_get = True
                r = submit(make_finding(), make_approval(), tmp, s.client)
            self.assertEqual(r["state"], "unverified")
            self.assertIn("read-back", r["verification_failure"])
            self.assertNotEqual(r["state"], "accepted")

    def test_metadata_digest_mismatch_fails_closed(self):
        with TemporaryDirectory() as tmp:
            finding = make_finding()
            approval = make_approval(finding)
            # craft payload whose stored digest differs from finding digest
            F_orig = F.digest
            payload = F.to_bead_payload(finding)
            payload["metadata"]["neo-digest"] = "0" * 64
            with StubServer() as s:
                td = TrustDir(tmp, approval)
                client = s.client
                key = F.idempotency_key(finding, td.path.read_text()[:8])
                s.client.create_bead(CITY, payload, key)
                r = H.submit(finding, td.path, CITY, client, Path(tmp) / "rec", td.roots)
            # pre-check label lookup returns the forged bead; read-back must refuse
            self.assertIn(r["state"], ("unverified", "refused", "duplicate"))
            if r["state"] != "duplicate":
                self.assertNotEqual(r["state"], "accepted")

    def test_network_error_is_api_error_not_accepted(self):
        with TemporaryDirectory() as tmp:
            ap_written = make_approval()
            td = TrustDir(tmp, ap_written)
            with StubServer() as s:
                def boom(*a, **k):
                    raise ApiError(500, {"code": "boom"})
                s.client.create_bead = boom
                r = H.submit(make_finding(), td.path, CITY, s.client,
                             Path(tmp) / "rec", td.roots)
            self.assertEqual(r["state"], "api-error")

    def test_5xx_retries_then_accepts(self):
        with TemporaryDirectory() as tmp:
            td = TrustDir(tmp, make_approval())
            with StubServer() as s:
                StubState.fail_next_creates = 2
                r = H.submit(make_finding(), td.path, CITY, s.client,
                             Path(tmp) / "rec", td.roots)
            self.assertEqual(r["state"], "accepted")
            self.assertEqual(r["attempts"], 3)

    def test_4xx_no_retry(self):
        with TemporaryDirectory() as tmp:
            td = TrustDir(tmp, make_approval())
            with StubServer() as s:
                s.client.create_bead = lambda *a, **k: (_ for _ in ()).throw(
                    ApiError(422, {"code": "validation-failed"}))
                r = H.submit(make_finding(), td.path, CITY, s.client,
                             Path(tmp) / "rec", td.roots)
            self.assertEqual(r["state"], "api-error")
            self.assertEqual(r["attempts"], 1)

    def test_ledger_replay_returns_prior_receipt_no_post(self):
        with TemporaryDirectory() as tmp:
            td = TrustDir(tmp, make_approval())
            with StubServer() as s:
                r1 = H.submit(make_finding(), td.path, CITY, s.client,
                              Path(tmp) / "rec", td.roots)
                r2 = H.submit(make_finding(), td.path, CITY, s.client,
                              Path(tmp) / "rec", td.roots)
            self.assertEqual(r1["state"], "accepted")
            self.assertEqual(r2["state"], "duplicate")
            self.assertEqual(r2["duplicate_source"], "approval-ledger")
            self.assertEqual(r2["bead_id"], r1["bead_id"])
            self.assertEqual(len(StubState.post_calls), 1)

    def test_label_dedup_without_ledger(self):
        # bead exists (e.g. ledger lost) — label check still prevents re-create
        with TemporaryDirectory() as tmp:
            td = TrustDir(tmp, make_approval())
            with StubServer() as s:
                s.client.create_bead(CITY, F.to_bead_payload(make_finding()),
                                     "neo-manual-seed")
                r = H.submit(make_finding(), td.path, CITY, s.client,
                             Path(tmp) / "rec", td.roots)
            self.assertEqual(r["state"], "duplicate")
            self.assertEqual(r["duplicate_source"], "label-check")

    def test_csrf_header_enforced(self):
        import urllib.request, urllib.error
        with StubServer() as s:
            req = urllib.request.Request(
                f"http://127.0.0.1:{s.port}/v0/city/{CITY}/beads",
                method="POST", data=b"{}",
                headers={"Content-Type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=5)
                self.fail("expected 403")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 403)


class TestStagesAndSanitization(unittest.TestCase):
    def test_verification_stages_distinct(self):
        open_bead = {"status": "open", "labels": ["neo-finding:X"]}
        running = {"status": "in_progress", "assignee": "engineer-1"}
        closed = {"status": "closed"}
        closed_verified = {"status": "closed",
                           "metadata": {"neo-finding-id": "DW-NEO-DEMO-01"}}
        self.assertEqual(NS.verification_stage(open_bead), "BEAD_ACCEPTED")
        self.assertEqual(NS.verification_stage(running), "AGENT_RUNNING")
        self.assertEqual(NS.verification_stage(closed), "WORK_VERIFIED_PENDING_PROOF")
        self.assertEqual(NS.verification_stage(
            closed_verified, [{"finding_id": "DW-NEO-DEMO-01",
                               "verification": {"pr": 7, "ci": "passed"}}]),
            "WORK_VERIFIED")

    def test_sanitize_drops_private_fields(self):
        snap = {
            "city": {"work": {}, "partial_errors": ["x" * 200]},
            "captured_at": "2026-10-27T00:00:00Z",
            "supervisor": {"status": "ok"},
            "pending_interactions": [
                {"kind": "tool_approval", "session_id": "s1", "request_id": "r1",
                 "prompt": "PRIVATE", "options": ["a"], "metadata": {"x": 1}}],
            "sessions": [{"id": "s1", "state": "active", "transcript_path": "/p"}],
            "findings": [{"id": "b1", "status": "open", "title": "t",
                          "description": "PRIVATE DETAIL",
                          "labels": ["neo-finding:X"],
                          "metadata": {"neo-finding-id": "X", "junk": "v"},
                          "assignee": None}],
            "recent_events": [{"seq": 1, "type": "bead.created",
                               "subject": "b1", "payload": {"big": "PRIVATE"}}],
        }
        s = NS.sanitize_snapshot(snap)
        p = s["pending_interactions"][0]
        self.assertEqual(set(p), {"kind", "session_id", "request_id"})
        raw = json.dumps(s)
        self.assertNotIn("PRIVATE", raw)
        self.assertNotIn("description", json.dumps(s["findings"][0]))
        self.assertNotIn("payload", json.dumps(s["recent_events"][0]))
        self.assertEqual(s["findings"][0]["stage"], "BEAD_ACCEPTED")

    def test_stale_snapshot_alert(self):
        old = "2026-10-27T00:00:00Z"
        snap = {"city": {"work": {}}, "agents": [], "findings": [],
                "captured_at": old, "pending_interactions": []}
        import calendar
        now = calendar.timegm(time.strptime(old, "%Y-%m-%dT%H:%M:%SZ")) + 40 * 60
        rules = [a["rule"] for a in NS.evaluate(snap, now_epoch=now)]
        self.assertIn("snapshot-stale", rules)

    def test_blocked_work_alert(self):
        snap = {"city": {"work": {}}, "agents": [], "findings": [],
                "blocked": ["b1", "b2"], "pending_interactions": [],
                "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        rules = [a["rule"] for a in NS.evaluate(snap)]
        self.assertIn("blocked-work", rules)


class TestSchemaValidator(unittest.TestCase):
    """P0 — the COMPLETE JSON Schema is applied, not a field list."""

    def test_valid_approval_passes_schema(self):
        from bridge import schema as SC
        a = make_approval()
        a.pop("schema", None)
        full = {"schema": "dw.approval.gascity-ingest.v1", **a}
        self.assertEqual(SC.validate(full, "dw.approval.gascity-ingest.v1.json"), [])

    def test_schema_rejects_unknown_field(self):
        from bridge import schema as SC
        a = make_approval()
        a["extra_field"] = "nope"
        errs = SC.validate({"schema": "dw.approval.gascity-ingest.v1", **a},
                           "dw.approval.gascity-ingest.v1.json")
        self.assertTrue(any("additional property" in e for e in errs))

    def test_schema_rejects_wrong_types(self):
        from bridge import schema as SC
        a = make_approval(expires_at="soon")
        errs = SC.validate({"schema": "dw.approval.gascity-ingest.v1", **a},
                           "dw.approval.gascity-ingest.v1.json")
        self.assertTrue(any("expires_at" in e for e in errs))

    def test_schema_environment_if_then(self):
        from bridge import schema as SC
        a = make_approval(environment="production")   # issuer staging-fixture
        errs = SC.validate({"schema": "dw.approval.gascity-ingest.v1", **a},
                           "dw.approval.gascity-ingest.v1.json")
        self.assertTrue(any("issuer" in e for e in errs))
        b = make_approval(environment="staging", recipient="designwins-orchestrator")
        errs = SC.validate({"schema": "dw.approval.gascity-ingest.v1", **b},
                           "dw.approval.gascity-ingest.v1.json")
        self.assertTrue(any("recipient" in e for e in errs))

    def test_schema_rejects_bad_digest_shape(self):
        from bridge import schema as SC
        a = make_approval(payload_digest="not-hex")
        errs = SC.validate({"schema": "dw.approval.gascity-ingest.v1", **a},
                           "dw.approval.gascity-ingest.v1.json")
        self.assertTrue(any("payload_digest" in e for e in errs))


class TestProvenanceChain(unittest.TestCase):
    """P0 — ownership/UID chain, symlinks, service-uid separation."""

    def test_symlinked_artifact_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "trust-staging"
            root.mkdir(mode=0o700)
            real = root / "real.json"
            real.write_text(json.dumps(make_approval()))
            os.chmod(real, 0o600)
            link = Path(tmp) / "outside.json"
            link.symlink_to(real)
            with StubServer() as s:
                r = H.submit(make_finding(), link, CITY, s.client,
                             Path(tmp) / "rec", [root])
            self.assertEqual(r["state"], "refused")
            self.assertEqual(r["refusal"]["reason"], "untrusted-path")

    def test_symlink_inside_trust_root_refused(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "trust-staging"
            root.mkdir(mode=0o700)
            real = root / "real.json"
            real.write_text(json.dumps(make_approval()))
            os.chmod(real, 0o600)
            (root / "alias.json").symlink_to(real)
            with StubServer() as s:
                r = H.submit(make_finding(), root / "alias.json", CITY, s.client,
                             Path(tmp) / "rec", [root])
            self.assertEqual(r["refusal"]["reason"], "untrusted-path")

    def test_production_root_owned_by_service_uid_refused(self):
        # a root whose name implies production but is owned by the invoking
        # (bridge/agent) uid must refuse: agents cannot hold approval roots
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "trust-prod"
            root.mkdir(mode=0o700)
            art = root / "ap.json"
            art.write_text(json.dumps(make_approval(environment="production",
                                                    issuer="sam")))
            os.chmod(art, 0o600)
            with StubServer() as s:
                r = H.submit(make_finding(), art, CITY, s.client,
                             Path(tmp) / "rec", [root])
            self.assertEqual(r["refusal"]["reason"], "untrusted-trust-dir")
            self.assertIn("invoking uid", r["refusal"]["detail"])

    def test_env_configured_production_root_enforces_uid_separation(self):
        import importlib
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "operator-approvals"
            root.mkdir(mode=0o700)
            art = root / "ap.json"
            art.write_text(json.dumps(make_approval(environment="production",
                                                    issuer="sam",
                                                    recipient="designwins-orchestrator")))
            os.chmod(art, 0o600)
            os.environ["GC_INGEST_TRUST_PROD"] = str(root)
            try:
                importlib.reload(H)
                with StubServer() as s:
                    r = H.submit(make_finding(), art, CITY, s.client,
                                 Path(tmp) / "rec", None)
                self.assertEqual(r["refusal"]["reason"], "untrusted-trust-dir")
            finally:
                del os.environ["GC_INGEST_TRUST_PROD"]
                importlib.reload(H)

    def test_wrong_owner_artifact_refused(self):
        # staging root owned by euid, but artifact... ownership equal is
        # required; simulate mismatch via a nested root trick is not possible
        # without chown — covered by production-uid test above. Structural
        # test: artifact must be a regular file.
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "trust-staging"
            root.mkdir(mode=0o700)
            art = root / "weird.json"
            os.mkfifo(art) if hasattr(os, "mkfifo") else None
            try:
                with StubServer() as s:
                    r = H.submit(make_finding(), art, CITY, s.client,
                                 Path(tmp) / "rec", [root])
                self.assertEqual(r["refusal"]["reason"], "invalid-approval")
            finally:
                art.unlink(missing_ok=True)


class TestStrictProofs(unittest.TestCase):
    """P1 — empty metadata, exact identity, subject-only events."""

    def test_empty_metadata_fails_verification(self):
        with TemporaryDirectory() as tmp:
            with StubServer() as s:
                StubState.strip_metadata_on_read = True
                r = submit(make_finding(), make_approval(), tmp, s.client)
            self.assertEqual(r["state"], "unverified")
            self.assertIn("metadata", r["verification_failure"])
            self.assertNotEqual(r["state"], "accepted")

    def test_wrong_persisted_digest_fails_verification(self):
        with TemporaryDirectory() as tmp:
            finding = make_finding()
            approval = make_approval(finding)
            payload = F.to_bead_payload(finding)
            payload["metadata"]["neo-digest"] = "f" * 64
            td = TrustDir(tmp, approval)
            with StubServer() as s:
                s.client.create_bead(CITY, payload, "neo-seed-key")
                r = H.submit(finding, td.path, CITY, s.client, Path(tmp) / "rec", td.roots)
            self.assertEqual(r["state"], "unverified")
            self.assertIn("digest", r["verification_failure"])

    def test_subject_only_event_is_not_full_proof(self):
        with TemporaryDirectory() as tmp:
            with StubServer() as s:
                StubState.drop_bead_labels_in_events = True
                r = submit(make_finding(), make_approval(), tmp, s.client)
            self.assertEqual(r["state"], "accepted-persisted")
            self.assertEqual(r["stage"], "BEAD_ACCEPTED_PERSISTED_ONLY")
            self.assertIsNone(r["event_proof"])

    def test_event_digest_mismatch_is_not_full_proof(self):
        with TemporaryDirectory() as tmp:
            with StubServer() as s:
                StubState.event_metadata_digest = "wrong"
                r = submit(make_finding(), make_approval(), tmp, s.client)
            self.assertIn(r["state"], ("accepted-persisted", "unverified"))
            self.assertNotEqual(r["state"], "accepted")

    def test_missing_event_downgrades_to_persisted_only(self):
        with TemporaryDirectory() as tmp:
            with StubServer() as s:
                StubState.suppress_create_events = True
                r = submit(make_finding(), make_approval(), tmp, s.client)
            self.assertEqual(r["state"], "accepted-persisted")

    def test_unrelated_event_is_not_proof(self):
        with TemporaryDirectory() as tmp:
            with StubServer() as s:
                StubState.suppress_create_events = True
                StubState.events_extra = [
                    {"seq": 99, "type": "bead.created", "ts": "stub",
                     "subject": "xx-unrelated",
                     "payload": {"bead": {"id": "xx-unrelated", "labels": []}}}]
                r = submit(make_finding(), make_approval(), tmp, s.client)
            self.assertEqual(r["state"], "accepted-persisted")

    def test_failed_readback_never_accepted(self):
        with TemporaryDirectory() as tmp:
            with StubServer() as s:
                StubState.fail_bead_get = True
                r = submit(make_finding(), make_approval(), tmp, s.client)
            self.assertEqual(r["state"], "unverified")

    def test_exact_identity_required_in_readback(self):
        # finding-id mismatch in persisted metadata must fail
        with TemporaryDirectory() as tmp:
            finding = make_finding()
            approval = make_approval(finding)
            payload = F.to_bead_payload(finding)
            payload["metadata"]["neo-finding-id"] = "DW-NEO-OTHER-99"
            td = TrustDir(tmp, approval)
            with StubServer() as s:
                s.client.create_bead(CITY, payload, "neo-seed-key")
                r = H.submit(finding, td.path, CITY, s.client, Path(tmp) / "rec", td.roots)
            self.assertEqual(r["state"], "unverified")
            self.assertIn("neo-finding-id", r["verification_failure"])


class TestAtomicSingleUse(unittest.TestCase):
    """P2 — atomic consumption, concurrency, crash recovery."""

    def test_concurrent_submissions_create_one_bead(self):
        from concurrent.futures import ThreadPoolExecutor
        with TemporaryDirectory() as tmp:
            td = TrustDir(tmp, make_approval())
            with StubServer() as s:
                def go(_):
                    return H.submit(make_finding(), td.path, CITY, s.client,
                                    Path(tmp) / "rec", td.roots)
                with ThreadPoolExecutor(max_workers=4) as ex:
                    results = list(ex.map(go, range(4)))
            self.assertEqual(len(StubState.beads), 1, f"one bead, got {StubState.beads}")
            bead_ids = {r.get("bead_id") for r in results}
            self.assertEqual(len(bead_ids), 1, f"single bead id, got {bead_ids}")
            for r in results:
                self.assertIn(r["state"], ("accepted", "accepted-persisted", "duplicate"))
            # ledger has exactly one reserve and one complete
            ledger = [json.loads(l) for l in
                      (Path(tmp) / "rec" / "approval-ledger.jsonl").read_text().splitlines() if l]
            kinds = sorted(e["kind"] for e in ledger)
            self.assertEqual(kinds, ["complete", "reserve"])

    def test_crash_between_post_and_receipt_resumes(self):
        with TemporaryDirectory() as tmp:
            td = TrustDir(tmp, make_approval())
            rec = Path(tmp) / "rec"
            # simulate crash: reserve written, no complete
            finding = make_finding()
            approval = H.load_approval(td.path, td.roots)
            H.consume_approval(rec, approval, finding, "key-crash")
            with StubServer() as s:
                r = H.submit(finding, td.path, CITY, s.client, rec, td.roots)
            self.assertEqual(r["state"], "accepted")
            ledger = [json.loads(l) for l in (rec / "approval-ledger.jsonl").read_text().splitlines() if l]
            self.assertEqual(sorted(e["kind"] for e in ledger), ["complete", "reserve"])
            # replay after completion returns prior receipt, no POST
            with StubServer() as s:
                r2 = H.submit(finding, td.path, CITY, s.client, rec, td.roots)
            self.assertEqual(r2["state"], "duplicate")
            self.assertEqual(r2["duplicate_source"], "approval-ledger")

    def test_consumption_survives_process_restart(self):
        with TemporaryDirectory() as tmp:
            td = TrustDir(tmp, make_approval())
            rec = Path(tmp) / "rec"
            with StubServer() as s:
                H.submit(make_finding(), td.path, CITY, s.client, rec, td.roots)
            # "restart" = brand-new in-memory state, same ledger file
            import importlib
            import bridge.handoff as H2
            importlib.reload(H2)
            with StubServer() as s:
                r = H2.submit(make_finding(), td.path, CITY, s.client, rec, td.roots)
            self.assertEqual(r["state"], "duplicate")
            self.assertEqual(r["duplicate_source"], "approval-ledger")

    def test_same_artifact_different_finding_refused_after_use(self):
        with TemporaryDirectory() as tmp:
            f1 = make_finding()
            f2 = make_finding(finding_id="DW-NEO-DEMO-02", task_id="DW-NEO-DEMO-02 — two")
            td = TrustDir(tmp, make_approval(f1))
            rec = Path(tmp) / "rec"
            with StubServer() as s:
                r1 = H.submit(f1, td.path, CITY, s.client, rec, td.roots)
                r2 = H.submit(f2, td.path, CITY, s.client, rec, td.roots)
            self.assertEqual(r1["state"], "accepted")
            # refused at the earliest binding law (task/digest) or by the
            # ledger's reuse rule — both are fail-closed single-use enforcement
            self.assertIn(r2["refusal"]["reason"],
                          ("approval-reused", "task-mismatch", "digest-mismatch"))

    def test_torn_ledger_line_tolerated(self):
        with TemporaryDirectory() as tmp:
            td = TrustDir(tmp, make_approval())
            rec = Path(tmp) / "rec"
            rec.mkdir(parents=True)
            (rec / "approval-ledger.jsonl").write_text('{"kind": "reserve", "artifact')  # torn
            with StubServer() as s:
                r = H.submit(make_finding(), td.path, CITY, s.client, rec, td.roots)
            self.assertEqual(r["state"], "accepted")

    def test_native_idempotency_replay_single_bead(self):
        with TemporaryDirectory() as tmp:
            td = TrustDir(tmp, make_approval())
            with StubServer() as s:
                r1 = H.submit(make_finding(), td.path, CITY, s.client,
                              Path(tmp) / "rec", td.roots)
                body, _ = s.client.create_bead(
                    CITY, F.to_bead_payload(make_finding()), r1["idempotency_key"])
                self.assertEqual(body["id"], r1["bead_id"])
                self.assertEqual(len(StubState.beads), 1)

    def test_supervisor_inflight_409_fails_closed(self):
        # live supervisor returns 409 idempotency-in-flight when the same
        # Idempotency-Key is concurrent — bridge must record api-error and
        # never fabricate acceptance (verified live on staging 2026-10-27)
        with TemporaryDirectory() as tmp:
            td = TrustDir(tmp, make_approval())
            with StubServer() as s:
                s.client.create_bead = lambda *a, **k: (_ for _ in ()).throw(
                    ApiError(409, {"code": "idempotency-in-flight"}))
                r = H.submit(make_finding(), td.path, CITY, s.client,
                             Path(tmp) / "rec", td.roots)
            self.assertEqual(r["state"], "api-error")
            self.assertEqual(r["error"]["code"], "idempotency-in-flight")
            self.assertNotEqual(r["state"], "accepted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
