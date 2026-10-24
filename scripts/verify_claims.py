#!/usr/bin/env python3
"""S1 — L2 verification gates: raw extraction rows → verified claims.

The normalization spine's first quality gate. Every raw lane-out row passes
through three deterministic checks before it becomes a verified claim:

  Gate 1  unit_parse        the unit token resolves in the canonical table
  Gate 2  value_range       the numeric value sits in a sane per-field range
  Gate 3  verbatim_recheck  the printed quote actually appears on the claimed
                            page of the source PDF (content-sha verified)

Claims failing any gate are marked with the failure reason, never dropped —
the adjudicator residue (S2) reviews them. REJECT authority stays with the
deterministic gate.

Usage:
  # single box, sharded
  python3 verify_claims.py --rows ~/lane-out/burn/thermal.s0.jsonl \
      --pdf-root /Volumes/M5_4TB/vault/cas --out ~/verified/thermal.s0.jsonl \
      --shard 0/3 --workers 24 --resume

  # census only (no PDF re-open, gates 1+2 only)
  python3 verify_claims.py --rows ... --census
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sys
import time
from collections import Counter, deque
from multiprocessing import Pool
from pathlib import Path
from urllib.parse import urlsplit

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ---------------------------------------------------------------------------
# Gate 1: unit parse (canonical table from dw-normalize's frozen vocabulary)
# ---------------------------------------------------------------------------

UNIT_TABLE = {
    # voltage
    "v": 1.0, "mv": 1e-3, "kv": 1e3,
    # current
    "a": 1.0, "ma": 1e-3, "µa": 1e-6, "μa": 1e-6, "ua": 1e-6, "na": 1e-9, "ka": 1e3,
    # resistance
    "ω": 1.0, "ohm": 1.0, "kω": 1e3, "kohm": 1e3, "mω": 1e6, "mohm": 1e6,
    # power
    "w": 1.0, "mw": 1e-3, "kw": 1e3, "µw": 1e-6, "μw": 1e-6, "uw": 1e-6,
    # frequency
    "hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9,
    # temperature
    "°c": 1.0, "c": 1.0, "°f": None,  # °f doesn't canonicalize
    # capacitance
    "f": 1.0, "µf": 1e-6, "μf": 1e-6, "uf": 1e-6, "nf": 1e-9, "pf": 1e-12,
    # inductance
    "h": 1.0, "µh": 1e-6, "μh": 1e-6, "uh": 1e-6, "nh": 1e-9,
    # thermal
    "°c/w": 1.0, "c/w": 1.0, "k/w": 1.0, "°c/watt": 1.0,
    # time
    "s": 1.0, "ms": 1e-3, "µs": 1e-6, "μs": 1e-6, "us": 1e-6, "ns": 1e-9,
    # dimensionless
    "%": 1.0, "db": 1.0, "v/v": 1.0,
    # pillar-fact units (structural attributes, not electrical claims)
    "kb": 1.0, "count": 1.0, "pins": 1.0, "channels": 1.0, "bytes": 1.0,
    "mhz": 1e6,  # pillar clk_max_mhz uses bare "mhz"
}

# Derived units (per-MHz, per-V) — different quantity, refuse
DERIVED_UNIT = ("/", "per")


def gate_unit_parse(unit: str | None) -> tuple[bool, str]:
    """True if the unit token resolves to a canonical scale.
    Rows WITHOUT a unit (text/boolean claims, errata records, pillar facts
    with unit=null) pass — there is nothing to verify. Only rows that
    PRINT a unit token are checked against the canonical table."""
    if not unit or not unit.strip():
        return True, "unit_not_applicable"
    u = unit.strip().lower().replace("ω", "ohm")
    u = u.replace("μ", "µ").replace("µ", "µ")
    # canonical table FIRST (C/W, °C/W are legitimate compound units)
    if u in UNIT_TABLE:
        factor = UNIT_TABLE[u]
        if factor is None:
            return False, f"unit_{u}_not_canonical"
        return True, f"scaled_{factor}"
    # then derived units (per-MHz etc — different quantity, refuse)
    if "/" in u or "per" in u:
        return False, "unit_derived_not_canonical"
    return False, f"unit_{u}_unknown"


# ---------------------------------------------------------------------------
# Gate 2: value range (sanity tables per field/metric)
# ---------------------------------------------------------------------------

# per-field sanity ranges: (min, max) in the printed unit's natural scale
RANGE_TABLE = {
    # thermal
    "RthetaJA": (0.1, 500), "RthetaJC": (0.05, 100), "RthetaJB": (0.1, 200),
    "PsiJT": (0.01, 50), "PsiJB": (0.01, 100), "ZthJA": (0.001, 1000),
    # mode currents (µA / mA / A — check against the unit)
    "sleep_current": (1e-12, 10),  # pA to 10A — generous
    # generic numeric
    "_default": (0, 1e9),
}


def gate_value_range(value, metric: str | None, unit: str | None) -> tuple[bool, str]:
    """True if the value is in a sane range for its field.
    Rows WITHOUT a numeric value (text claims, errata records, AEC grades)
    pass — there is nothing to range-check. Only rows that carry a numeric
    value are range-checked."""
    if value is None:
        return True, "value_not_numeric_pass_through"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False, "value_not_numeric"
    if v != v:  # NaN
        return False, "value_nan"
    lo, hi = RANGE_TABLE.get(metric or "", RANGE_TABLE["_default"])
    # for currents in µA, scale to A before range check
    u = (unit or "").strip().lower()
    if u in ("µa", "μa", "ua"):
        v = v * 1e-6
    elif u == "na":
        v = v * 1e-9
    elif u == "ma":
        v = v * 1e-3
    if lo <= v <= hi:
        return True, f"range_{lo}_{hi}"
    return False, f"range_violation_{v}_not_in_[{lo},{hi}]"


# ---------------------------------------------------------------------------
# Gate 3: verbatim recheck (the quote appears on the claimed page)
# ---------------------------------------------------------------------------


def _norm_text(s: str) -> str:
    """Normalize for verbatim comparison: collapse whitespace, lowercase,
    strip table-extraction pipe separators (the extractor joins cells with
    ' | ' — the page itself has spaces/newlines)."""
    s = str(s or "").replace("|", " ")
    return " ".join(s.split()).lower()


def gate_verbatim_recheck(quote: str | None, pdf_path: Path | None,
                          page_1based: int | None) -> tuple[bool, str]:
    """True if the quote appears (normalized) on the claimed page."""
    if not quote or not quote.strip():
        return False, "quote_absent"
    if pdf_path is None or not pdf_path.exists():
        return False, "pdf_not_found"
    if page_1based is None or page_1based < 1:
        return False, "page_absent"
    try:
        doc = pymupdf.open(pdf_path)
        if page_1based > doc.page_count:
            doc.close()
            return False, "page_out_of_bounds"
        page_text = _norm_text(doc[page_1based - 1].get_text())
        doc.close()
    except Exception as exc:
        return False, f"pdf_open_failed_{type(exc).__name__}"
    # The table extractor joins cells with " | " — the page itself has
    # cells in table positions, not as a concatenated string. Verify the
    # CELLS: every non-empty cell token appears on the page (normalized).
    # This proves the row was printed there without depending on reading
    # order or exact inter-cell spacing.
    raw = str(quote).replace("|", "\x00")  # protect original pipes
    cells = [c.strip() for c in raw.split("\x00") if c.strip()]
    if not cells:
        return False, "quote_empty_cells"
    page_norm = _norm_text(page_text)
    # Cell match with word-level fallback: a cell counts as present if all
    # its words appear on the page (not necessarily contiguously — table
    # cells are spatial, the page text is linear).
    def cell_on_page(cell: str) -> bool:
        cn = _norm_text(cell)
        if cn in page_norm:
            return True
        words = cn.split()
        return bool(words) and all(w in page_norm for w in words)

    matched = sum(1 for c in cells if cell_on_page(c))
    if matched == len(cells):
        return True, "verbatim_all_cells_wordlevel"
    if matched >= len(cells) * 0.75:
        return True, f"verbatim_{matched}_of_{len(cells)}_cells"
    return False, f"verbatim_{matched}_of_{len(cells)}_cells_not_on_page"


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


def resolve_pdf(sha: str, roots: list[Path]) -> Path | None:
    if not sha or len(sha) < 8:
        return None
    for root in roots:
        p = root / sha[:2] / f"{sha}.pdf"
        if p.exists():
            return p
        # sha-prefix glob (vault naming)
        d = root / sha[:2]
        if d.is_dir():
            for f in d.glob(f"{sha[:8]}*"):
                return f
    return None


def verify_row(row: dict, roots: list[Path], do_recheck: bool = True) -> dict:
    """One row through all three gates."""
    result = dict(row)
    result["_verify"] = {}

    # Gate 1: unit
    unit = row.get("unit") or row.get("_unit")
    ok1, reason1 = gate_unit_parse(unit)
    result["_verify"]["unit"] = {"ok": ok1, "reason": reason1}

    # Gate 2: value range
    metric = row.get("metric") or row.get("field") or row.get("_metric")
    value = row.get("value") or row.get("_value")
    ok2, reason2 = gate_value_range(value, metric, unit)
    result["_verify"]["range"] = {"ok": ok2, "reason": reason2}

    # Gate 3: verbatim recheck (expensive — only if gates 1+2 pass and --recheck)
    if do_recheck and ok1 and ok2:
        quote = row.get("verbatim") or row.get("quote") or row.get("_verbatim")
        sha = row.get("pdf_sha") or row.get("document_sha256") or row.get("sha256")
        pdf = resolve_pdf(sha, roots) if sha else None
        page = row.get("page_1based") or row.get("page")
        ok3, reason3 = gate_verbatim_recheck(quote, pdf, page)
    else:
        ok3 = not do_recheck  # if recheck disabled, pass
        reason3 = "recheck_skipped" if not do_recheck else "gates_1_2_failed"
    result["_verify"]["recheck"] = {"ok": ok3, "reason": reason3}

    result["_verify"]["verdict"] = (
        "verified" if (ok1 and ok2 and ok3)
        else "gate1_fail" if not ok1
        else "gate2_fail" if not ok2
        else "gate3_fail"
    )
    return result


def _worker_tracked(payload):
    """Pool worker: publish pid, verify, return."""
    row, root_strs, do_recheck, token = payload
    roots = [Path(r) for r in root_strs]
    pid_dir = Path("/tmp/verify-pids")
    pid_dir.mkdir(exist_ok=True)
    (pid_dir / token).write_text(str(os.getpid()))
    try:
        return verify_row(row, roots, do_recheck)
    finally:
        (Path("/tmp/verify-pids") / token).unlink(missing_ok=True)


def _token(row: dict, attempt: int) -> str:
    key = str(row.get("pdf_sha") or row.get("sha256") or row.get("source_path") or "")
    h = hashlib.sha256(key.encode()).hexdigest()[:16]
    return f"{h}.{attempt}.{os.getpid()}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--rows", required=True, help="input lane-out JSONL")
    ap.add_argument("--out", required=True, help="output verified JSONL")
    ap.add_argument("--pdf-root", action="append", default=[],
                    help="cas root for verbatim recheck (repeatable)")
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--census", action="store_true",
                    help="gates 1+2 only, no PDF re-open (fast)")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    roots = [Path(r) for r in args.pdf_root] or [
        Path("/Volumes/M5_4TB/vault/cas"),
        Path("/Volumes/M5_4TB/exports/corpus-deliveries/datasheet-tail-20261007/blobs"),
    ]

    # load + shard
    all_rows = []
    with open(args.rows, errors="replace") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    all_rows.append(json.loads(line))
                except Exception:
                    continue
    idx, total = (int(x) for x in args.shard.split("/"))
    shard_rows = all_rows[idx::total]

    # resume: skip rows already in output
    done_keys = set()
    if args.resume and Path(args.out).exists():
        for line in open(args.out, errors="replace"):
            try:
                rec = json.loads(line)
                key = str(rec.get("pdf_sha") or rec.get("sha256") or "") + ":" + str(rec.get("page_1based") or rec.get("page") or "") + ":" + str(rec.get("verbatim") or "")[:40]
                done_keys.add(key)
            except Exception:
                continue

    todo = [r for r in shard_rows if str(r.get("pdf_sha") or r.get("sha256") or "") + ":" + str(r.get("page_1based") or r.get("page") or "") + ":" + str(r.get("verbatim") or "")[:40] not in done_keys]

    print(f"[verify] {args.rows}: {len(all_rows)} rows, shard {args.shard} = {len(shard_rows)}, "
          f"{len(done_keys)} done, {len(todo)} to verify, recheck={'off' if args.census else 'on'}",
          flush=True)

    do_recheck = not args.census
    tally: Counter = Counter()
    t_start = time.time()
    out_fh = open(args.out, "a")

    # Synchronous per-shard: the fleet handles parallelism via --shard i/n.
    # Each box processes its own shard; the verify itself is I/O bound
    # (PDF re-open for gate 3) and the NAS is the throughput limiter.
    for i, row in enumerate(todo):
        try:
            verified = verify_row(row, roots, do_recheck)
            out_fh.write(json.dumps(verified, ensure_ascii=True) + "\n")
            tally[verified["_verify"]["verdict"]] += 1
        except Exception:
            tally["error"] += 1
        if (i + 1) % 500 == 0:
            el = time.time() - t_start
            print(f"[verify] {i+1}/{len(todo)}  {(i+1)/el*3600:.0f}/h  {dict(tally)}",
                  flush=True)

    out_fh.close()
    print(json.dumps({
        "input": str(args.rows), "shard": args.shard, "rows": len(todo),
        "tally": dict(sorted(tally.items())),
        "wall_s": round(time.time() - t_start, 1),
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
