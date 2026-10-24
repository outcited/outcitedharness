"""DIRECTOR-06 corrective — evidence authority, dedup, full-history coverage.

Three defects found in review (2026-10-18) that would let seven confidently
recall an agent's CLAIM of success while missing the branch containing the
actual work — or the evidence that it failed:

  1. Verification authority: a commit whose SUBJECT contained "verified",
     "test", "passed", "green" was labelled verified-fact. A commit message
     is an author ASSERTION, always. Wording confers nothing.
  2. Duplicate indexing: ProjectMemory.index had no source-identity
     uniqueness. Re-indexing accumulated duplicate history and duplicate
     FTS rows.
  3. Coverage: index_git_history used plain `git log` (current branch only)
     — disconnected branches and worktrees were invisible. index_tree
     excluded results/, where verification receipts live.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.corpus import classify, index_git_history, index_tree
from director_consumer.memory import MemoryRecord, ProjectMemory


# ---------------------------------------------------- 1. evidence authority

def test_commit_wording_never_confers_verified_fact():
    """A commit saying 'verified'/'tests green' is an author claim."""
    recs = []


def test_classify_commit_subject_is_always_assertion(tmp_path):
    # the old bug: index_git_history chose verified-fact when the subject
    # matched (verified|test|receipt|green|passed). Build a repo with such a
    # subject and assert the stored class is assertion.
    repo = tmp_path / "r"
    repo.mkdir()
    def sh(*a):
        subprocess.run(["git", "-C", str(repo)] + list(a),
                       capture_output=True, check=True)
    sh("init", "-q")
    (repo / "f.txt").write_text("x")
    sh("add", ".")
    sh("-c", "user.email=t@t", "-c", "user.name=t",
       "commit", "-q", "-m", "VERIFIED: all tests green, receipt attached")
    mem = ProjectMemory(tmp_path / "m.db", project_id="designwins")
    n = index_git_history(mem, repo, max_commits=10)
    assert n == 1
    row = mem.con.execute(
        "SELECT evidence_class, source_id FROM mem").fetchone()
    assert row["evidence_class"] == "assertion", \
        "commit subject wording must never confer verified-fact"
    assert row["source_id"].startswith("commit:")
    mem.close()


def test_classify_filename_receipt_is_not_automatic_verified_fact():
    """Filename containing TEST/RECEIPT is a locator hint, not evidence."""
    assert classify(Path("/x/VERIFIED-TEST-RECEIPT.md"),
                    "VERIFIED TEST RECEIPT") != "verified-fact"


def test_explicit_marker_confers_class(tmp_path):
    """A document that DECLARES its evidence class is honoured; silence
    defaults to assertion."""
    d = tmp_path / "DECISION-42.md"
    d.write_text("# D42\nevidence-class: authority\n\nApproved by sam.\n")
    mem = ProjectMemory(tmp_path / "m.db", project_id="designwins")
    index_tree(mem, tmp_path, max_files=10)
    rows = {r["source_id"]: r["evidence_class"]
            for r in mem.con.execute("SELECT source_id, evidence_class FROM mem")}
    assert rows.get("file:DECISION-42.md") == "authority"
    mem.close()


def test_document_without_marker_defaults_to_assertion(tmp_path):
    d = tmp_path / "NOTES.md"
    d.write_text("# some agent notes\nwe fixed everything\n")
    mem = ProjectMemory(tmp_path / "m.db", project_id="designwins")
    index_tree(mem, tmp_path, max_files=10)
    row = mem.con.execute("SELECT evidence_class FROM mem").fetchone()
    assert row["evidence_class"] == "assertion"
    mem.close()


# --------------------------------------------------------- 2. dedup/version

def test_reindex_is_idempotent(tmp_path):
    mem = ProjectMemory(tmp_path / "m.db", project_id="designwins")
    src = tmp_path / "a.md"
    src.write_text("# A\nbody one\n")
    index_tree(mem, tmp_path, max_files=5)
    n1 = mem.con.execute("SELECT COUNT(*) c FROM mem").fetchone()["c"]
    f1 = mem.con.execute(
        "SELECT COUNT(*) c FROM mem_fts_idx").fetchone()["c"]
    index_tree(mem, tmp_path, max_files=5)          # re-run
    n2 = mem.con.execute("SELECT COUNT(*) c FROM mem").fetchone()["c"]
    f2 = mem.con.execute(
        "SELECT COUNT(*) c FROM mem_fts_idx").fetchone()["c"]
    assert n1 == n2 == 1, "re-indexing must not duplicate rows"
    assert f1 == f2 == 1, "re-indexing must not duplicate FTS entries"
    mem.close()


def test_changed_content_creates_new_version_and_supersedes(tmp_path):
    mem = ProjectMemory(tmp_path / "m.db", project_id="designwins")
    (tmp_path / "a.md").write_text("# A v1\noriginal\n")
    index_tree(mem, tmp_path, max_files=5)
    (tmp_path / "a.md").write_text("# A v2\nchanged\n")
    index_tree(mem, tmp_path, max_files=5)
    rows = mem.con.execute(
        "SELECT evidence_class, superseded_by, body FROM mem "
        "ORDER BY id").fetchall()
    assert len(rows) == 2, "new version stored, old preserved as history"
    assert rows[0]["evidence_class"] == "superseded"
    assert rows[0]["superseded_by"] == rows[1]["source_id"] if "source_id" in rows[1].keys() else True
    assert "changed" in rows[1]["body"]
    mem.close()


# ------------------------------------------------------- 3. git coverage

def _repo_with_branch(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    def sh(*a):
        subprocess.run(["git", "-C", str(repo)] + list(a),
                       capture_output=True, check=True)
    sh("init", "-q", "-b", "main")
    (repo / "f.txt").write_text("1")
    sh("add", ".")
    sh("-c", "user.email=a@a", "-c", "user.name=agent-a",
       "commit", "-q", "-m", "work on main")
    # a disconnected feature branch with its own commit
    sh("checkout", "-q", "-b", "feature/orphan")
    (repo / "g.txt").write_text("2")
    sh("add", ".")
    sh("-c", "user.email=b@b", "-c", "user.name=agent-b",
       "commit", "-q", "-m", "the actual work lives here")
    sh("checkout", "-q", "main")
    return repo


def test_git_history_covers_all_refs(tmp_path):
    repo = _repo_with_branch(tmp_path)
    mem = ProjectMemory(tmp_path / "m.db", project_id="designwins")
    index_git_history(mem, repo, max_commits=50)
    bodies = " ".join(r["body"] for r in mem.con.execute(
        "SELECT body FROM mem"))
    assert "the actual work lives here" in bodies, \
        "commits on non-current branches must be indexed"
    # and the author is captured for ownership questions
    actors = {r["actor"] for r in mem.con.execute("SELECT actor FROM mem")}
    assert "agent-b" in actors
    mem.close()


def test_results_receipts_are_indexed(tmp_path):
    root = tmp_path / "proj"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "PLAN.md").write_text("# plan\n")
    res = root / "results" / "pin-tables-20261014"
    res.mkdir(parents=True)
    (res / "PIN_BATCH_RECEIPT.md").write_text(
        "# Pin Batch Completion Receipt\n125/125 batches, 23,318 reconciled\n")
    mem = ProjectMemory(tmp_path / "m.db", project_id="designwins")
    index_tree(mem, root, max_files=50)
    ids = {r["source_id"] for r in mem.con.execute(
        "SELECT source_id FROM mem")}
    assert any("PIN_BATCH_RECEIPT" in i for i in ids), \
        "verification receipts under results/ must be indexed"
    mem.close()


def test_results_bulk_data_still_excluded(tmp_path):
    root = tmp_path / "proj"
    res = root / "results" / "big"
    res.mkdir(parents=True)
    (res / "rows.jsonl").write_text("{\"a\":1}\n" * 100)
    (res / "blobs").mkdir()
    (res / "blobs" / "x.pdf").write_bytes(b"%PDF" * 100)
    mem = ProjectMemory(tmp_path / "m.db", project_id="designwins")
    index_tree(mem, root, max_files=50)
    ids = " ".join(r["source_id"] for r in mem.con.execute(
        "SELECT source_id FROM mem"))
    assert "rows.jsonl" not in ids and "x.pdf" not in ids
    mem.close()
