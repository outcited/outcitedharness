//! Above-OPN document census — port of
//! `harness/electronics/family_census.py` (schema
//! `harness.electronics-family-census.v1`).
//!
//! The census settles, per document and with page receipts, what must be
//! known BEFORE any model reads a family/series document:
//!
//! * **`grain`** — `above_opn | single_opn | not_device_family | unknown |
//!   extraction_failed`. Only `above_opn` documents feed the family-content
//!   and knife lanes; `above_opn_kind` then scopes those lanes.
//! * **`parts_named`** — concrete orderable tokens the document itself prints,
//!   each with its page. Never expanded from a wildcard, never taken from a
//!   taxonomy.
//! * **`series_tokens`** — wildcards verbatim as printed (`STM32C011x4`).
//! * **`pin_bogeys`** — pin counts derived from printed package tokens
//!   (`LQFP64 -> 64`): the denominators the pin lane is scored against.
//! * **`device_tables`** — located comparison/feature tables: orientation,
//!   variant columns/rows, and the attribute rows they address
//!   (qualifier-anchored: code flash is not data flash).
//! * **`denominator_cells`** — `parts_or_variants x attributes_addressed`:
//!   what a later extraction is scored against.
//!
//! Nothing here is a value extraction; it is the census that makes later
//! extraction measurable. As with [`crate::document_scope`] the port takes
//! the PDF layer's output — page texts and extracted table rows — instead of
//! opening documents itself, and fails the same ways the reference does.
//!
//! This module calls no models.

use std::collections::{BTreeMap, BTreeSet};

use regex::Regex;
use serde::{Deserialize, Serialize};
use std::sync::OnceLock;

use crate::document_scope::{read_scope, series_token, ScopeRow};

/// Schema stamp of a census row.
pub const CENSUS_SCHEMA: &str = "harness.electronics-family-census.v1";

/// Schema stamp of a census summary.
pub const CENSUS_SUMMARY_SCHEMA: &str = "harness.electronics-family-census-summary.v1";

/// Front pages always inspected for device tables / ordering lists.
pub const FRONT_PAGES: usize = 3;

/// Keyword scan window for device-table pages.
pub const KEYWORD_SCAN_PAGES: usize = 40;

/// Candidate-page cap: no census inspects more pages than this.
pub const MAX_TABLE_PAGES: usize = 20;

#[allow(
    clippy::expect_used,
    reason = "patterns are literals from the reference"
)]
fn re(src: &str) -> Regex {
    Regex::new(src).expect("pattern is a literal from the reference")
}

fn normalize(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn cell(value: Option<&str>) -> String {
    match value {
        None => String::new(),
        Some(v) => normalize(v),
    }
}

// ---------------------------------------------------------------------------
// Token rules
// ---------------------------------------------------------------------------

/// `STM32C011x4`, `GD32F405xx`, `STM32G0x1`: a lowercase `x` inside a token
/// marks a printed series wildcard, not a part.
#[must_use]
pub fn is_wildcard_token(token: &str) -> bool {
    // The reference compiles a bare `x`; any lowercase x is a wildcard mark.
    token.contains('x')
}

fn non_part_prefix() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(concat!(
            r"^(?:RM|UM|DS|AN|PM|ES|TN|DOC|REV|ISO|IEC|JESD|IEEE|ARM|SLA|SLV|SPR|",
            r"SBO|SNV|SLU|SLL|SNO|DDI|IHI|MIL|EIA|JEDEC|ROHS|UL|CE|FCC|USB|PCI|",
            r"CAN|SPI|I2C|I2S|SDIO|GPIO|ADC|DAC|DMA|RTC|PWM|UART|USART|LQFP|TQFP|",
            r"QFN|BGA|WLCSP|TSSOP|SOIC|DFN|MHZ|KHZ|KB|MB|GB|V|A)\d"
        ))
    })
}

fn non_part_token() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(concat!(
            r"^(?:X?OSC\d|SERNUM|CRC\d|SHA\d|AES\d|RSA\d|MD\d|UTF\d|ISO\d|RS\d{3}|",
            r"IEEE\d|CAT\d|CLASS\d|GRADE\d|LEVEL\d|TYPE\d|TIM\d|CH\d|IO\d|",
            r"P[A-K]\d{1,2}$|VDD\d|VSS\d|VBAT|NRST|BOOT\d|OPT\d|FIG\d|TAB\d|",
            r"REV\d|PAGE\d|NOTE\d|STEP\d|CASE\d|BIT\d|WORD\d|BYTE\d|",
            r"YUV\d|RGB\d|JPEG|MPEG|H26\d|",
            r"R\d{2}[A-Z]{2}\d{4}|P[A-Z]{3}\d{4}[A-Z]{2}(?:-[A-Z])?$)"
        ))
    })
}

fn stem_prefix() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"^[A-Z]{1,10}\d{1,3}"))
}

/// Concrete part token candidates — one letter may open the stem
/// (`R7FA2E2A72DNK`, `S32K144`); the two-letter floor of [`series_token`] is
/// for wildcards only.
fn part_token() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"\b([A-Z]{1,10}\d{1,4}[A-Z0-9]*(?:-[A-Z0-9]{1,6})?)\b"))
}

fn separator() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"[\s,/;()]"))
}

/// Package family table — `locator.py`'s `PACKAGE_KEY`, embedded here (the
/// Rust `locator` port lands with `dw-structure`).
fn package_key() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        let families = concat!(
            r"TFBGA|UFBGA|LFBGA|XFBGA|FBGA|BGA|WLCSP|DSBGA|CABGA|CSBGA|",
            r"LQFP|TQFP|QFP|VFQFPN|UFQFPN|VQFN|WQFN|TQFN|UTQFN|",
            r"XQFN|QFN|DFN|LGA|WLP|CSP|",
            r"TSSOP|SSOP|SOIC|SPDIP|PDIP"
        );
        re(&format!(
            concat!(
                r"(?i)\b({families})\s*[- ]?\s*(\d{{1,4}})\b",
                r"|\b(\d{{1,4}})\s*(?:[- ]\s*)?(?:PIN|BALL)?\s*({families})\b"
            ),
            families = families
        ))
    })
}

/// A concrete orderable part token: long enough, no separators, at least two
/// digits, and not a document id, package code, or pin name.
#[must_use]
pub fn is_concrete_part_token(token: &str) -> bool {
    if is_wildcard_token(token) {
        return false;
    }
    if !(6..=24).contains(&token.chars().count()) {
        return false;
    }
    if separator().is_match(token) {
        return false;
    }
    if !token.chars().next().is_some_and(char::is_alphabetic) {
        return false;
    }
    // The reference counts `str.isdigit` on an ASCII token stream.
    if token.chars().filter(char::is_ascii_digit).count() < 2 {
        return false;
    }
    if non_part_prefix().is_match(token) || non_part_token().is_match(token) {
        return false;
    }
    if package_key().is_match(token) {
        return false;
    }
    !token.ends_with('/')
}

/// Family stems: the leading letters + digits of each token, before any `/`
/// composite tail. Never expanded.
#[must_use]
pub fn stems_from_tokens<S: AsRef<str>>(tokens: impl IntoIterator<Item = S>) -> BTreeSet<String> {
    let mut stems = BTreeSet::new();
    for token in tokens {
        let head = token.as_ref().split('/').next().unwrap_or_default();
        if let Some(m) = stem_prefix().find(head) {
            stems.insert(m.as_str().to_string());
        }
    }
    stems
}

/// Concrete part tokens in `text`; when `stems` is given and non-empty, only
/// tokens sharing one of the document's own family stems.
#[must_use]
pub fn parts_from_text(text: &str, stems: Option<&BTreeSet<String>>) -> Vec<String> {
    let mut found: Vec<String> = Vec::new();
    for cap in part_token().captures_iter(&normalize(text)) {
        let Some(token) = cap.get(1).map(|m| m.as_str().to_string()) else {
            continue;
        };
        if !is_concrete_part_token(&token) {
            continue;
        }
        if let Some(stems) = stems {
            if !stems.is_empty() && !stems.iter().any(|s| token.starts_with(s.as_str())) {
                continue;
            }
        }
        if !found.contains(&token) {
            found.push(token);
        }
    }
    found
}

/// Wildcard series tokens in `text`, verbatim as printed.
#[must_use]
pub fn wildcards_from_text(text: &str) -> Vec<String> {
    let mut found: Vec<String> = Vec::new();
    for cap in series_token().captures_iter(&normalize(text)) {
        let Some(token) = cap.get(1).map(|m| m.as_str().to_string()) else {
            continue;
        };
        if is_wildcard_token(&token) && !found.contains(&token) {
            found.push(token);
        }
    }
    found
}

/// One printed package token: `LQFP64 -> (LQFP, 64)`.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct PackageBogey {
    /// Package family as printed.
    pub package_family: String,
    /// Pin count parsed from the token.
    pub pin_count: i64,
}

/// Pin-count bogeys printed in `text` — the pin lane's denominators.
#[must_use]
pub fn packages_from_text(text: &str) -> Vec<PackageBogey> {
    let upper = normalize(text).to_uppercase();
    let mut out: Vec<PackageBogey> = Vec::new();
    let mut seen: BTreeSet<(String, i64)> = BTreeSet::new();
    for caps in package_key().captures_iter(&upper) {
        let family = caps.get(1).or_else(|| caps.get(4));
        let count = caps.get(2).or_else(|| caps.get(3));
        let (Some(family), Some(count)) = (family, count) else {
            continue;
        };
        let Ok(pin_count) = count.as_str().parse::<i64>() else {
            continue;
        };
        if !(3..=2000).contains(&pin_count) {
            continue;
        }
        let family = family.as_str().to_uppercase();
        if !seen.insert((family.clone(), pin_count)) {
            continue;
        }
        out.push(PackageBogey {
            package_family: family,
            pin_count,
        });
    }
    out
}

// ---------------------------------------------------------------------------
// Attribute vocabulary
// ---------------------------------------------------------------------------

/// Data-column labels marking a ratings/characteristics table rather than a
/// device matrix: MIN/MAX/UNIT, bare units, or a value range.
fn unit_label() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(concat!(
            r"(?i)^(?:[-–]?\d[\d.,]*\s*)?(?:v|mv|a|ma|[µu]a|na|w|mw|°c|c|mhz|khz|hz|",
            r"ohm|ω|kω|ms|us|ns|%|value/units?|units?|min|max|typ|nom)",
            r"(?:\s+to\s+.*)?$|.*\bunits?\b"
        ))
    })
}

/// Electrical-characteristics headers (Symbol/Parameter/Conditions/Min/Typ/
/// Max/Unit) mention frequency and temperature but are not device tables.
fn parametric_header() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(concat!(
            r"(?i)^(?:symbol|parameter|conditions?|min\.?|typ\.?|max\.?|unit|units|",
            r"nom\.?|description|comments?|test\s+conditions?)$"
        ))
    })
}

/// A grid row that is really a table caption ("Table 1.12 Product list").
fn title_row() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"(?i)^(?:table|tab\.)\s*\d"))
}

/// The printed row/column labels that address each knife attribute. Order
/// matters where labels overlap: data flash and code flash are both "flash",
/// and the RA2E2 2 KB read came from ignoring the qualifier.
const ATTRIBUTE_PATTERNS: &[(&str, &str)] = &[
    ("data_flash_kb", r"(?i)\bdata\s+(?:flash|area)\b"),
    ("eeprom_kb", r"(?i)\beeprom\b"),
    // The reference's second alternative is `\bflash\b(?!\s+data)`; the regex
    // crate has no lookahead, so `code_flash_hit` replays it.
    (
        "code_flash_kb",
        r"(?i)\b(?:code|program)\s+(?:flash|area|memory)\b",
    ),
    ("sram_kb", r"(?i)\bs?ram\b"),
    (
        "freq_mhz",
        r"(?i)\b(?:max(?:imum|\.)?\s+)?(?:cpu\s+|core\s+|system\s+)?(?:frequency|clock\s+(?:speed|frequency|rate)|speed)\b|\bmhz\b|\bf\s*max\b",
    ),
    ("core", r"(?i)\b(?:core|cortex|cpu)\b"),
    ("package", r"(?i)\bpackages?\b"),
    (
        "pin_count",
        r"(?i)\b(?:pins?|pin\s+count|gpios?|i/os?|i/o\s+(?:pins|count))\b",
    ),
    ("adc_channels", r"(?i)\badcs?\b"),
    ("dac_channels", r"(?i)\bdacs?\b"),
    ("timers", r"(?i)\btimers?\b"),
    ("pwm_channels", r"(?i)\bpwm\b"),
    (
        "operating_voltage",
        r"(?i)\b(?:operating|supply|vdd|power\s+supply)\s*(?:voltage|range)?\b|\bvdd\b",
    ),
    (
        "temp_range",
        r"(?i)\b(?:operating\s+)?temperature\b|\btemp\.?\s+range\b",
    ),
    (
        "standby_current_ua",
        r"(?i)\b(?:standby|stop|sleep|shutdown|deep\s*sleep|power[\s-]?down)\b.*?\b(?:current|consumption|[µu]a)\b",
    ),
    ("usart_count", r"(?i)\bu?s?arts?\b|\buart\b"),
    ("spi_count", r"(?i)\bspi\b"),
    ("i2c_count", r"(?i)\bi2c\b"),
    ("can_count", r"(?i)\bcan(?:\s*fd)?\b"),
    ("usb", r"(?i)\busb\b"),
    ("ethernet", r"(?i)\bethernet\b|\beth\b"),
];

/// Every attribute name in precedence order.
#[must_use]
pub fn attribute_names() -> Vec<&'static str> {
    ATTRIBUTE_PATTERNS.iter().map(|(n, _)| *n).collect()
}

/// The reference's `\bflash\b(?!\s+data)` replayed: a bare "flash" not
/// immediately followed by a "data" qualifier counts as code flash.
fn code_flash_hit(text: &str) -> bool {
    static PRIMARY: OnceLock<Regex> = OnceLock::new();
    static FLASH: OnceLock<Regex> = OnceLock::new();
    static TAIL_DATA: OnceLock<Regex> = OnceLock::new();
    let primary = PRIMARY.get_or_init(|| re(r"(?i)\b(?:code|program)\s+(?:flash|area|memory)\b"));
    if primary.is_match(text) {
        return true;
    }
    let flash = FLASH.get_or_init(|| re(r"(?i)\bflash\b"));
    let tail_data = TAIL_DATA.get_or_init(|| re(r"(?i)^\s+data"));
    flash.find_iter(text).any(|m| {
        text.get(m.end()..)
            .is_some_and(|rest| !tail_data.is_match(rest))
    })
}

/// Attribute names addressed by one printed row/column label, with qualifier
/// precedence: "Data area (KB)" is data flash, not code flash.
#[must_use]
pub fn attributes_in_label(label: &str) -> Vec<String> {
    let text = normalize(label);
    if text.is_empty() {
        return Vec::new();
    }
    let mut hits: Vec<String> = Vec::new();
    for (name, src) in ATTRIBUTE_PATTERNS {
        let hit = if *name == "code_flash_kb" {
            code_flash_hit(&text)
        } else {
            re(src).is_match(&text)
        };
        if hit {
            hits.push((*name).to_string());
        }
    }
    if hits.iter().any(|h| h == "data_flash_kb") && hits.iter().any(|h| h == "code_flash_kb") {
        hits.retain(|h| h != "code_flash_kb");
    }
    if hits.iter().any(|h| h == "eeprom_kb")
        && hits.iter().any(|h| h == "code_flash_kb")
        && !text.to_lowercase().contains("flash")
    {
        hits.retain(|h| h != "code_flash_kb");
    }
    hits
}

// ---------------------------------------------------------------------------
// Device-table classification
// ---------------------------------------------------------------------------

/// Printed orientation of a device table.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Orientation {
    /// Variants run across the header (GD32/ST/Microchip family matrices).
    #[serde(rename = "parts_as_columns")]
    PartsAsColumns,
    /// Variants run down the first column (selection guides).
    #[serde(rename = "parts_as_rows")]
    PartsAsRows,
}

/// A located device-comparison / feature table — the census's evidence that
/// the document governs more than one orderable part.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TableDescription {
    /// Column- or row-oriented.
    pub orientation: Orientation,
    /// Rows in the stripped grid.
    pub rows: i64,
    /// Columns in the stripped grid.
    pub columns: i64,
    /// Variant columns/rows carried.
    pub variant_count: i64,
    /// Variant labels as printed (capped at 64).
    pub variant_labels: Vec<String>,
    /// Wildcard series tokens the header prints.
    pub series_tokens: Vec<String>,
    /// Attribute names the table addresses, in first-hit order.
    pub attributes_addressed: Vec<String>,
    /// How many rows carry an attribute hit.
    pub attribute_rows: i64,
    /// 1-based page the table was found on (added by [`census_document`]).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub page: Option<i64>,
}

/// Drop leading rows that are a table caption merged into the grid
/// ("Table 1.12 Product list (1 of 2)" spanning every column).
#[must_use]
pub fn strip_title_rows(rows: &[Vec<Option<String>>]) -> Vec<Vec<Option<String>>> {
    let mut out: Vec<Vec<Option<String>>> = rows.to_vec();
    while !out.is_empty() {
        let first = out.first().map_or_else(Vec::new, |r| {
            r.iter().map(|c| cell(c.as_deref())).collect::<Vec<_>>()
        });
        let filled: Vec<&String> = first.iter().filter(|c| !c.is_empty()).collect();
        if filled.len() == 1
            && filled
                .first()
                .is_some_and(|f| title_row().is_match(f.as_str()))
        {
            out.remove(0);
            continue;
        }
        break;
    }
    out
}

/// Return a device-table description, or `None` when the grid is not one: a
/// ratings/characteristics table (MIN/MAX/UNIT columns), an electrical
/// characteristics table (Symbol/Parameter/Conditions headers), or too small
/// to compare anything.
#[must_use]
#[allow(
    clippy::too_many_lines,
    reason = "faithful sequential port of classify_table"
)]
pub fn classify_table(rows: &[Vec<Option<String>>]) -> Option<TableDescription> {
    let rows = strip_title_rows(rows);
    if rows.len() < 3 {
        return None;
    }
    let first = rows.first()?;
    if first.is_empty() || first.len() < 3 {
        return None;
    }
    let width = rows.iter().map(Vec::len).max()?;

    let grid: Vec<Vec<String>> = rows
        .iter()
        .map(|row| {
            let mut g: Vec<String> = row.iter().map(|c| cell(c.as_deref())).collect();
            g.resize(width, String::new());
            g
        })
        .collect();

    let label_cols = 2.min(width - 1);
    let mut row_attributes: Vec<String> = Vec::new();
    let mut row_hits = 0usize;
    let mut carried = String::new();
    for row in grid.iter().skip(1) {
        let mut label = row
            .iter()
            .take(label_cols)
            .filter(|p| !p.is_empty())
            .cloned()
            .collect::<Vec<_>>()
            .join(" ");
        if row.first().is_some_and(|c| !c.is_empty()) {
            carried = row.first().cloned().unwrap_or_default();
        } else if label_cols > 1 {
            if let Some(second) = row.get(1).filter(|c| !c.is_empty()) {
                label = format!("{carried} {second}");
            }
        }
        let hits = attributes_in_label(&label);
        if !hits.is_empty() {
            row_hits += 1;
            for hit in hits {
                if !row_attributes.contains(&hit) {
                    row_attributes.push(hit);
                }
            }
        }
    }

    let mut header_attributes: Vec<String> = Vec::new();
    for c in grid.first().into_iter().flatten().skip(1) {
        for hit in attributes_in_label(c) {
            if !header_attributes.contains(&hit) {
                header_attributes.push(hit);
            }
        }
    }

    let header_cells: Vec<&String> = grid
        .first()
        .into_iter()
        .flatten()
        .filter(|c| !c.is_empty())
        .collect();
    let parametric_header_cells = grid
        .first()
        .into_iter()
        .chain(grid.get(1))
        .flatten()
        .filter(|c| !c.is_empty() && parametric_header().is_match(c))
        .count();
    if parametric_header_cells >= 2 {
        return None;
    }
    let header_text = header_cells
        .iter()
        .map(|s| s.as_str())
        .collect::<Vec<_>>()
        .join(" ");
    let header_parts = parts_from_text(&header_text, None);
    let header_wildcards = wildcards_from_text(&header_text);
    let second_row_codes: Vec<String> = grid
        .get(1)
        .into_iter()
        .flatten()
        .skip(1)
        .filter(|c| !c.is_empty() && re(r"^[A-Z0-9]{1,6}$").is_match(c))
        .cloned()
        .collect();

    if row_attributes.len() >= 2 && row_hits >= 2 {
        let data_columns = width - label_cols;
        let variant_labels: Vec<String> = if !header_parts.is_empty() {
            header_parts
        } else if !second_row_codes.is_empty() {
            second_row_codes
        } else {
            grid.first()
                .into_iter()
                .flatten()
                .skip(label_cols)
                .filter(|c| !c.is_empty())
                .cloned()
                .collect()
        };
        if variant_labels
            .iter()
            .any(|l| parametric_header().is_match(l) || unit_label().is_match(l))
        {
            return None;
        }
        let variant_count = i64::try_from(data_columns)
            .unwrap_or(0)
            .max(i64::try_from(variant_labels.len()).unwrap_or(0));
        return Some(TableDescription {
            orientation: Orientation::PartsAsColumns,
            rows: i64::try_from(rows.len()).unwrap_or(0),
            columns: i64::try_from(width).unwrap_or(0),
            variant_count,
            variant_labels: variant_labels.into_iter().take(64).collect(),
            series_tokens: header_wildcards,
            attributes_addressed: row_attributes,
            attribute_rows: i64::try_from(row_hits).unwrap_or(0),
            page: None,
        });
    }

    if header_attributes.len() >= 2 {
        let first_col_text = grid
            .iter()
            .skip(1)
            .filter_map(|row| row.first().cloned())
            .collect::<Vec<_>>()
            .join(" ");
        let first_col_parts = parts_from_text(&first_col_text, None);
        let first_col_wildcards = wildcards_from_text(&first_col_text);
        if first_col_parts.len() >= 2 || first_col_wildcards.len() >= 2 {
            let attribute_rows = i64::try_from(header_attributes.len()).unwrap_or(0);
            return Some(TableDescription {
                orientation: Orientation::PartsAsRows,
                rows: i64::try_from(rows.len()).unwrap_or(0),
                columns: i64::try_from(width).unwrap_or(0),
                variant_count: i64::try_from(rows.len().saturating_sub(1)).unwrap_or(0),
                variant_labels: first_col_parts.into_iter().take(64).collect(),
                series_tokens: first_col_wildcards,
                attributes_addressed: header_attributes,
                attribute_rows,
                page: None,
            });
        }
    }
    None
}

// ---------------------------------------------------------------------------
// Grain rules
// ---------------------------------------------------------------------------

/// The device-table facts [`classify_grain`] / [`above_opn_kind`] read — no
/// more than the reference's `t.get(...)` calls need.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct TableFacts {
    /// Variant columns/rows the table carries.
    #[serde(default)]
    pub variant_count: i64,
    /// Attribute names it addresses.
    #[serde(default)]
    pub attributes_addressed: Vec<String>,
}

impl From<&TableDescription> for TableFacts {
    fn from(t: &TableDescription) -> Self {
        Self {
            variant_count: t.variant_count,
            attributes_addressed: t.attributes_addressed.clone(),
        }
    }
}

/// Inputs of [`classify_grain`] — the reference's keyword arguments.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct GrainArgs {
    /// `read_ok` or an instrument failure verdict.
    pub scope_status: String,
    /// `device_family | cpu_core | peripheral | unknown`.
    pub document_subject: String,
    /// `datasheet | reference_manual | ...`.
    pub document_class: String,
    /// Wildcards printed anywhere inspected.
    pub series_tokens: Vec<String>,
    /// Concrete parts the document itself names.
    pub parts_named: Vec<String>,
    /// Located device tables.
    #[serde(default)]
    pub device_tables: Vec<TableFacts>,
    /// Registry stems known for the document.
    pub registry_stems: i64,
}

/// Inputs of [`above_opn_kind`] — the reference's keyword arguments.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct KindArgs {
    /// `datasheet | reference_manual | ...`.
    pub document_class: String,
    /// Wildcards printed anywhere inspected.
    pub series_tokens: Vec<String>,
    /// Concrete parts the document itself names.
    pub parts_named: Vec<String>,
    /// Located device tables.
    #[serde(default)]
    pub device_tables: Vec<TableFacts>,
}

impl From<&GrainArgs> for KindArgs {
    fn from(a: &GrainArgs) -> Self {
        Self {
            document_class: a.document_class.clone(),
            series_tokens: a.series_tokens.clone(),
            parts_named: a.parts_named.clone(),
            device_tables: a.device_tables.clone(),
        }
    }
}

/// Grain verdict with its reasons, in decision order.
#[must_use]
#[allow(
    clippy::too_many_lines,
    reason = "faithful sequential port of classify_grain"
)]
pub fn classify_grain(args: &GrainArgs) -> (String, Vec<String>) {
    let mut reasons: Vec<String> = Vec::new();
    if args.scope_status != "read_ok" {
        return ("extraction_failed".into(), vec![args.scope_status.clone()]);
    }
    if args.document_subject == "cpu_core" || args.document_subject == "peripheral" {
        return (
            "not_device_family".into(),
            vec![format!("document_subject={}", args.document_subject)],
        );
    }
    if !args.series_tokens.is_empty() {
        reasons.push("wildcard_series_tokens_printed".into());
    }
    if args.parts_named.len() >= 2 {
        reasons.push(format!("parts_named={}", args.parts_named.len()));
    }
    if args.device_tables.iter().any(|t| t.variant_count >= 2) {
        reasons.push("device_table_with_variants".into());
    }
    if matches!(
        args.document_class.as_str(),
        "reference_manual" | "user_manual" | "programming_manual"
    ) {
        reasons.push(format!("document_class={}", args.document_class));
    }
    if args.registry_stems >= 2 {
        reasons.push(format!("registry_stems={}", args.registry_stems));
    }
    if !reasons.is_empty() {
        return ("above_opn".into(), reasons);
    }
    if args.parts_named.len() == 1 || args.registry_stems == 1 {
        return (
            "single_opn".into(),
            vec![
                format!("parts_named={}", args.parts_named.len()),
                format!("registry_stems={}", args.registry_stems),
            ],
        );
    }
    ("unknown".into(), vec!["no_grain_signal".into()])
}

/// What kind of above-OPN document this is, for scoping the lanes.
///
/// `family_matrix` — a device table with >=2 variants and >=3 attributes; the
/// knife lane's primary input. `manual` — reference/user/programming manual
/// (shared-content lane; the variant matrix usually lives in the datasheet).
/// `ordering_variants` — >=2 orderable parts printed but no attribute matrix
/// (package/temperature/reel variants of one device). `wildcard_only` — a
/// printed series wildcard and nothing else.
#[must_use]
pub fn above_opn_kind(args: &KindArgs) -> Option<String> {
    if args
        .device_tables
        .iter()
        .any(|t| t.variant_count >= 2 && t.attributes_addressed.len() >= 3)
    {
        return Some("family_matrix".into());
    }
    if matches!(
        args.document_class.as_str(),
        "reference_manual" | "user_manual" | "programming_manual"
    ) {
        return Some("manual".into());
    }
    if args.parts_named.len() >= 2 {
        return Some("ordering_variants".into());
    }
    if !args.series_tokens.is_empty() {
        return Some("wildcard_only".into());
    }
    None
}

// ---------------------------------------------------------------------------
// The census
// ---------------------------------------------------------------------------

/// Extracted tables of one page: each element is one table's grid.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PageTables {
    /// 1-based page the tables were found on.
    pub page: i64,
    /// Tables as the PDF layer extracted them (grids of rows of cells).
    pub tables: Vec<Vec<Vec<Option<String>>>>,
}

/// One printed package token located on a page.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PackageEntry {
    /// Package family as printed.
    pub package_family: String,
    /// Pin count parsed from the token.
    pub pin_count: i64,
    /// 1-based page the token was read from.
    pub page: i64,
}

/// A concrete part token the document itself prints, with its page.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PartNamed {
    /// The token, verbatim.
    pub token: String,
    /// 1-based page it was read from.
    pub page: i64,
}

/// One census row — the reference's wire, field for field.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CensusRow {
    /// Schema stamp.
    pub schema: String,
    /// Where the document came from (the caller fills it).
    pub source_path: Option<String>,
    /// Document hash (the caller fills it).
    pub document_sha256: Option<String>,
    /// Caller-supplied vendor.
    pub vendor: Option<String>,
    /// Caller-supplied category.
    pub category: Option<String>,
    /// Page count of the scanned document.
    pub page_count: Option<i64>,
    /// Caller-supplied registry stems.
    pub registry_stems: i64,
    /// `read_ok` or an instrument failure verdict.
    pub scope_status: String,
    /// Why the instrument failed, when it did.
    pub extraction_failed_reason: Option<String>,
    /// Document class from the scope row.
    pub document_class: String,
    /// Document subject from the scope row.
    pub document_subject: String,
    /// Verbatim cover title line(s).
    pub title_verbatim: Option<String>,
    /// Series/part tokens printed inside the scope statement.
    pub governs_series: Vec<String>,
    /// The verbatim printed scope statement.
    pub scope_statement: Option<String>,
    /// 1-based page carrying the statement.
    pub scope_page: Option<i64>,
    /// Wildcards printed anywhere inspected, in discovery order.
    pub series_tokens: Vec<String>,
    /// Family stems anchored on the document's own tokens.
    pub family_stems: Vec<String>,
    /// Concrete parts the document itself names.
    pub parts_named: Vec<PartNamed>,
    /// How many.
    pub parts_named_count: i64,
    /// Expected pin counts from printed package tokens.
    pub pin_bogeys: Vec<i64>,
    /// Printed package tokens located, with pages.
    pub packages: Vec<PackageEntry>,
    /// Located device tables.
    pub device_tables: Vec<TableDescription>,
    /// Whether any device table was located.
    pub device_table_located: bool,
    /// Attribute names the tables address, in first-hit order.
    pub attributes_addressed: Vec<String>,
    /// Max variant count across tables.
    pub variant_count: i64,
    /// `parts_or_variants x attributes_addressed`.
    pub denominator_cells: i64,
    /// Candidate pages inspected, 1-based, in scan order.
    pub pages_inspected: Vec<i64>,
    /// Grain verdict.
    pub grain: String,
    /// Grain reasons, in decision order.
    pub grain_reasons: Vec<String>,
    /// Above-OPN kind, when grain is `above_opn`.
    pub above_opn_kind: Option<String>,
}

/// Printed headings that mark a page worth inspecting for a device table.
fn device_table_keywords() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(concat!(
            r"(?i)device\s+(?:summary|information|overview|table|comparison|list|",
            r"selection|features?)|ordering\s+information|product\s+(?:selection|",
            r"line|matrix|table|overview)|family\s+(?:overview|comparison|features?)|",
            r"part\s+number\s+(?:table|list|matrix)|feature\s+(?:comparison|summary)|",
            r"memory\s+(?:size|configuration)|product\s+(?:list|lineup|line-up)|",
            r"peripheral\s+counts?|features?\s+and\s+peripheral"
        ))
    })
}

fn lane_pages_of(lane_pages: Option<&BTreeMap<String, Vec<i64>>>) -> Vec<i64> {
    let mut out = Vec::new();
    let Some(map) = lane_pages else {
        return out;
    };
    for lane in ["series_summary", "opn_decoder"] {
        if let Some(pages) = map.get(lane) {
            out.extend(pages.iter().copied());
        }
    }
    out
}

/// 1-based pages to inspect for device tables, ordering lists, packages: the
/// front pages, any lane-hinted pages, then keyword pages.
fn candidate_pages(
    page_count: usize,
    lane_pages: Option<&BTreeMap<String, Vec<i64>>>,
    pages: &[String],
) -> Vec<i64> {
    let mut out: Vec<i64> = Vec::new();
    let front = FRONT_PAGES.min(page_count);
    for p in 1..=front {
        out.push(i64::try_from(p).unwrap_or(0));
    }
    let count = i64::try_from(page_count).unwrap_or(0);
    for page in lane_pages_of(lane_pages) {
        if (1..=count).contains(&page) {
            out.push(page);
        }
    }
    let scan = KEYWORD_SCAN_PAGES.min(page_count);
    let keywords = device_table_keywords();
    for index in 0..scan {
        let Some(text) = pages.get(index) else {
            continue;
        };
        let normalized = normalize(text);
        if keywords.is_match(&normalized) || parts_from_text(&normalized, None).len() >= 3 {
            out.push(i64::try_from(index + 1).unwrap_or(0));
        }
    }
    let mut ordered: Vec<i64> = Vec::new();
    for page in out {
        if !ordered.contains(&page) {
            ordered.push(page);
        }
    }
    ordered.into_iter().take(MAX_TABLE_PAGES).collect()
}

/// Above-OPN document census over the PDF layer's output.
///
/// `pages` is every page text (1-based page `i` is `pages[i - 1]`) and
/// `tables` the grids the PDF layer extracted per page — exactly what the
/// reference's `census_document(path)` reads from its own document. The scope
/// row is recomputed from the same page texts via [`read_scope`].
#[must_use]
#[allow(
    clippy::too_many_lines,
    reason = "faithful sequential port of census_document"
)]
pub fn census_document(
    pages: &[String],
    tables: &[PageTables],
    lane_pages: Option<&BTreeMap<String, Vec<i64>>>,
    registry_stems: i64,
    vendor: Option<&str>,
    category: Option<&str>,
) -> CensusRow {
    let scope: ScopeRow = read_scope(pages, None);
    let mut row = CensusRow {
        schema: CENSUS_SCHEMA.into(),
        source_path: None,
        document_sha256: scope.document_sha256.clone(),
        vendor: vendor.map(str::to_string),
        category: category.map(str::to_string),
        page_count: Some(scope.page_count),
        registry_stems,
        scope_status: scope.status.clone(),
        extraction_failed_reason: scope.extraction_failed_reason.clone(),
        document_class: scope.document_class.clone(),
        document_subject: scope.document_subject.clone(),
        title_verbatim: scope.title_verbatim.clone(),
        governs_series: scope.governs_series.clone(),
        scope_statement: scope.scope_statement.clone(),
        scope_page: scope.scope_page,
        series_tokens: Vec::new(),
        family_stems: Vec::new(),
        parts_named: Vec::new(),
        parts_named_count: 0,
        pin_bogeys: Vec::new(),
        packages: Vec::new(),
        device_tables: Vec::new(),
        device_table_located: false,
        attributes_addressed: Vec::new(),
        variant_count: 0,
        denominator_cells: 0,
        pages_inspected: Vec::new(),
        grain: "unknown".into(),
        grain_reasons: Vec::new(),
        above_opn_kind: None,
    };

    if scope.status != "read_ok" {
        let args = GrainArgs {
            scope_status: scope.status.clone(),
            document_subject: "unknown".into(),
            document_class: "unknown".into(),
            series_tokens: Vec::new(),
            parts_named: Vec::new(),
            device_tables: Vec::new(),
            registry_stems,
        };
        let (grain, grain_reasons) = classify_grain(&args);
        row.grain = grain;
        row.grain_reasons = grain_reasons;
        return row;
    }

    let inspected = candidate_pages(pages.len(), lane_pages, pages);
    row.pages_inspected.clone_from(&inspected);

    let title_text = [
        scope.title_verbatim.as_deref(),
        scope.scope_statement.as_deref(),
    ]
    .into_iter()
    .flatten()
    .collect::<Vec<_>>()
    .join(" ");
    let mut series_tokens: Vec<String> = scope.governs_series.clone();
    for token in wildcards_from_text(&title_text) {
        if !series_tokens.contains(&token) {
            series_tokens.push(token);
        }
    }

    let mut parts: Vec<PartNamed> = Vec::new();
    let mut packages: Vec<PackageEntry> = Vec::new();
    let mut tables_out: Vec<TableDescription> = Vec::new();
    let mut page_parts_pending: Vec<(i64, &str)> = Vec::new();

    for &page_number in &inspected {
        let Some(text) = pages.get(usize::try_from(page_number - 1).unwrap_or(0)) else {
            continue;
        };
        for token in wildcards_from_text(text) {
            if !series_tokens.contains(&token) {
                series_tokens.push(token);
            }
        }
        page_parts_pending.push((page_number, text.as_str()));
        for bogey in packages_from_text(text) {
            if !packages
                .iter()
                .any(|p| p.package_family == bogey.package_family && p.pin_count == bogey.pin_count)
            {
                packages.push(PackageEntry {
                    package_family: bogey.package_family,
                    pin_count: bogey.pin_count,
                    page: page_number,
                });
            }
        }
        let Some(page_tables) = tables.iter().find(|t| t.page == page_number) else {
            continue;
        };
        for grid in &page_tables.tables {
            // The reference skips non-device tables and table-finder crashes;
            // neither is a verdict about the document.
            let Some(mut description) = classify_table(grid) else {
                continue;
            };
            description.page = Some(page_number);
            for token in &description.series_tokens {
                if !series_tokens.contains(token) {
                    series_tokens.push(token.clone());
                }
            }
            tables_out.push(description);
        }
    }

    // Family stems anchor parts_named to the document's own family: printed
    // wildcards first, then concrete parts the device tables label, then the
    // title. Without a stem, parts are still collected but unanchored.
    let mut stems = stems_from_tokens(series_tokens.iter().cloned());
    if stems.is_empty() {
        stems = stems_from_tokens(
            tables_out
                .iter()
                .flat_map(|t| t.variant_labels.iter())
                .filter(|label| is_concrete_part_token(label))
                .cloned(),
        );
    }
    if stems.is_empty() {
        stems = stems_from_tokens(
            part_token()
                .captures_iter(&normalize(&title_text))
                .filter_map(|c| c.get(1).map(|m| m.as_str().to_string()))
                .filter(|t| is_concrete_part_token(t))
                .collect::<Vec<_>>(),
        );
    }
    for (page_number, text) in &page_parts_pending {
        for token in parts_from_text(text, Some(&stems)) {
            if !parts.iter().any(|p| p.token == token) {
                parts.push(PartNamed {
                    token,
                    page: *page_number,
                });
            }
        }
    }
    for table in &tables_out {
        for token in &table.variant_labels {
            if !is_concrete_part_token(token) {
                continue;
            }
            if !stems.is_empty() && !stems.iter().any(|s| token.starts_with(s.as_str())) {
                continue;
            }
            if !parts.iter().any(|p| p.token == *token) {
                parts.push(PartNamed {
                    token: token.clone(),
                    page: table.page.unwrap_or(0),
                });
            }
        }
    }

    let mut attributes: Vec<String> = Vec::new();
    for table in &tables_out {
        for name in &table.attributes_addressed {
            if !attributes.contains(name) {
                attributes.push(name.clone());
            }
        }
    }
    let variant_count = tables_out
        .iter()
        .map(|t| t.variant_count)
        .max()
        .unwrap_or(0);
    let parts_or_variants = i64::try_from(parts.len()).unwrap_or(0).max(variant_count);

    row.series_tokens = series_tokens;
    row.family_stems = stems.into_iter().collect();
    row.parts_named_count = i64::try_from(parts.len()).unwrap_or(0);
    row.parts_named = parts;
    let mut bogeys: Vec<i64> = packages.iter().map(|p| p.pin_count).collect();
    bogeys.sort_unstable();
    bogeys.dedup();
    row.pin_bogeys = bogeys;
    row.packages = packages;
    row.device_table_located = !tables_out.is_empty();
    row.device_tables = tables_out;
    row.attributes_addressed = attributes;
    row.variant_count = variant_count;
    row.denominator_cells =
        parts_or_variants * i64::try_from(row.attributes_addressed.len()).unwrap_or(0);

    let args = GrainArgs {
        scope_status: scope.status.clone(),
        document_subject: scope.document_subject.clone(),
        document_class: scope.document_class.clone(),
        series_tokens: row.series_tokens.clone(),
        parts_named: row.parts_named.iter().map(|p| p.token.clone()).collect(),
        device_tables: row.device_tables.iter().map(TableFacts::from).collect(),
        registry_stems,
    };
    let (grain, grain_reasons) = classify_grain(&args);
    row.grain = grain;
    row.grain_reasons = grain_reasons;
    if row.grain == "above_opn" {
        row.above_opn_kind = above_opn_kind(&KindArgs::from(&args));
    }
    row
}

// ---------------------------------------------------------------------------
// Rollup
// ---------------------------------------------------------------------------

/// A row as [`summarize`] reads it — every field optional, exactly as the
/// reference's `row.get(...)` calls tolerate partial rows.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct SummaryRow {
    /// Vendor bucket key.
    #[serde(default)]
    pub vendor: Option<String>,
    /// Grain verdict.
    #[serde(default)]
    pub grain: Option<String>,
    /// Document class.
    #[serde(default)]
    pub document_class: Option<String>,
    /// Above-OPN kind.
    #[serde(default)]
    pub above_opn_kind: Option<String>,
    /// Whether a device table was located.
    #[serde(default)]
    pub device_table_located: bool,
    /// Scope statement, if printed.
    #[serde(default)]
    pub scope_statement: Option<String>,
    /// Parts named count.
    #[serde(default)]
    pub parts_named_count: i64,
    /// Pin bogeys.
    #[serde(default)]
    pub pin_bogeys: Vec<i64>,
    /// Denominator cells.
    #[serde(default)]
    pub denominator_cells: i64,
    /// Attributes addressed.
    #[serde(default)]
    pub attributes_addressed: Vec<String>,
}

impl From<&CensusRow> for SummaryRow {
    fn from(r: &CensusRow) -> Self {
        Self {
            vendor: r.vendor.clone(),
            grain: Some(r.grain.clone()),
            document_class: Some(r.document_class.clone()),
            above_opn_kind: r.above_opn_kind.clone(),
            device_table_located: r.device_table_located,
            scope_statement: r.scope_statement.clone(),
            parts_named_count: r.parts_named_count,
            pin_bogeys: r.pin_bogeys.clone(),
            denominator_cells: r.denominator_cells,
            attributes_addressed: r.attributes_addressed.clone(),
        }
    }
}

/// Overall rollup of [`summarize`].
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct OverallSummary {
    /// Rows rolled up.
    pub documents: i64,
    /// Grain counts.
    pub grain: BTreeMap<String, i64>,
    /// Document class counts.
    pub document_class: BTreeMap<String, i64>,
    /// Above-OPN subset.
    pub above_opn: AboveOpnSummary,
}

/// The above-OPN subset of [`OverallSummary`].
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct AboveOpnSummary {
    /// Above-OPN documents.
    pub documents: i64,
    /// Above-OPN kind counts.
    pub kind: BTreeMap<String, i64>,
    /// Documents with a located device table.
    pub device_table_located: i64,
    /// Documents printing a scope statement.
    pub scope_statement_printed: i64,
    /// Parts named across the subset.
    pub parts_named_total: i64,
    /// Documents naming at least one part.
    pub documents_with_parts_named: i64,
    /// Documents with pin bogeys.
    pub documents_with_pin_bogeys: i64,
    /// Denominator cells across the subset.
    pub denominator_cells_total: i64,
    /// Attribute coverage counts.
    pub attributes_addressed: BTreeMap<String, i64>,
}

/// Per-vendor rollup of [`summarize`].
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct VendorBucket {
    /// Vendor documents.
    pub documents: i64,
    /// Grain counts.
    pub grain: BTreeMap<String, i64>,
    /// Above-OPN documents.
    pub above_opn: i64,
    /// Above-OPN kind counts.
    pub above_opn_kind: BTreeMap<String, i64>,
    /// Above-OPN documents with a located device table.
    pub above_opn_device_table_located: i64,
    /// Parts named across the vendor's above-OPN documents.
    pub above_opn_parts_named_total: i64,
    /// Denominator cells across the same.
    pub above_opn_denominator_cells: i64,
    /// Attribute coverage counts.
    pub attributes_addressed: BTreeMap<String, i64>,
}

/// Per-vendor and overall counts for the census report.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Summary {
    /// Schema stamp.
    pub schema: String,
    /// Counts across every row.
    pub overall: OverallSummary,
    /// Counts keyed by vendor (the reference orders this by -documents).
    pub by_vendor: BTreeMap<String, VendorBucket>,
}

fn counter_inc(map: &mut BTreeMap<String, i64>, key: &str) {
    *map.entry(key.to_string()).or_insert(0) += 1;
}

fn truthy(s: Option<&str>) -> Option<&str> {
    s.filter(|v| !v.is_empty())
}

/// Per-vendor and overall counts for the census report.
#[must_use]
#[allow(
    clippy::too_many_lines,
    reason = "faithful sequential port of summarize"
)]
pub fn summarize(rows: &[SummaryRow]) -> Summary {
    let mut summary = Summary {
        schema: CENSUS_SUMMARY_SCHEMA.into(),
        overall: OverallSummary {
            documents: i64::try_from(rows.len()).unwrap_or(0),
            ..OverallSummary::default()
        },
        by_vendor: BTreeMap::new(),
    };
    for row in rows {
        let grain = truthy(row.grain.as_deref()).unwrap_or("unknown");
        counter_inc(&mut summary.overall.grain, grain);
        let class = truthy(row.document_class.as_deref()).unwrap_or("unknown");
        counter_inc(&mut summary.overall.document_class, class);
        let vendor = truthy(row.vendor.as_deref()).unwrap_or("unknown");
        let b = summary.by_vendor.entry(vendor.to_string()).or_default();
        b.documents += 1;
        counter_inc(&mut b.grain, grain);
        if grain != "above_opn" {
            continue;
        }
        let a = &mut summary.overall.above_opn;
        a.documents += 1;
        b.above_opn += 1;
        let kind = truthy(row.above_opn_kind.as_deref()).unwrap_or("unclassified");
        counter_inc(&mut a.kind, kind);
        counter_inc(&mut b.above_opn_kind, kind);
        if row.device_table_located {
            a.device_table_located += 1;
            b.above_opn_device_table_located += 1;
        }
        if row
            .scope_statement
            .as_deref()
            .is_some_and(|s| !s.is_empty())
        {
            a.scope_statement_printed += 1;
        }
        let n_parts = row.parts_named_count;
        a.parts_named_total += n_parts;
        b.above_opn_parts_named_total += n_parts;
        if n_parts != 0 {
            a.documents_with_parts_named += 1;
        }
        if !row.pin_bogeys.is_empty() {
            a.documents_with_pin_bogeys += 1;
        }
        let cells = row.denominator_cells;
        a.denominator_cells_total += cells;
        b.above_opn_denominator_cells += cells;
        for name in &row.attributes_addressed {
            counter_inc(&mut a.attributes_addressed, name);
            counter_inc(&mut b.attributes_addressed, name);
        }
    }
    summary
}
