//! T-N1 — the L1 alias fold, positive and negative.
//!
//! The fold table joins spellings of ONE physical quantity (`RΘJA` →
//! `RTHJA`, `∆VGSTH`/`DVGSTH` → `ΔVGSTH`, word label → `RDS(ON)`). The
//! negative control is the load-bearing test: a fold across quantities
//! (`RTHJA` ↔ `RTHJC`, `ΔVGSTH` → `VGSTH`) corrupts both payoffs silently —
//! the compare view would merge different physics and the elimination
//! machine would kill parts on the wrong quantity. Such a fold must fail
//! this battery before it fails a customer.
//!
//! Additive gate (same discipline as the registry expansion): these cases
//! extend the contract and never touch `registry_gold`'s 1:1 battery.

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::float_cmp,
    clippy::indexing_slicing,
    reason = "gold tests assert on their own fixtures"
)]

use dw_decide::Axis;
use dw_knives::{alias_rows, axis_for_symbol, canonical_key, fold_symbol, norm_symbol};

/// T-N1 named positives: `V GS(TH)` / `V(GS)TH` / `VGS(TH)` → one key,
/// and `RΘJA` → `RTHJA`.
#[test]
fn t_n1_named_folds_one_key_each() {
    // The VGSTH spelling family folds through norm_symbol alone.
    for spelling in ["V GS(TH)", "V(GS)TH", "VGS(TH)", "vgs th"] {
        assert_eq!(canonical_key(spelling), "VGSTH", "spelling {spelling:?}");
    }

    // The Θ/TH fold is the alias table's job.
    assert_eq!(fold_symbol("RΘJA"), Some("RTHJA"));
    assert_eq!(canonical_key("RΘJA"), "RTHJA");
    assert_eq!(canonical_key("RTHJA"), "RTHJA");
    assert_eq!(axis_for_symbol("RΘJA"), Some(Axis::RthjaCperw));
    assert_eq!(axis_for_symbol("RTHJA"), Some(Axis::RthjaCperw));

    // Case and hyphen variants fold to the same key and axis.
    assert_eq!(canonical_key("Rθja"), "RTHJA");
    assert_eq!(canonical_key("RΘJ-A"), "RTHJA");
    assert_eq!(canonical_key("RTHJ-A"), "RTHJA");
    assert_eq!(axis_for_symbol("RΘJ-A"), Some(Axis::RthjaCperw));
}

/// Word label ↔ symbol: brands print the spelled row header where the
/// symbol is absent (G1). One key, one axis.
#[test]
fn t_n1_word_label_folds_to_symbol() {
    assert_eq!(fold_symbol("Drain-Source On-Resistance"), Some("RDS(ON)"));
    assert_eq!(
        canonical_key("Drain-Source On-Resistance"),
        canonical_key("RDS(ON)")
    );
    assert_eq!(canonical_key("Drain-Source On-Resistance"), "RDSON");
    assert_eq!(
        axis_for_symbol("Drain-Source On-Resistance"),
        Some(Axis::RdsOnOhm)
    );
}

/// Delta glyph variants: U+2206 INCREMENT, U+0394 GREEK CAPITAL DELTA and
/// the font-lost ASCII D are one printed symbol.
#[test]
fn t_n1_delta_glyphs_one_key() {
    assert_eq!(fold_symbol("∆VGSTH"), Some("ΔVGSTH"));
    assert_eq!(fold_symbol("DVGSTH"), Some("ΔVGSTH"));
    assert_eq!(canonical_key("∆VGSTH"), canonical_key("ΔVGSTH"));
    assert_eq!(canonical_key("DVGSTH"), canonical_key("ΔVGSTH"));
    assert_eq!(canonical_key("DVGSTH"), "ΔVGSTH");
}

/// NEGATIVE CONTROL — different physical quantities must never fold.
/// `RTHJA` (junction-to-ambient) vs `RTHJC` (junction-to-case); `ΔVGSTH`
/// (threshold shift/spread) vs `VGSTH` (threshold level). A table that
/// merges these fails here first.
#[test]
fn negative_control_quantities_never_fold() {
    // No fold maps RTHJA and RTHJC onto one identity.
    assert_ne!(canonical_key("RTHJA"), canonical_key("RTHJC"));
    assert_ne!(fold_symbol("RTHJA"), Some("RTHJC"));
    assert_ne!(fold_symbol("RTHJC"), Some("RTHJA"));
    assert_ne!(fold_symbol("RΘJA"), Some("RTHJC"));

    // RTHJC has no registry axis — and the Θ printing of it must NOT
    // capture the Rthja axis through a sloppy transliteration.
    assert_eq!(axis_for_symbol("RTHJC"), None);
    assert_eq!(axis_for_symbol("RΘJC"), None);

    // The threshold shift is not the threshold level.
    assert_ne!(canonical_key("ΔVGSTH"), canonical_key("VGSTH"));
    assert_ne!(canonical_key("DVGSTH"), canonical_key("VGSTH"));
}

/// NEGATIVE CONTROL — unmapped stays unmapped; the fold layer invents
/// nothing (D5).
#[test]
fn negative_control_unmapped_stay_unmapped() {
    assert_eq!(fold_symbol("FOO"), None);
    assert_eq!(fold_symbol(""), None);
    assert_eq!(axis_for_symbol("FOO"), None);
    assert_eq!(axis_for_symbol(""), None);
    assert_eq!(canonical_key("FOO"), "FOO");
}

/// The table law: frozen, additive-only, one-step.
///
/// Every `alias_norm` is its own norm (pre-normalized keys), every row
/// folds to its canonical key in one lookup, and no key is another row's
/// canonical (no chains — a cell key is reached in exactly one step).
#[test]
fn table_law_frozen_additive_one_step() {
    let rows = alias_rows();
    assert!(!rows.is_empty(), "the frozen table carries the G1 folds");

    let canonical_keys: Vec<String> = rows
        .iter()
        .map(|(_, canonical)| norm_symbol(canonical))
        .collect();

    for (alias_norm, canonical) in &rows {
        assert_eq!(norm_symbol(alias_norm), *alias_norm, "key {alias_norm:?}");
        assert!(!canonical.is_empty());
        assert_eq!(
            canonical_key(alias_norm),
            norm_symbol(canonical),
            "row {alias_norm:?} must fold in one step"
        );
        // No chains: a key may not be another row's canonical key.
        assert!(
            !canonical_keys.iter().any(|k| k == alias_norm),
            "{alias_norm:?} is a canonical key elsewhere — folds must be one-step"
        );
    }
}
