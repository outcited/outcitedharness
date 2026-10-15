//! Phase 0 shadow controller — observes Python's intake, records what the
//! Rust factory WOULD do, never claims work.
//!
//! Usage: `dw-shadow --roots <dirs...> [--stability <secs>] [--db <path>]`

#![forbid(unsafe_code)]
#![deny(missing_docs)]
#![allow(
    clippy::indexing_slicing,
    reason = "arg parsing indexes a validated Vec"
)]

use std::path::PathBuf;

use dw_controller::{FactoryError, FactoryState};

fn main() {
    let mut roots: Vec<PathBuf> = Vec::new();
    let mut stability_seconds: i64 = 120;
    let mut db_path = PathBuf::from("results/shadow-factory.sqlite3");
    let mut interval_secs: u64 = 300;

    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--roots" => {
                i += 1;
                while i < args.len() && !args[i].starts_with("--") {
                    roots.push(PathBuf::from(&args[i]));
                    i += 1;
                }
            }
            "--stability" if i + 1 < args.len() => {
                i += 1;
                stability_seconds = args[i].parse().unwrap_or(120);
                i += 1;
            }
            "--db" if i + 1 < args.len() => {
                i += 1;
                db_path = PathBuf::from(&args[i]);
                i += 1;
            }
            "--interval" if i + 1 < args.len() => {
                i += 1;
                interval_secs = args[i].parse().unwrap_or(300);
                i += 1;
            }
            _ => i += 1,
        }
    }

    if roots.is_empty() {
        eprintln!("dw-shadow: Phase 0 shadow controller (read-only observation)");
        eprintln!();
        eprintln!("usage: dw-shadow --roots <dirs...> [options]");
        eprintln!();
        eprintln!("  --roots       PDF directories to observe");
        eprintln!("  --stability   stability window seconds (default 120)");
        eprintln!("  --db          shadow db path (default results/shadow-factory.sqlite3)");
        eprintln!("  --interval    seconds between rounds (default 300)");
        std::process::exit(1);
    }

    eprintln!("dw-shadow: observation mode — no production claims");
    eprintln!("dw-shadow: db={}", db_path.display());
    eprintln!("dw-shadow: roots={roots:?} stability={stability_seconds}s");

    if let Err(error) = observe(&roots, stability_seconds, &db_path, interval_secs) {
        eprintln!("dw-shadow: FATAL: {error}");
        std::process::exit(1);
    }
}

/// Run observation rounds until interrupted.
fn observe(
    roots: &[PathBuf],
    stability_seconds: i64,
    db_path: &std::path::Path,
    interval_secs: u64,
) -> Result<(), FactoryError> {
    let state = FactoryState::open(db_path)?;
    let mut round: u64 = 0;
    loop {
        round += 1;
        let now = now_iso();
        eprintln!("dw-shadow: round {round} at {now}");
        match state.discover_pdfs(roots, stability_seconds, Some(&now)) {
            Ok(report) => {
                eprintln!(
                    "dw-shadow: r{round} discovered={} new={} ready={} dup={} quar={} unchanged={} unsafe={}",
                    report.discovered_paths,
                    report.new_observations,
                    report.ready,
                    report.duplicates,
                    report.quarantined,
                    report.unchanged,
                    report.unsafe_paths
                );
            }
            Err(error) => eprintln!("dw-shadow: r{round} ERROR: {error}"),
        }
        std::thread::sleep(std::time::Duration::from_secs(interval_secs));
    }
}

/// Current time as ISO-8601 UTC (the crate's timestamp format).
fn now_iso() -> String {
    let micros = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_micros()).unwrap_or(i64::MAX));
    dw_controller::ts::format(micros)
}
