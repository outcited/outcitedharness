//! Gate D4 — the phrase layer against real traffic.
//!
//! `DECISIONS.md` **D4** says the colloquial → knife binding must be gated
//! against the query telemetry, **weighted by search count**. This is that
//! gate.
//!
//! ## What this measures, and what it asserts
//!
//! | | |
//! |---|---|
//! | **measures** | how much real search traffic the phrase layer can act on, and with what license |
//! | **asserts** | the three D4 laws hold across *all* the traffic, and coverage does not regress |
//!
//! The coverage number is **information, not a bar**. Pinning it high would be
//! inventing a target; pinning it against regression is what a corpus gate is
//! for. The number is printed so it can be read and raised deliberately.
//!
//! ## Why weighting matters
//!
//! 1,342 query rows cover 7,423 searches, and the distribution is brutally
//! skewed: `"32b 24 MHz ARM MCU"` alone is 709. Unweighted accuracy would let
//! 600 one-hit typos drown it. Everything here is weighted by `search_count`.
//!
//! ## Substrate
//!
//! `tests/fixtures/gold/search_telemetry_v1.jsonl`, exported from
//! `microcontroller_engineering_intent_analysis_with_complex_searches.xlsx` —
//! Representative + Complex + Design Intent sheets, de-duplicated on
//! (query, theme) keeping the max count.
//!
//! # Lints
//!
//! Comparison harness. Restrictions are kept in library code.
#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    clippy::match_wildcard_for_single_variants,
    clippy::wildcard_enum_match_arm,
    clippy::too_many_lines,
    clippy::cast_precision_loss,
    clippy::cast_sign_loss
)]

use std::path::PathBuf;

use dw_knives::{seed_from_measured_chips, PhraseRegistry, Stamp};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
struct Telemetry {
    query: String,
    #[allow(dead_code)]
    theme: Option<String>,
    search_count: u64,
    #[allow(dead_code)]
    sheet: String,
}

fn repo_root() -> PathBuf {
    let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    loop {
        if dir.join("tests").join("fixtures").join("gold").is_dir() {
            return dir;
        }
        assert!(dir.pop(), "could not locate tests/fixtures/gold");
    }
}

fn telemetry() -> Vec<Telemetry> {
    let path = repo_root()
        .join("tests")
        .join("fixtures")
        .join("gold")
        .join("search_telemetry_v1.jsonl");
    let text = std::fs::read_to_string(path).expect("telemetry present");
    text.lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str(l).expect("row"))
        .collect()
}

/// Where a query landed, for the coverage report.
#[derive(Debug, Clone, Copy, Default)]
struct Coverage {
    /// Weighted searches that produced at least one binding.
    bound: u64,
    /// Weighted searches that produced at least one atom a `ProvenFit`
    /// binding would let eliminate.
    proven_fit: u64,
    /// Weighted searches that only produced `PositiveOnly` atoms.
    positive_only: u64,
    /// Weighted searches where every binding was `Blocked`.
    blocked_only: u64,
    /// Weighted searches that produced no binding at all.
    unmapped: u64,
    /// Weighted searches where some residue survived the bind.
    partial: u64,
}

/// Run the whole telemetry corpus through the phrase layer.
fn measure(registry: &PhraseRegistry, rows: &[Telemetry]) -> Coverage {
    let mut c = Coverage::default();
    for row in rows {
        let w = row.search_count;
        let bound = registry.bind(&row.query);

        if bound.bindings.is_empty() {
            c.unmapped += w;
            continue;
        }
        c.bound += w;

        let has_proven = bound.bindings.iter().any(|b| b.stamp.may_eliminate());
        let has_positive = bound
            .bindings
            .iter()
            .any(|b| matches!(b.stamp, Stamp::PositiveOnly));
        let all_blocked = bound
            .bindings
            .iter()
            .all(|b| matches!(b.stamp, Stamp::Blocked(_)));

        if has_proven {
            c.proven_fit += w;
        } else if has_positive {
            c.positive_only += w;
        } else if all_blocked {
            c.blocked_only += w;
        }
        if !bound.unmapped.is_empty() {
            c.partial += w;
        }
    }
    c
}

#[test]
fn d4_phrase_layer_over_real_traffic() {
    let registry = seed_from_measured_chips();
    let rows = telemetry();
    let total: u64 = rows.iter().map(|r| r.search_count).sum();

    assert_eq!(rows.len(), 1342, "telemetry cardinality");
    assert_eq!(total, 7423, "weighted search total");

    let c = measure(&registry, &rows);

    let pct = |n: u64| 100.0 * n as f64 / total as f64;
    eprintln!("\nD4 phrase-layer coverage over {total} weighted searches:");
    eprintln!("  bound           {:6}  {:5.2}%", c.bound, pct(c.bound));
    eprintln!(
        "    proven fit    {:6}  {:5.2}%   <- only these may eliminate",
        c.proven_fit,
        pct(c.proven_fit)
    );
    eprintln!(
        "    positive only {:6}  {:5.2}%",
        c.positive_only,
        pct(c.positive_only)
    );
    eprintln!(
        "    blocked only  {:6}  {:5.2}%",
        c.blocked_only,
        pct(c.blocked_only)
    );
    eprintln!(
        "  unmapped        {:6}  {:5.2}%",
        c.unmapped,
        pct(c.unmapped)
    );
    eprintln!(
        "  with residue    {:6}  {:5.2}%   <- D5: becomes MissingAxis",
        c.partial,
        pct(c.partial)
    );

    // 1. Every query is accounted for. Nothing is silently dropped.
    assert_eq!(
        c.bound + c.unmapped,
        total,
        "every query must be bound or explicitly unmapped"
    );

    // 2. Regression floor, set from **measurement, not aspiration**.
    //
    //    Measured 2026-10-10, before the axis work: 19 of 7,423 (0.26%), all
    //    `Blocked` — 0% could eliminate. Adding the ONE knife that cleared the
    //    measured floor (`core_family`, DECISIONS.md D9) took it to 632 (8.51%)
    //    with **625 ProvenFit**. 33x coverage from a single measured knife.
    //
    //    That is the point of the gate: it makes the axis-first thesis
    //    falsifiable. The number is a floor to fail below, not a target. Raise
    //    it deliberately when the registry grows. **Never lower it.**
    assert!(
        c.bound >= 632,
        "bound coverage regressed: {} weighted searches (floor 632, measured 2026-10-10)",
        c.bound
    );
    // ProvenFit is the number that matters. Coverage made entirely of Blocked
    // bindings is not progress (PRD-PHRASE 9).
    assert!(
        c.proven_fit >= 625,
        "ProvenFit coverage regressed: {} weighted searches (floor 625)",
        c.proven_fit
    );
}

/// D4 law 1, across all traffic: a binding's stamp is its license to
/// eliminate. Nothing else may produce a hard atom.
#[test]
fn d4_law1_no_illegal_hard_atoms_over_traffic() {
    let registry = seed_from_measured_chips();
    let rows = telemetry();

    let mut checked = 0usize;
    for row in &rows {
        let bound = registry.bind(&row.query);
        for binding in &bound.bindings {
            for atom in binding.contribute() {
                checked += 1;
                if atom.hard {
                    assert!(
                        binding.stamp.may_eliminate(),
                        "law 1 violated: {:?} produced a hard atom with stamp {:?}",
                        binding.phrase,
                        binding.stamp
                    );
                }
            }
        }
        // And a Blocked binding contributes nothing at all.
        for binding in &bound.bindings {
            if matches!(binding.stamp, Stamp::Blocked(_)) {
                assert!(
                    binding.contribute().is_empty(),
                    "law 1 violated: a Blocked binding contributed atoms"
                );
            }
        }
    }
    eprintln!("law 1 checked over {checked} contributed atoms");
}

/// D5, across all traffic: residue surfaces as unknowns, never as silence.
#[test]
fn d5_residue_surfaces_over_traffic() {
    let registry = seed_from_measured_chips();
    let rows = telemetry();

    let mut residue_rows = 0usize;
    let mut residue_weight = 0u64;
    for row in &rows {
        let bound = registry.bind(&row.query);
        if bound.unmapped.is_empty() {
            continue;
        }
        residue_rows += 1;
        residue_weight += row.search_count;
        let (unknowns, questions) = dw_knives::unknowns_from_residue(&bound, 1);
        assert!(
            !unknowns.is_empty(),
            "residue with no MissingAxis is a silent miss: {:?}",
            row.query
        );
        assert!(
            !questions.is_empty(),
            "an unknown with no question is a dead end"
        );
    }
    eprintln!("D5: {residue_rows} rows / {residue_weight} weighted carry residue");
}

/// The telemetry itself is real: a heavily skewed distribution. If this ever
/// flattens out, the weighting rationale is wrong and the numbers above mean
/// something different.
#[test]
fn telemetry_is_skewed() {
    let rows = telemetry();
    let max = rows.iter().map(|r| r.search_count).max().unwrap_or(0);
    let total: u64 = rows.iter().map(|r| r.search_count).sum();
    assert!(
        max >= total / 20,
        "the top query should be a material share of traffic; got {max}/{total}"
    );
}
