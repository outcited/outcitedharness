"""Work/ownership graph — the orchestrator's core query, tested honestly."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from director_consumer.workgraph import WorkThread, answer, git_threads, render, telemetry_threads


def _t(tid, kind="extraction-lane", owner="agent-x", state="active", blockers=None):
    return WorkThread(thread_id=tid, kind=kind, title=f"work on {tid}",
                      owner=owner, owner_evidence="test", state=state,
                      blockers=blockers or [], source="test")


def test_three_part_answer():
    ts = [_t("lane:tables", state="blocked",
             blockers=["needs --page-index, not a doc queue"])]
    a = answer("who is working on tables lane", ts)
    assert a["match"] == "ok"
    t = a["threads"][0]
    assert t["who"] == "agent-x"
    assert t["blocking"] == ["needs --page-index, not a doc queue"]
    assert "verification receipt" in t["proof_required (criterion, not a status)"]


def test_no_match_is_unknown_not_a_guess():
    a = answer("payroll onboarding", [_t("lane:thermal")])
    assert a["match"] == "none"
    assert "not guessing" in a["answer"]


def test_blocking_is_only_stated_when_measured():
    a = answer("thermal", [_t("lane:thermal", state="active")])
    assert a["threads"][0]["blocking"] == ["unknown"]


def test_feeder_skip_reasons_become_blockers(tmp_path):
    log = tmp_path / "feeder.log"
    log.write_text(
        "[feeder] SKIP tables: needs --page-index first\n"
        "[feeder] launched thermal [0]\n")
    ts = telemetry_threads(feeder_log=str(log))
    by_id = {t.thread_id: t for t in ts}
    assert by_id["lane:tables"].state == "blocked"
    assert "page-index" in by_id["lane:tables"].blockers[0]
    assert by_id["lane:thermal"].state == "active"


def test_crash_looping_unit_is_a_blocked_thread(tmp_path):
    d = tmp_path / "tel"
    d.mkdir()
    (d / "happy.json").write_text('{"services": ['
        '{"name": "extract-connector", "status": "failed", '
        '"workers": 0, "restarts": 17}]}')
    ts = telemetry_threads(telemetry_dir=str(d))
    assert any(t.state == "blocked" and "extract-connector" in t.title for t in ts)


def test_commit_asserts_work_but_proof_is_named():
    a = answer("frontend", [_t("integration/vision-to-claims", kind="frontend")])
    p = a["threads"][0]["proof_required (criterion, not a status)"]
    assert "merged to MAIN" in p and "DemoStore" in p


# ---------------- honesty pass (review 2026-10-18) ----------------

def test_owner_is_labelled_inference_not_assignment():
    ts = git_threads() or []
    for t in ts:
        if "inferred" in t.owner_evidence or "activity signal" in t.owner_evidence:
            assert "acknowledged" not in t.owner_evidence or \
                "NOT an acknowledged" in t.owner_evidence

def test_every_thread_defaults_to_unacknowledged_and_no_evidence():
    ts = git_threads() or []
    for t in ts:
        assert t.assignment == "none-acknowledged"
        assert t.completion_evidence == "none"

def test_render_separates_criterion_from_evidence():
    ts = [_t("integration/vision-to-claims", kind="frontend")]
    text = render(answer("frontend", ts))
    assert "done-when:" in text
    assert "evidence-now: none" in text
    assert "criterion, not evidence" in text
