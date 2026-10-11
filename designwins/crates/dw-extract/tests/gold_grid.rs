//! Gold gate: the Key Features grid, against the live `build_grid` reference.
//!
//! Gate: `build_grid` over each case's record reproduces the reference's grid
//! dict exactly (floats with tolerance) — rows, tiers, dedupe, typed facts,
//! `quantity_qualifiers` counters included.
//!
//! Negative control (ASSURANCE R2): a mutated expected row must be detected.

// Test target: fixture indexing (`case["expected"][0]`) and unwraps on
// fixture data are the documented test-module allowance from HANDOFF.md
// ("Test modules may carry a documented #[allow]"); the library lint is not
// weakened by this file.
#![allow(clippy::unwrap_used, clippy::expect_used, clippy::indexing_slicing)]

mod common;

use std::collections::BTreeMap;

use common::{diff_gold, load_gold};
use dw_extract::grid::build_grid;

/// Gate: Rust `build_grid` == Python `build_grid`.
/// Failure condition (R1): any case's grid diverges — e.g. a row on the
/// wrong tier after `_grid_eligible` drift, or a dedupe that dropped a page.
#[test]
fn grid_gold_reproduces_reference_output() {
    let cases = load_gold("extract_grid_gold_v1.jsonl");
    assert!(!cases.is_empty(), "fixture must not be empty");
    for case in &cases {
        let name = case["case"].as_str().unwrap();
        let actual = build_grid(&case["record"], Option::<&BTreeMap<String, String>>::None)
            .unwrap_or_else(|e| panic!("{name}: {e}"));
        let expected = &case["expected"];
        assert!(
            diff_gold(expected, &actual, "grid").is_none(),
            "{name}: {}\nexpected: {expected}\nactual:   {actual}",
            diff_gold(expected, &actual, "grid").unwrap_or_default()
        );
    }
}

/// Negative control: the comparator must detect an injected fault.
#[test]
fn negative_control_mutated_row_is_detected() {
    let cases = load_gold("extract_grid_gold_v1.jsonl");
    let case = cases
        .iter()
        .find(|c| !c["expected"]["rows"].as_array().unwrap().is_empty())
        .expect("need one case with rows");
    let actual = build_grid(&case["record"], Option::<&BTreeMap<String, String>>::None).unwrap();
    let mut mutated = case["expected"].clone();
    mutated["rows"][0]["tier"] = serde_json::json!("below_grid");
    assert!(
        diff_gold(&mutated, &actual, "grid").is_some(),
        "the gate must be able to fail when a row tier is mutated"
    );
}
