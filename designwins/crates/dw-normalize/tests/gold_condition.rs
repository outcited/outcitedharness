//! Gate N1 — gold byte-parity (`PRD-NORMALIZE` §7).
//!
//! `tests/fixtures/gold/condition_model_v1.jsonl` is the executable
//! specification of `parse_condition`. 167 rows. Every one must round-trip
//! identically, field for field.
//!
//! Comparison is on parsed JSON, so map iteration order cannot mask a
//! difference. Empty fields are omitted on both sides, which is why the
//! serialisation uses `skip_serializing_if`.
//!
//! **Roughly one in six printed conditions must be refused.** A run that types
//! everything is inventing facts and must fail here.
//!
//! # Lints
//!
//! This is a comparison harness: it indexes, unwraps, and asserts freely.
//! Those restrictions are kept in `dw-normalize`'s library code and are noise
//! here.
#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    clippy::match_wildcard_for_single_variants,
    clippy::wildcard_enum_match_arm,
    clippy::too_many_lines
)]

use std::path::PathBuf;

use dw_normalize::{parse_condition, ParseOutcome};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
struct GoldRow {
    #[allow(dead_code)]
    vendor: String,
    #[allow(dead_code)]
    stem: String,
    #[allow(dead_code)]
    freq: u64,
    condition_verbatim: String,
    expected: serde_json::Value,
}

fn repo_root() -> PathBuf {
    let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    loop {
        if dir.join("tests").join("fixtures").join("gold").is_dir() {
            return dir;
        }
        assert!(dir.pop(), "could not locate tests/fixtures/gold");
    }
}

fn gold_path() -> PathBuf {
    repo_root()
        .join("tests")
        .join("fixtures")
        .join("gold")
        .join("condition_model_v1.jsonl")
}

#[test]
fn gold_condition_model_167_of_167() {
    let text = std::fs::read_to_string(gold_path()).expect("gold present");
    let rows: Vec<GoldRow> = text
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str(l).expect("row parses"))
        .collect();

    assert_eq!(rows.len(), 167, "gold cardinality");

    let mut passed = 0usize;
    let mut failures: Vec<String> = Vec::new();
    let mut typed = 0usize;
    let mut rejected = 0usize;

    for (i, row) in rows.iter().enumerate() {
        let outcome = parse_condition(&row.condition_verbatim);
        match &outcome {
            ParseOutcome::Typed(_) => typed += 1,
            ParseOutcome::Rejected { .. } => rejected += 1,
        }
        let actual = serde_json::to_value(&outcome).expect("serialises");

        // Compare with sorted keys so map order cannot hide a difference.
        if values_match(&canonical(&actual), &canonical(&row.expected)) {
            passed += 1;
        } else {
            failures.push(format!(
                "row {i} verbatim={:?}\n    expected {}\n    actual   {}",
                row.condition_verbatim,
                serde_json::to_string(&canonical(&row.expected)).expect("json"),
                serde_json::to_string(&canonical(&actual)).expect("json"),
            ));
        }
    }

    if !failures.is_empty() {
        for f in failures.iter().take(20) {
            eprintln!("{f}");
        }
        if failures.len() > 20 {
            eprintln!("… and {} more", failures.len() - 20);
        }
    }

    eprintln!("gold condition_model_v1: {passed}/167  typed={typed} rejected={rejected}");
    assert_eq!(
        passed, 167,
        "Gate N1 requires 167/167. Divergence is a ledger entry (D2), never a re-specification."
    );
}

/// Recursively sort object keys so comparison is order-independent.
fn canonical(v: &serde_json::Value) -> serde_json::Value {
    match v {
        serde_json::Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let mut out = serde_json::Map::new();
            for k in keys {
                out.insert(k.clone(), canonical(&map[k]));
            }
            serde_json::Value::Object(out)
        }
        serde_json::Value::Array(items) => {
            serde_json::Value::Array(items.iter().map(canonical).collect())
        }
        other => other.clone(),
    }
}

/// Structural equality with numeric tolerance.
///
/// **The gold fixture is not bit-exact on floats.** Verified: `15 * 1e-9` is
/// `1.5000000000000002e-08` in *both* Python and Rust, but the gold stores
/// `1.5e-08` — a different f64. The fixture was serialised compactly.
///
/// So Gate N1 means: **structural parity exactly, numeric parity within a
/// tight relative tolerance.** Bit-exactness on floats would be testing the
/// serialiser, not the port. See `PRD-NORMALIZE` §7.
fn values_match(a: &serde_json::Value, b: &serde_json::Value) -> bool {
    use serde_json::Value;
    match (a, b) {
        (Value::Number(x), Value::Number(y)) => {
            let (Some(x), Some(y)) = (x.as_f64(), y.as_f64()) else {
                return x == y;
            };
            let scale = 1.0_f64.max(x.abs()).max(y.abs());
            (x - y).abs() <= 1e-12 * scale
        }
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len()
                && x.iter()
                    .all(|(k, v)| y.get(k).is_some_and(|w| values_match(v, w)))
        }
        (Value::Array(x), Value::Array(y)) => {
            x.len() == y.len() && x.iter().zip(y.iter()).all(|(u, v)| values_match(u, v))
        }
        _ => a == b,
    }
}
