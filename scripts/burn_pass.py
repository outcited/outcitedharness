#!/usr/bin/env python3
"""burn_pass — one parse pass per document, every deterministic reader, ledger.

The corpus burn: open each PDF ONCE, extract page text and tables ONCE, run
every reader over the shared parse. Every document gets a disposition record
(rows per reader, or an explicit reason) — coverage is computed from the
ledger, never assumed.

Readers (fill-only, printed evidence only):
    thermal   RθJA/RθJC/ΨJT/ΨJB/Zth from characteristic tables
    mode      power-mode currents from hinted pages
    aec       AEC grade mentions
    pillars   front-page pillar facts
    errata    errata delta records

Laws:
    - one pymupdf open per document; page text + find_tables cached per page
    - timeout requeues ONCE (bigger budget); only a second timeout is a hole
    - every sha gets a ledger row: status readers timings or named reason
    - rows are append-written and flushed per chunk; a crash loses only the
      in-flight chunk, and the queue is resumable via the ledger

Usage:
    python3 scripts/burn_pass.py --queue /tmp/burn-queue.jsonl \
        --out-dir /Users/samkim/lane-out/burn --shard 0/2 \
        --workers 12 --doc-timeout 60
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.electronics.aec_grades import grade_rows  # noqa: E402
from harness.electronics.errata_deltas import errata_records  # noqa: E402
from harness.electronics.mode_currents import (  # noqa: E402
    collapse_competing,
    mode_current_rows,
)
from harness.electronics.pillar_facts import pillar_facts_from_document  # noqa: E402
from harness.electronics.thermal_metrics import (  # noqa: E402
    collapse_thermal,
    thermal_rows,
)

MODE_PAGE_HINT = re.compile(
    r"power\s+(?:consumption|mode|current)|current\s+consumption|"
    r"low[- ]power|supply\s+current|"
    r"run|sleep|stop|standby|shutdown|hibernate|snooze|idle",
    re.I,
)
CURRENT_UNIT_HINT = re.compile(r"(?:nA|µA|uA|μA|mA)(?:\s*/\s*(?:MHz|kHz))?", re.I)

# Page gate for the thermal reader: read_characteristic_tables calls
# find_tables on EVERY page — the dominant burn cost (8s/doc on manuals).
# A thermal row requires its symbol/words/unit to print in the page's text
# layer, so pages missing all thermal tokens can never yield one. Tokens are
# a generous superset of thermal_metrics' matchers (every alternative of
# _THERMAL_SYMBOL/_WORDS/_UNIT contains one of these), so the gate never
# drops a page those matchers would accept — span-splitting can only make
# the page-level text looser, and every token family is caught here.
THERMAL_PAGE_GATE = re.compile(
    r"θ|ϑ|Θ|Ψ|R\s*th|R\s*θ|Z\s*th|theta|psi|thermal|junction|characterization|"
    r"C\s*/\s*[Ww]|K\s*/\s*[Ww]|watt",
    re.I,
)
# errata_records emits for any X.Y.Z-numbered section heading — the burn
# measurement showed 78,315/78,382 rows were TOC/section grabs with no
# elimination material. Two gates: the doc must carry errata-signature
# vocabulary, AND a row only survives with affected silicon or a workaround
# (a symptom-only record binds to no revision and eliminates nothing).
ERRATA_DOC_GATE = re.compile(
    r"errat|affected\s+silicon|silicon\s+revision|known\s+issues?|work[\s-]?around",
    re.I,
)

# Classes that never enter prose/table claim extraction (wrong tool or
# claims forbidden by law — brochure/white_paper marketing is never a claim).
SKIP_CLASSES = {
    "image_only": "vision_only_shape",
    "container": "unpacks_to_children",
    "eda_model": "typed_parser_shape",
    "tabular_sheet": "typed_parser_shape",
    "design_file": "not_a_claims_source",
    "sidecar_text": "metadata_only",
    "brochure": "marketing_never_claims",
    "white_paper": "marketing_never_claims",
}

READERS = ("thermal", "mode", "aec", "pillars", "errata")


class CachedPage:
    """Page proxy: get_text()/find_tables() memoized so every reader shares
    one extraction instead of re-parsing the same page."""

    def __init__(self, page):
        self._page = page
        self._text: dict[tuple, str] = {}
        self._tables = None

    def get_text(self, mode: str = "text", *args, **kwargs):
        key = (mode, args, tuple(sorted(kwargs.items())))
        if key not in self._text:
            self._text[key] = self._page.get_text(mode, *args, **kwargs)
        return self._text[key]

    def find_tables(self, *args, **kwargs):
        if self._tables is None:
            self._tables = self._page.find_tables(*args, **kwargs)
        return self._tables

    def __getattr__(self, name):
        return getattr(self._page, name)


class CachedDoc:
    """Document proxy handing out cached pages."""

    def __init__(self, doc):
        self._doc = doc
        self._pages: dict[int, CachedPage] = {}

    def __getitem__(self, i: int) -> CachedPage:
        if i not in self._pages:
            self._pages[i] = CachedPage(self._doc[i])
        return self._pages[i]

    @property
    def page_count(self) -> int:
        return self._doc.page_count

    def close(self):
        self._doc.close()

    def __getattr__(self, name):
        return getattr(self._doc, name)


def _page_texts(doc: CachedDoc) -> list[str]:
    return [doc[i].get_text("text") for i in range(doc.page_count)]


class _EmptyFinder:
    tables: list = []


class GatedDoc:
    """Doc view whose pages only run find_tables when the gate matches their
    text — the expensive call is skipped on pages that cannot yield a row."""

    def __init__(self, doc: CachedDoc, gate):
        self._doc = doc
        self._gate = gate

    def __getitem__(self, i: int):
        page = self._doc[i]
        if self._gate.search(page.get_text("text")):
            return page
        return _GatedPage(page)

    @property
    def page_count(self) -> int:
        return self._doc.page_count

    def __getattr__(self, name):
        return getattr(self._doc, name)


class _GatedPage:
    def __init__(self, page):
        self._page = page

    def find_tables(self, *args, **kwargs):
        return _EmptyFinder()

    def __getattr__(self, name):
        return getattr(self._page, name)


def _reader_thermal(doc: CachedDoc) -> list[dict]:
    from harness.electronics.power_datasheet import read_characteristic_tables

    facts = read_characteristic_tables(GatedDoc(doc, THERMAL_PAGE_GATE))
    return list(collapse_thermal(thermal_rows(facts)).values())


def _reader_mode(doc: CachedDoc, page_texts: list[str]) -> list[dict]:
    rows: list[dict] = []
    for page_index, text in enumerate(page_texts):
        if not MODE_PAGE_HINT.search(text) or not CURRENT_UNIT_HINT.search(text):
            continue
        try:
            tables = doc[page_index].find_tables().tables
        except Exception:  # noqa: BLE001 — PyMuPDF internals
            continue
        for table_index, table in enumerate(tables):
            try:
                grid = table.extract()
            except Exception:  # noqa: BLE001
                continue
            rows.extend(
                mode_current_rows(
                    {"table_index": table_index, "rows": grid},
                    document_sha256="",
                    page_1based=page_index + 1,
                    source_order=len(rows),
                )
            )
    return collapse_competing(rows)


def _reader_aec(page_texts: list[str]) -> list[dict]:
    return grade_rows(page_texts)


def _reader_pillars(doc: CachedDoc) -> list[dict]:
    return pillar_facts_from_document(doc)


# --- errata: two document shapes ---
# ST-style X.Y.Z issues (harness.electronics.errata_deltas) AND TI-style
# alphanumeric issue IDs (ADC42, PMM02...) with Category/Function/Description/
# Workaround fields. The burn measurement showed the old X.Y.Z-only reader
# both poisoned the lane (TOC grabs: 78,315/78,382 rows with zero elimination
# material) and missed TI errata entirely. Both are fixed here.
_TI_ISSUE_ID = re.compile(r"^([A-Z]{2,5}\d{1,4}[A-Z]?)\.?\s*$")
_TI_FIELD = re.compile(r"^(Category|Function|Description|Workaround|Suggested\s+Action)\s*$")


def _ti_errata_records(page_texts: list[str]) -> list[dict]:
    """Parse TI erratasheet bug blocks: ID / module / labeled fields."""
    lines: list[str] = []
    for text in page_texts:
        for raw in text.splitlines():
            line = " ".join(raw.split())
            if line:
                lines.append(line)

    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    mode = ""
    for index, line in enumerate(lines):
        match = _TI_ISSUE_ID.match(line)
        if match and index + 1 < len(lines):
            # look-ahead: a real bug block carries Category (erratasheet
            # structure); pin-mux tables (OUT3, RW1C, CMP4) only ever show a
            # bare "Function" column header, so Category is the discriminator.
            lookahead = lines[index + 1: index + 6]
            if not any(re.match(r"^Category\s*$", candidate) for candidate in lookahead):
                continue
            if current is not None:
                records.append(current)
            current = {
                "schema": "harness.electronics-errata-delta.v1",
                "record_kind": "issue",
                "issue_id": match.group(1),
                "module": lines[index + 1][:80] if not _TI_FIELD.match(lines[index + 1]) else None,
                "title_verbatim": None,
                "symptom_verbatim": None,
                "workaround_verbatim": None,
                "affected_silicon": {},
                "source": "ti_erratasheet",
            }
            mode = ""
            continue
        if current is None:
            continue
        field = _TI_FIELD.match(line)
        if field:
            key = field.group(1).lower().split()[0]
            mode = "title" if key == "function" else key
            continue
        if mode == "title":
            current["title_verbatim"] = ((current["title_verbatim"] or "") + " " + line).strip()[:200]
        elif mode == "description":
            current["symptom_verbatim"] = ((current["symptom_verbatim"] or "") + " " + line).strip()[:1200]
        elif mode in ("workaround", "suggested"):
            current["workaround_verbatim"] = ((current["workaround_verbatim"] or "") + " " + line).strip()[:1200]
        elif mode == "category":
            current["category_verbatim"] = line[:40]
    if current is not None:
        records.append(current)
    return [r for r in records if r.get("title_verbatim")]


def _reader_errata(page_texts: list[str]) -> list[dict]:
    joined = "\n".join(page_texts)
    if not ERRATA_DOC_GATE.search(joined):
        return []
    out = []
    # ST-style numbered issues — materiality filter kills TOC section grabs
    for rec in errata_records(page_texts):
        if rec.get("record_kind") == "clarification":
            out.append(rec)
        elif rec.get("affected_silicon") or rec.get("workaround_verbatim"):
            out.append(rec)
    # TI-style alphanumeric issues — always material by construction
    out.extend(_ti_errata_records(page_texts))
    return out


def scan_one(item: dict, path_map: list[tuple[str, str]], readers: tuple[str, ...],
             doc_timeout: float) -> dict:
    """One document: single open, shared parse, all readers, timings."""
    t0 = time.time()
    doc_class = item.get("doc_class")
    if doc_class in SKIP_CLASSES:
        return {"status": f"skip:{SKIP_CLASSES[doc_class]}", "source_path": item["source_path"],
                "doc_class": doc_class, "rows": {}, "timings": {}}
    source = item["source_path"]
    for old, new in path_map:
        if source.startswith(old):
            source = new + source[len(old):]
            break
    pdf = Path(source)
    if not (source.startswith("http://") or source.startswith("https://")) and not pdf.exists():
        return {"status": "missing_source", "source_path": item["source_path"],
                "rows": {}, "timings": {}}

    timings: dict[str, float] = {}
    try:
        t = time.time()
        if source.startswith("http://") or source.startswith("https://"):
            import urllib.parse
            import urllib.request

            parts = urllib.parse.urlsplit(source)
            url = urllib.parse.urlunsplit(
                (parts.scheme, parts.netloc, urllib.parse.quote(parts.path), parts.query, "")
            )
            with urllib.request.urlopen(url, timeout=120) as resp:
                data = resp.read()
        else:
            data = pdf.read_bytes()
        sha = hashlib.sha256(data).hexdigest()
        doc = CachedDoc(pymupdf.open(stream=data, filetype="pdf"))
        timings["open_ms"] = round((time.time() - t) * 1000, 1)
    except Exception as exc:  # noqa: BLE001
        return {"status": f"error:{type(exc).__name__}", "source_path": item["source_path"],
                "rows": {}, "timings": timings}

    try:
        t = time.time()
        texts = _page_texts(doc)
        timings["text_ms"] = round((time.time() - t) * 1000, 1)

        rows: dict[str, list[dict]] = {}
        reader_status: dict[str, dict] = {}
        for name in readers:
            t = time.time()
            try:
                if name == "thermal":
                    got = _reader_thermal(doc)
                elif name == "mode":
                    got = _reader_mode(doc, texts)
                elif name == "aec":
                    got = _reader_aec(texts)
                elif name == "pillars":
                    got = _reader_pillars(doc)
                elif name == "errata":
                    got = _reader_errata(texts)
                else:
                    got = []
            except Exception as exc:  # noqa: BLE001
                reader_status[name] = {"error": type(exc).__name__}
                continue
            ms = round((time.time() - t) * 1000, 1)
            reader_status[name] = {"rows": len(got), "ms": ms}
            if got:
                rows[name] = got

        page_count = doc.page_count
        doc.close()
        total_ms = round((time.time() - t0) * 1000, 1)
        status = "rows" if rows else "no_printed_values"
        return {
            "status": status,
            "sha256": sha,
            "source_path": item["source_path"],
            "doc_class": item.get("doc_class"),
            "class_source": item.get("class_source"),
            "page_count": page_count,
            "text_chars": sum(len(t.strip()) for t in texts),
            "readers": reader_status,
            "timings": timings,
            "total_ms": total_ms,
            "rows": rows,
        }
    except Exception as exc:  # noqa: BLE001
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass
        return {"status": f"error:{type(exc).__name__}", "source_path": item["source_path"],
                "rows": {}, "timings": timings}


def _worker(args):
    item, path_map, readers, doc_timeout = args
    return scan_one(item, path_map, readers, doc_timeout)


def _worker_tracked(payload):
    """Module-level (picklable): publish this worker's pid, then scan.
    The parent SIGKILLs the pid on deadline breach; multiprocessing.Pool
    respawns the worker automatically."""
    item, path_map, readers, doc_timeout, token = payload
    pid_file = Path("/tmp/burn-pids") / token
    pid_file.parent.mkdir(exist_ok=True)
    pid_file.write_text(str(os.getpid()))
    return _worker((item, path_map, readers, doc_timeout))


def _emit(fh_rows: dict, fh_ledger, result: dict) -> None:
    sha = result.get("sha256", "")
    base = {
        "pdf_sha": sha,
        "document_sha256": sha,
        "source_path": result.get("source_path"),
        "doc_class": result.get("doc_class"),
        "verdict": "printed_row",
    }
    for reader, rows in result.get("rows", {}).items():
        fh = fh_rows[reader]
        for row in rows:
            out = dict(row)
            out.update(base)
            fh.write(json.dumps(out, ensure_ascii=True, sort_keys=True) + "\n")
    ledger = {
        "sha256": sha or None,
        "source_path": result.get("source_path"),
        "doc_class": result.get("doc_class"),
        "class_source": result.get("class_source"),
        "status": result["status"],
        "page_count": result.get("page_count"),
        "text_chars": result.get("text_chars"),
        "readers": result.get("readers", {}),
        "timings": result.get("timings", {}),
        "total_ms": result.get("total_ms"),
    }
    fh_ledger.write(json.dumps(ledger, ensure_ascii=True, sort_keys=True) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--shard", default="0/1", help="i/n deterministic split by position")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--doc-timeout", type=float, default=60.0,
                    help="base per-doc budget seconds (size-aware: + per-MB)")
    ap.add_argument("--timeout-per-mb", type=float, default=2.0,
                    help="extra budget seconds per MB of source PDF (size-aware deadline)")
    ap.add_argument("--timeout-cap", type=float, default=600.0,
                    help="max per-doc budget seconds regardless of size")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--readers", nargs="+", default=list(READERS), choices=list(READERS))
    ap.add_argument("--map", action="append", default=[],
                    help="path prefix rewrite OLD=NEW (repeatable, for non-macOS mounts)")
    ap.add_argument("--resume", action="store_true",
                    help="skip sha256s already in the ledger")
    args = ap.parse_args()

    path_map = []
    for spec in args.map:
        old, _, new = spec.partition("=")
        if old and new:
            path_map.append((old, new))

    payload = json.loads(args.queue.read_text())
    items = payload.get("documents") or payload.get("items") or payload
    if not isinstance(items, list):
        raise SystemExit("queue must be a list or {documents: [...]}")

    idx, total = (int(x) for x in args.shard.split("/"))
    items = items[idx::total]

    def _mapped(sp: str) -> str:
        for old, new in path_map:
            if sp.startswith(old):
                return new + sp[len(old):]
        return sp

    def _doc_budget(sp: str) -> float:
        """Size-aware deadline: base + per-MB, capped. Missing/remote = base."""
        try:
            mb = os.path.getsize(_mapped(sp)) / 1e6
        except OSError:
            mb = 0.0
        return min(args.doc_timeout + args.timeout_per_mb * mb, args.timeout_cap)

    # small-first: workers never stall behind monsters; missing files sort last
    # (deterministic: size, then path). Set --timeout-per-mb 0 to disable only
    # the size-aware part; sorting is always on (harmless, strictly better).
    items.sort(key=lambda d: (_doc_budget(d.get("source_path", "")),
                              str(d.get("source_path", ""))))
    if args.limit:
        items = items[: args.limit]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f".s{idx}" if total > 1 else ""
    fh_rows = {
        name: (args.out_dir / f"{name}{suffix}.jsonl").open("a")
        for name in args.readers
    }
    fh_ledger = (args.out_dir / f"ledger{suffix}.jsonl").open("a")

    done: set[str] = set()
    if args.resume:
        ledger_path = args.out_dir / f"ledger{suffix}.jsonl"
        if ledger_path.exists():
            for line in ledger_path.open():
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                # only finished dispositions skip: errors and timeouts get
                # re-queued by a resume pass, never silently marked done
                if rec.get("source_path") and not (
                    rec.get("status", "").startswith("error")
                    or rec.get("status") in ("timeout", "missing_source")
                ):
                    done.add(rec["source_path"])

    tally: Counter = Counter()
    t_start = time.time()
    # Scheduling law v2 (2026-10-19, measured on winter/9955HX 32T):
    #   v1 bug — wait(ALL_COMPLETED) over big chunks let 1-2 hung PDFs stall
    #   the whole pool (measured 97% idle, ~3K docs/h on 32 threads).
    #   v2 law — a hung doc may own AT MOST its own worker slot. Killing a
    #   worker breaks ProcessPoolExecutor, but multiprocessing.Pool RESPAWNS
    #   killed workers automatically. So: persistent Pool, sliding in-flight
    #   window, per-doc deadline; on breach we SIGKILL that worker (pid via
    #   pid-file), record the timeout, and the slot returns in ~0.1s.
    # Throughput is then bounded by real per-doc cost (~2.8s CPU/doc), not
    # by pathological tails. Accuracy paths are untouched (byte-identical
    # cross-box validation stands).
    import signal as _signal
    from multiprocessing import Pool

    pid_dir = Path("/tmp/burn-pids")
    pid_dir.mkdir(exist_ok=True)

    def _token(item: dict, attempt: int) -> str:
        h = hashlib.sha256(str(item.get("source_path")).encode()).hexdigest()[:16]
        return f"{h}.{attempt}.{os.getpid()}"

    from collections import deque

    inflight: dict = {}
    attempts: dict[str, int] = {}
    queue_docs = deque(items)
    done_seen: set = set()
    window = args.workers * 2
    kill_budget = args.doc_timeout * 2

    def _submit(doc: dict, attempt: int):
        token = _token(doc, attempt)
        payload = (doc, path_map, tuple(args.readers), args.doc_timeout, token)
        inflight[token] = {
            "item": doc, "attempt": attempt, "t0": time.time(),
            "budget_kill": _doc_budget(doc.get("source_path", "")) * 2,
            "res": pool_ref[0].apply_async(_worker_tracked, (payload,)),
        }

    pool_ref = [None]
    pool_ref[0] = Pool(processes=args.workers)

    def _finish(token: str, result: dict):
        _emit(fh_rows, fh_ledger, result)
        tally[result["status"]] += 1

    while queue_docs or inflight:
        # top up the window
        while queue_docs and len(inflight) < window:
            doc = queue_docs.popleft()
            sp = doc.get("source_path")
            if sp in done_seen or sp in done:
                continue
            att = attempts.get(sp, 0)
            if att >= 2:
                _emit(fh_rows, fh_ledger,
                      {"status": "timeout", "source_path": sp,
                       "doc_class": doc.get("doc_class"), "rows": {}})
                tally["timeout"] += 1
                continue
            _submit(doc, att)
        # drain completions
        for token in list(inflight.keys()):
            entry = inflight[token]
            if entry["res"].ready():
                try:
                    result = entry["res"].get()
                except Exception as exc:  # noqa: BLE001 — killed worker etc.
                    result = {"status": f"error:{type(exc).__name__}",
                              "source_path": entry["item"].get("source_path"),
                              "rows": {}}
                sp = entry["item"].get("source_path")
                if result["status"].startswith("error"):
                    attempts[sp] = entry["attempt"] + 1
                    queue_docs.appendleft(entry["item"])
                else:
                    done_seen.add(sp)
                    _finish(token, result)
                inflight.pop(token, None)
                (pid_dir / token).unlink(missing_ok=True)
        # deadline enforcement (size-aware): SIGKILL the owning worker,
        # Pool respawns it. budget_kill = 2 x per-doc budget (base + s/MB, cap)
        now = time.time()
        for token in list(inflight.keys()):
            entry = inflight[token]
            if now - entry["t0"] > entry.get("budget_kill", kill_budget):
                pid_file = pid_dir / token
                if pid_file.exists():
                    try:
                        os.kill(int(pid_file.read_text().strip()), _signal.SIGKILL)
                    except (ProcessLookupError, ValueError):
                        pass
                sp = entry["item"].get("source_path")
                attempts[sp] = entry["attempt"] + 1
                if attempts[sp] < 2:
                    queue_docs.appendleft(entry["item"])
                else:
                    _emit(fh_rows, fh_ledger,
                          {"status": "timeout", "source_path": sp,
                           "doc_class": entry["item"].get("doc_class"), "rows": {}})
                    tally["timeout"] += 1
                    done_seen.add(sp)
                inflight.pop(token, None)
                tally["timeout_first_pass"] += 1
                (pid_dir / token).unlink(missing_ok=True)
        for fh in fh_rows.values():
            fh.flush()
        fh_ledger.flush()
        if tally and int(time.time() - t_start) % 60 < 6:
            elapsed = time.time() - t_start
            processed = sum(tally.values())
            rate = processed / elapsed * 3600 if elapsed > 0 else 0
            print(f"[burn] {processed} docs  {rate:.0f}/h  inflight={len(inflight)}  {dict(tally)}",
                  flush=True)
        time.sleep(2)

    pool_ref[0].terminate()
    pool_ref[0].join()
    for fh in fh_rows.values():
        fh.flush()
        fh.close()
    fh_ledger.flush()
    fh_ledger.close()
    elapsed = time.time() - t_start
    print(json.dumps({
        "queue": str(args.queue), "shard": args.shard,
        "items": len(items), "rows": {n: sum(1 for _ in open(args.out_dir / f"{n}{suffix}.jsonl"))
                                      for n in args.readers},
        "tally": dict(sorted(tally.items())),
        "wall_s": round(elapsed, 1),
        "docs_per_hour": round(len(items) / elapsed * 3600, 1) if elapsed else None,
        "scheduling": {"doc_timeout_base_s": args.doc_timeout,
                       "timeout_per_mb_s": args.timeout_per_mb,
                       "timeout_cap_s": args.timeout_cap,
                       "order": "size_ascending"},
    }, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
