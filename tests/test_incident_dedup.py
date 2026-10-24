"""Doctrine §4: repeat alerts collapse into the open incident.

Same identity + same rule + overlapping window -> one incident; repeats
append evidence and must NOT re-propose status messages."""
import json
import sqlite3
import time
import unittest

from director_consumer.incidents import CorrelationRule, IncidentStore
from director_consumer.schema import Assessment, SourceRef


def _assessment(rule_id: str, severity: str, trigger: str) -> Assessment:
    return Assessment(
        rule_id=rule_id, severity=severity, impacts=["fleet_infra"],
        action="notify", authority="agent:m5-opencode", rationale="r",
        evidence=[SourceRef(feed="fleet_changes", source_id=trigger,
                            original_ts=None, observed_ts=time.time(),
                            locator="x")],
        event_kind="fleet_change", event_summary=f"evt {trigger}",
        trigger_key=trigger)


class IncidentDedupTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        rule = CorrelationRule(cid="fleet-infra",
                               group_rules=["fleet-infra-change"],
                               group_key="feed", window_h=24,
                               suspected_cause="infra delta")
        self.inc = self.inc = IncidentStore(self.con, [rule], project="designwins")

    def test_repeat_alert_is_flagged_and_severity_kept(self):
        a1 = _assessment("fleet-infra-change", "P2", "happy#oom#176")
        iid = self.inc.observe(a1)
        self.assertIsNotNone(iid)
        repeat, sev = self.inc.repeat_status(iid)
        self.assertFalse(repeat, "first occurrence is not a repeat")
        self.assertEqual(sev, "P2")
        a2 = _assessment("fleet-infra-change", "P2", "happy#oom#183")
        iid2 = self.inc.observe(a2)
        self.assertEqual(iid, iid2,
                         "same identity+rule+window = same incident")
        repeat2, sev2 = self.inc.repeat_status(iid2)
        self.assertTrue(repeat2, "second occurrence must be a repeat")
        self.assertEqual(sev2, "P2")
        row = self.con.execute(
            "SELECT evidence, occurrence FROM incidents WHERE incident_id=?",
            (iid,)).fetchone()
        ev = json.loads(row[0])
        self.assertGreaterEqual(len(ev), 2, "repeats append evidence")

    def test_unknown_rule_no_incident(self):
        a = _assessment("no-such-rule", "P2", "x")
        self.assertIsNone(self.inc.observe(a))


if __name__ == "__main__":
    unittest.main()
