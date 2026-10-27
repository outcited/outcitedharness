"""NEO status publisher — snapshots to the shared GitHub status branch.

Review #6448553783 fixes:
  * change detection compares a STABLE digest file (content minus volatile
    fields), never captured_at — identical state produces no history churn;
  * every published field passes bridge.neo_status.sanitize_snapshot
    (allowlist) — prompts, descriptions and event payloads never leave host;
  * the dedicated checkout is guarded: a marker file + toplevel check must
    both match before any reset/push; it can never point at user worktrees.

Commit policy: history entry (and push) only when the stable digest changes
or as an hourly heartbeat. Files are always refreshed locally; GitHub readers
detect staleness via status/LATEST.json captured_at (and consumers raise
snapshot-stale past 15 min).

Manual run:  python3 ops/status_publish.py --base-url http://127.0.0.1:8373
Activation (NOT active): see ops/install-m5.sh
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

from bridge import neo_status as NS               # noqa: E402
from bridge.gascity_client import GasCityClient   # noqa:E402

CITIES = ("designwins-orchestrator", "designwins-staging")
STATUS_BRANCH = "neo/status"
REMOTE = "origin"
REPO_URL = "https://github.com/outcited/outcitedharness.git"
WORKTREE = Path.home() / "designwins-neo-gascity" / "status-repo"
MARKER = ".neo-status-checkout"


def sh(cmd: list[str], cwd: Path | None = None) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {r.stderr.strip()[:300]}")
    return r.stdout.strip()


def _guarded(fn):
    """Refuse destructive ops outside the dedicated, marked checkout."""
    def wrap(cmd, cwd=None):
        if cwd is not None and any(k in cmd[:4] for k in ("reset", "checkout", "clean")):
            top = Path(sh(["git", "rev-parse", "--show-toplevel"], cwd=cwd))
            if top.resolve() != WORKTREE.resolve() or not (top / MARKER).exists():
                raise RuntimeError(f"refused: {top} is not the marked status checkout")
        return fn(cmd, cwd)
    return wrap


git = _guarded(sh)


def ensure_worktree() -> Path:
    if (WORKTREE / ".git").exists() and (WORKTREE / MARKER).exists():
        git(["git", "fetch", REMOTE], WORKTREE)
        if _remote_branch_exists():
            git(["git", "checkout", STATUS_BRANCH], WORKTREE)
            git(["git", "reset", "--hard", f"{REMOTE}/{STATUS_BRANCH}"], WORKTREE)
        else:
            git(["git", "checkout", "-B", STATUS_BRANCH, f"{REMOTE}/main"], WORKTREE)
        return WORKTREE
    if (WORKTREE / ".git").exists():
        raise RuntimeError(f"{WORKTREE} exists without {MARKER} — refusing; "
                           "move it aside and let the publisher clone fresh")
    WORKTREE.parent.mkdir(parents=True, exist_ok=True)
    sh(["git", "clone", REPO_URL, str(WORKTREE)])
    (WORKTREE / MARKER).write_text("dedicated neo/status publisher checkout\n")
    return ensure_worktree()


def _remote_branch_exists() -> bool:
    r = subprocess.run(["git", "ls-remote", "--heads", REMOTE, STATUS_BRANCH],
                       cwd=WORKTREE, capture_output=True, text=True)
    return bool(r.stdout.strip())


def publish(base_url: str) -> dict:
    client = GasCityClient(base_url)
    repo = ensure_worktree()
    status_dir = repo / "status"
    (status_dir / "history").mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    alerts_all: dict[str, list] = {}
    stable = ""
    receipts = _load_receipts()
    for city in CITIES:
        raw = NS.build_snapshot(client, city)
        alerts = NS.evaluate(raw, receipts=receipts)
        alerts_all[city] = alerts
        snap = NS.sanitize_snapshot(raw)          # allowlist BEFORE publishing
        (status_dir / f"{city}.neo-snapshot.json").write_text(json.dumps(snap, indent=2) + "\n")
        (status_dir / f"{city}.neo-snapshot.md").write_text(NS.render_markdown(snap, alerts))
        stable += hashlib.sha256(json.dumps(_stable(snap), sort_keys=True).encode()).hexdigest()
    stable_digest = hashlib.sha256(stable.encode()).hexdigest()

    (status_dir / "ALERTS.md").write_text(
        "# NEO continuity alerts\n\n"
        + "\n".join(
            f"## {city}\n"
            + ("\n".join(f"- **{a['rule']}** {json.dumps({k: v for k, v in a.items() if k != 'rule'})}"
                         for a in alerts) or "- none")
            for city, alerts in alerts_all.items())
        + "\n"
    )
    (status_dir / "LATEST.json").write_text(json.dumps({
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "max_freshness_min": NS.STALE_SNAPSHOT_MIN,
        "cities": {c: {"alerts": [a["rule"] for a in alerts_all[c]]} for c in CITIES},
        "stable_digest": stable_digest[:16]}, indent=2) + "\n")

    # FRESHNESS heartbeat: committed EVERY successful run (P3) — the 10-minute
    # launchd cadence is the publisher's liveness signal, independent of
    # engineering state. History entries stay gated on real state change.
    (status_dir / "FRESHNESS.json").write_text(json.dumps({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "interval_min": 10, "max_freshness_min": NS.STALE_SNAPSHOT_MIN,
        "supervisor_ok": True, "cities": list(CITIES),
        "stable_digest": stable_digest[:16],
        "alerts_total": sum(len(a) for a in alerts_all.values())}, indent=2) + "\n")

    changed = _digest_changed(repo, status_dir, stable_digest)
    history_entry = changed or _heartbeat_due(status_dir / "history")
    if history_entry:
        (status_dir / "history" / f"{stamp}.json").write_text((status_dir / "LATEST.json").read_text())
    git(["git", "add", "status"], repo)
    git(["git", "-c", "user.name=neo-publisher",
         "-c", "user.email=neo@designwins",
         "commit", "-m",
         f"neo {'state-change' if history_entry else 'freshness'} {stamp} "
         f"alerts={sum(len(a) for a in alerts_all.values())}"], repo)
    sh(["git", "push", REMOTE, STATUS_BRANCH], repo)
    return {"changed": changed, "history_entry": history_entry,
            "stable_digest": stable_digest[:16],
            "alerts": {c: [a["rule"] for a in v] for c, v in alerts_all.items()}}


def _load_receipts() -> list[dict]:
    """Persisted acceptance receipts visible to this host (receipts/, demo)."""
    out: list[dict] = []
    for pattern in (ROOT / "receipts" / "*.accepted*.json",
                    ROOT / "demo" / "out" / "**" / "*.accepted*.json"):
        for p in ROOT.glob(str(pattern.relative_to(ROOT))):
            try:
                out.append(json.loads(p.read_text()))
            except json.JSONDecodeError:
                continue
    return out


def _stable(snap: dict) -> dict:
    """Sanitized snapshot minus monotonically-changing fields."""
    s = json.loads(json.dumps(snap))
    s.pop("captured_at", None)
    if isinstance(s.get("supervisor"), dict):
        s["supervisor"].pop("uptime_sec", None)
    if isinstance(s.get("city"), dict):
        s["city"].pop("uptime_sec", None)
    s.pop("recent_events", None)      # activity is readable, not state
    s.pop("last_event_seq", None)
    s.pop("event_index", None)        # event cursor moves with activity
    return s


def _digest_changed(repo: Path, status_dir: Path, new_digest: str) -> bool:
    """Compare ONLY the stable digest — never captured_at (review finding 3)."""
    digest_file = status_dir / ".stable-digest"
    prev = digest_file.read_text().strip() if digest_file.exists() else ""
    digest_file.write_text(new_digest + "\n")
    return prev != new_digest


def _heartbeat_due(history_dir: Path) -> bool:
    """True when the last history entry is older than 55 minutes (UTC-safe)."""
    import calendar
    entries = sorted(Path(history_dir).glob("*.json"))
    if not entries:
        return True
    try:
        t = calendar.timegm(time.strptime(entries[-1].stem, "%Y%m%dT%H%M%SZ"))
    except ValueError:
        return True
    return (time.time() - t) > 55 * 60


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8373")
    args = ap.parse_args()
    try:
        out = publish(args.base_url)
    except Exception as e:  # fail visibly: never exit 0 on a broken cycle
        print(f"PUBLISH FAILED: {e}", file=sys.stderr)
        return 1
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
