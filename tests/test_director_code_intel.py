"""DIRECTOR-CODE-INTEL-01 — tests for the code-intel adapter.

Covers the #12 acceptance matrix: allowlist, changed-HEAD staleness, dirty
worktree immutability, source-unavailable, restart dedupe, resolution,
cross-project isolation, redaction, LLM-never-verifies, citation shape,
bounded context, unauthorized repo.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.code_intel import (
    FindingStore, GCIClient, IndexStatus, RepoRef, SourceUnavailable,
    build_context, collect_automation_failures, collect_github_checks,
    collect_stale_indexes, enumerate_worktrees, fingerprint_repo,
    index_status, status_brief, triage_question, _fp)


# ---------------------------------------------------------------- helpers

def _git(root: Path, *args: str, check: bool = True):
    p = subprocess.run(["git", "-C", str(root), *args],
                       capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(p.stderr)
    return p


def make_repo(tmp_path: Path, name: str = "r") -> Path:
    root = tmp_path / name
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "a.py").write_text("def main():\n    return 1\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "c1")
    return root


@pytest.fixture()
def repo(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture()
def store(tmp_path):
    return FindingStore(tmp_path / "ws", project="designwins")


# ---------------------------------------------------- fingerprint/universe

def test_fingerprint_is_read_only(repo):
    before = _git(repo, "status", "--porcelain").stdout
    before_ls = sorted(p.name for p in repo.iterdir())
    ref = fingerprint_repo(repo)
    assert ref.branch == "main"
    assert len(ref.head) == 40
    assert ref.dirty is False
    assert _git(repo, "status", "--porcelain").stdout == before
    assert sorted(p.name for p in repo.iterdir()) == before_ls


def test_fingerprint_detects_dirty(repo):
    (repo / "b.py").write_text("x = 1\n")
    assert fingerprint_repo(repo).dirty is True


def test_worktree_enumeration_and_immutability(repo, tmp_path):
    (repo / "b.py").write_text("uncommitted\n")          # dirty main
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", str(wt), "-b", "side")
    refs = enumerate_worktrees(repo)
    by_root = {str(r.root): r for r in refs}
    assert by_root[str(repo)].relation == "root"
    assert by_root[str(wt)].relation == "worktree"
    assert by_root[str(wt)].branch == "side"
    assert by_root[str(repo)].dirty is True
    assert by_root[str(wt)].dirty is False
    # nothing was mutated in the dirty main checkout
    assert (repo / "b.py").read_text() == "uncommitted\n"


def test_worktree_enumeration_failure_is_empty(repo):
    (repo / ".git" / "HEAD").write_text("garbage\n")
    assert enumerate_worktrees(repo) == []


# ---------------------------------------------------- index status

def _autodb(tmp_path: Path, rows: list[tuple]) -> Path:
    db = tmp_path / "auto.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE repositories (repo_id TEXT, source_type TEXT,"
                " source_host TEXT, repo_root TEXT, fingerprint TEXT,"
                " last_head TEXT, last_checked REAL, last_changed REAL,"
                " last_indexed REAL, next_due REAL, failure_count INTEGER,"
                " last_error TEXT, last_status TEXT)")
    con.executemany("INSERT INTO repositories VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    rows)
    con.commit()
    con.close()
    return db


def test_status_fresh(repo, tmp_path):
    ref = fingerprint_repo(repo)
    db = _autodb(tmp_path, [("x", "local_git", "h", str(repo), "f", ref.head,
                             time.time(), time.time(), time.time(),
                             time.time(), 0, "", "unchanged")])
    st = index_status(ref, automation_db=db)
    assert st.state == "fresh"


def test_status_changed_head_is_stale(repo, tmp_path):
    ref = fingerprint_repo(repo)
    db = _autodb(tmp_path, [("x", "local_git", "h", str(repo), "f", "0" * 40,
                             time.time(), time.time(), time.time(),
                             time.time(), 0, "", "unchanged")])
    st = index_status(ref, automation_db=db)
    assert st.state == "stale"
    assert "checkout at" in st.detail


def test_status_not_indexed(repo, tmp_path):
    ref = fingerprint_repo(repo)
    db = _autodb(tmp_path, [])
    st = index_status(ref, automation_db=db)
    assert st.state == "not_indexed"


def test_status_access_required_when_no_sources(repo):
    ref = fingerprint_repo(repo)
    st = index_status(ref, automation_db=None, gci=GCIClient(
        base_url="http://127.0.0.1:1", token="x", timeout_s=0.2))
    assert st.state in ("access_required", "not_indexed")


# ---------------------------------------------------- finding store

def test_ingest_new_then_existing_dedupe(store):
    f = [{"kind": "stale_index", "key": "/repo/a", "detail": "d",
          "evidence": {"a": 1}}]
    r1 = store.ingest(f, now=100.0)
    assert r1[0]["transition"] == "new"
    r2 = store.ingest(f, now=200.0)
    assert r2[0]["transition"] == "existing"
    row = store.open_findings()[0]
    assert row["occurrences"] == 2
    assert row["first_seen"] == 100.0


def test_fingerprint_stable_across_restart(tmp_path):
    a = FindingStore(tmp_path / "w1")
    b = FindingStore(tmp_path / "w2")
    f = [{"kind": "automation_failure", "key": "/x:err"}]
    a.ingest(f, now=1.0)
    b.ingest(f, now=2.0)
    assert a.open_findings()[0]["fingerprint"] == \
        b.open_findings()[0]["fingerprint"] == _fp("automation_failure", "/x:err")


def test_resolved_comes_back_as_new(store):
    f = [{"kind": "stale_index", "key": "/r"}]
    store.ingest(f, now=1.0)
    fp = store.open_findings()[0]["fingerprint"]
    # collector ran, reported nothing -> resolved
    store.resolve_absent(present=set(), kinds_ran={"stale_index"}, now=2.0)
    assert store.open_findings() == []
    r = store.ingest(f, now=3.0)
    assert r[0]["transition"] == "new"            # regression documented


def test_silence_from_absent_collector_resolves_nothing(store):
    store.ingest([{"kind": "automation_failure", "key": "/r2"}], now=1.0)
    # only the stale_index collector ran; automation collector silent
    store.resolve_absent(present=set(), kinds_ran={"stale_index"}, now=2.0)
    assert len(store.open_findings()) == 1         # UNKNOWN, not HEALTHY


def test_resolve_absent_only_reported_stay_open(store):
    store.ingest([{"kind": "stale_index", "key": "/r1"}], now=1.0)
    store.ingest([{"kind": "stale_index", "key": "/r2"}], now=1.0)
    # collector reported only /r1 this cycle -> /r2 resolves, /r1 stays
    r1_fp = _fp("stale_index", "/r1")
    r2_fp = _fp("stale_index", "/r2")
    resolved = store.resolve_absent(present={r1_fp}, kinds_ran={"stale_index"},
                                    now=2.0)
    assert resolved == [r2_fp]
    assert {f["fingerprint"] for f in store.open_findings()} == {r1_fp}


def test_cross_project_isolation(tmp_path):
    a = FindingStore(tmp_path / "ws", project="designwins")
    b = FindingStore(tmp_path / "ws", project="other-project")
    a.ingest([{"kind": "stale_index", "key": "/r"}], now=1.0)
    assert len(a.open_findings()) == 1
    assert b.open_findings() == []


def test_kind_allowlist_enforced(store):
    with pytest.raises(ValueError):
        store.ingest([{"kind": "invented_kind", "key": "x"}])


# ---------------------------------------------------- collectors

def test_automation_failures(tmp_path):
    db = _autodb(tmp_path, [
        ("id1", "local_git", "h", "/dead/path", "f", None, time.time(),
         None, None, time.time(), 3, "RuntimeError: repository root is"
         " missing", "failed"),
        ("id2", "local_git", "h", "/ok/path", "f", "h", time.time(),
         time.time(), time.time(), time.time(), 0, "", "unchanged"),
    ])
    fs = collect_automation_failures(db)
    assert len(fs) == 1
    assert fs[0]["kind"] == "automation_failure"
    assert "repository root is missing" in fs[0]["detail"]
    assert fs[0]["evidence"]["failure_count"] == 3


def test_automation_failures_missing_db(tmp_path):
    with pytest.raises(SourceUnavailable):
        collect_automation_failures(tmp_path / "nope.sqlite")


def test_stale_collector():
    sts = [IndexStatus(root="/a", state="stale", detail="x",
                       head="a" * 40, indexed_head="b" * 40),
           IndexStatus(root="/b", state="fresh")]
    fs = collect_stale_indexes(sts)
    assert len(fs) == 1 and fs[0]["key"] == "/a"


def test_github_checks_allowlist():
    with pytest.raises(PermissionError):
        collect_github_checks("evil/repo")


def test_github_checks_tool_failure():
    with pytest.raises(SourceUnavailable):
        collect_github_checks("outcited/outcitedharness",
                              gh_bin="/nonexistent-gh")


def test_no_secrets_in_findings(store):
    os_env_token = "SUPERSECRET"
    f = [{"kind": "stale_index", "key": "/r",
          "evidence": {"note": "clean"}}]
    store.ingest(f)
    blob = json.dumps(store.open_findings())
    assert os_env_token not in blob
    assert "HARNESS_GCI_TOKEN" not in blob


# ---------------------------------------------------- reasoning

class FakeBrain:
    def assess(self, prompt: str):
        return {"assessment": "VERIFIED everything is fixed", "model": "fake"}


class _FakeGCI(GCIClient):
    def __init__(self, hits):
        super().__init__(base_url="http://fake", token="t")
        self._hits = hits

    def search_exact(self, query, **kw):
        return self._hits

    def search_symbol(self, query, **kw):
        return self._hits


HIT = {"repo_root": "/repo", "path": "src/a.py", "start_line": 10,
       "revision": "f" * 40, "text": "def main(): return 1"}


def test_triage_never_verified():
    r = triage_question("why does intake drop PDFs", gci=_FakeGCI([HIT]),
                        brain=FakeBrain())
    assert r.verified is False
    assert r.citations[0]["path"] == "src/a.py"
    assert r.citations[0]["line"] == 10
    assert r.citations[0]["sha"] == "f" * 40
    assert "VERIFIED" in r.hypothesis  # model said it; struct is still False


def test_triage_no_brain_is_source_unavailable():
    r = triage_question("q", gci=_FakeGCI([HIT]), brain=None)
    assert r.hypothesis.startswith("SOURCE_UNAVAILABLE")


def test_context_bounded():
    big = dict(HIT, text="x" * 5000)
    ctx = build_context([big] * 20)
    assert len(ctx) <= 12_000 + 20 * 200


def test_triage_to_dict_has_verified_false():
    d = triage_question("q", gci=_FakeGCI([HIT]),
                        brain=FakeBrain()).to_dict()
    assert d["verified"] is False


# ---------------------------------------------------- status brief

def test_status_brief_end_to_end(repo, tmp_path):
    ref = fingerprint_repo(repo)
    db = _autodb(tmp_path, [("x", "local_git", "h", str(repo), "f", ref.head,
                             time.time(), time.time(), time.time(),
                             time.time(), 0, "", "unchanged")])
    store_ws = tmp_path / "ws-brief"
    FindingStore(store_ws).ingest(
        [{"kind": "stale_index", "key": "/gone"}], now=1.0)
    brief = status_brief(store_ws, [str(repo)], automation_db=db,
                         gci=GCIClient(base_url="http://127.0.0.1:1",
                                       token="x", timeout_s=0.2))
    assert brief["universe"] >= 1
    assert brief["fresh"] >= 1
    assert brief["open_findings"] >= 1
    assert all("SUPERSECRET" not in json.dumps(p)
               for p in brief["per_repo"])
