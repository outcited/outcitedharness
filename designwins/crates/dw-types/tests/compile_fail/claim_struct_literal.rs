//! C3: `Claim` cannot be assembled by hand.
//!
//! The fields are private and there is no `Default` or `From`. If this
//! compiled, a claim could exist without its quote or its provenance, and
//! "every number opens to the page it came from" would be a comment again.

use dw_types::*;

fn main() {
    let quote = Verbatim::new("VDSS 600 V").unwrap();
    let sha = Sha256::new("a".repeat(64)).unwrap();
    let source = SourceRef::page(sha, 2);

    // Neither of these may compile.
    let _claim = Claim {
        value: "600".into(),
        quote,
        source,
        condition_verbatim: None,
    };
}
