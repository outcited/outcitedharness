//! L1 spelling fold — the frozen, additive-only symbol alias table.
//!
//! [`crate::norm_symbol`] folds case, whitespace, underscores, parentheses
//! and commas. It cannot fold what it cannot see: `Θ` vs `TH`, `Δ` vs `D`,
//! or a spelled word label where the symbol should be. Those spellings are
//! one physical quantity printed two ways — two cells in the compare view,
//! a silent miss in cross-brand normalization (gap G1).
//!
//! The table is **frozen, additive-only** (PRD-NORMALIZE §5 discipline):
//!
//! * `alias_norm` keys are pre-normalized — each is its own
//!   [`norm_symbol`] output — and unique. A duplicate is a rewrite of
//!   history and the loader refuses it.
//! * A fold joins spellings of ONE physical quantity only. A fold across
//!   quantities (`RTHJA` ↔ `RTHJC`) corrupts both payoffs silently, which
//!   is why the negative control ships with the gold (T-N1).
//! * Folds are one-step: no key is another row's canonical (no chains), so
//!   a cell key is always reached in exactly one lookup.
//!
//! Data: `data/symbol_aliases.jsonl` — `{alias_norm, canonical, evidence}`.
//! The `evidence` column rides along for the record and for adapters; the
//! Rust loader binds the two identity columns. Nothing here re-derives
//! another layer's job: this is spelling in, spelling out.

use std::collections::BTreeMap;
use std::sync::OnceLock;

use serde::Deserialize;

use crate::registry::norm_symbol;

/// One frozen row: a printed spelling and the canonical symbol it is.
///
/// `evidence` is present in the JSONL but not bound here — see module docs.
#[derive(Debug, Deserialize)]
struct AliasRow {
    /// The alias spelling, pre-normalized (its own [`norm_symbol`]).
    alias_norm: String,
    /// The canonical symbol as printed — the identity this spelling folds to.
    canonical: String,
}

/// The frozen table, parsed once. Panics on a malformed table: this is
/// literal data shipped with the crate and a bad row is a build defect,
/// never a runtime condition to absorb silently.
fn table() -> &'static BTreeMap<String, String> {
    static TABLE: OnceLock<BTreeMap<String, String>> = OnceLock::new();
    TABLE.get_or_init(|| {
        let raw = include_str!("../data/symbol_aliases.jsonl");
        let mut out: BTreeMap<String, String> = BTreeMap::new();
        for line in raw.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let row: AliasRow = match serde_json::from_str(line) {
                Ok(row) => row,
                Err(err) => {
                    panic!(
                        "dw-knives: symbol_aliases.jsonl rows are frozen and must parse: {line} ({err})"
                    )
                }
            };
            assert_eq!(
                norm_symbol(&row.alias_norm),
                row.alias_norm,
                "dw-knives: alias_norm must be its own norm: {}",
                row.alias_norm
            );
            assert!(
                !row.canonical.is_empty(),
                "dw-knives: alias canonical is empty for {}",
                row.alias_norm
            );
            let previous = out.insert(row.alias_norm.clone(), row.canonical.clone());
            assert!(
                previous.is_none(),
                "dw-knives: duplicate alias_norm — the table is frozen, additive-only: {}",
                row.alias_norm
            );
        }
        out
    })
}

/// L1 fold: the canonical symbol this printed spelling is an alias of.
///
/// `None` when the spelling is its own identity (or unmapped) — an unmapped
/// spelling is never force-folded (D5's honesty).
#[must_use]
pub fn fold_symbol(symbol: &str) -> Option<&'static str> {
    table().get(&norm_symbol(symbol)).map(String::as_str)
}

/// The L1 cell key for a printed symbol: the canonical's [`norm_symbol`]
/// when a fold exists, else the symbol's own norm.
///
/// Two spellings of one physical quantity produce one key; distinct
/// quantities never collide.
#[must_use]
pub fn canonical_key(symbol: &str) -> String {
    let n = norm_symbol(symbol);
    match table().get(&n) {
        Some(canonical) => norm_symbol(canonical),
        None => n,
    }
}

/// The frozen rows as `(alias_norm, canonical)` pairs — adapters and the
/// table-law tests read the contract through here.
#[must_use]
pub fn alias_rows() -> Vec<(&'static str, &'static str)> {
    table()
        .iter()
        .map(|(k, v)| (k.as_str(), v.as_str()))
        .collect()
}
