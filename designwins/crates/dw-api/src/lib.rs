//! # dw-api — the D7 delivery gate
//!
//! Seven routes, every response renderable end to end. Not resource endpoints
//! a UI assembles into an experience — a rendering substrate
//! (`docs/DECISIONS.md` **D7**, `docs/CONTRACTS.md` **C7**):
//!
//! ```text
//! GET  /healthz
//! GET  /api/extraction/v1/parts/{part_number}
//! GET  /api/discovery/v1/designs/{design_id}
//! GET  /api/pipeline/v1/jobs/{job_id}
//! POST /api/search/v1/query
//! GET  /api/coverage/v1/cube
//! GET  /api/coverage/v1/gaps
//! ```
//!
//! ## The D7 law, and how this crate makes it hard to break
//!
//! A response that is logically correct but omits evidence links, hides
//! `unknowns`, or needs assembling **fails**. So:
//!
//! * every response embeds [`feed::QueryResult`] — the seven-item feed block —
//!   flattened, so `release_id` and the feed are structurally present, never
//!   "added by convention";
//! * the feed's fields are **not** `Option` and are **never** skipped when
//!   empty on the wire: `unknowns: []` is a statement, a missing key is a lie;
//! * [`feed::FeedAudit`] re-checks the seven items after serialisation, and
//!   its tests carry negative controls (drop an item → the audit must fail);
//! * errors are [`problem::Problem`] — `application/problem+json`, and
//!   renderable: what happened and what to do about it.
//!
//! ## Data
//!
//! Handlers are pure functions over [`store::ApiStore`]. The shipped
//! [`store::DemoStore`] is fixture-backed and in-memory — no corpus
//! dependency, per the handoff — so a front end can hammer the wire shape
//! today while a catalog-backed store slots in behind the same trait.
//!
//! This crate calls no models and is `forbid(unsafe_code)`.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

pub mod feed;
pub mod problem;
pub mod routes;
pub mod store;

pub use feed::{EvidenceAsset, EvidenceRef, FeedAudit, Lineage, Query, QueryResult};
pub use problem::Problem;
pub use routes::{handle, parse_route, Request, Response, Route};
pub use store::{ApiStore, DemoStore, JobRecord, JobState, PartRecord};
