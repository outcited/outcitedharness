//! Gold: ordering-code decoders are 1:1 with the live reference.
//!
//! Regenerate (D8: the reference is the spec):
//!
//! ```text
//! python3 - <<'EOF' > tests/fixtures/gold/ordering_codes_v1.json
//! import json, sys; sys.path.insert(0, ".")
//! from harness.electronics.ordering_codes import decode, source_label
//! battery = [...]  # see git history — includes the honest negatives
//! json.dump({"battery": [...]}, ...)
//! EOF
//! ```
//!
//! The battery carries a positive for every scheme and the negatives that
//! must stay empty (ambiguous families like `MSP430`, the excluded STM32
//! branches, garbage). A decoder that guesses cannot pass.

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    reason = "gold tests assert on their own fixtures"
)]

use std::path::PathBuf;

use dw_graph::{decode, source_label, OrderingFacts};

fn gold_path() -> PathBuf {
    let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    while !dir.join("tests/fixtures/gold").is_dir() {
        assert!(dir.pop(), "could not locate tests/fixtures/gold");
    }
    dir.join("tests/fixtures/gold/ordering_codes_v1.json")
}

/// Every part decodes exactly as the live reference decodes it — including
/// the parts that must decode to nothing.
#[test]
fn battery_matches_the_live_reference() {
    let gold: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(gold_path()).unwrap()).unwrap();
    for case in gold["battery"].as_array().unwrap() {
        let part = case["part"].as_str();
        let ours = decode(part);
        let ours_value = serde_json::to_value(&ours).unwrap();
        assert_eq!(
            ours_value, case["decoded"],
            "part {part:?} decoded differently"
        );
        assert_eq!(
            source_label(&ours, "flash_kb"),
            case["label"].as_str().unwrap(),
            "provenance tag differs for {part:?}"
        );
    }
}

/// Negative controls — the two-source rule's honesty cases.
#[test]
fn negative_control_only_unambiguous_schemes_decode() {
    // Ambiguous families decode to nothing: returning None is the honest
    // answer (the reference's own docstring rule).
    assert_eq!(decode(Some("MSP430G2553")), OrderingFacts::default());
    assert_eq!(decode(Some("PIC16F877A")), OrderingFacts::default());
    // The excluded STM32 branches never fall through to the ST pattern.
    assert_eq!(decode(Some("STM32WB55CGU6")), OrderingFacts::default());
    assert_eq!(decode(Some("STM32MP157")), OrderingFacts::default());
    // ST pins are nominal — the corroborating field, never pin_count.
    let st = decode(Some("STM32F103C8T6"));
    assert_eq!(st.pin_count, None);
    assert_eq!(st.pin_count_nominal, Some(48));
    assert_eq!(decode(None), OrderingFacts::default());
    assert_eq!(decode(Some("  ")), OrderingFacts::default());
}
