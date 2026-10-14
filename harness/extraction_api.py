"""Extraction data API v1.1 — extraction-derived rows served from our stack.

Ownership (Sam directive, 2026-10-07): m5-opencode owns extraction-to-API
for extraction-derived data (curves, vision, thermal, errata, power
modes) served from this endpoint; cursor-cr keeps the store, identity,
facade, and commerce surfaces. The facade joins us 1:1 on part_number.

Contract v1.1 (bump from v1, 2026-10-09):
  - curve rows are CORPUS-REAL where extraction landed (stub flag cleared);
    the shape stub is retired — parts without curve data get zero curve
    rows, never an invented one
  - atom_class typed on every row: curve | thermal | errata
  - curve rows carry typed conditions per series
    (harness.electronics-condition-model.v1 keys) and deterministic
    curve_type + axis quantity classification (harness/electronics/curve_atoms.py)
  - errata rows live: issue_id, category, symptom/workaround verbatim,
    affected silicon, device-scoped

Contract mirrors the facade's error + auth shape
(contracts/designwins-v1/openapi.yaml in outcited/outcited.com):
  - application/problem+json errors {type,title,status,code,...}
  - Authorization: Bearer crk_... registered in the facade key store
    (env CR_API_KEYS, comma-separated; any match passes)

  GET /healthz
  GET /api/extraction/v1/parts/{part_number}   ?atom_class=curve|thermal|errata
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.electronics.curve_atoms import (  # noqa: E402
    atoms_for_payload,
    conditions_for_series,
)

CONTRACT_VERSION = "extraction-v1.1"
CATALOG = os.environ.get(
    "DISCOVERY_CATALOG", "/Volumes/M5_4TB/extract-results/catalog.db")
KEYS = [k.strip() for k in os.environ.get(
    "CR_API_KEYS", "crk_dev_local_stub,crk_m5_extraction_v1").split(",")
    if k.strip()]

# curve lanes: (path, burn_id, extraction_model)
CURVE_LANES = [
    (ROOT / "results/power-v2-axes-20261006/power-curve-rows.jsonl",
     "power-v2-axes-20261006", "cloud-vision lane (deepseek-flash/mimo)"),
    (ROOT / "results/typical-curves-wave-a-20261006/tp4-rows.jsonl",
     "typical-curves-wave-a-20261006", "dsv41_flash (local TP4)"),
    (ROOT / "results/vision-backlog-batch-20261007/datasheet-vision-rows.jsonl",
     "vision-backlog-batch-20261007", "cloud-vision lane (deepseek-flash/mimo)"),
    (ROOT / "results/vision-backlog-batch-20261007/remaining-classes-rows.jsonl",
     "vision-backlog-batch-20261007", "cloud-vision lane (deepseek-flash/mimo)"),
    (ROOT / "results/vision-backlog-batch-20261007/remaining-classes-rows-A.jsonl",
     "vision-backlog-batch-20261007", "cloud-vision lane (deepseek-flash/mimo)"),
    (ROOT / "results/vision-backlog-batch-20261007/remaining-classes-rows-B.jsonl",
     "vision-backlog-batch-20261007", "cloud-vision lane (deepseek-flash/mimo)"),
    (ROOT / "results/vision-backlog-batch-20261007/chunk1-netnew-rows.jsonl",
     "vision-backlog-batch-20261007", "cloud-vision lane (deepseek-flash/mimo)"),
    (ROOT / "results/vision-backlog-batch-20261007/chunk1-netnew-rows-A.jsonl",
     "vision-backlog-batch-20261007", "cloud-vision lane (deepseek-flash/mimo)"),
    (ROOT / "results/vision-backlog-batch-20261007/chunk1-netnew-rows-B.jsonl",
     "vision-backlog-batch-20261007", "cloud-vision lane (deepseek-flash/mimo)"),
    (ROOT / "results/vision-backlog-batch-20261007/chunk-3-vision-rows.jsonl",
     "vision-backlog-batch-20261007", "cloud-vision lane (deepseek-flash/mimo)"),
    (ROOT / "results/vision-backlog-batch-20261007/chunk-4-vision-rows-A.jsonl",
     "vision-backlog-batch-20261007", "cloud-vision lane (deepseek-flash/mimo)"),
    (ROOT / "results/vision-backlog-batch-20261007/chunk-4-vision-rows-B.jsonl",
     "vision-backlog-batch-20261007", "cloud-vision lane (deepseek-flash/mimo)"),
    (ROOT / "results/vision-backlog-batch-20261007/chunk-4-delta-rows-A.jsonl",
     "vision-backlog-batch-20261007", "cloud-vision lane (deepseek-flash/mimo)"),
    (ROOT / "results/vision-backlog-batch-20261007/chunk-4-delta-rows-B.jsonl",
     "vision-backlog-batch-20261007", "cloud-vision lane (deepseek-flash/mimo)"),
    (ROOT / "results/hold-rescore-20261008/rescued.jsonl",
     "hold-rescore-20261008", "doc-grounded rescore of cloud-vision read"),
    (ROOT / "results/vision-qwen-retry-20261008/retry2-rows.jsonl",
     "vision-qwen-retry-20261008", "qwen3.8-max"),
]

ERRATA_LANES = [
    ROOT / "results/errata-wave-c-20261006/errata-rows-v3-scoped.jsonl",
    ROOT / "results/errata-pcn-20261009/errata-rows.jsonl",
]

# Global part-stem -> [doc sha] from corpus delivery manifests: the join for
# parts outside the 1,882-part power catalog (the 26K+ staging store).
MANIFEST_GLOB = "/Volumes/M5_4TB/exports/corpus-deliveries/*/*.jsonl"
PART_SHAS: dict[str, list[str]] = defaultdict(list)

_THERMAL_SYMBOLS = {
    "RTHJA": {"atom_id": "thermal_rtheta_ja", "quantity": "RθJA",
              "unit": "°C/W"},
    "RTHJC": {"atom_id": "thermal_rtheta_jc", "quantity": "RθJC",
              "unit": "°C/W"},
    "RTH(J-C)": {"atom_id": "thermal_rtheta_jc", "quantity": "RθJC",
                 "unit": "°C/W"},
    "RTHJB": {"atom_id": "thermal_rtheta_jb", "quantity": "RθJB",
              "unit": "°C/W"},
}

# sha -> [curve api rows]; token -> [errata api rows]
CURVES_BY_SHA: dict[str, list[dict]] = defaultdict(list)
ERRATA_BY_TOKEN: dict[str, list[dict]] = defaultdict(list)
LOAD_STATS: dict[str, int] = {}


def _series_points(series: dict) -> list[list]:
    pts = []
    for p in series.get("points") or []:
        if isinstance(p, dict):
            x, y = p.get("x"), p.get("y")
        elif isinstance(p, (list, tuple)) and len(p) == 2:
            x, y = p
        else:
            continue
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            pts.append([x, y])
    return pts


def _curve_api_row(r: dict, burn_id: str, model: str) -> dict | None:
    payload = r.get("payload") or {}
    series_in = payload.get("series") or []
    series_out = []
    for s in series_in:
        pts = _series_points(s)
        if not pts:
            continue
        series_out.append({
            "name": s.get("name"),
            "condition_verbatim": s.get("condition") or s.get("condition_verbatim"),
            "conditions": conditions_for_series(s),
            "points": pts,
        })
    if not series_out:
        return None
    atoms = atoms_for_payload(payload)
    sha = r.get("document_sha256") or r.get("pdf_sha")
    first_cond = (series_out[0].get("conditions") or {}).get("keys")
    return {
        "row_id": f"curve-{(r.get('work_id') or sha or 'x')[:24]}-p{r.get('page_1based')}-f{r.get('figure_index')}",
        "part_number": None,  # filled at serve time
        **atoms,
        "conditions": first_cond if len(series_out) == 1 else None,
        "conditions_note": None if len(series_out) == 1 else "per-series (series[].conditions)",
        "series": series_out,
        "series_count": len(series_out),
        "page": r.get("page_1based"),
        "figure_index": r.get("figure_index"),
        "extraction_model": model,
        "burn_id": burn_id,
        "pdf_sha": sha,
        "render_sha": r.get("render_sha256"),
        "render_dpi": r.get("render_dpi"),
        "anchor_check": r.get("anchor_check"),
        "evidence": {"quote": payload.get("title"),
                     "source_path": r.get("source_path")},
        "stub": False,
    }


def load_rows() -> None:
    n_curves = n_errata = 0
    import glob as _glob
    for mf in _glob.glob(MANIFEST_GLOB):
        for line in open(mf):
            if '"sha256"' not in line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            sha, url = r.get("sha256"), r.get("source_url")
            if sha and url:
                stem = Path(str(url).split("?")[0]).stem
                key = stem[3:].upper() if stem.startswith("ti-") else stem.upper()
                if sha not in PART_SHAS[key]:
                    PART_SHAS[key].append(sha)
    for path, burn_id, model in CURVE_LANES:
        if not path.exists():
            continue
        for line in path.open():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("verdict") != "extracted":
                continue
            sha = r.get("document_sha256") or r.get("pdf_sha")
            if not sha:
                continue
            row = _curve_api_row(r, burn_id, model)
            if row:
                CURVES_BY_SHA[sha].append(row)
                n_curves += 1
                sp = r.get("source_path") or ""
                stem = Path(sp).stem
                if stem:
                    key = stem[3:].upper() if stem.startswith("ti-") else stem.upper()
                    if sha not in PART_SHAS[key]:
                        PART_SHAS[key].append(sha)
    # staging per-part curve files: richest part->sha join (covers every
    # part whose curves were indexed into the parts API)
    import glob as _glob2
    for st_file in _glob2.glob(
            "/Volumes/M5_4TB/staging/datasheet_extractions/*/"
            "datasheet_extractions/*.json"):
        try:
            d = json.loads(Path(st_file).read_text())
        except Exception:
            continue
        curves = d.get("curves")
        pn = d.get("part_number") or Path(st_file).stem
        if not isinstance(curves, list) or not pn:
            continue
        key = str(pn).upper()
        for c in curves:
            sha = c.get("pdf_sha")
            if sha and sha not in PART_SHAS[key]:
                PART_SHAS[key].append(sha)
    for errata_lane in ERRATA_LANES:
        if not errata_lane.exists():
            continue
        for line in errata_lane.open():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("record_kind") != "issue":
                continue
            scope = r.get("device_scope") or {}
            tokens = scope.get("device_tokens") or []
            row = {
                "row_id": f"errata-{r.get('issue_id')}-{(r.get('pdf_sha') or '')[:12]}",
                "atom_class": "errata",
                "issue_id": r.get("issue_id"),
                "category": r.get("category"),
                "title_verbatim": r.get("title_verbatim"),
                "symptom_verbatim": r.get("symptom_verbatim"),
                "workaround_verbatim": r.get("workaround_verbatim"),
                "affected_silicon": r.get("affected_silicon"),
                "device_scope": scope,
                "page": None,
                "extraction_model": "errata deterministic lane",
                "burn_id": errata_lane.parent.name,
                "pdf_sha": r.get("pdf_sha") or r.get("document_sha256"),
                "source_url": r.get("source_url"),
                "evidence": {"quote": r.get("symptom_verbatim")},
                "stub": False,
            }
            for t in tokens:
                ERRATA_BY_TOKEN[str(t).upper()].append(row)
            n_errata += 1
    LOAD_STATS.update({"curve_rows": n_curves, "errata_rows": n_errata,
                       "curve_docs": len(CURVES_BY_SHA),
                       "errata_tokens": len(ERRATA_BY_TOKEN),
                       "manifest_parts": len(PART_SHAS)})
    print(f"[extraction-api v1.1] loaded {n_curves} curve rows "
          f"({len(CURVES_BY_SHA)} docs), {n_errata} errata rows "
          f"({len(ERRATA_BY_TOKEN)} tokens), "
          f"{len(PART_SHAS)} manifest part joins")


def _problem(status: int, code: str, title: str, detail: str = "") -> bytes:
    return json.dumps({
        "type": "https://designwins.ai/problems/" + code,
        "title": title, "status": status, "code": code,
        "detail": detail, "retryable": False, "release_id": None,
    }).encode()


def _thermal_rows(con: sqlite3.Connection, part: str) -> list[dict]:
    rows = []
    for sym, meta in _THERMAL_SYMBOLS.items():
        for r in con.execute(
                "SELECT c.value, c.condition_norm, c.unit, c.provenance,"
                " c.doc_sha256 FROM claims_canonical c WHERE c.opn=? AND"
                " c.symbol=?", (part, sym)):
            prov = json.loads(r["provenance"])
            rows.append({
                "row_id": f"thermal-{meta['atom_id']}-{r['value']}",
                "atom_class": "thermal",
                "atom_id": meta["atom_id"],
                "quantity": meta["quantity"],
                "unit": r["unit"] or meta["unit"],
                "value": r["value"],
                "conditions": r["condition_norm"] or None,
                "page": prov.get("page"),
                "extraction_model": "power lane readers (deterministic)",
                "burn_id": "burn-power-v1",
                "pdf_sha": r["doc_sha256"],
                "evidence": {"quote": prov.get("quote"),
                             "row_header": prov.get("row_header"),
                             "column_header": prov.get("column_header")},
                "stub": False,
            })
    return rows


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self) -> bool:
        header = self.headers.get("Authorization", "")
        kind, _, token = header.partition(" ")
        return kind.lower() == "bearer" and token.strip() in KEYS

    def do_GET(self):
        route = self.path.partition("?")[0]
        if route == "/healthz":
            return self._send(200, json.dumps(
                {"status": "ok", "contract_version": CONTRACT_VERSION,
                 **LOAD_STATS}).encode(), "application/json")
        m = re.match(r"^/api/extraction/v1/parts/([^/]+)$", route)
        if not m:
            return self._send(404, _problem(
                404, "not_found", "unknown route"), "application/problem+json")
        if not self._authed():
            return self._send(401, _problem(
                401, "unauthenticated",
                "missing or invalid crk_ bearer key"),
                "application/problem+json")
        part = m.group(1).upper()
        qs = dict(p.split("=", 1) for p in
                  self.path.partition("?")[2].split("&") if "=" in p)
        want = qs.get("atom_class")

        con = sqlite3.connect(f"file:{CATALOG}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT opn, vendor, family, package FROM parts"
                          " WHERE opn=?", (part,)).fetchone()
        rows = []
        if row is None:
            # not in the 1,882-part power catalog: still serve curve/errata
            # rows when the part token joins our extraction indexes
            vendor = family = None
            part_shas: list[str] = list(PART_SHAS.get(part, []))
        else:
            vendor, family = row["vendor"], row["family"]
            part_shas = [r["doc_sha256"] for r in con.execute(
                "SELECT DISTINCT doc_sha256 FROM part_sources WHERE opn=?",
                (part,))]
            if not part_shas:
                one = con.execute(
                    "SELECT doc_sha256 FROM claims_canonical WHERE opn=?"
                    " LIMIT 1", (part,)).fetchone()
                if one:
                    part_shas = [one["doc_sha256"]]
        # ALWAYS union with our own joins (manifest stems, curve-lane source
        # stems, staging curve files): the catalog's shas point at cr-core's
        # document copies, ours at the delivery/parametrics corpus — same
        # datasheet, different file, different sha
        for sha in PART_SHAS.get(part, []):
            if sha not in part_shas:
                part_shas.append(sha)

        if want in (None, "thermal") and row is not None:
            rows.extend(_thermal_rows(con, part))
        if want in (None, "curve"):
            seen_row_ids = set()
            for sha in part_shas:
                for cr in CURVES_BY_SHA.get(sha, []):
                    if cr["row_id"] in seen_row_ids:
                        continue
                    seen_row_ids.add(cr["row_id"])
                    rows.append({**cr, "part_number": part})
        if want in (None, "errata"):
            for er in ERRATA_BY_TOKEN.get(part, []):
                rows.append({**er, "part_number": part})
        con.close()

        if row is None and not rows:
            return self._send(404, _problem(
                404, "not_found", f"part {part} not in corpus"),
                "application/problem+json")

        body = json.dumps({
            "contract_version": CONTRACT_VERSION,
            "part_number": part,
            "vendor": vendor, "family": family,
            "pdf_sha": part_shas[0] if part_shas else None,
            "row_count": len(rows),
            "coverage_note": (
                "all rows corpus-real (stub retired in v1.1); zero rows for "
                "an atom_class means no extraction landed for this part yet, "
                "never invented data"),
            "rows": rows,
        }, ensure_ascii=False).encode()
        return self._send(200, body, "application/json")


def main(listen=None):
    load_rows()
    host = os.environ.get("EXTRACTION_HOST", "127.0.0.1")
    port = int(os.environ.get("EXTRACTION_PORT", "8792"))
    print(f"extraction api on {host}:{port} (keys: {len(KEYS)})")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
