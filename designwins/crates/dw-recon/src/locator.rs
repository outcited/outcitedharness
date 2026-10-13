//! Fail-closed pin-definition table localization for technical datasheets
//! (`harness/electronics/locator.py`, ported 1:1).
//!
//! The locator selects definition tables, never package drawings or ballout
//! figures. Ambiguous documents are withheld with a machine-readable reason.
//! The `find_tables()` layer is PDF-layer: the port receives the same row
//! grids the reference extracts, and the TOC travels alongside them.

use std::collections::BTreeSet;

use regex::Regex;
use serde_json::Value;

/// JEDEC id cell: letter + 1..2 digits.
const JEDEC: &str = r"^[A-HJ-NP-WY]\d{1,2}$";

/// Package family tokens `PACKAGE_KEY` recognises (reference order).
const PACKAGE_FAMILIES: &str = r"TFBGA|UFBGA|LFBGA|XFBGA|FBGA|BGA|WLCSP|DSBGA|CABGA|CSBGA|LQFP|TQFP|QFP|VFQFPN|UFQFPN|VQFN|WQFN|TQFN|UTQFN|XQFN|QFN|DFN|LGA|WLP|CSP|TSSOP|SSOP|SOIC|SPDIP|PDIP";

/// Family-then-count or count-then-family package tokens.
const PACKAGE_KEY: &str = concat!(
    r"(?i)\b(",
    r"TFBGA|UFBGA|LFBGA|XFBGA|FBGA|BGA|WLCSP|DSBGA|CABGA|CSBGA|",
    r"LQFP|TQFP|QFP|VFQFPN|UFQFPN|VQFN|WQFN|TQFN|UTQFN|",
    r"XQFN|QFN|DFN|LGA|WLP|CSP|",
    r"TSSOP|SSOP|SOIC|SPDIP|PDIP",
    r")\s*[- ]?\s*(\d{1,4})\b",
    r"|\b(\d{1,4})\s*(?:[- ]\s*)?(?:PIN|BALL)?\s*",
    r"(",
    r"TFBGA|UFBGA|LFBGA|XFBGA|FBGA|BGA|WLCSP|DSBGA|CABGA|CSBGA|",
    r"LQFP|TQFP|QFP|VFQFPN|UFQFPN|VQFN|WQFN|TQFN|UTQFN|",
    r"XQFN|QFN|DFN|LGA|WLP|CSP|",
    r"TSSOP|SSOP|SOIC|SPDIP|PDIP",
    r")\b",
);

/// Titles naming a pin section (vendor conventions beyond ST).
const PIN_SECTION_TITLES: &[&str] = &[
    "PIN DESCRIPTIONS",
    "PIN DESCRIPTION",
    "PINOUTS AND PIN DESCRIPTION",
    "PINOUT, PIN DESCRIPTION",
    "PINOUTS/BALLOUTS, PIN DESCRIPTION",
    "PINOUT/BALLOUT SCHEMATICS",
    "PIN CONFIGURATION AND FUNCTIONS",
    "PINNING INFORMATION",
    "PIN ASSIGNMENT",
    "PIN FUNCTIONS",
    "PIN DIAGRAMS",
    "PINOUT DESCRIPTION",
    "PIN ALLOCATION TABLE",
    "PIN CONFIGURATION",
    "PIN DEFINITIONS",
];

/// Titles that end a pin section.
const SECTION_STOPS: &[&str] = &[
    "ELECTRICAL",
    "PACKAGE INFORMATION",
    "MEMORY MAPPING",
    "FUNCTIONAL OVERVIEW",
    "FUNCTIONAL DESCRIPTION",
    "LIST OF TABLES",
    "LIST OF FIGURES",
    "REVISION HISTORY",
];

#[allow(
    clippy::expect_used,
    reason = "patterns are literals from the reference"
)]
fn re(src: &str) -> Regex {
    Regex::new(src).expect("pattern is a literal from the reference")
}

fn jedec_re() -> &'static Regex {
    use std::sync::OnceLock;
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(JEDEC))
}

fn package_key_re() -> &'static Regex {
    use std::sync::OnceLock;
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(PACKAGE_KEY))
}

/// A TOC row.
#[derive(Debug, Clone)]
pub struct TocEntry {
    /// Bookmark level.
    pub level: i64,
    /// Bookmarked title, stripped.
    pub title: String,
    /// 1-based target page.
    pub page: i64,
}

/// One page of the PDF layer's output: text and extracted table grids.
pub struct PageInput<'a> {
    /// Page text (`get_text("text")`).
    pub text: &'a str,
    /// `find_tables().tables[i].extract()` grids, `None` when `find_tables`
    /// returned nothing.
    pub tables: Option<&'a [Vec<Vec<Option<String>>>]>,
}

/// The locator's verdict for one request.
#[derive(Debug, Clone)]
pub struct LocateResult {
    /// The document digest the caller supplied.
    pub document_id: String,
    /// The package the caller asked for.
    pub requested_package: String,
    /// The pin count the caller attested.
    pub expected_package_pins: i64,
    /// The source path the caller supplied.
    pub source_path: String,
    /// `send` or `withhold`.
    pub status: String,
    /// The machine-readable decision reason.
    pub reason: String,
    /// Which TOC class located the section.
    pub toc_class: Option<String>,
    /// The locating TOC title.
    pub toc_title: Option<String>,
    /// 1-based pages to send.
    pub pages_1based: Vec<i64>,
    /// The selected package column header.
    pub column_header: Option<String>,
    /// Every package column header seen.
    pub column_headers_all: Vec<String>,
    /// Distinct JEDEC ids in the selected column.
    pub corroborating_jedec_ids: Option<i64>,
}

/// `classify_title` buckets.
#[must_use]
pub fn classify_title(title: &str) -> Option<String> {
    let normalized: String = (title)
        .to_uppercase()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    if ["NRST PIN", "PIN INPUT", "PIN LOADING", "PIN CHARACTERISTIC"]
        .iter()
        .any(|marker| normalized.contains(marker))
    {
        return Some("electrical".to_string());
    }
    if [
        "PACKAGE INFORMATION",
        "OUTLINE",
        "MECHANICAL",
        "FOOTPRINT",
        "MARKING",
        "THERMAL",
    ]
    .iter()
    .any(|marker| normalized.contains(marker))
    {
        return Some("outline".to_string());
    }
    if normalized.contains("ALTERNATE FUNCTION") {
        return Some("af".to_string());
    }
    let is_table = normalized.starts_with("TABLE") || format!(" {normalized} ").contains(" TABLE ");
    let is_figure = normalized.starts_with("FIGURE") || normalized.starts_with("FIG.");
    let definition = [
        "PIN AND BALL",
        "PIN/BALL",
        "PIN DEFINITION",
        "BALL DEFINITION",
        "PIN DEFINITIONS",
        "BALL DEFINITIONS",
        "PIN AND BALL DESCRIPTION",
    ]
    .iter()
    .any(|marker| normalized.contains(marker));
    if is_table && definition {
        return Some(
            if normalized.contains("FMC PIN") {
                "other_table"
            } else {
                "definition_table"
            }
            .to_string(),
        );
    }
    if is_table
        && re(r"\bPIN DEFINITIONS?\b").is_match(&normalized)
        && !normalized.contains("ALTERNATE")
    {
        return Some("definition_table".to_string());
    }
    if is_figure && normalized.contains("BALLOUT") {
        return Some("ballout_figure".to_string());
    }
    if is_figure && normalized.contains("PINOUT") {
        return Some("pinout_figure".to_string());
    }
    if PIN_SECTION_TITLES
        .iter()
        .any(|marker| normalized.contains(marker))
        || re(r"\bPIN(OUTS)? AND PIN DESCRIPTION\b").is_match(&normalized)
    {
        return Some("pin_section".to_string());
    }
    None
}

/// Whitespace-collapsed cell text; `None` cells count as empty.
#[must_use]
pub fn cell(value: Option<&str>) -> String {
    value
        .unwrap_or("")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

/// First `limit` rows flattened into one uppercased blob.
fn table_blob(rows: &[Vec<Option<String>>], limit: usize) -> String {
    rows.iter()
        .take(limit)
        .flat_map(|row| row.iter().map(|c| cell(c.as_deref()).to_uppercase()))
        .collect::<Vec<_>>()
        .join(" | ")
}

fn is_legend(rows: &[Vec<Option<String>>]) -> bool {
    let blob = table_blob(rows, 3);
    ["ABBREVIATION", "DEFINITION", "PIN NAME"]
        .iter()
        .all(|marker| blob.contains(marker))
}

fn is_alternate_function_table(rows: &[Vec<Option<String>>]) -> bool {
    let blob = table_blob(rows, 3);
    re(r"\bAF0\b").is_match(&blob)
        || blob.contains("PORT A ALTERNATE")
        || (blob.contains("ALTERNATE FUNCTION") && package_key_re().find(&blob).is_none())
}

/// Package column headers in the first four rows.
#[must_use]
pub fn package_headers(rows: &[Vec<Option<String>>]) -> Vec<String> {
    let mut headers: Vec<String> = Vec::new();
    for row in rows.iter().take(4) {
        for value in row {
            let candidate = cell(value.as_deref());
            if candidate.is_empty()
                || candidate.chars().count() > 48
                || package_key_re().find(&candidate).is_none()
            {
                continue;
            }
            if !headers.contains(&candidate) {
                headers.push(candidate);
            }
        }
    }
    headers
}

/// A structural pin-definition table: package headers plus pin-name markers.
#[must_use]
pub fn is_definition_table(rows: &[Vec<Option<String>>]) -> bool {
    if rows.is_empty() || is_legend(rows) || is_alternate_function_table(rows) {
        return false;
    }
    if package_headers(rows).is_empty() {
        return false;
    }
    let blob = table_blob(rows, usize::MAX);
    [
        "PIN NAME",
        "PIN/BALL NAME",
        "PIN NUMBER",
        "PIN/BALL",
        "FUNCTION AFTER RESET",
    ]
    .iter()
    .any(|marker| blob.contains(marker))
}

/// The page's definition-table grids.
#[must_use]
pub fn definition_tables(page: &PageInput<'_>) -> Vec<Vec<Vec<Option<String>>>> {
    page.tables
        .into_iter()
        .flatten()
        .filter(|rows| is_definition_table(rows))
        .cloned()
        .collect()
}

/// `(family, count, smps)` identity of a printed package token.
#[must_use]
pub fn package_identity(value: &str) -> Option<(String, String, bool)> {
    let normalized = value
        .to_uppercase()
        .replace("WITH SMPS", "SMPS")
        .replace('_', " ");
    let caps = package_key_re().captures(&normalized)?;
    let family = caps
        .get(1)
        .or_else(|| caps.get(4))
        .map(|m| m.as_str().to_uppercase())?;
    let count = caps
        .get(2)
        .or_else(|| caps.get(3))
        .map(|m| m.as_str().to_string())?;
    Some((family, count, normalized.contains("SMPS")))
}

/// The printed pin count of a package token.
#[must_use]
pub fn package_pin_count(value: &str) -> Option<i64> {
    package_identity(value).and_then(|(_, count, _)| count.parse::<i64>().ok())
}

/// The header whose identity matches the request exactly (never a cousin).
#[must_use]
pub fn match_package_column(requested: &str, headers: &[String]) -> Option<String> {
    let requested_identity = package_identity(requested)?;
    let requested_text = cell(Some(requested)).to_uppercase();
    let (family, count, requested_smps) = &requested_identity;
    let mut matches: Vec<(i64, String)> = Vec::new();
    for header in headers {
        let Some(identity) = package_identity(header) else {
            continue;
        };
        if (&identity.0, &identity.1) != (family, count) {
            continue;
        }
        #[allow(
            clippy::bool_to_int_with_if,
            reason = "the reference scores with int(bool)"
        )]
        let score = 2
            + i64::from(identity.2 == *requested_smps)
            + i64::from(!identity.2 && !*requested_smps);
        matches.push((score, header.clone()));
    }
    if matches.is_empty() {
        return None;
    }
    let best_score = matches.iter().map(|(s, _)| *s).max()?;
    let best: Vec<&String> = matches
        .iter()
        .filter(|(s, _)| *s == best_score)
        .map(|(_, h)| h)
        .collect();
    if best.len() == 1 {
        return best.first().map(|h| (*h).clone());
    }
    let exact: Vec<&&String> = best
        .iter()
        .filter(|h| h.to_uppercase() == requested_text)
        .collect();
    let canonical = format!(
        "{family}{count}{}",
        if *requested_smps { "SMPS" } else { "" }
    );
    let requested_key: String = requested_text
        .chars()
        .filter(|c| c.is_ascii_uppercase() || c.is_ascii_digit())
        .collect();
    if requested_key != canonical && exact.len() == 1 {
        return exact.first().map(|h| (**h).clone());
    }
    None
}

/// Validate physical pin rows: unique identity, LQFP numeric range gate.
///
/// # Errors
///
/// Returns the reference's `ValueError` message verbatim.
pub fn validate_physical_pin_truth(
    pins: &[Value],
    package: &str,
    expected_package_pins: i64,
) -> Result<(), String> {
    if pins.is_empty() {
        return Err("physical pins must be a non-empty list".to_string());
    }
    let mut identifiers: Vec<String> = Vec::new();
    let mut records: Vec<(String, String)> = Vec::new();
    for row in pins {
        if !row.is_object() {
            return Err("physical pin row must be an object".to_string());
        }
        let raw_number = row.get("pin_no");
        let name = row.get("name").and_then(Value::as_str);
        let number_ok = matches!(raw_number, Some(Value::String(_) | Value::Number(_)));
        if !number_ok || name.is_none_or(|n| n.trim().is_empty()) {
            return Err("physical pin row has an invalid identity".to_string());
        }
        let raw = match raw_number {
            Some(Value::String(s)) => s.clone(),
            Some(Value::Number(n)) => {
                if n.is_u64() || n.is_i64() {
                    n.to_string()
                } else {
                    format!("{:?}", n.as_f64().unwrap_or_default())
                }
            }
            _ => String::new(),
        };
        let mut identifier: String = raw
            .to_uppercase()
            .split_whitespace()
            .collect::<Vec<_>>()
            .join("");
        let stripped = re(r"\(\d+\)$").replace_all(&identifier, "").into_owned();
        identifier = stripped;
        if identifier.is_empty() {
            return Err("physical pin row has an empty identifier".to_string());
        }
        identifiers.push(identifier.clone());
        let name = name.unwrap_or_default();
        let folded: String = name
            .to_uppercase()
            .chars()
            .filter(char::is_ascii_alphanumeric)
            .collect();
        records.push((identifier, folded));
    }
    let unique_records: BTreeSet<&(String, String)> = records.iter().collect();
    if unique_records.len() != records.len() {
        return Err("physical pin records contain exact duplicates".to_string());
    }
    let package_identity = package_identity(package);
    let is_lqfp = package_identity
        .as_ref()
        .is_some_and(|identity| identity.0 == "LQFP");
    if !is_lqfp {
        let unique_ids: BTreeSet<&String> = identifiers.iter().collect();
        if unique_ids.len() != identifiers.len() {
            return Err("physical pin identifiers are not unique".to_string());
        }
        return Ok(());
    }
    if identifiers
        .iter()
        .any(|id| !id.bytes().all(|b| b.is_ascii_digit()))
    {
        return Err("LQFP physical pin identifiers must be numeric".to_string());
    }
    let actual: BTreeSet<i64> = identifiers
        .iter()
        .filter_map(|id| id.parse::<i64>().ok())
        .collect();
    let expected: BTreeSet<i64> = (1..=expected_package_pins).collect();
    if actual != expected {
        return Err("LQFP physical pins do not match the package range".to_string());
    }
    Ok(())
}

/// TOC rows with the reference's `int`/`str` coercions.
#[must_use]
pub fn toc_entries(document_toc: &[Value]) -> Vec<TocEntry> {
    let mut output = Vec::new();
    for row in document_toc {
        let Some(row) = row.as_array() else {
            continue;
        };
        let (Some(first), Some(second), Some(third)) = (row.first(), row.get(1), row.get(2)) else {
            continue;
        };
        let level = as_int(first);
        let title = Some(match second {
            Value::String(t) => t.trim().to_string(),
            other => other.to_string().trim().to_string(),
        });
        let page = as_int(third);
        let (Some(level), Some(title), Some(page)) = (level, title, page) else {
            continue;
        };
        output.push(TocEntry { level, title, page });
    }
    output
}

fn as_int(value: &Value) -> Option<i64> {
    match value {
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Some(i)
            } else if let Some(u) = n.as_u64() {
                i64::try_from(u).ok()
            } else {
                #[allow(
                    clippy::cast_possible_truncation,
                    reason = "the reference int()s the value"
                )]
                let truncated = n.as_f64()? as i64;
                Some(truncated)
            }
        }
        Value::Bool(b) => Some(i64::from(*b)),
        Value::String(text) => {
            let trimmed = text.trim();
            let digits = trimmed
                .strip_prefix('+')
                .or_else(|| trimmed.strip_prefix('-'))
                .unwrap_or(trimmed);
            if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
                return None;
            }
            trimmed.parse::<i64>().ok()
        }
        _ => None,
    }
}

/// Consecutive definition pages from `start_page` up to `final_page`,
/// stopping at the first gap.
#[must_use]
#[allow(
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    reason = "page walks index as the reference does; loop bounds guard them"
)]
pub fn consecutive_definition_pages(
    pages: &[PageInput<'_>],
    start_page: i64,
    final_page: i64,
) -> Vec<i64> {
    let mut out: Vec<i64> = Vec::new();
    let mut found_first = false;
    let last = (i64::try_from(pages.len()).unwrap_or(i64::MAX)).min(final_page);
    for page_number in start_page.max(1)..=last {
        if definition_tables(&pages[(page_number - 1) as usize]).is_empty() {
            if found_first {
                break;
            }
        } else {
            found_first = true;
            out.push(page_number);
        }
    }
    out
}

#[allow(
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    reason = "page walks index as the reference does; loop bounds guard them"
)]
fn walk_back_continuation(pages: &[PageInput<'_>], start_page: i64) -> i64 {
    let mut page_number = start_page;
    while page_number > 1 && !definition_tables(&pages[(page_number - 2) as usize]).is_empty() {
        page_number -= 1;
    }
    page_number
}

fn pin_section(entries: &[TocEntry]) -> Option<(usize, &TocEntry)> {
    entries.iter().enumerate().find(|(_, entry)| {
        entry.level <= 2 && classify_title(&entry.title).as_deref() == Some("pin_section")
    })
}

#[allow(
    clippy::indexing_slicing,
    reason = "indexes the locating entry as the reference does; the caller holds the index"
)]
fn section_end(entries: &[TocEntry], start_index: usize, document_pages: i64) -> i64 {
    let start_level = entries[start_index].level;
    for entry in entries.iter().skip(start_index + 1) {
        let normalized = entry.title.to_uppercase();
        if entry.level <= start_level && SECTION_STOPS.iter().any(|m| normalized.contains(m)) {
            return entry.page - 1;
        }
    }
    document_pages
}

/// A JEDEC id cell, footnote-stripped and space-free.
#[must_use]
pub fn jedec_cell(value: Option<&str>) -> Option<String> {
    let normalized = re(r"\(\d+\)$")
        .replace_all(&cell(value).to_uppercase().replace(' ', ""), "")
        .into_owned();
    if jedec_re().is_match(&normalized) {
        Some(normalized)
    } else {
        None
    }
}

/// Distinct JEDEC ids printed in the selected package column.
#[must_use]
#[allow(
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    reason = "page walks index as the reference does; loop bounds guard them"
)]
pub fn corroborating_jedec_ids(pages: &[PageInput<'_>], selected_column: &str) -> i64 {
    let selected_identity = package_identity(selected_column);
    let mut identifiers: BTreeSet<String> = BTreeSet::new();
    for page in pages {
        for rows in definition_tables(page) {
            let mut column_index: Option<usize> = None;
            for row in rows.iter().take(4) {
                for (index, value) in row.iter().enumerate() {
                    let candidate = cell(value.as_deref());
                    let identity = package_identity(&candidate);
                    if candidate == selected_column
                        || (identity.is_some() && identity == selected_identity)
                    {
                        column_index = Some(index);
                    }
                }
            }
            let Some(column_index) = column_index else {
                continue;
            };
            for row in rows.iter().skip(1) {
                if let Some(value) = row.get(column_index) {
                    if let Some(identifier) = jedec_cell(value.as_deref()) {
                        identifiers.insert(identifier);
                    }
                }
            }
        }
    }
    i64::try_from(identifiers.len()).unwrap_or(i64::MAX)
}

/// Locate the pin-definition pages for one package request.
#[must_use]
#[allow(
    clippy::too_many_lines,
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    reason = "faithful port of locate_pin_definition_pages; page walks index as the reference does"
)]
pub fn locate_pin_definition_pages(
    pages: &[PageInput<'_>],
    document_toc: &[Value],
    document_id: &str,
    requested_package: &str,
    expected_package_pins: i64,
    source_path: &str,
    text_section_single_package: bool,
) -> LocateResult {
    let withheld = |reason: &str,
                    toc_class: Option<&str>,
                    toc_title: Option<String>,
                    pages: &[i64],
                    headers: &[String]| {
        LocateResult {
            document_id: document_id.to_string(),
            requested_package: requested_package.to_string(),
            expected_package_pins,
            source_path: source_path.to_string(),
            status: "withhold".to_string(),
            reason: reason.to_string(),
            toc_class: toc_class.map(str::to_string),
            toc_title,
            pages_1based: pages.to_vec(),
            column_header: None,
            column_headers_all: headers.to_vec(),
            corroborating_jedec_ids: None,
        }
    };
    if document_id.is_empty() || requested_package.is_empty() || expected_package_pins < 1 {
        return LocateResult {
            document_id: document_id.to_string(),
            requested_package: requested_package.to_string(),
            expected_package_pins,
            source_path: source_path.to_string(),
            status: "withhold".to_string(),
            reason: "invalid locator request".to_string(),
            toc_class: None,
            toc_title: None,
            pages_1based: Vec::new(),
            column_header: None,
            column_headers_all: Vec::new(),
            corroborating_jedec_ids: None,
        };
    }
    let entries = toc_entries(document_toc);
    let table_hits: Vec<(usize, &TocEntry)> = entries
        .iter()
        .enumerate()
        .filter(|(_, entry)| classify_title(&entry.title).as_deref() == Some("definition_table"))
        .collect();
    let document_pages = i64::try_from(pages.len()).unwrap_or(i64::MAX);
    let (start_page, final_page, toc_class, toc_title) =
        if let Some((index, entry)) = table_hits.first() {
            let mut start_page = entry.page;
            if entry.title.to_lowercase().contains("(continued)") {
                start_page = walk_back_continuation(pages, start_page);
            }
            let final_page = if index + 1 < entries.len() {
                entries[index + 1].page - 1
            } else {
                document_pages
            };
            (
                start_page,
                final_page,
                Some("definition_table"),
                Some(entry.title.clone()),
            )
        } else {
            let Some((index, entry)) = pin_section(&entries) else {
                return withheld(
                    "toc_has_no_definition_table_and_no_pin_section",
                    None,
                    None,
                    &[],
                    &[],
                );
            };
            let start_page = entry.page;
            let final_page = section_end(&entries, index, document_pages);
            (
                start_page,
                final_page,
                Some("pin_section"),
                Some(entry.title.clone()),
            )
        };
    let page_list = consecutive_definition_pages(pages, start_page, final_page);
    if page_list.is_empty() {
        // Analog/power vendors print pin-function tables without ruling
        // lines; when the caller attests one package, send the section's
        // first three pages (the extracted_n == package_n gate and
        // teacher-side grounding still apply downstream).
        if text_section_single_package && final_page >= start_page {
            let capped_final = (start_page + 2).min(final_page);
            return LocateResult {
                document_id: document_id.to_string(),
                requested_package: requested_package.to_string(),
                expected_package_pins,
                source_path: source_path.to_string(),
                status: "send".to_string(),
                reason: "toc_pin_section_without_ruled_table_single_package".to_string(),
                toc_class: toc_class.map(str::to_string),
                toc_title,
                pages_1based: (start_page..=capped_final).collect(),
                column_header: None,
                column_headers_all: Vec::new(),
                corroborating_jedec_ids: None,
            };
        }
        return withheld(
            "definition_table_pages_empty_find_tables",
            toc_class,
            toc_title,
            &[],
            &[],
        );
    }
    let mut headers: Vec<String> = Vec::new();
    for page_number in page_list.iter().take(3) {
        let page = &pages[(page_number - 1) as usize];
        for rows in definition_tables(page) {
            for header in package_headers(&rows) {
                if !headers.contains(&header) {
                    headers.push(header);
                }
            }
        }
    }
    let Some(selected_column) = match_package_column(requested_package, &headers) else {
        return withheld(
            "package_column_not_in_table_headers",
            toc_class,
            toc_title,
            &page_list,
            &headers,
        );
    };
    let selected_pages: Vec<PageInput<'_>> = page_list
        .iter()
        .map(|p| {
            let page = &pages[(p - 1) as usize];
            PageInput {
                text: page.text,
                tables: page.tables,
            }
        })
        .collect();
    let jadec = corroborating_jedec_ids(&selected_pages, &selected_column);
    LocateResult {
        document_id: document_id.to_string(),
        requested_package: requested_package.to_string(),
        expected_package_pins,
        source_path: source_path.to_string(),
        status: "send".to_string(),
        reason: "toc_definition_table_and_header_match".to_string(),
        toc_class: toc_class.map(str::to_string),
        toc_title,
        pages_1based: page_list,
        column_header: Some(selected_column.clone()),
        column_headers_all: headers,
        corroborating_jedec_ids: Some(jadec),
    }
}

/// `PACKAGE_KEY` — re-exported for the table header checks above.
#[must_use]
pub fn package_cell(value: &str) -> bool {
    package_key_re().is_match(value)
}

/// The unused-families mirror kept for parity with the reference constant.
#[must_use]
pub fn package_families_source() -> &'static str {
    PACKAGE_FAMILIES
}
