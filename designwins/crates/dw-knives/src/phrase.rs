//! The phrase layer — colloquial intent to technical knives.
//!
//! **This is the difference between a text matcher and the product** (D4).
//!
//! Engineers do not type `has_wireless_ble == true`. They type
//! *"outdoor weather thermostat bluetooth"*. That phrase decomposes into
//! several technical knives, and each one either narrows the cohort or is
//! honestly reported as unevaluable:
//!
//! ```text
//! "outdoor weather thermostat bluetooth"
//!   |-- outdoor    -> temp_range + qualification (moisture: not in index)
//!   |-- weather    -> n_adc / adc_resolution_bits
//!   |-- thermostat -> occasion: home_building  +  drive_a_motor
//!   |-- bluetooth  -> has_wireless_ble
//!   v
//! Vec<Atom> + residue  ->  dw-decide::evaluate  ->  COHORT + eliminations
//! ```
//!
//! ## Three laws
//!
//! 1. **A binding's [`Stamp`] is its license to eliminate.**
//!    [`Stamp::ProvenFit`] eliminates. [`Stamp::PositiveOnly`] may only
//!    include. [`Stamp::Blocked`] may do neither. This is the Infineon ruling
//!    of 2026-08-14 — *positive-fit only, they never eliminate* — promoted from
//!    a one-off ruling into a type.
//! 2. **The stamp is measured, not declared.** It comes from the classified
//!    fraction of the decision universe against [`ACTIVATION_FLOOR`]. Below the
//!    floor the binding is [`Stamp::Blocked`] with the reason.
//! 3. **`Unknown` never eliminates** (`C9.1`). An unclassified substrate
//!    contributes no elimination, ever.
//!
//! ## The four drawers (ratified 2026-08-14)
//!
//! These are distinct axes and must never be conflated:
//!
//! | drawer | cardinality | grain |
//! |---|---|---|
//! | `applications[]` | 0..N overlapping | **occasion** — a bench-able end product |
//! | `catalog_subcategory` | exactly 1 | **partition** — vendor catalog bucket |
//! | `target_markets_or_industries` | 0..N | **sector** |
//! | `qualification_or_vertical_signals` | 0..N | **qualification tier** |
//!
//! A string naming a bench-able end product goes in `applications[]`. A string
//! naming a sector, catalog bucket, or qualification tier goes in the other
//! three. Never `applications[]`. **Unknown stays Unknown** — do not derive
//! `target_markets` from `subcategory`.
//!
//! This crate calls no models and is `forbid(unsafe_code)`.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use dw_decide::Constraint;
use regex::Regex;

/// The minimum fraction of the decision universe that must be classified
/// before a substrate may be trusted at all.
///
/// Measured, not chosen: `holes.json` `activation_floor`. A substrate below
/// this is [`Stamp::Blocked`] regardless of how confident the phrase looks.
pub const ACTIVATION_FLOOR: f64 = 0.5;

// ---------------------------------------------------------------------------
// stamps
// ---------------------------------------------------------------------------

/// A binding's license to eliminate.
///
/// The type is the law: `PositiveOnly` has no method that returns "eliminate",
/// and `Blocked` carries the reason it cannot be trusted.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Stamp {
    /// Substrate classified at or above [`ACTIVATION_FLOOR`]. **May eliminate.**
    ProvenFit,
    /// Measured positive, but coverage is too spotty to rule anything out.
    /// **May include. Never eliminates.**
    PositiveOnly,
    /// Substrate below the floor, or two substrates disagree. May do neither.
    /// The reason is required — an unexplained block is a silent unknown.
    Blocked(String),
}

impl Stamp {
    /// Whether this binding may remove a candidate from a cohort.
    ///
    /// `PositiveOnly` returns `false`. That is the whole point of the type: a
    /// phrase that merely correlates cannot exclude anyone.
    #[must_use]
    pub const fn may_eliminate(&self) -> bool {
        matches!(self, Self::ProvenFit)
    }

    /// Whether this binding may add a candidate to the cohort.
    #[must_use]
    pub const fn may_include(&self) -> bool {
        matches!(self, Self::ProvenFit | Self::PositiveOnly)
    }

    /// Derive the stamp from a measured classified fraction.
    ///
    /// `reason` is required whenever the result is [`Stamp::Blocked`], so a
    /// block can never be silent.
    #[must_use]
    pub fn from_coverage(classified_fraction: f64, reason: impl Into<String>) -> Self {
        if classified_fraction >= ACTIVATION_FLOOR {
            Self::ProvenFit
        } else {
            Self::Blocked(format!(
                "classified fraction {:.3} is below the activation floor {:.3}: {}",
                classified_fraction,
                ACTIVATION_FLOOR,
                reason.into()
            ))
        }
    }
}

// ---------------------------------------------------------------------------
// drawers
// ---------------------------------------------------------------------------

/// Which drawer a phrase writes to (the four-drawers ruling of 2026-08-14).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Drawer {
    /// 0..N overlapping occasions — a bench-able end product.
    Applications,
    /// Exactly 1, mutually exclusive — a vendor catalog bucket.
    CatalogSubcategory,
    /// 0..N sectors.
    TargetMarkets,
    /// 0..N qualification tiers (AEC-Q100, -40/+125, functional safety).
    Qualification,
}

impl Drawer {
    /// The ruling in one line: only `Applications` is an occasion.
    #[must_use]
    pub const fn is_occasion(self) -> bool {
        matches!(self, Self::Applications)
    }
}

// ---------------------------------------------------------------------------
// bindings
// ---------------------------------------------------------------------------

/// A technical knife a phrase proposes. Maps 1:1 onto [`dw_decide::Atom`].
///
/// [`Constraint::Category`] and [`Constraint::Scalar`] are distinct here for
/// the same reason they are distinct in `dw-decide` (`PRD-PHRASE` §8.1): a
/// phrase like *"an ARM Cortex-M4 part"* binds a **category**, and category
/// membership is containment, not magnitude.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AtomTemplate {
    /// Which knife.
    pub knife: dw_decide::KnifeId,
    /// What it demands.
    pub constraint: dw_decide::Constraint,
    /// Whether the phrase makes this a hard requirement. Only
    /// [`Stamp::ProvenFit`] bindings may set this true.
    pub hard: bool,
    /// Which drawer this writes to.
    pub drawer: Drawer,
}

/// Parametric value capture: how a binding takes its requirement value
/// from the engineer's sentence instead of a fixed template.
///
/// A binding with a capture fires ONLY when both the phrase and the value
/// pattern match. A concept without its number ("an MCU that sleeps…")
/// does not fire and falls to residue — which D5 turns into the question
/// "what threshold?", the honest outcome.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ValueCapture {
    /// Regex over the sentence (case-insensitive). Capture group 1 is the
    /// numeral.
    pub pattern: String,
    /// Multiply the captured numeral to the axis's canonical unit
    /// (e.g. `2 µA` -> `2e-6` A).
    pub scale: f64,
}

/// One colloquial phrase and what it means technically.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ColloquialBinding {
    /// Stable id.
    pub id: String,
    /// The phrase as the human writes it.
    pub phrase: String,
    /// What it binds to.
    pub knives: Vec<AtomTemplate>,
    /// How confident the binding is, 0..1.
    pub confidence: f64,
    /// Where the binding was learned. Required — an unsourced binding is an
    /// invention.
    pub evidence: Vec<String>,
    /// The license to eliminate.
    pub stamp: Stamp,
    /// Optional sentence-value capture (see [`ValueCapture`]). `None` on
    /// every fixed-value binding.
    #[serde(default)]
    pub value_capture: Option<ValueCapture>,
}

impl ColloquialBinding {
    /// Build a binding. `evidence` must be non-empty.
    ///
    /// # Errors
    ///
    /// Returns [`dw_types::LawError::MissingProvenance`] if `evidence` is
    /// empty: an unsourced binding is an invention.
    pub fn new(
        id: impl Into<String>,
        phrase: impl Into<String>,
        knives: Vec<AtomTemplate>,
        confidence: f64,
        evidence: Vec<String>,
        stamp: Stamp,
    ) -> Result<Self, dw_types::LawError> {
        if evidence.is_empty() {
            return Err(dw_types::LawError::MissingProvenance("binding evidence"));
        }
        let confidence = confidence.clamp(0.0, 1.0);
        let mut knives = knives;
        // Law 1: only a ProvenFit binding may carry a hard atom. Anything else
        // is forced soft at construction, so the wrong shape cannot be built.
        if !stamp.may_eliminate() {
            for k in &mut knives {
                k.hard = false;
            }
        }
        Ok(Self {
            id: id.into(),
            phrase: phrase.into(),
            knives,
            confidence,
            evidence,
            stamp,
            value_capture: None,
        })
    }

    /// Attach a sentence-value capture to this binding (parametric
    /// requirement). Consumes and returns self for builder-style use.
    #[must_use]
    pub fn with_value_capture(mut self, capture: ValueCapture) -> Self {
        self.value_capture = Some(capture);
        self
    }

    /// The atoms this phrase contributes to a [`dw_decide::Build`].
    ///
    /// A [`Stamp::Blocked`] binding contributes **nothing**. A
    /// [`Stamp::PositiveOnly`] binding contributes its atoms with `hard`
    /// already forced false, so it can only ever widen.
    #[must_use]
    pub fn contribute(&self) -> Vec<dw_decide::Atom> {
        if matches!(self.stamp, Stamp::Blocked(_)) {
            return Vec::new();
        }
        self.knives
            .iter()
            .map(|k| dw_decide::Atom {
                knife: k.knife.clone(),
                constraint: k.constraint.clone(),
                hard: k.hard && self.stamp.may_eliminate(),
                required_conditions: BTreeMap::new(),
            })
            .collect()
    }
}

// ---------------------------------------------------------------------------
// the registry
// ---------------------------------------------------------------------------

/// The discovered-then-curated phrase registry.
///
/// Discovery produces candidate bindings; curation stamps them. A binding with
/// no stamp that clears [`ACTIVATION_FLOOR`] cannot reach a cohort.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct PhraseRegistry {
    bindings: Vec<ColloquialBinding>,
    /// Substrates we know about and how classified they are. Feeds
    /// [`Stamp::from_coverage`].
    coverage: BTreeMap<String, f64>,
}

/// What a phrase produced.
#[derive(Debug, Clone, PartialEq)]
pub struct Bound {
    /// Bindings that fired.
    pub bindings: Vec<ColloquialBinding>,
    /// Phrases in the input that bound to nothing at all.
    pub unmapped: Vec<String>,
    /// Atoms contributed by the bindings that fired.
    pub atoms: Vec<dw_decide::Atom>,
}

impl PhraseRegistry {
    /// An empty registry.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Record a measured substrate coverage fraction.
    pub fn observe_coverage(&mut self, substrate: impl Into<String>, fraction: f64) {
        self.coverage
            .insert(substrate.into(), fraction.clamp(0.0, 1.0));
    }

    /// The measured fraction for a substrate, or `None` if unmeasured.
    #[must_use]
    pub fn coverage_of(&self, substrate: &str) -> Option<f64> {
        self.coverage.get(substrate).copied()
    }

    /// Add a discovered binding.
    pub fn insert(&mut self, binding: ColloquialBinding) {
        self.bindings.push(binding);
    }

    /// Every binding, in insertion order.
    #[must_use]
    pub fn bindings(&self) -> &[ColloquialBinding] {
        &self.bindings
    }

    /// Bind a free-text phrase to technical knives.
    ///
    /// Matching is on normalised substring over the phrase, so `"outdoor
    /// weather thermostat bluetooth"` fires every binding whose phrase it
    /// contains. Anything that matches nothing lands in [`Bound::unmapped`],
    /// which is what [`dw_decide::MissingAxis`] is for (D5).
    #[must_use]
    pub fn bind(&self, text: &str) -> Bound {
        let normalised = text.to_lowercase();
        let mut bindings = Vec::new();
        let mut matched_spans: Vec<(usize, usize)> = Vec::new();

        for binding in &self.bindings {
            let needle = binding.phrase.to_lowercase();
            if needle.is_empty() {
                continue;
            }
            if let Some(pos) = normalised.find(&needle) {
                // Parametric bindings fire only when the sentence ALSO
                // carries the requirement value. A concept without its
                // number contributes nothing here and the words stay in
                // residue — D5 turns that into the threshold question.
                if let Some(capture) = &binding.value_capture {
                    let Ok(re) = Regex::new(&format!(r"(?i){}", capture.pattern)) else {
                        continue;
                    };
                    let Some(caps) = re.captures(&normalised) else {
                        continue;
                    };
                    let Some(raw) = caps.get(1) else {
                        continue;
                    };
                    let Ok(value) = raw.as_str().trim().parse::<f64>() else {
                        continue;
                    };
                    let scaled = value * capture.scale;
                    let mut instantiated = binding.clone();
                    for template in &mut instantiated.knives {
                        if let Constraint::Scalar { value, .. } = &mut template.constraint {
                            *value = scaled;
                        }
                    }
                    bindings.push(instantiated);
                    // Consume the WHOLE matched expression (e.g. "sleeps
                    // under 2 µa"), not just the concept word — residue
                    // must not carry fragments of a bound requirement
                    // (review 2026-10-20 P1).
                    let whole = caps.get(0).map(|m| m.as_str()).unwrap_or(&needle);
                    if let Some(wpos) = normalised.find(whole) {
                        matched_spans.push((wpos, wpos + whole.len()));
                    } else {
                        matched_spans.push((pos, pos + needle.len()));
                    }
                } else {
                    bindings.push(binding.clone());
                    matched_spans.push((pos, pos + needle.len()));
                }
            }
        }

        // Residue: the parts of the input no binding claimed. D5 turns these
        // into MissingAxis + Question rather than silently dropping them.
        let mut unmapped = Vec::new();
        let mut start = 0usize;
        let mut ordered = matched_spans;
        ordered.sort_by_key(|&(start, _)| start);
        for (s, e) in ordered {
            if s > start {
                let chunk = normalised[start..s].trim();
                if !chunk.is_empty() {
                    unmapped.push(chunk.to_string());
                }
            }
            start = start.max(e);
        }
        if start < normalised.len() {
            let chunk = normalised[start..].trim();
            if !chunk.is_empty() {
                unmapped.push(chunk.to_string());
            }
        }

        let atoms = bindings
            .iter()
            .flat_map(ColloquialBinding::contribute)
            .collect();
        Bound {
            bindings,
            unmapped,
            atoms,
        }
    }
}

#[cfg(test)]
#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    clippy::match_wildcard_for_single_variants,
    clippy::wildcard_enum_match_arm,
    clippy::too_many_lines
)]
mod tests {
    use super::*;
    use dw_decide::{Axis, Constraint, KnifeId, Op};

    fn knife(axis: Axis, hard: bool, drawer: Drawer) -> AtomTemplate {
        AtomTemplate {
            knife: KnifeId::Axis(axis),
            constraint: Constraint::Scalar {
                op: Op::MinRating,
                value: 1.0,
                margin_pct: None,
            },
            hard,
            drawer,
        }
    }

    fn binding(
        id: &str,
        phrase: &str,
        knives: Vec<AtomTemplate>,
        stamp: Stamp,
    ) -> ColloquialBinding {
        ColloquialBinding::new(id, phrase, knives, 0.9, vec!["holes.json".into()], stamp)
            .expect("sourced")
    }

    fn registry() -> PhraseRegistry {
        let mut r = PhraseRegistry::new();
        // N-pin colloquial (2026-10-24): package/pin-count traffic (D4 table
        // row "package" 2.1%). Drawer: CatalogSubcategory — parametric catalog
        // filter, classifying into the ratified set, not reopening it.
        // Stamp::Blocked: soft atom until measured traffic evidence lands (D6).
        r.insert(binding(
            "n-pin",
            r"\d{1,3}[\s-]?(?:pin|lead|ball|pad)s?",
            vec![knife(Axis::PinCount, true, Drawer::CatalogSubcategory)],
            Stamp::Blocked("2026-10-24 structural era: 11,707 parts with verified pin tables; measured-traffic stamp pending".into()),
        ));
        r.insert(binding(
            "bluetooth",
            "bluetooth",
            vec![knife(Axis::HasWirelessBle, true, Drawer::Applications)],
            Stamp::Blocked("nordic 1/31 true".into()),
        ));
        r.insert(binding(
            "car",
            "talks to a car",
            vec![knife(Axis::HasCan, true, Drawer::Applications)],
            Stamp::ProvenFit,
        ));
        r.insert(binding(
            "outdoor",
            "outdoor",
            vec![knife(Axis::TempRangeC, true, Drawer::Qualification)],
            Stamp::PositiveOnly,
        ));
        r
    }

    /// D4 law 1: a `PositiveOnly` binding may include but never eliminate.
    #[test]
    fn positive_only_never_eliminates() {
        let s = Stamp::PositiveOnly;
        assert!(s.may_include());
        assert!(!s.may_eliminate());
    }

    /// D4 law 1, enforced at construction: a non-`ProvenFit` binding cannot
    /// carry a hard atom.
    #[test]
    fn non_proven_fit_atoms_are_forced_soft() {
        let b = binding(
            "x",
            "x",
            vec![knife(Axis::HasCan, true, Drawer::Applications)],
            Stamp::PositiveOnly,
        );
        assert!(b.knives.iter().all(|k| !k.hard));
        for atom in b.contribute() {
            assert!(!atom.hard);
        }
    }

    /// A `Blocked` binding contributes nothing at all.
    #[test]
    fn blocked_contributes_nothing() {
        let r = registry();
        let bound = r.bind("bluetooth");
        assert_eq!(bound.bindings.len(), 1);
        assert!(
            bound.atoms.is_empty(),
            "blocked bindings contribute no atoms"
        );
    }

    /// D4 law 2: the stamp is measured against the activation floor.
    #[test]
    fn stamp_is_measured_not_declared() {
        assert_eq!(Stamp::from_coverage(0.6, "ok"), Stamp::ProvenFit);
        match Stamp::from_coverage(0.186, "nordic almost unmarked") {
            Stamp::Blocked(reason) => {
                assert!(reason.contains("below the activation floor"));
                assert!(reason.contains("nordic"));
            }
            other => panic!("0.186 must block, got {other:?}"),
        }
        // Exactly at the floor is ProvenFit.
        assert_eq!(
            Stamp::from_coverage(ACTIVATION_FLOOR, "at floor"),
            Stamp::ProvenFit
        );
    }

    /// D5: an unmapped phrase is a finding, not a silent miss.
    #[test]
    fn unmapped_phrase_is_reported() {
        let r = registry();
        let bound = r.bind("an outdoor weather thermostat bluetooth");
        assert!(
            !bound.unmapped.is_empty(),
            "unmapped residue must surface as MissingAxis"
        );
        assert!(
            bound.unmapped.iter().any(|u| u.contains("weather")),
            "no binding for 'weather', so it must be reported: {:?}",
            bound.unmapped
        );
    }

    /// The compound phrase fires several bindings and keeps their stamps apart.
    #[test]
    fn compound_phrase_fires_several_bindings() {
        let r = registry();
        let bound = r.bind("outdoor weather thermostat bluetooth");
        assert!(bound.bindings.len() >= 2, "outdoor + bluetooth");
        assert!(
            bound.atoms.iter().all(|a| !a.hard),
            "only ProvenFit bindings contribute hard atoms; none of these are"
        );
    }

    /// A `ProvenFit` binding may eliminate.
    #[test]
    fn proven_fit_may_eliminate() {
        let r = registry();
        let bound = r.bind("talks to a car");
        assert_eq!(bound.bindings.len(), 1);
        assert!(bound.bindings[0].stamp.may_eliminate());
        assert!(bound.atoms.iter().any(|a| a.hard));
    }

    /// The four drawers are distinct and only Applications is an occasion.
    #[test]
    fn four_drawers_are_distinct() {
        assert!(Drawer::Applications.is_occasion());
        assert!(!Drawer::CatalogSubcategory.is_occasion());
        assert!(!Drawer::TargetMarkets.is_occasion());
        assert!(!Drawer::Qualification.is_occasion());
    }

    /// A binding is unconstructible without evidence.
    #[test]
    fn binding_requires_evidence() {
        assert!(ColloquialBinding::new("x", "x", vec![], 0.5, vec![], Stamp::ProvenFit).is_err());
    }
}

// ---------------------------------------------------------------------------
// D5 — the unmapped case
// ---------------------------------------------------------------------------

/// Turn the residue of a bind into first-class unknowns and ranked questions.
///
/// **D5:** a colloquial phrase that binds to no knife and no axis emits a
/// [`dw_decide::MissingAxis`] and a [`dw_decide::Question`]. It is never a
/// silent miss and never a bare error. The question asked is the *technical*
/// one that would make the colloquial one evaluable.
///
/// The alternative — falling through to full-text search — would hide that the
/// phrase meant nothing to the cohort. That is silent wrongness, the exact
/// class QA finding F-11 names.
#[must_use]
pub fn unknowns_from_residue(
    bound: &Bound,
    cohort_size: usize,
) -> (Vec<dw_decide::MissingAxis>, Vec<dw_decide::Question>) {
    let mut unknowns = Vec::new();
    let mut questions = Vec::new();

    for chunk in &bound.unmapped {
        unknowns.push(dw_decide::MissingAxis {
            axis: None,
            phrase: Some(chunk.clone()),
            why: format!(
                "no knife or axis binds to {chunk:?}; the cohort cannot be narrowed by it"
            ),
            affected: cohort_size,
        });
        // Ask the technical question that would make the colloquial one
        // evaluable. Until it is answered, the phrase is unevaluable and the
        // cohort is honestly reported as such.
        questions.push(dw_decide::Question {
            axis: dw_decide::KnifeId::Axis(dw_decide::Axis::TempRangeC),
            prompt: format!("What engineering requirement does {chunk:?} express? (unbound phrase)"),
            help: format!(
                "{chunk:?} is not in the axis vocabulary. Answering with a typed \
                 requirement (temperature span, ingress, interface) turns an \
                 unevaluable phrase into a constraint that can narrow the cohort."
            ),
            expected_reduction_pct: 0.0,
            askable: true,
            proposed_value: None,
            parts_with_claims: 0,
            expected_fail_at_median: 0,
        });
    }

    // Blocked bindings are unknowns too: we heard the phrase, we cannot act
    // on it, and the reason is measured.
    for binding in &bound.bindings {
        if let Stamp::Blocked(reason) = &binding.stamp {
            unknowns.push(dw_decide::MissingAxis {
                axis: binding.knives.first().map(|k| k.knife.clone()),
                phrase: Some(binding.phrase.clone()),
                why: reason.clone(),
                affected: cohort_size,
            });
        }
    }

    (unknowns, questions)
}

#[cfg(test)]
#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    clippy::match_wildcard_for_single_variants,
    clippy::wildcard_enum_match_arm
)]
mod d5_tests {
    use super::*;
    use dw_decide::{Axis, Op};
    use dw_decide::{Constraint, KnifeId};

    /// D5: an unmapped phrase becomes a `MissingAxis` and a `Question`, never a
    /// silent miss and never a bare error.
    #[test]
    fn unmapped_phrase_becomes_unknown_and_question() {
        let mut r = PhraseRegistry::new();
        r.insert(
            ColloquialBinding::new(
                "bluetooth",
                "bluetooth",
                vec![AtomTemplate {
                    knife: KnifeId::Axis(Axis::HasWirelessBle),
                    constraint: Constraint::Scalar {
                        op: Op::MinRating,
                        value: 1.0,
                        margin_pct: None,
                    },
                    hard: true,
                    drawer: Drawer::Applications,
                }],
                0.8,
                vec!["holes.json".into()],
                Stamp::Blocked("nordic 1/31".into()),
            )
            .expect("sourced"),
        );

        let bound = r.bind("outdoor weather thermostat bluetooth");
        let (unknowns, questions) = unknowns_from_residue(&bound, 1000);

        // The blocked binding surfaces as an unknown with its measured reason.
        assert!(unknowns
            .iter()
            .any(|u| u.phrase.as_deref() == Some("bluetooth") && u.why.contains("nordic")));
        // The residue is the unbound **span**, kept whole: splitting it into
        // words would report four unknowns for "talks to a car" and lose the
        // fact that it was one phrase the human wrote.
        assert!(unknowns
            .iter()
            .any(|u| u.phrase.as_deref() == Some("outdoor weather thermostat")));
        assert!(!questions.is_empty(), "every unknown gets a question");
        assert!(questions.iter().all(|q| q.askable));
    }
}
