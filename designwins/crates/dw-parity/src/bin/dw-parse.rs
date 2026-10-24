//! `dw-parse` — the Rust engine behind the normalize seam
//! (`harness.electronics.normalize_provider`, RUST-CUTOVER-02).
//!
//! Lives in dw-parity (the engine-bridge crate): dw-normalize is
//! deliberately minimal (no `serde_json`), and the `ParseOutcome` wire shape
//! compared by this workspace's replacement gate (10,270 conditions, 0
//! diffs) is serialized here exactly as dw-parity's condition mode does.
//!
//! stdin: `{"condition": "<text>"}` → stdout: the `parse_condition` output
//! in the reference's JSON shape (`{"keys": …}` or `{"reject": …}`).
//! Malformed input exits non-zero; the seam fails closed.

// CLI fail-fast: malformed input exits non-zero before the index; the
// ParseOutcome serialization expect mirrors dw-parity's own bin.
#![allow(
    clippy::indexing_slicing,
    clippy::expect_used,
    reason = "parsed-JSON CLI; malformed input exits non-zero before use"
)]

use std::io::Read;

use dw_normalize::parse_condition;

fn main() {
    let mut raw = String::new();
    if std::io::stdin().read_to_string(&mut raw).is_err() {
        eprintln!("dw-parse: stdin unreadable");
        std::process::exit(2);
    }
    let req: serde_json::Value = match serde_json::from_str(&raw) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("dw-parse: malformed request: {e}");
            std::process::exit(2);
        }
    };
    let condition = req["condition"].as_str().unwrap_or("");
    let outcome =
        serde_json::to_value(parse_condition(condition)).expect("ParseOutcome serialises");
    print!("{outcome}");
}
