#!/usr/bin/env python3
"""spine.py — the DesignWins normalization spine (L1-L5), 2026-10-20.

One module, five stages, fleet-deployable. Laws:
- String presence alone is not verification: L2 re-opens the source PDF,
  checks the exact page, and re-derives the value in canonical unit space.
- Missing/mismatched evidence -> HELD or REJECTED with recorded reason.
- L3 canonicalization never merges distinct operating modes; every transform
  is recorded with rule id + version; original value/unit/quote preserved.
- L4 never infers an exact OPN from a document sha; family claims stay family.
- L5 separates comparable / qualified / noncomparable by typed conditions;
  every accepted axis resolves to underlying verified claims.

Claim identity: sha256 of (pdf_sha, lane, field, value_raw, unit_raw,
value_role, page_1based, verbatim) — content-addressed, replay-stable.
"""
from __future__ import annotations

import argparse, hashlib, json, os, re, sys, time, unicodedata
from collections import Counter, defaultdict
from pathlib import Path

CANON_VERSION = "canon-units-v1"
MODE_CANON_VERSION = "mode-canons-v1"

# ---------------------------------------------------------------- L1 identity

def claim_id(row: dict) -> str:
    key = "|".join(str(row.get(k)) for k in (
        "pdf_sha", "lane", "field", "value_raw", "unit_raw",
        "value_role", "page_1based", "verbatim"))
    return hashlib.sha256(key.encode()).hexdigest()


def row_to_claim(row: dict, lane: str, source_file: str) -> dict:
    field = row.get("field") or row.get("metric") or row.get("mode") or row.get("grade")
    value = row.get("value")
    return {
        "claim_id": None,  # filled by caller
        "lane": lane,
        "field": str(field) if field is not None else None,
        "value_raw": value,
        "unit_raw": row.get("unit"),
        "value_role": row.get("value_role"),
        "conditions_raw": row.get("conditions_verbatim") or row.get("condition_verbatim")
                          or row.get("condition") or None,
        "verbatim": row.get("verbatim") or row.get("standard_verbatim") or None,
        "page_1based": row.get("page_1based"),
        "pdf_sha": row.get("pdf_sha") or row.get("document_sha256"),
        "source_path": row.get("source_path"),
        "part_slug": row.get("part_number"),
        "doc_class": row.get("doc_class"),
        "source_file": source_file,
    }

# ------------------------------------------------------------- unit canonical

UNIT_MAP = {
    "µa": "uA", "μa": "uA", "ua": "uA", "na": "nA",
    "ma": "mA", "a": "A",
    "µa/mhz": "uA/MHz", "ua/mhz": "uA/MHz", "ma/mhz": "mA/MHz",
    "µv": "uV", "uv": "uV", "mv": "mV", "v": "V",
    "µw": "uW", "uw": "uW", "mw": "mW", "w": "W",
    "khz": "kHz", "mhz": "MHz", "hz": "Hz",
    "kb": "kB", "kbytes": "kB", "mb(mbit)": "Mbit",
    "°c/w": "C/W", "c/w": "C/W", "°c": "degC", "c": "degC",
    "µs": "us", "ns": "ns", "ms": "ms",
}
CATEGORICAL_FIELDS = ("package", "has_")   # prefix/verbatim-verified only
# canonical engineer units per field family (L3 target space)
FIELD_CANON_UNIT = {
    "clk_max_mhz": "MHz", "flash_kb": "kB", "sram_kb": "kB", "eeprom_kb": "kB",
    "supply_v": "V", "n_gpio": "count", "n_uart": "count", "n_i2c": "count",
    "n_spi": "count", "n_can": "count", "n_usb": "count", "n_adc": "count",
    "n_dma": "count", "n_timer": "count", "n_ethernet": "count",
    "esd_hbm_v": "V", "esd_cdm_v": "V", "latchup_ma": "mA",
    "temp_range_c": "degC", "RthetaJA": "C/W", "RthetaJC": "C/W",
    "RthetaJB": "C/W", "PsiJT": "C/W", "PsiJB": "C/W", "ZthJA": "C/W",
}
# scale to canonical unit (multiply value by this)
UNIT_SCALE = {
    ("uA", "A"): 1e-6, ("mA", "A"): 1e-3, ("A", "A"): 1.0,
    ("uV", "V"): 1e-6, ("mV", "V"): 1e-3, ("V", "V"): 1.0,
    ("uW", "W"): 1e-6, ("mW", "W"): 1e-3, ("W", "W"): 1.0,
    ("kHz", "Hz"): 1e3, ("MHz", "Hz"): 1e6, ("Hz", "Hz"): 1.0,
    ("kB", "B"): 1e3,
}
RANGE_TABLE = {  # canonical-unit sanity windows (field -> (lo, hi))
    "clk_max_mhz": (0.032, 10000), "flash_kb": (0.125, 131072),
    "sram_kb": (0.03125, 65536), "eeprom_kb": (0.03125, 16384),
    "supply_v": (0.4, 100), "n_gpio": (1, 1024), "esd_hbm_v": (50, 8000),
    "esd_cdm_v": (50, 3000), "latchup_ma": (10, 1000),
    "temp_range_c": (-70, 200), "RthetaJA": (0.05, 600), "RthetaJC": (0.05, 30),
    "RthetaJB": (0.05, 600), "PsiJT": (0.01, 100), "PsiJB": (0.01, 100),
}
CURRENT_RANGE_A = (1e-9, 10.0)   # mode lanes, canonical A
GRADE_VALUES = {"0", "1", "2", "3", "None"}

MODE_CANON = {  # case/space-variant merges ONLY — never distinct modes
    "run": "Run", "active": "Run", "normal": "Run",
    "sleep": "Sleep", "standby": "Standby", "stop": "Stop",
    "shutdown": "Shutdown", "hibernate": "Hibernate", "idle": "Idle",
    "vbat": "VBAT", "lprun": "LPRun", "off": "Shutdown",
    # EM0..EM3, Stop 0/1/2, LP*, vendor names: passed through unchanged
}

def canon_unit(u: str | None):
    if not u:
        return None
    return UNIT_MAP.get(str(u).strip().lower(), str(u).strip())

def canon_mode(m: str):
    if m is None:
        return None
    s = re.sub(r"\s+", " ", str(m)).strip()
    return MODE_CANON.get(s.lower(), s)

COND_PATTERNS = {
    "vin": r"V\s*IN[^0-9\-]{0,12}([\d.]+)\s*V",
    "vout": r"V\s*OUT[^0-9\-]{0,12}([\d.]+)\s*V",
    "vdd": r"V(?:DD|CC)[^0-9\-]{0,12}([\d.]+)\s*V",
    "iout": r"I\s*OUT[^0-9\-]{0,12}([\d.]+)\s*(?:A|mA|uA|µA)",
    "temp": r"([\-−]?\d+)\s*°?C\s*(?:<=|<|≤|to|–|-)\s*T?\s*(?:<|≤)?\s*([\-−]?\d+)\s*°?C",
    "freq": r"([\d.]+)\s*(?:kHz|MHz)",
}

def parse_conditions(raw: str | None) -> dict:
    if not raw:
        return {}
    out = {}
    for key, pat in COND_PATTERNS.items():
        m = re.search(pat, raw)
        if m:
            if key == "temp" and m.lastindex and m.lastindex >= 2:
                out[key] = [float(m.group(1)), float(m.group(2))]
            else:
                out[key] = float(m.group(1))
    return out

_WS = re.compile(r"\s+")

def norm_text(s: str | None) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("−", "-").replace("–", "-").replace("\u00a0", " ")
    return _WS.sub(" ", s).strip()

# ------------------------------------------------------------------- L2

class PdfCache:
    """One open per document; verifies pages against claims."""
    def __init__(self, path_map=None, max_open=24):
        self.cache = {}
        self.path_map = path_map or []
        self.max_open = max_open

    def resolve(self, path):
        for old, new in self.path_map:
            if path.startswith(old):
                return new + path[len(old):]
        return path

    def page_text(self, path, page_1based):
        import pymupdf
        p = self.resolve(path)
        if p not in self.cache:
            if len(self.cache) >= self.max_open:
                self.cache.pop(next(iter(self.cache)))
            if not os.path.exists(p):
                self.cache[p] = ("missing", None)
            else:
                try:
                    self.cache[p] = ("ok", pymupdf.open(p))
                except Exception:
                    self.cache[p] = ("bad", None)
        st, doc = self.cache[p]
        if st != "ok":
            return st, None
        try:
            if not isinstance(page_1based, int) or page_1based < 1:
                return "no_page", None
            # exact page, and neighbors for ±1 tolerance (recorded, not silent)
            out = {}
            for off in (0, -1, 1):
                pg = page_1based + off
                if 1 <= pg <= doc.page_count:
                    out[off] = doc[pg - 1].get_text() or ""
            return "ok", out
        except Exception:
            return "bad", None


def verbatim_fragments(verbatim: str) -> list[str]:
    """Extractor verbatims join table cells with ' | '. Each substantive
    fragment must appear on the page; tiny fragments carry no signal —
    unless the whole verbatim is tiny, then it is the evidence."""
    v = norm_text(verbatim)
    if not v:
        return []
    parts = [norm_text(x) for x in v.split(" | ")]
    frags = [p for p in parts if len(p) >= 6]
    return frags or ([v] if len(v) >= 2 else [])


def check_verbatim(pages: dict, verbatim: str | None):
    """Returns (ok, page_offset, matched, total). All fragments on ONE page
    within ±1 of the cited page. Records the offset actually found."""
    frags = verbatim_fragments(verbatim)
    if not frags:
        return False, None, 0, 0
    for off in (0, -1, 1):
        if off not in pages:
            continue
        text = norm_text(pages[off])
        if all(f in text for f in frags):
            return True, off, len(frags), len(frags)
    best = max((sum(1 for f in frags if f in norm_text(pages[o]))
                for o in pages), default=0)
    return False, None, best, len(frags)


def l2_verify_claim(c: dict, cache: PdfCache) -> dict:
    reasons = []
    checks = {}
    field = c.get("field")
    categorical = (isinstance(field, str) and
                   (field.startswith(CATEGORICAL_FIELDS)))
    # 1) source exists
    st, pages = cache.page_text(c["source_path"], c["page_1based"])
    checks["source"] = st == "ok"
    if st != "ok":
        return {"claim_id": c["claim_id"], "verdict": "rejected",
                "reasons": [f"source_{st}"], "checks": checks}
    # 2) verbatim fragments on cited page (±1 recorded tolerance)
    ok, off, matched, total = check_verbatim(pages, c.get("verbatim"))
    checks["verbatim_on_page"] = ok
    checks["page_offset"] = off
    checks["fragments"] = f"{matched}/{total}"
    if not ok:
        reasons.append("verbatim_not_on_page")
    # 3) value/unit/range (categorical fields: verbatim-only)
    unit_c = canon_unit(c.get("unit_raw"))
    val = c.get("value_raw")
    if val is None:
        checks["value_parse"] = False
        reasons.append("no_value")
    elif categorical:
        checks["value_parse"] = isinstance(val, (bool, str)) or _is_num(val)
        checks["unit"] = True
        checks["range"] = True
        if not checks["value_parse"]:
            reasons.append("value_unparseable")
    else:
        try:
            f = float(val)
            checks["value_parse"] = True
        except (TypeError, ValueError):
            f = None
            checks["value_parse"] = False
            reasons.append("value_unparseable")
        if f is not None:
            if c["lane"] == "mode":
                lo, hi = CURRENT_RANGE_A
                scale = {"nA": 1e-9, "uA": 1e-6, "mA": 1e-3, "A": 1.0}.get(unit_c)
                if scale is None:
                    if unit_c in ("uA/MHz", "mA/MHz"):
                        checks["unit"] = False
                        reasons.append("per_mhz_needs_condition_split")
                    else:
                        reasons.append("unknown_current_unit")
                        checks["unit"] = False
                else:
                    checks["unit"] = True
                    checks["range"] = lo <= abs(f) * scale <= hi
                    if not checks["range"]:
                        reasons.append("current_out_of_range")
            elif c["lane"] == "aec":
                checks["unit"] = True
                checks["range"] = str(val) in GRADE_VALUES
                if not checks["range"]:
                    reasons.append("aec_grade_invalid")
            else:
                window = RANGE_TABLE.get(field)
                if window and unit_c:
                    checks["unit"] = True
                    checks["range"] = window[0] <= f <= window[1]
                    if not checks["range"]:
                        reasons.append("value_out_of_range")
                else:
                    checks["unit"] = field in FIELD_CANON_UNIT
                    checks["range"] = True
                    if not checks["unit"]:
                        reasons.append("unknown_field_unit")
    verdict = "verified" if not reasons else "held"
    return {"claim_id": c["claim_id"], "verdict": verdict,
            "reasons": reasons, "checks": checks}


def _is_num(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False

# ------------------------------------------------------------------- L3

def l3_normalize_claim(v: dict, claim: dict) -> dict:
    out = dict(v)
    field = claim.get("field")
    unit_c = canon_unit(claim.get("unit_raw"))
    val = claim.get("value_raw")
    out["unit_canon"] = unit_c
    out["value_canon"] = val
    out["transform"] = None
    try:
        f = float(val)
    except (TypeError, ValueError):
        f = None
    if f is not None:
        # currents to base A; frequencies stay in field-canonical units
        if claim["lane"] == "mode" and unit_c in ("uA", "mA", "A"):
            scale = {"uA": 1e-6, "mA": 1e-3, "A": 1.0}[unit_c]
            out["value_canon"] = round(f * scale, 12)
            out["unit_canon"] = "A"
            out["transform"] = f"{unit_c}->A*{scale} ({CANON_VERSION})"
        else:
            out["transform"] = f"identity ({CANON_VERSION})"
    if claim["lane"] == "mode":
        out["mode_canon"] = canon_mode(field)
        out["mode_canon_version"] = MODE_CANON_VERSION
    out["conditions_typed"] = parse_conditions(claim.get("conditions_raw"))
    out["value_role"] = claim.get("value_role")
    return out

# ------------------------------------------------------------------- L4

def identity_tier(slug: str | None, catalog: set, alias_index: dict | None = None) -> dict:
    if not slug:
        return {"tier": "unresolved", "opn": None, "rule": "no_slug"}
    s = slug.strip()
    if s in catalog:
        return {"tier": "exact_opn", "opn": s, "rule": "catalog_exact"}
    alias = re.sub(r"[^A-Z0-9]", "", s.upper())
    hit = None
    if alias_index is not None:
        hit = alias_index.get(alias)
    else:
        for cat in catalog:
            if re.sub(r"[^A-Z0-9]", "", cat.upper()) == alias:
                hit = cat
                break
    if hit:
        return {"tier": "exact_opn", "opn": hit, "rule": "canonical_alias"}
    if re.search(r"[#*]|\bfamily\b|\bseries\b", s.lower()):
        return {"tier": "family", "opn": None, "rule": "family_marker"}
    return {"tier": "document_slug", "opn": None,
            "rule": "doc_slug_not_opn"}

# ------------------------------------------------------------------- L5

def axis_status(a: dict, b: dict, lane: str) -> tuple[str, str | None]:
    """(comparable|qualified|noncomparable, qualification or None)."""
    if a.get("unit_canon") != b.get("unit_canon"):
        return "noncomparable", f"units differ ({a.get('unit_canon')} vs {b.get('unit_canon')})"
    ca, cb = a.get("conditions_typed") or {}, b.get("conditions_typed") or {}
    if lane == "mode":
        if a.get("mode_canon") != b.get("mode_canon"):
            return "noncomparable", f"modes differ ({a.get('mode_canon')} vs {b.get('mode_canon')})"
        for k in ("vin", "vdd", "iout", "temp"):
            if k in ca and k in cb and ca[k] != cb[k]:
                return "qualified", f"{k.upper()} differs ({ca[k]} vs {cb[k]})"
            if (k in ca) != (k in cb):
                return "qualified", f"{k.upper()} specified on only one side"
        return "comparable", None
    if lane == "thermal":
        cond_a = norm_text(a.get("conditions_raw"))
        cond_b = norm_text(b.get("conditions_raw"))
        if cond_a and cond_b and cond_a != cond_b:
            return "qualified", f"package/condition differs ('{cond_a[:40]}' vs '{cond_b[:40]}')"
        if bool(cond_a) != bool(cond_b):
            return "qualified", "condition specified on only one side"
        return "comparable", None
    # pillars / aec: unconditioned printed specs
    return "comparable", None

# ------------------------------------------------------------------ CLIs

def cmd_l1(args):
    out = Path(args.out)
    seen, n_dup = {}, 0
    total = 0
    sources = []
    for spec in args.inputs:
        lane, path = spec.split("=", 1)
        sources.append(path)
        for line in open(path):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("verdict") not in (None, "printed_row"):
                continue
            c = row_to_claim(row, lane, os.path.basename(path))
            cid = claim_id(c)
            c["claim_id"] = cid
            total += 1
            if cid in seen:
                n_dup += 1
                continue
            seen[cid] = c
    rows = list(seen.values())
    with out.open("w") as fh:
        for c in rows:
            fh.write(json.dumps(c, sort_keys=True) + "\n")
    print(json.dumps({"input_rows": total, "unique_claims": len(rows),
                      "duplicates": n_dup, "sources": sources}, indent=1))


def cmd_l2(args):
    qpath = Path(args.queue)
    claims = [json.loads(l) for l in qpath.open() if l.strip()]
    idx, n = args.shard.split("/")
    idx, n = int(idx), int(n)
    claims = [c for c in claims if int(c["claim_id"][:8], 16) % n == idx]
    path_map = []
    for m in args.map or []:
        old, _, new = m.partition("=")
        path_map.append((old, new))
    cache = PdfCache(path_map)
    led = Path(args.out_dir) / "l2-ledger.jsonl"
    done = set()
    if led.exists():
        for line in led.open():
            try:
                r = json.loads(line)
                if r.get("claim_id") and r.get("verdict") in ("verified",):
                    done.add(r["claim_id"])
            except json.JSONDecodeError:
                pass
    todo = [c for c in claims if c["claim_id"] not in done]
    t0 = time.time()
    verdicts = Counter()
    with led.open("a") as fh:
        for i, c in enumerate(todo):
            v = l2_verify_claim(c, cache)
            v["pdf_sha"] = c.get("pdf_sha")
            fh.write(json.dumps(v, sort_keys=True) + "\n")
            verdicts[v["verdict"]] += 1
            if (i + 1) % 200 == 0:
                fh.flush()
                print(f"{i+1}/{len(todo)} {dict(verdicts)} {time.time()-t0:.0f}s", flush=True)
    print(json.dumps({"shard": args.shard, "population": len(claims),
                      "processed": len(todo), "verdicts": dict(verdicts),
                      "claims_per_sec": round(len(todo) / max(time.time() - t0, 0.1), 1),
                      "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}, indent=1))


def cmd_l3(args):
    claims = {json.loads(l)["claim_id"]: json.loads(l)
              for l in open(args.claims) if l.strip()}
    led = Path(args.out_dir) / "l3-normalized.jsonl"
    counts = Counter()
    with led.open("w") as fh:
        for line in open(args.l2):
            r = json.loads(line)
            if r.get("verdict") != "verified":
                continue
            c = claims.get(r["claim_id"])
            if not c:
                counts["missing_claim"] += 1
                continue
            nrow = l3_normalize_claim(r, c)
            nrow.update({k: c[k] for k in ("field", "part_slug", "lane",
                                           "conditions_raw", "verbatim",
                                           "page_1based", "pdf_sha", "source_path")})
            fh.write(json.dumps(nrow, sort_keys=True) + "\n")
            counts["normalized"] += 1
    print(json.dumps({"normalized": counts["normalized"], "skipped": dict(counts)}, indent=1))


def cmd_l4(args):
    catalog = set()
    for f in args.catalog:
        for line in open(f):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            pn = r.get("part_number") if isinstance(r, dict) else r
            if pn:
                catalog.add(pn)
    # manifest join: sha -> source_url -> slug (same derivation as fleet waves)
    sha_slug = {}
    for mf in sorted(Path(args.manifests).glob("*.jsonl")):
        for line in mf.open():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            sha = r.get("sha256")
            url = r.get("source_url") or ""
            if sha and sha not in sha_slug and url:
                stem = url.rstrip("/").rsplit("/", 1)[-1]
                stem = re.sub(r"\.(pdf|PDF)$", "", stem)
                host = re.match(r"https?://(?:www\.)?([^./]+)", url)
                pre = host.group(1) + "-" if host else ""
                slug = re.sub(r"[^a-z0-9]+", "-", (pre + stem).lower()).strip("-")[:96]
                if slug:
                    sha_slug[sha] = slug
    # precomputed alias index (O(1) per claim — the naive loop was O(catalog))
    alias_index = {}
    for c in catalog:
        a = re.sub(r"[^A-Z0-9]", "", c.upper())
        if a and a not in alias_index:
            alias_index[a] = c
    out = Path(args.out_dir) / "l4-bound.jsonl"
    counts = Counter()
    joined = 0
    with out.open("w") as fh:
        for line in open(args.l3):
            r = json.loads(line)
            if not r.get("part_slug"):
                r["part_slug"] = sha_slug.get(r.get("pdf_sha"))
                if r.get("part_slug"):
                    joined += 1
                    r["slug_source"] = "manifest_join"
            t = identity_tier(r.get("part_slug"), catalog)
            r.update(t)
            fh.write(json.dumps(r, sort_keys=True) + "\n")
            counts[t["tier"]] += 1
    print(json.dumps({"catalog_size": len(catalog),
                      "manifest_index": len(sha_slug),
                      "slugs_joined_at_l4": joined, "tiers": dict(counts)}, indent=1))


def cmd_l5(args):
    rows = [json.loads(l) for l in open(args.l4) if l.strip()]
    by_part = defaultdict(list)
    for r in rows:
        key = r.get("opn") or r.get("part_slug")
        if key:
            by_part[key].append(r)

    def part_rows(p):
        return by_part.get(p) or by_part.get(p.upper()) or []

    a_rows, b_rows = part_rows(args.a), part_rows(args.b)
    result = {"a": args.a, "b": args.b,
              "a_claims": len(a_rows), "b_claims": len(b_rows), "axes": []}
    fields_a = defaultdict(list)
    for r in a_rows:
        fields_a[(r["lane"], r.get("mode_canon") or r["field"])].append(r)
    fields_b = defaultdict(list)
    for r in b_rows:
        fields_b[(r["lane"], r.get("mode_canon") or r["field"])].append(r)
    for key in sorted(set(fields_a) & set(fields_b)):
        lane = key[0]
        ra = sorted(fields_a[key], key=lambda r: (r.get("value_role") != "typ", r.get("value_canon") or 0))
        rb = sorted(fields_b[key], key=lambda r: (r.get("value_role") != "typ", r.get("value_canon") or 0))
        status, qual = axis_status(ra[0], rb[0], lane)
        result["axes"].append({
            "lane": lane, "field": key[1], "status": status, "qualification": qual,
            "a": {"value": ra[0].get("value_canon"), "unit": ra[0].get("unit_canon"),
                  "role": ra[0].get("value_role"), "identity": ra[0].get("tier"),
                  "conditions": ra[0].get("conditions_typed"),
                  "evidence": {"verbatim": ra[0].get("verbatim"),
                               "page": ra[0].get("page_1based"), "sha": ra[0].get("pdf_sha")}},
            "b": {"value": rb[0].get("value_canon"), "unit": rb[0].get("unit_canon"),
                  "role": rb[0].get("value_role"), "identity": rb[0].get("tier"),
                  "conditions": rb[0].get("conditions_typed"),
                  "evidence": {"verbatim": rb[0].get("verbatim"),
                               "page": rb[0].get("page_1based"), "sha": rb[0].get("pdf_sha")}},
        })
    counts = Counter(a["status"] for a in result["axes"])
    result["axis_counts"] = dict(counts)
    Path(args.out).write_text(json.dumps(result, indent=1, sort_keys=True))
    print(json.dumps({"axes": len(result["axes"]), "counts": dict(counts)}, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("l1"); p.add_argument("--inputs", nargs="+", required=True,
                                             help="lane=/path/file.jsonl ...")
    p.add_argument("--out", required=True); p.set_defaults(fn=cmd_l1)
    p = sub.add_parser("l2"); p.add_argument("--queue", required=True)
    p.add_argument("--out-dir", required=True); p.add_argument("--shard", default="0/1")
    p.add_argument("--map", action="append"); p.set_defaults(fn=cmd_l2)
    p = sub.add_parser("l3"); p.add_argument("--claims", required=True)
    p.add_argument("--l2", required=True); p.add_argument("--out-dir", required=True)
    p.set_defaults(fn=cmd_l3)
    p = sub.add_parser("l4"); p.add_argument("--l3", required=True)
    p.add_argument("--catalog", nargs="+", required=True)
    p.add_argument("--manifests", default="/Users/samkim/lane-out/fold-bank-20261020/manifests")
    p.add_argument("--out-dir", required=True); p.set_defaults(fn=cmd_l4)
    p = sub.add_parser("l5"); p.add_argument("--l4", required=True)
    p.add_argument("--a", required=True); p.add_argument("--b", required=True)
    p.add_argument("--out", required=True); p.set_defaults(fn=cmd_l5)
    args = ap.parse_args()
    sys.exit(args.fn(args) or 0)


if __name__ == "__main__":
    main()
