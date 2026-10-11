//! Gold gate: claim minting — `iter_ground_truth_claims` /
//! `iter_pinout_row_claims` assembly, against the live Python reference.
//!
//! Gate: the claim wires (including `claim_id`, which is `stable_id` over the
//! identity payload) reproduce the reference exactly. This is the differential
//! check that pins claim identity to Python's `hashlib`.
//!
//! Negative control (ASSURANCE R2): a mutated field must change the claim id
//! — otherwise identity is not being computed.

// Test target: fixture indexing (`case["expected"][0]`) and unwraps on
// fixture data are the documented test-module allowance from HANDOFF.md
// ("Test modules may carry a documented #[allow]"); the library lint is not
// weakened by this file.
#![allow(clippy::unwrap_used, clippy::expect_used, clippy::indexing_slicing)]

mod common;

use std::collections::BTreeMap;

use common::{diff_gold, load_gold};
use dw_extract::queue::{ground_truth_claims, pinout_row_claims, GroundTruthRecord};

/// Gate: Rust claim assembly == Python claim assembly, wire for wire.
/// Failure condition (R1): any claim's `claim_id` or field set diverges —
/// e.g. the entity fallback ("unknown" vendor) or unit suffix drifting.
#[test]
fn claims_gold_reproduces_reference_wire() {
    let cases = load_gold("extract_claims_gold_v1.jsonl");
    assert!(!cases.is_empty(), "fixture must not be empty");
    for case in &cases {
        let name = case["case"].as_str().unwrap();
        let created_at = case["created_at"].as_str().unwrap();
        // Pydantic `mode="json"` renders the UTC datetime with a `Z`; the
        // claim wire carries that exact string.
        let wire_created_at = case["expected"][0]["created_at"].as_str().unwrap();
        let actual: Vec<serde_json::Value> = match case["kind"].as_str().unwrap() {
            "ground_truth" => {
                let record = GroundTruthRecord {
                    document: &case["document"],
                    summary: &case["summary"],
                    value: &case["value"],
                    stem: case["stem"].as_str().unwrap().to_string(),
                };
                let source_uri = case["source_uri"].as_str().unwrap();
                ground_truth_claims(&record, source_uri, wire_created_at)
                    .unwrap_or_else(|e| panic!("{name}: {e}"))
                    .iter()
                    .map(dw_extract::FactClaim::to_wire)
                    .collect()
            }
            "pinout" => {
                let mut uris: BTreeMap<String, String> = BTreeMap::new();
                for (k, v) in case["pdf_uris"].as_object().unwrap() {
                    uris.insert(k.clone(), v.as_str().unwrap().to_string());
                }
                pinout_row_claims(&case["example"], &uris, wire_created_at)
                    .unwrap_or_else(|e| panic!("{name}: {e}"))
                    .iter()
                    .map(dw_extract::FactClaim::to_wire)
                    .collect()
            }
            other => panic!("unknown claim fixture kind {other}"),
        };
        let expected = case["expected"].as_array().unwrap();
        assert_eq!(
            expected.len(),
            actual.len(),
            "{name}: claim count diverges (created_at={created_at})"
        );
        for (i, (e, a)) in expected.iter().zip(&actual).enumerate() {
            assert!(
                diff_gold(e, a, &format!("claim[{i}]")).is_none(),
                "{name}: {}\nexpected: {e}\nactual:   {a}",
                diff_gold(e, a, &format!("claim[{i}]")).unwrap_or_default()
            );
        }
    }
}

/// Negative control: the identity must move when the payload does.
#[test]
fn negative_control_field_mutation_changes_claim_id() {
    let cases = load_gold("extract_claims_gold_v1.jsonl");
    let case = &cases[0];
    let wire_created_at = case["expected"][0]["created_at"].as_str().unwrap();
    let record = GroundTruthRecord {
        document: &case["document"],
        summary: &case["summary"],
        value: &case["value"],
        stem: case["stem"].as_str().unwrap().to_string(),
    };
    let source_uri = case["source_uri"].as_str().unwrap();
    let claims = ground_truth_claims(&record, source_uri, wire_created_at).unwrap();
    assert!(!claims.is_empty());
    let mut mutated_value = case["value"].clone();
    mutated_value["clock_specs"]["max_freq_mhz"] = serde_json::json!(999);
    let mutated_record = GroundTruthRecord {
        document: &case["document"],
        summary: &case["summary"],
        value: &mutated_value,
        stem: case["stem"].as_str().unwrap().to_string(),
    };
    let mutated = ground_truth_claims(&mutated_record, source_uri, wire_created_at).unwrap();
    assert_ne!(
        claims[0].claim_id, mutated[0].claim_id,
        "the gate must be able to fail when a value is mutated"
    );
}
