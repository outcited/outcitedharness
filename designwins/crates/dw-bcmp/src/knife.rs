//! The declared, honored knives for the BCMP pilot — now sourced from the
//! shared `dw-knives` registry.
//!
//! This module is a thin re-export: the authoritative [`KnifeSpec`] table
//! (including `substrate_field`) lives in [`dw_knives::knife`] so every
//! consumer shares one law (F-03 registry generalisation, `hw-1m4` §C8).
//! `dw-bcmp` no longer owns a private copy; the local table was deleted only
//! after a golden parity test proved `knife_spec_json()` byte-equivalent.
//!
//! Behaviour, including the parked axis `temp_range_c` and its documented
//! reason, is preserved exactly.

pub use dw_knives::knife::{knife_spec_json, spec_for, KnifeSpec, KNIFE_SPEC, KNIFE_SPEC_VERSION};
