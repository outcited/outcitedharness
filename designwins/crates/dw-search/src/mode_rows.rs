//! Mode-current row bridge — real burn rows → typed facts (the evidence path
//! `population_from_rows` never had).
//!
//! Input: `harness.electronics-mode-currents.v1` JSONL rows from the shared
//! burn pass (open-once extraction, 2026-10-19). Output: a
//! [`Population`] whose sleep-current evidence carries the manufacturer
//! part identity, the document sha, the printed page, the parsed operating
//! conditions, and — the 2026-10-19 ruling — the printed **bound**
//! (guaranteed max = ceiling, guaranteed min = floor, typical = point that
//! never decides).
//!
//! Fail-closed everywhere: an unknown unit, a per-MHz quantity, a missing
//! sha, or an unparseable value skips the row with a reason. Nothing is
//! invented, nothing silently passes.

use std::collections::{BTreeMap, BTreeSet};

use crate::{Candidate as PCandidate, Population};
use dw_decide::{Axis, Bound, CandidateFacts, Evidence};
use dw_types::{Sha256, SourceRef};

/// One burn mode-current row (serde shape of the JSONL line).
#[derive(Debug, Clone, serde::Deserialize)]
pub struct ModeRow {
    /// Manufacturer part identity as the lane derived it (may be absent on
    /// sha-only rows; those key the candidate by sha).
    pub part_number: Option<String>,
    /// Document content hash — the provenance join.
    pub pdf_sha: Option<String>,
    /// The operating-mode name as printed ("Deep Sleep", "Stop 0").
    pub mode: String,
    /// The printed value, in the printed unit.
    pub value: f64,
    /// The printed unit ("µA", "μA", "mA", "nA", "mA/MHz", …).
    pub unit: Option<String>,
    /// The column role: `max` | `min` | `typ` | `value`.
    pub value_role: Option<String>,
    /// Verbatim operating conditions ("VDD = 3 V, TA = 25 °C").
    pub conditions_verbatim: Option<String>,
    /// 1-based page of the printed row.
    pub page_1based: Option<u32>,
}

/// Why a row did not become a fact. Surfaced to the UI, never swallowed.
#[derive(Debug, Clone, PartialEq)]
pub enum ModeSkip {
    /// Unit not in the Ampere table (or per-MHz — a different quantity).
    UnitNotApplicable(String),
    /// The mode name is not a sleep-family mode (this bridge is the
    /// sleep-current axis; other axes get their own adapters).
    NotSleepMode(String),
    /// The row carried no resolvable document sha — no provenance, no fact.
    MissingSha,
}

/// The sleep-family mode vocabulary (measured: 75 spellings fold to these).
fn is_sleep_mode(mode: &str) -> bool {
    let m = mode.to_lowercase();
    ["sleep", "standby", "hibernate", "shutdown", "lpm", "stop"].iter().any(|k| m.contains(k))
}

/// Scale a printed current value to Amperes via the frozen unit table
/// (`dw-normalize::units`). `None` = unknown or per-MHz — refused.
fn to_amperes(value: f64, unit: &str) -> Option<f64> {
    use dw_normalize::units::{scale, Kind, Scale};
    match scale(Kind::Ampere, Some(unit)) {
        Scale::Known(factor) => Some(value * factor),
        Scale::Unknown => None,
    }
}

/// The printed column role as a semantic [`Bound`] + operating permission.
fn bound_of(role: &str) -> Option<(Bound, bool)> {
    match role {
        // Guaranteed maximum: proves satisfaction, never violation.
        "max" => Some((Bound::Ceiling, true)),
        // Guaranteed minimum: proves violation, never satisfaction.
        "min" => Some((Bound::Floor, true)),
        // Typical never decides (C9.5); a bare `value` column carries no
        // guarantee either — both are discovery-only points.
        "typ" | "value" => Some((Bound::Point, false)),
        _ => None,
    }
}

/// The population built from real rows, plus every skip (with reason) so
/// the caller can surface them honestly.
#[derive(Debug, Default)]
pub struct ModePopulation {
    /// The typed facts.
    pub population: Population,
    /// Rows refused, with why.
    pub skipped: Vec<(String, ModeSkip)>,
}

/// Parse operating conditions with `dw-normalize` (fail-closed: a refusal
/// yields an empty map — the row still lands as evidence, it just cannot
/// cover a conditioned requirement).
fn parse_conditions(text: Option<&str>) -> BTreeMap<String, dw_normalize::CondValue> {
    let Some(text) = text else {
        return BTreeMap::new();
    };
    match dw_normalize::condition::parse_condition(text) {
        dw_normalize::condition::ParseOutcome::Typed(typed) => typed.keys,
        dw_normalize::condition::ParseOutcome::Rejected { .. } => BTreeMap::new(),
    }
}

/// Build a sleep-current [`Population`] from real burn mode rows.
pub fn population_from_mode_rows(rows: &[ModeRow]) -> ModePopulation {
    use crate::{Candidate as PCandidate, Population};

    let mut population = Population::default();
    let mut skipped = Vec::new();
    // Per (candidate, axis) we keep the strongest guaranteed evidence:
    // Ceiling > Floor > Point — a candidate with both max and min rows
    // shows its ceiling first (satisfaction is what the engineer asks for).
    let mut facts: BTreeMap<PCandidate, CandidateFacts> = BTreeMap::new();
    let mut provenance_keys: BTreeSet<(String, u32, String)> = BTreeSet::new();
    let mut best: BTreeMap<PCandidate, (u8, Evidence)> = BTreeMap::new();

    for row in rows {
        let mode = row.mode.clone();
        if !is_sleep_mode(&mode) {
            skipped.push((mode.clone(), ModeSkip::NotSleepMode(mode)));
            continue;
        }
        let Some(sha_str) = &row.pdf_sha else {
            skipped.push((mode.clone(), ModeSkip::MissingSha));
            continue;
        };
        let Ok(sha) = Sha256::new(sha_str.clone()) else {
            skipped.push((mode.clone(), ModeSkip::MissingSha));
            continue;
        };
        let unit = row.unit.clone().unwrap_or_default();
        let Some(amperes) = to_amperes(row.value, &unit) else {
            skipped.push((mode.clone(), ModeSkip::UnitNotApplicable(unit)));
            continue;
        };
        let role = row.value_role.clone().unwrap_or_default();
        let Some((bound, rating_allows_operating)) = bound_of(&role) else {
            skipped.push((
                mode.clone(),
                ModeSkip::UnitNotApplicable(format!("role:{role}")),
            ));
            continue;
        };

        let identity = row
            .part_number
            .clone()
            .unwrap_or_else(|| sha_str.chars().take(16).collect());
        let candidate = PCandidate {
            part_number: identity,
            vendor: String::new(),
        };
        let source = SourceRef {
            sha256: sha,
            page: row.page_1based.unwrap_or(1),
            row_header: Some(mode.clone()),
            column_header: Some(role),
            crop: None,
        };
        provenance_keys.insert((
            sha_str.clone(),
            row.page_1based.unwrap_or(1),
            mode.clone(),
        ));

        let rank = match bound {
            Bound::Ceiling => 3,
            Bound::Floor => 2,
            Bound::Point => 1,
        };
        let evidence =
            Evidence::measured_bound(amperes, bound, rating_allows_operating, vec![source])
                .with_conditions(parse_conditions(row.conditions_verbatim.as_deref()));
        let replace = best
            .get(&candidate)
            .map(|(r, _)| rank > *r)
            .unwrap_or(true);
        if replace {
            best.insert(candidate.clone(), (rank, evidence));
        }
        facts.entry(candidate).or_default();
    }

    for (candidate, (_, evidence)) in best {
        if let Some(cf) = facts.get_mut(&candidate) {
            cf.axes.insert(Axis::SleepCurrentA, evidence);
        }
    }
    population.facts = facts;
    ModePopulation {
        population,
        skipped,
    }
}

/// The real provenance resolver for this bridge: a source resolves when its
/// (sha, page, row) exists in the rows the population was built from.
/// Replaces `AlwaysResolvable` for real-data decisions — a number we cannot
/// open is a number we cannot assert (C9.1).
pub struct RowsProvenance {
    keys: BTreeSet<(String, u32, String)>,
}

impl RowsProvenance {
    /// Build from the same rows that fed the population.
    #[must_use]
    pub fn from_rows(rows: &[ModeRow]) -> Self {
        let mut keys = BTreeSet::new();
        for row in rows {
            if let (Some(sha), Some(page)) = (&row.pdf_sha, row.page_1based) {
                keys.insert((sha.clone(), page, row.mode.clone()));
            }
        }
        Self { keys }
    }
}

impl dw_decide::Provenance for RowsProvenance {
    fn resolvable(&self, sources: &[SourceRef]) -> bool {
        sources.iter().any(|s| {
            let page = s.page;
            s.row_header
                .as_ref()
                .map(|row| self.keys.contains(&(s.sha256.as_str().to_string(), page, row.clone())))
                .unwrap_or(false)
        })
    }
}

