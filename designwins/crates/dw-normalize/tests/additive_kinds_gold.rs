//! D10 — additive kinds gold (same-commit law: `DECISIONS.md` entry + this
//! fixture change land together).
//!
//! `condition_model_additive_kinds_v1.jsonl` is generated from the amended
//! Python reference (`harness/electronics/condition_model.py`, D8: a fixture
//! is a sample of the reference). Every row must round-trip byte-parity.
//!
//! The NEGATIVE rows are the ruling's teeth: cross-kind units (`VGS = 10 nC`),
//! the G4 mW-on-ohm case (`RG = 5 mW`), the dual-use `TF = 50 MHz`, and L1
//! bare numbers (`QG = 25`) must never type. A gate that types those is
//! inventing facts and fails here first.
//!
//! # Lints
//!
//! Comparison harness — same allowances as `gold_condition.rs`.
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
    dir.join("tests/fixtures/gold/condition_model_additive_kinds_v1.jsonl")
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
fn additive_kinds_gold_all_rows() {
    let text = std::fs::read_to_string(gold_path()).expect("gold present");
    let rows: Vec<GoldRow> = text
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str(l).expect("row parses"))
        .collect();

    assert_eq!(rows.len(), 18, "D10 gold cardinality");

    let mut failures: Vec<String> = Vec::new();
    let mut negatives = 0usize;
    for (i, row) in rows.iter().enumerate() {
        if row.note.starts_with("NEGATIVE") {
            negatives += 1;
        }
        let outcome = parse_condition(&row.condition_verbatim);
        let actual = serde_json::to_value(&outcome).expect("serialises");
        if !values_match(&canonical(&actual), &canonical(&row.expected)) {
            failures.push(format!(
                "row {i} verbatim={:?} note={:?}\n    expected {}\n    actual   {}",
                row.condition_verbatim,
                row.note,
                serde_json::to_string(&canonical(&row.expected)).expect("json"),
                serde_json::to_string(&canonical(&actual)).expect("json"),
            ));
        }
    }
    if !failures.is_empty() {
        for f in &failures {
            eprintln!("{f}");
        }
    }
    assert!(
        failures.is_empty(),
        "D10 gold divergence ({} rows)",
        failures.len()
    );
    assert_eq!(negatives, 4, "the four negatives are present");
}

/// NEGATIVE CONTROL, executable form: the rows the ruling forbids must come
/// out as `Rejected`, never `Typed` — even if someone regenerates the gold
/// with the wrong expectation, this holds the line.
#[test]
fn negative_control_cross_kind_never_types() {
    for verbatim in ["VGS = 10 nC", "RG = 5 mW", "TF = 50 MHz", "QG = 25"] {
        let outcome = parse_condition(verbatim);
        assert!(
            matches!(outcome, ParseOutcome::Rejected { .. }),
            "{verbatim:?} must never type — got {outcome:?}"
        );
    }
}

/// The new kinds DO type — the positive half of the same coin.
#[test]
fn positive_control_new_kinds_type() {
    for (verbatim, key) in [
        ("QG = 25 nC", "qg_coulomb"),
        ("CISS = 120 pF", "ciss_farad"),
        ("tf = 100 ns", "tf_second"),
        ("PTOT = 2.5 W", "ptot_watt"),
        ("EAS = 200 mJ", "eas_joule"),
    ] {
        let outcome = parse_condition(verbatim);
        let json = serde_json::to_value(&outcome).expect("serialises");
        let typed = json.get("keys").and_then(|k| k.get(key));
        assert!(typed.is_some(), "{verbatim:?} must type {key}: {json}");
    }
}
