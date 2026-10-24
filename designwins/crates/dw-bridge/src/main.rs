//! Normalization bridge canary — feeds extraction output through dw-normalize + dw-truth
//! Reads JSONL extraction output, runs verification + normalization, reports yield.

use std::collections::BTreeMap;
use std::io::{self, BufRead};

use dw_normalize::condition::parse_condition;
use dw_normalize::units::{scale, Kind, Scale};
use dw_truth::quote::{verify_claims, ClaimInput};

fn classify_unit_kind(metric: &str) -> Kind {
    let m = metric.to_lowercase();
    if m.contains("rtheta") || m.contains("psi") || m.contains("zth") {
        Kind::CelsiusPerWatt
    } else if m.contains("temp") || m.contains("tj") || m.contains("ta") {
        Kind::Celsius
    } else if m.contains("vds") || m.contains("vgs") || m.contains("vin") || m.contains("vout") {
        Kind::Volt
    } else if m.contains("i") && (m.contains("d") || m.contains("out") || m.contains("in")) {
        Kind::Ampere
    } else if m.contains("freq") || m.contains("f_") {
        Kind::Hertz
    } else {
        Kind::Celsius // fallback, will fail closed
    }
}

fn main() {
    let stdin = io::stdin();
    let mut total = 0u64;
    let mut verified = 0u64;
    let mut normalized_condition = 0u64;
    let mut normalized_unit = 0u64;
    let mut held = 0u64;
    let mut rejected = 0u64;
    let mut no_quote = 0u64;

    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => continue,
        };
        if line.trim().is_empty() {
            continue;
        }

        let record: serde_json::Value = match serde_json::from_str(&line) {
            Ok(v) => v,
            Err(_) => {
                rejected += 1;
                continue;
            }
        };

        total += 1;

        // Extract fields from Python extraction output
        let _pdf_sha = record["pdf_sha"]
            .as_str()
            .or(record["document_sha256"].as_str())
            .unwrap_or("");
        let verbatim = record["verbatim"]
            .as_str()
            .or(record["context_verbatim"].as_str())
            .unwrap_or("");
        let value = record["value"].clone();
        let unit = record["unit"].as_str().unwrap_or("");
        let condition = record["condition_verbatim"].as_str().unwrap_or("");
        let metric = record["metric"]
            .as_str()
            .or(record["grade"].as_str())
            .or(record["symbol"].as_str())
            .unwrap_or("");
        let table_title = record["table_title"].as_str().unwrap_or("");

        // Step 1: Verify quote (dw-truth)
        if verbatim.is_empty() {
            no_quote += 1;
            held += 1;
            continue;
        }

        let mut claims = BTreeMap::new();
        claims.insert(
            format!("claim-{}", total),
            ClaimInput {
                value: if value.is_null() { None } else { Some(value.clone()) },
                quote: verbatim.to_string(),
                table: table_title.to_string(),
                column_header: None,
                row_header: None,
                qualifier: record["qualifier"].as_str().map(String::from),
            },
        );

        let verdicts = verify_claims(&claims, verbatim);
        let is_verified = verdicts.iter().any(|v| {
            matches!(
                v.verdict,
                dw_truth::VerdictLabel::Supported | dw_truth::VerdictLabel::Review
            )
        });

        if is_verified {
            verified += 1;
        } else {
            held += 1;
        }

        // Step 2: Normalize condition (dw-normalize)
        if !condition.is_empty() {
            match parse_condition(condition) {
                dw_normalize::condition::ParseOutcome::Typed(_) => {
                    normalized_condition += 1;
                }
                dw_normalize::condition::ParseOutcome::Rejected { .. } => {}
            }
        }

        // Step 3: Normalize unit (dw-normalize)
        if !unit.is_empty() {
            let kind = classify_unit_kind(metric);
            match scale(kind, Some(unit)) {
                Scale::Known(_) => {
                    normalized_unit += 1;
                }
                Scale::Unknown => {}
            }
        }
    }

    // Report
    eprintln!("=== NORMALIZATION BRIDGE CANARY RESULTS ===");
    eprintln!("Total records:           {}", total);
    eprintln!("No quote (held):         {}", no_quote);
    eprintln!("Source-verified (quote): {}", verified);
    eprintln!("Held (verification fail):{}", held);
    eprintln!("Rejected (parse fail):   {}", rejected);
    eprintln!("Condition normalized:    {}", normalized_condition);
    eprintln!("Unit normalized:         {}", normalized_unit);
    eprintln!();
    eprintln!("=== YIELD RATES ===");
    if total > 0 {
        eprintln!("Verification rate:   {:.1}%", (verified as f64 / total as f64) * 100.0);
        eprintln!("Condition parse rate:{:.1}%", (normalized_condition as f64 / total as f64) * 100.0);
        eprintln!("Unit parse rate:     {:.1}%", (normalized_unit as f64 / total as f64) * 100.0);
        eprintln!("Overall usable:      {:.1}%", (verified.min(normalized_unit) as f64 / total as f64) * 100.0);
    }
}
