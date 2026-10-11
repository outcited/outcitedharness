//! Gold gate: BOM extraction, against the live `bom_extract` reference.
//!
//! Gate: `bom_from_pages` over each case's pages reproduces the reference's
//! `bom_from_document` dict exactly (floats with tolerance).
//!
//! Negative control (ASSURANCE R2): a mutated expected item must be
//! detected — the comparator must be able to go red.

// Test target: fixture indexing (`case["expected"][0]`) and unwraps on
// fixture data are the documented test-module allowance from HANDOFF.md
// ("Test modules may carry a documented #[allow]"); the library lint is not
// weakened by this file.
#![allow(clippy::unwrap_used, clippy::expect_used, clippy::indexing_slicing)]

mod common;

use common::{diff_gold, load_gold};
use dw_extract::bom::{bom_from_pages, PageInput};

fn pages_of(case: &serde_json::Value) -> Vec<PageInput> {
    case["pages"]
        .as_array()
        .unwrap()
        .iter()
        .map(|p| PageInput {
            text: p["text"].as_str().unwrap_or_default().to_string(),
            tables: p["tables"]
                .as_array()
                .unwrap()
                .iter()
                .map(|t| {
                    t.as_array()
                        .unwrap()
                        .iter()
                        .map(|row| {
                            row.as_array()
                                .unwrap()
                                .iter()
                                .map(|cell| cell.as_str().unwrap_or_default().to_string())
                                .collect()
                        })
                        .collect()
                })
                .collect(),
        })
        .collect()
}

/// Gate: Rust BOM output == Python `bom_from_document` output.
/// Failure condition (R1): any case's `expected` diverges — e.g. the flow
/// parser emitting `-source` designators or the table parser dropping rows.
#[test]
fn bom_gold_reproduces_reference_output() {
    let cases = load_gold("extract_bom_gold_v1.jsonl");
    assert!(!cases.is_empty(), "fixture must not be empty");
    for case in &cases {
        let name = case["case"].as_str().unwrap();
        let actual = bom_from_pages(&pages_of(case));
        let expected = &case["expected"];
        assert!(
            diff_gold(expected, &actual, "bom").is_none(),
            "{name}: {}\nexpected: {expected}\nactual:   {actual}",
            diff_gold(expected, &actual, "bom").unwrap_or_default()
        );
    }
}

/// Negative control: the comparator must detect an injected fault.
#[test]
fn negative_control_mutated_item_is_detected() {
    let cases = load_gold("extract_bom_gold_v1.jsonl");
    let case = cases
        .iter()
        .find(|c| c["expected"]["item_count"].as_u64().unwrap_or(0) > 0)
        .expect("need one case with items");
    let actual = bom_from_pages(&pages_of(case));
    let mut mutated = case["expected"].clone();
    mutated["items"][0]["part_number"] = serde_json::json!("FAKE-PART");
    assert!(
        diff_gold(&mutated, &actual, "bom").is_some(),
        "the gate must be able to fail when an item is mutated"
    );
}
