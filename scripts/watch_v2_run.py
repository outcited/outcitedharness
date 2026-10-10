#!/usr/bin/env python3
"""Watch the student-v2 training run on asus2 and mail on state transitions.

The run is `power-tables-v2-train` (image harness/llamafactory-qwen3-next:20260901)
on asus2. Truth comes from the trainer's own JSONL, never from `docker logs`:
llamafactory's stdout is block-buffered through docker, which already cost a
healthy 80-minute run (RUNBOOK failure mode 10).

States: OK / STALL (container up, log not advancing) / EXITED / DISK. Mail goes
to the canonical NAS inbox only on TRANSITION, so a run that stays broken sends
one mail, not four an hour — same discipline as the fleet probe.

Cadence: launchd com.harness.v2watch, every 15 min. Log: ~/Logs/v2watch.log.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HOST = os.environ.get("V2WATCH_HOST", "samkimasus2@100.68.133.1")
CONTAINER = os.environ.get("V2WATCH_CONTAINER", "power-tables-v2-train")
CKPT_DIR = os.environ.get(
    "V2WATCH_CKPT",
    "/home/samkimasus2/harness-training/checkpoints/power-tables-qwen3-vl-8b-v2",
)
# The trainer appends one row per logging_steps=10, which at the observed
# ~3.1 min/step is ~31 min between rows. A stall threshold below ~3 intervals
# alarms on a perfectly healthy run, so the floor is 90 min, not 25.
STALL_MIN = float(os.environ.get("V2WATCH_STALL_MIN", "90"))
DISK_FLOOR_GB = float(os.environ.get("V2WATCH_DISK_FLOOR_GB", "40"))

CANON_INBOX = Path(
    os.environ.get(
        "AGENT_INBOX",
        Path.home() / "Volumes/Electronicdataassets/agent-inbox",
    )
)
LOG = Path.home() / "Logs/v2watch.log"
STATE = Path.home() / "Logs/v2watch.state"
MAIL_BOXES = ("m5-opencode", "mac-mini")

PROBE = (
    f'echo "status=$(docker inspect -f "{{{{.State.Status}}}}" {CONTAINER} 2>/dev/null || echo MISSING)";'
    f'echo "now=$(date +%s)";'
    f'echo "mtime=$(stat -c %Y {CKPT_DIR}/trainer_log.jsonl 2>/dev/null || echo 0)";'
    f'echo "avail=$(df -BG --output=avail / | tail -1 | tr -dc 0-9)";'
    f'echo "ckpts=$(ls -d {CKPT_DIR}/checkpoint-* 2>/dev/null | wc -l)";'
    f'echo "last=$(tail -1 {CKPT_DIR}/trainer_log.jsonl 2>/dev/null)"'
)


def parse(stdout):
    """key=value lines -> observation. Returns None if the shape is wrong."""
    fields = {}
    for line in stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            fields.setdefault(key.strip(), value.strip())
    if not {"status", "now", "mtime"} <= fields.keys():
        return None
    try:
        progress = json.loads(fields.get("last") or "{}")
    except json.JSONDecodeError:
        progress = {}
    now, mtime = fields["now"], fields["mtime"]
    avail = fields.get("avail", "")
    return {
        "status": fields["status"],
        "age_min": (int(now) - int(mtime)) / 60.0 if now.isdigit() and mtime.isdigit() else None,
        "step": progress.get("current_steps"),
        "total": progress.get("total_steps"),
        "loss": progress.get("loss"),
        "pct": progress.get("percentage"),
        "remaining": progress.get("remaining_time"),
        "avail_gb": int(avail) if avail.isdigit() else None,
        "checkpoints": int(fields.get("ckpts") or 0),
    }


def collect(host=HOST):
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, PROBE],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    return parse(proc.stdout)


def classify(obs, stall_min=STALL_MIN, floor_gb=DISK_FLOOR_GB):
    if obs is None:
        return "UNREACHABLE", "asus2 ssh/probe failed"
    if obs["status"] != "running":
        return "EXITED", f"container state = {obs['status']}"
    if obs["avail_gb"] is not None and obs["avail_gb"] < floor_gb:
        return "DISK", f"only {obs['avail_gb']}G free on / (< {floor_gb:.0f}G floor)"
    if obs["age_min"] is not None and obs["age_min"] > stall_min:
        return "STALL", f"trainer_log.jsonl {obs['age_min']:.0f} min old (> {stall_min:.0f})"
    return "OK", ""


def summary(obs):
    if obs is None:
        return "asus2 unreachable"
    step = f"step {obs['step']}/{obs['total']} ({obs['pct']}%)" if obs.get("step") else "no progress row"
    loss = f"loss {obs['loss']:.4f}" if isinstance(obs.get("loss"), float) else ""
    left = f"ETA {obs['remaining']}" if obs.get("remaining") else ""
    return " · ".join(x for x in (step, loss, left, f"{obs['avail_gb']}G free", f"{obs['checkpoints']} ckpt(s)") if x)


def mail(state, reason, obs):
    if not CANON_INBOX.is_dir():
        return f"mail NOT sent (canonical inbox not mounted at {CANON_INBOX})"
    now = datetime.now(timezone.utc)
    slug = now.strftime("%Y%m%dT%H%M%SZ")
    subject = f"v2 training run {state}: {reason}"
    body = (
        f"---\nid: v2watch-{state.lower()}-{slug}\nfrom: m5-opencode\n"
        f"to: all\ntype: status\nin_reply_to: student-v2-training-asus2\n"
        f'subject: "{subject}"\ncreated_at: {now.strftime("%Y-%m-%dT%H:%M:%SZ")}\n'
        f"requires_response_by: null\nblocks_approval: null\n---\n\n"
        f"`{CONTAINER}` on asus2 changed state.\n\n"
        f"- state: **{state}** — {reason}\n"
        f"- now: {summary(obs)}\n"
        f"- source: {CKPT_DIR}/trainer_log.jsonl (never `docker logs`)\n\n"
        f"Run: `.venv/bin/python scripts/watch_v2_run.py --once --verbose` on the Mac.\n"
    )
    for box in MAIL_BOXES:
        d = CANON_INBOX / box
        d.mkdir(parents=True, exist_ok=True)
        name = f"{slug}_m5-opencode_{box}_status_v2watch-{state.lower()}.md"
        (d / name).write_text(body)
        row = {
            "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "from": "m5-opencode",
            "to": box,
            "type": "status",
            "id": f"v2watch-{state.lower()}-{slug}",
            "subject": subject[:120],
            "path": f"agent-inbox/{box}/{name}",
        }
        try:
            with (CANON_INBOX / "INDEX.jsonl").open("a") as fh:
                fh.write(json.dumps(row) + "\n")
        except OSError:
            pass
    return f"mailed {', '.join(MAIL_BOXES)}"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    verbose = "--verbose" in argv
    obs = collect()
    state, reason = classify(obs)
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} {state} {summary(obs)}"
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as fh:
        fh.write(line + ("\n" if state == "OK" else f" — {reason}\n"))

    prior = STATE.read_text().strip() if STATE.is_file() else ""
    if state != prior:
        if state == "OK":
            note = "initial state" if not prior else "recovered"
        else:
            note = mail(state, reason, obs)
        STATE.write_text(state)
        print(f"{line} — {reason} [{note}]" if reason else f"{line} [{note}]")
    elif verbose or state != "OK":
        print(f"{line} (unchanged)")
    return 0 if state == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
