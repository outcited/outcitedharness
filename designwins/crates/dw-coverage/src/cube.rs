//! The rollup cube — `category × aisle × vendor` (`PLAN.md` §7).
//!
//! The cube assembles a slice of the corpus into the one response a coverage
//! front end renders from: per-pillar state histograms **with `laned`
//! honesty** (a pillar that has no extraction lane says so instead of
//! looking covered), curve availability, D9's two denominators, the knife
//! coverage, and the top gaps.
//!
//! The manifest half is ported ([`crate::manifest`]); this assembly is new
//! design following `PLAN.md` §7's wire shape.

use serde::{Deserialize, Serialize};

use crate::gaps::{derive_gaps, Gap};
use crate::manifest::{lanes_of_pillar, Manifest, PILLARS};

/// Which slice of the corpus the cube covers.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct CubeSlice {
    /// e.g. `power`.
    pub category: Option<String>,
    /// e.g. `buck`.
    pub aisle: Option<String>,
    /// e.g. `TI`.
    pub vendor: Option<String>,
}

/// OPN expanse classification of the slice's documents (`PLAN` §6).
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct Documents {
    /// Documents held.
    pub held: u32,
    /// Documents serving more OPNs than held parts.
    pub above_opn: u32,
    /// Documents serving exactly one OPN.
    pub single_opn: u32,
    /// Everything else.
    pub other: u32,
}

/// D9's two denominators, named as the ruling names them.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct Denominator {
    /// Distinct parts in the slice.
    pub parts: u32,
    /// Attributes the slice's documents address.
    pub attributes_addressed: u32,
}

/// Curve availability in the slice (`PLAN` §8).
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct Curves {
    /// Vector curves (extractable).
    pub vector: u32,
    /// Raster-only (vision lane).
    pub raster: u32,
    /// No curves.
    pub none: u32,
    /// Not yet known.
    pub unknown: u32,
}

/// Knife coverage in the slice (D9).
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct KnivesCoverage {
    /// Axes the slice's corpus answers.
    pub axes_known: Vec<String>,
    /// Axes the slice expects.
    pub axes_expected: Vec<String>,
}

impl KnivesCoverage {
    /// `axes_known / axes_expected`, 4 decimals. `None` when nothing is
    /// expected — never a fabricated number.
    #[must_use]
    #[allow(clippy::cast_precision_loss, reason = "axis counts; exact below 2^53")]
    pub fn coverage_pct(&self) -> Option<f64> {
        if self.axes_expected.is_empty() {
            return None;
        }
        let known = self
            .axes_expected
            .iter()
            .filter(|a| self.axes_known.contains(a))
            .count();
        Some((100.0 * known as f64 / self.axes_expected.len() as f64 * 100.0).round() / 100.0)
    }
}

/// Per-pillar rollup inside the cube.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PillarRollup {
    /// Canonical name.
    pub name: String,
    /// P0 = 5, P1 = 2, P2 = 1.
    pub weight: u32,
    /// Pillar-state histogram across the slice's documents.
    pub state_counts: BTreeMapStringU32,
    /// **Honest**: `false` when the pillar has no extraction lane at all.
    pub laned: bool,
    /// The lane, when there is exactly one.
    pub lane: Option<String>,
    /// Ontology debt.
    pub conflates: Vec<String>,
    /// Work items targeted in the slice.
    pub targeted_pages: u32,
    /// Local results parsed in the slice.
    pub extracted_pages: u32,
    /// Teacher admissions in the slice.
    pub validated_items: u32,
}

/// The histogram type, spelled out so the serde shape is explicit and sorted.
pub type BTreeMapStringU32 = std::collections::BTreeMap<String, u32>;

/// The cube response (`PLAN` §7, `GET /api/coverage/v1/cube`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CoverageCube {
    /// Which publication.
    pub release_id: dw_types::ReleaseId,
    /// Which slice this answers.
    pub slice: CubeSlice,
    /// OPN expanse classification.
    pub documents: Documents,
    /// D9's denominators.
    pub denominator: Denominator,
    /// The 19 pillars, in ontology order.
    pub pillars: BTreeMapStringRollup,
    /// Curve availability.
    pub curves: Curves,
    /// Knife coverage.
    pub knives: KnivesWire,
    /// The worst gaps, by impact.
    pub top_gaps: Vec<Gap>,
}

/// Pillar rollups keyed by pillar id (insertion order = ontology order).
pub type BTreeMapStringRollup = std::collections::BTreeMap<String, PillarRollup>;

/// The knife block on the wire (counts + the computed percentage).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct KnivesWire {
    /// How many axes are known.
    pub axes_known: usize,
    /// How many are expected.
    pub axes_expected: usize,
    /// `known/expected`, 4 decimals; `None` when nothing is expected.
    pub coverage_pct: Option<f64>,
}

/// Caller-supplied slice measurements the manifest cannot know.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct SliceInputs {
    /// OPN expanse classification.
    pub documents: Documents,
    /// D9's denominators.
    pub denominator: Denominator,
    /// Curve availability.
    pub curves: Curves,
    /// Knife coverage.
    pub knives: KnivesCoverage,
    /// Referenced documents that are not held (the acquisition list).
    pub acquisitions: Vec<String>,
}

/// Assemble the cube for one slice from the manifest and the slice
/// measurements. Pure: same inputs, same cube.
#[must_use]
pub fn rollup(
    release_id: dw_types::ReleaseId,
    slice: CubeSlice,
    manifest: &Manifest,
    inputs: &SliceInputs,
) -> CoverageCube {
    let mut pillars = BTreeMapStringRollup::new();
    for p in PILLARS {
        let mut state_counts: BTreeMapStringU32 = BTreeMapStringU32::new();
        let (mut targeted, mut extracted, mut validated) = (0u32, 0u32, 0u32);
        for doc in manifest.documents.values() {
            if let Some(entry) = doc.pillars.get(p.id) {
                *state_counts
                    .entry(entry.state.clone().unwrap_or_default())
                    .or_insert(0) += 1;
                targeted += entry.targeted_pages;
                extracted += entry.extracted_pages;
                validated += entry.validated_items;
            }
        }
        let lanes = lanes_of_pillar(p.id);
        pillars.insert(
            p.id.to_string(),
            PillarRollup {
                name: p.name.to_string(),
                weight: p.weight,
                state_counts,
                laned: !lanes.is_empty(),
                lane: lanes.first().map(|s| (*s).to_string()),
                conflates: Vec::new(),
                targeted_pages: targeted,
                extracted_pages: extracted,
                validated_items: validated,
            },
        );
    }
    // conflates from the lane mapping (the lane that serves the pillar).
    for (id, rollup) in &mut pillars {
        if let Some(cap) = crate::manifest::CAPABILITY_PILLARS
            .iter()
            .find(|c| c.pillar == id.as_str())
        {
            rollup.conflates = cap.conflates.iter().map(|s| (*s).to_string()).collect();
        }
    }

    // `top_gaps` is a top list, not the whole ledger: the full gap ledger
    // lives at `GET /api/coverage/v1/gaps`; the cube carries the worst five.
    let mut top_gaps = derive_gaps(manifest, inputs);
    top_gaps.truncate(5);
    CoverageCube {
        knives: KnivesWire {
            axes_known: inputs.knives.axes_known.len(),
            axes_expected: inputs.knives.axes_expected.len(),
            coverage_pct: inputs.knives.coverage_pct(),
        },
        release_id,
        slice,
        documents: inputs.documents.clone(),
        denominator: inputs.denominator.clone(),
        pillars,
        curves: inputs.curves.clone(),
        top_gaps,
    }
}
