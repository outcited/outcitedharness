import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.pipeline import store
from harness.pipeline.ladder import classify_batch, severity_for


class StoreQueueTests(unittest.TestCase):
    def setUp(self):
        self.fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(self.fd)
        self.con = store.connect(self.path)

    def tearDown(self):
        self.con.close()
        os.unlink(self.path)

    def test_enqueue_claim_ack_roundtrip(self):
        jid = store.enqueue(self.con, "substrate", "sha256:abc", source_path="/x.pdf")
        claimed = store.claim(self.con, "w1")
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["id"], jid)
        second = store.claim(self.con, "w2")
        self.assertEqual(second, [])
        rid = store.ack(self.con, jid, "extract_worker", "2.0.0", json.dumps({"pages": []}),
                        document_sha256="abc", page_label_map={"1": "1"})
        row = self.con.execute("SELECT * FROM results WHERE id=?", (rid,)).fetchone()
        self.assertEqual(row["extractor_version"], "2.0.0")
        self.assertEqual(row["kind_hint"], "substrate")

    def test_claim_after_visibility_expiry(self):
        jid = store.enqueue(self.con, "substrate", "sha256:def")
        store.claim(self.con, "w1")
        self.con.execute("UPDATE jobs SET claimed_at = claimed_at - 7200 WHERE id=?", (jid,))
        self.con.commit()
        reclaimed = store.claim(self.con, "w2")
        self.assertEqual(len(reclaimed), 1)

    def test_fail_goes_pending_then_dead(self):
        jid = store.enqueue(self.con, "substrate", "sha256:x", max_attempts=2)
        for _ in range(2):
            store.claim(self.con, "w")
            store.fail(self.con, jid, "worker died")
        state = self.con.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()["state"]
        self.assertEqual(state, "dead")

    def test_result_unique_per_version(self):
        jid = store.enqueue(self.con, "substrate", "sha256:v")
        store.claim(self.con, "w")
        store.ack(self.con, jid, "e", "1.0", "{}")
        store.claim(self.con, "w")
        store.ack(self.con, jid, "e", "1.0", "{}")
        n = self.con.execute("SELECT COUNT(*) c FROM results").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_ledger_records_and_summarizes(self):
        jid = store.enqueue(self.con, "substrate", "sha256:l")
        rid = store.ack(self.con, jid, "e", "1.0", "{}")
        store.record_verdicts(self.con, rid, "canon", "fp8", [
            {"claim_index": 0, "verdict": "UNSUPPORTED", "failure_class": "unit_mismatch"},
            {"claim_index": 1, "verdict": "SUPPORTED"},
        ])
        summary = store.ledger_summary(self.con)
        classes = {s["failure_class"] for s in summary}
        self.assertIn("unit_mismatch", classes)


class LadderTests(unittest.TestCase):
    def test_p0_unit_mismatch_quarantines(self):
        self.assertEqual(severity_for("UNSUPPORTED", "unit_mismatch"), "P0")

    def test_batch_halts_when_p0_above_threshold(self):
        verdicts = [{"verdict": "UNSUPPORTED", "failure_class": "unit_mismatch"}] * 5 + [
            {"verdict": "SUPPORTED"}
        ] * 100
        rows, batch = classify_batch(verdicts)
        self.assertEqual(batch["batch"], "halt")
        self.assertTrue(all(r["action"] == "quarantine" for r in rows[:5]))

    def test_p1_class_promotes_to_hold(self):
        verdicts = [{"verdict": "NOT_IN_DOC"}] * 10 + [{"verdict": "SUPPORTED"}] * 100
        _, batch = classify_batch(verdicts)
        self.assertEqual(batch["batch"], "hold")

    def test_clean_batch_proceeds(self):
        verdicts = [{"verdict": "SUPPORTED"}] * 50
        rows, batch = classify_batch(verdicts)
        self.assertEqual(batch["batch"], "proceed")
        self.assertTrue(all(r["action"] == "note" for r in rows))


if __name__ == "__main__":
    unittest.main()
