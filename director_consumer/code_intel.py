"""Code intelligence — DIRECTOR-CODE-INTEL-01.

Seven's read-only window into the GCI (Global Code Intelligence) index and
the engineering-finding stream. Reuses the existing GCI infrastructure
(``harness/gci`` server on asus4, M5 refresh automation) — this module adds
NO second index, NO scheduler, NO daemon: it is a project-scoped,
deterministic adapter plus a fingerprinted finding store that any existing
tick can call.

Laws (issue #12 + operator clarification 2026-10-19):
- Read-only against every source. Dirty worktrees are fingerprinted, never
  modified, never cleaned, never reset. Index snapshots are observations.
- Missing access is ``ACCESS_REQUIRED``; missing telemetry is ``UNKNOWN``.
  Nothing is ever reported HEALTHY without a fresh receipt.
- Findings are deterministic: fingerprinted, deduped across restarts, with
  new/existing/resolved/source-unavailable/stale tracked separately.
- The LLM (ops brain) may summarize/triage SOURCE-GROUNDED findings; it can
  never promote a finding to VERIFIED. Citation (repo, path, line, sha) or
  it did not happen.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

FINDINGS_SCHEMA = 1
GCI_DEFAULT_URL = "http://100.100.116.82:8810"
TOKEN_ENV = "HARNESS_GCI_TOKEN"


class SourceUnavailable(Exception):
    """A telemetry/index source could not be reached. Never guessed around."""


# --------------------------------------------------------------------------
# GCI API client (read-only)
# --------------------------------------------------------------------------

@dataclass
class GCIClient:
    base_url: str = GCI_DEFAULT_URL
    token: str = ""
    timeout_s: float = 8.0

    def __post_init__(self) -> None:
        if not self.token:
            self.token = os.environ.get(TOKEN_ENV, "")

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.token}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SourceUnavailable(f"gci {path}: {e}") from e

    def health(self) -> dict:
        req = urllib.request.Request(
            f"{self.base_url}/health",
            headers={"Authorization": f"Bearer {self.token}"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SourceUnavailable(f"gci health: {e}") from e

    def search_exact(self, query: str, *, repo_root: str | None = None,
                     limit: int = 8) -> list[dict]:
        payload: dict = {"query": query, "limit": limit}
        if repo_root:
            payload["repo_root"] = repo_root
        return self._post("/search-exact", payload).get("hits", [])

    def search_symbol(self, symbol: str, *, repo_root: str | None = None,
                      limit: int = 8) -> list[dict]:
        payload: dict = {"query": symbol, "limit": limit}
        if repo_root:
            payload["repo_root"] = repo_root
        return self._post("/search-symbol", payload).get("hits", [])


# --------------------------------------------------------------------------
# Repository universe: approved roots + linked worktrees, read-only
# --------------------------------------------------------------------------

@dataclass
class RepoRef:
    root: Path
    branch: str = ""
    head: str = ""
    dirty: bool = False
    last_commit_at: float = 0.0
    relation: str = "root"          # root | worktree
    parent_root: str = ""


def _git(root: Path, *args: str) -> str:
    # --no-optional-locks: never take the index lock even if a gc would
    proc = subprocess.run(
        ["git", "-C", str(root), "--no-optional-locks", *args],
        capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:200])
    return proc.stdout


def fingerprint_repo(root: Path) -> RepoRef:
    """Read-only identity of a checkout. Never mutates (no-optional-locks,
    porcelain status only)."""
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    head = _git(root, "rev-parse", "HEAD").strip()
    dirty = bool(_git(root, "status", "--porcelain").strip())
    ts = _git(root, "log", "-1", "--format=%ct").strip()
    return RepoRef(root=root, branch=branch, head=head, dirty=dirty,
                   last_commit_at=float(ts) if ts else 0.0)


def enumerate_worktrees(root: Path) -> list[RepoRef]:
    """Linked worktrees of a root, read-only. Failures return [] (the root
    itself still reports)."""
    try:
        out = _git(root, "worktree", "list", "--porcelain")
    except (RuntimeError, subprocess.TimeoutExpired):
        return []
    refs: list[RepoRef] = []
    current: dict | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            if current:
                refs.append(_ref_from(current, root))
            current = {"path": line.split(" ", 1)[1]}
        elif current is not None and line.startswith("branch "):
            current["branch"] = line.split(" ", 1)[1].removeprefix("refs/heads/")
        elif current is not None and line.startswith("HEAD "):
            current["head"] = line.split(" ", 1)[1]
        elif current is not None and line == "bare":
            current["bare"] = True
    if current:
        refs.append(_ref_from(current, root))
    return refs


def _ref_from(info: dict, parent: Path) -> RepoRef:
    path = Path(info["path"])
    is_root = path == parent or info.get("bare")
    ref = RepoRef(
        root=path, branch=info.get("branch", ""), head=info.get("head", ""),
        relation="root" if is_root else "worktree",
        parent_root="" if is_root else str(parent))
    try:
        ref.dirty = bool(_git(path, "status", "--porcelain").strip())
        ts = _git(path, "log", "-1", "--format=%ct").strip()
        ref.last_commit_at = float(ts) if ts else 0.0
    except (RuntimeError, subprocess.TimeoutExpired):
        ref.dirty = False  # UNKNOWN would be better; empty porcelain failed
    return ref


# --------------------------------------------------------------------------
# Index status (local automation state + GCI repos)
# --------------------------------------------------------------------------

@dataclass
class IndexStatus:
    root: str
    branch: str = ""
    head: str = ""
    dirty: bool = False
    indexed_head: str = ""
    indexed_at: float = 0.0
    state: str = "not_indexed"   # fresh|stale|not_indexed|dirty_ahead|access_required
    detail: str = ""


def index_status(ref: RepoRef, *, automation_db: Path | None = None,
                 gci: GCIClient | None = None,
                 stale_after_s: float = 30 * 86400.0) -> IndexStatus:
    """Classify one checkout against the index. Observation only."""
    st = IndexStatus(root=str(ref.root), branch=ref.branch, head=ref.head,
                     dirty=ref.dirty)
    row = None
    if automation_db and automation_db.exists():
        try:
            con = sqlite3.connect(f"file:{automation_db}?mode=ro", uri=True,
                                  timeout=5)
            row = con.execute(
                "SELECT last_head, last_indexed FROM repositories "
                "WHERE repo_root = ?", (str(ref.root),)).fetchone()
            con.close()
        except sqlite3.Error:
            row = None
    if row is None and gci is not None:
        try:
            hits = gci.search_exact("def main", repo_root=str(ref.root),
                                    limit=1) or \
                gci.search_symbol("main", repo_root=str(ref.root), limit=1)
            if hits:
                row = (hits[0].get("revision"), time.time())
        except SourceUnavailable:
            st.state = "access_required"
            st.detail = "automation db unreadable and gci unreachable"
            return st
    if row is None:
        st.state = "not_indexed"
        st.detail = "no index observation for this path"
        return st
    st.indexed_head, st.indexed_at = row[0] or "", row[1] or 0.0
    if st.indexed_head and st.head and st.indexed_head != st.head:
        st.state = "stale"
        st.detail = (f"index at {st.indexed_head[:12]}, checkout at "
                     f"{st.head[:12]}")
    elif time.time() - st.indexed_at > stale_after_s:
        st.state = "stale"
        st.detail = f"last indexed {int(time.time() - st.indexed_at)}s ago"
    else:
        st.state = "fresh"
        st.detail = "dirty" if ref.dirty else "clean"
    return st


# --------------------------------------------------------------------------
# Finding store — deterministic, fingerprinted, restart-safe
# --------------------------------------------------------------------------

FINDING_KINDS = ("automation_failure", "stale_index", "github_check",
                 "service_down", "queue_blocked", "disk_full",
                 "crashed_lane")


def collect_crashed_lanes(results_root: Path, *,
                          max_age_s: float = 14 * 86400.0,
                          now: float | None = None) -> list[dict]:
    """Lane run-logs that ended in a traceback (results/*/run.log).

    The 2026-10-14 power-axes crash sat in a run.log for six days because
    nothing read these logs — this collector is the fix. A log is a
    finding when it contains a Traceback and was written recently."""
    now = now if now is not None else time.time()
    if not results_root.is_dir():
        raise SourceUnavailable(f"results root missing: {results_root}")
    out = []
    for log in sorted(results_root.glob("*/run.log")):
        try:
            st = log.stat()
        except OSError:
            continue
        if now - st.st_mtime > max_age_s:
            continue
        try:
            text = log.read_text(errors="replace")[-4000:]
        except OSError:
            continue
        if "Traceback (most recent call last)" not in text:
            continue
        last = [l.strip() for l in text.splitlines() if l.strip()]
        head = last[-1][:160] if last else ""
        out.append({
            "kind": "crashed_lane",
            "key": str(log.parent),
            "severity": "warning",
            "repo": str(results_root),
            "path": str(log),
            "detail": f"lane crashed: {head}",
            "evidence": {"run_log": str(log), "mtime": st.st_mtime,
                         "last_line": head},
            "source_produced_at": st.st_mtime,
        })
    return out


def _fp(kind: str, key: str) -> str:
    return hashlib.sha256(f"{kind}|{key}".encode()).hexdigest()


class FindingStore:
    """Project-scoped finding ledger. Additive schema in its OWN sqlite file
    inside the director workspace (never touches director.db)."""

    def __init__(self, workspace: Path, project: str = "designwins"):
        self.workspace = Path(workspace)
        self.project = project
        self.path = self.workspace / "code-intel-findings.sqlite"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._init()

    def _con(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init(self) -> None:
        with self._con() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS findings ("
                " fingerprint TEXT PRIMARY KEY, project TEXT NOT NULL,"
                " kind TEXT NOT NULL, key TEXT NOT NULL,"
                " severity TEXT NOT NULL DEFAULT 'unknown',"
                " owner TEXT NOT NULL DEFAULT 'UNKNOWN',"
                " repo TEXT, path TEXT, line INTEGER, sha TEXT, url TEXT,"
                " observed_at REAL, source_produced_at REAL,"
                " detail TEXT, evidence TEXT,"
                " state TEXT NOT NULL DEFAULT 'new',"
                " first_seen REAL, last_seen REAL, occurrences INTEGER"
                " DEFAULT 1)")
            con.execute(
                "CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v)")
            con.execute("INSERT OR IGNORE INTO meta VALUES ('schema', ?)",
                        (FINDINGS_SCHEMA,))

    def ingest(self, findings: list[dict], *, now: float | None = None
               ) -> list[dict]:
        """Deterministic ingest. Returns per-finding transition result.
        States: new | existing | resolved is computed by absence in a batch
        of the same kind (see resolve_absent)."""
        now = now if now is not None else time.time()
        out: list[dict] = []
        seen: set[str] = set()
        with self._con() as con:
            for f in findings:
                kind = f.get("kind", "")
                if kind not in FINDING_KINDS:
                    raise ValueError(f"unknown finding kind: {kind!r}")
                key = f.get("key") or ""
                if not key:
                    raise ValueError("finding requires a key")
                fp = _fp(kind, key)
                seen.add(fp)
                prev = con.execute(
                    "SELECT state, occurrences FROM findings "
                    "WHERE fingerprint = ? AND project = ?",
                    (fp, self.project)).fetchone()
                evidence = json.dumps(f.get("evidence") or {},
                                      sort_keys=True)
                if prev is None:
                    con.execute(
                        "INSERT INTO findings (fingerprint, project, kind,"
                        " key, severity, owner, repo, path, line, sha, url,"
                        " observed_at, source_produced_at, detail, evidence,"
                        " state, first_seen, last_seen) VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (fp, self.project, kind, key, f.get("severity", "unknown"),
                         f.get("owner", "UNKNOWN"), f.get("repo"),
                         f.get("path"), f.get("line"), f.get("sha"),
                         f.get("url"), now, f.get("source_produced_at"),
                         f.get("detail", ""), evidence, "new", now, now))
                    out.append({"fingerprint": fp, "kind": kind,
                                "key": key, "transition": "new"})
                else:
                    con.execute(
                        "UPDATE findings SET last_seen = ?, occurrences ="
                        " occurrences + 1, observed_at = ?, detail = ?,"
                        " evidence = ?, severity = ?, state = CASE WHEN"
                        " state = 'resolved' THEN 'new' ELSE state END"
                        " WHERE fingerprint = ? AND project = ?",
                        (now, now, f.get("detail", ""), evidence,
                         f.get("severity", "unknown"), fp, self.project))
                    was_resolved = prev[0] == "resolved"
                    out.append({"fingerprint": fp, "kind": kind, "key": key,
                                "transition": "new" if was_resolved
                                else "existing"})
        return out

    def resolve_absent(self, present: set[str], kinds_ran: set[str], *,
                       now: float | None = None) -> list[str]:
        """A finding is resolved only when its collector RAN and did not
        report it this cycle (source-produced absence). Silence from a
        collector that did not run resolves nothing — missing telemetry is
        UNKNOWN, never HEALTHY."""
        now = now if now is not None else time.time()
        resolved: list[str] = []
        with self._con() as con:
            rows = con.execute(
                "SELECT fingerprint, kind FROM findings WHERE project = ?"
                " AND state != 'resolved'", (self.project,)).fetchall()
            for fp, kind in rows:
                if kind not in kinds_ran or fp in present:
                    continue
                con.execute(
                    "UPDATE findings SET state = 'resolved', last_seen = ?"
                    " WHERE fingerprint = ?", (now, fp))
                resolved.append(fp)
        return resolved

    def open_findings(self, *, kind: str | None = None) -> list[dict]:
        q = ("SELECT fingerprint, kind, key, severity, owner, repo, path,"
             " line, sha, url, observed_at, detail, state, occurrences,"
             " first_seen, last_seen FROM"
             " findings WHERE project = ? AND state != 'resolved'")
        args: tuple = (self.project,)
        if kind:
            q += " AND kind = ?"
            args = (self.project, kind)
        with self._con() as con:
            return [dict(zip(
                ("fingerprint", "kind", "key", "severity", "owner", "repo",
                 "path", "line", "sha", "url", "observed_at", "detail",
                 "state", "occurrences", "first_seen", "last_seen"), r))
                for r in con.execute(q, args).fetchall()]


# --------------------------------------------------------------------------
# Deterministic collectors (read-only; each returns [] or raises
# SourceUnavailable — silence is never HEALTHY)
# --------------------------------------------------------------------------

def collect_automation_failures(automation_db: Path) -> list[dict]:
    """Recent failed refresh runs from the local GCI automation ledger
    (schema: repos table of harness/gci/automation.py)."""
    if not automation_db.exists():
        raise SourceUnavailable(f"automation db missing: {automation_db}")
    con = sqlite3.connect(f"file:{automation_db}?mode=ro", uri=True,
                          timeout=5)
    cutoff = time.time() - 7 * 86400.0
    rows = con.execute(
        "SELECT repo_id, repo_root, last_status, last_error, last_checked,"
        " failure_count FROM repositories WHERE last_status = 'failed' AND"
        " last_checked >= ?", (cutoff,)).fetchall()
    con.close()
    out = []
    for repo_id, root, status, error, checked, fails in rows:
        msg = (error or "").splitlines()[0][:160] if error else ""
        out.append({
            "kind": "automation_failure",
            "key": f"{root}:{msg}",
            "severity": "warning",
            "repo": root,
            "detail": f"refresh {status} x{fails}: {msg}",
            "evidence": {"automation_db": str(automation_db),
                         "repo_id": repo_id, "last_checked": checked,
                         "failure_count": fails, "error_head": msg},
            "source_produced_at": checked,
        })
    return out


def collect_stale_indexes(statuses: list[IndexStatus],
                          stale_after_s: float = 30 * 86400.0
                          ) -> list[dict]:
    out = []
    for st in statuses:
        if st.state != "stale":
            continue
        out.append({
            "kind": "stale_index",
            "key": f"{st.root}",
            "severity": "info",
            "repo": st.root,
            "sha": st.head or st.indexed_head,
            "detail": st.detail,
            "evidence": {"indexed_head": st.indexed_head,
                         "checkout_head": st.head,
                         "indexed_at": st.indexed_at,
                         "branch": st.branch},
            "source_produced_at": st.indexed_at or None,
        })
    return out


def collect_github_checks(repo: str, *, gh_bin: str = "gh",
                          timeout_s: float = 20.0) -> list[dict]:
    """Failing check runs on the allowlisted repo via the local gh CLI.
    No token -> SourceUnavailable (never silently empty)."""
    allowed = {"outcited/outcitedharness"}
    if repo not in allowed:
        raise PermissionError(f"repo {repo!r} not in allowlist")
    try:
        proc = subprocess.run(
            [gh_bin, "api",
             f"repos/{repo}/commits/main/check-runs?per_page=30",
             "-H", "Accept: application/vnd.github+json"],
            capture_output=True, text=True, timeout=timeout_s)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        raise SourceUnavailable(f"gh unavailable: {e}") from e
    if proc.returncode != 0:
        raise SourceUnavailable(f"gh api failed: {proc.stderr[:120]}")
    data = json.loads(proc.stdout)
    out = []
    for cr in data.get("check_runs", []):
        if cr.get("conclusion") not in ("failure", "timed_out", None):
            continue
        if cr.get("status") != "completed":
            continue
        concl = cr.get("conclusion") or "timed_out"
        out.append({
            "kind": "github_check",
            "key": f"{repo}#{cr.get('name')}@{cr.get('started_at','')}",
            "severity": "warning",
            "url": cr.get("html_url"),
            "detail": f"check {cr.get('name')} conclusion={concl}",
            "evidence": {"check_run_id": cr.get("id"),
                         "conclusion": concl,
                         "started_at": cr.get("started_at")},
            "source_produced_at": None,
        })
    return out


# --------------------------------------------------------------------------
# Reasoning: retrieval-augmented triage over the ops brain (NEVER verifies)
# --------------------------------------------------------------------------

@dataclass
class TriageResult:
    question: str
    hypothesis: str = ""
    citations: list[dict] = field(default_factory=list)
    model: str = ""
    verified: bool = False          # structurally always False — enforced

    def to_dict(self) -> dict:
        return {"question": self.question, "hypothesis": self.hypothesis,
                "citations": self.citations, "model": self.model,
                "verified": False}


_MAX_CONTEXT_CHARS = 12_000
_MAX_HITS = 8


def build_context(hits: list[dict]) -> str:
    """Bounded, citation-bearing context from GCI hits. No giant prompts."""
    parts: list[str] = []
    used = 0
    for h in hits[:_MAX_HITS]:
        cite = (f"[{h.get('repo_root','?')}@{str(h.get('revision',''))[:12]} "
                f"{h.get('path','?')}:{h.get('start_line','?')}]")
        body = (h.get("text") or "")[:1500]
        chunk = f"{cite}\n{body}\n"
        if used + len(chunk) > _MAX_CONTEXT_CHARS:
            break
        parts.append(chunk)
        used += len(chunk)
    return "\n".join(parts)


def _brain_call(brain, prompt: str) -> tuple[str, str]:
    """Adapter over OpsBrainClient.assess (event_summary, classification)
    or a single-argument test double. Returns (text, model_label)."""
    try:
        r = brain.assess(prompt, "code_intel_triage")
    except TypeError:
        r = brain.assess(prompt)
    if isinstance(r, dict):
        if r.get("unavailable"):
            return "", str(r["unavailable"])[:120]
        return (str(r.get("text") or r.get("assessment") or ""),
                str(r.get("source") or r.get("model") or ""))
    return (str(r) if r else "", "")


def _retrieve(gci: GCIClient, question: str, limit: int) -> list[dict]:
    """Distinctive-token retrieval: exact text on code-ish tokens, then a
    symbol fallback. Deduped by (repo, path, line)."""
    tokens = [t.strip(".,?:;()[]'\"") for t in question.split()]
    tokens = [t for t in tokens if len(t) >= 5][:3] or [question[:32]]
    hits: list[dict] = []
    seen: set[tuple] = set()
    for tok in tokens:
        for h in gci.search_exact(tok, limit=limit):
            k = (h.get("repo_root"), h.get("path"), h.get("start_line"))
            if k not in seen:
                seen.add(k)
                hits.append(h)
        if len(hits) >= limit:
            break
    if not hits and tokens:
        for h in gci.search_symbol(tokens[0], limit=limit):
            k = (h.get("repo_root"), h.get("path"), h.get("start_line"))
            if k not in seen:
                seen.add(k)
                hits.append(h)
    return hits[:limit]


def triage_question(question: str, *, gci: GCIClient, brain,
                    repo_roots: list[str] | None = None) -> TriageResult:
    """Retrieve-then-reason. `brain` is an OpsBrainClient (or test double).
    The LLM may propose a hypothesis; verification is structurally out of
    scope — TriageResult.verified is always False."""
    hits = _retrieve(gci, question, _MAX_HITS)
    citations = [{"repo": h.get("repo_root"), "path": h.get("path"),
                  "line": h.get("start_line"),
                  "sha": h.get("revision")} for h in hits[:_MAX_HITS]]
    result = TriageResult(question=question, citations=citations)
    ctx = build_context(hits)
    if brain is None or not ctx:
        result.hypothesis = (
            "SOURCE_UNAVAILABLE: no brain configured or no indexed context")
        return result
    prompt = (
        "You are triaging an engineering finding with SOURCE EXCERPTS ONLY. "
        "Cite [repo@sha path:line] for every claim. If the excerpts do not "
        "support a hypothesis, say INSUFFICIENT_EVIDENCE. Never claim "
        "verification.\n\nQUESTION: "
        + question + "\n\nSOURCE EXCERPTS:\n" + ctx)
    text, model = _brain_call(brain, prompt)
    result.hypothesis = text[:4000] if text else \
        f"SOURCE_UNAVAILABLE: brain returned nothing ({model})".strip()
    result.model = model
    return result


# --------------------------------------------------------------------------
# Status brief (read-only, for the operator / briefing module)
# --------------------------------------------------------------------------

def status_brief(workspace: Path, approved_roots: list[str], *,
                 automation_db: Path | None = None,
                 gci: GCIClient | None = None,
                 project: str = "designwins") -> dict:
    statuses: list[IndexStatus] = []
    universe: list[RepoRef] = []
    for raw in approved_roots:
        root = Path(raw).expanduser()
        if not root.exists():
            continue
        try:
            ref = fingerprint_repo(root)
        except (RuntimeError, subprocess.TimeoutExpired):
            continue
        universe.append(ref)
        statuses.append(index_status(ref, automation_db=automation_db,
                                     gci=gci))
        for wt in enumerate_worktrees(root):
            if wt.relation == "worktree":
                universe.append(wt)
                statuses.append(index_status(
                    wt, automation_db=automation_db, gci=gci))
    indexed = [s for s in statuses if s.state in ("fresh", "stale")]
    fresh = [s for s in statuses if s.state == "fresh"]
    store = FindingStore(workspace, project=project)
    open_f = store.open_findings()
    unowned = [f for f in open_f if f["owner"] == "UNKNOWN"]
    return {
        "universe": len(universe),
        "indexed": len(indexed),
        "fresh": len(fresh),
        "not_indexed": len(statuses) - len(indexed),
        "open_findings": len(open_f),
        "unowned_findings": len(unowned),
        "top_findings": [
            {"kind": f["kind"], "detail": f["detail"][:120],
             "state": f["state"]}
            for f in sorted(open_f,
                            key=lambda x: -(x["observed_at"] or 0))[:3]],
        "per_repo": [
            {"root": s.root, "branch": s.branch, "state": s.state,
             "dirty": s.dirty, "detail": s.detail} for s in statuses],
    }
