//! # dw-parity — the discrepancy ledger
//!
//! The Python reference produces the truth throughout the port. This crate
//! runs the Rust candidate beside it over a frozen corpus and records every
//! difference.
//!
//! **Week 0 gate:** the ledger runs empty against Python's `quote_verify` on
//! the frozen set. An empty ledger is not "no differences found yet" — it is
//! the proof that the port has not drifted.
//!
//! **D2 classification.** Every difference gets exactly one class. Cutover is
//! permitted only when `unexplained == 0` and `rust_bug == 0`.
//!
//! | class | blocks cutover | meaning |
//! |---|---|---|
//! | [`DiffClass::RustBug`] | **yes** | the port is wrong |
//! | [`DiffClass::PythonBug`] | no | the reference is wrong; fix or document |
//! | [`DiffClass::IntentionalImprovement`] | no | deliberate change, needs a signed entry in `DECISIONS.md` |
//! | [`DiffClass::Unexplained`] | **yes** | nobody knows yet |
//!
//! This crate is `forbid(unsafe_code)`. It calls no models.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

use std::collections::BTreeMap;
use std::fmt;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

// ---------------------------------------------------------------------------
// errors
// ---------------------------------------------------------------------------

/// Failures from running a parity comparison.
#[derive(Debug, thiserror::Error)]
pub enum ParityError {
    /// A referenced artifact is missing.
    #[error("missing artifact: {0}")]
    MissingArtifact(PathBuf),
    /// A payload failed to parse.
    #[error("cannot parse {path}: {detail}")]
    Parse {
        /// Which file.
        path: PathBuf,
        /// What went wrong.
        detail: String,
    },
    /// A surface runner failed.
    #[error("surface {surface} failed: {detail}")]
    Surface {
        /// Which surface.
        surface: String,
        /// What went wrong.
        detail: String,
    },
}

// ---------------------------------------------------------------------------
// D2 classification
// ---------------------------------------------------------------------------

/// What a difference between reference and candidate means.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DiffClass {
    /// The Rust port is wrong. **Blocks cutover.**
    RustBug,
    /// The Python reference is wrong. Does not block, but must be fixed or
    /// recorded.
    PythonBug,
    /// A deliberate change. Does not block, but requires a signed entry in
    /// `DECISIONS.md` naming the decision.
    IntentionalImprovement,
    /// Not yet understood. **Blocks cutover.**
    Unexplained,
}

impl DiffClass {
    /// Whether this class blocks cutover (D2).
    #[must_use]
    pub const fn blocks_cutover(self) -> bool {
        matches!(self, Self::RustBug | Self::Unexplained)
    }

    /// Every class, for reporting.
    pub const ALL: [DiffClass; 4] = [
        Self::RustBug,
        Self::PythonBug,
        Self::IntentionalImprovement,
        Self::Unexplained,
    ];
}

impl fmt::Display for DiffClass {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::RustBug => "rust_bug",
            Self::PythonBug => "python_bug",
            Self::IntentionalImprovement => "intentional_improvement",
            Self::Unexplained => "unexplained",
        })
    }
}

// ---------------------------------------------------------------------------
// ledger
// ---------------------------------------------------------------------------

/// One recorded difference.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct LedgerEntry {
    /// Which comparison surface produced it (e.g. `quote_verify`).
    pub surface: String,
    /// Which object was being compared (part number, document sha, job id).
    pub subject: String,
    /// D2 classification. Required: an entry cannot exist unclassified.
    pub class: DiffClass,
    /// What the Python reference produced, serialised.
    pub reference: serde_json::Value,
    /// What the Rust candidate produced, serialised.
    pub candidate: serde_json::Value,
    /// Human explanation. Required for [`DiffClass::IntentionalImprovement`].
    pub note: String,
}

impl LedgerEntry {
    /// Record a difference. `note` must be non-empty.
    #[must_use]
    pub fn new(
        surface: impl Into<String>,
        subject: impl Into<String>,
        class: DiffClass,
        reference: serde_json::Value,
        candidate: serde_json::Value,
        note: impl Into<String>,
    ) -> Self {
        Self {
            surface: surface.into(),
            subject: subject.into(),
            class,
            reference,
            candidate,
            note: note.into(),
        }
    }
}

/// The discrepancy ledger. Append-only in spirit: entries are added, never
/// silently dropped.
#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Ledger {
    entries: Vec<LedgerEntry>,
}

impl Ledger {
    /// An empty ledger.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// Record a difference.
    pub fn push(&mut self, entry: LedgerEntry) {
        self.entries.push(entry);
    }

    /// Number of entries.
    #[must_use]
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// True when the ledger is empty — the Week 0 gate.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// All entries, in insertion order.
    #[must_use]
    pub fn entries(&self) -> &[LedgerEntry] {
        &self.entries
    }

    /// Count per class.
    #[must_use]
    pub fn counts(&self) -> BTreeMap<DiffClass, usize> {
        let mut out: BTreeMap<DiffClass, usize> = BTreeMap::new();
        for e in &self.entries {
            *out.entry(e.class).or_insert(0) += 1;
        }
        out
    }

    /// Count for one class.
    #[must_use]
    pub fn count(&self, class: DiffClass) -> usize {
        self.entries.iter().filter(|e| e.class == class).count()
    }

    /// D2: cutover is permitted when nothing blocking remains.
    #[must_use]
    pub fn cutover_permitted(&self) -> bool {
        !DiffClass::RustBug.blocking_present(self) && !DiffClass::Unexplained.blocking_present(self)
    }

    /// Render a one-screen summary.
    #[must_use]
    pub fn summary(&self) -> String {
        use std::fmt::Write as _;
        let counts = self.counts();
        let mut s = String::new();
        let _ = writeln!(s, "parity ledger: {} entries", self.entries.len());
        for class in DiffClass::ALL {
            let _ = writeln!(s, "  {class:<24} {}", counts.get(&class).unwrap_or(&0));
        }
        let _ = writeln!(
            s,
            "  cutover_permitted       {}",
            if self.cutover_permitted() {
                "yes"
            } else {
                "NO"
            }
        );
        s
    }
}

impl DiffClass {
    fn blocking_present(self, ledger: &Ledger) -> bool {
        ledger.count(self) > 0
    }
}

// ---------------------------------------------------------------------------
// the comparison surface
// ---------------------------------------------------------------------------

/// One unit of comparison.
///
/// A surface owns a named slice of behaviour — `quote_verify`,
/// `condition_model`, `rating_class`, `curve_atoms`, … — and knows how to
/// produce a serialised result for both sides. The runner does the diffing and
/// the classification, so every surface is compared the same way.
pub trait Surface {
    /// Stable name, used in ledger entries.
    fn name(&self) -> &'static str;

    /// Objects to compare, as stable identifiers.
    ///
    /// # Errors
    ///
    /// Returns [`ParityError`] if the subject set cannot be enumerated.
    fn subjects(&self) -> Result<Vec<String>, ParityError>;

    /// The Python reference result for one subject.
    ///
    /// # Errors
    ///
    /// Returns [`ParityError`] if the reference cannot be produced.
    fn reference(&self, subject: &str) -> Result<serde_json::Value, ParityError>;

    /// The Rust candidate result for one subject.
    ///
    /// # Errors
    ///
    /// Returns [`ParityError`] if the candidate cannot be produced.
    fn candidate(&self, subject: &str) -> Result<serde_json::Value, ParityError>;
}

/// Run one surface over all its subjects and return the ledger of differences.
///
/// # Errors
///
/// Returns [`ParityError`] if a surface cannot enumerate or evaluate a subject.
/// Evaluation failures are *not* diffs — they are infrastructure faults and
/// must not be recorded as parity results.
pub fn run_surface(surface: &dyn Surface) -> Result<Ledger, ParityError> {
    let mut ledger = Ledger::new();
    for subject in surface.subjects()? {
        let reference = surface.reference(&subject)?;
        let candidate = surface.candidate(&subject)?;
        if reference != candidate {
            ledger.push(LedgerEntry::new(
                surface.name(),
                subject,
                DiffClass::Unexplained,
                reference,
                candidate,
                "auto-classified as unexplained; triage required before cutover",
            ));
        }
    }
    Ok(ledger)
}

/// Run several surfaces and merge their ledgers.
///
/// # Errors
///
/// Returns [`ParityError`] on the first surface that fails to run.
pub fn run_surfaces(surfaces: &[&dyn Surface]) -> Result<Ledger, ParityError> {
    let mut merged = Ledger::new();
    for surface in surfaces {
        for entry in run_surface(*surface)?.entries {
            merged.push(entry);
        }
    }
    Ok(merged)
}

// ---------------------------------------------------------------------------
// frozen corpus
// ---------------------------------------------------------------------------

/// A pinned corpus: the input set every parity run is measured against.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CorpusManifest {
    /// Manifest schema version. Unknown major is a hard reject (`C6`).
    pub schema_version: String,
    /// The release this freeze belongs to.
    pub release: String,
    /// `(content sha256, byte length, path)` for every member, sorted.
    pub members: Vec<CorpusMember>,
}

/// One frozen input.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct CorpusMember {
    /// Content hash. The only identity that matters (`C1`).
    pub sha256: String,
    /// Size in bytes, as a cheap cross-check.
    pub bytes: u64,
    /// Where it was found when the freeze was taken.
    pub path: String,
}

impl CorpusManifest {
    /// Total bytes across the freeze.
    #[must_use]
    pub fn total_bytes(&self) -> u64 {
        self.members.iter().map(|m| m.bytes).sum()
    }

    /// Number of members.
    #[must_use]
    pub fn len(&self) -> usize {
        self.members.len()
    }

    /// True when the freeze is empty.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.members.is_empty()
    }

    /// Load a manifest from disk.
    ///
    /// # Errors
    ///
    /// Returns [`ParityError::MissingArtifact`] if absent, or
    /// [`ParityError::Parse`] if it does not parse.
    pub fn load(path: impl AsRef<Path>) -> Result<Self, ParityError> {
        let path = path.as_ref();
        let text = std::fs::read_to_string(path)
            .map_err(|_| ParityError::MissingArtifact(path.to_path_buf()))?;
        serde_json::from_str(&text).map_err(|e| ParityError::Parse {
            path: path.to_path_buf(),
            detail: e.to_string(),
        })
    }
}

pub mod replace;

pub use replace::{
    run_replacement, subjects_from_jsonl, ClosureCandidate, CommandReference, EnginePair,
};

#[cfg(test)]
#[allow(clippy::expect_used, clippy::unwrap_used)]
mod tests {
    use super::*;

    struct AlwaysEqual;
    impl Surface for AlwaysEqual {
        fn name(&self) -> &'static str {
            "always_equal"
        }
        fn subjects(&self) -> Result<Vec<String>, ParityError> {
            Ok(vec!["a".into(), "b".into()])
        }
        fn reference(&self, s: &str) -> Result<serde_json::Value, ParityError> {
            Ok(serde_json::json!({ "subject": s }))
        }
        fn candidate(&self, s: &str) -> Result<serde_json::Value, ParityError> {
            Ok(serde_json::json!({ "subject": s }))
        }
    }

    struct AlwaysDiffers;
    impl Surface for AlwaysDiffers {
        fn name(&self) -> &'static str {
            "always_differs"
        }
        fn subjects(&self) -> Result<Vec<String>, ParityError> {
            Ok(vec!["x".into()])
        }
        fn reference(&self, _: &str) -> Result<serde_json::Value, ParityError> {
            Ok(serde_json::json!({"v": 1}))
        }
        fn candidate(&self, _: &str) -> Result<serde_json::Value, ParityError> {
            Ok(serde_json::json!({"v": 2}))
        }
    }

    #[test]
    fn week0_gate_ledger_runs_empty() {
        let ledger = run_surface(&AlwaysEqual).expect("runs");
        assert!(ledger.is_empty());
        assert!(ledger.cutover_permitted());
    }

    #[test]
    fn a_difference_is_recorded_unexplained() {
        let ledger = run_surface(&AlwaysDiffers).expect("runs");
        assert_eq!(ledger.len(), 1);
        assert_eq!(ledger.count(DiffClass::Unexplained), 1);
        assert!(!ledger.cutover_permitted());
    }

    #[test]
    fn cutover_blocked_by_rust_bug_or_unexplained() {
        let mut ledger = Ledger::new();
        ledger.push(LedgerEntry::new(
            "s",
            "o",
            DiffClass::PythonBug,
            serde_json::json!(1),
            serde_json::json!(2),
            "reference is wrong",
        ));
        assert!(ledger.cutover_permitted());

        ledger.push(LedgerEntry::new(
            "s",
            "o",
            DiffClass::RustBug,
            serde_json::json!(1),
            serde_json::json!(2),
            "port is wrong",
        ));
        assert!(!ledger.cutover_permitted());
    }

    #[test]
    fn intentional_improvement_does_not_block() {
        let mut ledger = Ledger::new();
        ledger.push(LedgerEntry::new(
            "s",
            "o",
            DiffClass::IntentionalImprovement,
            serde_json::json!(1),
            serde_json::json!(2),
            "see DECISIONS.md D-xx",
        ));
        assert!(ledger.cutover_permitted());
        assert_eq!(ledger.count(DiffClass::IntentionalImprovement), 1);
    }

    #[test]
    fn summary_names_every_class() {
        let s = Ledger::new().summary();
        for class in DiffClass::ALL {
            assert!(s.contains(&class.to_string()));
        }
    }

    #[test]
    fn run_surfaces_merges() {
        let surfaces: Vec<&dyn Surface> = vec![&AlwaysEqual, &AlwaysDiffers];
        let ledger = run_surfaces(&surfaces).expect("runs");
        assert_eq!(ledger.len(), 1);
    }
}
