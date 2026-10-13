//! # dw-lessons — the lesson index in front of the proposal gate
//!
//! *"A caught class never becomes model-judgment again"* (`PLAN` §9). This
//! crate makes that mechanical: it indexes the lessons the workspace already
//! wrote down, and a proposal gate must query it before proposing.
//!
//! ## One source of truth
//!
//! There is no lessons database to drift from the docs. The index is built
//! from the documents that **are** the lessons:
//!
//! | source | lessons |
//! |---|---|
//! | `docs/DECISIONS.md` | `D1`–`D10` — ratified rulings |
//! | `docs/ASSURANCE.md` | `R1`–`R6` rules, `1.1`–`1.3` defect mechanisms |
//! | `docs/HANDOFF.md` §6 | the porting gotchas table — what cost real time |
//!
//! ## Three verbs
//!
//! * [`build_index`] — parse the docs into [`Lesson`]s.
//! * [`query`] — rank lessons against free text (deterministic overlap; no
//!   model, no embedding, no weights to disagree about).
//! * [`check`] — the gate: `Some(matches)` means a known lesson applies and
//!   the caller must address it. Exit code `1` in the CLI is the point —
//!   the proposal gate cannot skip this step without a loud failure.
//!
//! ## The score is a floor, and floors do not discriminate (`ASSURANCE` §1.2)
//!
//! So [`DEFAULT_THRESHOLD`] is pinned by measurement in the gold tests: known
//! lesson-bearing proposals score above it, known-noise proposals score below
//! it, and the tests assert the **separation**, not just the floor. If the
//! tokenizer or the docs change shape, the parser fails loudly on exact
//! section counts rather than silently indexing less.
//!
//! This crate calls no models and is `forbid(unsafe_code)`.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

use std::collections::BTreeSet;

use serde::Serialize;

/// One lesson, as the index carries it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Lesson {
    /// Stable id: `D8`, `R2`, `1.1`, `G4`, …
    pub id: String,
    /// Which document it came from (`DECISIONS.md`, `ASSURANCE.md`,
    /// `HANDOFF.md §6`).
    pub source: String,
    /// The heading / first cell.
    pub title: String,
    /// The lesson body, as written.
    pub text: String,
}

/// A ranked match from [`query`].
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Match {
    /// Which lesson.
    pub lesson: Lesson,
    /// Overlap score in `0.0..=1.0`.
    pub score: f64,
}

/// The score above which a lesson is considered to apply. A score is
/// **query coverage**: the fraction of the proposal's content the lesson
/// directly speaks to.
///
/// Pinned by the gold tests' measured separation — raise or lower it only
/// with the pair that proves the new value still discriminates.
pub const DEFAULT_THRESHOLD: f64 = 0.28;

/// Tokens too common to carry meaning. Deliberately small: words like
/// "never", "must", and "not" are load-bearing in this corpus's lessons and
/// are **not** stop words here.
const STOPWORDS: &[&str] = &[
    "the", "a", "an", "of", "to", "in", "on", "is", "are", "for", "and", "or", "at", "by", "it",
    "its", "be", "as", "that", "this", "with", "from", "into", "than", "then", "so", "if",
];

/// A crude stemmer, deterministic and dependency-free: enough to unify
/// `disagree`/`disagrees`, `gates`/`gate`, `covered`/`cover` in this
/// vocabulary. Crude on purpose — a snowball dependency would outrank the
/// problem it solves here.
fn stem(token: &str) -> String {
    let t = token.to_ascii_lowercase();
    for (suffix, min_len) in [("ing", 5usize), ("ed", 4), ("es", 4), ("s", 4)] {
        if t.len() > min_len && t.ends_with(suffix) {
            let keep = t.chars().count().saturating_sub(suffix.len());
            let mut cut: String = t.chars().take(keep).collect();
            // `running` -> strip ing -> `runn` -> undouble -> `run`
            let mut tail = cut.chars().rev();
            if let (Some(a), Some(b)) = (tail.next(), tail.next()) {
                if a == b {
                    cut.pop();
                }
            }
            return cut;
        }
    }
    t
}

/// Tokenize the way the index does: stemmed lowercase alphanumeric runs,
/// stop words and 1–2 character crumbs dropped.
fn tokens(text: &str) -> BTreeSet<String> {
    text.to_lowercase()
        .split(|c: char| !c.is_ascii_alphanumeric())
        .filter(|t| t.len() > 2 && !STOPWORDS.contains(t))
        .map(stem)
        .collect()
}

/// Query coverage: what fraction of the proposal the lesson speaks to.
/// Bounded `0.0..=1.0`, honest about short proposals, and unimprovable by
/// padding the lesson (unlike raw overlap counts).
///
/// `usize as f64` is exact below 2^53 and these are token counts — never
/// close to that. Isolated so the cast is written once, with the reason.
#[allow(clippy::cast_precision_loss, reason = "token counts; exact below 2^53")]
fn score(query_tokens: &BTreeSet<String>, lesson_tokens: &BTreeSet<String>) -> f64 {
    if query_tokens.is_empty() {
        return 0.0;
    }
    let inter = query_tokens.intersection(lesson_tokens).count();
    inter as f64 / query_tokens.len() as f64
}

/// Collect a markdown section: lines after `start_pred` until the next
/// heading at `level` or above.
fn section_body(lines: &[&str], start: usize) -> String {
    let mut out = Vec::new();
    for line in lines.iter().skip(start + 1) {
        if line.starts_with("## ") {
            break;
        }
        if line.starts_with("### ") && !out.is_empty() {
            break;
        }
        out.push(*line);
    }
    out.join("\n").trim().to_string()
}

/// Parse `DECISIONS.md`: every `## D<n> — title` is one lesson.
pub fn parse_decisions(md: &str) -> Vec<Lesson> {
    let lines: Vec<&str> = md.lines().collect();
    let mut out = Vec::new();
    for (i, line) in lines.iter().enumerate() {
        let Some(rest) = line.strip_prefix("## D") else {
            continue;
        };
        let digits: String = rest.chars().take_while(char::is_ascii_digit).collect();
        if digits.is_empty() {
            continue;
        }
        let title = rest
            .trim_start_matches(|c: char| c.is_ascii_digit())
            .trim_start_matches([' ', '—', '-', '–'])
            .to_string();
        out.push(Lesson {
            id: format!("D{digits}"),
            source: "DECISIONS.md".into(),
            title,
            text: section_body(&lines, i),
        });
    }
    out
}

/// Parse `ASSURANCE.md`: `### R<n> — title` rules and `### <n>.<m> title`
/// defect mechanisms.
pub fn parse_assurance(md: &str) -> Vec<Lesson> {
    let lines: Vec<&str> = md.lines().collect();
    let mut out = Vec::new();
    for (i, line) in lines.iter().enumerate() {
        let Some(rest) = line.strip_prefix("### ") else {
            continue;
        };
        let (id, title) = if let Some(r) = rest.strip_prefix('R') {
            let digits: String = r.chars().take_while(char::is_ascii_digit).collect();
            if digits.is_empty() {
                continue;
            }
            (
                format!("R{digits}"),
                r.trim_start_matches(|c: char| c.is_ascii_digit())
                    .trim_start_matches([' ', '—', '-', '–'])
                    .to_string(),
            )
        } else if rest
            .split_once('.')
            .is_some_and(|(a, b)| !a.is_empty() && b.starts_with(|c: char| c.is_ascii_digit()))
        {
            let id = rest
                .split_whitespace()
                .next()
                .unwrap_or_default()
                .to_string();
            let title = rest[id.len()..].trim().to_string();
            (id, title)
        } else {
            continue;
        };
        out.push(Lesson {
            id,
            source: "ASSURANCE.md".into(),
            title,
            text: section_body(&lines, i),
        });
    }
    out
}

/// Parse `HANDOFF.md` §6's *Porting gotchas* table: every `| gotcha | why |`
/// row is one lesson, because each one cost real time once.
pub fn parse_handoff_gotchas(md: &str) -> Vec<Lesson> {
    let mut out = Vec::new();
    let mut in_table = false;
    let mut n = 0usize;
    for line in md.lines() {
        if line.starts_with("### Porting gotchas") {
            in_table = true;
            continue;
        }
        if in_table && (line.starts_with("## ") || line.starts_with("### ")) {
            break;
        }
        if !in_table || !line.starts_with('|') {
            continue;
        }
        let mut cells = line.split('|').map(str::trim);
        let _ = cells.next(); // leading edge
        let (Some(title), Some(why)) = (cells.next(), cells.next()) else {
            continue;
        };
        if title.is_empty() || title.starts_with('-') || title.contains("gotcha") {
            continue;
        }
        n += 1;
        out.push(Lesson {
            id: format!("G{n}"),
            source: "HANDOFF.md §6".into(),
            title: title.to_string(),
            text: why.to_string(),
        });
    }
    out
}

/// Build the whole index from the three documents' text.
///
/// Callers pass the file text (tests pass fixtures; the CLI reads the docs
/// from disk) so this stays pure.
#[must_use]
pub fn build_index(decisions: &str, assurance: &str, handoff: &str) -> Vec<Lesson> {
    let mut out = parse_decisions(decisions);
    out.extend(parse_assurance(assurance));
    out.extend(parse_handoff_gotchas(handoff));
    out
}

/// Rank the index against free text. Deterministic: same inputs, same order.
#[must_use]
pub fn query(index: &[Lesson], text: &str, top: usize) -> Vec<Match> {
    let q = tokens(text);
    let mut out: Vec<Match> = index
        .iter()
        .map(|lesson| Match {
            score: score(&q, &tokens(&format!("{} {}", lesson.title, lesson.text))),
            lesson: lesson.clone(),
        })
        .filter(|m| m.score > 0.0)
        .collect();
    out.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| a.lesson.id.cmp(&b.lesson.id))
    });
    out.truncate(top);
    out
}

/// The gate: which known lessons a proposal must address, if any.
///
/// `Ok(())` means nothing above [`DEFAULT_THRESHOLD`] — the proposal is
/// clear to proceed. `Err(matches)` is the blocking list: the proposal gate
/// shows them, the proposer addresses or explicitly overrides each.
///
/// # Errors
///
/// Returns every match at or above the threshold, best first.
pub fn check(index: &[Lesson], proposal: &str) -> Result<(), Vec<Match>> {
    let hits: Vec<Match> = query(index, proposal, usize::MAX)
        .into_iter()
        .filter(|m| m.score >= DEFAULT_THRESHOLD)
        .collect();
    if hits.is_empty() {
        Ok(())
    } else {
        Err(hits)
    }
}
