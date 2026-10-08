"""Intent-aware query expansion (PRD-SEARCH-01 R3).

Given a human engineering description, propose a small set of alternate
technical interpretations as HYPOTHESES — never confirmed requirements.
The original user wording is preserved verbatim and every interpretation
carries a confidence; consumers (and the API contract) must present these
as "possible readings", not as what the engineer asked for.

Deterministic v1: rule-ordered keyword sets grounded in the axis vocabulary
the extraction lanes already use (condition_model, mode_currents,
power_axes_v2, connector_parametrics, aec_grades). A model-backed expander
can be layered later behind the same return shape; nothing here calls out.
"""

from __future__ import annotations

import re

EXPANSION_SCHEMA = "harness.search-intent-expansion.v1"

_MAX_INTERPRETATIONS = 6

# Each rule: regex over the raw query -> hypotheses (label, axes the
# retrieval layer should ALSO try, confidence). Rules are scanned in order;
# the first match's hypotheses lead. Axes names reuse the extraction lanes'
# vocabulary so downstream filters stay honest.
_RULES: list[tuple[re.Pattern[str], list[tuple[str, tuple[str, ...], float]]]] = [
    (re.compile(r"low[- ]?power|battery|coin[- ]?cell|energy", re.I), [
        ("low sleep / standby current",
         ("sleep_current", "standby_current", "shutdown_current"), 0.85),
        ("low active energy per measurement",
         ("active_current", "energy_per_measurement", "run_current"), 0.7),
        ("low-power operating modes documented",
         ("power_modes", "low_power_mode", "stop_mode"), 0.65),
    ]),
    (re.compile(r"wireless|radio|ble|bluetooth|zigbee|lorawan|wi-?fi|nfc", re.I), [
        ("integrated wireless MCU / transceiver",
         ("integrated_radio", "wireless_mcu", "rf_transceiver"), 0.8),
        ("wireless protocol support documented",
         ("protocol_stack", "modulation", "rf_band"), 0.6),
        ("RF / radio regulatory figures",
         ("tx_power", "rx_sensitivity", "antenna"), 0.55),
    ]),
    (re.compile(r"industrial|factory|plc|sensor node|harsh", re.I), [
        ("industrial communications compatibility",
         ("can_fd", "rs485", "ethernet", "io_link", "modbus"), 0.75),
        ("extended / high-temperature operating range",
         ("operating_temp", "temp_range", "ta_range"), 0.7),
        ("industrial qualification (AEC / Q100 / Q101)",
         ("aec_q100", "aec_q101", "industrial_qualification"), 0.5),
    ]),
    (re.compile(r"hot|high[- ]?temp|temperature|automo|engine|under[- ]?hood", re.I), [
        ("high-temperature operating capability",
         ("operating_temp", "tj_max", "temp_range"), 0.85),
        ("automotive qualification",
         ("aec_q100", "aec_q101", "automotive_qualification"), 0.6),
        ("thermal resistance / dissipation evidence",
         ("rth_ja", "thermal_resistance", "power_dissipation"), 0.5),
    ]),
    (re.compile(r"efficien|loss|switching|converter|smss|smps|power supply|vr", re.I), [
        ("switching-loss / efficiency evidence",
         ("switching_loss", "eoss", "qg", "fom"), 0.8),
        ("conduction loss (RDS(on)) evidence",
         ("rds_on", "conduction_loss"), 0.7),
        ("topology fit (buck/boost/PFC/LLC) evidence",
         ("topology", "buck", "boost", "pfc", "llc"), 0.6),
    ]),
    (re.compile(r"connector|header|terminal|pin header|mating", re.I), [
        ("pitch / positions evidence",
         ("pitch_mm", "positions"), 0.85),
        ("rated current / voltage evidence",
         ("current_rating_a", "voltage_rating_v"), 0.7),
        ("dielectric withstand (test stress, not operating rating)",
         ("dielectric_withstanding_v",), 0.45),
    ]),
    (re.compile(r"sleep|standby|shutdown|quiescent|iq", re.I), [
        ("sleep / standby current",
         ("sleep_current", "standby_current", "shutdown_current"), 0.85),
        ("quiescent current (IQ)",
         ("quiescent_current", "iq"), 0.75),
    ]),
    (re.compile(r"compact|small|tiny|miniature|space|footprint", re.I), [
        ("small package / footprint evidence",
         ("package", "footprint", "wlcsp", "dfn", "qfn"), 0.75),
        ("high integration (few external parts)",
         ("integration_class", "integrated_converter", "all_in_one"), 0.55),
    ]),
]


def expand_intent(query: str) -> dict:
    """Return {original, interpretations[]} — hypotheses, not requirements."""
    original = (query or "").strip()
    # Gather every matched rule's hypotheses, then interleave round-robin so
    # no single dimension (e.g. "low power") starves the others (e.g.
    # "industrial") under the cap.
    per_rule: list[list[dict]] = []
    for pattern, hypotheses in _RULES:
        if not pattern.search(original):
            continue
        rule_hits: list[dict] = []
        for label, axes, confidence in hypotheses:
            rule_hits.append({
                "label": label,
                "axes": list(axes),
                "confidence": confidence,
                "status": "hypothesis",
                "matched_rule": pattern.pattern,
            })
        if rule_hits:
            per_rule.append(rule_hits)
    interpretations: list[dict] = []
    seen_labels: set[str] = set()
    depth = 0
    while len(interpretations) < _MAX_INTERPRETATIONS and \
            any(depth < len(hits) for hits in per_rule):
        for hits in per_rule:
            if depth >= len(hits) or \
                    len(interpretations) >= _MAX_INTERPRETATIONS:
                continue
            candidate = hits[depth]
            if candidate["label"] in seen_labels:
                continue
            seen_labels.add(candidate["label"])
            interpretations.append(candidate)
        depth += 1
    return {
        "schema": EXPANSION_SCHEMA,
        "original": original,
        "interpretations": interpretations[:_MAX_INTERPRETATIONS],
    }


def axis_queries(query: str) -> list[str]:
    """Concrete auxiliary search strings derived from the hypotheses.

    Each is fed to the hybrid retriever alongside the original wording so a
    relevant result is not buried by vocabulary mismatch (R2). The original
    query always goes first and is never rewritten.
    """
    expansion = expand_intent(query)
    aux: list[str] = []
    for interp in expansion["interpretations"]:
        aux.append(f"{interp['label']} {' '.join(interp['axes'][:3])}".strip())
    return aux[:4]
