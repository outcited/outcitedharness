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

use dw_decide::{Bound, Candidate, CandidateFacts, Evidence, Op, SpanEndpoint, SpanEvidence};
use dw_knives::{rating_class, RatingClass};
use dw_types::ReleaseId;
use serde::{Deserialize, Serialize};

pub mod mode_rows;

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
    /// A range symbol may print both endpoints (`"-40 -> +40"`, `"-55 175"`).
    pub value: String,
    /// The column header, when the row came from a table.
    pub column_header: Option<String>,
    /// The printed unit, when the row carried one. Asserted against the
    /// axis's canonical unit at the population boundary; a mismatch parks.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub unit: Option<String>,
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
    /// A range symbol whose printed text could not be read as one or two
    /// endpoints (fail-closed; never best-guessed).
    RangeUnparseable,
    /// A span whose two printed endpoints are inverted (`min > max`) or
    /// non-finite — not a range at all.
    RangeInvalid,
    /// The printed unit does not map to the axis's canonical unit.
    CanonicalUnitMismatch,
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

/// The canonical unit KIND for a range-valued axis, if the axis is a range.
///
/// This is the unit-assertion boundary: a printed range in a unit that does not
/// map to the axis's canonical kind parks (never silently reinterpreted).
fn canonical_range_kind(op: Op) -> Option<dw_normalize::units::Kind> {
    use dw_normalize::units::Kind;
    if op == Op::Covers {
        // The only `Covers` axis today is the storage/operating temperature
        // span (`TstgRangeC`), canonical unit °C.
        Some(Kind::Celsius)
    } else {
        None
    }
}

/// The printed-unit spellings that assert cleanly to a canonical kind.
///
/// `dw_normalize::units::scale` is a *multiplier* lookup, and its `Celsius`
/// arm returns a multiplier for **any** token (the frozen vocabulary defers
/// temperature spellings). That is not an assertion: `unit_asserts` must reject
/// a printed unit that names a *different* kind (`v`, `kb`, …). So the
/// accepted spellings for the only range kind in use are listed explicitly —
/// minting no vocabulary, only the canonical Celsius spellings.
fn canonical_unit_tokens(kind: dw_normalize::units::Kind) -> &'static [&'static str] {
    use dw_normalize::units::Kind;
    match kind {
        Kind::Celsius => &["c", "°c"],
        Kind::CelsiusPerWatt => &["c/w", "°c/w"],
        _ => &[],
    }
}

/// Does the printed unit assert cleanly to the axis's canonical unit kind?
///
/// `None` means "no unit printed" — no assertion can be made, so it passes.
/// A printed unit that is not one of the canonical kind's spellings is a
/// mismatch and parks (never silently reinterpreted as the canonical unit).
fn unit_asserts(op: Op, unit: Option<&str>) -> bool {
    let Some(kind) = canonical_range_kind(op) else {
        return true;
    };
    let Some(unit) = unit else {
        return true;
    };
    let tok = dw_normalize::units::unit_token(unit);
    let tokens = canonical_unit_tokens(kind);
    let spelling_ok = tokens.contains(&tok.as_str());
    spelling_ok
        && matches!(
            dw_normalize::units::scale(kind, Some(unit)),
            dw_normalize::units::Scale::Known(_)
        )
}

/// Extract every signed finite-looking number from a printed range text.
///
/// The scan is deliberately simple and sign-preserving: `-55 175` yields
/// `[-55, 175]`, `-40 -> +40` yields `[-40, 40]`. It never takes absolute
/// values — that is exactly the historical TSTG collapse defect.
fn scan_signed_numbers(text: &str) -> Vec<f64> {
    let bytes = text.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while let Some(&cur) = bytes.get(i) {
        let c = cur as char;
        let sign_starts_number = c == '-' && bytes.get(i + 1).is_some_and(u8::is_ascii_digit);
        if c.is_ascii_digit() || sign_starts_number {
            let start = i;
            i += 1;
            while let Some(&d) = bytes.get(i) {
                let dc = d as char;
                let prev_is_exp = i
                    .checked_sub(1)
                    .and_then(|j| bytes.get(j))
                    .is_some_and(|b| matches!(*b as char, 'e' | 'E'));
                let exp_sign = (dc == '-' || dc == '+') && prev_is_exp;
                if dc.is_ascii_digit() || matches!(dc, '.' | 'e' | 'E') || exp_sign {
                    i += 1;
                } else {
                    break;
                }
            }
            if let Ok(v) = text[start..i].parse::<f64>() {
                out.push(v);
            }
        } else {
            i += 1;
        }
    }
    out
}

/// Read a range row into span evidence, or park it with a reason.
///
/// Two printed numbers give a two-sided span (lower first, as printed — an
/// inverted print parks). One number with a Min/Max column header gives a
/// single-ended span (which judges `UNKNOWN` under `Covers`). Anything else
/// parks fail-closed.
fn span_from_row(row: &ClaimRow) -> Result<Evidence, SkipReason> {
    let numbers = scan_signed_numbers(&row.value);
    if numbers.iter().any(|v| !v.is_finite()) {
        return Err(SkipReason::RangeInvalid);
    }
    let header = row
        .column_header
        .as_deref()
        .map(str::trim)
        .unwrap_or_default()
        .to_lowercase();
    let span = match numbers.as_slice() {
        [min, max, ..] => {
            let (min, max) = (*min, *max);
            if min > max {
                return Err(SkipReason::RangeInvalid);
            }
            SpanEvidence::two_sided(min, max, Bound::Point, vec![])
        }
        [only] => {
            let endpoint = SpanEndpoint::new(*only, Bound::Point, vec![]);
            if header.starts_with("min") {
                SpanEvidence::from_endpoints(Some(endpoint), None)
            } else if header.starts_with("max") {
                SpanEvidence::from_endpoints(None, Some(endpoint))
            } else {
                // A single number with no column role is not a range we can
                // place — fail closed rather than guess an endpoint.
                return Err(SkipReason::RangeUnparseable);
            }
            .ok_or(SkipReason::RangeUnparseable)?
        }
        [] => return Err(SkipReason::RangeUnparseable),
    };
    Ok(Evidence::spanned(span, true))
}

/// Project claim rows into typed facts.
///
/// Best claim wins per axis (the maximum measured value), matching what
/// `dw_decide::next_questions` means by "best claim per part". Range-valued
/// axes ([`Op::Covers`]) build a two-sided span from both printed endpoints
/// instead of a scalar.
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

        // Range-valued axis: span evidence, built from both printed columns.
        if spec.op == Op::Covers {
            if !unit_asserts(spec.op, row.unit.as_deref()) {
                out.skipped
                    .push((row.symbol.clone(), SkipReason::CanonicalUnitMismatch));
                continue;
            }
            let evidence = match span_from_row(row) {
                Ok(e) => e,
                Err(code) => {
                    out.skipped.push((row.symbol.clone(), code));
                    continue;
                }
            };
            let candidate = Candidate {
                part_number: row.part_number.clone(),
                vendor: String::new(),
            };
            let facts = out.facts.entry(candidate).or_default();
            // A complete span beats a partial one; otherwise first wins.
            let replace = match facts.axes.get(&axis).and_then(|e| e.span.as_ref()) {
                Some(existing) => !existing.is_complete(),
                None => true,
            };
            if replace {
                facts.axes.insert(axis, evidence);
            }
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
    provenance: &dyn dw_decide::Provenance,
) -> SearchResponse {
    let cohort =
        dw_decide::Cohort::from_candidates(population.facts.keys().cloned().collect::<Vec<_>>());
    let answer = dw_funnel::accept(
        sentence,
        registry,
        &cohort,
        &population.facts,
        release,
        provenance,
    );
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
        SkipReason::RangeUnparseable => "range symbol did not print one or two endpoints",
        SkipReason::RangeInvalid => "printed range is inverted or non-finite",
        SkipReason::CanonicalUnitMismatch => "printed unit is not the axis's canonical unit",
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
