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

pub mod alias;
pub mod category;
pub mod phrase;
pub mod rating;
pub mod registry;

pub use alias::{alias_rows, canonical_key, fold_symbol};

pub use category::{Assignment, CategoryGraph, Closure};
pub use phrase::{
    unknowns_from_residue, AtomTemplate, Bound, ColloquialBinding, Drawer, PhraseRegistry, Stamp,
    ACTIVATION_FLOOR,
};
pub use rating::{rating_class, RatingClass};
pub use registry::{axis_for_symbol, norm_symbol, AxisSpec, AXES};

use dw_decide::{Axis, CategoryId, CategoryOp, Constraint, KnifeId, Op};

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
            knife: KnifeId::Axis(axis),
            constraint: Constraint::Scalar {
                op: Op::MinRating,
                // A boolean capability is modelled as "at least 1": a candidate
                // with 0 does not have it. An atom with no threshold can never
                // fail, so it can never narrow the cohort — which would make
                // the whole phrase layer decorative.
                value: 1.0,
                margin_pct: None,
            },
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
///
/// # Panics
///
/// Never in practice. The category ids and the bindings are **literals**, and
/// [`dw_decide::CategoryId::new`] rejects only the empty string. A panic here
/// is a programming error in this file — the same reasoning that governs
/// [`sourced`] below.
#[allow(clippy::expect_used, clippy::unwrap_used)]
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

    // -----------------------------------------------------------------------
    // core_family — the 47% traffic class.
    //
    // Measured 2026-10-10 by `scripts/measure_knife_coverage.py`: 96.6% of
    // family documents carry a family, 98.6% of covered OPNs have one, 352
    // distinct families and 1,590 series tokens. It is the **only** knife that
    // clears `ACTIVATION_FLOOR` on the covered denominator (DECISIONS.md D9).
    //
    // It binds a *category*, not a scalar: "ARM Cortex-M4" is containment
    // (`Cortex-M4F c Cortex-M c ARM`), and `CategoryOp` refuses min/max for
    // exactly that reason (PRD-PHRASE 8.1).
    // -----------------------------------------------------------------------
    // -----------------------------------------------------------------------
    // sleep_current_budget — the parametric slice binding (2026-10-19).
    //
    // Measured substrate: 2,354 of 3,136 mode-row documents (75.1%) carry
    // sleep-family modes (burn 2026-10-19, 75 distinct spellings). The
    // AtMost op carries the 2026-10-19 ruling: a guaranteed maximum
    // (ceiling) proves satisfaction when under the budget; only a
    // guaranteed minimum (floor) above it proves violation. Typical rows
    // never decide. The threshold comes from the engineer's sentence —
    // "sleeps under 2 µA" — so a concept without a number does not fire
    // and lands in residue (D5: the threshold question).
    // -----------------------------------------------------------------------
    registry.insert(
        ColloquialBinding::new(
            "sleep_current_budget",
            "sleep",
            vec![AtomTemplate {
                knife: KnifeId::Axis(Axis::SleepCurrentA),
                constraint: Constraint::Scalar {
                    op: Op::AtMost,
                    value: 0.0, // replaced by the sentence's captured budget
                    margin_pct: None,
                },
                hard: true,
                drawer: Drawer::Applications,
            }],
            0.85,
            vec![
                "burn-2026-10-19/mode.s0.jsonl#sleep_modes 2354/3136 docs 75.1%".into(),
                "d-ruling-2026-10-19#proof-of-satisfaction-vs-violation".into(),
            ],
            Stamp::ProvenFit,
        )
        .expect("sourced")
        .with_value_capture(crate::phrase::ValueCapture {
            // "2 µA" / "2uA" / "2 μA" (micro sign and Greek mu both match)
            pattern: r"([\d.]+)\s*[µμu]\s*a\b".into(),
            scale: 1e-6,
        }),
    );

    registry.insert(
        ColloquialBinding::new(
            "core_family",
            "cortex",
            vec![AtomTemplate {
                knife: KnifeId::Category(CategoryId::new("ARM").unwrap()),
                constraint: Constraint::Category {
                    op: CategoryOp::Within,
                    node: CategoryId::new("ARM").unwrap(),
                },
                hard: true,
                drawer: Drawer::CatalogSubcategory,
            }],
            0.9,
            vec!["knife_coverage_v1.json#core_family".into()],
            // Covered 98.6% of the OPNs it speaks for. Reach is 39.0% of the
            // universe, which is a limit on usefulness, not on correctness —
            // the rest are `Unknown` and `Unknown` never eliminates (C9.1).
            ProvenFit,
        )
        .expect("sourced"),
    );

    registry
}
