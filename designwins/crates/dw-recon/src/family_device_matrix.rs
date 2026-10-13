//! Family device-matrix reader — port of
//! `harness/electronics/family_device_matrix.py` (schema
//! `harness.electronics-family-device-matrix.v1`).
//!
//! Deterministic reader for family device-comparison tables (knife lane v1).
//! Input: a document the census marked `above_opn` / `family_matrix`. Output:
//! one family record in the CR conventions shape: `shared` (attributes the
//! table prints identically for every variant) and `variants` (one row per
//! data column, bound to a concrete part only when the document itself binds
//! it — header token, header code list, or a printed device-summary join).
//!
//! Every value keeps its verbatim string, page, row label and column. A value
//! the reader cannot type with certainty is `unknown` with the verbatim
//! retained; nothing is guessed. Column-to-part binding that cannot be made
//! from printed text is `part_number: null` with the header token kept —
//! never a minted part number.
//!
//! The label grammar here is deliberately stricter than the census's
//! `attributes_in_label` (which only asks "does this row address the
//! attribute?"). Here the label decides which knife the value lands in:
//! "GPIOs" is `gpio_count` not `pin_count`, "Tamper pins" is nothing,
//! "Maximum CPU frequency" is `freq_mhz` not `core`, and a row that prints
//! "SPI / I2S : 4/3" becomes `spi_count=4` and `i2s_count=3` because the
//! document itself paired them.
//!
//! As with [`crate::document_scope`] and [`crate::family_census`] the port
//! takes the PDF layer's output — table grids, cell boxes, page words — and
//! reads no documents itself. This module calls no models.

use std::collections::{BTreeMap, BTreeSet};

use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::sync::OnceLock;

use crate::family_census::{
    attributes_in_label, classify_table, is_concrete_part_token, is_wildcard_token,
    packages_from_text, strip_title_rows,
};

/// Schema stamp of a family record.
pub const MATRIX_SCHEMA: &str = "harness.electronics-family-device-matrix.v1";

/// Which reader produced the record.
pub const EXTRACTED_BY: &str = "harness family_device_matrix v1 (deterministic PyMuPDF)";

#[allow(
    clippy::expect_used,
    reason = "patterns are literals from the reference"
)]
fn re(src: &str) -> Regex {
    Regex::new(src).expect("pattern is a literal from the reference")
}

fn norm(text: Option<&str>) -> String {
    match text {
        None => String::new(),
        Some(t) => t.split_whitespace().collect::<Vec<_>>().join(" "),
    }
}

/// Attributes that get a typed numeric leaf and their default unit.
pub const NUMERIC_ATTRIBUTES: &[(&str, &str)] = &[
    ("code_flash_kb", "KB"),
    ("data_flash_kb", "KB"),
    ("eeprom_kb", "KB"),
    ("sram_kb", "KB"),
    ("freq_mhz", "MHz"),
    ("pin_count", "pins"),
    ("gpio_count", "count"),
    ("adc_count", "count"),
    ("adc_channels", "channels"),
    ("dac_count", "count"),
    ("dac_channels", "channels"),
    ("timers", "count"),
    ("timer_advanced", "count"),
    ("timer_general_purpose", "count"),
    ("timer_basic", "count"),
    ("timer_low_power", "count"),
    ("timer_watchdog", "count"),
    ("timer_systick", "count"),
    ("rtc", "count"),
    ("pwm_channels", "channels"),
    ("usart_count", "count"),
    ("uart_count", "count"),
    ("lpuart_count", "count"),
    ("spi_count", "count"),
    ("i2s_count", "count"),
    ("i2c_count", "count"),
    ("can_count", "count"),
    ("sdio_count", "count"),
    ("comparator_count", "count"),
    ("opamp_count", "count"),
    ("operating_voltage", "V"),
    ("temp_range", "degC"),
    ("standby_current_ua", "uA"),
];

/// Attributes that are presence flags, not numbers.
const BOOLEAN_ATTRIBUTES: &[&str] = &[
    "usb",
    "ethernet",
    "trustzone",
    "crypto",
    "lcd",
    "camera",
    "fsmc",
];

/// (attribute, must-match, must-not-match). Evaluated against the full label
/// (carried group label + sub label); first match wins per token group.
const LABEL_RULES: &[(&str, &str, Option<&str>)] = &[
    (
        "data_flash_kb",
        r"(?i)\bdata\s*(?:flash|area|memory)\b",
        None,
    ),
    ("eeprom_kb", r"(?i)\beeprom\b", None),
    (
        "code_flash_kb",
        r"(?i)\bflash\b|\bprogram\s+memory\b|\bcode\s+(?:area|memory)\b",
        Some(
            r"(?i)\bdata\b|\bexternal\b|\boption\b|\bsector\b|\bpage\b|\bbank|\bfs?mc\b|\bnand\b|\bnor\b|controller|\becc\b|protection|interface|\botfdec\b|\bxspi\b|\bqspi\b|\bospi\b",
        ),
    ),
    (
        "sram_kb",
        r"(?i)\bs?ram\b",
        Some(
            r"(?i)\bbackup\b|\bcache\b|\bparity\b\s*only|\bdma\b|\bfs?mc\b|controller|\bexternal\b|\bpsram\b|\bsdram\b|\becc\b|\bsram\d\b|\baxi\b|\bahb\b|\bd\d\s+domain\b|\bitcm\b|\bdtcm\b|\btcm\b|\bccm\b|\bretention\b|instruction|flexible",
        ),
    ),
    (
        "freq_mhz",
        r"(?i)\b(?:frequency|freq\.?|clock\s+speed|cpu\s+speed|speed)\b|\bmhz\b",
        Some(r"(?i)\badc\b|\bbus\b|\bexternal\b"),
    ),
    (
        "core",
        r"(?i)\b(?:core|cpu|cortex)\b",
        Some(r"(?i)frequen|speed|mhz|\bram\b"),
    ),
    (
        "package",
        r"(?i)\bpackages?\b",
        Some(r"(?i)\bpins?\b|tamper|wakeup|\blegacy\b|\bsmps\b|dedicated|thermal|\bfootprint\b"),
    ),
    (
        "gpio_count",
        r"(?i)\bgpios?\b|\bi/os?\b|\bgeneral[\s-]purpose\s+i/?os?\b|\bio\s+pins?\b",
        Some(
            r"(?i)\bwakeup\b|\btamper\b|\bfast\b|\bnormal\b|\bfs?mc\b|tolerant|\b5\s*v\b|\btc\b|\btta?\b|\bft\b|\bhigh[\s-]sink\b|\bmultiplexed\b",
        ),
    ),
    (
        "timer_advanced",
        r"(?i)\btimers?\b.*\badvanced\b|\badvanced[\s-]control\b",
        None,
    ),
    (
        "timer_general_purpose",
        r"(?i)\btimers?\b.*\bgeneral\b|\bgeneral\s*-?\s*purpose\b",
        Some(r"(?i)\bi/?os?\b|input|output|gpio"),
    ),
    ("timer_basic", r"(?i)\btimers?\b.*\bbasic\b", None),
    (
        "timer_low_power",
        r"(?i)\btimers?\b.*\blow[\s-]power\b|\blptim\b",
        None,
    ),
    ("timer_systick", r"(?i)\bsystick\b", None),
    ("timer_watchdog", r"(?i)\bwatchdog\b|\bwdg\b", None),
    ("rtc", r"(?i)\brtc\b|\breal[\s-]time\s+clock\b", None),
    (
        "pwm_channels",
        r"(?i)\bpwm\b",
        Some(r"(?i)advanced|general|basic|motor|low[\s-]power|except|complementary"),
    ),
    (
        "timers",
        r"(?i)\btimers?\b",
        Some(
            r"(?i)advanced|general|basic|low|systick|watchdog|wdg|\blp\b|\b16-bit\b|\b32-bit\b|encoder|motor|pwm",
        ),
    ),
    (
        "adc_channels",
        r"(?i)\badcs?\b.*\bchannels?\b|\bchannels?\b.*\badcs?\b",
        None,
    ),
    (
        "adc_count",
        r"(?i)\badcs?\b",
        Some(r"(?i)channel|resolution|speed|rate|bit\b"),
    ),
    (
        "dac_channels",
        r"(?i)\bdacs?\b.*\bchannels?\b|\bchannels?\b.*\bdacs?\b",
        None,
    ),
    (
        "dac_count",
        r"(?i)\bdacs?\b",
        Some(r"(?i)channel|resolution|bit\b"),
    ),
    ("comparator_count", r"(?i)\bcomparators?\b|\bcomp\b", None),
    (
        "opamp_count",
        r"(?i)\bop[\s-]?amps?\b|\boperational\s+amplifier",
        None,
    ),
    (
        "operating_voltage",
        r"(?i)\b(?:operating|supply|power\s+supply)\s+voltage\b|\bvdd\b",
        Some(r"(?i)temperature|\busb\b|adc|analog|\bvbat\b|\bvref\b|\bvdda\b|\bvddio"),
    ),
    (
        "temp_range",
        r"(?i)\btemperature",
        Some(r"(?i)sensor|indicator|monitor|junction\s+only"),
    ),
    (
        "standby_current_ua",
        r"(?i)\b(?:standby|stop|shutdown|deep\s*sleep|power[\s-]?down)\b.*?\b(?:current|consumption)\b",
        None,
    ),
    (
        "lpuart_count",
        r"(?i)\blpuart\b|\blow[\s-]power\s+uart\b",
        None,
    ),
    ("usart_count", r"(?i)\busart\b", None),
    ("uart_count", r"(?i)\buart\b", None),
    ("i2s_count", r"(?i)\bi2s\b", None),
    (
        "spi_count",
        r"(?i)\bspi\b",
        Some(r"(?i)\bquad|\bocto|\bqspi\b|\bospi\b|\bxspi\b|\bhyper"),
    ),
    ("i2c_count", r"(?i)\bi2c\b|\bi²c\b", None),
    (
        "can_count",
        r"(?i)\bcan(?:\s*fd)?\b|\bfdcan\b|\bbxcan\b",
        None,
    ),
    ("sdio_count", r"(?i)\bsdio\b|\bsdmmc\b|\bsd/mmc\b", None),
    ("usb", r"(?i)\busb\b", None),
    ("ethernet", r"(?i)\bethernet\b|\beth\b", None),
    ("trustzone", r"(?i)\btrustzone\b", None),
    ("crypto", r"(?i)\bcrypto|\baes\b|\bhash\b|\bpka\b", None),
    ("lcd", r"(?i)\blcd\b|\bltdc\b", None),
    ("camera", r"(?i)\bcamera\b|\bdcmi\b", None),
    (
        "fsmc",
        r"(?i)\bfsmc\b|\bfmc\b",
        Some(
            r"(?i)\bnor\w*|\bnand\b|\bs?ram\b|\bpsram\b|\bsdram\b|multiplexed|\bmux\b|controller|\bi/o",
        ),
    ),
];

/// Unit printed in the label, when one is.
fn unit_from_label(label: &str) -> Option<&'static str> {
    static RULES: OnceLock<Vec<(Regex, &'static str)>> = OnceLock::new();
    let rules = RULES.get_or_init(|| {
        vec![
            (re(r"(?i)\b(?:kbytes?|kb|kbyte|kilobytes?)\b"), "KB"),
            (re(r"(?i)\b(?:mbytes?|mb|mbyte)\b"), "MB"),
            (re(r"(?i)\bmhz\b"), "MHz"),
            (re(r"(?i)\bkhz\b"), "kHz"),
            (re(r"(?i)[µu]a\b"), "uA"),
            (re(r"(?i)\bma\b"), "mA"),
            (re(r"(?i)°\s*c\b|\bdeg\s*c\b"), "degC"),
        ]
    });
    for (pattern, unit) in rules {
        if pattern.is_match(label) {
            return Some(*unit);
        }
    }
    // `(?<![a-z])v\b` with re.I: a V whose predecessor is not a letter.
    for (idx, ch) in label.char_indices() {
        if ch != 'v' && ch != 'V' {
            continue;
        }
        let after_ok = label[idx + 1..]
            .chars()
            .next()
            .is_none_or(|c| !c.is_alphanumeric() && c != '_');
        if !after_ok {
            continue;
        }
        let before_is_letter = label[..idx]
            .chars()
            .next_back()
            .is_some_and(char::is_alphabetic);
        if !before_is_letter {
            return Some("V");
        }
    }
    None
}

/// Single attribute for a (sub)label, or `None` when it addresses nothing we
/// read. First matching rule wins.
#[must_use]
pub fn label_attribute(label: &str) -> Option<&'static str> {
    for (attribute, must, must_not) in LABEL_RULES {
        if !re(must).is_match(label) {
            continue;
        }
        if must_not.is_some_and(|p| re(p).is_match(label)) {
            continue;
        }
        return Some(attribute);
    }
    None
}

/// The reference's `_strip_footnotes`: drop trailing "(n)" markers, looping
/// to a fixed point.
fn strip_footnotes(text: &str) -> String {
    let mut text = text.to_string();
    loop {
        let Some(m) = re(r"\s*\(\d{1,2}\)$").find(&text) else {
            return text;
        };
        let before_ok = m.start() > 0
            && text[..m.start()]
                .chars()
                .next_back()
                .is_some_and(|c| !c.is_whitespace());
        if !before_ok {
            return text;
        }
        text.truncate(m.start());
        let trimmed = text.trim_end().to_string();
        text = trimmed;
    }
}

/// `("SPI / I2S")` -> `["SPI", "I2S"]`; `("SPI [I2S]")` -> `["SPI", "I2S"]`.
/// Anything else -> `[sub_label]`. Only a slash between two multi-character
/// names is a pairing; "I/O", "D/A", "A/D", "R/W" are single names.
#[must_use]
#[allow(
    clippy::indexing_slicing,
    clippy::panic,
    reason = "grid walks index rows/columns as the reference does; guards upstream"
)]
pub fn split_composite_label(sub_label: &str) -> Vec<String> {
    // The reference searches `(?<=[\w)][\w)])\s*/\s*(?=\w\w)|\[` and splits
    // `(?<=[\w)][\w)])\s*/\s*(?=\w\w)|\s*\[\s*|\s*\]\s*`. The regex crate has
    // no look-around; both are replayed here. The look-behind is checked at
    // the position the match STARTS — the beginning of the whitespace run
    // before the slash (`\s*/\s*`), not the slash itself — and `)` counts as
    // wordish ("I/O Pins(1)/ Peripheral Pin Select" pairs at the second
    // slash; "I/O" and "D/A" do not pair at theirs).
    let chars: Vec<char> = sub_label.chars().collect();
    let n = chars.len();
    let wordish = |c: char| c.is_alphanumeric() || c == '_' || c == ')';
    let word = |c: char| c.is_alphanumeric() || c == '_';
    let mut spans: Vec<(usize, usize)> = Vec::new();
    let mut hit = false;
    let mut p = 0;
    while p < n {
        // `(?<=[\w)][\w)])\s*/\s*(?=\w\w)` at p.
        let mut j = p;
        while j < n && chars[j].is_whitespace() {
            j += 1;
        }
        if j < n && chars[j] == '/' && p >= 2 && wordish(chars[p - 2]) && wordish(chars[p - 1]) {
            let mut end = j + 1;
            while end < n && chars[end].is_whitespace() {
                end += 1;
            }
            if end + 2 <= n && word(chars[end]) && word(chars[end + 1]) {
                hit = true;
                spans.push((p, end));
                p = end;
                continue;
            }
        }
        // `\s*\[\s*` / `\s*\]\s*` at p. Only `\[` counts as the search's
        // pairing hit; `]` splits only once something else paired.
        let mut k = p;
        while k < n && chars[k].is_whitespace() {
            k += 1;
        }
        if k < n && (chars[k] == '[' || chars[k] == ']') {
            let mut end = k + 1;
            while end < n && chars[end].is_whitespace() {
                end += 1;
            }
            hit = hit || chars[k] == '[';
            spans.push((p, end));
            p = end;
            continue;
        }
        p += 1;
    }
    if !hit {
        return vec![sub_label.to_string()];
    }
    let mut parts: Vec<String> = Vec::new();
    let mut last = 0;
    for (start, end) in spans {
        if start > last {
            parts.push(chars[last..start].iter().collect());
        }
        last = end;
    }
    if last < n {
        parts.push(chars[last..].iter().collect());
    }
    parts.retain(|p| !p.is_empty());
    if (2..=3).contains(&parts.len()) {
        parts
    } else {
        vec![sub_label.to_string()]
    }
}

/// `("4/3 (simplex)", 2)` -> `["4", "3 (simplex)"]`; `None` when the value
/// does not carry exactly `count` components. Parentheticals after counts
/// are notes, not components ("3/2 (0-2)/(1-2)").
#[must_use]
pub fn split_composite_value(text: &str, count: usize) -> Option<Vec<String>> {
    let parts: Vec<String> = re(r"\s*/\s*|\s*\[\s*|\s*\]\s*")
        .split(text)
        .map(str::trim)
        .filter(|p| !p.is_empty())
        .map(str::to_string)
        .collect();
    if parts.len() == count {
        return Some(parts);
    }
    let bare = re(r"\s*\([^)]*\)").replace_all(text, "").into_owned();
    let bare = bare.trim().to_string();
    if bare != text {
        let parts: Vec<String> = re(r"\s*/\s*|\s*\[\s*|\s*\]\s*")
            .split(&bare)
            .map(str::trim)
            .filter(|p| !p.is_empty())
            .map(str::to_string)
            .collect();
        if parts.len() == count {
            return Some(parts);
        }
    }
    None
}

/// Interface names a shared module row lists ("eUSCI B: SPI, I2C" names one
/// module class speaking several protocols: the printed count applies to
/// every protocol listed).
fn shared_module_attributes(label: &str) -> Vec<&'static str> {
    static LIST: OnceLock<Regex> = OnceLock::new();
    static NAMES: &[(&str, &str)] = &[
        ("uart", "uart_count"),
        ("usart", "usart_count"),
        ("lpuart", "lpuart_count"),
        ("spi", "spi_count"),
        ("i2c", "i2c_count"),
        ("i²c", "i2c_count"),
        ("i2s", "i2s_count"),
        ("can", "can_count"),
        ("can fd", "can_count"),
    ];
    let list = LIST.get_or_init(|| {
        re(r"(?i)(?:^|[:\s_])((?:(?:e?USCI\s*[AB]|UART|USART|LPUART|SPI|I2C|I²C|I2S|IrDA|LIN|CAN(?:\s*FD)?|SMBus|PMBus)\s*,\s*)+(?:UART|USART|LPUART|SPI|I2C|I²C|I2S|IrDA|LIN|CAN(?:\s*FD)?|SMBus|PMBus))\b")
    });
    let Some(m) = list.captures(label) else {
        return Vec::new();
    };
    let Some(group) = m.get(1) else {
        return Vec::new();
    };
    let names: Vec<String> = group
        .as_str()
        .split(',')
        .map(|n| n.trim().to_lowercase())
        .collect();
    let mut out: Vec<&'static str> = Vec::new();
    for name in &names {
        if let Some((_, attribute)) = NAMES.iter().find(|(n, _)| n == name) {
            if !out.contains(attribute) {
                out.push(attribute);
            }
        }
    }
    if out.len() >= 2 || (!out.is_empty() && names.len() >= 2) {
        out
    } else {
        Vec::new()
    }
}

/// Interface rows whose counts may be summed across shared modules.
const INTERFACE_COUNT_ATTRIBUTES: &[&str] = &[
    "uart_count",
    "usart_count",
    "lpuart_count",
    "spi_count",
    "i2c_count",
    "i2s_count",
    "can_count",
];

/// Attributes addressed by one table row: `[(attribute, component_index)]`
/// where `component_index` is `None` for a plain row and `0..n-1` for a
/// composite row like 'SPI / I2S'.
#[must_use]
pub fn row_attributes(group_label: &str, sub_label: &str) -> Vec<(String, Option<usize>)> {
    if !sub_label.is_empty() {
        let components = split_composite_label(sub_label);
        if components.len() > 1 {
            let mut out: Vec<(String, Option<usize>)> = Vec::new();
            for (index, component) in components.iter().enumerate() {
                let full = format!("{group_label} {component}");
                let attribute = label_attribute(&full).or_else(|| label_attribute(component));
                if let Some(attribute) = attribute {
                    out.push((attribute.to_string(), Some(index)));
                }
            }
            if !out.is_empty() {
                return out;
            }
        }
    }
    let full = [group_label, sub_label]
        .iter()
        .filter(|p| !p.is_empty())
        .copied()
        .collect::<Vec<_>>()
        .join(" ");
    let shared = shared_module_attributes(&full);
    if !shared.is_empty() {
        return shared.into_iter().map(|a| (a.to_string(), None)).collect();
    }
    let attribute = label_attribute(&full).or_else(|| {
        if sub_label.is_empty() {
            None
        } else {
            label_attribute(sub_label)
        }
    });
    match attribute {
        Some(a) => vec![(a.to_string(), None)],
        None => Vec::new(),
    }
}

/// The unit words a printed suffix can carry.
fn unit_words() -> &'static BTreeMap<&'static str, &'static str> {
    static M: OnceLock<BTreeMap<&'static str, &'static str>> = OnceLock::new();
    M.get_or_init(|| {
        let mut m = BTreeMap::new();
        for (k, v) in [
            ("kbytes", "KB"),
            ("kbyte", "KB"),
            ("kb", "KB"),
            ("k", "KB"),
            ("k bytes", "KB"),
            ("k byte", "KB"),
            ("mbytes", "MB"),
            ("mbyte", "MB"),
            ("mb", "MB"),
            ("m", "MB"),
            ("m bytes", "MB"),
            ("m byte", "MB"),
            ("mhz", "MHz"),
            ("khz", "kHz"),
            ("ua", "uA"),
            ("µa", "uA"),
            ("ma", "mA"),
            ("v", "V"),
            ("°c", "degC"),
        ] {
            m.insert(k, v);
        }
        m
    })
}

fn numeric_unit(attribute: &str) -> Option<&'static str> {
    NUMERIC_ATTRIBUTES
        .iter()
        .find(|(a, _)| *a == attribute)
        .map(|(_, u)| *u)
}

/// Typed leaf: one printed cell, typed with `status` in
/// {typed, boolean, verbatim, unknown}. The verbatim string is always kept.
pub type Leaf = Map<String, Value>;

/// `(column, attributes, label_unit, label)` — one attribute column of a
/// parts-as-rows grid.
type RowColumn = (usize, Vec<(String, Option<usize>)>, Option<String>, String);

/// `(attribute, table_index, page, label, cols, whole_table, value)` — one
/// pending attribute row, before shared-vs-variant resolution.
type PendingRow = (
    String,
    usize,
    usize,
    String,
    Vec<(Value, Leaf)>,
    bool,
    Value,
);

fn leaf_new(verbatim: &str) -> Leaf {
    let mut leaf = Map::new();
    leaf.insert("verbatim".into(), json!(verbatim));
    leaf
}

fn leaf_str(leaf: &Leaf, key: &str) -> Option<String> {
    leaf.get(key).and_then(Value::as_str).map(str::to_string)
}

/// Type one printed cell.
#[must_use]
#[allow(
    clippy::too_many_lines,
    reason = "faithful sequential port of parse_value"
)]
#[allow(
    clippy::indexing_slicing,
    clippy::panic,
    reason = "grid walks index rows/columns as the reference does; guards upstream"
)]
pub fn parse_value(verbatim: &str, attribute: &str, label_unit: Option<&str>) -> Leaf {
    static YES_NO: OnceLock<Regex> = OnceLock::new();
    static LEAD: OnceLock<Regex> = OnceLock::new();
    static QUALIFIER: OnceLock<Regex> = OnceLock::new();
    static RANGE: OnceLock<Regex> = OnceLock::new();
    static RANGE_ANY: OnceLock<Regex> = OnceLock::new();
    static NONE: OnceLock<Regex> = OnceLock::new();
    static YES: OnceLock<Regex> = OnceLock::new();
    static NO: OnceLock<Regex> = OnceLock::new();
    static TIMES: OnceLock<Regex> = OnceLock::new();
    static DC_TO: OnceLock<Regex> = OnceLock::new();
    static MULTI: OnceLock<Regex> = OnceLock::new();
    static BITWIDTH: OnceLock<Regex> = OnceLock::new();
    static NUMBER: OnceLock<Regex> = OnceLock::new();
    static BREAKDOWN: OnceLock<Regex> = OnceLock::new();
    static WITH_UNIT: OnceLock<Regex> = OnceLock::new();
    static WITH_NOTE: OnceLock<Regex> = OnceLock::new();
    let raw = norm(Some(verbatim));
    let text = strip_footnotes(&raw);
    let mut leaf = leaf_new(&raw);
    if text.is_empty() {
        leaf.insert("status".into(), json!("unknown"));
        leaf.insert("reason".into(), json!("empty_cell"));
        return leaf;
    }

    if BOOLEAN_ATTRIBUTES.contains(&attribute) {
        if YES_NO
            .get_or_init(|| re(r"(?i)^(yes|y|✓|x|no|n|n/a|na)$"))
            .is_match(&text)
        {
            let yes = matches!(text.to_lowercase().as_str(), "yes" | "y" | "✓" | "x");
            leaf.insert("status".into(), json!("boolean"));
            leaf.insert("value".into(), json!(yes));
            return leaf;
        }
        if let Some(c) = LEAD
            .get_or_init(|| re(r"(?i)^(yes|no)\b[\s:(,-]*(.*)$"))
            .captures(&text)
        {
            let flag = c
                .get(1)
                .map(|m| m.as_str().to_lowercase())
                .unwrap_or_default();
            let note = c
                .get(2)
                .map(|m| m.as_str().trim_matches([' ', ')']).to_string());
            leaf.insert("status".into(), json!("boolean"));
            leaf.insert("value".into(), json!(flag == "yes"));
            if note.as_deref().is_some_and(|n| !n.is_empty()) {
                leaf.insert("note".into(), json!(note.unwrap_or_default()));
            }
            return leaf;
        }
        let num_lead = re(r"^\d+\s").is_match(&text) || re(r"^\d{1,6}(?:\.\d+)?$").is_match(&text);
        if num_lead {
            // "1", "2 (FS + HS)": instances printed where a flag was expected.
            let first = text.split_whitespace().next().unwrap_or("0");
            let value: f64 = first.parse().unwrap_or(0.0);
            leaf.insert("status".into(), json!("boolean"));
            leaf.insert("value".into(), json!(value > 0.0));
            leaf.insert("note".into(), json!(text));
            return leaf;
        }
        leaf.insert("status".into(), json!("verbatim"));
        return leaf;
    }

    if attribute == "package" {
        let packages = packages_from_text(&text);
        leaf.insert("status".into(), json!("verbatim"));
        if !packages.is_empty() {
            leaf.insert(
                "packages".into(),
                serde_json::to_value(&packages).unwrap_or(Value::Null),
            );
        }
        return leaf;
    }

    let Some(default_unit) = numeric_unit(attribute) else {
        leaf.insert("status".into(), json!("verbatim"));
        return leaf;
    };
    let mut unit = label_unit.unwrap_or(default_unit).to_string();

    // A printed qualifier ("Up to 2 Mbytes", "at least 4") bounds the number
    // rather than stating it. The number is typed and the exact words travel
    // with it, so no consumer can read a bound as a part value by accident.
    let qualified = QUALIFIER
        .get_or_init(|| {
            re(r"(?i)^(up\s+to|max(?:imum|\.)?|at\s+least|min(?:imum|\.)?)\s*:?\s*(.+)$")
        })
        .captures(&text);
    if let Some(c) = &qualified {
        if attribute != "operating_voltage" && attribute != "temp_range" {
            let inner_text = c.get(2).map(|m| m.as_str()).unwrap_or_default();
            let mut inner = parse_value(inner_text, attribute, label_unit);
            if leaf_str(&inner, "status").as_deref() == Some("typed") {
                inner.insert("verbatim".into(), json!(raw));
                inner.insert(
                    "qualifier".into(),
                    json!(c.get(1).map(|m| m.as_str()).unwrap_or_default()),
                );
                return inner;
            }
        }
    }

    if attribute == "operating_voltage" || attribute == "temp_range" {
        let mut cleaned = text.replace(['−', '–', '—'], "-").replace('º', "°");
        // `(?<=\d)\s*(?:V|°\s*C)\b` — a unit right after a digit.
        cleaned = re(r"(?i)(\d)\s*(?:V|°\s*C)\b")
            .replace_all(&cleaned, "$1")
            .into_owned();
        cleaned = re(r"(?i)\s*(?:V|°\s*C)\s*$")
            .replace_all(&cleaned, "")
            .into_owned();
        // `(?<![\d.])-\s+(?=\d)` — a spaced hyphen between number parts.
        cleaned = re(r"([^\d.])-+\s+(\d)")
            .replace_all(&cleaned, "$1-$2")
            .into_owned();
        let range_match = RANGE
            .get_or_init(|| re(r"(?i)^([-+]?\d+(?:\.\d+)?)\s*(?:to|-|\.\.)\s*([-+]?\d+(?:\.\d+)?)\s*([a-zµ°]*)$"))
            .captures(&cleaned);
        if range_match.is_none() && attribute == "temp_range" {
            // Multi-grade cells: the document states a set of grades.
            // min/max carry the first printed grade; every grade is kept.
            let ambient = re(r"(?i)\bjunction\b")
                .split(&cleaned)
                .next()
                .unwrap_or("")
                .to_string();
            let grades: Vec<(f64, f64)> = RANGE_ANY
                .get_or_init(|| re(r"(?i)([-+]?\s?\d+(?:\.\d+)?)\s*(?:V|°\s*C)?\s*(?:to|-)\s*([-+]?\s?\d+(?:\.\d+)?)"))
                .captures_iter(&ambient)
                .filter_map(|c| {
                    let a = c.get(1)?.as_str().replace([' ', '+'], "").parse::<f64>().ok()?;
                    let b = c.get(2)?.as_str().replace([' ', '+'], "").parse::<f64>().ok()?;
                    Some((a, b))
                })
                .collect();
            let junction = re(r"(?i)^\s*junction").is_match(&cleaned);
            if !grades.is_empty() && !junction {
                leaf.insert("status".into(), json!("typed"));
                leaf.insert("min".into(), json!(grades[0].0));
                leaf.insert("max".into(), json!(grades[0].1));
                leaf.insert("unit".into(), json!(unit));
                leaf.insert(
                    "grades".into(),
                    json!(grades
                        .iter()
                        .map(|(a, b)| json!([*a, *b]))
                        .collect::<Vec<_>>()),
                );
                leaf.insert(
                    "note".into(),
                    json!(if grades.len() > 1 {
                        "first of several printed grades"
                    } else {
                        "range within longer cell"
                    }),
                );
                return leaf;
            }
        }
        if range_match.is_none() && attribute == "operating_voltage" {
            if let Some(c) = RANGE_ANY
                .get_or_init(|| re(r"(?i)([-+]?\s?\d+(?:\.\d+)?)\s*(?:V|°\s*C)?\s*(?:to|-)\s*([-+]?\s?\d+(?:\.\d+)?)"))
                .captures(&cleaned)
            {
                let lo = c
                    .get(1)
                    .and_then(|g| g.as_str().replace('+', "").parse::<f64>().ok());
                let hi = c
                    .get(2)
                    .and_then(|g| g.as_str().replace('+', "").parse::<f64>().ok());
                if let (Some(lo), Some(hi)) = (lo, hi) {
                    let end = c.get(0).map_or(0, |m| m.end());
                    let note = cleaned[end..].trim().trim_matches(['(', ')', ' ']);
                    leaf.insert("status".into(), json!("typed"));
                    leaf.insert("min".into(), json!(lo));
                    leaf.insert("max".into(), json!(hi));
                    leaf.insert("unit".into(), json!(unit));
                    leaf.insert(
                        "note".into(),
                        json!(if note.is_empty() { "range within longer cell" } else { note }),
                    );
                    return leaf;
                }
            }
        }
        if let Some(c) = &range_match {
            let lo = c
                .get(1)
                .and_then(|g| g.as_str().replace('+', "").parse::<f64>().ok());
            let hi = c
                .get(2)
                .and_then(|g| g.as_str().replace('+', "").parse::<f64>().ok());
            if let (Some(lo), Some(hi)) = (lo, hi) {
                leaf.insert("status".into(), json!("typed"));
                leaf.insert("min".into(), json!(lo));
                leaf.insert("max".into(), json!(hi));
                leaf.insert("unit".into(), json!(unit));
                return leaf;
            }
        }
        leaf.insert("status".into(), json!("unknown"));
        leaf.insert("reason".into(), json!("not_a_range"));
        return leaf;
    }

    // Presence flags in a count row ("RTC: Yes", "SDIO: Yes") are kept as
    // booleans; ST's "-" is its explicit none.
    if NONE
        .get_or_init(|| re(r"(?i)^(-{1,2}|—|–|none|0)$"))
        .is_match(&text)
    {
        leaf.insert("status".into(), json!("typed"));
        leaf.insert("typ".into(), json!(0));
        leaf.insert("unit".into(), json!(unit));
        leaf.insert("note".into(), json!("explicit none"));
        return leaf;
    }
    if YES.get_or_init(|| re(r"(?i)^(yes|y|✓|x)$")).is_match(&text) {
        leaf.insert("status".into(), json!("boolean"));
        leaf.insert("value".into(), json!(true));
        return leaf;
    }
    if NO
        .get_or_init(|| re(r"(?i)^(no|n|n/a|na)$"))
        .is_match(&text)
    {
        leaf.insert("status".into(), json!("boolean"));
        leaf.insert("value".into(), json!(false));
        return leaf;
    }
    if let Some(c) = TIMES
        .get_or_init(|| re(r"^(\d{1,3})\s*[×x]\s*([A-Za-z][\w.-]*)\s*(\([^)]*\))?$"))
        .captures(&text)
    {
        if unit == "count" {
            // "2×FD (0-1)": two instances of the named kind.
            let n = c
                .get(1)
                .and_then(|g| g.as_str().parse::<i64>().ok())
                .unwrap_or(0);
            let mut note = c.get(2).map(|m| m.as_str().to_string()).unwrap_or_default();
            let tail = c.get(3).map(|m| m.as_str().to_string()).unwrap_or_default();
            note = format!("{note} {tail}").trim().to_string();
            leaf.insert("status".into(), json!("typed"));
            leaf.insert("typ".into(), json!(n));
            leaf.insert("unit".into(), json!(unit));
            leaf.insert("note".into(), json!(note));
            return leaf;
        }
    }
    if let Some(c) = DC_TO
        .get_or_init(|| re(r"(?i)^DC\s*[-–—to]+\s*(\d+(?:\.\d+)?)\s*MHz$"))
        .captures(&text)
    {
        if attribute == "freq_mhz" {
            // "DC – 40 MHz": the maximum is the printed ceiling.
            let raw_n = c.get(1).map_or("0", |m| m.as_str());
            let value = if raw_n.contains('.') {
                json!(raw_n.parse::<f64>().unwrap_or(0.0))
            } else {
                json!(raw_n.parse::<i64>().unwrap_or(0))
            };
            leaf.insert("status".into(), json!("typed"));
            leaf.insert("typ".into(), value);
            leaf.insert("unit".into(), json!(unit));
            leaf.insert("note".into(), json!("DC to maximum"));
            return leaf;
        }
    }
    let bitwidth_re =
        BITWIDTH.get_or_init(|| re(r"(?i)(\d{1,3})\s*\(\s*\d{1,2}\s*-?\s*bits?\s*\)"));
    if MULTI.get_or_init(|| re(r"(?i)\bor\b")).is_match(&text) && bitwidth_re.find(&text).is_none()
    {
        leaf.insert("status".into(), json!("unknown"));
        leaf.insert("reason".into(), json!("alternative_values"));
        return leaf;
    }

    let mut number: Option<f64> = None;
    if NUMBER
        .get_or_init(|| re(r"^\d{1,6}(?:\.\d+)?$"))
        .is_match(&text)
    {
        number = text.parse::<f64>().ok();
    } else if unit == "count"
        && bitwidth_re.find_iter(&text).count() >= 1
        && bitwidth_only(&text)
    {
        let sum: f64 = bitwidth_re
            .captures_iter(&text)
            .filter_map(|c| c.get(1)?.as_str().parse::<f64>().ok())
            .sum();
        number = Some(sum);
        leaf.insert(
            "components".into(),
            json!(bitwidth_re
                .find_iter(&text)
                .map(|m| m.as_str().to_string())
                .collect::<Vec<_>>()),
        );
        leaf.insert("note".into(), json!("sum of bit-width groups"));
    } else if let Some(c) = BREAKDOWN
        .get_or_init(|| re(r"^(\d{1,6})\s*\((?:\d+\s*[+x×]\s*)+\d+\)$"))
        .captures(&text)
        {
            let head = c.get(1).map_or("0", |m| m.as_str());
            number = head.parse::<f64>().ok();
            leaf.insert("breakdown".into(), json!(text[head.len()..].trim()));
        } else if let Some(c) = WITH_UNIT
            .get_or_init(|| {
                re(r"(?i)^(?P<n>\d{1,6}(?:\.\d+)?)\s*(?P<u>k\s*bytes?|kbytes?|kb|k|m\s*bytes?|mbytes?|mb|m|mhz|khz|[µu]a|ma|v|°c)$")
            })
            .captures(&text)
        {
            number = c.name("n").and_then(|g| g.as_str().parse::<f64>().ok());
            if let Some(u) = c.name("u") {
                let key: String = u
                    .as_str()
                    .to_lowercase()
                    .split_whitespace()
                    .collect::<Vec<_>>()
                    .join(" ");
                if let Some(w) = unit_words().get(key.as_str()) {
                    unit = (*w).to_string();
                }
            }
        } else if let Some(c) = WITH_NOTE
            .get_or_init(|| re(r"(?i)^(\d{1,6})\s*(\([^()]*\)|[a-z][a-z /.-]*)$"))
            .captures(&text)
        {
            let head = c.get(1).map_or("0", |m| m.as_str());
            number = head.parse::<f64>().ok();
            leaf.insert("note".into(), json!(text[head.len()..].trim()));
        }
    let Some(mut number) = number else {
        leaf.insert("status".into(), json!("unknown"));
        leaf.insert("reason".into(), json!("unparsed_numeric"));
        return leaf;
    };

    if attribute.ends_with("_kb") && unit == "MB" {
        if (number * 1024.0).fract() != 0.0 {
            // "4.2 Mbytes": a decimal marketing figure; not a memory size.
            leaf.insert("status".into(), json!("unknown"));
            leaf.insert("reason".into(), json!("fractional_megabytes"));
            return leaf;
        }
        number *= 1024.0;
        unit = "KB".to_string();
    }
    let value = if number.fract() == 0.0 {
        #[allow(
            clippy::cast_possible_truncation,
            reason = "the reference emits int(value) for integral floats"
        )]
        let n = number as i64;
        Value::from(n)
    } else {
        Value::from(number)
    };
    leaf.insert("status".into(), json!("typed"));
    leaf.insert("typ".into(), value);
    leaf.insert("unit".into(), json!(unit));
    leaf
}

/// True when the cell is only bit-width groups joined by and/+/,/space,
/// optionally followed by a short qualifier.
fn bitwidth_only(text: &str) -> bool {
    static GROUP: OnceLock<Regex> = OnceLock::new();
    let rest = GROUP
        .get_or_init(|| re(r"(?i)(\d{1,3})\s*\(\s*\d{1,2}\s*-?\s*bits?\s*\)"))
        .replace_all(text, "");
    let rest = re(r"(?i)\band\b|\+|,|/")
        .replace_all(&rest, " ")
        .into_owned();
    let rest = rest.trim().to_string();
    rest.split_whitespace().count() <= 3 && !re(r"\d").is_match(&rest)
}

// ---------------------------------------------------------------------------
// Header binding
// ---------------------------------------------------------------------------

/// `('STM32\nF446MC')` -> `"STM32F446MC"`; `('AVR64DB64\nAVR128DB64')` ->
/// `"AVR64DB64,AVR128DB64"` (whole parts sharing a prefix are a list).
#[must_use]
pub fn header_token(cell: &str) -> String {
    let pieces: Vec<&str> = cell.split_whitespace().collect();
    if pieces.len() > 1
        && pieces
            .iter()
            .all(|p| is_concrete_part_token(p.trim_end_matches(',')))
    {
        let prefixes: BTreeSet<String> = pieces
            .iter()
            .filter_map(|p| re(r"^[A-Za-z]+").find(p))
            .map(|m| m.as_str().to_uppercase())
            .collect();
        if prefixes.len() == 1 {
            return pieces
                .iter()
                .map(|p| p.trim_end_matches(','))
                .collect::<Vec<_>>()
                .join(",");
        }
    }
    re(r"\s+").replace_all(cell, "").into_owned()
}

/// `('STM32G050 _ F6 K6 K8 C6 C8')` -> `("STM32G050", ["F6", "K6", ...])`.
/// Also codes separated by / or _ and, when `expected` is given, a glued run
/// that chunks into exactly `expected` two-character codes.
#[allow(
    clippy::indexing_slicing,
    clippy::panic,
    reason = "grid walks index rows/columns as the reference does; guards upstream"
)]
fn split_stem_codes(cell: &str, expected: Option<usize>) -> Option<(String, Vec<String>)> {
    static CODE: OnceLock<Regex> = OnceLock::new();
    let text = re(r"[_/]+")
        .replace_all(&norm(Some(cell)), " ")
        .into_owned();
    let text = text.trim().to_string();
    let parts: Vec<&str> = text.split_whitespace().collect();
    if parts.len() < 2 {
        return None;
    }
    let stem = parts[0].to_string();
    if !(crate::family_census::part_token_is_fullmatch(&stem) || is_wildcard_token(&stem)) {
        return None;
    }
    let rest = &parts[1..];
    let code_re = CODE.get_or_init(|| re(r"^[A-Z][0-9A-Z](?:xxN|xxP|xxQ)?$"));
    let codes: Vec<String> = rest
        .iter()
        .filter(|p| code_re.is_match(p))
        .map(std::string::ToString::to_string)
        .collect();
    if codes.len() >= 2 && codes.len() == rest.len() {
        return Some((stem, codes));
    }
    if let Some(expected) = expected {
        if !rest.is_empty() {
            // Glued codes: split each blob into 2-char codes (optional xxN).
            let mut glued: Vec<String> = Vec::new();
            for blob in rest {
                let mut pos = 0;
                while pos < blob.len() {
                    let slice = &blob[pos..];
                    let m = re(r"^[A-Z][0-9A-Z](?:xxN|xxP|xxQ)?").find(slice)?;
                    if m.end() == 0 {
                        return None;
                    }
                    glued.push(m.as_str().to_string());
                    pos += m.end();
                }
            }
            if glued.len() == expected {
                return Some((stem, glued));
            }
        }
    }
    None
}

/// `('GD32F405xx')` -> `"GD32F405"`; composite `('STM32C011x4/x6')` -> `"STM32C011"`.
fn wildcard_base(token: &str) -> String {
    let token = token.split('/').next().unwrap_or(token);
    if token.contains('x') {
        re(r"x[A-Za-z0-9x]*$").replace(token, "").into_owned()
    } else {
        token.to_string()
    }
}

/// Members of a header wildcard like `STM32F101Tx` from a printed device
/// summary. Returns parts matching both.
fn join_wildcard(token: &str, device_summary: &BTreeMap<String, Vec<String>>) -> Vec<String> {
    let pattern = re(&format!(
        r"^{}[0-9A-Z]$",
        regex::escape(&wildcard_base(token))
    ));
    let mut members: Vec<String> = Vec::new();
    for parts in device_summary.values() {
        for part in parts {
            if pattern.is_match(part) && !members.contains(part) {
                members.push(part.clone());
            }
        }
    }
    members.sort();
    members
}

fn members_from_parts_named(token: &str, parts_named: &[String]) -> Vec<String> {
    // Concrete parts the document names elsewhere that instantiate a header
    // wildcard, ordered by the ST memory letter when every member has one.
    let pattern = re(&format!(
        r"^{}[0-9A-Z]$",
        regex::escape(&wildcard_base(token))
    ));
    let mut members: Vec<String> = parts_named
        .iter()
        .filter(|p| pattern.is_match(p))
        .cloned()
        .collect();
    members.sort();
    let sizes: Vec<Option<i64>> = members.iter().map(|p| st_flash_code_kb(Some(p))).collect();
    if !members.is_empty() && sizes.iter().all(Option::is_some) {
        let mut paired: Vec<(i64, String)> = members
            .iter()
            .zip(sizes.iter())
            .map(|(m, s)| (s.unwrap_or(0), m.clone()))
            .collect();
        paired.sort();
        members = paired.into_iter().map(|(_, m)| m).collect();
    }
    members
}

/// A data column bound to a part (or not) from printed header text only.
pub type Binding = Map<String, Value>;

fn mk_binding(col: usize, part: Option<String>, token: Option<String>, kind: &str) -> Binding {
    let mut b = Map::new();
    b.insert("column_index".into(), json!(col));
    b.insert("part_number".into(), part.map_or(Value::Null, Value::from));
    b.insert(
        "family_token".into(),
        token.map_or(Value::Null, Value::from),
    );
    b.insert("binding".into(), json!(kind));
    b
}

/// Bind each data column to a part number using only printed header text.
///
/// Returns `(header_rows, bindings)`. Each binding has `part_number` (or
/// null), `family_token`, `binding` in
/// {`header_token`, `header_codes`, `header_wildcard_codes`, `device_summary_join`,
/// `parts_named_join`, `header_token_package`, `header_token_list`, unbound} and
/// `column_index`.
#[must_use]
#[allow(
    clippy::too_many_lines,
    reason = "faithful sequential port of bind_columns"
)]
#[allow(
    clippy::indexing_slicing,
    clippy::panic,
    reason = "grid walks index rows/columns as the reference does; guards upstream"
)]
pub fn bind_columns(
    grid: &[Vec<String>],
    label_cols: usize,
    device_summary: &BTreeMap<String, Vec<String>>,
    parts_named: Option<&[String]>,
) -> (usize, Vec<Binding>) {
    static PKG_SUFFIX: OnceLock<Regex> = OnceLock::new();
    static LIST_SPLIT: OnceLock<Regex> = OnceLock::new();
    let width = grid.first().map_or(0, Vec::len);
    let data_cols: Vec<usize> = (label_cols..width).collect();
    let empty_row = Vec::new();
    let row0 = grid.first().unwrap_or(&empty_row).clone();
    let row1: Vec<String> = grid
        .get(1)
        .cloned()
        .unwrap_or_else(|| vec![String::new(); width]);

    // Case B: one header cell listing stem + codes.
    for cell in row0.iter().skip(label_cols) {
        if let Some((stem, codes)) = split_stem_codes(cell, Some(data_cols.len())) {
            if codes.len() == data_cols.len() {
                let base = wildcard_base(&stem);
                let out: Vec<Binding> = data_cols
                    .iter()
                    .zip(codes.iter())
                    .map(|(col, code)| {
                        if code.chars().count() > 2 {
                            // 'F8xxN' is an ordering-suffix variant, not a
                            // part number the document spells out.
                            let mut b =
                                mk_binding(*col, None, Some(format!("{base}{code}")), "unbound");
                            b.insert("reason".into(), json!("ordering_suffix_variant"));
                            b
                        } else {
                            mk_binding(
                                *col,
                                Some(format!("{base}{code}")),
                                Some(stem.clone()),
                                "header_codes",
                            )
                        }
                    })
                    .collect();
                return (1, out);
            }
            return (
                1,
                data_cols
                    .iter()
                    .map(|col| {
                        let mut b = mk_binding(*col, None, Some(stem.clone()), "unbound");
                        b.insert("reason".into(), json!("code_count_mismatch"));
                        b
                    })
                    .collect(),
            );
        }
    }

    let mut filled0: Vec<String> = Vec::new();
    let mut carry = String::new();
    for (col, cell) in row0.iter().enumerate() {
        let cell = norm(Some(cell));
        if !cell.is_empty() {
            carry.clone_from(&cell);
        }
        filled0.push(if col >= label_cols {
            carry.clone()
        } else {
            cell
        });
    }

    let tokens: Vec<String> = data_cols
        .iter()
        .map(|c| header_token(&filled0[*c]))
        .collect();
    let codes1: Vec<String> = data_cols.iter().map(|c| norm(Some(&row1[*c]))).collect();
    let codes1_ok = codes1
        .iter()
        .all(|c| re(r"^[A-Z][0-9A-Z]{1,3}$").is_match(c));
    let second_header_is_labels = {
        let sub = if label_cols > 1 {
            norm(Some(&row1[1]))
        } else {
            String::new()
        };
        !row_attributes(&norm(Some(&row1[0])), &sub).is_empty()
    };

    let mut bindings: Vec<Binding> = Vec::new();
    if codes1_ok && !second_header_is_labels && {
        let mut uniq: BTreeSet<&String> = BTreeSet::new();
        for c in &codes1 {
            uniq.insert(c);
        }
        uniq.len() >= 2
    } {
        // Case C: wildcard/stem over a code row (GD32F405xx / RE RG RK ...).
        for ((col, token), code) in data_cols.iter().zip(tokens.iter()).zip(codes1.iter()) {
            let (col, token, code) = (*col, token.clone(), code.clone());
            let mut base = if token.is_empty() {
                String::new()
            } else {
                wildcard_base(&token)
            };
            if !base.is_empty() && re(r"^[A-Za-z]{1,8}\d").find(&base).is_none() {
                // "Part Number" over a code row is a heading, not a stem.
                base = String::new();
            }
            if base.is_empty() {
                let mut b = mk_binding(
                    col,
                    None,
                    if token.is_empty() { None } else { Some(token) },
                    "unbound",
                );
                b.insert("reason".into(), json!("no_header_token"));
                bindings.push(b);
            } else {
                bindings.push(mk_binding(
                    col,
                    Some(format!("{base}{code}")),
                    Some(token.clone()),
                    "header_wildcard_codes",
                ));
            }
        }
        return (2, bindings);
    }

    // Case A: concrete token per column; a token spanning several columns is
    // ambiguous unless a printed device summary resolves the members.
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    for token in &tokens {
        *counts.entry(token.clone()).or_insert(0) += 1;
    }
    for (col, token) in data_cols.iter().zip(tokens.iter()) {
        let col = *col;
        let token_str = token.clone();
        let count = counts.get(&token_str).copied().unwrap_or(0);
        if !token_str.is_empty() && is_concrete_part_token(&token_str) && count == 1 {
            bindings.push(mk_binding(
                col,
                Some(token_str.clone()),
                Some(token_str.clone()),
                "header_token",
            ));
            continue;
        }
        if let Some(c) = PKG_SUFFIX
            .get_or_init(|| re(r"^([A-Z]{2,}[0-9][A-Z0-9]{3,}?)([A-Z])[xX]{2,4}$"))
            .captures(&token_str)
        {
            let head = c.get(1).map(|m| m.as_str().to_string()).unwrap_or_default();
            let letter = c.get(2).map(|m| m.as_str().to_string()).unwrap_or_default();
            if count == 1 && is_concrete_part_token(&head) {
                // "STM32U3C5VIYxxxx": the concrete part followed by its
                // package letter and the ordering wildcards.
                let mut b = mk_binding(
                    col,
                    Some(head),
                    Some(token_str.clone()),
                    "header_token_package",
                );
                b.insert("package_letter".into(), json!(letter));
                bindings.push(b);
                continue;
            }
        }
        if !token_str.is_empty()
            && count == 1
            && LIST_SPLIT
                .get_or_init(|| re(r"\s*[,/]\s*"))
                .is_match(&token_str)
        {
            // Several parts named for one column: the column binds to every
            // concrete part the header prints.
            let pieces: Vec<String> = LIST_SPLIT
                .get_or_init(|| re(r"\s*[,/]\s*"))
                .split(&token_str)
                .filter(|p| !p.is_empty())
                .map(std::string::ToString::to_string)
                .collect();
            let concrete: Vec<String> = pieces
                .iter()
                .filter(|p| is_concrete_part_token(p) && !p.contains("xx"))
                .cloned()
                .collect();
            if !concrete.is_empty() {
                let also: Vec<String> = pieces
                    .iter()
                    .filter(|p| !concrete.contains(p))
                    .cloned()
                    .collect();
                let mut b = mk_binding(
                    col,
                    Some(concrete[0].clone()),
                    Some(token_str.clone()),
                    "header_token_list",
                );
                b.insert("part_numbers".into(), json!(concrete));
                b.insert("also_covers".into(), json!(also));
                bindings.push(b);
                continue;
            }
        }
        if !token_str.is_empty() && is_wildcard_token(&token_str) && !device_summary.is_empty() {
            let members = join_wildcard(&token_str, device_summary);
            if members.len() == count {
                let offset = data_cols
                    .iter()
                    .zip(tokens.iter())
                    .filter(|(_, t)| **t == token_str)
                    .position(|(c, _)| *c == col)
                    .unwrap_or(0);
                let mut b = mk_binding(
                    col,
                    members.get(offset).cloned(),
                    Some(token_str.clone()),
                    "device_summary_join",
                );
                b.insert("join_members".into(), json!(members));
                bindings.push(b);
                continue;
            }
        }
        if !token_str.is_empty() && is_wildcard_token(&token_str) {
            if let Some(parts) = parts_named {
                let members = members_from_parts_named(&token_str, parts);
                let span = count;
                if members.len() == span && span > 1 {
                    let offset = data_cols
                        .iter()
                        .zip(tokens.iter())
                        .filter(|(_, t)| **t == token_str)
                        .position(|(c, _)| *c == col)
                        .unwrap_or(0);
                    let mut b = mk_binding(
                        col,
                        members.get(offset).cloned(),
                        Some(token_str.clone()),
                        "parts_named_join",
                    );
                    b.insert("join_members".into(), json!(members));
                    bindings.push(b);
                    continue;
                }
                if span == 1 && members.len() > 1 {
                    let mut b = mk_binding(
                        col,
                        Some(members[0].clone()),
                        Some(token_str.clone()),
                        "parts_named_join",
                    );
                    b.insert("part_numbers".into(), json!(members));
                    bindings.push(b);
                    continue;
                }
            }
        }
        let mut b = mk_binding(
            col,
            None,
            if token_str.is_empty() {
                None
            } else {
                Some(token_str.clone())
            },
            "unbound",
        );
        b.insert(
            "reason".into(),
            json!(if token_str.is_empty() {
                "empty_header"
            } else {
                "spanning_or_wildcard_header"
            }),
        );
        bindings.push(b);
    }
    (1, bindings)
}

// ---------------------------------------------------------------------------
// Device summary + ordering-code self-check
// ---------------------------------------------------------------------------

/// ST ordering-code memory letter -> code flash KB (public ST grammar, used
/// only as a self-consistency check, never as a value source).
fn st_memory_letter_kb() -> &'static BTreeMap<char, i64> {
    static M: OnceLock<BTreeMap<char, i64>> = OnceLock::new();
    M.get_or_init(|| {
        let mut m = BTreeMap::new();
        for (k, v) in [
            ('4', 16),
            ('6', 32),
            ('8', 64),
            ('B', 128),
            ('C', 256),
            ('D', 384),
            ('E', 512),
            ('F', 768),
            ('G', 1024),
            ('H', 1536),
            ('I', 2048),
            ('J', 4096),
            ('K', 3072),
        ] {
            m.insert(k, v);
        }
        m
    })
}

/// ST part shape: `STM32` + product line + pin letter + memory letter.
fn st_part() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        re(r"^STM32(?:[A-Z]\d[A-Z0-9]\d|[A-Z]{2}\d{2}|[A-Z]{3}\d{1,2}|[A-Z]{3}\d)([A-Z])([0-9A-Z])(?:[A-Z0-9]*)$")
    })
}

/// ST memory letter -> code flash KB for one part number, `None` when the
/// line is not checked (wireless/MPU) or the shape is not ST.
#[must_use]
pub fn st_flash_code_kb(part_number: Option<&str>) -> Option<i64> {
    let part_number = part_number?;
    if part_number.is_empty()
        || part_number.starts_with("STM32WB")
        || part_number.starts_with("STM32MP")
        || part_number.starts_with("STM32N")
    {
        return None;
    }
    let c = st_part().captures(part_number)?;
    let letter = c.get(2)?.as_str().chars().next()?;
    st_memory_letter_kb().get(&letter).copied()
}

/// `Reference -> Part number list` from the ST device-summary table.
#[must_use]
#[allow(
    clippy::indexing_slicing,
    clippy::panic,
    reason = "grid walks index rows/columns as the reference does; guards upstream"
)]
pub fn read_device_summary(
    tables: &[(usize, Vec<Vec<Option<String>>>)],
) -> BTreeMap<String, Vec<String>> {
    let mut summary: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for (_page, rows) in tables {
        let Some(first) = rows.first() else {
            continue;
        };
        if first.len() < 2 {
            continue;
        }
        let header = first
            .iter()
            .map(|c| norm(c.as_deref()))
            .collect::<Vec<_>>()
            .join(" ")
            .to_lowercase();
        if !header.contains("reference") || !header.contains("part") {
            continue;
        }
        for row in rows.iter().skip(1) {
            let reference = norm(row.first().and_then(|c| c.as_deref()));
            if reference.is_empty() {
                continue;
            }
            let rest = row[1..]
                .iter()
                .map(|c| norm(c.as_deref()))
                .collect::<Vec<_>>()
                .join(" ");
            let parts: Vec<String> = crate::family_census::part_tokens(&rest)
                .into_iter()
                .filter(|t| is_concrete_part_token(t))
                .collect();
            if !parts.is_empty() {
                let bucket = summary.entry(reference).or_default();
                for part in parts {
                    if !bucket.contains(&part) {
                        bucket.push(part);
                    }
                }
            }
        }
    }
    summary
}

// ---------------------------------------------------------------------------
// Geometry binding (rotated code grids)
// ---------------------------------------------------------------------------

fn geo_code() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"^_?([A-Z][0-9A-Z])/?$"))
}

fn geo_suffix() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| re(r"^(?:xx[A-Z]|_?[A-Z][0-9A-Z]xx[A-Z])/?$"))
}

/// One word of page text with its box (the PDF layer's `words` output).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Word {
    /// Left edge.
    pub x0: f64,
    /// Top edge.
    pub y0: f64,
    /// Right edge.
    pub x1: f64,
    /// Bottom edge.
    pub y1: f64,
    /// The word as printed.
    pub text: String,
}

/// x-ranges of the extracted grid's columns from the table's cell boxes.
#[allow(
    clippy::indexing_slicing,
    clippy::panic,
    reason = "grid walks index rows/columns as the reference does; guards upstream"
)]
fn column_ranges(cells: &[[f64; 4]], width: usize) -> Option<Vec<(f64, f64)>> {
    let mut xs: BTreeSet<i64> = BTreeSet::new();
    let mut right: f64 = 0.0;
    for cell in cells {
        #[allow(
            clippy::cast_possible_truncation,
            reason = "quantised tenths of a point; magnitudes are page bounds"
        )]
        let q = (cell[0] * 10.0).round() as i64;
        xs.insert(q);
        right = right.max(cell[2]);
    }
    let bounds: Vec<f64> = xs
        .iter()
        .map(|x| {
            #[allow(
                clippy::cast_precision_loss,
                reason = "inverse of the tenths quantisation above"
            )]
            let v = *x as f64;
            v / 10.0
        })
        .collect();
    if bounds.len() != width {
        return None;
    }
    Some(
        (0..width)
            .map(|i| (bounds[i], if i + 1 < width { bounds[i + 1] } else { right }))
            .collect(),
    )
}

/// Bind columns from the header's word positions when the header text is a
/// rotated code grid. Only printed pieces are used: a code lands in the
/// column whose x-range contains the word's centre; a suffix piece attaches
/// to the code directly above it in the same column.
#[must_use]
#[allow(clippy::too_many_lines, reason = "faithful port of geometry_bindings")]
#[allow(
    clippy::indexing_slicing,
    clippy::panic,
    reason = "grid walks index rows/columns as the reference does; guards upstream"
)]
pub fn geometry_bindings(
    words: &[Word],
    cells: &[[f64; 4]],
    header_cells: &[[f64; 4]],
    grid_width: usize,
    label_cols: usize,
    stem_hint: Option<&str>,
) -> Option<Vec<Binding>> {
    let width = grid_width;
    let ranges = column_ranges(cells, width)?;
    if header_cells.is_empty() {
        return None;
    }
    let x0 = header_cells
        .iter()
        .map(|c| c[0])
        .fold(f64::INFINITY, f64::min);
    let y0 = header_cells
        .iter()
        .map(|c| c[1])
        .fold(f64::INFINITY, f64::min);
    let x1 = header_cells
        .iter()
        .map(|c| c[2])
        .fold(f64::NEG_INFINITY, f64::max);
    let y1 = header_cells
        .iter()
        .map(|c| c[3])
        .fold(f64::NEG_INFINITY, f64::max);
    let words: Vec<&Word> = words
        .iter()
        .filter(|w| w.x0 >= x0 - 1.0 && w.x1 <= x1 + 1.0 && w.y0 >= y0 - 1.0 && w.y1 <= y1 + 1.0)
        .collect();
    let mut stem: Option<String> = None;
    for w in &words {
        let token = w.text.trim_matches('_').to_string();
        if token.chars().any(|c| c.is_ascii_digit())
            && (crate::family_census::part_token_is_fullmatch(&token) || is_wildcard_token(&token))
        {
            stem = Some(token);
            break;
        }
    }
    if stem.is_none() {
        stem = stem_hint.map(str::to_string);
    }
    let stem = stem?;
    let base = wildcard_base(&stem);

    let column_of = |word: &Word| -> Option<usize> {
        let centre = f64::midpoint(word.x0, word.x1);
        ranges
            .iter()
            .position(|(left, right)| *left <= centre && centre < *right)
    };

    let mut per_column: BTreeMap<usize, Vec<(f64, &'static str, String, bool)>> = BTreeMap::new();
    for w in &words {
        let text = w.text.clone();
        let Some(col) = column_of(w) else {
            continue;
        };
        if col < label_cols {
            continue;
        }
        let rotated = (w.y1 - w.y0) > (w.x1 - w.x0);
        if let Some(c) = geo_code().captures(&text) {
            per_column.entry(col).or_default().push((
                w.y0,
                "code",
                c.get(1).map(|m| m.as_str().to_string()).unwrap_or_default(),
                rotated,
            ));
        } else if geo_suffix().is_match(&text) {
            per_column.entry(col).or_default().push((
                w.y0,
                "suffix",
                text.trim_matches(|c| c == '_' || c == '/').to_string(),
                rotated,
            ));
        }
    }
    if per_column.is_empty() {
        return None;
    }

    let mut bindings: Vec<Binding> = Vec::new();
    for col in label_cols..width {
        let raw = per_column.get(&col).cloned().unwrap_or_default();
        // Horizontal words stack top-to-bottom; rotated (vertical) words read
        // bottom-to-top. Either way the printed order is the order of the
        // slash-separated values in the column's cells.
        let rotated_column = raw.iter().filter(|p| p.3).count() * 2 > raw.len();
        let mut sorted = raw.clone();
        sorted.sort_by(|a, b| {
            if rotated_column {
                b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal)
            } else {
                a.0.partial_cmp(&b.0).unwrap_or(std::cmp::Ordering::Equal)
            }
        });
        let mut members: Vec<Value> = Vec::new();
        for (_, kind, text, _) in &sorted {
            if *kind == "code" {
                let part = format!("{base}{text}");
                members.push(json!({"part_number": part, "token": part}));
            } else if text.chars().count() > 3 {
                // full 'F8xxN' word
                members.push(json!({"part_number": Value::Null, "token": format!("{base}{text}")}));
            } else if let Some(last) = members.last_mut() {
                if last.get("part_number").and_then(Value::as_str).is_some() {
                    // 'xxN' stacked under a code: ordering variant.
                    let token = format!(
                        "{}{}",
                        last.get("token")
                            .and_then(Value::as_str)
                            .unwrap_or_default(),
                        text
                    );
                    *last = json!({"part_number": Value::Null, "token": token});
                }
            }
        }
        if members.is_empty() {
            let mut b = mk_binding(col, None, Some(stem.clone()), "unbound");
            b.insert("reason".into(), json!("no_code_in_column"));
            bindings.push(b);
            continue;
        }
        let concrete: Vec<String> = members
            .iter()
            .filter_map(|m| {
                m.get("part_number")
                    .and_then(Value::as_str)
                    .map(str::to_string)
            })
            .collect();
        let mut b = mk_binding(
            col,
            concrete.first().cloned(),
            Some(stem.clone()),
            if concrete.is_empty() {
                "unbound"
            } else {
                "header_geometry"
            },
        );
        b.insert("members".into(), json!(members));
        if members.len() > 1 {
            b.insert("part_numbers".into(), json!(concrete));
        }
        if concrete.is_empty() {
            b.insert("reason".into(), json!("ordering_suffix_variant"));
            let token = members[0]
                .get("token")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            b.insert("family_token".into(), json!(token));
        }
        bindings.push(b);
    }
    if !bindings
        .iter()
        .any(|b| b.get("part_number").and_then(Value::as_str).is_some())
    {
        return None;
    }
    Some(bindings)
}

// ---------------------------------------------------------------------------
// Matrix reading
// ---------------------------------------------------------------------------

/// `('2 2', false), ('', true)` -> `[('2', false), ('2', false)]`: a cell
/// `PyMuPDF` glued across N columns is N per-column values when the token count
/// equals the run length. Anything else is left as printed.
#[must_use]
#[allow(
    clippy::indexing_slicing,
    clippy::panic,
    reason = "grid walks index rows/columns as the reference does; guards upstream"
)]
pub fn split_glued_runs(values: &[(String, bool)]) -> Vec<(String, bool)> {
    static INT_TOKENS: OnceLock<Regex> = OnceLock::new();
    let mut out: Vec<(String, bool)> = values.to_vec();
    let mut index = 0;
    while index < out.len() {
        if out[index].1 {
            index += 1;
            continue;
        }
        let mut run_end = index + 1;
        while run_end < out.len() && out[run_end].1 {
            run_end += 1;
        }
        let run = run_end - index;
        if run > 1 {
            let stripped = strip_footnotes(&out[index].0);
            if INT_TOKENS
                .get_or_init(|| re(r"^\d{1,4}(?:\s*[/ ]\s*\d{1,4})+$"))
                .is_match(&stripped)
            {
                let tokens: Vec<String> = re(r"\s*[/ ]\s*")
                    .split(&stripped)
                    .map(std::string::ToString::to_string)
                    .collect();
                if tokens.len() == run {
                    for (offset, token) in tokens.into_iter().enumerate() {
                        out[index + offset] = (token, false);
                    }
                }
            }
        }
        index = run_end;
    }
    out
}

fn leaf_receipt(page: usize, label: &str, column: usize, merged: bool) -> Value {
    let mut r = Map::new();
    r.insert("page".into(), json!(page));
    r.insert("row_label".into(), json!(label));
    r.insert("column_index".into(), json!(column));
    if merged {
        r.insert("merged_cell".into(), json!(true));
    }
    Value::Object(r)
}

/// Leaves for one cell: plain rows give one leaf; composite rows split the
/// value into components and give one leaf per component.
#[must_use]
#[allow(clippy::too_many_lines, reason = "faithful port of _cell_leaves")]
#[allow(
    clippy::indexing_slicing,
    clippy::panic,
    reason = "grid walks index rows/columns as the reference does; guards upstream"
)]
pub fn cell_leaves(
    verbatim: &str,
    attributes: &[(String, Option<usize>)],
    label_unit: Option<&str>,
    label_components: Option<usize>,
) -> Vec<(String, Leaf)> {
    static EXTRA: OnceLock<Regex> = OnceLock::new();
    let composite: Vec<(String, usize)> = attributes
        .iter()
        .filter_map(|(a, i)| i.map(|i| (a.clone(), i)))
        .collect();
    if composite.is_empty() {
        return attributes
            .iter()
            .map(|(a, _)| (a.clone(), parse_value(verbatim, a, label_unit)))
            .collect();
    }
    let text = strip_footnotes(&norm(Some(verbatim)));
    let extra = EXTRA
        .get_or_init(|| re(r"(?i)\s*\+\s*\d+\s*extra\b.*$"))
        .find(&text)
        .map(|m| m.as_str().trim().trim_start_matches([' ', '+']).to_string());
    let mut work = text.clone();
    if let Some(m) = EXTRA
        .get_or_init(|| re(r"(?i)\s*\+\s*\d+\s*extra\b.*$"))
        .find(&text)
    {
        work = text[..m.start()].trim().to_string();
    }
    let needed = composite.iter().map(|(_, i)| *i).max().unwrap_or(0) + 1;
    let mut components = split_composite_value(&work, needed);
    if components.is_none() {
        if let Some(lc) = label_components {
            if lc > needed {
                // The label names components that map to no attribute
                // ("I/O Pins / Peripheral Pin Select": 12/Y); split by position.
                components = split_composite_value(&work, lc);
            }
        }
    }
    let mut out: Vec<(String, Leaf)> = Vec::new();
    let mut repeated: BTreeSet<String> = BTreeSet::new();
    {
        let mut seen: BTreeSet<String> = BTreeSet::new();
        for (a, _) in &composite {
            if !seen.insert(a.clone()) {
                repeated.insert(a.clone());
            }
        }
    }
    for attribute in &repeated {
        // "FDCAN/TT-FDCAN: 1/1" names two kinds of one interface class; the
        // class count is the sum of the printed components.
        let indices: Vec<usize> = composite
            .iter()
            .filter(|(a, _)| a == attribute)
            .map(|(_, i)| *i)
            .collect();
        let Some(components) = &components else {
            let mut leaf = leaf_new(&norm(Some(verbatim)));
            leaf.insert("status".into(), json!("unknown"));
            leaf.insert("reason".into(), json!("composite_value_mismatch"));
            out.push((attribute.clone(), leaf));
            continue;
        };
        let leaves: Vec<(String, Leaf)> = indices
            .iter()
            .map(|i| {
                (
                    attribute.clone(),
                    parse_value(
                        components.get(*i).map(String::as_str).unwrap_or_default(),
                        attribute,
                        label_unit,
                    ),
                )
            })
            .collect();
        if numeric_unit(attribute).is_some()
            && leaves
                .iter()
                .all(|(_, l)| leaf_str(l, "status").as_deref() == Some("typed"))
        {
            let sum: f64 = leaves
                .iter()
                .filter_map(|(_, l)| l.get("typ").and_then(Value::as_f64))
                .sum();
            let mut leaf = leaf_new(&norm(Some(verbatim)));
            leaf.insert("status".into(), json!("typed"));
            #[allow(
                clippy::cast_possible_truncation,
                reason = "the reference emits int(value) for integral floats"
            )]
            let n = sum as i64;
            leaf.insert("typ".into(), Value::from(n));
            leaf.insert(
                "unit".into(),
                leaves[0].1.get("unit").cloned().unwrap_or(Value::Null),
            );
            leaf.insert("note".into(), json!("sum of components"));
            leaf.insert(
                "components".into(),
                json!(indices
                    .iter()
                    .map(|i| components[*i].clone())
                    .collect::<Vec<_>>()),
            );
            out.push((attribute.clone(), leaf));
        } else {
            let mut leaf = leaf_new(&norm(Some(verbatim)));
            leaf.insert("status".into(), json!("unknown"));
            leaf.insert("reason".into(), json!("composite_components_untyped"));
            out.push((attribute.clone(), leaf));
        }
    }
    for (attribute, index) in &composite {
        if repeated.contains(attribute) {
            continue;
        }
        let Some(components) = &components else {
            let mut leaf = leaf_new(&norm(Some(verbatim)));
            leaf.insert("status".into(), json!("unknown"));
            leaf.insert("reason".into(), json!("composite_value_mismatch"));
            out.push((attribute.clone(), leaf));
            continue;
        };
        let mut leaf = parse_value(
            components
                .get(*index)
                .map(String::as_str)
                .unwrap_or_default(),
            attribute,
            label_unit,
        );
        leaf.insert("verbatim".into(), json!(norm(Some(verbatim))));
        leaf.insert("component".into(), json!(components[*index].clone()));
        if let Some(extra) = &extra {
            leaf.insert("note".into(), json!(extra));
        }
        out.push((attribute.clone(), leaf));
    }
    out
}

/// A matrix table as read: header bindings, attribute rows with values.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct MatrixTable {
    /// 1-based page the table sits on.
    pub page: usize,
    /// Rows in the grid.
    pub rows: usize,
    /// Columns in the grid.
    pub columns: usize,
    /// Leading label columns.
    pub label_columns: usize,
    /// Leading header rows.
    pub header_rows: usize,
    /// `parts_as_rows` for selection guides; absent for parts-as-columns.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub orientation: Option<String>,
    /// Column-to-part bindings.
    pub bindings: Vec<Binding>,
    /// Attribute rows with per-column values.
    pub attribute_rows: Vec<AttributeRow>,
    /// Census-vocabulary attributes this table addresses.
    pub census_attributes: Vec<String>,
}

/// One attribute row of a matrix table.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct AttributeRow {
    /// Grid row index (or column index when transposed).
    pub row_index: usize,
    /// The printed label.
    pub label: String,
    /// `(attribute, component)` pairs the label addresses.
    pub attributes: Vec<(String, Option<usize>)>,
    /// Unit printed in the label.
    pub label_unit: Option<String>,
    /// Per-column `(value, merged_cell)`.
    pub values: Vec<(String, bool)>,
    /// Components the label names.
    pub label_components: usize,
}

/// Selection-guide / product-list layout: one part per row, attributes
/// across the header. Transposed into the same shape as `parts_as_columns`. A
/// blank cell under a value in the same column is a merged cell (fill-down).
#[allow(
    clippy::too_many_lines,
    reason = "faithful port of _read_parts_as_rows"
)]
#[allow(
    clippy::indexing_slicing,
    clippy::panic,
    reason = "grid walks index rows/columns as the reference does; guards upstream"
)]
fn read_parts_as_rows(rows: &[Vec<Option<String>>], page: usize) -> Option<MatrixTable> {
    let width = rows.iter().map(Vec::len).max()?;
    let grid: Vec<Vec<String>> = rows
        .iter()
        .map(|r| {
            let mut g: Vec<String> = r.iter().map(|c| norm(c.as_deref())).collect();
            g.resize(width, String::new());
            g
        })
        .collect();
    let header = grid.first()?.clone();
    let mut columns: Vec<RowColumn> = Vec::new();
    for (col, label) in header.iter().enumerate().skip(1) {
        let attributes = row_attributes("", label);
        if !attributes.is_empty() {
            columns.push((
                col,
                attributes,
                unit_from_label(label).map(str::to_string),
                label.clone(),
            ));
        }
    }
    if columns.len() < 2 {
        return None;
    }
    let mut bindings: Vec<Binding> = Vec::new();
    let mut carried = String::new();
    let mut body: Vec<Vec<String>> = Vec::new();
    for (row_index, row) in grid.iter().skip(1).enumerate() {
        let row_index = row_index + 1;
        let token = if row[0].is_empty() {
            String::new()
        } else {
            header_token(&row[0])
        };
        if !token.is_empty() {
            carried = token;
        }
        let part = carried.clone();
        if part.is_empty() {
            continue;
        }
        if is_concrete_part_token(&part) {
            bindings.push(mk_binding(
                row_index,
                Some(part.clone()),
                Some(part),
                "row_token",
            ));
        } else if is_wildcard_token(&part) {
            let mut b = mk_binding(row_index, None, Some(part), "unbound");
            b.insert("reason".into(), json!("wildcard_row"));
            bindings.push(b);
        } else {
            continue;
        }
        body.push(row.clone());
    }
    if bindings.len() < 2 {
        return None;
    }
    let mut attribute_rows: Vec<AttributeRow> = Vec::new();
    for (col, attributes, unit, label) in &columns {
        let mut values: Vec<(String, bool)> = Vec::new();
        let mut carry = String::new();
        for row in &body {
            let cell = row[*col].clone();
            let merged = if cell.is_empty() {
                !carry.is_empty()
            } else {
                carry.clone_from(&cell);
                false
            };
            values.push((if cell.is_empty() { carry.clone() } else { cell }, merged));
        }
        if values.iter().any(|(v, _)| !v.is_empty()) {
            attribute_rows.push(AttributeRow {
                row_index: *col,
                label: label.clone(),
                attributes: attributes.clone(),
                label_unit: unit.clone(),
                values,
                label_components: split_composite_label(label).len(),
            });
        }
    }
    if attribute_rows.len() < 2 {
        return None;
    }
    let census_attributes = attributes_in_label(
        &attribute_rows
            .iter()
            .map(|r| r.label.clone())
            .collect::<Vec<_>>()
            .join(" "),
    );
    Some(MatrixTable {
        page,
        rows: rows.len(),
        columns: width,
        label_columns: 1,
        header_rows: 1,
        orientation: Some("parts_as_rows".into()),
        bindings,
        attribute_rows,
        census_attributes,
    })
}

/// Read one extracted grid as a device matrix: `None` when the table is not
/// one (no part-naming column header, or fewer than two attribute rows).
#[must_use]
#[allow(clippy::too_many_lines, reason = "faithful port of read_matrix_table")]
#[allow(
    clippy::indexing_slicing,
    clippy::panic,
    reason = "grid walks index rows/columns as the reference does; guards upstream"
)]
pub fn read_matrix_table(
    rows: &[Vec<Option<String>>],
    page: usize,
    device_summary: &BTreeMap<String, Vec<String>>,
    words: Option<&[Word]>,
    cells: Option<&[[f64; 4]]>,
    header_cells: Option<&[[f64; 4]]>,
    parts_named: Option<&[String]>,
) -> Option<MatrixTable> {
    let rows = strip_title_rows(rows);
    let description = classify_table(&rows)?;
    if description.orientation == crate::family_census::Orientation::PartsAsRows {
        return read_parts_as_rows(&rows, page);
    }
    let width = rows.iter().map(Vec::len).max()?;
    let grid: Vec<Vec<String>> = rows
        .iter()
        .map(|r| {
            let mut g: Vec<String> = r.iter().map(|c| norm(c.as_deref())).collect();
            g.resize(width, String::new());
            g
        })
        .collect();
    let label_cols = 2.min(width - 1);
    let (mut header_rows, mut bindings) =
        bind_columns(&grid, label_cols, device_summary, parts_named);
    if let (Some(words), Some(cells), Some(header_cells)) = (words, cells, header_cells) {
        if !bindings
            .iter()
            .any(|b| b.get("part_number").and_then(Value::as_str).is_some())
        {
            if let Some(geometry) =
                geometry_bindings(words, cells, header_cells, width, label_cols, None)
            {
                bindings = geometry;
                header_rows = 1;
            }
        }
    }
    let names_part = bindings.iter().any(|b| {
        if b.get("part_number").and_then(Value::as_str).is_some() {
            return true;
        }
        let token = b.get("family_token").and_then(Value::as_str).unwrap_or("");
        !token.is_empty()
            && token.chars().any(|c| c.is_ascii_digit())
            && (crate::family_census::part_token_is_fullmatch(token) || is_wildcard_token(token))
    });
    if !names_part {
        // No column header names a part or a series: not a device table.
        return None;
    }
    let data_cols: Vec<usize> = bindings
        .iter()
        .filter_map(|b| {
            b.get("column_index")
                .and_then(Value::as_u64)
                .and_then(|c| usize::try_from(c).ok())
        })
        .collect();

    let mut attribute_rows: Vec<AttributeRow> = Vec::new();
    let mut carried = String::new();
    for (row_index, row) in grid.iter().enumerate().skip(header_rows) {
        if !row[0].is_empty() {
            carried.clone_from(&row[0]);
        }
        let sub = if label_cols > 1 {
            row.get(1).cloned().unwrap_or_default()
        } else {
            String::new()
        };
        let attributes = row_attributes(&carried, &sub);
        if attributes.is_empty() {
            continue;
        }
        let label = [&carried, &sub]
            .iter()
            .filter(|p| !p.is_empty())
            .copied()
            .cloned()
            .collect::<Vec<_>>()
            .join(" ");
        let unit = unit_from_label(&label).map(str::to_string);
        let mut values: Vec<(String, bool)> = Vec::new();
        let mut carry_value = String::new();
        for col in &data_cols {
            let cell = row.get(*col).cloned().unwrap_or_default();
            let merged = if cell.is_empty() {
                !carry_value.is_empty()
            } else {
                carry_value.clone_from(&cell);
                false
            };
            values.push((
                if cell.is_empty() {
                    carry_value.clone()
                } else {
                    cell
                },
                merged,
            ));
        }
        if values.iter().all(|(v, _)| v.is_empty()) {
            continue;
        }
        let values = split_glued_runs(&values);
        attribute_rows.push(AttributeRow {
            row_index,
            label,
            attributes,
            label_unit: unit,
            values,
            label_components: if sub.is_empty() {
                1
            } else {
                split_composite_label(&sub).len()
            },
        });
    }
    if attribute_rows.len() < 2 {
        return None;
    }
    let census_attributes = attributes_in_label(
        &attribute_rows
            .iter()
            .map(|r| r.label.clone())
            .collect::<Vec<_>>()
            .join(" "),
    );
    Some(MatrixTable {
        page,
        rows: rows.len(),
        columns: width,
        label_columns: label_cols,
        header_rows,
        orientation: None,
        bindings,
        attribute_rows,
        census_attributes,
    })
}

// ---------------------------------------------------------------------------
// Record building
// ---------------------------------------------------------------------------

fn b_get_str(b: &Binding, key: &str) -> Option<String> {
    b.get(key).and_then(Value::as_str).map(str::to_string)
}

/// `column_index` serialises as a number; read it as one.
fn b_col(b: &Binding) -> usize {
    let n = b
        .get("column_index")
        .and_then(Value::as_u64)
        .or_else(|| {
            b.get("column_index")
                .and_then(Value::as_str)
                .and_then(|s| s.parse::<u64>().ok())
        })
        .unwrap_or(0);
    usize::try_from(n).unwrap_or(0)
}

fn b_members(b: &Binding) -> Vec<Value> {
    b.get("members")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default()
}

/// Build the family record: shared vs variants, unknowns, conflicts,
/// consistency checks — the CR conventions shape.
#[must_use]
#[allow(
    clippy::too_many_lines,
    reason = "faithful port of build_family_record"
)]
#[allow(
    clippy::indexing_slicing,
    clippy::panic,
    reason = "grid walks index rows/columns as the reference does; guards upstream"
)]
pub fn build_family_record(
    document_sha256: &str,
    census_row: &Value,
    tables: &[MatrixTable],
    device_summary: &BTreeMap<String, Vec<String>>,
) -> Value {
    let series_tokens: Vec<String> = census_row
        .get("series_tokens")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    let parts_named: Vec<String> = census_row
        .get("parts_named")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(|v| v.get("token").and_then(Value::as_str).map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    let mut family: Option<String> = None;
    if let Some(first) = series_tokens.first() {
        let mut base = wildcard_base(first);
        while base.ends_with('x') {
            base.pop();
        }
        family = Some(base);
    } else if let Some(stem) = census_row
        .get("family_stems")
        .and_then(Value::as_array)
        .and_then(|a| a.first())
        .and_then(Value::as_str)
    {
        family = Some(stem.to_string());
    }

    let mut variants: BTreeMap<String, Value> = BTreeMap::new();
    let mut unbound_columns: Vec<Value> = Vec::new();
    let mut shared: Map<String, Value> = Map::new();
    let mut unknown: Vec<Value> = Vec::new();
    let mut conflicts: Vec<Value> = Vec::new();
    let mut counts = (0i64, 0i64, 0i64, 0i64); // total, typed, verbatim, unknown

    let count = |leaf: &Leaf, counts: &mut (i64, i64, i64, i64)| {
        counts.0 += 1;
        match leaf_str(leaf, "status").as_deref() {
            Some("typed" | "boolean") => counts.1 += 1,
            Some("verbatim") => counts.2 += 1,
            _ => counts.3 += 1,
        }
    };
    let uncount = |leaf: &Leaf, counts: &mut (i64, i64, i64, i64)| {
        counts.0 -= 1;
        match leaf_str(leaf, "status").as_deref() {
            Some("typed" | "boolean") => counts.1 -= 1,
            Some("verbatim") => counts.2 -= 1,
            _ => counts.3 -= 1,
        }
    };

    let mut pending: Vec<PendingRow> = Vec::new();
    // (attribute, table_index, page, label, [(binding, leaf)], whole_table, value)

    for (table_index, table) in tables.iter().enumerate() {
        let page = table.page;
        for binding in &table.bindings {
            if b_get_str(binding, "part_number").is_none() && binding.get("part_number").is_none() {
                // fall through: null part_number is unbound
            }
            if binding.get("part_number").is_none_or(Value::is_null) {
                let mut b = binding.clone();
                b.insert("page".into(), json!(page));
                unbound_columns.push(Value::Object(b));
            }
        }
        for arow in &table.attribute_rows {
            let label = &arow.label;
            let mut by_attribute: BTreeMap<String, Vec<(Value, Leaf)>> = BTreeMap::new();
            for (binding, (verbatim, merged)) in table.bindings.iter().zip(arow.values.iter()) {
                if verbatim.is_empty() {
                    continue;
                }
                let members = b_members(binding);
                if members.len() > 1 && !arow.attributes.iter().any(|(_, i)| i.is_some()) {
                    // Column names several parts and the cell may print one
                    // value per member; otherwise the one value applies to all.
                    let stripped = strip_footnotes(&norm(Some(verbatim)));
                    let pieces: Vec<String> = re(r"\s*/\s*")
                        .split(&stripped)
                        .map(str::trim)
                        .filter(|p| !p.is_empty())
                        .map(std::string::ToString::to_string)
                        .collect();
                    let per_member: Vec<String> = if pieces.len() == members.len() {
                        pieces.clone()
                    } else {
                        vec![verbatim.clone(); members.len()]
                    };
                    for (member, piece) in members.iter().zip(per_member.iter()) {
                        let bound_kind = b_get_str(binding, "binding").unwrap_or_default();
                        let sub = mk_binding(
                            b_col(binding),
                            member
                                .get("part_number")
                                .and_then(Value::as_str)
                                .map(str::to_string),
                            member
                                .get("token")
                                .and_then(Value::as_str)
                                .map(str::to_string),
                            if member.get("part_number").and_then(Value::as_str).is_some() {
                                bound_kind.as_str()
                            } else {
                                "unbound"
                            },
                        );
                        for (attribute, mut leaf) in cell_leaves(
                            piece,
                            &arow.attributes,
                            arow.label_unit.as_deref(),
                            Some(arow.label_components),
                        ) {
                            leaf.insert("verbatim".into(), json!(norm(Some(verbatim))));
                            if pieces.len() == members.len() {
                                leaf.insert("component".into(), json!(piece));
                            }
                            leaf.insert(
                                "receipt".into(),
                                leaf_receipt(
                                    page,
                                    label,
                                    usize::try_from(sub["column_index"].as_u64().unwrap_or(0))
                                        .unwrap_or(0),
                                    *merged,
                                ),
                            );
                            by_attribute
                                .entry(attribute)
                                .or_default()
                                .push((Value::Object(sub.clone()), leaf));
                        }
                    }
                    continue;
                }
                for (attribute, mut leaf) in cell_leaves(
                    verbatim,
                    &arow.attributes,
                    arow.label_unit.as_deref(),
                    Some(arow.label_components),
                ) {
                    leaf.insert(
                        "receipt".into(),
                        leaf_receipt(page, label, b_col(binding), *merged),
                    );
                    by_attribute
                        .entry(attribute)
                        .or_default()
                        .push((Value::Object(binding.clone()), leaf));
                }
            }
            for (attribute, cols) in &by_attribute {
                for (binding, leaf) in cols {
                    count(leaf, &mut counts);
                    if leaf_str(leaf, "status").as_deref() == Some("unknown") {
                        unknown.push(json!({
                            "attribute": attribute,
                            "part_number": binding.get("part_number").cloned().unwrap_or(Value::Null),
                            "column_index": binding.get("column_index").cloned().unwrap_or(Value::Null),
                            "page": page,
                            "row_label": label,
                            "verbatim": leaf.get("verbatim").cloned().unwrap_or(Value::Null),
                            "reason": leaf.get("reason").cloned().unwrap_or(Value::Null),
                        }));
                    }
                }
                let distinct: BTreeSet<String> = cols
                    .iter()
                    .map(|(_, l)| {
                        l.get("component")
                            .and_then(Value::as_str)
                            .map(str::to_string)
                            .or_else(|| {
                                l.get("verbatim")
                                    .and_then(Value::as_str)
                                    .map(str::to_string)
                            })
                            .unwrap_or_default()
                    })
                    .collect();
                let covered: BTreeSet<u64> = cols
                    .iter()
                    .map(|(b, _)| b.get("column_index").and_then(Value::as_u64).unwrap_or(0))
                    .collect();
                let all_cols: BTreeSet<u64> = table
                    .bindings
                    .iter()
                    .map(|b| b.get("column_index").and_then(Value::as_u64).unwrap_or(0))
                    .collect();
                let whole_table = distinct.len() == 1 && covered == all_cols;
                pending.push((
                    attribute.clone(),
                    table_index,
                    page,
                    label.clone(),
                    cols.clone(),
                    whole_table,
                    if whole_table {
                        Value::String(distinct.iter().next().cloned().unwrap_or_default())
                    } else {
                        Value::Null
                    },
                ));
            }
        }
    }

    // Shared only when every table prints the same single value across all of
    // its columns; a value common to one table but different in another is
    // per-variant, not family-wide.
    let mut by_attr: BTreeMap<String, Vec<&PendingRow>> = BTreeMap::new();
    for entry in &pending {
        by_attr.entry(entry.0.clone()).or_default().push(entry);
    }
    let table_parts = |index: usize| -> BTreeSet<String> {
        let mut out = BTreeSet::new();
        for b in &tables[index].bindings {
            let mut members = b_members(b);
            if members.is_empty() {
                members.push(json!({
                    "part_number": b.get("part_number").cloned().unwrap_or(Value::Null),
                    "token": b.get("family_token").cloned().unwrap_or(Value::Null),
                }));
            }
            for m in members {
                let part = m
                    .get("part_number")
                    .and_then(Value::as_str)
                    .map(str::to_string);
                out.insert(part.unwrap_or_else(|| {
                    format!(
                        "column:{}:{}",
                        tables[index].page,
                        b.get("column_index").and_then(Value::as_u64).unwrap_or(0)
                    )
                }));
            }
            if let Some(list) = b.get("part_numbers").and_then(Value::as_array) {
                for p in list {
                    if let Some(p) = p.as_str() {
                        out.insert(p.to_string());
                    }
                }
            }
        }
        out
    };
    let all_parts: BTreeSet<String> = if tables.is_empty() {
        BTreeSet::new()
    } else {
        (0..tables.len()).flat_map(table_parts).collect()
    };

    for (attribute, entries) in &by_attr {
        let covered_parts: BTreeSet<String> =
            entries.iter().flat_map(|e| table_parts(e.1)).collect();
        let values: BTreeSet<String> = entries
            .iter()
            .filter_map(|e| e.6.as_str().map(str::to_string))
            .collect();
        if entries.iter().all(|e| e.5) && values.len() == 1 && covered_parts == all_parts {
            let mut leaf = entries[0].4[0].1.clone();
            let mut receipt = leaf.get("receipt").cloned().unwrap_or(Value::Null);
            if let Some(r) = receipt.as_object_mut() {
                r.insert("applies_to".into(), json!("all_variants"));
            }
            leaf.insert("receipt".into(), receipt);
            shared.insert(attribute.clone(), Value::Object(leaf));
            continue;
        }
        for entry in entries {
            let (_, _, page, label, cols, _, _) = entry;
            for (binding, leaf) in cols {
                let Some(bnd) = binding.as_object() else {
                    continue;
                };
                let part_numbers: Vec<Option<String>> =
                    match binding.get("part_numbers").and_then(Value::as_array) {
                        Some(list) => list
                            .iter()
                            .map(|p| p.as_str().map(str::to_string))
                            .collect(),
                        None => vec![b_get_str(bnd, "part_number")],
                    };
                for part in &part_numbers {
                    let mut key = part.clone().unwrap_or_else(|| {
                        format!(
                            "column:{page}:{}",
                            binding
                                .get("column_index")
                                .and_then(Value::as_u64)
                                .unwrap_or(0)
                        )
                    });
                    if part.is_some() {
                        if let Some(letter) = b_get_str(bnd, "package_letter") {
                            key = format!("{}/{}", part.clone().unwrap_or_default(), letter);
                        }
                    }
                    let entry = variants.entry(key).or_insert_with(|| {
                        let mut m = Map::new();
                        m.insert(
                            "part_number".into(),
                            part.clone().map_or(Value::Null, Value::from),
                        );
                        m.insert(
                            "family_token".into(),
                            b_get_str(bnd, "family_token").map_or(Value::Null, Value::from),
                        );
                        m.insert(
                            "binding".into(),
                            b_get_str(bnd, "binding").map_or(Value::Null, Value::from),
                        );
                        m.insert(
                            "column_index".into(),
                            binding.get("column_index").cloned().unwrap_or(Value::Null),
                        );
                        m.insert("page".into(), json!(page));
                        m.insert("attributes".into(), json!({}));
                        Value::Object(m)
                    });
                    let Some(obj) = entry.as_object_mut() else {
                        continue;
                    };
                    if let Some(letter) = b_get_str(bnd, "package_letter") {
                        obj.insert("package_letter".into(), json!(letter));
                    }
                    if let Some(also) = bnd.get("also_covers").cloned() {
                        obj.insert("also_covers".into(), also);
                    }
                    let attrs = obj.entry("attributes").or_insert_with(|| json!({}));
                    let Some(attrs) = attrs.as_object_mut() else {
                        continue;
                    };
                    let first_of = part_numbers.first().cloned().flatten();
                    let is_first = part.clone() == first_of;
                    if let Some(existing) = attrs.get_mut(attribute) {
                        let Some(existing) = existing.as_object_mut() else {
                            continue;
                        };
                        let ex_status = leaf_str(&existing.clone(), "status");
                        let leaf_status = leaf_str(leaf, "status");
                        if BOOLEAN_ATTRIBUTES.contains(&attribute.as_str())
                            && ex_status.as_deref() == Some("boolean")
                            && leaf_status.as_deref() == Some("boolean")
                        {
                            let leaf_value =
                                leaf.get("value").and_then(Value::as_bool).unwrap_or(false);
                            if leaf_value
                                && !existing
                                    .get("value")
                                    .and_then(Value::as_bool)
                                    .unwrap_or(false)
                            {
                                existing.insert("value".into(), json!(true));
                            }
                            let merged = format!(
                                "{} | {}",
                                existing
                                    .get("verbatim")
                                    .and_then(Value::as_str)
                                    .unwrap_or_default(),
                                leaf.get("verbatim")
                                    .and_then(Value::as_str)
                                    .unwrap_or_default()
                            );
                            existing.insert("verbatim".into(), json!(merged));
                            let first_label = existing
                                .get("receipt")
                                .and_then(|r| r.get("row_label"))
                                .cloned()
                                .unwrap_or(Value::Null);
                            let rows = existing
                                .entry("rows")
                                .or_insert_with(|| json!([first_label]));
                            if let Some(list) = rows.as_array_mut() {
                                list.push(json!(label));
                            }
                            if is_first {
                                uncount(leaf, &mut counts);
                            }
                            continue;
                        }
                        if leaf_status.as_deref() == Some("unknown") {
                            // A second, unparseable row does not unseat a
                            // parsed one; already in the unknown list.
                            continue;
                        }
                        if ex_status.as_deref() == Some("unknown")
                            && leaf_str(&existing.clone(), "reason").as_deref()
                                != Some("repeated_row_disagrees")
                        {
                            attrs.insert(attribute.clone(), Value::Object(leaf.clone()));
                            continue;
                        }
                        if attribute == "package"
                            && leaf_str(&existing.clone(), "verbatim") != leaf_str(leaf, "verbatim")
                        {
                            // Packages printed on several rows: union.
                            let merged = format!(
                                "{} | {}",
                                existing
                                    .get("verbatim")
                                    .and_then(Value::as_str)
                                    .unwrap_or_default(),
                                leaf.get("verbatim")
                                    .and_then(Value::as_str)
                                    .unwrap_or_default()
                            );
                            existing.insert("verbatim".into(), json!(merged));
                            let mut seen: BTreeSet<(String, i64)> = BTreeSet::new();
                            if let Some(list) = existing.get("packages").and_then(Value::as_array) {
                                for p in list {
                                    if let (Some(f), Some(n)) = (
                                        p.get("package_family").and_then(Value::as_str),
                                        p.get("pin_count").and_then(Value::as_i64),
                                    ) {
                                        seen.insert((f.to_string(), n));
                                    }
                                }
                            }
                            let packages = existing.entry("packages").or_insert_with(|| json!([]));
                            if let Some(list) = packages.as_array_mut() {
                                if let Some(new_list) =
                                    leaf.get("packages").and_then(Value::as_array)
                                {
                                    for p in new_list {
                                        let key = (
                                            p.get("package_family")
                                                .and_then(Value::as_str)
                                                .unwrap_or_default()
                                                .to_string(),
                                            p.get("pin_count").and_then(Value::as_i64).unwrap_or(0),
                                        );
                                        if !seen.contains(&key) {
                                            seen.insert(key);
                                            list.push(p.clone());
                                        }
                                    }
                                }
                            }
                            if is_first {
                                uncount(leaf, &mut counts);
                            }
                            continue;
                        }
                        let ex_label = existing
                            .get("receipt")
                            .and_then(|r| r.get("row_label"))
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_string();
                        let width_note = attribute.starts_with("timer_")
                            && re(r"(?i)\bbits?\b").is_match(&format!("{ex_label} {label}"));
                        let shared_module = INTERFACE_COUNT_ATTRIBUTES
                            .contains(&attribute.as_str())
                            && !shared_module_attributes(&ex_label).is_empty()
                            && !shared_module_attributes(label).is_empty();
                        if ex_status.as_deref() == Some("typed")
                            && leaf_status.as_deref() == Some("typed")
                            && ex_label != *label
                            && (width_note || shared_module)
                        {
                            // "General purpose (32-bit)" + "(16-bit)" or
                            // two eUSCI classes: sum.
                            let sum = existing.get("typ").and_then(Value::as_f64).unwrap_or(0.0)
                                + leaf.get("typ").and_then(Value::as_f64).unwrap_or(0.0);
                            #[allow(
                                clippy::cast_possible_truncation,
                                reason = "the reference emits int(value) for integral floats"
                            )]
                            let n = sum as i64;
                            existing.insert("typ".into(), Value::from(n));
                            let merged = format!(
                                "{} + {}",
                                existing
                                    .get("verbatim")
                                    .and_then(Value::as_str)
                                    .unwrap_or_default(),
                                leaf.get("verbatim")
                                    .and_then(Value::as_str)
                                    .unwrap_or_default()
                            );
                            existing.insert("verbatim".into(), json!(merged));
                            existing.insert("note".into(), json!("sum of rows"));
                            let rows = existing
                                .entry("rows")
                                .or_insert_with(|| json!([json!(ex_label)]));
                            if let Some(list) = rows.as_array_mut() {
                                list.push(json!(label));
                            }
                            if is_first {
                                uncount(leaf, &mut counts);
                            }
                            continue;
                        }
                        if leaf_str(&existing.clone(), "verbatim") != leaf_str(leaf, "verbatim") {
                            // Two rows landed in one attribute with
                            // different values: trust neither.
                            conflicts.push(json!({
                                "part_number": part.clone().map_or(Value::Null, Value::from),
                                "attribute": attribute,
                                "values": [
                                    existing.get("verbatim").cloned().unwrap_or(Value::Null),
                                    leaf.get("verbatim").cloned().unwrap_or(Value::Null)
                                ],
                                "labels": [json!(ex_label), json!(label)],
                                "pages": [
                                    existing.get("receipt").and_then(|r| r.get("page")).cloned().unwrap_or(Value::Null),
                                    json!(page)
                                ],
                                "kind": "repeated_row_disagrees",
                            }));
                            match ex_status.as_deref() {
                                Some("typed" | "boolean") => {
                                    counts.1 -= 1;
                                    counts.3 += 1;
                                }
                                Some("verbatim") => {
                                    counts.2 -= 1;
                                    counts.3 += 1;
                                }
                                _ => {}
                            }
                            for k in ["typ", "min", "max", "value"] {
                                existing.remove(k);
                            }
                            existing.insert("status".into(), json!("unknown"));
                            existing.insert("reason".into(), json!("repeated_row_disagrees"));
                        }
                        continue;
                    }
                    attrs.insert(attribute.clone(), Value::Object(leaf.clone()));
                }
            }
        }
    }

    // Derived: pin_count from the package cell when every package printed for
    // the variant has one pin count. Kept separate from gpio_count.
    let derive_pins = |attrs: &mut Map<String, Value>, receipt_extra: Option<&Value>| {
        let Some(package) = attrs.get("package").cloned() else {
            return;
        };
        if attrs.contains_key("pin_count") {
            return;
        }
        let mut pins: BTreeSet<i64> = BTreeSet::new();
        if let Some(list) = package.get("packages").and_then(Value::as_array) {
            for p in list {
                if let Some(n) = p.get("pin_count").and_then(Value::as_i64) {
                    pins.insert(n);
                }
            }
        }
        if pins.len() == 1 {
            let mut receipt = package.get("receipt").cloned().unwrap_or_else(|| json!({}));
            if let (Some(r), Some(extra)) = (receipt.as_object_mut(), receipt_extra) {
                if let Some(extra) = extra.as_object() {
                    for (k, v) in extra {
                        r.insert(k.clone(), v.clone());
                    }
                }
            }
            attrs.insert(
                "pin_count".into(),
                json!({
                    "verbatim": package.get("verbatim").cloned().unwrap_or(Value::Null),
                    "status": "typed",
                    "typ": pins.iter().next().copied().unwrap_or(0),
                    "unit": "pins",
                    "derived_from": "package",
                    "receipt": receipt,
                }),
            );
        } else if pins.len() > 1 {
            let receipt = package.get("receipt").cloned().unwrap_or_else(|| json!({}));
            attrs.insert(
                "pin_count".into(),
                json!({
                    "verbatim": package.get("verbatim").cloned().unwrap_or(Value::Null),
                    "status": "unknown",
                    "reason": "multiple_pin_counts",
                    "receipt": receipt,
                }),
            );
        }
    };
    derive_pins(&mut shared, Some(&json!({"applies_to": "all_variants"})));
    for variant in variants.values_mut() {
        if let Some(attrs) = variant.get_mut("attributes").and_then(Value::as_object_mut) {
            derive_pins(attrs, None);
        }
    }

    // Plausibility: channel/GPIO counts cannot exceed the package pin count.
    for (key, variant) in &mut variants {
        let attrs = variant
            .get("attributes")
            .cloned()
            .unwrap_or_else(|| json!({}));
        let pins = attrs
            .get("pin_count")
            .cloned()
            .or_else(|| shared.get("pin_count").cloned())
            .unwrap_or(Value::Null);
        if leaf_str(&Map::new(), "x").is_some() {
            unreachable!();
        }
        let pins_map: Leaf = pins.as_object().cloned().unwrap_or_default();
        if leaf_str(&pins_map, "status").as_deref() != Some("typed") {
            continue;
        }
        let pin_count = pins_map.get("typ").and_then(Value::as_f64).unwrap_or(0.0);
        let part = key.clone();
        if let Some(attrs) = variant.get_mut("attributes").and_then(Value::as_object_mut) {
            for attribute in ["gpio_count", "adc_channels"] {
                if let Some(leaf) = attrs.get_mut(attribute).and_then(Value::as_object_mut) {
                    let status = leaf_str(&leaf.clone(), "status");
                    let typ = leaf.get("typ").and_then(Value::as_f64).unwrap_or(0.0);
                    if status.as_deref() == Some("typed") && typ > pin_count {
                        leaf.insert("status".into(), json!("unknown"));
                        #[allow(
                            clippy::cast_possible_truncation,
                            reason = "the reference formats int(pin_count)"
                        )]
                        let pins = pin_count as i64;
                        leaf.insert("reason".into(), json!(format!("exceeds_pin_count_{pins}")));
                        leaf.remove("typ");
                        let receipt = leaf.get("receipt").cloned().unwrap_or_else(|| json!({}));
                        unknown.push(json!({
                            "attribute": attribute,
                            "part_number": part,
                            "column_index": receipt.get("column_index").cloned().unwrap_or(Value::Null),
                            "page": receipt.get("page").cloned().unwrap_or(Value::Null),
                            "row_label": receipt.get("row_label").cloned().unwrap_or(Value::Null),
                            "verbatim": leaf.get("verbatim").cloned().unwrap_or(Value::Null),
                            "reason": leaf.get("reason").cloned().unwrap_or(Value::Null),
                        }));
                        counts.1 -= 1;
                        counts.3 += 1;
                    }
                }
            }
        }
    }

    // Self-consistency: ST memory letter vs printed code flash.
    let mut consistency: Vec<Value> = Vec::new();
    for (key, variant) in &mut variants {
        let Some(variant) = variant.as_object_mut() else {
            continue;
        };
        let part = variant
            .get("part_number")
            .and_then(Value::as_str)
            .map(str::to_string);
        let expected = st_flash_code_kb(part.as_deref());
        let leaf = variant
            .get("attributes")
            .and_then(|a| a.get("code_flash_kb"))
            .cloned()
            .or_else(|| shared.get("code_flash_kb").cloned());
        let (Some(expected), Some(leaf)) = (expected, leaf) else {
            continue;
        };
        let leaf_map = leaf.as_object().cloned().unwrap_or_default();
        if leaf_str(&leaf_map, "status").as_deref() != Some("typed") {
            continue;
        }
        let printed = leaf_map.get("typ").and_then(Value::as_i64);
        let ok = printed == Some(expected);
        let binding_kind = variant
            .get("binding")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        consistency.push(json!({
            "part_number": part.clone().map_or(Value::Null, Value::from),
            "check": "st_flash_code_vs_printed_code_flash",
            "expected_kb": expected,
            "printed_kb": printed.map_or(Value::Null, Value::from),
            "ok": ok,
            "binding": binding_kind,
        }));
        if !ok && binding_kind == "parts_named_join" {
            // The join ordered members by memory letter; the printed flash
            // disagrees, so the binding was wrong. Revoke it, keep the column.
            let mut revoked = mk_binding(
                variant
                    .get("column_index")
                    .and_then(Value::as_u64)
                    .map_or(0, |c| usize::try_from(c).unwrap_or(0)),
                None,
                variant
                    .get("family_token")
                    .and_then(Value::as_str)
                    .map(str::to_string),
                "unbound",
            );
            revoked.insert(
                "reason".into(),
                json!("parts_named_join_failed_flash_check"),
            );
            revoked.insert(
                "page".into(),
                variant.get("page").cloned().unwrap_or(Value::Null),
            );
            revoked.insert(
                "attempted".into(),
                part.clone().map_or(Value::Null, Value::from),
            );
            unbound_columns.push(Value::Object(revoked));
            variant.insert("part_number".to_string(), Value::Null);
            variant.insert("binding".to_string(), json!("unbound"));
            variant.insert(
                "reason".to_string(),
                json!("parts_named_join_failed_flash_check"),
            );
            let _ = key;
            continue;
        }
        if ok && binding_kind == "parts_named_join" {
            variant.insert("join_verified_by".to_string(), json!("st_flash_code"));
        }
        if !ok {
            conflicts.push(json!({
                "part_number": part.map_or(Value::Null, Value::from),
                "attribute": "code_flash_kb",
                "values": [
                    leaf_map.get("verbatim").cloned().unwrap_or(Value::Null),
                    json!(format!("ordering code implies {} KB", expected))
                ],
                "pages": [
                    leaf_map.get("receipt").and_then(|r| r.get("page")).cloned().unwrap_or(Value::Null)
                ],
                "kind": "ordering_code_mismatch",
            }));
        }
    }

    let bound_parts: BTreeSet<String> = variants
        .values()
        .filter_map(|v| {
            v.get("part_number")
                .and_then(Value::as_str)
                .map(str::to_string)
        })
        .collect();
    let mut covered: BTreeSet<String> = parts_named.iter().cloned().collect();
    covered.extend(bound_parts.iter().cloned());
    for ps in device_summary.values() {
        covered.extend(ps.iter().cloned());
    }
    let mut attributes_addressed: BTreeSet<String> = shared.keys().cloned().collect();
    for v in variants.values() {
        if let Some(attrs) = v.get("attributes").and_then(Value::as_object) {
            attributes_addressed.extend(attrs.keys().cloned());
        }
    }

    let mut variant_list: Vec<Value> = variants.into_values().collect();
    variant_list.sort_by_key(|v| {
        let none = v.get("part_number").is_none_or(Value::is_null);
        let part = v
            .get("part_number")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let col = v.get("column_index").and_then(Value::as_u64).unwrap_or(0);
        (none, part, col)
    });

    let mut meta = Map::new();
    meta.insert("extracted_by".into(), json!(EXTRACTED_BY));
    meta.insert("extracted_at".into(), Value::Null);
    meta.insert("document_sha256".into(), json!(document_sha256));
    meta.insert("source_path".into(), Value::Null);
    meta.insert("datasheet_url".into(), Value::Null);
    meta.insert("revision".into(), Value::Null);
    let mut pages: Vec<i64> = tables
        .iter()
        .map(|t| i64::try_from(t.page).unwrap_or(0))
        .collect();
    pages.sort_unstable();
    pages.dedup();
    meta.insert("device_table_pages".into(), json!(pages));
    meta.insert("models_used".into(), json!([]));

    let mut overview = Map::new();
    overview.insert("part_number".into(), Value::Null);
    overview.insert("family".into(), family.map_or(Value::Null, Value::from));
    overview.insert("series_tokens".into(), json!(series_tokens));
    overview.insert(
        "part_numbers_covered".into(),
        json!(covered.into_iter().collect::<Vec<_>>()),
    );
    overview.insert(
        "device_summary".into(),
        serde_json::to_value(device_summary).unwrap_or(Value::Null),
    );
    overview.insert(
        "vendor".into(),
        census_row.get("vendor").cloned().unwrap_or(Value::Null),
    );

    let mut bogey = Map::new();
    bogey.insert("parts_named".into(), json!(parts_named.len()));
    bogey.insert(
        "variant_columns".into(),
        json!(tables.iter().map(|t| t.bindings.len()).sum::<usize>()),
    );
    bogey.insert("variants_bound".into(), json!(bound_parts.len()));
    bogey.insert(
        "attributes_addressed".into(),
        json!(attributes_addressed.into_iter().collect::<Vec<_>>()),
    );
    bogey.insert("cells_total".into(), json!(counts.0));
    bogey.insert("cells_typed".into(), json!(counts.1));
    bogey.insert("cells_verbatim".into(), json!(counts.2));
    bogey.insert("cells_unknown".into(), json!(counts.3));

    let mut record = Map::new();
    record.insert("schema".into(), json!(MATRIX_SCHEMA));
    record.insert("_meta".into(), Value::Object(meta));
    record.insert("overview".into(), Value::Object(overview));
    record.insert("shared".into(), Value::Object(shared));
    record.insert("variants".into(), Value::Array(variant_list));
    record.insert("unbound_columns".into(), Value::Array(unbound_columns));
    record.insert("unknown".into(), Value::Array(unknown));
    record.insert("conflicts".into(), Value::Array(conflicts));
    record.insert("consistency".into(), Value::Array(consistency));
    record.insert("bogey".into(), Value::Object(bogey));
    Value::Object(record)
}

/// The PDF layer's inputs for one device-matrix document.
pub struct MatrixInput<'a> {
    /// Per-page extracted grids (page, rows) for the device-summary search.
    pub summary_tables: &'a [(usize, Vec<Vec<Option<String>>>)],
    /// Matrix candidate grids with geometry, in census-page order.
    pub matrices: &'a [MatrixGrid<'a>],
    /// The census row that marked this document `family_matrix`.
    pub census_row: &'a Value,
    /// Document hash (the caller fills it).
    pub document_sha256: &'a str,
}

/// One candidate grid with the PDF geometry the binder may need.
pub struct MatrixGrid<'a> {
    /// 1-based page.
    pub page: usize,
    /// Extracted rows.
    pub rows: &'a [Vec<Option<String>>],
    /// Page words for geometry binding.
    pub words: &'a [Word],
    /// Table cell boxes.
    pub cells: &'a [[f64; 4]],
    /// Header-row cell boxes.
    pub header_cells: &'a [[f64; 4]],
}

/// The family record for one `family_matrix` document, over the PDF layer's
/// output. Mirrors the reference's `extract_family_matrix`.
#[must_use]
#[allow(
    clippy::too_many_lines,
    reason = "faithful port of extract_family_matrix"
)]
pub fn extract_family_matrix(input: &MatrixInput<'_>) -> Value {
    let census_pages: BTreeSet<usize> = input
        .census_row
        .get("device_tables")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(|t| {
                    t.get("page")
                        .and_then(Value::as_u64)
                        .and_then(|p| usize::try_from(p).ok())
                })
                .collect()
        })
        .unwrap_or_default();
    let device_summary = read_device_summary(input.summary_tables);
    let parts_named: Vec<String> = input
        .census_row
        .get("parts_named")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(|v| v.get("token").and_then(Value::as_str).map(str::to_string))
                .collect()
        })
        .unwrap_or_default();

    let mut tables: Vec<MatrixTable> = Vec::new();
    for grid in input.matrices {
        if !census_pages.contains(&grid.page) {
            continue;
        }
        if let Some(matrix) = read_matrix_table(
            grid.rows,
            grid.page,
            &device_summary,
            Some(grid.words),
            Some(grid.cells),
            Some(grid.header_cells),
            Some(&parts_named),
        ) {
            if matrix.attribute_rows.len() >= 3 {
                tables.push(matrix);
            }
        }
    }
    build_family_record(
        input.document_sha256,
        input.census_row,
        &tables,
        &device_summary,
    )
}
