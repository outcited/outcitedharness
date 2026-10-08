#!/usr/bin/env python3
"""Student v2 checkpoint qualification rig.

Roles (2026-10-07): asus2 TRAINS (proven 1020-step box), dgx1 SERVES the
qualification endpoint (proven 41h serving). Watches asus2 training
checkpoints, syncs each adapter to dgx1, restarts the serve with it, runs
the v2 gate, logs trajectory. On first PASS: fires promotion (marker +
agent mail + power-aisle burn start).

State: results/lora-v2-pairs/qual-state.json
Trajectory: results/lora-v2-pairs/trajectory.jsonl
"""

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TRAIN_BOX = "samkimasus2@100.68.133.1"   # asus2 — training
SERVE_BOX = "samkim@100.81.201.24"       # dgx1 — serving/gate endpoint
CKPT_DIR = "/training/checkpoints/power-tables-qwen3vl-8b-v2"
ADAPTER_DEST = "/training/adapters/current"
SERVE_SCRIPT = "/training/serve_student_v2.sh"
GATE_ENDPOINT = "http://100.81.201.24:8950/v1/chat/completions"

RESULTS = REPO / "results/lora-v2-pairs"
STATE = RESULTS / "qual-state.json"
TRAJ = RESULTS / "trajectory.jsonl"
PROMOTED = RESULTS / "PROMOTED.json"
MAILBOX = Path("/Volumes/M5_4TB/agent-inbox")


def sh(cmd, timeout=600):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"processed": []}


def save_state(st):
    STATE.write_text(json.dumps(st, indent=2))


def list_checkpoints():
    rc, out, _ = sh(f"ssh -o ConnectTimeout=8 -o BatchMode=yes {TRAIN_BOX} "
                    f"ls {CKPT_DIR} 2>/dev/null")
    if rc != 0:
        return []
    cks = []
    for name in out.split():
        if not name.startswith("checkpoint-"):
            continue
        rc2, has, _ = sh(f"ssh -o ConnectTimeout=8 -o BatchMode=yes {TRAIN_BOX} "
                         f"ls {CKPT_DIR}/{name}/adapter_model.safetensors 2>/dev/null | wc -l")
        if rc2 == 0 and has.strip() == "1":
            cks.append(name)
    return sorted(cks, key=lambda n: int(n.split("-")[1]))


def sync_adapter(ck):
    print(f"[{now()}] syncing adapter {ck} -> dgx1 (via Mac)", flush=True)
    stage = Path("/tmp/adapter-stage")
    stage.mkdir(exist_ok=True)
    rc1, _, err1 = sh(f"rsync -az -e 'ssh -o ConnectTimeout=8 -o BatchMode=yes' "
                      f"{TRAIN_BOX}:{CKPT_DIR}/{ck}/adapter_model.safetensors "
                      f"{TRAIN_BOX}:{CKPT_DIR}/{ck}/adapter_config.json "
                      f"{stage}/", timeout=1800)
    if rc1 != 0:
        return False, err1
    rc2, _, err2 = sh(f"rsync -az -e 'ssh -o ConnectTimeout=8 -o BatchMode=yes' "
                      f"{stage}/adapter_model.safetensors {stage}/adapter_config.json "
                      f"{SERVE_BOX}:{ADAPTER_DEST}/", timeout=1800)
    return rc2 == 0, err2


def restart_serve():
    print(f"[{now()}] restarting student-serve with adapter", flush=True)
    rc, out, err = sh(f"ssh -o ConnectTimeout=8 -o BatchMode=yes {SERVE_BOX} "
                      f"bash {SERVE_SCRIPT}", timeout=900)
    print(out, flush=True)
    return rc == 0 and "READY" in out


def run_gate(ck):
    print(f"[{now()}] running gate on {ck}", flush=True)
    rc, out, err = sh(f"cd {REPO} && .venv/bin/python scripts/gate_v2.py "
                      f"--endpoint {GATE_ENDPOINT} --tag {ck}", timeout=10800)
    print(out[-1500:], flush=True)
    report = RESULTS / "gate-report-v2.json"
    if report.exists():
        return json.loads(report.read_text())
    return None


def log_trajectory(ck, rep):
    row = {"ts": now(), "checkpoint": ck, **{k: rep[k] for k in
           ("docs", "precision", "recall", "f1", "pass", "fields_matched",
            "fields_wrong_or_extra", "fields_missed") if k in rep}}
    with TRAJ.open("a") as f:
        f.write(json.dumps(row) + "\n")
    print(f"[{now()}] trajectory: {row}", flush=True)


def send_mail(subject, body):
    for box in ("m4opencode", "mac-mini"):
        mid = f"qual-{int(time.time())}"
        fpath = MAILBOX / box / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_m5-opencode_{box}_status_{mid}.md"
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(
            "---\n"
            f"id: {mid}\nfrom: m5-opencode\nto: {box}\ntype: status\n"
            "in_reply_to: null\n"
            f'subject: "{subject}"\n'
            f"created_at: {now()}\nrequires_response_by: null\nblocks_approval: null\n"
            "---\n\n" + body + "\n")
        print(f"[{now()}] mailed {box}", flush=True)


def fire_promotion(ck, rep):
    PROMOTED.write_text(json.dumps({"checkpoint": ck, "ts": now(), **rep}, indent=2))
    send_mail(
        f"student v2 PROMOTED — {ck} cleared gate",
        f"Checkpoint `{ck}` passed the v2 gate (f1={rep.get('f1')}, "
        f"precision={rep.get('precision')}, recall={rep.get('recall')}, "
        f"{rep.get('fields_matched')} fields matched on {rep.get('docs')} holdout docs).\n\n"
        f"Serving at dgx1:8950 as power-tables-student-v1. Firing the power-aisle "
        f"burn on the student tier. Report: results/lora-v2-pairs/gate-report-v2.json")
    print(f"[{now()}] PROMOTION FIRED — starting power-aisle burn", flush=True)
    sh(f"cd {REPO} && nohup .venv/bin/python scripts/burn_aisle.py --aisle power "
       f">> /Volumes/M5_4TB/extract-results/burn-continuous.log 2>&1 & echo started")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--poll", type=int, default=300)
    args = ap.parse_args()

    st = load_state()
    if PROMOTED.exists():
        print(f"[{now()}] already promoted: {PROMOTED.read_text()}", flush=True)
        return 0

    while True:
        cks = list_checkpoints()
        fresh = [c for c in cks if c not in st["processed"]]
        if fresh:
            ck = fresh[0]  # oldest first — clean learning curve
            print(f"[{now()}] gating checkpoint: {ck} ({len(fresh)} pending)", flush=True)
            ok, err = sync_adapter(ck)
            if not ok:
                print(f"[{now()}] sync failed: {err}", flush=True)
                time.sleep(args.poll)
                continue
            if not restart_serve():
                print(f"[{now()}] serve failed; will retry next poll", flush=True)
                time.sleep(args.poll)
                continue
            rep = run_gate(ck)
            if rep:
                log_trajectory(ck, rep)
                if rep.get("pass"):
                    fire_promotion(ck, rep)
                    return 0
            st["processed"].append(ck)
            save_state(st)
        else:
            print(f"[{now()}] no new checkpoints ({len(cks)} total)", flush=True)

        time.sleep(args.poll)


if __name__ == "__main__":
    raise SystemExit(main())
