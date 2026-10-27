"""NEO status publisher — snapshots to the shared GitHub status branch.

Mechanism (docs/NEO-GITHUB-MECHANISM.md): every run builds read-only snapshots
of both Gas City cities through the M5 SSH tunnel, evaluates continuity
alerts, and commits them to the `neo/status` branch of outcited/outcitedharness.
NEO (ChatGPT) reads that branch through its existing GitHub connection — it
needs NO direct M5 access.

Commit policy: snapshot files are always refreshed; a history entry is added
only when content changes, or as an hourly heartbeat. Nothing is written to
Gas City; main is never touched.

Manual seed (once, to prove the mechanism):
  python3 neo-gascity/ops/status_publish.py --once
launchd activation (NOT active yet): see ops/install-m5.sh
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge import neo_status as NS          # noqa: E402
from bridge.gascity_client import GasCityClient  # noqa: E402

CITIES = ("designwins-orchestrator", "designwins-staging")
STATUS_BRANCH = "neo/status"
REMOTE = "origin"
REPO_URL = "https://github.com/outcited/outcitedharness.git"
WORKTREE = Path.home() / "designwins-neo-gascity" / "status-repo"


def sh(cmd: list[str], cwd: Path | None = None) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def ensure_worktree() -> Path:
    if (WORKTREE / ".git").exists():
        sh(["git", "fetch", REMOTE], WORKTREE)
        if _remote_branch_exists():
            sh(["git", "checkout", STATUS_BRANCH], WORKTREE)
            sh(["git", "reset", "--hard", f"{REMOTE}/{STATUS_BRANCH}"], WORKTREE)
        else:
            sh(["git", "checkout", "-B", STATUS_BRANCH, f"{REMOTE}/main"], WORKTREE)
        return WORKTREE
    WORKTREE.parent.mkdir(parents=True, exist_ok=True)
    sh(["git", "clone", REPO_URL, str(WORKTREE)])
    return ensure_worktree()


def _remote_branch_exists() -> bool:
    r = subprocess.run(["git", "ls-remote", "--heads", REMOTE, STATUS_BRANCH],
                       cwd=WORKTREE, capture_output=True, text=True)
    return bool(r.stdout.strip())


def publish(base_url: str) -> dict:
    client = GasCityClient(base_url)
    repo = ensure_worktree()
    status_dir = repo / "status"
    status_dir.mkdir(exist_ok=True)
    (status_dir / "history").mkdir(exist_ok=True)

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    alerts_all: dict[str, list] = {}
    content_digest = ""
    for city in CITIES:
        snap = NS.build_snapshot(client, city)
        alerts = NS.evaluate(snap)
        alerts_all[city] = alerts
        (status_dir / f"{city}.neo-snapshot.json").write_text(json.dumps(snap, indent=2) + "\n")
        (status_dir / f"{city}.neo-snapshot.md").write_text(NS.render_markdown(snap, alerts))
        content_digest += hashlib.sha256(json.dumps(_stable(snap), sort_keys=True).encode()).hexdigest()
    (status_dir / "ALERTS.md").write_text(
        "# NEO continuity alerts\n\n"
        + "\n".join(
            f"## {city}\n"
            + ("\n".join(f"- **{a['rule']}** {json.dumps({k: v for k, v in a.items() if k != 'rule'})}"
                         for a in alerts) or "- none")
            for city, alerts in alerts_all.items())
        + "\n"
    )
    (status_dir / "LATEST.json").write_text(json.dumps(
        {"captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
         "cities": {c: {"alerts": [a["rule"] for a in alerts_all[c]]} for c in CITIES},
         "content_digest": hashlib.sha256(content_digest.encode()).hexdigest()[:16]},
        indent=2) + "\n")

    # commit policy: history entry on change or hourly heartbeat
    last = _last_history_entry(status_dir / "history")
    changed = _snapshot_changed(repo, status_dir)
    heartbeat = not last or (time.time() - last[1]) > 55 * 60
    if changed or heartbeat:
        (status_dir / "history" / f"{stamp}.json").write_text(
            (status_dir / "LATEST.json").read_text())
        sh(["git", "add", "status"], repo)
        sh(["git", "-c", "user.name=neo-publisher",
            "-c", "user.email=neo@designwins",
            "commit", "-m",
            f"neo status {stamp} alerts="
            f"{sum(len(a) for a in alerts_all.values())} "
            f"({'change' if changed else 'heartbeat'})"], repo)
        sh(["git", "push", "-u", REMOTE, STATUS_BRANCH], repo)
    return {"changed": changed, "heartbeat": heartbeat,
            "alerts": {c: [a["rule"] for a in v] for c, v in alerts_all.items()}}


def _stable(snap: dict) -> dict:
    """Snapshot minus monotonically-changing fields, for change detection.

    uptime counters and capture timestamps advance every run; everything else
    (counts, agents, beads, alerts, event seq) is a real state change.
    """
    s = json.loads(json.dumps(snap))  # deep copy
    s.pop("captured_at", None)
    sup = s.get("supervisor") or {}
    sup.pop("uptime_sec", None)
    city = s.get("city") or {}
    city.pop("uptime_sec", None)
    # activity-only movement (orders fire continuously) is readable in the
    # snapshot files but does not create history entries — only state and
    # alert changes do
    s.pop("recent_events", None)
    s.pop("last_event_seq", None)
    return s


def _snapshot_changed(repo: Path, status_dir: Path) -> bool:
    try:
        sh(["git", "diff", "--quiet", "HEAD", "--", "status/LATEST.json"], repo)
        return False
    except RuntimeError:
        return True


def _last_history_entry(hist: Path):
    entries = sorted(hist.glob("*.json"))
    if not entries:
        return None
    name = entries[-1].stem
    try:
        t = time.mktime(time.strptime(name, "%Y%m%dT%H%M%SZ"))
    except ValueError:
        return None
    return name, t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8373")
    args = ap.parse_args()
    out = publish(args.base_url)
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
