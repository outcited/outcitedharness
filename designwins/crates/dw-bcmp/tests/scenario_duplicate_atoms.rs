//! Scenario-level duplicate-atom rejection (`hw-1m4` §C9 — the PR #55 residual).
//!
//! Declaration uniqueness was pinned by PR #55; this pins the *scenario* layer:
//! a proposal that canonicalizes to an atom already honored in the same
//! scenario is rejected with [`ParkCode::DuplicateAtom`], never silently
//! merged into the set.

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    reason = "contract pins fail loudly by design"
)]

use dw_bcmp::scenario::{validate, AtomProposal, GeneratorRef, ParkCode, Scenario};

fn proposal(axis: &str, value: f64) -> AtomProposal {
    AtomProposal {
        axis: axis.into(),
        op: "min_rating".into(),
        value: serde_json::Value::from(value),
        unit: None,
        hard: true,
    }
}

fn scenario(atoms: Vec<AtomProposal>) -> Scenario {
    Scenario {
        scenario_id: "dup".into(),
        question: "q".into(),
        occasion: None,
        applications: vec![],
        proposed_atoms: atoms,
        generated_by: GeneratorRef {
            source: "hand".into(),
            model: None,
            endpoint: None,
            prompt_sha256: None,
        },
    }
}

/// The same atom proposed twice: the first is honored, the second is parked
/// with a machine code — never silently dropped, never doubled.
#[test]
fn duplicate_atom_is_rejected_not_silently_merged() {
    let v = validate(scenario(vec![
        proposal("flash_kb", 256.0),
        proposal("flash_kb", 256.0),
    ]));
    assert_eq!(v.atoms.len(), 1, "the duplicate must not be scored twice");
    assert_eq!(v.canonical.len(), 1);
    assert!(v.dispositions[0].outcome.is_ok());
    assert_eq!(
        v.dispositions[1].outcome.as_ref().unwrap_err(),
        &ParkCode::DuplicateAtom
    );
    assert_eq!(v.parked_counts().get("duplicate_atom"), Some(&1));
    assert!(v.is_scorable(), "the first instance still scores");
}

/// Rejection is on the *canonical* identity and per-atom: a duplicate is
/// detected even when interleaved with distinct honored atoms.
#[test]
fn duplicate_detection_is_on_canonical_identity() {
    let v = validate(scenario(vec![
        proposal("flash_kb", 256.0),
        proposal("freq_mhz", 48.0),
        proposal("flash_kb", 256.0),
    ]));
    assert_eq!(v.atoms.len(), 2);
    assert_eq!(v.canonical.len(), 2);
    assert_eq!(v.parked_counts().get("duplicate_atom"), Some(&1));
}

/// Distinct atoms are all honored — the rejection cannot pass by dropping
/// everything.
#[test]
fn distinct_atoms_are_all_honored() {
    let v = validate(scenario(vec![
        proposal("flash_kb", 256.0),
        proposal("freq_mhz", 48.0),
        proposal("has_can", 1.0),
    ]));
    assert_eq!(v.atoms.len(), 3);
    assert_eq!(v.canonical.len(), 3);
    assert!(v.parked_counts().is_empty());
}
