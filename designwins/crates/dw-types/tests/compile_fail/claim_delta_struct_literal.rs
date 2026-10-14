//! S4: `ClaimDelta` cannot be assembled by hand.
//!
//! Same law as `Claim` (C3): the fields are private, there is no `Default`
//! or `From`. If this compiled, an errata delta could exist without the base
//! document it corrects — a change with no origin — and elimination would
//! act on rumors.

use dw_types::*;

fn main() {
    let quote = Verbatim::new("ID dropped from 20 A to 18 A").unwrap();
    let sha = Sha256::new("a".repeat(64)).unwrap();
    let source = SourceRef::page(sha.clone(), 3);

    // This may not compile.
    let _delta = ClaimDelta {
        part: "BSC009".into(),
        attribute: "ID".into(),
        new_value: "18 A".into(),
        base_doc: sha,
        base_rev_code: None,
        quote,
        source,
    };
}
