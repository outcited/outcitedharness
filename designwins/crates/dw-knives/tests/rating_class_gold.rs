//! Gold: rating-class projection is 1:1 with the live reference.
//!
//! Regenerate (D8: the reference is the spec, fixtures sample it):
//!
//! ```text
//! python3 - <<'EOF' > tests/fixtures/gold/rating_class_projection_v1.json
//! import json, sys; sys.path.insert(0, ".")
//! from harness.electronics.rating_class import rating_class, RATING_CLASSES
//! battery = [...]  # see git history for the full battery
//! json.dump({"classes": list(RATING_CLASSES), "battery": [...]}, ...)
//! EOF
//! ```
//!
//! The battery pins the quirks on purpose: `"VDS(transient)"` projects to
//! `unknown` in the reference (its `\b` after `)` never matches) and must do
//! so here — the gold is the spec, not the docstring.

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    reason = "gold tests assert on their own fixtures"
)]

use std::path::PathBuf;

use dw_knives::{rating_class, AxisSpec, RatingClass, AXES};

fn gold_path() -> PathBuf {
    let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    while !dir.join("tests/fixtures/gold").is_dir() {
        assert!(dir.pop(), "could not locate tests/fixtures/gold");
    }
    dir.join("tests/fixtures/gold/rating_class_projection_v1.json")
}

fn gold() -> serde_json::Value {
    serde_json::from_str(&std::fs::read_to_string(gold_path()).expect("gold present"))
        .expect("gold parses")
}

/// Every `(symbol, parameter, table_kind, qualifier)` tuple projects exactly as
/// the live reference projects it.
#[test]
fn projection_battery_matches_the_reference() {
    let g = gold();
    let ours_names: Vec<&str> = [
        RatingClass::AbsoluteMaximum,
        RatingClass::Recommended,
        RatingClass::RatedLimit,
        RatingClass::Typical,
        RatingClass::Transient,
        RatingClass::Surge,
        RatingClass::Unknown,
    ]
    .iter()
    .map(|c| c.as_str())
    .collect();
    assert_eq!(
        g["classes"],
        serde_json::json!(ours_names),
        "class set diverged"
    );
    for case in g["battery"].as_array().unwrap() {
        let got = rating_class(
            case["symbol"].as_str(),
            case["parameter"].as_str(),
            case["table_kind"].as_str(),
            case["quantity_qualifier"].as_str(),
        );
        assert_eq!(
            got.as_str(),
            case["class"].as_str().unwrap(),
            "case {case} projected differently"
        );
    }
}

/// Negative controls — the guards the class exists for.
#[test]
fn negative_control_class_guards() {
    // The continuous veto: pulse vocabulary glued to a continuous row does
    // not make the row transient.
    assert_eq!(
        rating_class(Some("IDM"), Some("continuous current"), None, None),
        RatingClass::Unknown
    );
    // The documented quirk, pinned: the reference's trailing \b never fires
    // after `)`. If this starts returning `Transient`, the regex drifted.
    assert_eq!(
        rating_class(Some("VDS(transient)"), None, None, None),
        RatingClass::Unknown,
        "reference quirk: the parenthesised pulse alternative never matches"
    );
    // Fail-closed: nothing typed, nothing claimed.
    assert_eq!(rating_class(None, None, None, None), RatingClass::Unknown);
    // "operating" rescues `rated` into recommended.
    assert_eq!(
        rating_class(Some("ID"), Some("Operating"), None, Some("rated")),
        RatingClass::Recommended
    );
}

/// The class gate is the reference's `evaluate_claim` gate, axis by axis.
#[test]
fn class_gate_matches_the_reference() {
    fn spec(axis: dw_decide::Axis) -> &'static AxisSpec {
        AXES.iter().find(|s| s.axis == axis).unwrap()
    }
    // rds_on accepts only rated_limit/typical/unknown — an absmax RDS claim
    // cannot speak (that is C9.2 in the registry).
    let rds = spec(dw_decide::Axis::RdsOnOhm);
    assert!(!rds.accepts(RatingClass::AbsoluteMaximum));
    assert!(rds.accepts(RatingClass::Typical));
    assert!(!rds.accepts(RatingClass::Transient));
    // vds is a survival quantity — absmax claims speak; pulses never do.
    let vds = spec(dw_decide::Axis::VdsRatingV);
    assert!(vds.accepts(RatingClass::AbsoluteMaximum));
    assert!(!vds.accepts(RatingClass::Surge));
}
