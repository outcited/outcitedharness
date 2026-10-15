//! dw-factory: CLI interface to dw-controller for production orchestration.
//!
//! Subprocess-based integration: Python (or any language) calls these
//! commands to operate the factory. Rust owns ALL controller state;
//! the caller only performs model calls.
//!
//! Usage:
//! ```text
//! dw-factory discover --roots <dirs...> --db <path> [--stability <secs>]
//! dw-factory register --queue <file> --db <path> --out <dir> [--chunk-size N]
//! dw-factory lease --db <path> --node <name> [--lease-secs N]
//! dw-factory complete --db <path> --chunk <id> --token <t> --manifest-sha <sha>
//! dw-factory fail --db <path> --chunk <id> --token <t> --error <msg>
//! dw-factory recover --db <path>
//! dw-factory status --db <path>
//! dw-factory events --db <path>
//! ```

#![forbid(unsafe_code)]
#![deny(missing_docs)]
#![allow(
    clippy::too_many_lines,
    reason = "CLI dispatch is inherently linear"
)]

use std::path::PathBuf;

use dw_controller::{ChunkLease, FactoryState};
use serde_json::json;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 {
        usage();
        std::process::exit(1);
    }

    let cmd = args[1].as_str();
    let mut i = 2;
    let mut db = PathBuf::from("factory.sqlite3");
    let mut roots: Vec<PathBuf> = Vec::new();
    let mut stability: i64 = 120;
    let mut queue_file = PathBuf::new();
    let mut out_dir = PathBuf::new();
    let mut chunk_size: i64 = 10;
    let mut max_attempts: i64 = 3;
    let mut node = String::new();
    let mut lease_secs: i64 = 1800;
    let mut chunk_id = String::new();
    let mut token = String::new();
    let mut manifest_sha = String::new();
    let mut error = String::new();
    let mut retry_delay: i64 = 30;
    let mut terminal = false;
    let mut now: Option<String> = None;

    while i < args.len() {
        match args[i].as_str() {
            "--db" if i + 1 < args.len() => { i += 1; db = PathBuf::from(&args[i]); }
            "--roots" => {
                i += 1;
                while i < args.len() && !args[i].starts_with("--") {
                    roots.push(PathBuf::from(&args[i]));
                    i += 1;
                }
                continue;
            }
            "--stability" if i + 1 < args.len() => { i += 1; stability = args[i].parse().unwrap_or(120); }
            "--queue" if i + 1 < args.len() => { i += 1; queue_file = PathBuf::from(&args[i]); }
            "--out" if i + 1 < args.len() => { i += 1; out_dir = PathBuf::from(&args[i]); }
            "--chunk-size" if i + 1 < args.len() => { i += 1; chunk_size = args[i].parse().unwrap_or(10); }
            "--max-attempts" if i + 1 < args.len() => { i += 1; max_attempts = args[i].parse().unwrap_or(3); }
            "--node" if i + 1 < args.len() => { i += 1; node = args[i].clone(); }
            "--lease-secs" if i + 1 < args.len() => { i += 1; lease_secs = args[i].parse().unwrap_or(1800); }
            "--chunk" if i + 1 < args.len() => { i += 1; chunk_id = args[i].clone(); }
            "--token" if i + 1 < args.len() => { i += 1; token = args[i].clone(); }
            "--manifest-sha" if i + 1 < args.len() => { i += 1; manifest_sha = args[i].clone(); }
            "--error" if i + 1 < args.len() => { i += 1; error = args[i].clone(); }
            "--retry-delay" if i + 1 < args.len() => { i += 1; retry_delay = args[i].parse().unwrap_or(30); }
            "--terminal" => { terminal = true; }
            "--now" if i + 1 < args.len() => { i += 1; now = Some(args[i].clone()); }
            _ => {}
        }
        i += 1;
    }

    let state = match FactoryState::open(&db) {
        Ok(s) => s,
        Err(e) => { eprintln!("dw-factory: cannot open {}: {e}", db.display()); std::process::exit(1); }
    };

    let result = match cmd {
        "discover" => discover(&state, &roots, stability, now.as_deref()),
        "register" => register(&state, &queue_file, &out_dir, chunk_size, max_attempts, now.as_deref()),
        "lease" => lease(&state, &node, lease_secs, now.as_deref()),
        "complete" => complete(&state, &chunk_id, &token, &manifest_sha, now.as_deref()),
        "fail" => fail(&state, &chunk_id, &token, &error, terminal, retry_delay, now.as_deref()),
        "recover" => recover(&state, now.as_deref()),
        "status" => status(&state),
        "events" => events(&state),
        _ => { usage(); std::process::exit(1); }
    };

    match result {
        Ok(output) => println!("{output}"),
        Err(e) => { eprintln!("dw-factory: {e}"); std::process::exit(1); }
    }
}

type CmdResult = Result<String, String>;

fn discover(state: &FactoryState, roots: &[PathBuf], stability: i64, now: Option<&str>) -> CmdResult {
    if roots.is_empty() {
        return Err("--roots required".into());
    }
    let report = state.discover_pdfs(roots, stability, now).map_err(|e| e.to_string())?;
    Ok(serde_json::to_string_pretty(&report).unwrap_or_default())
}

fn register(state: &FactoryState, queue: &PathBuf, out: &PathBuf, chunk_size: i64, max_attempts: i64, now: Option<&str>) -> CmdResult {
    if queue.as_os_str().is_empty() || out.as_os_str().is_empty() {
        return Err("--queue and --out required".into());
    }
    let ids = state.register_queue(queue, out, chunk_size, max_attempts, 0, now).map_err(|e| e.to_string())?;
    Ok(serde_json::to_string(&ids).unwrap_or_default())
}

fn lease(state: &FactoryState, node: &str, lease_secs: i64, now: Option<&str>) -> CmdResult {
    if node.is_empty() {
        return Err("--node required".into());
    }
    match state.claim_chunk(node, lease_secs, now) {
        Ok(Some(lease)) => Ok(lease_json(&lease).to_string()),
        Ok(None) => Ok("null".into()),
        Err(e) => Err(e.to_string()),
    }
}

fn complete(state: &FactoryState, _chunk: &str, _token: &str, _manifest_sha: &str, _now: Option<&str>) -> CmdResult {
    // For the canary, we construct a minimal ChunkLease from the CLI args
    // and call complete_chunk. In production, the caller would get the
    // lease from the `lease` command output.
    // The complete_chunk method verifies the output directory's manifest.
    // For simplicity in the canary, we use a direct approach.
    Err("complete: use the Rust library directly for now — the CLI interface needs the full lease object".into())
}

fn fail(state: &FactoryState, _chunk: &str, _token: &str, _error: &str, _terminal: bool, _retry: i64, _now: Option<&str>) -> CmdResult {
    Err("fail: use the Rust library directly for now".into())
}

fn recover(state: &FactoryState, now: Option<&str>) -> CmdResult {
    let recovered = state.recover_expired(now).map_err(|e| e.to_string())?;
    Ok(serde_json::to_string(&recovered).unwrap_or_default())
}

fn status(state: &FactoryState) -> CmdResult {
    let s = state.status().map_err(|e| e.to_string())?;
    Ok(serde_json::to_string_pretty(&s).unwrap_or_default())
}

fn events(state: &FactoryState) -> CmdResult {
    let conn = state.connect().map_err(|e| e.to_string())?;
    let mut stmt = conn.prepare("SELECT event_id, event_type, subject_id, payload_json, created_at FROM factory_events ORDER BY created_at, rowid").map_err(|e| e.to_string())?;
    let rows = stmt.query_map([], |row| {
        Ok(json!({
            "event_id": row.get::<_, String>(0)?,
            "event_type": row.get::<_, String>(1)?,
            "subject_id": row.get::<_, String>(2)?,
            "payload_json": row.get::<_, String>(3)?,
            "created_at": row.get::<_, String>(4)?,
        }))
    }).map_err(|e| e.to_string())?;
    let mut out = Vec::new();
    for row in rows.flatten() {
        out.push(row);
    }
    Ok(serde_json::to_string_pretty(&out).unwrap_or_default())
}

fn lease_json(lease: &ChunkLease) -> serde_json::Value {
    json!({
        "chunk_id": lease.chunk_id,
        "node": lease.node,
        "attempt": lease.attempt,
        "lease_token": lease.lease_token,
        "lease_expires_at": lease.lease_expires_at,
        "queue_path": lease.queue_path,
        "queue_sha256": lease.queue_sha256,
        "offset": lease.offset,
        "item_count": lease.item_count,
        "output_directory": lease.output_directory,
    })
}

fn usage() {
    eprintln!("dw-factory: CLI interface to dw-controller");
    eprintln!();
    eprintln!("commands:");
    eprintln!("  discover  --roots <dirs...> --db <path> [--stability <secs>] [--now <iso>]");
    eprintln!("  register  --queue <file> --out <dir> --db <path> [--chunk-size N]");
    eprintln!("  lease     --db <path> --node <name> [--lease-secs N]");
    eprintln!("  recover   --db <path>");
    eprintln!("  status    --db <path>");
    eprintln!("  events    --db <path>");
}
