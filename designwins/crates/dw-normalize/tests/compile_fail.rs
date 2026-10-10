//! Gate N3 — compile-fail proofs (`PRD-NORMALIZE` §7).
//!
//! Each case is code that would violate a law if it compiled. The build
//! failing is the proof. Regenerate expected output with
//! `TRYBUILD=overwrite cargo test -p dw-normalize --test compile_fail`.

#[test]
fn laws_are_enforced_by_the_type_system() {
    let t = trybuild::TestCases::new();
    t.compile_fail("tests/compile_fail/*.rs");
}
