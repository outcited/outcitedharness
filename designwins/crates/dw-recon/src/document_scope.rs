//! Cover-page scope statements — port of
//! `harness/electronics/document_scope.py` (`read_scope`, schema
//! `harness.electronics-document-scope.v2`).
//!
//! Fail-closed in three directions:
//!
//! * **`scope_absent`** is a first-class answer: the cover prints no scope
//!   sentence. Never guessed from the filename.
//! * **`extraction_failed`** is an *instrument* verdict (no text layer,
//!   control token absent) — the opposite claim from "no scope printed",
//!   and the two are never conflated (CR's CID-encoded `GD32F4xx` manual read
//!   as zero hits for even `GD32`).
//! * series tokens are preserved **verbatim** — `STM32F405xx/07xx` is
//!   captured as printed, never expanded. Expansion is the consumer's call.
//!
//! The port takes front-page texts (the PDF layer feeds them) and clamps to
//! the reference's five-page scan itself.

use std::collections::BTreeSet;

use regex::Regex;
use serde::{Deserialize, Serialize};
use std::sync::OnceLock;

/// How many front pages the reference scans.
pub const FRONT_PAGES: usize = 5;

/// Below this much front-matter text the reader calls it an instrument
/// failure, not a verdict.
const MIN_TEXT: usize = 40;

#[allow(
    clippy::expect_used,
    reason = "patterns are literals from the reference"
)]
fn re(src: &str) -> Regex {
    Regex::new(src).expect("pattern is a literal from the reference")
}

/// One document's scope row — the reference's wire, field for field.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ScopeRow {
    /// Schema stamp.
    pub schema: String,
    /// Where the document came from (the caller fills it).
    pub source_path: Option<String>,
    /// Document hash (the caller fills it).
    pub document_sha256: Option<String>,
    /// Page count of the scanned document.
    pub page_count: i64,
    /// `read_ok` or `extraction_failed` — an instrument verdict is never a
    /// scope verdict.
    pub status: String,
    /// Why the instrument failed, when it did.
    pub extraction_failed_reason: Option<String>,
    /// The caller-supplied control token, if any.
    pub control_token: Option<String>,
    /// Whether the control token appeared in the front matter.
    pub control_token_found: Option<bool>,
    /// Series/part tokens printed inside the scope statement, verbatim.
    pub governs_series: Vec<String>,
    /// The verbatim printed statement.
    pub scope_statement: Option<String>,
    /// 1-based page carrying the statement.
    pub scope_page: Option<i64>,
    /// `reference_manual|user_manual|programming_manual|datasheet|application_note|errata|unknown`.
    pub document_class: String,
    /// `device_family|cpu_core|peripheral|unknown`.
    pub document_subject: String,
    /// Best-effort verbatim title line(s).
    pub title_verbatim: Option<String>,
    /// `false` when a scope statement printed; `None` when the instrument
    /// failed; `true` = the vendor prints no scope.
    pub scope_absent: Option<bool>,
    /// Why no scope statement was found, when none was.
    pub scope_absent_reason: Option<String>,
}

fn scope_for_list() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        let t = r"[A-Z]{2,10}\d[A-Za-z0-9]*(?:x{1,3})?";
        re(&format!(r"\bFor\s+{t}(?:\s*(?:,|and|&)\s*{t})*"))
    })
}

fn scope_use_list() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        let t = r"[A-Z]{2,10}\d[A-Za-z0-9]*(?:x{1,3})?(?:/[0-9A-Za-z]{1,8})?";
        re(&format!(
            r"how\s+to\s+use\s+the\s+{t}(?:\s*(?:,|and|&)\s*{t})*"
        ))
    })
}

fn scope_of_list() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        let t = r"[A-Z]{2,10}\d[A-Za-z0-9]*(?:x{1,3})?(?:/[0-9A-Za-z]{1,8})?";
        re(&format!(
            r"(?:peripherals|datasheets?)\s+of\s+(?:the\s+)?{t}(?:\s*(?:,|and|&)\s*{t})*"
        ))
    })
}

fn scope_prose() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(concat!(
            r"(?i)\b(Applicable\s+(?:products?|to)[:\s].{4,300}?",
            r"|This\s+(?:user\s+manual|reference\s+manual|document|datasheet)",
            r"\s+(?:applies\s+to|covers|describes).{4,300}?)(?:[.]|$)"
        ))
    })
}

fn series_token() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"\b([A-Z]{2,10}\d{1,4}[A-Zx0-9]*(?:/[0-9A-Za-z]{1,8})?)\b"))
}

/// The class vocabulary in the reference's order — earliest printed wins.
const CLASS_WORDS: &[(&str, &str)] = &[
    ("reference manual", "reference_manual"),
    ("user manual", "user_manual"),
    ("programming manual", "programming_manual"),
    ("application note", "application_note"),
    ("errata", "errata"),
    ("datasheet", "datasheet"),
    ("data sheet", "datasheet"),
];

fn core_signal() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"(?i)\b(cortex[\s-]?m\d+\w*|arm\S{0,8}\s+core|instruction\s+set|core\s+programming)\b")
    })
}

fn peripheral_names() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"\b(I2C|SPI|UART|USART|USB|CAN|ADC|DAC|DMA|RTC|TIMER|PWM|ETHERNET|FLASH\s+PROGRAM)\b")
    })
}

fn peripheral_doc() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"(?i)(framework\s+reference\s+manual|FRM|section\s+of\s+the)"))
}

fn document_subject(
    title: Option<&str>,
    front: &str,
    document_class: &str,
    has_series: bool,
) -> &'static str {
    let head: String = front.chars().take(2000).collect();
    let header = format!("{}\n{}", title.unwrap_or_default(), head);
    if document_class == "programming_manual" && core_signal().is_match(&header) {
        return "cpu_core";
    }
    if core_signal().is_match(title.unwrap_or_default())
        && header.to_lowercase().contains("programming")
    {
        return "cpu_core";
    }
    if let Some(t) = title {
        if peripheral_names().is_match(t)
            && (peripheral_doc().is_match(&header) || document_class == "reference_manual")
            && !has_series
        {
            return "peripheral";
        }
    }
    if has_series {
        return "device_family";
    }
    if matches!(
        document_class,
        "reference_manual" | "user_manual" | "datasheet"
    ) {
        return "device_family";
    }
    "unknown"
}

/// Read the cover-page scope statement from front-matter page texts — the
/// reference's `read_scope` with the PDF-opening step replaced by the
/// caller's pages (clamped to [`FRONT_PAGES`] exactly as the reference
/// scans five). `source_path`/`document_sha256` are the caller's to fill.
#[must_use]
#[allow(
    clippy::too_many_lines,
    reason = "faithful sequential port of read_scope — one fail-closed walk"
)]
pub fn read_scope(pages: &[String], control_token: Option<&str>) -> ScopeRow {
    let mut row = ScopeRow {
        schema: "harness.electronics-document-scope.v2".into(),
        source_path: None,
        document_sha256: None,
        page_count: i64::try_from(pages.len()).unwrap_or(i64::MAX),
        status: "read_ok".into(),
        extraction_failed_reason: None,
        control_token: control_token.map(str::to_string),
        control_token_found: None,
        governs_series: Vec::new(),
        scope_statement: None,
        scope_page: None,
        document_class: "unknown".into(),
        document_subject: "unknown".into(),
        title_verbatim: None,
        scope_absent: Some(true),
        scope_absent_reason: None,
    };
    let front: Vec<&String> = pages.iter().take(FRONT_PAGES).collect();
    let joined = front
        .iter()
        .map(|s| s.as_str())
        .collect::<Vec<_>>()
        .join("\n");
    if joined.trim().chars().count() < MIN_TEXT {
        row.status = "extraction_failed".into();
        row.extraction_failed_reason = Some("no_text_layer_on_front_matter".into());
        row.scope_absent = None;
        return row;
    }

    if let Some(token) = control_token {
        let found = joined.to_uppercase().contains(&token.to_uppercase());
        row.control_token_found = Some(found);
        if !found {
            row.status = "extraction_failed".into();
            row.extraction_failed_reason = Some("control_token_absent".into());
            row.scope_absent = None;
            return row;
        }
    }

    // Earliest class phrase wins: cover self-declarations come before
    // cross-references further down (the PM0214 trap).
    let cover_lowered = front.first().map(|s| s.to_lowercase()).unwrap_or_default();
    let lowered = joined.to_lowercase();
    'outer: for source in [&cover_lowered, &lowered] {
        let mut positions: Vec<(usize, &str)> = Vec::new();
        for (needle, label) in CLASS_WORDS {
            if let Some(i) = source.find(needle) {
                positions.push((i, *label));
            }
        }
        if let Some(first) = positions.iter().min() {
            row.document_class = first.1.to_string();
            break 'outer;
        }
    }

    // Title: first cover lines that are neither vendor boilerplate nor empty.
    if let Some(cover) = front.first() {
        let title_lines: Vec<&str> = cover
            .lines()
            .map(str::trim)
            .filter(|l| {
                !l.is_empty()
                    && re(r"^[\s\d.()-]+$").find(l).is_none()
                    && !l.to_lowercase().contains("inc")
                    && !l.to_lowercase().contains("revision")
            })
            .collect();
        if !title_lines.is_empty() {
            row.title_verbatim = Some(
                title_lines
                    .iter()
                    .take(2)
                    .copied()
                    .collect::<Vec<_>>()
                    .join(" "),
            );
        }
    }

    'pages: for (idx, page) in front.iter().enumerate() {
        let normalized = page.split_whitespace().collect::<Vec<_>>().join(" ");
        let mut candidates: Vec<String> = Vec::new();
        candidates.extend(
            scope_for_list()
                .find_iter(&normalized)
                .map(|m| m.as_str().to_string()),
        );
        candidates.extend(
            scope_use_list()
                .find_iter(&normalized)
                .map(|m| m.as_str().to_string()),
        );
        candidates.extend(
            scope_of_list()
                .find_iter(&normalized)
                .map(|m| m.as_str().to_string()),
        );
        candidates.extend(
            scope_prose()
                .captures_iter(&normalized)
                .filter_map(|m| m.get(1).map(|g| g.as_str().to_string())),
        );
        for statement in candidates {
            let statement = statement.split_whitespace().collect::<Vec<_>>().join(" ");
            let mut tokens: Vec<String> = Vec::new();
            let mut seen = BTreeSet::new();
            for cap in series_token().captures_iter(&statement) {
                let t = cap
                    .get(1)
                    .map_or_else(String::new, |m| m.as_str().to_string());
                if seen.insert(t.clone()) {
                    tokens.push(t);
                }
            }
            if tokens.is_empty() {
                continue;
            }
            row.governs_series = tokens;
            row.scope_statement = Some(statement);
            row.scope_page = Some(i64::try_from(idx).unwrap_or(0) + 1);
            row.scope_absent = Some(false);
            break 'pages;
        }
    }

    if row.scope_absent == Some(true) {
        row.scope_absent_reason = Some("no_scope_statement_printed".into());
    }
    row.document_subject = document_subject(
        row.title_verbatim.as_deref(),
        &joined,
        &row.document_class,
        !row.governs_series.is_empty(),
    )
    .to_string();
    row
}
