//! F-03 registry generalisation parity (`hw-1m4` §C8).
//!
//! The authoritative knife table moved from `dw-bcmp` into `dw-knives`. This
//! pin holds the **golden** `knife_spec_json()` captured *before* the move and
//! asserts the registry-sourced output is unchanged — byte-equivalent once
//! parsed — and that `dw-bcmp` re-exports the one table rather than owning a
//! copy. The local table was deleted only after this parity held.

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    reason = "parity pins fail loudly by design"
)]

use dw_bcmp::knife::{knife_spec_json, KNIFE_SPEC};
use dw_bcmp::scenario::{validate, AtomProposal, GeneratorRef, ParkCode, Scenario};

/// `knife_spec_json()` output captured on `origin/main` before the move to
/// `dw-knives` (2026-10-28). Any drift is a contract break, not a refresh.
const GOLDEN_KNIFE_SPEC: &str = r#"{
  "knives": [
    { "axis": "flash_kb", "note": "on-chip program flash, min requirement, KB", "op": "min_rating", "substrate_field": "flash_kb", "unit": "kb", "value_max": 8192.0, "value_min": 1.0 },
    { "axis": "freq_mhz", "note": "max core clock, min requirement, MHz", "op": "min_rating", "substrate_field": "clk_max_mhz", "unit": "mhz", "value_max": 1000.0, "value_min": 0.032 },
    { "axis": "has_can", "note": "CAN/FD peripheral count threshold (>=1 means has CAN)", "op": "min_rating", "substrate_field": "n_can", "unit": "count", "value_max": 8.0, "value_min": 1.0 },
    { "axis": "has_usb", "note": "USB peripheral count threshold (>=1 means has USB)", "op": "min_rating", "substrate_field": "n_usb", "unit": "count", "value_max": 4.0, "value_min": 1.0 }
  ],
  "parked_axes": {
    "has_aes": "axis not in dw-decide registry (F-03)",
    "has_motor_control": "no evidence substrate in released feed",
    "has_trng": "axis not in dw-decide registry (F-03)",
    "has_trustzone": "axis not in dw-decide registry (F-03)",
    "has_wireless_ble": "no evidence substrate in released feed",
    "n_adc": "axis not in dw-decide registry (F-03)",
    "n_dma": "axis not in dw-decide registry (F-03)",
    "n_gpio": "axis not in dw-decide registry (F-03)",
    "n_i2c": "axis not in dw-decide registry (F-03)",
    "n_spi": "axis not in dw-decide registry (F-03)",
    "n_uart": "axis not in dw-decide registry (F-03)",
    "sram_kb": "axis not in dw-decide registry (F-03)",
    "supply_v": "axis not in dw-decide registry (F-03)",
    "temp_range_c": "range-valued evidence unsupported by scalar Evidence shape (M4 contract gap)"
  },
  "version": "bcmp-knives-v1"
}"#;

/// The goldens are the same value, so the migration changed no output.
#[test]
fn knife_spec_json_matches_the_pre_migration_golden() {
    let golden: serde_json::Value =
        serde_json::from_str(GOLDEN_KNIFE_SPEC).expect("golden is valid JSON");
    assert_eq!(
        knife_spec_json(),
        golden,
        "registry-sourced knife spec must equal the pre-migration golden"
    );
}

/// `dw-bcmp` re-exports the `dw-knives` table — it does not own a copy. The
/// tables are the same rows value-for-value and every lookup resolves through
/// the shared registry (`dw_bcmp::knife` is a re-export; see its module doc).
#[test]
fn bcmp_consumes_the_knives_registry_by_reference() {
    assert_eq!(KNIFE_SPEC, dw_knives::knife::KNIFE_SPEC);
    for row in KNIFE_SPEC {
        assert_eq!(dw_knives::knife::spec_for(row.axis), Some(row));
    }
}

fn scenario(axis: &str) -> Scenario {
    Scenario {
        scenario_id: "parity".into(),
        question: "q".into(),
        occasion: None,
        applications: vec![],
        proposed_atoms: vec![AtomProposal {
            axis: axis.into(),
            op: "min_rating".into(),
            value: serde_json::Value::from(32.0),
            unit: None,
            hard: true,
        }],
        generated_by: GeneratorRef {
            source: "hand".into(),
            model: None,
            endpoint: None,
            prompt_sha256: None,
        },
    }
}

/// Fail-closed preservation: an axis outside `dw-decide` parks as
/// `UnsupportedAxis`; an engine axis the registry does not declare parks as
/// `UndeclaredKnife`. Neither is silently dropped.
#[test]
fn unsupported_and_undeclared_axes_stay_parked() {
    let v = validate(scenario("not_a_real_axis"));
    assert!(v.atoms.is_empty());
    assert_eq!(
        v.dispositions[0].outcome.as_ref().unwrap_err(),
        &ParkCode::UnsupportedAxis
    );

    // `temp_range_c` exists in the engine but is deliberately not declared.
    let v = validate(scenario("temp_range_c"));
    assert!(v.atoms.is_empty());
    assert_eq!(
        v.dispositions[0].outcome.as_ref().unwrap_err(),
        &ParkCode::UndeclaredKnife
    );
}
