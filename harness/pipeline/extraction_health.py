"""Fail-closed extraction failure ledger (hw-wk9).

Swallowed extractor exceptions must surface: every record() appends a
page + where + reason event, and artifacts carry a completeness flag so
a partial result is never indistinguishable from a genuine empty.
"""

from __future__ import annotations

from typing import Any


class ExtractionFailures:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, page: Any, where: str, reason: Any) -> None:
        self.events.append({"page": page, "where": where, "reason": str(reason)})

    def __len__(self) -> int:
        return len(self.events)

    @property
    def completeness(self) -> str:
        return "partial" if self.events else "complete"

    def summary(self) -> dict[str, Any]:
        return {
            "completeness": self.completeness,
            "failure_count": len(self.events),
            "failures": list(self.events),
        }


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Summary for a plain event list (e.g. cache round-trips)."""
    return {
        "completeness": "partial" if events else "complete",
        "failure_count": len(events),
        "failures": list(events),
    }


__all__ = ["ExtractionFailures", "summarize"]
