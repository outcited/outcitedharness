//! Tri-state brand projection over one engine evaluation.
//!
//! Doctrine (issue #29): *qualified only on entirely evidenced OPNs;
//! eliminated only when all applicable OPNs demonstrably fail; insufficient
//! otherwise.* "Applicable" = the part is in the released MCU cohort.

use std::collections::BTreeMap;

use dw_decide::{Axis, Candidate, Evaluation, Verdict};
use serde::{Deserialize, Serialize};

use crate::evidence::VerbatimRef;

/// The tri-state outcome for one brand under one atom set.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BrandState {
    /// At least one OPN passes every hard atom on resolvable evidence.
    Qualified,
    /// The brand has OPNs in the cohort and every one demonstrably failed
    /// at least one hard atom (engine `Fail`, which requires evidence).
    Eliminated,
    /// Neither of the above — missing or insufficient evidence dominates.
    /// Unknown never eliminates (C9.1).
    InsufficientEvidence,
}

/// One eliminated OPN with its killing knife and evidence.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EliminatedOpn {
    /// The part.
    pub opn: String,
    /// Axis name of the killing knife.
    pub axis: String,
    /// Engine reason string.
    pub reason: String,
    /// Document/page evidence refs (sha + page).
    pub evidence: Vec<EvidenceRef>,
    /// Verbatim quotes behind the evidence.
    pub verbatim: Vec<VerbatimRef>,
}

/// A compact evidence reference for receipts.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct EvidenceRef {
    /// Document sha.
    pub sha256: String,
    /// 1-based page.
    pub page: u32,
}

/// One qualified OPN, with the evidence per satisfied atom.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct QualifiedOpn {
    /// The part.
    pub opn: String,
    /// Axis name → evidence backing the pass.
    pub evidence: BTreeMap<String, Vec<EvidenceRef>>,
    /// Verbatim quotes per axis (capped).
    pub verbatim: BTreeMap<String, Vec<VerbatimRef>>,
}

/// The full projection for one brand.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BrandOutcome {
    /// Brand key (manufacturer as stamped).
    pub brand: String,
    /// Human display name.
    pub display: String,
    /// The tri-state.
    pub state: BrandState,
    /// OPNs of this brand in the released MCU cohort.
    pub cohort_opns: usize,
    /// Fully-evidenced passers (state == qualified implies non-empty).
    pub qualified_opns: Vec<QualifiedOpn>,
    /// Eliminated OPNs with killing knives.
    pub eliminated_opns: Vec<EliminatedOpn>,
    /// Survivors that are NOT qualified (near-miss or unknown atoms).
    /// Near-miss is not qualified; unknown never eliminates.
    pub surviving_unqualified_opns: Vec<String>,
    /// Axis → count of this brand's OPNs that were Unknown on it
    /// (the actionable missing-evidence ledger).
    pub unknown_axis_counts: BTreeMap<String, usize>,
}

/// Brand display names (receipt sugar only — keys are the stamped
/// manufacturers).
#[must_use]
pub fn brand_display(brand: &str) -> String {
    match brand {
        "st" => "STMicroelectronics".into(),
        "texas_instruments" => "Texas Instruments".into(),
        "nxp" => "NXP".into(),
        "infineon" => "Infineon".into(),
        "microchip" => "Microchip".into(),
        "renesas" => "Renesas".into(),
        "onsemi" => "onsemi".into(),
        "rohm" => "ROHM".into(),
        "vishay" => "Vishay".into(),
        "espressif" => "Espressif".into(),
        "analog_devices" => "Analog Devices".into(),
        other => other.to_string(),
    }
}

fn axis_name(axis: Axis) -> String {
    serde_json::to_value(axis)
        .ok()
        .and_then(|v| v.as_str().map(str::to_string))
        .unwrap_or_else(|| format!("{axis:?}"))
}

/// Project one evaluation into per-brand outcomes.
///
/// `verbatim` is the loader's (part, axis) → quotes index; `cap` bounds how
/// many quotes/refs ride the receipt.
#[must_use]
#[allow(
    clippy::too_many_lines,
    reason = "one projection over one evaluation; linear by brand"
)]
pub fn project_brands(
    eval: &Evaluation,
    atom_axes: &[Axis],
    verbatim: &BTreeMap<(String, String), Vec<VerbatimRef>>,
    cap: usize,
) -> BTreeMap<String, BrandOutcome> {
    // Index judgements per candidate.
    let mut per_part: BTreeMap<&Candidate, BTreeMap<Axis, (Verdict, Vec<EvidenceRef>)>> =
        BTreeMap::new();
    for j in &eval.judgements {
        let entry = per_part.entry(&j.candidate).or_default();
        let refs = j
            .evidence
            .iter()
            .map(|s| EvidenceRef {
                sha256: s.sha256.to_string(),
                page: s.page,
            })
            .take(cap)
            .collect::<Vec<_>>();
        entry.insert(j.axis, (j.outcome, refs));
    }
    let eliminated: BTreeMap<&str, &dw_decide::Exclusion> = eval
        .exclusions
        .iter()
        .map(|e| (e.candidate.part_number.as_str(), e))
        .collect();
    let survivors: BTreeMap<&str, &Candidate> = eval
        .cohort
        .members
        .iter()
        .map(|c| (c.part_number.as_str(), c))
        .collect();

    // Group the full cohort (not just survivors) by vendor.
    let mut brands: BTreeMap<&str, Vec<&Candidate>> = BTreeMap::new();
    for part in per_part.keys() {
        if let Some(c) = survivors.get(part.part_number.as_str()) {
            brands.entry(c.vendor.as_str()).or_default().push(c);
        } else if let Some(e) = eliminated.get(part.part_number.as_str()) {
            brands
                .entry(e.candidate.vendor.as_str())
                .or_default()
                .push(&e.candidate);
        }
    }

    let mut out = BTreeMap::new();
    for (brand, parts) in brands {
        let mut qualified = Vec::new();
        let mut eliminated_opns = Vec::new();
        let mut surviving_unqualified_opns = Vec::new();
        let mut unknown_counts: BTreeMap<String, usize> = BTreeMap::new();

        for part in &parts {
            let opn = part.part_number.clone();
            let Some(judgements) = per_part.get(*part) else {
                continue;
            };
            let all_pass = atom_axes.iter().all(|axis| {
                judgements
                    .get(axis)
                    .is_some_and(|(v, _)| matches!(v, Verdict::Pass))
            });
            let is_eliminated = eliminated.contains_key(opn.as_str());

            if all_pass && !atom_axes.is_empty() {
                let mut evidence = BTreeMap::new();
                let mut quotes = BTreeMap::new();
                for axis in atom_axes {
                    let name = axis_name(*axis);
                    if let Some((verdict, refs)) = judgements.get(axis) {
                        if matches!(verdict, Verdict::Pass) && !refs.is_empty() {
                            evidence.insert(name.clone(), refs.clone());
                            if let Some(v) = verbatim.get(&(opn.clone(), name.clone())) {
                                quotes.insert(name, v.iter().take(cap).cloned().collect());
                            }
                        }
                    }
                }
                qualified.push(QualifiedOpn {
                    opn: opn.clone(),
                    evidence,
                    verbatim: quotes,
                });
            } else if is_eliminated {
                if let Some(excl) = eliminated.get(opn.as_str()) {
                    let refs = excl
                        .ineligible
                        .evidence()
                        .iter()
                        .map(|s| EvidenceRef {
                            sha256: s.sha256.to_string(),
                            page: s.page,
                        })
                        .take(cap)
                        .collect();
                    let name = axis_name(excl.axis);
                    eliminated_opns.push(EliminatedOpn {
                        opn: opn.clone(),
                        axis: name.clone(),
                        reason: excl.ineligible.reason().to_string(),
                        evidence: refs,
                        verbatim: verbatim
                            .get(&(opn.clone(), name))
                            .map(|v| v.iter().take(cap).cloned().collect())
                            .unwrap_or_default(),
                    });
                }
            } else if survivors.contains_key(opn.as_str()) {
                // Survived but not fully evidenced: near-miss or unknown atoms.
                surviving_unqualified_opns.push(opn.clone());
            }

            for axis in atom_axes {
                if let Some((Verdict::Unknown, _)) = judgements.get(axis) {
                    *unknown_counts.entry(axis_name(*axis)).or_insert(0) += 1;
                }
            }
        }

        let state = if !qualified.is_empty() {
            BrandState::Qualified
        } else if parts
            .iter()
            .all(|p| eliminated.contains_key(p.part_number.as_str()))
            && !parts.is_empty()
        {
            BrandState::Eliminated
        } else {
            BrandState::InsufficientEvidence
        };

        out.insert(
            brand.to_string(),
            BrandOutcome {
                brand: brand.to_string(),
                display: brand_display(brand),
                state,
                cohort_opns: parts.len(),
                qualified_opns: qualified,
                eliminated_opns,
                surviving_unqualified_opns,
                unknown_axis_counts: unknown_counts,
            },
        );
    }
    out
}
