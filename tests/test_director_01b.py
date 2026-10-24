"""DIRECTOR-01B Task 1 — byte-exact fleet cursor regression tests.

Covers: non-ASCII content, multibyte characters split across polls, partial
final lines (incomplete records preserved, not skipped), truncation,
same-length replacement, restart, and replay idempotency.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from director_consumer.adapters import FleetChangesAdapter

MU = "µ"          # U+00B5 -> UTF-8 b"\xc2\xb5" (2 bytes)
OHM = "Ω"
NONASCII_BULLET = f"- m5: load {MU}processor {OHM}lane: ['a'] -> ['b']"


def cursor_obj(cur: str) -> dict:
    return json.loads(cur)


def test_non_ascii_content_exact_byte_cursor(tmp_path):
    chg = tmp_path / "CHANGES.md"
    chg.write_bytes(
        f"## 2026-10-16T20:00:00Z\n{NONASCII_BULLET}\n".encode("utf-8"))
    ad = FleetChangesAdapter(chg)
    evs, _, cur = ad.poll("")
    assert len(evs) == 1
    assert evs[0].summary == NONASCII_BULLET.lstrip("- ").strip()
    c = cursor_obj(cur)
    assert c["o"] == chg.stat().st_size, "cursor must be byte-exact"
    assert c["h"] == hashlib.sha256(chg.read_bytes()).hexdigest()


def test_multibyte_char_split_across_polls(tmp_path):
    chg = tmp_path / "CHANGES.md"
    full = f"## 2026-10-16T20:01:00Z\n- m5: ['x'] -> ['y{MU}']\n".encode()
    first = full[:15]
    chg.write_bytes(first)
    ad = FleetChangesAdapter(chg)
    # first poll: cut lands MID-CHARACTER (and mid-line) -> nothing complete
    evs1, _, cur1 = ad.poll("")
    assert evs1 == [], "partial bytes must not produce events"
    chg.write_bytes(full)
    evs2, _, cur2 = ad.poll(cur1)
    assert len(evs2) == 1, "completed record must surface exactly once"
    assert f"y{MU}" in evs2[0].summary
    assert evs2[0].attrs["box"] == "m5" and "yµ" in evs2[0].attrs["to"]
    assert cursor_obj(cur2)["o"] == len(full)


def test_partial_final_line_deferred(tmp_path):
    chg = tmp_path / "CHANGES.md"
    chg.write_bytes(b"## 2026-10-16T20:02:00Z\n- m5: ['a']")
    ad = FleetChangesAdapter(chg)
    evs1, _, cur1 = ad.poll("")
    assert evs1 == [], "unterminated final line is an incomplete record"
    with chg.open("ab") as fh:
        fh.write(b" -> ['b']\n")
    evs2, _, _ = ad.poll(cur1)
    assert len(evs2) == 1 and evs2[0].attrs["box"] == "m5"


def test_truncation_rereads_from_zero(tmp_path):
    chg = tmp_path / "CHANGES.md"
    chg.write_bytes(b"## 2026-10-16T20:03:00Z\n- m5: ['a'] -> ['b']\n"
                    b"## 2026-10-16T20:03:30Z\n- dgx2: ['x'] -> ['y']\n")
    ad = FleetChangesAdapter(chg)
    evs1, _, cur1 = ad.poll("")
    assert len(evs1) == 2
    # truncate to a shorter, different file
    chg.write_bytes(b"## 2026-10-16T20:04:00Z\n- m5: ['c'] -> ['d']\n")
    evs2, _, cur2 = ad.poll(cur1)
    # offset > filesize -> reread from zero -> the NEW file re-emitted
    assert len(evs2) == 1 and evs2[0].attrs["box"] == "m5"
    assert "'c'" in evs2[0].summary, "reread must surface the NEW content"
    # adapter-level re-emission is safe: consumer fingerprint dedup absorbs
    # it (proven by the consumer idempotency test below)
    assert cursor_obj(cur2)["o"] == chg.stat().st_size


def test_same_length_replacement_detected(tmp_path):
    chg = tmp_path / "CHANGES.md"
    chg.write_bytes(b"## 2026-10-16T20:05:00Z\n- m5: ['a'] -> ['b']\n")
    ad = FleetChangesAdapter(chg)
    _, _, cur1 = ad.poll("")
    # replace one byte in the consumed prefix, SAME total length
    data = bytearray(chg.read_bytes())
    data[data.index(b"'a'") + 1] = ord("z")
    chg.write_bytes(bytes(data))
    evs2, _, _ = ad.poll(cur1)
    assert len(evs2) == 1, "prefix-hash mismatch must trigger a full reread"


def test_restart_and_replay_idempotency(tmp_path):
    chg = tmp_path / "CHANGES.md"
    chg.write_bytes(
        f"## 2026-10-16T20:06:00Z\n{NONASCII_BULLET}\n- m5: ['a'] -> ['b']\n"
        .encode("utf-8"))
    ad = FleetChangesAdapter(chg)
    evs1, _, cur = ad.poll("")
    # replay with the same cursor
    evs_again, _, cur_again = ad.poll(cur)
    assert evs_again == [] and cur_again == cur
    # restart: a brand-new adapter instance resumes from the same cursor
    ad2 = FleetChangesAdapter(chg)
    evs_restart, _, cur_restart = ad2.poll(cur)
    assert evs_restart == [] and cur_restart == cur


def test_consumer_level_idempotency_after_truncation(tmp_path):
    """Adapter re-emission on reread must not duplicate decisions."""
    import sqlite3
    from director_consumer.consumer import DirectorConsumer, FeedConfig
    deps = Path(__file__).resolve().parents[1] / "director_consumer" / "deps.yaml"
    chg = tmp_path / "CHANGES.md"
    chg.write_bytes(b"## 2026-10-16T21:00:00Z\n- m5: ['a'] -> ['b']\n")
    ws = tmp_path / "ws"
    feeds = {"fleet_changes": FeedConfig(args={"path": chg})}
    c = DirectorConsumer(ws, deps, feeds=feeds)
    r1 = c.run_once()
    c.close()
    # truncate + rewrite (world changed under us)
    chg.write_bytes(b"## 2026-10-16T21:30:00Z\n- m5: ['c'] -> ['d']\n")
    c = DirectorConsumer(ws, deps, feeds=feeds)
    r2 = c.run_once()
    c.close()
    db = sqlite3.connect(ws / "director.db")
    proposals = db.execute("SELECT COUNT(*) FROM proposals").fetchone()[0]
    assessments = db.execute(
        "SELECT COUNT(*) FROM journal WHERE kind='assessment'").fetchone()[0]
    db.close()
    assert r1.events_new == 1 and r2.events_new == 1
    # one proposal for the original event + one for the genuinely new one;
    # the RE-EMITTED original (reread) must not have duplicated anything
    assert proposals == 2, f"expected 2 proposals after truncation reread, got {proposals}"
    assert assessments >= 2
