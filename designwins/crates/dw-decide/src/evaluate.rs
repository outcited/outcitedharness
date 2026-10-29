//! Running the funnel: compare a cohort against typed constraints and produce
//! the explanation the acceptance test demands.
//!
//! This is the "compare @ your point" step. Given the atoms in force and what
//! is known about each candidate, it produces:
//!
//! * per-candidate judgements with evidence
//! * every exclusion with reason and evidence
//! * what could not be evaluated, as first-class unknowns
//!
//! **Nothing here calls a model and nothing here guesses.** A candidate whose
//! evidence does not cover the operating point is `Unknown`, and `Unknown`
//! never eliminates (`C9.1`).

use std::collections::{BTreeMap, BTreeSet};

use dw_normalize::{condition_covers, CondValue};
use dw_types::{Ineligible, SourceRef, Verdict};

use serde::{Deserialize, Serialize};

use crate::{
    Atom, Axis, Candidate, CategoryId, CategoryOp, Cohort, Constraint, Exclusion, Judgement,
    KnifeId, MissingAxis, Op,
};

/// What the printed value IS, semantically — the column it was printed in.
///
/// The sleep-current ruling (2026-10-19): a **ceiling** (guaranteed max)
/// above a requirement does not prove the part draws more than the
/// requirement; only a **floor** (guaranteed min) above it proves
/// violation. `Point` is a plain measured value.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Bound {
    /// A plain measured point value.
    Point,
    /// A guaranteed maximum — the manufacturer's ceiling. Proves
    /// satisfaction of an at-most requirement when under it; never proves
    /// violation.
    Ceiling,
    /// A guaranteed minimum — the manufacturer's floor. Proves violation of
    /// an at-most requirement when above it; never proves satisfaction.
    Floor,
}

/// One endpoint of a range-valued (span) claim — a printed lower or upper
/// limit with its own bound, conditions, and provenance.
///
/// The magnitude is **signed and in the axis-canonical unit**, preserved
/// exactly as printed: the historical TSTG `-55 175` defect collapsed the
/// lower endpoint to `175` by dropping the sign (M4 contract gap #1).
#[derive(Debug, Clone, PartialEq)]
pub struct SpanEndpoint {
    /// Signed magnitude, axis-canonical unit. Never abs-clamped.
    pub value: f64,
    /// The semantic bound of this endpoint's printed column.
    pub bound: Bound,
    /// The typed condition this endpoint was printed under.
    pub conditions: BTreeMap<String, CondValue>,
    /// Where the endpoint came from.
    pub sources: Vec<SourceRef>,
}

impl SpanEndpoint {
    /// A span endpoint with a signed magnitude and its printed bound.
    #[must_use]
    pub fn new(value: f64, bound: Bound, sources: Vec<SourceRef>) -> Self {
        Self {
            value,
            bound,
            conditions: BTreeMap::new(),
            sources,
        }
    }

    /// Attach the typed condition.
    #[must_use]
    pub fn with_conditions(mut self, conditions: BTreeMap<String, CondValue>) -> Self {
        self.conditions = conditions;
        self
    }
}

/// A range-valued claim. **At least one endpoint** — a partial span exists
/// (one printed column) but judges `UNKNOWN` under [`Op::Covers`], because a
/// half-range cannot establish containment.
#[derive(Debug, Clone, PartialEq)]
pub struct SpanEvidence {
    /// The lower endpoint, when printed.
    pub min: Option<SpanEndpoint>,
    /// The upper endpoint, when printed.
    pub max: Option<SpanEndpoint>,
}

impl SpanEvidence {
    /// Build from endpoints, or `None` when neither is present (an empty span
    /// is not evidence).
    #[must_use]
    pub fn from_endpoints(min: Option<SpanEndpoint>, max: Option<SpanEndpoint>) -> Option<Self> {
        if min.is_none() && max.is_none() {
            None
        } else {
            Some(Self { min, max })
        }
    }

    /// A complete two-sided span from two signed endpoint magnitudes.
    #[must_use]
    pub fn two_sided(min: f64, max: f64, bound: Bound, sources: Vec<SourceRef>) -> Self {
        Self {
            min: Some(SpanEndpoint::new(min, bound, sources.clone())),
            max: Some(SpanEndpoint::new(max, bound, sources)),
        }
    }

    /// True when both endpoints are present.
    #[must_use]
    pub fn is_complete(&self) -> bool {
        self.min.is_some() && self.max.is_some()
    }

    /// Both endpoints, lower first.
    pub fn endpoints(&self) -> impl Iterator<Item = &SpanEndpoint> {
        self.min.iter().chain(self.max.iter())
    }

    /// The union of every endpoint's sources, in endpoint order.
    #[must_use]
    pub fn sources(&self) -> Vec<SourceRef> {
        let mut out = Vec::new();
        for ep in self.endpoints() {
            out.extend(ep.sources.iter().cloned());
        }
        out
    }

    /// True when any endpoint magnitude is non-finite (fail-closed input).
    #[must_use]
    pub fn has_non_finite_endpoint(&self) -> bool {
        self.endpoints().any(|ep| !ep.value.is_finite())
    }

    /// True when a complete span is inverted (`min > max`) — a printed range
    /// that cannot be a range. Parks fail-closed.
    #[must_use]
    pub fn is_inverted(&self) -> bool {
        match (&self.min, &self.max) {
            (Some(mn), Some(mx)) => mn.value > mx.value,
            _ => false,
        }
    }
}

/// What is known about one candidate, on one axis.
///
/// **Scalar and span are exclusive.** A legal `Evidence` carries exactly one
/// of [`Evidence::value`] and [`Evidence::span`]; the constructors enforce it
/// and [`judge`] refuses a shape carrying both (fail-closed).
#[derive(Debug, Clone, PartialEq)]
pub struct Evidence {
    /// The measured magnitude, when there is one.
    pub value: Option<f64>,
    /// The semantic bound of the printed value (see [`Bound`]).
    pub bound: Bound,
    /// The rating class of the printed value. An `absmax` never satisfies an
    /// operating atom (`C9.2`), which is why it must be carried.
    pub rating_allows_operating: bool,
    /// The typed condition the value was printed under.
    pub conditions: BTreeMap<String, CondValue>,
    /// Where the number came from. Every verdict carries these so the UI can
    /// open the page without a second call (§0, D7).
    pub sources: Vec<SourceRef>,
    /// Range-valued evidence (M4 contract gap #1). `None` for a scalar.
    pub span: Option<SpanEvidence>,
}

impl Evidence {
    /// Evidence with a value and its page. Point-bounded (see [`Bound`]).
    #[must_use]
    pub fn measured(value: f64, rating_allows_operating: bool, sources: Vec<SourceRef>) -> Self {
        Self {
            value: Some(value),
            bound: Bound::Point,
            rating_allows_operating,
            conditions: BTreeMap::new(),
            sources,
            span: None,
        }
    }

    /// Evidence carrying a printed **bound** — a guaranteed max (ceiling)
    /// or guaranteed min (floor) column value. The sleep-current ruling:
    /// ceilings prove satisfaction, floors prove violation, neither proves
    /// both.
    #[must_use]
    pub fn measured_bound(
        value: f64,
        bound: Bound,
        rating_allows_operating: bool,
        sources: Vec<SourceRef>,
    ) -> Self {
        Self {
            value: Some(value),
            bound,
            rating_allows_operating,
            conditions: BTreeMap::new(),
            sources,
            span: None,
        }
    }

    /// Evidence we have no number for. Yields `Unknown`, never a guess.
    #[must_use]
    pub fn unknown(sources: Vec<SourceRef>) -> Self {
        Self {
            value: None,
            bound: Bound::Point,
            rating_allows_operating: false,
            conditions: BTreeMap::new(),
            sources,
            span: None,
        }
    }

    /// Range-valued evidence with at least one endpoint. The top-level
    /// [`Evidence::sources`] is the union of every endpoint's sources so the
    /// provenance gate covers the whole span.
    #[must_use]
    pub fn spanned(span: SpanEvidence, rating_allows_operating: bool) -> Self {
        let sources = span.sources();
        Self {
            value: None,
            bound: Bound::Point,
            rating_allows_operating,
            conditions: BTreeMap::new(),
            sources,
            span: Some(span),
        }
    }

    /// Attach the printed condition.
    #[must_use]
    pub fn with_conditions(mut self, conditions: BTreeMap<String, CondValue>) -> Self {
        self.conditions = conditions;
        self
    }

    /// True when the scalar/span exclusivity invariant holds — a legal
    /// `Evidence` never carries both a scalar value and a span.
    #[must_use]
    pub fn is_well_formed(&self) -> bool {
        !(self.value.is_some() && self.span.is_some())
    }
}

/// A named dimension of **category** facts — a namespace whose nodes contain
/// one another (`Cortex-M4F ⊂ Cortex-M ⊂ ARM`).
///
/// Dimensions are **separate namespaces that never merge**: `core_family` and
/// `package` are distinct keys in [`CandidateFacts::dimensions`], and a node
/// name that appears in both is still two disjoint facts. `PRD-PHRASE` §8.1:
/// a category is containment, not magnitude, and membership in one dimension
/// says nothing about another (M4 contract gap #2).
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct DimensionId(String);

impl DimensionId {
    /// The **core family** dimension (the 47% traffic class).
    pub const CORE_FAMILY: &'static str = "core_family";
    /// The **package** dimension — nominal, not scalar (`parts.package` is
    /// `TEXT`, PRD-PHRASE §8.1 *"Category is not Axis"*).
    pub const PACKAGE: &'static str = "package";

    /// Name a dimension.
    ///
    /// # Errors
    ///
    /// Returns [`dw_types::LawError::Empty`] if the name is empty.
    pub fn new(name: impl Into<String>) -> Result<Self, dw_types::LawError> {
        let s = name.into();
        if s.is_empty() {
            return Err(dw_types::LawError::Empty {
                field: "dimension id",
            });
        }
        Ok(Self(s))
    }

    /// The core-family dimension handle.
    #[must_use]
    pub fn core_family() -> Self {
        Self(Self::CORE_FAMILY.to_string())
    }

    /// The package dimension handle.
    #[must_use]
    pub fn package() -> Self {
        Self(Self::PACKAGE.to_string())
    }

    /// The dimension name.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// One candidate's membership in a single category dimension, with the
/// measured provenance that justifies it.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct CategoryMembership {
    /// The **most specific** node this candidate sits at. `None` is `Unknown`,
    /// never "miscellaneous".
    pub leaf: Option<CategoryId>,
    /// Every ancestor of `leaf`, **materialised**. Storing the closure rather
    /// than the edges means [`CategoryOp::Within`] is a set lookup and no graph
    /// has to be threaded through the evaluator.
    pub ancestors: BTreeSet<CategoryId>,
    /// Where the assignment came from — the document that printed it, with its
    /// row receipt. Required for any category-based exclusion, so §16.5 ("no
    /// unsupported exclusion") holds for categories exactly as scalars.
    pub evidence: Vec<SourceRef>,
}

impl CategoryMembership {
    /// A membership from a leaf and its materialised ancestors.
    #[must_use]
    pub fn new(
        leaf: Option<CategoryId>,
        ancestors: BTreeSet<CategoryId>,
        evidence: Vec<SourceRef>,
    ) -> Self {
        Self {
            leaf,
            ancestors,
            evidence,
        }
    }

    /// Which nodes this candidate belongs to, leaf included.
    pub fn nodes(&self) -> impl Iterator<Item = &CategoryId> {
        self.leaf.iter().chain(self.ancestors.iter())
    }

    /// `CategoryOp::Is` / `Within` / `Excludes` against the materialised
    /// closure. `None` when no leaf is recorded (that is `Unknown`).
    #[must_use]
    pub fn matches(&self, op: CategoryOp, node: &CategoryId) -> Option<bool> {
        let leaf = self.leaf.as_ref()?;
        let within = self.leaf.as_ref() == Some(node) || self.ancestors.contains(node);
        Some(match op {
            CategoryOp::Is => leaf == node,
            CategoryOp::Within => within,
            CategoryOp::Excludes => !within,
        })
    }
}

/// Everything the evaluator knows about one candidate.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct CandidateFacts {
    /// Per-axis evidence. An absent axis is `Unknown`, never `None`-means-ok.
    pub axes: BTreeMap<Axis, Evidence>,
    /// Per-**dimension** category membership. An absent dimension is
    /// `Unknown`, never a guess (M4 contract gap #2).
    pub dimensions: BTreeMap<DimensionId, CategoryMembership>,
}

impl CandidateFacts {
    /// The membership recorded for one dimension, when there is one.
    #[must_use]
    pub fn category_membership(&self, dim: &DimensionId) -> Option<&CategoryMembership> {
        self.dimensions.get(dim)
    }

    /// Which nodes this candidate belongs to in the `core_family` dimension.
    ///
    /// Retained as the dimension the seed phrase layer binds; new dimensions
    /// should use [`CandidateFacts::category_nodes_in`].
    pub fn category_nodes(&self) -> impl Iterator<Item = &CategoryId> {
        self.dimensions
            .get(&DimensionId::core_family())
            .into_iter()
            .flat_map(CategoryMembership::nodes)
    }

    /// Which nodes this candidate belongs to in one dimension.
    pub fn category_nodes_in<'a>(
        &'a self,
        dim: &'a DimensionId,
    ) -> impl Iterator<Item = &'a CategoryId> + 'a {
        self.dimensions
            .get(dim)
            .into_iter()
            .flat_map(CategoryMembership::nodes)
    }

    /// `CategoryOp::Is` / `Within` / `Excludes` in one dimension, evaluated
    /// against the materialised closure.
    #[must_use]
    pub fn category_matches_in(
        &self,
        dim: &DimensionId,
        op: CategoryOp,
        node: &CategoryId,
    ) -> Option<bool> {
        self.category_membership(dim)?.matches(op, node)
    }

    /// `CategoryOp::Is` / `Within` / `Excludes` in the `core_family`
    /// dimension (the seed binding; see [`judge_category`]).
    #[must_use]
    pub fn category_matches(&self, op: CategoryOp, node: &CategoryId) -> Option<bool> {
        self.category_matches_in(&DimensionId::core_family(), op, node)
    }
}

/// Judge a category atom against a candidate's materialised node closure in
/// one dimension.
///
/// `None` means the candidate has **no membership recorded for that
/// dimension** — which is `Unknown`, and `Unknown` never eliminates. The
/// dimensions never merge: a candidate with `core_family` but no `package`
/// judges `Unknown` on a package atom (M4 contract gap #2).
#[must_use]
pub fn judge_category_in(
    facts: Option<&CandidateFacts>,
    dim: &DimensionId,
    op: CategoryOp,
    node: &CategoryId,
) -> Verdict {
    match facts.and_then(|f| f.category_matches_in(dim, op, node)) {
        Some(true) => Verdict::Pass,
        Some(false) => Verdict::Fail,
        None => Verdict::Unknown,
    }
}

/// Judge a `core_family` category atom against a candidate's materialised
/// node closure.
///
/// This is [`judge_category_in`] with [`DimensionId::core_family`], retained
/// because the seed phrase layer binds `core_family` category atoms and
/// package activation is prohibited (M4 `do-9id`).
#[must_use]
pub fn judge_category(
    facts: Option<&CandidateFacts>,
    op: CategoryOp,
    node: &CategoryId,
) -> Verdict {
    judge_category_in(facts, &DimensionId::core_family(), op, node)
}

/// The margin band inside which a shortfall is `NearMiss` rather than `Fail`.
///
/// Stored explicitly because `C9.3` forbids a silent default.
pub const DEFAULT_NEAR_MISS_PCT: f64 = 10.0;

/// Can a piece of evidence be opened?
///
/// This is the trust law in trait form. `dw-evidence::EvidenceStore`
/// implements it against real pixels; tests use [`AlwaysResolvable`].
///
/// It is a parameter of [`judge`] rather than a caller-side check **so it
/// cannot be forgotten**. A provenance check that lives at the call site is a
/// provenance check that gets skipped the first time someone is in a hurry.
pub trait Provenance {
    /// True when every source reference on this evidence resolves to a
    /// content-addressed page render and row-band crop.
    fn resolvable(&self, sources: &[SourceRef]) -> bool;
}

/// A resolver that accepts everything. For tests and for the period before a
/// store is wired in — and **only** for that.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct AlwaysResolvable;

impl Provenance for AlwaysResolvable {
    fn resolvable(&self, _: &[SourceRef]) -> bool {
        true
    }
}

/// Compare one evidence against one atom.
///
/// # The decision, in order
///
/// 1. No evidence → `Unknown`. Never eliminates.
/// 2. Evidence exists but the rating class forbids operating use (an
///    absolute maximum) → `Unknown`, because it is the wrong quantity and
///    cannot be compared at all (`C9.2`). It is **not** a `Fail`: the part may
///    still be fine, we simply have not established it.
/// 3. Evidence exists but its condition does not **cover** the required
///    operating point → `Unknown` (`C9.6`).
/// 4. Evidence that does not resolve to pixels is `Unknown` — **provenance
///    failure refuses an answer rather than passing one**
///    (`PROVENANCE.md` §4.1).
/// 5. Compare, with the margin applied to the **requirement** (`C9.3`).
#[must_use]
pub fn judge(evidence: Option<&Evidence>, atom: &Atom, provenance: &dyn Provenance) -> Verdict {
    let Some(ev) = evidence else {
        return Verdict::Unknown;
    };

    // Scalar/span exclusivity: a shape carrying both is malformed and cannot
    // be trusted to compare honestly — fail closed.
    if !ev.is_well_formed() {
        return Verdict::Unknown;
    }

    // Trust gate: a number we cannot open is a number we cannot assert. This
    // is `C9.1` applied to provenance itself.
    if !provenance.resolvable(&ev.sources) {
        return Verdict::Unknown;
    }

    // C9.2: an absolute maximum is a survival boundary, not an operating
    // guarantee. The wrong quantity cannot be compared — say so, do not guess.
    if !ev.rating_allows_operating {
        return Verdict::Unknown;
    }

    // C9.6: the evidence condition must COVER the requirement's condition.
    if !atom.required_conditions.is_empty() {
        let coverage = condition_covers(Some(&ev.conditions), Some(&atom.required_conditions));
        if !coverage.may_satisfy() {
            return Verdict::Unknown;
        }
    }

    // A category atom has no magnitude and no margin. `PRD-PHRASE` §8.1: a
    // minimum on a core family is meaningless and the type refuses it.
    let (op, required, inflated) = match &atom.constraint {
        Constraint::Scalar {
            op,
            value,
            margin_pct,
        } => {
            let inflated = match margin_pct {
                Some(p) => *value * (1.0 + p / 100.0),
                None => *value,
            };
            (*op, *value, inflated)
        }
        Constraint::Category { .. } => {
            // Category atoms carry no magnitude and no evidence-in-quotes;
            // membership lives on `CandidateFacts` and is judged by
            // [`judge_category`]. `judge` never invents an answer for them.
            return Verdict::Unknown;
        }
    };
    let band = required.abs() * (DEFAULT_NEAR_MISS_PCT / 100.0);

    // `Covers` is the range op: it requires span evidence and is judged over
    // both endpoints. Every other op is scalar and requires scalar evidence.
    if op == Op::Covers {
        return judge_covers(ev, required, band, &atom.required_conditions);
    }

    // A scalar op on span evidence is the wrong quantity for the axis —
    // fail closed rather than coerce a range into a point.
    let Some(value) = ev.value else {
        return Verdict::Unknown;
    };

    match op {
        Op::MinRating => {
            if value >= inflated {
                Verdict::Pass
            } else if value >= required - band {
                Verdict::NearMiss
            } else {
                Verdict::Fail
            }
        }
        Op::AtMost => {
            // The 2026-10-19 ruling, verbatim: a ceiling above the
            // requirement fails to SATISFY (Unknown / NearMiss), it never
            // proves violation; a floor above the requirement proves
            // violation; a point value proves neither.
            match ev.bound {
                Bound::Ceiling => {
                    if value <= inflated {
                        Verdict::Pass
                    } else if value <= required + band {
                        Verdict::NearMiss
                    } else {
                        Verdict::Unknown
                    }
                }
                Bound::Floor => {
                    if value > required + band {
                        Verdict::Fail
                    } else if value > inflated {
                        Verdict::NearMiss
                    } else {
                        Verdict::Unknown
                    }
                }
                Bound::Point => Verdict::Unknown,
            }
        }
        Op::MaxRating => {
            if value <= inflated {
                Verdict::Pass
            } else if value <= required + band {
                Verdict::NearMiss
            } else {
                Verdict::Fail
            }
        }
        // Unreachable: `Covers` returned above. Kept explicit so a future op
        // cannot silently fall through to a scalar comparison.
        Op::Covers => Verdict::Unknown,
    }
}

/// `Op::Covers` over span evidence: the claim's range must contain the
/// requirement, with a per-endpoint near-miss band.
///
/// Containment is `claim.min <= requirement <= claim.max` (the requirement is
/// a scalar operating point, so `req.min == req.max`). A violation on either
/// endpoint is `NearMiss` when it is within the axis's near-miss band of the
/// **requirement** magnitude, otherwise `Fail`.
///
/// Fail closed on every incomplete or untyped shape:
/// * scalar evidence on a range axis → `Unknown`;
/// * a partial span (one endpoint) → `Unknown` (a half-range cannot establish
///   containment);
/// * inverted or non-finite spans → `Unknown`;
/// * any endpoint whose conditions do not cover the requirement → `Unknown`.
///
/// `Unknown` never eliminates (`C9.1`).
#[must_use]
fn judge_covers(
    ev: &Evidence,
    required: f64,
    band: f64,
    required_conditions: &BTreeMap<String, CondValue>,
) -> Verdict {
    let Some(span) = ev.span.as_ref() else {
        return Verdict::Unknown;
    };
    // Inverted or non-finite spans are not ranges — a printed value that
    // cannot be a range parks, never best-guesses.
    if span.has_non_finite_endpoint() || span.is_inverted() {
        return Verdict::Unknown;
    }
    let (Some(min_ep), Some(max_ep)) = (span.min.as_ref(), span.max.as_ref()) else {
        return Verdict::Unknown;
    };

    // C9.6 per endpoint: both endpoints must cover the required condition.
    if !required_conditions.is_empty() {
        for ep in [min_ep, max_ep] {
            let coverage = condition_covers(Some(&ep.conditions), Some(required_conditions));
            if !coverage.may_satisfy() {
                return Verdict::Unknown;
            }
        }
    }

    // Lower endpoint above the requirement: the range starts too high.
    if min_ep.value > required {
        return if min_ep.value - required <= band {
            Verdict::NearMiss
        } else {
            Verdict::Fail
        };
    }
    // Upper endpoint below the requirement: the range ends too low.
    if max_ep.value < required {
        return if required - max_ep.value <= band {
            Verdict::NearMiss
        } else {
            Verdict::Fail
        };
    }
    Verdict::Pass
}

/// The result of running the funnel over a cohort.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Evaluation {
    /// Per-candidate, per-atom verdicts with evidence.
    pub judgements: Vec<Judgement>,
    /// Every exclusion, with reason and evidence.
    pub exclusions: Vec<Exclusion>,
    /// Axes we could not evaluate, and how many candidates that affects.
    pub unknowns: Vec<MissingAxis>,
    /// Candidates that survived every hard atom.
    pub cohort: Cohort,
}

/// Run the constraints in force over a cohort.
///
/// Returns the judgements, exclusions, unknowns, and the surviving cohort —
/// everything the acceptance test names, in one pass, so a front end can render
/// the whole experience from one response (D7).
#[must_use]
pub fn evaluate(
    cohort: &Cohort,
    atoms: &[Atom],
    facts: &BTreeMap<Candidate, CandidateFacts>,
    provenance: &dyn Provenance,
) -> Evaluation {
    let mut eval = Evaluation::default();
    let mut unknown_counts: BTreeMap<Axis, usize> = BTreeMap::new();
    let mut survivors = Vec::new();

    for candidate in &cohort.members {
        let known = facts.get(candidate);
        let mut eliminated = false;

        for atom in atoms {
            // Category atoms are judged on the node closure, not on evidence.
            if let (KnifeId::Category(node), Constraint::Category { op: cop, .. }) =
                (&atom.knife, &atom.constraint)
            {
                let outcome = judge_category(known, *cop, node);
                eval.judgements.push(Judgement {
                    candidate: candidate.clone(),
                    axis: Axis::TempRangeC,
                    outcome,
                    evidence: Vec::new(),
                });
                if !eliminated && crate::Elimination::eliminates(outcome, atom.hard) {
                    let sources = known
                        .and_then(|f| f.category_membership(&DimensionId::core_family()))
                        .map(|m| m.evidence.clone())
                        .unwrap_or_default();
                    // §16.5: an exclusion is unconstructible without evidence.
                    // A category membership with no provenance therefore cannot
                    // eliminate — which is the correct behaviour, not a gap.
                    if let Ok(ineligible) =
                        Ineligible::new(format!("{cop:?} {} failed", node.as_str()), sources)
                    {
                        eval.exclusions.push(Exclusion {
                            candidate: candidate.clone(),
                            axis: Axis::TempRangeC,
                            ineligible,
                            outcome,
                        });
                        eliminated = true;
                    }
                }
                continue;
            }

            let axis = match &atom.knife {
                KnifeId::Axis(a) => *a,
                KnifeId::Category(_) => Axis::TempRangeC,
            };
            let evidence = known.and_then(|f| f.axes.get(&axis));
            let outcome = judge(evidence, atom, provenance);

            eval.judgements.push(Judgement {
                candidate: candidate.clone(),
                axis,
                outcome,
                evidence: evidence.map(|e| e.sources.clone()).unwrap_or_default(),
            });

            // Unresolvable evidence is an unknown too: we have a number we
            // cannot open, which is the same as not having established it.
            // Span evidence establishes a number via its endpoints, not via
            // `value`, so it counts as measured here.
            let unresolvable = evidence.is_some_and(|e| !provenance.resolvable(&e.sources));
            let measured = evidence.is_some_and(|e| e.value.is_some() || e.span.is_some());
            if !measured || unresolvable {
                *unknown_counts.entry(axis).or_insert(0) += 1;
            }

            // The elimination law, applied in exactly one place.
            if !eliminated && crate::Elimination::eliminates(outcome, atom.hard) {
                let reason = format!("{axis:?} failed at the required operating point");
                let sources = evidence.map(|e| e.sources.clone()).unwrap_or_default();
                // `Ineligible` is unconstructible without evidence; if we have
                // none, the exclusion cannot be motivated and must not be
                // recorded. That case is `Unknown`, not `Fail`, so it cannot
                // reach here.
                if let Ok(ineligible) = Ineligible::new(reason, sources) {
                    eval.exclusions.push(Exclusion {
                        candidate: candidate.clone(),
                        axis,
                        ineligible,
                        outcome,
                    });
                    eliminated = true;
                }
            }
        }

        if !eliminated {
            survivors.push(candidate.clone());
        }
    }

    for (axis, affected) in unknown_counts {
        eval.unknowns.push(MissingAxis {
            axis: Some(KnifeId::Axis(axis)),
            phrase: None,
            why: "no evidence on this axis for these candidates".to_string(),
            affected,
        });
    }

    eval.cohort = Cohort::from_candidates(survivors);
    eval
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
    use crate::{
        ConfirmState, Constraint, Derived, DesignContext, DesignIntent, KnifeId, Op, Requirement,
    };
    use dw_types::{ReleaseId, Sha256};

    fn src() -> Vec<SourceRef> {
        let sha = Sha256::new("a".repeat(64)).unwrap();
        vec![SourceRef::page(sha, 2)]
    }

    fn vds_atom(hard: bool, margin: Option<f64>) -> Atom {
        Atom {
            knife: KnifeId::Axis(Axis::VdsRatingV),
            constraint: Constraint::Scalar {
                op: Op::MinRating,
                value: 36.0,
                margin_pct: margin,
            },
            hard,
            required_conditions: BTreeMap::new(),
        }
    }

    fn cand(n: &str) -> Candidate {
        Candidate {
            part_number: n.to_string(),
            vendor: "ti".to_string(),
        }
    }

    /// C9.1: missing evidence is Unknown, and Unknown never eliminates.
    #[test]
    fn missing_evidence_never_eliminates() {
        let cohort = Cohort::from_candidates(vec![cand("A")]);
        let mut facts = BTreeMap::new();
        facts.insert(cand("A"), CandidateFacts::default());

        let eval = evaluate(&cohort, &[vds_atom(true, None)], &facts, &AlwaysResolvable);
        assert!(eval.exclusions.is_empty());
        assert_eq!(eval.cohort.len(), 1);
        assert_eq!(eval.unknowns.len(), 1);
        assert_eq!(eval.unknowns[0].affected, 1);
    }

    /// The trust gate (`PROVENANCE.md` §4.1): a number we cannot open is a
    /// number we cannot assert. Unresolvable evidence is `Unknown`, never
    /// `Pass` — provenance failure refuses an answer instead of passing one.
    #[test]
    fn unresolvable_evidence_is_unknown_not_pass() {
        struct NothingResolves;
        impl Provenance for NothingResolves {
            fn resolvable(&self, _: &[SourceRef]) -> bool {
                false
            }
        }

        let a = vds_atom(true, None);
        // Plenty of rating, plenty of condition coverage — and no pixels.
        let ev = Evidence::measured(1000.0, true, src());
        assert_eq!(
            judge(Some(&ev), &a, &NothingResolves),
            Verdict::Unknown,
            "an unopenable number must not become Pass"
        );
        // The same evidence with a working resolver does pass.
        assert_eq!(judge(Some(&ev), &a, &AlwaysResolvable), Verdict::Pass);
    }

    /// The trust gate is in the signature, not at a call site, so it cannot
    /// be skipped by someone in a hurry.
    #[test]
    fn evaluate_refuses_unresolvable_evidence() {
        struct NothingResolves;
        impl Provenance for NothingResolves {
            fn resolvable(&self, _: &[SourceRef]) -> bool {
                false
            }
        }

        let cohort = Cohort::from_candidates(vec![cand("A")]);
        let mut facts = BTreeMap::new();
        facts.insert(
            cand("A"),
            CandidateFacts {
                axes: BTreeMap::from([(Axis::VdsRatingV, Evidence::measured(40.0, true, src()))]),
                ..Default::default()
            },
        );

        let eval = evaluate(&cohort, &[vds_atom(true, None)], &facts, &NothingResolves);
        assert!(eval.exclusions.is_empty(), "no proof, no elimination");
        assert_eq!(
            eval.cohort.len(),
            1,
            "nothing is eliminated without evidence"
        );
        assert_eq!(eval.unknowns.len(), 1, "and the gap is reported");
    }

    /// C9.2: an absolute maximum is the wrong quantity, so it is Unknown —
    /// not Fail. The part may be fine; we have not established it.
    #[test]
    fn absmax_evidence_is_unknown_not_fail() {
        let a = vds_atom(true, None);
        let ev = Evidence::measured(40.0, false, src());
        assert_eq!(judge(Some(&ev), &a, &AlwaysResolvable), Verdict::Unknown);
        assert!(!crate::Elimination::eliminates(Verdict::Unknown, true));
    }

    #[test]
    fn adequate_rating_passes_and_eliminates_nothing() {
        let cohort = Cohort::from_candidates(vec![cand("A"), cand("B")]);
        let mut facts = BTreeMap::new();
        facts.insert(
            cand("A"),
            CandidateFacts {
                axes: BTreeMap::from([(Axis::VdsRatingV, Evidence::measured(40.0, true, src()))]),
                ..Default::default()
            },
        );
        facts.insert(
            cand("B"),
            CandidateFacts {
                axes: BTreeMap::from([(Axis::VdsRatingV, Evidence::measured(34.0, true, src()))]),
                ..Default::default()
            },
        );

        let eval = evaluate(&cohort, &[vds_atom(true, None)], &facts, &AlwaysResolvable);
        assert!(
            eval.exclusions.is_empty(),
            "34 is a near-miss at 10%, not a fail"
        );
        assert_eq!(eval.cohort.len(), 2);
    }

    /// C9.3: near-miss is a separate cohort, not a Fail.
    #[test]
    fn near_miss_is_not_fail() {
        let a = vds_atom(true, None);
        assert_eq!(
            judge(
                Some(&Evidence::measured(34.0, true, src())),
                &a,
                &AlwaysResolvable
            ),
            Verdict::NearMiss
        );
        assert_eq!(
            judge(
                Some(&Evidence::measured(30.0, true, src())),
                &a,
                &AlwaysResolvable
            ),
            Verdict::Fail
        );
        assert_eq!(
            judge(
                Some(&Evidence::measured(36.0, true, src())),
                &a,
                &AlwaysResolvable
            ),
            Verdict::Pass
        );
    }

    /// C9.3: margin inflates the requirement, so a claim that passed raw can
    /// become a near-miss under margin.
    #[test]
    fn margin_is_applied_to_the_requirement() {
        let none = vds_atom(true, None);
        let pct20 = vds_atom(true, Some(20.0));
        let ev = Evidence::measured(40.0, true, src());
        assert_eq!(judge(Some(&ev), &none, &AlwaysResolvable), Verdict::Pass);
        assert_eq!(
            judge(Some(&ev), &pct20, &AlwaysResolvable),
            Verdict::NearMiss
        );
    }

    /// C9.6: the evidence condition must COVER the required operating point.
    #[test]
    fn uncovered_condition_is_unknown() {
        let mut a = vds_atom(true, None);
        a.required_conditions
            .insert("ta_c".to_string(), CondValue::Scalar(125.0));
        let ev = Evidence::measured(40.0, true, src()).with_conditions(BTreeMap::from([(
            "ta_c".to_string(),
            CondValue::Scalar(25.0),
        )]));
        assert_eq!(judge(Some(&ev), &a, &AlwaysResolvable), Verdict::Unknown);
    }

    #[test]
    fn covered_condition_is_comparable() {
        let mut a = vds_atom(true, None);
        a.required_conditions
            .insert("ta_c".to_string(), CondValue::Scalar(25.0));
        let ev = Evidence::measured(40.0, true, src()).with_conditions(BTreeMap::from([(
            "ta_c".to_string(),
            CondValue::Scalar(25.0),
        )]));
        assert_eq!(judge(Some(&ev), &a, &AlwaysResolvable), Verdict::Pass);
    }

    /// A soft atom never eliminates, however badly it fails.
    #[test]
    fn soft_atoms_never_eliminate() {
        let cohort = Cohort::from_candidates(vec![cand("A")]);
        let mut facts = BTreeMap::new();
        facts.insert(
            cand("A"),
            CandidateFacts {
                axes: BTreeMap::from([(Axis::VdsRatingV, Evidence::measured(1.0, true, src()))]),
                ..Default::default()
            },
        );
        let eval = evaluate(&cohort, &[vds_atom(false, None)], &facts, &AlwaysResolvable);
        assert!(eval.exclusions.is_empty());
        assert_eq!(eval.cohort.len(), 1);
    }

    /// Every exclusion carries reason and evidence, so the UI can open it.
    #[test]
    fn exclusions_are_clickable_open() {
        let cohort = Cohort::from_candidates(vec![cand("A")]);
        let mut facts = BTreeMap::new();
        facts.insert(
            cand("A"),
            CandidateFacts {
                axes: BTreeMap::from([(Axis::VdsRatingV, Evidence::measured(1.0, true, src()))]),
                ..Default::default()
            },
        );
        let eval = evaluate(&cohort, &[vds_atom(true, None)], &facts, &AlwaysResolvable);
        assert_eq!(eval.exclusions.len(), 1);
        let e = &eval.exclusions[0];
        assert!(!e.ineligible.reason().is_empty());
        assert!(
            !e.ineligible.evidence().is_empty(),
            "D7: exclusion must open to a page"
        );
    }

    /// The full acceptance-test shape, end to end.
    #[test]
    fn acceptance_shape_is_complete() {
        use crate::Build;
        let cohort = Cohort::from_candidates(vec![cand("A"), cand("B")]);
        let mut facts = BTreeMap::new();
        facts.insert(
            cand("A"),
            CandidateFacts {
                axes: BTreeMap::from([(Axis::VdsRatingV, Evidence::measured(40.0, true, src()))]),
                ..Default::default()
            },
        );
        facts.insert(
            cand("B"),
            CandidateFacts {
                axes: BTreeMap::from([(Axis::VdsRatingV, Evidence::measured(1.0, true, src()))]),
                ..Default::default()
            },
        );
        let eval = evaluate(&cohort, &[vds_atom(true, None)], &facts, &AlwaysResolvable);

        let build = Build {
            id: "b".into(),
            release: ReleaseId::new("r1").unwrap(),
            intent: DesignIntent::Text("36 V input supply".into()),
            context: DesignContext::default(),
            declared: vec![Requirement {
                id: "r".into(),
                verbatim: "must survive 36 V".into(),
                atoms: vec![vds_atom(true, None)],
            }],
            derived: vec![Derived {
                id: "d".into(),
                rationale: "context implies automotive temperature".into(),
                atoms: vec![],
                state: ConfirmState::Pending,
            }],
            constraints: vec![],
            cohort: eval.cohort.clone(),
            explain: eval.judgements.clone(),
            exclusions: eval.exclusions.clone(),
            unknowns: eval.unknowns.clone(),
            next: vec![],
        };

        // Every part of the acceptance test is present.
        assert_eq!(build.cohort.len(), 1, "cohort of distinct parts");
        assert!(!build.explain.is_empty(), "which constraints were applied");
        assert_eq!(build.exclusions.len(), 1, "every exclusion with evidence");
        assert!(
            build.derived[0].state == ConfirmState::Pending,
            "derived shown"
        );
        let s = build.summary();
        assert!(s.contains("cohort       1 distinct parts"));
    }

    // ---- Range-valued (span) evidence — M4 contract gap #1 -----------------

    /// A `Covers` atom: the requirement is a scalar operating point.
    fn covers_atom(required: f64) -> Atom {
        Atom {
            knife: KnifeId::Axis(Axis::TstgRangeC),
            constraint: Constraint::Scalar {
                op: Op::Covers,
                value: required,
                margin_pct: None,
            },
            hard: true,
            required_conditions: BTreeMap::new(),
        }
    }

    /// Evidence with at least one signed endpoint (provenance resolvable).
    fn span(min: Option<f64>, max: Option<f64>) -> Evidence {
        let mk = |v: f64| SpanEndpoint::new(v, Bound::Point, src());
        let ev = SpanEvidence::from_endpoints(min.map(mk), max.map(mk))
            .expect("at least one endpoint is evidence");
        Evidence::spanned(ev, true)
    }

    /// Containment: `claim.min <= req <= claim.max` is Pass; a shortfall on
    /// either endpoint is Fail, and the near-miss band is a band of the
    /// **requirement**.
    #[test]
    fn covers_span_containment_passes_fails_and_near_misses() {
        let atom = covers_atom(85.0);
        // TSTG -55..175 covers an operating point of 85 (band = 8.5).
        assert_eq!(
            judge(
                Some(&span(Some(-55.0), Some(175.0))),
                &atom,
                &AlwaysResolvable
            ),
            Verdict::Pass
        );
        // Upper endpoint 80 sits 5 below the requirement — inside the band.
        assert_eq!(
            judge(
                Some(&span(Some(-55.0), Some(80.0))),
                &atom,
                &AlwaysResolvable
            ),
            Verdict::NearMiss
        );
        // Upper endpoint 50 is 35 short — a Fail.
        assert_eq!(
            judge(
                Some(&span(Some(-55.0), Some(50.0))),
                &atom,
                &AlwaysResolvable
            ),
            Verdict::Fail
        );
        // Lower endpoint 90 starts above the requirement — 5 over the band.
        assert_eq!(
            judge(
                Some(&span(Some(90.0), Some(175.0))),
                &atom,
                &AlwaysResolvable
            ),
            Verdict::NearMiss
        );
        assert_eq!(
            judge(
                Some(&span(Some(120.0), Some(175.0))),
                &atom,
                &AlwaysResolvable
            ),
            Verdict::Fail
        );
    }

    /// The near-miss band is a fraction of the requirement's magnitude, so a
    /// **negative** requirement bands symmetrically (regression: the
    /// `-55 175` collapse class made negative endpoints unrepresentable).
    #[test]
    fn covers_near_miss_uses_requirement_magnitude_with_negatives() {
        let atom = covers_atom(-100.0); // band = 10.0
                                        // -95 sits 5 above the requirement — inside the band.
        assert_eq!(
            judge(
                Some(&span(Some(-95.0), Some(0.0))),
                &atom,
                &AlwaysResolvable
            ),
            Verdict::NearMiss
        );
        // A signed lower endpoint below the requirement contains it.
        assert_eq!(
            judge(
                Some(&span(Some(-120.0), Some(0.0))),
                &atom,
                &AlwaysResolvable
            ),
            Verdict::Pass
        );
    }

    /// A scalar on a range axis, and a range on a scalar axis, are the wrong
    /// quantities — `Unknown`, never a coerced comparison.
    #[test]
    fn scalar_and_span_shapes_do_not_cross_axes() {
        assert_eq!(
            judge(
                Some(&Evidence::measured(85.0, true, src())),
                &covers_atom(85.0),
                &AlwaysResolvable
            ),
            Verdict::Unknown,
            "scalar evidence cannot satisfy a Covers axis"
        );
        assert_eq!(
            judge(
                Some(&span(Some(30.0), Some(40.0))),
                &vds_atom(true, None),
                &AlwaysResolvable
            ),
            Verdict::Unknown,
            "span evidence cannot satisfy a scalar axis"
        );
    }

    /// A partial span (one printed column) cannot establish containment.
    #[test]
    fn partial_span_is_unknown() {
        let atom = covers_atom(85.0);
        assert_eq!(
            judge(Some(&span(Some(-55.0), None)), &atom, &AlwaysResolvable),
            Verdict::Unknown
        );
        assert_eq!(
            judge(Some(&span(None, Some(175.0))), &atom, &AlwaysResolvable),
            Verdict::Unknown
        );
    }

    /// An inverted printed range is not a range.
    #[test]
    fn inverted_span_is_unknown() {
        let atom = covers_atom(85.0);
        let ev = span(Some(175.0), Some(-55.0));
        assert!(ev.span.as_ref().unwrap().is_inverted());
        assert_eq!(judge(Some(&ev), &atom, &AlwaysResolvable), Verdict::Unknown);
    }

    /// Non-finite endpoints park fail-closed.
    #[test]
    fn non_finite_span_is_unknown() {
        let atom = covers_atom(85.0);
        for bad in [f64::NEG_INFINITY, f64::INFINITY, f64::NAN] {
            assert_eq!(
                judge(
                    Some(&span(Some(bad), Some(175.0))),
                    &atom,
                    &AlwaysResolvable
                ),
                Verdict::Unknown,
                "endpoint {bad} must park"
            );
        }
    }

    /// A span endpoint whose condition does not cover the requirement is
    /// `Unknown` — `C9.6` applied per endpoint.
    #[test]
    fn span_endpoint_condition_must_cover_requirement() {
        let mut atom = covers_atom(85.0);
        atom.required_conditions
            .insert("ta_c".to_string(), CondValue::Scalar(125.0));

        let ep = |v: f64| {
            SpanEndpoint::new(v, Bound::Point, src()).with_conditions(BTreeMap::from([(
                "ta_c".to_string(),
                CondValue::Scalar(25.0),
            )]))
        };
        let span_ev = SpanEvidence::from_endpoints(Some(ep(-55.0)), Some(ep(175.0))).unwrap();
        // Top-level conditions cover so the per-endpoint check is reached.
        let ev = Evidence::spanned(span_ev, true).with_conditions(BTreeMap::from([(
            "ta_c".to_string(),
            CondValue::Scalar(125.0),
        )]));
        assert_eq!(judge(Some(&ev), &atom, &AlwaysResolvable), Verdict::Unknown);
    }

    /// A malformed shape carrying both a scalar and a span cannot be trusted.
    #[test]
    fn dual_scalar_and_span_is_malformed_and_unknown() {
        let mut ev = span(Some(-55.0), Some(175.0));
        ev.value = Some(85.0);
        assert!(!ev.is_well_formed());
        assert_eq!(
            judge(Some(&ev), &covers_atom(85.0), &AlwaysResolvable),
            Verdict::Unknown
        );
        assert_eq!(
            judge(Some(&ev), &vds_atom(true, None), &AlwaysResolvable),
            Verdict::Unknown
        );
    }
}
