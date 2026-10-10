//! Thermal metrics — `PRD-NORMALIZE` §5.7.
//!
//! Port of `harness/electronics/thermal_metrics.py`.
//!
//! **Fill-only, printed evidence only.** A metric is emitted when the symbol
//! (or its spelled form) appears with a value and a `°C/W` or `K/W` unit **from
//! the same printed row**. Condition text (JEDEC reference, layer count, still
//! air) stays verbatim; package scope is kept when the row prints it.
//!
//! **A dimensionless `RθJA` mention is not a value.** Rows without a thermal
//! unit never emit.

use fancy_regex::Regex;

use crate::re;

/// Schema id. Matches the reference.
pub const THERMAL_SCHEMA: &str = "harness.electronics-thermal-metrics.v1";

/// Which thermal quantity a printed row names.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ThermalKind {
    /// Junction-to-ambient thermal resistance.
    RthetaJa,
    /// Junction-to-case thermal resistance.
    RthetaJc,
    /// Junction-to-board thermal resistance.
    RthetaJb,
    /// Junction-to-top characterisation parameter.
    PsiJt,
    /// Junction-to-board characterisation parameter.
    PsiJb,
    /// Transient thermal impedance, junction-to-ambient.
    ZthJa,
}

impl ThermalKind {
    /// The stable string the reference emits.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::RthetaJa => "RthetaJA",
            Self::RthetaJc => "RthetaJC",
            Self::RthetaJb => "RthetaJB",
            Self::PsiJt => "PsiJT",
            Self::PsiJb => "PsiJB",
            Self::ZthJa => "ZthJA",
        }
    }
}

/// One printed thermal value.
#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct ThermalRow {
    /// Which quantity.
    pub kind: ThermalKind,
    /// The magnitude in `°C/W` (or `K/W`).
    pub value: f64,
    /// The unit exactly as printed.
    pub unit: String,
    /// The label the row printed, kept verbatim.
    pub label: String,
    /// Condition text, kept verbatim. Never typed here.
    pub condition: Option<String>,
    /// Package scope, when the row printed one.
    pub package: Option<String>,
}

fn thermal_symbol() -> Regex {
    re::build(
        r"R\s*θ\s*JA|R\s*θ\s*JC|R\s*θ\s*JB|Rth\s*JA|Rth\s*JC|Rth\s*JB|Θ\s*JA|Θ\s*JC|θ\s*JA|θ\s*JC|theta\s*[- ]?\s*JA|theta\s*[- ]?\s*JC|Ψ\s*JT|Ψ\s*JB|psi\s*[- ]?\s*JT|psi\s*[- ]?\s*JB|Z\s*θ\s*JA|Zth\s*JA|Z\s*th\s*JA",
    )
}

fn thermal_words() -> Regex {
    re::build(
        r"thermal\s+resistance.{0,32}(?:ambient|case|board|top)|junction[- ]to[- ](?:ambient|case|board|top)|characterization\s+parameter.{0,24}(?:top|board)|transient\s+thermal\s+impedance",
    )
}

fn thermal_unit() -> Regex {
    re::build(r"(?:°\s*C|C|K)\s*/\s*(?:W|watt)")
}

/// Classify a label into a [`ThermalKind`], or `None`.
///
/// Spelled forms are accepted alongside symbols (`junction-to-ambient`,
/// `thermal resistance … ambient`), because rows print both.
#[must_use]
pub fn kind_of(text: &str) -> Option<ThermalKind> {
    let flat: String = {
        let lower = text.to_lowercase();
        lower
            .chars()
            .filter(|c| !c.is_whitespace())
            .collect::<String>()
            .replace(['θ', 'Θ'], "theta")
            .replace('ψ', "psi")
    };
    if flat.contains("zth") || flat.contains("ztheta") {
        return Some(ThermalKind::ZthJa);
    }
    if flat.contains("psijb") {
        return Some(ThermalKind::PsiJb);
    }
    if flat.contains("psijt") {
        return Some(ThermalKind::PsiJt);
    }
    if flat.contains("rthetaja") || flat.contains("rthetaa") || flat.contains("rthja") {
        return Some(ThermalKind::RthetaJa);
    }
    if flat.contains("rthetajc") || flat.contains("rthjc") {
        return Some(ThermalKind::RthetaJc);
    }
    if flat.contains("rthetajb") || flat.contains("rthjb") {
        return Some(ThermalKind::RthetaJb);
    }

    let words = text
        .to_lowercase()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join("-");
    if words.contains("junction-to-ambient")
        || words.contains("thermal-resistance") && words.contains("ambient")
    {
        return Some(ThermalKind::RthetaJa);
    }
    if words.contains("junction-to-case")
        || (words.contains("thermal-resistance") && words.contains("case"))
    {
        return Some(ThermalKind::RthetaJc);
    }
    if words.contains("junction-to-board") {
        return Some(ThermalKind::PsiJb);
    }
    if words.contains("junction-to-top") {
        return Some(ThermalKind::PsiJt);
    }
    None
}

/// A printed fact row, as the readers emit it.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct ThermalFact {
    /// The printed symbol, if any.
    pub symbol: String,
    /// The printed parameter / description, if any.
    pub parameter: String,
    /// The printed value, if the row printed one.
    pub value: Option<f64>,
    /// The printed unit, if the row printed one.
    pub unit: Option<String>,
    /// Condition text, kept verbatim.
    pub condition: Option<String>,
    /// Package scope, kept when printed.
    pub package: Option<String>,
}

/// Emit one row per printed thermal value; mentions without values stay out.
///
/// **The unit must come from the same printed row as the value.** A
/// dimensionless `RθJA` mention is not a value and never emits.
#[must_use]
pub fn thermal_rows(facts: &[ThermalFact]) -> Vec<ThermalRow> {
    let mut rows = Vec::new();
    for fact in facts {
        let label = format!("{} {}", fact.symbol, fact.parameter);
        if thermal_symbol().find(&label).ok().flatten().is_none()
            && thermal_words().find(&label).ok().flatten().is_none()
        {
            continue;
        }
        // Fill-only: the unit and the value must both be printed on this row.
        let (Some(value), Some(unit)) = (fact.value, fact.unit.as_deref()) else {
            continue;
        };
        if thermal_unit().find(unit).ok().flatten().is_none() {
            continue;
        }
        let Some(kind) = kind_of(&label) else {
            continue;
        };
        rows.push(ThermalRow {
            kind,
            value,
            unit: unit.to_string(),
            label: label.trim().to_string(),
            condition: fact.condition.clone(),
            package: fact.package.clone(),
        });
    }
    rows
}

#[cfg(test)]
#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    clippy::match_wildcard_for_single_variants,
    clippy::wildcard_enum_match_arm
)]
mod tests {
    use super::*;

    #[test]
    fn kind_classifies_symbols_and_spelled_forms() {
        assert_eq!(kind_of("RθJA"), Some(ThermalKind::RthetaJa));
        assert_eq!(kind_of("Rth JC"), Some(ThermalKind::RthetaJc));
        assert_eq!(kind_of("ΨJT"), Some(ThermalKind::PsiJt));
        assert_eq!(kind_of("ZthJA"), Some(ThermalKind::ZthJa));
        assert_eq!(kind_of("junction-to-ambient"), Some(ThermalKind::RthetaJa));
        assert_eq!(kind_of("gate resistance"), None);
    }

    #[test]
    fn dimensionless_mention_emits_nothing() {
        // PRD §5.7: "A dimensionless RθJA mention is not a value."
        let facts = [ThermalFact {
            symbol: "RθJA".into(),
            parameter: "Thermal resistance".into(),
            value: None,
            unit: None,
            condition: None,
            package: None,
        }];
        assert!(thermal_rows(&facts).is_empty());
    }

    #[test]
    fn printed_value_with_thermal_unit_emits() {
        let facts = [ThermalFact {
            symbol: "RθJA".into(),
            parameter: "Thermal resistance, junction-ambient".into(),
            value: Some(62.5),
            unit: Some("°C/W".into()),
            condition: Some("JEDEC still air".into()),
            package: None,
        }];
        let rows = thermal_rows(&facts);
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].kind, ThermalKind::RthetaJa);
        assert!((rows[0].value - 62.5).abs() < 1e-9);
        assert_eq!(rows[0].condition.as_deref(), Some("JEDEC still air"));
    }

    #[test]
    fn non_thermal_unit_never_emits() {
        let facts = [ThermalFact {
            symbol: "RθJA".into(),
            parameter: "Thermal resistance".into(),
            value: Some(62.5),
            unit: Some("Ω".into()),
            condition: None,
            package: None,
        }];
        assert!(thermal_rows(&facts).is_empty());
    }
}
