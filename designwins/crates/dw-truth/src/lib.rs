//! # dw-truth — the gate
//!
//! Quote verification, admission, and the ladder. This is the crate that turns
//! "a model said so" into "the document says so."
//!
//! Port of `harness/pipeline/{quote_verify,admission,ladder,crop_verify,
//! ball_validate}.py`.
//!
//! ## Why this is the highest-leverage crate in the workspace
//!
//! [`quote`] is the **free, perfect adjudication layer**: it replaces judgment
//! with matching. A claim whose quote is fabricated, whose value does not parse
//! from that quote, or whose header does not govern it, is `P0` — and every one
//! of those is decided by string matching and arithmetic, not by a model.
//!
//! It is also the graduation target for the hallucination ledger: a failure
//! class that this crate catches deterministically never needs model judgment
//! again (`PLAN` §9, "caught classes graduate to deterministic validators").
//!
//! ## Laws
//!
//! | law | enforced by |
//! |-----|-------------|
//! | quote must be verbatim | [`quote::find_quote`] |
//! | value must parse from the quote | [`quote::value_in_quote`] |
//! | wrong cell never binds | [`quote::column_position_binds`], [`quote::header_binds`] |
//! | junction/storage never satisfy ambient | [`quote::temp_convention_check`] |
//! | `Unknown` never eliminates | `dw-types::Verdict` |
//!
//! This crate calls no models and is `forbid(unsafe_code)`.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

pub mod quote;

pub use quote::{
    column_position_binds, find_quote, header_binds, norm, temp_convention_check, tokens,
    value_in_quote, verify_claims, ClaimInput, Field, ReasonCode, Severity, Verdict, VerdictLabel,
    TEMP_WRONG_QUANTITY,
};
