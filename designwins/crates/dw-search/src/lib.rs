//! # dw-search — the D4 front door
//!
//! `dw-search` resolves **colloquial design intent into technical knives,
//! then eliminates a cohort** (`DECISIONS.md` **D4**). Text retrieval is the
//! residue path, not the product. Built, not ported — `harness/search/` is
//! empty; the machinery lives in [`dw_knives::phrase`] (binding),
//! [`dw_decide`] (judgement), and [`dw_funnel`] (the funnel).
//!
//! What this crate adds is the **substrate between corpus and funnel**:
//!
//! ```text
//! claim rows (symbol, value, qualifier, …)
//!   -> axis registry (dw-knives::registry)     symbol -> axis
//!   -> class gate (AxisSpec::accepts)          transient/surge/absmax rules
//!   -> facts (CandidateFacts, best claim wins)
//!   -> dw_funnel::accept(sentence, …)          cohort + exclusions + next
//! ```
//!
//! ## Guards the loader applies, each from a law
//!
//! * **Class gate** — the reference's `evaluate_claim` projection: axes with
//!   `accepts_classes` filter to them; axes without reject transient/surge.
//!   A claim that cannot run never becomes a fact (fail-closed).
//! * **Wrong-bound guard** — a Min-column claim on a span quantity (`TJ`,
//!   `TSTG`) is the range floor, not a rating magnitude; it is dropped here,
//!   exactly as the reference returns `wrong_bound` `UNKNOWN` for those axes.
//! * **Best claim per part** — the maximum measured value per axis, which is
//!   what `next_question`'s population means by "best".
//!
//! This crate calls no models and is `forbid(unsafe_code)`.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

use std::collections::BTreeMap;

use dw_decide::{Candidate, CandidateFacts, Evidence};
use dw_knives::{rating_class, RatingClass};
use dw_types::ReleaseId;
use serde::{Deserialize, Serialize};

/// One claim row as the corpus prints it (`claims_canonical`-shaped).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ClaimRow {
    /// The orderable part number (`opn`).
    pub part_number: String,
    /// The datasheet symbol as printed.
    pub symbol: String,
    /// The parameter name as printed, when there is one.
    pub parameter: Option<String>,
    /// The table this row came from (`absolute_maximum`, `recommended`, …).
    pub table_kind: Option<String>,
    /// The printed qualifier (`typical`, `minimum`, `maximum`, `rated`, …).
    pub qualifier: Option<String>,
    /// The printed value. A **string** in the corpus (`"0.5"`, not `0.5`).
    pub value: String,
    /// The column header, when the row came from a table.
    pub column_header: Option<String>,
}

/// Why a claim row did not become a fact. Named, never silent.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SkipReason {
    /// No registry axis answers this symbol (D5's honest unknown).
    UnknownAxis,
    /// The class gate refused the claim (`AxisSpec::accepts`).
    ClassNotAccepted,
    /// Min-column claim on a span quantity — the range floor, not a rating.
    WrongBound,
    /// The value does not parse as a number.
    UnparseableValue,
}

/// A population: typed facts per candidate, plus the rows that were skipped
/// and **why** — what we could not evaluate is part of the answer.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Population {
    /// Facts per candidate.
    pub facts: BTreeMap<Candidate, CandidateFacts>,
    /// Rows that did not become facts, with their reasons.
    pub skipped: Vec<(String, SkipReason)>,
}

/// Does the reference's `wrong_bound` guard fire for this row?
///
/// A Min-column claim on an axis with `min_column_evidence: false` is the
/// range floor (`TJ -55` read as a max-temp rating) — never evidence.
fn wrong_bound(spec_min_column: bool, column_header: Option<&str>) -> bool {
    if spec_min_column {
        return false;
    }
    let Some(col) = column_header.map(str::to_lowercase) else {
        return false;
    };
    let col = col.trim();
    // `_BOUND_MIN` in the reference: a column header that names the minimum.
    col.starts_with("min") || col == "minimum"
}

/// Project claim rows into typed facts.
///
/// Best claim wins per axis (the maximum measured value), matching what
/// `dw_decide::next_questions` means by "best claim per part".
#[must_use]
pub fn population_from_rows(rows: &[ClaimRow]) -> Population {
    let registry = dw_knives::registry::by_symbol();
    let mut out = Population::default();
    for row in rows {
        let key = dw_knives::norm_symbol(&row.symbol);
        let Some(spec) = registry.get(&key) else {
            out.skipped
                .push((row.symbol.clone(), SkipReason::UnknownAxis));
            continue;
        };
        let axis = spec.axis;
        let class = rating_class(
            Some(row.symbol.as_str()),
            row.parameter.as_deref(),
            row.table_kind.as_deref(),
            row.qualifier.as_deref(),
        );
        if !spec.accepts(class) {
            out.skipped
                .push((row.symbol.clone(), SkipReason::ClassNotAccepted));
            continue;
        }
        if wrong_bound(spec.min_column_evidence, row.column_header.as_deref()) {
            out.skipped
                .push((row.symbol.clone(), SkipReason::WrongBound));
            continue;
        }
        let Ok(value) = row.value.trim().parse::<f64>() else {
            out.skipped
                .push((row.symbol.clone(), SkipReason::UnparseableValue));
            continue;
        };
        let candidate = Candidate {
            part_number: row.part_number.clone(),
            vendor: String::new(),
        };
        let facts = out.facts.entry(candidate).or_default();
        let replace = facts
            .axes
            .get(&axis)
            .and_then(|e| e.value)
            .is_none_or(|v| value > v);
        if replace {
            // The class gate already applied the C9.2 projection: an absmax
            // claim never becomes operating evidence on axes that refuse
            // that class, and pulses/surges never become facts at all.
            facts
                .axes
                .insert(axis, Evidence::measured(value, true, vec![]));
        }
    }
    out
}

/// The D4 front door: one sentence in, a defensible cohort out.
///
/// Thin on purpose — the funnel is [`dw_funnel::accept`]; what this adds is
/// the request/response shape a front door needs (the bindings and residue
/// beside the [`dw_decide::Build`]).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct SearchResponse {
    /// What the phrase layer bound to, and what it did not.
    pub residue: Vec<String>,
    /// The full build: cohort, exclusions, unknowns, next.
    pub build: dw_decide::Build,
}

/// Search the population with one sentence.
#[must_use]
pub fn search(
    sentence: &str,
    registry: &dw_knives::PhraseRegistry,
    population: &Population,
    release: ReleaseId,
) -> SearchResponse {
    let cohort =
        dw_decide::Cohort::from_candidates(population.facts.keys().cloned().collect::<Vec<_>>());
    let answer = dw_funnel::accept(sentence, registry, &cohort, &population.facts, release);
    SearchResponse {
        residue: answer.bound.unmapped.clone(),
        build: answer.build,
    }
}

/// Rating-class notes for callers that surface `skipped` rows in a UI.
#[must_use]
pub fn skip_reason_str(r: SkipReason) -> &'static str {
    match r {
        SkipReason::UnknownAxis => "no registry axis answers this symbol",
        SkipReason::ClassNotAccepted => "the rating class cannot speak on this axis",
        SkipReason::WrongBound => "min-column claim on a span quantity (range floor)",
        SkipReason::UnparseableValue => "value does not parse as a number",
    }
}

/// Kept for symmetry with [`RatingClass`]'s public surface in the wire.
#[must_use]
pub fn class_of(row: &ClaimRow) -> RatingClass {
    rating_class(
        Some(row.symbol.as_str()),
        row.parameter.as_deref(),
        row.table_kind.as_deref(),
        row.qualifier.as_deref(),
    )
}
