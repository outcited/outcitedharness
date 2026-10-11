//! The population loader's gates and negative controls (`ASSURANCE.md` R2).
//!
//! Each guard gets a case where the old/wrong behaviour would keep the row
//! and the guard must drop it — plus the cases that must survive, so the
//! loader cannot pass by dropping everything.

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    reason = "tests assert on their own fixtures"
)]

use dw_decide::Axis;
use dw_search::{class_of, population_from_rows, search, skip_reason_str, ClaimRow, SkipReason};
use dw_types::ReleaseId;

fn row(symbol: &str, value: &str) -> ClaimRow {
    ClaimRow {
        part_number: "P1".into(),
        symbol: symbol.into(),
        parameter: None,
        table_kind: None,
        qualifier: None,
        value: value.into(),
        column_header: None,
    }
}

/// The guards each drop exactly what they claim to drop — and nothing else
/// survives except real facts.
#[test]
fn loader_guards_drop_exactly_their_class() {
    let mut transient = row("RDSON", "0.1");
    transient.table_kind = Some("absolute_maximum".into());
    let mut wrong_bound = row("TJ", "-55");
    wrong_bound.column_header = Some("Min".into());
    let unparseable = row("VDSS", "n.a.");
    let unknown = row("FOO", "1.0");
    let good = row("VDSS", "40");
    let good_min_column = {
        let mut r = row("VDSS", "60");
        r.column_header = Some("Min".into());
        r
    };

    let pop = population_from_rows(&[
        transient,
        wrong_bound,
        unparseable,
        unknown,
        good.clone(),
        good_min_column.clone(),
    ]);
    assert_eq!(pop.facts.len(), 1, "one candidate");
    let facts = &pop.facts.values().next().unwrap();
    let vds = facts.axes.get(&Axis::VdsRatingV).unwrap();
    assert_eq!(
        vds.value,
        Some(60.0),
        "best claim wins (max), min-column VDSS kept"
    );
    assert!(
        !facts.axes.contains_key(&Axis::TjMaxC),
        "wrong-bound TJ dropped"
    );
    assert!(
        !facts.axes.contains_key(&Axis::RdsOnOhm),
        "absmax RDS dropped"
    );

    let reasons: Vec<SkipReason> = pop.skipped.iter().map(|(_, r)| *r).collect();
    assert!(reasons.contains(&SkipReason::ClassNotAccepted));
    assert!(reasons.contains(&SkipReason::WrongBound));
    assert!(reasons.contains(&SkipReason::UnparseableValue));
    assert!(reasons.contains(&SkipReason::UnknownAxis));
    assert_eq!(pop.skipped.len(), 4, "no row silently vanishes");
    assert_eq!(class_of(&good), dw_knives::RatingClass::Unknown);
}

/// Negative control — the wrong-bound guard is about the column, not the
/// axis: the same `TJ` row from a Max column is a real rating and stays.
#[test]
fn negative_control_wrong_bound_needs_the_min_column() {
    let mut from_min = row("TJ", "150");
    from_min.column_header = Some("Min".into());
    let mut from_max = row("TJ", "150");
    from_max.column_header = Some("Max".into());
    let pop = population_from_rows(&[from_min.clone(), from_max.clone()]);
    let facts = pop.facts.values().next().unwrap();
    assert_eq!(
        facts.axes.get(&Axis::TjMaxC).unwrap().value,
        Some(150.0),
        "the Max-column claim survives"
    );
    assert_eq!(pop.skipped.len(), 1);
    assert_eq!(pop.skipped[0].1, SkipReason::WrongBound);
}

/// The front door runs the real funnel over the loaded population.
#[test]
fn search_runs_the_funnel_over_the_population() {
    let rows = vec![row("VDSS", "40"), row("ID", "2"), {
        let mut r = row("VDSS", "60");
        r.part_number = "P2".into();
        r
    }];
    let pop = population_from_rows(&rows);
    let registry = dw_knives::seed_from_measured_chips();
    let release = ReleaseId::new("rel-test").unwrap();
    let resp = search("bluetooth sensor", &registry, &pop, release.clone());
    assert_eq!(resp.build.release, release);
    assert_eq!(resp.build.cohort.len(), 2, "distinct parts in, cohort out");
    assert!(
        !resp.residue.is_empty() || !resp.build.unknowns.is_empty(),
        "unmapped input is reported, never silent (D5)"
    );
    let _ = skip_reason_str(SkipReason::WrongBound);
}
