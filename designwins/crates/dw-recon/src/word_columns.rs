//! Deterministic word-level package-column extraction for digitally-born PDFs
//! (`harness/electronics/word_columns.py`, ported 1:1).
//!
//! The page's own text spans are the primary source: identifiers are assigned
//! to package columns by x-overlap with the printed column headers and
//! matched to signal names within the same row band, so every emitted row
//! claim carries coordinate provenance back to the source document. Word
//! geometry is PDF-layer: the port consumes the `(x0, y0, x1, y1, text)`
//! tuples `get_text("words")` returns.

use std::collections::{BTreeMap, VecDeque};

use regex::Regex;
use serde_json::{json, Value};

/// `LQFP144`, `UFBGA176+25`, `64-TQFP` — printed package column headers.
const PACKAGE_HEADER: &str = r"^(?:(?:LQFP|QFN|TQFN|UFBGA|TFBGA|WLCSP|UFQFPN|VFBGA|LGA|BGA|CSP|QFP|TSSOP|SSOP|MSOP|SOP|SOIC|DIP)\d{2,4}(?:\+\d{1,3})?|\d{2,4}-(?:LQFP|QFN|TQFN|UFBGA|TFBGA|WLCSP|UFQFPN|VFBGA|LGA|BGA|CSP|QFP|TSSOP|SSOP|MSOP|SOP|SOIC|TQFP))$";

/// `Pin`, `Pin no.`, `Ball`, `No.` — the identifier column headers.
const PIN_COLUMN_HEADER: &str =
    r"(?i)^(?:pins?|pin\s*\(s\)|pin\s*no\.?|pin\s*number|pin#|ball|ball\s*no\.?|no\.?)$";

/// A bare `Pin(s)` header: demands pure numeric identifiers beneath.
const BARE_PIN_HEADER: &str = r"(?i)^pins?$";

/// `Name`, `Pin name`, `Pin/Ball name`.
const NAME_COLUMN_HEADER: &str = r"(?i)^(?:pin/ball|pin)?\s*name$";

/// A physical pin identifier word.
const IDENTIFIER: &str = r"^\d{1,4}$|^[A-Z]{1,2}\d{1,3}$";

/// A signal name word.
const SIGNAL_NAME: &str = r"^[A-Za-z][A-Za-z0-9_/.#&-]{0,23}$";

/// Identifier x-window slack left of the header.
const COLUMN_LEFT_MARGIN: f64 = 5.0;
/// Identifier x-window slack right of the header.
const COLUMN_RIGHT_MARGIN: f64 = 12.0;
/// Name-join slack either side of the name anchor.
const NAME_ANCHOR_MARGIN: f64 = 32.0;
/// Row band tolerance in y.
const ROW_BAND_TOLERANCE: f64 = 9.0;
/// Header band tolerance in y.
const HEADER_BAND_TOLERANCE: f64 = 30.0;
/// Package header band tolerance in y.
const PACKAGE_HEADER_BAND_TOLERANCE: f64 = 20.0;
/// Headers this far from the anchor in y are irrelevant.
const HEADER_ANCHOR_RELEVANCE: f64 = 60.0;
/// Word gap that still joins one name run.
const NAME_JOIN_GAP: f64 = 10.0;
/// Rows below which a column is noise.
const MINIMUM_COLUMN_ROWS: usize = 2;

/// How far back a headerless continuation page looks for its header band.
const CONTINUATION_LOOKBACK: usize = 8;

#[allow(
    clippy::expect_used,
    reason = "patterns are literals from the reference"
)]
fn re(src: &str) -> Regex {
    Regex::new(src).expect("pattern is a literal from the reference")
}

fn package_header_re() -> &'static Regex {
    use std::sync::OnceLock;
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(PACKAGE_HEADER))
}

fn pin_column_header_re() -> &'static Regex {
    use std::sync::OnceLock;
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(PIN_COLUMN_HEADER))
}

fn bare_pin_header_re() -> &'static Regex {
    use std::sync::OnceLock;
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(BARE_PIN_HEADER))
}

fn name_column_header_re() -> &'static Regex {
    use std::sync::OnceLock;
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(NAME_COLUMN_HEADER))
}

fn identifier_re() -> &'static Regex {
    use std::sync::OnceLock;
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(IDENTIFIER))
}

fn signal_name_re() -> &'static Regex {
    use std::sync::OnceLock;
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(SIGNAL_NAME))
}

/// One page word: `(x0, y0, x1, y1, text)`.
pub type Word = (f64, f64, f64, f64, String);

/// One extracted pin row claim with coordinate provenance.
#[derive(Debug, Clone)]
pub struct PinRowClaim {
    /// The printed pin identifier.
    pub pin_no: String,
    /// The joined signal name.
    pub name: String,
    /// The identifier word's box.
    pub identifier_span: (String, (f64, f64, f64, f64)),
    /// The joined name words' box.
    pub name_span: (String, (f64, f64, f64, f64)),
}

/// One package column: header, rows and their spans.
#[derive(Debug, Clone)]
pub struct PackageColumn {
    /// The printed header text.
    pub header: String,
    /// `"{header}@{round(x0)}"` — the dictionary key.
    pub column_key: String,
    /// The header word's box.
    pub header_span: (String, (f64, f64, f64, f64)),
    /// Rows sorted by identifier y.
    pub rows: Vec<PinRowClaim>,
}

/// `(pin_count, extra_ball_count)` of a package token, when it names one.
#[must_use]
pub fn package_pin_bogey(package_token: &str) -> Option<(i64, i64)> {
    let caps = re(r"[A-Z]+(\d{2,4})(?:\+(\d{1,3}))?").captures(package_token)?;
    if caps.get(0).is_none_or(|m| m.as_str() != package_token) {
        return None;
    }
    let count = caps.get(1)?.as_str().parse::<i64>().ok()?;
    let extra = caps
        .get(2)
        .map_or(Ok(0), |m| m.as_str().parse::<i64>())
        .unwrap_or(0);
    Some((count, extra))
}

fn center(word: &Word, axis: usize) -> f64 {
    match axis {
        0 => f64::midpoint(word.0, word.2),
        _ => f64::midpoint(word.1, word.3),
    }
}

fn window(header: &Word) -> (f64, f64) {
    (
        header.0 - COLUMN_LEFT_MARGIN,
        header.2 + COLUMN_RIGHT_MARGIN,
    )
}

fn in_window(value: f64, window: (f64, f64)) -> bool {
    window.0 <= value && value <= window.1
}

/// Words joined into name runs while the x gap stays within `NAME_JOIN_GAP`.
fn runs(line: &[Word]) -> Vec<Vec<Word>> {
    let mut sorted: Vec<&Word> = line.iter().collect();
    sorted.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal));
    let mut out: Vec<Vec<Word>> = Vec::new();
    for word in sorted {
        if let Some(last) = out.last_mut() {
            let tail = last.last().map(|w| w.2).unwrap_or_default();
            if word.0 - tail <= NAME_JOIN_GAP {
                last.push(word.clone());
                continue;
            }
        }
        out.push(vec![word.clone()]);
    }
    out
}

/// The y band between this identifier and its neighbours (Voronoi).
#[allow(
    clippy::indexing_slicing,
    reason = "indexes the identifier list as the reference does; the caller holds the index"
)]
fn voronoi_band(identifiers: &[Word], index: usize) -> (f64, f64) {
    let y = center(&identifiers[index], 1);
    let lower = if index > 0 {
        f64::midpoint(center(&identifiers[index - 1], 1), y)
    } else {
        y - ROW_BAND_TOLERANCE
    };
    let upper = if index + 1 < identifiers.len() {
        f64::midpoint(y, center(&identifiers[index + 1], 1))
    } else {
        y + ROW_BAND_TOLERANCE
    };
    (lower, upper)
}

fn header_like(word: &Word) -> bool {
    package_header_re().is_match(&word.4)
        || pin_column_header_re().is_match(&word.4)
        || name_column_header_re().is_match(&word.4)
}

fn in_header_band(header: &Word, words: &[Word], tolerance: f64) -> bool {
    words.iter().any(|word| {
        !std::ptr::eq(word, header) && header_like(word) && (word.1 - header.1).abs() <= tolerance
    })
}

/// Row claims for one name anchor: identifiers paired with names in band.
#[allow(
    clippy::too_many_lines,
    clippy::indexing_slicing,
    reason = "faithful port of _rows_for_anchor; indexes as the reference does"
)]
fn rows_for_anchor(
    identifier_words: &[Word],
    body: &[Word],
    anchor: &Word,
    identifier_windows: &[(f64, f64)],
    header_words: Option<&[Word]>,
) -> BTreeMap<String, PinRowClaim> {
    let name_window = (anchor.0 - NAME_ANCHOR_MARGIN, anchor.2 + NAME_ANCHOR_MARGIN);
    let mut windows: Vec<(f64, f64)> = identifier_windows.to_vec();
    if let Some(headers) = header_words {
        windows = identifier_windows
            .iter()
            .zip(headers.iter())
            .filter(|(_, header)| (header.1 - anchor.1).abs() <= HEADER_ANCHOR_RELEVANCE)
            .map(|(window, _)| *window)
            .collect();
    }
    let anchor_x = center(anchor, 0);
    let mut identifiers: Vec<&Word> = identifier_words.iter().collect();
    identifiers.sort_by(|a, b| {
        center(a, 1)
            .partial_cmp(&center(b, 1))
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    let mut rows: BTreeMap<String, PinRowClaim> = BTreeMap::new();
    for (index, identifier) in identifiers.iter().enumerate() {
        let y = center(identifier, 1);
        let (band_low, band_high) = voronoi_band(
            &identifiers.iter().map(|w| (*w).clone()).collect::<Vec<_>>(),
            index,
        );
        let mut candidates: Vec<&Word> = body
            .iter()
            .filter(|word| {
                let x = center(word, 0);
                in_window(x, name_window)
                    && !windows.iter().any(|window| in_window(x, *window))
                    && ((band_low <= center(word, 1) && center(word, 1) < band_high)
                        || (center(word, 1) - y).abs() <= ROW_BAND_TOLERANCE)
                    && signal_name_re().is_match(&word.4)
                    && word.4 != identifier.4
            })
            .collect();
        if candidates.is_empty() {
            continue;
        }
        candidates.sort_by(|a, b| {
            let ya = center(a, 1);
            let yb = center(b, 1);
            ya.partial_cmp(&yb)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal))
        });
        // Words within 4pt of a line's first word join that line.
        let mut lines: Vec<Vec<Word>> = Vec::new();
        for word in candidates {
            if let Some(last) = lines.last_mut() {
                if (center(word, 1) - center(&last[0], 1)).abs() <= 4.0 {
                    last.push(word.clone());
                    continue;
                }
            }
            lines.push(vec![word.clone()]);
        }
        let primary_index = lines
            .iter()
            .enumerate()
            .min_by(|(_, a), (_, b)| {
                (center(&a[0], 1) - y)
                    .abs()
                    .partial_cmp(&(center(&b[0], 1) - y).abs())
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .map_or(0, |(i, _)| i);
        let primary_line = &lines[primary_index];
        let line_runs = runs(primary_line);
        let primary_run = line_runs
            .iter()
            .min_by(|a, b| {
                let da =
                    (f64::midpoint(center(&a[0], 0), center(&a[a.len() - 1], 0)) - anchor_x).abs();
                let db =
                    (f64::midpoint(center(&b[0], 0), center(&b[b.len() - 1], 0)) - anchor_x).abs();
                da.partial_cmp(&db).unwrap_or(std::cmp::Ordering::Equal)
            })
            .cloned()
            .unwrap_or_default();
        let mut parts: Vec<Word> = Vec::new();
        for (index, line) in lines.iter().enumerate() {
            if index == primary_index {
                parts.extend(primary_run.iter().cloned());
            } else {
                let line_runs = runs(line);
                if line_runs.len() == 1 {
                    parts.extend(line_runs.into_iter().next().unwrap_or_default());
                }
            }
        }
        parts.sort_by(|a, b| {
            center(a, 1)
                .partial_cmp(&center(b, 1))
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal))
        });
        let text: String = parts.iter().map(|w| w.4.as_str()).collect();
        let x0 = parts.iter().map(|w| w.0).fold(f64::INFINITY, f64::min);
        let y0 = parts.iter().map(|w| w.1).fold(f64::INFINITY, f64::min);
        let x1 = parts.iter().map(|w| w.2).fold(f64::NEG_INFINITY, f64::max);
        let y1 = parts.iter().map(|w| w.3).fold(f64::NEG_INFINITY, f64::max);
        rows.entry(identifier.4.clone()).or_insert(PinRowClaim {
            pin_no: identifier.4.clone(),
            name: text.clone(),
            identifier_span: (
                identifier.4.clone(),
                (identifier.0, identifier.1, identifier.2, identifier.3),
            ),
            name_span: (text, (x0, y0, x1, y1)),
        });
    }
    rows
}

/// Python `round()` — banker's rounding, used for `column_key`.
#[allow(
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    reason = "the reference round()s the coordinate"
)]
fn py_round(value: f64) -> i64 {
    let floor = value.floor();
    let diff = value - floor;
    if diff > 0.5 {
        (floor + 1.0) as i64
    } else if diff < 0.5 {
        floor as i64
    } else {
        // .5 ties round to the even integer.
        let fl = floor as i64;
        if fl % 2 == 0 {
            fl
        } else {
            fl + 1
        }
    }
}

/// Package columns in insertion order (the reference's dict order).
pub type Columns = VecDeque<(String, PackageColumn)>;

/// The word pipeline: header band, name anchors, per-column row claims.
#[must_use]
#[allow(
    clippy::too_many_lines,
    clippy::indexing_slicing,
    reason = "faithful port of _pipeline; the geometry walks are the reference's"
)]
pub fn pipeline(words: &[Word], body_words: Option<&[Word]>) -> Columns {
    let header_candidates: Vec<&Word> = words
        .iter()
        .filter(|w| {
            (package_header_re().is_match(&w.4) || pin_column_header_re().is_match(&w.4))
                && in_header_band(
                    w,
                    words,
                    if package_header_re().is_match(&w.4) {
                        PACKAGE_HEADER_BAND_TOLERANCE
                    } else {
                        HEADER_BAND_TOLERANCE
                    },
                )
        })
        .collect();
    let name_anchors: Vec<&Word> = words
        .iter()
        .filter(|w| {
            name_column_header_re().is_match(&w.4)
                && in_header_band(w, words, HEADER_BAND_TOLERANCE)
        })
        .collect();
    if header_candidates.is_empty() || name_anchors.is_empty() {
        return Columns::new();
    }
    let mut header_words: Vec<Word> = Vec::new();
    for header in &header_candidates {
        let window = window(header);
        let beneath: Vec<&Word> = words
            .iter()
            .filter(|w| {
                w.1 > header.3 && identifier_re().is_match(&w.4) && in_window(center(w, 0), window)
            })
            .collect();
        if beneath.len() < MINIMUM_COLUMN_ROWS {
            continue;
        }
        if bare_pin_header_re().is_match(&header.4) {
            let pure_numbers = beneath
                .iter()
                .filter(|w| re(r"^\d{1,4}$").is_match(&w.4))
                .count();
            if pure_numbers < MINIMUM_COLUMN_ROWS {
                continue;
            }
        }
        header_words.push((*header).clone());
    }
    if header_words.is_empty() {
        return Columns::new();
    }
    let body: Vec<Word> = match body_words {
        None => {
            let header_bottom = header_words
                .iter()
                .map(|w| w.3)
                .fold(f64::INFINITY, f64::min);
            words
                .iter()
                .filter(|w| {
                    w.1 > header_bottom && !matches!(w.4.trim(), "-" | "\u{2014}" | "\u{2013}" | "")
                })
                .cloned()
                .collect()
        }
        Some(body) => body
            .iter()
            .filter(|w| !matches!(w.4.trim(), "-" | "\u{2014}" | "\u{2013}" | ""))
            .cloned()
            .collect(),
    };
    let identifier_windows: Vec<(f64, f64)> = header_words.iter().map(window).collect();
    let mut columns: Columns = Columns::new();
    for header in &header_words {
        let window = window(header);
        let identifier_words: Vec<Word> = body
            .iter()
            .filter(|w| identifier_re().is_match(&w.4) && in_window(center(w, 0), window))
            .cloned()
            .collect();
        if identifier_words.is_empty() {
            continue;
        }
        let mut best_rows: BTreeMap<String, PinRowClaim> = BTreeMap::new();
        let mut best_anchor: Option<&Word> = None;
        for anchor in &name_anchors {
            let rows = rows_for_anchor(
                &identifier_words,
                &body,
                anchor,
                &identifier_windows,
                Some(&header_words),
            );
            let anchor_distance = (center(anchor, 0) - center(header, 0)).abs();
            let best_distance = best_anchor.map(|b| (center(b, 0) - center(header, 0)).abs());
            if rows.len() > best_rows.len()
                || (rows.len() == best_rows.len()
                    && !rows.is_empty()
                    && best_distance.is_none_or(|d| anchor_distance < d))
            {
                best_rows = rows;
                best_anchor = Some(anchor);
            }
        }
        let mut ordered: Vec<PinRowClaim> = best_rows.into_values().collect();
        ordered.sort_by(|a, b| {
            a.identifier_span
                .1
                 .1
                .partial_cmp(&b.identifier_span.1 .1)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        if ordered.len() >= MINIMUM_COLUMN_ROWS {
            let key = format!("{}@{}", header.4, py_round(header.0));
            columns.push_back((
                key.clone(),
                PackageColumn {
                    header: header.4.clone(),
                    column_key: key,
                    header_span: (header.4.clone(), (header.0, header.1, header.2, header.3)),
                    rows: ordered,
                },
            ));
        }
    }
    columns
}

/// Extract package pin columns from one page's words. `page_words(page)`
/// replays the PDF layer (`_load_words`) including the continuation lookback:
/// when a page yields no columns, headers are re-validated on preceding pages
/// and applied to this page's rows.
#[must_use]
pub fn extract_pin_columns<F>(page_1based: i64, mut page_words: F) -> Columns
where
    F: FnMut(i64) -> Option<Vec<Word>>,
{
    let Some(words) = page_words(page_1based) else {
        return Columns::new();
    };
    let columns = pipeline(&words, None);
    if !columns.is_empty() {
        return columns;
    }
    for lookback in 1..=CONTINUATION_LOOKBACK {
        let target = page_1based - i64::try_from(lookback).unwrap_or(i64::MAX);
        let Some(previous) = page_words(target) else {
            break;
        };
        if !pipeline(&previous, None).is_empty() {
            return pipeline(&previous, Some(&words));
        }
    }
    Columns::new()
}

/// Pick the columns serving a requested package: exact normalized header
/// text wins, then the locator's package identity matcher, then all generic
/// pin columns merged.
#[must_use]
pub fn select_package_columns(columns: &Columns, requested_package: &str) -> Vec<PackageColumn> {
    let requested_key: String = requested_package
        .to_uppercase()
        .chars()
        .filter(char::is_ascii_alphanumeric)
        .collect();
    if requested_key.is_empty() {
        return Vec::new();
    }
    let exact: Vec<&PackageColumn> = columns
        .iter()
        .map(|(_, column)| column)
        .filter(|column| {
            column
                .header
                .to_uppercase()
                .chars()
                .filter(char::is_ascii_alphanumeric)
                .collect::<String>()
                == requested_key
        })
        .collect();
    if exact.len() == 1 {
        return exact.into_iter().cloned().collect();
    }
    let headers: Vec<String> = columns.iter().map(|(_, c)| c.header.clone()).collect();
    if let Some(matched) = crate::locator::match_package_column(requested_package, &headers) {
        let candidates: Vec<&PackageColumn> = columns
            .iter()
            .map(|(_, column)| column)
            .filter(|column| column.header == matched)
            .collect();
        if candidates.len() == 1 {
            return candidates.into_iter().cloned().collect();
        }
    }
    columns
        .iter()
        .map(|(_, column)| column)
        .filter(|column| !re(r"[A-Z]+\d").is_match(&column.header))
        .cloned()
        .collect()
}

/// The teacher-facing capability view of one column.
#[must_use]
pub fn capability_result(column: &PackageColumn) -> Value {
    json!({
        "package": column.header,
        "pins": column
            .rows
            .iter()
            .map(|row| json!({"pin_no": row.pin_no, "name": row.name}))
            .collect::<Vec<_>>(),
    })
}
