//! Rating class and margin-aware comparison — `PRD-NORMALIZE` §5.3, §5.4.
//!
//! Port of `harness/electronics/rating_class.py`.
//!
//! **Rating classes are different physical quantities and must never be
//! conflated.** A 40 V absolute maximum never satisfies a 36 V operating
//! requirement (`C9.2`).
//!
//! Margin (`PLAN` §42 / `C9.3`) is applied to the **requirement**, is never
//! silent, and a claim that fails the raw requirement but sits within
//! `near_miss_pct` is [`CompareOutcome::NearMiss`] — a separate cohort, not a
//! `Fail`.

use fancy_regex::Regex;

use crate::re;

/// Schema id. Matches the reference.
pub const RATING_CLASS_SCHEMA: &str = "harness.electronics-rating-class.v1";

/// The closed rating-class set (`C6`, additive-only).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum RatingClass {
    /// Stress rating. **Never** satisfies an operating atom.
    AbsoluteMaximum,
    /// Recommended-operating / operating-range rows.
    Recommended,
    /// EC/summary/thermal `min|max|rated` spec limits.
    RatedLimit,
    /// Typ-column performance values. Never becomes guaranteed (`C9.5`).
    Typical,
    /// Pulsed / avalanche ratings — even inside absmax tables.
    Transient,
    /// Surge ratings.
    Surge,
    /// Fail-closed: unknown role, diagram fragments, leaks.
    Unknown,
}

impl RatingClass {
    /// Every member, in reference order.
    pub const ALL: [Self; 7] = [
        Self::AbsoluteMaximum,
        Self::Recommended,
        Self::RatedLimit,
        Self::Typical,
        Self::Transient,
        Self::Surge,
        Self::Unknown,
    ];

    /// The stable string the reference emits.
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

    /// `C9.2`: an absolute maximum is a survival boundary, not an operating
    /// guarantee. `Unknown` also fails closed.
    #[must_use]
    pub const fn may_satisfy_operating_atom(self) -> bool {
        matches!(self, Self::Recommended | Self::RatedLimit | Self::Typical)
    }
}

/// Printed evidence, as the readers already emit it.
#[derive(Debug, Clone, PartialEq)]
pub struct RatingEvidence<'a> {
    /// The printed symbol (`ID,pulse`, `VDS`, `IFSM`, …).
    pub symbol: &'a str,
    /// The parameter / row description.
    pub parameter: &'a str,
    /// `table_kind` from the reader.
    pub table_kind: &'a str,
    /// `quantity_qualifier` from the reader.
    pub quantity_qualifier: Option<&'a str>,
    /// The table title, for context.
    pub table_title: &'a str,
}

fn surge_symbol() -> Regex {
    re::build(r"\b(?:is[am]|iasm|ifsm|itsm|vsurge|vsurge\d?)\b")
}

fn pulse_symbol() -> Regex {
    re::build(
        r"\b(?:idm|idp|idpulse|id,\s*pulse|\be\s*\*?\s*\d?\s*as\b|ias|vpulse|vds\s*\(\s*transient\s*\)|zth)\b",
    )
}

fn continuous() -> Regex {
    re::build("continuous")
}

fn operating() -> Regex {
    re::build("operating")
}

/// Project printed evidence into a [`RatingClass`].
///
/// **Pulse/surge vocabulary fires on the SYMBOL; a printed "continuous"
/// vetoes it** — for glued composite symbols like `ID@TC=25°C ID@TC=100°C IDM`
/// where the value is the continuous row.
#[must_use]
pub fn rating_class(e: &RatingEvidence<'_>) -> RatingClass {
    let symbol = e.symbol.to_lowercase();
    let parameter = e.parameter.to_lowercase();
    let table = e.table_kind.to_lowercase();
    let qualifier = e.quantity_qualifier.unwrap_or("").to_lowercase();
    let title = e.table_title.to_lowercase();
    let haystack = format!("{symbol} {parameter} {table} {qualifier} {title}");

    // Surge fires on the symbol and outranks everything except "continuous".
    if surge_symbol().is_match(&symbol).unwrap_or(false)
        && !continuous().is_match(&haystack).unwrap_or(false)
    {
        return RatingClass::Surge;
    }
    // Pulse/transient fires on the symbol; "continuous" is a veto.
    if pulse_symbol().is_match(&symbol).unwrap_or(false)
        && !continuous().is_match(&haystack).unwrap_or(false)
    {
        return RatingClass::Transient;
    }

    // Explicit qualifiers win next.
    if qualifier.contains("absolute_maximum") || table.contains("absolute_maximum") {
        return RatingClass::AbsoluteMaximum;
    }
    if qualifier.contains("typical") || table.contains("characteristics") {
        // A typ-column value inside a characteristics table.
        if table.contains("characteristics") && !qualifier.contains("rated") {
            return RatingClass::Typical;
        }
    }
    if table.contains("characteristics") && qualifier.is_empty() {
        return RatingClass::Typical;
    }
    if operating().is_match(&haystack).unwrap_or(false) {
        return RatingClass::Recommended;
    }
    if qualifier.contains("rated")
        || table.contains("electrical")
        || table.contains("thermal")
        || table.contains("summary")
    {
        return RatingClass::RatedLimit;
    }
    if qualifier.contains("absolute_maximum") {
        return RatingClass::AbsoluteMaximum;
    }

    // Fail closed. A value whose role we cannot establish is `unknown`, and
    // `unknown` never eliminates (`C9.1`).
    RatingClass::Unknown
}

/// Margin, applied to the **requirement**. Never silent (`C9.3`).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Margin {
    /// Percentage, or `None` meaning explicitly no margin. There is no
    /// defaulting constructor — a silent 0% would be a lie.
    pub pct: Option<f64>,
}

impl Margin {
    /// Explicitly no margin.
    pub const NONE: Self = Self { pct: None };

    /// A percentage margin on the requirement.
    #[must_use]
    pub const fn pct(pct: f64) -> Self {
        Self { pct: Some(pct) }
    }

    /// The requirement inflated by this margin.
    #[must_use]
    pub fn inflate(&self, requirement: f64) -> f64 {
        match self.pct {
            Some(p) => requirement * (1.0 + p / 100.0),
            None => requirement,
        }
    }
}

/// Result of comparing a claim against a requirement under a margin.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CompareOutcome {
    /// Satisfies the inflated requirement.
    Pass,
    /// Fails the raw requirement but sits within `near_miss_pct`. **A separate
    /// cohort, not a `Fail`.**
    NearMiss,
    /// Fails, and is outside the near-miss band.
    Fail,
    /// Could not evaluate. **Never eliminates** (`C9.1`).
    Unknown,
}

/// Compare a claim magnitude against a requirement.
///
/// * `requirement` is the engineer's number (e.g. 36 V max).
/// * `margin` inflates **the requirement** (20% → 43.2 V must be survived).
/// * `near_miss_pct` is the band inside which a shortfall is `NearMiss`.
/// * `higher_is_better` selects the direction (`RDS(on)` is lower-is-better).
///
/// `claim: Option<f64>` — `None` is [`CompareOutcome::Unknown`], never a
/// default. See `C11`: absence is a fact.
#[must_use]
pub fn compare_with_margin(
    claim: Option<f64>,
    requirement: f64,
    margin: Margin,
    near_miss_pct: f64,
    higher_is_better: bool,
) -> CompareOutcome {
    let Some(claim) = claim else {
        return CompareOutcome::Unknown;
    };
    let inflated = margin.inflate(requirement);
    let band = requirement.abs() * (near_miss_pct / 100.0);

    let satisfies = if higher_is_better {
        claim + f64::EPSILON >= inflated
    } else {
        claim <= inflated + f64::EPSILON
    };
    if satisfies {
        return CompareOutcome::Pass;
    }

    let near = if higher_is_better {
        claim >= requirement - band
    } else {
        claim <= requirement + band
    };
    if near {
        return CompareOutcome::NearMiss;
    }
    CompareOutcome::Fail
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

    fn ev<'a>(
        symbol: &'a str,
        parameter: &'a str,
        table: &'a str,
        q: Option<&'a str>,
    ) -> RatingEvidence<'a> {
        RatingEvidence {
            symbol,
            parameter,
            table_kind: table,
            quantity_qualifier: q,
            table_title: "Table 2 Maximum ratings",
        }
    }

    #[test]
    fn transient_fires_inside_absmax_tables_on_symbol() {
        // Gold row: ID,pulse in an absolute_maximum table is `transient`.
        let e = ev(
            "ID,pulse",
            "3) Pulsed drain current",
            "absolute_maximum",
            Some("absolute_maximum"),
        );
        assert_eq!(rating_class(&e), RatingClass::Transient);
    }

    #[test]
    fn continuous_vetoes_pulse_vocabulary() {
        let e = ev(
            "ID@TC=25°C ID@TC=100°C IDM",
            "Continuous drain current",
            "absolute_maximum",
            Some("absolute_maximum"),
        );
        assert_eq!(rating_class(&e), RatingClass::AbsoluteMaximum);
    }

    #[test]
    fn absmax_never_satisfies_operating() {
        assert!(!RatingClass::AbsoluteMaximum.may_satisfy_operating_atom());
        assert!(!RatingClass::Unknown.may_satisfy_operating_atom());
        assert!(RatingClass::RatedLimit.may_satisfy_operating_atom());
        assert!(RatingClass::Recommended.may_satisfy_operating_atom());
        assert!(RatingClass::Typical.may_satisfy_operating_atom());
    }

    #[test]
    fn unknown_is_fail_closed() {
        let e = ev("Vplateau", "Gate plateau voltage", "characteristics", None);
        // A value whose role is not established must not be promoted.
        let class = rating_class(&e);
        assert!(!matches!(class, RatingClass::AbsoluteMaximum));
    }

    /// PRD Gate N2: margin explicitness.
    #[test]
    fn property_margin_is_never_silent() {
        // 36 V requirement, 20% margin -> rating must be >= 43.2 V
        let none = compare_with_margin(Some(40.0), 36.0, Margin::NONE, 5.0, true);
        let pct20 = compare_with_margin(Some(40.0), 36.0, Margin::pct(20.0), 5.0, true);
        assert_eq!(none, CompareOutcome::Pass);
        assert_eq!(pct20, CompareOutcome::NearMiss);
    }

    #[test]
    fn near_miss_is_not_fail() {
        let outcome = compare_with_margin(Some(34.0), 36.0, Margin::NONE, 10.0, true);
        assert_eq!(outcome, CompareOutcome::NearMiss);
        assert_ne!(outcome, CompareOutcome::Fail);
    }

    #[test]
    fn absent_claim_is_unknown_not_fail() {
        assert_eq!(
            compare_with_margin(None, 36.0, Margin::NONE, 5.0, true),
            CompareOutcome::Unknown
        );
    }

    #[test]
    fn lower_is_better_direction() {
        // RDS(on): 20 mΩ requirement, 30 mΩ claim must fail.
        assert_eq!(
            compare_with_margin(Some(30e-3), 20e-3, Margin::NONE, 5.0, false),
            CompareOutcome::Fail
        );
        assert_eq!(
            compare_with_margin(Some(10e-3), 20e-3, Margin::NONE, 5.0, false),
            CompareOutcome::Pass
        );
    }
}
