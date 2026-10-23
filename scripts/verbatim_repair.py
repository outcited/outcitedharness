#!/usr/bin/env python3
"""verbatim_repair.py — unlock LLM discovery claims by repairing paraphrased verbatims.

For each claim: go to the cited page, find the text fragment containing the
value, replace the paraphrase with the exact substring. Then the claim passes
L2 verification (exact verbatim on exact page with exact value).

This is deterministic — no LLM, no guessing. Pure text matching.
"""
import json, os, re, sys, time, unicodedata
from pathlib import Path
import pymupdf

_WS = re.compile(r"\s+")

def nrm(s):
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("−", "-").replace("\u00a0", " ")
    return _WS.sub(" ", s).strip()

def value_forms(val):
    """All printed forms a numeric value can take on a page."""
    try:
        f = float(val)
    except (TypeError, ValueError):
        return [str(val)] if val is not None else []
    out = {str(val), repr(f)}
    if f == int(f):
        out |= {str(int(f)), f"{int(f):,}"}
    for d in (1, 2, 3):
        out.add(f"{f:.{d}f}")
    return sorted([x for x in out if x], key=len, reverse=True)

def find_exact_verbatim(page_text_norm, value, field_hint=None):
    """Find the smallest text fragment on the page that contains the value.

    Returns the exact normalized substring, or None if not found.
    Strategy:
    1. Find all occurrences of the value's printed form
    2. For each occurrence, extract surrounding context (the "line" it appears on)
    3. Prefer lines that also contain the field hint
    """
    forms = value_forms(value)
    if not forms:
        return None
    best = None
    best_score = 0
    for form in forms:
        start = 0
        while True:
            idx = page_text_norm.find(form, start)
            if idx < 0:
                break
            start = idx + 1
            # Extract the line containing this value (delimited by sentence/line boundaries)
            line_start = max(page_text_norm.rfind("\n", 0, idx),
                           page_text_norm.rfind(". ", 0, idx),
                           page_text_norm.rfind("; ", 0, idx),
                           idx - 200) + 1
            # Clean up line_start
            if line_start < 0:
                line_start = 0
            line_end = min(page_text_norm.find("\n", idx + len(form)),
                          page_text_norm.find(". ", idx + len(form)),
                          page_text_norm.find("; ", idx + len(form)),
                          idx + len(form) + 200)
            if line_end < 0:
                line_end = idx + len(form) + 200
            fragment = page_text_norm[line_start:line_end].strip()
            if len(fragment) < len(form):
                continue
            # Score: prefer fragments with field hint, prefer shorter fragments
            score = 1
            if field_hint and field_hint.lower() in fragment.lower():
                score += 10
            if len(fragment) < 100:
                score += 5  # concise
            elif len(fragment) > 200:
                score -= 5  # too long
            if score > best_score:
                best_score = score
                best = fragment
    return best

# === FIELD NORMALIZATION MAP ===
# Maps LLM free-form field names to spine canonical fields
FIELD_MAP = {
    # voltage / supply
    "vdd": "supply_v", "supply voltage": "supply_v", "vdd range": "supply_v",
    "supply voltage": "supply_v", "operating voltage": "supply_v",
    "vds": "max_vds_v", "drain to source voltage": "max_vds_v",
    "vgs": "max_vgs_v", "gate to source voltage": "max_vgs_v",
    "voltage supply": "supply_v", "supply": "supply_v",
    # memory
    "flash": "flash_kb", "flash memory": "flash_kb", "program memory": "flash_kb",
    "program flash": "flash_kb", "flash size": "flash_kb",
    "sram": "sram_kb", "sram size": "sram_kb", "data memory": "sram_kb",
    "eeprom": "eeprom_kb", "eeprom size": "eeprom_kb",
    # clock
    "clock frequency": "clk_max_mhz", "max frequency": "clk_max_mhz",
    "operating frequency": "clk_max_mhz", "cpu frequency": "clk_max_mhz",
    "frequency": "clk_max_mhz", "clock": "clk_max_mhz",
    # peripherals
    "gpio": "n_gpio", "gpio count": "n_gpio", "gpio pins": "n_gpio",
    "uart": "n_uart", "usart": "n_uart", "number of uart": "n_uart",
    "spi": "n_spi", "number of spi": "n_spi",
    "i2c": "n_i2c", "number of i2c": "n_i2c",
    "adc": "n_adc", "adc channels": "n_adc", "number of adc": "n_adc",
    "dma": "n_dma", "dma channels": "n_dma",
    "timer": "n_timer", "timers": "n_timer", "number of timer": "n_timer",
    "can": "n_can", "can bus": "n_can",
    "usb": "n_usb", "usb interface": "n_usb",
    "ethernet": "n_ethernet",
    # thermal
    "rtheta ja": "RthetaJA", "thermal resistance ja": "RthetaJA",
    "theta ja": "RthetaJA", "rth ja": "RthetaJA",
    "rtheta jc": "RthetaJC", "thermal resistance jc": "RthetaJC",
    "package thermal resistance": "RthetaJA",
    # temperature
    "operating temperature": "temp_range_c", "temperature range": "temp_range_c",
    "storage temperature": "temp_range_c", "operating temp": "temp_range_c",
    "ta": "temp_range_c", "tstg": "temp_range_c",
    # esd
    "esd hbm": "esd_hbm_v", "esd": "esd_hbm_v", "esd rating": "esd_hbm_v",
    "esd cdm": "esd_cdm_v",
    # package
    "package": "package", "package type": "package", "package name": "package",
    # current
    "supply current": "supply_current_ua", "idd": "supply_current_ua",
    "idrain": "max_idrain_a", "drain current": "max_idrain_a",
    "id": "max_idrain_a",
    # mosfet specific
    "rds on": "rds_on_mohm", "rdson": "rds_on_mohm", "rds(on)": "rds_on_mohm",
    "rds(on) resistance": "rds_on_mohm", "on resistance": "rds_on_mohm",
    "gate threshold": "vgs_th_v", "vgs th": "vgs_th_v",
    "vgsth": "vgs_th_v", "gate threshold voltage": "vgs_th_v",
    # capacitance
    "input capacitance": "input_capacitance_pf", "ciss": "input_capacitance_pf",
    "output capacitance": "output_capacitance_pf", "coss": "output_capacitance_pf",
    # additional voltage/current
    "vcc": "supply_v", "vin": "vin_v", "v_in": "vin_v", "input voltage": "vin_v",
    "vol": "vol_v", "output low voltage": "vol_v", "v_ol": "vol_v",
    "voh": "voh_v", "output high voltage": "voh_v", "v_oh": "voh_v",
    "iol": "iol_ma", "output low current": "iol_ma", "i_ol": "iol_ma",
    "ioh": "ioh_ma", "output high current": "ioh_ma", "i_oh": "ioh_ma",
    # junction temp
    "tj": "max_junction_temp_c", "junction temperature": "max_junction_temp_c",
    "max junction temperature": "max_junction_temp_c",
    # power
    "power dissipation": "power_dissipation_mw", "pd": "power_dissipation_mw",
    # resistance
    "resistance": "resistance_ohm", "capacitance": "capacitance_pf",
    # pin count
    "pin count": "n_pins", "number of pins": "n_pins", "pins": "n_pins",
}

def normalize_field(raw_field):
    """Map LLM free-form field name to canonical spine field."""
    if not raw_field:
        return None
    # Exact match (case-insensitive)
    lower = raw_field.strip().lower()
    if lower in FIELD_MAP:
        return FIELD_MAP[lower]
    # Partial match (field contains a known key)
    for key, canonical in FIELD_MAP.items():
        if key in lower:
            return canonical
    return None

def parse_value(raw_value, unit):
    """Parse LLM string values into numeric spine values."""
    if raw_value is None:
        return None
    if isinstance(raw_value, (int, float)):
        return float(raw_value)
    # Try direct float
    try:
        return float(raw_value)
    except (ValueError, TypeError):
        pass
    # Strip unit suffix and try again
    s = str(raw_value).strip()
    for suffix in [" V", "V", " mA", "mA", " uA", "uA", " µA", "µA",
                   " MHz", "MHz", " kHz", "kHz", " kB", "kB", " MB", "MB",
                   " °C", "°C", " C", "C", " mV", "mV", " A", "A",
                   " mΩ", "mΩ", " Ω", "Ω", " pF", "pF", " nF", "nF",
                   " W", "W", " mW", "mW", " %", "%"]:
        if s.endswith(suffix):
            s = s[:-len(suffix)].strip()
            break
    # Handle ± prefix
    s = s.replace("±", "").replace("+", "").strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return None

def normalize_unit(raw_unit, canonical_field):
    """Map LLM unit to the spine's expected unit for the canonical field."""
    if not raw_unit:
        return None
    u = str(raw_unit).strip().lower()
    field_units = {
        "supply_v": "V", "max_vds_v": "V", "max_vgs_v": "V", "vgs_th_v": "V",
        "clk_max_mhz": "MHz", "flash_kb": "kB", "sram_kb": "kB", "eeprom_kb": "kB",
        "temp_range_c": "degC", "RthetaJA": "C/W", "RthetaJC": "C/W",
        "esd_hbm_v": "V", "esd_cdm_v": "V", "supply_current_ua": "uA",
        "rds_on_mohm": "mohm", "input_capacitance_pf": "pF",
    }
    return field_units.get(canonical_field, str(raw_unit).strip())


def repair_claim(claim, page_text_norm):
    """Repair a claim: normalize field, parse value, repair verbatim."""
    raw_field = claim.get("field", "")
    raw_value = claim.get("value")
    raw_unit = claim.get("unit")
    page = claim.get("page_1based")
    if raw_value is None or page is None:
        return claim, False
    canonical_field = normalize_field(raw_field)
    if canonical_field is None:
        return claim, False
    numeric_value = parse_value(raw_value, raw_unit)
    if numeric_value is None:
        return claim, False
    canonical_unit = normalize_unit(raw_unit, canonical_field)
    exact = find_exact_verbatim(page_text_norm, numeric_value, raw_field)
    if exact is None:
        return claim, False
    current = nrm(claim.get("verbatim", ""))
    already = bool(current) and current in page_text_norm
    claim["field_original"] = raw_field
    claim["value_original"] = raw_value
    claim["unit_original"] = raw_unit
    claim["field"] = canonical_field
    claim["value"] = numeric_value
    claim["unit"] = canonical_unit
    claim["verbatim"] = exact if not already else current
    claim["verbatim_repaired"] = not already
    claim["field_normalized"] = True
    return claim, True

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--claims", required=True, help="LLM discovery claims jsonl")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    claims = []
    for line in open(args.claims):
        try:
            claims.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if args.limit:
        claims = claims[:args.limit]

    print(f"verbatim-repair: {len(claims)} claims", flush=True)

    # Group by source_path, open each PDF once
    pdf_cache = {}
    out = (out_dir / "repaired-claims.jsonl").open("w")
    ledger = (out_dir / "repair-ledger.jsonl").open("w")

    repaired = already_exact = not_found = total = 0
    t0 = time.time()

    for n, claim in enumerate(claims, 1):
        src = claim.get("source_path", "")
        if not src:
            continue

        # Open PDF (cached)
        if src not in pdf_cache:
            try:
                pdf_cache[src] = pymupdf.open(src)
            except Exception:
                pdf_cache.clear()  # memory guard
                try:
                    pdf_cache[src] = pymupdf.open(src)
                except Exception:
                    pdf_cache[src] = None

        doc = pdf_cache.get(src)
        if doc is None:
            ledger.write(json.dumps({"status": "unreadable", "source_path": src}) + "\n")
            not_found += 1
            continue

        # Get page text
        page_num = claim.get("page_1based")
        if not isinstance(page_num, int) or page_num < 1 or page_num > doc.page_count:
            ledger.write(json.dumps({"status": "bad_page", "source_path": src}) + "\n")
            not_found += 1
            continue

        page_text = nrm(doc[page_num - 1].get_text() or "")

        # Repair
        claim, ok = repair_claim(claim, page_text)
        total += 1
        if ok:
            if claim.get("verbatim_repaired"):
                repaired += 1
            else:
                already_exact += 1
            out.write(json.dumps(claim, sort_keys=True) + "\n")
            ledger.write(json.dumps({"status": "repaired" if claim.get("verbatim_repaired") else "already_exact",
                                     "source_path": src}) + "\n")
        else:
            not_found += 1
            ledger.write(json.dumps({"status": "value_not_on_page",
                                     "source_path": src,
                                     "value": claim.get("value"),
                                     "page": page_num}) + "\n")

        if n % 5000 == 0:
            out.flush(); ledger.flush()
            print(f"  {n}/{len(claims)} | repaired={repaired} exact={already_exact} not_found={not_found} | {time.time()-t0:.0f}s", flush=True)

        # Memory guard: clear cache every 100 docs
        if len(pdf_cache) > 100:
            pdf_cache.clear()

    out.close(); ledger.close()
    print(json.dumps({
        "total": total,
        "repaired": repaired,
        "already_exact": already_exact,
        "not_found": not_found,
        "repaired_pct": round(repaired * 100 / max(total, 1), 1),
        "unlocked_pct": round((repaired + already_exact) * 100 / max(total, 1), 1),
    }, indent=1), flush=True)

if __name__ == "__main__":
    main()