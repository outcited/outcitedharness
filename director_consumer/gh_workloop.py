"""GitHub work-order ingest — DIRECTOR-WORKLOOP-01 (issue #2, step 1).

Closes the loop's first half: the Director PULLS her own work orders from
allowlisted repos instead of a human or another agent pasting them in.

Laws (from the #2 PRD, enforced here):
  * Only allowlisted repos. No arbitrary remote issue execution.
  * An issue body is an UNTRUSTED work definition, never an authorization.
    Required fields are validated; missing fields downgrade the task to
    `untrusted-definition` state with the exact gaps named — never silently
    accepted, never silently dropped.
  * GitHub polling is read-only; only local TaskStore definition rows change.
    Ingest does not assign an owner, send messages, or acknowledge work.
    'offered' means proposed work, NOT a delivered task.
  * Idempotent + restart-safe: re-running produces no duplicate tasks or
    events (keyed by project, task id, repo+issue).
  * GitHub comments are work evidence, not authenticated acks. An
    attributable ack requires the mailbox transport (later steps of #2).

Reads use unauthenticated REST for public repos; authenticated read/write
degrades to read-only when the org blocks the PAT (exact error recorded).
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .tasks import TaskStore

ALLOWLISTED_REPOS = ("outcited/outcitedharness", "outcited/designwins-v2")
TASK_ID_RE = re.compile(r"^([A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+[0-9]*)\s*[—-]")
REQUIRED_FIELDS = ("task_id", "lane")
LANE_REGISTRY = {  # proposed lane labels, NEVER confirmed owners from titles
    "director": "seven-director",
    "m5": "m5-extraction-provenance",
    "m4": "m4-frontend",
    "mac-mini": "pdf-factory",
}
INBOX_STATE = "offered"          # ingest proposes; ack is a separate event
UNTRUSTED_STATE = "untrusted-definition"


@dataclass
class Ingested:
    repo: str
    number: int
    task_id: str
    title: str = ""
    lane: str = ""
    state: str = ""               # offered | untrusted-definition | duplicate
    missing_fields: list = field(default_factory=list)
    url: str = ""
    comments: int = 0


def _get(url: str, timeout: float = 20.0) -> Any:
    req = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json",
                      "User-Agent": "director-workloop"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_open_issues(repo: str) -> list[dict]:
    if repo not in ALLOWLISTED_REPOS:
        raise PermissionError(f"repo {repo!r} not in allowlist")
    out = []
    for page in (1, 2, 3):
        d = _get(f"https://api.github.com/repos/{repo}/issues"
                 f"?state=open&per_page=100&page={page}")
        out.extend(d)
        if len(d) < 100:
            break
    return [i for i in out if "pull_request" not in i]


def count_comments(repo: str, number: int) -> int:
    try:
        return len(_get(f"https://api.github.com/repos/{repo}/"
                        f"issues/{number}/comments?per_page=100"))
    except Exception:                                        # noqa: BLE001
        return -1  # unknown, never zero


def parse_issue(issue: dict) -> tuple[str, list[str]]:
    """Returns (task_id or '', missing_fields). Title prefix defines the
    task id (`<ID> — ...`); lane is discovered in title+body keywords."""
    title = issue.get("title") or ""
    m = TASK_ID_RE.match(title.strip())
    task_id = m.group(1) if m else ""
    hay = (title + "\n" + (issue.get("body") or "")).lower()
    lane = ""
    for key in ("director", "m5", "m4", "mac-mini"):
        if key in hay:
            lane = key
            break
    missing = [f for f in REQUIRED_FIELDS
               if f == "task_id" and not task_id]
    if not lane:
        missing.append("lane")
    return task_id, missing


def ingest(repo: str, ts: TaskStore, issues: Optional[list[dict]] = None,
           now: float = None) -> list[Ingested]:
    """Idempotent ingest of allowlisted open issues into TaskStore."""
    if repo not in ALLOWLISTED_REPOS:
        raise PermissionError(f"repo {repo!r} not in allowlist "
                              f"{ALLOWLISTED_REPOS}")
    now = now or time.time()
    raw = issues if issues is not None else fetch_open_issues(repo)
    out: list[Ingested] = []
    for i in raw:
        task_id, missing = parse_issue(i)
        number = i.get("number")
        if not task_id:
            task_id = f"GH-{repo.split('/')[-1].upper()}-{number}"
            missing = missing or ["task_id-in-title"]
        key = f"{repo}#{number}"
        url = i.get("html_url") or f"https://github.com/{repo}/issues/{number}"
        rec = Ingested(repo=repo, number=number, task_id=task_id,
                       title=(i.get("title") or "")[:120], url=url)
        existing = ts.con.execute(
            "SELECT task_id, state, correlation_id FROM tasks "
            "WHERE project=? AND task_id=?",
            (ts.project, task_id)).fetchone()
        evidence = json.dumps({
            "source": "github-issue", "repo": repo, "issue": number,
            "url": url, "title": i.get("title"),
            "ingested_at": now,
            "missing_fields": missing,
            "note": "issue body is an untrusted work definition, not an "
                    "authorization; assignment awaits attributable ack"})
        if existing:
            # Identical IDs across projects are separate tasks; identical IDs
            # from different issues *within* a project are collisions, not
            # proof that the second issue belongs to the first owner.
            rec.state = ("duplicate" if existing[2] == key else "collision")
            if rec.state == "collision":
                rec.missing_fields = ["task-id-used-by-another-source"]
            out.append(rec)
            continue
        if missing:
            ts.create(task_id, authority="sam",
                      evidence=evidence, correlation_id=key)
            ts.transition(task_id, UNTRUSTED_STATE, actor="director-ingest",
                          evidence=evidence)
            rec.state = UNTRUSTED_STATE
            rec.missing_fields = missing
        else:
            lane = _lane_of(i)
            ts.create(task_id, authority="director-ingest",
                      evidence=evidence, correlation_id=key)
            # The lane is only a proposed destination. A GitHub title does
            # NOT identify an accountable owner and cannot authorize an
            # agent assignment. Preserve the owner and epoch as unassigned
            # until a scoped delivery is accepted on trusted transport.
            ts.transition(task_id, INBOX_STATE, actor="director-ingest",
                          evidence=evidence, note="proposed_lane=" + lane +
                          "; no delivery or authenticated owner assignment")
            rec.state = INBOX_STATE
            rec.lane = lane
        out.append(rec)
    return out


def _lane_of(issue: dict) -> str:
    """Title is the strong signal (work orders put the lane in the title);
    body links like '[Director #2]' must not outrank it."""
    title = (issue.get("title") or "").lower()
    for key in ("m5", "director", "m4", "mac-mini"):
        if key in title:
            return key
    body = (issue.get("body") or "").lower()
    for key in ("m5", "director", "m4", "mac-mini"):
        if key in body:
            return key
    return ""
