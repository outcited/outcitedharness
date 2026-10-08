#!/usr/bin/env python3
"""Soak sampler (L3): hourly system vitals into one CSV + alerts.

Tracks queue depth, throughput, frontier spend, ledger rates, endpoint
health. Alert lines appear when nominal-load invariants break (frontier
spend > 0, queue depth growing across samples, endpoints down).
"""

import csv
import json
import os
import sqlite3
import time
import urllib.request
from pathlib import Path

DB = "/Volumes/M5_4TB/extract-results/pipeline.db"
SOAK = Path("/Volumes/M5_4TB/extract-results/soak.csv")
ENDPOINTS = {
    "v41_tp4": "http://100.116.221.82:8888/v1/models",
    "canon": "http://100.100.116.82:8900/v1/models",
    "student": "http://100.68.133.1:8950/v1/models",
}
FIELDS = ["ts", "pending", "claimed", "done", "dead", "done_delta",
          "frontier_today", "canon_today", "v41", "canon_up", "student_up", "alerts"]


def sample(prev):
    row = {"ts": int(time.time())}
    alerts = []
    con = sqlite3.connect(DB, timeout=15)
    con.row_factory = sqlite3.Row
    for state in ("pending", "claimed", "done", "dead"):
        row[state] = con.execute(
            "SELECT COUNT(*) c FROM jobs WHERE state=?", (state,)).fetchone()["c"]
    day = time.strftime("%Y-%m-%d")
    for tier in ("frontier", "local-canon"):
        r = con.execute("SELECT jobs FROM budget_spend WHERE day=? AND tier=?",
                        (day, tier)).fetchone()
        row["frontier_today" if tier == "frontier" else "canon_today"] = r["jobs"] if r else 0
    led = con.execute(
        "SELECT severity, COUNT(*) n FROM adjudication_ledger WHERE created_at > ?"
        " GROUP BY severity", (time.time() - 3600,)).fetchall()
    con.close()
    row["done_delta"] = row["done"] - prev.get("done", row["done"])
    if row["frontier_today"] > 0:
        alerts.append(f"frontier_spend={row['frontier_today']}")
    if prev and prev.get("pending") and row["pending"] > prev["pending"] * 1.05 and row["done_delta"] == 0:
        alerts.append("queue_growing_no_throughput")
    flags = {"v41_tp4": "v41", "canon": "canon_up", "student": "student_up"}
    for name, url in ENDPOINTS.items():
        row[flags[name]] = 0
        try:
            urllib.request.urlopen(url, timeout=8)
            row[flags[name]] = 1
        except Exception:
            if name == "v41_tp4":
                alerts.append("v41_down")
    row["alerts"] = ";".join(alerts)
    return row


def main():
    prev = {}
    if SOAK.exists():
        with SOAK.open() as f:
            for r in csv.DictReader(f):
                prev = {k: (int(v) if v.isdigit() else 0) for k, v in r.items()}
    row = sample(prev)
    new_file = not SOAK.exists()
    with SOAK.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)
    print(json.dumps(row))


if __name__ == "__main__":
    main()
