//! Contract-pinning negative tests for the BCMP knife declaration.
//!
//! PACKAGE-TEMP-AXES-01 (`hw-37d`) — **partial contract-pinning work, NOT a
//! completion of the bead.** Owner ruling 2026-10-28
//! (outcitedharness#8 comment 6463991870): `package` is nominal
//! (*"Category is not Axis"*) and `temp_range_c` is range-valued; neither may
//! be registered as an `AxisSpec`/scalar knife, and the independently
//! achievable work is to *pin* that both stay parked with a reason — never
//! silently dropped, never best-guessed. The scalar `Evidence` shape and the
//! registry generalisation are M4 `do-9id` (DW-M4-CONTRACT-GAPS-01).
//!
//! These tests add no atoms and change no engine semantics; they only assert
//! the declaration contract that already ships.
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    reason = "contract pins fail loudly by design"
)]

use std::collections::BTreeSet;

use dw_bcmp::knife::{knife_spec_json, spec_for, KNIFE_SPEC};
use dw_bcmp::scenario::{validate, AtomProposal, GeneratorRef, ParkCode, Scenario};

/// The documented parking reason for `temp_range_c` — a contract string, not a
/// free-text note. Changing it is an M4 `do-9id` decision.
const TEMP_RANGE_C_PARKED_REASON: &str =
    "range-valued evidence unsupported by scalar Evidence shape (M4 contract gap)";

fn atom(axis: &str, op: &str, value: f64) -> AtomProposal {
    AtomProposal {
        axis: axis.into(),
        op: op.into(),
        value: serde_json::Value::from(value),
        unit: None,
        hard: true,
    }
}

fn scenario(id: &str, atoms: Vec<AtomProposal>) -> Scenario {
    Scenario {
        scenario_id: id.into(),
        question: format!("question for {id}"),
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

/// A declaration may not claim the same axis twice, and every declaration must
/// resolve to a real `dw-decide` axis + op — otherwise it silently cannot bind
/// and would drop out of validation without a reason.
#[test]
fn declared_knives_are_unique_and_engine_bindable() {
    let mut seen: BTreeSet<&str> = BTreeSet::new();
    for k in KNIFE_SPEC {
        assert!(seen.insert(k.axis), "duplicate knife axis: {}", k.axis);
        assert!(
            k.engine_axis().is_some(),
            "{} does not bind a dw-decide axis",
            k.axis
        );
        assert!(
            k.engine_op().is_some(),
            "{} does not bind a dw-decide op",
            k.axis
        );
        assert!(
            !k.substrate_field.is_empty(),
            "{} has no substrate field",
            k.axis
        );
    }
}

/// Every parked axis carries a non-empty reason and is *not* also honored: a
/// parked axis must never be silently upgraded to a declaration.
#[test]
fn parked_axes_carry_a_reason_and_are_never_also_honored() {
    let spec = knife_spec_json();
    let parked = spec["parked_axes"]
        .as_object()
        .expect("parked_axes is a map");
    assert!(!parked.is_empty(), "parked_axes must not be empty");
    for (axis, reason) in parked {
        let reason = reason.as_str().unwrap_or_default();
        assert!(!reason.trim().is_empty(), "{axis} parked without a reason");
        assert!(
            spec_for(axis).is_none(),
            "{axis} is both parked and honored"
        );
    }
}

/// `temp_range_c` stays parked with its documented reason — the scalar
/// `Evidence` shape cannot carry a range (M4 `do-9id`). This is the negative
/// pin against a superficial `Op::Covers` registration.
#[test]
fn temp_range_c_stays_parked_with_documented_reason() {
    let spec = knife_spec_json();
    assert_eq!(
        spec["parked_axes"]["temp_range_c"].as_str(),
        Some(TEMP_RANGE_C_PARKED_REASON),
        "temp_range_c parking reason is contract (M4 do-9id)"
    );
    assert!(
        spec_for("temp_range_c").is_none(),
        "temp_range_c must not be honored as a knife"
    );
}

/// An undeclared/parked axis proposed as an atom is parked with a machine
/// code and a disposition — never silently dropped, and never scorable.
#[test]
fn parked_atom_is_dispositioned_with_code_never_dropped() {
    let v = validate(scenario(
        "pin-temp",
        vec![atom("temp_range_c", "covers", 85.0)],
    ));
    assert_eq!(
        v.dispositions.len(),
        1,
        "the atom was silently dropped instead of parked"
    );
    assert!(
        matches!(
            v.dispositions[0].outcome.as_ref().unwrap_err(),
            ParkCode::UndeclaredKnife
        ),
        "temp_range_c must park as an undeclared knife, got {:?}",
        v.dispositions[0].outcome
    );
    assert_eq!(v.parked_counts().get("undeclared_knife"), Some(&1));
    assert!(!v.is_scorable());
}
