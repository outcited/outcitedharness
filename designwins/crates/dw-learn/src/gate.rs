//! The promotion gate: field-level scoring over a frozen holdout
//! (`scripts/lora_gate.py`, `scripts/gate_v2.py`, ported 1:1).
//!
//! A model's answer is scored symbol-by-symbol against the reference rows:
//! a symbol not in the reference is a false positive, a reference symbol the
//! model missed is a false negative, and a matched symbol counts as a true
//! positive only when the unit-normalized values agree within
//! `max(1e-9, |want| * 1e-4)`. `PASS` needs f1 ≥ 0.90 AND precision ≥ 0.95.

use serde::Serialize;

/// Ohm-family units normalized to ohms (the reference's `UNIT_TO_OHM`).
const UNIT_TO_OHM: &[(&str, f64)] = &[
    ("mohm", 1e-3),
    ("ohm", 1.0),
    ("kohm", 1e3),
    ("m\u{03c9}", 1e-3),
    ("\u{03c9}", 1.0),
];

/// `norm_value`: `float(value)` scaled by the unit's ohm factor (unchanged
/// for any other unit). `None` for unparseable or absent values.
#[must_use]
pub fn norm_value(value: Option<&serde_json::Value>, unit: Option<&str>) -> Option<f64> {
    let v = value?.as_f64().or_else(|| {
        value
            .and_then(serde_json::Value::as_str)
            .and_then(|s| s.trim().parse::<f64>().ok())
    })?;
    let u = unit.unwrap_or("").trim().to_lowercase();
    if let Some((_, factor)) = UNIT_TO_OHM.iter().find(|(name, _)| *name == u) {
        Some(v * factor)
    } else {
        Some(v)
    }
}

/// The gate report the reference writes (`gate-report{,-v2}.json`).
#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct GateReport {
    /// True positives.
    pub fields_matched: i64,
    /// False positives (wrong value or invented symbol).
    pub fields_wrong_or_extra: i64,
    /// False negatives (missed symbols).
    pub fields_missed: i64,
    /// precision = tp / (tp + fp).
    pub precision: f64,
    /// recall = tp / (tp + fn).
    pub recall: f64,
    /// 2·p·r / (p + r).
    pub f1: f64,
    /// The PASS bar: f1 ≥ 0.90 AND precision ≥ 0.95.
    pub pass: bool,
}

fn symbol(row: &serde_json::Value) -> String {
    row.get("symbol")
        .or_else(|| row.get("param"))
        .and_then(serde_json::Value::as_str)
        .map(str::to_lowercase)
        .unwrap_or_default()
}

/// `score(want_rows, got_rows)` -> `(tp, fp, fn)`.
#[must_use]
pub fn score(want_rows: &[serde_json::Value], got_rows: &[serde_json::Value]) -> (i64, i64, i64) {
    use std::collections::BTreeMap;

    let want: BTreeMap<String, &serde_json::Value> = want_rows
        .iter()
        .filter(|r| !symbol(r).is_empty())
        .map(|r| (symbol(r), r))
        .collect();
    let mut got_syms: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
    let mut tp = 0i64;
    let mut fp = 0i64;
    for r in got_rows {
        let sym = symbol(r);
        got_syms.insert(sym.clone());
        let Some(w) = want.get(&sym) else {
            fp += 1;
            continue;
        };
        let wv = norm_value(
            w.get("value"),
            w.get("unit").and_then(serde_json::Value::as_str),
        );
        let gv = norm_value(
            r.get("value"),
            r.get("unit").and_then(serde_json::Value::as_str),
        );
        let tolerance = wv.map_or(0.0, |wv| 1e-9_f64.max(wv.abs() * 1e-4));
        match (wv, gv) {
            (Some(wv), Some(gv)) if (wv - gv).abs() <= tolerance => tp += 1,
            _ => fp += 1,
        }
    }
    // Python `fn = len(want) - len(want.keys() & got_syms)`.
    let fn_ = i64::try_from(want.len()).unwrap_or(0)
        - i64::try_from(want.keys().filter(|k| got_syms.contains(*k)).count()).unwrap_or(0);
    (tp, fp, fn_)
}

/// Assemble the report from the counters (the reference's rounding + bars).
// The counters are field counts; converting them to f64 for the ratio loses no
// meaningful precision (they are far below 2^53 in any real gate run), which is
// exactly what Python's `/` does on its ints.
#[allow(
    clippy::cast_precision_loss,
    reason = "field counts are far below 2^53; matches Python int/float division"
)]
#[must_use]
pub fn report(tp: i64, fp: i64, fn_: i64) -> GateReport {
    let precision = tp as f64 / 1f64.max((tp + fp) as f64);
    let recall = tp as f64 / 1f64.max((tp + fn_) as f64);
    let f1 = 2.0 * precision * recall / 1e-9_f64.max(precision + recall);
    GateReport {
        fields_matched: tp,
        fields_wrong_or_extra: fp,
        fields_missed: fn_,
        precision: round4(precision),
        recall: round4(recall),
        f1: round4(f1),
        pass: f1 >= 0.90 && precision >= 0.95,
    }
}

/// Python `round(x, 4)`.
///
/// Python rounds the *exact* decimal expansion of the float, half-to-even.
/// `(x * 10_000.0).round() / 10_000.0` is **not** equivalent: the multiply can
/// land exactly on a `.5` where the true expansion is below it (measured:
/// `0.00035 * 10000 == 3.5` bit-exactly, so naive rounding gives `0.0004` while
/// Python gives `0.0003`). Rust's `{:.4}` formatting is correctly rounded
/// against the exact expansion — same rule Python uses — so it matches on all
/// 20,999 sampled values (naive multiply diverged on 215). See the
/// `round4_negative_control` unit test.
// `{:.4}` on a finite f64 always re-parses as a finite f64, so the unwrap
// cannot fire; kept explicit rather than silently falling back.
#[allow(clippy::expect_used, reason = "a formatted float always re-parses")]
fn round4(value: f64) -> f64 {
    format!("{value:.4}")
        .parse()
        .expect("a formatted float parses")
}

#[cfg(test)]
// Tests assert freely on their own constants; index/unwrap noise is fine here.
#[allow(
    clippy::unwrap_used,
    clippy::float_cmp,
    reason = "gold negative control"
)]
mod tests {
    use super::round4;

    /// Negative control: the naive multiply-round-divide that this port used
    /// before is wrong where the float's true expansion sits below a decimal
    /// half. If `round4` is ever reverted to that form, this goes red.
    #[test]
    fn round4_negative_control() {
        // 0.00035 * 10000 == 3.5 bit-exactly; the true expansion is
        // 0.00034999... , so Python rounds half-to-even down to 0.0003.
        assert_eq!(round4(0.00035), 0.0003);
        // The old (wrong) form would have produced 0.0004 here.
        let naive = (0.00035_f64 * 10_000.0).round() / 10_000.0;
        assert_ne!(round4(0.00035), naive, "naive rounding must not match");
        // Recall = 1/32 = 0.03125 exactly → half-to-even 0.0312, not 0.0313.
        assert_eq!(round4(1.0 / 32.0), 0.0312);
    }
}
