//! `dw-bcmp-run` — the BCMP pilot batch runner.
//!
//! ```text
//! dw-bcmp-run --job-dir DIR --feed pillars.m5.jsonl --vault /Volumes/M5_4TB \
//!             [--max-scenarios N] [--emit-knife-spec FILE]
//! ```
//!
//! Durable layout under `--job-dir`:
//!
//! ```text
//! scenarios/*.jsonl   append-only validated scenario feed
//! receipts/<sha>.json one provenance-pinned receipt per unique atom set
//! state.json          checkpoint (feed pin, counters, done ledger)
//! parked.log.jsonl    parked scenarios/atoms, with codes
//! logs/last-run.json  invocation summary
//! STOP                touch to stop gracefully at the next scenario
//! ```

use std::path::PathBuf;
use std::process::ExitCode;

use dw_bcmp::receipt::{run_batch, RunOpts};

fn main() -> ExitCode {
    let mut job_dir = PathBuf::new();
    let mut feed = PathBuf::new();
    let mut vault = PathBuf::new();
    let mut repo_root = PathBuf::new();
    let mut max_scenarios = 0usize;
    let mut emit_spec: Option<PathBuf> = None;

    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        let mut value = || {
            args.next()
                .unwrap_or_else(|| usage_and_exit(&format!("missing value for {arg}")))
        };
        match arg.as_str() {
            "--job-dir" => job_dir = PathBuf::from(value()),
            "--feed" => feed = PathBuf::from(value()),
            "--vault" => vault = PathBuf::from(value()),
            "--repo-root" => repo_root = PathBuf::from(value()),
            "--max-scenarios" => {
                max_scenarios = value().parse().unwrap_or_else(|_| {
                    usage_and_exit("--max-scenarios must be a number");
                });
            }
            "--emit-knife-spec" => emit_spec = Some(PathBuf::from(value())),
            "--help" | "-h" => {
                print_usage();
                return ExitCode::SUCCESS;
            }
            other => usage_and_exit(&format!("unknown flag {other}")),
        }
    }

    if let Some(path) = emit_spec {
        let spec = dw_bcmp::knife_spec_json();
        match std::fs::write(&path, serde_json::to_vec_pretty(&spec).unwrap_or_default()) {
            Ok(()) => {
                println!("knife spec written to {}", path.display());
                if job_dir.as_os_str().is_empty() {
                    return ExitCode::SUCCESS;
                }
            }
            Err(e) => {
                eprintln!("write spec: {e}");
                return ExitCode::FAILURE;
            }
        }
    }

    if job_dir.as_os_str().is_empty() || feed.as_os_str().is_empty() || vault.as_os_str().is_empty()
    {
        usage_and_exit("--job-dir, --feed and --vault are required");
    }
    if repo_root.as_os_str().is_empty() {
        repo_root = std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."));
    }

    let opts = RunOpts {
        job_dir,
        feed,
        vault_root: vault,
        repo_root,
        max_scenarios,
        evidence_cap: 4,
    };
    match run_batch(&opts) {
        Ok(outcome) => {
            println!(
                "{}",
                serde_json::to_string_pretty(&outcome).unwrap_or_default()
            );
            if outcome.error.is_some() {
                ExitCode::FAILURE
            } else {
                ExitCode::SUCCESS
            }
        }
        Err(e) => {
            eprintln!("bcmp run failed: {e}");
            ExitCode::FAILURE
        }
    }
}

fn print_usage() {
    println!(
        "dw-bcmp-run --job-dir DIR --feed pillars.m5.jsonl --vault VAULT_ROOT \
         [--repo-root DIR] [--max-scenarios N] [--emit-knife-spec FILE]"
    );
}

fn usage_and_exit(why: &str) -> ! {
    eprintln!("error: {why}");
    print_usage();
    std::process::exit(2);
}
