//! # dw-recon — document reconnaissance
//!
//! What does a document actually govern? Filename binding lies (CR's
//! `GD32F4xx_User_Manual` names six series and F403 is not one of them);
//! the authoritative grain signal is the **printed scope statement**. This
//! reader returns it verbatim, fail-closed — and never confuses an
//! unreadable page with "the vendor states no scope".
//!
//! * [`document_scope`] — the cover-page scope reader
//!   (`harness/electronics/document_scope.py`, ported 1:1; gold from the
//!   live reference over rendered PDFs, `tests/fixtures/gold/
//!   document_scope_v1.json`).
//!
//! This crate calls no models and is `forbid(unsafe_code)`.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

pub mod document_scope;

pub use document_scope::{read_scope, ScopeRow};
