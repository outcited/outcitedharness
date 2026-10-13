//! Pair materialization, byte-compatible with the two reference builders
//! (`scripts/build_lora_pairs.py` v1, `scripts/build_lora_pairs_v2.py` v2).
//!
//! Pairs are the quote-native training examples the student learns from
//! (contract **C8**): a system instruction, a user message carrying the
//! printed pages, and an assistant target of compact parametric rows. The
//! pymupdf/sqlite seams (page text, burn claims, the PDF index) travel as
//! captured inputs — the PDF layer, exactly as it does in the recon golds.
//!
//! Byte compatibility is exact: Python's `json.dumps(..., ensure_ascii=False)`
//! separators (`", "`, `": "`), key insertion order, and the seeded
//! train/holdout split ([`crate::shuffle`]).

use serde_json::{json, Map, Value};

use crate::gate::GateReport;
use crate::shuffle::seeded_shuffle;

/// The shared extraction instruction (byte-identical to both builders).
pub const SYSTEM: &str = "You are a datasheet parametric extraction engine. Given datasheet pages, extract the parametric table rows for the requested part as compact JSON: [{\"symbol\": str, \"value\": number, \"unit\": str, \"qualifier\": str|null, \"condition\": str|null}]. Use only values printed in the document.";

/// The v1 pair split seed (the reference's `1788172800`).
pub const SPLIT_SEED: u64 = 1_788_172_800;

/// Serialize with Python's `json.dumps(..., ensure_ascii=False)` byte shape:
/// `", "` / `": "` separators, insertion-order keys, raw UTF-8.
///
/// # Panics
///
/// Never in practice: serializing a [`Value`] into a `Vec<u8>` cannot fail, and
/// `serde_json` always emits UTF-8. Both unwraps are kept explicit rather than
/// silently swallowed.
#[must_use]
#[allow(
    clippy::expect_used,
    reason = "serializing a Value to a byte buffer cannot fail"
)]
pub fn py_dumps(value: &Value) -> String {
    let mut out = Vec::new();
    let formatter = PyFormatter;
    let mut ser = serde_json::Serializer::with_formatter(&mut out, formatter);
    serde::Serialize::serialize(value, &mut ser).expect("serializing a Value cannot fail");
    String::from_utf8(out).expect("serde_json emits UTF-8")
}

/// `, ` and `: ` after the first element (Python's default separators).
struct PyFormatter;

impl serde_json::ser::Formatter for PyFormatter {
    fn begin_array_value<W>(&mut self, writer: &mut W, first: bool) -> std::io::Result<()>
    where
        W: ?Sized + std::io::Write,
    {
        if first {
            Ok(())
        } else {
            writer.write_all(b", ")
        }
    }

    fn begin_object_key<W>(&mut self, writer: &mut W, first: bool) -> std::io::Result<()>
    where
        W: ?Sized + std::io::Write,
    {
        if first {
            Ok(())
        } else {
            writer.write_all(b", ")
        }
    }

    fn begin_object_value<W>(&mut self, writer: &mut W) -> std::io::Result<()>
    where
        W: ?Sized + std::io::Write,
    {
        writer.write_all(b": ")
    }
}

/// A v1 row `{symbol, value, unit, qualifier, condition}` in the reference's
/// insertion order, with its null-vs-present shape preserved.
fn v1_row(row: &Value) -> Value {
    let mut out = Map::new();
    for key in ["symbol", "value", "unit", "qualifier", "condition"] {
        out.insert(
            key.to_string(),
            row.get(key).cloned().unwrap_or(Value::Null),
        );
    }
    Value::Object(out)
}

/// v1 `main()` over captured inputs: `docs` are `grid_rows.jsonl` rows,
/// `pdfs` maps artifact name -> `(sha256, text)`. Returns the JSONL bytes.
#[must_use]
pub fn build_pairs_v1(
    docs: &[Value],
    pdfs: &std::collections::BTreeMap<String, (String, String)>,
) -> String {
    use std::collections::BTreeMap;

    // load_rows: group by document_sha256 preserving first-seen meta.
    let mut by_doc: BTreeMap<String, (Value, Vec<Value>)> = BTreeMap::new();
    for row in docs {
        let Some(sha) = row.get("document_sha256").and_then(Value::as_str) else {
            continue;
        };
        let entry = by_doc.entry(sha.to_string()).or_insert_with(|| {
            (
                json!({
                    "artifact": row.get("source_artifact").cloned().unwrap_or(Value::Null),
                    "vendor": row.get("vendor").cloned().unwrap_or(Value::Null),
                    "part": row.get("scope_as_printed").cloned().unwrap_or(Value::Null),
                    "dataset": "grids",
                }),
                Vec::new(),
            )
        });
        entry.1.push(json!({
            "symbol": row.get("symbol").cloned().unwrap_or(Value::Null),
            "value": row.get("value").cloned().unwrap_or(Value::Null),
            "unit": row.get("unit").cloned().unwrap_or(Value::Null),
            "qualifier": row.get("quantity_qualifier").cloned().unwrap_or(Value::Null),
            "condition": row
                .get("condition_verbatim")
                .filter(|v| !v.is_null())
                .or_else(|| row.get("table_condition"))
                .cloned()
                .unwrap_or(Value::Null),
        }));
    }

    let mut lines: Vec<String> = Vec::new();
    for (sha, (meta, rows)) in &by_doc {
        let rows: Vec<Value> = rows
            .iter()
            .filter(|r| {
                r.get("symbol").is_some_and(|s| !s.is_null())
                    && r.get("value").is_some_and(|v| !v.is_null())
            })
            .map(v1_row)
            .collect();
        if rows.is_empty() {
            continue;
        }
        let artifact = meta.get("artifact").and_then(Value::as_str).unwrap_or("");
        let Some((pdf_sha, text)) = pdfs.get(artifact) else {
            continue;
        };
        if pdf_sha != sha || text.trim().len() < 200 {
            continue;
        }
        let part = meta.get("part").and_then(Value::as_str).unwrap_or("");
        let vendor = meta.get("vendor").and_then(Value::as_str).unwrap_or("");
        let user = format!("Part: {part}\nVendor: {vendor}\n\nDatasheet pages:\n{text}");
        let mut meta_out = meta.as_object().cloned().unwrap_or_default();
        meta_out.insert("document_sha256".into(), json!(sha));
        meta_out.insert("n_rows".into(), json!(rows.len()));
        let rec = json!({
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user},
                {"role": "assistant", "content": py_dumps(&Value::Array(rows.clone()))},
            ],
            "meta": Value::Object(meta_out),
        });
        lines.push(py_dumps(&rec));
    }
    if lines.is_empty() {
        String::new()
    } else {
        lines.join("\n") + "\n"
    }
}

/// v2 `main()` over captured inputs: `stem_claims` maps burn stem -> claim
/// rows, `pdf_index` maps filename -> path, `texts` maps filename -> page
/// text. Returns `(train_jsonl, holdout_jsonl)`.
#[must_use]
pub fn build_pairs_v2(
    stem_claims: &std::collections::BTreeMap<String, Vec<Value>>,
    pdf_index: &std::collections::BTreeMap<String, String>,
    texts: &std::collections::BTreeMap<String, String>,
    min_claims: usize,
    limit: Option<usize>,
) -> (String, String) {
    let mut pairs: Vec<Value> = Vec::new();
    for (stem, claims) in stem_claims {
        if claims.len() < min_claims {
            continue;
        }
        let Some(path) = find_pdf_for_stem(stem, pdf_index) else {
            continue;
        };
        let name = path.rsplit('/').next().unwrap_or(&path);
        let Some(text) = texts.get(name) else {
            continue;
        };
        if text.len() < 100 {
            continue;
        }
        let mut rows: Vec<Value> = Vec::new();
        let mut seen: std::collections::BTreeSet<(String, String, String)> =
            std::collections::BTreeSet::new();
        for c in claims {
            let sym = c
                .get("symbol")
                .filter(|v| !v.is_null())
                .or_else(|| c.get("param").filter(|v| !v.is_null()))
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let val = c.get("value").cloned().unwrap_or(Value::Null);
            let unit = c
                .get("unit")
                .filter(|v| !v.is_null())
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let key = (
                sym.to_lowercase(),
                canonical_number(&val),
                unit.to_lowercase(),
            );
            if !seen.insert(key) {
                continue;
            }
            let mut row = Map::new();
            row.insert("symbol".into(), json!(sym));
            row.insert("value".into(), val);
            row.insert(
                "unit".into(),
                if unit.is_empty() {
                    Value::Null
                } else {
                    json!(unit)
                },
            );
            row.insert(
                "qualifier".into(),
                c.get("qualifier").cloned().unwrap_or(Value::Null),
            );
            row.insert(
                "condition".into(),
                c.get("condition").cloned().unwrap_or(Value::Null),
            );
            rows.push(Value::Object(row));
        }
        if rows.is_empty() {
            continue;
        }
        let (vendor, part) = match stem.split_once('-') {
            Some((v, p)) => (v.to_string(), p.to_string()),
            None => (stem.clone(), stem.clone()),
        };
        pairs.push(json!({
            "instruction": SYSTEM,
            "input": format!("Part: {part}\nVendor: {vendor}\n\nDatasheet pages:\n{text}"),
            "output": py_dumps(&Value::Array(rows)),
        }));
        if limit.is_some_and(|l| pairs.len() >= l) {
            break;
        }
    }
    // The reference shuffles `pairs` in place with the split seed, then takes
    // a 150..=300 holdout (10% floor) from the front.
    let shuffled = seeded_shuffle(SPLIT_SEED, &pairs);
    let holdout_size = 300.min(150.max(shuffled.len() / 10)).min(shuffled.len());
    let (holdout, train) = shuffled.split_at(holdout_size);
    (jsonl(train), jsonl(holdout))
}

fn canonical_number(value: &Value) -> String {
    if let Some(f) = value.as_f64() {
        return f.to_string();
    }
    match value {
        Value::String(s) => s.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

fn jsonl(rows: &[Value]) -> String {
    if rows.is_empty() {
        return String::new();
    }
    rows.iter().map(py_dumps).collect::<Vec<_>>().join("\n") + "\n"
}

/// The reference's stem -> PDF resolution: exact key, then `vendor-part` /
/// `part` / `part.pdf`, then a case-insensitive substring match.
fn find_pdf_for_stem(
    stem: &str,
    pdf_index: &std::collections::BTreeMap<String, String>,
) -> Option<String> {
    if let Some(path) = pdf_index.get(stem) {
        return Some(path.clone());
    }
    if let Some((vendor, part)) = stem.split_once('-') {
        for key in [
            format!("{vendor}-{part}"),
            part.to_string(),
            format!("{part}.pdf"),
        ] {
            if let Some(path) = pdf_index.get(&key) {
                return Some(path.clone());
            }
        }
    }
    let lower = stem.to_lowercase();
    pdf_index
        .iter()
        .find(|(key, _)| lower.contains(&key.to_lowercase()) || key.to_lowercase().contains(&lower))
        .map(|(_, path)| path.clone())
}

/// The gate report type is re-exported here for callers that build reports.
pub type Report = GateReport;
