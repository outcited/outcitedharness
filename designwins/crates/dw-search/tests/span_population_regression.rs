//! Range-valued population regressions (M4 contract gap #1, `hw-1m4` §A3).
//!
//! The historical TSTG defect read the lower printed column `-55` as `175`
//! (sign dropped / best-column wins). These pins make that collapse, and the
//! in-feed `-40 -> +40` class, impossible: both signed endpoints survive.

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    clippy::float_cmp,
    reason = "contract pins fail loudly by design; exact printed values are the point"
)]

use dw_decide::{Axis, Candidate, CandidateFacts, SpanEndpoint, SpanEvidence};
use dw_search::{population_from_rows, ClaimRow, Population, SkipReason};

fn tstg(value: &str, column_header: Option<&str>, unit: Option<&str>) -> ClaimRow {
    ClaimRow {
        part_number: "P1".into(),
        symbol: "TSTG".into(),
        parameter: None,
        table_kind: None,
        qualifier: None,
        value: value.into(),
        column_header: column_header.map(str::to_string),
        unit: unit.map(str::to_string),
    }
}

fn pop(rows: &[ClaimRow]) -> Population {
    population_from_rows(rows)
}

fn facts_of(p: &Population) -> &CandidateFacts {
    p.facts
        .get(&Candidate {
            part_number: "P1".into(),
            vendor: String::new(),
        })
        .expect("P1 must have facts")
}

fn span_of(f: &CandidateFacts) -> &SpanEvidence {
    f.axes
        .get(&Axis::TstgRangeC)
        .expect("TSTG span present")
        .span
        .as_ref()
        .expect("range axis carries span evidence")
}

fn value_of(ep: &SpanEndpoint) -> f64 {
    ep.value
}

/// Regression: `-55 175` keeps **both** signed endpoints — the lower endpoint
/// is `-55`, never collapsed to `175`.
#[test]
fn tstg_range_preserves_both_signed_endpoints() {
    let p = pop(&[tstg("-55 175", None, None)]);
    assert!(p.skipped.is_empty(), "a real range parks nothing");
    let span = span_of(facts_of(&p));
    assert!(
        span.is_complete(),
        "two printed numbers make a two-sided span"
    );
    assert_eq!(value_of(span.min.as_ref().unwrap()), -55.0);
    assert_eq!(value_of(span.max.as_ref().unwrap()), 175.0);
}

/// Regression: the in-feed `-40 -> +40` class keeps the negative sign — it can
/// never become `+40 -> +40`.
#[test]
fn minus_forty_to_plus_forty_does_not_collapse() {
    let p = pop(&[tstg("-40 -> +40", None, None)]);
    let span = span_of(facts_of(&p));
    assert_eq!(value_of(span.min.as_ref().unwrap()), -40.0);
    assert_eq!(value_of(span.max.as_ref().unwrap()), 40.0);
}

/// A printed range whose endpoints are inverted is not a range — it parks.
#[test]
fn inverted_range_parks() {
    let p = pop(&[tstg("175 -> -55", None, None)]);
    assert!(p.facts.is_empty(), "no fact from an inverted range");
    assert_eq!(p.skipped, vec![("TSTG".into(), SkipReason::RangeInvalid)]);
}

/// A non-finite endpoint parks fail-closed.
#[test]
fn non_finite_range_parks() {
    let p = pop(&[tstg("1e999 175", None, None)]);
    assert!(p.facts.is_empty());
    assert_eq!(p.skipped, vec![("TSTG".into(), SkipReason::RangeInvalid)]);
}

/// A printed unit that does not map to the axis's canonical kind parks; the
/// canonical unit (or no unit) passes.
#[test]
fn non_canonical_unit_parks() {
    let p = pop(&[tstg("-55 175", None, Some("v"))]);
    assert!(p.facts.is_empty());
    assert_eq!(
        p.skipped,
        vec![("TSTG".into(), SkipReason::CanonicalUnitMismatch)]
    );

    let ok = pop(&[tstg("-55 175", None, Some("°C"))]);
    assert!(ok.skipped.is_empty(), "the canonical unit is accepted");
    assert!(span_of(facts_of(&ok)).is_complete());
}

/// A single printed number in a `Min`/`Max` column is a partial span (one
/// endpoint), which the engine judges `UNKNOWN` — never a scalar.
#[test]
fn single_bound_with_column_role_is_partial() {
    let min = pop(&[tstg("-55", Some("Min"), None)]);
    let span = span_of(facts_of(&min));
    assert!(span.min.is_some() && span.max.is_none());
    assert!(!span.is_complete());

    let max = pop(&[tstg("175", Some("Max"), None)]);
    let span = span_of(facts_of(&max));
    assert!(span.min.is_none() && span.max.is_some());
}

/// A lone number with no column role cannot be placed as an endpoint — parks.
#[test]
fn single_number_without_column_role_parks() {
    let p = pop(&[tstg("-55", None, None)]);
    assert!(p.facts.is_empty());
    assert_eq!(
        p.skipped,
        vec![("TSTG".into(), SkipReason::RangeUnparseable)]
    );
}
