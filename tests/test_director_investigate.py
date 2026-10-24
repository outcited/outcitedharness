"""Code-intel investigation loop — dedupe, persistence, citations, grounding.

DIRECTOR-07 §1.B regression matrix (reviews 6277037762 + 6277368358 pt 3),
all on the REAL DirectorState schema (hash-chained journal, no side tables):

- same P1 over two ticks = one model call, one journal receipt
- persistence failure NEVER suppresses a retry (acknowledged dedupe)
- LLM/GCI unavailability is journaled but NOT acknowledged -> retried
- starvation: a deduped top incident does not block a distinct newer one
- typed source identity: unchanged assessment -> same fingerprint across
  ticks; changed evidence -> re-opened
- restart safety: dedupe rebuilt from the hash-chained journal
- receipts are in the journal (kind='investigation') and the chain
  verifies afterwards
"""
import contextlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.consumer import DirectorConsumer, RunReport
from director_consumer.investigate import (
    STATUS_DEDUPE_SKIP, STATUS_INSUFFICIENT_EVIDENCE,
    STATUS_INVESTIGATED, STATUS_LLM_UNAVAILABLE, STATUS_NO_HITS,
    STATUS_SOURCE_UNAVAILABLE, InvestigationResult, _build_citations,
    _build_context, _extract_search_terms, _fingerprint, investigate,
)
from director_consumer.state import DirectorState

P1 = {
    "severity": "P1",
    "rule_id": "watcher-health-stale",
    "trigger_key": "abc123",
    "event_summary": "sentinel last-known-good: newest source 249.2h old",
    "evidence": [{"source_id": "feed:sentinel"}],
}

P1_CHANGED = dict(P1, event_summary="sentinel last-known-good: 300.5h old")

GCI_HITS = [
    {"repo_root": "/Users/samkim/Harnessv1", "revision": "7271e521abc",
     "path": "director_consumer/continuity.py", "start_line": 111,
     "end_line": 130, "text": "def _measure_rust(): ...", "symbol": "_measure_rust"},
    {"repo_root": "/Users/samkim/Harnessv1", "revision": "7271e521abc",
     "path": "director_consumer/adapters.py", "start_line": 232,
     "end_line": 240, "text": "class SentinelAdapter:", "symbol": "SentinelAdapter"},
]


# ---------------------------------------------------------------- helpers

def make_state(tmp_path) -> DirectorState:
    return DirectorState(tmp_path / "ws")


def add_assessment(state: DirectorState, payload: dict) -> None:
    """Append an assessment to the hash-chained journal, as ingest does."""
    with state.con:
        state._append(state.con, "assessment", payload)


def investigations(state: DirectorState) -> list[dict]:
    return [json.loads(r[0]) for r in state.con.execute(
        "SELECT payload FROM journal WHERE kind='investigation' "
        "ORDER BY rowid").fetchall()]


@contextlib.contextmanager
def gci_llmMocks(hypothesis="The SentinelAdapter monitors watcher health."):
    with patch("director_consumer.investigate._gci_search",
               return_value=GCI_HITS) as gci, \
         patch("director_consumer.investigate._llm_call",
               return_value=(hypothesis, {"total_tokens": 150,
                                          "_latency_ms": 5000})) as llm:
        yield gci, llm


def consumer_shell(state: DirectorState, project: str = "designwins"):
    """A DirectorConsumer with only what run_code_intel touches."""
    c = DirectorConsumer.__new__(DirectorConsumer)
    c.state = state
    c.project_name = project
    c.llm = object()  # non-None: the LLM gate must pass
    return c


# ------------------------------------------------- investigate(): dedupe

def test_dedupe_same_p1_two_ticks_one_model_call(tmp_path):
    """The critical acceptance: same unchanged P1 investigated ONCE."""
    state = make_state(tmp_path)
    dedupe = set()
    with gci_llmMocks() as (gci, llm):
        r1 = investigate(P1, "designwins", "rev1", "token",
                         "http://asus2:8900", "qwen", dedupe, state)
        assert r1.status == STATUS_INVESTIGATED
        assert r1.persisted
        assert len(dedupe) == 1

        r2 = investigate(P1, "designwins", "rev1", "token",
                         "http://asus2:8900", "qwen", dedupe, state)
        assert r2.status == STATUS_DEDUPE_SKIP
        assert llm.call_count == 1, "one model call total"

    assert len(investigations(state)) == 1, "exactly one journal receipt"


def test_changed_evidence_eligible_for_new_investigation(tmp_path):
    state = make_state(tmp_path)
    dedupe = set()
    with gci_llmMocks("Updated analysis."):
        r1 = investigate(P1, "designwins", "rev1", "token",
                         "http://asus2:8900", "qwen", dedupe, state)
        assert r1.status == STATUS_INVESTIGATED
        # Source revision changed -> new fingerprint -> new investigation
        r2 = investigate(P1, "designwins", "rev2", "token",
                         "http://asus2:8900", "qwen", dedupe, state)
        assert r2.status == STATUS_INVESTIGATED
        assert r2.fingerprint != r1.fingerprint


# ------------------------------------- investigate(): persistence honesty

def test_no_gci_hits_is_no_hits_not_invented(tmp_path):
    state = make_state(tmp_path)
    with patch("director_consumer.investigate._gci_search",
               return_value=[]):
        r = investigate(P1, "designwins", "rev1", "token",
                        "http://asus2:8900", "qwen", set(), state)
    assert r.status == STATUS_NO_HITS
    assert r.hypothesis == ""  # no model call was made
    assert r.verified is False
    assert r.persisted and len(investigations(state)) == 1


def test_model_failure_is_llm_unavailable_and_retried(tmp_path):
    """LLM down: journaled as a receipt, NOT acknowledged -> retried."""
    state = make_state(tmp_path)
    dedupe = set()
    with patch("director_consumer.investigate._gci_search",
               return_value=GCI_HITS), \
         patch("director_consumer.investigate._llm_call",
               side_effect=Exception("connection refused")):
        r1 = investigate(P1, "designwins", "rev1", "token",
                         "http://asus2:8900", "qwen", dedupe, state)
        assert r1.status == STATUS_LLM_UNAVAILABLE
        assert "connection refused" in r1.hypothesis
        assert r1.persisted  # receipt kept
        assert dedupe == set()  # NOT acknowledged

    with gci_llmMocks() as (gci, llm):
        r2 = investigate(P1, "designwins", "rev1", "token",
                         "http://asus2:8900", "qwen", dedupe, state)
        assert r2.status == STATUS_INVESTIGATED  # the retry happened
        assert llm.call_count == 1
    assert len(dedupe) == 1  # acknowledged only after the real result


def test_persist_failure_never_suppresses_retry(tmp_path):
    """6277037762 bug 3: a failed journal write must not dedupe."""
    state = make_state(tmp_path)
    dedupe = set()

    def boom(payload):
        raise RuntimeError("disk I/O error")

    state.journal_investigation = boom  # simulate a failing journal
    with gci_llmMocks() as (gci, llm):
        r1 = investigate(P1, "designwins", "rev1", "token",
                         "http://asus2:8900", "qwen", dedupe, state)
        assert not r1.persisted
        assert "disk I/O error" in r1.persist_error
        assert dedupe == set()  # lost receipt -> no suppression

    # Journal recovers -> the investigation is redone and acknowledged
    del state.journal_investigation  # back to the bound method
    with gci_llmMocks() as (gci, llm):
        r2 = investigate(P1, "designwins", "rev1", "token",
                         "http://asus2:8900", "qwen", dedupe, state)
        assert r2.status == STATUS_INVESTIGATED
    assert len(investigations(state)) == 1  # only the committed one


def test_no_token_is_source_unavailable(tmp_path):
    state = make_state(tmp_path)
    dedupe = set()
    r = investigate(P1, "designwins", "rev1", "",
                    "http://asus2:8900", "qwen", dedupe, state)
    assert r.status == STATUS_SOURCE_UNAVAILABLE
    assert dedupe == set()  # retryable: not acknowledged


def test_result_is_always_unverified():
    """verified is structurally False — no code path can set it True."""
    r = InvestigationResult()
    assert r.verified is False
    d = r.to_dict()
    assert d["verified"] is False


# ------------------------------------------- investigate(): grounding

def test_citations_include_revision_and_lines():
    cites = _build_citations(GCI_HITS)
    assert cites[0]["revision"] == "7271e521abc"[:12]
    assert cites[0]["path"] == "director_consumer/continuity.py"
    assert cites[0]["start_line"] == 111


def test_context_is_bounded():
    many = [{"repo_root": "x", "revision": "r" * 40, "path": "p",
             "start_line": 1, "text": "A" * 2000} for _ in range(20)]
    ctx = _build_context(many, max_chars=5000)
    assert len(ctx) <= 5100


def test_search_terms_from_rule_not_random_words():
    terms = _extract_search_terms(P1)
    assert "watcher_health_stale" in terms
    assert any("sentinel" in t for t in terms)


def test_model_saying_insufficient_is_insufficient(tmp_path):
    state = make_state(tmp_path)
    dedupe = set()
    with patch("director_consumer.investigate._gci_search",
               return_value=GCI_HITS), \
         patch("director_consumer.investigate._llm_call",
               return_value=("Insufficient evidence to determine...",
                             {"total_tokens": 50})):
        r = investigate(P1, "designwins", "rev1", "token",
                        "http://asus2:8900", "qwen", dedupe, state)
    assert r.status == STATUS_INSUFFICIENT_EVIDENCE
    assert r.fingerprint in dedupe  # terminal: acknowledged


# ------------------------------------------- journal integration

def test_investigation_persisted_to_hash_chained_journal(tmp_path):
    state = make_state(tmp_path)
    with gci_llmMocks("Analysis of the sentinel watcher."):
        investigate(P1, "designwins", "rev1", "token",
                    "http://asus2:8900", "qwen", set(), state)
    entries = investigations(state)
    assert len(entries) == 1
    d = entries[0]
    assert d["status"] == STATUS_INVESTIGATED
    assert d["verified"] is False
    assert len(d["citations"]) > 0
    ok, bad_row = state.verify_chain()
    assert ok, f"chain must verify after investigation inserts (bad={bad_row})"
    # no ad-hoc side table was ever created
    tables = {r[0] for r in state.con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "investigations" not in tables


def test_dedupe_persists_across_process_restarts(tmp_path):
    """Restart: a fresh DirectorState on the same workspace rebuilds
    dedupe from the hash-chained journal — same P1 stays deduped."""
    state = make_state(tmp_path)
    with gci_llmMocks("First analysis."):
        investigate(P1, "designwins", "rev1", "token",
                    "http://asus2:8900", "qwen", set(), state)
    # Simulate restart: same on-disk workspace, new objects
    state2 = DirectorState(tmp_path / "ws")
    dedupe2 = {d["fingerprint"] for d in investigations(state2)}
    assert len(dedupe2) == 1
    with patch("director_consumer.investigate._llm_call") as mock_call:
        r2 = investigate(P1, "designwins", "rev1", "token",
                         "http://asus2:8900", "qwen", dedupe2, state2)
        assert r2.status == STATUS_DEDUPE_SKIP
        mock_call.assert_not_called()


# ------------------------------------------- run_code_intel(): consumer

def test_run_code_intel_source_identity_stable_across_ticks(
        tmp_path, monkeypatch):
    """6277037762 bug 2 fixed: source identity no longer scrapes the
    (never-matching) kind='journal_run' row — it is the assessment's
    content hash, stable while evidence is unchanged."""
    monkeypatch.setenv("HARNESS_GCI_TOKEN", "token")
    state = make_state(tmp_path)
    add_assessment(state, P1)
    c = consumer_shell(state)
    with gci_llmMocks() as (gci, llm):
        c.run_code_intel(RunReport())
        assert llm.call_count == 1
        c2 = consumer_shell(state)  # next tick, same instance memory gone
        c2.run_code_intel(RunReport())
        assert llm.call_count == 1, "unchanged evidence = no new model call"


def test_run_code_intel_starvation_fix(tmp_path, monkeypatch):
    """6277037762 bug 4 fixed: a deduped incident at the head of the
    recent list must not consume the cycle's budget — the distinct
    incident behind it still gets investigated."""
    monkeypatch.setenv("HARNESS_GCI_TOKEN", "token")
    state = make_state(tmp_path)
    fresh = dict(P1, trigger_key="incident-B", rule_id="lane-stalled")
    add_assessment(state, fresh)          # older rowid
    add_assessment(state, P1)             # newest = head of the list
    c = consumer_shell(state)

    # Head (P1) already investigated + acknowledged in a prior cycle
    with gci_llmMocks():
        c.run_code_intel(RunReport())
    assert len(investigations(state)) == 1
    assert investigations(state)[0]["trigger_key"] == P1["trigger_key"]

    # New cycle: head is deduped; the distinct incident-B must run NOW
    with gci_llmMocks() as (gci, llm):
        c2 = consumer_shell(state)
        c2._intel_dedupe = {d["fingerprint"]
                            for d in investigations(state)}
        c2.run_code_intel(RunReport())
        assert llm.call_count == 1, "budget spent on incident-B, not skip"
    done = investigations(state)
    assert len(done) == 2
    assert done[1]["trigger_key"] == "incident-B"


def test_run_code_intel_restart_safe_on_real_schema(tmp_path, monkeypatch):
    """Full restart (new state + new consumer) does not re-investigate."""
    monkeypatch.setenv("HARNESS_GCI_TOKEN", "token")
    state = make_state(tmp_path)
    add_assessment(state, P1)
    c = consumer_shell(state)
    with gci_llmMocks():
        c.run_code_intel(RunReport())
    # Restart: reopen the same workspace, fresh consumer + dedupe
    state2 = DirectorState(tmp_path / "ws")
    c2 = consumer_shell(state2)
    with gci_llmMocks() as (gci, llm):
        c2.run_code_intel(RunReport())
        llm.assert_not_called()


def test_run_code_intel_noop_without_token(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_GCI_TOKEN", raising=False)
    state = make_state(tmp_path)
    add_assessment(state, P1)
    consumer_shell(state).run_code_intel(RunReport())  # must not raise
    assert investigations(state) == []
