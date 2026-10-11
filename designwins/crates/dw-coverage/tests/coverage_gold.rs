//! Gold: `build_coverage_manifest` is 1:1 with the live reference, and the
//! cube/gap rollup obeys PLAN §7's honesty rules.
//!
//! Regenerate (D8: the reference is the spec):
//!
//! ```text
//! python3 - <<'EOF' > tests/fixtures/gold/coverage_manifest_v1.json
//! import json, sys; sys.path.insert(0, ".")
//! from harness.electronics.coverage import build_coverage_manifest
//! battery = [...]  # see git history
//! json.dump(build_coverage_manifest(work, local, cands, outs), ...)
//! EOF
//! ```

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::float_cmp,
    clippy::indexing_slicing,
    clippy::similar_names,
    reason = "gold tests assert on their own fixtures"
)]

use std::path::PathBuf;

use dw_coverage::{
    advance, build_coverage_manifest, derive_gaps, rollup, Attempt, Candidate, CubeSlice, Curves,
    Denominator, GapKind, KnivesCoverage, LocalResult, SliceInputs, VerifiedOutcome, WorkItem,
};
use dw_types::ReleaseId;

fn gold_path() -> PathBuf {
    let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    while !dir.join("tests/fixtures/gold").is_dir() {
        assert!(dir.pop(), "could not locate tests/fixtures/gold");
    }
    dir.join("tests/fixtures/gold/coverage_manifest_v1.json")
}

fn battery() -> (
    Vec<WorkItem>,
    Vec<LocalResult>,
    Vec<Candidate>,
    Vec<VerifiedOutcome>,
) {
    let work = vec![
        WorkItem {
            work_id: "w1".into(),
            capability: "pin_or_ball".into(),
            document_sha256: "A".into(),
            vendor: Some("ROHM".into()),
        },
        WorkItem {
            work_id: "w2".into(),
            capability: "parametrics".into(),
            document_sha256: "A".into(),
            vendor: None,
        },
        WorkItem {
            work_id: "w3".into(),
            capability: "parametrics".into(),
            document_sha256: "B".into(),
            vendor: None,
        },
        WorkItem {
            work_id: "w4".into(),
            capability: "opn_decoder".into(),
            document_sha256: "C".into(),
            vendor: Some("TI".into()),
        },
        WorkItem {
            work_id: "w5".into(),
            capability: "not_a_lane".into(),
            document_sha256: "A".into(),
            vendor: None,
        },
    ];
    let local = vec![
        LocalResult {
            work_id: "w1".into(),
        },
        LocalResult {
            work_id: "w3".into(),
        },
        LocalResult {
            work_id: "nope".into(),
        },
    ];
    let cands = vec![
        Candidate {
            document_sha256: "A".into(),
            capability: "parametrics".into(),
            local_attempts: vec![
                Attempt {
                    status: "ok".into(),
                },
                Attempt {
                    status: "cross_source_disagreement".into(),
                },
            ],
        },
        Candidate {
            document_sha256: "A".into(),
            capability: "parametrics".into(),
            local_attempts: vec![Attempt {
                status: "ok".into(),
            }],
        },
        Candidate {
            document_sha256: "D".into(),
            capability: "parametrics".into(),
            local_attempts: vec![Attempt {
                status: "ok".into(),
            }],
        },
    ];
    let outs = vec![
        VerifiedOutcome {
            work_id: Some("w2".into()),
            document_sha256: None,
            capability: None,
            disposition: "admitted".into(),
        },
        VerifiedOutcome {
            work_id: Some("w3".into()),
            document_sha256: None,
            capability: None,
            disposition: "quarantined".into(),
        },
        VerifiedOutcome {
            work_id: Some("w4".into()),
            document_sha256: None,
            capability: None,
            disposition: "admitted".into(),
        },
        VerifiedOutcome {
            work_id: None,
            document_sha256: Some("A".into()),
            capability: Some("not_a_lane".into()),
            disposition: "admitted".into(),
        },
    ];
    (work, local, cands, outs)
}

/// The typed port reproduces the live reference manifest byte for byte.
#[test]
fn manifest_matches_the_live_reference_gold() {
    let (work, local, cands, outs) = battery();
    let ours = build_coverage_manifest(&work, &local, &cands, &outs);
    let ours_value = serde_json::to_value(&ours).unwrap();
    let theirs: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(gold_path()).unwrap()).unwrap();
    assert_eq!(ours_value, theirs, "manifest diverged from the reference");
}

/// Negative controls — the two rules the states exist for.
#[test]
fn negative_control_states_never_exaggerate() {
    // CONFLICT is sticky: a later teacher admission must not mask it.
    assert_eq!(advance(Some("CONFLICT"), "VALIDATED"), "CONFLICT");
    assert_eq!(advance(Some("LOCATED"), "VALIDATED"), "VALIDATED");
    assert_eq!(advance(Some("VALIDATED"), "EXTRACTED"), "VALIDATED");
    // Without any outcome, EXTRACTED is the ceiling (asserted via the gold
    // battery's A/P06 pillar, which stays EXTRACTED with no teacher).
    let (work, local, cands, outs) = battery();
    let m = build_coverage_manifest(&work, &local, &cands, &outs);
    let a = &m.documents["A"];
    assert_eq!(a.pillars["P06"].state.as_deref(), Some("EXTRACTED"));
}

/// The cube carries `laned` honesty and the gap ledger fires each kind on
/// its trigger — and only on its trigger.
#[test]
fn cube_is_honest_and_gaps_fire_on_triggers() {
    let (work, local, cands, outs) = battery();
    let manifest = build_coverage_manifest(&work, &local, &cands, &outs);
    let inputs = SliceInputs {
        documents: dw_coverage::Documents {
            held: 3,
            above_opn: 1,
            single_opn: 2,
            other: 0,
        },
        denominator: Denominator {
            parts: 12,
            attributes_addressed: 40,
        },
        curves: Curves {
            vector: 1,
            raster: 0,
            none: 0,
            unknown: 0,
        },
        knives: KnivesCoverage {
            axes_known: vec!["vds_rating_v".into()],
            axes_expected: vec!["vds_rating_v".into(), "rds_on_ohm".into()],
        },
        acquisitions: vec!["ADP5003.pdf".into()],
    };
    let cube = rollup(
        ReleaseId::new("rel-x").unwrap(),
        CubeSlice::default(),
        &manifest,
        &inputs,
    );
    // P01..P05 etc. have no lane — they must say so instead of looking
    // covered. P06 is served by pin_or_ball.
    assert!(
        !cube.pillars["P01"].laned,
        "unlaned pillar must not look covered"
    );
    assert!(cube.pillars["P06"].laned);
    assert_eq!(cube.pillars["P09"].state_counts.get("CONFLICT"), Some(&2));
    assert_eq!(cube.knives.coverage_pct, Some(50.0));

    // The full ledger fires each kind on its trigger — the cube's top_gaps
    // is a truncated top list of the same ledger, worst first.
    let gaps = derive_gaps(&manifest, &inputs);
    let kinds: Vec<GapKind> = gaps.iter().map(|g| g.kind).collect();
    assert!(kinds.contains(&GapKind::Acquire));
    assert!(kinds.contains(&GapKind::Axis));
    assert!(kinds.contains(&GapKind::Lane));
    assert!(gaps
        .windows(2)
        .all(|w| w[0].impact_weight >= w[1].impact_weight));
    assert!(cube.top_gaps.len() <= 5);
    assert_eq!(cube.top_gaps.len(), gaps.len().min(5));

    // No axis gap when everything is answered.
    let mut answered = inputs.clone();
    answered.knives.axes_known = vec!["vds_rating_v".into(), "rds_on_ohm".into()];
    let gaps = derive_gaps(&manifest, &answered);
    assert!(!gaps.iter().any(|g| g.kind == GapKind::Axis));

    // Extract gap: targeted, never extracted (stuck at LOCATED).
    let stuck = build_coverage_manifest(
        &[WorkItem {
            work_id: "w".into(),
            capability: "pin_or_ball".into(),
            document_sha256: "E".into(),
            vendor: None,
        }],
        &[],
        &[],
        &[],
    );
    let gaps = derive_gaps(&stuck, &inputs);
    assert!(
        gaps.iter()
            .any(|g| g.kind == GapKind::Extract && g.what.contains("P06")),
        "LOCATED-stuck pillar must raise an extract gap: {gaps:?}"
    );
}
