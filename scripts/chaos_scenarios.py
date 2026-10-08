#!/usr/bin/env python3
"""Chaos scenarios for the extraction pipeline (L2 proof).

Each scenario injects a failure and asserts the system's contract:
  no job loss, no junk verified, other lanes unaffected, bounded recovery.

Runs against a sandbox pipeline DB (never production). Scenarios:
  1. worker_dies_mid_claim      -> visibility timeout requeues
  2. canon_down                 -> breaker opens, adjudicate 503s, no fake verdicts
  3. dispatcher_restart         -> state survives (SQLite), no re-enqueue storm
  4. poison_pdf                 -> job dead-letters after max_attempts, ledger row
  5. budget_exhaustion          -> frontier jobs 429, deterministic unaffected
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.pipeline import store
from harness.pipeline.ladder import classify_batch


class Sandbox:
    def __enter__(self):
        self.fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(self.fd)
        self.con = store.connect(self.path)
        return self

    def __exit__(self, *a):
        self.con.close()
        os.unlink(self.path)


def scenario_worker_dies_mid_claim():
    with Sandbox() as s:
        jid = store.enqueue(s.con, "substrate", "pdf:doc1", source_path="/x.pdf")
        store.claim(s.con, "worker-that-dies")
        s.con.execute("UPDATE jobs SET claimed_at = claimed_at - 7200 WHERE id=?", (jid,))
        s.con.commit()
        reclaimed = store.claim(s.con, "worker-2")
        assert len(reclaimed) == 1 and reclaimed[0]["id"] == jid, "job must requeue after visibility expiry"
        return "PASS: visibility timeout requeued the orphaned job"


def scenario_canon_down():
    from harness.pipeline import api
    api._BREAKER["open_until"] = 0.0
    api._BREAKER["fails"] = 0
    with Sandbox() as s:
        jid = store.enqueue(s.con, "extract", "sha256:doc2")
        rid = store.ack(s.con, jid, "student", "1", json.dumps([{"symbol": "VDS", "value": 60}]))
        h = api.Handler.__new__(api.Handler)
        h.con = s.con
        h._json = lambda code, obj: (code, obj)
        with mock.patch.object(api.urllib.request, "urlopen",
                               side_effect=ConnectionRefusedError("canon down")):
            for _ in range(5):
                code, _ = h._adjudicate({"result_id": rid})
                assert code == 503, "canon down must 503, never fake a verdict"
            code, out = h._adjudicate({"result_id": rid})
            assert "circuit breaker open" in str(out.get("error", "")), "breaker must open after 5 fails"
        n = s.con.execute("SELECT COUNT(*) c FROM adjudication_ledger WHERE verdict='SUPPORTED'").fetchone()["c"]
        assert n == 0, "no verdict rows may be written while canon is down"
        return "PASS: canon-down 503s, breaker opened, zero fabricated verdicts"


def scenario_dispatcher_restart():
    with Sandbox() as s:
        for i in range(5):
            store.enqueue(s.con, "substrate", f"pdf:doc{i}")
        store.claim(s.con, "w", limit=2)
        s.con.close()
        s.con = store.connect(s.path)
        pending = s.con.execute("SELECT COUNT(*) c FROM jobs WHERE state='pending'").fetchone()["c"]
        total = s.con.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
        assert total == 5 and pending == 3, f"state must survive restart (total={total}, pending={pending})"
        return "PASS: dispatcher restart preserved all 5 jobs, claims intact"


def scenario_poison_pdf():
    with Sandbox() as s:
        jid = store.enqueue(s.con, "substrate", "pdf:poison", max_attempts=3)
        for attempt in range(3):
            store.claim(s.con, "w")
            store.fail(s.con, jid, "not a real pdf")
        state = s.con.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()["state"]
        assert state == "dead", "poison must dead-letter, never loop forever"
        ledger = s.con.execute(
            "SELECT COUNT(*) c FROM adjudication_ledger WHERE verdict='JOB_FAILED'").fetchone()["c"]
        assert ledger == 3, "each failure lands in the ledger"
        return "PASS: poison pdf dead-lettered after 3 attempts with full ledger trail"


def scenario_budget_exhaustion():
    with Sandbox() as s:
        for _ in range(store.TIER_DAILY_CAPS["frontier"]):
            ok, _ = store.budget_check(s.con, "frontier")
            assert ok
        ok, spend = store.budget_check(s.con, "frontier")
        assert not ok and spend["cap"] == 50, "frontier must 429 at cap"
        ok_det, _ = store.budget_check(s.con, "deterministic")
        assert ok_det, "deterministic tier unaffected by frontier cap"
        return "PASS: frontier capped at 50/day, deterministic unaffected"


def scenario_ladder_halt_prevents_pollution():
    verdicts = [{"verdict": "UNSUPPORTED", "reason_code": "unit_mismatch"}] * 10 + \
               [{"verdict": "SUPPORTED"}] * 90
    rows, batch = classify_batch(verdicts)
    assert batch["batch"] == "halt", "10% P0 must halt batch"
    quarantined = sum(1 for r in rows if r["action"] == "quarantine")
    assert quarantined == 10
    return "PASS: 10% unit-error batch halted, 10 rows quarantined"


SCENARIOS = [
    scenario_worker_dies_mid_claim,
    scenario_canon_down,
    scenario_dispatcher_restart,
    scenario_poison_pdf,
    scenario_budget_exhaustion,
    scenario_ladder_halt_prevents_pollution,
]


def main():
    passed = failed = 0
    for sc in SCENARIOS:
        try:
            print(f"{sc.__name__}: {sc()}", flush=True)
            passed += 1
        except AssertionError as e:
            print(f"{sc.__name__}: FAIL — {e}", flush=True)
            failed += 1
        except Exception as e:
            print(f"{sc.__name__}: ERROR — {e}", flush=True)
            failed += 1
    print(f"\nCHaos matrix: {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
