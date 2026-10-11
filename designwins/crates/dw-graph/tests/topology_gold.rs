//! Gold: `read_topology` answers the hand-labeled truth over 105 real docs.
//!
//! `tests/fixtures/gold/power_topology_v1.jsonl` is **hand-labeled**
//! (`scripts/build_power_topology_fixture.py`): the oracle of what the
//! printed front-matter supports. Topologies compare **as sets** — the
//! labels list them in hand-entry order (verified 2026-10-11: the live
//! reference's own order matches this port byte for byte on the three rows
//! whose label order differs). Integration class and isolation compare
//! exactly. The corpus traps named in the reference docstring (stray figure
//! `boost`, "GSM Modules", `CoolMOS` `LLC`, `non-isolated`, bare `LDO`) are
//! all in it.

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    reason = "gold tests assert on their own fixtures"
)]

use std::path::PathBuf;

use dw_graph::{read_topology, PageText};

fn gold_path() -> PathBuf {
    let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    while !dir.join("tests/fixtures/gold").is_dir() {
        assert!(dir.pop(), "could not locate tests/fixtures/gold");
    }
    dir.join("tests/fixtures/gold/power_topology_v1.jsonl")
}

/// Every one of the 105 corpus rows answers exactly what the reference
/// answers — topology list, integration class, isolation.
#[test]
fn all_rows_match_the_live_reference() {
    let text = std::fs::read_to_string(gold_path()).unwrap();
    let mut ok = 0usize;
    let mut failures = Vec::new();
    for line in text.lines().filter(|l| !l.trim().is_empty()) {
        let row: serde_json::Value = serde_json::from_str(line).unwrap();
        let part = row["part_number"].as_str().unwrap();
        let front = row["front_text"].as_str().unwrap();
        let pages = vec![PageText {
            text: front.to_string(),
            page: 1,
        }];
        let got = read_topology(&pages, Some(part));
        let exp = &row["expected"];
        let mut got_topo = got.topologies.clone();
        let mut want_topo: Vec<String> = exp["topologies"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_str().unwrap().to_string())
            .collect();
        got_topo.sort();
        want_topo.sort();
        let same = got_topo == want_topo
            && got.integration_class.as_deref() == exp["integration_class"].as_str()
            && got.isolated == exp["isolated"].as_bool();
        if same {
            ok += 1;
        } else {
            failures.push(format!(
                "{part}: got topologies={:?} class={:?} iso={:?} | want {}",
                got_topo, got.integration_class, got.isolated, exp
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{ok} ok, {} diverged:\n{}",
        failures.len(),
        failures.join("\n")
    );
    assert_eq!(ok, 105);
}

/// Negative controls — the fail-closed rules the vocabulary exists for.
#[test]
fn negative_control_fail_closed() {
    // Nothing printed: absent answers, never guesses.
    let got = read_topology(
        &[PageText {
            text: "General description".into(),
            page: 1,
        }],
        None,
    );
    assert!(got.topology_absent);
    assert!(got.integration_absent);
    assert!(got.isolated.is_none());
    // "non-isolated" never satisfies isolation (PTH04T241W).
    let got = read_topology(
        &[PageText {
            text: "Non-isolated power module".into(),
            page: 1,
        }],
        None,
    );
    assert_eq!(got.isolated, Some(false));
    // A bare figure-word boost must not flip the answer without a head noun.
    let got = read_topology(
        &[PageText {
            text: "boost efficiency in the application".into(),
            page: 1,
        }],
        None,
    );
    assert!(got.topologies.is_empty(), "stray boost claimed a topology");
    // Body "GSM Modules" never sets integration class.
    let got = read_topology(
        &[PageText {
            text: "GSM Modules application list".into(),
            page: 1,
        }],
        None,
    );
    assert!(got.integration_absent);
}
