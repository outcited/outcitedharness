//! The authoritative knife specification — one law shared by every consumer.
//!
//! The `dw-knives` [`crate::registry::AXES`] table is a **symbol-level
//! measurement contract** for power datasheets (`VDSS`, `RDS(ON)`, …). The MCU
//! axes of `dw-decide::Axis` (`flash_kb`, `freq_mhz`, `has_can`, `has_usb`, …)
//! are declared here as [`KnifeSpec`] rows: which axis is honored, under which
//! op, in which unit, with which plausible value band, and which released-feed
//! substrate answers it. This is the F-03 registry generalisation — the table
//! moved here from `dw-bcmp` so every consumer shares one law; `dw-bcmp` now
//! consumes it and no longer owns a private copy.
//!
//! Every scenario atom is validated against [`KNIFE_SPEC`]. Anything else is
//! parked with a reason — never silently dropped, never best-guessed.

use dw_decide::{Axis, Op};
use serde::{Deserialize, Serialize};

/// Version of the pilot knife declaration. Bumped when the table changes;
/// receipts pin it.
pub const KNIFE_SPEC_VERSION: &str = "bcmp-knives-v1";

/// One declared knife.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct KnifeSpec {
    /// Serde name of the engine axis (e.g. `flash_kb`).
    pub axis: &'static str,
    /// The comparison the axis honors under this declaration.
    pub op: &'static str,
    /// Canonical unit the value is stated in.
    pub unit: &'static str,
    /// Inclusive plausible band for stated requirements.
    pub value_min: f64,
    /// Inclusive plausible band for stated requirements.
    pub value_max: f64,
    /// The pillar field in the released feed that answers this knife.
    pub substrate_field: &'static str,
    /// Why this knife is honored here (audit note).
    pub note: &'static str,
}

impl KnifeSpec {
    /// Resolve the engine axis for this spec row, or `None` if the engine has
    /// no such axis (should not happen — the table is authored against it).
    #[must_use]
    pub fn engine_axis(&self) -> Option<Axis> {
        serde_json::from_value::<Axis>(serde_json::Value::String(self.axis.to_string())).ok()
    }

    /// Resolve the engine op for this spec row.
    #[must_use]
    pub fn engine_op(&self) -> Option<Op> {
        serde_json::from_value::<Op>(serde_json::Value::String(self.op.to_string())).ok()
    }
}

/// The declared, honored knives. Parked-by-construction (not in this table):
/// every other axis the engine knows (`temp_range_c` needs range-valued
/// evidence the scalar `Evidence` shape cannot carry honestly — M4 contract
/// gap; `has_wireless_ble` / `has_motor_control` have **no evidence substrate**
/// in the released feed) and every axis outside `dw-decide` entirely
/// (`sram_kb`, `supply_v`, `n_gpio`, … — registry expansion is F-03).
pub const KNIFE_SPEC: &[KnifeSpec] = &[
    KnifeSpec {
        axis: "flash_kb",
        op: "min_rating",
        unit: "kb",
        value_min: 1.0,
        value_max: 8192.0,
        substrate_field: "flash_kb",
        note: "on-chip program flash, min requirement, KB",
    },
    KnifeSpec {
        axis: "freq_mhz",
        op: "min_rating",
        unit: "mhz",
        value_min: 0.032,
        value_max: 1000.0,
        substrate_field: "clk_max_mhz",
        note: "max core clock, min requirement, MHz",
    },
    KnifeSpec {
        axis: "has_can",
        op: "min_rating",
        unit: "count",
        value_min: 1.0,
        value_max: 8.0,
        substrate_field: "n_can",
        note: "CAN/FD peripheral count threshold (>=1 means has CAN)",
    },
    KnifeSpec {
        axis: "has_usb",
        op: "min_rating",
        unit: "count",
        value_min: 1.0,
        value_max: 4.0,
        substrate_field: "n_usb",
        note: "USB peripheral count threshold (>=1 means has USB)",
    },
];

/// Look up the declaration for an axis name.
#[must_use]
pub fn spec_for(axis: &str) -> Option<&'static KnifeSpec> {
    KNIFE_SPEC.iter().find(|s| s.axis == axis)
}

/// The knife spec as JSON (for the generator; one law, emitted by the engine
/// side).
#[must_use]
pub fn knife_spec_json() -> serde_json::Value {
    serde_json::json!({
        "version": KNIFE_SPEC_VERSION,
        "knives": KNIFE_SPEC,
        "parked_axes": {
            "temp_range_c": "range-valued evidence unsupported by scalar Evidence shape (M4 contract gap)",
            "has_wireless_ble": "no evidence substrate in released feed",
            "has_motor_control": "no evidence substrate in released feed",
            "sram_kb": "axis not in dw-decide registry (F-03)",
            "supply_v": "axis not in dw-decide registry (F-03)",
            "n_gpio": "axis not in dw-decide registry (F-03)",
            "n_i2c": "axis not in dw-decide registry (F-03)",
            "n_spi": "axis not in dw-decide registry (F-03)",
            "n_uart": "axis not in dw-decide registry (F-03)",
            "n_adc": "axis not in dw-decide registry (F-03)",
            "n_dma": "axis not in dw-decide registry (F-03)",
            "has_aes": "axis not in dw-decide registry (F-03)",
            "has_trng": "axis not in dw-decide registry (F-03)",
            "has_trustzone": "axis not in dw-decide registry (F-03)",
        },
    })
}
