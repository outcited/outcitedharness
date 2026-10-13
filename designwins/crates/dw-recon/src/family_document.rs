//! Family-document reader — port of
//! `harness/electronics/family_document.py` (schema
//! `harness.electronics-family-document.v1`).
//!
//! What a reference manual or family datasheet states about itself: cover
//! identity, chapter list normalised to the peripheral vocabulary, every
//! peripheral instance named with page support, the feature bullets with
//! numbers typed and the qualifier kept verbatim, memory rows with sizes,
//! and package-qualified I/O counts. Nothing inferred; every row keeps its
//! receipt. As with [`crate::document_scope`] and [`crate::family_census`]
//! the port takes the PDF layer's output — page texts, extracted table
//! grids, bookmarks, the PDF title, the filename stem — instead of opening
//! documents itself.
//!
//! This module calls no models.

use std::collections::{BTreeMap, BTreeSet};

use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::sync::OnceLock;

use crate::family_census::{parts_from_text, wildcards_from_text, PageTables};

/// Schema stamp of a family-document row.
pub const CENSUS_SCHEMA: &str = "harness.electronics-family-document.v1";

/// Pages scanned for a `Features` heading past the opening pages.
pub const FEATURES_SEARCH_PAGES: usize = 120;

#[allow(
    clippy::expect_used,
    reason = "patterns are literals from the reference"
)]
fn re(src: &str) -> Regex {
    Regex::new(src).expect("pattern is a literal from the reference")
}

fn norm(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Document ids by vendor house style.
fn doc_id() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(concat!(
            r"\b(RM\d{4}|UM\d{4}|PM\d{4}|DS\d{4,5}|AN\d{4}|ES\d{4}|",
            r"SLAU\d{3}[A-Z]?|SLAS\d{3}[A-Z]?|SPRU[A-Z0-9]{3,4}|SPRS[A-Z0-9]{3,4}|SPNU\d{3}[A-Z]?|SPNS\d{3}[A-Z]?|SLVS[A-Z0-9]{3,4}|SLLS[A-Z0-9]{3,4}|",
            r"DS\d{8}[A-Z]?|DS\d{5}[A-Z]|",
            r"R01UH\d{4}[A-Z]{2}\d{4}|R01DS\d{4}[A-Z]{2}\d{4}|R01AN\d{4}[A-Z]{2}\d{4}|",
            r"[A-Z]{2,6}\d{2,4}RM|[A-Z]{2,6}\d{2,4}P\d{2,3}M\d{2,3}[A-Z]{2}\d{1,2}RM)\b"
        ))
    })
}

fn revision() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"\b(?:Rev(?:ision)?\.?\s*([0-9]+(?:\.[0-9]+)?[A-Z]?)|Version\s*([0-9]+(?:\.[0-9]+)?))\b")
    })
}

/// A peripheral class name (`"spi"`, `"timer"`, ...) and its chapter-title
/// pattern, in the reference's precedence order.
const CHAPTER_VOCAB: &[(&str, &str)] = &[
    (
        "memory_map",
        r"(?i)\bmemory (?:map|organi[sz]ation)\b|\bmemory and bus",
    ),
    (
        "flash",
        r"(?i)\b(?:embedded )?flash\b|\bnvm\b|\bprogram memory\b",
    ),
    ("eeprom", r"(?i)\beeprom\b(?!\s+emulation)"),
    ("sram", r"(?i)\bs?ram\b(?!p)"),
    ("ethernet", r"(?i)\bethernet|\bemac\b|\beth\b"),
    ("usb", r"(?i)\busb\b|\botg\b|\bucpd|\busb type-c"),
    ("sdmmc", r"(?i)\bsdmmc|\bsdio\b|\bsd/mmc|\bemmc|\bmmc\b"),
    (
        "xspi",
        r"(?i)\bquad-?spi|\bqspi|\bocto-?spi|\bospi|\bxspi|\bhexadeca|\bhyperbus",
    ),
    (
        "fmc",
        r"(?i)\bfmc\b|\bfsmc\b|\bflexible (?:static )?memory controller|\bemif\b|\bexternal memory interface|\bebi\b",
    ),
    (
        "can",
        r"\bCAN\b|(?i:\bcan\s*(?:2\.0|fd|bus|controller|module|interface)\b|\bfdcan|\bbxcan|\bmcan|\bdcan|\becan|\bcontroller area network|\bflexcan|\btwai\b)",
    ),
    ("lin", r"(?i)\blin\b"),
    ("i3c", r"(?i)\bi3c\b"),
    ("i2c", r"(?i)\bi2c|\bi²c|\binter-integrated"),
    (
        "usart",
        r"(?i)\busart|\buart|\blpuart|\bsci\b|\bserial communication interface",
    ),
    (
        "spi",
        r"(?i)\bspi\b|\bserial peripheral interface|\bmibspi|\bssi\b|\bi2s\b",
    ),
    (
        "display",
        r"(?i)\bltdc|\blcd\b|\bdsi\b|\bdisplay|\bgfxmmu|\bdma2d\b|\bchrom-?art|\bgpu\b|\bneochrom",
    ),
    ("dcmi", r"(?i)\bdcmi|\bcamera|\bpssi\b|\bcsi\b"),
    (
        "audio",
        r"(?i)\bsai\b|\bspdif\b|\bdfsdm\b|\bmdf\b|\badf\b|\bpdm\b",
    ),
    (
        "touch",
        r"(?i)\btsc\b|\btouch sensing|\bcaptivate\b|\bctsu\b|\bptc\b|peripheral touch controller|capacitive touch|\btouch\s+(?:controller|sensor|key|button)",
    ),
    ("hrtim", r"(?i)\bhrtim|\bhigh-resolution timer"),
    ("lptim", r"(?i)\blow[- ]power timer|\blptim"),
    ("rtc", r"(?i)\breal[- ]time clock|\brtc\b"),
    ("watchdog", r"(?i)\bwatchdog|\biwdg\b|\bwwdg\b|\bwdt\b"),
    (
        "adc",
        r"(?i)\badc|analog[- ]to[- ]digital|\bA/D\b|\bS12AD\b|\bsigma[- ]delta\s+(?:adc|converter)|\bSDADC",
    ),
    ("dac", r"(?i)\bdac|digital[- ]to[- ]analog|\bD/A\b"),
    (
        "temp_sensor",
        r"(?i)temperature\s+sensor|\bTSN\b|\bTEMPSENSOR\b",
    ),
    ("opamp", r"(?i)\bop-?amp|\boperational amplifier|\bpga\b"),
    ("comparator", r"(?i)\bcomp(?:arator)?s?\b"),
    (
        "crypto",
        r"(?i)\baes\b|\bcryp\b|\bcrypto\b|\bhash\b|\bpka\b|\bsaes\b|\brng\b|\btrng|\bcryptograph|\bsecurity\b|\bsecure\b|\bhsm\b|\bmathacl\b|\bsha-?[123]\b|\bsha-?256\b|advanced encryption standard|elliptic curve|public key|\becdsa\b|\becdh\b",
    ),
    ("trustzone", r"(?i)\btrustzone|\bgtzc\b|\btz\b"),
    ("npu", r"(?i)\bnpu\b|\bneural|\bai accelerator|\btinyengine"),
    (
        "wireless",
        r"(?i)\bradio\b|\bbluetooth|\bble\b|\b802\.15\.4|\bzigbee|\bthread\b|\bwi-?fi|\brf subsystem|\bsub-ghz",
    ),
    (
        "safety",
        r"(?i)\becc\b|\bcrc\b|cyclic redundancy|\bpbist\b|\bstc\b|\besm\b|\berror signaling|\bccm-r4\b|\bself-test\b|\bfunctional safety",
    ),
    (
        "debug",
        r"(?i)\bdebug|\bdbg\b|\bswd\b|\bjtag\b|\btrace\b|\betm\b|\bcoresight",
    ),
    (
        "power",
        r"(?i)\bpower (?:control|management|supply)|\bpwr\b|\bpmcu\b|\bpower-saving|low[- ]power modes|\bhibernat|\bbattery[- ]backed|\bdc-?dc\b|\bldo\b|voltage regulator|brown-?out|\bbod\b|energy management|\bemu\b|\benergy modes?\b|\bEM[0-4]\b",
    ),
    (
        "reset_clock",
        r"(?i)\breset and clock|\brcc\b|\bclock (?:system|tree|configuration|control)|\boscillator|\bsysctl\b|\bclock module",
    ),
    (
        "gpio",
        r"(?i)\bgpio|\bgeneral[- ]purpose i/?os?|\bi/o ports?|\bport (?:control|i/o)|\biomux\b|\bpinmux\b|\bi/o multiplexing",
    ),
    (
        "dma",
        r"(?i)\bdma\b|[µμu]dma\b|\bdirect memory access|\bgpdma\b|\bmdma\b|\bbdma\b|\bdmamux\b|\bedma",
    ),
    (
        "timer",
        r"(?i)\btimers?\b|\btim\d|\btimg\b|\btima\b|\bgptm\b|\bepwm\b|\becap\b|\beqep\b|\brti\b|\bhet\b|\bn2het\b|\boutput compare\b|\binput capture\b|\bpwm\b|\bqei\b|\bquadrature encoder\b|\bcryotimer\b|\bletimer\b|\bwtimer\b|\bpulse counter\b|\bpcnt\b|\bpca\b|programmable counter array",
    ),
    (
        "interrupts",
        r"(?i)\binterrupt|\bnvic\b|\bexti\b|\bvim\b|\bevents?\b|peripheral reflex system|\bprs\b",
    ),
    (
        "cpu_core",
        r"(?i)\b(?:cortex|cpu|processor core|core architecture|arm)\b|memory protection unit|\bmpu\b|floating[- ]point unit|\bfpu\b|\bc?8051\b|\bcip-51\b|\brisc-v\b|\bxtensa\b|\bc28x\b|\brl78\b|\brxv\d\b|\bavr\b(?:\s+(?:core|cpu))|\bmips\b",
    ),
];

/// Look-alike titles that must NOT claim the class; tried against the
/// exclusion first — a hit there blocks the class.
const CHAPTER_EXCLUDE: &[(&str, &str)] = &[
    (
        "cpu_core",
        r"(?i)\btimers?\b|arbitr|\bcpu (?:self-test|compare)|\bfilter\b|\bprimecell\b|dma\b",
    ),
    ("comparator", r"(?i)\bclock\b|\bbus\b|\bdcc\b|\bwindow\b"),
    ("trustzone", r"(?i)\btrip"),
    ("sram", r"(?i)\bmessage ram\b|\bprotection\b|\bmemory ram\b"),
    ("spi", r"(?i)\bexport\b|\bclb\b"),
    ("timer", r"(?i)\bwatchdog\b|\brtc\b|\breal-time clock"),
    ("gpio", r"(?i)\bsysconfig\b|\btool"),
    ("flash", r"(?i)\bprotection\b|\berase/program"),
    ("usb", r"(?i)\bsignals\b"),
];

/// Map a chapter title onto its peripheral class, or `None`.
#[must_use]
pub fn classify_chapter_title(title: &str) -> Option<String> {
    for (name, src) in CHAPTER_VOCAB {
        if !vocab_match(name, src, title) {
            continue;
        }
        if let Some((_, excl)) = CHAPTER_EXCLUDE.iter().find(|(n, _)| n == name) {
            if re(excl).is_match(title) {
                continue;
            }
        }
        return Some((*name).to_string());
    }
    None
}

/// Natural-order sort key: digit runs compare as numbers.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub enum NaturalSeg {
    /// A non-digit run.
    Text(String),
    /// A digit run, compared numerically.
    Num(i64),
}

/// `("TIM1", "TIM2", "TIM10")` sorts as a human would.
#[must_use]
pub fn natural(text: &str) -> Vec<NaturalSeg> {
    let mut out = Vec::new();
    let re_digits = re(r"(\d+)");
    let mut last = 0;
    for m in re_digits.find_iter(text) {
        let head = &text[last..m.start()];
        if !head.is_empty() {
            out.push(NaturalSeg::Text(head.to_string()));
        }
        out.push(NaturalSeg::Num(
            m.as_str().parse::<i64>().unwrap_or(i64::MAX),
        ));
        last = m.end();
    }
    if last < text.len() {
        out.push(NaturalSeg::Text(text[last..].to_string()));
    }
    out
}

// ---------------------------------------------------------------------------
// Look-around replays
// ---------------------------------------------------------------------------
// The regex crate has no look-around. The reference's handful of lookahead and
// lookbehind uses are replayed here on the same leftmost-first scan, keeping
// the patterns themselves literal to the reference.

/// The reference's `(?i)\beeprom\b(?!\s+emulation)`: a word-boundary
/// "eeprom" not immediately followed by "emulation".
fn eeprom_hit(text: &str) -> bool {
    let base = re(r"(?i)\beeprom\b");
    let tail = re(r"(?i)^\s+emulation");
    let hit = base
        .find_iter(text)
        .any(|m| !tail.is_match(text.get(m.end()..).unwrap_or("")));
    hit
}

/// The reference's `(?i)\bs?ram\b(?!p)`.
fn sram_hit(text: &str) -> bool {
    let base = re(r"(?i)\bs?ram\b");
    let tail = re(r"(?i)^p");
    let hit = base
        .find_iter(text)
        .any(|m| !tail.is_match(text.get(m.end()..).unwrap_or("")));
    hit
}

/// Vocab pattern dispatch: the two look-around patterns replay by hand.
fn vocab_match(name: &str, src: &str, text: &str) -> bool {
    match name {
        "eeprom" => eeprom_hit(text),
        "sram" => sram_hit(text),
        _ => re(src).is_match(text),
    }
}

/// The reference's `_COUNT_PREFIX` match at the start of `text`, replayed by
/// hand: its three alternatives carry lookahead guards the regex crate
/// cannot express. Returns (match end, digit count, word count).
fn count_prefix_match(text: &str) -> Option<(usize, Option<i64>, Option<String>)> {
    let prefix_end = re(r"(?i)^up\s+to\s+").find(text).map(|m| m.end());
    let mut starts: Vec<usize> = Vec::new();
    if let Some(e) = prefix_end {
        starts.push(e); // greedy optional: with the prefix first
    }
    starts.push(0);
    for pos in starts {
        let rest = &text[pos..];
        // (\d{1,2})\s*(?:x\s*|x\s+)
        if let Some(m) = re(r"^\d{1,2}\s*(?:×\s*|x\s+)").find(rest) {
            let digits = re(r"^\d{1,2}").find(rest).map_or("", |d| d.as_str());
            return Some((pos + m.end(), digits.parse::<i64>().ok(), None));
        }
        // (words)\s+(?=[A-Za-z0-9])
        let words: Vec<&str> = COUNT_WORDS.iter().map(|(w, _)| *w).collect();
        if let Some(c) = re(&format!(r"(?i)^({})\s+", words.join("|"))).captures(rest) {
            let end = c.get(0).map_or(0, |m| m.end());
            if re(r"^[A-Za-z0-9]").is_match(rest.get(end..).unwrap_or("")) {
                let word = c
                    .get(1)
                    .map(|g| g.as_str().to_lowercase())
                    .unwrap_or_default();
                return Some((pos + end, None, Some(word)));
            }
        }
        // (\d{1,2})\s+(?!(?:or|to|...)\b)(?=[A-Za-z])
        if let Some(m) = re(r"^\d{1,2}\s+").find(rest) {
            let tail = rest.get(m.end()..).unwrap_or("");
            let banned = re(concat!(
                r"^(?:or|to|and|x|bits?|Kbytes?|KB|MB|Mbytes?|MHz|kHz|GHz|V|mA|µA|µs|ns|ms|regions?|",
                r"wait|external|internal|independent|channels?|pins?|wire|lanes?|ports?|modes?|",
                r"levels?|priorit\w+|cycles?|entries|vectors?|slots?|bytes?|words?|segments?|commons?)\b"
            ));
            if !banned.is_match(tail) && re(r"^[A-Za-z]").is_match(tail) {
                let digits = re(r"^\d{1,2}").find(rest).map_or("", |d| d.as_str());
                return Some((pos + m.end(), digits.parse::<i64>().ok(), None));
            }
        }
    }
    None
}

/// The reference's `_LABEL_CUT.search(text)` start, replayed by hand: the
/// second alternative ends in a `(?=\S)` guard.
fn label_cut_at(text: &str) -> Option<usize> {
    let alt1 = re(
        r"(?i)^,\s+(?:each|all|with|including|supporting|configurable|capable|featuring|providing|up to|for)\b",
    );
    let alt2 = re(
        r"(?i)^\s+(?:with|supporting|including|featuring|capable of|configurable as|for periodic|for use|operates?|running)\s+",
    );
    for start in 0..=text.len() {
        let rest = text.get(start..).unwrap_or("");
        if rest.is_empty() && start != 0 {
            break;
        }
        if alt1.is_match(rest) {
            return Some(start);
        }
        if let Some(m) = alt2.find(rest) {
            if re(r"^\S").is_match(rest.get(m.end()..).unwrap_or("")) {
                return Some(start);
            }
        }
    }
    None
}

/// The reference's `_DESC_NOUN.search(clause)`: a word token that does not
/// start with one of the reference's banned units or joiners.
fn desc_noun_hit(text: &str) -> bool {
    let base = re(r"\b[A-Za-z][A-Za-z0-9/\-]{2,}\b");
    let banned = re(concat!(
        r"^(?:up|to|or|and|of|from|at|KB|MB|Kbytes?|Mbytes?|Kbit|Mbit|MHz|kHz|GHz|bit|bits|",
        r"V|mA|µA|ms|µs|ns|MSps|Msps|ksps)\b"
    ));
    let hit = base
        .find_iter(text)
        .any(|m| !banned.is_match(text.get(m.start()..).unwrap_or("")));
    hit
}

/// The reference's `re.split(r"(?<=[A-Za-z0-9)%])\.\s+(?=[A-Z])", p)`.
fn split_sentences(paragraph: &str) -> Vec<&str> {
    let _ = paragraph.len();
    let consume = re(r"\.\s+");
    let mut out = Vec::new();
    let mut seg_start = 0;
    let mut pos = 0;
    while pos < paragraph.len() {
        let rest = paragraph.get(pos..).unwrap_or("");
        if let Some(m) = consume.find(rest) {
            let abs_start = pos + m.start();
            let abs_end = pos + m.end();
            let before_ok = abs_start > 0
                && paragraph[..abs_start]
                    .chars()
                    .next_back()
                    .is_some_and(|c| c.is_ascii_alphanumeric() || c == ')' || c == '%');
            let after_ok = paragraph
                .get(abs_end..)
                .and_then(|t| t.chars().next())
                .is_some_and(|c| c.is_ascii_uppercase());
            if before_ok && after_ok {
                out.push(&paragraph[seg_start..abs_start]);
                seg_start = abs_end;
                pos = abs_end;
                continue;
            }
            pos = abs_start + 1;
            continue;
        }
        break;
    }
    out.push(&paragraph[seg_start..]);
    out
}

/// One split alternative: a consumed run plus optional guards on what
/// precedes and follows it, exactly as the reference's look-around.
struct SplitRule {
    /// The consumed run, anchored at the scan position.
    consume: &'static str,
    /// What must precede the run (`(?<=...)`).
    before: Option<&'static str>,
    /// What must follow the run (`(?=...)`).
    after: Option<&'static str>,
}

/// `re.split` semantics: leftmost non-overlapping matches, alternatives
/// tried in order at each position, delimiters removed.
fn split_rules<'a>(text: &'a str, rules: &[SplitRule]) -> Vec<&'a str> {
    let mut out = Vec::new();
    let mut seg_start = 0;
    let mut pos = 0;
    while pos < text.len() {
        let mut matched = false;
        for rule in rules {
            let rest = text.get(pos..).unwrap_or("");
            if rest.is_empty() {
                break;
            }
            let Some(m) = re(&format!(r"^{}", rule.consume)).find(rest) else {
                continue;
            };
            if let Some(before) = rule.before {
                if !re(before).is_match(text.get(..pos).unwrap_or("")) {
                    continue;
                }
            }
            if let Some(after) = rule.after {
                if !re(after).is_match(rest.get(m.end()..).unwrap_or("")) {
                    continue;
                }
            }
            let abs_end = pos + m.end();
            out.push(&text[seg_start..pos]);
            seg_start = abs_end;
            pos = abs_end;
            matched = true;
            break;
        }
        if matched {
            continue;
        }
        // Advance one char (UTF-8 aware) to the next scan position.
        pos += text[pos..].chars().next().map_or(1, char::len_utf8);
    }
    out.push(&text[seg_start..]);
    out
}

/// The reference's description-clause splitter: the alternation order of
/// `re.split` is preserved ("," runs first, then the guarded "and").
fn split_clauses(sentence: &str) -> Vec<&str> {
    static AFTER_AND: &str = concat!(
        r"^(?:up\s+to\s+)?(?:\d|(?:a|an|available|",
        r"one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|sixteen)\b)"
    );
    let rules = [
        SplitRule {
            consume: r",\s+(?:and\s+|as\s+well\s+as\s+)?",
            before: None,
            after: None,
        },
        SplitRule {
            consume: r"\s+and\s+",
            before: None,
            after: Some(AFTER_AND),
        },
        SplitRule {
            consume: r";\s+",
            before: None,
            after: None,
        },
        SplitRule {
            consume: r"\s+",
            before: None,
            after: Some(r"^(?:operating|running)\s+at\b"),
        },
        SplitRule {
            consume: r"\s+with\s+",
            before: None,
            after: Some(r"^(?:up\s+to\s+)?\d"),
        },
    ];
    split_rules(sentence, &rules)
}

/// The reference's features-table description splitter.
fn split_table_desc(description: &str) -> Vec<&str> {
    let rules = [
        SplitRule {
            consume: r"\s*;\s*",
            before: None,
            after: None,
        },
        SplitRule {
            consume: r"\.\s+",
            before: None,
            after: Some(r"^[A-Z]"),
        },
        SplitRule {
            consume: r"\s+",
            before: Some(r"\brange$"),
            after: Some(r"^[A-Z]"),
        },
        SplitRule {
            consume: r",\s+",
            before: None,
            after: Some(r"^(?:each\s+with\b|for\s+a\s+total\b|with\s+four\b)"),
        },
    ];
    split_rules(description, &rules)
}

// ---------------------------------------------------------------------------
// Identity
// ---------------------------------------------------------------------------

/// Lines a cover claim is bound to: wildcards and concrete parts as printed.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct LinesCovered {
    /// Wildcard series tokens printed on the cover.
    pub wildcards: Vec<String>,
    /// Concrete parts printed on the cover (minus the document id).
    pub parts: Vec<String>,
    /// Vendor family tokens ("RA4C1 Group" -> `RA4C1`).
    pub group_tokens: Vec<String>,
    /// Family phrases ("STM32H5 series", "RA2E1 Group").
    pub family_phrases: Vec<String>,
    /// Tokens recovered from the filename as last resort.
    pub filename_tokens: Vec<String>,
}

/// Cover-page facts: id, title, lines covered as printed, revision.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Identity {
    /// Document id by vendor house style (`RM0390`, `DS80001`...).
    pub document_id: Option<String>,
    /// Verbatim title line(s) from the cover.
    pub title_verbatim: String,
    /// The PDF's own title metadata.
    pub pdf_title: Option<String>,
    /// Revision as printed.
    pub revision: Option<String>,
    /// What the cover says the document covers.
    pub lines_covered: LinesCovered,
    /// Page receipt.
    pub receipt: Receipt,
}

/// A one-page receipt, optionally with continuation pages.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Receipt {
    /// Primary page.
    pub page: i64,
    /// Continuation pages, when the fact spans several.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pages: Option<Vec<i64>>,
}

fn generic_scope(phrase: &str) -> bool {
    static WORDS: &[&str] = &[
        "bit",
        "mcu",
        "mcus",
        "microcontroller",
        "microcontrollers",
        "group",
        "series",
        "family",
        "families",
        "line",
        "arm",
        "based",
        "advanced",
        "renesas",
        "the",
        "of",
        "and",
        "32",
        "16",
        "8",
        "risc",
        "flash",
        "real",
        "time",
        "dual",
        "core",
        "wireless",
        "soc",
        "socs",
    ];
    let tokens: Vec<String> = phrase
        .to_lowercase()
        .split([' ', '-', '/'])
        .filter(|t| !t.is_empty())
        .map(str::to_string)
        .collect();
    tokens.iter().all(|t| WORDS.contains(&t.as_str()))
        || !tokens.iter().any(|t| t.chars().any(|c| c.is_ascii_digit()))
}

/// Cover-page facts: id, title, lines covered as printed, revision.
///
/// `pdf_title` is the PDF metadata title and `source_stem` the file stem the
/// PDF layer read the document from (the filename is a last-resort source of
/// family tokens).
#[must_use]
pub fn read_identity(
    cover: &str,
    second: &str,
    pdf_title: Option<&str>,
    source_stem: &str,
) -> Identity {
    let mut found_id: Option<String> = None;
    for text in [cover, second] {
        if let Some(m) = doc_id().find(text) {
            found_id = Some(m.as_str().to_string());
            break;
        }
    }
    let mut found_revision = None;
    if let Some(c) = revision()
        .captures(cover)
        .or_else(|| revision().captures(second))
    {
        found_revision = c
            .get(1)
            .or_else(|| c.get(2))
            .map(|g| g.as_str().to_string());
    }
    let lines: Vec<String> = cover
        .split('\n')
        .map(norm)
        .filter(|l| l.chars().count() > 3)
        .collect();
    let title_verbatim: String = lines.iter().take(6).cloned().collect::<Vec<_>>().join(" ")[..]
        .chars()
        .take(300)
        .collect();
    // Lines covered: wildcards and concrete parts printed on the cover.
    let mut covered_tokens = wildcards_from_text(cover);
    let covered_parts: Vec<String> = parts_from_text(cover, None)
        .into_iter()
        .filter(|p| Some(p) != found_id.as_ref() && !doc_id().is_match(p))
        .collect();
    // Microchip-style masks: dsPIC33EPXXX(GP/MC/MU)806/810/814.
    for m in re(r"\b(?:ds)?PIC\d{2}[A-Z]{1,3}X{2,3}(?:\([A-Z/]+\))?[0-9/]+").find_iter(cover) {
        let t = m.as_str().to_string();
        if !covered_tokens.contains(&t) {
            covered_tokens.push(t);
        }
    }
    // Family words like "STM32H5 series", "RA2E1 Group".
    let family_phrases: Vec<String> = {
        let mut out: Vec<String> = Vec::new();
        for m in re(
            r"(?i)\b([A-Z][A-Za-z0-9]{1,12}(?:[ -][A-Za-z0-9]{1,10}){0,2}[ -](?:series|family|families|group|line|MCUs?|microcontrollers))\b",
        )
        .captures_iter(cover)
        {
            let Some(p) = m.get(1).map(|g| g.as_str().to_string()) else {
                continue;
            };
            if !generic_scope(&p) && !out.contains(&p) {
                out.push(p);
            }
        }
        out.sort();
        out.dedup();
        out.into_iter().take(12).collect()
    };
    // "RA4C1 Group", "RL78/G13": the vendor's own family token.
    let mut group_tokens: Vec<String> = Vec::new();
    for m in re(
        r"\b((?:[A-Z]{1,5}\d[A-Z0-9]{1,8})|RL78/[A-Z]\d{1,2}[A-Z]?)\s+(?:Group|Series|Family|Line)\b",
    )
    .captures_iter(cover)
    {
        let Some(token) = m.get(1).map(|g| g.as_str().to_string()) else {
            continue;
        };
        if !group_tokens.contains(&token) && !doc_id().is_match(&token) {
            group_tokens.push(token);
        }
    }
    // Filename as last resort: ra_ra4c1.pdf -> RA4C1.
    let stem = source_stem.replace(['_', '-'], " ");
    let filename_tokens: Vec<String> = re(r"(?i)\b([a-z]{1,5}\d[a-z0-9]{1,8})\b")
        .captures_iter(&stem)
        .filter_map(|c| c.get(1).map(|g| g.as_str().to_uppercase()))
        .filter(|t| !doc_id().is_match(t))
        .take(4)
        .collect();
    Identity {
        document_id: found_id,
        title_verbatim,
        pdf_title: pdf_title.map(str::to_string),
        revision: found_revision,
        lines_covered: LinesCovered {
            wildcards: covered_tokens.into_iter().take(40).collect(),
            parts: covered_parts.into_iter().take(80).collect(),
            group_tokens: group_tokens.into_iter().take(8).collect(),
            family_phrases,
            filename_tokens,
        },
        receipt: Receipt {
            page: 1,
            pages: None,
        },
    }
}

// ---------------------------------------------------------------------------
// Chapters
// ---------------------------------------------------------------------------

/// One chapter-list entry.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ChapterEntry {
    /// 1 = top level, 2 = subchapter.
    pub level: i64,
    /// Title as printed (normalised).
    pub title: String,
    /// 1-based page the chapter starts on.
    pub page: i64,
}

/// Chapter list from bookmarks (or the printed table of contents), normalised
/// to the peripheral vocabulary.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Chapters {
    /// `bookmarks` or `printed_toc`.
    #[serde(default)]
    pub source: String,
    /// How many entries the source carried.
    #[serde(default)]
    pub entries: i64,
    /// Top-level chapters, capped at 120.
    #[serde(default)]
    pub chapters: Vec<ChapterEntry>,
    /// Every entry, for the feature readers (not part of the wire).
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub all_entries: Vec<ChapterEntry>,
    /// Entries grouped by peripheral class, capped at 12 per class.
    #[serde(default)]
    pub peripheral_classes: BTreeMap<String, Vec<ChapterEntry>>,
}

/// Chapter list from bookmarks, falling back to the printed table of
/// contents ("3 CPU .......... 430" lines).
#[must_use]
pub fn read_chapters(
    toc: &[(i64, String, i64)],
    page_texts: &BTreeMap<i64, String>,
    page_count: i64,
) -> Chapters {
    let mut top: Vec<(i64, String, i64)> = toc
        .iter()
        .filter(|(lvl, title, page)| *lvl <= 2 && !title.is_empty() && *page > 0)
        .map(|(lvl, title, page)| (*lvl, norm(title), *page))
        .collect();
    let mut source = "bookmarks";
    if top.len() < 5 {
        source = "printed_toc";
        top.clear();
        for pno in 1..=page_count.min(30) {
            let Some(text) = page_texts.get(&pno) else {
                continue;
            };
            for line in text.split('\n') {
                if let Some(c) =
                    re(r"^\s*(\d{1,2}(?:\.\d{1,2})?)\s+([A-Z][^.]{3,80}?)\s*\.{3,}\s*(\d{1,4})\s*$")
                        .captures(line)
                {
                    let level = if c.get(1).map_or("", |g| g.as_str()).contains('.') {
                        2
                    } else {
                        1
                    };
                    let title = norm(c.get(2).map_or("", |g| g.as_str()));
                    let page = c
                        .get(3)
                        .and_then(|g| g.as_str().parse::<i64>().ok())
                        .unwrap_or(0);
                    top.push((level, title, page));
                }
            }
        }
    }
    let mut classes: BTreeMap<String, Vec<ChapterEntry>> = BTreeMap::new();
    for (level, title, page) in &top {
        if let Some(name) = classify_chapter_title(title) {
            classes.entry(name).or_default().push(ChapterEntry {
                level: *level,
                title: title.clone(),
                page: *page,
            });
        }
    }
    let chapters: Vec<ChapterEntry> = top
        .iter()
        .filter(|(l, _, _)| *l == 1)
        .take(120)
        .map(|(level, title, page)| ChapterEntry {
            level: *level,
            title: title.clone(),
            page: *page,
        })
        .collect();
    let all_entries: Vec<ChapterEntry> = top
        .iter()
        .map(|(level, title, page)| ChapterEntry {
            level: *level,
            title: title.clone(),
            page: *page,
        })
        .collect();
    let peripheral_classes: BTreeMap<String, Vec<ChapterEntry>> = classes
        .into_iter()
        .map(|(k, v)| (k, v.into_iter().take(12).collect()))
        .collect();
    Chapters {
        source: source.to_string(),
        entries: i64::try_from(top.len()).unwrap_or(0),
        chapters,
        all_entries,
        peripheral_classes,
    }
}

// ---------------------------------------------------------------------------
// Peripheral instances
// ---------------------------------------------------------------------------

/// Instance grammar: canonical class -> pattern whose first group is the
/// instance designator. Word-bounded, case-sensitive to avoid prose.
const INSTANCE_GRAMMAR: &[(&str, &str)] = &[
    ("TIM", r"\bTIM(\d{1,2})\b"),
    ("LPTIM", r"\bLPTIM(\d)\b"),
    ("HRTIM", r"\bHRTIM(\d)\b"),
    ("TIMG", r"\bTIMG(\d{1,2})\b"),
    ("TIMA", r"\bTIMA(\d)\b"),
    ("EPWM", r"\bEPWM(\d{1,2})\b"),
    ("ECAP", r"\bECAP(\d{1,2})\b"),
    ("EQEP", r"\bEQEP(\d)\b"),
    ("USART", r"\bUSART(\d)\b"),
    ("UART", r"\bUART(\d)\b"),
    ("LPUART", r"\bLPUART(\d)\b"),
    ("SCI", r"\bSCI([A-D]|\d{1,2})\b"),
    ("RSPI", r"\bRSPI(\d)\b"),
    ("RIIC", r"\bRIIC(\d)\b"),
    ("MTU", r"\bMTU(\d{1,2})\b"),
    ("TPU", r"\bTPU(\d)\b"),
    ("CMT", r"\bCMT(\d)\b"),
    ("TMR", r"\bTMR(\d)\b"),
    ("GPT32", r"\bGPT(?:32|16|32E|32EH)(\d)\b"),
    ("AGT", r"\bAGT(\d)\b"),
    ("S12AD", r"\bS12AD([A-Z]?\d?)\b"),
    ("RSCAN", r"\bRSCAN(\d?)\b"),
    ("DMAC", r"\bDMAC(\d)\b"),
    ("DTC", r"\b(DTC)\b"),
    ("ELC", r"\b(ELC)\b"),
    ("MIBSPI", r"\bMib(?:SPI|SPIP)(\d)\b"),
    ("N2HET", r"\bN2HET(\d)\b"),
    ("RTI", r"\b(RTI)\b"),
    ("GIO", r"\bGIO([A-H])\b"),
    ("MIBADC", r"\bMibADC(\d)\b"),
    ("LIN", r"\bLIN(\d)\b"),
    ("EMAC", r"\b(EMAC)\b"),
    ("SPI", r"\bSPI(\d)\b"),
    ("I2S", r"\bI2S(\d)\b"),
    ("SAI", r"\bSAI(\d)\b"),
    ("I2C", r"\bI2C(\d)\b"),
    ("I3C", r"\bI3C(\d)\b"),
    ("FDCAN", r"\bFDCAN(\d)\b"),
    ("CAN", r"\b(?:bx)?CAN(\d)\b"),
    ("MCAN", r"\bMCAN(\d)\b"),
    ("DCAN", r"\bDCAN(\d)\b"),
    ("ADC", r"\bADC(\d|[A-D])\b"),
    ("DAC", r"\bDAC(\d|[A-D])\b"),
    ("COMP", r"\bCOMP(\d)\b"),
    ("OPAMP", r"\bOPAMP(\d)\b"),
    ("DMA", r"\b(?:GP|B|M)?DMA(\d)\b"),
    ("GPIO", r"\bGPIO([A-Z])\b"),
    (
        "USB",
        r"\b(USB_OTG_FS|USB_OTG_HS|USB_DRD_FS|OTG_FS|OTG_HS|USBFS|USBHS|USB0|USB1|USBOTG|USBFSH|USBHSD|USBHSH)\b",
    ),
    ("ETH", r"\bETH(\d)\b"),
    ("SDMMC", r"\bSDMMC(\d)\b"),
    ("OCTOSPI", r"\b(?:OCTO|X|HSPI|QUAD)SPI(\d)\b"),
    ("FMC", r"\b(FMC|FSMC)\b"),
    ("RTC", r"\b(RTC)\b"),
    ("IWDG", r"\b(IWDG|WWDG|WDT\d?)\b"),
    ("AES", r"\b(AES|SAES|HASH|PKA|RNG|CRYP)\b"),
    ("LTDC", r"\b(LTDC|DSI|DMA2D|GFXMMU|DCMI|PSSI)\b"),
    ("UCPD", r"\bUCPD(\d)\b"),
    ("TSC", r"\b(TSC)\b"),
    ("WDOG", r"\bWDOG(\d)\b"),
    ("PCA", r"\bPCA(\d)\b"),
    ("SMB", r"\bSMB(\d)\b"),
    ("LLWU", r"\b(LLWU)\b"),
    ("DMAMUX", r"\bDMAMUX(\d?)\b"),
    ("EWM", r"\b(EWM)\b"),
    ("CRC", r"\b(CRC)\b"),
    ("LCDK", r"\b(LCDC|eLCDIF|LCDIF|SLCD)\b"),
    ("MIPI", r"\b(MIPI_DSI|MIPI_CSI)\b"),
    ("LPADC", r"\bLPADC(\d)\b"),
];

/// One instance designator the document uses.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Instance {
    /// The designator as this document prints it.
    pub instance: String,
    /// How many pages name it.
    pub pages: i64,
    /// First page naming it.
    pub first_page: i64,
    /// Named on fewer pages than the support floor.
    pub weak: bool,
}

/// Per-class instance report.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct InstanceClass {
    /// Instances meeting the support floor.
    pub count_asserted: i64,
    /// Every instance, strongest first, capped at 64.
    pub instances: Vec<Instance>,
}

fn bare_class(cls: &str) -> bool {
    matches!(
        cls,
        "USB"
            | "FMC"
            | "RTC"
            | "IWDG"
            | "AES"
            | "LTDC"
            | "TSC"
            | "DTC"
            | "ELC"
            | "RTI"
            | "EMAC"
            | "PIT"
            | "SCTIMER"
            | "ENET"
            | "SDHC"
            | "LLWU"
            | "EWM"
            | "CRC"
            | "LCDK"
            | "MIPI"
    )
}

/// Every peripheral instance name the document uses, with page support.
///
/// `weak_pages` is the support floor: an instance named on fewer pages is
/// flagged weak and does not count toward `count_asserted`.
#[must_use]
pub fn read_instances(
    page_texts: &BTreeMap<i64, String>,
    weak_pages: i64,
) -> BTreeMap<String, InstanceClass> {
    let mut support: BTreeMap<String, BTreeMap<String, BTreeSet<i64>>> = BTreeMap::new();
    for (pno, text) in page_texts {
        for (cls, src) in INSTANCE_GRAMMAR {
            for m in re(src).captures_iter(text) {
                let Some(name) = m.get(1).map(|g| g.as_str().to_string()) else {
                    continue;
                };
                support
                    .entry((*cls).to_string())
                    .or_default()
                    .entry(name)
                    .or_default()
                    .insert(*pno);
            }
        }
    }
    let mut out: BTreeMap<String, InstanceClass> = BTreeMap::new();
    for (cls, entries) in support {
        let mut instances: Vec<Instance> = Vec::new();
        for (name, pages) in entries {
            let instance = if bare_class(&cls) || name.starts_with(&cls) {
                name
            } else {
                format!("{cls}{name}")
            };
            instances.push(Instance {
                instance,
                pages: i64::try_from(pages.len()).unwrap_or(0),
                first_page: *pages.iter().min().unwrap_or(&0),
                weak: pages.len() < usize::try_from(weak_pages.max(0)).unwrap_or(0),
            });
        }
        instances.sort_by(|a, b| {
            a.weak
                .cmp(&b.weak)
                .then_with(|| natural(&a.instance).cmp(&natural(&b.instance)))
        });
        let count_asserted =
            i64::try_from(instances.iter().filter(|i| !i.weak).count()).unwrap_or(0);
        out.insert(
            cls,
            InstanceClass {
                count_asserted,
                instances: instances.into_iter().take(64).collect(),
            },
        );
    }
    out
}

// ---------------------------------------------------------------------------
// Bullets
// ---------------------------------------------------------------------------

/// Bullet glyphs vendors use, including private-use-area glyphs Wingdings and
/// Symbol fonts leave in extracted text (Renesas `\u{f0b7}`).
fn glyph_class() -> String {
    r"•·▪◦■●♦◆►▶➢➤✓\u{2022}\u{2023}\u{25aa}\u{25ab}\u{25e6}\u{2043}\u{2219}\u{f000}-\u{f0ff}"
        .to_string()
}

fn glyph_line(extra: &str) -> Regex {
    if extra.is_empty() {
        re(&format!(r"^\s*([{}])\s*(.*)$", glyph_class()))
    } else {
        re(&format!(r"^\s*([{}]|{})\s*(.*)$", glyph_class(), extra))
    }
}

fn dash_line() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"^\s*[–\-]\s*(\S.*)?$"))
}

fn count_suffix() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"(?i)\s*(?:[×x]\s*(\d{1,2})|(\d{1,2})\s*[×x]|\((\d{1,2})\s*(?:channels?|units?|modules?|ch)\))\s*(\(.*\))?\s*$")
    })
}

const COUNT_WORDS: &[(&str, i64)] = &[
    ("one", 1),
    ("two", 2),
    ("three", 3),
    ("four", 4),
    ("five", 5),
    ("six", 6),
    ("seven", 7),
    ("eight", 8),
    ("nine", 9),
    ("ten", 10),
    ("eleven", 11),
    ("twelve", 12),
    ("sixteen", 16),
];

/// Bullets that are a vendor section heading rather than a fact.
fn section_heading() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(concat!(
            r"(?i)^(?:processor|core|cpu|cpu\s+core|memor(?:y|ies)|system|low[- ]power(?:\s+modes?)?|power(?:\s+management)?|peripherals?|i/os?|packages?|",
            r"operating\s+(?:voltage|conditions)|debug(?:\s+mode)?|connectivity|communications?(?:\s+interfaces?)?|analog(?:ue)?(?:\s+peripherals)?|timers?(?:/counters?)?|counters?/timers?(?:\s+and\s+pwm)?|security(?:\s+and\s+\w+)?|safety|",
            r"clocks?(?:,\s*reset\s+and\s+supply\s+management)?|clock\s+management|reset\s+and\s+clock\s+control|dma|human\s+machine\s+interface(?:\s+\(hmi\))?|graphics|",
            r"system\s+and\s+power\s+management|multiple\s+clock\s+sources|general[- ]purpose\s+i/os?|input/output|features|key\s+features|main\s+features|other\s+features|",
            r"advanced\s+analog\s+features|up\s+to\s+\d+\s+(?:fast\s+)?i/o\s+ports|\d+\s+timers|\d+\s+communication\s+interfaces)\s*:?$"
        ))
    })
}

fn numbered_heading() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"^\s*\d{1,2}(?:\.\d{1,2}){1,2}\s+[A-Z][A-Za-z]+(?:[ \-/][A-Za-z()]+){0,7}\s*$")
    })
}

/// A bullet item: the text, the section it sits under, its level, and its
/// parent bullet when it is a dash hanging off a glyph bullet.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BulletItem {
    /// The bullet text (wrapped lines joined).
    pub text: String,
    /// Enclosing section; the wire always carries the key (null when none).
    pub section: Option<String>,
    /// 1 = top bullet, 2 = dash under a bullet.
    pub level: i64,
    /// The bullet this dash hangs off (only when one exists).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parent: Option<String>,
}

struct BulletState {
    items: Vec<BulletItem>,
    section: Option<String>,
    section_from_bullet: bool,
    current: Option<(BulletItem, bool)>, // (item, opened-by-glyph)
    last_bullet: Option<BulletItem>,
    has_dashes: bool,
}

impl BulletState {
    /// The reference's `close()`: finish the open bullet, letting a
    /// heading-like level-1 bullet name the section for what follows.
    fn close(&mut self) {
        let Some((mut c, opened_by_glyph)) = self.current.take() else {
            return;
        };
        if c.text.trim().is_empty() {
            return;
        }
        c.text = norm(&c.text);
        if c.level == 1 && c.parent.is_none() && opened_by_glyph {
            self.last_bullet = Some(c.clone());
            // In a glyph-over-dash layout a heading bullet's scope ends at
            // the next glyph bullet. In an all-glyph layout it persists.
            if self.section_from_bullet && self.has_dashes {
                self.section = None;
                self.section_from_bullet = false;
                c.section = None;
            }
        }
        // A level-1 bullet that is a vendor section heading names the
        // section for what follows; it is kept as a fact only when it
        // carries a number.
        if c.level == 1 && section_heading().is_match(&c.text) {
            self.section = Some(c.text.trim_end_matches(':').to_string());
            self.section_from_bullet = true;
            c.section.clone_from(&self.section);
            if c.text.chars().any(|ch| ch.is_ascii_digit()) {
                self.items.push(c);
            }
        } else {
            self.items.push(c);
        }
    }
}

/// Bullet texts (flat) from a block; kept for callers that only need text.
#[must_use]
pub fn bullets(text: &str) -> Vec<String> {
    bullet_items(text).into_iter().map(|i| i.text).collect()
}

/// Structured bullets from a text block: `{text, section, level}`.
///
/// Marker hierarchy is learned per block: when two distinct glyphs appear,
/// the one seen first is the section header level (Renesas "■ Memory" over
/// "\u{f0b7} 512-KB code flash"); a dash line is a sub-bullet. A glyph alone
/// on its line takes the following line as its text. Parsing stops at the
/// next numbered heading.
#[must_use]
#[allow(clippy::too_many_lines, reason = "faithful port of _bullet_items")]
pub fn bullet_items(text: &str) -> Vec<BulletItem> {
    let lines: Vec<&str> = text.split('\n').map(str::trim_end).collect();
    // A font-substituted bullet renders as one letter alone on its line
    // (Atmel "z" from Wingdings). If a single character stands alone on six
    // or more lines of the block, it is this block's bullet glyph.
    let mut lone: BTreeMap<&str, i64> = BTreeMap::new();
    for l in &lines {
        let t = l.trim();
        if t.chars().count() == 1
            && !t.chars().next().is_some_and(|c| c.is_ascii_digit())
            && !re(r"^[–\-]$").is_match(t)
        {
            *lone.entry(t).or_insert(0) += 1;
        }
    }
    let extra: Vec<String> = lone
        .iter()
        .filter(|(_, &n)| n >= 6)
        .map(|(c, _)| regex::escape(c))
        .collect();
    let gline = glyph_line(&extra.join("|"));

    let mut glyph_order: Vec<String> = Vec::new();
    for l in &lines {
        if let Some(g) = gline.captures(l).and_then(|m| m.get(1)) {
            let g = g.as_str().to_string();
            if !glyph_order.contains(&g) {
                glyph_order.push(g);
            }
        }
    }
    let header_glyph: Option<String> = if glyph_order.len() >= 2 {
        glyph_order.first().cloned()
    } else {
        None
    };
    let has_dashes = lines.iter().filter(|l| dash_line().is_match(l)).count() >= 3;

    let mut state = BulletState {
        items: Vec::new(),
        section: None,
        section_from_bullet: false,
        current: None,
        last_bullet: None,
        has_dashes,
    };
    let mut pending_glyph: Option<&str> = None;

    for line in &lines {
        if line.trim().is_empty() {
            continue;
        }
        // A numbered section heading ends the list; a figure like
        // "1.25 DMIPS/MHz (Dhrystone 2.1)" does not.
        if numbered_heading().is_match(line) && (state.current.is_some() || !state.items.is_empty())
        {
            state.close();
            return state.items;
        }
        if let Some(m) = gline.captures(line) {
            let glyph = m.get(1).map_or("", |g| g.as_str());
            let rest = m.get(2).map_or("", |g| g.as_str()).trim();
            if Some(glyph) == header_glyph.as_deref() {
                state.close();
                state.section = if rest.is_empty() {
                    None
                } else {
                    Some(rest.to_string())
                };
                pending_glyph = if rest.is_empty() {
                    Some("header")
                } else {
                    None
                };
                continue;
            }
            state.close();
            state.current = Some((
                BulletItem {
                    text: rest.to_string(),
                    section: state.section.clone(),
                    level: 1,
                    parent: None,
                },
                true,
            ));
            pending_glyph = if rest.is_empty() {
                Some("bullet")
            } else {
                None
            };
            continue;
        }
        let dash = dash_line().captures(line);
        if let Some(m) = dash {
            if state.current.is_some() || !state.items.is_empty() || pending_glyph == Some("dash") {
                state.close();
                let parent = state.last_bullet.as_ref().map(|b| b.text.clone());
                // Under a section-heading bullet the dashes are the facts
                // themselves: level 1.
                let heading_parent = parent
                    .as_deref()
                    .is_some_and(|p| section_heading().is_match(&norm(p)));
                let level = if heading_parent { 1 } else { 2 };
                let mut item = BulletItem {
                    text: m.get(1).map_or("", |g| g.as_str()).trim().to_string(),
                    section: state.section.clone(),
                    level,
                    parent: None,
                };
                if !heading_parent {
                    if let Some(p) = parent {
                        item.parent = Some(norm(&p));
                    }
                }
                pending_glyph = if m.get(1).map_or("", |g| g.as_str()).is_empty() {
                    Some("dash")
                } else {
                    None
                };
                state.current = Some((item, false));
                continue;
            }
        }
        if pending_glyph == Some("header") {
            state.section = Some(norm(line));
            pending_glyph = None;
            continue;
        }
        if let Some((c, _)) = state.current.as_mut() {
            if matches!(pending_glyph, Some("bullet" | "dash")) && c.text.is_empty() {
                // The glyph stood alone; this line is the bullet's text.
                c.text = line.trim().to_string();
                pending_glyph = None;
                continue;
            }
        }
        if let Some((c, _)) = state.current.as_mut() {
            let trimmed = line.trim();
            if line.chars().count() < 120 && trimmed != trimmed.to_uppercase() {
                c.text = format!("{} {}", c.text, trimmed);
                pending_glyph = None;
            }
        }
    }
    state.close();
    state.items
}

/// `("Serial Communications Interface (SCI) × 4")` -> `("Serial
/// Communications Interface (SCI)", 4)`.
#[allow(
    clippy::indexing_slicing,
    clippy::panic,
    reason = "grid walks index rows/columns as the reference does; guards upstream"
)]
#[must_use]
pub fn split_count(text: &str) -> (String, Option<i64>) {
    if let Some(caps) = count_suffix().captures(text) {
        let m = caps.get(0).map(|g| g.as_str()).unwrap_or_default();
        let start = text.find(m).unwrap_or(0);
        let count = caps
            .get(1)
            .or_else(|| caps.get(2))
            .or_else(|| caps.get(3))
            .and_then(|g| g.as_str().parse::<i64>().ok());
        let mut label = text[..start].trim_end().to_string();
        if let Some(p) = caps.get(4) {
            label = format!("{label} {}", p.as_str());
        }
        return (label, count);
    }
    if let Some((end, count, word)) = count_prefix_match(text) {
        return (
            text[end..].trim().to_string(),
            word.map_or(count, |w| {
                COUNT_WORDS
                    .iter()
                    .find(|(k, _)| *k == w.as_str())
                    .map(|(_, v)| *v)
            }),
        );
    }
    (text.to_string(), None)
}

// ---------------------------------------------------------------------------
// Features
// ---------------------------------------------------------------------------

fn qualifier() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"(?i)\b(up\s+to|maximum|max\.?|minimum|min\.?|typical|typ\.?|at\s+least|as\s+low\s+as|down\s+to)\b")
    })
}

fn num_unit() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(concat!(
            r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)",
            r"(?i)[\s-]*(Mbytes?|Kbytes?|MB|KB|Kbit|Mbit|MHz|kHz|GHz|bits?|bytes?|channels?|V|mA|µA|uA|nA|°C|ms|µs|us|ns|I/Os?|pins?|x|×)?\b"
        ))
    })
}

fn singular_acronyms(text: &str) -> String {
    // "three SPIs, two I2Cs" -> "SPI", "I2C" for classification only.
    re(r"\b([A-Z][A-Z0-9]{1,7})s\b")
        .replace_all(text, "$1")
        .into_owned()
}

/// The classes a label names, in the vocabulary's precedence order.
fn chapter_classes(label: &str) -> Vec<String> {
    let checked = singular_acronyms(label);
    let mut out = Vec::new();
    for (name, src) in CHAPTER_VOCAB {
        if !vocab_match(name, src, &checked) {
            continue;
        }
        if let Some((_, excl)) = CHAPTER_EXCLUDE.iter().find(|(n, _)| n == name) {
            if re(excl).is_match(label) {
                continue;
            }
        }
        out.push((*name).to_string());
        if out.len() == 4 {
            break;
        }
    }
    out
}

/// A typed number as printed: value with its unit, if any.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TypedNumber {
    /// The number, integral when the reference prints one.
    pub value: Value,
    /// The unit as printed.
    pub unit: Option<String>,
}

/// Numbers a label states, typed, unit kept as printed.
fn typed_numbers(text: &str) -> Vec<TypedNumber> {
    let mut numbers = Vec::new();
    for m in num_unit().captures_iter(text) {
        let raw = m.get(1).map_or("0", |g| g.as_str()).replace(',', "");
        let value: f64 = raw.parse().unwrap_or(0.0);
        let unit = m
            .get(2)
            .map(|g| g.as_str().trim().to_string())
            .filter(|u| !u.is_empty());
        let v = if value.fract() == 0.0 {
            #[allow(
                clippy::cast_possible_truncation,
                reason = "the reference emits int(value) for integral floats"
            )]
            let n = value as i64;
            Value::from(n)
        } else {
            Value::from(value)
        };
        numbers.push(TypedNumber { value: v, unit });
        if numbers.len() == 8 {
            break;
        }
    }
    numbers
}

/// A feature as printed: the verbatim line, the fact it states, the numbers
/// and the qualifier, and the peripheral classes it names.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FeatureRow {
    /// The full line, normalised.
    pub verbatim: String,
    /// The fact: the head of the line, after the qualifier cut.
    pub label: String,
    /// How many of the thing, when the line states it.
    pub count: Option<i64>,
    /// Numbers the label states, typed.
    pub numbers: Vec<TypedNumber>,
    /// The qualifier as printed ("Up to", "Maximum").
    pub qualifier_verbatim: Option<String>,
    /// Peripheral classes the label names (max 4).
    pub classes: Vec<String>,
    /// Page receipt.
    pub receipt: Receipt,
    /// The section the caller attributes the row to.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub vendor_section: Option<String>,
    /// Class of `vendor_section`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub section_class: Option<Option<String>>,
    /// Bullet level, when the row came from a bullet list.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub level: Option<i64>,
    /// Parent bullet, when the row hangs off one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parent: Option<String>,
    /// Section heading of the enclosing features list.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub section: Option<Option<String>>,
    /// Enclosing chapter title.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub chapter: Option<Option<String>>,
    /// Class of the enclosing chapter.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub chapter_class: Option<Option<String>>,
    /// Which reader produced the row.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
}

/// One feature line, typed: qualifier verbatim, numbers typed, classes from
/// the chapter vocabulary.
#[allow(
    clippy::indexing_slicing,
    clippy::panic,
    reason = "grid walks index rows/columns as the reference does; guards upstream"
)]
#[must_use]
#[allow(clippy::too_many_lines, reason = "faithful port of _feature")]
pub fn feature(
    text: &str,
    page: i64,
    section: Option<&str>,
    level: Option<i64>,
    parent: Option<&str>,
) -> FeatureRow {
    let text = norm(text);
    let qual = qualifier()
        .captures(&text)
        .and_then(|c| c.get(1))
        .map(|g| g.as_str().to_string());
    // The fact is the head of the line; ", each with up to 4 IC/OC/PWM ..." is
    // its description. The full line stays in verbatim.
    let mut head = text.clone();
    let cut = label_cut_at(&text);
    // "C8051 core with 25 MHz maximum operating frequency": the tail after
    // "with" is a sized fact of its own, so the line stays whole.
    let sized_tail = cut.is_some_and(|start| {
        re(concat!(
            r"(?i)^\s*,?\s*(?:with|supporting|including|featuring|up to|for)\s+",
            r"(?:up\s+to\s+)?\d+(?:\.\d+)?\s*-?\s*(?:MHz|kHz|GHz|KB|MB|Kbytes?|Mbytes?|V)\b"
        ))
        .is_match(text.get(start..).unwrap_or(""))
    });
    if let Some(start) = cut {
        if start >= 8 && re(r"[A-Za-z]").is_match(&text[..start]) && !sized_tail {
            head = text[..start]
                .trim_end_matches([' ', ',', ':', ';'])
                .to_string();
        }
    }
    if head.matches('(').count() > head.matches(')').count() {
        if let Some(idx) = head.rfind('(') {
            // a parenthetical the cut split open
            head = head[..idx]
                .trim_end_matches([' ', ',', ':', ';'])
                .to_string();
        }
    }
    let (label, count) = split_count(&head);
    let mut label = label;
    if label.chars().count() > 200 {
        label = label.chars().take(200).collect();
    }
    let mut verbatim = text;
    if verbatim.chars().count() > 300 {
        verbatim = verbatim.chars().take(300).collect();
    }
    let numbers = typed_numbers(&label);
    let classes = chapter_classes(&label);
    let mut row = FeatureRow {
        verbatim,
        label,
        count,
        numbers,
        qualifier_verbatim: qual,
        classes,
        receipt: Receipt { page, pages: None },
        vendor_section: section.map(str::to_string),
        section_class: None,
        level,
        parent: None,
        section: None,
        chapter: None,
        chapter_class: None,
        source: None,
    };
    if section.is_some() {
        row.section_class = Some(section.and_then(classify_chapter_title));
    }
    if let Some(p) = parent {
        let mut p = p.to_string();
        if p.chars().count() > 200 {
            p = p.chars().take(200).collect();
        }
        row.parent = Some(p);
    }
    row
}

// ---------------------------------------------------------------------------
// Chapter features
// ---------------------------------------------------------------------------

fn features_heading() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(concat!(
            r"(?im)^\s*(?:\d{1,2}(?:\.\d{1,2}){0,2}\s+)?",
            r"(?P<subject>[A-Za-z][A-Za-z0-9/™®+\- ]{0,60}?)\s*",
            r"(?:main\s+|key\s+|general\s+)?features(?:\s+overview)?\s*$"
        ))
    })
}

/// Bullet lists under any "<subject> [main] features" heading anywhere in the
/// document, tagged with the enclosing chapter. This is where a reference
/// manual states what each peripheral does for the whole family.
#[must_use]
#[allow(
    clippy::too_many_lines,
    reason = "faithful port of read_chapter_features"
)]
pub fn read_chapter_features(
    page_texts: &BTreeMap<i64, String>,
    chapters: &Chapters,
    _page_count: i64,
) -> Vec<FeatureRow> {
    let mut entries: Vec<ChapterEntry> = chapters.all_entries.clone();
    entries.sort_by_key(|e| e.page);
    let top_level: Vec<ChapterEntry> = entries.into_iter().filter(|e| e.level == 1).collect();
    let mut out: Vec<FeatureRow> = Vec::new();
    let mut seen_pages: BTreeSet<i64> = BTreeSet::new();
    for pno in page_texts.keys().copied().collect::<Vec<_>>() {
        let Some(text) = page_texts.get(&pno) else {
            continue;
        };
        let Some(heading) = features_heading().captures(text) else {
            continue;
        };
        if seen_pages.contains(&pno) {
            continue;
        }
        let subject = norm(heading.name("subject").map_or("", |g| g.as_str()));
        if subject.chars().count() > 48
            || re(r"(?i)\b(?:refer|see|for|the|this|these|following|with|of)\b").is_match(&subject)
        {
            continue; // a sentence ending in "features", not a heading
        }
        let mut best: Option<ChapterEntry> = None;
        for entry in &top_level {
            if entry.page <= pno {
                best = Some(entry.clone());
            } else {
                break;
            }
        }
        let chapter_class = best.as_ref().and_then(|c| classify_chapter_title(&c.title));
        let after = &text[heading.get(0).map_or(0, |m| m.end())..];
        let nxt = page_texts.get(&(pno + 1)).cloned().unwrap_or_default();
        let nxt_cut: String = nxt.chars().take(4000).collect();
        let mut list = bullet_items(after);
        list.extend(bullet_items(&nxt_cut));
        if list.is_empty() {
            continue;
        }
        seen_pages.insert(pno);
        seen_pages.insert(pno + 1);
        for item in list.iter().take(60) {
            let mut row = feature(
                &item.text,
                pno,
                None,
                Some(item.level),
                item.parent.as_deref(),
            );
            row.section = Some(if subject.is_empty() {
                None
            } else {
                Some(subject.clone())
            });
            row.chapter = Some(best.as_ref().map(|c| c.title.clone()));
            row.chapter_class = Some(chapter_class.clone().or_else(|| {
                if subject.is_empty() {
                    None
                } else {
                    classify_chapter_title(&subject)
                }
            }));
            out.push(row);
        }
        if out.len() >= 3000 {
            break;
        }
    }
    out.into_iter().take(3000).collect()
}

// ---------------------------------------------------------------------------
// Features pages
// ---------------------------------------------------------------------------

fn features_heading_any() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(concat!(
            r"(?im)^\s*(?:\d{1,2}(?:\.\d{1,2}){0,2}\s*\n?\s*)?",
            r"(?:[A-Za-z][A-Za-z0-9/™®+\- ]{0,40}?\s+)?",
            r"(?:key\s+|device\s+|product\s+|microcontroller\s+)?features(?:\s+overview)?\s*$",
            r"|^\s*(?:\d{1,2}(?:\.\d{1,2}){0,2}\s*\n?\s*)?",
            r"[A-Za-z0-9][A-Za-z0-9/+\- ]{0,30}?\s+(?:sub-?family|family|series)\s+introduction\s*$"
        ))
    })
}

fn applications_lead() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"(?im)\b(?:example\s+applications|applications?\s+include(?:\s+the\s+following)?|target\s+applications|typical\s+applications)\s*:?\s*$")
    })
}

fn toc_line() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"^\d{1,4}\s+\d{1,2}(?:\.\d{1,2}){1,3}\s+\S|.*\.\s?\.\s?\.\s?\.|.*\s\.\s*$")
    })
}

fn overview_stop() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"(?m)^\s*1\.\s+Overview\b|^\s*Table\s+1\.\d"))
}

fn glyph_lines(text: &str) -> usize {
    let gl = glyph_line("");
    text.split('\n')
        .filter(|line| gl.is_match(line) || dash_line().is_match(line))
        .count()
}

/// Bullet features on the opening pages, verbatim, numbers typed, qualifier
/// as printed.
#[must_use]
#[allow(clippy::too_many_lines, reason = "faithful port of read_features")]
pub fn read_features(page_texts: &BTreeMap<i64, String>, max_pages: usize) -> Vec<FeatureRow> {
    let mut out: Vec<FeatureRow> = Vec::new();
    if page_texts.is_empty() {
        return out;
    }
    let last = *page_texts.keys().max().unwrap_or(&0);
    // Pages to read: the opening pages, plus the "Features" page wherever the
    // front matter puts it (reference manuals put it after a 40-page TOC),
    // plus the pages that continue its bullet list.
    let mut pages: Vec<i64> = (1..=i64::try_from(max_pages).unwrap_or(0).min(last)).collect();
    let mut heading_page: Option<i64> = None;
    for pno in (i64::try_from(max_pages).unwrap_or(0) + 1)
        ..=i64::try_from(FEATURES_SEARCH_PAGES).unwrap_or(0).min(last)
    {
        let text = page_texts.get(&pno).map_or("", String::as_str);
        if features_heading_any().is_match(text) && glyph_lines(text) >= 8 {
            pages.push(pno);
            heading_page = Some(pno);
            let mut nxt = pno + 1;
            // Continuation pages: still dense with bullets, not a new chapter,
            // and not a features table.
            while nxt <= last {
                let t = page_texts.get(&nxt).map_or("", String::as_str);
                if glyph_lines(t) < 8
                    || overview_stop().is_match(t)
                    || features_table_caption().is_match(t)
                {
                    break;
                }
                pages.push(nxt);
                nxt += 1;
            }
            break;
        }
    }
    for pno in &pages {
        let text = page_texts.get(pno).map_or("", String::as_str);
        if !re(r"(?i)\bfeatures\b").is_match(text)
            && *pno > 2
            && *pno <= i64::try_from(max_pages).unwrap_or(0)
        {
            continue;
        }
        // A "Features" heading on the page starts the list; otherwise the whole page.
        let heading = features_heading_any().find(text);
        let block = match heading {
            Some(m) => &text[m.end()..],
            None => text,
        };
        // A cover that lists applications mixes those bullets into the
        // feature column in text order; an application names a market, not a block.
        let applications_page = applications_lead().is_match(text);
        for item in bullet_items(block) {
            if toc_line().is_match(&item.text) {
                continue;
            }
            if applications_page && is_application(&item.text) {
                continue;
            }
            let mut row = feature(
                &item.text,
                *pno,
                item.section.as_deref(),
                Some(item.level),
                item.parent.as_deref(),
            );
            row.source = Some(
                if Some(*pno) == heading_page || *pno <= i64::try_from(max_pages).unwrap_or(0) {
                    "features_heading_page"
                } else {
                    "features_continuation"
                }
                .to_string(),
            );
            out.push(row);
            if out.len() >= 400 {
                return out;
            }
        }
    }
    out
}

fn features_table_caption() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"(?im)^\s*Table\s+\d{1,2}[-.]\d{1,2}\.?\s+.{0,60}\b(?:Features|Module functional categories|functional categories)\s*$")
    })
}

fn features_table_header() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"(?i)^(?:feature|module\s+category|category|module|function|block)s?$"))
}

fn application_words() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(concat!(
            r"(?i)\b(?:automation|consumer|medical|lighting|health|fitness|accessor(?:y|ies)|iot|smart\s+\w+|",
            r"metering|meters?|appliances?|automotive|wearables?|e-?bikes?|drones?|toys|gaming|building|",
            r"industrial|white\s+goods|hvac|point\s+of\s+sale|expander|motor\s+control|sensor\s+controllers?|",
            r"sensors|equipment|devices|systems|electronics|home|security|controls?|monitoring|thermostats?|",
            r"locks?|remotes?|hubs?|gateways?|trackers?|infrastructure|energy\s+harvesting|inverters?|",
            r"chargers?|robotics?|telecom|networking|communications?\s+equipment)\b"
        ))
    })
}

/// An application bullet names a market and carries no number, no unit and
/// no peripheral: "Motor control", "Home automation and security".
fn is_application(text: &str) -> bool {
    if re(r"\d").is_match(text) || text.chars().count() > 48 {
        return false;
    }
    if !application_words().is_match(text) {
        return false;
    }
    let remainder = application_words().replace_all(text, " ").into_owned();
    let checked = singular_acronyms(&remainder);
    let hit = CHAPTER_VOCAB
        .iter()
        .filter(|(name, _)| !matches!(*name, "power" | "gpio" | "cpu_core"))
        .any(|(name, src)| vocab_match(name, src, &checked));
    !hit
}

// ---------------------------------------------------------------------------
// Features tables (TI Tiva/Hercules shape)
// ---------------------------------------------------------------------------

/// The "Table 1-1. <Part> Microcontroller Features" grids: the description
/// cell is the fact as printed; the feature cell names the peripheral.
#[allow(
    clippy::indexing_slicing,
    clippy::panic,
    reason = "grid walks index rows/columns as the reference does; guards upstream"
)]
#[must_use]
#[allow(
    clippy::too_many_lines,
    reason = "faithful port of read_features_table"
)]
pub fn read_features_table(
    page_texts: &BTreeMap<i64, String>,
    tables: &[PageTables],
    max_pages: usize,
) -> Vec<FeatureRow> {
    let mut out: Vec<FeatureRow> = Vec::new();
    for pno in 1..=i64::try_from(max_pages).unwrap_or(0) {
        let text = page_texts.get(&pno).map_or("", String::as_str);
        if !features_table_caption().is_match(text) {
            continue;
        }
        let Some(page_tables) = tables.iter().find(|t| t.page == pno) else {
            continue;
        };
        for grid in &page_tables.tables {
            let raw_rows: Vec<Vec<String>> = grid
                .iter()
                .map(|row| row.iter().map(|c| c.clone().unwrap_or_default()).collect())
                .collect();
            let rows: Vec<Vec<String>> = raw_rows
                .iter()
                .map(|row| row.iter().map(|c| norm(c)).collect())
                .collect();
            if rows.is_empty() || rows[0].len() != 2 {
                continue;
            }
            // The caption may be extracted as a first spanning row.
            let start = usize::from(
                features_table_caption().is_match(&rows[0][0]) && rows[0][1].is_empty(),
            );
            if start >= rows.len() {
                continue;
            }
            let header: Vec<String> = rows[start].iter().map(|c| c.to_lowercase()).collect();
            if !(features_table_header().is_match(&header[0]) && header[1].contains("description"))
            {
                continue;
            }
            let mut section: Option<String> = None;
            for (i, row) in rows.iter().enumerate().skip(start + 1) {
                let feature_name = &row[0];
                let description = &row[1];
                let raw_description = raw_rows[i][1].clone();
                if !feature_name.is_empty() && description.is_empty() {
                    section = Some(feature_name.clone());
                    continue;
                }
                if description.is_empty() || description.chars().count() < 3 {
                    continue;
                }
                // A category row whose description is a bullet list: each
                // bullet is a fact, the category is its section.
                let first_line = raw_description.trim().lines().next().unwrap_or_default();
                if glyph_line("").is_match(first_line) {
                    for item in bullet_items(&raw_description) {
                        if item.level != 1 || item.text.trim_end().ends_with(':') {
                            continue;
                        }
                        let mut row = feature(&item.text, pno, Some(feature_name), Some(1), None);
                        row.source = Some("features_table".into());
                        out.push(row);
                    }
                    continue;
                }
                // Parenthetical ranges are the fact; the frame is the section.
                for part in split_table_desc(description) {
                    let part = part.trim().trim_matches(['.', ' ']);
                    if part.chars().count() < 3
                        || re(r"(?i)^(?:each|for a total|with)\b").is_match(part)
                    {
                        continue;
                    }
                    let parent = if feature_name.is_empty() {
                        None
                    } else {
                        Some(feature_name.as_str())
                    };
                    let mut row = feature(part, pno, section.as_deref(), Some(1), parent);
                    row.source = Some("features_table".into());
                    out.push(row);
                }
            }
        }
        if !out.is_empty() {
            break;
        }
    }
    out.into_iter().take(120).collect()
}

// ---------------------------------------------------------------------------
// Description paragraph
// ---------------------------------------------------------------------------

fn desc_heading() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"(?im)^\s*(?:\d{1,2}\.?\s+)?(?:general\s+description|description|introduction|overview|product\s+overview|device\s+overview)\s*$")
    })
}

fn desc_lead() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(concat!(
            r"(?i)^(?:(?:it|they|which|the\s+(?:devices?|series|family|mcus?|products?)|these\s+devices|",
            r"this\s+(?:device|series|family)|all\s+devices|[A-Z][A-Za-z0-9/\-]+(?:\s+\S+){0,6}?\s+",
            r"(?:devices?|series|family|mcus?|microcontrollers?))\s+)?",
            r"(?:also\s+|further\s+)?(?:provides?|incorporates?|offers?|features?|includes?|integrates?|has|have|contains?|supports?|",
            r"comes?\s+with|is\s+equipped\s+with|embeds?|operates?\s+from|is\s+available\s+in|are\s+available\s+in|available\s+in|",
            r"delivers?|combines?|operating\s+at)\s+(?:a\s+|an\s+|the\s+)?"
        ))
    })
}

fn desc_strip() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(concat!(
            r"(?i)^(?:as\s+well\s+as|and|also|with|plus|along\s+with|together\s+with|",
            r"standard\s+and\s+advanced\s+communication\s+interfaces:|communication\s+interfaces:|peripherals:|including|namely|",
            r"in\s+addition\s+to|operates?\s+from|from)\s+|^(?:a|an|the)\s+"
        ))
    })
}

fn desc_fact() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        let counts: Vec<&str> = COUNT_WORDS.iter().map(|(w, _)| *w).collect();
        re(&format!(
            concat!(
                r"(?i)\b(?:\d+(?:\.\d+)?|{})\b.*(?:\b(?:KB|MB|Kbytes?|Mbytes?|Kbit|Mbit|MHz|kHz|V|bit|channels?|pins?|I/Os?|timers?|ADCs?|DACs?|SPIs?|I2Cs?|I2Ss?|USARTs?|UARTs?|CANs?|USB\w*|SDIO|comparators?|op-?amps?|DMA|GPIOs?|packages?|leads?|cores?)\b|°C)",
                r"|\b(?:SDIO|USB\w*|CAN\w*|Ethernet|RTC|CRC|LCD|TSC|RNG|AES|SHA|TrustZone|FPU|MPU|DSP|DMA|QSPI|OSPI|FSMC|EXMC|SDMMC|I3C|LIN)\b"
            ),
            counts.join("|")
        ))
    })
}

/// Clauses of the description paragraph that state a counted, sized or named
/// family fact, verbatim; leading verb phrases dropped so the clause reads
/// as a feature line ("up to three 12-bit 2.6M MSPS ADCs").
#[must_use]
#[allow(
    clippy::too_many_lines,
    reason = "faithful port of read_description_facts"
)]
pub fn read_description_facts(
    page_texts: &BTreeMap<i64, String>,
    max_pages: usize,
) -> Vec<FeatureRow> {
    let mut out: Vec<FeatureRow> = Vec::new();
    let last = *page_texts.keys().max().unwrap_or(&0);
    for pno in 1..=i64::try_from(max_pages).unwrap_or(0).min(last) {
        let Some(text) = page_texts.get(&pno) else {
            continue;
        };
        let Some(heading) = desc_heading().find(text) else {
            continue;
        };
        let mut block = &text[heading.end()..];
        if let Some(nxt) =
            re(r"(?m)^\s*\d{1,2}(?:\.\d{1,2})?\.?\s+[A-Z][a-z]+(?:\s+\w+){0,5}\s*$").find(block)
        {
            if nxt.start() > 200 {
                block = &block[..nxt.start()];
            }
        }
        let paragraph = norm(block);
        if paragraph.chars().count() < 200 {
            continue;
        }
        for sentence in split_sentences(&paragraph) {
            if !re(r"(?i)\b(?:up\s+to|provides?|incorporates?|offers?|features?|includes?|integrates?|operates?|available|embeds?|contains?|supports?)\b")
                .is_match(sentence)
            {
                continue;
            }
            // A colon introduces a list; the part before it is framing.
            let sentence = re(r"^[^:]{0,120}?\binterfaces?:\s*")
                .replace(sentence, "")
                .into_owned();
            for clause in split_clauses(&sentence) {
                let mut clause = clause.trim_matches([' ', '.']).to_string();
                clause = desc_lead().replace(&clause, "").into_owned();
                for _ in 0..2 {
                    clause = desc_strip().replace(&clause, "").into_owned();
                }
                clause = re(r"(?i)\s+(?:to\s+obtain|which|that|in\s+terms\s+of|for\s+enhanced|providing|allowing|enabling|with\s+flash\s+access|with\s+zero)\b.*$")
                    .replace(&clause, "")
                    .into_owned();
                clause = re(r"(?i)^(?:operating|running)\s+at\s+")
                    .replace(&clause, "")
                    .into_owned();
                // A parenthetical list split by the comma rule leaves halves.
                if clause.contains('(') && !clause.contains(')') {
                    if let Some(idx) = clause.find('(') {
                        clause = clause[idx + 1..].to_string();
                    }
                } else if clause.contains(')') && !clause.contains('(') {
                    if let Some(idx) = clause.find(')') {
                        clause = clause[..idx].to_string();
                    }
                }
                // A verb in the middle starts a new statement; keep the head.
                clause = re(r"(?i)\s+(?:features?|provides?|includes?|incorporates?|offers?|supports?|embeds?|integrates?|has|have)\s+(?:a|an|the|up\s+to)\b.*$")
                    .replace(&clause, "")
                    .into_owned();
                // "... and Reset pin enabled": an "and" tail that states no fact.
                if let Some(idx) = clause.find(" and ") {
                    let tail = &clause[idx + 5..];
                    if !desc_fact().is_match(tail) {
                        clause = clause[..idx].to_string();
                    }
                }
                clause = clause.trim().trim_matches('.').to_string();
                if clause.contains('•')
                    || clause.contains('\u{f0b7}')
                    || re(r"\s(?:is|are|was|were|belongs?)\s").is_match(&clause)
                {
                    continue;
                }
                if re(r"(?i)\b(?:most\s+powerful|needed\s+for|delivering|enabled)\b|@")
                    .is_match(&clause)
                    || !desc_noun_hit(&clause)
                {
                    continue;
                }
                if clause.chars().count() < 4 || clause.chars().count() > 90 {
                    continue;
                }
                if !desc_fact().is_match(&clause) {
                    continue;
                }
                if re(r"(?i)\b(?:suitable|applications?|such\s+as|areas?|markets?|ratio|efficien|ideal|designed|targets?|where|each|selected|configured|optimized|ranging)\b")
                    .is_match(&clause)
                {
                    continue;
                }
                if re(r"(?i)\bcan\s+(?:be|also|operate|run|support|perform|act|handle|reach|wake|use|form)\b")
                    .is_match(&clause)
                {
                    continue; // the verb, not the bus
                }
                let mut row = feature(&clause, pno, Some("Description"), Some(1), None);
                row.source = Some("description".into());
                out.push(row);
            }
        }
        if !out.is_empty() {
            break;
        }
    }
    out.into_iter().take(60).collect()
}

// ---------------------------------------------------------------------------
// Includes list / Item-Description spec list
// ---------------------------------------------------------------------------

fn includes_lead() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"(?im)\bincludes\s*:\s*$"))
}

fn toc_leaders() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"\.{4,}"))
}

fn spec_list_open() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"(?i)\bList of Specifications\b"))
}

fn spec_list_stop() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"(?im)^\s*\d{1,2}\.\d{1,2}\s+Power Supply\b"))
}

fn spec_section() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"^\s*\d{1,2}(?:\.\d{1,2}){1,2}\s+([A-Z].+)$"))
}

fn spec_skip() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(concat!(
            r"(?i)^(?:RZ/G\w*|R01UH\w+|Rev\.?\s*\d|Item|Description|Others|",
            r"List of Specifications|[A-Z][a-z]{2}\s+\d{1,2},\s*\d{4}|",
            r"\d{1,2}-\d{1,3}|Notes?:.*|Figure\s+\d|1\.\s*Overview)\s*$"
        ))
    })
}

fn spec_desc_cont() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(concat!(
            r"(?i)^(?:the|a|an|when|this|there|it|they|supports?|wait|connectable|period|",
            r"number|target modules|function of|enable and|on/off|note:?|ready|",
            r"allocation|area|space|i/f|byte-control|also|as well|selected|",
            r"includes?|transfer|address|maximum number|minimum number|data packing|",
            r"memory write|peripheral \(|auto-|deep-|ddr |jedec)\b"
        ))
    })
}

fn short_spec_items() -> &'static [&'static str] {
    &["package", "process", "jtag", "qspi"]
}

fn looks_like_spec_item(line: &str) -> bool {
    // A left-column Item name, not a wrapped description sentence.
    if line.chars().count() > 70 || spec_skip().is_match(line) || spec_desc_cont().is_match(line) {
        return false;
    }
    if line.ends_with(':') && !line.contains('(') {
        return false;
    }
    if re(r"\.\s").is_match(line) {
        return false;
    }
    if re(r"\([A-Z][A-Z0-9/]{1,16}\)\s*$").is_match(line) {
        return true;
    }
    if re(concat!(
        r"(?i)^(?:System CPU|ARM debugger|Package|Process|JTAG|QSPI|Memory connections|",
        r"Operating clock|Local bus|External bus|General-purpose I/O|Thermal sensor|",
        r"Pin function|Serial communication|Clock-synchronized|High-speed serial|",
        r"PWM timer|Boot Function|Reset)\b"
    ))
    .is_match(line)
    {
        return true;
    }
    line.chars().count() <= 36
        && line.chars().next().is_some_and(char::is_uppercase)
        && line.contains(' ')
}

fn looks_like_item_wrap(line: &str) -> bool {
    if re(r"\([A-Z][A-Z0-9/]{1,16}\)\s*$").is_match(line) && !re(r"\d{2,}").is_match(line) {
        return true;
    }
    !line.is_empty() && line.chars().next().is_some_and(char::is_lowercase)
}

fn spec_item_complete(parts: &[String]) -> bool {
    let name = norm(&parts.join(" "));
    if short_spec_items().contains(&name.to_lowercase().as_str()) {
        return true;
    }
    re(r"\([A-Z][A-Z0-9/]{1,16}\)\s*$").is_match(&name)
}

/// Cover introduction "The <part> includes:" bullets (RZ/G product card).
#[must_use]
pub fn read_includes_list(page_texts: &BTreeMap<i64, String>, max_pages: usize) -> Vec<FeatureRow> {
    let mut out: Vec<FeatureRow> = Vec::new();
    for (pno, text) in page_texts {
        if *pno > i64::try_from(max_pages).unwrap_or(0) {
            break;
        }
        let Some(lead) = includes_lead().find(text) else {
            continue;
        };
        for item in bullet_items(&text[lead.end()..]) {
            let body = item.text.trim_end_matches([' ', ',', ';']).to_string();
            if body.chars().count() < 4 {
                continue;
            }
            let mut row = feature(
                &body,
                *pno,
                Some("Introduction"),
                Some(item.level),
                item.parent.as_deref(),
            );
            row.source = Some("includes_list".into());
            out.push(row);
        }
        break;
    }
    out.into_iter().take(40).collect()
}

/// Item / Description specification list as printed, via text (no tables).
#[allow(
    clippy::indexing_slicing,
    clippy::panic,
    reason = "grid walks index rows/columns as the reference does; guards upstream"
)]
#[must_use]
#[allow(
    clippy::too_many_lines,
    reason = "faithful port of read_item_description_specs"
)]
pub fn read_item_description_specs(
    page_texts: &BTreeMap<i64, String>,
    max_pages: usize,
) -> Vec<FeatureRow> {
    let mut out: Vec<FeatureRow> = Vec::new();
    let mut in_list = false;
    let mut section: Option<String> = None;
    let mut item_parts: Vec<String> = Vec::new();
    let mut had_bullets = false;
    let mut prose_parts: Vec<String> = Vec::new();

    let emit_text = |out: &mut Vec<FeatureRow>,
                     text: &str,
                     page: i64,
                     level: i64,
                     parent: Option<&str>,
                     item_parts: &[String],
                     section: &Option<String>| {
        let body = text.trim_matches([' ', ',', ';']);
        if body.chars().count() < 3 || spec_skip().is_match(body) {
            return;
        }
        let name = norm(&item_parts.join(" "));
        let own = if name.is_empty() { None } else { Some(name) };
        let mut row = feature(
            body,
            page,
            own.as_deref().or(section.as_deref()),
            Some(level),
            parent,
        );
        row.source = Some("item_description".into());
        out.push(row);
    };

    for (pno, text) in page_texts {
        if *pno > i64::try_from(max_pages).unwrap_or(0) {
            break;
        }
        if toc_leaders().is_match(text) && re(r"(?im)^\s*Contents\b").is_match(text) {
            continue; // TOC cites the spec list; it is not the list
        }
        if spec_list_open().is_match(text) {
            in_list = true;
        }
        if !in_list {
            continue;
        }
        if spec_list_stop().is_match(text) && (!item_parts.is_empty() || !out.is_empty()) {
            if !item_parts.is_empty() && !prose_parts.is_empty() {
                let joined = norm(&prose_parts.join(" "));
                emit_text(&mut out, &joined, *pno, 1, None, &item_parts, &section);
                prose_parts.clear();
            }
            break; // the list ends the document's spec section
        }
        let lines: Vec<&str> = text.split('\n').map(str::trim_end).collect();
        let mut i = 0;
        while i < lines.len() {
            let raw = lines[i];
            let line = raw.trim();
            if line.is_empty() {
                i += 1;
                continue;
            }
            if spec_skip().is_match(line) {
                i += 1;
                continue;
            }
            if spec_list_stop().is_match(line) {
                if !item_parts.is_empty() && !prose_parts.is_empty() {
                    let joined = norm(&prose_parts.join(" "));
                    emit_text(&mut out, &joined, *pno, 1, None, &item_parts, &section);
                    prose_parts.clear();
                }
                in_list = false;
                break;
            }
            let numbered = spec_section().captures(line);
            if numbered.is_some() && !glyph_line("").is_match(raw) && !dash_line().is_match(raw) {
                section = numbered.and_then(|c| c.get(1)).map(|g| norm(g.as_str()));
                i += 1;
                continue;
            }
            let glyph = glyph_line("").captures(raw);
            let dash = dash_line().captures(raw);
            if glyph.is_some() || dash.is_some() {
                let mut body = glyph
                    .as_ref()
                    .and_then(|c| c.get(2))
                    .or_else(|| dash.as_ref().and_then(|c| c.get(1)))
                    .map(|g| g.as_str().trim().to_string())
                    .unwrap_or_default();
                if body.is_empty() {
                    let mut nxt = i + 1;
                    while nxt < lines.len() && lines[nxt].trim().is_empty() {
                        nxt += 1;
                    }
                    if nxt < lines.len() {
                        body = lines[nxt].trim().to_string();
                        i = nxt;
                    }
                }
                if !body.is_empty() {
                    let mut parent: Option<String> = None;
                    let mut level = 1;
                    if dash.is_some()
                        && had_bullets
                        && out.last().and_then(|r| r.source.as_deref()) == Some("item_description")
                    {
                        parent = out.last().map(|r| r.label.clone());
                        level = 2;
                    }
                    emit_text(
                        &mut out,
                        &body,
                        *pno,
                        level,
                        parent.as_deref(),
                        &item_parts,
                        &section,
                    );
                    had_bullets = true;
                    prose_parts.clear();
                }
                i += 1;
                continue;
            }
            if had_bullets {
                if looks_like_spec_item(line) {
                    item_parts = vec![line.to_string()];
                    had_bullets = false;
                    prose_parts.clear();
                }
            } else if !item_parts.is_empty() && looks_like_item_wrap(line) {
                item_parts.push(line.to_string());
            } else if !item_parts.is_empty()
                && looks_like_spec_item(line)
                && (!prose_parts.is_empty() || spec_item_complete(&item_parts))
            {
                if !item_parts.is_empty() && !prose_parts.is_empty() {
                    let joined = norm(&prose_parts.join(" "));
                    emit_text(&mut out, &joined, *pno, 1, None, &item_parts, &section);
                    prose_parts.clear();
                }
                item_parts = vec![line.to_string()];
            } else if !item_parts.is_empty() {
                prose_parts.push(line.to_string());
            } else if looks_like_spec_item(line) {
                item_parts = vec![line.to_string()];
            }
            i += 1;
        }
        if out.len() >= 200 {
            break;
        }
    }
    if !item_parts.is_empty() && !prose_parts.is_empty() {
        let last = *page_texts.keys().max().unwrap_or(&1);
        let joined = norm(&prose_parts.join(" "));
        emit_text(&mut out, &joined, last, 1, None, &item_parts, &section);
    }
    out.into_iter().take(200).collect()
}

// ---------------------------------------------------------------------------
// Prose facts
// ---------------------------------------------------------------------------

fn core_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(concat!(
            r"\b(?:Arm|ARM)\W{0,3}\s*(Cortex\W{0,3}[- ]?[MRA]\d{1,2}\+?(?:F|D)?(?:\s+(?:with|and)\s+(?:FPU|DSP|MPU)(?:\s+and\s+(?:FPU|DSP|MPU))?)?)",
            r"|\b(Cortex\W{0,3}[- ]?[MRA]\d{1,2}\+?(?:F|D)?)",
            r"|\b(RISC-V(?:\s+[A-Z0-9]{2,8})?|C28x|TMS320C28x|RXv[1-3] core|RL78 CPU core|CIP-51|Xtensa(?:\s+LX\d)?|AVR®?(?:\s+CPU)?|dsPIC33E|PIC24E|MIPS32®? [A-Za-z0-9]+|e200z\d|8051|Tricore™?|PowerPC)\b"
        ))
    })
}

fn max_freq() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"(?i)\b(?:(up\s+to|max(?:imum)?(?:\s+of)?|maximum\s+frequency\s+of)\s*(\d{1,3}(?:\.\d)?)\s*MHz|(\d{1,3}(?:\.\d)?)\s*MHz\s+(max(?:imum)?(?:\s+(?:operating\s+)?frequency)?))")
    })
}

fn supply_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"\b(\d\.\d{1,2})\s*V?\s*(?:to|–|-|~)\s*(\d\.\d{1,2})\s*V\b"))
}

fn temp_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"[-–−]\s*40\s*°?\s*C?\s*(?:to|–|-|~|\.\.)\s*\+?\s*(85|105|125|150)\s*°\s*C")
    })
}

fn package_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"\b((?:LFQFP|TFLGA|PLQP|PTLG|PWQN|PLBG|LQFP|UFQFPN|UFBGA|TFBGA|WLCSP|LFBGA|QFN|VQFN|HWQFN|TQFP|VFQFPN|EWLCSP|LGA|TSSOP|SOIC|SSOP|SO|DIP|PDIP|SOT|VSSOP|HVQFN|HTQFP|nFBGA|PLCC|CSP|HLQFP|WQFN|DFN|UDFN|uDFN|LQFN|QFP|BGA)\s?-?\d{1,3}[A-Z]?)\b")
    })
}

/// A family fact the document states in prose.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ProseFact {
    /// `core | max_frequency | supply_range | temperature_range | packages`.
    pub kind: String,
    /// The statement, verbatim (normalised).
    pub verbatim: String,
    /// The value as printed: a number, a range, or a list.
    pub value: Value,
    /// The unit as printed.
    pub unit: Option<String>,
    /// The qualifier as printed.
    pub qualifier_verbatim: Option<String>,
    /// Page receipt.
    pub receipt: Receipt,
    /// The line the statement sits in.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub context: Option<String>,
}

/// Family facts a document states in prose on its opening pages: core,
/// maximum frequency, supply range, temperature range, packages. Verbatim
/// match kept; nothing inferred.
#[must_use]
#[allow(clippy::too_many_lines, reason = "faithful port of read_prose_facts")]
pub fn read_prose_facts(page_texts: &BTreeMap<i64, String>, max_pages: usize) -> Vec<ProseFact> {
    let mut out: Vec<ProseFact> = Vec::new();
    let mut seen: BTreeSet<(String, String)> = BTreeSet::new();
    let last = *page_texts.keys().max().unwrap_or(&0);
    let scan_to = i64::try_from(max_pages).unwrap_or(0).min(last);

    let mut supply_candidates: Vec<(f64, f64, String, i64, String)> = Vec::new();
    // Cores: one row per designator, spelling variants collapsed. A core the
    // document names once in a comparison table is not this family's core:
    // a row needs the opening pages or repeated mention (page support >= 3,
    // or >= 10% of the core mentions).
    let mut core_pages: BTreeMap<String, BTreeSet<i64>> = BTreeMap::new();
    let mut core_first: BTreeMap<String, (i64, String)> = BTreeMap::new();
    for pno in 1..=scan_to {
        let Some(text) = page_texts.get(&pno) else {
            continue;
        };
        for m in core_re().captures_iter(text) {
            let Some(core) = (1..=3).find_map(|i| m.get(i).map(|g| g.as_str().to_string())) else {
                continue;
            };
            let lower = re(r"\b(?:arm|with|fpu|dsp|mpu|and|core)\b")
                .replace_all(&core.to_lowercase(), "")
                .into_owned();
            let stem: String = re(r"[^a-z0-9]+").replace_all(&lower, "").into_owned();
            core_pages.entry(stem.clone()).or_default().insert(pno);
            core_first
                .entry(stem)
                .or_insert_with(|| (pno, m.get(0).map_or("", |g| g.as_str()).to_string()));
        }
    }
    let total_core_pages: i64 = core_pages
        .values()
        .map(|s| i64::try_from(s.len()).unwrap_or(0))
        .sum();
    let total_core_pages = total_core_pages.max(1);
    for (stem, pages) in &core_pages {
        let Some((pno, verbatim)) = core_first.get(stem) else {
            continue;
        };
        let n = i64::try_from(pages.len()).unwrap_or(0);
        if *pages.iter().min().unwrap_or(&99) <= 3 || (n >= 3 && n * 10 >= total_core_pages) {
            let mut key = ("core", stem.clone());
            let k2 = re(r"[^a-z0-9.]+")
                .replace_all(&key.1.to_lowercase(), "")
                .into_owned();
            key.1 = k2;
            if seen.insert((key.0.to_string(), key.1.clone())) {
                out.push(ProseFact {
                    kind: key.0.to_string(),
                    verbatim: norm(verbatim),
                    value: Value::Null,
                    unit: None,
                    qualifier_verbatim: None,
                    receipt: Receipt {
                        page: *pno,
                        pages: None,
                    },
                    context: Some(norm(&format!("pages={n}"))),
                });
            }
        }
    }

    for pno in 1..=scan_to {
        let Some(text) = page_texts.get(&pno) else {
            continue;
        };
        for line in text.split('\n') {
            // A CPU/system clock statement, not a peripheral's bit rate.
            if !re(r"(?i)frequency|\bcpu\b|\bcore\b|\bsystem clock|\bsysclk|\bhclk|running|operat|performance|\bmips\b|\bdmips\b").is_match(line) {
                continue;
            }
            if re(r"(?i)\bi2c|\bspi\b|\busb|\badc|\bdac|\buart|\busart|\bsdio|\bsdmmc|\bdma\b|\btimer|\bcan\b|\bethernet|\bbus\b|\bpll\b|\bhse\b|\bhsi\b|\blse\b|\blsi\b|oscillator|\bxspi|\bqspi|\bfmc\b|\bsampling").is_match(line) {
                continue;
            }
            for m in max_freq().captures_iter(line) {
                let value = m
                    .get(2)
                    .or_else(|| m.get(3))
                    .map(|g| g.as_str().to_string())
                    .unwrap_or_default();
                let qualifier = m
                    .get(1)
                    .or_else(|| m.get(4))
                    .map(|g| g.as_str().to_string());
                let key_text = re(r"[^a-z0-9.]+")
                    .replace_all(&value.to_lowercase(), "")
                    .into_owned();
                if seen.insert(("max_frequency".into(), key_text)) {
                    let v = if value.contains('.') {
                        Value::from(value.parse::<f64>().unwrap_or(0.0))
                    } else {
                        Value::from(value.parse::<i64>().unwrap_or(0))
                    };
                    out.push(ProseFact {
                        kind: "max_frequency".into(),
                        verbatim: norm(m.get(0).map_or("", |g| g.as_str())),
                        value: v,
                        unit: Some("MHz".into()),
                        qualifier_verbatim: qualifier,
                        receipt: Receipt {
                            page: pno,
                            pages: None,
                        },
                        context: None,
                    });
                }
            }
        }
        for line in text.split('\n') {
            if !re(r"(?i)operating voltage|supply voltage|power supply|\bV(?:DD|CC)\b|operating range|voltage range").is_match(line) {
                continue;
            }
            for m in supply_re().captures_iter(line) {
                let lo: f64 = m
                    .get(1)
                    .and_then(|g| g.as_str().parse().ok())
                    .unwrap_or(0.0);
                let hi: f64 = m
                    .get(2)
                    .and_then(|g| g.as_str().parse().ok())
                    .unwrap_or(0.0);
                if (0.9..=6.0).contains(&lo) && lo < hi && hi <= 6.0 {
                    supply_candidates.push((
                        lo,
                        hi,
                        m.get(0).map_or("", |g| g.as_str()).to_string(),
                        pno,
                        line.to_string(),
                    ));
                }
            }
        }
        for m in temp_re().captures_iter(text) {
            let pos = m.get(0).map_or(0, |g| g.start());
            let start = text[..pos].rfind('\n').map_or(0, |i| i + 1);
            let end = text[pos..].find('\n').map_or(text.len(), |i| pos + i);
            let context = &text[start..end];
            // Ta / Tj distinguish two attributes; keep them as separate rows.
            let axis = if re(r"(?i)\bTj\b|junction").is_match(context) {
                "j"
            } else if re(r"(?i)\bTa\b|ambient").is_match(context) {
                "a"
            } else {
                "-"
            };
            let bound = m
                .get(1)
                .and_then(|g| g.as_str().parse::<i64>().ok())
                .unwrap_or(0);
            let key_text = format!("{bound}{axis}");
            if seen.insert((
                "temperature_range".into(),
                re(r"[^a-z0-9.]+")
                    .replace_all(&key_text.to_lowercase(), "")
                    .into_owned(),
            )) {
                out.push(ProseFact {
                    kind: "temperature_range".into(),
                    verbatim: norm(m.get(0).map_or("", |g| g.as_str())),
                    value: Value::Array(vec![Value::from(-40), Value::from(bound)]),
                    unit: Some("°C".into()),
                    qualifier_verbatim: None,
                    receipt: Receipt {
                        page: pno,
                        pages: None,
                    },
                    context: Some(norm(context)),
                });
            }
        }
        let mut packages: Vec<String> = package_re()
            .captures_iter(text)
            .filter_map(|c| c.get(1).map(|g| g.as_str().replace(' ', "")))
            .collect();
        packages.sort();
        packages.dedup();
        if !packages.is_empty() && pno <= 12 {
            let key_text = re(r"[^a-z0-9.]+")
                .replace_all(&packages.join(", ").to_lowercase(), "")
                .into_owned();
            if seen.insert(("packages".into(), key_text)) {
                out.push(ProseFact {
                    kind: "packages".into(),
                    verbatim: norm(&packages.join(", ")),
                    value: Value::Array(packages.iter().map(|p| Value::from(p.as_str())).collect()),
                    unit: None,
                    qualifier_verbatim: None,
                    receipt: Receipt {
                        page: pno,
                        pages: None,
                    },
                    context: None,
                });
            }
        }
    }
    if !supply_candidates.is_empty() {
        // Rated (operating) and absolute-maximum are two attributes: the
        // widest range of each kind, with its line as context.
        let (rated, absolute): (Vec<_>, Vec<_>) = supply_candidates
            .into_iter()
            .partition(|c| !re(r"(?i)absolute|abs\.?\s*max|maximum ratings?").is_match(&c.4));
        for group in [rated, absolute] {
            if group.is_empty() {
                continue;
            }
            // The document's own statement of the supply outranks a wider
            // range found on a later page (an analog domain, a pin).
            let stated: Vec<_> = group
                .iter()
                .filter(|c| {
                    re(r"(?i)operat|power\s+supply|supply\s+voltage|\bV(?:DD|CC)\b\s*(?:=|range|from)")
                        .is_match(&c.4)
                })
                .cloned()
                .collect();
            let pool = if stated.is_empty() {
                group
            } else {
                stated.clone()
            };
            let pick = pool.iter().max_by(|a, b| {
                let ka = (if stated.is_empty() { 0 } else { -a.3 }, a.1 - a.0, -a.3);
                let kb = (if stated.is_empty() { 0 } else { -b.3 }, b.1 - b.0, -b.3);
                ka.partial_cmp(&kb).unwrap_or(std::cmp::Ordering::Equal)
            });
            if let Some((lo, hi, verbatim, pno, line)) = pick {
                let key_text = re(r"[^a-z0-9.]+")
                    .replace_all(&format!("{lo}-{hi}").to_lowercase(), "")
                    .into_owned();
                if seen.insert(("supply_range".into(), key_text)) {
                    out.push(ProseFact {
                        kind: "supply_range".into(),
                        verbatim: norm(verbatim),
                        value: Value::Array(vec![Value::from(*lo), Value::from(*hi)]),
                        unit: Some("V".into()),
                        qualifier_verbatim: None,
                        receipt: Receipt {
                            page: *pno,
                            pages: None,
                        },
                        context: Some(norm(line)),
                    });
                }
            }
        }
    }
    out.into_iter().take(80).collect()
}

// ---------------------------------------------------------------------------
// Package-qualified I/O
// ---------------------------------------------------------------------------

/// Renesas MPU house style: "FC-BGA2727-831" is 27 mm x 27 mm, 831 balls.
fn fc_bga() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"(?i)\bFC-?BGA\s*(\d{2})(\d{2})-(\d{2,4})\b"))
}

fn stated_gpio_ports() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"(?i)General-purpose\s+I/O\s+ports?\s*:\s*(\d+)\s+ports?"))
}

const PKG_WORD: &str = r"(?:LFQFP|TFLGA|PLQP|PTLG|PWQN|PLBG|PVQN|LQFP|UFQFPN|UFBGA|TFBGA|WLCSP|LFBGA|QFN|VQFN|HWQFN|TQFP|VFQFPN|EWLCSP|LGA|TSSOP|SOIC|SSOP|DIP|PDIP|VSSOP|HVQFN|HTQFP|nFBGA|PLCC|CSP|HLQFP|WQFN|DFN|UDFN|LQFN|QFP|BGA|WFLGA|WLBGA|VFBGA)";

/// Renesas RA/RX house style: "I/O ports for the 100-pin LQFP" ... "I/O pins: 80".
fn io_for_package() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(&format!(
            r"(?i)I/O\s+ports?\s+for\s+the\s+(?P<pins>\d{{2,3}})-pin\s+(?P<pkg>{PKG_WORD})[^\n]*\n(?:[^\n]*\n){{0,2}}?[–\-\s]*I/O\s+pins?\s*[:：]\s*(?P<io>\d{{1,3}})"
        ))
    })
}

/// Generic proximity: "<N>-pin <PKG>" and "I/O (pins|ports)[:] <M>" within a
/// short window, either order.
fn io_near_package() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(&format!(
            r"(?i)(?P<pins>\d{{2,3}})-(?:pin|lead)\s+(?P<pkg>{PKG_WORD})\b(?P<mid>[^\n]{{0,60}}\n?[^\n]{{0,80}}?)\b(?:I/Os?|GPIOs?|I/O\s+(?:pins|ports)|general[- ]purpose\s+I/Os?)\s*[:：]?\s*(?P<io>\d{{1,3}})\b"
        ))
    })
}

/// Package-qualified I/O counts: the document's own "N-pin PACKAGE -> M I/O"
/// statements. Each row is one (pin count, package) with its I/O count,
/// verbatim and page. Never a scalar for the family.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct IoRow {
    /// Pins the package carries.
    pub pin_count: i64,
    /// Package family as printed.
    pub package: String,
    /// I/O pins or ports stated.
    pub io_count: i64,
    /// Which pattern read the row.
    pub pattern: String,
    /// The statement, verbatim.
    pub verbatim: String,
    /// Page receipt.
    pub receipt: Receipt,
}

/// Package-qualified I/O counts from the document's own statements.
#[must_use]
#[allow(clippy::too_many_lines, reason = "faithful port of read_io_by_package")]
pub fn read_io_by_package(page_texts: &BTreeMap<i64, String>) -> Vec<IoRow> {
    let mut out: Vec<IoRow> = Vec::new();
    let mut seen: BTreeSet<(i64, String, i64)> = BTreeSet::new();
    for (pno, text) in page_texts {
        for (pattern, kind) in [
            (io_for_package(), "io_ports_for_package"),
            (io_near_package(), "io_near_package"),
        ] {
            for m in pattern.captures_iter(text) {
                let pins: i64 = m
                    .name("pins")
                    .and_then(|g| g.as_str().parse().ok())
                    .unwrap_or(0);
                let io: i64 = m
                    .name("io")
                    .and_then(|g| g.as_str().parse().ok())
                    .unwrap_or(0);
                let pkg = m.name("pkg").map_or("", |g| g.as_str()).to_uppercase();
                if io > pins || io < 4 {
                    continue; // plausibility: I/O never exceeds pins
                }
                let mid = m.name("mid").map_or("", |g| g.as_str());
                if kind == "io_near_package" && re(r"\d").is_match(mid) && mid.chars().count() > 40
                {
                    continue; // too much text between; likely unrelated numbers
                }
                if !seen.insert((pins, pkg.clone(), io)) {
                    continue;
                }
                out.push(IoRow {
                    pin_count: pins,
                    package: pkg,
                    io_count: io,
                    pattern: kind.to_string(),
                    verbatim: {
                        let mut v = norm(m.get(0).map_or("", |g| g.as_str()));
                        if v.chars().count() > 200 {
                            v = v.chars().take(200).collect();
                        }
                        v
                    },
                    receipt: Receipt {
                        page: *pno,
                        pages: None,
                    },
                });
            }
        }
    }
    // RZ/G: GPIO count is in the Item/Description spec list; the only package
    // is named later as FC-BGAxxxx-N. Pair them. Do not count the ch.3 ballout
    // figure (locator: definition tables, never ballout figures).
    let mut gpio_by_count: BTreeMap<i64, (i64, String)> = BTreeMap::new();
    let mut pkg_by_key: BTreeMap<(String, i64), (i64, String)> = BTreeMap::new();
    for (pno, text) in page_texts {
        for m in stated_gpio_ports().captures_iter(text) {
            let count = m
                .get(1)
                .and_then(|g| g.as_str().parse::<i64>().ok())
                .unwrap_or(0);
            gpio_by_count
                .entry(count)
                .or_insert_with(|| (*pno, norm(m.get(0).map_or("", |g| g.as_str()))));
        }
        for m in fc_bga().captures_iter(text) {
            let pins = m
                .get(3)
                .and_then(|g| g.as_str().parse::<i64>().ok())
                .unwrap_or(0);
            pkg_by_key
                .entry(("FCBGA".to_string(), pins))
                .or_insert_with(|| (*pno, m.get(0).map_or("", |g| g.as_str()).replace(' ', "")));
        }
    }
    if gpio_by_count.len() == 1 && pkg_by_key.len() == 1 {
        let (Some((io, (gp_page, gp_verb))), Some(((pkg, pins), (pkg_page, pkg_verb)))) =
            (gpio_by_count.iter().next(), pkg_by_key.iter().next())
        else {
            return out.into_iter().take(40).collect();
        };
        if (4..*pins).contains(io) {
            if !seen.insert((*pins, pkg.clone(), *io)) {
                return out.into_iter().take(40).collect();
            }
            out.push(IoRow {
                pin_count: *pins,
                package: pkg.clone(),
                io_count: *io,
                pattern: "stated_gpio_and_fcbga".into(),
                verbatim: format!("{gp_verb}; {pkg_verb}"),
                receipt: Receipt {
                    page: *gp_page,
                    pages: Some(vec![*gp_page, *pkg_page]),
                },
            });
        }
    }
    out.into_iter().take(40).collect()
}

// ---------------------------------------------------------------------------
// I/O counted from the pinout tables
// ---------------------------------------------------------------------------

/// Port-pin names across vendors: PA0 (ST/GD/AVR/Tiva/EFM32), PTA0 (Kinetis),
/// P1.0 (MSP430/EFM8/XMC), P000 (Renesas RA/RX), `PIO0_0` (LPC), GPIO0 (C2000).
const PORT_PIN_SRC: &str = r"\b(?:P[A-K]\d{1,2}|PT[A-E]\d{1,2}|P[A-C]\d{2}|R[A-G]\d|P\d\.\d{1,2}|P\d{3}|PIO\d_\d{1,2}|P\d_\d{1,2}|GPIO\d{1,3})\b";

fn port_pin() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(PORT_PIN_SRC))
}

fn pkg_cell() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(&format!(
            r"(?i)(?P<pkg1>{PKG_WORD})\s?-?\s?(?P<pins1>\d{{2,3}})\b|\b(?P<pins2>\d{{2,3}})\s?-?\s?(?:pin|lead)s?\s+(?P<pkg2>{PKG_WORD})\b|\b(?P<pins3>\d{{2,3}})\s*(?P<pkg3>{PKG_WORD})\b"
        ))
    })
}

fn pin_table_caption_bare() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"(?i)signals?\s+by\s+pin\s+number|\bpin\s+definitions?\b|\bpinout\s+(?:table|description)|\bpin\s+assignments?\s+(?:table|for)\b")
    })
}

/// A pin-name cell is the port pin, possibly with its alternate names
/// ("PC13-TAMPER-RTC", "PA0/WKUP"); a mux cell "PF3 (7)/PB5 (7)" is not.
fn pin_name_cell() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(&format!(
            r"^\s*{PORT_PIN_SRC}(?:\(\d\))?(?:\s*[-/_]\s*[A-Za-z0-9_+.\-]+(?:\s?(?:IN|OUT))?(?:\(\d\))?)*\s*_?\s*$"
        ))
    })
}

fn pin_table_caption() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(&format!(
            r"(?i)(?:(?P<pkg>{PKG_WORD})\s?-?\s?(?P<pins>\d{{2,3}})|(?P<pins_b>\d{{2,3}})\s?-?\s?(?:pin|lead)\s+(?P<pkg_b>{PKG_WORD}))\s+(?:pin\s+(?:definitions?|assignments?|descriptions?|out|functions?)|pinout|signals?\s+by\s+pin)"
        ))
    })
}

fn pinout_page() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"(?i)pin\s*(?:out|definitions?|assignments?|descriptions?|functions?|list|map)|signals?\s+by\s+pin|pin\s+multiplexing|\bmultiplexing\b|pin\s+configuration")
    })
}

/// The port pin a pin-name cell names: a strict pin-name cell, or a
/// "Default: OSCIN / Remap: PD0" cell naming exactly one port pin.
fn pin_name_in_cell(cell: &str) -> Option<String> {
    let joined = cell.replace('\n', "");
    if pin_name_cell().is_match(&joined) {
        return port_pin().find(&joined).map(|m| m.as_str().to_string());
    }
    let names: BTreeSet<String> = port_pin()
        .find_iter(&joined)
        .map(|m| m.as_str().to_string())
        .collect();
    if names.len() == 1 && !re(r"\(\d\)\s*/").is_match(&joined) && joined.chars().count() <= 40 {
        return names.into_iter().next();
    }
    None
}

fn package_in_cell(cell: &str) -> Option<(String, i64)> {
    let joined = cell.replace('\n', " ");
    let m = pkg_cell().captures(&joined)?;
    for (pkg, pins) in [("pkg1", "pins1"), ("pkg2", "pins2"), ("pkg3", "pins3")] {
        if let Some(p) = m.name(pkg) {
            let pins = m.name(pins).and_then(|g| g.as_str().parse::<i64>().ok())?;
            return Some((p.as_str().to_uppercase(), pins));
        }
    }
    None
}

/// I/O per package counted from the pinout table itself: the distinct
/// port-pin names that have a pin number in a package's column (ST/Renesas/
/// SAM shape: one column per package) or in a table captioned with the
/// package (GD32/TI shape: one table per package). The count is what the
/// document lists, not a statement it makes; `pattern` says so.
#[allow(
    clippy::indexing_slicing,
    clippy::panic,
    reason = "grid walks index rows/columns as the reference does; guards upstream"
)]
#[must_use]
#[allow(
    clippy::too_many_lines,
    reason = "faithful port of read_io_from_pin_tables"
)]
pub fn read_io_from_pin_tables(
    page_texts: &BTreeMap<i64, String>,
    tables: &[PageTables],
    page_count: i64,
) -> Vec<IoRow> {
    type PackagePins = (BTreeSet<String>, BTreeSet<i64>);
    type PkgCols = BTreeMap<usize, (String, i64)>;
    let mut per_package: BTreeMap<(String, i64), PackagePins> = BTreeMap::new();
    // (page, package columns, pin column) of the table being continued.
    let mut layout: Option<(i64, PkgCols, usize)> = None;
    let mut caption_layout: Option<(i64, (String, i64), usize, usize)> = None;
    // A document that names one package throughout (TI Tiva "64LQFP") owns
    // an uncaptioned pinout table.
    let mut named: BTreeMap<(String, i64), i64> = BTreeMap::new();
    for text in page_texts.values() {
        for m in pkg_cell().find_iter(text) {
            if let Some(p) = package_in_cell(m.as_str()) {
                *named.entry(p).or_insert(0) += 1;
            }
        }
    }
    let mut ranked: Vec<(&(String, i64), &i64)> = named.iter().collect();
    ranked.sort_by(|a, b| b.1.cmp(a.1));
    let single_package: Option<(String, i64)> = if let Some(first) = ranked.first() {
        if *first.1 >= 5
            && (ranked.len() == 1 || *first.1 >= 5 * *ranked.get(1).map_or(&1, |r| r.1))
        {
            Some(first.0.clone())
        } else {
            None
        }
    } else {
        None
    };

    for pno in 1..=page_count {
        let text = page_texts.get(&pno).map_or("", String::as_str);
        if port_pin().find_iter(text).count() < 5 {
            layout = None;
            caption_layout = None;
            continue;
        }
        let continuing = layout.as_ref().is_some_and(|l| l.0 == pno - 1)
            || caption_layout.as_ref().is_some_and(|c| c.0 == pno - 1);
        if !pinout_page().is_match(text) && !continuing {
            continue;
        }
        let Some(page_tables) = tables.iter().find(|t| t.page == pno) else {
            continue;
        };
        let caption = pin_table_caption().captures(text);
        let caption_pkg: Option<(String, i64)> = if let Some(c) = &caption {
            let pkg = c.name("pkg").or_else(|| c.name("pkg_b"));
            let pins = c.name("pins").or_else(|| c.name("pins_b"));
            match (pkg, pins) {
                (Some(p), Some(n)) => {
                    Some((p.as_str().to_uppercase(), n.as_str().parse().unwrap_or(0)))
                }
                _ => None,
            }
        } else if single_package.is_some() && pin_table_caption_bare().is_match(text) {
            single_package.clone()
        } else {
            None
        };
        for grid in &page_tables.tables {
            let rows: Vec<Vec<String>> = grid
                .iter()
                .map(|row| {
                    row.iter()
                        .map(|c| c.clone().unwrap_or_default().trim().to_string())
                        .collect()
                })
                .collect();
            if rows.len() < 6 {
                continue;
            }
            let width = rows[0].len();
            // Header: the first two rows, looking for package cells.
            let mut pkg_cols: BTreeMap<usize, (String, i64)> = BTreeMap::new();
            let mut header_rows = 0;
            for (hi, row) in rows.iter().take(2).enumerate() {
                let found: BTreeMap<usize, (String, i64)> = row
                    .iter()
                    .enumerate()
                    .filter_map(|(i, c)| package_in_cell(c).map(|p| (i, p)))
                    .collect();
                if !found.is_empty() {
                    pkg_cols = found;
                    header_rows = hi + 1;
                    break;
                }
            }
            // Pin-name column: the one with the most port-pin cells.
            let counts: Vec<usize> = (0..width)
                .map(|i| {
                    rows[header_rows..]
                        .iter()
                        .filter(|r| {
                            r.get(i).is_some_and(|c| {
                                pin_name_cell().is_match(c.replace('\n', "").as_str())
                            })
                        })
                        .count()
                })
                .collect();
            let pin_col = counts
                .iter()
                .enumerate()
                .max_by_key(|(i, c)| (*c, std::cmp::Reverse(*i)))
                .map_or(0, |(i, _)| i);
            if counts.get(pin_col).copied().unwrap_or(0) < 5 {
                continue;
            }
            if pkg_cols.is_empty() {
                if let Some((lp, lp_cols, lpin)) = &layout {
                    if *lp == pno - 1
                        && *lpin == pin_col
                        && width > *lp_cols.keys().max().unwrap_or(&0)
                    {
                        pkg_cols = lp_cols.clone(); // continuation page of the same table
                    }
                }
            }
            if pkg_cols.is_empty() {
                let mut pkg_key = caption_pkg.clone();
                if pkg_key.is_none() {
                    if let Some((cp, ckey, _, _)) = &caption_layout {
                        if *cp == pno - 1 {
                            pkg_key = Some(ckey.clone()); // captioned table continues
                        }
                    }
                }
                if let Some(pkg_key) = pkg_key {
                    caption_layout = Some((pno, pkg_key.clone(), width, pin_col));
                    let entry = per_package.entry(pkg_key).or_default();
                    for r in &rows[header_rows..] {
                        let pin_cell = r.get(pin_col);
                        if let Some(name) = pin_cell.and_then(|c| pin_name_in_cell(c)) {
                            entry.0.insert(name);
                            entry.1.insert(pno);
                        }
                    }
                }
            } else {
                layout = Some((pno, pkg_cols.clone(), pin_col));
                for (col, (pkg, pins)) in &pkg_cols {
                    let entry = per_package.entry((pkg.clone(), *pins)).or_default();
                    for r in &rows[header_rows..] {
                        let Some(cell) = r.get(*col) else {
                            continue;
                        };
                        let Some(pin_cell) = r.get(pin_col) else {
                            continue;
                        };
                        if let Some(name) = pin_name_in_cell(pin_cell) {
                            let cell = cell.trim();
                            if !cell.is_empty() && !matches!(cell, "-" | "—" | "–" | "N/A" | "NC")
                            {
                                entry.0.insert(name);
                                entry.1.insert(pno);
                            }
                        }
                    }
                }
            }
        }
    }
    let mut out: Vec<IoRow> = Vec::new();
    let mut keys: Vec<(&(String, i64), &PackagePins)> = per_package.iter().collect();
    keys.sort_by_key(|(k, _)| k.1);
    for ((pkg, pins), (pin_names, pages)) in keys {
        let io = i64::try_from(pin_names.len()).unwrap_or(0);
        if io < 6 || io >= pins - 1 {
            continue; // a package always spends pins on supply; equality means not a pinout
        }
        let mut page_list: Vec<i64> = pages.iter().copied().collect();
        page_list.sort_unstable();
        out.push(IoRow {
            pin_count: *pins,
            package: pkg.clone(),
            io_count: io,
            pattern: "pin_table_count".into(),
            verbatim: format!(
                "{io} port pins listed with a pin number for {pkg}{pins} in the pinout table"
            ),
            receipt: Receipt {
                page: *page_list.first().unwrap_or(&0),
                pages: Some(page_list.into_iter().take(8).collect()),
            },
        });
    }
    out.into_iter().take(24).collect()
}

// ---------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------

/// `round(x, 3)` semantics: half-even on the true value (format/parse).
fn round3(x: f64) -> f64 {
    format!("{x:.3}").parse::<f64>().unwrap_or(x)
}

fn size_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"(?i)(\d+(?:\.\d+)?)\s*(Mbytes?|Kbytes?|MB|KB|Kbit|Mbit|bytes?)\b"))
}

#[allow(
    dead_code,
    reason = "kept 1:1: the reference defines _MEMORY_ROW and never calls it"
)]
fn memory_row_re() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"(?i)^(?P<name>[A-Za-z][\w\s/().-]{1,40}?)\s+(?P<size>\d+(?:\.\d+)?\s*(?:Mbytes?|Kbytes?|MB|KB|Kbit|Mbit|bytes?))\b")
    })
}

/// A memory row: a region named with a size.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MemoryRow {
    /// The line, verbatim (normalised).
    pub verbatim: String,
    /// Size in KB.
    pub size_kb: f64,
    /// The qualifier as printed.
    pub qualifier_verbatim: Option<String>,
    /// Page receipt.
    pub receipt: Receipt,
}

/// Rows naming a memory region with a size, from memory-map/flash/SRAM
/// chapter pages and the cover.
#[must_use]
#[allow(clippy::too_many_lines, reason = "faithful port of read_memory")]
pub fn read_memory(
    page_texts: &BTreeMap<i64, String>,
    chapters: &Chapters,
    page_count: i64,
) -> Vec<MemoryRow> {
    let mut pages: Vec<i64> = vec![1, 2, 3];
    for cls in ["memory_map", "flash", "sram"] {
        if let Some(entries) = chapters.peripheral_classes.get(cls) {
            for e in entries.iter().take(3) {
                pages.extend(e.page..(e.page + 6).min(page_count + 1));
            }
        }
    }
    let mut seen: BTreeSet<(String, String)> = BTreeSet::new();
    let mut out: Vec<MemoryRow> = Vec::new();
    let mut sorted: Vec<i64> = pages
        .into_iter()
        .filter(|p| (1..=page_count).contains(p))
        .collect();
    sorted.sort_unstable();
    sorted.dedup();
    for pno in sorted {
        let Some(text) = page_texts.get(&pno) else {
            continue;
        };
        for line in text.split('\n') {
            let line = norm(line);
            if !size_re().is_match(&line)
                || !re(r"(?i)flash|sram|ram\b|rom\b|eeprom|otp|backup|itcm|dtcm|tcm|ccm|program memory|data memory").is_match(&line)
            {
                continue;
            }
            if re(r"(?i)for example|if a device|for instance|e\.g\.|assume|^(?:table|figure|fig\.)\s*\d").is_match(&line) {
                continue;
            }
            let size = size_re().captures(&line);
            let Some((value, unit)) = size.as_ref().and_then(|c| {
                let v: f64 = c.get(1)?.as_str().parse().ok()?;
                let u = c.get(2)?.as_str().to_lowercase();
                Some((v, u))
            }) else {
                continue;
            };
            let kb = if unit.starts_with('m') {
                value * 1024.0
            } else if unit.starts_with("byte") {
                value / 1024.0
            } else if unit.contains("bit") {
                value / 8.0
            } else {
                value
            };
            let key = (
                line.chars().take(60).collect::<String>(),
                size.as_ref()
                    .map(|c| c.get(0).map_or("", |g| g.as_str()).to_string())
                    .unwrap_or_default(),
            );
            if !seen.insert(key) {
                continue;
            }
            let qualifier = qualifier()
                .captures(&line)
                .and_then(|c| c.get(1))
                .map(|g| g.as_str().to_string());
            let mut v = line.clone();
            if v.chars().count() > 200 {
                v = v.chars().take(200).collect();
            }
            out.push(MemoryRow {
                verbatim: v,
                size_kb: round3(kb),
                qualifier_verbatim: qualifier,
                receipt: Receipt {
                    page: pno,
                    pages: None,
                },
            });
        }
    }
    out.into_iter().take(120).collect()
}

// ---------------------------------------------------------------------------
// The document
// ---------------------------------------------------------------------------

/// Provenance stamp of a family-document row.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Meta {
    /// Which reader produced the row.
    pub extracted_by: String,
    /// Extraction time (the caller fills it).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub extracted_at: Option<String>,
    /// Document hash (the caller fills it).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub document_sha256: Option<String>,
    /// Where the document came from (the caller fills it).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_path: Option<String>,
    /// Page count of the document.
    pub page_count: i64,
    /// How many pages the readers scanned.
    pub pages_read: i64,
    /// Caller-supplied vendor.
    pub vendor: Option<String>,
    /// Models the readers called (always empty: this layer is deterministic).
    pub models_used: Vec<String>,
}

/// What the document claims about itself — the report's scoreboard.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Bogey {
    /// Chapter-list entries.
    pub chapter_entries: i64,
    /// Peripheral classes named by the chapter list.
    pub peripheral_classes: i64,
    /// Peripheral classes named by instance designators.
    pub instance_classes: i64,
    /// Instances meeting the support floor.
    pub instances_asserted: i64,
    /// Feature rows read.
    pub features: i64,
    /// Feature rows whose qualifier is printed verbatim.
    pub features_qualified: i64,
    /// Chapter-scoped feature rows read.
    pub chapter_features: i64,
    /// Distinct (page, section) pairs of the same.
    pub chapter_feature_sections: i64,
    /// Memory rows read.
    pub memory_rows: i64,
    /// Package-qualified I/O rows read.
    pub io_by_package_rows: i64,
}

/// One family-document row — the reference's wire, field for field.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct DocumentRow {
    /// Schema stamp.
    pub schema: String,
    /// Provenance stamp.
    #[serde(rename = "_meta")]
    pub meta: Meta,
    /// Cover identity.
    pub identity: Identity,
    /// Chapter list.
    pub chapters: Chapters,
    /// Peripheral instances by class.
    pub peripheral_instances: BTreeMap<String, InstanceClass>,
    /// Feature rows, every reader's output in order.
    pub features: Vec<FeatureRow>,
    /// Chapter-scoped feature rows.
    pub chapter_features: Vec<FeatureRow>,
    /// Memory rows.
    pub memory: Vec<MemoryRow>,
    /// Prose facts.
    pub prose_facts: Vec<ProseFact>,
    /// Package-qualified I/O rows.
    pub io_by_package: Vec<IoRow>,
    /// The report's scoreboard.
    pub bogey: Bogey,
}

/// What the PDF layer feeds the reader: page texts, extracted tables,
/// bookmarks, the PDF title, and the filename stem.
pub struct FamilyDocInput<'a> {
    /// Page texts, 1-based (page `i` is `pages[i - 1]`).
    pub pages: &'a BTreeMap<i64, String>,
    /// Table grids the PDF layer extracted per page.
    pub tables: &'a [PageTables],
    /// Bookmarks as (level, title, page).
    pub toc: &'a [(i64, String, i64)],
    /// The PDF's own title metadata.
    pub pdf_title: Option<&'a str>,
    /// The file stem the PDF layer read the document from.
    pub source_stem: &'a str,
    /// Page count of the document.
    pub page_count: i64,
    /// Caller-supplied vendor.
    pub vendor: Option<&'a str>,
    /// Page-scan cap, matching the reference's `max_text_pages`.
    pub max_text_pages: Option<usize>,
}

/// The family-document reader over the PDF layer's output.
#[must_use]
#[allow(
    clippy::too_many_lines,
    reason = "faithful port of read_family_document"
)]
pub fn read_family_document(input: &FamilyDocInput<'_>) -> DocumentRow {
    let limit = input.max_text_pages.map_or(input.page_count, |m| {
        i64::try_from(m).unwrap_or(0).min(input.page_count)
    });
    let page_texts: BTreeMap<i64, String> = input
        .pages
        .iter()
        .filter(|(pno, _)| **pno <= limit)
        .map(|(p, t)| (*p, t.clone()))
        .collect();
    let cover = page_texts.get(&1).cloned().unwrap_or_default();
    let second = page_texts.get(&2).cloned().unwrap_or_default();
    let identity = read_identity(&cover, &second, input.pdf_title, input.source_stem);
    let mut chapters = read_chapters(input.toc, &page_texts, input.page_count);
    let instances = read_instances(&page_texts, 2);
    let mut features = read_features(&page_texts, 6);
    features.extend(read_features_table(&page_texts, input.tables, 80));
    features.extend(read_description_facts(&page_texts, 14));
    features.extend(read_includes_list(&page_texts, 20));
    features.extend(read_item_description_specs(&page_texts, 40));
    let chapter_features = read_chapter_features(&page_texts, &chapters, input.page_count);
    let memory = read_memory(&page_texts, &chapters, input.page_count);
    let prose_facts = read_prose_facts(&page_texts, 160);
    let mut io_by_package = read_io_by_package(&page_texts);
    io_by_package.extend(read_io_from_pin_tables(
        &page_texts,
        input.tables,
        input.page_count,
    ));
    // The wire never carries all_entries; the feature readers consume it.
    let chapter_entries = chapters.entries;
    chapters.all_entries.clear();
    let bogey = Bogey {
        chapter_entries,
        peripheral_classes: i64::try_from(chapters.peripheral_classes.len()).unwrap_or(0),
        instance_classes: i64::try_from(instances.len()).unwrap_or(0),
        instances_asserted: instances.values().map(|c| c.count_asserted).sum(),
        features: i64::try_from(features.len()).unwrap_or(0),
        features_qualified: i64::try_from(
            features
                .iter()
                .filter(|f| f.qualifier_verbatim.is_some())
                .count(),
        )
        .unwrap_or(0),
        chapter_features: i64::try_from(chapter_features.len()).unwrap_or(0),
        chapter_feature_sections: i64::try_from({
            let mut pairs: BTreeSet<(i64, Option<String>)> = BTreeSet::new();
            for f in &chapter_features {
                pairs.insert((f.receipt.page, f.section.clone().flatten()));
            }
            pairs.len()
        })
        .unwrap_or(0),
        memory_rows: i64::try_from(memory.len()).unwrap_or(0),
        io_by_package_rows: i64::try_from(io_by_package.len()).unwrap_or(0),
    };
    DocumentRow {
        schema: CENSUS_SCHEMA.into(),
        meta: Meta {
            extracted_by: "harness.electronics.family_document".into(),
            extracted_at: None,
            document_sha256: None,
            source_path: None,
            page_count: input.page_count,
            pages_read: limit,
            vendor: input.vendor.map(str::to_string),
            models_used: Vec::new(),
        },
        identity,
        chapters,
        peripheral_instances: instances,
        features,
        chapter_features,
        memory,
        prose_facts,
        io_by_package,
        bogey,
    }
}
