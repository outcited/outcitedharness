#!/usr/bin/env python3
"""PDF text-extraction worker. Runs on any box with python3 + pymupdf.

Watches ~/extract-jobs/incoming/ for PDFs, writes text JSON to
~/extract-jobs/out/, bad files to ~/extract-jobs/err/. Idempotent, loops.
"""

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import pymupdf

HOME = Path.home()
IN = HOME / "extract-jobs" / "incoming"
OUT = HOME / "extract-jobs" / "out"
ERR = HOME / "extract-jobs" / "err"
EXTRACTOR = "extract_worker"
EXTRACTOR_VERSION = "2.1.0"

WORD = re.compile(r"[A-Za-z]{3,}")
NUM = re.compile(r"\d")


def text_quality(text: str) -> dict:
    stripped = text.strip()
    n = len(stripped)
    words = WORD.findall(stripped)
    alnum = sum(c.isalnum() for c in stripped)
    uniq_words = len({w.lower() for w in words})
    has_numbers = bool(NUM.search(stripped))
    good = n >= 400 and (alnum / n if n else 0) > 0.55 and len(words) >= 40 and uniq_words >= 20 and has_numbers
    return {"chars": n, "words": len(words), "uniq_words": uniq_words,
            "alnum_ratio": round(alnum / n, 3) if n else 0, "quality_ok": good}


def sha256(path, buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(buf):
            h.update(chunk)
    return h.hexdigest()


def process(pdf: Path):
    doc = pymupdf.open(pdf)
    pages = []
    labels = {}
    total = 0
    for i, page in enumerate(doc):
        try:
            text = page.get_text("text")
        except Exception:
            text = ""
        try:
            label = page.get_label()
        except Exception:
            label = None
        if label:
            labels[str(i + 1)] = label
        total += len(text.strip())
        pages.append({"page": i + 1, "label": label, "text": text})
    joined = "\n".join(p["text"] for p in pages)
    quality = text_quality(joined)
    rec = {
        "sha256": sha256(pdf),
        "filename": pdf.name,
        "page_count": len(pages),
        "text_chars": total,
        "text_quality": quality,
        "needs_ocr": not quality["quality_ok"],
        "producer": doc.metadata.get("producer"),
        "extractor": EXTRACTOR,
        "extractor_version": EXTRACTOR_VERSION,
        "page_labels": labels,
        "pages": pages,
    }
    doc.close()
    tmp = OUT / f".{pdf.stem}.json"
    final = OUT / f"{pdf.stem}.json"
    tmp.write_text(json.dumps(rec, ensure_ascii=False))
    tmp.rename(final)
    pdf.unlink()


def main():
    for d in (IN, OUT, ERR):
        d.mkdir(parents=True, exist_ok=True)
    print(f"worker up: in={IN}", flush=True)
    while True:
        pdfs = sorted(IN.glob("*.pdf"))
        if not pdfs:
            time.sleep(2)
            continue
        for pdf in pdfs[:64]:
            try:
                process(pdf)
            except Exception as e:
                print(f"ERR {pdf.name}: {e}", flush=True)
                try:
                    pdf.rename(ERR / pdf.name)
                except OSError:
                    pass


if __name__ == "__main__":
    sys.exit(main())
