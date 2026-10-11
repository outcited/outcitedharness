//! Power topology reader — port of `harness/electronics/power_topology.py`
//! (`read_topology`). Gold: `tests/fixtures/gold/power_topology_v1.jsonl`.
//!
//! Printed topology + integration class, **fail-closed**: an answer that is
//! not printed is absent, never guessed. Every rule is named by the corpus
//! trap that raised it (see the reference docstring — figure text like a
//! stray `boost` must not flip the answer; body words like "GSM Modules"
//! never set integration class).

use fancy_regex::Regex as FancyRegex;
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::sync::OnceLock;

/// How many front pages are scanned.
const SCAN_PAGES: usize = 3;
/// How far after a topology word its head noun may sit.
const WINDOW: usize = 48;
/// How far before a topology word an introducer may sit.
const INTRODUCER_BACK: usize = 100;
/// How far past a title anchor title nouns are trusted.
const TITLE_WINDOW: usize = 250;
/// How far before a class noun its article/negative context is checked.
const CLASS_BEFORE: usize = 24;
/// Quote width on the wire.
const QUOTE_LEN: usize = 120;

/// One page of flattened front text.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PageText {
    /// The page's text.
    pub text: String,
    /// The 1-based page number.
    pub page: u32,
}

/// One evidence row: what printed, where.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Evidence {
    /// Which field it supports.
    pub field: String,
    /// The claimed value.
    pub value: serde_json::Value,
    /// The justifying context, quoted.
    pub quote: String,
    /// Where it printed.
    pub page: u32,
}

/// What the front pages print about topology, integration, and isolation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TopologyRead {
    /// Canonical topology names, possibly several, in vocabulary order.
    pub topologies: Vec<String>,
    /// Names claimed only through a compound print (a four-switch
    /// buck-boost claims buck and boost as DERIVED).
    pub topologies_derived: Vec<String>,
    /// `integrated` | `controller` | `module` | `power_stage`, or none.
    pub integration_class: Option<String>,
    /// `true` when isolation prints, `false` when `non-isolated` prints.
    pub isolated: Option<bool>,
    /// What printed where.
    pub evidence: Vec<Evidence>,
    /// First-class absent answers.
    pub topology_absent: bool,
    /// First-class absent answers.
    pub integration_absent: bool,
}

#[allow(
    clippy::expect_used,
    reason = "patterns are literals from the reference"
)]
fn re(src: &str) -> Regex {
    Regex::new(src).expect("pattern is a literal from the reference")
}

#[allow(
    clippy::expect_used,
    reason = "patterns are literals from the reference"
)]
fn fre(src: &str) -> FancyRegex {
    FancyRegex::new(src).expect("pattern is a literal from the reference")
}

/// The topology vocabulary, in the reference's order (first match wins).
fn topo_vocab() -> &'static [(Regex, &'static str)] {
    static V: OnceLock<Vec<(Regex, &'static str)>> = OnceLock::new();
    V.get_or_init(|| {
        [
            ("buck-boost", r"(?i)buck[\s\-/]?boost"),
            ("sepic", r"(?i)\bsepic\b"),
            ("flyback", r"(?i)fly\s?back"),
            ("forward", r"(?i)\bforward\b"),
            ("push-pull", r"(?i)push[\s\-]?pull"),
            ("half-bridge", r"(?i)half[\s\-]?bridge"),
            ("full-bridge", r"(?i)full[\s\-]?bridge"),
            ("llc", r"(?i)\bllc\b"),
            ("resonant", r"(?i)\bresonant\b"),
            ("charge-pump", r"(?i)charge[\s\-]?pump"),
            ("inverting", r"(?i)\binverting\b"),
            ("buck", r"(?i)\bbuck\b|step[\s\-]?down"),
            ("boost", r"(?i)\bboost\b|step[\s\-]?up"),
            ("ldo", r"(?i)low[\s\-]?dropout|\bldo(?:s)?\b"),
        ]
        .into_iter()
        .map(|(n, p)| (re(p), n))
        .collect()
    })
}

fn head_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(
            r"(?i)converters?|controllers?|regulators?|modules?|switchers?|topolog\w*|power\s+stages?|conversion|gate\s+drivers?|led\s+drivers?|dc[\s\-]?dc",
        )
    })
}

fn intro_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"(?i)topolog\w*|supports\b|ideal\s+for\b|configur\w*|implement\w*"))
}

/// `(?<!non-)(?<!non )isolated` — lookbehind, hence fancy-regex.
fn iso_re() -> &'static FancyRegex {
    static R: OnceLock<FancyRegex> = OnceLock::new();
    R.get_or_init(|| {
        fre(
            r"(?i)(?<!non-)(?<!non )isolated|isolation\s+(?:barrier|rating|voltage)|\d[\d,.]*\s*-?\s*(?:kv|vrms)\s+isolation",
        )
    })
}

fn non_iso_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"(?i)non[\s\-]?isolated"))
}

fn ldo_qualifier() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(
            r"(?i)\b(?:dual|triple|three|two|single|cmos|ultra|micropower|variable|fixed|general|noise|iq|psrr|low|high|precision)\b|\d\s*$|\d$",
        )
    })
}

fn class_article() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"\b(?:the|an|a)\s*$"))
}

fn class_negative(value: &str) -> Option<&'static Regex> {
    static CONTROLLER: OnceLock<Regex> = OnceLock::new();
    static MODULE: OnceLock<Regex> = OnceLock::new();
    static POWER_STAGE: OnceLock<Regex> = OnceLock::new();
    match value {
        "controller" => Some(
            CONTROLLER.get_or_init(|| re(r"(?i)remote|motor|transmitter|most\b|multi[\s\-]phase")),
        ),
        "module" => Some(
            MODULE.get_or_init(|| re(r"(?i)dimm|gsm|gprs|camera|motor|server|hev\b|ev\b|led\s*$")),
        ),
        "power_stage" => Some(POWER_STAGE.get_or_init(|| re(r"(?i)form\b"))),
        _ => None,
    }
}

fn title_class() -> &'static [(Regex, &'static str)] {
    static V: OnceLock<Vec<(Regex, &'static str)>> = OnceLock::new();
    V.get_or_init(|| {
        [
            (r"(?i)power\s+stages?", "power_stage"),
            (r"(?i)\bmodules?\b|power\s+module", "module"),
            (r"(?i)\bcontrollers?\b", "controller"),
        ]
        .into_iter()
        .map(|(p, v)| (re(p), v))
        .collect()
    })
}

fn fet_direct() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"(?i)(?:integrated|internal)\s+(?:power\s+|single[\s\-]channel\s+)?(?:fets?|mosfets?|load\s+switch|power\s+switch)\b")
    })
}

fn fet_window() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"(?i)(?:integrated|internal)\s+[a-z0-9,\s\-ΩΩωµμ°%.]{0,30}(?:fets?|mosfets?|switch(?:es)?)\b")
    })
}

fn power_word() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"(?i)power|high[\s\-]?side|low[\s\-]?side|output|n[\s\-]?channel|nfet|dmos|gan|ω|Ω|\d+(?:\.\d+)?\s*-?\s*a\b")
    })
}

fn flatten(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Char index of a byte offset (match boundaries are always char boundaries).
fn char_idx(text: &str, byte: usize) -> usize {
    text.char_indices().take_while(|(i, _)| *i < byte).count()
}

/// Slice by character offsets exactly like Python's `text[start:end]` — the
/// reference's indexing is by code point and the corpus is full of `µ`, `Ω`,
/// `℃` (`HANDOFF.md` §5.4's lesson).
fn cslice(text: &str, start: usize, end: usize) -> String {
    text.chars()
        .skip(start)
        .take(end.saturating_sub(start))
        .collect()
}

/// `(start, end)` spans in `text` where title nouns are trusted.
fn title_spans(text: &str, part_number: Option<&str>, first_page: bool) -> Vec<(usize, usize)> {
    let mut spans = Vec::new();
    if first_page {
        spans.push((0, TITLE_WINDOW.min(text.chars().count())));
    }
    if let Some(part) = part_number {
        let stem = regex::escape(part);
        let family = re(r"[0-9](?:[0-9A-Za-z]*)$").replace(part, "").to_string();
        let mut pats = vec![stem.clone()];
        let fam_pat = regex::escape(&family);
        if family.chars().count() >= 4 && fam_pat != stem {
            pats.push(fam_pat);
        }
        for pat in pats {
            let rx = re(&format!("(?i){pat}"));
            for m in rx.find_iter(text) {
                let end = char_idx(text, m.end());
                spans.push((end, end + TITLE_WINDOW));
            }
        }
    }
    spans
}

fn quote_at(text: &str, start: usize, end: usize) -> String {
    cslice(text, start, end).chars().take(QUOTE_LEN).collect()
}

/// Read topology/integration/isolation evidence from front page texts —
/// the reference's `read_topology`, fail-closed.
///
/// # Lints
///
/// One linear read mirroring the reference: scan → titles → FET → isolation.
/// Splitting it would scatter the fail-closed order the corpus traps
/// depend on.
#[must_use]
#[allow(
    clippy::too_many_lines,
    reason = "faithful sequential port of read_topology"
)]
pub fn read_topology(pages: &[PageText], part_number: Option<&str>) -> TopologyRead {
    let flat: Vec<(String, u32)> = pages
        .iter()
        .take(SCAN_PAGES)
        .map(|p| (flatten(&p.text), p.page))
        .collect();

    let mut evidence: Vec<Evidence> = Vec::new();
    let mut topologies: Vec<String> = Vec::new();
    let mut derived: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
    let mut compound_spans: Vec<(usize, usize)> = Vec::new();
    let mut integration: Option<String> = None;
    let mut isolated: Option<bool> = None;

    for (text, page_no) in &flat {
        for (rx, name) in topo_vocab() {
            if topologies.iter().any(|t| t == name) {
                continue;
            }
            for m in rx.find_iter(text) {
                let m_start = char_idx(text, m.start());
                let m_end = char_idx(text, m.end());
                let qualified_ldo = *name == "ldo"
                    && ["LDO", "LDOS"].contains(&m.as_str().to_uppercase().as_str())
                    && ldo_qualifier().is_match(&cslice(text, m_start.saturating_sub(30), m_start));
                let head_window = cslice(text, m_end, m_end + WINDOW);
                let intro_window = cslice(text, m_start.saturating_sub(INTRODUCER_BACK), m_start);
                if qualified_ldo
                    || head_re().is_match(&head_window)
                    || intro_re().is_match(&intro_window)
                {
                    evidence.push(Evidence {
                        field: "topology".into(),
                        value: serde_json::Value::String((*name).to_string()),
                        quote: quote_at(text, m_start, m_end + WINDOW),
                        page: *page_no,
                    });
                    if *name == "buck-boost" {
                        compound_spans.push((m_start, m_end));
                    } else if matches!(*name, "buck" | "boost") {
                        for (b, e) in &compound_spans {
                            if m_start < *e && m_end > *b {
                                derived.insert((*name).to_string());
                                break;
                            }
                        }
                    }
                    topologies.push((*name).to_string());
                    break;
                }
            }
        }
    }

    let topologies_derived: Vec<String> = derived
        .into_iter()
        .filter(|d| topologies.contains(d))
        .collect();

    for (idx, (text, page_no)) in flat.iter().enumerate() {
        for (b, e) in title_spans(text, part_number, idx == 0) {
            let head = cslice(text, b, (b + TITLE_WINDOW).min(e));
            for (rx, value) in title_class() {
                let Some(m) = rx.find(&head) else {
                    continue;
                };
                let h_start = char_idx(&head, m.start());
                let h_end = char_idx(&head, m.end());
                let before = cslice(&head, h_start.saturating_sub(CLASS_BEFORE), h_start);
                if class_article().is_match(&before) {
                    continue;
                }
                if let Some(neg) = class_negative(value) {
                    if neg.is_match(&before) {
                        continue;
                    }
                }
                evidence.push(Evidence {
                    field: "integration_class".into(),
                    value: serde_json::Value::String((*value).to_string()),
                    quote: quote_at(text, b + h_start.saturating_sub(30), b + h_end + 30),
                    page: *page_no,
                });
            }
        }
    }

    let classes: Vec<String> = evidence
        .iter()
        .filter(|e| e.field == "integration_class")
        .filter_map(|e| e.value.as_str().map(String::from))
        .collect();
    for cls in ["power_stage", "module", "controller"] {
        if classes.iter().any(|c| c == cls) {
            integration = Some(cls.to_string());
            break;
        }
    }
    if integration.is_none() {
        integration = classes.first().cloned();
    }

    if integration.is_none() {
        for (text, page_no) in &flat {
            let direct = fet_direct().find(text);
            let hit = direct.or_else(|| {
                fet_window()
                    .find(text)
                    .filter(|m| power_word().is_match(m.as_str()))
            });
            if let Some(m) = hit {
                let start = char_idx(text, m.start());
                let end = char_idx(text, m.end());
                integration = Some("integrated".to_string());
                evidence.push(Evidence {
                    field: "integration_class".into(),
                    value: serde_json::Value::String("integrated".into()),
                    quote: quote_at(text, start.saturating_sub(30), end + 30),
                    page: *page_no,
                });
                break;
            }
        }
    }

    for (text, page_no) in &flat {
        if isolated.is_some() {
            break;
        }
        if let Some(m) = iso_re().find(text).ok().flatten() {
            let start = char_idx(text, m.start());
            let end = char_idx(text, m.end());
            isolated = Some(true);
            evidence.push(Evidence {
                field: "isolated".into(),
                value: serde_json::Value::Bool(true),
                quote: quote_at(text, start.saturating_sub(40), end + 40),
                page: *page_no,
            });
            continue;
        }
        if let Some(m) = non_iso_re().find(text) {
            let start = char_idx(text, m.start());
            let end = char_idx(text, m.end());
            isolated = Some(false);
            evidence.push(Evidence {
                field: "isolated".into(),
                value: serde_json::Value::Bool(false),
                quote: quote_at(text, start.saturating_sub(40), end + 40),
                page: *page_no,
            });
        }
    }

    TopologyRead {
        topology_absent: topologies.is_empty(),
        integration_absent: integration.is_none(),
        topologies,
        topologies_derived,
        integration_class: integration,
        isolated,
        evidence,
    }
}
