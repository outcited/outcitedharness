//! # dw-bcmp — the Brand Competitive Map pilot (BCMP-00, issue #29)
//!
//! A thin durable pipeline over the **existing** deterministic engine:
//!
//! ```text
//! scenario (question + proposed atoms)
//!   -> validate against the DECLARED knife spec      (this crate, M5-owned)
//!   -> park unsupported / unverifiable atoms          (never silently drop)
//!   -> canonicalize + dedupe the atom set
//!   -> dw_decide::evaluate over the released cohort   (M4's engine, verbatim)
//!   -> tri-state brand projection (qualified | eliminated | insufficient_evidence)
//!   -> provenance-pinned receipt, replayable
//! ```
//!
//! ## What is owned here and what is not
//!
//! - **Not owned (M4):** `dw-decide` evaluation semantics, the `dw-knives`
//!   registry. This crate does not re-implement judgement; every verdict is
//!   produced by [`dw_decide::evaluate`].
//! - **Owned (M5):** the knife *declaration* for pillar-backed MCU axes
//!   (the `dw-knives` `AXES` table covers power symbols only — registry
//!   generalisation is open work F-03), the evidence admission policy over
//!   the released pillar feed, brand projection, receipts, checkpointing.
//!
//! ## Laws carried (from CONTRACTS/DECISIONS/PROVENANCE)
//!
//! - `Unknown` never eliminates (C9.1) — a brand with missing evidence is
//!   `insufficient_evidence`, never `eliminated`.
//! - An exclusion is unconstructible without evidence (§16.5) — the engine
//!   enforces this; receipts surface the evidence for every elimination.
//! - Nothing calls a model. Nothing guesses.
//! - Dataset mutation is a `replay_required` refusal, not a silent rerun.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

pub mod evidence;
pub mod knife;
pub mod project;
pub mod receipt;
pub mod scenario;

pub use evidence::{load_feed, FeedMeta, LoadedEvidence, VaultProvenance};
pub use knife::{knife_spec_json, KnifeSpec, KNIFE_SPEC, KNIFE_SPEC_VERSION};
pub use project::{project_brands, BrandState};
pub use receipt::{run_batch, BatchOutcome, RunError};

use dw_types::Sha256;

/// Canonical SHA-256 of a byte slice, hex-encoded (delegates to `dw-extract`).
#[must_use]
pub fn sha256_hex(bytes: &[u8]) -> String {
    dw_extract::sha256::sha256_hex(bytes)
}

/// Parse a hex digest into the engine's [`Sha256`], or `None`.
#[must_use]
pub fn parse_sha(s: &str) -> Option<Sha256> {
    Sha256::new(s).ok()
}
