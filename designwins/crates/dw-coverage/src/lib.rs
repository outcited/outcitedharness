//! # dw-coverage — coverage as a product
//!
//! What we know we don't know. Three surfaces, per `PLAN.md` §5.8/§7:
//!
//! * [`manifest`] — the 19-pillar ontology + 8-state machine +
//!   `build_coverage_manifest`, ported 1:1 from
//!   `harness/electronics/coverage.py`. States never exaggerate: `VALIDATED`
//!   needs a grounded teacher admission; `CONFLICT` is sticky.
//! * [`cube`] — the rollup cube (`category × aisle × vendor`) with `laned`
//!   honesty, curve availability, D9's denominators, and the gap ledger.
//! * [`gaps`] — the gap ledger: `acquire | extract | lane | axis`, each with
//!   what, why, and impact weight.
//!
//! The trust equation (`PLAN` §7): *complete corpus × honest knowledge of
//! gaps = a cohort you can defend.*
//!
//! This crate calls no models and is `forbid(unsafe_code)`.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

pub mod cube;
pub mod gaps;
pub mod manifest;

pub use cube::{
    rollup, CoverageCube, CubeSlice, Curves, Denominator, Documents, KnivesCoverage, KnivesWire,
    PillarRollup, SliceInputs,
};
pub use gaps::{derive_gaps, Gap, GapKind};
pub use manifest::{
    advance, build_coverage_manifest, lanes_of_pillar, pillar_ids, Attempt, Candidate,
    CapabilityPillar, CapabilityPillarEntry, DocumentEntry, LocalResult, Manifest, PillarEntry,
    PillarOntologyEntry, PillarSpec, Summary, VerifiedOutcome, WorkItem, CAPABILITY_PILLARS,
    PILLARS, STATES,
};
