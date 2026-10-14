//! Schema validation (`validate_response` in `local_model.py`).
//!
//! A strict, recursive walk over a JSON-Schema-style `schema` dict checking
//! type matches, required keys, and nested property/item structure. Error
//! messages are byte-compatible with the reference's `ValueError` strings.

use serde_json::Value;
use std::fmt::Write as _;

/// `validate_response(value, schema, path)` — the reference's schema validator.
///
/// # Errors
///
/// [`ValidateError`] carrying the reference's exact error message
/// (`"$: expected object"`, `"$.pins: missing ['name']"`, etc.).
pub fn validate_response(value: &Value, schema: &Value, path: &str) -> Result<(), ValidateError> {
    // Type check (str or list-of-str).
    if let Some(expected) = schema.get("type") {
        match expected {
            Value::String(s) => {
                if !type_matches(value, s) {
                    return Err(ValidateError(format!("{path}: expected {s}")));
                }
            }
            Value::Array(list) => {
                let any_match = list
                    .iter()
                    .any(|item| item.as_str().is_some_and(|s| type_matches(value, s)));
                if !any_match {
                    return Err(ValidateError(format!("{path}: unexpected value type")));
                }
            }
            _ => {}
        }
    }

    // Required keys + recursive properties.
    if let Value::Object(map) = value {
        if let Some(required) = schema.get("required").and_then(Value::as_array) {
            let missing: Vec<String> = required
                .iter()
                .filter_map(Value::as_str)
                .filter(|k| !map.contains_key(*k))
                .map(String::from)
                .collect();
            if !missing.is_empty() {
                let list = py_repr_string_list(&missing);
                return Err(ValidateError(format!("{path}: missing {list}")));
            }
        }
        if let Some(properties) = schema.get("properties").and_then(Value::as_object) {
            for (key, child) in map {
                if let Some(child_schema) = properties.get(key) {
                    if child_schema.is_object() {
                        validate_response(child, child_schema, &format!("{path}.{key}"))?;
                    }
                }
            }
        }
    }

    // Array items.
    if let (Value::Array(list), Some(item_schema)) = (value, schema.get("items")) {
        if item_schema.is_object() {
            for (index, child) in list.iter().enumerate() {
                validate_response(child, item_schema, &format!("{path}[{index}]"))?;
            }
        }
    }

    Ok(())
}

/// `_type_matches(value, expected)` — Python's type predicate.
fn type_matches(value: &Value, expected: &str) -> bool {
    match expected {
        "object" => value.is_object(),
        "array" => value.is_array(),
        "string" => value.is_string(),
        "integer" => value.as_number().is_some_and(|n| n.is_i64() || n.is_u64()),
        "number" => value.is_number(),
        "boolean" => value.is_boolean(),
        "null" => value.is_null(),
        _ => true,
    }
}

/// A validation failure carrying the reference's message.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{0}")]
pub struct ValidateError(String);

impl ValidateError {
    /// The error message.
    #[must_use]
    pub fn message(&self) -> &str {
        &self.0
    }
}

/// Format a `missing` list the way Python's `repr` does for string lists.
///
/// Python prints `["a", "b"]` with single quotes: `['a', 'b']`.
#[must_use]
fn py_repr_string_list(items: &[String]) -> String {
    let mut out = String::from("[");
    for (i, s) in items.iter().enumerate() {
        if i > 0 {
            out.push_str(", ");
        }
        let _ = write!(out, "'{s}'");
    }
    out.push(']');
    out
}
