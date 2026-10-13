//! The gate, and its negative controls (`ASSURANCE.md` R2).
//!
//! The threshold is a floor — floors do not discriminate (§1.2) — so the
//! last test asserts the **separation**: every signal proposal scores at or
//! above the threshold and every noise proposal scores strictly below it,
//! with margin on both sides. If the tokenizer, the parser, or the docs
//! change shape, this goes red rather than quietly losing power.

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::float_cmp,
    reason = "tests assert on their own fixtures"
)]

use std::path::PathBuf;

use dw_lessons::{build_index, check, query, DEFAULT_THRESHOLD};

fn docs_dir() -> PathBuf {
    let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    while !dir.join("docs/DECISIONS.md").is_file() {
        assert!(dir.pop(), "could not locate designwins/docs");
    }
    dir.join("docs")
}

fn index() -> Vec<dw_lessons::Lesson> {
    let d = docs_dir();
    let read = |n: &str| std::fs::read_to_string(d.join(n)).expect("docs present");
    build_index(
        &read("DECISIONS.md"),
        &read("ASSURANCE.md"),
        &read("HANDOFF.md"),
    )
}

/// The index must not silently shrink: if a heading format changes, the
/// parser fails here instead of quietly indexing less than the docs hold.
#[test]
fn the_index_carries_every_known_lesson() {
    let idx = index();
    let decisions = idx.iter().filter(|l| l.source == "DECISIONS.md").count();
    let rules = idx.iter().filter(|l| l.id.starts_with('R')).count();
    let defects = idx.iter().filter(|l| l.id.starts_with("1.")).count();
    let gotchas = idx
        .iter()
        .filter(|l| l.source.starts_with("HANDOFF"))
        .count();
    assert_eq!(decisions, 10, "DECISIONS.md must yield D1-D10");
    assert_eq!(rules, 6, "ASSURANCE.md must yield R1-R6");
    assert_eq!(defects, 3, "ASSURANCE.md must yield mechanisms 1.1-1.3");
    assert!(
        gotchas >= 10,
        "HANDOFF §6 gotchas table went quiet: {gotchas}"
    );
    for want in ["D8", "R2", "R5", "1.2"] {
        assert!(idx.iter().any(|l| l.id == want), "missing lesson {want}");
    }
}

/// Proposals that re-walk known failure classes must hit their lessons.
#[test]
fn signal_proposals_find_their_lessons() {
    let idx = index();

    // D8: bending code toward a fixture is exactly "the gold is wrong".
    let hits = query(
        &idx,
        "the curated gold disagrees with my implementation, relax the gate to numeric tolerance",
        5,
    );
    assert!(
        hits.iter().any(|m| m.lesson.id == "D8"),
        "D8 not found: {hits:?}",
    );

    // R5: the live reference outranks any fixture.
    let hits = query(
        &idx,
        "compare against the fixture generated last month instead of running the live reference",
        5,
    );
    assert!(
        hits.iter().any(|m| m.lesson.id == "R5"),
        "R5 not found: {hits:?}",
    );

    // 1.2: floors do not discriminate.
    let hits = query(&idx, "assert coverage >= 0.5 as the bar and ship", 5);
    assert!(
        hits.iter().any(|m| m.lesson.id == "1.2"),
        "1.2 not found: {hits:?}",
    );

    // R2: every gate has a negative control.
    let hits = query(
        &idx,
        "add a gate with no negative control, it cannot go red",
        5,
    );
    assert!(
        hits.iter().any(|m| m.lesson.id == "R2"),
        "R2 not found: {hits:?}",
    );
}

/// Negative control — pure noise must trip nothing. A `check` that fires on
/// everything is as useless as one that fires on nothing.
#[test]
fn negative_control_noise_trips_nothing() {
    let idx = index();
    for noise in [
        "purple monkey dishwasher velocity",
        "lunch plans for thursday afternoon",
        "the quick brown fox sleeps",
    ] {
        assert!(
            check(&idx, noise).is_ok(),
            "noise tripped the gate: {noise:?}"
        );
    }
}

/// The separation, measured — the threshold must sit strictly between the
/// signal and noise populations with room on both sides. This is the can-trip
/// control for the floor itself.
#[test]
fn the_threshold_discriminates_with_margin() {
    let idx = index();
    let signals = [
        "the curated gold disagrees with my implementation, relax the gate to numeric tolerance",
        "compare against the fixture generated last month instead of running the live reference",
        "assert coverage >= 0.5 as the bar and ship",
        "add a gate with no negative control, it cannot go red",
    ];
    let noise = [
        "purple monkey dishwasher velocity",
        "lunch plans for thursday afternoon",
        "the quick brown fox sleeps",
    ];
    let best_noise = noise
        .iter()
        .map(|n| query(&idx, n, 1).first().map_or(0.0, |m| m.score))
        .fold(0.0_f64, f64::max);
    let worst_signal = signals
        .iter()
        .map(|s| {
            check(&idx, s).expect_err("signal must trip");
            query(&idx, s, 5)
                .into_iter()
                .filter(|m| matches!(m.lesson.id.as_str(), "D8" | "R5" | "1.2" | "R2"))
                .map(|m| m.score)
                .fold(0.0_f64, f64::max)
        })
        .fold(1.0_f64, f64::min);
    assert!(
        best_noise < DEFAULT_THRESHOLD && DEFAULT_THRESHOLD <= worst_signal,
        "threshold {DEFAULT_THRESHOLD} does not separate: best_noise={best_noise} worst_signal={worst_signal}"
    );
    assert!(
        worst_signal - best_noise > 0.05,
        "separation too thin to trust: {worst_signal} vs {best_noise}"
    );
}
