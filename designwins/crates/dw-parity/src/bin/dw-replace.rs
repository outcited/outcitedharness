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
use dw_truth::verify_claims;
use serde_json::Value;

fn usage() -> &'static str {
    "usage: dw-replace --mode <condition|quote|claims> --corpus <jsonl> [--field <key>] [--limit N] [--out <ledger.json>]\n\n\
     Runs the Python reference and the Rust candidate on the same subjects and\n\
     reports the discrepancy ledger. Empty ledger = the Rust port replaces the\n\
     Python for that surface.\n\
     mode=condition  subject is a condition string; --field names its column\n\
     mode=quote      subject is a case path under tests/fixtures/gold/quote_parity/\n\
     mode=claims     subject is a claim id; --corpus is claims.jsonl and the\n\
                     doc text map comes from $N4_DOCTEXT (Gate N4, the 87K diff)\n"
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

    if corpus.as_os_str().is_empty() || !matches!(mode.as_str(), "condition" | "quote" | "claims") {
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

    let (ref_name, cand_name) = match mode.as_str() {
        "condition" => (
            "python:harness.electronics.condition_model.parse_condition",
            "rust:dw_normalize::parse_condition",
        ),
        // `quote` and `claims` share the same engine pair.
        _ => (
            "python:harness.pipeline.quote_verify.verify_claims",
            "rust:dw_truth::verify_claims",
        ),
    };
    let reference = CommandReference {
        name: ref_name.to_string(),
        argv: vec![
            "python3".to_string(),
            repo_root
                .join("scripts/parity_reference.py")
                .to_string_lossy()
                .into(),
            mode.clone(),
        ],
        cwd: repo_root.clone(),
    };

    let cases_root = repo_root.join("tests/fixtures/gold/quote_parity");
    let mode_for_candidate = mode.clone();

    // Gate N4: load the claim corpus + doc text once, shared by every subject.
    // Paths come from env so the Python reference reads byte-identical evidence.
    let (n4_by_id, n4_doctext) = if mode_for_candidate == "claims" {
        let claims_path = std::env::var("N4_CLAIMS").expect("N4_CLAIMS env");
        let doctext_path = std::env::var("N4_DOCTEXT").expect("N4_DOCTEXT env");
        let mut by_id: std::collections::HashMap<String, Value> = std::collections::HashMap::new();
        for line in std::fs::read_to_string(&claims_path)
            .expect("claims readable")
            .lines()
        {
            if line.trim().is_empty() {
                continue;
            }
            let rec: Value = serde_json::from_str(line).expect("claim parses");
            if let Some(id) = rec.get("id").and_then(|v| v.as_str()) {
                by_id.insert(id.to_string(), rec);
            }
        }
        let doctext: Value = serde_json::from_str(
            &std::fs::read_to_string(&doctext_path).expect("doctext readable"),
        )
        .expect("doctext parses");
        (by_id, doctext)
    } else {
        (std::collections::HashMap::new(), Value::Null)
    };

    let candidate = ClosureCandidate {
        name: cand_name.to_string(),
        run: move |s: &str| -> Value {
            if mode_for_candidate == "condition" {
                return serde_json::to_value(parse_condition(s)).expect("serialises");
            }
            if mode_for_candidate == "claims" {
                let Some(rec) = n4_by_id.get(s) else {
                    return Value::Array(vec![]);
                };
                let doc_sha = rec
                    .get("doc_sha256")
                    .and_then(|v| v.as_str())
                    .unwrap_or_default();
                let doc_text = n4_doctext
                    .get(doc_sha)
                    .and_then(|v| v.as_str())
                    .unwrap_or_default();
                let field = rec
                    .get("field")
                    .and_then(|v| v.as_str())
                    .unwrap_or_default()
                    .to_string();
                let c = rec.get("claim").cloned().unwrap_or_default();
                let input = dw_truth::ClaimInput {
                    value: c.get("value").cloned(),
                    quote: c
                        .get("quote")
                        .and_then(|v| v.as_str())
                        .unwrap_or_default()
                        .to_string(),
                    table: c
                        .get("table")
                        .and_then(|v| v.as_str())
                        .unwrap_or_default()
                        .to_string(),
                    column_header: c
                        .get("column_header")
                        .and_then(|v| v.as_str())
                        .map(str::to_string),
                    row_header: c
                        .get("row_header")
                        .and_then(|v| v.as_str())
                        .map(str::to_string),
                    qualifier: c
                        .get("qualifier")
                        .and_then(|v| v.as_str())
                        .map(str::to_string),
                };
                let mut claims = std::collections::BTreeMap::new();
                claims.insert(field, input);
                return serde_json::to_value(verify_claims(&claims, doc_text)).expect("serialises");
            }
            // Same case file the reference read — the gate is only meaningful
            // if both engines saw identical evidence.
            let path = cases_root.join(s);
            let case: Value =
                serde_json::from_str(&std::fs::read_to_string(&path).expect("case file readable"))
                    .expect("case parses");
            let doc_text = case
                .get("doc_text")
                .and_then(|v| v.as_str())
                .unwrap_or_default();
            let mut claims = std::collections::BTreeMap::new();
            for row in case
                .get("claims")
                .and_then(|v| v.as_array())
                .cloned()
                .unwrap_or_default()
            {
                let field = row
                    .get("field")
                    .and_then(|v| v.as_str())
                    .unwrap_or_default()
                    .to_string();
                let c = row.get("claim").cloned().unwrap_or_default();
                let c = &c;
                let input = dw_truth::ClaimInput {
                    value: c.get("value").cloned(),
                    quote: c
                        .get("quote")
                        .and_then(|v| v.as_str())
                        .unwrap_or_default()
                        .to_string(),
                    table: c
                        .get("table")
                        .and_then(|v| v.as_str())
                        .unwrap_or_default()
                        .to_string(),
                    column_header: c
                        .get("column_header")
                        .and_then(|v| v.as_str())
                        .map(str::to_string),
                    row_header: c
                        .get("row_header")
                        .and_then(|v| v.as_str())
                        .map(str::to_string),
                    qualifier: c
                        .get("qualifier")
                        .and_then(|v| v.as_str())
                        .map(str::to_string),
                };
                claims.insert(field, input);
            }
            serde_json::to_value(verify_claims(&claims, doc_text)).expect("serialises")
        },
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
