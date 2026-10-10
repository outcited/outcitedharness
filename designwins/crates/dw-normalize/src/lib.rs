//! # dw-normalize — typed normalization
//!
//! The layer that makes claims **comparable**. Everything downstream —
//! `dw-truth` admission, `dw-decide` elimination, `dw-knives` axes — consumes
//! its output, which makes it the highest-leverage correctness surface in the
//! workspace.
//!
//! Specification: [`PRD-NORMALIZE`](../../docs/PRD-NORMALIZE.md). Frozen
//! semantics lifted from the Python reference; the gold fixtures are the
//! executable proof.
//!
//! | module | replaces | gold gate |
//! |--------|----------|-----------|
//! | [`condition`] | `condition_model.parse_condition` | `condition_model_v1.jsonl` (167) |
//! | [`coverage`]  | `condition_model.condition_covers` | same fixture |
//! | [`rating`]    | `rating_class.rating_class`, `compare_with_margin` | `rating_class_v1.jsonl` (63) |
//! | [`units`]     | the multiplier tables | property tests |
//!
//! ## Laws
//!
//! | law | enforced by |
//! |-----|-------------|
//! | verbatim never destroyed | [`TypedCondition::verbatim`] is required |
//! | fail closed | [`ParseOutcome`] has exactly two variants |
//! | no invented units | [`Rejection::UnitsAbsent`]; [`units::Scale::Unknown`] |
//! | margin never silent | [`Margin`] has no defaulting constructor |
//! | `Unknown` never eliminates | [`Coverage::may_satisfy`], [`CompareOutcome::Unknown`] |
//! | determinism | `BTreeMap`/`BTreeSet` only |
//! | float discipline | [`coverage::values_agree`] with stored epsilon |
//!
//! This crate is pure: no I/O, no model calls, `forbid(unsafe_code)`.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

pub mod condition;
pub mod coverage;
pub mod rating;
pub mod units;

/// Regex construction lives here and nowhere else in the crate.
///
/// Every pattern is a **compile-time constant**. A failure to compile one is a
/// programming error, not a runtime condition, so failing loudly at first use
/// is correct — the alternative, silently losing a symbol, is exactly the
/// fail-open this crate exists to prevent (`PRD-NORMALIZE` §5.1 L1).
///
/// `clippy::expect_used` is allowed for this module **only**.
#[allow(clippy::expect_used)]
pub(crate) mod re {
    use fancy_regex::Regex;

    /// Build a regex from a literal pattern. Panics on a malformed constant.
    #[must_use]
    pub fn build(pattern: &str) -> Regex {
        Regex::new(pattern).expect("dw-normalize: constant regex must compile")
    }
}

pub use condition::{
    parse_condition, CondValue, ConditionKey, Mode, ParseOutcome, Rejection, TypedCondition,
    CONDITION_MODEL_SCHEMA,
};
pub use coverage::{condition_covers, Coverage, VALUE_EPS};
pub use rating::{
    compare_with_margin, rating_class, CompareOutcome, Margin, RatingClass, RatingEvidence,
    RATING_CLASS_SCHEMA,
};
pub use units::{scale, unit_token, Kind, Scale};
