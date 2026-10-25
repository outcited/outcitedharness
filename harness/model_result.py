"""Fail-closed model result contract (P5).

A model call that FAILED is not the same claim as a model call that found
nothing. Before this module every caller collapsed both to ``[]``, so a bad
JSON parse, a truncated completion, or an exhausted retry budget scored
identically to a legitimate empty extraction.

Status vocabulary:

* ``OK``     — the model answered and returned one or more rows.
* ``EMPTY``  — the model answered with a parseable, empty array.
* ``FAILED`` — no trustworthy answer (network error, truncated output,
               unparseable JSON, non-array payload). Never scored as zero;
               it is a refusal and needs retry/operator attention.

``rows`` is only meaningful for ``OK``/``EMPTY``. No bracket repair is
performed: appending ``]`` to a truncated array fabricates structure.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

OK = "OK"
EMPTY = "EMPTY"
FAILED = "FAILED"
STATUSES = frozenset({OK, EMPTY, FAILED})

_EXCERPT_CHARS = 500
_UNSET = object()


def _excerpt(text: str) -> str:
    text = text or ""
    if len(text) <= _EXCERPT_CHARS:
        return text
    return text[:_EXCERPT_CHARS] + "...[truncated]"


@dataclass(frozen=True)
class ModelResult:
    """The outcome of one model call, explicit about failure."""

    status: str
    rows: list[Any] = field(default_factory=list)
    reason: str | None = None
    attempts: int = 0
    raw_excerpt: str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown model-result status: {self.status!r}")
        if self.status == OK and not self.rows:
            raise ValueError("OK requires at least one row; use EMPTY")
        if self.status == EMPTY and self.rows:
            raise ValueError("EMPTY must not carry rows; use OK")
        if self.status == FAILED and not self.reason:
            raise ValueError("FAILED requires a reason")

    @property
    def ok(self) -> bool:
        return self.status == OK

    @property
    def failed(self) -> bool:
        return self.status == FAILED

    def as_receipt(self) -> dict[str, Any]:
        """Compact, JSON-safe provenance for ledgers and reports."""
        return {
            "status": self.status,
            "reason": self.reason,
            "attempts": self.attempts,
            "rows": len(self.rows),
            "model": self.model,
            "raw_excerpt": self.raw_excerpt,
        }


def ok(rows: list[Any], *, attempts: int = 0, model: str | None = None) -> ModelResult:
    """Successful call: EMPTY when the model legitimately returned no rows."""
    if rows:
        return ModelResult(OK, list(rows), attempts=attempts, model=model)
    return ModelResult(EMPTY, [], attempts=attempts, model=model)


def failed(
    reason: str,
    *,
    attempts: int = 0,
    raw_excerpt: str | None = None,
    model: str | None = None,
) -> ModelResult:
    return ModelResult(
        FAILED,
        [],
        reason=reason,
        attempts=attempts,
        raw_excerpt=raw_excerpt,
        model=model,
    )


def parse_json_array(
    content: str | None,
    *,
    attempts: int = 0,
    finish_reason: str | None = None,
    model: str | None = None,
) -> ModelResult:
    """Parse a model reply that should contain a JSON array.

    Preserves the historical happy-path behaviour (first ``[`` .. last ``]``,
    DOTALL) but returns FAILED instead of ``[]`` when the reply cannot be
    trusted. ``finish_reason == "length"`` is surfaced as ``truncated_output``
    because a completion cut at the token budget is not evidence of absence.
    """
    text = content or ""
    if finish_reason == "length":
        return failed(
            "truncated_output",
            attempts=attempts,
            raw_excerpt=_excerpt(text),
            model=model,
        )
    stripped = text.strip()
    if not stripped:
        return failed(
            "empty_content",
            attempts=attempts,
            raw_excerpt=_excerpt(text),
            model=model,
        )
    # A whole-reply JSON value wins over span extraction: an object envelope
    # like {"rows": []} is not an array payload and must not be read as EMPTY.
    try:
        whole = json.loads(stripped)
    except json.JSONDecodeError:
        whole = _UNSET
    if whole is not _UNSET:
        if not isinstance(whole, list):
            return failed(
                f"not_a_list: {type(whole).__name__}",
                attempts=attempts,
                raw_excerpt=_excerpt(stripped),
                model=model,
            )
        return ok(whole, attempts=attempts, model=model)
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        reason = "unparseable_json: unterminated array" if "[" in stripped else "no_json_array"
        return failed(
            reason,
            attempts=attempts,
            raw_excerpt=_excerpt(text),
            model=model,
        )
    fragment = match.group(0)
    try:
        value = json.loads(fragment)
    except json.JSONDecodeError as exc:
        return failed(
            f"unparseable_json: {exc.msg} at char {exc.pos}",
            attempts=attempts,
            raw_excerpt=_excerpt(fragment),
            model=model,
        )
    if not isinstance(value, list):
        return failed(
            f"not_a_list: {type(value).__name__}",
            attempts=attempts,
            raw_excerpt=_excerpt(fragment),
            model=model,
        )
    return ok(value, attempts=attempts, model=model)
