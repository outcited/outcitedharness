"""Proposed agentmail + escalation drafts — existing grammar, observe mode.

Filenames follow the fleet convention exactly:
    <YYYYMMDDTHHMMSSZ>_<from>_<to>_<type>_<subject-slug>.md
Types reuse the observed vocabulary: status, question, answer, directive,
handoff, work-brief. NOTHING IS DELIVERED: artifacts are written only under
the director workspace (outbox/ for agent mail, escalations/ for human
drafts) and carry an explicit `proposed: true` header.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from .schema import Assessment, Proposal
from .impact import authority_note

FROM_IDENTITY = "engineering-director"

SLUG_RE = re.compile(r"[^a-z0-9]+")


def slug(text: str, maxlen: int = 48) -> str:
    s = SLUG_RE.sub("-", text.lower()).strip("-")
    return (s[:maxlen].rstrip("-")) or "event"


def ts() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def evidence_lines(a: Assessment) -> str:
    out = []
    for r in a.evidence:
        line = f"- feed={r.feed} source_id={r.source_id}"
        if r.original_ts:
            line += f" ts={r.original_ts}"
        if r.locator:
            line += f" locator={r.locator}"
        out.append(line)
    return "\n".join(out) or "- (none)"


ACTION_TO_MAIL = {
    "propose_investigation": ("question", "investigate-meaning-drift"),
    "propose_backup_response": ("status", "backup-restore-needed"),
    "propose_status_note": ("status", None),          # subject from event
    "propose_handoff_status": ("handoff", None),
    "propose_park_review": ("question", "review-parked-docs"),
    "propose_release_nudge": ("status", "promotion-packet-awaiting-owner"),
}


def build_message(a: Assessment) -> Proposal | None:
    """Proposed agent mail for agent-authority actions. None => no message."""
    if a.authority == "sam" or a.action == "none":
        return None
    m = ACTION_TO_MAIL.get(a.action)
    if m is None:
        return None
    mtype, subject = m
    subject = subject or slug(a.event_summary)
    to = a.authority.removeprefix("agent:") if a.authority.startswith("agent:") \
        else "m5-opencode"
    name = f"{ts()}_{FROM_IDENTITY}_{to}_{mtype}_{subject}.md"
    body = f"""---
proposed: true
mode: observe
delivered: false
from: {FROM_IDENTITY}
to: {to}
type: {mtype}
trigger_rule: {a.rule_id}
severity: {a.severity}
deps_map_evidence: see assessment {a.trigger_key}
---

## {subject}

Event: {a.event_summary}

Classification: {a.severity} impacts={",".join(a.impacts)}
Rule: {a.rule_id} — {a.rationale.splitlines()[0] if a.rationale else ""}

Evidence:
{evidence_lines(a)}

(This message was generated in observation mode by the engineering-director
consumer. It has NOT been delivered. A human or a future granted phase must
explicitly approve delivery.)
"""
    return Proposal(ptype="message", trigger_key=a.trigger_key,
                    filename=name, body=body, authority=a.authority)


def build_escalation(a: Assessment) -> Proposal | None:
    """Human-escalation draft for sam-authority actions. Draft only."""
    if a.authority != "sam":
        return None
    note = authority_note(a.action) or "decision authority: human-only"
    name = f"{ts()}_{FROM_IDENTITY}_sam_escalation_{slug(a.event_summary)}.md"
    body = f"""---
proposed: true
mode: observe
delivered: false
type: escalation-draft
authority: sam
authority_rule: {note}
trigger_rule: {a.rule_id}
severity: {a.severity}
---

## Escalation draft — human decision required

Event: {a.event_summary}

Requested decision: {a.action}
Why human: {note}
Rule rationale: {a.rationale}

Evidence:
{evidence_lines(a)}

DECISIONS.md remains authoritative. No action has been taken. This draft
exists so a human can approve, edit, or reject it.
"""
    return Proposal(ptype="escalation", trigger_key=a.trigger_key,
                    filename=name, body=body, authority="sam")


def write_proposal(p: Proposal, workspace: Path) -> Path:
    sub = "escalations" if p.ptype == "escalation" else "outbox"
    d = Path(workspace) / sub
    d.mkdir(parents=True, exist_ok=True)
    # never overwrite: if the same filename exists (same second), suffix it
    target = d / p.filename
    i = 1
    while target.exists():
        target = d / f"{p.filename[:-3]}-{i}.md"
        i += 1
    target.write_text(p.body)
    return target
