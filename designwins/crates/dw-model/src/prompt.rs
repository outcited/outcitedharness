//! Prompt construction (`local_prompt` + `focused_page_context` in
//! `local_model.py`).
//!
//! Builds the byte-identical instruction string sent to an extraction provider.
//! Two JSON encodings are involved and they are **not** interchangeable:
//!
//! * page evidence — `json.dumps(..., ensure_ascii=False, sort_keys=True,
//!   separators=(",", ":"))` = [`dw_extract::canonical::canonical_json`].
//! * response schema — `json.dumps(schema, sort_keys=True)` = default
//!   separators (`", "` / `": "`), `ensure_ascii=True`.
//!
//! The `!r` scope-rule interpolation uses Python's `repr()`.

use serde_json::{Map, Value};

use crate::schemas::schema_for;

/// A prompt construction failure.
#[derive(Debug, thiserror::Error)]
pub enum PromptError {
    /// `page_evidence` is missing `document_sha256` or `page_1based`.
    #[error("page evidence missing key: {0}")]
    MissingKey(String),
    /// Focused evidence exceeds 80 000 bytes even after dropping digital text.
    #[error("focused page evidence exceeds safe prompt bound")]
    TooLarge,
    /// `canonical_json` rejected a non-finite float in the evidence tree.
    #[error("non-finite number in page evidence")]
    NonFinite,
    /// Unknown capability name.
    #[error("unsupported capability: {0}")]
    UnknownCapability(String),
}

// ── Python repr ──────────────────────────────────────────────────────────

/// Python `repr(s)` for a string — shortest quote style, backslash-escaped.
fn py_repr_str(s: &str) -> String {
    if !s.contains('\'') {
        format!("'{s}'")
    } else if !s.contains('"') {
        format!("\"{s}\"")
    } else {
        let escaped = s.replace('\\', "\\\\").replace('\'', "\\'");
        format!("'{escaped}'")
    }
}

/// Python `repr(value)` for the scope-rule `!r` interpolation.
fn py_repr(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(b) => (if *b { "True" } else { "False" }).to_string(),
        Value::String(s) => py_repr_str(s),
        Value::Number(n) => n.to_string(),
        other => py_repr_str(&other.to_string()),
    }
}

// ── JSON with Python default separators (`, ` / `: `) and sorted keys ────

/// A `serde_json::ser::Formatter` matching Python's default `json.dumps`
/// separators: `", "` between items, `": "` after keys.
struct PyPrettyFormatter;

impl serde_json::ser::Formatter for PyPrettyFormatter {
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

/// `json.dumps(value, sort_keys=True)` — sorted keys, default separators.
// Serializing a `Value` into a `Vec<u8>` cannot fail; the UTF-8 conversion
// succeeds because serde_json always emits valid UTF-8.
#[allow(
    clippy::expect_used,
    reason = "serializing a Value to a byte buffer cannot fail"
)]
fn py_dumps_sorted(value: &Value) -> String {
    let sorted = sorted_value(value);
    let mut out = Vec::new();
    let mut ser = serde_json::Serializer::with_formatter(&mut out, PyPrettyFormatter);
    serde::Serialize::serialize(&sorted, &mut ser).expect("serializing a Value cannot fail");
    String::from_utf8(out).expect("serde_json emits UTF-8")
}

/// A copy of `value` with every object's keys inserted in ascending order.
fn sorted_value(value: &Value) -> Value {
    match value {
        Value::Array(items) => Value::Array(items.iter().map(sorted_value).collect()),
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let mut out = Map::new();
            for key in keys {
                if let Some(v) = map.get(key) {
                    out.insert(key.clone(), sorted_value(v));
                }
            }
            Value::Object(out)
        }
        other => other.clone(),
    }
}

// ── bbox overlap ─────────────────────────────────────────────────────────

/// `_overlaps(left, right)` — axis-aligned 2-D rect intersection.
fn overlaps(left: &Value, right: &Value) -> bool {
    let (Some(l), Some(r)) = (as_bbox(left), as_bbox(right)) else {
        return false;
    };
    l[2] > r[0] && r[2] > l[0] && l[3] > r[1] && r[3] > l[1]
}

fn as_bbox(v: &Value) -> Option<[f64; 4]> {
    let arr = v.as_array()?;
    if arr.len() != 4 {
        return None;
    }
    let mut out = [0.0f64; 4];
    for (i, item) in arr.iter().enumerate() {
        let slot = out.get_mut(i)?;
        *slot = item.as_f64()?;
    }
    Some(out)
}

// ── focused_page_context ─────────────────────────────────────────────────

/// Filter blocks/tables by region overlap when regions are present.
fn filter_by_regions(page_evidence: &Value, regions: &[&Value]) -> (Vec<Value>, Vec<Value>) {
    if regions.is_empty() {
        return (
            clone_array(page_evidence, "blocks"),
            clone_array(page_evidence, "tables"),
        );
    }
    let bboxes: Vec<Value> = regions
        .iter()
        .filter_map(|r| r.get("bbox").cloned())
        .collect();
    let table_indexes: std::collections::BTreeSet<i64> = regions
        .iter()
        .filter_map(|r| r.get("table_index").and_then(Value::as_i64))
        .collect();
    let blocks = clone_array(page_evidence, "blocks")
        .into_iter()
        .filter(|b| {
            b.get("bbox")
                .is_some_and(|bb| bboxes.iter().any(|b2| overlaps(bb, b2)))
        })
        .collect();
    let tables = clone_array(page_evidence, "tables")
        .into_iter()
        .enumerate()
        .filter(|(fallback, t)| {
            t.get("table_index")
                .and_then(Value::as_i64)
                .unwrap_or_else(|| i64::try_from(*fallback).unwrap_or(0))
                .pipe(|idx| table_indexes.contains(&idx))
        })
        .map(|(_, t)| t)
        .collect();
    (blocks, tables)
}

/// `focused_page_context(capability, page_evidence)` — project broad page
/// evidence onto selected structural/package scope.
///
/// # Errors
///
/// [`PromptError::MissingKey`] when `document_sha256` or `page_1based` is
/// absent from `page_evidence`.
pub fn focused_page_context(capability: &str, page_evidence: &Value) -> Result<Value, PromptError> {
    let structural = page_evidence.get("structural_evidence");
    let is_structural_map = structural.is_some_and(Value::is_object);
    let regions: Vec<&Value> = structural
        .and_then(|s| s.get("regions"))
        .and_then(Value::as_array)
        .map(|a| a.iter().collect())
        .unwrap_or_default();

    let (blocks, tables) = filter_by_regions(page_evidence, &regions);

    let scope = structural.and_then(|s| s.get("package_scope"));
    let selected_header = if is_structural_map && scope.is_some_and(Value::is_object) {
        scope
            .and_then(|s| s.get("column_header"))
            .and_then(Value::as_str)
            .unwrap_or("")
            .trim()
            .to_lowercase()
    } else {
        String::new()
    };

    let tables = if (capability == "pin_or_ball" || capability == "pin_semantics")
        && !selected_header.is_empty()
    {
        tables
            .iter()
            .map(|t| dw_recon::table_extractors::project_pin_table(t, &selected_header))
            .collect()
    } else {
        tables
    };

    let digital_text = page_evidence
        .get("digital_text")
        .cloned()
        .unwrap_or(Value::Null);
    let digital_text = if digital_text.is_object() {
        let text = digital_text
            .get("text")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let mut map = digital_text.as_object().cloned().unwrap_or_default();
        map.insert(
            "text".into(),
            Value::String(text.chars().take(30_000).collect()),
        );
        map.insert(
            "truncated".into(),
            Value::Bool(text.chars().count() > 30_000),
        );
        Value::Object(map)
    } else {
        digital_text
    };

    let doc_sha = page_evidence
        .get("document_sha256")
        .cloned()
        .ok_or_else(|| PromptError::MissingKey("document_sha256".into()))?;
    let page = page_evidence
        .get("page_1based")
        .cloned()
        .ok_or_else(|| PromptError::MissingKey("page_1based".into()))?;

    let mut out = Map::new();
    out.insert("document_sha256".into(), doc_sha);
    out.insert("page_1based".into(), page);
    out.insert("blocks".into(), Value::Array(blocks));
    out.insert("tables".into(), Value::Array(tables));
    out.insert("digital_text".into(), digital_text);
    out.insert(
        "structural_evidence".into(),
        structural.cloned().unwrap_or(Value::Null),
    );
    Ok(Value::Object(out))
}

// ── local_prompt ─────────────────────────────────────────────────────────

/// `local_prompt(capability, page_evidence, include_page_evidence)` — the
/// byte-identical instruction string.
///
/// # Errors
///
/// [`PromptError::MissingKey`] when page evidence lacks required keys;
/// [`PromptError::TooLarge`] when focused evidence exceeds 80 000 bytes;
/// [`PromptError::NonFinite`] on a non-finite float in the evidence tree;
/// [`PromptError::UnknownCapability`] for an unrecognised capability name.
pub fn local_prompt(
    capability: &str,
    page_evidence: &Value,
    include_page_evidence: bool,
) -> Result<String, PromptError> {
    let schema = schema_for(capability);
    let structural = page_evidence.get("structural_evidence");
    let package_scope = structural.and_then(|s| s.get("package_scope"));

    let encoded = if include_page_evidence {
        let evidence = focused_page_context(capability, page_evidence)?;
        let mut encoded = canonical_json_string(&evidence)?;
        if encoded.len() > 80_000 {
            let mut ev = evidence.as_object().cloned().unwrap_or_default();
            let mut dt = Map::new();
            dt.insert("available".into(), Value::Bool(false));
            dt.insert("text".into(), Value::String(String::new()));
            dt.insert(
                "reason".into(),
                Value::String("omitted_to_preserve_complete_structural_json".into()),
            );
            ev.insert("digital_text".into(), Value::Object(dt));
            encoded = canonical_json_string(&Value::Object(ev))?;
        }
        if encoded.len() > 80_000 {
            return Err(PromptError::TooLarge);
        }
        encoded
    } else {
        String::new()
    };

    let capability_rules = rules_for(capability)?;

    let scope_rule = if package_scope.is_some_and(Value::is_object) {
        let pkg = package_scope.and_then(|s| s.get("package"));
        let col = package_scope
            .and_then(|s| s.get("column_header"))
            .and_then(Value::as_str);
        if col.is_some_and(|c| !c.is_empty()) {
            format!(
                "Extract only package {} using table column {}. Ignore every other package column. ",
                pkg.map_or_else(|| "None".into(), py_repr),
                py_repr(&Value::String(col.unwrap_or_default().to_string())),
            )
        } else {
            format!(
                "This document ships one package variant, {}. Extract the full printed pin table for it. ",
                pkg.map_or_else(|| "None".into(), py_repr),
            )
        }
    } else {
        String::new()
    };

    let source_instruction = if include_page_evidence {
        format!("Extracted page evidence:\n{encoded}")
    } else {
        "Read only the supplied rendered datasheet page image. No PDF-extracted table text is provided or authoritative.".to_string()
    };

    let schema_str = py_dumps_sorted(schema);
    Ok(format!(
        "Extract capability={capability} from the supplied datasheet page. Use only printed evidence. Preserve units and package identity. Use null for absent scalar values. Return JSON only.\n\nCapability rules: {scope_rule}{capability_rules}\n\nResponse schema:\n{schema_str}\n\n{source_instruction}"
    ))
}

// ── helpers ──────────────────────────────────────────────────────────────

fn clone_array(v: &Value, key: &str) -> Vec<Value> {
    v.get(key)
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
}

/// `json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`
/// — the page-evidence encoding.
fn canonical_json_string(v: &Value) -> Result<String, PromptError> {
    let bytes = dw_extract::canonical::canonical_json(v).map_err(|_| PromptError::NonFinite)?;
    String::from_utf8(bytes).map_err(|_| PromptError::NonFinite)
}

trait Pipe: Sized {
    fn pipe<R>(self, f: impl FnOnce(Self) -> R) -> R;
}
impl<T> Pipe for T {
    fn pipe<R>(self, f: impl FnOnce(Self) -> R) -> R {
        f(self)
    }
}

/// The per-capability rule block embedded in every prompt.
fn rules_for(capability: &str) -> Result<&'static str, PromptError> {
    Ok(match capability {
        "pin_or_ball" => "Return one row per visibly printed physical pin or ball. Never combine multiple identifiers into one row and never fill a sequence that is not printed. Keep package variants isolated. Set package_as_read to the exact printed header text of the pin-number column you read, copied verbatim from the page; use null only when no pin-number column header is printed. ",
        "pin_semantics" => "Return one row per visibly printed physical pin or ball. Never combine multiple identifiers into one row, and omit rows whose selected-package pin or ball cell is blank, -, —, or N/A. Use dir only for a printed direction or pin-category value such as I, O, I/O, P, or S. Use type only for a printed electrical I/O structure or level such as FT_h or 5VT. Use supply_domain only for an explicitly headed supply-domain value. Put values from Description or Function columns in functions. Never copy a description or supply domain into type. Copy each value only from the matching identity row and matching semantic column. Represent blank and non-value scalar cells as JSON null. Keep package variants isolated. Set package_as_read to the exact printed header text of the pin-number column you read, copied verbatim from the page; use null only when no pin-number column header is printed. ",
        "parametrics" => "Return one fact per actual data value. Skip blank cells and non-value markers such as -, —, or N/A. Copy field exactly from one printed Parameter or Symbol cell; never construct it by joining cells. Put size, test, and operating qualifiers in conditions. Copy value, unit, min/typ/max role, and conditions verbatim from the same visible row and its headers. Never paraphrase, expand an acronym, interpret a label, or combine qualifiers from separate cells; omit a fact that requires such a transformation. Use value_role for the printed min/typ/max/value header. Do not combine values, select a different condition, or convert units. ",
        "power_modes" => "Return one row per printed power or low-power mode current. Copy mode names verbatim from the printed Mode or Operating mode cell; never normalize or invent a mode name. Each current value must come from the same printed row as its mode and its unit; value_role is the printed min/typ/max header. Put VDD, frequency, temperature, and peripheral qualifiers in conditions verbatim from the row and column headers. TA = 25 C is recorded like any other condition, never assumed. Skip rows whose value is blank, -, —, or N/A, and skip prose. Do not convert units. ",
        "typical_characteristics" => "Read the printed plot and digitize it. Copy the plot title and each axis label and unit verbatim. Set axis min and max from the printed axis range or the plotted tick span. For each printed curve return one series: name and condition copied from its legend entry verbatim. Points are digitized (x, y) samples in the printed axis units at visually identifiable features (endpoints, bends, crossings, labeled markers) — never invent samples between features. Do not extrapolate past the plotted range. Do not read values from another plot on the page. ",
        "series_summary" => "Copy each characteristic and application nearly verbatim from the page, then summarize only those copied facts. Do not invent positioning, recommendations, or competitors. ",
        "opn_decoder" => "Decode only fields explicitly shown by the ordering diagram or table. Return suffixes as objects with code and nullable meaning. Use null for every unstated segment. ",
        other => return Err(PromptError::UnknownCapability(other.into())),
    })
}
