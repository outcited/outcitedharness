"""AgentInboxAdapter — canonical AgentMail INDEX.jsonl as a Director feed.

Pattern-faithful to SentinelAdapter: read-only, cursor = sha of consumed
index suffix, events = one per new mail entry. Deployed copy is imported by
consumer wiring (see feeds.yaml `agent_inbox`); this module is additive and
touches nothing existing.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

from .schema import Event, SourceRef, StateMarker, utcnow


class AgentInboxAdapter:
    """Canonical agent-inbox INDEX.jsonl: new entries become director events.

    Read-only. Cursor = "<last_line_no>:<sha16>" so appends are incremental
    and rewrites of old lines are detected (cursor mismatch re-emits only
    the tail after the last matching line number).
    """

    feed = "agent_inbox"

    def __init__(self, index: Path, boxes: Optional[list[str]] = None,
                 max_age_s: float = 7 * 86400, watch_all: bool = True):
        self.index = Path(index)
        self.boxes = set(boxes or [])
        self.max_age_s = max_age_s
        self.watch_all = watch_all

    def poll(self, cursor: str):
        if not self.index.exists():
            return [], [StateMarker("feed_missing", self.feed,
                                    f"INDEX.jsonl missing: {self.index}", utcnow())], cursor or "0:"
        lines = self.index.read_text(errors="replace").splitlines()
        entries = []
        for i, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append((i, json.loads(line)))
            except json.JSONDecodeError:
                continue
        last_no = 0
        m = re.match(r"^(\d+):", cursor or "")
        if m:
            last_no = int(m.group(1))
        events = []
        for no, e in entries:
            if no <= last_no:
                continue
            to = str(e.get("to", ""))
            frm = str(e.get("from", ""))
            if self.boxes and to not in self.boxes and not self.watch_all:
                continue
            subject = str(e.get("subject", ""))[:160]
            body = (f"[{e.get('type','mail')}] {frm} -> {to}: {subject} "
                    f"(id={e.get('id','?')}, path={e.get('path','?')})")
            ref = SourceRef(self.feed, f"mail#{e.get('id', no)}",
                            frm or None, utcnow(), locator=str(e.get("path", "")))
            ev = Event(self.feed, "agent_mail", body[:200], [ref])
            ev.attrs.update({"from": frm, "to": to, "mail_id": e.get("id"),
                             "type": e.get("type")})
            events.append(ev)
        sig = hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]
        return events, [], f"{len(lines)}:{sig}"
