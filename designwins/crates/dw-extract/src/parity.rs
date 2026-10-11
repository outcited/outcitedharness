//! Live-parity comparator — the discrepancy half of the replacement gate.
//!
//! The replacement gate runs the Python reference and the Rust candidate on
//! the same subjects **in the same run** (the `dw-replace` pattern,
//! `docs/HANDOFF.md` §2). This module is the comparison: structure exactly,
//! numerics with float tolerance (Python and Rust serialise floats through
//! different reprs — `1.5e-08` vs `1.5e-8` — and the gate must not read that
//! as a divergence).
//!
//! Every gate ships with a negative control: `negative_control_*` below
//! inject faults and assert the comparator goes red.

use serde_json::Value;

/// One discrepancy between the two engines.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Discrepancy {
    /// Dotted/indexed path to the first divergent node.
    pub path: String,
    /// The reference value rendered compactly.
    pub reference: String,
    /// The candidate value rendered compactly.
    pub candidate: String,
}

/// Compare a reference output and a candidate output.
///
/// Returns every discrepancy found (capped traversal at the first divergence
/// per node), empty when the two agree. Floats compare with a relative
/// tolerance of `1e-9` — gold-parity numerics (docs/HANDOFF.md §6), and the
/// wire is parsed JSON so the text repr never reaches this function.
#[must_use]
pub fn diff_wire(reference: &Value, candidate: &Value) -> Vec<Discrepancy> {
    let mut out = Vec::new();
    walk(reference, candidate, "$", &mut out);
    out
}

fn walk(reference: &Value, candidate: &Value, path: &str, out: &mut Vec<Discrepancy>) {
    match (reference, candidate) {
        (Value::Number(a), Value::Number(b)) => {
            let equal = match (a.as_f64(), b.as_f64()) {
                (Some(x), Some(y)) => (x - y).abs() <= 1e-9 * 1.0_f64.max(x.abs()),
                _ => a == b,
            };
            if !equal {
                push(out, path, reference, candidate);
            }
        }
        (Value::Array(a), Value::Array(b)) => {
            if a.len() != b.len() {
                push(out, path, reference, candidate);
                return;
            }
            for (i, (x, y)) in a.iter().zip(b).enumerate() {
                walk(x, y, &format!("{path}[{i}]"), out);
            }
        }
        (Value::Object(a), Value::Object(b)) => {
            for (k, x) in a {
                match b.get(k) {
                    Some(y) => walk(x, y, &format!("{path}.{k}"), out),
                    None => out.push(Discrepancy {
                        path: format!("{path}.{k}"),
                        reference: compact(x),
                        candidate: String::from("<missing>"),
                    }),
                }
            }
            for (k, y) in b {
                if !a.contains_key(k) {
                    out.push(Discrepancy {
                        path: format!("{path}.{k}"),
                        reference: String::from("<missing>"),
                        candidate: compact(y),
                    });
                }
            }
        }
        (a, b) => {
            if a != b {
                push(out, path, a, b);
            }
        }
    }
}

fn push(out: &mut Vec<Discrepancy>, path: &str, reference: &Value, candidate: &Value) {
    out.push(Discrepancy {
        path: path.to_string(),
        reference: compact(reference),
        candidate: compact(candidate),
    });
}

fn compact(value: &Value) -> String {
    serde_json::to_string(value).unwrap_or_else(|_| String::from("<unrenderable>"))
}

/// A parity ledger line: engine identities (C2 — "the two that agreed") and
/// the count. The full discrepancy list is the caller's to print or serialise.
#[derive(Debug, Clone)]
pub struct ParitySummary {
    /// The reference engine identity, e.g. `python:harness.electronics...`.
    pub reference: String,
    /// The candidate engine identity, e.g. `rust:dw_extract::grid::build_grid`.
    pub candidate: String,
    /// Subjects compared in this run.
    pub subjects: usize,
    /// Subjects with zero discrepancies.
    pub agreements: usize,
    /// Total discrepancy records across all subjects.
    pub discrepancies: usize,
}

impl ParitySummary {
    /// `cutover_permitted` — yes only on an empty ledger.
    #[must_use]
    pub fn cutover_permitted(&self) -> bool {
        self.discrepancies == 0
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::indexing_slicing)]
mod tests {
    use serde_json::json;

    use super::*;

    /// Gate: agreement on structurally equal wires, including floats that
    /// differ only in serialisation (`1.5e-08` parses equal on both sides).
    #[test]
    fn diff_wire_agrees_on_equal_structure() {
        let a = json!({"rows": [{"value": 1.5e-8, "label": "x", "pages": [1, 2]}], "n": 3});
        let b = serde_json::from_str::<Value>(
            r#"{"rows":[{"value":1.5e-8,"label":"x","pages":[1,2]}],"n":3}"#,
        )
        .unwrap();
        assert!(diff_wire(&a, &b).is_empty());
    }

    /// Negative control (ASSURANCE R2): a one-unit mutation must be caught —
    /// if this passes while the gate is green on real data, the comparator is
    /// not discriminating.
    #[test]
    fn negative_control_value_mutation_is_caught() {
        let a = json!({"rows": [{"value": 48, "label": "x"}]});
        let b = json!({"rows": [{"value": 47, "label": "x"}]});
        let diffs = diff_wire(&a, &b);
        assert_eq!(diffs.len(), 1);
        assert_eq!(diffs[0].path, "$.rows[0].value");
    }

    /// Negative control: missing and extra keys are discrepancies — an
    /// asymmetric wire is exactly what the reference's `null`-vs-omitted
    /// shapes produce when a port drifts.
    #[test]
    fn negative_control_key_drift_is_caught() {
        let a = json!({"kept": 1, "gone": 2});
        let b = json!({"kept": 1, "extra": 2});
        let diffs = diff_wire(&a, &b);
        assert_eq!(diffs.len(), 2);
    }

    /// Gate: the summary's cutover flag is driven by the ledger alone.
    #[test]
    fn cutover_permitted_only_on_empty_ledger() {
        let clean = ParitySummary {
            reference: "python:x".into(),
            candidate: "rust:y".into(),
            subjects: 2,
            agreements: 2,
            discrepancies: 0,
        };
        let dirty = ParitySummary {
            reference: clean.reference.clone(),
            candidate: clean.candidate.clone(),
            subjects: clean.subjects,
            agreements: 1,
            discrepancies: 1,
        };
        assert!(clean.cutover_permitted());
        assert!(!dirty.cutover_permitted());
    }
}
