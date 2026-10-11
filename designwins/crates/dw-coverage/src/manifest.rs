//! The pillar coverage manifest — port of `harness/electronics/coverage.py`
//! (`build_coverage_manifest` and the ontology it rolls).
//!
//! Every processed document is scored not by "how much did we get" but by
//! "of what we targeted, what did we locate, extract, and validate". The
//! ontology is canonical; states never exaggerate:
//!
//! * `VALIDATED` needs a grounded teacher admission — without one the
//!   maximum reachable state is `EXTRACTED`.
//! * `CONFLICT` is **sticky**: once evidence disagrees, later successes must
//!   not silently mask it.
//!
//! The input rows are the sealed run artifacts (`work_items`,
//! `local_results`, `candidates`, `verified_outcomes`); the output is the
//! per-document manifest plus the weighted rollup the cube serves.

use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

/// One canonical pillar (PRD section 3; weights per PRD section 9).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PillarSpec {
    /// `P01`…`P19`.
    pub id: &'static str,
    /// Canonical name.
    pub name: &'static str,
    /// P0 = 5, P1 = 2, P2 = 1.
    pub weight: u32,
}

/// The canonical pillar ontology, in order.
pub const PILLARS: &[PillarSpec] = &[
    PillarSpec {
        id: "P01",
        name: "Document Identity & Provenance",
        weight: 5,
    },
    PillarSpec {
        id: "P02",
        name: "Product Identity, OPN & Ordering",
        weight: 5,
    },
    PillarSpec {
        id: "P03",
        name: "Family / Variant Matrix",
        weight: 5,
    },
    PillarSpec {
        id: "P04",
        name: "Functional Features & Architecture",
        weight: 5,
    },
    PillarSpec {
        id: "P05",
        name: "Package & Mechanical",
        weight: 5,
    },
    PillarSpec {
        id: "P06",
        name: "Pin / Ball / Pad Mapping",
        weight: 5,
    },
    PillarSpec {
        id: "P07",
        name: "Pin Function & Multiplexing Semantics",
        weight: 5,
    },
    PillarSpec {
        id: "P08",
        name: "Absolute Maximum Ratings & Operating Envelope",
        weight: 5,
    },
    PillarSpec {
        id: "P09",
        name: "DC Electrical Characteristics",
        weight: 5,
    },
    PillarSpec {
        id: "P10",
        name: "AC, Dynamic & Timing Characteristics",
        weight: 5,
    },
    PillarSpec {
        id: "P11",
        name: "Power, Reset, Clocking & Sequencing",
        weight: 5,
    },
    PillarSpec {
        id: "P12",
        name: "Thermal & Derating",
        weight: 5,
    },
    PillarSpec {
        id: "P13",
        name: "Functional-Block / Peripheral Performance",
        weight: 5,
    },
    PillarSpec {
        id: "P14",
        name: "Memory, Configuration, Boot & Security",
        weight: 2,
    },
    PillarSpec {
        id: "P15",
        name: "Typical Performance & Characterization Curves",
        weight: 2,
    },
    PillarSpec {
        id: "P16",
        name: "Application & Design Implementation Guidance",
        weight: 2,
    },
    PillarSpec {
        id: "P17",
        name: "Qualification, Reliability & Compliance",
        weight: 2,
    },
    PillarSpec {
        id: "P18",
        name: "Ecosystem & Development Support",
        weight: 1,
    },
    PillarSpec {
        id: "P19",
        name: "Revision & Change History",
        weight: 2,
    },
];

/// One extraction lane's mapping onto the ontology. `conflates` records known
/// ontology debt for honest reporting.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CapabilityPillar {
    /// The lane name as run artifacts print it.
    pub capability: &'static str,
    /// Which pillar it serves.
    pub pillar: &'static str,
    /// Pillars the lane conflates into its output.
    pub conflates: &'static [&'static str],
}

/// The extraction lanes currently implemented (canonical mapping).
pub const CAPABILITY_PILLARS: &[CapabilityPillar] = &[
    CapabilityPillar {
        capability: "pin_or_ball",
        pillar: "P06",
        conflates: &[],
    },
    CapabilityPillar {
        capability: "pin_semantics",
        pillar: "P07",
        conflates: &["P06"],
    },
    CapabilityPillar {
        capability: "parametrics",
        pillar: "P09",
        conflates: &["P08", "P10", "P13"],
    },
    CapabilityPillar {
        capability: "series_summary",
        pillar: "P04",
        conflates: &[],
    },
    CapabilityPillar {
        capability: "opn_decoder",
        pillar: "P02",
        conflates: &[],
    },
];

/// Pillar states, in progression order (`CONFLICT` last: it is sticky).
pub const STATES: [&str; 8] = [
    "NOT_APPLICABLE",
    "EXPECTED_NOT_FOUND",
    "LOCATED",
    "EXTRACTED",
    "NORMALIZED",
    "PROVENANCED",
    "VALIDATED",
    "CONFLICT",
];

#[allow(clippy::expect_used, reason = "state names come from STATES")]
fn progression(state: &str) -> usize {
    STATES
        .iter()
        .position(|s| *s == state)
        .expect("state names come from STATES")
}

#[allow(clippy::expect_used, reason = "state names come from STATES")]
fn static_state(name: &str) -> &'static str {
    STATES
        .iter()
        .find(|s| **s == name)
        .copied()
        .expect("state names come from STATES")
}

/// Move a pillar forward through the state machine.
///
/// `CONFLICT` is sticky: once evidence disagrees, later successes must not
/// silently mask it (the PRD's rule, ported exactly).
///
/// # Panics
///
/// If `proposed` is not one of [`STATES`] — callers pass those constants.
#[must_use]
#[allow(clippy::expect_used, reason = "proposed is always a STATES constant")]
pub fn advance(current: Option<&str>, proposed: &str) -> &'static str {
    if current == Some("CONFLICT") || proposed == "CONFLICT" {
        return "CONFLICT";
    }
    let Some(cur) = current else {
        return static_state(proposed);
    };
    if progression(proposed) > progression(cur) {
        static_state(proposed)
    } else {
        static_state(cur)
    }
}

/// A structurally gated work item targeting one document page.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkItem {
    /// Stable id the other artifacts reference.
    pub work_id: String,
    /// Which lane.
    pub capability: String,
    /// Which document.
    pub document_sha256: String,
    /// Which vendor, when known.
    pub vendor: Option<String>,
}

/// A local-model result for a work item.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LocalResult {
    /// Which work item it answers.
    pub work_id: String,
}

/// One verification attempt on a candidate.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Attempt {
    /// e.g. `cross_source_disagreement` — which flips the pillar to `CONFLICT`.
    pub status: String,
}

/// A candidate extraction with its local attempts.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Candidate {
    /// Which document.
    pub document_sha256: String,
    /// Which lane.
    pub capability: String,
    /// What local verification saw.
    pub local_attempts: Vec<Attempt>,
}

/// A grounded teacher outcome.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct VerifiedOutcome {
    /// Which work item, when the outcome carries one.
    pub work_id: Option<String>,
    /// Which document, when identified directly.
    pub document_sha256: Option<String>,
    /// Which lane, when identified directly.
    pub capability: Option<String>,
    /// `admitted` validates; `quarantined`/`rejected` conflict.
    pub disposition: String,
}

/// The ontology entry as the manifest serialises it (the map key carries
/// the pillar id).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PillarOntologyEntry {
    /// Canonical name.
    pub name: String,
    /// P0 = 5, P1 = 2, P2 = 1.
    pub weight: u32,
}

/// The lane mapping as the manifest serialises it (the map key carries the
/// capability name).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CapabilityPillarEntry {
    /// Which pillar it serves.
    pub pillar: String,
    /// Pillars the lane conflates into its output.
    pub conflates: Vec<String>,
}

/// Per-pillar accounting for one document.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PillarEntry {
    /// Which lane feeds it.
    pub capability: String,
    /// Ontology debt.
    pub conflates: Vec<String>,
    /// Current state.
    pub state: Option<String>,
    /// Work items targeted.
    pub targeted_pages: u32,
    /// Local results parsed.
    pub extracted_pages: u32,
    /// Attempt statuses, sorted by status name.
    pub attempt_statuses: BTreeMap<String, u32>,
    /// Teacher admissions.
    pub validated_items: u32,
    /// Quarantined/rejected outcomes.
    pub quarantined_items: u32,
}

/// The manifest: per-document pillar accounting plus the weighted rollup.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Manifest {
    /// Schema stamp, as the reference emits it.
    pub schema: String,
    /// The canonical ontology.
    pub pillar_ontology: BTreeMap<String, PillarOntologyEntry>,
    /// The lane mapping.
    pub capability_pillars: BTreeMap<String, CapabilityPillarEntry>,
    /// Per-document accounting, keyed by document sha (sorted).
    pub documents: BTreeMap<String, DocumentEntry>,
    /// Weighted rollup.
    pub summary: Summary,
}

/// One document's accounting.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DocumentEntry {
    /// The document.
    pub document_sha256: String,
    /// Vendor, when any work item carried one.
    pub vendor: Option<String>,
    /// Pillar accounting keyed by pillar id.
    pub pillars: BTreeMap<String, PillarEntry>,
}

/// The weighted rollup (D9's denominators live beside it in the cube).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Summary {
    /// How many documents were touched.
    pub documents: u32,
    /// Pillar-state histogram (sorted by state name).
    pub document_pillar_states: BTreeMap<String, u32>,
    /// Sum of pillar weights touched.
    pub weighted_applicable: u32,
    /// Weights at or past `EXTRACTED`.
    pub weighted_extracted: u32,
    /// Weights at `VALIDATED`.
    pub weighted_validated: u32,
    /// `weighted_extracted / weighted_applicable`, 4 decimals. `None` when
    /// nothing applies — never a fabricated `0.0`.
    pub extraction_coverage_index: Option<f64>,
    /// `weighted_validated / weighted_applicable`, 4 decimals.
    pub validated_coverage_index: Option<f64>,
}

fn find_capability(capability: &str) -> Option<&'static CapabilityPillar> {
    CAPABILITY_PILLARS
        .iter()
        .find(|c| c.capability == capability)
}

fn pillar_weight(pillar_id: &str) -> u32 {
    PILLARS
        .iter()
        .find(|p| p.id == pillar_id)
        .map_or(0, |p| p.weight)
}

/// Roll sealed run artifacts into a per-document coverage manifest.
///
/// State assignment is strictly evidence-backed, exactly as the reference:
/// `LOCATED` (a targeted work item) → `EXTRACTED` (a parseable local result)
/// → `VALIDATED` (a grounded teacher admission), `CONFLICT` from
/// cross-source disagreement or a quarantined/rejected outcome. Without
/// teacher verification the maximum reachable state is `EXTRACTED`.
///
/// # Panics
///
/// If a local result references a work item whose document or capability
/// vanished — impossible by construction: work items register both when
/// they are seen.
///
/// # Lints
///
/// One linear walk of the four artifact lists, mirroring the reference line
/// by line; splitting it into helpers would scatter the state machine this
/// function exists to make auditable.
#[must_use]
#[allow(
    clippy::too_many_lines,
    clippy::expect_used,
    reason = "faithful sequential port of build_coverage_manifest; see # Panics"
)]
pub fn build_coverage_manifest(
    work_items: &[WorkItem],
    local_results: &[LocalResult],
    candidates: &[Candidate],
    verified_outcomes: &[VerifiedOutcome],
) -> Manifest {
    let mut work_by_id: BTreeMap<&str, &WorkItem> = BTreeMap::new();
    let mut documents: BTreeMap<String, DocumentEntry> = BTreeMap::new();

    for item in work_items {
        let Some(mapping) = find_capability(&item.capability) else {
            continue;
        };
        work_by_id.insert(item.work_id.as_str(), item);
        let doc = documents
            .entry(item.document_sha256.clone())
            .or_insert_with(|| DocumentEntry {
                document_sha256: item.document_sha256.clone(),
                vendor: None,
                pillars: BTreeMap::new(),
            });
        if let Some(v) = &item.vendor {
            doc.vendor = Some(v.clone());
        }
        let pillar = doc
            .pillars
            .entry(mapping.pillar.to_string())
            .or_insert_with(|| PillarEntry {
                capability: item.capability.clone(),
                conflates: mapping.conflates.iter().map(|s| (*s).to_string()).collect(),
                state: None,
                targeted_pages: 0,
                extracted_pages: 0,
                attempt_statuses: BTreeMap::new(),
                validated_items: 0,
                quarantined_items: 0,
            });
        pillar.targeted_pages += 1;
        pillar.state = Some(advance(pillar.state.as_deref(), "LOCATED").to_string());
    }

    for row in local_results {
        let Some(item) = work_by_id.get(row.work_id.as_str()) else {
            continue;
        };
        let mapping = find_capability(&item.capability).expect("work items map");
        let doc = documents
            .get_mut(&item.document_sha256)
            .expect("work created doc");
        let pillar = doc
            .pillars
            .entry(mapping.pillar.to_string())
            .or_insert_with(|| PillarEntry {
                capability: item.capability.clone(),
                conflates: mapping.conflates.iter().map(|s| (*s).to_string()).collect(),
                state: None,
                targeted_pages: 0,
                extracted_pages: 0,
                attempt_statuses: BTreeMap::new(),
                validated_items: 0,
                quarantined_items: 0,
            });
        pillar.extracted_pages += 1;
        pillar.state = Some(advance(pillar.state.as_deref(), "EXTRACTED").to_string());
    }

    for candidate in candidates {
        let Some(mapping) = find_capability(&candidate.capability) else {
            continue;
        };
        // The reference only counts attempts on documents already touched by
        // work items (`sha not in documents` skips) — ported as-is.
        let Some(doc) = documents.get_mut(&candidate.document_sha256) else {
            continue;
        };
        let pillar = doc
            .pillars
            .entry(mapping.pillar.to_string())
            .or_insert_with(|| PillarEntry {
                capability: candidate.capability.clone(),
                conflates: mapping.conflates.iter().map(|s| (*s).to_string()).collect(),
                state: None,
                targeted_pages: 0,
                extracted_pages: 0,
                attempt_statuses: BTreeMap::new(),
                validated_items: 0,
                quarantined_items: 0,
            });
        for attempt in &candidate.local_attempts {
            *pillar
                .attempt_statuses
                .entry(attempt.status.clone())
                .or_insert(0) += 1;
            if attempt.status == "cross_source_disagreement" {
                pillar.state = Some(advance(pillar.state.as_deref(), "CONFLICT").to_string());
            }
        }
    }

    for outcome in verified_outcomes {
        let (sha, capability) =
            if let Some(item) = work_by_id.get(outcome.work_id.as_deref().unwrap_or_default()) {
                (item.document_sha256.clone(), item.capability.clone())
            } else {
                (
                    outcome.document_sha256.clone().unwrap_or_default(),
                    outcome.capability.clone().unwrap_or_default(),
                )
            };
        let Some(mapping) = find_capability(&capability) else {
            continue;
        };
        let Some(doc) = documents.get_mut(&sha) else {
            continue;
        };
        let pillar = doc
            .pillars
            .entry(mapping.pillar.to_string())
            .or_insert_with(|| PillarEntry {
                capability: capability.clone(),
                conflates: mapping.conflates.iter().map(|s| (*s).to_string()).collect(),
                state: None,
                targeted_pages: 0,
                extracted_pages: 0,
                attempt_statuses: BTreeMap::new(),
                validated_items: 0,
                quarantined_items: 0,
            });
        match outcome.disposition.as_str() {
            "admitted" => {
                pillar.validated_items += 1;
                pillar.state = Some(advance(pillar.state.as_deref(), "VALIDATED").to_string());
            }
            "quarantined" | "rejected" => {
                pillar.quarantined_items += 1;
                pillar.state = Some(advance(pillar.state.as_deref(), "CONFLICT").to_string());
            }
            _ => {}
        }
    }

    #[allow(
        clippy::cast_possible_truncation,
        reason = "document counts fit u32; a manifest never holds more"
    )]
    let document_count = documents.len() as u32;
    let mut weighted_total = 0u32;
    let mut weighted_validated = 0u32;
    let mut weighted_extracted = 0u32;
    let mut state_counts: BTreeMap<String, u32> = BTreeMap::new();
    for doc in documents.values() {
        for (pillar_id, pillar) in &doc.pillars {
            let weight = pillar_weight(pillar_id);
            weighted_total += weight;
            let state = pillar.state.clone().unwrap_or_default();
            *state_counts.entry(state.clone()).or_insert(0) += 1;
            if state == "VALIDATED" {
                weighted_validated += weight;
            }
            if matches!(
                state.as_str(),
                "EXTRACTED" | "NORMALIZED" | "PROVENANCED" | "VALIDATED"
            ) {
                weighted_extracted += weight;
            }
        }
    }

    let round4 = |n: u32, d: u32| -> Option<f64> {
        if d == 0 {
            None
        } else {
            Some((f64::from(n) / f64::from(d) * 10000.0).round() / 10000.0)
        }
    };

    let mut pillar_ontology = BTreeMap::new();
    for p in PILLARS {
        pillar_ontology.insert(
            p.id.to_string(),
            PillarOntologyEntry {
                name: p.name.to_string(),
                weight: p.weight,
            },
        );
    }
    let mut capability_pillars = BTreeMap::new();
    for c in CAPABILITY_PILLARS {
        capability_pillars.insert(
            c.capability.to_string(),
            CapabilityPillarEntry {
                pillar: c.pillar.to_string(),
                conflates: c.conflates.iter().map(|s| (*s).to_string()).collect(),
            },
        );
    }

    Manifest {
        schema: "harness.electronics-coverage-manifest.v1".into(),
        pillar_ontology,
        capability_pillars,
        documents,
        summary: Summary {
            documents: document_count,
            document_pillar_states: state_counts,
            weighted_applicable: weighted_total,
            weighted_extracted,
            weighted_validated,
            extraction_coverage_index: round4(weighted_extracted, weighted_total),
            validated_coverage_index: round4(weighted_validated, weighted_total),
        },
    }
}

/// The pillar ids a lane serves — the cube's `laned` flag is computed from
/// this set (a pillar with no lane is honest `laned: false`).
#[must_use]
pub fn lanes_of_pillar(pillar_id: &str) -> Vec<&'static str> {
    CAPABILITY_PILLARS
        .iter()
        .filter(|c| c.pillar == pillar_id)
        .map(|c| c.capability)
        .collect()
}

/// Every pillar id in the ontology, in order.
#[must_use]
pub fn pillar_ids() -> BTreeSet<&'static str> {
    PILLARS.iter().map(|p| p.id).collect()
}
