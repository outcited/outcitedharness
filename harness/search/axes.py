"""Numeric specification axes (PRD-FACET-02 R2).

Category-specific engineering dimensions drawn from canonical claims and
typed condition data. The registry maps printed symbols to canonical field
identities (``RDS(ON)`` and ``R_DS(ON)`` are one axis), normalizes SI units
(``mΩ`` -> ohm, ``µA``/``μA`` -> ampere), and types every value explicitly:
point, interval, inequality, or unknown — never a bare number that loses its
qualifier or condition.

Curve evidence is NOT converted into specification limits here: a typical
curve and a guaranteed limit are different evidence classes, and this module
only reads canonical claims (guaranteed/typ as printed, with their class).

Axis ops mirror harness/discovery/evaluator.py so cohort filtering reuses
the same comparator vocabulary:
  min_rating — a larger value is better rated (VDS, ID)
  max_rating — a smaller value is better (RDS(on))
  range      — a span that must contain the requirement (TSTG)
  point      — a characteristic value with no better/worse direction
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


AXES_SCHEMA = "harness.search-spec-axes.v1"

# --- SI normalization -----------------------------------------------------
#
# SI prefixes are CASE-SENSITIVE (M=mega, m=milli): normalization must
# resolve the prefix on the ORIGINAL casing before lowercasing the base.
# "MHz" -> 1e6 hz; "mOhm" -> 1e-3 ohm. (Regression caught by the MCU
# journey: a naive lowercase-first pass turned 80 MHz into 0.08 "hz".)

_PREFIX = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3,
           "M": 1e6, "G": 1e9}
# NOTE: "b" (bytes) is deliberately absent — KB/MB in datasheets are
# binary-ish magnitudes kept at printed scale, never SI-rescaled.
_PREFIXABLE = frozenset(("v", "a", "ohm", "w", "f", "h", "s", "hz", "j",
                         "c"))

_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def norm_symbol(symbol: str | None) -> str:
    s = re.sub(r"[\s_]+", "", str(symbol or "")).upper()
    return s.replace("(", "").replace(")", "").replace(",", "")


def _canonical_base(unit: str, bare_c_is_temp: bool = True) -> str:
    """Lowercase base unit with symbol canonicalization (Ω->ohm, ℃/°C->degc,
    µ/μ->u handled at prefix stage). A BARE 'c' is degrees Celsius in this
    corpus; a PREFIXED c (µC, nC) is coulombs and stays 'c'."""
    u = unit.replace("Ω", "ohm").replace("ω", "ohm").replace("Ohm", "ohm")
    u = u.replace("℃", "degc").replace("°C", "degc").replace("°c", "degc")
    u = u.replace("µ", "u").replace("μ", "u")
    u = u.strip().lower()
    if u == "c" and bare_c_is_temp:
        u = "degc"
    if u == "ohms":
        u = "ohm"
    return u


def normalize_unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    u = str(unit).strip().replace("µ", "u").replace("μ", "u")
    if not u:
        return None
    if "/" in u:          # composites (K/W, °C/W, V/ns): canonical, unscaled
        head, _, tail = u.partition("/")
        return f"{_canonical_base(head)}/{_canonical_base(tail)}"
    if u == "%":
        return "%"
    # prefix resolution on ORIGINAL casing, then canonical base
    if len(u) > 1 and u[0] in _PREFIX:
        base = _canonical_base(u[1:], bare_c_is_temp=False)
        if base in _PREFIXABLE:
            return base
    return _canonical_base(u)


def si_normalize(value: float | None, unit: str | None
                 ) -> tuple[float | None, str | None]:
    """(value, unit) -> base-SI (value, unit). Unknown units pass through
    unscaled; composites and % are canonicalized but never rescaled."""
    norm = normalize_unit(unit)
    if value is None:
        return None, norm
    if not unit:
        return value, None
    u = str(unit).strip().replace("µ", "u").replace("μ", "u")
    if "/" in u or u == "%" or norm is None:
        return value, norm
    if len(u) > 1 and u[0] in _PREFIX:
        base = _canonical_base(u[1:], bare_c_is_temp=False)
        if base in _PREFIXABLE:
            return value * _PREFIX[u[0]], base
    return value, norm


# --- axis registry --------------------------------------------------------

@dataclass(frozen=True)
class SpecAxis:
    name: str
    symbols: tuple[str, ...]
    unit: str
    op: str = "point"          # min_rating | max_rating | range | point
    kind: str = "numeric"      # numeric | text
    categories: tuple[str, ...] = ()


_POWER = ("power",)
_MCU = ("mcu",)
_CONN = ("connectors",)

SPEC_AXES: tuple[SpecAxis, ...] = (
    # power
    SpecAxis("vds_rating_v", ("VDSS", "VBRDSS", "VDS"), "V", "min_rating", categories=_POWER),
    SpecAxis("id_continuous_a", ("ID", "IDDC", "ID_PULSE"), "A", "min_rating", categories=_POWER),
    SpecAxis("rds_on_ohm", ("RDSON", "RDS(ON)", "R_DS(ON)", "RDS(ON)MAX"), "ohm", "max_rating", categories=_POWER),
    SpecAxis("vgs_th_v", ("VGS(TH)", "VGSTH", "VGS_TH"), "V", "range", categories=_POWER),
    SpecAxis("gate_charge_nc", ("QG", "QG(TOT)", "QGTOT"), "C", "max_rating", categories=_POWER),
    SpecAxis("coss_pf", ("COSS", "C_OSS"), "F", "point", categories=_POWER),
    SpecAxis("tj_max_c", ("TJ", "TJMAX"), "°C", "min_rating", categories=_POWER),
    SpecAxis("tstg_range_c", ("TSTG",), "°C", "range", categories=_POWER),
    SpecAxis("thermal_resistance_c_per_w", ("RTHJA", "RTHJC", "RTH(J-A)"), "c/w", "max_rating", categories=_POWER),
    SpecAxis("output_voltage_v", ("VOUT", "V_OUT"), "V", "point", categories=_POWER),
    SpecAxis("output_current_a", ("IOUT", "IOUT_MAX", "ILOAD"), "A", "min_rating", categories=_POWER),
    SpecAxis("switching_frequency_hz", ("FSW", "FSWITCH", "FSW_MAX"), "hz", "min_rating", categories=_POWER),
    # mcu
    SpecAxis("flash_kb", ("FLASH_KB", "FLASH", "PROGRAM_MEMORY"), "KB", "min_rating", categories=_MCU),
    SpecAxis("sram_kb", ("SRAM_KB", "SRAM", "RAM"), "KB", "min_rating", categories=_MCU),
    SpecAxis("frequency_mhz", ("FREQ_MHZ", "FCPU", "FREQUENCY", "MAX_FREQ"), "hz", "min_rating", categories=_MCU),
    SpecAxis("supply_voltage_v", ("VDD", "VDD_MIN", "VCC", "SUPPLY_VOLTAGE"), "V", "range", categories=_MCU),
    SpecAxis("adc_bits", ("ADC_BITS", "ADC_RESOLUTION"), "bit", "point", categories=_MCU),
    SpecAxis("core", ("CORE", "ARCHITECTURE", "CPU_CORE"), "", "point", kind="text", categories=_MCU),
    # connectors
    SpecAxis("pitch_mm", ("PITCH_MM", "PITCH"), "mm", "point", categories=_CONN),
    SpecAxis("positions", ("POSITIONS", "POSITION_COUNT", "NUMBER_OF_POSITIONS"), "count", "point", categories=_CONN),
    SpecAxis("rows", ("ROWS", "ROW_COUNT"), "count", "point", categories=_CONN),
    SpecAxis("current_rating_a", ("CURRENT_RATING_A", "RATED_CURRENT"), "A", "min_rating", categories=_CONN),
    SpecAxis("voltage_rating_v", ("VOLTAGE_RATING_V", "RATED_VOLTAGE"), "V", "min_rating", categories=_CONN),
    SpecAxis("dielectric_withstanding_v", ("DIELECTRIC_WITHSTANDING_V",), "V", "min_rating", categories=_CONN),
    SpecAxis("temp_range_c", ("TEMP_RANGE_C", "OPERATING_TEMPERATURE_RANGE"), "°C", "range", categories=_CONN),
)

_ALIAS: dict[str, SpecAxis] = {}
for _axis in SPEC_AXES:
    for _sym in _axis.symbols:
        _ALIAS.setdefault(norm_symbol(_sym), _axis)


def axis_for(symbol: str | None) -> SpecAxis | None:
    return _ALIAS.get(norm_symbol(symbol))


def axes_for_category(category: str | None) -> list[SpecAxis]:
    if not category:
        return []
    return [a for a in SPEC_AXES if category in a.categories]


# --- value typing ---------------------------------------------------------

def type_claim_value(*, value, unit, qualifier, condition_norm,
                     condition_typed: dict | None, axis: SpecAxis) -> dict:
    """One claim -> a typed axis value.

    kind: point | interval | inequality | unknown | text
    Never invents a bound: a typ claim is a point with class 'typical';
    a min/max pair becomes an interval only when the caller pairs them.
    """
    if axis.kind == "text":
        return {"kind": "text", "value": value,
                "condition": condition_norm or None,
                "class": qualifier or None}
    if not isinstance(value, (int, float)):
        return {"kind": "unknown", "value": None,
                "condition": condition_norm or None, "class": None}
    base, base_unit = si_normalize(float(value), unit)
    q = (qualifier or "").lower()
    kind = "point"
    if q in ("min", "min."):
        kind = "inequality"
    elif q in ("max", "max."):
        kind = "inequality"
    elif q in ("typ", "typical"):
        kind = "point"
    return {
        "kind": kind,
        "value": base,
        "unit": base_unit,
        "min_max": q if kind == "inequality" else None,
        # rating class comes from the comparator vocabulary; typ stays typ
        "rating_class": {"min": "rated_limit", "max": "rated_limit",
                         "typ": "typical"}.get(q, "unknown"),
        "condition": condition_norm or None,
        "condition_typed": (condition_typed or {}).get("keys"),
        "class": qualifier or None,
    }