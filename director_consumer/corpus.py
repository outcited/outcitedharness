"""Bounded institutional corpus indexer — DIRECTOR-06 §2.

Indexes project history into the retrieval index. Deliberately NOT the whole
disk: PRDs and decisions, approved policy and config history, engineering
requirements, git commit/PR outcomes, verification and test receipts, Director
decision packets, task/incident history, factory operations and experiment
reports, and approved playbooks.

Explicitly excluded: raw datasheet PDFs, production logs, secrets/credentials,
unrelated project content.

Evidence class is derived from the artifact's role and is never inferred from
its wording:
  authority      — PRD, approved decision, policy, architecture contract
  verified-fact  — receipt carrying measurements / test results
  assertion      — agent or owner report without an independent measurement
  hypothesis     — proposal, recommendation, plan not yet approved
  superseded     — explicitly marked older revision
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Iterable, Optional

from .memory import MemoryRecord, ProjectMemory

# An explicit declaration inside the document is the only filename-independent
# way to confer a class. Anything else is a hint, not evidence.
MARKER_RE = re.compile(r"^\s*evidence-class:\s*(authority|verified-fact)"
                       r"\s*$", re.I | re.M)
AUTHORITY_NAME_RE = re.compile(
    r"^(PRD-|DECISION-|ARCHORITY|POLICY|ARCHITECTURE-|CONTRACT-|"
    r"AUTHORIZATION-|ENGINEERING-REQUIREMENT-)", re.I)

SKIP_DIRS = {".git", "__pycache__", "node_modules", "datasets",
             "build", "dist", ".venv", ".litellm-venv", ".pytest_cache",
             "pdfs-incoming", "batches", "images", "renders",
             # bulk artefact dirs under results/ — receipts are .md/.json
             "blobs", "renders", "output-cloud", "output"}
SKIP_EXT = {".pdf", ".png", ".jpg", ".jpeg", ".pyc", ".so", ".bin", ".db",
            ".sqlite", ".sqlite3", ".zip", ".tar", ".gz", ".whl", ".jsonl",
            ".log", ".jsonl.tmp"}
SECRET_PAT = re.compile(r"(\.env|id_rsa|id_ed25519|authorized_keys|\.pem$|"
                        r"credential|secret|token|\.key$|password)", re.I)


def classify(path: Path, title: str, body: str = "") -> str:
    """Evidence class of a source. Rules (provenance law):

    - An explicit `evidence-class:` line inside the document is honoured.
    - A git commit message is ALWAYS an author assertion. Subject wording
      ("verified", "tests green") confers nothing.
    - Filenames never confer verified-fact: a file NAMED a receipt is a
      locator, not proof. Verification is conferred only by the Director
      verifying the artifact itself (index_experience with a
      verification_artifact) or an explicit marker.
    """
    if body:
        m = MARKER_RE.search(body[:4000])
        if m:
            return m.group(1).lower()
    if AUTHORITY_NAME_RE.match(path.name):
        return "authority"
    return "assertion"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def index_tree(mem: ProjectMemory, root: Path, project_id: str = "designwins",
               max_files: int = 4000, max_bytes: int = 2_000_000,
               actor: str = "") -> int:
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        in_results = "results" in Path(dirpath).parts
        for fn in filenames:
            if n >= max_files:
                return n
            p = Path(dirpath) / fn
            if p.suffix.lower() in SKIP_EXT:
                continue
            # inside results/ only receipts and small structured summaries
            # are institutional memory; raw rows/blobs are not
            if in_results and p.suffix.lower() not in (".md", ".json"):
                continue
            if SECRET_PAT.search(str(p)):
                continue
            if p.stat().st_size > max_bytes:
                continue
            try:
                body = p.read_text(errors="replace")
            except Exception:  # noqa: BLE001
                continue
            if not body.strip():
                continue
            title = _title(body, p)
            mem.index(MemoryRecord(
                project_id=project_id, source_id=f"file:{p.relative_to(root)}",
                locator=str(p), content_sha=_sha(body),
                source_type="document", title=title, body=body[:20000],
                event_ts=p.stat().st_mtime, indexed_ts=time.time(),
                actor=actor or "indexer",
                evidence_class=classify(p, title, body),
                source_available="available",
                source_freshness=time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                               time.gmtime(p.stat().st_mtime))))
            n += 1
    return n


def _title(body: str, p: Path) -> str:
    for line in body.splitlines()[:12]:
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("# ").strip()
    return p.stem


def index_git_history(mem: ProjectMemory, repo: Path,
                      project_id: str = "designwins",
                      max_commits: int = 600) -> int:
    """Commit messages + changed files. Verified only where a receipt exists;
    a message that claims a fix is an ASSERTION until the code is inspected."""
    n = 0
    try:
        # --all: disconnected branches and worktrees contain the actual work;
        # plain `git log` sees only the current branch and silently misses it.
        out = subprocess.run(
            ["git", "-C", str(repo), "log", "--all",
             f"--max-count={max_commits}",
             "--pretty=format:%H%n%an%n%ad%n%s%n%b%n---END---"],
            capture_output=True, text=True, timeout=120).stdout
    except Exception:  # noqa: BLE001
        return 0
    for block in out.split("---END---"):
        parts = [x for x in block.strip().splitlines() if x != ""]
        if len(parts) < 3:
            continue
        sha, author, date = parts[0], parts[1], parts[2]
        subject = parts[3] if len(parts) > 3 else ""
        body = "\n".join(parts[3:])
        # a commit message is an author claim, always — regardless of wording
        cls = "assertion"
        mem.index(MemoryRecord(
            project_id=project_id, source_id=f"commit:{sha[:12]}",
            locator=f"{repo}/commit/{sha}", content_sha=_sha(body),
            source_type="git-commit", title=subject[:160], body=body[:8000],
            event_ts=None, indexed_ts=time.time(), actor=author,
            evidence_class=cls, related_commit=sha[:12],
            source_available="available",
            source_freshness=date[:19]))
        n += 1
    return n


def index_experience(mem: ProjectMemory, exp: dict,
                     project_id: str = "designwins") -> int:
    """A verified engineering experience. Only called with outcome evidence;
    model-authored explanations must never arrive here as verified lessons."""
    title = exp.get("problem") or exp.get("lesson") or "experience"
    body = "\n".join(f"{k}: {v}" for k, v in exp.items() if v)
    cls = ("verified-fact" if exp.get("verification_artifact")
           else "hypothesis")
    mem.index(MemoryRecord(
        project_id=project_id,
        source_id=f"experience:{exp.get('id', _sha(body)[:12])}",
        locator=exp.get("locator", f"experience/{exp.get('id','')}"),
        content_sha=_sha(body), source_type="engineering-experience",
        title=str(title)[:160], body=body, indexed_ts=time.time(),
        actor=exp.get("accountable_owner", ""), evidence_class=cls,
        related_incident=exp.get("incident_id", ""),
        related_decision=exp.get("decision_id", ""),
        related_task=exp.get("task_id", ""),
        source_available="available"))
    return 1
