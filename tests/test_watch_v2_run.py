from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "watch_v2_run.py"
SPEC = importlib.util.spec_from_file_location("watch_v2_run", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
watch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watch)


# Real stdout captured from asus2 on 2026-10-10T03:03Z (step 150/645).
HEALTHY = """status=running
now=1791601277
mtime=1791600152
avail=267
ckpts=0
last={"current_steps": 150, "total_steps": 645, "loss": 0.01955578476190567, "lr": 8.985180731086534e-05, "epoch": 0.7005253940435342, "percentage": 23.26, "elapsed_time": "8:18:36", "remaining_time": "1 day, 3:25:24"}
"""


def _obs(**over):
    obs = watch.parse(HEALTHY)
    assert obs is not None
    obs.update(over)
    return obs


def test_parse_reads_every_field() -> None:
    obs = watch.parse(HEALTHY)
    assert obs is not None
    assert obs["status"] == "running"
    assert obs["step"] == 150 and obs["total"] == 645
    assert obs["pct"] == 23.26
    assert obs["avail_gb"] == 267
    assert obs["checkpoints"] == 0
    assert obs["age_min"] == (1791601277 - 1791600152) / 60.0


def test_parse_survives_a_json_row_it_cannot_read() -> None:
    obs = watch.parse(HEALTHY.replace('last={"current_steps"', 'last={"broken"'))
    assert obs is not None and obs["step"] is None


def test_parse_rejects_a_truncated_probe() -> None:
    assert watch.parse("status=running\nnow=1\n") is None
    assert watch.parse("") is None
    assert watch.parse("ssh: connect to host ... port 22: Operation timed out") is None


def test_classify_ok_and_stall_use_the_logging_cadence() -> None:
    assert watch.classify(_obs(), stall_min=90)[0] == "OK"
    # 10-step logging is ~31 min at 3.1 min/step: 40 min old is NOT a stall.
    assert watch.classify(_obs(age_min=40.0), stall_min=90)[0] == "OK"
    state, reason = watch.classify(_obs(age_min=95.0), stall_min=90)
    assert state == "STALL" and "95 min old" in reason


def test_classify_exit_beats_stall_and_disk() -> None:
    assert watch.classify(_obs(status="exited"), stall_min=90)[0] == "EXITED"
    assert watch.classify(_obs(status="MISSING"), stall_min=90)[0] == "EXITED"
    state, reason = watch.classify(_obs(avail_gb=12), floor_gb=40)
    assert state == "DISK" and "12G free" in reason
    # Disk is checked before stall: a full disk is the cause, the stale log the symptom.
    assert watch.classify(_obs(avail_gb=5, age_min=500.0), stall_min=90, floor_gb=40)[0] == "DISK"


def test_classify_unreachable_is_distinct_from_exited() -> None:
    assert watch.classify(None)[0] == "UNREACHABLE"


def test_summary_carries_progress_and_headroom() -> None:
    text = watch.summary(_obs())
    assert "step 150/645" in text and "267G free" in text and "loss 0.0196" in text
    assert watch.summary(None) == "asus2 unreachable"


def test_probe_command_is_key_value_so_newlines_cannot_merge_fields() -> None:
    # Regression: `tr -dc '0-9'` stripped the newline after the df value, so
    # avail and ckpts concatenated ("267" + "0" -> "2670") and the old
    # line-count parser declared a healthy box UNREACHABLE.
    assert watch.PROBE.count("echo \"") == 6
    assert "tr -dc 0-9" in watch.PROBE
    assert watch.parse('status=running\nnow=10\nmtime=10\navail=2670\nckpts=3\nlast={}\n')["avail_gb"] == 2670


def test_mail_goes_to_the_canonical_inbox_only(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(watch, "CANON_INBOX", tmp_path / "absent")
    assert "NOT sent" in watch.mail("STALL", "log 95 min old", _obs())
    inbox = tmp_path / "agent-inbox"
    inbox.mkdir()
    monkeypatch.setattr(watch, "CANON_INBOX", inbox)
    note = watch.mail("STALL", "log 95 min old", _obs())
    assert "mailed" in note
    for box in watch.MAIL_BOXES:
        sent = list((inbox / box).glob("*_status_v2watch-stall.md"))
        assert len(sent) == 1
        assert "in_reply_to: student-v2-training-asus2" in sent[0].read_text()
    rows = [l for l in (inbox / "INDEX.jsonl").read_text().splitlines()]
    assert len(rows) == len(watch.MAIL_BOXES)
    assert "/Volumes/M5_4TB" not in str(inbox)
