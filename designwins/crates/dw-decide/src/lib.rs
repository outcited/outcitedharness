//! # dw-decide — the funnel
//!
//! **This crate is the product.** Everything else in the workspace is in
//! service of it.
//!
//! ```text
//! WHAT ARE YOU BUILDING?  ->  typed constraints  ->  COHORT
//!    (text · image · BOM · "like an X but…")
//!                          ->  compare @ your point  ->  explain  ->  next question
//! ```
//!
//! The acceptance test (PLAN §0):
//!
//! > A human types one sentence about what they are building. The system
//! > returns a cohort, shows which constraints were derived and confirmed,
//! > names every exclusion with its evidence, lists what it could not
//! > evaluate, and suggests the next question that would narrow it most.
//! > Every number opens to the page it came from.
//!
//! ## Three laws (CONTRACTS.md)
//!
//! 1. **`derived` is never silent.** An inferred constraint is shown and
//!    confirmable. A silent derived constraint is the worst bug in the system.
//! 2. **`cohort` is distinct parts — a type.** Not passage matches, not
//!    document hits. A cohort member is a candidate you could buy.
//! 3. **`unknowns` is a first-class field.** What we could not evaluate is
//!    part of the answer, never an omission.
//!
//! This crate calls no models and is `forbid(unsafe_code)`.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

use std::collections::BTreeMap;

use dw_types::{Admitted, Ineligible, ReleaseId, SourceRef, Verdict as Outcome};

pub use dw_normalize::CondValue;
use serde::{Deserialize, Serialize};

// ---------------------------------------------------------------------------
// intent and context
// ---------------------------------------------------------------------------

/// What the human said they are building. Any modality.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum DesignIntent {
    /// Free text: "an outdoor weather thermostat with bluetooth".
    Text(String),
    /// An image of a block diagram, sketch, or reference design.
    Image {
        /// Content hash of the image.
        sha256: String,
    },
    /// A bill of materials.
    Bom {
        /// The BOM rows.
        rows: Vec<String>,
    },
    /// Existing part numbers used as a starting point ("like an X but…").
    References(Vec<String>),
}

/// The environment the design must survive.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Domain {
    /// Industrial.
    Industrial,
    /// Automotive.
    Automotive,
    /// Consumer.
    Consumer,
    /// Medical.
    Medical,
}

/// Everything the environment demands of the parts.
#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize)]
pub struct DesignContext {
    /// Which vertical.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub domain: Option<Domain>,
    /// Ambient temperature span the design must survive, in °C.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ta_range: Option<(f64, f64)>,
    /// Isolation required.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub isolation: Option<bool>,
    /// Supply rails, in volts.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub rails: Vec<f64>,
    /// Free-text environment notes. Kept verbatim.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub environment: Option<String>,
}

// ---------------------------------------------------------------------------
// requirements and atoms
// ---------------------------------------------------------------------------

/// An axis a requirement can be stated against.
///
/// Discovered-then-curated (`dw-knives`); this crate consumes the vocabulary
/// and does not own it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Axis {
    /// Blocking voltage the switch must survive.
    VdsRatingV,
    /// Continuous current the device must carry.
    IdContinuousA,
    /// On-resistance — conduction loss driver.
    RdsOnOhm,
    /// Maximum junction temperature.
    TjMaxC,
    /// Storage/operating temperature span.
    TstgRangeC,
    /// Ambient temperature span.
    TempRangeC,
    /// Has a CAN interface.
    HasCan,
    /// Has Bluetooth Low Energy.
    HasWirelessBle,
    /// Has USB.
    HasUsb,
    /// Has motor-control peripherals (PWM, gate drive, quadrature decode).
    HasMotorControl,
    /// Flash, in kilobytes.
    FlashKb,
    /// Frequency, in megahertz.
    FreqMhz,
    /// Gate voltage the switch must survive (abs-max VGS).
    VgsRatingV,
    /// Gate threshold voltage — logic-level driveability (lower is better).
    VgsThV,
    /// Source-drain diode forward drop (lower is better).
    VsdV,
    /// Drain leakage current (lower is better).
    IdssA,
    /// Gate leakage current (lower is better).
    IgssA,
    /// Continuous source current the device must carry.
    IsA,
    /// Gate resistance — drive strength (lower is better).
    RgOhm,
    /// Junction-to-ambient thermal resistance (lower is better).
    RthjaCperw,
    /// Sleep-mode supply current (lower is better). An upper-bound
    /// *consumption* axis: a guaranteed maximum proves satisfaction, a
    /// guaranteed minimum proves violation, and neither alone proves the
    /// other direction.
    SleepCurrentA,
}

/// How a **scalar** axis is compared against a claim.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Op {
    /// The claim must be at least this. Higher is better.
    MinRating,
    /// The claim must be at most this. Lower is better.
    MaxRating,
    /// The claim's range must contain this.
    Covers,
    /// Requirement: the part *consumes* at most this. Unlike
    /// [`Op::MaxRating`], a claim above the requirement does NOT prove
    /// violation: a guaranteed maximum (ceiling) above the requirement
    /// merely fails to *satisfy* it — the actual draw may still be under.
    /// Violation is proven only by a guaranteed minimum (floor) above the
    /// requirement. Verdicts are [`Bound`]-aware (D-ruling 2026-10-19).
    AtMost,
}

/// A node in the category graph.
///
/// Categories **contain** one another: `AM13E230x ⊂ AM13x ⊂ AM1x`, and
/// `Cortex-M4F ⊂ Cortex-M ⊂ ARM`. Membership is therefore not equality, and a
/// core family is not a number — see [`CategoryOp`] and `PRD-PHRASE` §8.1.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct CategoryId(String);

impl CategoryId {
    /// Name a category node.
    ///
    /// # Errors
    ///
    /// Returns [`dw_types::LawError::Empty`] if the name is empty.
    pub fn new(name: impl Into<String>) -> Result<Self, dw_types::LawError> {
        let s = name.into();
        if s.is_empty() {
            return Err(dw_types::LawError::Empty {
                field: "category id",
            });
        }
        Ok(Self(s))
    }

    /// The node name.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// How a category is compared. **There is no `MinRating`/`MaxRating` here** —
/// a minimum on a core family is meaningless, and the type refuses to express
/// it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CategoryOp {
    /// Exactly this node.
    Is,
    /// Inside the named subtree. `Cortex-M4F` satisfies `Within(Cortex-M)`;
    /// `Cortex-M0` does not satisfy `Within(Cortex-M4)`.
    Within,
    /// Outside the named subtree.
    Excludes,
}

/// A knife: either a scalar axis or a category-graph node.
///
/// **`Category` is not `Axis`** (`PRD-PHRASE` §8.1). A scalar compares
/// magnitudes; a category compares containment. Fusing them into one enum with
/// one op set is how "minimum ARM Cortex" would end up expressible.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum KnifeId {
    /// A named scalar axis: `FlashKb`, `FreqMhz`, `TempRangeC`, …
    Axis(Axis),
    /// A node in the category graph: core family, product category.
    Category(CategoryId),
}

/// What a constraint demands.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Constraint {
    /// A magnitude, compared with [`Op`].
    Scalar {
        /// How it is compared.
        op: Op,
        /// The required magnitude.
        value: f64,
        /// Margin applied to the **requirement**, never silent (`C9.3`).
        #[serde(skip_serializing_if = "Option::is_none")]
        margin_pct: Option<f64>,
    },
    /// Graph membership, compared with [`CategoryOp`].
    Category {
        /// How it is compared.
        op: CategoryOp,
        /// Which node.
        node: CategoryId,
    },
}

impl Constraint {
    /// The margin on this constraint, if it is a scalar.
    #[must_use]
    pub const fn margin_pct(&self) -> Option<f64> {
        match self {
            Self::Scalar { margin_pct, .. } => *margin_pct,
            Self::Category { .. } => None,
        }
    }
}

/// A typed constraint.
///
/// Every atom carries the **required conditions** it must hold under, because
/// `C9.6` demands that an evidence condition *cover* the operating point.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Atom {
    /// Which knife. `Axis` and `Category` carry different op sets on purpose.
    pub knife: KnifeId,
    /// What it demands.
    pub constraint: Constraint,
    /// Hard atoms eliminate. Soft atoms only rank.
    pub hard: bool,
    /// Conditions the evidence must cover (`C9.6`).
    ///
    /// Values are [`CondValue`], not `f64`, because a required condition can be
    /// a range (`-40 to 125 °C`) and [`dw_normalize::condition_covers`] is the
    /// only thing that knows how to compare one.
    ///
    /// `serde(default)` because the field is skipped when empty on the way
    /// out: a wire shape that cannot be read back by its own deserializer is
    /// a trap (`HANDOFF.md` §6 — "skip needs default on the way in").
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub required_conditions: BTreeMap<String, CondValue>,
}

impl Atom {
    /// A scalar atom.
    #[must_use]
    pub fn scalar(axis: Axis, op: Op, value: f64, hard: bool) -> Self {
        Self {
            knife: KnifeId::Axis(axis),
            constraint: Constraint::Scalar {
                op,
                value,
                margin_pct: None,
            },
            hard,
            required_conditions: BTreeMap::new(),
        }
    }

    /// A category atom. Note there is no `hard`-only constructor for a scalar
    /// category comparison with a margin — the type has no such shape.
    #[must_use]
    pub fn category(op: CategoryOp, node: CategoryId, hard: bool) -> Self {
        Self {
            knife: KnifeId::Category(node.clone()),
            constraint: Constraint::Category { op, node },
            hard,
            required_conditions: BTreeMap::new(),
        }
    }

    /// The axis, when this is a scalar atom.
    #[must_use]
    pub const fn axis(&self) -> Option<Axis> {
        match self.knife {
            KnifeId::Axis(a) => Some(a),
            KnifeId::Category(_) => None,
        }
    }
}

/// A requirement the human stated explicitly.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Requirement {
    /// Stable id within the build.
    pub id: String,
    /// Free text as the human wrote it. Never replaced.
    pub verbatim: String,
    /// The atoms this requirement expands to.
    pub atoms: Vec<Atom>,
}

/// A constraint **inferred** from intent or context.
///
/// **Never silent** (CONTRACTS law 1). A `Derived` is shown to the human and
/// must be confirmable or rejectable before it may eliminate anything. Until
/// confirmed, it is a [`ConfirmState::Pending`] and behaves as `Unknown`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Derived {
    /// Stable id within the build.
    pub id: String,
    /// Why we inferred it. Required — an unexplained derivation is silent.
    pub rationale: String,
    /// The atoms this derivation proposes.
    pub atoms: Vec<Atom>,
    /// Confirmation state. Starts `Pending`.
    pub state: ConfirmState,
}

/// Whether a derived constraint has been confirmed by the human.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ConfirmState {
    /// Proposed, not yet confirmed. **Cannot eliminate.**
    Pending,
    /// The human accepted it. May eliminate like a declared requirement.
    Confirmed,
    /// The human rejected it. Contributes nothing.
    Rejected,
}

// ---------------------------------------------------------------------------
// the cohort
// ---------------------------------------------------------------------------

/// A candidate part. Distinct, orderable, buyable.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct Candidate {
    /// The orderable part number.
    pub part_number: String,
    /// The manufacturer.
    pub vendor: String,
}

/// **Distinct parts**, not passage matches (CONTRACTS law 2).
///
/// A cohort member is a candidate you could buy. The type carries that
/// guarantee so nothing downstream can quietly widen it to documents.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct Cohort {
    /// Distinct candidates, sorted so the output is deterministic.
    pub members: Vec<Candidate>,
}

impl Cohort {
    /// Build a cohort from candidates, de-duplicating and sorting.
    #[must_use]
    pub fn from_candidates(mut members: Vec<Candidate>) -> Self {
        members.sort();
        members.dedup();
        Self { members }
    }

    /// Number of **distinct** candidates.
    #[must_use]
    pub fn len(&self) -> usize {
        self.members.len()
    }

    /// True when nothing survived.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.members.is_empty()
    }
}

// ---------------------------------------------------------------------------
// elimination
// ---------------------------------------------------------------------------

/// Why a candidate is out, with its evidence. Never unmotivated
/// (§16.5: `Ineligible` is unconstructible without reason + evidence).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Exclusion {
    /// The candidate that was excluded.
    pub candidate: Candidate,
    /// The axis it failed.
    pub axis: Axis,
    /// The reason and evidence.
    pub ineligible: Ineligible,
    /// The verdict that produced the exclusion.
    pub outcome: Outcome,
}

/// A verdict on one candidate against one atom, with the evidence that
/// produced it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Judgement {
    /// Which candidate.
    pub candidate: Candidate,
    /// Which atom.
    pub axis: Axis,
    /// The outcome. `Unknown` **never eliminates** (`C9.1`).
    pub outcome: Outcome,
    /// The evidence, when there is any. Every number opens to its page (§0).
    /// Skipped when empty on the wire, defaulted on the way in — see [`Atom`].
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub evidence: Vec<SourceRef>,
}

/// The elimination law, as a predicate so it cannot be reimplemented
/// inconsistently anywhere in the workspace.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Elimination;

impl Elimination {
    /// A candidate is eliminated **only** by a proven `Fail` on a **hard** atom.
    ///
    /// `Unknown` is not a failure to evaluate *against* the candidate — it is a
    /// failure to evaluate *at all*, and it says nothing about the candidate
    /// (`C9.1`). `NearMiss` is a separate cohort, not a `Fail` (`C9.3`).
    #[must_use]
    pub const fn eliminates(outcome: Outcome, hard: bool) -> bool {
        hard && matches!(outcome, Outcome::Fail)
    }
}

// ---------------------------------------------------------------------------
// unknowns
// ---------------------------------------------------------------------------

/// An axis we could not evaluate. **First-class** (CONTRACTS law 3).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MissingAxis {
    /// Which knife, when we know its name. `KnifeId` rather than `Axis`:
    /// an unanswerable *category* question is as real as an unanswerable
    /// scalar one, and "ARM Cortex?" has no axis name to carry.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub axis: Option<KnifeId>,
    /// The colloquial phrase that bound to nothing, when that is the origin.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub phrase: Option<String>,
    /// Why it could not be evaluated. Required.
    pub why: String,
    /// How many candidates it affects. Drives question ranking.
    pub affected: usize,
}

// ---------------------------------------------------------------------------
// questions
// ---------------------------------------------------------------------------

/// The next question to ask, ranked by expected cohort reduction.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Question {
    /// The axis this question would pin.
    pub axis: KnifeId,
    /// The question in the engineer's language.
    pub prompt: String,
    /// Help text explaining why the axis matters.
    pub help: String,
    /// Expected fraction of the current cohort that would be eliminated if the
    /// median became a hard requirement. Drives the ranking.
    pub expected_reduction_pct: f64,
    /// Whether we can ask it now (the axis is classified enough to be useful).
    pub askable: bool,
    /// The median measured claim, proposed as the screen
    /// (`service.next_question`, spec 29-30). `None` when nothing is measured.
    pub proposed_value: Option<f64>,
    /// How many parts carry a claim that answers this axis.
    pub parts_with_claims: usize,
    /// How many current candidates would fail at the proposed screen.
    /// The reference ranks by **this**, not by the percentage.
    pub expected_fail_at_median: usize,
}

// ---------------------------------------------------------------------------
// the build
// ---------------------------------------------------------------------------

/// The top of the funnel. Every crate's job statement is written in terms of
/// serving this.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Build {
    /// Stable id.
    pub id: String,
    /// Which immutable publication this answer came from.
    pub release: ReleaseId,

    /// What the human said.
    pub intent: DesignIntent,
    /// What the environment demands.
    pub context: DesignContext,

    /// Explicitly stated requirements.
    pub declared: Vec<Requirement>,
    /// **Inferred** constraints — shown and confirmable, never silent.
    pub derived: Vec<Derived>,
    /// The atoms actually in force.
    pub constraints: Vec<Atom>,

    /// **Distinct parts**, not passage matches.
    pub cohort: Cohort,
    /// Per-candidate verdicts with evidence.
    pub explain: Vec<Judgement>,
    /// Every exclusion, with reason and evidence.
    pub exclusions: Vec<Exclusion>,
    /// What we could not evaluate.
    pub unknowns: Vec<MissingAxis>,
    /// The next questions, ranked by expected cohort reduction.
    pub next: Vec<Question>,
}

impl Build {
    /// Atoms that are actually in force: every declared atom, plus only those
    /// derived atoms the human has confirmed.
    ///
    /// **A `Pending` derivation contributes nothing.** That is what makes
    /// "derived is never silent" mechanical — the derivation must be acted on
    /// before it can eliminate anyone.
    #[must_use]
    pub fn effective_constraints(&self) -> Vec<Atom> {
        let mut out: Vec<Atom> = Vec::new();
        for r in &self.declared {
            out.extend(r.atoms.iter().cloned());
        }
        for d in &self.derived {
            if d.state == ConfirmState::Confirmed {
                out.extend(d.atoms.iter().cloned());
            }
        }
        out
    }

    /// Candidates eliminated by the current constraints.
    #[must_use]
    pub fn eliminated(&self) -> Vec<Candidate> {
        self.exclusions
            .iter()
            .map(|e| e.candidate.clone())
            .collect()
    }

    /// The engineering summary, in the shape the acceptance test describes.
    #[must_use]
    pub fn summary(&self) -> String {
        use std::fmt::Write as _;
        let mut s = String::new();
        let _ = writeln!(s, "build {} (release {})", self.id, self.release);
        let _ = writeln!(s, "  cohort       {} distinct parts", self.cohort.len());
        let _ = writeln!(
            s,
            "  constraints  {} declared atoms, {} derived ({} confirmed)",
            self.declared.iter().map(|r| r.atoms.len()).sum::<usize>(),
            self.derived.len(),
            self.derived
                .iter()
                .filter(|d| d.state == ConfirmState::Confirmed)
                .count()
        );
        let _ = writeln!(s, "  exclusions   {}", self.exclusions.len());
        let _ = writeln!(s, "  unknowns     {}", self.unknowns.len());
        if let Some(q) = self.next.first() {
            let _ = writeln!(
                s,
                "  next         {} (expect {:.0}% reduction)",
                q.prompt, q.expected_reduction_pct
            );
        }
        s
    }
}

// ---------------------------------------------------------------------------
// re-export the outcome so callers need one import
// ---------------------------------------------------------------------------

pub mod evaluate;
pub mod questions;

pub use dw_types::Verdict;
pub use evaluate::{
    evaluate, judge, judge_category, judge_category_in, AlwaysResolvable, Bound, CandidateFacts,
    CategoryMembership, DimensionId, Evaluation, Evidence, Provenance, SpanEndpoint, SpanEvidence,
    DEFAULT_NEAR_MISS_PCT,
};
pub use questions::next_questions;

/// An admitted claim, for callers building judgements from verified evidence.
pub type Verified<T> = Admitted<T>;

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
    use dw_types::Sha256;

    fn cand(n: &str) -> Candidate {
        Candidate {
            part_number: n.to_string(),
            vendor: "ti".to_string(),
        }
    }

    fn atom(axis: Axis, hard: bool) -> Atom {
        Atom::scalar(axis, Op::MinRating, 36.0, hard)
    }

    fn build() -> Build {
        Build {
            id: "b1".into(),
            release: ReleaseId::new("designwins-2026-W46").unwrap(),
            intent: DesignIntent::Text("an outdoor weather thermostat".into()),
            context: DesignContext::default(),
            declared: vec![Requirement {
                id: "r1".into(),
                verbatim: "must survive 36 V".into(),
                atoms: vec![atom(Axis::VdsRatingV, true)],
            }],
            derived: vec![Derived {
                id: "d1".into(),
                rationale: "outdoor implies an extended ambient range".into(),
                atoms: vec![atom(Axis::TempRangeC, true)],
                state: ConfirmState::Pending,
            }],
            constraints: vec![],
            cohort: Cohort::from_candidates(vec![cand("TPS54202"), cand("LMR33610")]),
            explain: vec![],
            exclusions: vec![],
            unknowns: vec![],
            next: vec![],
        }
    }

    /// C9.1: Unknown never eliminates.
    #[test]
    fn unknown_never_eliminates() {
        assert!(!Elimination::eliminates(Verdict::Unknown, true));
        assert!(!Elimination::eliminates(Verdict::Pass, true));
        assert!(!Elimination::eliminates(Verdict::NearMiss, true));
    }

    /// Only a proven Fail on a hard atom eliminates.
    #[test]
    fn only_hard_fail_eliminates() {
        assert!(Elimination::eliminates(Verdict::Fail, true));
        assert!(!Elimination::eliminates(Verdict::Fail, false));
    }

    /// CONTRACTS law 2: a cohort is distinct parts.
    #[test]
    fn cohort_dedupes_and_sorts() {
        let c = Cohort::from_candidates(vec![cand("B"), cand("A"), cand("B")]);
        assert_eq!(c.len(), 2);
        assert_eq!(c.members[0].part_number, "A");
        assert_eq!(c.members[1].part_number, "B");
    }

    /// CONTRACTS law 1: derived is never silent — a Pending derivation cannot
    /// eliminate anything.
    #[test]
    fn pending_derivation_contributes_nothing() {
        let b = build();
        assert_eq!(b.effective_constraints().len(), 1, "only the declared atom");

        let mut confirmed = build();
        confirmed.derived[0].state = ConfirmState::Confirmed;
        assert_eq!(
            confirmed.effective_constraints().len(),
            2,
            "confirmed derivation joins"
        );

        let mut rejected = build();
        rejected.derived[0].state = ConfirmState::Rejected;
        assert_eq!(rejected.effective_constraints().len(), 1);
    }

    /// §16.5: an exclusion is unconstructible without reason and evidence.
    #[test]
    fn exclusion_carries_reason_and_evidence() {
        let sha = Sha256::new("a".repeat(64)).unwrap();
        let evidence = vec![SourceRef::page(sha, 2)];
        assert!(Ineligible::new("no CAN", evidence.clone()).is_ok());
        assert!(Ineligible::new("", evidence).is_err());
        assert!(Ineligible::new("no CAN", vec![]).is_err());
    }

    /// CONTRACTS law 3: unknowns are first-class, never inferred from absence.
    #[test]
    fn unknowns_are_first_class() {
        let mut b = build();
        assert!(b.unknowns.is_empty());
        b.unknowns.push(MissingAxis {
            axis: None,
            phrase: Some("moisture".into()),
            why: "ingress protection is not in the index".into(),
            affected: b.cohort.len(),
        });
        assert!(b.summary().contains("unknowns     1"));
    }

    #[test]
    fn summary_reports_the_funnel() {
        let s = build().summary();
        assert!(s.contains("cohort       2 distinct parts"));
        assert!(s.contains("derived (0 confirmed)"));
        assert!(s.contains("exclusions   0"));
    }

    /// The wire shape must round-trip: fields skipped when empty still parse
    /// back (`HANDOFF.md` §6 — "skip needs default on the way in").
    ///
    /// Negative control: before `serde(default)` landed on
    /// `required_conditions` and `evidence`, both halves of this failed with
    /// `missing field` on our **own** serialiser output — a shape nothing
    /// could read back.
    #[test]
    fn wire_shapes_round_trip_when_empty_blocks_are_skipped() {
        let atom = atom(Axis::FlashKb, true);
        let wire = serde_json::to_string(&atom).unwrap();
        let back: Atom = serde_json::from_str(&wire).unwrap();
        assert_eq!(atom, back);

        let j = Judgement {
            candidate: cand("X"),
            axis: Axis::FlashKb,
            outcome: Verdict::Unknown,
            evidence: vec![],
        };
        let wire = serde_json::to_string(&j).unwrap();
        let back: Judgement = serde_json::from_str(&wire).unwrap();
        assert_eq!(j, back);
    }
}
