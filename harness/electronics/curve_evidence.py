"""Decision-grade curve evidence (PRD-CURVE-02) — the layer above the
digitization lane.

A digitized graph is not automatically comparable, and a comparable graph
is not an approved knife. This module upgrades wave-A extraction rows and
frozen reference plots into typed curve evidence objects and answers
engineering questions under three laws:

1. conditions are never defaulted — a comparison runs only when the
   relevant printed conditions are compatible; anything else returns
   ``not_comparable`` with a reason;
2. interpolation is bounded by the plotted data — no extrapolation
   outside a curve's supported range, and the digitizer's resolution
   rides every computed value;
3. a typical curve never becomes a guarantee — the evidence class is
   carried to every result, and derived comparisons are proposals.

The digitization itself (plot regions, vision extraction, grounding,
EC-table anchoring) stays in ``typical_curves``; this module consumes its
outputs. Typed numeric conditions come from the printed-condition model
(``condition_model.parse_condition``) behind a thin alias map (PVIN->VIN,
ƒS->FSW, EN->VEN) — curve vocabulary, not a second parser. Categorical
conditions (``MODE = FCCM``, ``VCC = Internal LDO``) have no numeric
value; they are captured here verbatim-normalized and compared by exact
equality, fail-closed.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Mapping, Sequence

from harness.electronics.condition_model import parse_condition

CURVE_EVIDENCE_SCHEMA = "harness.electronics-curve-evidence.v1"

# --- quantity kinds -----------------------------------------------------------

_UNIT_FAMILY: dict[str, str] = {
    "v": "voltage", "mv": "voltage", "uv": "voltage",
    "a": "current", "ma": "current", "ua": "current", "µa": "current",
    "w": "power", "mw": "power",
    "%": "ratio", 
    "c": "temperature", "°c": "temperature",
    "hz": "frequency", "khz": "frequency", "mhz": "frequency",
    "ω": "resistance", "ohm": "resistance", "mω": "resistance",
    "s": "time", "ms": "time", "us": "time", "µs": "time", "ns": "time",
}
_UNIT_TO_BASE: dict[str, tuple[str, float]] = {
    "v": ("v", 1.0), "mv": ("v", 1e-3),
    "a": ("a", 1.0), "ma": ("a", 1e-3), "ua": ("a", 1e-6), "µa": ("a", 1e-6),
    "w": ("w", 1.0), "mw": ("w", 1e-3),
    "%": ("pct", 1.0),
    "c": ("c", 1.0), "°c": ("c", 1.0),
    "hz": ("hz", 1.0), "khz": ("hz", 1e3), "mhz": ("hz", 1e6),
    "ohm": ("ohm", 1.0), "ω": ("ohm", 1.0), "mω": ("ohm", 1e-3),
    "s": ("s", 1.0), "ms": ("s", 1e-3), "us": ("s", 1e-6),
    "µs": ("s", 1e-6), "ns": ("s", 1e-9),
}


def normalize_unit(unit: str | None) -> str | None:
    text = re.sub(r"\s+", "", str(unit or "")).lower().replace("μ", "µ")
    return text or None


def unit_base(unit: str | None) -> tuple[str, float] | None:
    """(base family, multiplier to base) — None when the unit is unknown;
    unknown units never silently convert."""

    key = normalize_unit(unit)
    return _UNIT_TO_BASE.get(key) if key else None


_X_KIND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("load_current", re.compile(r"load\s*current|output\s*current|i\s*out\b", re.I)),
    ("input_voltage", re.compile(r"input\s*voltage|supply\s*voltage|v\s*in\b|v\s*cc\b|v\s*dd\b", re.I)),
    ("output_voltage", re.compile(r"output\s*voltage|v\s*out\b", re.I)),
    ("temperature", re.compile(r"temperature|\bta\b|ambient", re.I)),
    ("frequency", re.compile(r"frequency|\bfsw\b|mclk|clock", re.I)),
    ("time", re.compile(r"\btime\b|pulse\s*width|duration", re.I)),
    ("gate_voltage", re.compile(r"v\s*gs\b|gate.*voltage", re.I)),
)
_Y_KIND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("efficiency", re.compile(r"efficiency", re.I)),
    ("power_dissipation", re.compile(r"dissipation|power\s*loss", re.I)),
    ("current_consumption", re.compile(
        r"quiescent|supply\s*current|active.*current|shutdown.*current|"
        r"current\s*consumption| IDD\b|iq\b", re.I)),
    ("current_output", re.compile(r"output\s*current|short.*current|peak.*current|"
                                  r"inductor\s*current", re.I)),
    ("rds_on", re.compile(r"rds\s*\(?\s*on|rds_on", re.I)),
    ("thermal_impedance", re.compile(r"z\s*th|thermal\s*impedance|r\s*th|"
                                     r"transient", re.I)),
    ("voltage_threshold", re.compile(r"threshold\s*voltage|voltage", re.I)),
    ("current", re.compile(r"current", re.I)),
)

# quantity kind swept by the x axis -> condition keys that are NOT fixed
_SWEEP_KEYS = {
    "load_current": {"iout_a", "id_a"},
    "input_voltage": {"vin_v", "vcc_v", "vdd_v"},
    "output_voltage": {"vout_v"},
    "temperature": {"ta_c", "t_c", "tvj_c", "tj_c"},
    "frequency": {"f_hz"},
    "gate_voltage": {"vgs_v"},
}

# --- condition capture ---------------------------------------------------------

_ALIASES = (
    (re.compile(r"\bPVIN\b"), "VIN"),
    (re.compile(r"ƒS\b"), "FSW"),
    (re.compile(r"\bEN\b(?=\s*=)"), "VEN"),
)

_CATEGORICAL = re.compile(
    r"\b([A-Za-z][A-Za-z0-9_]{0,5})\s*=\s*([A-Za-z][A-Za-z0-9 .]{0,40}?)"
    r"(?=\s*,|$)"
)

_LEGEND_TEMP = re.compile(r"^\s*([+-]?\d{1,3})\s*C\s*$")


def _alias(text: str) -> str:
    for pattern, replacement in _ALIASES:
        text = pattern.sub(replacement, text)
    return text


def typed_conditions(condition_texts: Sequence[str | None]) -> dict[str, Any]:
    """Typed numeric + categorical conditions from verbatim strings.

    Numeric keys come from the printed-condition model (fail-closed: an
    unparsable string contributes nothing). Categorical assignments
    (``MODE = FCCM``) are kept verbatim-normalized under ``categorical``.
    Duplicates are unioned; a repeated key with a different value is kept
    as a list under ``conflict`` — never averaged, never first-wins-silent.
    """

    keys: dict[str, Any] = {}
    categorical: dict[str, str] = {}
    conflicts: list[dict[str, Any]] = []
    verbatim: list[str] = []
    for text in condition_texts:
        if not text or not str(text).strip():
            continue
        verbatim.append(str(text).strip())
        aliased = _alias(str(text))
        parsed = parse_condition(aliased)
        if "keys" in parsed:
            for key, value in parsed["keys"].items():
                if key in keys and keys[key] != value:
                    conflicts.append(
                        {"key": key, "values": [keys[key], value]}
                    )
                else:
                    keys[key] = value
        for match in _CATEGORICAL.finditer(aliased):
            symbol = match.group(1).lower()
            value = " ".join(match.group(2).lower().split())
            if symbol in categorical and categorical[symbol] != value:
                conflicts.append(
                    {"key": f"categorical.{symbol}",
                     "values": [categorical[symbol], value]}
                )
            else:
                categorical[symbol] = value
    out: dict[str, Any] = {"keys": keys}
    if categorical:
        out["categorical"] = categorical
    if verbatim:
        out["verbatim"] = verbatim
    resolved: list[dict[str, Any]] = []
    still_conflict: list[dict[str, Any]] = []
    for conflict in conflicts:
        key = conflict["key"]
        values = conflict["values"]
        ranges = [v for v in values if isinstance(v, list)]
        points = [v for v in values if not isinstance(v, list)]
        if len(ranges) == 1 and points and all(
            min(ranges[0]) - 1e-9 <= float(p) <= max(ranges[0]) + 1e-9
            for p in points
        ):
            # printed envelope ("2.25 V to 5.5 V") + stated default
            # ("Typical values at VIN = 5 V"): the point is the condition,
            # the range is the envelope — resolved, never silently
            keys[key] = points[-1]
            resolved.append({"key": key, "condition": points[-1],
                             "envelope": ranges[0]})
        else:
            still_conflict.append(conflict)
    if resolved:
        out["resolved_envelopes"] = resolved
    if still_conflict:
        out["conflict"] = still_conflict
    return out


def _drop_sweep(conditions: Mapping[str, Any], x_kind: str | None) -> dict[str, Any]:
    drop = _SWEEP_KEYS.get(x_kind or "", set())
    if not drop:
        return dict(conditions)
    out = dict(conditions)
    keys = {k: v for k, v in dict(conditions.get("keys") or {}).items()
            if k not in drop}
    out["keys"] = keys
    verbatim = [v for v in list(conditions.get("verbatim") or [])
                if not any(re.search(rf"\b{re.escape(k)}\s*=", _alias(v), re.I)
                           for k in drop)]
    if verbatim:
        out["verbatim"] = verbatim
    elif "verbatim" in out:
        del out["verbatim"]
    return out


def _values_agree(a: Any, b: Any) -> bool:
    def rng(v):
        if isinstance(v, list):
            return (float(min(v)), float(max(v)))
        return (float(v), float(v))

    ab, ae = rng(a)
    bb, be = rng(b)
    eps = 1e-9 * max(1.0, abs(ab), abs(ae), abs(bb), abs(be))
    return ab <= be + eps and bb <= ae + eps


def condition_compatibility(
    curve_conditions: Mapping[str, Any] | None,
    required: Mapping[str, Any] | None,
    *,
    x_kind: str | None = None,
    x_value: float | None = None,
) -> dict[str, Any]:
    """R2 — comparable only when every required numeric condition is stated
    by the curve and agrees; missing or disagreeing conditions are
    ``not_comparable`` with a reason. Missing conditions are never defaulted.

    required: typed keys ({vin_v: 12.0, ...}) plus optional
    ``categorical`` map ({mode: 'fccm'}).

    Sweep law: a required key that names the curve's own x quantity is
    satisfied by the operating point itself (x_value), never by a fixed
    printed condition; disagreeing with x_value is a mismatch.
    """

    req_keys = dict(required or {})
    req_categorical = {}
    if "categorical" in req_keys:
        req_categorical = dict(req_keys.pop("categorical") or {})
    cond = dict(curve_conditions or {})
    keys = dict(cond.get("keys") or {})
    cat = dict(cond.get("categorical") or {})
    sweep = _SWEEP_KEYS.get(x_kind or "", set())
    if sweep and x_value is not None:
        swept = {k: v for k, v in req_keys.items() if k in sweep}
        req_keys = {k: v for k, v in req_keys.items() if k not in sweep}
    else:
        swept = {}
    if cond.get("conflict"):
        return {
            "status": "not_comparable",
            "reason": "condition_conflict",
            "detail": cond["conflict"],
        }
    if not req_keys and not req_categorical:
        matched = {}
        mismatched = []
        for key, value in swept.items():
            if _values_agree(x_value, value):
                matched[key] = {"swept_at": x_value}
            else:
                mismatched.append(
                    {"key": key, "curve": {"swept_at": x_value},
                     "required": value}
                )
        if mismatched:
            return {"status": "not_comparable",
                    "reason": "condition_mismatch",
                    "missing": [], "mismatched": mismatched}
        return {"status": "comparable", "matched": matched,
                "missing": [], "mismatched": []}
    missing: list[str] = []
    mismatched: list[dict[str, Any]] = []
    matched: dict[str, Any] = {}
    for key, value in req_keys.items():
        if key not in keys:
            missing.append(key)
        elif not _values_agree(keys[key], value):
            mismatched.append(
                {"key": key, "curve": keys[key], "required": value}
            )
        else:
            matched[key] = keys[key]
    for key, value in swept.items():
        if _values_agree(x_value, value):
            matched[key] = {"swept_at": x_value}
        else:
            mismatched.append(
                {"key": key, "curve": {"swept_at": x_value},
                 "required": value}
            )
    for key, value in req_categorical.items():
        want = " ".join(str(value).lower().split())
        if key not in cat:
            missing.append(f"categorical.{key}")
        elif cat[key] != want:
            mismatched.append(
                {"key": f"categorical.{key}", "curve": cat[key],
                 "required": want}
            )
        else:
            matched[f"categorical.{key}"] = cat[key]
    if missing or mismatched:
        reason = "condition_mismatch" if mismatched else "condition_missing"
        return {
            "status": "not_comparable",
            "reason": reason,
            "missing": missing,
            "mismatched": mismatched,
        }
    return {
        "status": "comparable",
        "matched": matched,
        "missing": [],
        "mismatched": [],
    }


# --- evidence class ------------------------------------------------------------

def evidence_class(*texts: str | None) -> str:
    """Classify the printed evidence class: typical wins (a typical-
    characteristics page with a max column is still a typical page)."""

    blob = " ".join(str(t or "") for t in texts).lower()
    if re.search(r"\btypical\b|\btyp\b", blob):
        return "typical"
    if re.search(r"worst[- ]case|guarantee", blob):
        return "guaranteed"
    if re.search(r"\bminimum\b|\.?min\b", blob):
        return "minimum"
    if re.search(r"\bmaximum\b|\.?max\b", blob):
        return "maximum"
    return "unspecified"


# --- relevance registry (R5) ----------------------------------------------------

PHENOMENA: dict[str, dict[str, Any]] = {
    "efficiency_vs_load": {
        "y_kind": "efficiency", "x_kind": "load_current",
        "categories": ["power.dcdc", "power.battery"],
        "phenomenon": "converter efficiency as a function of load current",
        "use_cases": ["efficiency_ranking_at_operating_point",
                      "light_load_efficiency", "load_profile_efficiency",
                      "thermal_budget"],
        "required_condition_keys": ["vin_v", "vout_v"],
        "engineer_params": ["load current or bounded load profile",
                            "input voltage", "output voltage",
                            "switching frequency (when the legend splits it)"],
        "constraints": ["typical curve at a stated temperature (often 25C)",
                        "comparison valid only at matched conditions"],
        "limitations": ["typical, not guaranteed", "specific BOM/PCB",
                        "no extrapolation below the plotted minimum load"],
    },
    "power_dissipation_vs_load": {
        "y_kind": "power_dissipation", "x_kind": "load_current",
        "categories": ["power.dcdc", "power.battery"],
        "phenomenon": "converter power loss as a function of load current",
        "use_cases": ["thermal_budget", "efficiency_ranking_at_operating_point"],
        "required_condition_keys": ["vin_v", "vout_v"],
        "engineer_params": ["load current", "input voltage",
                            "output voltage"],
        "constraints": ["typical curve, stated ambient"],
        "limitations": ["typical, not guaranteed", "board/BOM specific"],
    },
    "supply_current_vs_temperature": {
        "y_kind": "current_consumption", "x_kind": "temperature",
        "categories": ["power.dcdc", "mcu"],
        "phenomenon": "supply/quiescent current over ambient temperature",
        "use_cases": ["standby_power_budget", "battery_life",
                      "hot_quiescent_drift"],
        "required_condition_keys": [],
        "engineer_params": ["ambient temperature", "input voltage",
                            "device mode"],
        "constraints": ["per printed enable/mode condition"],
        "limitations": ["typical, not guaranteed"],
    },
    "supply_current_vs_input_voltage": {
        "y_kind": "current_consumption", "x_kind": "input_voltage",
        "categories": ["power.dcdc", "mcu"],
        "phenomenon": "supply current over input voltage",
        "use_cases": ["standby_power_budget", "battery_life"],
        "required_condition_keys": [],
        "engineer_params": ["input voltage", "temperature", "device mode"],
        "constraints": ["per printed enable/mode condition"],
        "limitations": ["typical, not guaranteed"],
    },
    "output_current_capability_vs_input_voltage": {
        "y_kind": "current_output", "x_kind": "input_voltage",
        "categories": ["power.dcdc"],
        "phenomenon": "current limit / minimum peak current vs input voltage",
        "use_cases": ["overload_behavior", "startup_heavily_loaded"],
        "required_condition_keys": [],
        "engineer_params": ["input voltage", "temperature"],
        "constraints": ["per printed output/mode condition"],
        "limitations": ["typical, not guaranteed",
                        "current limit is not a continuous rating"],
    },
    "threshold_voltage_vs_temperature": {
        "y_kind": "voltage_threshold", "x_kind": "temperature",
        "categories": ["power.dcdc", "power.gate"],
        "phenomenon": "enable/precision threshold over temperature",
        "use_cases": ["enable_logic_design", "uvlo_margin"],
        "required_condition_keys": [],
        "engineer_params": ["ambient temperature"],
        "constraints": ["per printed hysteresis direction (up/dn series)"],
        "limitations": ["typical, not guaranteed"],
    },
    "case_temperature_limit_vs_load": {
        "y_kind": "temperature", "x_kind": "load_current",
        "categories": ["power.dcdc"],
        "phenomenon": "maximum case temperature the part sustains at a "
                      "given load (printed derating envelope orientation)",
        "use_cases": ["thermal_derating", "warm_enclosure_operation",
                      "thermal_budget"],
        "required_condition_keys": [],
        "engineer_params": ["load current", "case cooling assumption"],
        "constraints": ["per printed input/output/fsw page conditions"],
        "limitations": ["typical, not guaranteed",
                        "case temperature, not junction",
                        "no heat-sink or airflow model attached"],
    },
    "output_voltage_regulation_vs_load": {
        "y_kind": "voltage", "x_kind": "load_current",
        "categories": ["power.dcdc"],
        "phenomenon": "output voltage regulation over load",
        "use_cases": ["load_regulation", "output_accuracy"],
        "required_condition_keys": [],
        "engineer_params": ["load current", "input voltage"],
        "constraints": ["per printed output rail"],
        "limitations": ["typical, not guaranteed"],
    },
    "output_voltage_regulation_vs_input_voltage": {
        "y_kind": "voltage", "x_kind": "input_voltage",
        "categories": ["power.dcdc"],
        "phenomenon": "output voltage regulation over input voltage",
        "use_cases": ["line_regulation", "output_accuracy"],
        "required_condition_keys": [],
        "engineer_params": ["input voltage", "load current"],
        "constraints": ["per printed output rail"],
        "limitations": ["typical, not guaranteed"],
    },
}


def quantity_kind(axis: Mapping[str, Any], side: str) -> str | None:
    label = f"{axis.get('label') or ''}"
    unit = normalize_unit(axis.get("unit"))
    family = _UNIT_FAMILY.get(unit or "")
    patterns = _X_KIND_PATTERNS if side == "x" else _Y_KIND_PATTERNS
    for kind, pattern in patterns:
        if pattern.search(label):
            if side == "y" and kind in ("current_output", "current_consumption"):
                return kind
            return kind
    if side == "y":
        return {"voltage": "voltage", "current": "current",
                "ratio": "ratio", "power": "power",
                "resistance": "resistance", "frequency": "frequency",
                "temperature": "temperature"}.get(family)
    return {"voltage": "voltage", "current": "current",
            "frequency": "frequency", "temperature": "temperature",
            "time": "time"}.get(family)


def relevance_tags(curve: "CurveEvidence") -> list[dict[str, Any]]:
    x_kind = quantity_kind(curve.axes.get("x") or {}, "x")
    y_kind = quantity_kind(curve.axes.get("y") or {}, "y")
    tags = []
    for name, spec in PHENOMENA.items():
        if spec["x_kind"] == x_kind and spec["y_kind"] == y_kind:
            tags.append({
                "phenomenon": name,
                "categories": spec["categories"],
                "use_cases": spec["use_cases"],
                "engineer_params": spec["engineer_params"],
                "constraints": spec["constraints"],
                "limitations": spec["limitations"],
                "supported_region": curve.supported_region,
            })
    return tags


def condition_sufficiency(
    curve: "CurveEvidence",
    required: Mapping[str, Any] | None,
) -> list[str]:
    """Phenomenon-required condition keys the engineer did not supply.
    Non-empty means insufficient — the comparison is refused rather than
    run at silently borrowed conditions (R2: no defaults, even ones the
    curve itself prints; the response names the unmatched keys instead)."""

    given = set(dict(required or {}))
    if "categorical" in given:
        categorical = dict(required or {}).get("categorical") or {}
        given |= {f"categorical.{k}" for k in categorical}
        given.discard("categorical")
    missing: list[str] = []
    for tag in curve.relevance:
        for key in PHENOMENA[tag["phenomenon"]]["required_condition_keys"]:
            if key not in given:
                missing.append(key)
    return sorted(set(missing))


# --- curve evidence object (R1) -------------------------------------------------

_VERDICT_USABLE = {"extracted", "reference"}


class CurveEvidence:
    """One digitized curve (one series of one figure) with provenance,
    typed conditions, evidence class, applicability, uncertainty and
    verification status. The underlying payload is preserved verbatim."""

    def __init__(
        self,
        *,
        document_sha256: str,
        page_1based: int,
        figure_index: int,
        series_index: int,
        caption: str | None,
        region_bbox: Sequence[float] | None,
        figure_revision: str | None,
        axes: Mapping[str, Mapping[str, Any]],
        series: Mapping[str, Any],
        conditions_verbatim: Sequence[str],
        evidence_class_: str,
        applies_to: Mapping[str, str | None],
        uncertainty: Mapping[str, Any],
        verification: Mapping[str, Any],
        source_path: str | None = None,
        render_sha256: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> None:
        self.document_sha256 = document_sha256
        self.page_1based = page_1based
        self.figure_index = figure_index
        self.series_index = series_index
        self.caption = caption
        self.region_bbox = tuple(region_bbox) if region_bbox else None
        self.figure_revision = figure_revision
        self.axes = dict(axes)
        self.series = dict(series)
        self.conditions_verbatim = list(conditions_verbatim)
        self.evidence_class = evidence_class_
        self.applies_to = dict(applies_to)
        self.uncertainty = dict(uncertainty)
        self.verification = dict(verification)
        self.source_path = source_path
        self.render_sha256 = render_sha256
        self.payload = dict(payload or {})
        self.x_kind = quantity_kind(self.axes.get("x") or {}, "x")
        self.y_kind = quantity_kind(self.axes.get("y") or {}, "y")
        def _pt(p: Any) -> dict[str, float] | None:
            if isinstance(p, Mapping):
                x, y = p.get("x"), p.get("y")
            elif isinstance(p, (list, tuple)) and len(p) == 2:
                x, y = p[0], p[1]
            else:
                return None
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                return {"x": float(x), "y": float(y)}
            return None

        points = sorted(
            (p for p in (_pt(raw) for raw in series.get("points") or []) if p),
            key=lambda p: p["x"],
        )
        self.points = points
        xs = [p["x"] for p in points]
        self.supported_region = (
            {"min": xs[0], "max": xs[-1]} if xs else None
        )
        self.x_scale = str((self.axes.get("x") or {}).get("scale") or "linear")
        name_text = str(series.get("name") or "")
        self.conditions = _drop_sweep(
            typed_conditions(
                list(conditions_verbatim) + [series.get("condition")]
            ),
            self.x_kind,
        )
        # legend conditions override page/plot defaults (challenge #1:
        # "VIN = 12 V" legend vs "VIN = 13.5 V unless otherwise specified")
        name_parsed = _drop_sweep(
            typed_conditions([name_text]), self.x_kind
        )
        overrides: dict[str, Any] = {}
        keys = dict(self.conditions.get("keys") or {})
        for key, value in dict(name_parsed.get("keys") or {}).items():
            if key in keys and keys[key] != value:
                overrides[key] = {"page_default": keys[key],
                                  "series_legend": value}
            keys[key] = value
        self.conditions["keys"] = keys
        if overrides:
            self.conditions["legend_override"] = {
                **dict(self.conditions.get("legend_override") or {}), **overrides,
            }
        legend_temp = _LEGEND_TEMP.match(name_text)
        if legend_temp:
            value = float(legend_temp.group(1))
            # legend temperatures on these plots are ambient (TA); the
            # series-specific value overrides the page-level default
            if keys.get("ta_c") != value:
                overridden = keys.pop("ta_c", None)
                keys["ta_c"] = value
                self.conditions["keys"] = keys
                self.conditions["legend_temperature"] = True
                if overridden is not None:
                    self.conditions["legend_override"] = {
                        **dict(self.conditions.get("legend_override") or {}),
                        "ta_c": {"page_default": overridden,
                                 "series_legend": value},
                    }
        self.curve_id = "curve-" + hashlib.sha256(
            f"{document_sha256}:{page_1based}:{figure_index}:{series_index}"
            .encode()
        ).hexdigest()[:32]
        self.relevance = relevance_tags(self)

    # -- constructors --------------------------------------------------------

    @classmethod
    def from_reference_plot(
        cls,
        record: Mapping[str, Any],
        plot: Mapping[str, Any],
        series_index: int,
        *,
        applies_to: Mapping[str, str | None] | None = None,
        figure_revision: str | None = None,
    ) -> "CurveEvidence":
        """Build evidence from a frozen reference/gold plot record."""

        series = (plot.get("series") or [])[series_index]
        axes = plot.get("axes") or {}
        apply = dict(applies_to or {})
        if not apply.get("part"):
            part_match = re.search(
                r"\b(SiC\d{3}|TPS\d+[A-Z0-9]*|LM[RFQ]\d+[A-Z0-9\-]*|"
                r"NCP\d+[A-Z0-9]*|MCP\d+[A-Z0-9]*)\b",
                str(plot.get("title") or ""),
            )
            if part_match:
                apply.setdefault("part", part_match.group(1))
        if not apply.get("part"):
            stem = str(record.get("source_artifact") or "")
            part = re.sub(r"\.(pdf|json)$", "", stem, flags=re.I)
            part = re.sub(r"^(dcdc|battery|gate|isolation|ldo|mosfet)_", "", part)
            apply.setdefault("part", part or None)
        if not apply.get("manufacturer"):
            apply.setdefault(
                "manufacturer", record.get("manufacturer") or None
            )
        if not apply.get("category"):
            stem = str(record.get("source_artifact") or "")
            m = re.match(r"(dcdc|battery|gate|isolation|ldo|mosfet)_", stem, re.I)
            apply.setdefault(
                "category", f"power.{m.group(1).lower()}" if m else "power.dcdc"
            )
        conds = list(plot.get("conditions_plot") or [])
        conds += list(plot.get("conditions_page") or [])
        return cls(
            document_sha256=str(record.get("document_sha256") or ""),
            page_1based=int(record.get("page_1based") or 0),
            figure_index=int(plot.get("_figure_index") or 0),
            series_index=series_index,
            caption=plot.get("title"),
            region_bbox=plot.get("_bbox"),
            figure_revision=figure_revision or record.get("figure_revision"),
            axes=axes,
            series=series,
            conditions_verbatim=conds,
            evidence_class_=evidence_class(
                plot.get("title"), record.get("section_title"),
                record.get("source"),
                *conds,
            ),
            applies_to=apply,
            uncertainty={
                "method": "vector_reference",
                "point_error_pct": None,
                "note": "deterministic geometry from the vector PDF; "
                        "human sign-off pending",
                "numeric_quality": plot.get("_numeric_quality"),
                "human_signed": bool(record.get("_human_signed")),
            },
            verification={"status": "reference", "grounding": None,
                          "anchor_agreement": None},
            source_path=record.get("source_path"),
            render_sha256=record.get("render_sha256"),
            payload=plot,
        )

    @classmethod
    def from_extraction_row(
        cls,
        row: Mapping[str, Any],
        *,
        applies_to: Mapping[str, str | None] | None = None,
        section_text: str | None = None,
    ) -> list["CurveEvidence"]:
        """Upgrade a wave-A ``extract_typical_curves`` row into evidence
        objects (one per series). Rows that are not usable verifications
        still convert — with a verification status that blocks querying."""

        payload = row.get("payload") or {}
        axes = payload.get("axes") or {}
        out = []
        for index, series in enumerate(payload.get("series") or []):
            out.append(cls(
                document_sha256=str(row.get("document_sha256") or ""),
                page_1based=int(row.get("page_1based") or 0),
                figure_index=int(row.get("figure_index") or 0),
                series_index=index,
                caption=payload.get("title") or row.get("caption"),
                region_bbox=row.get("region_bbox"),
                figure_revision=row.get("figure_revision"),
                axes=axes,
                series=series,
                conditions_verbatim=[
                    series.get("condition") or "",
                    payload.get("condition") or "",
                    row.get("condition") or "",
                ],
                evidence_class_=evidence_class(
                    payload.get("title"), section_text
                ),
                applies_to=dict(applies_to or {}),
                uncertainty={
                    "method": "vision_digitization",
                    "point_error_pct": row.get("point_error_pct"),
                    "note": "unvalidated vision digitization unless a gold "
                            "score is attached",
                },
                verification={
                    "status": str(row.get("verdict") or "unknown"),
                    "grounding": not row.get("ungrounded_labels"),
                    "anchor_agreement": (
                        (row.get("anchor_check") or {}).get("agreement_rate")
                    ),
                },
                source_path=row.get("source_path"),
                render_sha256=row.get("render_sha256"),
                payload=payload,
            ))
        return out

    # -- law helpers -----------------------------------------------------------

    @property
    def usable(self) -> bool:
        return self.verification.get("status") in _VERDICT_USABLE and \
            bool(self.points)

    def citation(self) -> dict[str, Any]:
        return {
            "curve_id": self.curve_id,
            "document_sha256": self.document_sha256,
            "source_path": self.source_path,
            "page_1based": self.page_1based,
            "figure_index": self.figure_index,
            "series_index": self.series_index,
            "caption": self.caption,
            "figure_revision": self.figure_revision,
            "render_sha256": self.render_sha256,
            "region_bbox": list(self.region_bbox) if self.region_bbox else None,
            "series_name": self.series.get("name"),
            "conditions_verbatim": self.conditions_verbatim,
        }

    def _resolution(self) -> float | None:
        """Median sample spacing along x (in scale space for log axes) —
        the bound on what the digitization can resolve."""

        if len(self.points) < 2:
            return None

        def sx(v: float) -> float:
            return math.log10(v) if self.x_scale == "log10" and v > 0 else v

        gaps = [
            abs(sx(b["x"]) - sx(a["x"]))
            for a, b in zip(self.points, self.points[1:])
        ]
        gaps.sort()
        n = len(gaps)
        return gaps[n // 2] if n % 2 else (gaps[n // 2 - 1] + gaps[n // 2]) / 2.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": CURVE_EVIDENCE_SCHEMA,
            "curve_id": self.curve_id,
            "document_sha256": self.document_sha256,
            "page_1based": self.page_1based,
            "figure_index": self.figure_index,
            "series_index": self.series_index,
            "caption": self.caption,
            "region_bbox": list(self.region_bbox) if self.region_bbox else None,
            "figure_revision": self.figure_revision,
            "x": {
                "quantity_kind": self.x_kind,
                "label": (self.axes.get("x") or {}).get("label"),
                "unit": (self.axes.get("x") or {}).get("unit"),
                "scale": self.x_scale,
                **{
                    k: (self.axes.get("x") or {}).get(k)
                    for k in ("min", "max")
                },
            },
            "y": {
                "quantity_kind": self.y_kind,
                "label": (self.axes.get("y") or {}).get("label"),
                "unit": (self.axes.get("y") or {}).get("unit"),
                **{
                    k: (self.axes.get("y") or {}).get(k)
                    for k in ("min", "max")
                },
            },
            "series_name": self.series.get("name"),
            "points": self.points,
            "conditions": self.conditions,
            "evidence_class": self.evidence_class,
            "applies_to": self.applies_to,
            "uncertainty": self.uncertainty,
            "verification": self.verification,
            "supported_region": self.supported_region,
            "resolution": self._resolution(),
            "relevance": self.relevance,
            "payload": self.payload,
        }


# --- operating-point query (R3) --------------------------------------------------

def _interp(points: Sequence[Mapping[str, float]], x: float,
            scale: str) -> float | None:
    """Bounded linear interpolation along the digitized polyline; None when
    x sits outside the sampled support (no clamping, no extrapolation)."""

    def s(v: float) -> float:
        return math.log10(v) if scale == "log10" and v > 0 else v

    sx = s(x)
    for left, right in zip(points, points[1:]):
        a, b = s(left["x"]), s(right["x"])
        if a <= sx <= b:
            if b == a:
                return float(left["y"])
            t = (sx - a) / (b - a)
            return float(left["y"] + t * (right["y"] - left["y"]))
    return None


def convert_value(value: float, from_unit: str | None,
                  to_unit: str | None) -> float | None:
    """Unit-family-aware conversion (A<->mA, V<->mV, ...). None when the
    families differ — incompatible units never silently convert."""

    a = unit_base(from_unit)
    b = unit_base(to_unit)
    if a is None or b is None or a[0] != b[0]:
        return None
    return value * a[1] / b[1]


def query_operating_point(
    curve: CurveEvidence,
    x: float,
    *,
    required_conditions: Mapping[str, Any] | None = None,
    x_unit: str | None = None,
) -> dict[str, Any]:
    """Evaluate one curve at an operating point, bounded and cited.

    Statuses: ok | out_of_range | not_comparable | not_usable.
    Extrapolation never happens: x must sit inside the sampled support.
    x_unit states the engineer's unit; it converts to the curve's printed
    axis unit when the families match, and refuses when they do not.
    The evidence class rides the result — a typical curve yields a typical
    value, never a guaranteed limit.
    """

    base = {
        "curve_id": curve.curve_id,
        "citation": curve.citation(),
        "supported_region": curve.supported_region,
        "evidence_class": curve.evidence_class,
    }
    if not curve.usable:
        return {
            **base,
            "status": "not_usable",
            "reason": f"verification:{curve.verification.get('status')}",
        }
    curve_unit = (curve.axes.get("x") or {}).get("unit")
    unit_note = None
    if x_unit is not None and normalize_unit(x_unit) != normalize_unit(curve_unit):
        if not curve_unit:
            # axis label names the quantity but prints no unit; the query
            # proceeds in the printed numbers and says so — never silently
            unit_note = "curve_axis_unit_unstated"
        else:
            converted = convert_value(float(x), x_unit, curve_unit)
            if converted is None:
                return {
                    **base,
                    "status": "not_usable",
                    "reason": f"unit_incompatible:{x_unit}->{curve_unit}",
                }
            x = converted
    compat = condition_compatibility(
        curve.conditions, required_conditions,
        x_kind=curve.x_kind, x_value=float(x),
    )
    if compat["status"] != "comparable":
        return {**base, "status": "not_comparable", "reason": compat["reason"],
                "compatibility": compat}
    region = curve.supported_region
    eps = 1e-9 * max(1.0, abs(region["min"]), abs(region["max"]))
    if x < region["min"] - eps or x > region["max"] + eps:
        return {
            **base,
            "status": "out_of_range",
            "reason": "x outside the plotted support; extrapolation "
                      "requires a separately approved engineering model",
            "requested": x,
        }
    value = _interp(curve.points, float(x), curve.x_scale)
    if value is None:  # single-point support or degenerate spacing
        return {**base, "status": "out_of_range",
                "reason": "insufficient point support"}
    nearest = min(curve.points, key=lambda p: abs(p["x"] - x))
    resolution = curve._resolution()
    local = None
    for left, right in zip(curve.points, curve.points[1:]):
        if left["x"] <= x <= right["x"]:
            local = abs(right["x"] - left["x"])
            break
    return {
        **base,
        "status": "ok",
        "x": x,
        "unit_note": unit_note,
        "value": value,
        "unit": (curve.axes.get("y") or {}).get("unit"),
        "guarantee": False,
        "typical_note": (
            "typical value from a printed curve — not a guaranteed limit"
            if curve.evidence_class == "typical" else
            f"evidence class: {curve.evidence_class}"
        ),
        "uncertainty": {
            **curve.uncertainty,
            "resolution": resolution,
            "local_sample_spacing": local,
            "distance_to_nearest_sample": abs(nearest["x"] - x),
        },
        "matched_conditions": compat["matched"],
        "conditions_verbatim": curve.conditions_verbatim,
    }


# --- derived decision evidence (R4) ------------------------------------------------

_DERIVED_METHOD = "linear_interpolation_on_digitized_polyline"


def compare_at_operating_point(
    curves: Sequence[CurveEvidence],
    x: float,
    *,
    required_conditions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Relative performance across parts/curves at one operating point.

    Every entry cites its curve, method, matched conditions and
    uncertainty. The result is a PROPOSAL until validated under the aisle
    policy — typical curves never rank parts into hard verdicts here.
    """

    entries = []
    rejected = []
    for curve in curves:
        result = query_operating_point(
            curve, x, required_conditions=required_conditions
        )
        if result["status"] == "ok":
            entries.append({
                "curve_id": curve.curve_id,
                "part": curve.applies_to.get("part"),
                "series_name": curve.series.get("name"),
                "value": result["value"],
                "unit": result["unit"],
                "evidence_class": curve.evidence_class,
                "citation": result["citation"],
                "uncertainty": result["uncertainty"],
            })
        else:
            rejected.append({
                "curve_id": curve.curve_id,
                "part": curve.applies_to.get("part"),
                "status": result["status"],
                "reason": result.get("reason"),
            })
    entries.sort(key=lambda e: -(e["value"] if isinstance(e["value"], (int, float)) else 0))
    unpinned: dict[str, list[str]] = {}
    for curve in curves:
        for key, value in (curve.conditions.get("categorical") or {}).items():
            want = (dict(required_conditions or {}).get("categorical") or {}).get(key)
            if want is None:
                unpinned.setdefault(f"categorical.{key}", [])
                if value not in unpinned[f"categorical.{key}"]:
                    unpinned[f"categorical.{key}"].append(value)
    return {
        "schema": CURVE_EVIDENCE_SCHEMA,
        "kind": "comparison_at_operating_point",
        "status": "proposal",
        "x": x,
        "required_conditions": dict(required_conditions or {}),
        "method": _DERIVED_METHOD,
        "assumptions": [
            "values are typical printed-curve readings at matched "
            "conditions, not guaranteed limits",
            "linear interpolation between digitized samples, bounded by "
            "the plotted support",
        ],
        "condition_dimensions_unpinned": unpinned or None,
        "entries": entries,
        "rejected": rejected,
        "note": "proposal evidence only — promotion to any elimination "
                "rule requires the aisle qualification policy",
    }


def evaluate_load_distribution(
    curve: CurveEvidence,
    segments: Sequence[Mapping[str, Any]],
    *,
    required_conditions: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Weighted mean of y over a bounded load profile
    (segments: [{x, weight}]). Any segment outside the support rejects the
    whole evaluation — a profile is answered only when fully covered."""

    compat = condition_compatibility(curve.conditions, required_conditions)
    if compat["status"] != "comparable":
        return {
            "curve_id": curve.curve_id,
            "status": "not_comparable",
            "reason": compat["reason"],
            "compatibility": compat,
        }
    total_weight = sum(float(s.get("weight") or 0) for s in segments)
    if total_weight <= 0:
        return {"curve_id": curve.curve_id, "status": "rejected",
                "reason": "profile weights must be positive"}
    weighted = 0.0
    for segment in segments:
        x = float(segment["x"])
        result = query_operating_point(
            curve, x, required_conditions=required_conditions
        )
        if result["status"] != "ok":
            return {
                "curve_id": curve.curve_id,
                "status": "out_of_range",
                "reason": f"profile point {x} outside plotted support",
                "requested": x,
            }
        weighted += float(segment["weight"]) * result["value"]
    return {
        "schema": CURVE_EVIDENCE_SCHEMA,
        "kind": "load_distribution_mean",
        "status": "proposal",
        "curve_id": curve.curve_id,
        "citation": curve.citation(),
        "weighted_mean": weighted / total_weight,
        "unit": (curve.axes.get("y") or {}).get("unit"),
        "evidence_class": curve.evidence_class,
        "guarantee": False,
        "method": _DERIVED_METHOD + " + weight mean",
        "matched_conditions": compat["matched"],
    }


__all__ = [
    "CURVE_EVIDENCE_SCHEMA",
    "CurveEvidence",
    "PHENOMENA",
    "compare_at_operating_point",
    "condition_compatibility",
    "evidence_class",
    "evaluate_load_distribution",
    "quantity_kind",
    "query_operating_point",
    "relevance_tags",
    "typed_conditions",
    "unit_base",
]
