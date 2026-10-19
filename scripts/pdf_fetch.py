#!/usr/bin/env python3
"""Shared PDF source resolution for lane scripts.

Lanes may run on boxes without the corpus mounted: sources that start with
http(s):// are fetched into memory and opened from the stream; everything
else opens by path. One place, same law as burn_pass's scan_one.
"""

from __future__ import annotations

import urllib.parse
import urllib.request
from pathlib import Path

import pymupdf


def fetch_bytes(source: str | Path, timeout: int = 120) -> bytes:
    s = str(source)
    if s.startswith("http://") or s.startswith("https://"):
        parts = urllib.parse.urlsplit(s)
        url = urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, urllib.parse.quote(parts.path), parts.query, "")
        )
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()
    return Path(s).read_bytes()


def open_pdf(source: str | Path, timeout: int = 120):
    data = fetch_bytes(source, timeout=timeout)
    return pymupdf.open(stream=data, filetype="pdf")
