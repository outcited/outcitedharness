//! C3 / PRD-NORMALIZE §6.3: a `TypedCondition` cannot exist without its
//! provenance anchor.
//!
//! `verbatim` is a [`dw_types::Verbatim`], not a `String`. That is the
//! mechanical form of "every number opens to the page it came from": a raw
//! string cannot stand in for evidence, so this does not compile.

use dw_normalize::TypedCondition;

fn main() {
    let _ = TypedCondition {
        keys: Default::default(),
        mode: None,
        approx: Default::default(),
        sym: Default::default(),
        bare: Default::default(),
        comparators: vec![],
        residue: vec![],
        verbatim: String::from("VGS = 0 V"),
    };
}
