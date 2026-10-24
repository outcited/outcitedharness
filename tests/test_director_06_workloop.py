"""DIRECTOR-WORKLOOP-01 step 1 — GitHub ingest: allowlist, untrusted
definitions, idempotency, no-ack-by-ingest."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sqlite3
import pytest
from director_consumer.gh_workloop import (
    ALLOWLISTED_REPOS, Ingested, ingest, parse_issue)
from director_consumer.tasks import TaskStore


def _ts(tmp_path):
    con = sqlite3.connect(tmp_path / "w.db")
    return TaskStore(con, project="designwins")


def _issue(n, title, body=""):
    return {"number": n, "title": title, "body": body,
            "html_url": f"https://github.com/o/r/issues/{n}"}


def test_title_prefix_defines_task_id():
    tid, missing = parse_issue(_issue(2, "DIRECTOR-WORKLOOP-01 — eliminate copy/paste: GitHub tasks -> real agent ack -> verified PR", "Director lane on Ryzen"))
    assert tid == "DIRECTOR-WORKLOOP-01"
    assert missing == []


def test_missing_lane_is_untrusted_not_dropped(tmp_path):
    ts = _ts(tmp_path)
    out = ingest("outcited/outcitedharness", ts,
                 issues=[_issue(9, "SOMEWORK-07 — no lane keywords anywhere")])
    assert out[0].state == "untrusted-definition"
    assert "lane" in out[0].missing_fields
    row = ts.con.execute("SELECT state FROM tasks WHERE task_id='SOMEWORK-07'").fetchone()
    assert row[0] == "untrusted-definition"


def test_lane_is_proposed_but_never_confers_accountable_ownership(tmp_path):
    ts = _ts(tmp_path)
    out = ingest("outcited/outcitedharness", ts,
                 issues=[_issue(5, "DW-TRUTH-FUNNEL-01 — M5: actual claim/provenance", "m5 lane owner")])
    assert out[0].state == "offered" and out[0].lane == "m5"
    row = ts.con.execute("SELECT state, accountable_owner, epoch FROM tasks WHERE task_id='DW-TRUTH-FUNNEL-01'").fetchone()
    assert row == ("offered", "", 0), "issue title never proves assignment"
    # ingest NEVER acks: no attributable-ack event exists
    evs = ts.con.execute("SELECT to_state FROM task_events WHERE task_id='DW-TRUTH-FUNNEL-01'").fetchall()
    assert all(e[0] != "attributable_ack" for e in evs)


def test_reingest_is_idempotent(tmp_path):
    ts = _ts(tmp_path)
    iss = [_issue(2, "DIRECTOR-WORKLOOP-01 — director does the loop", "director lane")]
    ingest("outcited/outcitedharness", ts, issues=iss)
    out2 = ingest("outcited/outcitedharness", ts, issues=iss)
    assert out2[0].state == "duplicate"
    n = ts.con.execute("SELECT COUNT(*) FROM tasks WHERE task_id='DIRECTOR-WORKLOOP-01'").fetchone()
    assert n[0] == 1
    ev = ts.con.execute("SELECT COUNT(*) FROM task_events WHERE task_id='DIRECTOR-WORKLOOP-01'").fetchone()
    assert ev[0] == 2, "no extra events on re-ingest"


def test_repo_allowlist_is_enforced(tmp_path):
    with pytest.raises(PermissionError):
        ingest("evil/example", _ts(tmp_path), issues=[])


def test_issue_body_is_labelled_untrusted(tmp_path):
    ts = _ts(tmp_path)
    ingest("outcited/outcitedharness", ts,
           issues=[_issue(2, "DIRECTOR-WORKLOOP-01 — director", "director lane")])
    ev = ts.con.execute("SELECT evidence FROM task_events WHERE task_id='DIRECTOR-WORKLOOP-01' LIMIT 1").fetchone()
    d = json.loads(ev[0])
    assert "untrusted work definition" in d["note"]
