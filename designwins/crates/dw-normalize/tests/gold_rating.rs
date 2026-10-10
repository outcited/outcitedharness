//! Gate N1 (second half) — rating-class gold parity.
//!
//! `tests/fixtures/gold/rating_class_v1.jsonl` is the executable specification
//! of `rating_class`. 63 rows. Every projection must match.
//!
//! **This fixture is where the hard cases live.** The distribution is not
//! flat: `unknown` is the second-largest class, and `transient` fires *inside*
//! absolute-maximum tables on symbol vocabulary. A naive `table_kind`
//! projection gets both wrong, which is precisely why the class exists.
//!
//! | class | n |
//! |---|---:|
//! | `unknown` (fail-closed) | 17 |
//! | `absolute_maximum` | 16 |
//! | `rated_limit` | 11 |
//! | `typical` | 9 |
//! | `transient` | 6 |
//! | `recommended` | 3 |
//! | `surge` | 1 |
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

use dw_normalize::{rating_class, RatingClass, RatingEvidence};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
struct GoldRow {
    #[allow(dead_code)]
    symbol: String,
    #[allow(dead_code)]
    parameter: String,
    table_kind: String,
    quantity_qualifier: Option<String>,
    #[allow(dead_code)]
    value: serde_json::Value,
    #[allow(dead_code)]
    unit: Option<String>,
    #[allow(dead_code)]
    verbatim: String,
    table_title: String,
    expected_rating_class: String,
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
        .join("rating_class_v1.jsonl")
}

#[test]
fn gold_rating_class_63_of_63() {
    let text = std::fs::read_to_string(gold_path()).expect("gold present");
    let rows: Vec<GoldRow> = text
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str(l).expect("row parses"))
        .collect();

    assert_eq!(rows.len(), 63, "gold cardinality");

    let mut passed = 0usize;
    let mut failures: Vec<String> = Vec::new();
    let mut by_class: std::collections::BTreeMap<&str, (usize, usize)> =
        std::collections::BTreeMap::default();

    for (i, row) in rows.iter().enumerate() {
        let evidence = RatingEvidence {
            symbol: &row.symbol,
            parameter: &row.parameter,
            table_kind: &row.table_kind,
            quantity_qualifier: row.quantity_qualifier.as_deref(),
            table_title: &row.table_title,
        };
        let actual = rating_class(&evidence);
        let entry = by_class
            .entry(row.expected_rating_class.as_str())
            .or_default();
        entry.1 += 1;

        if actual.as_str() == row.expected_rating_class {
            passed += 1;
            entry.0 += 1;
        } else {
            failures.push(format!(
                "row {i} symbol={:?} parameter={:?} table={:?} qualifier={:?}\n\
                 \x20   expected {}\n\
                 \x20   actual   {}",
                row.symbol,
                row.parameter,
                row.table_kind,
                row.quantity_qualifier,
                row.expected_rating_class,
                actual.as_str(),
            ));
        }
    }

    for f in failures.iter().take(20) {
        eprintln!("{f}");
    }
    if failures.len() > 20 {
        eprintln!("… and {} more", failures.len() - 20);
    }

    eprintln!("\ngold rating_class_v1: {passed}/63");
    for (class, (ok, total)) in &by_class {
        eprintln!("  {class:<20} {ok}/{total}");
    }

    assert_eq!(
        passed, 63,
        "Gate N1 requires 63/63. Divergence is a ledger entry (D2), never a re-specification."
    );
}

/// The class set is closed and additive-only (`C6`). Every gold label must map
/// to a variant, so adding a vocabulary term without a variant is a test failure.
#[test]
fn rating_class_vocabulary_is_closed() {
    for label in [
        "absolute_maximum",
        "recommended",
        "rated_limit",
        "typical",
        "transient",
        "surge",
        "unknown",
    ] {
        let found = RatingClass::ALL.iter().any(|c| c.as_str() == label);
        assert!(found, "{label} has no RatingClass variant");
    }
    assert_eq!(RatingClass::ALL.len(), 7);
}
