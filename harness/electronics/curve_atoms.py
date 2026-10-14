"""Typed atom_class derivation for curve rows (CURVE-07 consumer).

Deterministic: axis label + unit -> canonical quantity key (same symbol
table as condition_model), (x, y) quantity pair + title -> curve_type.
No model calls. Contract: extraction-v1.1 atom_class fields.

curve_type vocabulary (frozen, additive-only):
  efficiency_vs_load, vout_vs_load, vout_vs_vin, transfer_vgs_id,
  output_id_vds, rds_on_vs_vgs, rds_on_vs_id, vgs_th_vs_temp, id_vs_temp,
  soa, switching_waveform, bode_gain, bode_phase, ripple_vs_load,
  transient_response, thermal_derating, current_vs_time, current_vs_temp,
  current_vs_freq, voltage_vs_temp, threshold_vs_temp, normalized_vs_temp,
  time_vs_temp, resistance_vs_temp, freq_vs_temp, impedance_vs_freq,
  iin_vs_vin, power_dissipation,
  capacitor_esr, regulation_vs_temp, frequency_vs_something, adc_linearity,
  iv_generic, unclassified
"""

from __future__ import annotations

import re
from typing import Any

from harness.electronics.condition_model import _COMPILED_SYMBOLS, parse_condition

# unit families for disambiguation
_UNIT_KIND = {
    "v": ("v", "volt"),
    "a": ("a", "amp", "ma", "µa", "ua"),
    "hz": ("hz", "khz", "mhz", "ghz"),
    "c": ("°c", "degc", "celsius"),
    "ohm": ("ω", "ohm", "mω", "kω"),
    "percent": ("%", "percent"),
    "w": ("w", "watt", "mw"),
    "h": ("h", "µh", "uh", "nh", "mh"),
    "s": ("s", "ms", "µs", "us", "ns", "sec"),
}


def _unit_kind(unit: str | None) -> str | None:
    if not unit:
        return None
    u = str(unit).strip().lower()
    for kind, tokens in _UNIT_KIND.items():
        for t in tokens:
            if u == t or u.startswith(t):
                return kind
    return None


def axis_quantity(label: Any, unit: Any = None) -> str | None:
    """Canonical quantity key for an axis label, e.g. 'Output Current' -> iout_a.

    Uses the condition_model symbol table (same canonical keys as typed
    conditions), with unit-family cross-check when a unit is printed.
    """
    if not label:
        return None
    text = str(label)
    uk = _unit_kind(unit)
    best = None
    for pattern, key, kind in _COMPILED_SYMBOLS:
        m = pattern.search(text)
        if m:
            # prefer matches whose kind agrees with the printed unit
            if uk and kind not in _unit_agrees(uk, kind):
                if best is None:
                    best = key
                continue
            return key
    if best:
        return best
    # word-based fallbacks for plain-English labels without symbols/units
    low = text.lower()
    if "efficiency" in low:
        return "efficiency_pct"
    if "gain" in low or re.search(r"\bdb\b", low):
        return "gain_db"
    if "phase" in low:
        return "phase_deg"
    if "normalized" in low or "normalised" in low:
        return "normalized_factor"
    if "dnl" in low or "inl" in low or (unit and str(unit).upper() == "LSB"):
        return "linearity_lsb"
    _WORDS = (
        ("current", "current_a"), ("voltage", "voltage_v"),
        ("frequency", "frequency_hz"), ("resistance", "resistance_ohm"),
        ("impedance", "resistance_ohm"), ("power", "power_w"),
        ("temperature", "temperature_c"), ("time", "time_s"),
        ("charge", "charge_c"), ("capacitance", "capacitance_f"),
    )
    for word, key in _WORDS:
        if word in low:
            return key
    # generic switched/current-pin symbols: IVCCQ, IDD, ISS...
    if re.match(r"^i[a-z]*[_ ]?[a-z]{2,}$", low.replace(" ", "")):
        return "current_a"
    if re.search(r"threshold|uvlo|\ben\b|enable|shutdown|por\b|bor\b", low):
        return "threshold_v" if uk in (None, "v") else None
    if uk == "percent":
        return "percent"
    if uk:
        return {"v": "voltage_v", "a": "current_a", "hz": "frequency_hz",
                "c": "temperature_c", "ohm": "resistance_ohm",
                "percent": "percent",
                "w": "power_w", "s": "time_s", "h": "inductance_h"}.get(uk)
    return None


def _unit_agrees(axis_kind: str, key_kind: str) -> tuple[str, ...]:
    """Which key kinds an axis unit family can legitimately express."""
    table = {
        "v": ("v", "c_per_w"),
        "a": ("a", "a_per_s"),
        "hz": ("hz",),
        "c": ("c", "c_per_w"),
        "ohm": ("ohm",),
        "percent": ("v", "a", "hz"),  # normalized axes
        "w": ("v", "a"),
        "h": ("h",),
        "s": ("a_per_s",),
    }
    return table.get(axis_kind, (key_kind,))


_CURVE_TYPES: tuple[tuple[str | None, str | None, str], ...] = (
    ("iout_a", "efficiency_pct", "efficiency_vs_load"),
    ("iout_a", "vout_v", "vout_vs_load"),
    ("vin_v", "vout_v", "vout_vs_vin"),
    ("vgs_v", "id_a", "transfer_vgs_id"),
    ("id_a", "vds_v", "output_id_vds"),
    ("vds_v", "id_a", "output_id_vds"),
    ("vgs_v", "r_ohm", "rds_on_vs_vgs"),
    ("id_a", "resistance_ohm", "rds_on_vs_id"),
    ("tj_c", "vgs_v", "vgs_th_vs_temp"),
    ("tj_c", "id_a", "id_vs_temp"),
    ("f_hz", "gain_db", "bode_gain"),
    ("f_hz", "phase_deg", "bode_phase"),
    ("iout_a", "percent", "ripple_vs_load"),
    ("time_s", "voltage_v", "switching_waveform"),
    ("time_s", "current_a", "current_vs_time"),
    ("temperature_c", "power_w", "thermal_derating"),
    ("iout_a", "power_w", "power_dissipation"),
    ("voltage_v", "current_a", "iv_generic"),
    # evidence-driven additions (2026-10-09 cluster analysis of 4,104
    # unclassified rows): MCU/power datasheet staples
    ("temperature_c", "current_a", "current_vs_temp"),
    ("temperature_c", "voltage_v", "voltage_vs_temp"),
    ("temperature_c", "threshold_v", "threshold_vs_temp"),
    ("temperature_c", "normalized_factor", "normalized_vs_temp"),
    ("temperature_c", "time_s", "time_vs_temp"),
    ("temperature_c", "resistance_ohm", "resistance_vs_temp"),
    ("f_hz", "current_a", "current_vs_freq"),
    ("f_hz", "id_a", "current_vs_freq"),
    ("temperature_c", "f_hz", "freq_vs_temp"),
    ("f_hz", "resistance_ohm", "impedance_vs_freq"),
    ("resistance_ohm", "f_hz", "impedance_vs_freq"),
    ("voltage_v", "time_s", "transient_response"),
    ("vin_v", "time_s", "transient_response"),
    ("vout_v", "time_s", "transient_response"),
    ("current_a", "voltage_v", "iv_generic"),
    ("vin_v", "current_a", "iin_vs_vin"),
    ("current_a", "time_s", "current_vs_time"),
    ("time_s", "voltage_v", "switching_waveform"),
    ("linearity_lsb", None, "adc_linearity"),
    (None, "linearity_lsb", "adc_linearity"),
)


def curve_type(x_q: str | None, y_q: str | None, title: Any = None) -> str:
    low = str(title or "").lower()
    # SOA is title-strict: FET output characteristics share the v/i shape
    if "safe operating" in low or re.search(r"\bsoa\b", low):
        return "soa"
    if "derat" in low:
        return "thermal_derating"
    if "efficiency" in low and (x_q or y_q == "efficiency_pct"):
        return "efficiency_vs_load"
    for xs, ys, name in _CURVE_TYPES:
        if x_q == xs and y_q == ys:
            return name
    if y_q == "efficiency_pct":
        return "efficiency_vs_load"
    return "unclassified"


def atoms_for_payload(payload: dict) -> dict:
    """Full atom_class block for one digitized curve payload."""
    axes_in = payload.get("axes") or {}
    out_axes = []
    quantities = {}
    for role in ("x", "y"):
        ax = axes_in.get(role) or {}
        q = axis_quantity(ax.get("label"), ax.get("unit"))
        quantities[role] = q
        out_axes.append({"role": role, "quantity": q,
                         "unit": ax.get("unit"),
                         "label_verbatim": ax.get("label"),
                         "min": ax.get("min"), "max": ax.get("max")})
    ct = curve_type(quantities.get("x"), quantities.get("y"),
                    payload.get("title"))
    return {
        "atom_class": "curve",
        "curve_type": ct,
        "classification": "typical_performance",
        "axes": out_axes,
    }


def conditions_for_series(series: dict) -> dict | None:
    """Typed conditions for one series via the shared condition model."""
    verbatim = series.get("condition") or series.get("condition_verbatim")
    if not verbatim:
        return None
    parsed = parse_condition(str(verbatim))
    keys = parsed.get("keys") if isinstance(parsed, dict) else None
    if not keys:
        return None
    return {"verbatim": str(verbatim), "keys": keys,
            "parser": "harness.electronics-condition-model.v1"}
