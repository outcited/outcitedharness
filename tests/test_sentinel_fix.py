"""Behavioral regression tests for scripts/sentinel.py (SENTINEL-FIX-01).

Root cause (confirmed with host evidence 2026-10-19, see issue #12):

1. HOURLY: sentinel.py connected to the RETIRED
   /Volumes/M5_4TB/extract-results/pipeline.db (10.3 GB, last write Oct 7).
   The active DB is pipeline-live.db under ~/Harnessv1/results/.
2. NIGHTLY: last-known-good.json was written to the external-volume
   sentinel dir owned by _lpoperator:1026 — PermissionError for samkim.

The live fix rewrote the script around the active DB + a local writable
sentinel dir. These tests pin the FIXED behavior hermetically: the
HARNESS_SENTINEL_RESULTS env var points the script at an isolated fixture
directory, so nothing here touches host state.

Pinned semantics (current live behavior, deliberately asserted):
- hourly with recent claims: ALERTS.md rewritten empty, exit 0
- hourly with no recent claims: ALERTS.md names the condition, exit 0
- hourly with a missing/unreadable DB: nonzero exit (fail-closed by crash)
- canon non-determinism (ledger): ALERTS.md flags it, exit 0
- nightly: LKG written as {"checked": 0, "drifted": 0}, exit 0
- ALERTS.md is OVERWRITTEN, not appended; a later writer (canon check)
  clobbers an earlier one — pinned here as a known quirk for follow-up.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SENTINEL_SCRIPT = REPO / "scripts" / "sentinel.py"


# ---------------------------------------------------------------- helpers

def make_results_db(path: Path, *, recent: int = 0, stale: int = 0,
                    ledger: tuple[tuple[int, str, str], ...] = ()):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE results (
            id INTEGER PRIMARY KEY, created_at TEXT NOT NULL);
        CREATE TABLE adjudication_ledger (
            result_id INTEGER NOT NULL, judge TEXT NOT NULL,
            verdict TEXT NOT NULL);
    """)
    for _ in range(recent):
        con.execute("INSERT INTO results (created_at) VALUES (datetime('now'))")
    for _ in range(stale):
        con.execute("INSERT INTO results (created_at)"
                    " VALUES (datetime('now', '-3 hours'))")
    for rid, judge, verdict in ledger:
        con.execute("INSERT INTO adjudication_ledger VALUES (?,?,?)",
                    (rid, judge, verdict))
    con.commit()
    con.close()


def run_sentinel(root: Path, mode: str) -> subprocess.CompletedProcess:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(root),
        "HARNESS_SENTINEL_RESULTS": str(root / "results"),
    }
    return subprocess.run(
        [sys.executable, str(SENTINEL_SCRIPT), mode],
        env=env, capture_output=True, text=True, timeout=120)


def alerts_text(root: Path) -> str:
    p = root / "results" / "sentinel" / "ALERTS.md"
    return p.read_text() if p.exists() else ""


# ---------------------------------------------------------------- hourly

def test_hourly_recent_claims_pass_clean(tmp_path):
    make_results_db(tmp_path / "results" / "pipeline-live.db", recent=3)
    r = run_sentinel(tmp_path, "hourly")
    assert r.returncode == 0, r.stderr
    assert alerts_text(tmp_path) == ""


def test_hourly_no_recent_claims_writes_alert(tmp_path):
    make_results_db(tmp_path / "results" / "pipeline-live.db",
                    stale=5)  # rows exist, none within 2h
    r = run_sentinel(tmp_path, "hourly")
    assert r.returncode == 0, r.stderr
    assert "no claims in the last 2 hours" in alerts_text(tmp_path)


def test_hourly_missing_db_fails_closed(tmp_path):
    (tmp_path / "results").mkdir(parents=True)  # no pipeline-live.db
    r = run_sentinel(tmp_path, "hourly")
    assert r.returncode != 0  # crash on missing schema = fail-closed


def test_hourly_canon_nondeterminism_flagged(tmp_path):
    make_results_db(
        tmp_path / "results" / "pipeline-live.db",
        recent=1,
        ledger=[(1, "canon", "pass"), (1, "canon", "fail"),
                (2, "canon", "pass")])
    r = run_sentinel(tmp_path, "hourly")
    assert r.returncode == 0, r.stderr
    assert "canon non-deterministic for 1 results" in alerts_text(tmp_path)


def test_hourly_canon_deterministic_clean(tmp_path):
    make_results_db(
        tmp_path / "results" / "pipeline-live.db",
        recent=1,
        ledger=[(1, "canon", "pass"), (2, "canon", "pass")])
    r = run_sentinel(tmp_path, "hourly")
    assert r.returncode == 0, r.stderr
    assert "non-deterministic" not in alerts_text(tmp_path)


def test_hourly_alerts_overwrite_quirk(tmp_path):
    """Both conditions fire: canon writer clobbers the hourly alert line.

    Pins CURRENT behavior (ALERTS.md is rewritten, not appended) so any
    change to append-semantics must consciously update this test.
    """
    make_results_db(
        tmp_path / "results" / "pipeline-live.db",
        recent=0, stale=2,
        ledger=[(7, "canon", "pass"), (7, "canon", "fail")])
    r = run_sentinel(tmp_path, "hourly")
    assert r.returncode == 0, r.stderr
    text = alerts_text(tmp_path)
    assert "non-deterministic" in text
    assert "no claims" not in text  # overwritten


# ---------------------------------------------------------------- nightly

def test_nightly_writes_lkg(tmp_path):
    r = run_sentinel(tmp_path, "nightly")
    assert r.returncode == 0, r.stderr
    lkg = tmp_path / "results" / "sentinel" / "last-known-good.json"
    assert json.loads(lkg.read_text()) == {"checked": 0, "drifted": 0}


# ---------------------------------------------------------------- defaults

def test_production_defaults_unchanged():
    """The env seam must not move the production paths."""
    src = SENTINEL_SCRIPT.read_text()
    assert '"/Users/samkim/Harnessv1/results"' in src
    assert "pipeline-live.db" in src
    # the functional RESULTS assignment must never point at the
    # retired external volume (it may appear in comments/legacy only)
    results_line = next(l for l in src.splitlines() if l.startswith("RESULTS"))
    assert "/Volumes/" not in results_line
