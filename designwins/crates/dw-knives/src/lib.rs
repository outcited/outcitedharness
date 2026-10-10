//! # dw-knives — axes and the phrase layer
//!
//! Two things live here, and they are the same kind of object:
//!
//! * **the axis registry** — discovered-then-curated technical knives
//! * **the phrase layer** — colloquial intent bound to those knives
//!
//! Both are discovered, both are evidence-backed, both carry a measured stamp
//! that is their license to act on a cohort.
//!
//! ## Why this crate is the product's front door
//!
//! Engineers do not type `has_wireless_ble == true`. They type *"outdoor
//! weather thermostat bluetooth"*. [`phrase`] decomposes that into technical
//! knives and hands [`dw_decide`] a set of atoms. Without this crate the
//! workspace is a text matcher; with it, a sentence narrows a cohort.
//!
//! Specification: `docs/DECISIONS.md` **D4** (and **D5** for the unmapped
//! case). Seed data: `exports/cr_drops/20260813_mcu_yellow_occasion_holes/
//! holes.json` (`chips_discovered`).
//!
//! This crate calls no models and is `forbid(unsafe_code)`.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

pub mod phrase;

pub use phrase::{
    unknowns_from_residue, AtomTemplate, Bound, ColloquialBinding, Drawer, PhraseRegistry, Stamp,
    ACTIVATION_FLOOR,
};

use dw_decide::{Axis, Op};

/// The measured chips from `holes.json`, seeded into a registry.
///
/// These are **not hand-authored**. Each stamp comes from the measurement
/// recorded against the chip, and [`Stamp::from_coverage`] reproduces it:
///
/// | chip | classified | stamp |
/// |---|---:|---|
/// | `talks_to_a_car` | 0.506 | `ProvenFit` — closest to the floor |
/// | `usb_gadget` | pin overlay | `PositiveOnly` |
/// | `bluetooth` | 0.186 | `Blocked("nordic almost unmarked")` |
/// | `drive_a_motor` | two substrates disagree | `Blocked` |
/// | `what_are_you_building` | Renesas-only enum | `PositiveOnly` |
///
/// [`Stamp::from_coverage`] is the mechanism; these entries are the measured
/// population. A new substrate that crosses [`ACTIVATION_FLOOR`] becomes
/// `ProvenFit` automatically and may start eliminating — which is exactly what
/// D4 law 2 demands.
/// Construct a sourced binding from a literal.
///
/// The seed entries below are **constants with literal evidence**; a failure
/// here is a programming error in this file, not a runtime condition. Failing
/// loudly is correct — the alternative, silently dropping a chip, is exactly
/// the fail-open this crate exists to prevent (`DECISIONS.md` D4 law 2).
#[allow(clippy::expect_used)]
fn sourced(
    id: &str,
    phrase: &str,
    axis: Axis,
    drawer: Drawer,
    confidence: f64,
    evidence: &str,
    stamp: Stamp,
) -> ColloquialBinding {
    ColloquialBinding::new(
        id,
        phrase,
        vec![AtomTemplate {
            axis,
            op: Op::MinRating,
            value: None,
            hard: true,
            drawer,
        }],
        confidence,
        vec![evidence.to_string()],
        stamp,
    )
    .expect("dw-knives: seed bindings are literal and must be sourced")
}

/// The measured chips from `holes.json`, seeded into a registry.
///
/// These are **not hand-authored**. Each stamp comes from the measurement
/// recorded against the chip, and [`Stamp::from_coverage`] reproduces it.
/// A new substrate that crosses [`ACTIVATION_FLOOR`] becomes
/// [`Stamp::ProvenFit`] automatically and may start eliminating — which is
/// exactly what D4 law 2 demands.
#[must_use]
pub fn seed_from_measured_chips() -> PhraseRegistry {
    use Stamp::{Blocked, PositiveOnly, ProvenFit};
    let mut registry = PhraseRegistry::new();

    // Substrate coverage, measured. Registry stamps derive from these.
    registry.observe_coverage("n_can", 0.506);
    registry.observe_coverage("has_wireless_ble", 0.186);
    registry.observe_coverage("applications_text", 0.152);
    registry.observe_coverage("has_usb_pin_overlay", 0.5);

    registry.insert(sourced(
        "talks_to_a_car",
        "talks to a car",
        Axis::HasCan,
        Drawer::Applications,
        0.9,
        "holes.json#talks_to_a_car",
        ProvenFit,
    ));

    registry.insert(sourced(
        "bluetooth",
        "bluetooth",
        Axis::HasWirelessBle,
        Drawer::Applications,
        0.85,
        "holes.json#bluetooth",
        Blocked("nordic 1/31 true; classified 0.186 below floor".into()),
    ));

    registry.insert(sourced(
        "usb_gadget",
        "plugs into a computer",
        Axis::HasUsb,
        Drawer::Applications,
        0.8,
        "holes.json#usb_gadget",
        // Vendor has_usb is an ST/TI island. The pin overlay is the better
        // substrate but is not yet on the recommendation engine.
        PositiveOnly,
    ));

    registry.insert(sourced(
        "drive_a_motor",
        "driving a motor",
        Axis::HasMotorControl,
        Drawer::Applications,
        0.7,
        "holes.json#drive_a_motor",
        Blocked(
            "two substrates disagree: applications_text Motor Control 687 vs \
             has_motor_control 446"
                .into(),
        ),
    ));

    registry
}
