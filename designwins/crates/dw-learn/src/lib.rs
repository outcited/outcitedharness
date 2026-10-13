//! # dw-learn — the learning loop
//!
//! Rust owns the loop and the promotion gate; Python owns only the gradient
//! step (`DECISIONS.md` **D3**). This crate ports the two deterministic
//! halves:
//!
//! * [`pair`] — pair materialization, byte-compatible with v1
//!   (`scripts/build_lora_pairs.py`) and v2
//!   (`scripts/build_lora_pairs_v2.py`): quote-native training pairs the
//!   student trains on (contract **C8**).
//! * [`gate`] — the promotion gate (`scripts/lora_gate.py`,
//!   `scripts/gate_v2.py`): field-level precision/recall/f1 over a frozen
//!   holdout with unit normalization, PASS = f1 ≥ 0.90 ∧ precision ≥ 0.95.
//!
//! The pymupdf/sqlite seams (PDF text, burn claims, the PDF index) travel as
//! captured inputs, exactly as the PDF layer does in the recon golds. The
//! gradient step itself is spawned as `python -m harness.training.worker`
//! behind the `HandlerSpec` boundary — not this crate's job.

pub mod gate;
pub mod pair;
pub mod shuffle;

pub use gate::{norm_value, score, GateReport};
pub use pair::{build_pairs_v1, build_pairs_v2};
