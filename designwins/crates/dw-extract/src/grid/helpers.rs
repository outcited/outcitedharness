//! Regex and text utilities for the grid port.
//!
//! Python `re` semantics notes (docs/HANDOFF.md §6): `re.I` becomes a leading
//! `(?i)`; lookaround patterns use `fancy-regex` (`find` not `search`,
//! `captures` for groups, `is_match` -> `Result`); Python `Pattern.match`
//! anchors at 0 — expressed as `^...`. String lengths and slices Python
//! computes in code points are `chars()` here, never byte indexing.

use std::collections::BTreeSet;

use serde_json::Value;

use super::tables::{
    ACRONYM_PLURAL, ALT_LIST, COMMA_LIST, NON_SCOPE, PACKAGE_OR_TEMP, POWER_FACT, RANGE,
    STOP_TOKENS, VARIES, VENDOR_DOMAIN, VENDOR_HINTS,
};

/// `_norm_key` — lowercase letters only, words separated by single spaces.
#[must_use]
pub fn norm_key(text: Option<&str>) -> String {
    let lowered = text.unwrap_or("").to_lowercase();
    let spaced: String = lowered
        .chars()
        .map(|c| if c.is_ascii_alphabetic() { c } else { ' ' })
        .collect();
    spaced.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// `varies_by_part` — range, list or hedge in the text.
#[must_use]
pub fn varies_by_part(text: &str) -> bool {
    is_match(RANGE, text)
        || is_match(COMMA_LIST, text)
        || is_match(ALT_LIST, text)
        || is_match(VARIES, text)
}

/// `_label_key` — one key for "UART"/"UARTs" and case/hyphen variants.
#[must_use]
pub fn label_key(label: &str) -> String {
    // Case-sensitive in the reference: acronym plurals only. "timers" stays
    // distinct from "timer" as printed.
    let unplural = sub_all(ACRONYM_PLURAL, label, "$1");
    let lowered = unplural.to_lowercase();
    let stripped: String = lowered
        .chars()
        .filter(|c| !matches!(c, '®' | '™' | '©'))
        .collect();
    let spaced = sub_all(r"[\s\-–_]+", &stripped, " ");
    let filtered: String = spaced
        .chars()
        .filter(|c| {
            c.is_ascii_lowercase()
                || c.is_ascii_digit()
                || matches!(c, ' ' | '.' | '/' | '(' | ')' | '+')
        })
        .collect();
    filtered.trim().to_string()
}

/// `_tokens` — label words minus stop tokens.
pub(super) fn tokens(label: &str) -> BTreeSet<String> {
    let key = label_key(label);
    let mut out = BTreeSet::new();
    for token in split_non_alnum_dot(&key) {
        if !token.is_empty() && !STOP_TOKENS.contains(&token.as_str()) {
            out.insert(token);
        }
    }
    out
}

fn split_non_alnum_dot(text: &str) -> Vec<String> {
    let mut parts = Vec::new();
    let mut current = String::new();
    for c in text.chars() {
        if c.is_ascii_alphanumeric() || c == '.' {
            current.push(c);
        } else if !current.is_empty() {
            parts.push(std::mem::take(&mut current));
        }
    }
    if !current.is_empty() {
        parts.push(current);
    }
    parts
}

/// `_grid_eligible` — short enough to render and not a sentence fragment.
#[must_use]
pub fn grid_eligible(text: &str) -> bool {
    text.chars().count() <= 72
        && !is_match(super::tables::SENTENCE_START, text)
        && !text.trim_end().ends_with([':', ',', ';'])
        && !text.ends_with('.')
}

/// `normalize_vendor` — name to domain; dotted input is already canonical.
#[must_use]
pub fn normalize_vendor(vendor: Option<&str>) -> Option<String> {
    let raw = vendor?.trim();
    if raw.is_empty() {
        return None;
    }
    let key = raw.to_lowercase();
    if key.contains('.') {
        return Some(key);
    }
    Some(
        VENDOR_DOMAIN
            .iter()
            .find(|(k, _)| *k == key)
            .map_or_else(|| key.clone(), |(_, d)| (*d).to_string()),
    )
}

/// `infer_vendor` — probe document id, scope and filename against the hints.
#[must_use]
pub fn infer_vendor(document_id: Option<&str>, scope: &str, artifact: &str) -> Option<String> {
    let probes = [
        document_id.unwrap_or("").to_string(),
        scope.split(',').next().unwrap_or("").trim().to_string(),
        artifact.split('.').next().unwrap_or("").to_string(),
    ];
    for probe in &probes {
        if probe.is_empty() {
            continue;
        }
        for (pattern, domain) in VENDOR_HINTS {
            if is_match(pattern, probe) {
                return Some((*domain).to_string());
            }
        }
    }
    None
}

/// `_vendor_stem` — "SPI1, SPI2" -> "SPI"; over four stems the class label.
#[must_use]
pub fn vendor_stem(names: &[String], icls: &str) -> String {
    let mut stems: Vec<String> = Vec::new();
    for name in names {
        let stem = sub_all(super::tables::STEM_STRIP, name, "");
        if !stems.contains(&stem) {
            stems.push(stem);
        }
    }
    if stems.len() <= 4 {
        stems.join(" / ")
    } else {
        icls.to_string()
    }
}

/// `json_value` — comparison key for claim values.
///
/// The reference keys with `json.dumps(value, sort_keys=True)` (spaces after
/// separators); this uses the crate's compact canonical encoding. The bytes
/// differ; the *equality semantics* are identical (equal Python keys iff
/// equal Rust keys), which is all `_dedupe` / `_prefer_specific` consume.
#[must_use]
pub fn json_value(value: &Value) -> String {
    crate::canonical::canonical_json(value)
        .map(|b| String::from_utf8_lossy(&b).to_string())
        .unwrap_or_default()
}

/// `_package_or_temp` probe.
pub(super) fn package_or_temp(text: &str) -> bool {
    is_match(PACKAGE_OR_TEMP, text)
}

/// `_power_fact` probe.
pub(super) fn power_fact(text: &str) -> bool {
    is_match(POWER_FACT, text)
}

/// `_NON_SCOPE` test (Python `Pattern.match`, i.e. anchored at 0).
pub(super) fn non_scope(text: &str) -> bool {
    is_match(NON_SCOPE, text)
}

// ------------------------------------------------------------------ regex

fn re(pattern: &str) -> Option<fancy_regex::Regex> {
    fancy_regex::Regex::new(pattern).ok()
}

/// `Pattern.search` semantics: a match anywhere in `text`.
pub(super) fn is_match(pattern: &str, text: &str) -> bool {
    re(pattern).is_some_and(|r| r.is_match(text).unwrap_or(false))
}

/// First match text.
pub(super) fn search(pattern: &str, text: &str) -> Option<String> {
    re(pattern)
        .and_then(|r| r.find(text).ok().flatten())
        .map(|m| m.as_str().to_string())
}

/// All non-overlapping matches.
pub(super) fn find_all(pattern: &str, text: &str) -> Vec<String> {
    re(pattern)
        .map(|r| {
            r.find_iter(text)
                .filter_map(Result::ok)
                .map(|m| m.as_str().to_string())
                .collect()
        })
        .unwrap_or_default()
}

/// `re.captures` — index 0 is the whole match, then group 1..n.
pub(super) fn captures(pattern: &str, text: &str) -> Option<Vec<Option<String>>> {
    let caps = re(pattern)?.captures(text).ok().flatten()?;
    Some(
        (0..caps.len())
            .map(|i| caps.get(i).map(|m| m.as_str().to_string()))
            .collect(),
    )
}

/// `re.sub(..., flags=re.A)` over all matches; `$1` group refs in
/// `replacement` (regex-crate syntax — the reference writes `\1`).
pub(super) fn sub_all(pattern: &str, text: &str, replacement: &str) -> String {
    re(pattern).map_or_else(
        || text.to_string(),
        |r| r.replace_all(text, replacement).to_string(),
    )
}

/// `re.escape` — escapes only regex metacharacters, as Python 3.7+ does.
pub(super) fn regex_escape(text: &str) -> String {
    let mut out = String::new();
    for c in text.chars() {
        if matches!(
            c,
            '(' | ')'
                | '['
                | ']'
                | '{'
                | '}'
                | '?'
                | '*'
                | '+'
                | '-'
                | '|'
                | '^'
                | '$'
                | '.'
                | '&'
                | '~'
                | '#'
                | ' '
        ) {
            out.push('\\');
        }
        out.push(c);
    }
    out
}

// ------------------------------------------------------------------- misc

/// JSON number -> i64 (page numbers and counts).
///
/// # Lints
///
/// The `f64 -> i64` narrowing is intentional: page/limit counts in records
/// may parse as floats (`5.0`) and the reference's `int()` truncates too.
#[allow(clippy::cast_possible_truncation)]
pub(super) fn num_as_i64(v: &Value) -> Option<i64> {
    v.as_i64().or_else(|| v.as_f64().map(|f| f as i64))
}

/// `str(value)` for the scalar shapes the reference stringifies.
pub(super) fn py_str(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => b.to_string(),
        Value::Number(n) => n.to_string(),
        other => other.to_string(),
    }
}

pub(super) fn set_key(row: &mut Value, key: &str, value: Value) {
    if let Some(map) = row.as_object_mut() {
        map.insert(key.to_string(), value);
    }
}

pub(super) fn arr_empty(v: &Value) -> bool {
    v.as_array().is_none_or(Vec::is_empty)
}
