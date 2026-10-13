//! Structural datasheet-table discovery (`harness/electronics/regions.py`,
//! ported 1:1 for the scoring half).
//!
//! Tables are scored by structure, never by vendor-specific pin names: a
//! register map or an ordering guide is vetoed (a real pin header overrides
//! the register veto), physical identifier columns need 3+ hits at 45%
//! density (`PHYSICAL_ID` accepts large BGA balls like `AA10`), parametric
//! tables need a parameter header AND two value-role headers, and text
//! regions bound only visible evidence and require the capability's signal.
//! The pymupdf render half (`render_*`, `pdftotext_layout_page`) is process
//! I/O and stays Python-side.

use regex::Regex;
use serde_json::{json, Value};

use crate::locator::{is_definition_table, package_headers};
use crate::table_extractors::{cell, pin_semantic_roles, PARAMETER_HEADER};

/// `Value` rows -> the `Option<String>` grids the locator readers take.
fn opt_rows(rows: &[Vec<Value>]) -> Vec<Vec<Option<String>>> {
    rows.iter()
        .map(|row| {
            row.iter()
                .map(|value| match value {
                    Value::Null => None,
                    Value::String(s) => Some(s.clone()),
                    other => Some(other.to_string()),
                })
                .collect()
        })
        .collect()
}

#[allow(
    clippy::expect_used,
    reason = "patterns are literals from the reference"
)]
fn re(src: &str) -> Regex {
    Regex::new(src).expect("pattern is a literal from the reference")
}

/// `Pin`, `Ball`, `Pin number`, `Pin assignment` — the identifier header.
const PIN_HEADER: &str =
    r"(?i)\b(?:PIN|BALL|TERMINAL)(?:\s*/\s*(?:PIN|BALL))?\s*(?:NUMBER|NO\.?|NAME|ASSIGNMENT)?\b";
/// `(Pin |Ball |Signal |Terminal )?Name`.
const NAME_HEADER: &str = r"(?i)\b(?:(?:PIN|BALL|SIGNAL|TERMINAL)\s*(?:/BALL\s*)?)?NAME\b";
/// `Description`, `Function`, `Type`, `Direction`.
const SEMANTIC_HEADER: &str = r"(?i)\b(?:DESCRIPTION|FUNCTION|TYPE|DIRECTION)\b";
/// Register-map vocabulary that vetoes a pin table.
const REGISTER_VETO: &str = r"(?i)\b(?:OFFSET|RESET\s+VALUE|BIT\s+FIELD|REGISTER)\b|0x[0-9A-F]{2,}";
/// Ordering-guide vocabulary that vetoes a parametric table.
const ORDERING_VETO: &str = r"(?i)\b(?:ORDERING|ORDERABLE|PART\s+NUMBER|PACKAGE\s+OPTION)\b";
/// `Features`, `Applications`, `Product overview`, ...
const SUMMARY_SIGNAL: &str = r"(?i)\b(?:FEATURES?|APPLICATIONS?|DESCRIPTION|PRODUCT\s+(?:OVERVIEW|SUMMARY)|DEVICE\s+(?:OVERVIEW|INFORMATION))\b";
/// `Ordering information`, `Orderable part number`, `Nomenclature`, ...
const OPN_SIGNAL: &str = r"(?i)\b(?:ORDERING\s+(?:INFORMATION|GUIDE)|ORDERABLE\s+PART\s+NUMBER|PART\s+NUMBERING|DEVICE\s+NAMING|NOMENCLATURE|PACKAGE\s+OPTION)\b";
/// `Min`, `Typ`, `Max`, `Value`, `Unit`, `Condition` — value-role headers.
const PARAMETRIC_ROLE_HEADER: &str = r"(?i)\b(?:MIN(?:IMUM)?|TYP(?:ICAL)?|MAX(?:IMUM)?|VALUE|UNITS?|CONDITIONS?|TEST\s+CONDITIONS?)\b";
/// A printed physical pin/ball identifier (accepts `AA10`).
const PHYSICAL_ID: &str = r"(?i)^(?:\d{1,4}|[A-HJ-NP-Z]{1,3}\d{1,3}|(?:EP|PAD|TAB|DAP)\d*)$";

/// `PACKAGE_KEY` — family-then-count or count-then-family package tokens.
#[must_use]
pub fn package_key_re() -> &'static Regex {
    crate::locator::package_key_re()
}

fn register_veto_re() -> &'static Regex {
    use std::sync::OnceLock;
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(REGISTER_VETO))
}

fn header_text(rows: &[Vec<Value>], limit: usize) -> String {
    rows.iter()
        .take(limit)
        .flat_map(|row| row.iter().map(|value| cell(Some(value))))
        .filter(|text| !text.is_empty())
        .collect::<Vec<_>>()
        .join(" | ")
}

fn all_text(rows: &[Vec<Value>]) -> String {
    rows.iter()
        .flat_map(|row| row.iter().map(|value| cell(Some(value))))
        .filter(|text| !text.is_empty())
        .collect::<Vec<_>>()
        .join(" | ")
}

/// Score table structure without relying on vendor-specific pin names.
#[must_use]
#[allow(
    clippy::too_many_lines,
    clippy::needless_range_loop,
    clippy::cast_precision_loss,
    reason = "faithful port of pin_table_score; column walks and the density ratio are the reference's"
)]
pub fn pin_table_score(rows: &[Vec<Value>]) -> (i64, Vec<String>) {
    if rows.len() < 3 {
        return (0, vec!["too_few_rows".to_string()]);
    }
    let header = header_text(rows, 4);
    let all = all_text(rows);
    if register_veto_re().is_match(&all)
        && !(re(NAME_HEADER).is_match(&header) && re(PIN_HEADER).is_match(&header))
    {
        return (0, vec!["register_table_veto".to_string()]);
    }
    let maximum_columns = rows.iter().map(Vec::len).max().unwrap_or(0);
    let mut structural_columns = 0i64;
    for column in 0..maximum_columns {
        let values: Vec<String> = rows
            .iter()
            .skip(1)
            .filter(|row| column < row.len())
            .map(|row| cell(row.get(column)))
            .filter(|text| !text.is_empty())
            .collect();
        let physical = values
            .iter()
            .filter(|value| re(PHYSICAL_ID).is_match(value))
            .count();
        if physical >= 3 && (physical as f64) / (1f64.max(values.len() as f64)) >= 0.45 {
            structural_columns += 1;
        }
    }
    let mut reasons: Vec<String> = Vec::new();
    let mut score = 0i64;
    if is_definition_table(&opt_rows(rows)) {
        score += 8;
        reasons.push("locator_definition_table".to_string());
    }
    if re(NAME_HEADER).is_match(&header) {
        score += 3;
        reasons.push("pin_name_header".to_string());
    }
    if re(PIN_HEADER).is_match(&header) {
        score += 2;
        reasons.push("pin_identity_header".to_string());
    }
    if re(SEMANTIC_HEADER).is_match(&header) {
        score += 1;
        reasons.push("semantic_header".to_string());
    }
    if !package_headers(&opt_rows(rows)).is_empty() {
        score += 2;
        reasons.push("package_header".to_string());
    }
    if structural_columns > 0 {
        score += 3.min(structural_columns + 1);
        reasons.push("physical_identifier_column".to_string());
    }
    let definition_headers = [
        "locator_definition_table",
        "pin_name_header",
        "pin_identity_header",
    ];
    if !reasons
        .iter()
        .any(|r| definition_headers.contains(&r.as_str()))
    {
        return (0, vec!["missing_pin_definition_header".to_string()]);
    }
    if reasons.is_empty() {
        return (score, vec!["no_structural_signal".to_string()]);
    }
    (score, reasons)
}

/// Require an explicit parameter field and multiple value-role headers.
#[must_use]
#[allow(
    clippy::indexing_slicing,
    clippy::needless_range_loop,
    reason = "faithful port of parametric_table_score; indexes as the reference does"
)]
pub fn parametric_table_score(rows: &[Vec<Value>]) -> (i64, Vec<String>) {
    if rows.len() < 2 {
        return (0, vec!["too_few_rows".to_string()]);
    }
    // The header row is the one with the most parameter/role headers.
    let header_index = (0..6.min(rows.len()))
        .max_by_key(|index| {
            rows[*index]
                .iter()
                .map(|value| {
                    let text = cell(Some(value));
                    i64::from(
                        re(PARAMETER_HEADER).is_match(&text)
                            || re(PARAMETRIC_ROLE_HEADER).is_match(&text),
                    )
                })
                .sum::<i64>()
        })
        .unwrap_or(0);
    let header = header_text(rows, header_index + 1);
    let all = all_text(rows);
    if register_veto_re().is_match(&all) {
        return (0, vec!["register_table_veto".to_string()]);
    }
    if re(ORDERING_VETO).is_match(&header) {
        return (0, vec!["ordering_table_veto".to_string()]);
    }
    if !re(PARAMETER_HEADER).is_match(&header) {
        return (0, vec!["missing_parameter_header".to_string()]);
    }
    let roles: std::collections::BTreeSet<String> = re(PARAMETRIC_ROLE_HEADER)
        .find_iter(&header)
        .map(|m| m.as_str().to_lowercase())
        .collect();
    if roles.len() < 2 {
        return (0, vec!["insufficient_value_headers".to_string()]);
    }
    let mut reasons = vec![
        "parameter_header".to_string(),
        "multiple_value_headers".to_string(),
    ];
    let mut score = 7i64;
    let populated_rows = rows
        .iter()
        .skip(1)
        .filter(|row| {
            row.iter()
                .map(|value| cell(Some(value)))
                .filter(|t| !t.is_empty())
                .count()
                >= 2
        })
        .count();
    if populated_rows >= 2 {
        score += 1;
        reasons.push("multiple_data_rows".to_string());
    }
    (score, reasons)
}

fn as_rows(value: &Value) -> Option<Vec<Vec<Value>>> {
    let rows = value.get("rows")?.as_array()?;
    Some(
        rows.iter()
            .filter_map(|row| row.as_array().cloned())
            .collect(),
    )
}

fn as_bbox(value: &Value) -> Option<Vec<f64>> {
    let bbox = value.get("bbox")?.as_array()?;
    if bbox.len() != 4 {
        return None;
    }
    Some(
        bbox.iter()
            .map(|v| v.as_f64().unwrap_or_default())
            .collect(),
    )
}

/// Structural pin regions: scored tables with their geometry and headers.
#[must_use]
pub fn structural_pin_regions(page_evidence: &Value, minimum_score: i64) -> Vec<Value> {
    let mut regions = Vec::new();
    for (fallback_index, table) in page_evidence
        .get("tables")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .enumerate()
    {
        let (Some(rows), Some(bbox)) = (as_rows(table), as_bbox(table)) else {
            continue;
        };
        let (score, reasons) = pin_table_score(&rows);
        if score < minimum_score {
            continue;
        }
        regions.push(json!({
            "table_index": table.get("table_index").and_then(Value::as_u64).unwrap_or(fallback_index as u64),
            "bbox": bbox,
            "score": score,
            "reasons": reasons,
            "package_headers": package_headers(&opt_rows(&rows)),
            "semantic_roles": pin_semantic_roles(table),
            "rows": rows.len(),
        }));
    }
    regions
}

/// Structural parametric regions: scored parameter tables with geometry.
#[must_use]
pub fn structural_parametric_regions(page_evidence: &Value, minimum_score: i64) -> Vec<Value> {
    let mut regions = Vec::new();
    for (fallback_index, table) in page_evidence
        .get("tables")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .enumerate()
    {
        let (Some(rows), Some(bbox)) = (as_rows(table), as_bbox(table)) else {
            continue;
        };
        let (score, reasons) = parametric_table_score(&rows);
        if score < minimum_score {
            continue;
        }
        regions.push(json!({
            "table_index": table.get("table_index").and_then(Value::as_u64).unwrap_or(fallback_index as u64),
            "bbox": bbox,
            "score": score,
            "reasons": reasons,
            "package_headers": [],
            "rows": rows.len(),
        }));
    }
    regions
}

/// One source-bounded page region for summary or OPN evidence.
///
/// # Errors
///
/// Returns `"unsupported text-region capability: {capability}"` for anything
/// but `series_summary` / `opn_decoder`.
#[allow(
    clippy::indexing_slicing,
    reason = "the bbox is a fixed 4-tuple as the evidence carries it"
)]
pub fn structural_text_regions(
    page_evidence: &Value,
    capability: &str,
) -> Result<Vec<Value>, String> {
    let signal = match capability {
        "series_summary" => SUMMARY_SIGNAL,
        "opn_decoder" => OPN_SIGNAL,
        other => {
            return Err(format!("unsupported text-region capability: {other}"));
        }
    };
    let blocks: Vec<&Value> = page_evidence
        .get("blocks")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|block| as_bbox(block).is_some() && !cell(block.get("text")).is_empty())
        .collect();
    let tables: Vec<&Value> = page_evidence
        .get("tables")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|table| as_bbox(table).is_some())
        .collect();
    let visible_text = blocks
        .iter()
        .map(|block| cell(block.get("text")))
        .chain(tables.iter().map(|table| {
            table
                .get("rows")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_array)
                .flat_map(|row| row.iter().map(|value| cell(Some(value))))
                .filter(|text| !text.is_empty())
                .collect::<Vec<_>>()
                .join(" | ")
        }))
        .collect::<Vec<_>>()
        .join("\n");
    if !re(signal).is_match(&visible_text) {
        return Ok(Vec::new());
    }
    let bboxes: Vec<Vec<f64>> = blocks
        .iter()
        .chain(tables.iter())
        .filter_map(|entry| as_bbox(entry))
        .collect();
    if bboxes.is_empty() {
        return Ok(Vec::new());
    }
    let bbox = json!([
        bboxes.iter().map(|b| b[0]).fold(f64::INFINITY, f64::min),
        bboxes.iter().map(|b| b[1]).fold(f64::INFINITY, f64::min),
        bboxes
            .iter()
            .map(|b| b[2])
            .fold(f64::NEG_INFINITY, f64::max),
        bboxes
            .iter()
            .map(|b| b[3])
            .fold(f64::NEG_INFINITY, f64::max),
    ]);
    let rows: i64 = tables
        .iter()
        .map(|table| {
            table
                .get("rows")
                .and_then(Value::as_array)
                .map_or(0, Vec::len)
        })
        .fold(0i64, |acc, len| {
            acc.saturating_add(i64::try_from(len).unwrap_or(i64::MAX))
        });
    Ok(vec![json!({
        "table_index": Value::Null,
        "bbox": bbox,
        "score": 5,
        "reasons": [if capability == "series_summary" {
            "visible_summary_evidence"
        } else {
            "visible_ordering_evidence"
        }],
        "package_headers": [],
        "semantic_roles": [],
        "rows": rows,
    })])
}

/// Package tokens printed in the page blocks, first-seen order, deduped
/// case-insensitively.
#[must_use]
pub fn printed_package_mentions(page_evidence: &Value) -> Vec<String> {
    let mut mentions: Vec<String> = Vec::new();
    for block in page_evidence
        .get("blocks")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let text = cell(block.get("text"));
        for caps in package_key_re().captures_iter(&text) {
            let Some(value) = caps.get(0) else {
                continue;
            };
            let value = cell(Some(&Value::String(value.as_str().to_string())));
            if value.is_empty() {
                continue;
            }
            if !mentions
                .iter()
                .any(|m| m.to_lowercase() == value.to_lowercase())
            {
                mentions.push(value);
            }
        }
    }
    mentions
}
