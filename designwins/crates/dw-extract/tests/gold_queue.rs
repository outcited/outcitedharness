//! Gold gate: the extraction work queue, against the live
//! `build_extraction_work_queue` reference.
//!
//! Gate: the queue dict — routes, policy, counts and the self-digest —
//! reproduces the reference exactly. The `evidence_sha256` self-digest is a
//! live differential check of `canonical_json` + SHA-256 against Python.
//!
//! Negative control (ASSURANCE R2): a mutated route must be detected.

// Test target: fixture indexing (`case["expected"][0]`) and unwraps on
// fixture data are the documented test-module allowance from HANDOFF.md
// ("Test modules may carry a documented #[allow]"); the library lint is not
// weakened by this file.
#![allow(clippy::unwrap_used, clippy::expect_used, clippy::indexing_slicing)]

mod common;

use common::{diff_gold, load_gold};
use dw_extract::queue::build_extraction_work_queue;

fn registry_of(case: &serde_json::Value) -> serde_json::Value {
    case["registry"].clone()
}

fn aligned_of(case: &serde_json::Value) -> Vec<String> {
    case["aligned"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect()
}

/// Gate: Rust queue == Python queue, self-digest included.
/// Failure condition (R1): any divergence in routes/counts or the digest.
#[test]
fn queue_gold_reproduces_reference_output() {
    let cases = load_gold("extract_queue_gold_v1.jsonl");
    assert!(!cases.is_empty(), "fixture must not be empty");
    for case in &cases {
        let name = case["case"].as_str().unwrap();
        let actual = build_extraction_work_queue(&registry_of(case), &aligned_of(case))
            .unwrap_or_else(|e| panic!("{name}: {e}"));
        let expected = &case["expected"];
        assert!(
            diff_gold(expected, &actual, "queue").is_none(),
            "{name}: {}\nexpected: {expected}\nactual:   {actual}",
            diff_gold(expected, &actual, "queue").unwrap_or_default()
        );
    }
}

/// Negative control: the comparator must detect an injected fault.
#[test]
fn negative_control_mutated_route_is_detected() {
    let cases = load_gold("extract_queue_gold_v1.jsonl");
    let case = &cases[0];
    let actual = build_extraction_work_queue(&registry_of(case), &aligned_of(case)).unwrap();
    let mut mutated = case["expected"].clone();
    mutated["work"][0]["routes"]["pin_or_ball"] =
        serde_json::json!("pymupdf_localize_then_local_text_or_vision");
    assert!(
        diff_gold(&mutated, &actual, "queue").is_some(),
        "the gate must be able to fail when a route is mutated"
    );
}
