"""Code-intel investigation loop — dedupe, persistence, citations, grounding.
The critical acceptance: same P1 over two ticks = one model call with a
retained receipt; no retrieval = INSUFFICIENT_EVIDENCE, never invented."""
import json
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.investigate import (
    STATUS_DEDUPE_SKIP, STATUS_INSUFFICIENT_EVIDENCE,
    STATUS_INVESTIGATED, STATUS_LLM_UNAVAILABLE, STATUS_NO_HITS,
    STATUS_SOURCE_UNAVAILABLE, InvestigationResult, _build_citations,
    _build_context, _extract_search_terms, _fingerprint, investigate,
)

P1 = {
    "severity": "P1",
    "rule_id": "watcher-health-stale",
    "trigger_key": "abc123",
    "event_summary": "sentinel last-known-good: newest source 249.2h old",
    "evidence": [{"source_id": "feed:sentinel"}],
}

GCI_HITS = [
    {"repo_root": "/Users/samkim/Harnessv1", "revision": "7271e521abc",
     "path": "director_consumer/continuity.py", "start_line": 111,
     "end_line": 130, "text": "def _measure_rust(): ...", "symbol": "_measure_rust"},
    {"repo_root": "/Users/samkim/Harnessv1", "revision": "7271e521abc",
     "path": "director_consumer/adapters.py", "start_line": 232,
     "end_line": 240, "text": "class SentinelAdapter:", "symbol": "SentinelAdapter"},
]


def _db(tmp):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE journal(ts REAL, kind TEXT, payload TEXT)")
    con.commit()
    return con


def test_dedupe_same_p1_two_ticks_one_model_call(tmp_path):
    """The critical acceptance: same unchanged P1 investigated ONCE."""
    con = _db(tmp_path)
    dedupe = set()
    with patch("director_consumer.investigate._gci_search", return_value=GCI_HITS), \
         patch("director_consumer.investigate._llm_call",
               return_value=("The SentinelAdapter monitors watcher health.",
                             {"total_tokens": 150, "_latency_ms": 5000})):
        r1 = investigate(P1, "designwins", "rev1", "token",
                         "http://asus2:8900", "qwen", dedupe, con)
        assert r1.status == STATUS_INVESTIGATED
        assert len(dedupe) == 1

        # Second tick: same P1, same source revision
        r2 = investigate(P1, "designwins", "rev1", "token",
                         "http://asus2:8900", "qwen", dedupe, con)
        assert r2.status == STATUS_DEDUPE_SKIP
        # Only ONE investigation was persisted
        rows = con.execute(
            "SELECT COUNT(*) FROM journal WHERE kind='code_intel_investigation'"
        ).fetchone()[0]
        assert rows == 1, "exactly one model call, one receipt"


def test_changed_evidence_eligible_for_new_investigation(tmp_path):
    con = _db(tmp_path)
    dedupe = set()
    with patch("director_consumer.investigate._gci_search", return_value=GCI_HITS), \
         patch("director_consumer.investigate._llm_call",
               return_value=("Updated analysis.", {"total_tokens": 100})):
        r1 = investigate(P1, "designwins", "rev1", "token",
                         "http://asus2:8900", "qwen", dedupe, con)
        assert r1.status == STATUS_INVESTIGATED
        # Source revision changed -> new fingerprint -> new investigation
        r2 = investigate(P1, "designwins", "rev2", "token",
                         "http://asus2:8900", "qwen", dedupe, con)
        assert r2.status == STATUS_INVESTIGATED
        assert r2.fingerprint != r1.fingerprint


def test_no_gci_hits_is_no_hits_not_invented(tmp_path):
    con = _db(tmp_path)
    with patch("director_consumer.investigate._gci_search", return_value=[]):
        r = investigate(P1, "designwins", "rev1", "token",
                        "http://asus2:8900", "qwen", set(), con)
    assert r.status == STATUS_NO_HITS
    assert r.hypothesis == ""  # no model call was made
    assert r.verified is False


def test_model_failure_is_llm_unavailable_not_silent(tmp_path):
    con = _db(tmp_path)
    with patch("director_consumer.investigate._gci_search", return_value=GCI_HITS), \
         patch("director_consumer.investigate._llm_call",
               side_effect=Exception("connection refused")):
        r = investigate(P1, "designwins", "rev1", "token",
                        "http://asus2:8900", "qwen", set(), con)
    assert r.status == STATUS_LLM_UNAVAILABLE
    assert "connection refused" in r.hypothesis
    assert r.verified is False


def test_no_token_is_source_unavailable(tmp_path):
    con = _db(tmp_path)
    r = investigate(P1, "designwins", "rev1", "",
                    "http://asus2:8900", "qwen", set(), con)
    assert r.status == STATUS_SOURCE_UNAVAILABLE


def test_result_is_always_unverified():
    """verified is structurally False — no code path can set it True."""
    r = InvestigationResult()
    assert r.verified is False
    d = r.to_dict()
    assert d["verified"] is False


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


def test_investigation_persisted_to_journal(tmp_path):
    con = _db(tmp_path)
    with patch("director_consumer.investigate._gci_search", return_value=GCI_HITS), \
         patch("director_consumer.investigate._llm_call",
               return_value=("Analysis of the sentinel watcher.",
                             {"total_tokens": 120})):
        investigate(P1, "designwins", "rev1", "token",
                    "http://asus2:8900", "qwen", set(), con)
    rows = con.execute(
        "SELECT payload FROM journal WHERE kind='code_intel_investigation'"
    ).fetchall()
    assert len(rows) == 1
    d = json.loads(rows[0][0])
    assert d["status"] == STATUS_INVESTIGATED
    assert d["verified"] is False
    assert len(d["citations"]) > 0


def test_search_terms_from_rule_not_random_words():
    terms = _extract_search_terms(P1)
    assert "watcher_health_stale" in terms
    assert any("sentinel" in t for t in terms)


def test_model_saying_insufficient_is_insufficient(tmp_path):
    con = _db(tmp_path)
    with patch("director_consumer.investigate._gci_search", return_value=GCI_HITS), \
         patch("director_consumer.investigate._llm_call",
               return_value=("Insufficient evidence to determine...",
                             {"total_tokens": 50})):
        r = investigate(P1, "designwins", "rev1", "token",
                        "http://asus2:8900", "qwen", set(), con)
    assert r.status == STATUS_INSUFFICIENT_EVIDENCE


def test_dedupe_persists_across_process_restarts(tmp_path):
    """Simulate a restart: the dedupe set is rebuilt from the journal."""
    con = _db(tmp_path)
    with patch("director_consumer.investigate._gci_search", return_value=GCI_HITS), \
         patch("director_consumer.investigate._llm_call",
               return_value=("First analysis.", {"total_tokens": 100})):
        investigate(P1, "designwins", "rev1", "token",
                    "http://asus2:8900", "qwen", set(), con)
    # Simulate restart: rebuild dedupe from journal
    dedupe2 = set()
    for r in con.execute(
            "SELECT payload FROM journal WHERE kind='code_intel_investigation'"):
        d = json.loads(r[0])
        if d.get("fingerprint"):
            dedupe2.add(d["fingerprint"])
    assert len(dedupe2) == 1
    # Same P1, same rev -> deduped
    with patch("director_consumer.investigate._llm_call") as mock_call:
        r2 = investigate(P1, "designwins", "rev1", "token",
                         "http://asus2:8900", "qwen", dedupe2, con)
        assert r2.status == STATUS_DEDUPE_SKIP
        mock_call.assert_not_called()
