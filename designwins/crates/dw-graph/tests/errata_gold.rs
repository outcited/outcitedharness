//! Gold: the errata readers are 1:1 with the live reference.
//!
//! `tests/fixtures/gold/errata_battery_v1.json` is the shared input battery
//! (branch-covering: X-matrix marks, duplicate ids, number-only headings,
//! tableish/note title rejections, truncation at 1200, TI field merging, the
//! check-mark matrix with position-based joins); `errata_deltas_v1.json` is
//! the live reference's output over it. Regenerate with a driver that calls
//! `errata_records` / `clarification_records` / `device_scope` /
//! `ti_advisory_details` / `ti_affected_matrix` on the battery (see git
//! history).

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    clippy::float_cmp,
    reason = "gold tests assert on their own fixtures"
)]

use std::path::PathBuf;

use dw_graph::{
    clarification_records, device_scope, errata_records, ti_advisory_details, ti_affected_matrix,
    TiWord,
};

fn gold_dir() -> PathBuf {
    let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    while !dir.join("tests/fixtures/gold").is_dir() {
        assert!(dir.pop(), "could not locate tests/fixtures/gold");
    }
    dir.join("tests/fixtures/gold")
}

fn load(name: &str) -> serde_json::Value {
    let dir = gold_dir();
    serde_json::from_str(&std::fs::read_to_string(dir.join(name)).unwrap()).unwrap()
}

#[test]
fn all_five_readers_match_the_live_reference() {
    let battery = load("errata_battery_v1.json");
    let gold = load("errata_deltas_v1.json");

    let page_texts: Vec<String> = battery["errata_page_texts"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    let ours = serde_json::to_value(errata_records(&page_texts)).unwrap();
    assert_eq!(ours, gold["errata_records"], "errata_records diverged");

    let clar_pages: Vec<String> = battery["clarification_page_texts"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    let ours = serde_json::to_value(clarification_records(&clar_pages)).unwrap();
    assert_eq!(
        ours, gold["clarification_records"],
        "clarification_records diverged"
    );

    let ours = serde_json::to_value(device_scope(
        battery["device_scope_front_text"].as_str().unwrap(),
        battery["device_scope_filename"].as_str().unwrap(),
    ))
    .unwrap();
    assert_eq!(ours, gold["device_scope"], "device_scope diverged");

    let lines: Vec<String> = battery["ti_lines"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    let ours = serde_json::to_value(ti_advisory_details(&lines)).unwrap();
    assert_eq!(
        ours, gold["ti_advisory_details"],
        "ti_advisory_details diverged"
    );

    let words: Vec<TiWord> = battery["ti_matrix_words"]
        .as_array()
        .unwrap()
        .iter()
        .map(|w| {
            let arr = w.as_array().unwrap();
            TiWord {
                x0: arr[0].as_f64().unwrap(),
                y0: arr[1].as_f64().unwrap(),
                x1: arr[2].as_f64().unwrap(),
                y1: arr[3].as_f64().unwrap(),
                text: arr[4].as_str().unwrap().to_string(),
            }
        })
        .collect();
    let ours = serde_json::to_value(ti_affected_matrix(&[words])).unwrap();
    assert_eq!(
        ours, gold["ti_affected_matrix"],
        "ti_affected_matrix diverged"
    );
}

/// Negative controls — "nothing inferred" is the whole contract.
#[test]
fn negative_control_nothing_inferred() {
    // Prose that is not a numbered issue is not an errata record.
    let recs = errata_records(&["Some free prose about a bug.".into()]);
    assert!(recs.is_empty());
    // A mark without its printed revision grid attributes nothing.
    let recs =
        errata_records(&["2.1.1 A Fine Title\nThe symptom.\nAffected Silicon Revisions\nX".into()]);
    assert_eq!(recs.len(), 1, "debug: {recs:?}");
    assert!(
        recs[0].affected_silicon.is_empty(),
        "unattributable marks withhold"
    );
    // A heading whose title is a table line is not a heading at all — the
    // section never starts and its prose is not an errata record.
    let recs = errata_records(&["2.1.2 12 MHz µA VCC = 3.3 V\nBody line.".into()]);
    assert!(
        recs.is_empty(),
        "tableish title must not open a record: {recs:?}"
    );
}
