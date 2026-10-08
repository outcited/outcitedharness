import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os
import tempfile

from harness.catalog.store import (attach_claim, connect, plausible_opn,
                                   register_part, resolve_consensus)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(self.fd)
        self.con = connect(self.path)

    def tearDown(self):
        self.con.close()
        os.unlink(self.path)

    def test_plausible_opn_filters_prose(self):
        self.assertTrue(plausible_opn("2N7002L"))
        self.assertTrue(plausible_opn("STM32H7B0IBK6"))
        self.assertFalse(plausible_opn("ABSOLUTE"))
        self.assertFalse(plausible_opn("NOTE"))
        self.assertFalse(plausible_opn("ABC"))

    def test_register_and_source(self):
        register_part(self.con, "2n7002l", vendor="infineon",
                      doc_sha="aa" * 32, coverage="primary",
                      evidence="filename")
        n = self.con.execute("SELECT COUNT(*) c FROM parts").fetchone()["c"]
        self.assertEqual(n, 1)
        s = self.con.execute("SELECT COUNT(*) c FROM part_sources").fetchone()["c"]
        self.assertEqual(s, 1)

    def test_consensus_agreement_across_docs(self):
        for i, sha in enumerate(["aa" * 32, "bb" * 32, "cc" * 32]):
            register_part(self.con, "PARTX", doc_sha=sha)
            attach_claim(self.con, "PARTX",
                         {"symbol": "RDS_ON", "qualifier": "maximum",
                          "condition": "VGS=10V", "value": 0.0055, "unit": "ohm",
                          "quote": "RDS(on) 5.5 mOhm max"},
                         sha, extractor="burn", version="1")
        res = resolve_consensus(self.con, "PARTX", "RDS_ON")
        self.assertEqual(res[0]["resolved_value"], 0.0055)
        self.assertEqual(res[0]["source_count"], 3)
        self.assertGreater(res[0]["confidence"], 0.8)
        self.assertIsNone(res[0]["conflict_class"])

    def test_consensus_conflict_unresolved_without_revisions(self):
        for sha, val in [("aa" * 32, 0.0055), ("bb" * 32, 0.0062)]:
            register_part(self.con, "PARTY", doc_sha=sha)
            attach_claim(self.con, "PARTY",
                         {"symbol": "RDS_ON", "qualifier": "maximum",
                          "value": val, "unit": "ohm", "quote": "x"},
                         sha)
        res = resolve_consensus(self.con, "PARTY", "RDS_ON")
        self.assertEqual(res[0]["conflict_class"], "unresolved")
        self.assertEqual(res[0]["resolved_value"], None)

    def test_consensus_resolves_by_revision_date(self):
        self.con.execute(
            "INSERT INTO doc_revisions (doc_sha256, rev_code, rev_date) VALUES (?,?,?)",
            ("aa" * 32, "Rev A", "2019-01-01"))
        self.con.execute(
            "INSERT INTO doc_revisions (doc_sha256, rev_code, rev_date) VALUES (?,?,?)",
            ("bb" * 32, "Rev B", "2021-06-01"))
        self.con.commit()
        for sha, val in [("aa" * 32, 0.0055), ("bb" * 32, 0.0062)]:
            register_part(self.con, "PARTZ", doc_sha=sha)
            attach_claim(self.con, "PARTZ",
                         {"symbol": "RDS_ON", "qualifier": "maximum",
                          "value": val, "unit": "ohm"}, sha)
        res = resolve_consensus(self.con, "PARTZ", "RDS_ON")
        self.assertEqual(res[0]["conflict_class"], "revision_diff")
        self.assertEqual(res[0]["resolved_value"], 0.0062)


if __name__ == "__main__":
    unittest.main()
