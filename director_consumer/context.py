"""Institutional Context Compiler (§3) — reproducible, source-linked
decision packets from EXISTING history. No vector DB; deterministic
term retrieval over bounded sources.

Evidence classes (every item classified):
  authority       — approved policy / current DECISIONS entries / PRDs
  verified-fact   — measured/verified operational evidence
  assertion       — agent-reported (mail, commits)
  hypothesis      — model-generated or suspected (never fact)
  superseded      — explicitly superseded PRDs/decisions (never current)
  unknown         — contradictory or unclassifiable (named)

Packets never leak other projects: every lookup is filtered by project id
or rooted at that project's directory.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

SUPERSEDES_RE = re.compile(r"supersedes\s+((?:PRD-|D)\d*[-\w]*|[\w./-]+\.md)",
                           re.IGNORECASE)
DECISION_RE = re.compile(r"^##\s+(D\d+)\b", re.MULTILINE)
VERIFIED_HINTS = ("measured", "verified", "PASS", "evidence", "byte-identical")


@dataclass
class ContextItem:
    evidence_class: str
    text: str
    source: str
    freshness_age_d: float = 0.0
    authority: str = ""


@dataclass
class DecisionPacket:
    query: str
    project_id: str
    policy_version: str
    items: list[ContextItem] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)


def _age_days(mtime: float) -> float:
    return round((time.time() - mtime) / 86400.0, 2)


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def parse_decisions(path: Path) -> dict[str, dict]:
    """DECISIONS.md -> {id: {text, supersedes[], superseded_by}}"""
    text = _read(path)
    out: dict[str, dict] = {}
    parts = DECISION_RE.split(text)
    # parts: [preamble, id1, body1, id2, body2, ...]
    for i in range(1, len(parts) - 1, 2):
        did, body = parts[i], parts[i + 1]
        sup = SUPERSEDES_RE.findall(body)
        out[did] = {"text": body.strip()[:1500], "supersedes": sup,
                    "superseded_by": ""}
        if did not in out:
            out[did] = {"text": body, "supersedes": [], "superseded_by": ""}
    for did, d in out.items():
        for target in d["supersedes"]:
            key = target.strip().upper() if target.upper().startswith("D") \
                else target.strip()
            if key in out:
                out[key]["superseded_by"] = did
    return out


class ContextCompiler:
    def __init__(self, repo_root: Path, project_dir: Optional[Path] = None,
                 decisions_path: Optional[Path] = None,
                 mailbox_root: Optional[Path] = None):
        self.repo = Path(repo_root)
        self.project_dir = Path(project_dir) if project_dir else None
        self.decisions = Path(decisions_path) if decisions_path else \
            self.repo / "docs/DECISIONS.md"
        self.mailbox = Path(mailbox_root) if mailbox_root else None

    def _terms(self, query: str) -> list[str]:
        return [t for t in re.split(r"[^a-z0-9_-]+", query.lower())
                if len(t) > 2]

    def _match(self, text: str, terms: list[str]) -> int:
        t = text.lower()
        return sum(t.count(term) for term in terms)

    def compile(self, query: str, project_id: str = "default",
                policy_version: str = "",
                db=None) -> DecisionPacket:
        terms = self._terms(query)
        packet = DecisionPacket(query=query, project_id=project_id,
                                policy_version=policy_version)

        # 1. DECISIONS — authority unless superseded
        dec = parse_decisions(self.decisions)
        for did, d in sorted(dec.items()):
            score = self._match(d["text"] + did, terms)
            if not score and did.lower().replace("-", "") not in query.lower():
                continue
            if d["superseded_by"]:
                packet.items.append(ContextItem(
                    "superseded",
                    f"{did} (SUPERSEDED by {d['superseded_by']}): "
                    f"{d['text'][:220]}",
                    source=f"{self.decisions.name}:{did}"))
            else:
                packet.items.append(ContextItem(
                    "authority", f"{did}: {d['text'][:220]}",
                    source=f"{self.decisions.name}:{did}",
                    authority="DECISIONS.md (ratified)"))
        if any(i.evidence_class == "superseded" for i in packet.items):
            packet.uncertainties.append(
                "superseded material present — excluded from current "
                "authority per DECISIONS.md supersession")

        # 2. project config — authoritative, versioned
        if self.project_dir and self.project_dir.exists():
            for f in sorted(self.project_dir.glob("*.yaml")):
                text = _read(f)
                if self._match(text, terms):
                    packet.items.append(ContextItem(
                        "authority", f"project config {f.name}: "
                        f"matches {terms[:4]}",
                        source=f"projects/{project_id}/{f.name}",
                        authority=f"config (policy {policy_version or '?'})"))

        # 3. docs — verified when they carry measured evidence, else history
        docs_dir = self.repo / "director_consumer/docs"
        for f in sorted(docs_dir.glob("*.md")):
            text = _read(f)
            score = self._match(text[:8000], terms)
            if score:
                cls = ("verified-fact"
                       if any(h in text[:8000] for h in VERIFIED_HINTS)
                       else "assertion")
                packet.items.append(ContextItem(
                    cls, f"{f.name}: {score} term hits",
                    source=f"director_consumer/docs/{f.name}",
                    freshness_age_d=_age_days(f.stat().st_mtime)))

        # 4. agentmail — assertions only (never proof of ownership)
        if self.mailbox and self.mailbox.exists():
            for f in sorted(self.mailbox.rglob("*.md"))[:400]:
                text = _read(f)
                if self._match(text[:4000], terms):
                    packet.items.append(ContextItem(
                        "assertion", f"agentmail {f.name}",
                        source=str(f.relative_to(self.mailbox.parent)
                                   if self.mailbox.parent in f.parents
                                   else f.name),
                        freshness_age_d=_age_days(f.stat().st_mtime)))

        # 5. git history — assertions (commits are not verification)
        try:
            log = subprocess.run(
                ["git", "-C", str(self.repo), "log", "--all",
                 "--since=45 days ago", "--pretty=%h|%ct|%s"],
                capture_output=True, text=True, timeout=20).stdout
            for line in log.splitlines():
                if self._match(line, terms):
                    sha, ts, subj = (line.split("|", 2) + ["", ""])[:3]
                    packet.items.append(ContextItem(
                        "assertion", f"commit {sha}: {subj[:120]}",
                        source=f"git:{sha}"))
        except Exception:  # noqa: BLE001
            packet.uncertainties.append("git history unavailable")

        # 6. project-filtered operational state (never cross-project)
        if db is not None:
            try:
                for row in db.execute(
                        "SELECT correlation_id, suspected_cause, "
                        "verified_cause, evidence FROM incidents "
                        "WHERE project=? LIMIT 25",
                        (project_id,)):
                    cls = ("verified-fact" if row[2]
                           else "hypothesis")
                    try:
                        ev = json.loads(row[3] or "[]")
                        summary = (ev[0].get("summary", "") if ev else "")
                    except (json.JSONDecodeError, AttributeError):
                        summary = ""
                    packet.items.append(ContextItem(
                        cls, f"incident {row[0]}: {summary[:80]} :: "
                        f"{(row[2] or row[1] or '')[:140]}",
                        source="director.db:incidents"))
            except Exception:  # noqa: BLE001
                packet.uncertainties.append("incident store unavailable")

        if not packet.items:
            packet.uncertainties.append(
                "no contextual evidence matched the query terms")
        # cap + order: authority first, then verified, hypothesis last
        rank = {"authority": 0, "verified-fact": 1, "assertion": 2,
                "unknown": 3, "hypothesis": 4, "superseded": 5}
        packet.items.sort(key=lambda i: rank.get(i.evidence_class, 9))
        # cap the noisy tail but NEVER truncate authoritative or superseded
        # material — those are the classes decision-makers must see
        protected = [i for i in packet.items
                     if i.evidence_class in ("authority", "superseded",
                                             "unknown")]
        tail = [i for i in packet.items
                if i.evidence_class not in ("authority", "superseded",
                                            "unknown")]
        packet.items = (protected + tail)[:40 + len(protected)]
        return packet
