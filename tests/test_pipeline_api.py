import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.pipeline import api, store


class FakeR:
    def __init__(self, code=200, body=None):
        self.code, self.body = code, body or {}

    def read(self):
        return json.dumps({"choices": [{"message": {"content": json.dumps(self.body)}}]}).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ApiContractTests(unittest.TestCase):
    def test_job_validation_rejects_off_council_pillar(self):
        h = api.Handler.__new__(api.Handler)
        h._json = lambda code, obj: (code, obj)
        code, out = h._create_job({"pillar": "weather", "grain": "doc",
                                   "corpus": {"selector": "sha256:x"}})
        self.assertEqual(code, 422)
        self.assertTrue(any("not on the council list" in e for e in out["errors"]))

    def test_job_validation_rejects_unknown_grain_and_budget(self):
        h = api.Handler.__new__(api.Handler)
        h._json = lambda code, obj: (code, obj)
        code, out = h._create_job({"pillar": "pinouts", "grain": "galaxy",
                                   "budget": {"tier": "quantum"},
                                   "corpus": {"selector": "sha256:x"}})
        self.assertEqual(code, 422)
        self.assertEqual(len(out["errors"]), 2)

    def test_job_create_ok(self):
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            h = api.Handler.__new__(api.Handler)
            h.con = store.connect(path)
            h._json = lambda code, obj: (code, obj)
            code, out = h._create_job({"pillar": "pinouts", "grain": "page",
                                       "schema": "pin-gate-v4",
                                       "corpus": {"selector": "sha256:x"},
                                       "budget": {"tier": "local-vision"}})
            self.assertEqual(code, 201)
            self.assertEqual(out["schema"], "pin-gate-v4")
        finally:
            os.unlink(path)

    def test_call_canon_parses_verdicts(self):
        fake = FakeR(body={"verdicts": [
            {"i": 0, "verdict": "UNSUPPORTED", "reason_code": "unit_mismatch",
             "evidence_quote": "5.5 mOhm"}]})
        with mock.patch.object(api.urllib.request, "urlopen", return_value=fake):
            verdicts = api.call_canon("text", [{"symbol": "RDS"}])
        self.assertEqual(verdicts[0]["reason_code"], "unit_mismatch")
        self.assertEqual(verdicts[0]["evidence_quote"], "5.5 mOhm")

    def test_adjudicate_attaches_ladder_actions(self):
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            h = api.Handler.__new__(api.Handler)
            h.con = store.connect(path)
            h._json = lambda code, obj: (code, obj)
            jid = store.enqueue(h.con, "extract", "sha256:z")
            rid = store.ack(h.con, jid, "student", "1", json.dumps(
                [{"symbol": "RDS", "value": 999, "unit": "kOhm"}]))
            fake = FakeR(body={"verdicts": [
                {"i": 0, "verdict": "UNSUPPORTED", "reason_code": "unit_mismatch",
                 "evidence_quote": "51 mOhm max"}]})
            with mock.patch.object(api.urllib.request, "urlopen", return_value=fake):
                code, out = h._adjudicate({"result_id": rid})
            self.assertEqual(code, 200)
            self.assertEqual(out["row_actions"][0]["severity"], "P0")
            self.assertEqual(out["row_actions"][0]["action"], "quarantine")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
