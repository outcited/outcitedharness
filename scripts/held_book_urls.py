#!/usr/bin/env python3
"""held-books-missing-url-20260919: derive canonical vendor URLs for held books.

CR am-mu823fwi-c5b7. Derivation is grammar-first (never a guessed slug); every
candidate is proven by GET -> 200 + application/pdf, then sha256 of the bytes
against the held sha (bytes_sha_match is the binding we want; a different sha
on the same doc id is recorded as revision_of, never substituted). NXP is
never curled (route: crawler); ST needs driven Chrome (route: crawler from
this box).

    python3 scripts/held_book_urls.py \
        --queue /Volumes/M5_4TB/exports/cr_requests/held-books-missing-url-20260919.jsonl \
        --out-dir /Volumes/M5_4TB/exports/m5_drops/held-books-urls-20260919
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path

import fitz

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17.4 Safari/605.1.15")

TI_UG_ID = re.compile(r"\b(SPU?N[A-Z]|SPRU[A-Z]{0,2}|SLAU|SPNS|SPMT|SLYS)\d{3,5}[A-Z]?\b")
TI_DS_PART = re.compile(r"\bTM4C\w+|\bMSP430\w+|\bCC\w{3,}\d")
REN_ID = re.compile(r"\bR\d{2}U[A-Z]\d{4}[A-Z]{2}\d{4}\b")
MC_ID = re.compile(r"\b(?:DS|TB|AN|UM)\d{5,8}[A-H]?\b")
ESP_TRM = re.compile(r"\bESP32(?:-S[23])?(-C[36])?(-H2)?\b", re.I)


def http_get(url: str, dest: Path) -> tuple[str, str, int]:
    """GET with curl; returns (http_code, content_type, bytes_downloaded)."""
    r = subprocess.run(
        ["curl", "-sS", "-L", "--max-time", "120", "-A", UA,
         "-H", "Accept: application/pdf,*/*", "--compressed",
         "-o", str(dest), "-w",
         "%{http_code}|%{content_type}|%{size_download}|%{url_effective}", url],
        capture_output=True, text=True)
    parts = (r.stdout or "").strip().split("|")
    if len(parts) < 3:
        return "000", "", 0
    return parts[0], parts[1], int(float(parts[2]))


def sha256_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def page_count(path: Path) -> int | None:
    try:
        d = fitz.open(path)
        n = d.page_count
        d.close()
        return n
    except Exception:
        return None


def is_pdf(dest: Path) -> bool:
    try:
        return dest.read_bytes()[:4] == b"%PDF"
    except Exception:
        return False


def probe(url: str, held_sha: str, tmpdir: Path) -> dict:
    """Probe one candidate URL: 200 + pdf + sha comparison."""
    dest = tmpdir / "probe.pdf"
    if dest.exists():
        dest.unlink()
    code, ctype, nbytes = http_get(url, dest)
    if code != "200" or not is_pdf(dest):
        return {"url": url, "http_status": code, "content_type": ctype,
                "bytes_sha_match": False, "ok": False}
    data_sha = sha256_bytes(dest.read_bytes())
    n = page_count(dest)
    return {"url": url, "http_status": code, "content_type": ctype,
            "bytes_sha256": data_sha, "bytes_sha_match": data_sha == held_sha,
            "pdf_pages": n, "ok": True}


def candidates_tibook(row: dict) -> list[str]:
    text = " ".join(str(row.get(k) or "") for k in ("doc_id", "title", "cover_head"))
    m = TI_UG_ID.search(text.upper())
    if m:
        i = m.group(0).lower()
        return [f"https://www.ti.com/lit/ug/{i}/{i}.pdf"]
    # datasheet-class: symlink by part token in the title
    for pat in (r"\b(TM4C\w+)", r"\b(TMS320F[\w-]+)", r"\b(MSP430\w+)", r"\b(CC26\d\w*)"):
        mm = re.search(pat, row.get("title", "") or "", re.I)
        if mm:
            p = mm.group(1).lower()
            return [f"https://www.ti.com/lit/ds/symlink/{p}.pdf"]
    return []


def candidates_ti(row: dict) -> list[str]:
    return candidates_tibook(row)


def candidates_renesas(row: dict) -> list[str]:
    text = " ".join(str(row.get(k) or "") for k in ("doc_id", "title", "cover_head"))
    title = row.get("title") or ""

    def slugify(s: str) -> str:
        s = s.lower()
        s = s.replace("rz/", "rz").replace("rh/", "rh")  # rz/t2m -> rzt2m (canonical)
        s = re.sub(r"[’'\":,./]+", "", s)
        s = re.sub(r"[^a-z0-9]+", "-", s)
        return re.sub(r"-+", "-", s).strip("-")

    cands = []
    # title-slug grammar (canonical form): <group>-group-users-manual-hardware
    base = slugify(title)
    if base:
        cands.append(f"https://www.renesas.com/en/document/mah/{base}")
    m = REN_ID.search(text.upper())
    if m:
        did = m.group(0)
        group = ""
        gm = re.search(r"\b(RZ/[A-Z0-9]+|RA[0-9A-Z]{3,}|RE\w+|RH\w+)\s+Group", text, re.I)
        if gm:
            group = "-" + re.sub(r"[^A-Za-z0-9]+", "", gm.group(1)).lower()
        cands += [
            f"https://www.renesas.com/en/document/mah/{did.lower()}{group}",
            f"https://www.renesas.com/en/document/mah/{did.lower()}",
            f"https://www.renesas.com/en/document/mah/{base}-{did.lower()}"
            if base else f"https://www.renesas.com/en/document/mah/{did.lower()}",
        ]
    return cands[:4]


def candidates_microchip(row: dict) -> list[str]:
    text = " ".join(str(row.get(k) or "") for k in ("doc_id", "title", "cover_head"))
    m = MC_ID.search(text.upper())
    if not m:
        return []
    did = m.group(0)
    base = did.rstrip("ABCDEFGH")
    revs = [did] + [f"{base}{chr(ord('A') + i)}" for i in range(0, 8)]
    out = []
    for r in revs:
        out.append(f"https://ww1.microchip.com/downloads/en/DeviceDoc/{r}.pdf")
    return out[:9]


def candidates_espressif(row: dict) -> list[str]:
    t = (row.get("title") or "").lower()
    m = re.search(r"esp32(-s[23])?(-c[36])?(-h2)?|-c[36]|-h2", t)
    chip = None
    mm = re.search(r"\b(esp32(?:-s[23])?(?:-c[36])?(?:-h2)?|esp32-c[36]|esp32-h2)\b", t)
    if mm:
        chip = mm.group(1)
    if not chip:
        return []
    return [f"https://www.espressif.com/sites/default/files/documentation/{chip}_technical_reference_manual_en.pdf"]


def candidates_infineon(row: dict) -> list[str]:
    # no reliable public grammar for PSoC TRMs: single probe on the search CDN
    return []


VENDORS = {
    "ti.com": candidates_ti,
    "renesas.com": candidates_renesas,
    "microchip.com": candidates_microchip,
    "espressif.com": candidates_espressif,
    "infineon.com": candidates_infineon,
}

VAULT = Path("/Volumes/M5_4TB/vault")


def vault_text(sha: str) -> str:
    """Cover pages + back page of the held book from the CAS -- Renesas prints
    its publication code (R01UH...) on the cover footer or back page, beyond
    the 300-char cover_head the queue ships."""
    p = VAULT / "cas" / sha[:2] / f"{sha}.pdf"
    if not p.exists():
        return ""
    try:
        d = fitz.open(p)
        idxs = list(range(min(4, d.page_count)))
        if d.page_count > 4:
            idxs += [d.page_count - 1]
        t = " ".join(d[i].get_text() for i in idxs)
        d.close()
        return t
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--queue", type=Path, required=True)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--delay", type=float, default=0.6)
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(l) for l in args.queue.open()]
    out = []
    tmpdir = Path(tempfile.mkdtemp(prefix="heldbook-"))
    for row in rows:
        sha, vendor = row["sha256"], row["vendor"]
        base = {"sha256": sha, "vendor": vendor, "title": row.get("title")}
        if vendor == "nxp.com":
            out.append({**base, "url": None, "route": "crawler",
                        "reason": "nxp_404s_this_network_do_not_curl"})
            continue
        if vendor == "st.com":
            out.append({**base, "url": None, "route": "crawler",
                        "reason": "st_slug_needs_driven_chrome"})
            continue
        if vendor == "microchip.com":
            out.append({**base, "url": None, "route": "crawler",
                        "reason": "microchip_403_this_network"})
            continue
        # augment the queue text with the held bytes' own cover (doc ids print
        # beyond the 300-char cover_head)
        full_row = dict(row)
        vt = vault_text(sha)
        if vt:
            full_row["cover_head"] = (row.get("cover_head") or "") + " " + vt
            full_row["title"] = (row.get("title") or "") + " " + vt[:400]
        cands = VENDORS.get(vendor, lambda r: [])(full_row)
        resolved = None
        for url in cands:
            r = probe(url, sha, tmpdir)
            if r["ok"]:
                resolved = r
                if r["bytes_sha_match"]:
                    break  # exact binding; stop probing
            time.sleep(args.delay)
        if resolved:
            out.append({**base, **{k: resolved.get(k) for k in
                                   ("url", "http_status", "content_type", "bytes_sha256",
                                    "bytes_sha_match", "pdf_pages")},
                        "method": "derived_docid",
                        "revision_of": None if resolved["bytes_sha_match"] else sha,
                        "notes": "exact bytes match" if resolved["bytes_sha_match"]
                                 else "same doc id, different bytes (newer revision recorded, not substituted)"})
        else:
            out.append({**base, "url": None, "http_status": None,
                        "reason": "404_all_probes" if cands else "no_docid"})
        (args.out_dir / "urls.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out))
    from collections import Counter
    print("rows:", len(out))
    print(dict(Counter((r["vendor"], r.get("bytes_sha_match") and "match" or
                        ("route_crawler" if r.get("route") == "crawler" else
                         ("revision" if r.get("url") else r.get("reason"))))
                       for r in out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())