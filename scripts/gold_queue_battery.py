#!/usr/bin/env python3
"""Generate dw-queue's gold: a scenario battery against the LIVE
`harness/pipeline/store.py`, with the clock pinned so the output is byte-
deterministic. D8: the reference is the spec; this samples it.

Each scenario runs on a fresh temp db; the gold carries the full table state
after the scenario. dw-queue's tests replay the same ops through the Rust
port and compare.

Usage: python3 scripts/gold_queue_battery.py > tests/fixtures/gold/queue_battery_v1.json
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from harness.pipeline import store  # noqa: E402

T0 = 1_700_000_000.0


class Clock:
    def __init__(self):
        self.t = T0

    def now(self):
        return self.t


def dump(con: sqlite3.Connection) -> dict:
    out = {}
    for table in ("jobs", "results", "adjudication_ledger", "budget_spend"):
        order = "day, tier" if table == "budget_spend" else "id"
        rows = [dict(r) for r in con.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()]
        out[table] = rows
    return out


def fresh():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    Path(tmp.name).unlink()
    return store.connect(tmp.name)


def run_scenario(fn, clock: Clock):
    store.now = clock.now
    con = fresh()
    fn(con, clock)
    return dump(con)


def s_enqueue_claim_ack(con, clock):
    store.enqueue(con, "substrate", "sha256:aaa", source_path="/x/a.pdf", max_attempts=3)
    store.enqueue(con, "substrate", "sha256:bbb", budget_tier="local-student", payload={"p": 1})
    store.enqueue(con, "extract", "sha256:ccc")
    clock.t += 1
    claimed = store.claim(con, "worker-1", kinds=("substrate",), limit=10)
    clock.t += 1
    store.ack(con, claimed[0]["id"], "ext", "v1", '{"rows": 1}')
    store.ack(con, claimed[1]["id"], "ext", "v1", '{"rows": 2}')


def s_fail_to_dead(con, clock):
    jid = store.enqueue(con, "substrate", "sha256:ddd", max_attempts=2)
    for _ in range(3):
        clock.t += 5
        got = store.claim(con, "worker-2", kinds=("substrate",), limit=5)
        if got:
            store.fail(con, got[0]["id"], reason="boom")


def s_visibility_reclaim(con, clock):
    jid = store.enqueue(con, "substrate", "sha256:eee")
    clock.t += 1
    a = store.claim(con, "worker-a", kinds=("substrate",), limit=5)
    clock.t += 100
    # A still holds it: B gets nothing while the lease is visible.
    b_early = store.claim(con, "worker-b", kinds=("substrate",), limit=5)
    clock.t += 4000
    b_late = store.claim(con, "worker-b", kinds=("substrate",), limit=5)
    store.ack(con, b_late[0]["id"], "ext", "v1", "{}")


def s_ack_dedup_and_ledger(con, clock):
    jid = store.enqueue(con, "substrate", "sha256:fff")
    clock.t += 1
    got = store.claim(con, "worker-3", kinds=("substrate",), limit=5)
    first = store.ack(con, got[0]["id"], "ext", "v1", '{"n": 1}')
    second = store.ack(con, got[0]["id"], "ext", "v1", '{"n": 1}')
    store.record_verdicts(con, first, "judge", "j1", [
        {"claim_index": 0, "severity": "P0", "verdict": "UNSUPPORTED",
         "reason_code": "value_not_in_quote", "evidence_quote": "q", "failure_class": "hallucination"},
    ], extractor="ext")


def s_budget(con, clock):
    caps = {"frontier": 2}
    r1 = store.budget_check(con, "frontier", caps)
    r2 = store.budget_check(con, "frontier", caps)
    r3 = store.budget_check(con, "frontier", caps)
    assert r1[0] and r2[0] and not r3[0], (r1, r2, r3)


SCENARIOS = [
    ("enqueue_claim_ack", s_enqueue_claim_ack),
    ("fail_to_dead", s_fail_to_dead),
    ("visibility_reclaim", s_visibility_reclaim),
    ("ack_dedup_and_ledger", s_ack_dedup_and_ledger),
    ("budget", s_budget),
]


def main() -> int:
    gold = {}
    for name, fn in SCENARIOS:
        gold[name] = run_scenario(fn, Clock())
    json.dump(gold, sys.stdout, indent=1, sort_keys=True)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
