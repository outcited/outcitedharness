//! # dw-model — the deterministic model layer
//!
//! The only crate that may call a model (PLAN.md). This crate ports the
//! deterministic halves of `local_model.py`, `cloud_vision.py`, and
//! `frontier_batch.py`:
//!
//! * [`schemas`] — the 8 response schemas (`RESPONSE_SCHEMAS`).
//! * [`validate`] — `validate_response`: recursive type/required walk.
//! * [`parse`] — `parse_json_response`: fence-strip + raw JSON decode.
//! * [`prompt`] — `local_prompt` / `focused_page_context`: byte-identical
//!   instruction strings.
//! * [`bundle`] — `prepare_batch_bundle`: the sealed frontier batch writer.
//!
//! Provider HTTP shells (`LocalExtractionClient`, `cloud_vision`) stay thin
//! and are deliberately not ported here — no oracle pins them.

pub mod bundle;
pub mod parse;
pub mod prompt;
pub mod schemas;
pub mod validate;

pub use bundle::{prepare_batch_bundle, BundleError, BundleSpec, PREPARED_SCHEMA};
pub use parse::parse_json_response;
pub use prompt::{focused_page_context, local_prompt, PromptError};
pub use schemas::{response_schemas, schema_for};
pub use validate::validate_response;
