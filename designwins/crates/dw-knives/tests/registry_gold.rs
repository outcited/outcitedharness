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
        dw_decide::Axis::VgsRatingV => "vgs_rating_v",
        dw_decide::Axis::VgsThV => "vgs_th_v",
        dw_decide::Axis::VsdV => "vsd_v",
        dw_decide::Axis::IdssA => "idss_a",
        dw_decide::Axis::IgssA => "igss_a",
        dw_decide::Axis::IsA => "is_a",
        dw_decide::Axis::RgOhm => "rg_ohm",
        dw_decide::Axis::RthjaCperw => "rthja_c_per_w",
        dw_decide::Axis::SleepCurrentA => "sleep_current_a",
        other => panic!("registry_gold has no name for {other:?}"),
    }
}

fn op_name(op: dw_decide::Op) -> &'static str {
    match op {
        dw_decide::Op::MinRating => "min_rating",
        dw_decide::Op::MaxRating => "max_rating",
        dw_decide::Op::Covers => "covers",
        dw_decide::Op::AtMost => "at_most",
    }
}

fn gold() -> serde_json::Value {
    serde_json::from_str(&std::fs::read_to_string(gold_path()).expect("gold present"))
        .expect("gold parses")
}

/// The PORTED five are 1:1 — same symbols, same ops, same class filters,
/// same near-miss bands, same Min-column policy as the reference. Additive
/// axes (discovered-then-curated, Sam mandate 2026-10-12) are checked
/// separately and never touch this parity.
#[test]
fn five_axes_one_to_one() {
    let g = gold();
    let axes = g["axes"].as_object().unwrap();
    assert_eq!(axes.len(), 5);
    assert!(AXES.len() >= 5, "the reference five are always present");
    // The five are the first five (declaration order is observable: question
    // ranking breaks ties on it) and the gold iterates exactly them.
    for spec in &AXES[..5] {
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

/// The additive batch (Sam mandate 2026-10-12: every extractable attribute is
/// a knife — a niche attribute still carves the cohort under semantic search).
/// These eight resolve to the FROZEN unit kinds (dw-normalize) — the
/// Coulomb/Farad/Second batch (QG/CISS/TF families, 804 symbols measured)
/// waits on the additive unit kinds and is queued as a law extension.
#[test]
fn additive_eight_registered() {
    let want: &[(&str, &str)] = &[
        ("vgs_rating_v", "min_rating"),
        ("vgs_th_v", "max_rating"),
        ("vsd_v", "max_rating"),
        ("idss_a", "max_rating"),
        ("igss_a", "max_rating"),
        ("is_a", "min_rating"),
        ("rg_ohm", "max_rating"),
        ("rthja_c_per_w", "max_rating"),
        // 2026-10-19 slice: measured 75.1% substrate coverage (2,354/3,136
        // mode-row docs carry sleep-family modes, burn 2026-10-19).
        ("sleep_current_a", "at_most"),
    ];
    for (name, op) in want {
        let spec = AXES
            .iter()
            .find(|s| axis_name(s.axis) == *name)
            .unwrap_or_else(|| panic!("additive axis {name} missing from AXES"));
        assert_eq!(op_name(spec.op), *op, "op drift on {name}");
        assert!(!spec.symbols.is_empty(), "{name} carries no symbols");
    }
    assert_eq!(AXES.len(), 14, "five ported + eight additive + sleep");
}
