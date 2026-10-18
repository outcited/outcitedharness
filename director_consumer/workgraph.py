"""Work/ownership graph — DIRECTOR-06 §4E, the orchestrator's core query.

Answers the owner's question directly:
  "Who is working on this, what's blocking them, and what evidence would
   prove it is finished?"

Honesty rules carried over from the memory work:
  * an agent's commit message is an ASSERTION of work, never proof of done
  * "who" comes from git authorship and the declared agent network — never
    guessed from a branch name alone
  * "blocking" is only stated where a measurement says so (telemetry skip
    reasons, crash-looping units, missing inputs) — otherwise UNKNOWN
  * "what would prove it finished" is a NAMED artifact per work type,
    not a vibe
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

UNKNOWN = "unknown"

# What artifact proves each class of work finished. Named, checkable.
PROOF_MAP = {
    "extraction-lane": "lane-out/<lane>.jsonl rows stop growing AND a "
                       "verification receipt quotes rows against source PDFs",
    "feeder-health":   "work-feeder.service active, log shows launched/SKIP "
                       "reasons with no respawn spam, lane counts non-zero",
    "factory-counter": "intake-telemetry.json shows an identity-based "
                       "(not mtime) new-document count AND the 10-doc "
                       "validation burn actually runs",
    "frontend":        "PR merged to MAIN (not stacked), Next build green in "
                       "CI, screen served from real export (no DemoStore "
                       "fallback on search/design/job/gaps)",
    "api-wiring":      "dw-serve run with DW_EXPORT_ROOT answering search/"
                       "design/job/gaps from real export; demo fallback "
                       "removed or fail-closed",
    "verification":    "adjudication ledger row (or lane receipt) with a "
                       "content verdict + evidence quote per claim",
    "identity":        "identity-join output with resolved OPN->package->n "
                       "for the cohort, sealed receipt",
    "director-deploy": "systemctl status shows the corrected commit in "
                       "VERSION; journal chain verified",
}


@dataclass
class WorkThread:
    thread_id: str
    kind: str                      # key into PROOF_MAP
    title: str = ""
    owner: str = UNKNOWN           # agent or human, from measurement
    owner_evidence: str = ""       # how we know the owner
    state: str = UNKNOWN           # active | stale | blocked | done | unknown
    last_activity: Any = UNKNOWN
    blockers: list[str] = field(default_factory=list)
    proof_required: str = ""
    source: str = ""               # where this thread was measured


def _sh(cmd, timeout=30):
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout).stdout.strip()
    except Exception:                                        # noqa: BLE001
        return ""


# branch prefix -> owning agent, from the declared agent network (fleet.yaml
# agents:). Ownership declared in config beats commit-author guessing.
BRANCH_OWNERS = {
    "agent/power-data-01": "power-data-01 (m5 sibling worktree)",
    "director/": "m5-opencode director session",
    "integration/": "v2c agent (m5)",
    "curve/": "curve agent",
    "discovery/": "discovery agent",
    "facet/": "facet agent",
    "queue/": "queue-fix agent",
    "search/": "search agent",
    "audit/": "audit agent",
}


def _owner_for(branch: str, author: str) -> tuple[str, str]:
    for prefix, agent in BRANCH_OWNERS.items():
        if branch.startswith(prefix):
            return agent, f"declared owner of branch {branch!r}"
    return author or UNKNOWN, "last commit author (no declared owner)"


def git_threads(roots_glob: str = "/Users/samkim/Harnessv1*") -> list[WorkThread]:
    """One WorkThread per worktree with a real branch. Owner = most recent
    commit author (an assertion of activity, not of completion)."""
    out: list[WorkThread] = []
    for d in sorted(Path("/Users/samkim").glob(Path(roots_glob).name)):
        if not (d / ".git").exists() and not (d / ".git").is_file():
            continue
        branch = _sh(["git", "-C", str(d), "rev-parse", "--abbrev-ref", "HEAD"])
        if not branch:
            continue
        last = _sh(["git", "-C", str(d), "log", "-1",
                    "--format=%an|%ad|%s", "--date=short"])
        if not last:
            continue
        author, date, subject = (last.split("|", 2) + ["", ""])[:3]
        ts = _sh(["git", "-C", str(d), "log", "-1", "--format=%ct"])
        age_days = (time.time() - float(ts or 0)) / 86400 if ts else None
        recent = [l for l in _sh(["git", "-C", str(d), "log", "--oneline",
                                  "--since=3 days ago"]).splitlines() if l]
        dirty = bool(_sh(["git", "-C", str(d), "status", "--porcelain"]))
        state = ("active" if recent else
                 "stale" if age_days is not None and age_days > 3 else
                 "unknown")
        owner, owner_ev = _owner_for(branch, author)
        out.append(WorkThread(
            thread_id=d.name, kind=PROOF_KIND_FOR(branch, subject),
            title=f"{branch}: {subject[:80]}",
            owner=owner,
            owner_evidence=f"{owner_ev} @ {date}",
            state=state, last_activity=date,
            blockers=(["uncommitted changes in worktree"] if dirty else []),
            source=f"worktree:{d}"))
    return out


def PROOF_KIND_FOR(branch: str, subject: str) -> str:
    b = (branch + " " + subject).lower()
    if any(k in b for k in ("frontend", "fe-", "golden-path", "vision-to-claims")):
        return "frontend"
    if any(k in b for k in ("dw-api", "export", "serve")):
        return "api-wiring"
    if any(k in b for k in ("verify", "adjudicat", "gold")):
        return "verification"
    if any(k in b for k in ("identity", "pinout", "opn")):
        return "identity"
    if any(k in b for k in ("director", "orchestrator")):
        return "director-deploy"
    if any(k in b for k in ("feeder", "queue", "dispatch")):
        return "feeder-health"
    return "extraction-lane"


def telemetry_threads(feeder_log: str = "",
                      telemetry_dir: str = "") -> list[WorkThread]:
    """Lanes from the live feeder log + telemetry records. Blocking reasons
    come only from measured SKIP lines / crash states."""
    threads: list[WorkThread] = []
    seen: set[str] = set()

    def _add(t: WorkThread):
        if t.thread_id not in seen:
            seen.add(t.thread_id)
            threads.append(t)

    if feeder_log and Path(feeder_log).exists():
        text = Path(feeder_log).read_text(errors="replace")
        for m in re.finditer(r"\[feeder\] SKIP (\w+): (.+)", text):
            lane, reason = m.group(1), m.group(2)
            _add(WorkThread(
                thread_id=f"lane:{lane}", kind="extraction-lane",
                title=f"extraction lane {lane}", owner="happy-owner",
                owner_evidence="work-feeder on happy",
                state="blocked", blockers=[reason],
                proof_required=PROOF_MAP["extraction-lane"],
                source="feeder-log"))
        for m in re.finditer(r"\[feeder\] launched (\w+) \[(\d+)\]", text):
            lane = m.group(1)
            if f"lane:{lane}" not in seen:
                _add(WorkThread(
                    thread_id=f"lane:{lane}", kind="extraction-lane",
                    title=f"extraction lane {lane}", owner="happy-owner",
                    owner_evidence="work-feeder on happy",
                    state="active", proof_required=PROOF_MAP["extraction-lane"],
                    source="feeder-log"))
    if telemetry_dir and Path(telemetry_dir).is_dir():
        for f in sorted(Path(telemetry_dir).glob("*.json")):
            try:
                d = json.loads(f.read_text())
            except Exception:                                # noqa: BLE001
                continue
            for s in d.get("services", []):
                if isinstance(s, dict) and (s.get("restarts") or 0) > 5:
                    _add(WorkThread(
                        thread_id=f"unit:{s.get('name')}",
                        kind="feeder-health",
                        title=f"crash-looping unit {s.get('name')}",
                        owner="happy-owner",
                        owner_evidence="systemd unit on happy",
                        state="blocked",
                        blockers=[f"{s.get('restarts')} restarts"],
                        proof_required=PROOF_MAP["feeder-health"],
                        source=f.name))
    return threads


def answer(query: str, threads: Optional[list[WorkThread]] = None) -> dict:
    """The orchestrator's core answer. Matching is lexical on thread ids and
    titles; scope-honesty: no match returns unknown, never a guess."""
    threads = threads or []
    q = query.lower()
    scored = []
    for t in threads:
        hay = f"{t.thread_id} {t.title} {t.kind}".lower()
        score = sum(1 for w in re.findall(r"[a-z0-9_-]+", q) if w in hay)
        if score:
            scored.append((score, t))
    scored.sort(key=lambda x: -x[0])
    hits = [t for _, t in scored[:5]]
    if not hits:
        return {"query": query, "match": "none",
                "answer": "no measured work thread matches; not guessing",
                "threads": []}
    return {
        "query": query,
        "match": "ok",
        "threads": [{
            "thread": t.thread_id,
            "who": t.owner,
            "who_evidence": t.owner_evidence,
            "state": t.state,
            "blocking": t.blockers or [UNKNOWN],
            "proof_required": t.proof_required or PROOF_MAP.get(t.kind, UNKNOWN),
            "source": t.source,
        } for t in hits],
        "note": "commit messages assert work; only the named proof artifact "
                "establishes completion",
    }


def render(a: dict) -> str:
    if a.get("match") != "ok":
        return f"Q: {a['query']}\n  {a['answer']}"
    lines = [f"Q: {a['query']}"]
    for t in a["threads"]:
        lines.append(f"  {t['thread']} — {t['state']}")
        lines.append(f"    who:     {t['who']}  ({t['who_evidence']})")
        lines.append(f"    blocking: {', '.join(t['blocking'])}")
        lines.append(f"    proof:   {t['proof_required']}")
    lines.append(f"  {a['note']}")
    return "\n".join(lines)
