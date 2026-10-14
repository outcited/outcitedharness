//! Model response parsing (`parse_json_response` in `local_model.py`).
//!
//! Strips Markdown code fences, finds the first `{`, and parses one JSON
//! object from there — trailing text is ignored, matching `json.JSONDecoder
//! .raw_decode`.

use regex::Regex;
use serde_json::Value;
use std::sync::OnceLock;

/// Parse a model response into a JSON object.
///
/// # Errors
///
/// [`ParseError`] when there is no JSON object, or when the parsed value
/// is not an object — matching the reference's `ValueError` strings.
pub fn parse_json_response(text: &str) -> Result<Value, ParseError> {
    let stripped = strip_fences(text.trim());
    let start = stripped
        .find('{')
        .ok_or(ParseError("model response has no JSON object".to_string()))?;
    let after = &stripped[start..];
    let value = raw_decode(after).map_err(|e| ParseError(e.to_string()))?;
    if !value.is_object() {
        return Err(ParseError(
            "model response is not a JSON object".to_string(),
        ));
    }
    Ok(value)
}

/// `re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)` then
/// `re.sub(r"\s*```$", "", s)`.
// The two patterns are compile-time constants with no capture groups; a
// regex compile failure here is a developer error, not a runtime condition.
#[allow(
    clippy::expect_used,
    reason = "regex patterns are compile-time constants"
)]
fn strip_fences(s: &str) -> String {
    static OPEN: OnceLock<Regex> = OnceLock::new();
    static CLOSE: OnceLock<Regex> = OnceLock::new();
    let open = OPEN.get_or_init(|| Regex::new(r"(?i)^```(?:json)?\s*").expect("valid regex"));
    let close = CLOSE.get_or_init(|| Regex::new(r"\s*```$").expect("valid regex"));
    let s = open.replace(s, "");
    close.replace(&s, "").into_owned()
}

/// `json.JSONDecoder().raw_decode(s)` — parse the first JSON value,
/// ignoring trailing text.
fn raw_decode(s: &str) -> Result<Value, serde_json::Error> {
    serde_json::Deserializer::from_str(s)
        .into_iter::<Value>()
        .next()
        .unwrap_or_else(|| {
            Err(serde_json::Error::io(std::io::Error::new(
                std::io::ErrorKind::UnexpectedEof,
                "EOF while parsing a value",
            )))
        })
}

/// A parse failure carrying the reference's message.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{0}")]
pub struct ParseError(String);

impl ParseError {
    /// The error message.
    #[must_use]
    pub fn message(&self) -> &str {
        &self.0
    }
}
