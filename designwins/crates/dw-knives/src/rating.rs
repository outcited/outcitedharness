//! Rating-class projection — port of `harness/electronics/rating_class.py`
//! (`rating_class` only; `compare_with_margin` is ratified into
//! [`dw_decide::judge`] as C9.3 and does not live here).
//!
//! Spec section 43: absolute maximum, recommended operating, typical,
//! transient, and surge are **different quantities** and must never be
//! conflated. A 40 V absolute maximum never satisfies a 36 V operating
//! requirement (C9.2).
//!
//! The vocabulary patterns are ported **verbatim**, quirks included: the
//! reference's `vds\s*\(\s*transient\s*\)` alternative is followed by `\b`,
//! which never matches after a closing paren — so `"VDS(transient)"` projects
//! to `Unknown` in the reference and here. The gold records it; "fixing" it
//! would diverge from the reference it replaces (`DECISIONS.md` D8).

#![allow(
    clippy::expect_used,
    reason = "the regexes are literal patterns in this file; a bad literal is a build-time mistake, not a runtime condition"
)]

use std::sync::OnceLock;

use regex::Regex;

/// The closed class set, as the reference names it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum RatingClass {
    /// Stress rating — never satisfies an operating atom (C9.2).
    AbsoluteMaximum,
    /// Recommended-operating / operating-range rows.
    Recommended,
    /// EC/summary/thermal `min`|`max`|`rated` spec limits.
    RatedLimit,
    /// Typ-column performance values.
    Typical,
    /// Pulsed / avalanche ratings (IDM, IAS, VPULSE, Zth, …).
    Transient,
    /// Surge ratings (ISM, IASM, IFSM, ITSM, VSURGE).
    Surge,
    /// Fail-closed: role unknown, diagram fragments, leaks.
    Unknown,
}

impl RatingClass {
    /// The stable name the reference emits.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::AbsoluteMaximum => "absolute_maximum",
            Self::Recommended => "recommended",
            Self::RatedLimit => "rated_limit",
            Self::Typical => "typical",
            Self::Transient => "transient",
            Self::Surge => "surge",
            Self::Unknown => "unknown",
        }
    }
}

fn surge_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        Regex::new(r"(?i)\b(?:is[am]|iasm|ifsm|itsm|vsurge|vsurge\d?)\b")
            .expect("surge pattern compiles")
    })
}

fn pulse_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        // Verbatim from the reference — including the `\b` after the
        // parenthesised `vds\s*\(\s*transient\s*\)` alternative, which never
        // matches. See the module docs.
        Regex::new(
            r"(?i)\b(?:idm|idp|idpulse|id,\s*pulse|\be\s*\*?\s*\d?\s*as\b|ias|vpulse|vds\s*\(\s*transient\s*\)|zth)\b",
        )
        .expect("pulse pattern compiles")
    })
}

fn continuous_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)continuous").expect("continuous pattern compiles"))
}

fn operating_re() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"(?i)operating").expect("operating pattern compiles"))
}

/// Project one printed row to the closed rating-class set — the reference's
/// `rating_class(symbol, parameter, table_kind, quantity_qualifier)`.
#[must_use]
pub fn rating_class(
    symbol: Option<&str>,
    parameter: Option<&str>,
    table_kind: Option<&str>,
    quantity_qualifier: Option<&str>,
) -> RatingClass {
    let sym = symbol.unwrap_or_default();
    let text = format!("{sym} {}", parameter.unwrap_or_default());
    // Pulse/surge vocabulary fires on the symbol text; a parameter that
    // prints "continuous" vetoes it (glued composite symbols).
    if !continuous_re().is_match(&text) {
        if surge_re().is_match(&text) {
            return RatingClass::Surge;
        }
        if pulse_re().is_match(&text) {
            return RatingClass::Transient;
        }
    }
    match table_kind.unwrap_or_default() {
        "absolute_maximum" => return RatingClass::AbsoluteMaximum,
        "recommended" => return RatingClass::Recommended,
        _ => {}
    }
    match quantity_qualifier.unwrap_or_default() {
        "absolute_maximum" => RatingClass::AbsoluteMaximum,
        "typical" => RatingClass::Typical,
        "minimum" | "maximum" => RatingClass::RatedLimit,
        "rated" => {
            if operating_re().is_match(&text) {
                RatingClass::Recommended
            } else {
                RatingClass::RatedLimit
            }
        }
        _ => RatingClass::Unknown,
    }
}
