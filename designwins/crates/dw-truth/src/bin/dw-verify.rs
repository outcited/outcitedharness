//! `dw-verify` — the Rust engine behind the `verify_provider` seam
//! (RUST-CUTOVER-02). Reads one JSON request on stdin:
//!
//!   {"mode": "verdicts", "claims": [...], "text": "..."}  ->
//!       {"verdicts": [{"i", "verdict", "reason", "severity"}, ...]}
//!   `{"mode": "quick_ok", ...}` ->
//!       {"ok": bool}
//!
//! The loops mirror `burn_aisle.burn_doc`'s verdict loop and `_quick_ok`
//! step-for-step (same kernels as dw-soak's `verify_and_survive`; frozen-
//! replay parity 127/127, RUST-CUTOVER-01). Malformed input exits
//! non-zero — the seam fails closed and the doc takes the error
//! disposition; a claim never vanishes without a recorded verdict.

// CLI fail-fast: malformed input exits non-zero (the seam fails closed);
// index panics on malformed shapes are unreachable after the parse check.
#![allow(
    clippy::indexing_slicing,
    reason = "parsed-JSON CLI; malformed input exits non-zero before use"
)]

use std::io::Read;

use dw_truth::{column_position_binds, find_quote, value_in_quote, Field};
use serde_json::{json, Value};

fn alias_field(symbol: &str) -> Option<Field> {
    // verbatim port of `burn_aisle._alias_field`
    let s = symbol.to_lowercase();
    if s.contains("rds") {
        Field::parse("v_abs_max")
    } else if s.contains("vds") || s.contains("vin") {
        Field::parse("vin_max_v")
    } else if s.trim() == "id" || s.contains("iout") || s.contains("id_") {
        Field::parse("iout_max_a")
    } else if s.contains("tj") || s.contains("temp") {
        Field::parse("temp_max_c")
    } else {
        None
    }
}

fn value_f64(claim: &Value) -> Option<f64> {
    claim["value"]
        .as_f64()
        .or_else(|| claim["value"].as_str().and_then(|s| s.trim().parse().ok()))
}

fn verdicts(claims: &[Value], text: &str) -> Vec<Value> {
    let mut out = Vec::new();
    for (i, c) in claims.iter().enumerate() {
        if !c.is_object() || c["value"].is_null() {
            continue;
        }
        let quote = c["quote"].as_str().unwrap_or("");
        if quote.is_empty() {
            out.push(json!({
                "i": i, "verdict": "UNSUPPORTED", "reason": "unquoted",
                "severity": "P0",
            }));
            continue;
        }
        let field = alias_field(c["symbol"].as_str().unwrap_or(""));
        let mut ok = find_quote(quote, text, None, field) && value_in_quote(c.get("value"), quote);
        if ok
            && (!c["column_header"].is_null() || !c["row_header"].is_null())
            && column_position_binds(c["column_header"].as_str(), quote, text, value_f64(c))
                == Some(false)
        {
            ok = false;
        }
        out.push(json!({
            "i": i,
            "verdict": if ok { "SUPPORTED" } else { "UNSUPPORTED" },
            "reason": if ok {
                Value::Null
            } else if !c["column_header"].is_null() {
                Value::String("wrong_cell".into())
            } else {
                Value::String("quote_not_in_doc".into())
            },
            "severity": if ok { "P2" } else { "P0" },
        }));
    }
    out
}

fn quick_ok(claim: &Value, text: &str) -> bool {
    let Some(quote) = claim["quote"].as_str() else {
        return false;
    };
    if quote.is_empty() {
        return false;
    }
    let field = alias_field(claim["symbol"].as_str().unwrap_or(""));
    find_quote(quote, text, None, field)
}

fn main() {
    let mut raw = String::new();
    if std::io::stdin().read_to_string(&mut raw).is_err() {
        eprintln!("dw-verify: stdin unreadable");
        std::process::exit(2);
    }
    let req: Value = match serde_json::from_str(&raw) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("dw-verify: malformed request: {e}");
            std::process::exit(2);
        }
    };
    let text = req["text"].as_str().unwrap_or("");
    let claims = req["claims"].as_array().cloned().unwrap_or_default();
    let response = match req["mode"].as_str() {
        Some("verdicts") => json!({"verdicts": verdicts(&claims, text)}),
        Some("quick_ok") => json!({
            "ok": claims.first().is_some_and(|c| quick_ok(c, text)),
        }),
        other => {
            eprintln!("dw-verify: unknown mode {other:?}");
            std::process::exit(2);
        }
    };
    print!("{response}");
}
