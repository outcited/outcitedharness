//! Vendor ordering-code decoders — port of
//! `harness/electronics/ordering_codes.py` (`decode` / `source_label`).
//!
//! The patterns are verbatim from the reference. What is decoded is what the
//! vendor's printed scheme states — never a guess. A decoder that disagrees
//! with its own vendor's tables gets fixed or removed, never softened.

use std::sync::OnceLock;

use regex::Regex;
use serde::{Deserialize, Serialize};

/// ST and `GigaDevice` share the memory-letter and pin-letter alphabets (`GD32`
/// mirrors STM32 numbering).
fn st_memory_letter_kb(letter: &str) -> Option<i32> {
    Some(match letter {
        "4" => 16,
        "6" => 32,
        "8" => 64,
        "B" => 128,
        "C" => 256,
        "D" => 384,
        "E" => 512,
        "F" => 768,
        "G" => 1024,
        "H" => 1536,
        "I" => 2048,
        "J" => 4096,
        "K" => 3072,
        _ => return None,
    })
}

fn st_pin_letter(letter: &str) -> Option<i32> {
    Some(match letter {
        "D" => 14,
        "Y" | "F" => 20,
        "G" => 28,
        "K" => 32,
        "T" => 36,
        "S" => 44,
        "C" => 48,
        "U" => 63,
        "R" => 64,
        "M" => 81,
        "O" => 90,
        "V" => 100,
        "Q" => 132,
        "Z" => 144,
        "I" => 176,
        "A" => 169,
        "B" => 208,
        "N" => 216,
        "X" => 240,
        _ => return None,
    })
}

#[allow(
    clippy::expect_used,
    reason = "patterns are literals from the reference"
)]
fn re(src: &str) -> Regex {
    Regex::new(src).expect("pattern is a literal from the reference")
}

fn st_part() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        re(r"^STM32(?:[A-Z]\d[A-Z0-9]\d|[A-Z]{2}\d{2}|[A-Z]{3}\d{1,2}|[A-Z]{3}\d)([A-Z])([0-9A-Z])(?:[A-Z0-9]*)$")
    })
}

fn gd_part() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| re(r"^GD32(?:[A-Z]{1,2}\d{3})([A-Z])([0-9A-Z])(?:[A-Z]\d)?$"))
}

fn silabs_32() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        re(r"^EF[MR]32[A-Z]{2}\d{1,2}[A-Z]\d{3}F(\d{2,4})[GI][MQLJ](\d{2,3})(?:-[A-Z])?$")
    })
}

fn silabs_8() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| {
        re(r"^EFM8[A-Z]{2}\d{1,2}F(\d{1,3})[A-Z]-[A-Z]-\d?(?:QFN|QSOP|SOIC|TSSOP)(\d{2})")
    })
}

fn avr_dx() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| re(r"^AVR(\d{2,3})(?:DA|DB|DD|DU|EA|EB)(\d{2})$"))
}

fn attiny() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| re(r"(?i)^ATtiny(\d{1,2})([012])([2467])$"))
}

fn atmega0() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| re(r"(?i)^ATmega(\d{1,2})0[89]$"))
}

fn pic32() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| re(r"^PIC32M[XZK]\d{3}F(\d{2,4})[A-Z]"))
}

fn pic24fj() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| re(r"^PIC24FJ(\d{2,4})G[ABCU]\d{3}$"))
}

fn ra() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| re(r"^R7FA\d[A-Z]\d[A-Z0-9]([3579BDFH])\d[A-Z][A-Z]{2}$"))
}

fn attiny_pins(code: &str) -> Option<i32> {
    Some(match code {
        "2" => 8,
        "4" => 14,
        "6" => 20,
        "7" => 24,
        _ => return None,
    })
}

fn pic32_pins(letter: &str) -> Option<i32> {
    Some(match letter {
        "B" => 28,
        "C" => 36,
        "D" => 44,
        "H" => 64,
        "L" => 100,
        _ => return None,
    })
}

fn ra_flash(code: &str) -> Option<i32> {
    Some(match code {
        "3" => 16,
        "5" => 32,
        "7" => 64,
        "9" => 128,
        "B" => 256,
        "D" => 512,
        "F" => 1024,
        "H" => 2048,
        _ => return None,
    })
}

/// What the vendor's printed ordering scheme states. Keys are omitted when
/// the scheme does not print them — the wire mirrors the reference exactly
/// (and `serde(default)` keeps it round-trippable: the §6 skip/default rule).
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct OrderingFacts {
    /// Which scheme decoded it (`stm32`, `gd32`, …).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub scheme: Option<String>,
    /// Flash size in KB, as the scheme prints it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub flash_kb: Option<i32>,
    /// Exact pin count, when the scheme states one.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pin_count: Option<i32>,
    /// ST's pin letter is the **package class**, not the exact ball count —
    /// corroborates when equal, never contradicts.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub pin_count_nominal: Option<i32>,
}

impl OrderingFacts {
    fn scheme(mut self, s: &str) -> Self {
        self.scheme = Some(s.to_string());
        self
    }
}

/// Decode what the vendor's printed ordering scheme states for a part.
/// Empty when the part matches no unambiguous scheme — the honest answer.
///
/// # Lints
///
/// One dispatch over the schemes **in reference order** — order is
/// semantics (first match wins), so the list stays whole.
#[must_use]
#[allow(clippy::too_many_lines, reason = "reference-ordered scheme dispatch")]
pub fn decode(part_number: Option<&str>) -> OrderingFacts {
    let Some(part_number) = part_number else {
        return OrderingFacts::default();
    };
    let part = part_number.trim();
    if part.is_empty() {
        return OrderingFacts::default();
    }

    if part.starts_with("STM32")
        && !part.starts_with("STM32WB")
        && !part.starts_with("STM32MP")
        && !part.starts_with("STM32N")
        && !part.starts_with("STM32WL")
    {
        let Some(m) = st_part().captures(part) else {
            return OrderingFacts::default();
        };
        let mut out = OrderingFacts::default().scheme("stm32");
        if let Some(flash) = m.get(2).and_then(|x| st_memory_letter_kb(x.as_str())) {
            out.flash_kb = Some(flash);
        }
        if let Some(pins) = m.get(1).and_then(|x| st_pin_letter(x.as_str())) {
            out.pin_count_nominal = Some(pins);
        }
        return out;
    }

    if part.starts_with("GD32") {
        let Some(m) = gd_part().captures(part) else {
            return OrderingFacts::default();
        };
        let mut out = OrderingFacts::default().scheme("gd32");
        if let Some(flash) = m.get(2).and_then(|x| st_memory_letter_kb(x.as_str())) {
            out.flash_kb = Some(flash);
        }
        if let Some(pins) = m.get(1).and_then(|x| st_pin_letter(x.as_str())) {
            out.pin_count = Some(pins);
        }
        return out;
    }

    if let Some(m) = silabs_32().captures(part) {
        return OrderingFacts {
            scheme: Some("silabs_efm32_efr32".into()),
            flash_kb: m.get(1).and_then(|g| g.as_str().parse().ok()),
            pin_count: m.get(2).and_then(|g| g.as_str().parse().ok()),
            pin_count_nominal: None,
        };
    }
    if let Some(m) = silabs_8().captures(part) {
        return OrderingFacts {
            scheme: Some("silabs_efm8".into()),
            flash_kb: m.get(1).and_then(|g| g.as_str().parse().ok()),
            pin_count: m.get(2).and_then(|g| g.as_str().parse().ok()),
            pin_count_nominal: None,
        };
    }
    if let Some(m) = avr_dx().captures(part) {
        return OrderingFacts {
            scheme: Some("avr_dx".into()),
            flash_kb: m.get(1).and_then(|g| g.as_str().parse().ok()),
            pin_count: m.get(2).and_then(|g| g.as_str().parse().ok()),
            pin_count_nominal: None,
        };
    }
    if let Some(m) = attiny().captures(part) {
        return OrderingFacts {
            scheme: Some("tinyavr".into()),
            flash_kb: m.get(1).and_then(|g| g.as_str().parse().ok()),
            pin_count: m.get(3).and_then(|g| attiny_pins(g.as_str())),
            pin_count_nominal: None,
        };
    }
    if let Some(m) = atmega0().captures(part) {
        return OrderingFacts {
            scheme: Some("megaavr0".into()),
            flash_kb: m.get(1).and_then(|g| g.as_str().parse().ok()),
            pin_count: None,
            pin_count_nominal: None,
        };
    }
    if let Some(m) = pic32().captures(part) {
        let mut out = OrderingFacts {
            scheme: Some("pic32".into()),
            flash_kb: m.get(1).and_then(|g| g.as_str().parse().ok()),
            pin_count: None,
            pin_count_nominal: None,
        };
        // The reference reads the pin letter at `match.end() - 1`: the one
        // `[A-Z]` the pattern consumes after the flash group.
        let letter = m
            .get(0)
            .and_then(|whole| whole.as_str().chars().next_back().map(String::from));
        if let Some(l) = letter {
            out.pin_count = pic32_pins(l.as_str());
        }
        return out;
    }
    if let Some(m) = pic24fj().captures(part) {
        return OrderingFacts {
            scheme: Some("pic24fj".into()),
            flash_kb: m.get(1).and_then(|g| g.as_str().parse().ok()),
            pin_count: None,
            pin_count_nominal: None,
        };
    }
    if let Some(m) = ra().captures(part) {
        return OrderingFacts {
            scheme: Some("renesas_ra".into()),
            flash_kb: m.get(1).and_then(|g| ra_flash(g.as_str())),
            pin_count: None,
            pin_count_nominal: None,
        };
    }

    OrderingFacts::default()
}

/// Provenance tag for a decoded value.
#[must_use]
pub fn source_label(decoded: &OrderingFacts, key: &str) -> String {
    let scheme = decoded.scheme.as_deref().unwrap_or("vendor");
    format!("ordering_code:{scheme}:{key}")
}
