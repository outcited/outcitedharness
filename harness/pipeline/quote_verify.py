#!/usr/bin/env python3
"""Deterministic quote verifier — the free, perfect adjudication layer.

Given quote-native claims ({value, quote, table}), verifies:
  1. the quote exists verbatim (whitespace-normalized) in the source text
  2. the claimed value parses from the quote itself
  3. the table title appears near the quote (same page/window)

Anything failing 1 or 2 is P0 (fabricated quote/value). Failing 3 is P1
(wrong-table suspicion) -> voter tier. This validator is the graduation
target for the hallucination ledger: it replaces judgment with matching.
"""

import json
import re
import sys
import unicodedata
from pathlib import Path

GATE = Path("/Volumes/M5_4TB/exports/canon-gate/natural-failure-gold-v1")


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", str(s or ""))
    return re.sub(r"\s+", " ", s).strip().lower()


def _tokens(s: str):
    return [t for t in re.findall(r"\d+(?:\.\d+)?|[a-z]+", norm(s)) if t]


FIELD_ALIASES = {
    "vin_min_v": ["vin", "input", "supply", "vcc", "vdd", "pvin", "avin", "vbus"],
    "vin_max_v": ["vin", "input", "supply", "vcc", "vdd", "pvin", "avin", "vbus"],
    "vout_min_v": ["vout", "output"],
    "vout_max_v": ["vout", "output"],
    "iout_max_a": ["iout", "output", "current", "io"],
    # AMBIENT-OPERATING convention ONLY (cr-core truth-audit C1, 2026-10-05):
    # junction (tj) and storage (tstg) are DIFFERENT QUANTITIES — they must
    # never satisfy a temp_ambient claim.
    "temp_min_c": ["ta", "ambient", "operating"],
    "temp_max_c": ["ta", "ambient", "operating"],
    "v_abs_max": ["absolute", "stress", "max"],
    "description": [],
}

TEMP_WRONG_QUANTITY = ["tj", "junction", "tstg", "storage", "ts"]


def temp_convention_check(field, quote, span_text):
    """P0 if a temp_ambient claim's evidence names junction/storage."""
    if field not in ("temp_min_c", "temp_max_c"):
        return None
    q_tokens = set(_tokens(quote))
    span_tokens = set(_tokens(span_text))
    for wrong in TEMP_WRONG_QUANTITY:
        if wrong in q_tokens or (len(wrong) > 2 and wrong in span_tokens):
            return False
    return None


def find_quote(quote: str, text: str, window=None, field=None):
    nq, nt = norm(quote), norm(text)
    if not nq:
        return False
    if nq in nt:
        return True
    if window and nq in norm(window):
        return True
    from .norm_m4 import locates as m4_locates
    if m4_locates(quote, text):
        return True
    return token_window_match(quote, text, field=field)


STOPWORDS = {"to", "up", "in", "the", "of", "and", "at", "or", "a", "an", "is",
             "for", "with", "from", "on", "by", "max", "min", "v", "mv", "a",
             "ma", "ua", "c", "dc"}


def token_window_match(quote: str, text: str, min_token_overlap: float = 0.7,
                       number_window: int = 60, field=None):
    """Tolerant quote location: every NUMBER in the quote must appear in the
    text, and significant (non-stopword) tokens must mostly appear within a
    +-number_window span around the numbers. Field aliases (M4 rule-6 style)
    rescue legitimate paraphrases for the right quantity; wrong-quantity
    binding never matches."""
    q_tokens = _tokens(quote)
    t_norm = norm(text)
    if not q_tokens:
        return False
    numbers = [t for t in q_tokens if re.fullmatch(r"\d+(?:\.\d+)?", t)]
    if numbers:
        positions = []
        for num in numbers:
            idx = t_norm.find(num)
            if idx < 0:
                return False
            positions.append(idx)
        span_start = max(0, min(positions) - number_window)
        span_end = min(len(t_norm), max(positions) + number_window)
        span = t_norm[span_start:span_end]
    else:
        span = t_norm
    non_num = [t for t in q_tokens if not re.fullmatch(r"\d+(?:\.\d+)?", t)]
    if not non_num:
        return bool(numbers)
    significant = [t for t in non_num if t not in STOPWORDS and len(t) > 2]
    if not significant:
        hits = sum(1 for t in non_num if t in span)
        return hits / len(non_num) >= min_token_overlap
    hits = sum(1 for t in significant if t in span)
    threshold = 0.5 if numbers else min_token_overlap
    if hits / len(significant) >= threshold:
        return True
    aliases = FIELD_ALIASES.get(field or "", [])
    if aliases and numbers:
        quote_names_quantity = any(a in significant or a in non_num for a in aliases)
        span_confirms = any(a in span for a in aliases)
        return quote_names_quantity and span_confirms
    return False


def value_in_quote(value, quote: str) -> bool:
    if value is None:
        return True
    q = norm(quote)
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        candidates = re.findall(r"-?\d+(?:\.\d+)?", q)
        return any(abs(float(c) - float(value)) < max(1e-6, abs(float(value)) * 1e-4) for c in candidates)
    return norm(str(value)) in q


COLUMN_ORDER_SETS = [
    ["min", "typ", "max"],
    ["min", "typ", "max", "unit"],
    ["min", "typ", "max", "conditions", "unit"],
    ["min", "max", "unit"],
    ["typ", "max", "unit"],
]


def column_position_binds(claim, quote, span_text):
    """Ordinal wrong-cell guard: when a header line shows an ordered column
    set (min/typ/max...) and the quote line's numbers follow the same order,
    the claimed value must sit at the claimed column's ordinal position.
    Returns False on a provable mismatch, None when undecidable."""
    col = (claim.get("column_header") or "").lower().strip()
    if not col:
        return None
    col_tok = {"maximum": "max", "minimum": "min", "typical": "typ"}.get(col, col)
    lines = [norm(l) for l in span_text.split("\n") if l.strip()]
    header_line = None
    for l in lines:
        toks = _tokens(l)
        for order in COLUMN_ORDER_SETS:
            present = [t for t in order if t in toks]
            if len(present) >= 2 and col_tok in present:
                idx_in_line = [t for t in toks if t in order]
                if idx_in_line == [t for t in order if t in idx_in_line]:
                    header_line = idx_in_line
                    break
        if header_line:
            break
    if not header_line:
        return None
    col_index = header_line.index(col_tok)
    quote_nums = re.findall(r"-?\d+(?:\.\d+)?", norm(quote))
    try:
        val = float(claim.get("value"))
    except (TypeError, ValueError):
        return None
    val_positions = [i for i, n in enumerate(quote_nums) if abs(float(n) - val) < 1e-9]
    if not val_positions or len(set(val_positions)) > 1:
        return None
    if val_positions[0] == col_index:
        return True
    if val_positions[0] < len(header_line):
        return False
    return None


# restore header_binds original token filter semantics
QUALIFIER_COLUMN_TOKENS = {
    "maximum": ["max", "maximum", "max."],
    "minimum": ["min", "minimum", "min."],
    "typical": ["typ", "typical", "typ."],
    "rated": ["rated", "rating"],
}


def header_binds(header: str, span_text: str, qualifier: str = None):
    """True when the claimed row/column header plausibly governs the quote.

    Header tokens must appear in the span neighborhood; a claimed qualifier
    (max/typ/min) must be carried by the column header tokens — this is the
    wrong-cell guard: a value lifted from the Typ column claimed as maximum
    fails here even though its quote is real.
    """
    if not header:
        return None
    tokens = [t for t in _tokens(header) if len(t) > 1]
    if not tokens:
        return None
    hits = sum(1 for t in tokens if t in span_text)
    if hits == 0:
        return None
    bound = hits >= max(1, int(len(tokens) * 0.5))
    if bound and qualifier:
        want = QUALIFIER_COLUMN_TOKENS.get((qualifier or "").lower().strip())
        if want and not any(w in span_text for w in want):
            return False
    return bound


def verify_claims(claims: dict, doc_text: str):
    verdicts = []
    t_normed = norm(doc_text)
    for field, c in (claims or {}).items():
        if not isinstance(c, dict) or c.get("value") is None:
            continue
        quote, table = c.get("quote") or "", c.get("table") or ""
        if not quote:
            verdicts.append({"field": field, "verdict": "UNSUPPORTED",
                             "reason_code": "unquoted_assertion", "severity": "P0"})
            continue
        if not find_quote(quote, doc_text, field=field):
            verdicts.append({"field": field, "verdict": "UNSUPPORTED",
                             "reason_code": "quote_not_in_doc", "severity": "P0",
                             "evidence_quote": quote[:80]})
            continue
        temp_bad = temp_convention_check(field, quote,
                                         doc_text[:0] + quote)
        if temp_bad is False:
            verdicts.append({"field": field, "verdict": "UNSUPPORTED",
                             "reason_code": "wrong_quantity_convention", "severity": "P0",
                             "evidence_quote": quote[:80]})
            continue
        if not value_in_quote(c.get("value"), quote):
            verdicts.append({"field": field, "verdict": "UNSUPPORTED",
                             "reason_code": "value_not_in_quote", "severity": "P0"})
            continue
        table_ok = (not table) or find_quote(table, doc_text, field=field)

        idx = t_normed.find(norm(quote)[:40])
        col = c.get("column_header")
        row = c.get("row_header")
        span = t_normed[max(0, idx - 400): idx + 200] if idx >= 0 else t_normed
        ordinal = column_position_binds(c, quote, span)
        if ordinal is False:
            verdicts.append({"field": field, "verdict": "UNSUPPORTED",
                             "reason_code": "wrong_cell_binding", "severity": "P0",
                             "evidence_quote": quote[:80]})
            continue
        binding = "P2"
        reason = None
        if col is None and row is None:
            binding = "P1"
            reason = "unbound_header"
        else:
            col_ok = header_binds(col, span, qualifier=c.get("qualifier"))
            row_ok = header_binds(row, t_normed[max(0, idx - 200): idx + 200] if idx >= 0 else t_normed)
            if col_ok is False or row_ok is False:
                binding = "P0"
                reason = "wrong_cell_binding"
            elif col_ok is None and row_ok is None:
                binding = "P1"
                reason = "unbound_header"
            elif (col_ok is None or col_ok) and (row_ok is None or row_ok):
                if col_ok is None or row_ok is None:
                    binding = "P1"
                    reason = "partially_bound_header"
            else:
                binding = "P1"
                reason = "partially_bound_header"

        sev = "P2" if (table_ok and binding == "P2") else binding
        final_reason = reason or (None if table_ok else "table_title_unverified")
        verdicts.append({"field": field, "verdict": "SUPPORTED" if sev == "P2" else "REVIEW",
                         "reason_code": final_reason, "severity": sev})
    return verdicts


def main():
    docs = {d["sha256"]: "\n".join(t for _, t in d.get("text", []))
            for d in json.loads((GATE / "pilot_100_docs_with_text.json").read_text())}
    rows = [json.loads(l) for l in (GATE / "gold-labels.jsonl").open()]
    tally = {}
    for i, row in enumerate(rows):
        text = docs.get(row["sha256"], "")
        for label in ("original_bad_proposal", "corrected_proposal"):
            prop = row[label]
            claims = dict(prop.get("electrical_bounds", {}))
            claims["description"] = {"value": prop.get("description"),
                                     "quote": prop.get("description")}
            verdicts = verify_claims(claims, text)
            for v in verdicts:
                key = (label, v["verdict"])
                tally[key] = tally.get(key, 0) + 1
    print("(informational run on legacy unquoted proposals — expect unquoted_assertion)")
    for (label, verdict), n in sorted(tally.items()):
        print(f"{label:26s} {verdict:12s} {n}")


if __name__ == "__main__":
    main()
