//! Question ranking by **measured** cohort reduction.
//!
//! Port of `harness/discovery/service.py::next_question` (spec 29–30) —
//! ported, not re-derived. The algorithm, exactly as the reference runs it:
//!
//! 1. for every registry axis the design has not pinned,
//! 2. collect the **best claim value per part**; none → `askable: false`,
//! 3. propose the **median** as the screening value,
//! 4. probe: how many parts would **FAIL** if the median became a hard
//!    requirement (`UNKNOWN` never eliminates — spec section 7),
//! 5. `expected_reduction_pct = 100 · fail / max(considered, 1)`,
//! 6. rank by **absolute fail count** (not the percentage), stable — so
//!    registry declaration order breaks ties observably.
//!
//! ## Mapping to the typed layer
//!
//! The reference works on claim rows (`axis_for_symbol`); this port works on
//! [`CandidateFacts`], where extraction already chose the per-axis evidence.
//! Two reference guards therefore live upstream here: the rating-class filter
//! (`accepts_classes`) and the Min-column wrong-bound guard are applied when
//! facts are built. What remains claim-level in the reference and is
//! restated here: **a single scalar bound never proves containment** —
//! `covers` probes are `Unknown` at this layer because [`Evidence::value`] is
//! one number (the reference's `single_bound` rule).
//!
//! Where the workspace has ratified laws, the laws win over the reference:
//! C9.2 (absmax ⇒ `Unknown`) is enforced through [`judge`] and is *not*
//! softened to match the older claim-level evaluator.

use std::collections::{BTreeMap, BTreeSet};

use dw_types::Verdict;

use crate::evaluate::{judge, AlwaysResolvable, CandidateFacts};
use crate::{Atom, Axis, Constraint, KnifeId, Op, Question};

/// One axis the ranking can ask about: its probe op and its help prose.
struct ProbeAxis {
    axis: Axis,
    op: Op,
    help: &'static str,
}

/// The registry, in the **reference's declaration order** (`AXES` in
/// `evaluator.py`). The final sort is stable, so this order decides ties.
///
/// The help prose is the reference's `AXIS_HELP` verbatim — including the
/// unformatted `%`-template in `vds_rating_v`, which the reference emits
/// as-is. Fixing it here would be re-deriving, and the wire is compared.
const PROBE_AXES: &[ProbeAxis] = &[
    ProbeAxis {
        axis: Axis::VdsRatingV,
        op: Op::MinRating,
        help: "The blocking voltage the switch must survive. For a 24 V \
               industrial rail (18-36 V) pick at least the 36 V maximum, plus \
               margin you choose (a 20%% margin makes the effective \
               requirement %.1f V).",
    },
    ProbeAxis {
        axis: Axis::IdContinuousA,
        op: Op::MinRating,
        help: "Continuous current the device must carry at temperature. \
               Datasheets derate: a part rated 5 A at TC=25C may carry far \
               less at 100C — add a required condition to pin the evidence.",
    },
    ProbeAxis {
        axis: Axis::RdsOnOhm,
        op: Op::MaxRating,
        help: "Maximum on-resistance (conduction loss driver). Softer \
               constraint: fails score lower, never eliminate.",
    },
    ProbeAxis {
        axis: Axis::TjMaxC,
        op: Op::MinRating,
        help: "Maximum junction temperature the part is rated for. 125C is \
               common; 150C+ for automotive/industrial.",
    },
    ProbeAxis {
        axis: Axis::TstgRangeC,
        op: Op::Covers,
        help: "The storage/operating temperature span that must contain your \
               environment.",
    },
];

/// Ratio of two small counts, as the reference computes it
/// (`100 · fail / max(considered, 1)`, one decimal).
///
/// `usize as f64` is exact below 2^53, and these are per-axis claim counts
/// over a corpus — never close to that. Isolated so the cast is written once,
/// with the reason.
#[allow(
    clippy::cast_precision_loss,
    reason = "small corpus counts; exact below 2^53"
)]
fn reduction_pct(fail: usize, considered: usize) -> f64 {
    (100.0 * fail as f64 / considered.max(1) as f64 * 10.0).round() / 10.0
}

/// The verdict a probe produces at the typed layer.
///
/// `min_rating` / `max_rating` go through [`judge`] (margin on the
/// requirement, C9.2, C9.6, provenance). `covers` is `Unknown`: containment
/// needs two bounds and typed evidence is one number — the reference's
/// `single_bound` rule, and why `covers` probes never `FAIL` anyone.
fn probe_verdict(facts: Option<&crate::evaluate::Evidence>, probe: &Atom, op: Op) -> Verdict {
    if matches!(op, Op::Covers) {
        return Verdict::Unknown;
    }
    judge(facts, probe, &AlwaysResolvable)
}

/// Rank unanswered axes by expected cohort reduction, exactly as
/// `service.next_question` does over the claim corpus.
///
/// `pinned` is what the design already answers (its active atoms); those
/// axes are skipped entirely, as in the reference.
#[must_use]
pub fn next_questions(
    population: &BTreeMap<crate::Candidate, CandidateFacts>,
    pinned: &BTreeSet<Axis>,
) -> Vec<Question> {
    let mut questions = Vec::new();
    for spec in PROBE_AXES {
        if pinned.contains(&spec.axis) {
            continue;
        }
        let mut values: Vec<f64> = population
            .values()
            .filter_map(|facts| facts.axes.get(&spec.axis).and_then(|e| e.value))
            .collect();
        values.sort_by(f64::total_cmp);
        let Some(&median) = values.get(values.len() / 2) else {
            // No part answers this axis. The reference still reports the
            // question — "we cannot ask yet" is information (D5's honesty),
            // and asking it is how the axis starts getting answered.
            questions.push(Question {
                axis: KnifeId::Axis(spec.axis),
                prompt: format!(
                    "What is the {:?} requirement? (nothing measured yet)",
                    spec.axis
                ),
                help: spec.help.to_string(),
                expected_reduction_pct: 0.0,
                askable: false,
                proposed_value: None,
                parts_with_claims: 0,
                expected_fail_at_median: 0,
            });
            continue;
        };
        let probe = Atom {
            knife: KnifeId::Axis(spec.axis),
            constraint: Constraint::Scalar {
                op: spec.op,
                value: median,
                margin_pct: None,
            },
            hard: true,
            required_conditions: BTreeMap::new(),
        };
        let (mut considered, mut fail) = (0usize, 0usize);
        for facts in population.values() {
            let verdict = probe_verdict(facts.axes.get(&spec.axis), &probe, spec.op);
            if matches!(verdict, Verdict::Pass | Verdict::NearMiss | Verdict::Fail) {
                considered += 1;
                if verdict == Verdict::Fail {
                    fail += 1;
                }
            }
        }
        // The reference rounds half-even via `round(x, 1)`; the fixture
        // numbers stay clear of .x5 ties so one formula is exact on both.
        let pct = reduction_pct(fail, considered);
        questions.push(Question {
            axis: KnifeId::Axis(spec.axis),
            prompt: format!(
                "How much {:?} is enough? (median measured claim: {median})",
                spec.axis
            ),
            help: spec.help.to_string(),
            expected_reduction_pct: pct,
            askable: true,
            proposed_value: Some(median),
            parts_with_claims: values.len(),
            expected_fail_at_median: fail,
        });
    }
    // Reference: `sort(key=lambda q: -q["expected_fail_at_median"])` —
    // stable, so registry order breaks ties. `sort_by_key` is stable too.
    questions.sort_by_key(|q| std::cmp::Reverse(q.expected_fail_at_median));
    questions
}
