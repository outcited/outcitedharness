//! §16.5: an `Ineligible` cannot exist without a reason and evidence.
//!
//! If this compiled, a candidate could be excluded with no justification, and
//! "every `Ineligible` carries reason + evidence ref" would be aspirational.

use dw_types::*;

fn main() {
    // Private fields: an unsourced exclusion cannot be assembled.
    let _ineligible = Ineligible {
        reason: String::new(),
        evidence: vec![],
    };
}
