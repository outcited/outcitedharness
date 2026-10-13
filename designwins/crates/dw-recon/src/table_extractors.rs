//! Conservative deterministic parsers for extracted datasheet tables
//! (`harness/electronics/table_extractors.py`, ported 1:1).
//!
//! The parsers only emit what is visibly printed: physical pin identifiers
//! stay verbatim (`1`, `A1`, `12(1)`), semantic fields are field-aligned to
//! the printed row and never inferred, and parametric rows expand to
//! value-level facts WITHOUT unit conversion. Tables are the PDF layer's
//! `extract()` grids (with `None` cells), so they travel as captured inputs.

use std::collections::BTreeMap;

use regex::Regex;
use serde_json::{json, Map, Value};

use crate::locator::package_pin_count;

#[allow(
    clippy::expect_used,
    reason = "patterns are literals from the reference"
)]
fn re(src: &str) -> Regex {
    Regex::new(src).expect("pattern is a literal from the reference")
}

/// `Pin`, `Ball`, `Pin no.`, `Pin#` — the identifier column header.
const PIN_HEADER: &str = r"(?i)^(?:PIN|BALL)(?:\s*(?:\(S\)|S|NO\.?|NUMBER|#))?$";
/// `Name`, `Pin name`, `Pin/Ball name`.
const NAME_HEADER: &str = r"(?i)^(?:(?:PIN|BALL|SIGNAL|PIN\s*/\s*BALL)\s*)?NAME\b";
/// `Type` — the electrical-type header.
const TYPE_HEADER: &str = r"(?i)\bTYPE\b";
/// `Direction`, `Dir`, `I/O`.
const DIRECTION_HEADER: &str = r"(?i)\b(?:DIRECTION|DIR)\b|^I\s*/\s*O$";
/// `I/O structure`, `Electrical type`.
const IO_STRUCTURE_HEADER: &str = r"(?i)\bI\s*/?\s*O\s+(?:STRUCTURE|LEVEL)\b|\bELECTRICAL\s+(?:TYPE|STRUCTURE|CHARACTERISTICS?)\b";
/// `I/O supply domain`, `Power domain`.
const SUPPLY_DOMAIN_HEADER: &str = r"(?i)\b(?:I\s*/?\s*O|POWER|SUPPLY)\s+(?:SUPPLY\s+)?DOMAIN\b";
/// `Description(s)`.
const DESCRIPTION_HEADER: &str = r"(?i)\bDESCRIPTIONS?\b";
/// `Function(s)`, `Multiplex`, `Reset state`.
const FUNCTION_HEADER: &str = r"(?i)\b(?:FUNCTIONS?|MULTIPLEX|RESET STATE)\b";
/// Function or description.
const FUNCTION_OR_DESCRIPTION_HEADER: &str =
    r"(?i)\b(?:FUNCTIONS?|DESCRIPTIONS?|MULTIPLEX|RESET STATE)\b";
/// `Parameter`, `Characteristic`, `Symbol`.
pub const PARAMETER_HEADER: &str = r"(?i)\b(?:PARAMETER|CHARACTERISTIC|SYMBOL)\b";
/// `Min`, `Typ`, `Max`, `Value`, `Unit`, `Condition`.
pub const VALUE_HEADER: &str = r"(?i)\b(?:MIN|TYP|MAX|VALUE|UNIT|CONDITION)\b";

/// Printed value-role columns mapped to normalized roles.
const PARAMETRIC_VALUE_ROLES: &[(&str, &str)] = &[
    ("min", "min"),
    ("minimum", "minimum"),
    ("typ", "typ"),
    ("typical", "typical"),
    ("max", "max"),
    ("maximum", "maximum"),
    ("value", "value"),
];

/// Cells that print no value.
const NON_VALUE_MARKERS: &[&str] = &["-", "\u{2014}", "\u{2013}", "na", "n/a"];

/// `Type` cell values that are directions, not types.
const DIRECTION_CODES: &[&str] = &["a", "ai", "ao", "i", "io", "o", "p", "s"];

/// Whitespace-collapsed cell text; `None` cells count as empty.
#[must_use]
pub fn cell(value: Option<&Value>) -> String {
    let text = match value {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
    };
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn normalized_cell(value: Option<&Value>) -> String {
    cell(value)
        .to_uppercase()
        .chars()
        .filter(char::is_ascii_alphanumeric)
        .collect()
}

fn header_score(row: &[Value], patterns: &[&str]) -> i64 {
    row.iter()
        .map(|value| {
            let text = cell(Some(value));
            i64::from(
                patterns
                    .iter()
                    .any(|p| re(&format!("(?i){p}")).is_match(&text)),
            )
        })
        .sum()
}

fn column(row: &[Value], pattern: &str) -> Option<usize> {
    let re = re(pattern);
    let matches: Vec<usize> = row
        .iter()
        .enumerate()
        .filter(|(_, value)| re.is_match(&cell(Some(value))))
        .map(|(index, _)| index)
        .collect();
    if matches.len() == 1 {
        matches.first().copied()
    } else {
        None
    }
}

fn value_at(row: &[Value], index: Option<usize>) -> Option<String> {
    let index = index?;
    let text = cell(row.get(index));
    if text.is_empty() {
        None
    } else {
        Some(text)
    }
}

/// A printed physical pin identifier, verbatim.
fn physical(value: Option<&Value>) -> Option<String> {
    let raw = cell(value).to_uppercase();
    if ["-", "\u{2014}", "N/A", "NA", "NC"].contains(&raw.as_str()) {
        return None;
    }
    if raw.chars().count() > 24 {
        return None;
    }
    if re(r"^\d{1,4}(?:\(\d+\))?$").is_match(&raw) {
        return Some(raw);
    }
    let caps = re(r"^([A-HJ-NP-Z]{1,3})\s*(\d{1,3})$").captures(&raw)?;
    Some(format!("{}{}", &caps[1], &caps[2]))
}

/// One or more explicit physical IDs without concatenating them.
#[must_use]
pub fn physical_pin_identifiers(value: Option<&Value>) -> Vec<String> {
    let raw = cell(value);
    if let Some(single) = physical(Some(&Value::String(raw.clone()))) {
        return vec![single];
    }
    let values: Vec<String> = re(r"[,;\s]+")
        .split(&raw)
        .filter_map(|token| {
            if token.is_empty() {
                None
            } else {
                physical(Some(&Value::String(token.to_string())))
            }
        })
        .collect();
    if values.len() > 1 {
        values.into_iter().fold(Vec::new(), |mut acc, v| {
            if !acc.contains(&v) {
                acc.push(v);
            }
            acc
        })
    } else {
        Vec::new()
    }
}

/// The header cells (joined across the header band) and the first data row.
#[must_use]
#[allow(
    clippy::indexing_slicing,
    clippy::needless_range_loop,
    reason = "faithful port of _pin_table_layout; indexes as the reference does"
)]
pub fn pin_table_layout(table: &Value) -> (Vec<String>, usize) {
    let Some(rows) = table.get("rows").and_then(Value::as_array) else {
        return (Vec::new(), 0);
    };
    if rows.is_empty() {
        return (Vec::new(), 0);
    }
    let mut header_signal_seen = false;
    let mut data_start = 6.min(rows.len());
    let header_patterns = [
        PIN_HEADER,
        NAME_HEADER,
        TYPE_HEADER,
        FUNCTION_OR_DESCRIPTION_HEADER,
    ];
    for (index, row) in rows.iter().take(6).enumerate() {
        let Some(row) = row.as_array() else {
            continue;
        };
        let physicals = row
            .iter()
            .filter(|value| physical(Some(value)).is_some())
            .count();
        if physicals >= 2 || (physicals >= 1 && header_signal_seen) {
            data_start = index;
            break;
        }
        header_signal_seen = header_signal_seen
            || row.iter().any(|value| {
                let text = cell(Some(value));
                header_patterns
                    .iter()
                    .any(|p| re(&format!("(?i){p}")).is_match(&text))
            });
    }
    let width = rows
        .iter()
        .take(data_start)
        .filter_map(|row| row.as_array())
        .map(Vec::len)
        .max()
        .unwrap_or(0);
    let headers = (0..width)
        .map(|column| {
            rows.iter()
                .take(data_start)
                .filter_map(|row| row.as_array())
                .filter(|row| column < row.len())
                .map(|row| cell(row.get(column)))
                .filter(|text| !text.is_empty())
                .collect::<Vec<_>>()
                .join(" ")
        })
        .collect();
    (headers, data_start)
}

/// The header cells of a pin table.
#[must_use]
pub fn pin_header_cells(table: &Value) -> Vec<String> {
    pin_table_layout(table).0
}

fn physical_header(value: &str) -> bool {
    package_pin_count(value).is_some() || re(&format!("(?i){PIN_HEADER}")).is_match(value)
}

/// Only the visibly printed pin/ball identities of a table.
#[must_use]
#[allow(
    clippy::too_many_lines,
    clippy::indexing_slicing,
    clippy::needless_range_loop,
    clippy::missing_panics_doc,
    reason = "faithful port of pin_identity_rows; indexes as the reference does"
)]
pub fn pin_identity_rows(table: &Value) -> Vec<Value> {
    let (headers, data_start) = pin_table_layout(table);
    let Some(rows) = table.get("rows").and_then(Value::as_array) else {
        return Vec::new();
    };
    if headers.is_empty() {
        return Vec::new();
    }
    let physical_columns: Vec<usize> = headers
        .iter()
        .enumerate()
        .filter(|(_, header)| physical_header(header))
        .map(|(index, _)| index)
        .collect();
    let name_columns: Vec<usize> = headers
        .iter()
        .enumerate()
        .filter(|(index, header)| {
            re(&format!("(?i){NAME_HEADER}")).is_match(header) && !physical_columns.contains(index)
        })
        .map(|(index, _)| index)
        .collect();
    if physical_columns.is_empty() || name_columns.is_empty() {
        return Vec::new();
    }
    let mut output = Vec::new();
    let mut seen: std::collections::BTreeSet<(String, String)> = std::collections::BTreeSet::new();
    for (row_index, row) in rows.iter().enumerate().skip(data_start) {
        let Some(row) = row.as_array() else {
            continue;
        };
        for name_column in &name_columns {
            if *name_column >= row.len() {
                continue;
            }
            let name = cell(row.get(*name_column));
            if name.is_empty()
                || NON_VALUE_MARKERS.contains(&name.to_lowercase().as_str())
                || name.chars().count() > 160
            {
                continue;
            }
            let physical_column = *physical_columns
                .iter()
                .min_by_key(|index| (**index).abs_diff(*name_column))
                .unwrap_or(&0);
            let physicals = physical_pin_identifiers(row.get(physical_column));
            if physicals.is_empty() {
                continue;
            }
            for physical_id in physicals {
                let folded: String = name
                    .to_uppercase()
                    .split_whitespace()
                    .collect::<Vec<_>>()
                    .join("");
                let identity = (physical_id.clone(), folded);
                if seen.contains(&identity) {
                    continue;
                }
                seen.insert(identity);
                output.push(json!({
                    "pin_no": physical_id,
                    "name": name,
                    "row_index": row_index,
                    "table": table,
                }));
            }
        }
    }
    output
}

fn semantic_role(header: &str, value: &str) -> Option<String> {
    if re(&format!("(?i){SUPPLY_DOMAIN_HEADER}")).is_match(header) {
        return Some("supply_domain".to_string());
    }
    if re(&format!("(?i){IO_STRUCTURE_HEADER}")).is_match(header) {
        return Some("type".to_string());
    }
    if re(&format!("(?i){DIRECTION_HEADER}")).is_match(header) {
        return Some("dir".to_string());
    }
    if re(&format!("(?i){TYPE_HEADER}")).is_match(header) {
        let normalized: String = value
            .to_lowercase()
            .chars()
            .filter(char::is_ascii_alphanumeric)
            .collect();
        return Some(
            if DIRECTION_CODES.contains(&normalized.as_str()) {
                "dir"
            } else {
                "type"
            }
            .to_string(),
        );
    }
    if re(&format!("(?i){FUNCTION_HEADER}")).is_match(header)
        || re(&format!("(?i){DESCRIPTION_HEADER}")).is_match(header)
    {
        return Some("functions".to_string());
    }
    None
}

/// Field-aligned, verbatim semantics for one printed pin row.
#[must_use]
#[allow(
    clippy::too_many_lines,
    clippy::indexing_slicing,
    clippy::needless_range_loop,
    clippy::missing_panics_doc,
    reason = "faithful port of pin_semantic_values; indexes as the reference does"
)]
pub fn pin_semantic_values(
    table: &Value,
    row_index: usize,
    pin_no: Option<&Value>,
    name: Option<&Value>,
) -> BTreeMap<String, Vec<String>> {
    let mut values: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for key in ["type", "dir", "supply_domain", "functions"] {
        values.insert(key.to_string(), Vec::new());
    }
    let rows = table.get("rows").and_then(Value::as_array);
    let headers = pin_header_cells(table);
    let Some(rows) = rows else {
        return values;
    };
    let Some(row) = rows
        .get(row_index)
        .and_then(Value::as_array)
        .filter(|_| !headers.is_empty())
    else {
        return values;
    };
    let wanted_name = normalized_cell(name);
    let wanted_number = normalized_cell(pin_no);
    let name_columns: Vec<usize> = row
        .iter()
        .enumerate()
        .filter(|(_, value)| normalized_cell(Some(value)) == wanted_name)
        .map(|(index, _)| index)
        .collect();
    let number_columns: Vec<usize> = row
        .iter()
        .enumerate()
        .filter(|(_, value)| {
            wanted_number.is_empty()
                || normalized_cell(Some(value)) == wanted_number
                || re(r"[,;\s]+").split(&cell(Some(value))).any(|part| {
                    normalized_cell(Some(&Value::String(part.to_string()))) == wanted_number
                })
        })
        .map(|(index, _)| index)
        .collect();
    if name_columns.is_empty() || number_columns.is_empty() {
        return values;
    }
    let name_column = name_columns[0];
    let number_column = *number_columns
        .iter()
        .min_by_key(|index| (**index).abs_diff(name_column))
        .unwrap_or(&0);
    let group_start = name_column.min(number_column);
    let later_name_headers: Vec<usize> = headers
        .iter()
        .enumerate()
        .filter(|(index, header)| {
            *index > name_column && re(&format!("(?i){NAME_HEADER}")).is_match(header)
        })
        .map(|(index, _)| index)
        .collect();
    let group_end = later_name_headers.first().copied().unwrap_or(row.len());
    for index in group_start..(group_end.min(row.len()).min(headers.len())) {
        if index == name_column || index == number_column {
            continue;
        }
        let value = cell(row.get(index));
        if value.is_empty() || NON_VALUE_MARKERS.contains(&value.to_lowercase().as_str()) {
            continue;
        }
        if let Some(role) = semantic_role(&headers[index], &value) {
            let list = values.entry(role).or_default();
            if !list.contains(&value) {
                list.push(value);
            }
        }
    }
    values
}

/// Semantic fields with at least one printed, non-value-filtered cell.
#[must_use]
#[allow(
    clippy::indexing_slicing,
    clippy::needless_range_loop,
    reason = "faithful port of pin_semantic_roles; indexes as the reference does"
)]
pub fn pin_semantic_roles(table: &Value) -> Vec<String> {
    let (headers, data_start) = pin_table_layout(table);
    let Some(rows) = table.get("rows").and_then(Value::as_array) else {
        return Vec::new();
    };
    if headers.is_empty() {
        return Vec::new();
    }
    let mut roles: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
    for row in rows.iter().skip(data_start) {
        let Some(row) = row.as_array() else {
            continue;
        };
        for index in 0..(row.len().min(headers.len())) {
            let value = cell(row.get(index));
            if value.is_empty() || NON_VALUE_MARKERS.contains(&value.to_lowercase().as_str()) {
                continue;
            }
            if let Some(role) = semantic_role(&headers[index], &value) {
                roles.insert(role);
            }
        }
    }
    roles.into_iter().collect()
}

/// Remove unselected package columns while preserving shared semantics.
#[must_use]
pub fn project_pin_table(table: &Value, selected_header: &str) -> Value {
    let Some(rows) = table.get("rows").and_then(Value::as_array) else {
        return table.clone();
    };
    let normalized_header = cell(Some(&Value::String(selected_header.to_string()))).to_lowercase();
    let matches: Vec<usize> = rows
        .iter()
        .take(4)
        .filter_map(|row| row.as_array())
        .flat_map(|row| {
            row.iter()
                .enumerate()
                .filter(|(_, value)| cell(Some(value)).to_lowercase() == normalized_header)
                .map(|(index, _)| index)
                .collect::<Vec<_>>()
        })
        .collect();
    let unique: std::collections::BTreeSet<usize> = matches.iter().copied().collect();
    if unique.len() != 1 {
        return table.clone();
    }
    let selected = *unique.iter().next().unwrap_or(&0);
    let width = rows
        .iter()
        .filter_map(|row| row.as_array())
        .map(Vec::len)
        .max()
        .unwrap_or(0);
    let package_columns: std::collections::BTreeSet<usize> = rows
        .iter()
        .take(4)
        .filter_map(|row| row.as_array())
        .flat_map(|row| {
            row.iter()
                .enumerate()
                .filter(|(_, value)| package_pin_count(&cell(Some(value))).is_some())
                .map(|(index, _)| index)
                .collect::<Vec<_>>()
        })
        .collect();
    let columns: Vec<usize> = if package_columns.contains(&selected) && package_columns.len() > 1 {
        (0..width)
            .filter(|column| *column == selected || !package_columns.contains(column))
            .collect()
    } else {
        (selected..=selected.saturating_add(1))
            .filter(|column| *column < width)
            .collect()
    };
    let mut out = table.as_object().cloned().unwrap_or_default();
    let projected_rows: Vec<Value> = rows
        .iter()
        .filter_map(|row| row.as_array())
        .map(|row| {
            json!(columns
                .iter()
                .map(|column| row.get(*column).cloned().unwrap_or(Value::Null))
                .collect::<Vec<_>>())
        })
        .collect();
    out.insert("rows".into(), json!(projected_rows));
    out.insert("projected_source_columns".into(), json!(columns));
    Value::Object(out)
}

/// Pin rows parsed from a table: printed identities only.
#[must_use]
#[allow(
    clippy::too_many_lines,
    clippy::indexing_slicing,
    clippy::needless_range_loop,
    clippy::missing_panics_doc,
    reason = "faithful port of parse_pin_table; indexes as the reference does"
)]
pub fn parse_pin_table(table: &Value, document_sha256: &str, page_1based: i64) -> Vec<Value> {
    let Some(rows) = table.get("rows").and_then(Value::as_array) else {
        return Vec::new();
    };
    if rows.len() < 2 {
        return Vec::new();
    }
    let header_candidates: Vec<(usize, &Vec<Value>)> = rows
        .iter()
        .take(6)
        .enumerate()
        .filter_map(|(index, row)| row.as_array().map(|row| (index, row)))
        .collect();
    if header_candidates.is_empty() {
        return Vec::new();
    }
    let patterns = [
        PIN_HEADER,
        NAME_HEADER,
        TYPE_HEADER,
        FUNCTION_OR_DESCRIPTION_HEADER,
    ];
    let Some((header_index, header)) = header_candidates
        .iter()
        .max_by_key(|(_, row)| header_score(row, &patterns))
        .copied()
    else {
        return Vec::new();
    };
    let name_index = column(header, NAME_HEADER);
    let Some(name_index) = name_index else {
        return Vec::new();
    };
    let mut package_by_column: BTreeMap<usize, String> = BTreeMap::new();
    for candidate_row in rows.iter().take(4.max(header_index + 1)) {
        let Some(candidate_row) = candidate_row.as_array() else {
            continue;
        };
        for (index, value) in candidate_row.iter().enumerate() {
            let text = cell(Some(value));
            if package_pin_count(&text).is_some() {
                package_by_column.insert(index, text);
            }
        }
    }
    let mut package_columns: Vec<(usize, String)> = package_by_column.into_iter().collect();
    let generic_pin_index = column(header, PIN_HEADER);
    if package_columns.is_empty() {
        if let Some(generic_pin_index) = generic_pin_index {
            package_columns.push((generic_pin_index, "UNRESOLVED".to_string()));
        }
    }
    if package_columns.is_empty() {
        return Vec::new();
    }
    let type_index = column(header, TYPE_HEADER);
    let direction_index = column(header, DIRECTION_HEADER);
    let function_index = column(header, FUNCTION_OR_DESCRIPTION_HEADER);
    let mut output = Vec::new();
    for (package_index, package) in &package_columns {
        for (row_index, row) in rows.iter().enumerate().skip(header_index + 1) {
            let Some(row) = row.as_array() else {
                continue;
            };
            let package_cell = value_at(row, Some(*package_index));
            let package_value = package_cell.as_ref().map(|s| Value::String(s.clone()));
            let physical_id = physical(package_value.as_ref());
            let name = value_at(row, Some(name_index));
            let (Some(physical_id), Some(name)) = (physical_id, name) else {
                continue;
            };
            if name.chars().count() > 160 {
                continue;
            }
            let functions = value_at(row, function_index);
            output.push(json!({
                "document_sha256": document_sha256,
                "page_1based": page_1based,
                "table_index": table.get("table_index").cloned().unwrap_or(Value::Null),
                "row_index": row_index,
                "package": package,
                "expected_package_pins": package_pin_count(package),
                "pin_no": physical_id,
                "name": name,
                "type": value_at(row, type_index),
                "direction": value_at(row, direction_index),
                "functions_verbatim": functions,
                "method": "pymupdf_deterministic_table",
            }));
        }
    }
    output
}

/// Parametric rows parsed from a table: every printed column kept verbatim.
#[must_use]
#[allow(
    clippy::indexing_slicing,
    clippy::needless_range_loop,
    reason = "faithful port of parse_parametric_table; indexes as the reference does"
)]
pub fn parse_parametric_table(
    table: &Value,
    document_sha256: &str,
    page_1based: i64,
) -> Vec<Value> {
    let Some(rows) = table.get("rows").and_then(Value::as_array) else {
        return Vec::new();
    };
    if rows.len() < 2 {
        return Vec::new();
    }
    let header_candidates: Vec<(usize, &Vec<Value>)> = rows
        .iter()
        .take(6)
        .enumerate()
        .filter_map(|(index, row)| row.as_array().map(|row| (index, row)))
        .collect();
    if header_candidates.is_empty() {
        return Vec::new();
    }
    let Some((header_index, header)) = header_candidates
        .iter()
        .max_by_key(|(_, row)| header_score(row, &[PARAMETER_HEADER, VALUE_HEADER]))
        .copied()
    else {
        return Vec::new();
    };
    if header_score(header, &[PARAMETER_HEADER]) < 1 {
        return Vec::new();
    }
    if header_score(header, &[VALUE_HEADER]) < 2 {
        return Vec::new();
    }
    let names: Vec<String> = header
        .iter()
        .enumerate()
        .map(|(index, value)| {
            let text = cell(Some(value)).to_lowercase().replace(' ', "_");
            if text.is_empty() {
                format!("column_{index}")
            } else {
                text
            }
        })
        .collect();
    let mut output = Vec::new();
    for (row_index, row) in rows.iter().enumerate().skip(header_index + 1) {
        let Some(row) = row.as_array() else {
            continue;
        };
        let mut values = Map::new();
        for (index, value) in row.iter().take(names.len()).enumerate() {
            let text = cell(Some(value));
            if !text.is_empty() {
                values.insert(names[index].clone(), json!(text));
            }
        }
        if values.len() < 2 {
            continue;
        }
        output.push(json!({
            "document_sha256": document_sha256,
            "page_1based": page_1based,
            "table_index": table.get("table_index").cloned().unwrap_or(Value::Null),
            "row_index": row_index,
            "values_verbatim": Value::Object(values),
            "method": "pymupdf_deterministic_table",
        }));
    }
    output
}

fn header_key(value: Option<&Value>) -> String {
    let text = cell(value).to_lowercase();
    let mut out: String = text
        .chars()
        .map(|c| if c.is_ascii_alphanumeric() { c } else { '_' })
        .collect();
    while out.starts_with('_') {
        out.remove(0);
    }
    while out.ends_with('_') {
        out.pop();
    }
    out
}

/// Expand one verbatim table row into value-level, conversion-free facts.
/// `key_order` is the reference's insertion order of `values_verbatim`
/// (the gold wire sorts object keys, so the order travels separately).
#[must_use]
pub fn normalize_parametric_facts(row: &Value, key_order: &[String]) -> Vec<Value> {
    let raw = match row.get("values_verbatim") {
        Some(Value::Object(map)) => map.clone(),
        Some(_) => return Vec::new(),
        None => match row.as_object() {
            Some(map) => map.clone(),
            None => return Vec::new(),
        },
    };
    // Insertion order matters: the reference emits facts in `values` order.
    let mut values: Vec<(String, String)> = Vec::new();
    for key in key_order {
        let Some(value) = raw.get(key) else {
            continue;
        };
        let text = cell(Some(value));
        if !text.is_empty() {
            let key = header_key(Some(&Value::String(key.clone())));
            if !values.iter().any(|(k, _)| *k == key) {
                values.push((key, text));
            }
        }
    }
    let get = |name: &str| -> Option<String> {
        values
            .iter()
            .find(|(k, _)| k == name)
            .map(|(_, v)| v.clone())
    };
    if values.is_empty() {
        return Vec::new();
    }
    let field_key = [
        "characteristic",
        "description",
        "parameter_name",
        "column_1",
        "parameter",
        "symbol",
    ]
    .iter()
    .find(|key| get(key).is_some_and(|v| !v.is_empty()))
    .map(|s| (*s).to_string());
    let Some(field_key) = field_key else {
        return Vec::new();
    };
    let unit_key = ["unit", "units"]
        .iter()
        .find(|key| get(key).is_some_and(|v| !v.is_empty()))
        .map(|s| (*s).to_string());
    let role_keys: Vec<String> = PARAMETRIC_VALUE_ROLES
        .iter()
        .map(|(key, _)| (*key).to_string())
        .collect();
    let mut ignored: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
    ignored.insert(field_key.clone());
    if let Some(unit_key) = &unit_key {
        ignored.insert(unit_key.clone());
    }
    for key in &role_keys {
        ignored.insert(key.clone());
    }
    let conditions: Map<String, Value> = values
        .iter()
        .filter(|(key, _)| !ignored.contains(key))
        .map(|(key, value)| (key.clone(), json!(value)))
        .collect();
    values
        .iter()
        .filter_map(|(key, value)| {
            let role = PARAMETRIC_VALUE_ROLES
                .iter()
                .find(|(k, _)| *k == key.as_str())
                .map(|(_, role)| *role)?;
            if NON_VALUE_MARKERS.contains(&value.trim().to_lowercase().as_str()) {
                return None;
            }
            Some(json!({
                "field": get(&field_key).unwrap_or_default(),
                "value": value,
                "value_role": role,
                "unit": unit_key.as_ref().map_or(Value::Null, |k| json!(get(k).unwrap_or_default())),
                "conditions": conditions,
            }))
        })
        .collect()
}
