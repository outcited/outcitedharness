//! The category graph — core family as containment, not magnitude.
//!
//! `PRD-PHRASE` §8.1 and `PLAN.md` §6. A core family is a **graph node**:
//!
//! ```text
//! AM13E230  ⊂  AM13E230x  ⊂  AM13x  ⊂  AM1x
//! Cortex-M4F ⊂ Cortex-M    ⊂ ARM
//! ```
//!
//! Membership is containment, so `Cortex-M4F` satisfies `Within(Cortex-M)`
//! while `Cortex-M0` does not satisfy `Within(Cortex-M4)`. That is why
//! [`CategoryOp`] has `Is`/`Within`/`Excludes` and **no `MinRating`** — a
//! minimum on a core family is meaningless and `dw-decide` refuses to express
//! it.
//!
//! ## Materialised closure
//!
//! The graph is stored as direct parent edges **plus** a materialised ancestor
//! set per OPN. That costs memory and buys the evaluator simplicity:
//! `CategoryOp::Within` is a set lookup and no graph has to be threaded
//! through `dw_decide::judge`.
//!
//! ## Provenance
//!
//! Every assignment came from a family document that printed it, with a row
//! receipt (`page`, `row_label`, `column_index`) — see
//! `results/family-matrix-*-20260908/records.jsonl`. [`Assignment`] carries
//! those source refs so a category-based exclusion satisfies §16.5 exactly as
//! a scalar one does.

#![allow(missing_docs)]

use std::collections::{BTreeMap, BTreeSet};

use dw_decide::{Candidate, CandidateFacts, CategoryId, CategoryMembership, DimensionId};
use dw_types::SourceRef;
use serde::{Deserialize, Serialize};

/// The measured category graph.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CategoryGraph {
    /// Direct parent edges: child → its immediate ancestors.
    pub parents: BTreeMap<String, Vec<String>>,
    /// Every OPN the graph knows, with its leaf and materialised ancestors.
    pub opn_closure: BTreeMap<String, Closure>,
    /// Node kinds, for reporting.
    pub node_kinds: BTreeMap<String, usize>,
}

/// Where one OPN sits in the graph.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Closure {
    /// The most specific node.
    pub leaf: String,
    /// Every ancestor of `leaf`, including the intermediate series tokens.
    pub ancestors: Vec<String>,
}

/// A category assignment with its provenance.
#[derive(Debug, Clone, PartialEq)]
pub struct Assignment {
    /// Which OPN.
    pub candidate: Candidate,
    /// The most specific node.
    pub leaf: CategoryId,
    /// Materialised ancestors.
    pub ancestors: BTreeSet<CategoryId>,
    /// Where it was printed. Required for any exclusion (§16.5).
    pub evidence: Vec<SourceRef>,
}

impl CategoryGraph {
    /// Load the graph from `tests/fixtures/gold/category_graph_v1.json`.
    ///
    /// # Errors
    ///
    /// Returns `Err` naming the path when the fixture is absent or malformed.
    pub fn load_json(text: &str) -> Result<Self, String> {
        serde_json::from_str(text).map_err(|e| format!("category graph: {e}"))
    }

    /// How many nodes the graph holds.
    #[must_use]
    pub fn node_count(&self) -> usize {
        self.node_kinds.values().sum()
    }

    /// The ancestor closure of a node, following parent edges.
    #[must_use]
    pub fn ancestors_of(&self, node: &str) -> BTreeSet<String> {
        let mut out = BTreeSet::new();
        let mut stack: Vec<String> = self.parents.get(node).cloned().unwrap_or_default();
        while let Some(n) = stack.pop() {
            if out.insert(n.clone()) {
                if let Some(ps) = self.parents.get(&n) {
                    stack.extend(ps.iter().cloned());
                }
            }
        }
        out
    }

    /// Build [`CandidateFacts`] category fields for one OPN.
    ///
    /// `evidence` is the family document that printed the assignment. Without
    /// it the assignment is recorded but **cannot eliminate** — §16.5 makes an
    /// exclusion unconstructible without evidence, which is the correct
    /// behaviour rather than a gap.
    #[must_use]
    pub fn facts_for(
        &self,
        opn: &str,
        evidence: Vec<SourceRef>,
    ) -> Option<(CategoryId, BTreeSet<CategoryId>, Vec<SourceRef>)> {
        let c = self.opn_closure.get(opn)?;
        let leaf = CategoryId::new(c.leaf.clone()).ok()?;
        let ancestors = c
            .ancestors
            .iter()
            .filter_map(|a| CategoryId::new(a.clone()).ok())
            .collect();
        Some((leaf, ancestors, evidence))
    }

    /// Fold every OPN in the graph into a `Candidate` → `CandidateFacts` map,
    /// in the **`core_family`** dimension.
    ///
    /// `evidence_for` supplies the provenance per OPN; return `Vec::new()` for
    /// an OPN whose source document is unknown, which leaves it unable to
    /// eliminate.
    #[must_use]
    pub fn facts_map<F>(&self, mut evidence_for: F) -> BTreeMap<Candidate, CandidateFacts>
    where
        F: FnMut(&str) -> Vec<SourceRef>,
    {
        let mut graphs = CategoryGraphs::default();
        graphs.insert(DimensionId::core_family(), self.clone());
        graphs.facts_map(|_dim, opn| evidence_for(opn))
    }
}

/// N category graphs, one per **dimension** (M4 contract gap #2/#3).
///
/// Each dimension is an independent containment namespace with its own
/// measured provenance. Dimensions **never merge**: a node name that appears
/// in both `core_family` and `package` is two disjoint memberships, and a
/// candidate missing a dimension judges `Unknown` for it (never eliminated).
#[derive(Debug, Clone, PartialEq, Default)]
pub struct CategoryGraphs {
    /// Per-dimension measured graphs.
    pub dimensions: BTreeMap<DimensionId, CategoryGraph>,
}

impl CategoryGraphs {
    /// Register (or replace) one dimension's graph.
    pub fn insert(&mut self, dim: DimensionId, graph: CategoryGraph) {
        self.dimensions.insert(dim, graph);
    }

    /// The dimensions present.
    pub fn dimensions(&self) -> impl Iterator<Item = &DimensionId> {
        self.dimensions.keys()
    }

    /// Fold every dimension into one `Candidate` → `CandidateFacts` map.
    ///
    /// `evidence_for` supplies the provenance per `(dimension, opn)`. Each
    /// dimension writes its own [`CategoryMembership`] into the candidate's
    /// `dimensions` map; no dimension can overwrite another.
    #[must_use]
    pub fn facts_map<F>(&self, mut evidence_for: F) -> BTreeMap<Candidate, CandidateFacts>
    where
        F: FnMut(&DimensionId, &str) -> Vec<SourceRef>,
    {
        let mut out: BTreeMap<Candidate, CandidateFacts> = BTreeMap::new();
        for (dim, graph) in &self.dimensions {
            for opn in graph.opn_closure.keys() {
                let Some((leaf, ancestors, evidence)) =
                    graph.facts_for(opn, evidence_for(dim, opn))
                else {
                    continue;
                };
                let candidate = Candidate {
                    part_number: opn.clone(),
                    vendor: String::new(),
                };
                out.entry(candidate).or_default().dimensions.insert(
                    dim.clone(),
                    CategoryMembership::new(Some(leaf), ancestors, evidence),
                );
            }
        }
        out
    }
}

#[cfg(test)]
#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    clippy::match_wildcard_for_single_variants,
    clippy::wildcard_enum_match_arm,
    clippy::too_many_lines
)]
mod tests {
    use super::*;

    fn graph() -> CategoryGraph {
        let mut dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        loop {
            let p = dir.join("tests/fixtures/gold/category_graph_v1.json");
            if p.exists() {
                let text = std::fs::read_to_string(&p).expect("graph fixture present");
                return CategoryGraph::load_json(&text).expect("parses");
            }
            assert!(dir.pop(), "could not locate tests/fixtures/gold");
        }
    }

    #[test]
    fn nesting_is_containment_not_equality() {
        let g = graph();
        let anc = g.ancestors_of("AM13E230");
        assert!(anc.contains("AM13E230x"), "{anc:?}");
        assert!(anc.contains("AM13x"), "{anc:?}");
        assert!(anc.contains("AM1x"), "{anc:?}");
        // …and the reverse does not hold: AM1x does not contain AM13E230.
        let root_anc = g.ancestors_of("AM1x");
        assert!(!root_anc.contains("AM13E230"));
    }

    #[test]
    fn within_is_a_set_lookup() {
        let g = graph();
        let (leaf, ancestors, _) = g.facts_for("AM13E23019", vec![]).expect("opn known");
        assert_eq!(leaf.as_str(), "AM13E230");
        assert!(ancestors.contains(&CategoryId::new("AM13x").unwrap()));

        let mut facts = CandidateFacts::default();
        facts.dimensions.insert(
            DimensionId::core_family(),
            CategoryMembership::new(Some(leaf), ancestors, vec![]),
        );
        let m4 = CategoryId::new("AM13x").unwrap();
        let other = CategoryId::new("AM1x").unwrap();
        assert_eq!(
            facts.category_matches(dw_decide::CategoryOp::Within, &m4),
            Some(true)
        );
        assert_eq!(
            facts.category_matches(dw_decide::CategoryOp::Is, &m4),
            Some(false),
            "AM13E230 is inside AM13x but is not AM13x"
        );
        assert_eq!(
            facts.category_matches(dw_decide::CategoryOp::Excludes, &other),
            Some(false)
        );
    }

    /// A candidate with no category recorded is `Unknown`, never a guess.
    #[test]
    fn no_category_is_unknown_not_false() {
        let facts = CandidateFacts::default();
        assert_eq!(
            facts.category_matches(
                dw_decide::CategoryOp::Within,
                &CategoryId::new("AM13x").unwrap()
            ),
            None
        );
        assert_eq!(
            dw_decide::evaluate::judge_category(
                Some(&facts),
                dw_decide::CategoryOp::Within,
                &CategoryId::new("AM13x").unwrap()
            ),
            dw_types::Verdict::Unknown
        );
    }

    /// §16.5 holds for categories too: no evidence, no elimination.
    #[test]
    fn category_exclusion_needs_evidence() {
        use dw_types::Ineligible;
        assert!(Ineligible::new("not AM13x", vec![]).is_err());
    }

    /// **Negative control** (`ASSURANCE.md` R2).
    ///
    /// `CategoryOp` must distinguish containment from equality. If `Is` returned
    /// true for any ancestor, an "ARM Cortex-M4" requirement would match a
    /// Cortex-M0 — and the whole reason the type exists is to refuse that.
    #[test]
    fn negative_control_is_is_not_within() {
        let g = graph();
        let (leaf, ancestors, _) = g.facts_for("AM13E23019", vec![]).expect("opn known");
        let mut facts = CandidateFacts::default();
        facts.dimensions.insert(
            DimensionId::core_family(),
            CategoryMembership::new(Some(leaf.clone()), ancestors, vec![]),
        );

        let root = CategoryId::new("AM1x").unwrap();
        let self_id = leaf.clone();

        // The candidate IS AM13E230 and is WITHIN AM1x.
        assert_eq!(
            facts.category_matches(dw_decide::CategoryOp::Is, &self_id),
            Some(true)
        );
        assert_eq!(
            facts.category_matches(dw_decide::CategoryOp::Within, &root),
            Some(true)
        );
        // …but it is NOT AM1x. If `Is` matched ancestors, this would be true and
        // the category type would be doing nothing.
        assert_eq!(
            facts.category_matches(dw_decide::CategoryOp::Is, &root),
            Some(false),
            "Is must not match an ancestor"
        );
        assert_eq!(
            facts.category_matches(dw_decide::CategoryOp::Excludes, &root),
            Some(false)
        );
    }

    /// M4 contract gap #2: `core_family` and `package` are **separate
    /// namespaces that never merge**, and a candidate lacking a dimension
    /// judges `Unknown` for it — never eliminated.
    ///
    /// The package fixtures here carry **synthetic provenance** and exist for
    /// this mechanism test only; no package taxonomy is authored.
    #[test]
    fn dimensions_never_merge_and_missing_dimension_is_unknown() {
        let g = graph();
        // A synthetic one-node package graph: `QFN48 ⊂ QFN`, tagged with a
        // fake but type-correct provenance (tests only).
        let package = CategoryGraph {
            parents: BTreeMap::from([("QFN48".to_string(), vec!["QFN".to_string()])]),
            opn_closure: BTreeMap::from([(
                "AM13E23019".to_string(),
                Closure {
                    leaf: "QFN48".to_string(),
                    ancestors: vec!["QFN".to_string()],
                },
            )]),
            node_kinds: BTreeMap::from([("package".to_string(), 2)]),
        };

        let mut graphs = CategoryGraphs::default();
        graphs.insert(DimensionId::core_family(), g);
        graphs.insert(DimensionId::package(), package);
        let facts = graphs.facts_map(|_dim, _opn| vec![]);
        let f = facts
            .get(&Candidate {
                part_number: "AM13E23019".into(),
                vendor: String::new(),
            })
            .expect("opn present in both dimensions");

        // The same OPN is a core family AND a package — two disjoint facts.
        assert_eq!(
            f.category_matches_in(
                &DimensionId::core_family(),
                dw_decide::CategoryOp::Within,
                &CategoryId::new("AM13x").unwrap()
            ),
            Some(true)
        );
        assert_eq!(
            f.category_matches_in(
                &DimensionId::package(),
                dw_decide::CategoryOp::Within,
                &CategoryId::new("QFN").unwrap()
            ),
            Some(true)
        );
        // …and the namespaces do not bleed: AM13x is not a package node.
        assert_eq!(
            f.category_matches_in(
                &DimensionId::package(),
                dw_decide::CategoryOp::Within,
                &CategoryId::new("AM13x").unwrap()
            ),
            Some(false)
        );

        // A candidate with only core_family is Unknown on package — never
        // eliminated, never false.
        let mut only_core = CandidateFacts::default();
        only_core.dimensions.insert(
            DimensionId::core_family(),
            CategoryMembership::new(
                Some(CategoryId::new("AM13E230").unwrap()),
                BTreeSet::new(),
                vec![],
            ),
        );
        assert_eq!(
            only_core.category_matches_in(
                &DimensionId::package(),
                dw_decide::CategoryOp::Within,
                &CategoryId::new("QFN").unwrap()
            ),
            None
        );
        assert_eq!(
            dw_decide::judge_category_in(
                Some(&only_core),
                &DimensionId::package(),
                dw_decide::CategoryOp::Within,
                &CategoryId::new("QFN").unwrap()
            ),
            dw_types::Verdict::Unknown
        );
    }
}
