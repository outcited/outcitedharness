//! E5 — claim value-shape gold (G3), against the frozen rejection classes.
//!
//! `claim_value_shapes_v1.jsonl` is generated from the Python reference
//! (D8). Ranges type as `Value::Range`; dash/question/unit-less/comma-decimal
//! shapes fail closed into the named rejection classes. A gate that types
//! `1,2 V` as 1.2 or `?` as a number is inventing facts.
//!
//! # Lints — comparison harness (same allowances as `gold_condition.rs`).
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
use serde_json::Value;

#[derive(Debug, Deserialize)]
struct GoldRow {
    #[allow(dead_code)]
    note: String,
    condition_verbatim: String,
    expected: Value,
}

fn gold_path() -> PathBuf {
    let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    while !dir.join("tests").join("fixtures").join("gold").is_dir() {
        assert!(dir.pop(), "could not locate tests/fixtures/gold");
    }
    dir.join("tests/fixtures/gold/claim_value_shapes_v1.jsonl")
}

fn values_match(a: &Value, b: &Value) -> bool {
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
        (Value::Array(items), Value::Array(other)) => {
            items.len() == other.len()
                && items
                    .iter()
                    .zip(other.iter())
                    .all(|(u, v)| values_match(u, v))
        }
        _ => a == b,
    }
}

fn canonical(v: &Value) -> Value {
    match v {
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let mut out = serde_json::Map::new();
            for k in keys {
                out.insert(k.clone(), canonical(&map[k]));
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().map(canonical).collect()),
        other => other.clone(),
    }
}

#[test]
fn e5_value_shapes_gold_all_rows() {
    let text = std::fs::read_to_string(gold_path()).expect("gold present");
    let rows: Vec<GoldRow> = text
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str(l).expect("row parses"))
        .collect();
    assert_eq!(rows.len(), 12, "E5 gold cardinality");
    let mut failures = Vec::new();
    for (i, row) in rows.iter().enumerate() {
        let actual =
            serde_json::to_value(parse_condition(&row.condition_verbatim)).expect("serialises");
        if !values_match(&canonical(&actual), &canonical(&row.expected)) {
            failures.push(format!(
                "row {i} {:?} note={:?}\n  expected {}\n  actual   {}",
                row.condition_verbatim,
                row.note,
                serde_json::to_string(&canonical(&row.expected)).expect("json"),
                serde_json::to_string(&canonical(&actual)).expect("json"),
            ));
        }
    }
    for f in &failures {
        eprintln!("{f}");
    }
    assert!(
        failures.is_empty(),
        "E5 divergence ({} rows)",
        failures.len()
    );
}

/// NEGATIVE CONTROL — the fail-closed shapes stay untyped even if someone
/// regenerates the gold wrong. Ranges are the only shape that types wide.
#[test]
fn negative_control_shapes_never_type() {
    for verbatim in [
        "TC = 25",
        "VGS = 0 ,VDS = 80",
        "-",
        "–",
        "?",
        "VDS = 1,2 V",
        "TJ,TSTG = -55 to 175",
    ] {
        assert!(
            matches!(parse_condition(verbatim), ParseOutcome::Rejected { .. }),
            "{verbatim:?} must fail closed"
        );
    }
}

/// The range shape types — the positive half.
#[test]
fn positive_control_ranges_type() {
    let outcome = parse_condition("VDS = -0.3 to +45 V");
    let json = serde_json::to_value(&outcome).expect("serialises");
    assert_eq!(json["keys"]["vds_v"], serde_json::json!([-0.3, 45.0]));
}
