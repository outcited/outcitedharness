//! Compile-fail proofs for the laws in `docs/CONTRACTS.md`.
//!
//! Each case here is code that *would* violate a law if it compiled. The build
//! failing is the proof. Regenerate expected output with
//! `TRYBUILD=overwrite cargo test --test compile_fail`.

#[test]
fn laws_are_enforced_by_the_type_system() {
    let t = trybuild::TestCases::new();
    t.compile_fail("tests/compile_fail/*.rs");
}
