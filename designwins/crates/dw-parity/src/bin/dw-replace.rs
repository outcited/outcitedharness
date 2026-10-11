//! `dw-replace` — the replacement gate.
//!
//! Invokes the Python reference and the Rust candidate **live, on the same
//! inputs, in the same run**, and reports the discrepancy ledger.
//!
//! ```text
//! dw-replace --mode condition --corpus tests/fixtures/gold/claim_conditions_v1.jsonl
//! ```
//!
//! An empty ledger is the proof that the Rust port supersedes the Python:
//! both engines produced identical output for every subject as it stands
//! today. This is stronger than the gold gates, which compare against a
//! snapshot generated from the reference at some earlier moment.
//!
//! Exit codes: `0` ledger clean · `1` discrepancies · `2` bad invocation ·
//! `3` an engine failed (infrastructure fault, never a parity result).

#![forbid(unsafe_code)]

use std::path::PathBuf;
use std::process::ExitCode;

use dw_normalize::parse_condition;
use dw_parity::replace::{
    run_replacement, subjects_from_jsonl, ClosureCandidate, CommandReference, EnginePair,
};
use dw_parity::ParityError;

fn usage() -> &'static str {
    "usage: dw-replace --mode condition --corpus <jsonl> [--field <key>] [--limit N] [--out <ledger.json>]\n\n\
     Runs the Python reference and the Rust candidate on the same subjects and\n\
     reports the discrepancy ledger. Empty ledger = the Rust port replaces the\n\
     Python for that surface.\n"
}

/// CLI entry: parse arguments, run both engines, report, exit.
///
/// # Lints
///
/// A `main` that parses flags, runs a gate, and maps the result to an exit code
/// is one linear sequence. Splitting it would scatter the exit-code contract,
/// which is the part a caller depends on. `clippy::too_many_lines` is allowed
/// here and nowhere else in the crate.
///
/// # Panics
///
/// The single `expect` is on serialising a `ParseOutcome`, which has a
/// `Serialize` impl and cannot fail.
#[allow(clippy::too_many_lines, clippy::expect_used)]
fn main() -> ExitCode {
    let mut mode = String::new();
    let mut corpus = PathBuf::new();
    let mut field = String::from("condition_verbatim");
    let mut limit: Option<usize> = None;
    let mut out: Option<PathBuf> = None;

    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--mode" => mode = args.next().unwrap_or_default(),
            "--corpus" => corpus = PathBuf::from(args.next().unwrap_or_default()),
            "--field" => field = args.next().unwrap_or_default(),
            "--limit" => limit = args.next().and_then(|v| v.parse().ok()),
            "--out" => out = Some(PathBuf::from(args.next().unwrap_or_default())),
            "-h" | "--help" => {
                eprintln!("{}", usage());
                return ExitCode::from(2);
            }
            other => {
                eprintln!("unknown argument: {other}\n{}", usage());
                return ExitCode::from(2);
            }
        }
    }

    if mode != "condition" || corpus.as_os_str().is_empty() {
        eprintln!("{}", usage());
        return ExitCode::from(2);
    }

    // The reference runs from the repo root so  imports.
    // Walk up rather than counting: the workspace sits at  inside
    // the repo, and the count changes if either moves.
    let repo_root = {
        let mut d = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        loop {
            if d.join("scripts/parity_reference.py").exists()
                && d.join("harness/electronics/condition_model.py").exists()
            {
                break d;
            }
            if !d.pop() {
                eprintln!("dw-replace: could not locate the repo root");
                return ExitCode::from(3);
            }
        }
    };

    let mut subjects = match subjects_from_jsonl(&corpus, &field) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("dw-replace: {e}");
            return ExitCode::FAILURE;
        }
    };
    if let Some(n) = limit {
        subjects.truncate(n);
    }

    let reference = CommandReference {
        name: "python:harness.electronics.condition_model.parse_condition".to_string(),
        argv: vec![
            "python3".to_string(),
            repo_root
                .join("scripts/parity_reference.py")
                .to_string_lossy()
                .into(),
            "condition".to_string(),
        ],
        cwd: repo_root.clone(),
    };
    let candidate = ClosureCandidate {
        name: "rust:dw_normalize::parse_condition".to_string(),
        run: |s: &str| serde_json::to_value(parse_condition(s)).expect("serialises"),
    };

    let pair = EnginePair {
        reference: reference.name.clone(),
        candidate: candidate.name.clone(),
    };

    let ledger = match run_replacement(&reference, &candidate, &subjects) {
        Ok(l) => l,
        Err(ParityError::Surface { surface, detail }) => {
            eprintln!("dw-replace: engine failure in {surface}: {detail}");
            eprintln!("dw-replace: an engine that cannot answer is NOT a parity result");
            return ExitCode::from(3);
        }
        Err(e) => {
            eprintln!("dw-replace: {e}");
            return ExitCode::FAILURE;
        }
    };

    print!(
        "{}",
        dw_parity::replace::replacement_report(&pair, subjects.len(), &ledger)
    );
    for entry in ledger.entries().iter().take(10) {
        eprintln!(
            "  diff [{}] {}\n    reference {}\n    candidate {}",
            entry.subject, entry.note, entry.reference, entry.candidate
        );
    }
    if ledger.len() > 10 {
        eprintln!("  … and {} more", ledger.len() - 10);
    }

    if let Some(path) = &out {
        let text = serde_json::to_string_pretty(&ledger).expect("ledger serialises");
        if let Err(e) = std::fs::write(path, text) {
            eprintln!("dw-replace: cannot write {}: {e}", path.display());
            return ExitCode::FAILURE;
        }
    }

    if ledger.is_empty() {
        println!("\nREPLACEMENT PROVEN: the Rust candidate reproduces the reference.");
        ExitCode::SUCCESS
    } else {
        println!("\nNOT YET: discrepancies remain. Triage per D2 before cutover.");
        ExitCode::FAILURE
    }
}
