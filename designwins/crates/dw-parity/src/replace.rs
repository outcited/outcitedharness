//! The replacement gate — proof that the Rust port supersedes the Python.
//!
//! The gold gates (`N1`, `N4a`, `N4b`) compare Rust against fixtures **that
//! were generated from the Python reference**. That proves parity against a
//! snapshot. It does not prove the two agree *today*, and it cannot catch a
//! change on either side that the snapshot predates.
//!
//! This gate closes that. It invokes **both engines live, on the same inputs,
//! in the same run**, and writes a discrepancy ledger.
//!
//! ```text
//! subject ──┬─> Python reference (subprocess) ──┐
//!           │                                    ├─> diff ─> Ledger
//!           └─> Rust candidate  (in-process)  ──┘
//! ```
//!
//! An empty ledger is the replacement proof: the Rust candidate produces
//! exactly what the Python produced, on the corpus as it stands right now.
//!
//! ## Why a subprocess for the reference
//!
//! The reference is Python and must stay Python — it is the oracle. Calling it
//! through `std::process::Command` keeps the two engines in separate address
//! spaces so neither can accidentally share state, and makes the reference
//! swappable (`python3 -m harness...`) without touching the Rust side.
//!
//! ## `C2` applies
//!
//! Both engines are recorded in the ledger, because "the two agree" is only
//! meaningful together with "and these are the two that agreed."

use std::path::PathBuf;
use std::process::Command;

use serde_json::Value;

use crate::{DiffClass, Ledger, LedgerEntry, ParityError};

/// A reference implementation invoked as a subprocess.
///
/// The command must read one subject per line on stdin and write one JSON value
/// per line on stdout, in the same order. That contract keeps the harness
/// generic: the Rust side never has to know what language the reference is.
pub struct CommandReference {
    /// Name used in ledger entries.
    pub name: String,
    /// Program and arguments, e.g. `["python3", "-m", "harness.parity_ref"]`.
    pub argv: Vec<String>,
    /// Working directory for the subprocess.
    pub cwd: PathBuf,
}

impl CommandReference {
    /// Run the reference over a batch of subjects, in order.
    ///
    /// # Errors
    ///
    /// Returns [`ParityError::Surface`] if the process fails or emits a
    /// different number of lines than it was given. A reference that cannot
    /// produce one output per input is not a reference.
    pub fn run(&self, subjects: &[String]) -> Result<Vec<Value>, ParityError> {
        let (program, args) = self
            .argv
            .split_first()
            .ok_or_else(|| ParityError::Surface {
                surface: self.name.clone(),
                detail: "empty argv".to_string(),
            })?;

        let mut child = Command::new(program)
            .args(args)
            .current_dir(&self.cwd)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .map_err(|e| ParityError::Surface {
                surface: self.name.clone(),
                detail: format!("spawn {program}: {e}"),
            })?;

        // stdin must be written on its own thread. Writing it inline deadlocks
        // once the batch exceeds the pipe buffer: the parent blocks on a full
        // stdin while the child blocks on a full stdout, and neither moves.
        // Verified — 300 subjects worked, 10,270 hung. (ASSURANCE R2: the
        // negative control is a batch larger than the buffer, and the first
        // version of this function did not have one.)
        let mut stdin = child.stdin.take().ok_or_else(|| ParityError::Surface {
            surface: self.name.clone(),
            detail: "no stdin".to_string(),
        })?;
        let batch: Vec<String> = subjects.to_vec();
        let writer = std::thread::spawn(move || -> std::io::Result<()> {
            use std::io::Write as _;
            for s in &batch {
                writeln!(stdin, "{s}")?;
            }
            // Dropping stdin sends EOF; without it the reference blocks on read.
            drop(stdin);
            Ok(())
        });

        let output = child.wait_with_output().map_err(|e| ParityError::Surface {
            surface: self.name.clone(),
            detail: format!("wait: {e}"),
        })?;
        writer
            .join()
            .map_err(|_| ParityError::Surface {
                surface: self.name.clone(),
                detail: "stdin writer panicked".to_string(),
            })?
            .map_err(|e| ParityError::Surface {
                surface: self.name.clone(),
                detail: format!("write stdin: {e}"),
            })?;

        if !output.status.success() {
            return Err(ParityError::Surface {
                surface: self.name.clone(),
                detail: format!(
                    "reference exited {}: {}",
                    output.status,
                    String::from_utf8_lossy(&output.stderr)
                ),
            });
        }

        let text = String::from_utf8_lossy(&output.stdout);
        let mut out = Vec::with_capacity(subjects.len());
        for line in text.lines().filter(|l| !l.trim().is_empty()) {
            out.push(
                serde_json::from_str(line).map_err(|e| ParityError::Surface {
                    surface: self.name.clone(),
                    detail: format!("reference emitted bad JSON: {e}"),
                })?,
            );
        }

        if out.len() != subjects.len() {
            return Err(ParityError::Surface {
                surface: self.name.clone(),
                detail: format!(
                    "reference produced {} outputs for {} subjects",
                    out.len(),
                    subjects.len()
                ),
            });
        }
        Ok(out)
    }
}

/// A candidate implementation as an in-process closure.
pub struct ClosureCandidate<F>
where
    F: Fn(&str) -> Value,
{
    /// Name used in ledger entries.
    pub name: String,
    /// The candidate.
    pub run: F,
}

/// Run both engines over `subjects` and return the discrepancy ledger.
///
/// Comparison is on [`serde_json::Value`] after key sorting, so map ordering
/// cannot manufacture a difference. Numeric values are compared with a tight
/// relative tolerance for the same reason the gold gates do it: the fixtures
/// were serialised compactly and bit-exactness would test the serialiser.
///
/// # Errors
///
/// Returns [`ParityError::Surface`] if either engine fails to run. An engine
/// that cannot produce an answer is an infrastructure fault, **not** a parity
/// result, and must not be recorded in the ledger as one.
pub fn run_replacement<F>(
    reference: &CommandReference,
    candidate: &ClosureCandidate<F>,
    subjects: &[String],
) -> Result<Ledger, ParityError>
where
    F: Fn(&str) -> Value,
{
    let expected = reference.run(subjects)?;
    let mut ledger = Ledger::new();

    for (subject, want) in subjects.iter().zip(expected.iter()) {
        let got = (candidate.run)(subject);
        if !values_match(&canonicalize(want), &canonicalize(&got)) {
            ledger.push(LedgerEntry::new(
                format!("{} vs {}", reference.name, candidate.name),
                subject.clone(),
                DiffClass::Unexplained,
                want.clone(),
                got,
                "live side-by-side: engines disagree. Triage before cutover (D2).",
            ));
        }
    }
    Ok(ledger)
}

/// Engine identities, so "the two agreed" is never anonymous.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EnginePair {
    /// The reference, e.g. `"python3 harness.electronics.condition_model"`.
    pub reference: String,
    /// The candidate, e.g. `"dw-normalize 0.1.0"`.
    pub candidate: String,
}

impl EnginePair {
    /// Render into a report line.
    #[must_use]
    pub fn describe(&self) -> String {
        format!("{}  vs  {}", self.reference, self.candidate)
    }
}

/// Recursively sort object keys.
fn canonicalize(v: &Value) -> Value {
    match v {
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let mut out = serde_json::Map::new();
            for k in keys {
                out.insert(k.clone(), canonicalize(&map[k]));
            }
            Value::Object(out)
        }
        Value::Array(items) => {
            // If every element is an object carrying a `field` key, the array
            // is a keyed record set and its order is not semantic. The
            // reference emits claim order; a map-based candidate emits sorted
            // order. Sorting here makes the comparison about content.
            let keyed = !items.is_empty()
                && items
                    .iter()
                    .all(|i| i.get("field").and_then(|f| f.as_str()).is_some());
            let mut mapped: Vec<Value> = items.iter().map(canonicalize).collect();
            if keyed {
                mapped.sort_by_key(|v| {
                    v.get("field")
                        .and_then(|f| f.as_str())
                        .unwrap_or_default()
                        .to_string()
                });
            }
            Value::Array(mapped)
        }
        other => other.clone(),
    }
}

/// Structural equality with a tight relative tolerance on numbers.
fn values_match(a: &Value, b: &Value) -> bool {
    match (a, b) {
        (Value::Number(x), Value::Number(y)) => {
            let (Some(x), Some(y)) = (x.as_f64(), y.as_f64()) else {
                return x == y;
            };
            let scale = 1.0_f64.max(x.abs()).max(y.abs());
            (x - y).abs() <= 1e-12 * scale
        }
        (Value::Object(x), Value::Object(y)) => {
            x.len() == y.len()
                && x.iter()
                    .all(|(k, v)| y.get(k).is_some_and(|w| values_match(v, w)))
        }
        (Value::Array(x), Value::Array(y)) => {
            x.len() == y.len() && x.iter().zip(y).all(|(u, v)| values_match(u, v))
        }
        _ => a == b,
    }
}

/// Subject lists loaded from a JSONL file, one subject per row under `key`.
///
/// # Errors
///
/// Returns [`ParityError::MissingArtifact`] or [`ParityError::Parse`].
pub fn subjects_from_jsonl(
    path: impl AsRef<std::path::Path>,
    key: &str,
) -> Result<Vec<String>, ParityError> {
    let path = path.as_ref();
    let text = std::fs::read_to_string(path)
        .map_err(|_| ParityError::MissingArtifact(path.to_path_buf()))?;
    let mut out = Vec::new();
    for (n, line) in text.lines().filter(|l| !l.trim().is_empty()).enumerate() {
        let v: Value = serde_json::from_str(line).map_err(|e| ParityError::Parse {
            path: path.to_path_buf(),
            detail: format!("line {}: {e}", n + 1),
        })?;
        let subject = v
            .get(key)
            .and_then(|s| s.as_str())
            .ok_or_else(|| ParityError::Parse {
                path: path.to_path_buf(),
                detail: format!("line {}: missing string field {key}", n + 1),
            })?;
        out.push(subject.to_string());
    }
    Ok(out)
}

/// Summarise a ledger for a human, naming the engines.
#[must_use]
pub fn replacement_report(pair: &EnginePair, subjects: usize, ledger: &Ledger) -> String {
    use std::fmt::Write as _;
    let mut s = String::new();
    let _ = writeln!(s, "replacement gate: {}", pair.describe());
    let _ = writeln!(s, "  subjects compared  {subjects}");
    let _ = writeln!(
        s,
        "  agreements         {}",
        subjects.saturating_sub(ledger.len())
    );
    let _ = writeln!(s, "  discrepancies      {}", ledger.len());
    for (class, n) in ledger.counts() {
        let _ = writeln!(s, "    {class:<24} {n}");
    }
    let _ = writeln!(
        s,
        "  cutover_permitted  {}",
        if ledger.cutover_permitted() {
            "yes"
        } else {
            "NO"
        }
    );
    s
}

#[cfg(test)]
#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    clippy::match_wildcard_for_single_variants,
    clippy::wildcard_enum_match_arm,
    clippy::too_many_lines
)]
mod tests {
    use super::*;

    /// **Negative control** (`ASSURANCE.md` R2): the gate must be able to go
    /// red. A comparison that returns "equal" for everything would make the
    /// replacement proof worthless.
    #[test]
    fn negative_control_gate_detects_divergence() {
        let got = json(r#"{"keys":{"vgs_v":1.0}}"#);
        let want = json(r#"{"keys":{"vgs_v":0.0}}"#);
        assert!(values_match(&want, &want));
        assert!(
            !values_match(&want, &got),
            "a diverged value must be detected"
        );
    }

    #[test]
    fn negative_control_detects_missing_and_extra_fields() {
        let want = json(r#"{"keys":{"a":1},"mode":"ac"}"#);
        assert!(!values_match(&want, &json(r#"{"keys":{"a":1}}"#)));
        assert!(!values_match(
            &want,
            &json(r#"{"keys":{"a":1},"mode":"ac","residue":["x"]}"#)
        ));
    }

    #[test]
    fn numeric_tolerance_is_relative_and_tight() {
        assert!(values_match(
            &json("1.5e-8"),
            &json("1.5000000000000002e-8")
        ));
        assert!(!values_match(&json("1.0"), &json("1.01")));
    }

    #[test]
    fn canonicalize_sorts_keys() {
        let a = canonicalize(&json(r#"{"b":1,"a":2}"#));
        let b = canonicalize(&json(r#"{"a":2,"b":1}"#));
        assert_eq!(a, b);
    }

    fn json(s: &str) -> Value {
        serde_json::from_str(s).expect("json")
    }

    /// **Negative control** (`ASSURANCE.md` R2) for the pipe deadlock.
    ///
    /// Writing stdin inline deadlocks once the batch exceeds the OS pipe buffer:
    /// the parent blocks on a full stdin while the child blocks on a full stdout.
    /// Measured here — 300 subjects worked, 10,270 hung. A gate that is only
    /// tested below the buffer size cannot see it, which is exactly how it shipped.
    #[test]
    fn negative_control_reference_survives_a_batch_larger_than_the_pipe_buffer() {
        let script = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../scripts/parity_reference.py");
        if !script.exists() {
            // The reference is only present in the full repo; skip rather than
            // fail in a stripped checkout.
            return;
        }
        let cwd = script
            .parent()
            .and_then(std::path::Path::parent)
            .expect("repo root")
            .to_path_buf();

        let reference = CommandReference {
        name: "python:echo".to_string(),
        // A trivial echo is enough: the deadlock is in the transport, not the
        // reference's logic.
        argv: vec![
            "python3".to_string(),
            "-c".to_string(),
            "import sys\\nfor line in sys.stdin:\\n    sys.stdout.write(line)\\n    sys.stdout.flush()\\n"
                .to_string(),
        ],
        cwd,
    };

        // Comfortably past the 64 KiB pipe buffer.
        let subjects: Vec<String> = (0..4000).map(|i| format!("subject-{i:06}")).collect();
        let got = reference.run(&subjects).expect("no deadlock");
        assert_eq!(got.len(), subjects.len());
    }
}
