//! Gate N4a — corpus-scale condition parity against `catalog.db`.
//!
//! `PLAN.md` Week 1 gate: **"87K claim diff = 0 unexplained."** The substrate
//! is `extract-results/catalog.db`, table `conditions_typed`, which holds 9,551
//! printed conditions with the reference parser's typed output alongside.
//!
//! | corpus | rows | what it proves |
//! |---|---:|---|
//! | `condition_model_v1.jsonl` (N1) | 167 | the laws, by hand-picked cases |
//! | `condition_typed_catalog_v1.jsonl` (**this**) | **9,551** | the same laws over everything the factory actually saw |
//!
//! The 167-row fixture is the *specification*. This is the *drift detector*.
//! A parser can pass a curated set and diverge on the tail — that is the whole
//! reason this gate exists.
//!
//! ## Note on `parser_version`
//!
//! The corpus was produced by `condition_model.v1.1` while the gold fixture is
//! v1. If the two disagree, this test is where it shows up. A divergence is a
//! ledger entry (`dw-parity`, D2), **never a silent re-specification** of
//! `dw-normalize`.
//!
//! ## Float comparison
//!
//! Structure is compared exactly. Numerics use a tight relative tolerance,
//! because the corpus was serialised compactly — the same finding recorded in
//! `gold_condition.rs` (see `PRD-NORMALIZE` §7).
//!
//! # Lints
//!
//! Comparison harness: indexes, unwraps, asserts freely. Those restrictions
//! are kept in library code and are noise here.
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
struct CorpusRow {
    condition_verbatim: String,
    expected: serde_json::Value,
    #[allow(dead_code)]
    parser_version: String,
    #[allow(dead_code)]
    row_count: u64,
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

fn corpus_path() -> PathBuf {
    repo_root()
        .join("tests")
        .join("fixtures")
        .join("gold")
        .join("condition_typed_catalog_v1.jsonl")
}

/// Structural equality with numeric tolerance. See `gold_condition.rs`.
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

#[test]
fn gold_condition_typed_catalog_9551() {
    let text = std::fs::read_to_string(corpus_path()).expect("corpus present");
    let rows: Vec<CorpusRow> = text
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str(l).expect("row parses"))
        .collect();

    assert_eq!(rows.len(), 9551, "corpus cardinality");

    let mut passed = 0usize;
    let mut failures: Vec<String> = Vec::new();
    let mut typed = 0usize;
    let mut rejected = 0usize;

    for row in &rows {
        let outcome = parse_condition(&row.condition_verbatim);
        match &outcome {
            ParseOutcome::Typed(_) => typed += 1,
            ParseOutcome::Rejected { .. } => rejected += 1,
        }
        let actual = serde_json::to_value(&outcome).expect("serialises");
        if values_match(&canonical(&actual), &canonical(&row.expected)) {
            passed += 1;
        } else {
            failures.push(format!(
                "verbatim={:?}\n    expected {}\n    actual   {}",
                row.condition_verbatim,
                serde_json::to_string(&canonical(&row.expected)).expect("json"),
                serde_json::to_string(&canonical(&actual)).expect("json"),
            ));
        }
    }

    for f in failures.iter().take(15) {
        eprintln!("{f}");
    }
    if failures.len() > 15 {
        eprintln!("… and {} more", failures.len() - 15);
    }

    // Divergence is grouped by class so the report names what is actually
    // wrong rather than dumping 9,000 lines.
    let mut by_kind: std::collections::BTreeMap<&str, usize> =
        std::collections::BTreeMap::default();
    for f in &failures {
        let expected = f.lines().find(|l| l.contains("expected")).unwrap_or("");
        let key = if expected.contains("reject") {
            "reference rejected, we did not"
        } else if expected.contains("\"keys\"") {
            "typed, but differently"
        } else {
            "other"
        };
        *by_kind.entry(key).or_default() += 1;
    }
    eprintln!("\nN4a condition_typed_catalog: {passed}/9551  typed={typed} rejected={rejected}");
    for (k, v) in &by_kind {
        eprintln!("  {k:<36} {v}");
    }

    assert_eq!(
        passed, 9551,
        "Gate N4a requires 9551/9551. Divergence is a ledger entry (D2), never a re-specification."
    );
}

/// The corpus is the same schema as the curated gold — if these ever diverge,
/// one of them is stale.
#[test]
fn corpus_and_gold_agree_on_schema() {
    let text = std::fs::read_to_string(corpus_path()).expect("corpus present");
    let row: CorpusRow =
        serde_json::from_str(text.lines().next().expect("non-empty")).expect("row");
    let outcome = parse_condition(&row.condition_verbatim);
    let actual = serde_json::to_value(&outcome).expect("serialises");
    assert!(
        actual.is_object(),
        "ParseOutcome serialises to a JSON object, same as the curated gold"
    );
}
