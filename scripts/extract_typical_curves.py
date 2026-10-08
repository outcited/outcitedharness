#!/usr/bin/env python3
"""typical-curves wave: digitize printed characteristic plots (vision lane).

Reads a queue of figure pages (capability=typical_characteristics), renders
each plot region at 200 DPI, asks the local vision tier for the
``typical_characteristics`` contract (axes + legended series + digitized
points in printed axis units), then closes with the two deterministic
checks: axis/legend grounding against the page word list, and the
cross-substrate anchor against the same document's EC-table typ values.

Fill-only: a plot that fails validation or grounding ships as a verdict
row with its problems — never points that cannot be printed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.electronics.local_model import LocalExtractionClient  # noqa: E402
from harness.electronics.mode_currents import mode_current_rows  # noqa: E402
from harness.electronics.typical_curves import (  # noqa: E402
    CURVE_SCHEMA,
    anchor_check,
    axis_labels_grounded,
    plot_regions_for_page,
    validate_curve_payload,
)

RENDER_DPI = 600


def _queue_pages(path: Path) -> list[dict]:
    payload = json.loads(path.read_text())
    items = payload.get("work") if isinstance(payload, dict) else payload
    out = []
    for item in items or []:
        source = item.get("source_path") or item.get("pdf_path") or item.get("path")
        page = item.get("page_1based") or item.get("page")
        if not source or not page:
            continue
        out.append(
            {
                "source_path": str(source),
                "page_1based": int(page),
                "document_sha256": item.get("document_sha256") or item.get("sha256"),
                "work_id": item.get("work_id"),
            }
        )
    return out


def _table_facts(pdf: Path) -> list[dict]:
    """EC-table + mode-current facts for the cross-substrate anchor.

    Mode-current rows (the deterministic Wave B reader) are the primary
    source for current-vs-voltage curves: same document, same printed rows,
    different substrate — that is the agreement the lane measures.
    """
    facts: list[dict] = []
    doc = pymupdf.open(pdf)
    try:
        for page_index in range(doc.page_count):
            page = doc[page_index]
            try:
                tables = page.find_tables().tables
            except Exception:  # pragma: no cover - PyMuPDF internals
                continue
            for table_index, table in enumerate(tables):
                try:
                    grid = table.extract()
                except Exception:  # pragma: no cover - PyMuPDF internals
                    continue
                for row in mode_current_rows(
                    {"table_index": table_index, "rows": grid},
                    document_sha256="",
                    page_1based=page_index + 1,
                ):
                    condition = row.get("conditions_verbatim") or ""
                    vdd = (row.get("anchors") or {}).get("vdd_v")
                    if vdd is not None and not re.search(r"V\s*(?:DD|CC)", condition):
                        condition = f"{condition} VCC = {vdd} V".strip()
                    facts.append(
                        {
                            "field": f"{row['mode']} current",
                            "typ": row["value"],
                            "unit": row["unit"],
                            "condition_verbatim": condition,
                        }
                    )
    except Exception:
        pass
    finally:
        doc.close()
    try:
        from harness.electronics.power_datasheet import read_characteristic_tables

        doc = pymupdf.open(pdf)
        try:
            facts.extend(read_characteristic_tables(doc))
        finally:
            doc.close()
    except Exception:
        pass
    return facts


def _render_region(page, bbox, out_png: Path) -> None:
    clip = pymupdf.Rect(*bbox) & page.rect
    matrix = pymupdf.Matrix(RENDER_DPI / 72.0, RENDER_DPI / 72.0)
    pixmap = page.get_pixmap(matrix=matrix, clip=clip)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(str(out_png))


_FACTS_CACHE: dict[str, list[dict]] = {}
_FACTS_CACHE_LIMIT = 6


def _facts_cached(pdf: Path, doc_sha: str | None) -> list[dict]:
    """Anchor facts per DOCUMENT, not per page — the queue repeats each
    doc's pages and a full-document rescan per item made the burn
    CPU-bound (measured: workers pinned at 99% in _table_facts). A shared
    on-disk cache makes the scan happen once per document across all
    workers and all restarts (measured: 6/12 workers buried in 3,000-page
    RM rescans before this)."""

    key = doc_sha or str(pdf)
    cached = _FACTS_CACHE.get(key)
    if cached is not None:
        return cached
    cache_dir = os.environ.get("FACTS_CACHE_DIR")
    cache_file = Path(cache_dir) / f"{key}.json" if cache_dir else None
    if cache_file and cache_file.exists():
        try:
            facts = json.loads(cache_file.read_text())
            _FACTS_CACHE[key] = facts
            return facts
        except Exception:
            pass
    facts = _table_facts(pdf)
    if cache_file:
        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(facts, ensure_ascii=False, default=str))
            tmp.replace(cache_file)
        except Exception:
            pass
    if len(_FACTS_CACHE) >= _FACTS_CACHE_LIMIT:
        _FACTS_CACHE.pop(next(iter(_FACTS_CACHE)))
    _FACTS_CACHE[key] = facts
    return facts


def extract_page(
    client: LocalExtractionClient,
    item: dict,
    *,
    renders: Path,
) -> list[dict]:
    pdf = Path(item["source_path"])
    doc_sha = item["document_sha256"] or hashlib.sha256(pdf.read_bytes()).hexdigest()
    doc = pymupdf.open(pdf)
    try:
        page = doc[item["page_1based"] - 1]
        regions = plot_regions_for_page(page)
        page_words = [str(w[4]) for w in page.get_text("words")]
        outputs = []
        for figure_index, region in enumerate(regions):
            bbox = region["bbox"]
            png = renders / f"{doc_sha[:16]}-p{item['page_1based']}-f{figure_index}.png"
            if not png.exists():
                _render_region(page, bbox, png)
            outputs.append((figure_index, region, bbox, png))
    finally:
        doc.close()
    rows = []
    facts = _facts_cached(pdf, item.get("document_sha256"))
    for figure_index, region, bbox, png in outputs:
        result = client.extract(
            capability="typical_characteristics",
            page_evidence={},
            image_path=png,
        )
        payload = result.get("answer") or result.get("result") or result
        if not isinstance(payload, dict):
            payload = {}
        problems = validate_curve_payload(payload)
        grounded, missing = axis_labels_grounded(payload, page_words)
        anchors = anchor_check(payload, table_facts=facts)
        verdict = "extracted"
        if problems:
            verdict = "rejected_structure"
        elif not grounded:
            verdict = "hold_ungrounded_axis"
        elif anchors["comparable"] and anchors["agreement_rate"] is not None and anchors["agreement_rate"] < 0.5:
            verdict = "hold_anchor_disagreement"
        rows.append(
            {
                "schema": CURVE_SCHEMA,
                "work_id": item["work_id"],
                "document_sha256": doc_sha,
                "source_path": item["source_path"],
                "page_1based": item["page_1based"],
                "figure_index": figure_index,
                "region_bbox": list(bbox),
                "region_source": region["source"],
                "render_sha256": hashlib.sha256(png.read_bytes()).hexdigest(),
                "render_dpi": RENDER_DPI,
                "payload": payload,
                "verdict": verdict,
                "problems": problems,
                "ungrounded_labels": missing,
                "anchor_check": anchors,
            }
        )
    return rows


def _scan_item(
    item: dict,
    base_url: str,
    model: str,
    renders: Path,
    cloud_key: str = "",
    thinking_off: bool = False,
) -> list[dict] | str:
    if not Path(item["source_path"]).exists():
        return "missing_pdf"
    try:
        if cloud_key:
            from harness.electronics.cloud_vision import (
                CloudVisionClient,
                curve_prompt,
            )

            cloud = CloudVisionClient(
                base_url=base_url,
                model=model,
                api_key=cloud_key,
                extra_body={"thinking": {"type": "disabled"}} if thinking_off else None,
            )

            class _Adapter:
                def extract(self, *, capability, page_evidence, image_path):
                    return cloud.extract(
                        capability=capability,
                        image_path=image_path,
                        prompt=curve_prompt(),
                    )

            client = _Adapter()
        else:
            client = LocalExtractionClient(base_url=base_url, model=model)
        return extract_page(client, item, renders=renders)
    except Exception as exc:
        return type(exc).__name__


def _absorb(rows: list[dict], out_rows: list[dict], tally: Counter) -> None:
    for row in rows:
        out_rows.append(row)
        tally[row["verdict"]] += 1
        tally["series"] += len((row.get("payload") or {}).get("series") or [])
        if row["anchor_check"]["comparable"]:
            tally["anchor_comparable"] += row["anchor_check"]["comparable"]
            tally["anchor_agreed"] += row["anchor_check"]["agreed"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--renders", type=Path, required=True)
    ap.add_argument("--base-url", default="http://100.68.133.1:8888/v1")
    ap.add_argument("--model", default="dsv41_flash")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--api-key-env", default="MIMO_API_KEY")
    ap.add_argument(
        "--thinking-off", action="store_true",
        help="send thinking:{type:disabled} (DeepSeek official honors it: "
        "0 reasoning tokens, 2.5s, measured 2026-10-06)",
    )
    ap.add_argument(
        "--cloud",
        action="store_true",
        help="volume vision lane: cloud client (key from MIMO_API_KEY env/.env)",
    )
    args = ap.parse_args()
    cloud_key = ""
    if args.cloud:
        cloud_key = os.environ.get(args.api_key_env) or ""
        if not cloud_key:
            env_path = Path(__file__).resolve().parents[1] / ".env"
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if line.startswith(f"{args.api_key_env}="):
                        cloud_key = line.split("=", 1)[1].strip()
        if not cloud_key:
            raise SystemExit(f"--cloud requires {args.api_key_env} in env or .env")

    pages = _queue_pages(args.queue)
    # same-document pages adjacent: the per-process facts cache then hits
    # for every page of a document instead of rescanning it per item
    pages.sort(key=lambda item: (item["source_path"], item["page_1based"]))
    if args.limit:
        pages = pages[: args.limit]
    for item in pages:
        if not item["document_sha256"]:
            pdf = Path(item["source_path"])
            if not pdf.exists():
                continue
            item["document_sha256"] = hashlib.sha256(pdf.read_bytes()).hexdigest()
    out_rows = []
    tally: Counter[str] = Counter()
    done_ids: set[str] = set()
    if args.out.exists():
        for line in args.out.read_text().splitlines():
            if line.strip():
                try:
                    done_ids.add(json.loads(line).get("work_id"))
                except Exception:
                    continue
        if done_ids:
            pages = [p for p in pages if p["work_id"] not in done_ids]
            tally["resumed_skipped"] = len(done_ids)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault(
        "FACTS_CACHE_DIR", str(args.out.parent / "facts-cache")
    )
    sink = args.out.open("a") if args.out.exists() else args.out.open("w")
    if args.workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            for item, result in zip(
                pages,
                pool.map(
                    _scan_item,
                    pages,
                    [args.base_url] * len(pages),
                    [args.model] * len(pages),
                    [args.renders] * len(pages),
                    [cloud_key] * len(pages),
                    [args.thinking_off] * len(pages),
                ),
            ):
                if isinstance(result, str):
                    tally[result] += 1
                    continue
                _absorb(result, out_rows, tally)
                for row in result:
                    sink.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                sink.flush()
    else:
        for item in pages:
            pdf = Path(item["source_path"])
            if not pdf.exists():
                tally["missing_pdf"] += 1
                continue
            try:
                result = _scan_item(
                    item, args.base_url, args.model, args.renders, cloud_key
                )
                if isinstance(result, str):
                    tally[result] += 1
                    continue
                rows = result
            except Exception as exc:
                tally[f"error:{type(exc).__name__}"] += 1
                continue
            _absorb(rows, out_rows, tally)
            for row in rows:
                sink.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            sink.flush()
    sink.close()
    print(json.dumps({"pages": len(pages), "rows": len(out_rows), **dict(sorted(tally.items()))}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
