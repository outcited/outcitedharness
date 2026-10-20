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
    /// Manufacturer (page-1 evidence, stamped by the enrich step).
    pub manufacturer: Option<String>,
    /// Exact printed part identity — an OPN or the family the document
    /// declares. A document sha is NOT an identity; this field is.
    pub part_identity: Option<String>,
    /// Where the identity was read ("DS12930 Rev 2 page 1").
    pub identity_source: Option<String>,
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
    type Acc = Vec<(f64, Bound, bool, BTreeMap<String, dw_normalize::CondValue>, SourceRef)>;
    let mut best: BTreeMap<PCandidate, (u8, Acc)> = BTreeMap::new();

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

        // Identity law (2026-10-19 gate 2): a document sha is not an OPN.
        // Use the enrich-stamped part identity; vendor from the stamped
        // manufacturer. Absent identity -> the sha prefix, visibly not an
        // OPN, so the journey layer flags it.
        let identity = row
            .part_identity
            .clone()
            .or(row.part_number.clone())
            .unwrap_or_else(|| sha_str.chars().take(16).collect());
        let candidate = PCandidate {
            part_number: identity,
            vendor: row.manufacturer.clone().unwrap_or_default(),
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

        // Mode-aware aggregation (the applicability ruling, 2026-10-19):
        // a candidate's sleep evidence aggregates ACROSS its printed modes.
        //   ceiling value = the TIGHTEST guaranteed max over all modes —
        //     the satisfaction check the engineer asked for;
        //   floor value   = the LOWEST guaranteed min over all modes — the
        //     best the manufacturer will guarantee; a FAIL means even that
        //     exceeds the budget (universal across the printed inventory).
        // A floor in ONE mode never fails a part whose other mode floors
        // lower; a single row never speaks for the inventory.
        let entry = best.entry(candidate.clone()).or_insert_with(|| (0u8, Acc::new()));
        entry.1.push((
            amperes,
            bound,
            rating_allows_operating,
            parse_conditions(row.conditions_verbatim.as_deref()),
            source,
        ));
        facts.entry(candidate).or_default();
    }

    for (candidate, (_, rows_acc)) in best {
        let Some(cf) = facts.get_mut(&candidate) else {
            continue;
        };
        let mut sources: Vec<SourceRef> = Vec::new();
        for (_, _, _, _, s) in &rows_acc {
            sources.push(s.clone());
        }
        // Prefer the tightest ceiling (existential satisfaction); a part
        // with any guaranteed-max sleep mode is judged on its best mode.
        // Otherwise the lowest floor (universal violation); otherwise a
        // typical point (never decides).
        let ceilings: Vec<_> = rows_acc.iter().filter(|r| r.1 == Bound::Ceiling).collect();
        let floors: Vec<_> = rows_acc.iter().filter(|r| r.1 == Bound::Floor).collect();
        let evidence = if let Some(min_c) = ceilings.iter().min_by(|a, b| {
            a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal)
        }) {
            Evidence::measured_bound(min_c.0, Bound::Ceiling, min_c.2, sources)
                .with_conditions(min_c.3.clone())
        } else if let Some(min_f) = floors.iter().min_by(|a, b| {
            a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal)
        }) {
            Evidence::measured_bound(min_f.0, Bound::Floor, min_f.2, sources)
                .with_conditions(min_f.3.clone())
        } else if let Some(p) = rows_acc.first() {
            Evidence::measured_bound(p.0, Bound::Point, p.2, sources)
                .with_conditions(p.3.clone())
        } else {
            continue;
        };
        cf.axes.insert(Axis::SleepCurrentA, evidence);
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

