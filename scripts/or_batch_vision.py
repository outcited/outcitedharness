#!/usr/bin/env python3
"""OpenRouter batch lane for the L0 vision backlog (plot contract).

prepare:  L0 vision pages -> chunked request files (regions rendered,
          images embedded, deterministic prompts, custom_id = work lineage)
submit:   upload + create batch jobs (70% off :batch pricing)
retrieve: poll, download, schema-validate, ground against page words,
          emit rows (same shape as extract_typical_curves) + training
          pairs export compatible.

Pilot first (process law): one small file through the whole loop before
the mega-submission. Every retrieved answer is a model answer under the
factory law: verified or held, never trusted, always paired.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harness.electronics.claims import canonical_json  # noqa: E402
from harness.electronics.typical_curves import (  # noqa: E402
    axis_labels_grounded,
    plot_regions_for_page,
    validate_curve_payload,
)

OR_BASE = "https://openrouter.ai/api/v1"
MODEL = "deepseek/deepseek-v4.1-flash:batch"


def _key() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("OPENROUTER_API_KEY missing")


def _prompt() -> str:
    from harness.electronics.cloud_vision import curve_prompt

    return curve_prompt()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def cmd_prepare(args) -> int:
    l0_records = [json.loads(l) for l in open(args.l0_records)]
    by_class = [r for r in l0_records if r["doc_class"] == args.doc_class]
    queue = []
    for record in by_class:
        pages = record.get("vision_candidate_pages") or []
        for page in pages:
            queue.append(
                {
                    "work_id": "vb-" + hashlib.sha256(
                        canonical_json({"p": record["source_path"], "pg": page})
                    ).hexdigest()[:32],
                    "source_path": record["source_path"],
                    "page_1based": page,
                }
            )
    done = set()
    if args.out.exists():
        for line in args.out.read_text().splitlines():
            try:
                done.add(json.loads(line).get("work_id"))
            except Exception:
                pass
    queue = [q for q in queue if q["work_id"] not in done]
    print(f"queue items: {len(queue)} (skipped {len(done)} done)")

    prompt = _prompt()
    args_dir = args.requests_dir
    args_dir.mkdir(parents=True, exist_ok=True)
    renders = args_dir / "renders"
    renders.mkdir(exist_ok=True)
    file_index = 0
    requests_in_file = 0
    sink = None
    emitted = 0
    for item in queue:
        pdf = Path(item["source_path"])
        if not pdf.exists():
            continue
        try:
            doc = pymupdf.open(pdf)
            if item["page_1based"] > doc.page_count:
                doc.close()
                continue
            page = doc[item["page_1based"] - 1]
            doc_sha = _sha256(pdf)
            regions = plot_regions_for_page(page)
            page_words = [str(w[4]) for w in page.get_text("words")]
            outputs = []
            for figure_index, region in enumerate(regions):
                bbox = region["bbox"]
                png = renders / f"{doc_sha[:16]}-p{item['page_1based']}-f{figure_index}.png"
                if not png.exists():
                    clip = pymupdf.Rect(*bbox) & page.rect
                    pixmap = page.get_pixmap(
                        matrix=pymupdf.Matrix(600 / 72.0, 600 / 72.0), clip=clip
                    )
                    pixmap.save(str(png))
                outputs.append(
                    {
                        "figure_index": figure_index,
                        "render_sha256": hashlib.sha256(png.read_bytes()).hexdigest(),
                        "image_b64": base64.b64encode(png.read_bytes()).decode("ascii"),
                        "region_source": region["source"],
                    }
                )
            doc.close()
        except Exception:
            continue
        for figure in outputs:
            if sink is None or requests_in_file >= args.chunk:
                if sink is not None:
                    sink.close()
                file_index += 1
                sink = (args_dir / f"requests-{file_index:04d}.jsonl").open("w")
                requests_in_file = 0
            custom_id = f"{item['work_id']}-f{figure['figure_index']}"
            request = {
                "custom_id": custom_id,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": MODEL,
                    "max_tokens": 16000,
                    "reasoning": {"effort": "low"},
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": "data:image/png;base64,"
                                        + figure["image_b64"]
                                    },
                                },
                            ],
                        }
                    ],
                },
            }
            sink.write(json.dumps(request, sort_keys=True) + "\n")
            requests_in_file += 1
            emitted += 1
            with (args_dir / "figure-meta.jsonl").open("a") as meta:
                meta.write(
                    json.dumps(
                        {
                            "custom_id": custom_id,
                            "work_id": item["work_id"],
                            "source_path": item["source_path"],
                            "page_1based": item["page_1based"],
                            "figure_index": figure["figure_index"],
                            "render_sha256": figure["render_sha256"],
                            "region_source": figure["region_source"],
                            "page_words": page_words[:1200],
                        },
                        sort_keys=True,
                    )
                    + "\n"
                )
    if sink is not None:
        sink.close()
    print(f"request files: {file_index} | requests emitted: {emitted}")
    return 0


def _or_request(path: str, data=None, key: str | None = None, raw=None, content_type="application/json"):
    headers = {"Content-Type": content_type}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    body = raw if raw is not None else (json.dumps(data).encode() if data else None)
    req = urllib.request.Request(OR_BASE + path, data=body, headers=headers)
    return json.load(urllib.request.urlopen(req, timeout=300))


def cmd_submit(args) -> int:
    key = _key()
    batch_ids = {}
    ids_path = args.requests_dir / "batch-ids.json"
    if ids_path.exists():
        batch_ids = json.loads(ids_path.read_text())
    for requests_file in sorted(args.requests_dir.glob("requests-*.jsonl")):
        stem = requests_file.stem
        if stem in batch_ids:
            continue
        boundary = "----m5batch"
        content = requests_file.read_bytes()
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\nbatch\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{stem}.jsonl\"\r\n"
            f"Content-Type: application/json\r\n\r\n"
        ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
        try:
            upload = _or_request(
                "/files", raw=body, key=key,
                content_type=f"multipart/form-data; boundary={boundary}",
            )
            batch = _or_request(
                "/batch",
                data={
                    "input_file_id": upload.get("id"),
                    "endpoint": "/v1/chat/completions",
                    "completion_window": "24h",
                },
                key=key,
            )
            batch_ids[stem] = {
                "file_id": upload.get("id"),
                "batch_id": batch.get("id"),
                "status": batch.get("status"),
                "requests": sum(1 for _ in requests_file.open()),
            }
            print(f"{stem}: batch {batch.get('id')} status={batch.get('status')} requests={batch_ids[stem]['requests']}")
            ids_path.write_text(json.dumps(batch_ids, indent=1))
            time.sleep(2)
        except urllib.error.HTTPError as exc:
            print(f"{stem}: HTTP {exc.code}: {exc.read()[:200]}")
            break
    return 0


def cmd_retrieve(args) -> int:
    from harness.electronics.local_model import parse_json_response, validate_response
    from harness.electronics.local_model import RESPONSE_SCHEMAS

    key = _key()
    ids_path = args.requests_dir / "batch-ids.json"
    batch_ids = json.loads(ids_path.read_text()) if ids_path.exists() else {}
    meta = {}
    meta_path = args.requests_dir / "figure-meta.jsonl"
    if meta_path.exists():
        for line in meta_path.read_text().splitlines():
            try:
                m = json.loads(line)
                meta[m["custom_id"]] = m
            except Exception:
                pass
    done_ids = set()
    if args.out.exists():
        for line in args.out.read_text().splitlines():
            try:
                done_ids.add(json.loads(line).get("custom_id"))
            except Exception:
                pass
    args.out.parent.mkdir(parents=True, exist_ok=True)
    sink = args.out.open("a")
    rows_written = 0
    for stem, info in batch_ids.items():
        if not info.get("batch_id") or stem in (args.skip or []):
            continue
        try:
            batch = _or_request(f"/batch/{info['batch_id']}", key=key)
        except Exception as exc:
            print(f"{stem}: poll error {exc}")
            continue
        status = batch.get("status")
        print(f"{stem}: {status}")
        if status != "completed":
            continue
        output_file = (batch.get("output_file_id") or {}).get("file_id") if isinstance(batch.get("output_file_id"), dict) else batch.get("output_file_id")
        if not output_file:
            print(f"{stem}: no output file id; keys={list(batch.keys())}")
            continue
        try:
            content = _or_request(f"/files/{output_file}/content", key=key)
        except Exception as exc:
            print(f"{stem}: content error {exc}")
            continue
        results = content if isinstance(content, list) else content.get("data") or []
        for entry in results:
            custom_id = entry.get("custom_id")
            if not custom_id or custom_id in done_ids:
                continue
            m = meta.get(custom_id)
            if not m:
                continue
            body = (entry.get("response") or {}).get("body") or {}
            choice = (body.get("choices") or [{}])[0]
            text = str((choice.get("message") or {}).get("content") or "")
            verdict = "retrieved"
            payload = {}
            problems = []
            missing = []
            try:
                payload = parse_json_response(text)
                if isinstance(payload, dict):
                    inner = payload.get("properties")
                    if isinstance(inner, dict) and ("axes" in inner or "series" in inner):
                        payload = inner
                points = payload.get("series") or []
                for series in points:
                    raw_pts = series.get("points") or []
                    series["points"] = [
                        {"x": p[0], "y": p[1]} if isinstance(p, (list, tuple)) and len(p) == 2 else p
                        for p in raw_pts
                    ]
                validate_response(payload, RESPONSE_SCHEMAS["typical_characteristics"])
                problems = validate_curve_payload(payload)
                ok, missing = axis_labels_grounded(payload, m.get("page_words") or [])
                if problems:
                    verdict = "rejected_structure"
                elif not ok:
                    verdict = "hold_ungrounded_axis"
                else:
                    verdict = "extracted"
            except Exception:
                verdict = "rejected_parse"
            row = {
                "schema": "harness.electronics-typical-curves.v1",
                "custom_id": custom_id,
                "work_id": m["work_id"],
                "document_sha256": None,
                "source_path": m["source_path"],
                "page_1based": m["page_1based"],
                "figure_index": m["figure_index"],
                "render_sha256": m["render_sha256"],
                "region_source": m["region_source"],
                "provider": "openrouter-batch",
                "model": MODEL,
                "payload": payload if verdict != "rejected_parse" else {"raw": text[:2000]},
                "verdict": verdict,
                "problems": problems,
                "ungrounded_labels": missing,
                "usage": body.get("usage"),
            }
            sink.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            rows_written += 1
            done_ids.add(custom_id)
        sink.flush()
    sink.close()
    print(f"rows written: {rows_written} (total in file: {sum(1 for _ in open(args.out))})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--l0-records", type=Path, required=True)
    p.add_argument("--doc-class", default="datasheet")
    p.add_argument("--out", type=Path, required=True, help="done-work marker file (queue state)")
    p.add_argument("--requests-dir", type=Path, required=True)
    p.add_argument("--chunk", type=int, default=100)
    p.set_defaults(func=cmd_prepare)
    p = sub.add_parser("submit")
    p.add_argument("--requests-dir", type=Path, required=True)
    p.set_defaults(func=cmd_submit)
    p = sub.add_parser("retrieve")
    p.add_argument("--requests-dir", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--skip", nargs="*", default=None)
    p.set_defaults(func=cmd_retrieve)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
