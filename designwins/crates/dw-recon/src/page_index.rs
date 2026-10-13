//! PyMuPDF-first section indexing for electronics datasheets
//! (`harness/electronics/page_index.py`, ported 1:1).
//!
//! The reader turns a document's TOC and page texts into lane pages
//! (`pin_or_ball`, `parametrics`, `opn_decoder`, ...) and exact pin
//! locations. TOC entries that classify into a lane land first; page-text
//! headings fill only the lanes the TOC missed (3..=180 chars, collapsed
//! whitespace, deduped by `(page, casefold(title))`). The single-package
//! relaxation for the pin locator is safe only when exactly one package is
//! requested AND the printed front matter names at most that family — a lone
//! binding is a weak proxy on multi-package documents.
//!
//! `locator.locate_pin_definition_pages` is a PDF-layer reader (§5.5) whose
//! results travel as captured inputs here: [`index_document`] takes a
//! `locate` callback and hands it the same arguments the reference passes,
//! including the `single_package` decision the port recomputes.

use std::collections::{BTreeMap, BTreeSet};

use regex::Regex;
use serde_json::{json, Value};

use crate::corpus::canonical_json;

/// The page-index schema id.
pub const PAGE_INDEX_SCHEMA: &str = "harness.electronics-page-index.v1";

/// Headings longer than this are noise, not titles.
pub const HEADING_MAX_LENGTH: usize = 180;

/// Front matter is scanned this deep for printed package families.
pub const PACKAGE_SCAN_PAGES: usize = 30;

/// Longer tokens first so `\b` alternation prefers UFBGA over BGA, WSON over
/// SON (the reference's ordering).
const PACKAGE_FAMILY: &str = r"\b(UFQFPN|VFQFPN|HTSSOP|DSBGA|WLCSP|UFBGA|TFBGA|LFBGA|VFBGA|NFBGA|HSOIC|TSSOP|VSSOP|LQFP|TQFP|VQFN|UQFN|WQFN|SSOP|MSOP|SOIC|PDIP|SDIP|WSON|VSON|QFP|QFN|BGA|LGA|SOT|SON)\b";

/// One extraction lane and its title patterns (reference insertion order —
/// `classify_section` reports lanes in this order).
const LANE_PATTERNS: &[(&str, &[&str])] = &[
    (
        "pin_or_ball",
        &[
            r"\bPIN(?:OUT|OUTS)?\b",
            r"\bBALL(?:OUT|OUTS)?\b",
            r"\bPIN\s*(?:AND|/)\s*BALL\b",
            r"\bTERMINAL\s+(?:FUNCTIONS?|ASSIGNMENTS?)\b",
        ],
    ),
    (
        "pin_semantics",
        &[
            r"\bPIN\s+(?:DESCRIPTION|FUNCTION)",
            r"\bBALL\s+(?:DESCRIPTION|FUNCTION)",
            r"\bALTERNATE\s+FUNCTION",
            r"\bMULTIPLEX",
        ],
    ),
    (
        "parametrics",
        &[
            r"\bELECTRICAL\s+CHARACTERISTICS?\b",
            r"\bABSOLUTE\s+MAXIMUM\b",
            r"\bRECOMMENDED\s+OPERATING\b",
            r"\bCLOCK",
            r"\bMEMORY\b",
            r"\bPOWER\s+(?:CONSUMPTION|MODES?)\b",
            r"\bTHERMAL\s+CHARACTERISTICS?\b",
        ],
    ),
    (
        "power_modes",
        &[
            r"\bPOWER\s+(?:CONSUMPTION|DISSIPATION)\b",
            r"\bCURRENT\s+CONSUMPTION\b",
            r"\bSUPPLY\s+CURRENTS?\b",
            r"\b(?:LOW[- ]?POWER|POWER|ACTIVE|OPERATING)\s+MODE\s+CURRENT",
        ],
    ),
    (
        "typical_characteristics",
        &[
            r"\bTYPICAL\s+(?:OPERATING\s+)?CHARACTERISTICS?\b",
            r"\bTYPICAL\s+PERFORMANCE\b",
            r"\bPERFORMANCE\s+(?:CURVES|GRAPHS|PLOTS)\b",
            r"\b(?:CURVES|GRAPHS)\s+AND\s+PLOTS\b",
        ],
    ),
    (
        "series_summary",
        &[
            r"\bFEATURES?\b",
            r"\bDESCRIPTION\b",
            r"\bOVERVIEW\b",
            r"\bAPPLICATIONS?\b",
            r"\bPRODUCT\s+SUMMARY\b",
        ],
    ),
    (
        "opn_decoder",
        &[
            r"\bORDERING\s+INFORMATION\b",
            r"\bPART\s+NUMBERING\b",
            r"\bDEVICE\s+INFORMATION\b",
            r"\bNOMENCLATURE\b",
            r"\bORDERABLE\s+(?:PART|DEVICE)",
        ],
    ),
    (
        "design_specs",
        &[
            r"\b(?:TOP[- ]LEVEL\s+)?SPECIFICATIONS?\b",
            r"\bDESIGN\s+REQUIREMENTS?\b",
            r"\bVOLTAGE\s+AND\s+CURRENT\s+REQUIREMENTS?\b",
            r"\bSYSTEM\s+(?:DESCRIPTION|OVERVIEW)\b",
            r"\bDESIGN\s+OVERVIEW\b",
        ],
    ),
    (
        "test_results",
        &[
            r"\bTEST(?:ING)?\s+(?:AND\s+)?RESULTS?\b",
            r"\bTEST\s+(?:DATA|SETUP|PREREQUISITES?)\b",
            r"\bMEASURED\s+PERFORMANCE\b",
            r"\bWAVEFORMS?\b",
            r"\bTHERMAL\s+(?:IMAGES?|RESULTS?)\b",
            r"\bPERFORMANCE\s+(?:DATA|RESULTS?)\b",
        ],
    ),
    (
        "bom_parts",
        &[
            r"\bBILL\s+OF\s+MATERIALS?\b",
            r"\bBOM\b",
            r"\bPREFERRED\s+COMPONENTS?\b",
            r"\bCOMPONENTS?\s+(?:LIST|SELECTION|TABLE)\b",
            r"\bKEY\s+COMPONENTS?\b",
        ],
    ),
    (
        "schematic_layout",
        &[
            r"\bSCHEMATICS?\b",
            r"\bLAYOUT\s+(?:GUIDELINES?|CONSIDERATIONS?|RECOMMENDATIONS?)\b",
            r"\bDESIGN\s+FILES?\b",
            r"\bPCB\s+(?:LAYOUT|DESIGN|FABRICATION)\b",
            r"\bGERBER\b",
            r"\bBLOCK\s+DIAGRAM\b",
            r"\bTOPOLOGY\b",
        ],
    ),
];

#[allow(
    clippy::expect_used,
    reason = "patterns are literals from the reference"
)]
fn re(src: &str) -> Regex {
    Regex::new(src).expect("pattern is a literal from the reference")
}

fn lane_regexes() -> &'static Vec<Vec<Regex>> {
    use std::sync::OnceLock;
    static R: OnceLock<Vec<Vec<Regex>>> = OnceLock::new();
    R.get_or_init(|| {
        LANE_PATTERNS
            .iter()
            .map(|(_, patterns)| patterns.iter().map(|p| re(&format!("(?i){p}"))).collect())
            .collect()
    })
}

fn package_family_re() -> &'static Regex {
    use std::sync::OnceLock;
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(PACKAGE_FAMILY))
}

/// The lanes a title addresses, in `LANE_PATTERNS` order.
#[must_use]
pub fn classify_section(title: &str) -> Vec<String> {
    let normalized: String = title.split_whitespace().collect::<Vec<_>>().join(" ");
    LANE_PATTERNS
        .iter()
        .zip(lane_regexes())
        .filter(|((_, _), patterns)| patterns.iter().any(|p| p.is_match(&normalized)))
        .map(|((lane, _), _)| (*lane).to_string())
        .collect()
}

/// Python `int(value)` for JSON numbers, bools and integer strings
/// (`int(" 7 ")` == 7; `int("1.5")` raises and the row is skipped).
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

/// TOC entries that classify into a lane, with bookkeeping for the report.
#[must_use]
pub fn toc_entries(toc: &[Value], page_count: usize) -> Vec<Value> {
    let mut output = Vec::new();
    for raw in toc {
        let Some(raw) = raw.as_array() else {
            continue;
        };
        let (Some(first), Some(second), Some(third)) = (raw.first(), raw.get(1), raw.get(2)) else {
            continue;
        };
        // Python `int(raw[0])`/`int(raw[2])` accept ints and truncating
        // floats; `str(raw[1]).strip()` stringifies any title.
        let level = as_int(first);
        let title = Some(match second {
            Value::String(t) => t.trim().to_string(),
            other => other.to_string().trim().to_string(),
        });
        let page = as_int(third);
        let (Some(level), Some(title), Some(page)) = (level, title, page) else {
            continue;
        };
        if title.is_empty() || page < 1 || page > i64::try_from(page_count).unwrap_or(i64::MAX) {
            continue;
        }
        let lanes = classify_section(&title);
        if !lanes.is_empty() {
            output.push(json!({
                "level": level,
                "title": title,
                "page_1based": page,
                "lanes": lanes,
                "source": "toc",
            }));
        }
    }
    output
}

/// Whitespace-collapsed lines of `text` that classify into a lane.
fn heading_lines(text: &str) -> Vec<String> {
    let mut output = Vec::new();
    for line in text.split('\n') {
        let normalized: String = line.split_whitespace().collect::<Vec<_>>().join(" ");
        let length = normalized.chars().count();
        if (3..=HEADING_MAX_LENGTH).contains(&length) && !classify_section(&normalized).is_empty() {
            output.push(normalized);
        }
    }
    output
}

/// Page-text headings for lanes the TOC missed, in scan order.
#[must_use]
pub fn fallback_headings(
    pages: &[String],
    existing_lanes: &BTreeSet<String>,
    maximum_pages: Option<usize>,
) -> Vec<Value> {
    let missing: Vec<String> = LANE_PATTERNS
        .iter()
        .map(|(lane, _)| (*lane).to_string())
        .filter(|lane| !existing_lanes.contains(lane))
        .collect();
    if missing.is_empty() {
        return Vec::new();
    }
    let page_limit = maximum_pages.map_or(pages.len(), |max| max.min(pages.len()));
    let mut output = Vec::new();
    let mut seen: BTreeSet<(usize, String)> = BTreeSet::new();
    for (page_index, text) in pages.iter().enumerate().take(page_limit) {
        for title in heading_lines(text) {
            let lanes: Vec<String> = classify_section(&title)
                .into_iter()
                .filter(|lane| missing.iter().any(|m| m == lane))
                .collect();
            if lanes.is_empty() {
                continue;
            }
            let identity = (page_index + 1, title.to_lowercase());
            if seen.contains(&identity) {
                continue;
            }
            seen.insert(identity);
            output.push(json!({
                "level": Value::Null,
                "title": title,
                "page_1based": i64::try_from(page_index + 1).unwrap_or(i64::MAX),
                "lanes": lanes,
                "source": "page_text",
            }));
        }
    }
    output
}

/// Distinct uppercase package family tokens printed in the front matter
/// (case-sensitive, like the reference's regex).
#[must_use]
pub fn printed_package_families(pages: &[String]) -> BTreeSet<String> {
    let mut families = BTreeSet::new();
    for text in pages.iter().take(PACKAGE_SCAN_PAGES) {
        for caps in package_family_re().captures_iter(text) {
            if let Some(m) = caps.get(1) {
                families.insert(m.as_str().to_string());
            }
        }
    }
    families
}

/// `(package, expected_pins)` requests from ground-truth records, sorted and
/// deduplicated; the printed count wins, `declared_pin_total` falls back.
#[must_use]
pub fn package_requests_from_ground_truth(records: &[Value]) -> Vec<(String, i64)> {
    let mut requests: BTreeSet<(String, i64)> = BTreeSet::new();
    for value in records {
        let Some(pinout) = value.get("pinout").and_then(Value::as_object) else {
            continue;
        };
        let declared = pinout.get("declared_pin_total");
        let Some(packages) = pinout.get("packages").and_then(Value::as_array) else {
            continue;
        };
        for package in packages {
            let text = if package.is_null() {
                String::new()
            } else {
                match package {
                    Value::String(s) => s.clone(),
                    Value::Bool(true) => "True".to_string(),
                    Value::Bool(false) => "False".to_string(),
                    Value::Number(n) => {
                        if n.is_u64() || n.is_i64() {
                            n.to_string()
                        } else {
                            format!("{:?}", n.as_f64().unwrap_or_default())
                        }
                    }
                    other => other.to_string(),
                }
            };
            let text = text.trim().to_string();
            let matches: Vec<i64> = digit_runs(&text);
            let expected = matches.first().copied().or_else(|| {
                // `isinstance(declared, int)` — Python `bool` is an `int`.
                match declared {
                    Some(Value::Number(n)) if n.is_i64() || n.is_u64() => n.as_i64(),
                    Some(Value::Bool(b)) => Some(i64::from(*b)),
                    _ => None,
                }
            });
            if !text.is_empty() {
                if let Some(expected) = expected {
                    if expected != 0 {
                        requests.insert((text, expected));
                    }
                }
            }
        }
    }
    requests.into_iter().collect()
}

/// `\b(\d{2,4})\b` — every 2-to-4-digit run, in order.
fn digit_runs(text: &str) -> Vec<i64> {
    use std::sync::OnceLock;
    static R: OnceLock<Regex> = OnceLock::new();
    let digits = R.get_or_init(|| re(r"\b(\d{2,4})\b"));
    digits
        .captures_iter(text)
        .filter_map(|c| c.get(1).and_then(|m| m.as_str().parse::<i64>().ok()))
        .collect()
}

/// The page-index report: TOC lanes, fallback lanes, lane pages and exact
/// pin locations, self-digested over the canonical JSON encoding.
///
/// `locate(package, expected_pins, single_package)` stands in for
/// `locator.locate_pin_definition_pages` (§5.5) and must return the result
/// object with `requested_package`, `expected_package_pins`, `status`,
/// `reason`, `pages_1based`, `column_header`, `column_headers_all`,
/// `corroborating_jedec_ids`.
///
/// # Errors
///
/// Returns `"document_sha256 is invalid"` when the digest is not 64 chars.
pub fn index_document<F>(
    pages: &[String],
    toc: &[Value],
    document_sha256: &str,
    source_path: &str,
    package_requests: &[(String, i64)],
    fallback_maximum_pages: Option<usize>,
    mut locate: F,
) -> Result<Value, String>
where
    F: FnMut(&str, i64, bool) -> Value,
{
    if document_sha256.len() != 64 {
        return Err("document_sha256 is invalid".to_string());
    }
    let page_count = pages.len();
    let toc_entries = toc_entries(toc, page_count);
    let toc_lanes: BTreeSet<String> = toc_entries
        .iter()
        .flat_map(|e| {
            e.get("lanes")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect::<Vec<_>>()
        })
        .collect();
    let fallback = fallback_headings(pages, &toc_lanes, fallback_maximum_pages);
    let entries: Vec<Value> = toc_entries.into_iter().chain(fallback).collect();
    let mut lane_pages: BTreeMap<String, BTreeSet<i64>> = BTreeMap::new();
    for entry in &entries {
        if let Some(lanes) = entry.get("lanes").and_then(Value::as_array) {
            if let Some(page) = entry.get("page_1based").and_then(Value::as_i64) {
                for lane in lanes.iter().filter_map(Value::as_str) {
                    lane_pages.entry(lane.to_string()).or_default().insert(page);
                }
            }
        }
    }
    let mut unique_requests: Vec<(String, i64)> = Vec::new();
    for (package, expected_pins) in package_requests {
        let request = (package.trim().to_string(), *expected_pins);
        if !request.0.is_empty() && request.1 >= 1 && !unique_requests.contains(&request) {
            unique_requests.push(request);
        }
    }
    // The borderless-table relaxation needs no package-column projection, so
    // it is only safe when the document binds exactly one package request AND
    // its printed front matter names at most one package family, matching the
    // request. A lone binding is a weak proxy on multi-package documents.
    let mut single_package = false;
    if unique_requests.len() == 1 {
        let Some((first_package, _)) = unique_requests.first() else {
            unreachable!("guarded by len() == 1");
        };
        let requested_family = strip_trailing_count(&first_package.to_uppercase());
        single_package = !requested_family.is_empty()
            && printed_package_families(pages)
                .iter()
                .all(|family| *family == requested_family);
    }
    let mut exact: Vec<Value> = Vec::new();
    for (package, expected_pins) in &unique_requests {
        let result = locate(package, *expected_pins, single_package);
        let list = |key: &str| -> Value {
            match result.get(key) {
                Some(Value::Array(items)) => Value::Array(items.clone()),
                Some(Value::Null) | None => json!([]),
                Some(other) => json!([other.clone()]),
            }
        };
        exact.push(json!({
            "package": result.get("requested_package").cloned().unwrap_or(Value::Null),
            "expected_package_pins": result.get("expected_package_pins").cloned().unwrap_or(Value::Null),
            "status": result.get("status").cloned().unwrap_or(Value::Null),
            "reason": result.get("reason").cloned().unwrap_or(Value::Null),
            "pages_1based": list("pages_1based"),
            "column_header": result.get("column_header").cloned().unwrap_or(Value::Null),
            "column_headers_all": list("column_headers_all"),
            "corroborating_jedec_ids": result.get("corroborating_jedec_ids").cloned().unwrap_or(Value::Null),
        }));
    }
    let mut lane_pages_json = serde_json::Map::new();
    for (lane, pages) in &lane_pages {
        lane_pages_json.insert(
            lane.clone(),
            json!(pages.iter().copied().collect::<Vec<_>>()),
        );
    }
    let core = json!({
        "schema": PAGE_INDEX_SCHEMA,
        "document_sha256": document_sha256,
        "source_path": source_path,
        "page_count": page_count,
        "section_entries": entries,
        "lane_pages": lane_pages_json,
        "exact_pin_locations": exact,
    });
    let digest = crate::sha256::sha256_hex(canonical_json(&core).as_bytes());
    let mut out = core.as_object().cloned().unwrap_or_default();
    out.insert("evidence_sha256".into(), json!(digest));
    Ok(Value::Object(out))
}

/// `re.sub(r"[-\s]?\d+$", "", text)` — the printed package family of a
/// request ("LQFP64" -> "LQFP").
fn strip_trailing_count(text: &str) -> String {
    use std::sync::OnceLock;
    static R: OnceLock<Regex> = OnceLock::new();
    let trailing = R.get_or_init(|| re(r"[-\s]?\d+$"));
    trailing.replace_all(text, "").into_owned()
}
