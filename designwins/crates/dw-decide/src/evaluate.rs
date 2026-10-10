//! Running the funnel: compare a cohort against typed constraints and produce
//! the explanation the acceptance test demands.
//!
//! This is the "compare @ your point" step. Given the atoms in force and what
//! is known about each candidate, it produces:
//!
//! * per-candidate judgements with evidence
//! * every exclusion with reason and evidence
//! * what could not be evaluated, as first-class unknowns
//!
//! **Nothing here calls a model and nothing here guesses.** A candidate whose
//! evidence does not cover the operating point is `Unknown`, and `Unknown`
//! never eliminates (`C9.1`).

use std::collections::BTreeMap;

use dw_normalize::{condition_covers, CondValue};
use dw_types::{Ineligible, SourceRef, Verdict};

use crate::{Atom, Axis, Candidate, Cohort, Exclusion, Judgement, MissingAxis, Op};

/// What is known about one candidate, on one axis.
#[derive(Debug, Clone, PartialEq)]
pub struct Evidence {
    /// The measured magnitude, when there is one.
    pub value: Option<f64>,
    /// The rating class of the printed value. An `absmax` never satisfies an
    /// operating atom (`C9.2`), which is why it must be carried.
    pub rating_allows_operating: bool,
    /// The typed condition the value was printed under.
    pub conditions: BTreeMap<String, CondValue>,
    /// Where the number came from. Every verdict carries these so the UI can
    /// open the page without a second call (§0, D7).
    pub sources: Vec<SourceRef>,
}

impl Evidence {
    /// Evidence with a value and its page.
    #[must_use]
    pub fn measured(value: f64, rating_allows_operating: bool, sources: Vec<SourceRef>) -> Self {
        Self {
            value: Some(value),
            rating_allows_operating,
            conditions: BTreeMap::new(),
            sources,
        }
    }

    /// Evidence we have no number for. Yields `Unknown`, never a guess.
    #[must_use]
    pub fn unknown(sources: Vec<SourceRef>) -> Self {
        Self {
            value: None,
            rating_allows_operating: false,
            conditions: BTreeMap::new(),
            sources,
        }
    }

    /// Attach the printed condition.
    #[must_use]
    pub fn with_conditions(mut self, conditions: BTreeMap<String, CondValue>) -> Self {
        self.conditions = conditions;
        self
    }
}

/// Everything the evaluator knows about one candidate.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct CandidateFacts {
    /// Per-axis evidence. An absent axis is `Unknown`, never `None`-means-ok.
    pub axes: BTreeMap<Axis, Evidence>,
}

/// The margin band inside which a shortfall is `NearMiss` rather than `Fail`.
///
/// Stored explicitly because `C9.3` forbids a silent default.
pub const DEFAULT_NEAR_MISS_PCT: f64 = 10.0;

/// Can a piece of evidence be opened?
///
/// This is the trust law in trait form. `dw-evidence::EvidenceStore`
/// implements it against real pixels; tests use [`AlwaysResolvable`].
///
/// It is a parameter of [`judge`] rather than a caller-side check **so it
/// cannot be forgotten**. A provenance check that lives at the call site is a
/// provenance check that gets skipped the first time someone is in a hurry.
pub trait Provenance {
    /// True when every source reference on this evidence resolves to a
    /// content-addressed page render and row-band crop.
    fn resolvable(&self, sources: &[SourceRef]) -> bool;
}

/// A resolver that accepts everything. For tests and for the period before a
/// store is wired in — and **only** for that.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct AlwaysResolvable;

impl Provenance for AlwaysResolvable {
    fn resolvable(&self, _: &[SourceRef]) -> bool {
        true
    }
}

/// Compare one evidence against one atom.
///
/// # The decision, in order
///
/// 1. No evidence → `Unknown`. Never eliminates.
/// 2. Evidence exists but the rating class forbids operating use (an
///    absolute maximum) → `Unknown`, because it is the wrong quantity and
///    cannot be compared at all (`C9.2`). It is **not** a `Fail`: the part may
///    still be fine, we simply have not established it.
/// 3. Evidence exists but its condition does not **cover** the required
///    operating point → `Unknown` (`C9.6`).
/// 4. Evidence that does not resolve to pixels is `Unknown` — **provenance
///    failure refuses an answer rather than passing one**
///    (`PROVENANCE.md` §4.1).
/// 5. Compare, with the margin applied to the **requirement** (`C9.3`).
#[must_use]
pub fn judge(evidence: Option<&Evidence>, atom: &Atom, provenance: &dyn Provenance) -> Verdict {
    let Some(ev) = evidence else {
        return Verdict::Unknown;
    };
    let Some(value) = ev.value else {
        return Verdict::Unknown;
    };

    // Trust gate: a number we cannot open is a number we cannot assert. This
    // is `C9.1` applied to provenance itself.
    if !provenance.resolvable(&ev.sources) {
        return Verdict::Unknown;
    }

    // C9.2: an absolute maximum is a survival boundary, not an operating
    // guarantee. The wrong quantity cannot be compared — say so, do not guess.
    if !ev.rating_allows_operating {
        return Verdict::Unknown;
    }

    // C9.6: the evidence condition must COVER the requirement's condition.
    if !atom.required_conditions.is_empty() {
        let coverage = condition_covers(Some(&ev.conditions), Some(&atom.required_conditions));
        if !coverage.may_satisfy() {
            return Verdict::Unknown;
        }
    }

    let required = atom.value.unwrap_or(f64::NEG_INFINITY);
    let inflated = match atom.margin_pct {
        Some(p) => required * (1.0 + p / 100.0),
        None => required,
    };
    let band = required.abs() * (DEFAULT_NEAR_MISS_PCT / 100.0);

    match atom.op {
        Op::MinRating => {
            if value >= inflated {
                Verdict::Pass
            } else if value >= required - band {
                Verdict::NearMiss
            } else {
                Verdict::Fail
            }
        }
        Op::MaxRating => {
            if value <= inflated {
                Verdict::Pass
            } else if value <= required + band {
                Verdict::NearMiss
            } else {
                Verdict::Fail
            }
        }
        Op::Covers => {
            // Range containment: the evidence must span the requirement.
            // Without a lower bound on the evidence we cannot establish it.
            if value >= required {
                Verdict::Pass
            } else if value >= required - band {
                Verdict::NearMiss
            } else {
                Verdict::Fail
            }
        }
    }
}

/// The result of running the funnel over a cohort.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct Evaluation {
    /// Per-candidate, per-atom verdicts with evidence.
    pub judgements: Vec<Judgement>,
    /// Every exclusion, with reason and evidence.
    pub exclusions: Vec<Exclusion>,
    /// Axes we could not evaluate, and how many candidates that affects.
    pub unknowns: Vec<MissingAxis>,
    /// Candidates that survived every hard atom.
    pub cohort: Cohort,
}

/// Run the constraints in force over a cohort.
///
/// Returns the judgements, exclusions, unknowns, and the surviving cohort —
/// everything the acceptance test names, in one pass, so a front end can render
/// the whole experience from one response (D7).
#[must_use]
pub fn evaluate(
    cohort: &Cohort,
    atoms: &[Atom],
    facts: &BTreeMap<Candidate, CandidateFacts>,
    provenance: &dyn Provenance,
) -> Evaluation {
    let mut eval = Evaluation::default();
    let mut unknown_counts: BTreeMap<Axis, usize> = BTreeMap::new();
    let mut survivors = Vec::new();

    for candidate in &cohort.members {
        let known = facts.get(candidate);
        let mut eliminated = false;

        for atom in atoms {
            let evidence = known.and_then(|f| f.axes.get(&atom.axis));
            let outcome = judge(evidence, atom, provenance);

            eval.judgements.push(Judgement {
                candidate: candidate.clone(),
                axis: atom.axis,
                outcome,
                evidence: evidence.map(|e| e.sources.clone()).unwrap_or_default(),
            });

            // Unresolvable evidence is an unknown too: we have a number we
            // cannot open, which is the same as not having established it.
            let unresolvable = evidence.is_some_and(|e| !provenance.resolvable(&e.sources));
            if evidence.is_none_or(|e| e.value.is_none()) || unresolvable {
                *unknown_counts.entry(atom.axis).or_insert(0) += 1;
            }

            // The elimination law, applied in exactly one place.
            if !eliminated && crate::Elimination::eliminates(outcome, atom.hard) {
                let reason = format!("{:?} failed at the required operating point", atom.axis);
                let sources = evidence.map(|e| e.sources.clone()).unwrap_or_default();
                // `Ineligible` is unconstructible without evidence; if we have
                // none, the exclusion cannot be motivated and must not be
                // recorded. That case is `Unknown`, not `Fail`, so it cannot
                // reach here.
                if let Ok(ineligible) = Ineligible::new(reason, sources) {
                    eval.exclusions.push(Exclusion {
                        candidate: candidate.clone(),
                        axis: atom.axis,
                        ineligible,
                        outcome,
                    });
                    eliminated = true;
                }
            }
        }

        if !eliminated {
            survivors.push(candidate.clone());
        }
    }

    for (axis, affected) in unknown_counts {
        eval.unknowns.push(MissingAxis {
            axis: Some(axis),
            phrase: None,
            why: "no evidence on this axis for these candidates".to_string(),
            affected,
        });
    }

    eval.cohort = Cohort::from_candidates(survivors);
    eval
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
    use crate::{ConfirmState, Derived, DesignContext, DesignIntent, Op, Requirement};
    use dw_types::{ReleaseId, Sha256};

    fn src() -> Vec<SourceRef> {
        let sha = Sha256::new("a".repeat(64)).unwrap();
        vec![SourceRef::page(sha, 2)]
    }

    fn vds_atom(hard: bool, margin: Option<f64>) -> Atom {
        Atom {
            axis: Axis::VdsRatingV,
            op: Op::MinRating,
            value: Some(36.0),
            hard,
            margin_pct: margin,
            required_conditions: BTreeMap::new(),
        }
    }

    fn cand(n: &str) -> Candidate {
        Candidate {
            part_number: n.to_string(),
            vendor: "ti".to_string(),
        }
    }

    /// C9.1: missing evidence is Unknown, and Unknown never eliminates.
    #[test]
    fn missing_evidence_never_eliminates() {
        let cohort = Cohort::from_candidates(vec![cand("A")]);
        let mut facts = BTreeMap::new();
        facts.insert(cand("A"), CandidateFacts::default());

        let eval = evaluate(&cohort, &[vds_atom(true, None)], &facts, &AlwaysResolvable);
        assert!(eval.exclusions.is_empty());
        assert_eq!(eval.cohort.len(), 1);
        assert_eq!(eval.unknowns.len(), 1);
        assert_eq!(eval.unknowns[0].affected, 1);
    }

    /// The trust gate (`PROVENANCE.md` §4.1): a number we cannot open is a
    /// number we cannot assert. Unresolvable evidence is `Unknown`, never
    /// `Pass` — provenance failure refuses an answer instead of passing one.
    #[test]
    fn unresolvable_evidence_is_unknown_not_pass() {
        struct NothingResolves;
        impl Provenance for NothingResolves {
            fn resolvable(&self, _: &[SourceRef]) -> bool {
                false
            }
        }

        let a = vds_atom(true, None);
        // Plenty of rating, plenty of condition coverage — and no pixels.
        let ev = Evidence::measured(1000.0, true, src());
        assert_eq!(
            judge(Some(&ev), &a, &NothingResolves),
            Verdict::Unknown,
            "an unopenable number must not become Pass"
        );
        // The same evidence with a working resolver does pass.
        assert_eq!(judge(Some(&ev), &a, &AlwaysResolvable), Verdict::Pass);
    }

    /// The trust gate is in the signature, not at a call site, so it cannot
    /// be skipped by someone in a hurry.
    #[test]
    fn evaluate_refuses_unresolvable_evidence() {
        struct NothingResolves;
        impl Provenance for NothingResolves {
            fn resolvable(&self, _: &[SourceRef]) -> bool {
                false
            }
        }

        let cohort = Cohort::from_candidates(vec![cand("A")]);
        let mut facts = BTreeMap::new();
        facts.insert(
            cand("A"),
            CandidateFacts {
                axes: BTreeMap::from([(Axis::VdsRatingV, Evidence::measured(40.0, true, src()))]),
            },
        );

        let eval = evaluate(&cohort, &[vds_atom(true, None)], &facts, &NothingResolves);
        assert!(eval.exclusions.is_empty(), "no proof, no elimination");
        assert_eq!(
            eval.cohort.len(),
            1,
            "nothing is eliminated without evidence"
        );
        assert_eq!(eval.unknowns.len(), 1, "and the gap is reported");
    }

    /// C9.2: an absolute maximum is the wrong quantity, so it is Unknown —
    /// not Fail. The part may be fine; we have not established it.
    #[test]
    fn absmax_evidence_is_unknown_not_fail() {
        let a = vds_atom(true, None);
        let ev = Evidence::measured(40.0, false, src());
        assert_eq!(judge(Some(&ev), &a, &AlwaysResolvable), Verdict::Unknown);
        assert!(!crate::Elimination::eliminates(Verdict::Unknown, true));
    }

    #[test]
    fn adequate_rating_passes_and_eliminates_nothing() {
        let cohort = Cohort::from_candidates(vec![cand("A"), cand("B")]);
        let mut facts = BTreeMap::new();
        facts.insert(
            cand("A"),
            CandidateFacts {
                axes: BTreeMap::from([(Axis::VdsRatingV, Evidence::measured(40.0, true, src()))]),
            },
        );
        facts.insert(
            cand("B"),
            CandidateFacts {
                axes: BTreeMap::from([(Axis::VdsRatingV, Evidence::measured(34.0, true, src()))]),
            },
        );

        let eval = evaluate(&cohort, &[vds_atom(true, None)], &facts, &AlwaysResolvable);
        assert!(
            eval.exclusions.is_empty(),
            "34 is a near-miss at 10%, not a fail"
        );
        assert_eq!(eval.cohort.len(), 2);
    }

    /// C9.3: near-miss is a separate cohort, not a Fail.
    #[test]
    fn near_miss_is_not_fail() {
        let a = vds_atom(true, None);
        assert_eq!(
            judge(
                Some(&Evidence::measured(34.0, true, src())),
                &a,
                &AlwaysResolvable
            ),
            Verdict::NearMiss
        );
        assert_eq!(
            judge(
                Some(&Evidence::measured(30.0, true, src())),
                &a,
                &AlwaysResolvable
            ),
            Verdict::Fail
        );
        assert_eq!(
            judge(
                Some(&Evidence::measured(36.0, true, src())),
                &a,
                &AlwaysResolvable
            ),
            Verdict::Pass
        );
    }

    /// C9.3: margin inflates the requirement, so a claim that passed raw can
    /// become a near-miss under margin.
    #[test]
    fn margin_is_applied_to_the_requirement() {
        let none = vds_atom(true, None);
        let pct20 = vds_atom(true, Some(20.0));
        let ev = Evidence::measured(40.0, true, src());
        assert_eq!(judge(Some(&ev), &none, &AlwaysResolvable), Verdict::Pass);
        assert_eq!(
            judge(Some(&ev), &pct20, &AlwaysResolvable),
            Verdict::NearMiss
        );
    }

    /// C9.6: the evidence condition must COVER the required operating point.
    #[test]
    fn uncovered_condition_is_unknown() {
        let mut a = vds_atom(true, None);
        a.required_conditions
            .insert("ta_c".to_string(), CondValue::Scalar(125.0));
        let ev = Evidence::measured(40.0, true, src()).with_conditions(BTreeMap::from([(
            "ta_c".to_string(),
            CondValue::Scalar(25.0),
        )]));
        assert_eq!(judge(Some(&ev), &a, &AlwaysResolvable), Verdict::Unknown);
    }

    #[test]
    fn covered_condition_is_comparable() {
        let mut a = vds_atom(true, None);
        a.required_conditions
            .insert("ta_c".to_string(), CondValue::Scalar(25.0));
        let ev = Evidence::measured(40.0, true, src()).with_conditions(BTreeMap::from([(
            "ta_c".to_string(),
            CondValue::Scalar(25.0),
        )]));
        assert_eq!(judge(Some(&ev), &a, &AlwaysResolvable), Verdict::Pass);
    }

    /// A soft atom never eliminates, however badly it fails.
    #[test]
    fn soft_atoms_never_eliminate() {
        let cohort = Cohort::from_candidates(vec![cand("A")]);
        let mut facts = BTreeMap::new();
        facts.insert(
            cand("A"),
            CandidateFacts {
                axes: BTreeMap::from([(Axis::VdsRatingV, Evidence::measured(1.0, true, src()))]),
            },
        );
        let eval = evaluate(&cohort, &[vds_atom(false, None)], &facts, &AlwaysResolvable);
        assert!(eval.exclusions.is_empty());
        assert_eq!(eval.cohort.len(), 1);
    }

    /// Every exclusion carries reason and evidence, so the UI can open it.
    #[test]
    fn exclusions_are_clickable_open() {
        let cohort = Cohort::from_candidates(vec![cand("A")]);
        let mut facts = BTreeMap::new();
        facts.insert(
            cand("A"),
            CandidateFacts {
                axes: BTreeMap::from([(Axis::VdsRatingV, Evidence::measured(1.0, true, src()))]),
            },
        );
        let eval = evaluate(&cohort, &[vds_atom(true, None)], &facts, &AlwaysResolvable);
        assert_eq!(eval.exclusions.len(), 1);
        let e = &eval.exclusions[0];
        assert!(!e.ineligible.reason().is_empty());
        assert!(
            !e.ineligible.evidence().is_empty(),
            "D7: exclusion must open to a page"
        );
    }

    /// The full acceptance-test shape, end to end.
    #[test]
    fn acceptance_shape_is_complete() {
        use crate::Build;
        let cohort = Cohort::from_candidates(vec![cand("A"), cand("B")]);
        let mut facts = BTreeMap::new();
        facts.insert(
            cand("A"),
            CandidateFacts {
                axes: BTreeMap::from([(Axis::VdsRatingV, Evidence::measured(40.0, true, src()))]),
            },
        );
        facts.insert(
            cand("B"),
            CandidateFacts {
                axes: BTreeMap::from([(Axis::VdsRatingV, Evidence::measured(1.0, true, src()))]),
            },
        );
        let eval = evaluate(&cohort, &[vds_atom(true, None)], &facts, &AlwaysResolvable);

        let build = Build {
            id: "b".into(),
            release: ReleaseId::new("r1").unwrap(),
            intent: DesignIntent::Text("36 V input supply".into()),
            context: DesignContext::default(),
            declared: vec![Requirement {
                id: "r".into(),
                verbatim: "must survive 36 V".into(),
                atoms: vec![vds_atom(true, None)],
            }],
            derived: vec![Derived {
                id: "d".into(),
                rationale: "context implies automotive temperature".into(),
                atoms: vec![],
                state: ConfirmState::Pending,
            }],
            constraints: vec![],
            cohort: eval.cohort.clone(),
            explain: eval.judgements.clone(),
            exclusions: eval.exclusions.clone(),
            unknowns: eval.unknowns.clone(),
            next: vec![],
        };

        // Every part of the acceptance test is present.
        assert_eq!(build.cohort.len(), 1, "cohort of distinct parts");
        assert!(!build.explain.is_empty(), "which constraints were applied");
        assert_eq!(build.exclusions.len(), 1, "every exclusion with evidence");
        assert!(
            build.derived[0].state == ConfirmState::Pending,
            "derived shown"
        );
        let s = build.summary();
        assert!(s.contains("cohort       1 distinct parts"));
    }
}
