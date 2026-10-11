//! Gold: the axis registry is 1:1 with the live reference.
//!
//! Regenerate (D8: the reference is the spec, fixtures sample it):
//!
//! ```text
//! python3 - <<'EOF' > tests/fixtures/gold/axis_registry_v1.json
//! import json, sys; sys.path.insert(0, ".")
//! from harness.discovery.evaluator import AXES, axis_for_symbol
//! battery = [...]  # see git history for the full battery
//! json.dump({"axes": {...}, "battery": {...}}, sys.stdout, indent=2, sort_keys=True)
//! EOF
//! ```
//!
//! The battery includes the normalization cases (`RDS_ON_MAX`, `VDS_S`,
//! `TJ_MAX`) and the negatives (`IDMAX`, `TSTGMAX`, `FOO`, empty) — the
//! negatives are the control: a tokenizer that guesses cannot pass.

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::float_cmp,
    clippy::indexing_slicing,
    reason = "gold tests assert on their own fixtures"
)]

use std::path::PathBuf;

use dw_knives::{axis_for_symbol, norm_symbol, AXES};

fn gold_path() -> PathBuf {
    let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    while !dir.join("tests/fixtures/gold").is_dir() {
        assert!(dir.pop(), "could not locate tests/fixtures/gold");
    }
    dir.join("tests/fixtures/gold/axis_registry_v1.json")
}

fn axis_name(a: dw_decide::Axis) -> &'static str {
    match a {
        dw_decide::Axis::VdsRatingV => "vds_rating_v",
        dw_decide::Axis::IdContinuousA => "id_continuous_a",
        dw_decide::Axis::RdsOnOhm => "rds_on_ohm",
        dw_decide::Axis::TjMaxC => "tj_max_c",
        dw_decide::Axis::TstgRangeC => "tstg_range_c",
        other => panic!("the five-axis registry never mentions {other:?}"),
    }
}

fn op_name(op: dw_decide::Op) -> &'static str {
    match op {
        dw_decide::Op::MinRating => "min_rating",
        dw_decide::Op::MaxRating => "max_rating",
        dw_decide::Op::Covers => "covers",
    }
}

fn gold() -> serde_json::Value {
    serde_json::from_str(&std::fs::read_to_string(gold_path()).expect("gold present"))
        .expect("gold parses")
}

/// The registry is 5 axes, 1:1 — same symbols, same ops, same class filters,
/// same near-miss bands, same Min-column policy as the reference.
#[test]
fn five_axes_one_to_one() {
    let g = gold();
    let axes = g["axes"].as_object().unwrap();
    assert_eq!(AXES.len(), 5);
    assert_eq!(axes.len(), 5);
    for spec in AXES {
        let ref_spec = &axes[axis_name(spec.axis)];
        let mut ours: Vec<String> = spec.symbols.iter().copied().map(norm_symbol).collect();
        let mut theirs: Vec<String> = ref_spec["symbols"]
            .as_array()
            .unwrap()
            .iter()
            .map(|s| norm_symbol(s.as_str().unwrap()))
            .collect();
        ours.sort_unstable();
        theirs.sort_unstable();
        assert_eq!(ours, theirs, "symbols differ on {:?}", spec.axis);
        assert_eq!(op_name(spec.op), ref_spec["op"].as_str().unwrap());
        let ours_classes: Option<Vec<&str>> =
            spec.accepts_classes.map(<[&str]>::to_vec).map(|mut v| {
                v.sort_unstable();
                v
            });
        let theirs_classes: Option<Vec<&str>> = ref_spec["accepts_classes"].as_array().map(|a| {
            let mut v: Vec<&str> = a.iter().map(|x| x.as_str().unwrap()).collect();
            v.sort_unstable();
            v
        });
        assert_eq!(
            ours_classes, theirs_classes,
            "classes differ on {:?}",
            spec.axis
        );
        assert_eq!(
            spec.default_near_miss_pct,
            ref_spec["default_near_miss_pct"].as_f64().unwrap()
        );
        assert_eq!(
            spec.min_column_evidence,
            ref_spec["min_column_evidence"].as_bool().unwrap(),
            "Min-column policy differs on {:?}",
            spec.axis
        );
    }
}

/// Every symbol in the battery maps exactly as the live reference maps it —
/// including the negatives, which must stay `None`. A mapping that guesses
/// cannot pass this.
#[test]
fn symbol_battery_matches_the_reference() {
    let g = gold();
    for (symbol, expected) in g["battery"].as_object().unwrap() {
        let ours = axis_for_symbol(symbol).map(axis_name);
        let theirs = expected.as_str();
        assert_eq!(ours, theirs, "symbol {symbol:?} mapped differently");
    }
}

/// Negative control — the normalization is part of the contract.
#[test]
fn negative_control_symbol_normalization() {
    assert_eq!(
        axis_for_symbol("RDS(ON)MAX"),
        axis_for_symbol("RDS_ON_MAX"),
        "parentheses and underscores must fold together"
    );
    assert_eq!(axis_for_symbol("VDS_S"), axis_for_symbol("vdss"));
    assert_eq!(
        axis_for_symbol("IDMAX"),
        None,
        "unknown symbols stay unknown"
    );
    assert_eq!(axis_for_symbol(""), None);
    // Cross-axis guard: TJ is its own axis, never the storage span.
    assert_ne!(
        axis_for_symbol("TJ"),
        axis_for_symbol("TSTG"),
        "junction and storage are different quantities (truth-audit C1)"
    );
}
