//! The axis registry — discovered-then-curated technical knives.
//!
//! Port of `harness/discovery/evaluator.py`'s `AXES` / `axis_for_symbol`
//! (MIGRATION: `dw-knives | evaluator::AXES`, gate *5 axes 1:1*). The axes
//! themselves live in [`dw_decide`] as typed [`Axis`] values; this registry
//! is their **measurement contract**: which datasheet symbols answer them,
//! how a claim is compared, and which rating classes may speak at all.
//!
//! Same kind of object as the [`crate::phrase`] bindings — discovered,
//! curated, gold gated — which is why it lives beside them.
//!
//! ## The normalization is part of the contract
//!
//! The reference matches symbols through `_norm_symbol`: strip whitespace and
//! underscores, uppercase, strip parentheses and commas. `RDS(ON)MAX`,
//! `rds(on)max`, and `RDS_ON_MAX` are one symbol. A tokenizer that keeps the
//! parentheses fails the gate on the first real corpus row.

use std::collections::BTreeMap;

use dw_decide::{Axis, Op};

use crate::rating::RatingClass;

/// One registry entry: what a datasheet prints, and what it means.
#[derive(Debug, Clone, PartialEq)]
pub struct AxisSpec {
    /// The typed axis this answers.
    pub axis: Axis,
    /// Datasheet symbols that answer it, as printed in the reference.
    pub symbols: &'static [&'static str],
    /// How a claim on this axis is compared (`min_rating` / `max_rating` /
    /// `covers`).
    pub op: Op,
    /// Rating classes whose claims may speak on this axis. `None` = any class
    /// except transient/surge (which never satisfy continuous axes).
    pub accepts_classes: Option<&'static [&'static str]>,
    /// Near-miss band, percent of the requirement.
    pub default_near_miss_pct: f64,
    /// `true`: Min-column claims are valid rating evidence (guaranteed-minimum
    /// specs like `VDSS` print in the Min column). `false`: span quantities
    /// (`TJ`/`TSTG` `-55 175`) — a Min-column claim is the range floor, not a
    /// rating magnitude (the `wrong_bound` guard's input).
    pub min_column_evidence: bool,
}

impl AxisSpec {
    /// The reference's `evaluate_claim` class gate. `false` means the claim
    /// cannot even run on this axis.
    ///
    /// An axis with explicit `accepts_classes` filters to exactly them; an
    /// axis without them accepts any class **except** transient and surge —
    /// pulsed events never satisfy continuous axes. Note `absolute_maximum`
    /// is accepted where the axis itself IS a survival quantity (`VDSS`);
    /// C9.2's operating law is the typed layer's guard at judgement time,
    /// not this projection.
    #[must_use]
    pub fn accepts(&self, class: RatingClass) -> bool {
        match self.accepts_classes {
            Some(list) => list.contains(&class.as_str()),
            None => !matches!(class, RatingClass::Transient | RatingClass::Surge),
        }
    }
}

/// The five axes, in the reference's declaration order.
///
/// Order is observable: question ranking breaks ties on it
/// (`dw_decide::next_questions`).
pub const AXES: &[AxisSpec] = &[
    AxisSpec {
        axis: Axis::VdsRatingV,
        symbols: &["VDSS", "VBRDSS", "VDSSMAX"],
        op: Op::MinRating,
        accepts_classes: None,
        default_near_miss_pct: 10.0,
        min_column_evidence: true,
    },
    AxisSpec {
        axis: Axis::IdContinuousA,
        symbols: &["IDDC", "ID"],
        op: Op::MinRating,
        accepts_classes: None,
        default_near_miss_pct: 10.0,
        min_column_evidence: true,
    },
    AxisSpec {
        axis: Axis::RdsOnOhm,
        symbols: &["RDSON", "RDS(ON)MAX"],
        op: Op::MaxRating,
        accepts_classes: Some(&["rated_limit", "typical", "unknown"]),
        default_near_miss_pct: 10.0,
        min_column_evidence: true,
    },
    AxisSpec {
        axis: Axis::TjMaxC,
        symbols: &["TJ", "TJMAX"],
        op: Op::MinRating,
        accepts_classes: None,
        default_near_miss_pct: 10.0,
        min_column_evidence: false,
    },
    AxisSpec {
        axis: Axis::TstgRangeC,
        symbols: &["TSTG"],
        op: Op::Covers,
        accepts_classes: None,
        default_near_miss_pct: 10.0,
        min_column_evidence: false,
    },
    // ---- additive, discovered-then-curated (Sam mandate 2026-10-12: every
    // extractable attribute is a knife; niche coverage still carves cohorts).
    // Evidence: attribute_mine (claims counts are the ranking); ops follow the
    // physics (leakage/threshold/drop lower-is-better; carry/survive
    // higher-is-better). Class filters accept the measured qualifier spread on
    // characteristics rows; ratings rows pass all classes like Vds/Id.
    AxisSpec {
        axis: Axis::VgsRatingV,
        symbols: &["VGS", "VGSS", "VGSMAX"],
        op: Op::MinRating,
        accepts_classes: None,
        default_near_miss_pct: 10.0,
        min_column_evidence: true,
    },
    AxisSpec {
        axis: Axis::VgsThV,
        symbols: &["VGSTH", "VGSTHMAX"],
        op: Op::MaxRating,
        accepts_classes: Some(&["rated_limit", "typical", "unknown"]),
        default_near_miss_pct: 10.0,
        min_column_evidence: false,
    },
    AxisSpec {
        axis: Axis::VsdV,
        symbols: &["VSD", "VF"],
        op: Op::MaxRating,
        accepts_classes: Some(&["rated_limit", "typical", "unknown"]),
        default_near_miss_pct: 10.0,
        min_column_evidence: false,
    },
    AxisSpec {
        axis: Axis::IdssA,
        symbols: &["IDSS", "IDSSMAX"],
        op: Op::MaxRating,
        accepts_classes: Some(&["rated_limit", "typical", "unknown"]),
        default_near_miss_pct: 10.0,
        min_column_evidence: false,
    },
    AxisSpec {
        axis: Axis::IgssA,
        symbols: &["IGSS", "IGSSMAX"],
        op: Op::MaxRating,
        accepts_classes: Some(&["rated_limit", "typical", "unknown"]),
        default_near_miss_pct: 10.0,
        min_column_evidence: false,
    },
    AxisSpec {
        axis: Axis::IsA,
        symbols: &["IS", "ISMAX"],
        op: Op::MinRating,
        accepts_classes: None,
        default_near_miss_pct: 10.0,
        min_column_evidence: true,
    },
    AxisSpec {
        axis: Axis::RgOhm,
        symbols: &["RG"],
        op: Op::MaxRating,
        accepts_classes: Some(&["rated_limit", "typical", "unknown"]),
        default_near_miss_pct: 10.0,
        min_column_evidence: false,
    },
    AxisSpec {
        axis: Axis::RthjaCperw,
        symbols: &["RTHJA", "RTHJ-A", "RΘJA"],
        op: Op::MaxRating,
        accepts_classes: Some(&["rated_limit", "typical", "unknown"]),
        default_near_miss_pct: 10.0,
        min_column_evidence: false,
    },
    AxisSpec {
        axis: Axis::PinCount,
        symbols: &["PINS", "PINCOUNT", "NOOFPINS", "PINCOUNTMAX", "PINQUANTITY"],
        op: Op::MinRating,
        accepts_classes: None,
        default_near_miss_pct: 10.0,
        min_column_evidence: true,
    },
    AxisSpec {
        axis: Axis::SleepCurrentA,
        // Conservative symbol set: the EC-table spellings of sleep-mode
        // supply current. Mode-name evidence ("Deep Sleep", "Stop 1") is
        // bridged by dw-search's mode-row adapter, not symbol matching.
        symbols: &["ISLEEP", "IDD_SLEEP", "IDD-SLEEP", "ILPM", "ISTBY2"],
        op: Op::AtMost,
        // Sleep rows print as spec limits (max col = guaranteed ceiling) or
        // recommended operating values; typ is discovery-only (C9.5) and is
        // handled by the rating class, not by exclusion here.
        accepts_classes: Some(&["rated_limit", "recommended", "unknown"]),
        default_near_miss_pct: 10.0,
        // A Min-column sleep current is a guaranteed floor — valid
        // evidence, and the only thing that can PROVE violation.
        min_column_evidence: true,
    },
];

/// `_norm_symbol` from the reference: strip whitespace and underscores,
/// uppercase, strip parentheses and commas. `None`/empty normalizes to
/// nothing and never matches.
#[must_use]
pub fn norm_symbol(symbol: &str) -> String {
    let mut out = String::with_capacity(symbol.len());
    for c in symbol.chars() {
        if c.is_whitespace() || c == '_' {
            continue;
        }
        if matches!(c, '(' | ')' | ',') {
            continue;
        }
        for u in c.to_uppercase() {
            out.push(u);
        }
    }
    out
}

/// Which axis a datasheet symbol answers.
///
/// Lookup order (E1, G1): the **alias table first** ([`crate::alias`] —
/// spelling folds `norm_symbol` cannot see: `Θ`/`TH`, `Δ`/`D`, word
/// labels), then `norm_symbol` matching against [`AXES`]. `None` when the
/// symbol is not in the registry — an unmapped symbol is an unknown axis
/// (D5's honesty), never a silent best-guess.
#[must_use]
pub fn axis_for_symbol(symbol: &str) -> Option<Axis> {
    let n = crate::alias::canonical_key(symbol);
    AXES.iter()
        .find(|spec| spec.symbols.iter().any(|s| norm_symbol(s) == n))
        .map(|spec| spec.axis)
}

/// The registry as a symbol → spec map, keyed by normalized symbol.
#[must_use]
pub fn by_symbol() -> BTreeMap<String, &'static AxisSpec> {
    let mut out = BTreeMap::new();
    for spec in AXES {
        for s in spec.symbols {
            out.insert(norm_symbol(s), spec);
        }
    }
    out
}
