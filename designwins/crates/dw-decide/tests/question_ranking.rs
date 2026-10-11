//! Gold: question ranking — generated from the **live** reference.
//!
//! Regenerate (D8: the reference is the spec, fixtures sample it):
//!
//! ```text
//! python3 scripts/gold_question_ranking.py \
//!   tests/fixtures/gold/question_ranking_population_v1.json \
//!   > tests/fixtures/gold/question_ranking_v1.json
//! ```
//!
//! The population fixture is shared: the script feeds the claim rows to
//! `service.next_question`; this test feeds the equivalent typed facts to
//! `dw_decide::next_questions` and asserts the same answers.

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    clippy::float_cmp,
    clippy::cast_possible_truncation,
    reason = "gold tests assert on their own fixtures"
)]

use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;

use dw_decide::{next_questions, Axis, Candidate, CandidateFacts, Evidence, KnifeId};

fn gold_dir() -> PathBuf {
    let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    while !dir.join("tests/fixtures/gold").is_dir() {
        assert!(dir.pop(), "could not locate tests/fixtures/gold");
    }
    dir.join("tests/fixtures/gold")
}

fn cand(n: &str) -> Candidate {
    Candidate {
        part_number: n.to_string(),
        vendor: "fixture".to_string(),
    }
}

fn facts(pairs: &[(Axis, f64)]) -> CandidateFacts {
    let mut axes = BTreeMap::new();
    for (axis, value) in pairs {
        axes.insert(*axis, Evidence::measured(*value, true, vec![]));
    }
    CandidateFacts {
        axes,
        ..CandidateFacts::default()
    }
}

/// The typed equivalent of `question_ranking_population_v1.json` — same
/// parts, same per-axis values, same one-claim-per-axis shape.
fn population() -> BTreeMap<Candidate, CandidateFacts> {
    let rows: &[(&str, &[(Axis, f64)])] = &[
        (
            "P1",
            &[
                (Axis::VdsRatingV, 40.0),
                (Axis::RdsOnOhm, 0.1),
                (Axis::TstgRangeC, 125.0),
            ],
        ),
        ("P2", &[(Axis::VdsRatingV, 60.0), (Axis::RdsOnOhm, 0.2)]),
        ("P3", &[(Axis::VdsRatingV, 100.0), (Axis::RdsOnOhm, 0.3)]),
        ("P4", &[(Axis::VdsRatingV, 120.0), (Axis::RdsOnOhm, 0.4)]),
        ("P5", &[(Axis::VdsRatingV, 200.0), (Axis::RdsOnOhm, 1.0)]),
        ("P6", &[(Axis::RdsOnOhm, 2.0)]),
        ("P7", &[(Axis::RdsOnOhm, 3.0)]),
    ];
    rows.iter()
        .map(|(p, pairs)| (cand(p), facts(pairs)))
        .collect()
}

fn axis_name(k: &KnifeId) -> &'static str {
    match k {
        KnifeId::Axis(Axis::VdsRatingV) => "vds_rating_v",
        KnifeId::Axis(Axis::IdContinuousA) => "id_continuous_a",
        KnifeId::Axis(Axis::RdsOnOhm) => "rds_on_ohm",
        KnifeId::Axis(Axis::TjMaxC) => "tj_max_c",
        KnifeId::Axis(Axis::TstgRangeC) => "tstg_range_c",
        other => panic!("fixture gold never mentions {other:?}"),
    }
}

/// The gate: the typed port reproduces the live reference's answers, field
/// for field — order, askability, medians, fail counts, percentages, and the
/// verbatim help prose.
#[test]
fn ranking_matches_the_live_reference_gold() {
    let gold: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(gold_dir().join("question_ranking_v1.json"))
            .expect("gold present"),
    )
    .expect("gold parses");
    let pinned = BTreeSet::from([Axis::IdContinuousA]);
    let questions = next_questions(&population(), &pinned);

    let expected = gold["questions"].as_array().expect("gold has questions");
    assert_eq!(
        questions.len(),
        expected.len(),
        "the reference returned {} questions, the port {}",
        expected.len(),
        questions.len()
    );
    for (q, e) in questions.iter().zip(expected) {
        assert_eq!(axis_name(&q.axis), e["axis"].as_str().unwrap());
        assert_eq!(q.askable, e["askable"].as_bool().unwrap());
        if q.askable {
            assert_eq!(
                q.help,
                e["help"].as_str().unwrap(),
                "help prose must be verbatim"
            );
            assert_eq!(
                q.parts_with_claims,
                e["parts_with_claims"].as_u64().unwrap() as usize
            );
            assert_eq!(
                q.proposed_value.unwrap(),
                e["proposed_value"].as_f64().unwrap()
            );
            assert_eq!(
                q.expected_fail_at_median,
                e["expected_fail_at_median"].as_u64().unwrap() as usize
            );
            assert_eq!(
                q.expected_reduction_pct,
                e["expected_reduction_pct"].as_f64().unwrap()
            );
        } else {
            // The reference omits help entirely on unaskable questions.
            assert_eq!(q.parts_with_claims, 0);
            assert_eq!(q.proposed_value, None);
        }
    }
}

/// Negative control — the ranking must follow **measured fails**, not
/// population size. Seven parts on `vds` (all passing the probe) rank below
/// five parts on `rds_on` (two failing it). The old `affected` proxy ranked
/// by population; if that ever comes back, this goes red.
#[test]
fn negative_control_ranking_follows_measured_fails_not_population_size() {
    let rows: &[(&str, &[(Axis, f64)])] = &[
        ("Q1", &[(Axis::VdsRatingV, 50.0), (Axis::RdsOnOhm, 0.1)]),
        ("Q2", &[(Axis::VdsRatingV, 50.0), (Axis::RdsOnOhm, 0.2)]),
        ("Q3", &[(Axis::VdsRatingV, 50.0), (Axis::RdsOnOhm, 0.3)]),
        ("Q4", &[(Axis::VdsRatingV, 50.0), (Axis::RdsOnOhm, 1.0)]),
        ("Q5", &[(Axis::VdsRatingV, 50.0), (Axis::RdsOnOhm, 2.0)]),
        ("Q6", &[(Axis::VdsRatingV, 50.0)]),
        ("Q7", &[(Axis::VdsRatingV, 50.0)]),
    ];
    let pop: BTreeMap<_, _> = rows.iter().map(|(p, f)| (cand(p), facts(f))).collect();
    let questions = next_questions(&pop, &BTreeSet::new());
    assert_eq!(questions.len(), 5, "no axis is pinned, all five are asked");
    let first = &questions[0];
    assert_eq!(
        axis_name(&first.axis),
        "rds_on_ohm",
        "fewer parts, more measured fails"
    );
    assert_eq!(first.expected_fail_at_median, 2);
    assert_eq!(first.expected_reduction_pct, 40.0);
    let second = &questions[1];
    assert_eq!(axis_name(&second.axis), "vds_rating_v");
    assert_eq!(second.expected_fail_at_median, 0);
    assert_eq!(second.parts_with_claims, 7);
}

/// Negative control — ties fall back to registry declaration order (the
/// reference's sort is stable). Two axes with identical measured fails must
/// come out `vds` before `rds_on`, which is `AXES` order in the reference.
#[test]
fn negative_control_ties_keep_registry_order() {
    let rows: &[(&str, &[(Axis, f64)])] = &[
        ("T1", &[(Axis::VdsRatingV, 100.0), (Axis::RdsOnOhm, 0.1)]),
        ("T2", &[(Axis::VdsRatingV, 100.0), (Axis::RdsOnOhm, 0.1)]),
        ("T3", &[(Axis::VdsRatingV, 100.0), (Axis::RdsOnOhm, 0.1)]),
        ("T4", &[(Axis::VdsRatingV, 1.0), (Axis::RdsOnOhm, 10.0)]),
        ("T5", &[(Axis::VdsRatingV, 1.0), (Axis::RdsOnOhm, 10.0)]),
    ];
    let pop: BTreeMap<_, _> = rows.iter().map(|(p, f)| (cand(p), facts(f))).collect();
    let questions = next_questions(&pop, &BTreeSet::new());
    let top_two: Vec<&str> = questions
        .iter()
        .take(2)
        .map(|q| axis_name(&q.axis))
        .collect();
    assert_eq!(
        top_two,
        vec!["vds_rating_v", "rds_on_ohm"],
        "equal fails must fall back to registry order, not flip"
    );
    assert_eq!(questions[0].expected_fail_at_median, 2);
    assert_eq!(questions[1].expected_fail_at_median, 2);
}
