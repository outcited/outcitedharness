//! C12: a `Conflict` has no path to `Validated`.
//!
//! Conflict is sticky. If this compiled, contradicted evidence could be
//! laundered into accepted truth by a `From` impl or a public field write.

use dw_types::*;

fn main() {
    let quote = Verbatim::new("VDSS 600 V").unwrap();
    let sha = Sha256::new("a".repeat(64)).unwrap();
    let source = SourceRef::page(sha, 2).with_crop(CropRef {
        crop_sha256: sha,
        dpi: 144,
        bbox: [0, 0, 10, 10],
    });
    let a = Claim::new("600", quote.clone(), source.clone()).unwrap();
    let b = Claim::new("650", quote, source).unwrap();
    let conflict = Conflict::new(vec![a, b]).unwrap();

    // Neither of these conversions may exist.
    let validated: Validated = conflict.into();
    let _ = validated;
}
