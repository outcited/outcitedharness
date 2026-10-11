//! # dw-extract — the extraction surface
//!
//! Document in, structured facts out. Port of the Python extraction surface:
//! `harness/electronics/extraction.py`, `bom_extract.py`,
//! `key_features_grid.py` (see `docs/MIGRATION.md`; the gate is *extraction
//! gold*).
//!
//! **Scaffold only.** The API surface is the implementer's design, chosen to
//! mirror the reference's observable behaviour — not to improve it. Read
//! `HANDOFF.md` in this crate before writing a line, and
//! `docs/ASSURANCE.md` before writing a check: every gate ships with a
//! negative control.
//!
//! This crate calls no models and is `forbid(unsafe_code)`.

#![forbid(unsafe_code)]
#![deny(missing_docs)]
