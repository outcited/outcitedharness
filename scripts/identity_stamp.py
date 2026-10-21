#!/usr/bin/env python3
"""identity_stamp.py — automatic part-identity stamping from page-1 evidence.

The identity law (PR15 review, 2026-10-20): a document sha is not an OPN.
This reads each document's first page, extracts (manufacturer, part_identity,
identity_source) via deterministic signals, and stamps every extraction row
for that sha. Rows that cannot be stamped keep identity: null and are
quarantined by the Rust bridge (MissingIdentity) — never guessed.

Signals (ordered by confidence, all deterministic):
  1. TI style:      "<OPN> [Microcontroller|Data Sheet|...]" on the first line
                    (TMS320F280015x, TM4C1233H6PZ)
  2. ST style:      "<PartSeries>\\n...Data Sheet" + DS-number
  3. Microchip:     "<MCP|PIC|dsPIC|ATSAM|AVR...><digits>..." after the
                    copyright line
  4. Espressif:     "ESP32-<X> | Technical Reference Manual"
  5. Vendor URL:    www.<vendor>.com on page 1 -> manufacturer only
  6. Manifest join: delivery manifests carry (manufacturer, source_url) —
                    the fallback when page 1 is ambiguous

Usage:
  python3 identity_stamp.py --rows mode.jsonl --manifests /path/ \
      --out mode_stamped.jsonl [--pdf-root /Volumes/M5_4TB/vault/cas]

Fail-closed: every row gets identity fields or an explicit
identity_status: "unresolved" with a reason. Never a sha prefix.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
import sys
from pathlib import Path

import pymupdf

# ---------------------------------------------------------------------------
# manufacturer signals
# ---------------------------------------------------------------------------

MFR_URL = re.compile(r"www\.([a-z0-9.-]+)\.(com|net|org|io)\b", re.I)
MFR_NAME = re.compile(
    r"(texas instruments|stmicroelectronics|microchip technology|infineon|"
    r"vishay|nexperia|onsemi|on semiconductor|renesas|rohm|toshiba|nxp|"
    r"analog devices|skyworks|qorvo|espressif|samsung|murata|tdk|yageo|"
    r"avx|kemet|samsung electro-mechanics|taiyo yuden|wurth|bourns|"
    r"alpha wire|bel fuse| littelfuse|te connectivity|molex|amphenol)",
    re.I,
)

URL_TO_MFR = {
    "ti.com": "texas_instruments", "st.com": "st", "microchip.com": "microchip",
    "infineon.com": "infineon", "vishay.com": "vishay", "nexperia.com": "nexperia",
    "onsemi.com": "onsemi", "renesas.com": "renesas", "rohm.com": "rohm",
    "toshiba.com": "toshiba", "nxp.com": "nxp", "analog.com": "analog_devices",
    "espressif.com": "espressif", "murata.com": "murata", "samsung.com": "samsung",
    "qorvo.com": "qorvo", "skyworksinc.com": "skyworks",
}

# ---------------------------------------------------------------------------
# part-identity signals (the OPN or declared family, as PRINTED)
# ---------------------------------------------------------------------------

# TI: "TMS320F280015x Real-Time Microcontrollers" / "TM4C1233H6PZ Microcontroller"
TI_OPN = re.compile(r"\b((?:TMS|TM4C|MSP|LM[0-9]|TPS|TLV|BQ|OPA|ADS|DAC|SN)[A-Z0-9]{3,12}[A-Z0-9x*/-]*)\b")
# ST: "STM32H747xI/G" / "STM32L071C8" / "GD32..." / "STM8..."
ST_OPN = re.compile(r"\b((?:STM32|STM8|GD32|ISOCORE)[A-Z0-9]{2,12}[A-Z0-9x/-]*(?:/[A-Z0-9x]+)?)\b")
# Microchip: "MCP6481/2/4" / "PIC32CZ" / "dsPIC33" / "ATSAM..." / "AVR..."
MC_OPN = re.compile(r"\b((?:MCP|PIC|dsPIC|ATSAM|ATMEGA|ATTINY|AVR|SST|CAT|MIC)[0-9][A-Z0-9]{2,12}(?:/[0-9]+)?)\b")
# Espressif: "ESP32-C5" / "ESP32-S3" / "ESP8266"
ESP_OPN = re.compile(r"\b(ESP[0-9]{2,4}-[A-Z0-9]{1,3})\b")
# Infineon: "OptiMOS 6" is a family, part numbers like "IAUC500N10S5N012"
INF_OPN = re.compile(r"\b((?:IAUC|IPB|IPD|IRF|BSC|BSZ|SI[SC])[0-9][A-Z0-9]{4,12})\b")
# Vishay: "WSK1206...18"
VISHAY_OPN = re.compile(r"\b(WSK|D2D|CRCW|PLT|MBRS|SMB|BAV|CMZ)[0-9][A-Z0-9.{2,8}]*[0-9A-Z]\b")
# NXP/Freescale: MPC5675K, MK60DN512, i.MX...
NXP_OPN = re.compile(r"\b((?:MPC|MK|MC9S|KE|KL|Kinetis|i\.MX)[A-Z0-9]{2,12})\b")
# ROHM: SH8K11, RQ5, etc
ROHM_OPN = re.compile(r"\b((?:SH|RQ|RP|RT|RB|EM|SCH|HR)[0-9][A-Z0-9]{2,8}[A-Z0-9]*)\b")
# Toshiba: TPD, TC7...
TOS_OPN = re.compile(r"\b((?:TPD|TC7|TLP|Toshiba)[A-Z0-9]{2,10})\b")
# Generic: uppercase-alnum with digits, 6-20 chars, on the first 2 lines
GENERIC_OPN = re.compile(r"\b([A-Z]{2,6}[0-9][A-Z0-9]{3,16})\b")

PART_PATTERNS = [TI_OPN, ST_OPN, MC_OPN, ESP_OPN, INF_OPN, VISHAY_OPN, NXP_OPN, ROHM_OPN, TOS_OPN]


def _first_opn(text_head: str) -> str | None:
    """First matching OPN-style token, by pattern confidence order."""
    lines = text_head.split("\n")[:6]
    head = "\n".join(lines)
    for pat in PART_PATTERNS:
        m = pat.search(head)
        if m:
            return m.group(1)
    # generic fallback: only from the FIRST line (title line)
    if lines:
        m = GENERIC_OPN.search(lines[0])
        if m:
            return m.group(1)
    return None


def _manufacturer(text_head: str) -> str | None:
    m = MFR_NAME.search(text_head)
    if m:
        raw = m.group(1).lower().strip()
        return {
            "texas instruments": "texas_instruments",
            "stmicroelectronics": "st",
            "microchip technology": "microchip",
            "on semiconductor": "onsemi",
            "analog devices": "analog_devices",
        }.get(raw, raw.replace(" ", "_"))
    m = MFR_URL.search(text_head)
    if m:
        domain = m.group(1).lower().rstrip(".")
        for key, mfr in URL_TO_MFR.items():
            host = key.split(".")[0]
            if key in domain or domain == host or domain.endswith("." + host):
                return mfr
    return None


def stamp_document(pdf_path: Path) -> dict:
    """Read page 1, extract (manufacturer, part, source). Deterministic."""
    data = pdf_path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        n = min(2, doc.page_count)
        text = "\n".join(doc[i].get_text() for i in range(n))
    finally:
        doc.close()

    # OPN lives in the title block (first lines); the manufacturer URL can
    # be on page 1 or 2 (footer, contact block) — search both pages
    lines = text.split("\n")
    title_head = "\n".join(lines[:12])
    mfr = _manufacturer(text) or _manufacturer(title_head)
    opn = _first_opn(title_head)

    if opn and mfr:
        return {
            "sha256": sha,
            "manufacturer": mfr,
            "part_identity": opn,
            "identity_source": "page_1_title_line",
            "identity_status": "stamped",
        }
    if opn:
        return {
            "sha256": sha,
            "manufacturer": mfr,
            "part_identity": opn,
            "identity_source": "page_1_title_line",
            "identity_status": "stamped_no_mfr" if mfr is None else "stamped",
        }
    return {
        "sha256": sha,
        "manufacturer": mfr,
        "part_identity": None,
        "identity_source": None,
        "identity_status": "unresolved",
        "reason": "no_opn_signal_on_page_1",
    }


def resolve_pdf(sha: str, roots: list[Path]) -> Path | None:
    for root in roots:
        for cand in (
            root / sha[:2] / f"{sha[:4]}" / f"{sha}.pdf",
            root / sha[:2] / f"{sha}.pdf",
        ):
            if cand.exists():
                return cand
        # the vault uses sha-prefix filenames (not always the full sha)
        if (root / sha[:2]).is_dir():
            for f in (root / sha[:2]).glob(f"{sha[:8]}*"):
                return f
    return None


def build_manifest_index(deliveries_root: Path) -> dict[str, dict]:
    """sha → manifest row (manufacturer, source_url). The delivery manifests
    are the authoritative secondary identity source: the source_url carries
    the part in its path for most vendors."""
    idx = {}
    for mf in sorted(deliveries_root.glob("*/corpus-delivery-manifest.jsonl")):
        with open(mf, errors="replace") as f:
            for line in f:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                sha = o.get("sha256")
                if sha and sha not in idx:
                    idx[sha] = {"manufacturer": o.get("manufacturer"),
                                "source_url": o.get("source_url"),
                                "category": o.get("category"),
                                "asset_type": o.get("asset_type")}
    return idx


def part_from_url(url: str) -> str | None:
    """Derive a part slug from the source URL path — the same rule the
    enrichment lane uses: last path segment, stripped of extensions and
    query strings."""
    from urllib.parse import urlsplit
    path = urlsplit(url).path
    stem = Path(path).stem
    stem = re.sub(r"[-_](en|datasheet|rev\d+.*|data[-_]?sheet)$", "", stem, flags=re.I)
    return stem if len(stem) >= 4 else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shas", help="file with one sha per line, or '-' for stdin")
    ap.add_argument("--out", required=True, help="output JSONL of stamp records")
    ap.add_argument("--pdf-root", action="append", default=[], help="cas root (repeatable)")
    ap.add_argument("--manifests", type=Path,
                    default=Path("/Volumes/M5_4TB/exports/corpus-deliveries"),
                    help="corpus-deliveries root for the manifest join")
    args = ap.parse_args()

    manifests = build_manifest_index(args.manifests)

    roots = [Path(r) for r in args.pdf_root] or [
        Path("/Volumes/M5_4TB/vault/cas"),
        Path("/Volumes/M5_4TB/exports/corpus-deliveries/datasheet-tail-20261007/blobs"),
    ]

    shas = [l.strip() for l in Path(args.shas).read_text().splitlines() if l.strip()] if args.shas else []
    stamped = unresolved = 0
    with open(args.out, "w") as out:
        for sha in shas:
            pdf = resolve_pdf(sha, roots)
            if not pdf:
                rec = {"sha256": sha, "identity_status": "unresolved", "reason": "pdf_not_found"}
                out.write(json.dumps(rec) + "\n")
                unresolved += 1
                continue
            try:
                rec = stamp_document(pdf)
            except Exception:
                rec = {"sha256": sha, "identity_status": "unresolved", "reason": "pdf_open_failed"}
            # Manifest join: secondary identity source. If page-1 didn't
            # find the part, the source_url path often carries it.
            if not rec.get("part_identity"):
                m = manifests.get(sha, {})
                url_part = part_from_url(m.get("source_url") or "")
                if url_part:
                    rec["part_identity"] = url_part
                    rec["identity_source"] = "manifest_url"
                    rec["identity_status"] = "stamped"
                elif m.get("manufacturer"):
                    rec["manufacturer"] = m["manufacturer"]
                    rec["identity_status"] = "mfr_only_unresolved_part"
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if rec.get("identity_status", "").startswith("stamped"):
                stamped += 1
            else:
                unresolved += 1
    print(json.dumps({"total": len(shas), "stamped": stamped, "unresolved": unresolved}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
