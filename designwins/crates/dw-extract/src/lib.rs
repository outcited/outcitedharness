//! # dw-extract — the extraction surface
//!
//! Document in, structured facts out. Port of the Python extraction surface:
//! `harness/electronics/extraction.py`, `bom_extract.py`,
//! `key_features_grid.py` (see `docs/MIGRATION.md`; the gate is *extraction
//! gold*).
//!
//! The API surface is the implementer's design, chosen to mirror the
//! reference's observable behaviour — not to improve it. Read `HANDOFF.md`
//! in this crate before writing a line, and `docs/ASSURANCE.md` before
//! writing a check: every gate ships with a negative control.
//!
//! This crate calls no models and is `forbid(unsafe_code)`.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

pub mod bom;
pub mod canonical;
pub mod claims;
pub mod corpus;
pub mod grid;
pub mod queue;
pub mod sha256;

pub use canonical::{canonical_json, stable_id, CanonicalError};
pub use claims::{make_claim, ClaimSpec, FactClaim};

/// Extraction-surface errors.
#[derive(Debug, thiserror::Error)]
pub enum ExtractError {
    /// Canonical-JSON encoding failed (non-finite, range, prefix).
    #[error("canonical encoding: {0}")]
    Canonical(String),
    /// A claim spec violates the reference's claim contract.
    #[error("invalid claim: {0}")]
    InvalidClaim(String),
    /// A corpus registry fails `corpus.verify_corpus_registry`.
    #[error("corpus registry: {0}")]
    Registry(String),
    /// Input JSON is malformed or of the wrong shape.
    #[error("malformed input: {0}")]
    Malformed(String),
    /// File-system or digest verification failure in a fixture corpus.
    #[error("integrity: {0}")]
    Integrity(String),
}
