//! Condition typing — `PRD-NORMALIZE` §5.1.
//!
//! Port of `harness/electronics/condition_model.py::parse_condition`.
//!
//! Input is a **printed test condition** (`VGS = 0 V, VDS = 25 V, f = 1 MHz`).
//! Output is a typed key map, or a refusal. There is no third outcome.
//!
//! The ten laws (L1–L10) in the PRD exist because the corpus raised them.
//! Each is enforced here; the gold fixture `condition_model_v1.jsonl` (167
//! rows) is the executable proof.
//!
//! **Fail closed.** Roughly one in six printed conditions must be refused.
//! An implementation that types everything is inventing facts.

use std::collections::{BTreeMap, BTreeSet};

use fancy_regex::Regex;
use serde::{Deserialize, Serialize};

use dw_types::Verbatim;

use crate::re;

use crate::units::{scale, Kind, Scale};

/// Schema id for typed condition output. Matches the reference.
pub const CONDITION_MODEL_SCHEMA: &str = "harness.electronics-condition-model.v1";

// ---------------------------------------------------------------------------
// vocabulary
// ---------------------------------------------------------------------------

/// A canonical condition anchor. Frozen, additive-only (`C6`).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ConditionKey {
    /// `di F/dt`.
    DifdtAPerS,
    /// `V_GS(on)`.
    VgsOnV,
    /// `V_DS,peak`.
    VdsPeakV,
    /// `T_vj(start)`.
    TvjStartC,
    /// `R_th JA`.
    RthjaCPerW,
    /// `R_G,ext`.
    RgExtOhm,
    /// `L σ`.
    LsigmaH,
    /// `STBY`.
    StbyV,
    /// `I_SINK`.
    IsinkA,
    /// `I_SD`.
    IsdA,
    /// `I_REG`.
    IregA,
    /// `I_AS`.
    IAsA,
    /// `I_OUT` / `I_O`.
    IoutA,
    /// `I_DS` / `I_D`.
    IdA,
    /// `I_F`.
    IfA,
    /// `I_S`.
    IsA,
    /// `V_OUT` / `VO`.
    VoutV,
    /// `V_IN`.
    VinV,
    /// `V_CC`.
    VccV,
    /// `V_DD`.
    VddV,
    /// `V_EN`.
    VenV,
    /// `V_SD`.
    VsdV,
    /// `V_GS`.
    VgsV,
    /// `V_DS`.
    VdsV,
    /// `VR`.
    VrV,
    /// `T_STG`.
    TstgC,
    /// `T_vj`.
    TvjC,
    /// `T_A`.
    TaC,
    /// `T_C`.
    TcC,
    /// `T_J`.
    TjC,
    /// `f` / `f_sw` / `f_osc`.
    FHz,
    /// `f_rr`.
    FrrHz,
    /// `R_GS`.
    RgsOhm,
    /// `R_G`.
    RgOhm,
    /// `L`.
    LH,
    /// `R`.
    ROhm,
    /// Bare number + `°C` — unambiguous in kind (L3).
    TC,
}

impl ConditionKey {
    /// The stable string the reference emits.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::DifdtAPerS => "difdt_a_per_s",
            Self::VgsOnV => "vgs_on_v",
            Self::VdsPeakV => "vds_peak_v",
            Self::TvjStartC => "tvj_start_c",
            Self::RthjaCPerW => "rthja_c_per_w",
            Self::RgExtOhm => "rg_ext_ohm",
            Self::LsigmaH => "lsigma_h",
            Self::StbyV => "stby_v",
            Self::IsinkA => "isink_a",
            Self::IsdA => "isd_a",
            Self::IregA => "ireg_a",
            Self::IAsA => "i_as_a",
            Self::IoutA => "iout_a",
            Self::IdA => "id_a",
            Self::IfA => "if_a",
            Self::IsA => "is_a",
            Self::VoutV => "vout_v",
            Self::VinV => "vin_v",
            Self::VccV => "vcc_v",
            Self::VddV => "vdd_v",
            Self::VenV => "ven_v",
            Self::VsdV => "vsd_v",
            Self::VgsV => "vgs_v",
            Self::VdsV => "vds_v",
            Self::VrV => "vr_v",
            Self::TstgC => "tstg_c",
            Self::TvjC => "tvj_c",
            Self::TaC => "ta_c",
            Self::TcC => "tc_c",
            Self::TjC => "tj_c",
            Self::FHz => "f_hz",
            Self::FrrHz => "frr_hz",
            Self::RgsOhm => "rgs_ohm",
            Self::RgOhm => "rg_ohm",
            Self::LH => "l_h",
            Self::ROhm => "r_ohm",
            Self::TC => "t_c",
        }
    }
}

/// A typed value: a scalar, or a printed range/list.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum CondValue {
    /// A single number.
    Scalar(f64),
    /// A printed range or list: `[lo, hi]`.
    Range(f64, f64),
}

impl CondValue {
    /// The span this value covers, as `(lo, hi)`.
    #[must_use]
    pub const fn span(self) -> (f64, f64) {
        match self {
            Self::Scalar(v) => (v, v),
            Self::Range(lo, hi) => (lo, hi),
        }
    }
}

/// Why a printed condition could not be typed (`PRD-NORMALIZE` Appendix B).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Rejection {
    /// Only dash characters. Never a typed condition.
    Dash,
    /// Punctuation-only, or nothing recognised.
    NoAnchors,
    /// Known symbol + bare number + no unit. Never becomes a `Quantity` (L1).
    UnitsAbsent,
    /// Single-letter `V`/`I` + separator + number. Never typed (L2).
    AmbiguousSymbols,
}

impl Rejection {
    /// The stable string the reference emits.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Dash => "dash",
            Self::NoAnchors => "no_anchors",
            Self::UnitsAbsent => "units_absent",
            Self::AmbiguousSymbols => "ambiguous_symbols",
        }
    }
}

/// Test-condition mode words (L7).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    /// Static.
    Static,
    /// Alternating.
    Ac,
    /// Dynamic.
    Dynamic,
}

/// A typed condition. `verbatim` is the provenance anchor and is never
/// destroyed or replaced (`C3`).
///
/// **No `Deserialize`.** A typed condition must be *produced* by
/// [`parse_condition`], which is where the laws live. Deserialising one from
/// JSON would be a way to bypass [`Verbatim`]'s constructor and the fail-closed
/// rules, so the trait is deliberately absent. Only `Serialize` exists, for the
/// gold comparison.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct TypedCondition {
    /// Canonical SI anchors.
    pub keys: BTreeMap<String, CondValue>,
    /// `static` / `ac` / `dynamic`, when printed.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mode: Option<Mode>,
    /// Keys bound with `≈` / `⋍`.
    #[serde(skip_serializing_if = "BTreeSet::is_empty")]
    pub approx: BTreeSet<String>,
    /// Keys bound with `±`.
    #[serde(skip_serializing_if = "BTreeSet::is_empty")]
    pub sym: BTreeSet<String>,
    /// Keys typed without a printed symbol.
    #[serde(skip_serializing_if = "BTreeSet::is_empty")]
    pub bare: BTreeSet<String>,
    /// Verbatim segments that used a comparator (≤ ≥ < > ≦ ≧ ＜ ＞).
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub comparators: Vec<String>,
    /// Verbatim text no anchor claimed.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub residue: Vec<String>,
    /// The printed string, unchanged (`C3`).
    ///
    /// This is a [`Verbatim`], not a `String`, so a typed condition cannot
    /// exist without its provenance anchor. See
    /// `tests/compile_fail/typed_condition_no_verbatim.rs`.
    #[serde(skip)]
    pub verbatim: Verbatim,
}

/// The only two outcomes of a parse. No `Default`, no silent fallback.
///
/// **No `Deserialize`,** for the same reason as [`TypedCondition`]: the laws
/// are enforced by [`parse_condition`], and a JSON round-trip would route
/// around them. See `tests/compile_fail/`.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(untagged)]
pub enum ParseOutcome {
    /// Typed anchors.
    Typed(TypedCondition),
    /// Refused, with the reason.
    Rejected {
        /// Why the condition could not be typed.
        reject: Rejection,
    },
}

// ---------------------------------------------------------------------------
// regex vocabulary — ported verbatim from the reference
// ---------------------------------------------------------------------------

const NUM: &str = r"[-+]?\d+(?:\.\d+)?";
const SEP_CLASS: &str = r"=≤≥≦≧<>＜＞";
const RANGE_SEP: &str = r"(?:to|/|~|–|—)";
const TEMP_UNIT: &str = r"(?:°\s*C(?![A-Za-z])|℃|º\s*C(?![A-Za-z]))";
const CMP_CHARS: &str = "≤≥≦≧<>＜＞";

fn sep_re() -> Regex {
    // Python: `_SEP_RE.match(tail)` — anchored at the start of `tail`.
    re::build(&format!(r"^(?:\s*[{SEP_CLASS}]\s*|\s*[≈⋍]\s*)"))
}

fn dash_only() -> Regex {
    re::build(r"^[\s\-–—―‒]+$")
}

fn punct_only() -> Regex {
    re::build(r"^[\s\-–—.();:]*$")
}

fn mode_re() -> Regex {
    // Reference compiles with `re.I`.
    re::build(r"(?i)\b(static|ac|dynamic)\b")
}

fn ambig_single() -> Regex {
    re::build(&format!(r"\b[VI]\s*[{SEP_CLASS}]\s*[-+]?\d"))
}

/// Unit regex per kind (`_UNIT_RE` in the reference).
fn unit_re(kind: Kind) -> String {
    match kind {
        Kind::Volt => r"(?:mV|kV|V)\b".to_string(),
        Kind::Ampere => r"(?:nA|µA|μA|uA|mA|kA|A)\b".to_string(),
        Kind::Hertz => r"(?:kHz|MHz|GHz|Hz)\b".to_string(),
        Kind::Celsius => TEMP_UNIT.to_string(),
        Kind::CelsiusPerWatt => r"(?:(?:°\s*C|℃|º\s*C|K)\s*/\s*W)".to_string(),
        Kind::Ohm => r"(?:mΩ|kΩ|MΩ|Ω|mohm|kohm|ohm)\b".to_string(),
        Kind::Henry => r"(?:nH|µH|μH|uH|mH|H)\b".to_string(),
        Kind::AmperePerSecond => r"(?:A\s*/\s*(?:µs|μs|us|ns|ms|s))\b".to_string(),
    }
}

fn value_regex(kind: Kind) -> Regex {
    let p = kind.prefix();
    let u = unit_re(kind);
    // Python: `_VALUE_RE[kind].match(tail, pos)` — anchored at `pos`.
    // NOTE: Rust raw strings have no `\`-newline continuation (Python does).
    // Every pattern here is single-line for that reason.
    re::build(&format!(
        "^[±]?\\s*(?P<{p}_lo>{NUM})\\s*(?P<{p}_lu>{u})?(?:\\s*(?P<{p}_sep>{RANGE_SEP})\\s*(?P<{p}_hi>{NUM})\\s*(?P<{p}_hu>{u})?)?(?:\\s*(?P<{p}_unit>{u}))?"
    ))
}

/// Reversed bounds (`2.5 V≤VOUT`, `1.0V≦VOUT＜1.2V`) — scanned BEFORE symbol
/// anchors so the bound value is not stolen as a plain anchor (L9).
fn rev_bound() -> Regex {
    re::build(&format!(
        "(?P<rb_lo>{NUM})\\s*(?P<rb_lu>mV|kV|V)\\s*[≤≥≦<＜]\\s*(?P<rb_sym>V\\s*_?\\s*(?:OUT|O|IN|DD|CC|DS|GS))(?:\\s*[≤≥≦<＜]\\s*(?P<rb_hi>{NUM})\\s*(?P<rb_hu>mV|kV|V))?"
    ))
}

const REV_KEY: &[(&str, &str)] = &[
    ("VOUT", "vout_v"),
    ("VO", "vout_v"),
    ("VIN", "vin_v"),
    ("VDD", "vdd_v"),
    ("VCC", "vcc_v"),
    ("VDS", "vds_v"),
    ("VGS", "vgs_v"),
];

fn v_mult_by_token(tok: &str) -> Option<f64> {
    match tok {
        "mv" => Some(1e-3),
        "v" => Some(1.0),
        "kv" => Some(1e3),
        _ => None,
    }
}

/// Bare number + `°C` is unambiguous in kind and types as `t_c` flagged `bare` (L3).
fn bare_temp() -> Regex {
    re::build(&format!(
        "(?<![\\w.])(?P<bt_lo>{NUM})\\s*(?:~|–|—|to)?\\s*(?:(?P<bt_mid>{NUM})\\s*)?(?P<bt_u>{TEMP_UNIT})(?![\\w/])"
    ))
}

/// Cached per-symbol compiled regexes, built once (L4 still applies: the
/// *scan order* is the table order; only compilation is cached).
fn compiled_symbols() -> &'static [(Regex, ConditionKey, Kind)] {
    use std::sync::OnceLock;
    static CELL: OnceLock<Vec<(Regex, ConditionKey, Kind)>> = OnceLock::new();
    CELL.get_or_init(|| {
        SYMBOLS
            .iter()
            .map(|(pattern, key, kind)| (re::build(&format!("(?i){pattern}")), *key, *kind))
            .collect()
    })
}

/// Cached per-kind value regexes, indexed by [`Kind::index`].
///
/// The table is exactly [`Kind::ALL`] long and built once, so a missing kind is
/// a compile error via [`Kind::index`], never a runtime `expect`.
fn compiled_value_re(kind: Kind) -> &'static Regex {
    use std::sync::OnceLock;
    static V: OnceLock<Vec<Regex>> = OnceLock::new();
    let table = V.get_or_init(|| Kind::ALL.iter().map(|k| value_regex(*k)).collect());
    table
        .get(kind.index())
        .unwrap_or_else(|| unreachable!("Kind::index is exhaustive over Kind::ALL"))
}

/// The frozen symbol table, **in order** (L4: first parsable occurrence wins).
///
/// Ported verbatim from `_SYMBOLS`. Do not reorder — the order is semantic.
const SYMBOLS: &[(&str, ConditionKey, Kind)] = &[
    (
        r"di\s*F\s*/\s*dt",
        ConditionKey::DifdtAPerS,
        Kind::AmperePerSecond,
    ),
    (
        r"V\s*_?\s*GS\s*\(\s*on\s*\)",
        ConditionKey::VgsOnV,
        Kind::Volt,
    ),
    (
        r"V\s*_?\s*DS\s*,\s*peak",
        ConditionKey::VdsPeakV,
        Kind::Volt,
    ),
    (
        r"T\s*_?\s*vj\s*\(\s*start\s*\)",
        ConditionKey::TvjStartC,
        Kind::Celsius,
    ),
    (
        r"R\s*_?\s*(?:th|θ)?\s*_?\s*JA",
        ConditionKey::RthjaCPerW,
        Kind::CelsiusPerWatt,
    ),
    (r"R\s*_?\s*G\s*,\s*ext", ConditionKey::RgExtOhm, Kind::Ohm),
    (r"L\s*σ", ConditionKey::LsigmaH, Kind::Henry),
    (r"\bSTBY", ConditionKey::StbyV, Kind::Volt),
    (r"\bI\s*_?\s*SINK", ConditionKey::IsinkA, Kind::Ampere),
    (r"\bI\s*_?\s*SD", ConditionKey::IsdA, Kind::Ampere),
    (r"\bI\s*_?\s*REG", ConditionKey::IregA, Kind::Ampere),
    (r"\bI\s*_?\s*AS", ConditionKey::IAsA, Kind::Ampere),
    (r"\bI\s*_?\s*OUT", ConditionKey::IoutA, Kind::Ampere),
    (r"\bI\s*_?\s*DS", ConditionKey::IdA, Kind::Ampere),
    (r"\bI\s*_?\s*O\b", ConditionKey::IoutA, Kind::Ampere),
    (r"\bI\s*_?\s*F\b", ConditionKey::IfA, Kind::Ampere),
    (r"\bI\s*_?\s*S\b", ConditionKey::IsA, Kind::Ampere),
    (r"\bI\s*_?\s*D", ConditionKey::IdA, Kind::Ampere),
    (r"\bV\s*_?\s*OUT", ConditionKey::VoutV, Kind::Volt),
    (r"\bV\s*_?\s*IN\b", ConditionKey::VinV, Kind::Volt),
    (r"\bV\s*_?\s*CC", ConditionKey::VccV, Kind::Volt),
    (r"\bV\s*_?\s*DD", ConditionKey::VddV, Kind::Volt),
    (r"\bV\s*_?\s*EN\b", ConditionKey::VenV, Kind::Volt),
    (r"\bV\s*_?\s*SD", ConditionKey::VsdV, Kind::Volt),
    (r"\bV\s*_?\s*GS", ConditionKey::VgsV, Kind::Volt),
    (r"\bV\s*_?\s*DS", ConditionKey::VdsV, Kind::Volt),
    (r"\bVR", ConditionKey::VrV, Kind::Volt),
    (r"\bVO\b", ConditionKey::VoutV, Kind::Volt),
    (r"\bT\s*_?\s*STG", ConditionKey::TstgC, Kind::Celsius),
    (r"\bT\s*_?\s*vj", ConditionKey::TvjC, Kind::Celsius),
    (r"\bT\s*_?\s*A\b", ConditionKey::TaC, Kind::Celsius),
    (r"\bT\s*_?\s*C\b", ConditionKey::TcC, Kind::Celsius),
    (r"\bT\s*_?\s*J\b", ConditionKey::TjC, Kind::Celsius),
    (r"\bf\s*sw", ConditionKey::FHz, Kind::Hertz),
    (r"\bf\s*osc", ConditionKey::FHz, Kind::Hertz),
    (r"\bf\s*rr", ConditionKey::FrrHz, Kind::Hertz),
    (r"\b[fƒ]", ConditionKey::FHz, Kind::Hertz),
    (r"\bR\s*_?\s*GS", ConditionKey::RgsOhm, Kind::Ohm),
    (r"\bR\s*_?\s*G\b", ConditionKey::RgOhm, Kind::Ohm),
    (r"\bL\b", ConditionKey::LH, Kind::Henry),
    (r"\bR\b", ConditionKey::ROhm, Kind::Ohm),
];

// ---------------------------------------------------------------------------
// parse
// ---------------------------------------------------------------------------

fn clean(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn overlaps(start: usize, end: usize, spans: &[(usize, usize)]) -> bool {
    spans.iter().any(|&(b, e)| start < e && end > b)
}

fn f64_of(text: &str) -> f64 {
    text.parse::<f64>().unwrap_or(0.0)
}

/// Parse a printed test condition into typed anchors, fail-closed.
///
/// # Panics
///
/// Never. Every path returns a [`ParseOutcome`].
///
/// # Why this function is long
///
/// It is a **faithful sequential port** of the reference's single scan. The
/// value of the port is that a reviewer can diff it line-by-line against
/// `condition_model.py::parse_condition` and see the four phases in the same
/// order the gold fixture exercises them. Splitting it into helpers would
/// obscure that mapping, which is the only reason this crate can claim parity.
#[allow(clippy::too_many_lines)]
#[must_use]
pub fn parse_condition(text: &str) -> ParseOutcome {
    let s = clean(text);
    if s.is_empty() || dash_only().is_match(&s).unwrap_or(false) {
        return ParseOutcome::Rejected {
            reject: Rejection::Dash,
        };
    }

    let mut spans: Vec<(usize, usize)> = Vec::new();
    let mut keys: BTreeMap<String, CondValue> = BTreeMap::new();
    let mut approx: BTreeSet<String> = BTreeSet::new();
    let mut sym: BTreeSet<String> = BTreeSet::new();
    let mut bare: BTreeSet<String> = BTreeSet::new();
    let mut comparators: Vec<(usize, String)> = Vec::new();
    let mut mode: Option<Mode> = None;
    let mut unitless_hit = false;

    // L7: mode words
    if let Ok(Some(m)) = mode_re().find(&s) {
        mode = match m.as_str().to_lowercase().as_str() {
            "static" => Some(Mode::Static),
            "ac" => Some(Mode::Ac),
            "dynamic" => Some(Mode::Dynamic),
            _ => None,
        };
        spans.push((m.start(), m.end()));
    }

    // L9: reversed bounds first
    let rb = rev_bound();
    if let Ok(iter) = rb.find_iter(&s).collect::<Result<Vec<_>, _>>() {
        for m in iter {
            let g = m.as_str();
            let Ok(Some(caps)) = rb.captures(g) else {
                continue;
            };
            let m_start = m.start();
            let m_end = m.end();
            let sym_norm: String = caps
                .name("rb_sym")
                .map(|x| {
                    x.as_str()
                        .chars()
                        .filter(|c| !c.is_whitespace() && *c != '_')
                        .collect::<String>()
                        .to_uppercase()
                })
                .unwrap_or_default();
            let Some(key) = REV_KEY
                .iter()
                .find(|(k, _)| *k == sym_norm)
                .map(|(_, v)| *v)
            else {
                continue;
            };
            if keys.contains_key(key) || overlaps(m_start, m_end, &spans) {
                continue;
            }
            let lo_tok = caps.name("rb_lu").map(|x| x.as_str().to_lowercase());
            let Some(lo_mult) = lo_tok.as_deref().and_then(v_mult_by_token) else {
                continue;
            };
            let lo = f64_of(caps.name("rb_lo").map_or("0", |m| m.as_str())) * lo_mult;
            let value = match caps.name("rb_hi") {
                Some(hi) => {
                    let hi_tok = caps.name("rb_hu").map(|x| x.as_str().to_lowercase());
                    let hi_mult = hi_tok
                        .as_deref()
                        .and_then(v_mult_by_token)
                        .unwrap_or(lo_mult);
                    CondValue::Range(lo, f64_of(hi.as_str()) * hi_mult)
                }
                None => CondValue::Scalar(lo),
            };
            comparators.push((m_start, clean(g)));
            keys.insert(key.to_string(), value);
            spans.push((m_start, m_end));
        }
    }

    // L4: symbol-anchored scans, first parsable occurrence wins
    for (anchor, key, kind) in compiled_symbols() {
        let key_str = key.as_str();
        if keys.contains_key(key_str) {
            continue;
        }
        let sep = sep_re();
        let value_re = compiled_value_re(*kind);
        let p = kind.prefix();

        let Ok(matches) = anchor.find_iter(s.as_str()).collect::<Result<Vec<_>, _>>() else {
            continue;
        };
        for m in matches {
            let tail = &s[m.end()..];
            let Ok(Some(sm)) = sep.find(tail) else {
                continue;
            };
            let after_sep = &tail[sm.end()..];
            let Ok(Some(vcaps)) = value_re.captures(after_sep) else {
                continue;
            };
            // Python's `Pattern.match(tail, pos)` reports offsets within `tail`.
            // `vcaps` is relative to `after_sep`, so add `sm.end()` back.
            let match_end = vcaps.get(0).map_or(0, |hit| hit.end());
            let match_start = vcaps.get(0).map_or(0, |hit| hit.start());
            let v_end = m.end() + sm.end() + match_end;

            let grab = |suffix: &str| -> Option<String> {
                vcaps
                    .name(&format!("{p}_{suffix}"))
                    .map(|hit| hit.as_str().to_string())
            };
            // `_lo` is the NUMBER; `_lu`/`_unit` is the UNIT. `_scale` wants
            // the unit (the reference calls `_scale(kind, lu or unit)`).
            let lo_num = grab("lo");
            let lo_unit = grab("lu").or_else(|| grab("unit"));
            let hi_unit = grab("hu");
            let hi_num = grab("hi");

            let Some(lo_text) = lo_num else {
                continue;
            };

            // L1: no unit anywhere -> units_absent, never a Quantity.
            let any_unit = grab("lu").is_some() || grab("unit").is_some() || grab("hu").is_some();
            if !any_unit {
                let rest = &after_sep[match_start..];
                if re::build(&format!(r"^\s*{NUM}\s*(?:[,;]|$)"))
                    .is_match(rest)
                    .unwrap_or(false)
                {
                    unitless_hit = true;
                }
                continue;
            }

            // `_scale(kind, lu or unit)`, falling back to the hi-unit's scale for
            // a hi-unit-only range (`0 to 10 V`, `0/18 V`).
            let mut lo_mult = match scale(*kind, lo_unit.as_deref()) {
                Scale::Known(f) => Some(f),
                Scale::Unknown => None,
            };
            if lo_mult.is_none() {
                lo_mult = hi_unit
                    .as_deref()
                    .and_then(|h| match scale(*kind, Some(h)) {
                        Scale::Known(f) => Some(f),
                        Scale::Unknown => None,
                    });
            }
            let Some(lo_mult) = lo_mult else {
                continue;
            };
            let hi_mult = hi_unit
                .or_else(|| grab("unit"))
                .and_then(|t| match scale(*kind, Some(&t)) {
                    Scale::Known(f) => Some(f),
                    Scale::Unknown => None,
                })
                .unwrap_or(lo_mult);

            if overlaps(m.start(), v_end, &spans) {
                continue;
            }

            let lo = f64_of(lo_text.as_str());
            let value = match hi_num {
                Some(hi) => CondValue::Range(lo * lo_mult, f64_of(hi.as_str()) * hi_mult),
                None => CondValue::Scalar(lo * lo_mult),
            };

            let seg = &s[m.start()..v_end];
            let sep_txt = sm.as_str().trim();
            if sep_txt.starts_with(|c| CMP_CHARS.contains(c)) {
                comparators.push((m.start(), seg.to_string()));
            }
            if sep_txt.contains('≈') || sep_txt.contains('⋍') {
                approx.insert(key_str.to_string());
            }
            if seg.contains('±') {
                sym.insert(key_str.to_string());
            }
            keys.insert(key_str.to_string(), value);
            spans.push((m.start(), v_end));
            break;
        }
    }

    // L3: bare temperatures
    let bt = bare_temp();
    if let Ok(iter) = bt.find_iter(&s).collect::<Result<Vec<_>, _>>() {
        for m in iter {
            if keys.contains_key("t_c") || overlaps(m.start(), m.end(), &spans) {
                continue;
            }
            let (m_start, m_end) = (m.start(), m.end());
            let Ok(Some(caps)) = bt.captures(m.as_str()) else {
                continue;
            };
            let lo = f64_of(caps.name("bt_lo").map_or("0", |m| m.as_str()));
            let value = match caps.name("bt_mid") {
                Some(mid) => CondValue::Range(lo, f64_of(mid.as_str())),
                None => CondValue::Scalar(lo),
            };
            keys.insert("t_c".to_string(), value);
            bare.insert("t_c".to_string());
            spans.push((m_start, m_end));
        }
    }

    // `s` is non-empty here (the dash/empty path returned above), so this
    // cannot fail. If it somehow did, fail closed rather than panic.
    let Ok(verbatim) = Verbatim::new(text) else {
        return ParseOutcome::Rejected {
            reject: Rejection::NoAnchors,
        };
    };

    if !keys.is_empty() || mode.is_some() {
        // Gold emits comparators in **document order**, which is not the table
        // iteration order the scan uses. Sort on the match position.
        comparators.sort_by_key(|(pos, _)| *pos);
        let comparators: Vec<String> = comparators.into_iter().map(|(_, s)| s).collect();
        return ParseOutcome::Typed(TypedCondition {
            keys,
            mode,
            approx,
            sym,
            bare,
            comparators,
            residue: residue(&s, &spans),
            verbatim: verbatim.clone(),
        });
    }

    if unitless_hit {
        return ParseOutcome::Rejected {
            reject: Rejection::UnitsAbsent,
        };
    }
    if ambig_single().is_match(&s).unwrap_or(false) {
        return ParseOutcome::Rejected {
            reject: Rejection::AmbiguousSymbols,
        };
    }
    if punct_only().is_match(&s).unwrap_or(false) {
        return ParseOutcome::Rejected {
            reject: Rejection::NoAnchors,
        };
    }
    ParseOutcome::Rejected {
        reject: Rejection::NoAnchors,
    }
}

/// Verbatim text no anchor claimed.
///
/// Ported from the reference `_residue`: chunks are split on `[,;]+`, each part
/// whitespace-cleaned, and anything punctuation-only is dropped. That is why
/// `"VGS = 0 V, VDS = 25 V"` yields **no** residue — the separators are not
/// evidence.
fn residue(s: &str, spans: &[(usize, usize)]) -> Vec<String> {
    let mut dead = vec![false; s.len()];
    for &(b, e) in spans {
        for d in dead.iter_mut().take(e.min(s.len())).skip(b) {
            *d = true;
        }
    }
    let mut chunks: Vec<String> = Vec::new();
    let mut cur = String::new();
    for (i, ch) in s.char_indices() {
        if dead.get(i).copied().unwrap_or(false) {
            if !cur.is_empty() {
                chunks.push(std::mem::take(&mut cur));
            }
        } else {
            cur.push(ch);
        }
    }
    if !cur.is_empty() {
        chunks.push(cur);
    }

    let mut out = Vec::new();
    for chunk in chunks {
        for part in chunk.split([',', ';']) {
            let p = clean(part);
            if !p.is_empty() && !punct_only().is_match(&p).unwrap_or(false) {
                out.push(p);
            }
        }
    }
    out
}

#[cfg(test)]
// Tests assert freely: they index, unwrap, and match on single variants to
// prove the laws. Those restrictions are kept in library code and are noise
// here.
#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    clippy::wildcard_enum_match_arm,
    clippy::match_wildcard_for_single_variants,
    clippy::match_same_arms,
    clippy::too_many_lines
)]
mod tests {
    use super::*;

    #[test]
    fn dash_rejects() {
        for s in ["", "-", "–", "  -- "] {
            match parse_condition(s) {
                ParseOutcome::Rejected { reject } => assert_eq!(reject, Rejection::Dash),
                other => panic!("{s:?} should reject as dash, got {other:?}"),
            }
        }
    }

    #[test]
    fn l1_never_invents_units() {
        match parse_condition("VGS=0 ,VDS=80") {
            ParseOutcome::Rejected { reject } => assert_eq!(reject, Rejection::UnitsAbsent),
            other => panic!("L1 violated: {other:?}"),
        }
    }

    #[test]
    fn l2_single_letter_is_ambiguous() {
        match parse_condition("V =12 V") {
            ParseOutcome::Rejected { reject } => assert_eq!(reject, Rejection::AmbiguousSymbols),
            other => panic!("L2 violated: {other:?}"),
        }
    }

    #[test]
    fn typed_condition_carries_verbatim() {
        let text = "VGS = 0 V, VDS = 25 V, f = 1 MHz";
        match parse_condition(text) {
            ParseOutcome::Typed(t) => {
                assert_eq!(t.verbatim.as_str(), text);
                assert_eq!(t.keys.get("vgs_v"), Some(&CondValue::Scalar(0.0)));
                assert_eq!(t.keys.get("vds_v"), Some(&CondValue::Scalar(25.0)));
                assert_eq!(t.keys.get("f_hz"), Some(&CondValue::Scalar(1e6)));
            }
            other => panic!("should type, got {other:?}"),
        }
    }

    #[test]
    fn empty_fields_omit_from_serialisation() {
        match parse_condition("static") {
            ParseOutcome::Typed(t) => {
                let json = serde_json::to_value(&t).expect("json");
                let obj = json.as_object().expect("object");
                assert!(obj.contains_key("mode"));
                assert!(!obj.contains_key("approx"));
                assert!(!obj.contains_key("sym"));
                assert!(!obj.contains_key("bare"));
                assert!(!obj.contains_key("comparators"));
                assert!(!obj.contains_key("residue"));
            }
            other => panic!("should type, got {other:?}"),
        }
    }

    #[test]
    fn l7_mode_words_type() {
        match parse_condition("static") {
            ParseOutcome::Typed(t) => assert_eq!(t.mode, Some(Mode::Static)),
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn l3_bare_temp_is_unambiguous_in_kind() {
        match parse_condition("25°C") {
            ParseOutcome::Typed(t) => {
                assert!(t.bare.contains("t_c"));
                assert_eq!(t.keys.get("t_c"), Some(&CondValue::Scalar(25.0)));
            }
            other => panic!("{other:?}"),
        }
    }
}
