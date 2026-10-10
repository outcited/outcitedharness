//! `dw-parity` — run the parity comparison and report the discrepancy ledger.
//!
//! ```text
//! dw-parity --corpus <manifest.json> --out <ledger.json>
//! dw-parity --check   # exit non-zero if the ledger blocks cutover
//! ```
//!
//! The Week 0 gate is `--check` passing on a frozen corpus: the ledger runs
//! empty against the Python reference. See `docs/DECISIONS.md` D2.

#![forbid(unsafe_code)]

use std::path::PathBuf;
use std::process::ExitCode;

use dw_parity::{CorpusManifest, Ledger, ParityError};

fn usage() -> &'static str {
    "usage:\n  dw-parity --corpus <manifest.json> [--out <ledger.json>] [--check]\n\n\
     Runs the parity comparison over a frozen corpus and writes the discrepancy\n\
     ledger. --check exits non-zero if the ledger blocks cutover (D2).\n"
}

struct Args {
    corpus: PathBuf,
    out: Option<PathBuf>,
    check: bool,
}

fn parse_args() -> Result<Args, String> {
    let mut corpus = None;
    let mut out = None;
    let mut check = false;

    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--corpus" => {
                corpus = Some(PathBuf::from(args.next().ok_or("--corpus needs a path")?));
            }
            "--out" => {
                out = Some(PathBuf::from(args.next().ok_or("--out needs a path")?));
            }
            "--check" => check = true,
            "-h" | "--help" => return Err(usage().to_string()),
            other => return Err(format!("unknown argument: {other}\n{}", usage())),
        }
    }

    Ok(Args {
        corpus: corpus.ok_or_else(|| format!("--corpus is required\n{}", usage()))?,
        out,
        check,
    })
}

fn run(args: &Args) -> Result<Ledger, ParityError> {
    let manifest = CorpusManifest::load(&args.corpus)?;
    eprintln!(
        "corpus: {} members, {} bytes, release {}",
        manifest.len(),
        manifest.total_bytes(),
        manifest.release
    );

    // No surfaces are registered yet: dw-types ships the law, and the first
    // surface (`quote_verify`) lands with dw-truth. An empty run therefore
    // produces an empty ledger, which is the Week 0 gate.
    let surfaces: Vec<&dyn dw_parity::Surface> = Vec::new();
    let ledger = dw_parity::run_surfaces(&surfaces)?;
    Ok(ledger)
}

fn main() -> ExitCode {
    let args = match parse_args() {
        Ok(a) => a,
        Err(message) => {
            eprintln!("{message}");
            return ExitCode::from(2);
        }
    };

    let ledger = match run(&args) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("dw-parity: {e}");
            return ExitCode::FAILURE;
        }
    };

    print!("{}", ledger.summary());

    if let Some(path) = &args.out {
        match serde_json::to_string_pretty(&ledger) {
            Ok(text) => {
                if let Err(e) = std::fs::write(path, text) {
                    eprintln!("dw-parity: cannot write {}: {e}", path.display());
                    return ExitCode::FAILURE;
                }
                eprintln!("ledger written to {}", path.display());
            }
            Err(e) => {
                eprintln!("dw-parity: cannot serialise ledger: {e}");
                return ExitCode::FAILURE;
            }
        }
    }

    if args.check && !ledger.cutover_permitted() {
        eprintln!("dw-parity: ledger blocks cutover (D2)");
        return ExitCode::FAILURE;
    }

    ExitCode::SUCCESS
}
