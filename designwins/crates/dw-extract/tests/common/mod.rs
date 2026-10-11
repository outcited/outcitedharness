//! Shared gold-gate machinery: load `extract_*` fixtures and deep-compare
//! with float tolerance (docs/HANDOFF.md §6: gold is not bit-exact on
//! floats — structure exactly, numerics with tolerance).
//!
//! The comparison returns a diff string instead of panicking so every gate
//! can ship its negative control (ASSURANCE R2: the gate must be able to
//! fail, and the control proves it can).

#![allow(clippy::unwrap_used, clippy::expect_used, clippy::indexing_slicing)]

use std::path::PathBuf;

use serde_json::Value;

/// Repo-root `tests/fixtures/gold` (this crate's `extract_*` fixtures).
pub fn gold_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../tests/fixtures/gold")
}

/// Load one `extract_*` fixture as its case list.
pub fn load_gold(name: &str) -> Vec<Value> {
    let raw =
        std::fs::read_to_string(gold_dir().join(name)).unwrap_or_else(|e| panic!("{name}: {e}"));
    raw.lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str(l).unwrap())
        .collect()
}

/// Deep comparison with Python-float tolerance. Returns a compact diff
/// description on mismatch, `None` when the values agree.
pub fn diff_gold(expected: &Value, actual: &Value, path: &str) -> Option<String> {
    match (expected, actual) {
        (Value::Number(e), Value::Number(a)) => match (e.as_f64(), a.as_f64()) {
            (Some(ef), Some(af)) if (ef - af).abs() <= 1e-9 * 1.0_f64.max(ef.abs()) => None,
            _ => {
                if e == a {
                    None
                } else {
                    Some(format!("{path}: number {e} != {a}"))
                }
            }
        },
        (Value::String(e), Value::String(a)) => {
            (e != a).then(|| format!("{path}: string {e:?} != {a:?}"))
        }
        (Value::Array(e), Value::Array(a)) => {
            if e.len() != a.len() {
                return Some(format!("{path}: len {} != {}", e.len(), a.len()));
            }
            for (i, (ev, av)) in e.iter().zip(a).enumerate() {
                if let Some(d) = diff_gold(ev, av, &format!("{path}[{i}]")) {
                    return Some(d);
                }
            }
            None
        }
        (Value::Object(e), Value::Object(a)) => {
            for (k, ev) in e {
                match a.get(k) {
                    Some(av) => {
                        if let Some(d) = diff_gold(ev, av, &format!("{path}.{k}")) {
                            return Some(d);
                        }
                    }
                    None => return Some(format!("{path}.{k}: missing in actual")),
                }
            }
            for k in a.keys() {
                if !e.contains_key(k) {
                    return Some(format!("{path}.{k}: unexpected in actual"));
                }
            }
            None
        }
        (e, a) => {
            if e == a {
                None
            } else {
                Some(format!("{path}: {e} != {a}"))
            }
        }
    }
}
