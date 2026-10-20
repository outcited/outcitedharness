"""DesignWins observation-scope inventory — issue #7 owner clarification.

Seven's visibility is CONFIG-DRIVEN, not a hardcoded repo list: the
repos registered in projects/designwins/project.yaml define the
observation scope. Adding a registered dependency to that file makes it
visible without code edits. This module separates:

  * OBSERVATION scope (config-driven, read-only facts, no TaskStore
    writes, no execution semantics) — every registered repo + host +
    lane + feed, with coverage status;
  * EXECUTION/ingest authority (gh_workloop.ALLOWLISTED_REPOS, reviewed
    separately) — unchanged by adding observation scope.

Coverage statuses are honest: OBSERVED | NOT_INGESTED (readable but
outside the execution allowlist — a fact, not an error) | UNKNOWN
(unreadable/missing access — an explicit coverage gap, never green).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from . import gh_workloop

# lane -> responsibilities. Mirrors projects/designwins/project.yaml
# ownership_boundary. Proposed lanes, NOT authenticated ownership
# (confirmed=false until an attributable ack exists).
DEFAULT_OWNERSHIP_BOUNDARY = {
    "m5": {"owns": ["extraction", "source-sha-page-quote-verification",
                    "truth-funnel", "evidence-correctness-at-handoff"],
           "confirmed": False},
    "cr-core": {"owns": ["release-pinned-store", "facade", "decision-api",
                         "authenticated-pdf-resolver"],
                "confirmed": False},
    "m4": {"owns": ["fe-client", "engineer-journeys",
                    "evidence-presentation"],
           "confirmed": False},
    "pdf-factory": {"owns": ["acquisition-to-receiver-receipts"],
                    "confirmed": False},
    "seven": {"owns": ["blockers", "interface-contract-reconciliation",
                       "integration-acceptance", "escalation"],
              "confirmed": False},
}

_FULL_NAME_RE = re.compile(
    r"(?:github\.com/)?([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def normalize_repo(entry: str) -> str:
    """'github.com/outcited/outcitedharness' -> 'outcited/outcitedharness'."""
    m = _FULL_NAME_RE.search((entry or "").strip())
    return m.group(1) if m else ""


@dataclass
class RepoCoverage:
    repo: str
    status: str                    # OBSERVED | NOT_INGESTED | UNKNOWN
    detail: str = ""
    open_issues: int = -1          # -1 = unknown, never zero-by-default
    execution_ingest: bool = False
    lanes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"repo": self.repo, "status": self.status,
                "detail": self.detail, "open_issues": self.open_issues,
                "execution_ingest": self.execution_ingest,
                "lanes": self.lanes}


def load_observation_scope(project_yaml: Path) -> list[str]:
    """Read the registered repositories from project config. This is the
    ONLY place observation scope grows — no code edit required."""
    d = yaml.safe_load(Path(project_yaml).read_text())
    out = []
    for r in d.get("repositories", []) or []:
        full = normalize_repo(str(r))
        if full and full not in out:
            out.append(full)
    return out


def coverage_report(project_yaml: Path,
                    readable: Optional[dict[str, bool]] = None,
                    open_issues: Optional[dict[str, int]] = None,
                    ingest_allowlist: tuple = gh_workloop.ALLOWLISTED_REPOS,
                    ) -> list[RepoCoverage]:
    """Classify every registered repo. `readable`/`open_issues` are the
    caller's measured facts (e.g. from read-only GitHub probes); a repo
    missing from `readable` is UNKNOWN — a coverage gap, never green."""
    readable = readable if readable is not None else {}
    open_issues = open_issues if open_issues is not None else {}
    out: list[RepoCoverage] = []
    for repo in load_observation_scope(project_yaml):
        can_read = readable.get(repo)
        if can_read is None:
            can_read = repo in open_issues   # counted issues = readable
        if can_read is True:
            status = ("OBSERVED" if repo in ingest_allowlist
                      else "NOT_INGESTED")
        else:
            status = "UNKNOWN"
        detail = {
            "OBSERVED": "readable AND in the reviewed execution "
                        "ingest allowlist",
            "NOT_INGESTED": "readable read-only; OUTSIDE the execution "
                            "ingest allowlist — observation only, issues "
                            "are facts, never executable assignments",
            "UNKNOWN": "not readable with current credentials — explicit "
                       "coverage gap",
        }[status]
        out.append(RepoCoverage(
            repo=repo, status=status, detail=detail,
            open_issues=open_issues.get(repo, -1),
            execution_ingest=repo in ingest_allowlist))
    return out


def lane_inventory(boundary: Optional[dict] = None) -> list[dict]:
    """Lane responsibilities with confirmed=false until authenticated
    ack exists (proposed lane is not ownership)."""
    b = boundary or DEFAULT_OWNERSHIP_BOUNDARY
    return [{"lane": lane, "owns": spec["owns"],
             "confirmed": spec.get("confirmed", False)}
            for lane, spec in sorted(b.items())]


def gap_matrix(coverage: list[RepoCoverage],
               hosts: Optional[dict[str, str]] = None) -> dict:
    """Compact coverage summary: what is observed vs exactly what access/
    data is missing. Missing data is listed, never silently dropped."""
    hosts = hosts or {}
    missing_repos = [c.repo for c in coverage if c.status == "UNKNOWN"]
    not_ingested = [c.repo for c in coverage
                    if c.status == "NOT_INGESTED"]
    unreachable_hosts = sorted(h for h, s in hosts.items()
                               if s in ("unknown", "unreachable", ""))
    return {
        "repos_observed": sum(1 for c in coverage
                              if c.status in ("OBSERVED", "NOT_INGESTED")),
        "repos_unknown_access": missing_repos,
        "repos_observation_only": not_ingested,
        "hosts_unreachable": unreachable_hosts,
        "note": "UNKNOWN/unreachable are explicit coverage gaps; the board "
                "shows them, never green",
    }
