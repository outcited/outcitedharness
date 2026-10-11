//! The gap ledger — what we know we don't know (`PLAN.md` §7).
//!
//! Four kinds, each with a trigger that is a fact, not an opinion:
//!
//! | kind | trigger | impact |
//! |---|---|---|
//! | `acquire` | a referenced document is not held | 5 — whole pillars are dark without the document |
//! | `lane` | a pillar with weight has **no** extraction lane | pillar weight |
//! | `extract` | a pillar is targeted but nothing extracted (`LOCATED` stuck) | pillar weight |
//! | `axis` | an expected axis the corpus does not answer | 2 — blocks cohort narrowing |
//!
//! The weights are this crate's rollup choice (the pillar weights are the
//! ontology's; `acquire`/`axis` borrow the P0/P1 tiers they block). The
//! derivations are mechanical: a gap exists exactly when its trigger does.

use serde::{Deserialize, Serialize};

use crate::cube::SliceInputs;
use crate::manifest::{lanes_of_pillar, Manifest, PILLARS};

/// Which kind of gap.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GapKind {
    /// A referenced document is not held.
    Acquire,
    /// Targeted but nothing extracted.
    Extract,
    /// A weighted pillar has no extraction lane.
    Lane,
    /// An expected axis is unanswered.
    Axis,
}

/// One gap.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Gap {
    /// Which kind.
    pub kind: GapKind,
    /// What is missing, named.
    pub what: String,
    /// Why it is a gap — the trigger, stated.
    pub why: String,
    /// How much it blocks, by the weights above.
    pub impact_weight: u32,
}

/// Derive every gap that holds for a slice, worst first.
///
/// Deterministic: same manifest and inputs, same ledger.
#[must_use]
pub fn derive_gaps(manifest: &Manifest, inputs: &SliceInputs) -> Vec<Gap> {
    let mut out = Vec::new();

    for name in &inputs.acquisitions {
        out.push(Gap {
            kind: GapKind::Acquire,
            what: name.clone(),
            why: "referenced but not held".into(),
            impact_weight: 5,
        });
    }

    for p in PILLARS {
        if lanes_of_pillar(p.id).is_empty() {
            out.push(Gap {
                kind: GapKind::Lane,
                what: format!("{} ({})", p.name, p.id),
                why: "no extraction lane serves this pillar".into(),
                impact_weight: p.weight,
            });
        }
    }

    // A pillar stuck at LOCATED somewhere: targeted, never extracted.
    let mut stuck: std::collections::BTreeMap<String, (u32, u32)> =
        std::collections::BTreeMap::new();
    for doc in manifest.documents.values() {
        for (pillar_id, entry) in &doc.pillars {
            if entry.state.as_deref() == Some("LOCATED") && entry.extracted_pages == 0 {
                let slot = stuck.entry(pillar_id.clone()).or_insert((0, 0));
                slot.0 += 1;
                slot.1 = crate::manifest::PILLARS
                    .iter()
                    .find(|p| p.id == pillar_id.as_str())
                    .map_or(0, |p| p.weight);
            }
        }
    }
    for (pillar_id, (docs, weight)) in stuck {
        let name = crate::manifest::PILLARS
            .iter()
            .find(|p| p.id == pillar_id.as_str())
            .map_or(pillar_id.as_str(), |p| p.name);
        out.push(Gap {
            kind: GapKind::Extract,
            what: format!("{name} ({pillar_id})"),
            why: format!("{docs} document(s) targeted, nothing extracted"),
            impact_weight: weight,
        });
    }

    for axis in &inputs.knives.axes_expected {
        if !inputs.knives.axes_known.contains(axis) {
            out.push(Gap {
                kind: GapKind::Axis,
                what: axis.clone(),
                why: "the corpus does not answer this axis".into(),
                impact_weight: 2,
            });
        }
    }

    out.sort_by(|a, b| {
        b.impact_weight
            .cmp(&a.impact_weight)
            .then_with(|| a.what.cmp(&b.what))
    });
    out
}
