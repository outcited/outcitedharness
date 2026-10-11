//! Running the whole funnel from one sentence.
//!
//! This is `PLAN.md` §0, made executable:
//!
//! > A human types one sentence about what they are building. The system
//! > returns a cohort, shows which constraints were derived and confirmed,
//! > names every exclusion with its evidence, lists what it could not
//! > evaluate, and suggests the next question that would narrow it most.
//! > Every number opens to the page it came from.
//!
//! [`accept`] does exactly that in one call. It is deliberately the only
//! entry point: everything else in the workspace exists to serve it.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

use std::collections::BTreeMap;

use dw_decide::{
    evaluate, AlwaysResolvable, Build, Candidate, CandidateFacts, Cohort, ConfirmState, Derived,
    DesignContext, DesignIntent,
};
use dw_types::ReleaseId;

/// What one sentence produces.
#[derive(Debug, Clone, PartialEq)]
pub struct Answer {
    /// The full build, in the shape every front end renders from.
    pub build: Build,
    /// What the phrase bound to, and what it did not.
    pub bound: dw_knives::phrase::Bound,
}

/// Run the whole funnel from one sentence.
///
/// # The pipeline
///
/// ```text
/// sentence  ->  dw-knives::phrase::bind   (colloquial -> knives)
///           ->  atoms + residue
///           ->  dw-decide::evaluate       (compare @ your point)
///           ->  Build { cohort, explain, exclusions, unknowns, next }
/// ```
///
/// Nothing here calls a model. Nothing here guesses. A phrase that binds to
/// nothing produces a [`dw_decide::MissingAxis`] and a [`dw_decide::Question`], never a
/// silent miss (D5).
#[must_use]
pub fn accept(
    sentence: &str,
    registry: &dw_knives::PhraseRegistry,
    cohort: &Cohort,
    facts: &BTreeMap<Candidate, CandidateFacts>,
    release: ReleaseId,
) -> Answer {
    let bound = registry.bind(sentence);
    let (mut unknowns, mut questions) = dw_knives::unknowns_from_residue(&bound, cohort.len());

    let eval = evaluate(cohort, &bound.atoms, facts, &AlwaysResolvable);
    unknowns.extend(eval.unknowns.clone());

    // Rank the questions by expected cohort reduction: prefer the ones whose
    // axis we could not evaluate across the most candidates. Until measured
    // data exists per axis, `affected` is the honest proxy and is stated as
    // such rather than invented as a percentage.
    questions.sort_by(|a, b| {
        let ea = unknowns
            .iter()
            .filter(|u| u.axis.as_ref() == Some(&a.axis))
            .map(|u| u.affected)
            .sum::<usize>();
        let eb = unknowns
            .iter()
            .filter(|u| u.axis.as_ref() == Some(&b.axis))
            .map(|u| u.affected)
            .sum::<usize>();
        eb.cmp(&ea)
    });

    let build = Build {
        id: format!("build:{}", 0),
        release,
        intent: DesignIntent::Text(sentence.to_string()),
        context: DesignContext::default(),
        declared: vec![],
        derived: bound
            .bindings
            .iter()
            .map(|b| Derived {
                id: b.id.clone(),
                rationale: format!(
                    "{:?} bound to {} knife(s) at confidence {:.2} ({:?})",
                    b.phrase,
                    b.knives.len(),
                    b.confidence,
                    b.stamp
                ),
                atoms: b.contribute(),
                state: ConfirmState::Pending,
            })
            .collect(),
        constraints: bound.atoms.clone(),
        cohort: eval.cohort.clone(),
        explain: eval.judgements.clone(),
        exclusions: eval.exclusions.clone(),
        unknowns,
        next: questions,
    };

    Answer { build, bound }
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
    use dw_decide::Op;
    use dw_decide::{Constraint, Evidence, KnifeId};
    use dw_knives::{AtomTemplate, Drawer, Stamp};
    use dw_types::Sha256;

    fn src() -> Vec<dw_types::SourceRef> {
        vec![dw_types::SourceRef::page(
            Sha256::new("a".repeat(64)).unwrap(),
            2,
        )]
    }

    fn candidate(n: &str) -> Candidate {
        Candidate {
            part_number: n.to_string(),
            vendor: "ti".to_string(),
        }
    }

    /// PLAN §0, end to end, in one call.
    #[test]
    fn acceptance_test_sentence_to_cohort() {
        let mut registry = dw_knives::PhraseRegistry::new();
        registry.insert(
            dw_knives::ColloquialBinding::new(
                "bluetooth",
                "bluetooth",
                vec![AtomTemplate {
                    knife: KnifeId::Axis(dw_decide::Axis::HasWirelessBle),
                    constraint: Constraint::Scalar {
                        op: Op::MinRating,
                        value: 1.0,
                        margin_pct: None,
                    },
                    hard: true,
                    drawer: Drawer::Applications,
                }],
                0.9,
                vec!["holes.json#bluetooth".into()],
                Stamp::Blocked("nordic 1/31 true".into()),
            )
            .expect("sourced"),
        );

        let cohort = Cohort::from_candidates(vec![candidate("A"), candidate("B")]);
        let mut facts = BTreeMap::new();
        facts.insert(
            candidate("A"),
            CandidateFacts {
                axes: BTreeMap::from([(
                    dw_decide::Axis::VdsRatingV,
                    Evidence::measured(40.0, true, src()),
                )]),
                ..Default::default()
            },
        );

        let answer = accept(
            "an outdoor weather thermostat bluetooth",
            &registry,
            &cohort,
            &facts,
            ReleaseId::new("designwins-2026-W46").unwrap(),
        );

        // "returns a cohort" — distinct parts, even when nothing eliminated.
        assert_eq!(answer.build.cohort.len(), 2);

        // "shows which constraints were derived and confirmed" — every binding
        // surfaces as a Derived, and none is confirmed without the human.
        assert_eq!(answer.build.derived.len(), 1);
        assert_eq!(answer.build.derived[0].state, ConfirmState::Pending);
        assert!(
            answer.build.derived[0].rationale.contains("bluetooth"),
            "a derivation without a rationale is silent: {}",
            answer.build.derived[0].rationale
        );

        // "names every exclusion with its evidence" — none here, because the
        // only binding is Blocked and a Blocked binding may not eliminate.
        assert!(answer.build.exclusions.is_empty());

        // "lists what it could not evaluate" — the blocked binding AND the
        // unmapped residue both surface.
        assert!(
            answer
                .build
                .unknowns
                .iter()
                .any(|u| u.why.contains("nordic")),
            "the measured reason must survive into the answer"
        );
        assert!(
            answer.build.unknowns.iter().any(|u| u
                .phrase
                .as_deref()
                .is_some_and(|p| p.contains("outdoor weather thermostat"))),
            "the unmapped span must surface, not vanish: {:?}",
            answer.build.unknowns
        );

        // "suggests the next question" — every unknown gets one.
        assert!(!answer.build.next.is_empty());

        // "every number opens to the page it came from" — judgements carry refs.
        assert!(answer
            .build
            .explain
            .iter()
            .all(|j| !j.evidence.is_empty() || j.outcome == dw_types::Verdict::Unknown));
    }

    /// A proven-fit binding actually narrows the cohort and explains why.
    #[test]
    fn proven_fit_narrows_and_explains() {
        let mut registry = dw_knives::PhraseRegistry::new();
        registry.insert(
            dw_knives::ColloquialBinding::new(
                "car",
                "talks to a car",
                vec![AtomTemplate {
                    knife: KnifeId::Axis(dw_decide::Axis::HasCan),
                    constraint: Constraint::Scalar {
                        op: Op::MinRating,
                        // A boolean capability is modelled as "at least 1": a
                        // candidate with 0 does not have it. An atom with no
                        // threshold can never fail, so it can never narrow.
                        value: 1.0,
                        margin_pct: None,
                    },
                    hard: true,
                    drawer: Drawer::Applications,
                }],
                0.9,
                vec!["holes.json#talks_to_a_car".into()],
                Stamp::ProvenFit,
            )
            .expect("sourced"),
        );

        let cohort = Cohort::from_candidates(vec![candidate("A"), candidate("B")]);
        let mut facts = BTreeMap::new();
        facts.insert(
            candidate("A"),
            CandidateFacts {
                axes: BTreeMap::from([(
                    dw_decide::Axis::HasCan,
                    Evidence::measured(1.0, true, src()),
                )]),
                ..Default::default()
            },
        );
        facts.insert(
            candidate("B"),
            CandidateFacts {
                axes: BTreeMap::from([(
                    dw_decide::Axis::HasCan,
                    Evidence::measured(0.0, true, src()),
                )]),
                ..Default::default()
            },
        );

        let answer = accept(
            "talks to a car",
            &registry,
            &cohort,
            &facts,
            ReleaseId::new("r").unwrap(),
        );

        assert_eq!(answer.build.cohort.len(), 1, "B is eliminated");
        assert_eq!(answer.build.exclusions.len(), 1);
        let e = &answer.build.exclusions[0];
        assert_eq!(e.candidate.part_number, "B");
        assert!(!e.ineligible.reason().is_empty());
        assert!(
            !e.ineligible.evidence().is_empty(),
            "D7: exclusion opens to a page"
        );
    }

    /// The summary is what a UI would show before drilling in.
    #[test]
    fn summary_is_renderable() {
        let registry = dw_knives::seed_from_measured_chips();
        let cohort = Cohort::from_candidates(vec![candidate("A")]);
        let facts = BTreeMap::new();
        let answer = accept(
            "outdoor weather thermostat bluetooth",
            &registry,
            &cohort,
            &facts,
            ReleaseId::new("r").unwrap(),
        );
        let s = answer.build.summary();
        assert!(s.contains("cohort       1 distinct parts"));
        assert!(s.contains("unknowns"));
    }
}
