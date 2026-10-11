//! Errata delta records — port of `harness/electronics/errata_deltas.py`.
//!
//! Silicon-errata documents change what the datasheet asserts; the truth
//! audit needs the delta, not the prose. The deterministic readers walk
//! numbered issue sections and emit one record per issue: verbatim title,
//! symptom, work-around, and affected silicon revisions exactly as printed
//! (X-matrix resolved from the printed grid — a revision is affected only
//! when the document prints its mark). Nothing inferred: prose that cannot
//! be attributed to a numbered issue is not an errata record.

use std::collections::{BTreeMap, BTreeSet};

use regex::Regex;
use serde::{Deserialize, Serialize};
use std::sync::OnceLock;

/// The wire schema stamp, as the reference emits it.
pub const ERRATA_SCHEMA: &str = "harness.electronics-errata-delta.v1";

/// Verbatim-text cap from the reference (`[:1200]`).
const TEXT_CAP: usize = 1200;

#[allow(
    clippy::expect_used,
    reason = "patterns are literals from the reference"
)]
fn re(src: &str) -> Regex {
    Regex::new(src).expect("pattern is a literal from the reference")
}

/// Capture group `i` as `String`, empty when absent — no indexing.
fn grp(c: &regex::Captures<'_>, i: usize) -> String {
    c.get(i)
        .map_or_else(String::new, |m| m.as_str().to_string())
}

/// One numbered silicon errata issue.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct IssueRecord {
    /// Schema stamp.
    pub schema: String,
    /// `"issue"`.
    pub record_kind: String,
    /// The printed section id (`2.2.1`, or a TI advisory id).
    pub issue_id: String,
    /// Verbatim title, when printed.
    pub title_verbatim: Option<String>,
    /// Verbatim symptom, capped at 1200 chars.
    pub symptom_verbatim: Option<String>,
    /// Verbatim work-around, capped at 1200 chars.
    pub workaround_verbatim: Option<String>,
    /// device → revision tokens, from the printed mark matrix.
    pub affected_silicon: BTreeMap<String, Vec<String>>,
}

/// A Data Sheet Clarifications entry: the printed correction, verbatim.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ClarificationRecord {
    /// Schema stamp.
    pub schema: String,
    /// `"clarification"`.
    pub record_kind: String,
    /// The printed section id.
    pub issue_id: String,
    /// Verbatim title, when printed.
    pub title_verbatim: Option<String>,
    /// Verbatim correction, capped at 1200 chars.
    pub correction_verbatim: Option<String>,
}

/// A TI advisory detail section (`BCL16` + labeled fields).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AdvisoryRecord {
    /// Schema stamp.
    pub schema: String,
    /// `"issue"`.
    pub record_kind: String,
    /// The advisory id.
    pub issue_id: String,
    /// `Function` field, verbatim.
    pub title_verbatim: Option<String>,
    /// `Description` field, verbatim.
    pub symptom_verbatim: Option<String>,
    /// `Workaround` field, verbatim.
    pub workaround_verbatim: Option<String>,
    /// `Category` field, verbatim.
    pub category: Option<String>,
}

/// The document's own device claim.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct DeviceScope {
    /// The device title line as printed.
    pub device_title_verbatim: Option<String>,
    /// Concrete device tokens printed on the front page (capped at 40).
    pub device_tokens: Vec<String>,
}

/// One printed word with its bounding box (TI matrix input).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TiWord {
    /// Left edge.
    pub x0: f64,
    /// Top edge.
    pub y0: f64,
    /// Right edge.
    pub x1: f64,
    /// Bottom edge.
    pub y1: f64,
    /// The printed text.
    pub text: String,
}

/// Split page texts into whitespace-collapsed non-empty lines.
fn lines_of(page_texts: &[String]) -> Vec<String> {
    let mut out = Vec::new();
    for text in page_texts {
        for raw in text.split('\n') {
            let line = raw.split_whitespace().collect::<Vec<_>>().join(" ");
            if !line.is_empty() {
                out.push(line);
            }
        }
    }
    out
}

fn number_parts(identifier: &str) -> Vec<i64> {
    identifier
        .split('.')
        .filter_map(|t| t.parse::<i64>().ok())
        .collect()
}

fn title_char() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"[A-Za-z]{3,}"))
}

fn tableish_title() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"(?i)\d\s*(?:MHz|kHz|Hz|µA|uA|μA|mA|nA|mA)\b.{0,20}\bV(?:CC|DD)\s*="))
}

fn mark_line() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"^[Xx\-–—\s]*$"))
}

fn note_line() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"(?i)^note\b"))
}

fn plausible_title(line: &str) -> bool {
    if line.is_empty() || line.chars().count() < 4 || line.chars().count() > 120 {
        return false;
    }
    if !title_char().is_match(line) {
        return false;
    }
    if re(r"^[\d.\s-]+$").is_match(line) {
        return false;
    }
    if tableish_title().is_match(line) || mark_line().is_match(line) || note_line().is_match(line) {
        return false;
    }
    line.chars()
        .next()
        .is_some_and(|c| c.is_alphabetic() || c.is_uppercase())
}

fn issue_heading() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"^(\d+\.\d+\.\d+)\.?\s+(\S.{3,120})$"))
}

fn issue_number_only() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"^(\d+\.\d+\.\d+)\.?\s*$"))
}

fn clar_heading() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"^(\d+\.\d+)\.?\s+(\S.{3,120})$"))
}

fn clar_number_only() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"^(\d+\.\d+)\.?\s*$"))
}

fn workaround_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"(?i)^work[\s-]?around\b"))
}

fn affected_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"(?i)^affected\s+silicon\s+revisions?\b"))
}

fn clarifications_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"(?i)^data\s+sheet\s+clarifications?\b"))
}

fn device_line_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"^([A-Za-z][A-Za-z0-9/+._-]{2,60})\s*$"))
}

fn revision_line_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"^(Rev\.?\s*)?([A-Z]\d?|\d+[A-Z]?)\s*$"))
}

fn cap1200(s: &str) -> String {
    s.chars().take(TEXT_CAP).collect()
}

/// Parse the printed device → revision mark grid. X marks attribute
/// positionally when the mark row is unambiguous (marks == revisions);
/// anything else withholds rather than guesses.
fn affected_revisions(section: &[String]) -> BTreeMap<String, Vec<String>> {
    let mut affected: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let mut device: Option<String> = None;
    let mut revisions: Vec<String> = Vec::new();
    let mut marks: Vec<String> = Vec::new();

    // The reference's `_commit` attributes the marks seen so far against the
    // revisions seen so far and clears NOTHING — the caller decides. A
    // revision line commits the previous marks and starts a new mark row
    // while revisions ACCUMULATE; device/affected boundaries reset both.
    let commit = |device: &Option<String>,
                  revisions: &[String],
                  marks: &[String],
                  affected: &mut BTreeMap<String, Vec<String>>| {
        if let Some(dev) = device {
            if !revisions.is_empty() && !marks.is_empty() && marks.len() == revisions.len() {
                let entry = affected.entry(dev.clone()).or_default();
                for (revision, mark) in revisions.iter().zip(marks.iter()) {
                    if mark.to_uppercase() == "X" && !entry.contains(revision) {
                        entry.push(revision.clone());
                    }
                }
            }
        }
    };

    for line in section {
        if affected_re().is_match(line) {
            commit(&device, &revisions, &marks, &mut affected);
            device = None;
            revisions.clear();
            marks.clear();
            continue;
        }
        if mark_line().is_match(line) {
            marks.extend(line.split_whitespace().map(String::from));
            continue;
        }
        if let Some(caps) = revision_line_re().captures(line) {
            if device.is_some() {
                commit(&device, &revisions, &marks, &mut affected);
                marks.clear();
                revisions.push(grp(&caps, 2));
                continue;
            }
        }
        if device_line_re().is_match(line) && !revision_line_re().is_match(line) {
            commit(&device, &revisions, &marks, &mut affected);
            device = Some(line.clone());
            revisions.clear();
            marks.clear();
            affected.entry(line.clone()).or_default();
        }
    }
    commit(&device, &revisions, &marks, &mut affected);
    affected.retain(|_, revs| !revs.is_empty());
    affected
}

/// One record per numbered issue — the reference's `errata_records`.
///
/// # Lints
///
/// One sequential walk mirroring the reference (heading → workaround →
/// affected → mode dispatch), kept whole so the fail-closed order is
/// auditable.
#[must_use]
#[allow(
    clippy::too_many_lines,
    reason = "faithful sequential port of errata_records"
)]
pub fn errata_records(page_texts: &[String]) -> Vec<IssueRecord> {
    let lines = lines_of(page_texts);
    let mut records: Vec<IssueRecord> = Vec::new();
    let mut current: Option<IssueRecord> = None;
    let mut section: Vec<String> = Vec::new();
    let mut mode = "body";
    let mut last_id: Option<Vec<i64>> = None;

    let mut index = 0usize;
    while let Some(line) = lines.get(index).cloned() {
        let next_line = lines.get(index + 1).cloned().unwrap_or_default();
        let mut heading = issue_heading()
            .captures(&line)
            .map(|c| (grp(&c, 1), grp(&c, 2)));
        if heading.as_ref().is_some_and(|(_, t)| !plausible_title(t)) {
            heading = None;
        }
        let number = if heading.is_none() {
            issue_number_only()
                .captures(&line)
                .filter(|_| plausible_title(&next_line))
                .map(|c| grp(&c, 1))
        } else {
            None
        };
        if heading.is_some() || number.is_some() {
            let identifier = heading
                .as_ref()
                .map_or_else(|| number.clone().unwrap_or_default(), |(i, _)| i.clone());
            let parts = number_parts(&identifier);
            if last_id.as_ref().is_some_and(|last| parts <= *last) {
                index += 1;
                continue;
            }
            last_id = Some(parts);
            if let Some(mut done) = current.take() {
                done.affected_silicon = affected_revisions(&section);
                if done.symptom_verbatim.is_some()
                    || done.workaround_verbatim.is_some()
                    || !done.affected_silicon.is_empty()
                {
                    records.push(done);
                }
            }
            section.clear();
            let mut rec = IssueRecord {
                schema: ERRATA_SCHEMA.into(),
                record_kind: "issue".into(),
                issue_id: identifier,
                title_verbatim: heading.as_ref().map(|(_, t)| t.clone()),
                symptom_verbatim: None,
                workaround_verbatim: None,
                affected_silicon: BTreeMap::new(),
            };
            if heading.is_none() {
                rec.title_verbatim = Some(next_line);
                index += 1;
            }
            current = Some(rec);
            mode = "title";
            index += 1;
            continue;
        }
        let Some(rec) = current.as_mut() else {
            index += 1;
            continue;
        };
        if workaround_re().is_match(&line) {
            mode = "workaround";
            let rest = workaround_re().replace(&line, "");
            let rest = rest.trim_start_matches([' ', '.', ':', '-']);
            if !rest.is_empty() {
                rec.workaround_verbatim = Some(rest.to_string());
            }
            index += 1;
            continue;
        }
        if affected_re().is_match(&line) {
            mode = "affected";
            section.clear();
            index += 1;
            continue;
        }
        match mode {
            "affected" => section.push(line),
            "workaround" => {
                rec.workaround_verbatim = Some(match rec.workaround_verbatim.take() {
                    Some(prev) => format!("{prev} {line}"),
                    None => line,
                });
            }
            "title" => {
                let candidate = match rec.title_verbatim.clone() {
                    Some(prev) => format!("{prev} {line}"),
                    None => line.clone(),
                };
                if candidate.chars().count() <= 120
                    && !candidate.ends_with('.')
                    && !mark_line().is_match(&line)
                    && !note_line().is_match(&line)
                {
                    rec.title_verbatim = Some(candidate);
                } else {
                    rec.symptom_verbatim = Some(line);
                    mode = "body";
                }
            }
            _ => {
                rec.symptom_verbatim = Some(match rec.symptom_verbatim.take() {
                    Some(prev) => format!("{prev} {line}"),
                    None => line,
                });
            }
        }
        index += 1;
    }
    if let Some(mut done) = current.take() {
        done.affected_silicon = affected_revisions(&section);
        if done.symptom_verbatim.is_some()
            || done.workaround_verbatim.is_some()
            || !done.affected_silicon.is_empty()
        {
            records.push(done);
        }
    }
    for rec in &mut records {
        rec.symptom_verbatim = rec.symptom_verbatim.as_deref().map(cap1200);
        rec.workaround_verbatim = rec.workaround_verbatim.as_deref().map(cap1200);
    }
    records
}

/// Data-sheet clarifications: the printed corrections, verbatim — the
/// reference's `clarification_records`.
#[must_use]
pub fn clarification_records(page_texts: &[String]) -> Vec<ClarificationRecord> {
    let lines = lines_of(page_texts);
    let mut records: Vec<ClarificationRecord> = Vec::new();
    let mut inside = false;
    let mut current: Option<ClarificationRecord> = None;
    let mut last_id: Option<Vec<i64>> = None;
    let mut index = 0usize;
    while let Some(line) = lines.get(index).cloned() {
        let next_line = lines.get(index + 1).cloned().unwrap_or_default();
        if clarifications_re().is_match(&line) {
            inside = true;
            index += 1;
            continue;
        }
        if !inside {
            index += 1;
            continue;
        }
        let mut heading = clar_heading()
            .captures(&line)
            .map(|c| (grp(&c, 1), grp(&c, 2)));
        if heading.as_ref().is_some_and(|(_, t)| !plausible_title(t)) {
            heading = None;
        }
        let number = if heading.is_none() {
            clar_number_only()
                .captures(&line)
                .filter(|_| plausible_title(&next_line))
                .map(|c| grp(&c, 1))
        } else {
            None
        };
        if heading.is_some() || number.is_some() {
            let identifier = heading
                .as_ref()
                .map_or_else(|| number.clone().unwrap_or_default(), |(i, _)| i.clone());
            let parts = number_parts(&identifier);
            if last_id.as_ref().is_some_and(|last| parts <= *last) {
                index += 1;
                continue;
            }
            last_id = Some(parts);
            if let Some(done) = current.take() {
                if done.correction_verbatim.is_some() {
                    records.push(done);
                }
            }
            current = Some(ClarificationRecord {
                schema: ERRATA_SCHEMA.into(),
                record_kind: "clarification".into(),
                issue_id: identifier,
                title_verbatim: Some(
                    heading
                        .as_ref()
                        .map_or_else(|| next_line.clone(), |(_, t)| t.clone()),
                ),
                correction_verbatim: None,
            });
            index += if number.is_some() { 2 } else { 1 };
            continue;
        }
        if let Some(rec) = current.as_mut() {
            rec.correction_verbatim = Some(match rec.correction_verbatim.take() {
                Some(prev) => format!("{prev} {line}"),
                None => line,
            });
        }
        index += 1;
    }
    if let Some(mut done) = current.take() {
        done.correction_verbatim = done.correction_verbatim.as_deref().map(cap1200);
        if done.correction_verbatim.is_some() {
            records.push(done);
        }
    }
    for rec in &mut records {
        rec.correction_verbatim = rec.correction_verbatim.as_deref().map(cap1200);
    }
    records
}

fn device_title_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(
            r"(?i)^(?:\S.{2,50}\s+)?(?:Microcontrollers?|Microprocessors?|Processors?|Controllers?|MCUs?|SoCs?|Device Famil(?:y|ies)|Data\s+Converters?|Amplifiers?|Modules?|Transceivers?|Sensors?)\s*$",
        )
    })
}

fn device_tokens_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"\b([A-Z][A-Z0-9]{2,12}\d[A-Z0-9]{0,12}(?:-[A-Z0-9]+)?)\b"))
}

/// The document's own device claim — the reference's `device_scope`.
/// `filename` is accepted for signature parity; the reference does not use it.
#[must_use]
pub fn device_scope(front_text: &str, _filename: &str) -> DeviceScope {
    let lines: Vec<String> = front_text
        .split('\n')
        .map(|l| l.split_whitespace().collect::<Vec<_>>().join(" "))
        .filter(|l| !l.is_empty())
        .collect();
    let mut title_line = None;
    for line in lines.iter().take(15) {
        let lower = line.to_lowercase();
        if device_title_re().is_match(line)
            && !lower.starts_with("abstract")
            && !lower.starts_with("errata")
            && !lower.starts_with("known")
            && !lower.starts_with("introduction")
        {
            title_line = Some(line.clone());
            break;
        }
    }
    let head = lines.iter().take(15).cloned().collect::<Vec<_>>().join(" ");
    let mut tokens: Vec<String> = device_tokens_re()
        .captures_iter(&head)
        .map(|c| grp(&c, 1))
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    tokens.truncate(40);
    DeviceScope {
        device_title_verbatim: title_line,
        device_tokens: tokens,
    }
}

fn advisory_id() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"^([A-Z]{2,6}\d{1,3})$"))
}

fn ti_field() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"(?i)^(Category|Function|Description|Workaround)\s*:?\s*$"))
}

/// TI detail sections: advisory-id heading followed by labeled fields —
/// the reference's `ti_advisory_details`. An advisory mentioned twice is
/// one record; the detail fields fill the earlier one.
#[must_use]
pub fn ti_advisory_details(lines: &[String]) -> Vec<AdvisoryRecord> {
    let mut records: Vec<AdvisoryRecord> = Vec::new();
    let mut by_id: BTreeMap<String, usize> = BTreeMap::new();
    let mut current: Option<usize> = None;
    let mut field: Option<String> = None;
    for line in lines {
        if let Some(caps) = advisory_id().captures(line) {
            let id = grp(&caps, 1);
            if let Some(&idx) = by_id.get(&id) {
                current = Some(idx);
            } else {
                records.push(AdvisoryRecord {
                    schema: ERRATA_SCHEMA.into(),
                    record_kind: "issue".into(),
                    issue_id: id.clone(),
                    title_verbatim: None,
                    symptom_verbatim: None,
                    workaround_verbatim: None,
                    category: None,
                });
                by_id.insert(id, records.len() - 1);
                current = Some(records.len() - 1);
            }
            field = None;
            continue;
        }
        let Some(idx) = current else {
            continue;
        };
        if let Some(caps) = ti_field().captures(line) {
            field = Some(grp(&caps, 1).to_lowercase());
            continue;
        }
        let Some(rec) = records.get_mut(idx) else {
            continue;
        };
        match field.as_deref() {
            Some("category") => {
                rec.category = Some(cap1200(line).chars().take(80).collect());
                field = None;
            }
            Some("function") => {
                rec.title_verbatim = Some(match rec.title_verbatim.take() {
                    Some(prev) => format!("{prev} {line}"),
                    None => line.clone(),
                });
            }
            Some("description") => {
                rec.symptom_verbatim = Some(match rec.symptom_verbatim.take() {
                    Some(prev) => format!("{prev} {line}"),
                    None => line.clone(),
                });
            }
            Some("workaround") => {
                rec.workaround_verbatim = Some(match rec.workaround_verbatim.take() {
                    Some(prev) => format!("{prev} {line}"),
                    None => line.clone(),
                });
            }
            _ => {}
        }
    }
    for rec in &mut records {
        rec.title_verbatim = rec.title_verbatim.as_deref().map(cap1200);
        rec.symptom_verbatim = rec.symptom_verbatim.as_deref().map(cap1200);
        rec.workaround_verbatim = rec.workaround_verbatim.as_deref().map(cap1200);
    }
    records
}

fn ti_revision() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"(?i)^Rev\.?\s*([A-Z])$"))
}

/// TI summary matrix: advisory rows × revision columns, joined by
/// x-position of the printed check marks — the reference's
/// `ti_affected_matrix`. Text order alone cannot say which revision a mark
/// belongs to.
#[must_use]
pub fn ti_affected_matrix(page_words: &[Vec<TiWord>]) -> BTreeMap<String, Vec<String>> {
    let mut affected: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for words in page_words {
        let revision_words: Vec<&TiWord> = words
            .iter()
            .filter(|w| w.text == "Rev" || ti_revision().is_match(&w.text))
            .collect();
        let rows: Vec<&TiWord> = words
            .iter()
            .filter(|w| advisory_id().is_match(&w.text))
            .collect();
        let marks: Vec<&TiWord> = words.iter().filter(|w| w.text == "✓").collect();
        if revision_words.is_empty() || rows.is_empty() || marks.is_empty() {
            continue;
        }
        let mut columns: Vec<(f64, String)> = Vec::new();
        for word in words {
            if let Some(caps) = ti_revision().captures(&word.text) {
                columns.push((
                    f64::midpoint(word.x0, word.x1),
                    grp(&caps, 1).to_uppercase(),
                ));
                continue;
            }
            if word.text.chars().count() == 1
                && word
                    .text
                    .chars()
                    .next()
                    .is_some_and(|c| c.is_ascii_uppercase())
            {
                let center = f64::midpoint(word.x0, word.x1);
                let beneath = words.iter().any(|other| {
                    other.text == "Rev"
                        && (f64::midpoint(other.x0, other.x1) - center).abs() < 6.0
                        && (other.y0 - word.y1) >= 0.0
                        && (other.y0 - word.y1) <= 14.0
                });
                if beneath {
                    columns.push((center, word.text.clone()));
                }
            }
        }
        if columns.is_empty() {
            continue;
        }
        let mut sorted_rows: Vec<&&TiWord> = rows.iter().collect();
        sorted_rows.sort_by(|a, b| {
            f64::midpoint(a.y0, a.y1)
                .partial_cmp(&f64::midpoint(b.y0, b.y1))
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        for mark in &marks {
            let center_y = f64::midpoint(mark.y0, mark.y1);
            let center_x = f64::midpoint(mark.x0, mark.x1);
            let Some(nearest) = sorted_rows.iter().min_by(|a, b| {
                let da = (f64::midpoint(a.y0, a.y1) - center_y).abs();
                let db = (f64::midpoint(b.y0, b.y1) - center_y).abs();
                da.partial_cmp(&db).unwrap_or(std::cmp::Ordering::Equal)
            }) else {
                continue;
            };
            if (f64::midpoint(nearest.y0, nearest.y1) - center_y).abs() > 12.0 {
                continue;
            }
            let Some(letter) = columns
                .iter()
                .min_by(|a, b| {
                    let da = (a.0 - center_x).abs();
                    let db = (b.0 - center_x).abs();
                    da.partial_cmp(&db).unwrap_or(std::cmp::Ordering::Equal)
                })
                .map(|c| c.1.clone())
            else {
                continue;
            };
            affected
                .entry(nearest.text.clone())
                .or_default()
                .insert(format!("Rev {letter}"));
        }
    }
    affected
        .into_iter()
        .map(|(k, v)| (k, v.into_iter().collect()))
        .collect()
}
