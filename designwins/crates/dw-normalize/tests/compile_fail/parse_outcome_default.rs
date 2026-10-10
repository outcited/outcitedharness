//! PRD-NORMALIZE §6.3: `ParseOutcome` has no silent fallback.
//!
//! A `Default` would let a caller manufacture "typed" output without parsing,
//! which is the fail-open path the whole crate exists to prevent.

use dw_normalize::ParseOutcome;

fn main() {
    let outcome = ParseOutcome::default();
    let _ = outcome;
}
