//! # dw-types — the law as types
//!
//! Replaces invariants that lived in comments across
//! `harness/electronics/{claims,admission,condition_model,rating_class,curve_atoms}.py`.
//!
//! Each contract from `docs/CONTRACTS.md` is enforced here by construction.
//! The compile-fail tests in `tests/compile_fail/` prove it: try to break the
//! law and the build breaks instead.
//!
//! | law | enforced by |
//! |-----|-------------|
//! | C3  | [`Claim`] is unconstructible without [`Verbatim`] + [`SourceRef`] |
//! | C9  | [`Verdict::Unknown`] never eliminates; see [`Elimination`] |
//! | C10 | [`Embedding`] carries a [`WeightFingerprint`] |
//! | C11 | [`Observed<T>`] — `Option` is banned for facts |
//! | C12 | [`Conflict`] has no path to [`Validated`] |
//! | C13 | [`SourceRef`] + [`CropRef`] are content-addressed |
//!
//! This crate is `forbid(unsafe_code)`. It does no I/O and calls no models.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

use std::collections::BTreeMap;
use std::fmt;

use serde::{Deserialize, Serialize};

// ---------------------------------------------------------------------------
// errors
// ---------------------------------------------------------------------------

/// Errors produced when a law is violated at a construction site.
///
/// These are *not* expected in correct code: the types prevent the violation.
/// They exist so runtime validation paths (API input, JSONL ingest) can refuse
/// the same shapes the type system refuses to build.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum LawError {
    /// A string that must be non-empty was empty.
    #[error("{field} must be non-empty")]
    Empty {
        /// Which field failed.
        field: &'static str,
    },
    /// A `sha256` was not 64 lowercase hex characters.
    #[error("invalid sha256: {0}")]
    InvalidSha256(String),
    /// A `Verbatim` quote does not appear in the referenced page text.
    #[error("quote is not verbatim in the referenced source")]
    NotVerbatim,
    /// A `Claim` was attempted without provenance.
    #[error("claim is missing provenance: {0}")]
    MissingProvenance(&'static str),
    /// An `Ineligible` was attempted without a reason.
    #[error("ineligible without a reason")]
    UnmotivatedExclusion,
}

// ---------------------------------------------------------------------------
// C13 — provenance: content-addressed references
// ---------------------------------------------------------------------------

/// A lowercase hex SHA-256. Unconstructible unless well-formed.
#[derive(Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct Sha256(String);

impl Sha256 {
    /// Length of a SHA-256 hex digest.
    pub const LEN: usize = 64;

    /// Wrap a hex digest, rejecting anything that is not 64 lowercase hex chars.
    ///
    /// # Errors
    ///
    /// Returns [`LawError::InvalidSha256`] unless the input is exactly
    /// [`Sha256::LEN`] lowercase hexadecimal characters.
    pub fn new(raw: impl Into<String>) -> Result<Self, LawError> {
        let s = raw.into();
        if s.len() != Self::LEN || !s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f')) {
            return Err(LawError::InvalidSha256(s));
        }
        Ok(Self(s))
    }

    /// Borrow the hex digest.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for Sha256 {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Sha256({}…)", &self.0[..12])
    }
}

impl fmt::Display for Sha256 {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// A row-band crop of a page render. Content-addressed, so a crop cannot be
/// silently swapped for a different one.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CropRef {
    /// Content hash of the crop image bytes.
    pub crop_sha256: Sha256,
    /// Render resolution. `C13` pins this at 144 DPI.
    pub dpi: u32,
    /// Crop rectangle in device pixels at `dpi`.
    pub bbox: [u32; 4],
}

impl CropRef {
    /// The provenance snapshot resolution mandated by `C13`.
    pub const PROVENANCE_DPI: u32 = 144;
}

/// Where a value came from. Enough for a front end to open the page and the
/// exact row without another call (`C7` feed contract, D7).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SourceRef {
    /// Document content hash — joins to the vault on this, never a filename.
    pub sha256: Sha256,
    /// 1-based page number within the document.
    pub page: u32,
    /// The row header that labels the value, when it came from a table.
    pub row_header: Option<String>,
    /// The column header that labels the value, when it came from a table.
    pub column_header: Option<String>,
    /// Evidence crop. Required for anything a front end will display as a
    /// number — see [`Claim::new`].
    pub crop: Option<CropRef>,
}

impl SourceRef {
    /// Reference to a document page, optionally within a table cell.
    #[must_use]
    pub fn page(sha256: Sha256, page: u32) -> Self {
        Self {
            sha256,
            page,
            row_header: None,
            column_header: None,
            crop: None,
        }
    }

    /// Attach the evidence crop required by `C13`.
    #[must_use]
    pub fn with_crop(mut self, crop: CropRef) -> Self {
        self.crop = Some(crop);
        self
    }

    /// Borrow the crop, if this source has been provenanced.
    #[must_use]
    pub fn crop(&self) -> Option<&CropRef> {
        self.crop.as_ref()
    }
}

// ---------------------------------------------------------------------------
// C3 — verbatim evidence
// ---------------------------------------------------------------------------

/// Text copied exactly from a document. Non-empty by construction.
///
/// A [`Claim`] cannot exist without one of these. This is the mechanical form
/// of "every number opens to the page it came from."
#[derive(Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct Verbatim(String);

impl Verbatim {
    /// Wrap quoted text, rejecting the empty string.
    ///
    /// # Errors
    ///
    /// Returns [`LawError::Empty`] if the quote is empty.
    pub fn new(raw: impl Into<String>) -> Result<Self, LawError> {
        let s = raw.into();
        if s.is_empty() {
            return Err(LawError::Empty { field: "verbatim" });
        }
        Ok(Self(s))
    }

    /// Borrow the quoted text.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for Verbatim {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "Verbatim({:?})", self.0)
    }
}

impl fmt::Display for Verbatim {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{:?}", self.0)
    }
}

// ---------------------------------------------------------------------------
// C11 — absence is a fact
// ---------------------------------------------------------------------------

/// Absence has three distinct meanings and `Option` is banned for facts.
///
/// `NotObserved != NotApplicable != None`. Collapsing them is how a dataset
/// quietly lies: "we looked and it was not there" and "this does not apply"
/// produce opposite engineering conclusions.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub enum Observed<T> {
    /// The fact is present and known.
    Present(T),
    /// We looked. It was not there. This is a finding.
    NotObserved,
    /// This axis does not apply to this object. Also a finding.
    NotApplicable,
}

impl<T> Observed<T> {
    /// True when a value is present.
    #[must_use]
    pub const fn is_present(&self) -> bool {
        matches!(self, Self::Present(_))
    }

    /// True when the axis was looked for and not found.
    #[must_use]
    pub const fn is_not_observed(&self) -> bool {
        matches!(self, Self::NotObserved)
    }

    /// True when the axis does not apply.
    #[must_use]
    pub const fn is_not_applicable(&self) -> bool {
        matches!(self, Self::NotApplicable)
    }

    /// Borrow the present value.
    #[must_use]
    pub const fn as_ref(&self) -> Observed<&T> {
        match self {
            Self::Present(v) => Observed::Present(v),
            Self::NotObserved => Observed::NotObserved,
            Self::NotApplicable => Observed::NotApplicable,
        }
    }

    /// Map a present value, carrying absence through unchanged.
    #[must_use]
    pub fn map<U>(self, f: impl FnOnce(T) -> U) -> Observed<U> {
        match self {
            Self::Present(v) => Observed::Present(f(v)),
            Self::NotObserved => Observed::NotObserved,
            Self::NotApplicable => Observed::NotApplicable,
        }
    }
}

impl<T: fmt::Debug> fmt::Display for Observed<T> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Present(v) => write!(f, "{v:?}"),
            Self::NotObserved => f.write_str("not observed"),
            Self::NotApplicable => f.write_str("not applicable"),
        }
    }
}

// ---------------------------------------------------------------------------
// C3 — the claim
// ---------------------------------------------------------------------------

/// A measured or printed fact, with its evidence.
///
/// **Unconstructible without [`Verbatim`] and [`SourceRef`].** The only way to
/// build one is [`Claim::new`], which demands both. There is no `Default`, no
/// `From`, and no public field write. The compile-fail test proves it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Claim {
    value: String,
    quote: Verbatim,
    source: SourceRef,
    condition_verbatim: Option<Verbatim>,
}

impl Claim {
    /// Build a claim. Requires the printed quote and the provenance.
    ///
    /// `value` is held as text here because a claim's *identity* is the string
    /// the document printed; numeric interpretation happens in `dw-normalize`
    /// and is itself a derived, reviewable step.
    ///
    /// # Errors
    ///
    /// Returns [`LawError::Empty`] if `value` is empty, or
    /// [`LawError::MissingProvenance`] if `source` carries no crop (`C13`).
    pub fn new(
        value: impl Into<String>,
        quote: Verbatim,
        source: SourceRef,
    ) -> Result<Self, LawError> {
        let value = value.into();
        if value.is_empty() {
            return Err(LawError::Empty {
                field: "claim value",
            });
        }
        if source.crop.is_none() {
            return Err(LawError::MissingProvenance("source.crop (C13)"));
        }
        Ok(Self {
            value,
            quote,
            source,
            condition_verbatim: None,
        })
    }

    /// The value as printed.
    #[must_use]
    pub fn value(&self) -> &str {
        &self.value
    }

    /// The verbatim quote backing the value.
    #[must_use]
    pub fn quote(&self) -> &Verbatim {
        &self.quote
    }

    /// Where it came from.
    #[must_use]
    pub fn source(&self) -> &SourceRef {
        &self.source
    }

    /// The condition as printed, if the document stated one.
    #[must_use]
    pub fn condition_verbatim(&self) -> Option<&Verbatim> {
        self.condition_verbatim.as_ref()
    }

    /// Attach the printed condition.
    #[must_use]
    pub fn with_condition(mut self, condition: Verbatim) -> Self {
        self.condition_verbatim = Some(condition);
        self
    }
}

// ---------------------------------------------------------------------------
// §16.3 — no unverified promotion
// ---------------------------------------------------------------------------

/// A value that passed `dw-truth` admission and may therefore leave the
/// extractor.
///
/// Newtype wrapper: a raw value cannot be handed to the catalog by accident.
/// Promotion is a deliberate, typed act.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct Admitted<T>(T);

impl<T> Admitted<T> {
    /// The only constructor. Named for the act it records.
    #[must_use]
    pub fn admit(value: T) -> Self {
        Self(value)
    }

    /// Unwrap. The name is the point: you are degrading a guarantee.
    #[must_use]
    pub fn into_inner(self) -> T {
        self.0
    }

    /// Borrow the inner value.
    #[must_use]
    pub fn get(&self) -> &T {
        &self.0
    }
}

// ---------------------------------------------------------------------------
// C12 — conflict is sticky
// ---------------------------------------------------------------------------

/// A claim that two sources disagree on. Sticky by construction.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Conflict {
    /// The competing claims. At least two.
    pub sides: Vec<Claim>,
}

impl Conflict {
    /// Record a disagreement between at least two claims.
    ///
    /// # Errors
    ///
    /// Returns [`LawError::MissingProvenance`] if fewer than two claims are
    /// given: a conflict with one side is not a conflict.
    pub fn new(sides: Vec<Claim>) -> Result<Self, LawError> {
        if sides.len() < 2 {
            return Err(LawError::MissingProvenance("conflict needs >= 2 claims"));
        }
        Ok(Self { sides })
    }
}

/// A claim that survived admission and has not been contradicted.
///
/// There is **no `From<Conflict>`** and no `Conflict::into_validated`. The
/// compile-fail test proves the conversion does not exist.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Validated {
    /// The admitted claim.
    pub claim: Admitted<Claim>,
}

impl Validated {
    /// Wrap an admitted claim as validated.
    #[must_use]
    pub const fn new(claim: Admitted<Claim>) -> Self {
        Self { claim }
    }
}

// ---------------------------------------------------------------------------
// §16.5 — no unsupported exclusion
// ---------------------------------------------------------------------------

/// A candidate excluded from a cohort. Unconstructible without a reason and
/// evidence, so no exclusion can be unsourced.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Ineligible {
    /// Why it was excluded. Non-empty.
    reason: String,
    /// The evidence that justifies the exclusion.
    evidence: Vec<SourceRef>,
}

impl Ineligible {
    /// Exclude a candidate. `reason` must be non-empty; `evidence` must not.
    ///
    /// # Errors
    ///
    /// Returns [`LawError::Empty`] if `reason` is empty, or
    /// [`LawError::UnmotivatedExclusion`] if `evidence` is empty.
    pub fn new(reason: impl Into<String>, evidence: Vec<SourceRef>) -> Result<Self, LawError> {
        let reason = reason.into();
        if reason.is_empty() {
            return Err(LawError::Empty {
                field: "ineligible reason",
            });
        }
        if evidence.is_empty() {
            return Err(LawError::UnmotivatedExclusion);
        }
        Ok(Self { reason, evidence })
    }

    /// Why.
    #[must_use]
    pub fn reason(&self) -> &str {
        &self.reason
    }

    /// On what evidence.
    #[must_use]
    pub fn evidence(&self) -> &[SourceRef] {
        &self.evidence
    }
}

// ---------------------------------------------------------------------------
// C9 — elimination
// ---------------------------------------------------------------------------

/// The verdict on one candidate against one atom.
///
/// [`Verdict::Unknown`] **never eliminates**. See [`Elimination::eliminates`].
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub enum Verdict {
    /// Satisfies the atom under its required conditions.
    Pass,
    /// Fails by less than the stated margin.
    NearMiss,
    /// Eliminated. Always carries a reason and evidence via [`Ineligible`].
    Fail,
    /// Could not evaluate. **Never eliminates.**
    Unknown,
}

impl Verdict {
    /// `C9.1`: only `Fail` eliminates.
    #[must_use]
    pub const fn may_eliminate(self) -> bool {
        matches!(self, Self::Fail)
    }
}

/// The elimination law, exposed as a predicate so callers cannot reimplement
/// it inconsistently.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Elimination;

impl Elimination {
    /// A candidate is eliminated only by a `Fail`.
    ///
    /// `Unknown` is not a failure to evaluate *against* the candidate — it is
    /// a failure to evaluate *at all*, and it says nothing about the candidate.
    #[must_use]
    pub const fn eliminates(v: Verdict) -> bool {
        v.may_eliminate()
    }
}

// ---------------------------------------------------------------------------
// C10 — embed law
// ---------------------------------------------------------------------------

/// Identity of the model weights a vector was computed with.
///
/// A second embedder is unrepresentable: [`Embedding`] cannot exist without
/// one of these, and vectors with different fingerprints must never be mixed.
#[derive(Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct WeightFingerprint(String);

impl WeightFingerprint {
    /// Pin the fleet to specific weights.
    ///
    /// # Errors
    ///
    /// Returns [`LawError::Empty`] if the fingerprint is empty.
    pub fn new(raw: impl Into<String>) -> Result<Self, LawError> {
        let s = raw.into();
        if s.is_empty() {
            return Err(LawError::Empty {
                field: "weight fingerprint",
            });
        }
        Ok(Self(s))
    }

    /// Borrow the fingerprint.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for WeightFingerprint {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "WeightFingerprint({}…)", &self.0[..self.0.len().min(12)])
    }
}

/// A vector that knows which weights produced it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Embedding {
    vector: Vec<f32>,
    fingerprint: WeightFingerprint,
}

impl Embedding {
    /// Embed a vector under a known fingerprint.
    ///
    /// # Errors
    ///
    /// Returns [`LawError::Empty`] if the vector is empty.
    pub fn new(vector: Vec<f32>, fingerprint: WeightFingerprint) -> Result<Self, LawError> {
        if vector.is_empty() {
            return Err(LawError::Empty {
                field: "embedding vector",
            });
        }
        Ok(Self {
            vector,
            fingerprint,
        })
    }

    /// The vector.
    #[must_use]
    pub fn vector(&self) -> &[f32] {
        &self.vector
    }

    /// Which weights produced it.
    #[must_use]
    pub fn fingerprint(&self) -> &WeightFingerprint {
        &self.fingerprint
    }

    /// `C10`: vectors from different generations must never be compared.
    #[must_use]
    pub fn comparable_with(&self, other: &Self) -> bool {
        self.fingerprint == other.fingerprint
    }
}

// ---------------------------------------------------------------------------
// §16.2 — one release per answer
// ---------------------------------------------------------------------------

/// Which immutable publication an answer came from.
#[derive(Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct ReleaseId(String);

impl ReleaseId {
    /// Identify a release.
    ///
    /// # Errors
    ///
    /// Returns [`LawError::Empty`] if the identifier is empty.
    pub fn new(raw: impl Into<String>) -> Result<Self, LawError> {
        let s = raw.into();
        if s.is_empty() {
            return Err(LawError::Empty {
                field: "release id",
            });
        }
        Ok(Self(s))
    }

    /// Borrow the identifier.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for ReleaseId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "ReleaseId({})", self.0)
    }
}

impl fmt::Display for ReleaseId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

// ---------------------------------------------------------------------------
// units and quantities
// ---------------------------------------------------------------------------

/// Canonical unit for a physical quantity. Conversion happens in
/// `dw-normalize`; this crate only refuses to compare across units.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub enum Unit {
    /// Volts.
    Volt,
    /// Amperes.
    Ampere,
    /// Ohms.
    Ohm,
    /// Watts.
    Watt,
    /// Hertz.
    Hertz,
    /// Degrees Celsius.
    Celsius,
    /// Seconds.
    Second,
    /// Metres.
    Metre,
    /// Grams.
    Gram,
    /// Coulombs.
    Coulomb,
    /// Farads.
    Farad,
    /// Henrys.
    Henry,
    /// Dimensionless.
    None,
}

/// A number with its unit and the tolerance used to compare it.
///
/// `f64` throughout. Equality is never `==` on floats; comparisons go through
/// [`Quantity::approx_eq`] with an explicit, stored tolerance.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Quantity {
    value: f64,
    unit: Unit,
}

impl Quantity {
    /// Absolute tolerance used by [`Self::approx_eq`] when none is given.
    pub const DEFAULT_TOLERANCE: f64 = 1e-9;

    /// A measured magnitude in a canonical unit.
    #[must_use]
    pub const fn new(value: f64, unit: Unit) -> Self {
        Self { value, unit }
    }

    /// The magnitude.
    #[must_use]
    pub const fn value(&self) -> f64 {
        self.value
    }

    /// The unit.
    #[must_use]
    pub const fn unit(&self) -> Unit {
        self.unit
    }

    /// Tolerant equality. Units must match; magnitudes are compared with an
    /// explicit absolute tolerance. `clippy::float_cmp` is denied workspace-wide
    /// precisely so that every comparison lands here.
    #[must_use]
    pub fn approx_eq(&self, other: &Self, tolerance: f64) -> bool {
        self.unit == other.unit && (self.value - other.value).abs() <= tolerance
    }

    /// Tolerant equality at [`Self::DEFAULT_TOLERANCE`].
    #[must_use]
    pub fn approx_eq_default(&self, other: &Self) -> bool {
        self.approx_eq(other, Self::DEFAULT_TOLERANCE)
    }
}

// ---------------------------------------------------------------------------
// vocabularies — C6, frozen and additive-only
// ---------------------------------------------------------------------------

/// How strongly a printed value is guaranteed. `C6`, additive-only.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub enum Rating {
    /// Absolute maximum. Never satisfies an operating atom (`C9.2`).
    AbsMax,
    /// Recommended operating condition.
    Recommended,
    /// A guaranteed limit.
    RatedLimit,
    /// Typical. Never becomes guaranteed (`C9.5`).
    Typical,
    /// Transient rating.
    Transient,
    /// Surge rating.
    Surge,
    /// Not classified. Never eliminates.
    Unknown,
}

impl Rating {
    /// `C9.2`: an absolute maximum is a survival boundary, not an operating
    /// guarantee, so it can never satisfy an operating atom.
    #[must_use]
    pub const fn may_satisfy_operating_atom(self) -> bool {
        !matches!(self, Self::AbsMax | Self::Unknown)
    }
}

/// Ordering of a comparison. Distinct from [`Rating`] on purpose: "minimum 500 V"
/// is a guaranteed floor on a breakdown spec, not a different class of rating.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub enum Qualifier {
    /// The value is a minimum.
    Min,
    /// The value is a maximum.
    Max,
    /// The value is typical.
    Typ,
    /// The value is the only one given.
    Exact,
}

// ---------------------------------------------------------------------------
// deterministic serialization
// ---------------------------------------------------------------------------

/// String-keyed map with sorted iteration.
///
/// `BTreeMap` rather than `HashMap` on purpose: `HashMap` iteration order must
/// never reach serialized output (PLAN §10). This alias is the reminder.
pub type SortedMap<K, V> = BTreeMap<K, V>;

#[cfg(test)]
#[allow(clippy::expect_used, clippy::unwrap_used)]
mod tests {
    use super::*;

    fn sha() -> Sha256 {
        Sha256::new("a".repeat(64)).expect("valid digest")
    }

    fn source() -> SourceRef {
        SourceRef::page(sha(), 2).with_crop(CropRef {
            crop_sha256: sha(),
            dpi: 144,
            bbox: [0, 0, 100, 20],
        })
    }

    #[test]
    fn sha256_rejects_bad_hex() {
        assert!(Sha256::new("z".repeat(64)).is_err());
        assert!(Sha256::new("A".repeat(64)).is_err());
        assert!(Sha256::new("a".repeat(63)).is_err());
        assert!(Sha256::new("a".repeat(64)).is_ok());
    }

    #[test]
    fn verbatim_rejects_empty() {
        assert!(Verbatim::new("").is_err());
        assert!(Verbatim::new("x").is_ok());
    }

    #[test]
    fn claim_requires_provenance() {
        let quote = Verbatim::new("VDSS 600 V").expect("non-empty");
        let bare = SourceRef::page(sha(), 2);
        assert!(matches!(
            Claim::new("600", quote.clone(), bare),
            Err(LawError::MissingProvenance(_))
        ));
        assert!(Claim::new("600", quote, source()).is_ok());
    }

    #[test]
    fn claim_rejects_empty_value() {
        let quote = Verbatim::new("x").expect("non-empty");
        assert!(matches!(
            Claim::new("", quote, source()),
            Err(LawError::Empty { .. })
        ));
    }

    #[test]
    fn observed_distinguishes_the_three_states() {
        let present: Observed<u32> = Observed::Present(3);
        let missing: Observed<u32> = Observed::NotObserved;
        let na: Observed<u32> = Observed::NotApplicable;
        assert!(present.is_present());
        assert!(missing.is_not_observed());
        assert!(na.is_not_applicable());
        assert_ne!(missing, na);
    }

    #[test]
    fn unknown_never_eliminates() {
        assert!(!Verdict::Unknown.may_eliminate());
        assert!(!Verdict::Pass.may_eliminate());
        assert!(!Verdict::NearMiss.may_eliminate());
        assert!(Verdict::Fail.may_eliminate());
        assert!(Elimination::eliminates(Verdict::Fail));
        assert!(!Elimination::eliminates(Verdict::Unknown));
    }

    #[test]
    fn absmax_never_satisfies_operating() {
        assert!(!Rating::AbsMax.may_satisfy_operating_atom());
        assert!(!Rating::Unknown.may_satisfy_operating_atom());
        assert!(Rating::RatedLimit.may_satisfy_operating_atom());
        assert!(Rating::Typical.may_satisfy_operating_atom());
    }

    #[test]
    fn conflict_needs_two_sides() {
        let quote = Verbatim::new("x").expect("non-empty");
        let one = Claim::new("600", quote.clone(), source()).expect("claim");
        let two = Claim::new("650", quote, source()).expect("claim");
        assert!(Conflict::new(vec![one.clone()]).is_err());
        assert!(Conflict::new(vec![one, two]).is_ok());
    }

    #[test]
    fn ineligible_requires_reason_and_evidence() {
        assert!(Ineligible::new("", vec![source()]).is_err());
        assert!(Ineligible::new("no CAN", vec![]).is_err());
        assert!(Ineligible::new("no CAN", vec![source()]).is_ok());
    }

    #[test]
    fn embeddings_from_different_weights_are_not_comparable() {
        let a = WeightFingerprint::new("bge-large-en-v1.5").expect("fp");
        let b = WeightFingerprint::new("bge-m3").expect("fp");
        let e1 = Embedding::new(vec![0.0, 1.0], a.clone()).expect("emb");
        let e2 = Embedding::new(vec![1.0, 0.0], b).expect("emb");
        let e3 = Embedding::new(vec![1.0, 0.0], a).expect("emb");
        assert!(!e1.comparable_with(&e2));
        assert!(e1.comparable_with(&e3));
    }

    #[test]
    fn quantity_never_uses_float_eq() {
        let a = Quantity::new(0.1 + 0.2, Unit::Volt);
        let b = Quantity::new(0.3, Unit::Volt);
        assert!(a.approx_eq_default(&b));
        assert!(!a.approx_eq(&Quantity::new(0.3, Unit::Ampere), 1e-9));
    }

    #[test]
    fn claim_serialization_is_deterministic() {
        let quote = Verbatim::new("VDSS 600 V").expect("non-empty");
        let claim = Claim::new("600", quote, source()).expect("claim");
        let once = serde_json::to_string(&claim).expect("json");
        let twice = serde_json::to_string(&claim).expect("json");
        assert_eq!(once, twice);
    }

    #[test]
    fn admitted_is_the_only_promotion_path() {
        let quote = Verbatim::new("x").expect("non-empty");
        let claim = Claim::new("600", quote, source()).expect("claim");
        let admitted = Admitted::admit(claim);
        assert_eq!(admitted.get().value(), "600");
    }
}
