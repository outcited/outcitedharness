//! `extract-replace` — the live replacement gate for the extraction surface.
//!
//! The gold gates compare Rust against fixtures generated from the reference
//! earlier; this gate invokes **both engines live, on the same subjects, in
//! the same run** (the `dw-replace` pattern, `docs/HANDOFF.md` §2). An empty
//! ledger is the replacement proof: the Rust candidate produced exactly what
//! the Python produced today.
//!
//! ```text
//! subject ──┬─> Python reference (tools/reference_shim.py, subprocess) ──┐
//!           │                                                            ├─> diff ─> ledger
//!           └─> Rust candidate  (in-process)  ───────────────────────────┘
//! ```
//!
//! Both engine identities are recorded (C2: "the two that agreed").
//!
//! ```text
//! cargo run -q -p dw-extract --bin extract-replace -- --mode grid
//! ```
//!
//! Exit codes: `0` ledger clean · `1` discrepancies · `2` bad invocation ·
//! `3` an engine failed (infrastructure fault, never a parity result).

#![forbid(unsafe_code)]

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use dw_extract::bom::{bom_from_pages, PageInput};
use dw_extract::parity::{diff_wire, ParitySummary};
use dw_extract::queue::{
    build_extraction_work_queue, ground_truth_claims, pinout_row_claims, GroundTruthRecord,
};
use dw_extract::{canonical_json, stable_id};
use serde_json::{json, Value};

const CREATED_AT: &str = "2026-09-01T00:00:00Z";

/// Lowercase hex digits for the canonical-wire echo.
const HEX: [char; 16] = [
    '0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'a', 'b', 'c', 'd', 'e', 'f',
];

fn usage() -> &'static str {
    "usage: extract-replace --mode <grid|bom|queue|canonical|claims> [--corpus <jsonl>] [--limit N] [--out <ledger.json>]\n\n\
     Runs the Python reference (tools/reference_shim.py) and the Rust candidate\n\
     on the same subjects and reports the discrepancy ledger. Empty ledger =\n\
     the Rust port replaces the Python for that surface.\n"
}

/// # Lints
///
/// A `main` that parses flags, runs a gate, and maps the result to an exit
/// code is one linear sequence — splitting it would scatter the exit-code
/// contract, which is the part a caller depends on (the `dw-replace`
/// precedent). `run_candidate` mirrors the reference's one-branch-per-mode
/// dispatch. Allowed here and nowhere else in the crate.
#[allow(clippy::too_many_lines)]
fn main() -> std::process::ExitCode {
    let mut mode = String::new();
    let mut corpus: Option<PathBuf> = None;
    let mut limit: Option<usize> = None;
    let mut out: Option<PathBuf> = None;
    let mut fault = false;

    let mut args = std::env::args().skip(1);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--mode" => mode = args.next().unwrap_or_default(),
            "--corpus" => corpus = args.next().map(PathBuf::from),
            "--limit" => limit = args.next().and_then(|v| v.parse().ok()),
            "--out" => out = args.next().map(PathBuf::from),
            // Negative control (ASSURANCE §3): perturb one candidate output
            // before diffing. A run with --fault MUST exit 1; if it exits 0
            // the gate is decoration.
            "--fault" => fault = true,
            "-h" | "--help" => {
                eprintln!("{}", usage());
                return std::process::ExitCode::from(2);
            }
            other => {
                eprintln!("unknown argument: {other}\n{}", usage());
                return std::process::ExitCode::from(2);
            }
        }
    }
    if !matches!(
        mode.as_str(),
        "grid" | "bom" | "queue" | "canonical" | "claims"
    ) {
        eprintln!("{}", usage());
        return std::process::ExitCode::from(2);
    }

    let corpus = corpus.unwrap_or_else(|| default_corpus(&mode));
    let cases = match load_cases(&corpus) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("{}: {e}", corpus.display());
            return std::process::ExitCode::from(3);
        }
    };
    let cases: Vec<Value> = cases
        .into_iter()
        .take(limit.unwrap_or(usize::MAX))
        .collect();
    if cases.is_empty() {
        eprintln!("{}: no subjects", corpus.display());
        return std::process::ExitCode::from(3);
    }

    let subjects: Vec<String> = cases
        .iter()
        .map(|c| subject_line(&mode, c))
        .collect::<Result<Vec<_>, _>>()
        .unwrap_or_else(|e: String| {
            eprintln!("subject build failed: {e}");
            std::process::exit(3);
        });

    let reference = match run_reference(&mode, &subjects) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("reference failed: {e}");
            return std::process::ExitCode::from(3);
        }
    };
    if reference.len() != subjects.len() {
        eprintln!(
            "reference returned {} lines for {} subjects",
            reference.len(),
            subjects.len()
        );
        return std::process::ExitCode::from(3);
    }

    let mut summary = ParitySummary {
        reference: reference_id(&mode),
        candidate: candidate_id(&mode),
        subjects: subjects.len(),
        agreements: 0,
        discrepancies: 0,
    };
    let mut ledger: Vec<Value> = Vec::new();
    for (i, (case, (subject, reference_out))) in cases
        .iter()
        .zip(subjects.iter().zip(reference.iter()))
        .enumerate()
    {
        let candidate = match run_candidate(&mode, subject) {
            Ok(c) => c,
            Err(e) => {
                eprintln!("candidate failed on subject {i}: {e}");
                return std::process::ExitCode::from(3);
            }
        };
        let candidate = if fault && i == 0 {
            fault_inject(&candidate)
        } else {
            candidate
        };
        let diffs = diff_wire(reference_out, &candidate);
        if diffs.is_empty() {
            summary.agreements += 1;
        } else {
            summary.discrepancies += diffs.len();
            ledger.push(json!({
                "subject": i,
                "case": case.get("case"),
                "diffs": diffs.iter().map(|d| json!({
                    "path": d.path,
                    "reference": d.reference,
                    "candidate": d.candidate,
                })).collect::<Vec<_>>(),
            }));
        }
    }

    let verdict = if summary.cutover_permitted() {
        "yes"
    } else {
        "no"
    };
    println!(
        "{}\n  vs {}\n  subjects {}  agreements {}  discrepancies {}  cutover_permitted {verdict}",
        summary.reference,
        summary.candidate,
        summary.subjects,
        summary.agreements,
        summary.discrepancies,
    );
    if let Some(path) = out {
        let ledger_doc = json!({
            "reference": summary.reference,
            "candidate": summary.candidate,
            "subjects": summary.subjects,
            "agreements": summary.agreements,
            "discrepancies": summary.discrepancies,
            "cutover_permitted": summary.cutover_permitted(),
            "entries": ledger,
        });
        if let Err(e) = std::fs::write(
            &path,
            serde_json::to_vec_pretty(&ledger_doc).unwrap_or_default(),
        ) {
            eprintln!("{}: {e}", path.display());
            return std::process::ExitCode::from(3);
        }
    }
    for entry in &ledger {
        eprintln!("{entry}");
    }
    if summary.cutover_permitted() {
        std::process::ExitCode::SUCCESS
    } else {
        std::process::ExitCode::from(1)
    }
}

/// Change the first leaf found in depth-first order.
fn fault_walk(v: &mut Value) -> bool {
    match v {
        Value::Number(n) => {
            *n = serde_json::Number::from(n.as_i64().unwrap_or(0) + 1);
            true
        }
        Value::String(s) => {
            s.push_str("-fault");
            true
        }
        Value::Array(a) => a.iter_mut().any(fault_walk),
        Value::Object(o) => o.values_mut().any(fault_walk),
        _ => false,
    }
}

/// The documented fault: change one leaf of the candidate output.
fn fault_inject(candidate: &Value) -> Value {
    let mut mutated = candidate.clone();
    fault_walk(&mut mutated);
    mutated
}

fn default_corpus(mode: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(format!(
        "../../../tests/fixtures/gold/extract_{mode}_gold_v1.jsonl"
    ))
}

fn load_cases(path: &Path) -> Result<Vec<Value>, String> {
    let raw = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    raw.lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str(l).map_err(|e| e.to_string()))
        .collect()
}

/// The input half of each fixture line, as the subject both engines see.
fn subject_line(mode: &str, case: &Value) -> Result<String, String> {
    let subject = match mode {
        "grid" => json!({"record": case.get("record")}),
        "bom" => json!({"pages": case.get("pages")}),
        "queue" => json!({"registry": case.get("registry"), "aligned": case.get("aligned")}),
        "canonical" => json!({"payload": case.get("payload")}),
        "claims" => {
            if case.get("kind") == Some(&json!("ground_truth")) {
                json!({
                    "kind": "ground_truth",
                    "document": case.get("document"),
                    "summary": case.get("summary"),
                    "value": case.get("value"),
                    "stem": case.get("stem"),
                    "source_uri": case.get("source_uri"),
                })
            } else {
                json!({
                    "kind": "pinout",
                    "example": case.get("example"),
                    "pdf_uris": case.get("pdf_uris"),
                })
            }
        }
        other => return Err(format!("unknown mode {other}")),
    };
    serde_json::to_string(&subject).map_err(|e| e.to_string())
}

fn reference_id(mode: &str) -> String {
    format!("python:harness.electronics.{mode}")
}

fn candidate_id(mode: &str) -> String {
    match mode {
        "grid" => "rust:dw_extract::grid::build_grid".to_string(),
        "bom" => "rust:dw_extract::bom::bom_from_pages".to_string(),
        "queue" => "rust:dw_extract::queue::build_extraction_work_queue".to_string(),
        "canonical" => "rust:dw_extract::canonical_json+stable_id".to_string(),
        _ => "rust:dw_extract::queue::claim assembly".to_string(),
    }
}

/// The reference over stdin/stdout (the subprocess keeps the two engines in
/// separate address spaces; neither shares state with the other).
fn run_reference(mode: &str, subjects: &[String]) -> Result<Vec<Value>, String> {
    let shim = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tools/reference_shim.py")
        .canonicalize()
        .map_err(|e| e.to_string())?;
    let repo = shim
        .ancestors()
        .nth(4)
        .ok_or_else(|| String::from("repo root"))?
        .to_path_buf();
    let mut child = Command::new("python3")
        .arg(&shim)
        .arg(mode)
        .current_dir(&repo)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("spawn python3: {e}"))?;
    let mut stdin = child.stdin.take().ok_or_else(|| String::from("no stdin"))?;
    // stdin must be written on its own thread: writing inline deadlocks once
    // the batch exceeds the pipe buffer (the dw-parity lesson, 10,270
    // subjects hung). Small batches today; the shape is load-bearing.
    let batch: Vec<String> = subjects.to_vec();
    let writer = std::thread::spawn(move || -> std::io::Result<()> {
        for s in &batch {
            writeln!(stdin, "{s}")?;
        }
        drop(stdin);
        Ok(())
    });
    let output = child.wait_with_output().map_err(|e| e.to_string())?;
    writer
        .join()
        .map_err(|_| String::from("stdin writer panicked"))?
        .map_err(|e| format!("stdin write: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "reference exit {:?}: {}",
            output.status.code(),
            String::from_utf8_lossy(&output.stderr)
        ));
    }
    let stdout = String::from_utf8_lossy(&output.stdout);
    stdout
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str(l).map_err(|e| e.to_string()))
        .collect()
}

#[allow(clippy::too_many_lines)]
fn run_candidate(mode: &str, subject: &str) -> Result<Value, String> {
    let s: Value = serde_json::from_str(subject).map_err(|e| e.to_string())?;
    match mode {
        "grid" => {
            let record = s.get("record").cloned().unwrap_or(Value::Null);
            dw_extract::grid::build_grid(&record, None).map_err(|e| e.to_string())
        }
        "bom" => {
            let empty = Vec::new();
            let pages: Vec<PageInput> = s
                .get("pages")
                .and_then(Value::as_array)
                .unwrap_or(&empty)
                .iter()
                .map(|p| PageInput {
                    text: p
                        .get("text")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string(),
                    tables: p
                        .get("tables")
                        .and_then(Value::as_array)
                        .map(|ts| {
                            ts.iter()
                                .map(|t| {
                                    t.as_array()
                                        .map(|rows| {
                                            rows.iter()
                                                .map(|row| {
                                                    row.as_array()
                                                        .map(|cells| {
                                                            cells
                                                                .iter()
                                                                .map(|c| {
                                                                    c.as_str()
                                                                        .unwrap_or_default()
                                                                        .to_string()
                                                                })
                                                                .collect()
                                                        })
                                                        .unwrap_or_default()
                                                })
                                                .collect()
                                        })
                                        .unwrap_or_default()
                                })
                                .collect()
                        })
                        .unwrap_or_default(),
                })
                .collect();
            Ok(bom_from_pages(&pages))
        }
        "queue" => {
            let registry = s.get("registry").cloned().unwrap_or(Value::Null);
            let aligned: Vec<String> = s
                .get("aligned")
                .and_then(Value::as_array)
                .map(|a| {
                    a.iter()
                        .filter_map(Value::as_str)
                        .map(ToString::to_string)
                        .collect()
                })
                .unwrap_or_default();
            build_extraction_work_queue(&registry, &aligned).map_err(|e| e.to_string())
        }
        "canonical" => {
            let payload = s.get("payload").cloned().unwrap_or(Value::Null);
            let bytes = canonical_json(&payload).map_err(|e| e.to_string())?;
            let mut hex = String::with_capacity(bytes.len() * 2);
            for b in bytes {
                if let Some(d) = HEX.get((b >> 4 & 0xf) as usize) {
                    hex.push(*d);
                }
                if let Some(d) = HEX.get((b & 0xf) as usize) {
                    hex.push(*d);
                }
            }
            let id = stable_id("claim", &payload).map_err(|e| e.to_string())?;
            Ok(json!({"canonical_hex": hex, "stable_claim_id": id}))
        }
        "claims" => {
            if s.get("kind") == Some(&json!("ground_truth")) {
                let record = GroundTruthRecord {
                    document: s.get("document").unwrap_or(&Value::Null),
                    summary: s.get("summary").unwrap_or(&Value::Null),
                    value: s.get("value").unwrap_or(&Value::Null),
                    stem: s
                        .get("stem")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_string(),
                };
                let source_uri = s
                    .get("source_uri")
                    .and_then(Value::as_str)
                    .unwrap_or_default();
                let claims = ground_truth_claims(&record, source_uri, CREATED_AT)
                    .map_err(|e| e.to_string())?;
                Ok(Value::Array(
                    claims.iter().map(dw_extract::FactClaim::to_wire).collect(),
                ))
            } else {
                let example = s.get("example").cloned().unwrap_or(Value::Null);
                let mut uris = std::collections::BTreeMap::new();
                if let Some(map) = s.get("pdf_uris").and_then(Value::as_object) {
                    for (k, v) in map {
                        if let Some(uri) = v.as_str() {
                            uris.insert(k.clone(), uri.to_string());
                        }
                    }
                }
                let claims =
                    pinout_row_claims(&example, &uris, CREATED_AT).map_err(|e| e.to_string())?;
                Ok(Value::Array(
                    claims.iter().map(dw_extract::FactClaim::to_wire).collect(),
                ))
            }
        }
        other => Err(format!("unknown mode {other}")),
    }
}
