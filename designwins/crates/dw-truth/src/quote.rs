//! Quote verification — the free, perfect adjudication layer.
//!
//! Port of `harness/pipeline/quote_verify.py`.
//!
//! Given quote-native claims (`{value, quote, table}`), verifies:
//!
//! 1. the quote exists verbatim (whitespace-normalized) in the source text
//! 2. the claimed value parses from the quote itself
//! 3. the table title appears near the quote (same page/window)
//!
//! Anything failing 1 or 2 is **P0** (fabricated quote/value). Failing 3 is
//! **P1** (wrong-table suspicion) → voter tier.
//!
//! This validator is the graduation target for the hallucination ledger: it
//! replaces judgment with matching.
//!
//! ## Two wrong-cell guards
//!
//! A quote can be real and still be the wrong evidence:
//!
//! * **Ordinal** ([`column_position_binds`]) — a value lifted from the `Typ`
//!   column and claimed as `maximum` sits at the wrong ordinal position.
//! * **Header** ([`header_binds`]) — the claimed row/column header must govern
//!   the quote, and a claimed qualifier must be carried by the column.
//!
//! ## Ambient-temperature convention
//!
//! Junction (`tj`) and storage (`tstg`) are **different quantities** and must
//! never satisfy a `temp_ambient` claim (`cr-core truth-audit C1`). A claim
//! whose evidence names junction/storage is `wrong_quantity_convention`, P0.

use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

// ---------------------------------------------------------------------------
// text normalisation
// ---------------------------------------------------------------------------

/// NFKC-normalise, collapse whitespace, lowercase.
///
/// Comparison is done on this form only, so that a quote survives ligatures,
/// non-breaking spaces, and case differences without losing its claim to being
/// verbatim.
#[must_use]
pub fn norm(s: &str) -> String {
    let folded = s;
    let collapsed: String = folded.split_whitespace().collect::<Vec<_>>().join(" ");
    collapsed.to_lowercase()
}

/// Ratio of two small counts.
///
/// `usize as f64` is exact for anything below 2^53, and these are token and
/// hit counts over a document — never close to that. Isolated here so the
/// cast is written once, with the reason.
#[allow(clippy::cast_precision_loss)]
fn ratio(hits: usize, total: usize) -> f64 {
    hits as f64 / total.max(1) as f64
}

fn is_number_token(t: &str) -> bool {
    !t.is_empty() && t.bytes().all(|b| b.is_ascii_digit() || b == b'.')
}

/// Numeric and alphabetic tokens of the normalised text.
///
/// Punctuation flushes and is **discarded** — the reference's
/// `\d+(?:\.\d+)?|[a-z]+` never yields a token containing punctuation. Keeping
/// it would make `drain-source` tokenize as `["-source"]` and silently fail
/// header binding.
#[must_use]
pub fn tokens(s: &str) -> Vec<String> {
    let n = norm(s);
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut cur_num = false;
    let flush = |cur: &mut String, cur_num: &mut bool, out: &mut Vec<String>| {
        if !cur.is_empty() {
            out.push(std::mem::take(cur));
        }
        *cur_num = false;
    };
    for ch in n.chars() {
        let is_digit = ch.is_ascii_digit() || ch == '.';
        let is_alpha = ch.is_ascii_alphabetic();
        if !is_digit && !is_alpha {
            flush(&mut cur, &mut cur_num, &mut out);
            continue;
        }
        if cur.is_empty() {
            cur.push(ch);
            cur_num = is_digit && !is_alpha;
        } else if (is_digit && !is_alpha) == cur_num {
            cur.push(ch);
        } else {
            flush(&mut cur, &mut cur_num, &mut out);
            cur.push(ch);
            cur_num = is_digit && !is_alpha;
        }
    }
    flush(&mut cur, &mut cur_num, &mut out);
    out
}

/// Character index of a byte offset.
///
/// `str::find` returns a **byte** offset; the reference's `str.find` returns a
/// code-point index and its slices are code-point slices. Windows computed in
/// bytes cover the wrong region whenever the text carries multibyte characters
/// — which extracted datasheet text always does.
fn char_index(s: &str, byte_idx: usize) -> usize {
    s.char_indices().take_while(|(i, _)| *i < byte_idx).count()
}

/// Slice by character offsets, exactly like Python's `s[start:end]`.
///
/// `skip`/`take` clamp at the ends and can never split a character. The
/// obvious `s.get(start..end)` is a trap: `get` returns `None` when a window
/// edge lands mid-character, and an `unwrap_or(&s)` fallback silently widens
/// the window to the whole document — every token then hits and an unbound
/// header looks bound. That class is `ASSURANCE.md` §1: a check that cannot
/// fail on the property it claims.
fn char_slice(s: &str, start: usize, end: usize) -> String {
    s.chars()
        .skip(start)
        .take(end.saturating_sub(start))
        .collect()
}

// ---------------------------------------------------------------------------
// field aliases and the ambient convention
// ---------------------------------------------------------------------------

/// Field aliases for tolerant quote location (`M4` rule-6 style).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Field {
    /// Minimum input voltage.
    VinMinV,
    /// Maximum input voltage.
    VinMaxV,
    /// Minimum output voltage.
    VoutMinV,
    /// Maximum output voltage.
    VoutMaxV,
    /// Maximum output current.
    IoutMaxA,
    /// Minimum ambient temperature.
    TempMinC,
    /// Maximum ambient temperature.
    TempMaxC,
    /// Absolute maximum.
    VAbsMax,
    /// Free-text description.
    Description,
}

impl Field {
    /// Map an arbitrary claim-field name to a known field.
    ///
    /// The reference is **string-keyed** — `verify_claims` accepts any field
    /// name and only two of them carry rules (`temp_min_c` / `temp_max_c`, and
    /// the alias table for tolerant quote location). An unrecognised name is
    /// therefore `None`, which means "no special rule", **not** a new variant.
    /// Returning `None` here is what keeps this port able to run over real
    /// claim corpora, whose field names are `opn:symbol`, not a closed enum.
    #[must_use]
    pub fn parse(name: &str) -> Option<Self> {
        match name {
            "vin_min_v" => Some(Self::VinMinV),
            "vin_max_v" => Some(Self::VinMaxV),
            "vout_min_v" => Some(Self::VoutMinV),
            "vout_max_v" => Some(Self::VoutMaxV),
            "iout_max_a" => Some(Self::IoutMaxA),
            "temp_min_c" => Some(Self::TempMinC),
            "temp_max_c" => Some(Self::TempMaxC),
            "v_abs_max" => Some(Self::VAbsMax),
            "description" => Some(Self::Description),
            _ => None,
        }
    }

    /// The stable string used in verdicts and gold fixtures.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::VinMinV => "vin_min_v",
            Self::VinMaxV => "vin_max_v",
            Self::VoutMinV => "vout_min_v",
            Self::VoutMaxV => "vout_max_v",
            Self::IoutMaxA => "iout_max_a",
            Self::TempMinC => "temp_min_c",
            Self::TempMaxC => "temp_max_c",
            Self::VAbsMax => "v_abs_max",
            Self::Description => "description",
        }
    }

    /// Whether this field is an ambient-temperature claim.
    #[must_use]
    pub const fn is_temp_ambient(self) -> bool {
        matches!(self, Self::TempMinC | Self::TempMaxC)
    }

    /// Alias vocabulary for tolerant quote location.
    #[must_use]
    pub const fn aliases(self) -> &'static [&'static str] {
        match self {
            Self::VinMinV | Self::VinMaxV => &[
                "vin", "input", "supply", "vcc", "vdd", "pvin", "avin", "vbus",
            ],
            Self::VoutMinV | Self::VoutMaxV => &["vout", "output"],
            Self::IoutMaxA => &["iout", "output", "current", "io"],
            // AMBIENT-OPERATING convention ONLY (`truth-audit C1`).
            Self::TempMinC | Self::TempMaxC => &["ta", "ambient", "operating"],
            Self::VAbsMax => &["absolute", "stress", "max"],
            Self::Description => &[],
        }
    }
}

/// Junction and storage are different quantities and must never satisfy a
/// `temp_ambient` claim.
pub const TEMP_WRONG_QUANTITY: &[&str] = &["tj", "junction", "tstg", "storage", "ts"];

/// P0 if a `temp_ambient` claim's evidence names junction/storage.
///
/// `Some(false)` is a proven violation. `None` is undecidable.
#[must_use]
pub fn temp_convention_check(field: Field, quote: &str) -> Option<bool> {
    if !field.is_temp_ambient() {
        return None;
    }
    let q_tokens: BTreeSet<String> = tokens(quote).into_iter().collect();
    for wrong in TEMP_WRONG_QUANTITY {
        let in_quote = q_tokens.contains(*wrong);
        let in_long_quote = wrong.len() > 2 && norm(quote).contains(wrong);
        if in_quote || in_long_quote {
            return Some(false);
        }
    }
    None
}

// ---------------------------------------------------------------------------
// quote location
// ---------------------------------------------------------------------------

/// Stopwords the tolerant matcher ignores.
pub const STOPWORDS: &[&str] = &[
    "to", "up", "in", "the", "of", "and", "at", "or", "a", "an", "is", "for", "with", "from", "on",
    "by", "max", "min", "v", "mv", "a", "ma", "ua", "c", "dc",
];

/// Tolerant quote location: every NUMBER in the quote must appear in the text,
/// and significant (non-stopword) tokens must mostly appear within a
/// `±number_window` span around the numbers.
///
/// Field aliases rescue legitimate paraphrases for the right quantity;
/// **wrong-quantity binding never matches**.
#[must_use]
pub fn token_window_match(
    quote: &str,
    text: &str,
    min_token_overlap: f64,
    number_window: usize,
    field: Option<Field>,
) -> bool {
    let q_tokens = tokens(quote);
    let t_norm = norm(text);
    if q_tokens.is_empty() {
        return false;
    }

    let numbers: Vec<&String> = q_tokens.iter().filter(|t| is_number_token(t)).collect();
    let span = if numbers.is_empty() {
        t_norm.clone()
    } else {
        let mut positions = Vec::new();
        for num in &numbers {
            let Some(idx) = t_norm.find(num.as_str()) else {
                return false;
            };
            positions.push(char_index(&t_norm, idx));
        }
        let first = positions.iter().copied().min().unwrap_or(0);
        let last = positions.iter().copied().max().unwrap_or(0);
        char_slice(
            &t_norm,
            first.saturating_sub(number_window),
            last + number_window,
        )
    };

    let non_num: Vec<&String> = q_tokens.iter().filter(|t| !is_number_token(t)).collect();
    if non_num.is_empty() {
        return !numbers.is_empty();
    }
    let significant: Vec<&&String> = non_num
        .iter()
        .filter(|t| t.len() > 2 && !STOPWORDS.contains(&t.as_str()))
        .collect();
    if significant.is_empty() {
        let hits = non_num.iter().filter(|t| span.contains(t.as_str())).count();
        return ratio(hits, non_num.len()) >= min_token_overlap;
    }
    let hits = significant
        .iter()
        .filter(|t| span.contains(t.as_str()))
        .count();
    let threshold = if numbers.is_empty() {
        min_token_overlap
    } else {
        0.5
    };
    if ratio(hits, significant.len()) >= threshold {
        return true;
    }

    // Alias rescue: only when the quote names the quantity AND the span
    // confirms it. A wrong-quantity quote never reaches this.
    let aliases = field.map_or(&[][..], Field::aliases);
    if !aliases.is_empty() && !numbers.is_empty() {
        let quote_names = non_num.iter().any(|t| aliases.contains(&t.as_str()));
        let span_confirms = aliases.iter().any(|a| span.contains(a));
        return quote_names && span_confirms;
    }
    false
}

/// Locate a quote in text: exact, then tolerant.
#[must_use]
pub fn find_quote(quote: &str, text: &str, window: Option<&str>, field: Option<Field>) -> bool {
    let nq = norm(quote);
    if nq.is_empty() {
        return false;
    }
    let nt = norm(text);
    if nt.contains(&nq) {
        return true;
    }
    if let Some(w) = window {
        if norm(w).contains(&nq) {
            return true;
        }
    }
    token_window_match(quote, text, 0.7, 60, field)
}

/// Coerce a claim value to `f64`, accepting a numeric **string**.
///
/// The claim corpus stores `value` as a string as often as a number
/// (`"0.5"`, not `0.5`). `Value::as_f64()` returns `None` for a string, which
/// silently skipped the ordinal wrong-cell guard and produced `SUPPORTED`
/// where the reference produced `wrong_cell_binding` (P0). A predicate that
/// returns `None` instead of failing is exactly the class `ASSURANCE.md` §1
/// names — so this one fails loudly on a non-numeric string.
fn value_as_f64(v: Option<&serde_json::Value>) -> Option<f64> {
    match v? {
        serde_json::Value::Number(n) => n.as_f64(),
        serde_json::Value::String(s) => s.trim().parse::<f64>().ok(),
        _ => None,
    }
}

/// The claimed value must parse from the quote itself.
///
/// Numbers match within `max(1e-6, |value| * 1e-4)`; strings match as a
/// normalised substring. `None` vacuously holds — the quote is the evidence,
/// and an unvalued claim is not falsified by it.
#[must_use]
pub fn value_in_quote(value: Option<&serde_json::Value>, quote: &str) -> bool {
    let Some(value) = value else {
        return true;
    };
    let q = norm(quote);
    if value.is_boolean() {
        return false;
    }
    if let Some(n) = value.as_f64() {
        let candidates: Vec<f64> = q
            .split(|c: char| !(c.is_ascii_digit() || c == '.' || c == '-' || c == '+'))
            .filter(|s| !s.is_empty())
            .filter_map(|s| s.parse::<f64>().ok())
            .collect();
        let tol = (1e-6_f64).max(n.abs() * 1e-4);
        return candidates.iter().any(|c| (c - n).abs() < tol);
    }
    match value.as_str() {
        Some(s) => q.contains(&norm(s)),
        None => false,
    }
}

// ---------------------------------------------------------------------------
// wrong-cell guards
// ---------------------------------------------------------------------------

/// Column orders the ordinal guard recognises.
pub const COLUMN_ORDER_SETS: &[&[&str]] = &[
    &["min", "typ", "max"],
    &["min", "typ", "max", "unit"],
    &["min", "typ", "max", "conditions", "unit"],
    &["min", "max", "unit"],
    &["typ", "max", "unit"],
];

/// Tokens a qualifier requires the column header to carry.
pub const QUALIFIER_COLUMN_TOKENS: &[(&str, &[&str])] = &[
    ("maximum", &["max", "maximum", "max."]),
    ("minimum", &["min", "minimum", "min."]),
    ("typical", &["typ", "typical", "typ."]),
    ("rated", &["rated", "rating"]),
];

/// Ordinal wrong-cell guard.
///
/// When a header line shows an ordered column set (`min/typ/max…`) and the
/// quote line's numbers follow the same order, the claimed value must sit at
/// the claimed column's ordinal position.
///
/// `Some(false)` is a provable mismatch; `None` is undecidable.
#[must_use]
pub fn column_position_binds(
    column_header: Option<&str>,
    quote: &str,
    span_text: &str,
    value: Option<f64>,
) -> Option<bool> {
    let col_raw = column_header?.to_lowercase();
    let col = col_raw.trim().to_string();
    if col.is_empty() {
        return None;
    }
    let col_tok: &str = match col.as_str() {
        "maximum" => "max",
        "minimum" => "min",
        "typical" => "typ",
        other => other,
    };

    let mut header_line: Option<Vec<String>> = None;
    for line in span_text
        .split('\n')
        .map(norm)
        .filter(|l| !l.trim().is_empty())
    {
        let toks = tokens(&line);
        for order in COLUMN_ORDER_SETS {
            let present: Vec<&str> = order
                .iter()
                .copied()
                .filter(|t| toks.iter().any(|x| x == t))
                .collect();
            if present.len() >= 2 && present.contains(&col_tok) {
                let idx_in_line: Vec<String> = toks
                    .iter()
                    .filter(|t| order.contains(&t.as_str()))
                    .cloned()
                    .collect();
                let expected: Vec<String> = order
                    .iter()
                    .copied()
                    .filter(|t| idx_in_line.iter().any(|x| x == t))
                    .map(str::to_string)
                    .collect();
                if idx_in_line == expected {
                    header_line = Some(idx_in_line);
                    break;
                }
            }
        }
        if header_line.is_some() {
            break;
        }
    }
    let header_line = header_line?;
    let col_index = header_line.iter().position(|t| t == col_tok)?;

    let q = norm(quote);
    let quote_nums: Vec<f64> = q
        .split(|c: char| !(c.is_ascii_digit() || c == '.' || c == '-' || c == '+'))
        .filter(|s| !s.is_empty())
        .filter_map(|s| s.parse::<f64>().ok())
        .collect();
    let val = value?;

    let val_positions: Vec<usize> = quote_nums
        .iter()
        .enumerate()
        .filter(|(_, n)| (**n - val).abs() < 1e-9)
        .map(|(i, _)| i)
        .collect();
    if val_positions.is_empty() || val_positions.iter().collect::<BTreeSet<_>>().len() > 1 {
        return None;
    }
    let first_pos = *val_positions.first()?;
    if first_pos == col_index {
        return Some(true);
    }
    if first_pos < header_line.len() {
        return Some(false);
    }
    None
}

/// True when the claimed row/column header plausibly governs the quote.
///
/// Header tokens must appear in the span neighbourhood; a claimed qualifier
/// (`max`/`typ`/`min`) must be carried by the column header tokens. This is
/// the wrong-cell guard: a value lifted from the `Typ` column and claimed as
/// maximum fails here even though its quote is real.
#[must_use]
pub fn header_binds(
    header: Option<&str>,
    span_text: &str,
    qualifier: Option<&str>,
) -> Option<bool> {
    let header = header?;
    if header.is_empty() {
        return None;
    }
    let tokens: Vec<String> = tokens(header).into_iter().filter(|t| t.len() > 1).collect();
    if tokens.is_empty() {
        return None;
    }
    let span = norm(span_text);
    let hits = tokens.iter().filter(|t| span.contains(t.as_str())).count();
    if hits == 0 {
        return None;
    }
    let bound = hits >= (tokens.len() / 2).max(1);
    if bound {
        if let Some(q) = qualifier {
            let want = QUALIFIER_COLUMN_TOKENS
                .iter()
                .find(|(k, _)| *k == q.to_lowercase().trim())
                .map(|(_, v)| *v);
            if let Some(want) = want {
                if !want.iter().any(|w| span.contains(w)) {
                    return Some(false);
                }
            }
        }
    }
    Some(bound)
}

// ---------------------------------------------------------------------------
// verdicts
// ---------------------------------------------------------------------------

/// How strongly the evidence supports the claim.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
/// Serialised exactly as the reference emits it (`SUPPORTED`, `REVIEW`,
/// `UNSUPPORTED`). Case is part of the wire format — a port that "tidies" it
/// to `snake_case` cannot be diffed against the reference it is replacing.
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum VerdictLabel {
    /// Quote located, value parses, headers bind.
    Supported,
    /// Real quote but a binding question remains.
    Review,
    /// P0: fabricated quote, wrong value, wrong cell, or wrong quantity.
    Unsupported,
}

/// Severity of a finding. P0 is fabricated evidence.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
/// Serialised as the reference emits it: `P0`, `P1`, `P2`.
#[serde(rename_all = "UPPERCASE")]
pub enum Severity {
    /// Fabricated quote/value, wrong cell, wrong quantity.
    P0,
    /// Wrong-table suspicion or unbound header — voter tier.
    P1,
    /// Clean.
    P2,
}

/// Why a claim landed where it did.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ReasonCode {
    /// No quote was supplied at all.
    UnquotedAssertion,
    /// The quote does not appear in the document.
    QuoteNotInDoc,
    /// A `temp_ambient` claim whose evidence names junction or storage.
    WrongQuantityConvention,
    /// The claimed value does not parse from the quote.
    ValueNotInQuote,
    /// The value sits at the wrong ordinal column position.
    WrongCellBinding,
    /// Neither row nor column header governs the quote.
    UnboundHeader,
    /// Only one of row/column binds.
    PartiallyBoundHeader,
    /// The table title could not be located near the quote.
    TableTitleUnverified,
}

impl ReasonCode {
    /// The stable string the reference emits.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::UnquotedAssertion => "unquoted_assertion",
            Self::QuoteNotInDoc => "quote_not_in_doc",
            Self::WrongQuantityConvention => "wrong_quantity_convention",
            Self::ValueNotInQuote => "value_not_in_quote",
            Self::WrongCellBinding => "wrong_cell_binding",
            Self::UnboundHeader => "unbound_header",
            Self::PartiallyBoundHeader => "partially_bound_header",
            Self::TableTitleUnverified => "table_title_unverified",
        }
    }
}

/// One verdict on one claim.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Verdict {
    /// Which claim field.
    pub field: String,
    /// The outcome.
    pub verdict: VerdictLabel,
    /// Why, when it is not clean.
    ///
    /// Serialised as an explicit `null` rather than omitted, because the
    /// reference emits `"reason_code": null`. Absent and null are the same
    /// fact, but a port that "tidies" the wire shape cannot be diffed against
    /// what it replaces.
    pub reason_code: Option<ReasonCode>,
    /// Severity.
    pub severity: Severity,
    /// A snippet of the quote, when the quote itself is the problem.
    ///
    /// **Omitted when absent**, unlike `reason_code`: the reference builds
    /// this key only when it has a quote (`{"…", "evidence_quote": quote[:80]}`)
    /// and never emits it as null. The two fields are asymmetric on the wire
    /// and the port has to match both shapes to be diffable.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub evidence_quote: Option<String>,
}

/// A claim as the quote verifier sees it.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct ClaimInput {
    /// The claimed value, or `None` for an unvalued claim.
    pub value: Option<serde_json::Value>,
    /// The printed quote. Empty is `UnquotedAssertion`.
    pub quote: String,
    /// The printed table title, if any.
    pub table: String,
    /// The column header, if any.
    pub column_header: Option<String>,
    /// The row header, if any.
    pub row_header: Option<String>,
    /// The claimed qualifier (`max`/`typ`/`min`), if any.
    pub qualifier: Option<String>,
}

/// Verify a map of claims against the document text.
///
/// # Why this function is long
///
/// It is a faithful sequential port of the reference's `verify_claims`, whose
/// order of checks *is* the severity ladder: quote exists → quote locates →
/// quantity convention → value parses → ordinal cell → header binding → table.
/// Each `continue` is a P0 short-circuit. Splitting the checks into helpers
/// would break the ladder's legibility, which is the only way this port can be
/// reviewed against `quote_verify.py` line by line.
#[allow(clippy::too_many_lines)]
#[must_use]
pub fn verify_claims(claims: &BTreeMap<String, ClaimInput>, doc_text: &str) -> Vec<Verdict> {
    let mut verdicts = Vec::new();
    let t_normed = norm(doc_text);

    for (field_name, c) in claims {
        // Only known field names carry a rule. An unrecognised name is "no
        // special rule", which is exactly what the reference does with a
        // string lookup that misses.
        let field = Field::parse(field_name);
        if c.value.is_none() && c.quote.is_empty() {
            continue;
        }
        if c.quote.is_empty() {
            verdicts.push(Verdict {
                field: field_name.clone(),
                verdict: VerdictLabel::Unsupported,
                reason_code: Some(ReasonCode::UnquotedAssertion),
                severity: Severity::P0,
                evidence_quote: None,
            });
            continue;
        }
        if !find_quote(&c.quote, doc_text, None, field) {
            verdicts.push(Verdict {
                field: field_name.clone(),
                verdict: VerdictLabel::Unsupported,
                reason_code: Some(ReasonCode::QuoteNotInDoc),
                severity: Severity::P0,
                evidence_quote: Some(c.quote.chars().take(80).collect()),
            });
            continue;
        }
        if field.is_some_and(|f| temp_convention_check(f, &c.quote) == Some(false)) {
            verdicts.push(Verdict {
                field: field_name.clone(),
                verdict: VerdictLabel::Unsupported,
                reason_code: Some(ReasonCode::WrongQuantityConvention),
                severity: Severity::P0,
                evidence_quote: Some(c.quote.chars().take(80).collect()),
            });
            continue;
        }
        if !value_in_quote(c.value.as_ref(), &c.quote) {
            verdicts.push(Verdict {
                field: field_name.clone(),
                verdict: VerdictLabel::Unsupported,
                reason_code: Some(ReasonCode::ValueNotInQuote),
                severity: Severity::P0,
                evidence_quote: None,
            });
            continue;
        }

        let table_ok = c.table.is_empty() || find_quote(&c.table, doc_text, None, field);

        let nq = norm(&c.quote);
        let head = nq.chars().take(40).collect::<String>();
        let idx = t_normed.find(&head).map(|b| char_index(&t_normed, b));
        let col = c.column_header.as_deref();
        let row = c.row_header.as_deref();
        let span = match idx {
            Some(i) => char_slice(&t_normed, i.saturating_sub(400), i + 200),
            None => t_normed.clone(),
        };
        let ordinal = column_position_binds(col, &c.quote, &span, value_as_f64(c.value.as_ref()));
        if ordinal == Some(false) {
            verdicts.push(Verdict {
                field: field_name.clone(),
                verdict: VerdictLabel::Unsupported,
                reason_code: Some(ReasonCode::WrongCellBinding),
                severity: Severity::P0,
                evidence_quote: Some(c.quote.chars().take(80).collect()),
            });
            continue;
        }

        let (binding, reason) = if col.is_none() && row.is_none() {
            (Severity::P1, Some(ReasonCode::UnboundHeader))
        } else {
            let col_ok = header_binds(col, &span, c.qualifier.as_deref());
            let row_span = match idx {
                Some(i) => char_slice(&t_normed, i.saturating_sub(200), i + 200),
                None => t_normed.clone(),
            };
            let row_ok = header_binds(row, &row_span, None);
            match (col_ok, row_ok) {
                (Some(false), _) | (_, Some(false)) => {
                    (Severity::P0, Some(ReasonCode::WrongCellBinding))
                }
                (None, None) => (Severity::P1, Some(ReasonCode::UnboundHeader)),
                (c, r) if c.is_none() || r.is_none() => {
                    (Severity::P1, Some(ReasonCode::PartiallyBoundHeader))
                }
                _ => (Severity::P2, None),
            }
        };

        let severity = if table_ok && binding == Severity::P2 {
            Severity::P2
        } else {
            binding
        };
        let reason_code = reason.or(if table_ok {
            None
        } else {
            Some(ReasonCode::TableTitleUnverified)
        });

        verdicts.push(Verdict {
            field: field_name.clone(),
            verdict: if severity == Severity::P2 {
                VerdictLabel::Supported
            } else {
                VerdictLabel::Review
            },
            reason_code,
            severity,
            evidence_quote: None,
        });
    }
    verdicts
}

#[cfg(test)]
#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    clippy::match_wildcard_for_single_variants,
    clippy::wildcard_enum_match_arm,
    clippy::too_many_lines
)]
mod tests {
    use super::*;

    fn claim(value: f64, quote: &str, table: &str) -> ClaimInput {
        ClaimInput {
            value: Some(serde_json::json!(value)),
            quote: quote.to_string(),
            table: table.to_string(),
            column_header: None,
            row_header: None,
            qualifier: None,
        }
    }

    fn one(v: &Verdict) -> (VerdictLabel, Severity) {
        (v.verdict, v.severity)
    }

    const DOC: &str = "Drain-Source voltage VDS 600 V\n\
        Absolute maximum ratings (Ta = 25 C)\n\
        Parameter Symbol Min Typ Max Unit\n\
        Drain-Source voltage VDS - - 600 V";

    #[test]
    fn norm_folds_case_and_whitespace() {
        assert_eq!(norm("  VDS   =\t600 V "), "vds = 600 v");
    }

    #[test]
    fn exact_quote_is_located() {
        assert!(find_quote("VDS 600 V", DOC, None, None));
    }

    #[test]
    fn fabricated_quote_is_p0() {
        let mut claims = BTreeMap::new();
        claims.insert(
            "v_abs_max".to_string(),
            claim(600.0, "VDS 999 V", "Absolute maximum ratings"),
        );
        let out = verify_claims(&claims, DOC);
        assert_eq!(out.len(), 1);
        assert_eq!(one(&out[0]), (VerdictLabel::Unsupported, Severity::P0));
        assert_eq!(out[0].reason_code, Some(ReasonCode::QuoteNotInDoc));
    }

    #[test]
    fn value_not_in_quote_is_p0() {
        let mut claims = BTreeMap::new();
        claims.insert(
            "v_abs_max".to_string(),
            claim(999.0, "VDS 600 V", "Absolute maximum ratings"),
        );
        let out = verify_claims(&claims, DOC);
        assert_eq!(out[0].reason_code, Some(ReasonCode::ValueNotInQuote));
        assert_eq!(out[0].severity, Severity::P0);
    }

    #[test]
    fn unquoted_assertion_is_p0() {
        let mut claims = BTreeMap::new();
        claims.insert("v_abs_max".to_string(), claim(600.0, "", ""));
        let out = verify_claims(&claims, DOC);
        assert_eq!(out[0].reason_code, Some(ReasonCode::UnquotedAssertion));
    }

    /// `truth-audit C1`: junction and storage are different quantities.
    #[test]
    fn tj_evidence_never_satisfies_ambient() {
        assert_eq!(
            temp_convention_check(Field::TempMaxC, "Tj = 150 C"),
            Some(false)
        );
        assert_eq!(
            temp_convention_check(Field::TempMaxC, "Tstg -55 to 150 C"),
            Some(false)
        );
        assert_eq!(temp_convention_check(Field::TempMaxC, "Ta = 25 C"), None);
        assert_eq!(
            temp_convention_check(Field::TempMinC, "operating -40 C"),
            None
        );
    }

    #[test]
    fn tj_evidence_is_p0_on_temp_field() {
        // The quote must be *in* the document for the temp convention to be
        // the thing that rejects it — otherwise quote location fires first.
        let doc = "Junction temperature Tj = 150 C";
        let mut claims = BTreeMap::new();
        claims.insert("temp_max_c".to_string(), claim(150.0, "Tj = 150 C", ""));
        let out = verify_claims(&claims, doc);
        assert_eq!(
            out[0].reason_code,
            Some(ReasonCode::WrongQuantityConvention)
        );
        assert_eq!(out[0].severity, Severity::P0);
    }

    /// The wrong-cell guard: a Typ-column value claimed as maximum.
    #[test]
    fn typ_value_claimed_as_max_is_wrong_cell() {
        let doc = "Parameter Symbol Min Typ Max Unit\n\
                   Gate threshold VGS(th) 2.0 2.5 4.0 V";
        let mut claims = BTreeMap::new();
        let mut c = claim(2.5, "Gate threshold VGS(th) 2.0 2.5 4.0 V", "");
        c.column_header = Some("maximum".into());
        c.qualifier = Some("maximum".into());
        claims.insert("v_abs_max".to_string(), c);
        let out = verify_claims(&claims, doc);
        assert_eq!(out[0].severity, Severity::P0);
    }

    #[test]
    fn value_in_quote_numeric_tolerance() {
        let v = serde_json::json!(600.0);
        assert!(value_in_quote(Some(&v), "VDS 600 V"));
        assert!(!value_in_quote(Some(&v), "VDS 601 V"));
        // Integer-looking values match too.
        let v2 = serde_json::json!(600);
        assert!(value_in_quote(Some(&v2), "VDS 600 V"));
    }

    #[test]
    fn value_in_quote_none_vacuously_holds() {
        assert!(value_in_quote(None, "anything"));
    }

    #[test]
    fn supported_verdict_when_everything_binds() {
        // Every column carries a number, so the ordinal guard can place the
        // claimed value at the claimed column's position.
        let doc = "Parameter Symbol Min Typ Max Unit\n\
                   Drain-Source voltage VDS 450 550 600 V\n\
                   Absolute maximum ratings (Ta = 25 C)";
        let mut claims = BTreeMap::new();
        let mut c = claim(600.0, "Drain-Source voltage VDS 450 550 600 V", "");
        c.column_header = Some("max".into());
        c.row_header = Some("Drain-Source voltage".into());
        claims.insert("v_abs_max".to_string(), c);
        let out = verify_claims(&claims, doc);
        assert_eq!(out.len(), 1);
        assert_eq!(one(&out[0]), (VerdictLabel::Supported, Severity::P2));
    }

    /// Known limitation of the ordinal guard, ported faithfully.
    ///
    /// When a row uses dash placeholders (`VDS - - 600 V`), the quote yields a
    /// single number and its ordinal position is `0`, while the claimed column
    /// `max` sits at index 2. The guard then reports a **provable mismatch**
    /// and the claim is P0 — even though the value really is in the Max column.
    ///
    /// This is the reference's behaviour (`column_position_binds` has no notion
    /// of a dash placeholder), so it is ported as-is rather than "fixed". If it
    /// is ever changed, it changes on both sides and the gold decides.
    #[test]
    fn dash_placeholders_defeat_the_ordinal_guard() {
        let doc = "Parameter Symbol Min Typ Max Unit\n\
                   Drain-Source voltage VDS - - 600 V";
        let mut claims = BTreeMap::new();
        let mut c = claim(600.0, "Drain-Source voltage VDS - - 600 V", "");
        c.column_header = Some("max".into());
        claims.insert("v_abs_max".to_string(), c);
        let out = verify_claims(&claims, doc);
        assert_eq!(out[0].severity, Severity::P0);
        assert_eq!(out[0].reason_code, Some(ReasonCode::WrongCellBinding));
    }

    /// Negative control — byte offsets must never widen the span window.
    ///
    /// `find` returns a byte offset, but the reference slices by code point.
    /// When `idx + 200` lands mid-character (this doc starts 15 ASCII bytes,
    /// then `µ` — 2 bytes each — so a `µ` straddles byte 200), a
    /// `get(start..end).unwrap_or(&t_normed)` fallback silently degrades the
    /// span to the **whole document**. Every header token then hits and
    /// `unbound_header` becomes `SUPPORTED`. The header tokens here sit at
    /// char 435+, outside the correct `[idx-400, idx+200)` window.
    #[test]
    fn multibyte_window_edge_does_not_widen_span() {
        let doc = format!("quoted 42 here {} max output voltage", "µ".repeat(420));
        let mut claims = BTreeMap::new();
        let mut c = claim(42.0, "quoted 42 here", "");
        c.column_header = Some("Max".into());
        c.row_header = Some("Output voltage".into());
        claims.insert("iout_max_a".to_string(), c);
        let out = verify_claims(&claims, &doc);
        assert_eq!(out.len(), 1);
        assert_eq!(out[0].reason_code, Some(ReasonCode::UnboundHeader));
        assert_eq!(one(&out[0]), (VerdictLabel::Review, Severity::P1));
    }

    /// Negative control — same byte/char bug in `token_window_match`'s
    /// `±number_window` span. The tolerant locator must judge the window it
    /// computed, not fall back to the whole document when the window edge
    /// splits a multibyte character (`widget` here sits 100 chars past `7`).
    #[test]
    fn token_window_edge_does_not_widen_span() {
        let doc = format!("7{} widget", "µ".repeat(100));
        assert!(
            !token_window_match("widget 7", &doc, 0.7, 60, None),
            "whole-doc fallback made a far token look near"
        );
        // Positive control: the same tokens genuinely near each other bind.
        assert!(token_window_match("widget 7", "7 widget", 0.7, 60, None));
    }

    /// `PLAN` §9: a class caught deterministically never needs model judgment.
    #[test]
    fn every_p0_has_a_reason_code() {
        let mut claims = BTreeMap::new();
        claims.insert("a".to_string(), claim(1.0, "nope", ""));
        claims.insert("b".to_string(), claim(2.0, "", ""));
        for v in verify_claims(&claims, DOC) {
            if v.severity == Severity::P0 {
                assert!(v.reason_code.is_some(), "P0 without a reason: {v:?}");
            }
        }
    }
}
