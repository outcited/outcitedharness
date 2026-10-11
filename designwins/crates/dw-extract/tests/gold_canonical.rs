//! Gold gate: canonical JSON + stable id, against live-Python vectors.
//!
//! Gate: every `extract_canonical_gold_v1.jsonl` case must reproduce the
//! reference's `canonical_json` bytes and `stable_id` exactly — this is what
//! pins claim ids to Python's `hashlib`.
//!
//! Negative control (ASSURANCE R2): flipping one byte of a payload must
//! change the reproduced digest — otherwise the gate compares nothing.

// Test target: fixture indexing (`case["expected"][0]`) and unwraps on
// fixture data are the documented test-module allowance from HANDOFF.md
// ("Test modules may carry a documented #[allow]"); the library lint is not
// weakened by this file.
#![allow(clippy::unwrap_used, clippy::expect_used, clippy::indexing_slicing)]

mod common;

use common::{diff_gold, load_gold};
use dw_extract::{canonical_json, stable_id};
use serde_json::Value;

/// Gate: Rust canonical encoding == Python canonical encoding, byte for byte.
/// Failure condition (R1): any `canonical_hex` mismatch, e.g. Rust's
/// `1.5e-8` float spelling vs Python's `1.5e-08`.
#[test]
fn canonical_gold_reproduces_python_bytes_and_ids() {
    let cases = load_gold("extract_canonical_gold_v1.jsonl");
    assert!(!cases.is_empty(), "fixture must not be empty");
    let mut checked = 0;
    for case in &cases {
        let name = case["case"].as_str().unwrap();
        let payload = &case["payload"];
        let hex = hex::encode(&canonical_json(payload).unwrap());
        let expected_hex = case["canonical_hex"].as_str().unwrap();
        assert_eq!(hex, expected_hex, "{name}: canonical bytes diverge");
        let id = stable_id("claim", payload).unwrap();
        let expected_id = case["stable_claim_id"].as_str().unwrap();
        assert_eq!(id, expected_id, "{name}: stable_id diverges");
        checked += 1;
    }
    assert_eq!(checked, cases.len());
}

/// Negative control: the digest must move when the payload does.
#[test]
fn negative_control_payload_mutation_changes_digest() {
    let cases = load_gold("extract_canonical_gold_v1.jsonl");
    let case = &cases[0];
    let mut payload = case["payload"].clone();
    if let Value::Object(map) = &mut payload {
        map.insert("field".to_string(), Value::String("mutated".to_string()));
    }
    let hex = hex::encode(&canonical_json(&payload).unwrap());
    assert_ne!(
        hex,
        case["canonical_hex"].as_str().unwrap(),
        "the gate must be able to fail on a one-field mutation"
    );
}

/// Gate: the value list round-trips structurally (guards fixture drift).
#[test]
fn gold_fixture_values_match_wire_payloads() {
    for case in load_gold("extract_canonical_gold_v1.jsonl") {
        let from_payload = &case["payload"]["value"];
        let value = &case["value"];
        assert!(
            diff_gold(value, from_payload, "value").is_none(),
            "fixture self-consistency: {}",
            case["case"].as_str().unwrap()
        );
    }
}

/// Minimal hex encoder (no dev-dependencies in this crate).
mod hex {
    const HEX: [char; 16] = [
        '0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'a', 'b', 'c', 'd', 'e', 'f',
    ];

    /// Lowercase hex of `bytes`.
    pub fn encode(bytes: &[u8]) -> String {
        let mut out = String::with_capacity(bytes.len() * 2);
        for b in bytes {
            out.push(HEX[(b >> 4 & 0xf) as usize]);
            out.push(HEX[(b & 0xf) as usize]);
        }
        out
    }
}
