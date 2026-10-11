//! Typed facts — `typed_facts` and the per-group grammars (`_core_facts`,
//! `_memory_facts`, …). Nothing inferred: every kind needs its anchor word.

use serde_json::{json, Value};

use super::helpers::{captures, find_all, is_match, num_as_i64, search};
use super::tables::{
    BITRATE, BITS, CHANNELS, CONVERSION_TIME, COREMARK, CORE_MHZ, CORE_NAME, CORE_WIDTH, CURRENT,
    CURRENT_PER_MHZ, DMIPS, FPU, FPU_PRECISION, LCD_SEG, LP_MODE, MEMORY_KIND, MODE_WORDS,
    MULTI_CORE, OSC, OSC_FREQ, PROTOCOL_FLAGS, PWM_CH, RESOLUTION_PX, SAMPLE_RATE, SECURITY_VOCAB,
    TIMER_CHANNELS, TIMER_MHZ, TOUCH_CH, USB_SPEED, VREF, WAKEUP,
};

/// `typed_facts` — grammar-derived typed facts for one grid row.
#[must_use]
pub fn typed_facts(row: &Value) -> Value {
    let label = row.get("label").and_then(Value::as_str).unwrap_or("");
    let verbatim = row.get("verbatim").and_then(Value::as_str).unwrap_or("");
    // U+2014 em dash joins the two, exactly as the reference formats it.
    let text = format!("{label} \u{2014} {verbatim}");
    let cls = row
        .get("peripheral_class")
        .and_then(Value::as_str)
        .unwrap_or("");
    let group = row.get("group").and_then(Value::as_str).unwrap_or("");
    let mut facts: Vec<Value> = Vec::new();
    match group {
        "processing" => core_facts(&text, row, &mut facts),
        "memory" => memory_facts(&text, row, &mut facts),
        "analogue" => analogue_facts(&text, row, cls, &mut facts),
        "timers_pwm_control" => timer_facts(&text, row, cls, &mut facts),
        "power_clock_reset" => power_facts(&text, row, &mut facts),
        "security_safety_identity" => security_facts(&text, &mut facts),
        "connectivity" => connectivity_facts(&text, row, cls, &mut facts),
        "graphics_vision_touch_hmi" => graphics_facts(&text, row, cls, &mut facts),
        _ => {}
    }
    Value::Array(facts)
}

fn fact(pairs: Vec<(&str, Value)>) -> Value {
    serde_json::Map::from_iter(pairs.into_iter().map(|(k, v)| (k.to_string(), v))).into()
}

fn yes() -> Value {
    json!(true)
}

/// `_n` — integer-valued floats print as ints.
///
/// # Lints
///
/// `float_cmp` on `value == value.trunc()` is the exact integer-valued test
/// the reference's `float.is_integer()` performs; an epsilon here would
/// change which numbers become JSON ints. The `f64 -> i64` narrowing is
/// guarded by the `9e15` bound (the exact-integer limit) in the branch.
#[allow(clippy::float_cmp, clippy::cast_possible_truncation)]
fn n(text: &str) -> Value {
    let value: f64 = text.trim().parse().unwrap_or_default();
    if value.is_finite() && value == value.trunc() && value.abs() < 9_007_199_254_740_992.0 {
        json!(value as i64)
    } else if value.is_finite() {
        json!(value)
    } else {
        Value::Null
    }
}

/// `_n` over an already-parsed number (`round(m * scale, 4)` results).
#[allow(clippy::float_cmp, clippy::cast_possible_truncation)]
fn n_f64(value: f64) -> Value {
    if value.is_finite() && value == value.trunc() && value.abs() < 9_007_199_254_740_992.0 {
        json!(value as i64)
    } else if value.is_finite() {
        json!(value)
    } else {
        Value::Null
    }
}

/// `round(m * scale, 4)` — Python rounds the binary value half-to-even to 4
/// decimals, then `str()` then `_n()` re-parses; `format!("{:.4}")` uses the
/// same rounding, so format-then-parse reproduces it.
fn round4(value: f64) -> f64 {
    format!("{value:.4}").parse().unwrap_or(value)
}

// ------------------------------------------------------------- processing

/// # Lints
///
/// Too long because the reference grammar is long; split points are the
/// reference's own helper seams and moving code between them would obscure
/// the port. Bounded here and nowhere else.
#[allow(clippy::too_many_lines)]
fn core_facts(text: &str, row: &Value, out: &mut Vec<Value>) {
    let core = search(CORE_NAME, text);
    if let Some(printed) = core {
        let cleaned: String = printed
            .chars()
            .filter(|c| !matches!(c, '®' | '™'))
            .collect();
        let joined: String = printed
            .chars()
            .filter(|c| !matches!(c, '®' | '™' | ' '))
            .collect();
        let normalized = joined
            .to_uppercase()
            .replace("CORTEX-", "Cortex-")
            .replace("CORTEX", "Cortex");
        out.push(fact(vec![
            ("kind", json!("core_name")),
            ("value", json!(cleaned)),
            ("value_normalized", json!(normalized)),
        ]));
    }
    if let Some(caps) = captures(CORE_MHZ, text) {
        let hit = caps.first().cloned().flatten().unwrap_or_default();
        let num = caps.get(1).cloned().flatten().unwrap_or_default();
        if !is_match(
            r"(?i)oscillator|crystal|\bRC\b|\bHSI\b|\bHSE\b|\bLSE\b|\bLSI\b|\bIRC\b|\bPLL\b|flash|SPI|USB|Ethernet|timer|ADC|bus|APB|AHB",
            &hit,
        ) {
            let value = n(&num);
            // i64->f64 is safe in the 1..=3000 band under test.
            #[allow(clippy::cast_precision_loss)]
            let as_real = value.as_f64().or_else(|| value.as_i64().map(|i| i as f64));
            if as_real.is_some_and(|v| (1.0..=3000.0).contains(&v)) {
                let qualifier = if is_match(r"(?i)up\s+to|maximum|max\.?", text) {
                    json!("up to")
                } else {
                    Value::Null
                };
                out.push(fact(vec![
                    ("kind", json!("core_max_mhz")),
                    ("value", value),
                    ("qualifier_verbatim", qualifier),
                ]));
            }
        }
    }
    if let Some(caps) = captures(DMIPS, text) {
        let g1 = caps.get(1).cloned().flatten();
        let g2 = caps.get(2).cloned().flatten();
        if let Some(v) = g1 {
            out.push(fact(vec![("kind", json!("dmips")), ("value", n(&v))]));
        } else if let Some(v) = g2 {
            out.push(fact(vec![
                ("kind", json!("dmips_per_mhz")),
                ("value", n(&v)),
            ]));
        }
    }
    if let Some(caps) = captures(COREMARK, text) {
        if let Some(v) = caps.get(1).cloned().flatten() {
            out.push(fact(vec![("kind", json!("coremark")), ("value", n(&v))]));
        }
    }
    if is_match(FPU, text) {
        let precision = captures(FPU_PRECISION, text)
            .and_then(|c| c.get(1).cloned().flatten())
            .map(|p| p.to_lowercase());
        out.push(fact(vec![
            ("kind", json!("fpu")),
            ("value", yes()),
            ("precision", precision.map_or(Value::Null, Value::String)),
        ]));
    }
    if is_match(r"(?i)\bMPU\b|memory\s+protection\s+unit", text) {
        out.push(fact(vec![("kind", json!("mpu")), ("value", yes())]));
    }
    if is_match(
        r"(?i)\bDSP\b(?!\s*/)|DSP\s+instructions|digital\s+signal\s+processing",
        text,
    ) {
        out.push(fact(vec![
            ("kind", json!("dsp_extension")),
            ("value", yes()),
        ]));
    }
    if is_match(r"(?i)TrustZone", text) {
        out.push(fact(vec![("kind", json!("trustzone")), ("value", yes())]));
    }
    if let Some(caps) = captures(MULTI_CORE, text) {
        let word = caps
            .get(1)
            .cloned()
            .flatten()
            .or_else(|| caps.get(2).cloned().flatten())
            .unwrap_or_default()
            .to_lowercase();
        let count = match word.as_str() {
            "dual" | "two" | "2" => 2,
            "triple" | "three" | "3" => 3,
            "quad" | "four" | "4" => 4,
            _ => 0,
        };
        out.push(fact(vec![
            ("kind", json!("core_count")),
            ("value", json!(count)),
        ]));
    }
    if let Some(caps) = captures(CORE_WIDTH, text) {
        if let (Some(bits), true) = (caps.get(1).cloned().flatten(), core_name_present(text)) {
            out.push(fact(vec![
                ("kind", json!("core_width_bits")),
                ("value", json!(bits.parse::<u64>().unwrap_or(0))),
            ]));
        }
    }
    let _ = row;
}

fn core_name_present(text: &str) -> bool {
    search(CORE_NAME, text).is_some()
}

// ----------------------------------------------------------------- memory

#[allow(clippy::too_many_lines)]
fn memory_facts(text: &str, row: &Value, out: &mut Vec<Value>) {
    let unit = row
        .get("unit")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_lowercase();
    let scale = match unit.as_str() {
        "kb" | "kbytes" | "kbyte" => 1.0,
        "mb" | "mbytes" | "mbyte" => 1024.0,
        "gb" => 1024.0 * 1024.0,
        "bytes" | "byte" | "b" => 1.0 / 1024.0,
        "kbit" => 1.0 / 8.0,
        "mbit" => 128.0,
        _ => return,
    };
    let Some(value) = row.get("value").filter(|v| **v != Value::Null) else {
        return;
    };
    let members: Vec<Value> = match value {
        Value::Array(items) => items.clone(),
        other => vec![other.clone()],
    };
    if !members
        .iter()
        .all(|m| m.is_number() || m.as_f64().is_some())
    {
        return;
    }
    let kind = MEMORY_KIND
        .iter()
        .find(|(_, pattern)| is_match(pattern, text))
        .map(|(name, _)| *name);
    let Some(kind) = kind else {
        return;
    };
    let sizes: Vec<Value> = members
        .iter()
        .filter_map(|m| m.as_f64().map(|f| n_f64(round4(f * scale))))
        .collect();
    let sized_value = if value.is_array() {
        Value::Array(sizes)
    } else {
        sizes.first().cloned().unwrap_or(Value::Null)
    };
    out.push(fact(vec![
        ("kind", json!(format!("{kind}_kb"))),
        ("value", sized_value),
        (
            "quantity_qualifier",
            row.get("quantity_qualifier")
                .cloned()
                .unwrap_or(Value::Null),
        ),
        (
            "qualifier_verbatim",
            row.get("qualifier_verbatim")
                .cloned()
                .unwrap_or(Value::Null),
        ),
        (
            "varies_by_part",
            json!(row.get("varies_by_part").is_some_and(|v| *v == json!(true))),
        ),
    ]));
}

// --------------------------------------------------------------- analogue

#[allow(clippy::too_many_lines)]
fn analogue_facts(text: &str, row: &Value, cls: &str, out: &mut Vec<Value>) {
    let adc_like = is_match(r"(?i)\bADC|A/D|S12AD|analog", text);
    let dac_like = is_match(r"(?i)\bDAC|D/A", text);
    if let Some(caps) = captures(BITS, text) {
        let kind = if dac_like {
            Some("dac")
        } else if is_match(
            r"(?i)\bADC|A/D|S12AD|analog[- ]to[- ]digital|SAR|sigma",
            text,
        ) {
            Some("adc")
        } else if cls.is_empty() {
            None
        } else {
            Some(cls)
        };
        if matches!(kind, Some("adc" | "dac"))
            && matches!(cls, "adc" | "dac" | "comparator" | "opamp" | "")
        {
            if let (Some(kind), Some(bits)) = (kind, caps.get(1).cloned().flatten()) {
                out.push(fact(vec![
                    ("kind", json!(format!("{kind}_resolution_bits"))),
                    ("value", json!(bits.parse::<u64>().unwrap_or(0))),
                    (
                        "count",
                        row.get("instances").cloned().unwrap_or(Value::Null),
                    ),
                ]));
            }
        }
    }
    if let Some(caps) = captures(CHANNELS, text) {
        let qualifier = caps
            .first()
            .cloned()
            .flatten()
            .is_some_and(|hit| is_match(r"(?i)up\s+to", &hit));
        let qualifier = if qualifier {
            json!("Up to")
        } else {
            Value::Null
        };
        if adc_like && !dac_like {
            if let Some(v) = caps.get(1).cloned().flatten() {
                out.push(fact(vec![
                    ("kind", json!("adc_channels")),
                    ("value", json!(v.parse::<u64>().unwrap_or(0))),
                    ("qualifier_verbatim", qualifier),
                ]));
            }
        } else if dac_like {
            if let Some(v) = caps.get(1).cloned().flatten() {
                out.push(fact(vec![
                    ("kind", json!("dac_channels")),
                    ("value", json!(v.parse::<u64>().unwrap_or(0))),
                    ("qualifier_verbatim", qualifier),
                ]));
            }
        }
    }
    if let Some(caps) = captures(SAMPLE_RATE, text) {
        if let (Some(v), Some(u)) = (
            caps.get(1).cloned().flatten(),
            caps.get(2).cloned().flatten(),
        ) {
            out.push(fact(vec![
                ("kind", json!("adc_sample_rate")),
                ("value", n(&v)),
                ("unit", json!(u)),
            ]));
        }
    }
    if let Some(caps) = captures(CONVERSION_TIME, text) {
        if adc_like {
            let value = caps
                .get(1)
                .cloned()
                .flatten()
                .or_else(|| caps.get(3).cloned().flatten());
            let unit = caps
                .get(2)
                .cloned()
                .flatten()
                .or_else(|| caps.get(4).cloned().flatten());
            if let (Some(v), Some(u)) = (value, unit) {
                out.push(fact(vec![
                    ("kind", json!("adc_conversion_time")),
                    ("value", n(&v)),
                    ("unit", json!(u.replace(['u', '\u{3bc}'], "\u{b5}"))),
                ]));
            }
        }
    }
    if let Some(caps) = captures(VREF, text) {
        let v = caps
            .get(1)
            .cloned()
            .flatten()
            .or_else(|| caps.get(2).cloned().flatten());
        if let Some(v) = v {
            out.push(fact(vec![
                ("kind", json!("internal_reference_v")),
                ("value", n(&v)),
            ]));
        }
    }
    if is_match(r"(?i)temp(?:erature)?\s+sensor", text) {
        out.push(fact(vec![
            ("kind", json!("temperature_sensor")),
            ("value", yes()),
        ]));
    }
    if row.get("instances").is_some_and(|i| *i != Value::Null)
        && matches!(cls, "adc" | "dac" | "comparator" | "opamp")
    {
        out.push(fact(vec![
            ("kind", json!(format!("{cls}_instances"))),
            (
                "value",
                row.get("instances").cloned().unwrap_or(Value::Null),
            ),
        ]));
    }
}

// -------------------------------------------------------- timers and PWM

#[allow(clippy::too_many_lines)]
fn timer_facts(text: &str, row: &Value, cls: &str, out: &mut Vec<Value>) {
    if row.get("instances").is_some_and(|i| *i != Value::Null)
        && matches!(cls, "timer" | "lptim" | "hrtim" | "rtc" | "watchdog")
    {
        out.push(fact(vec![
            ("kind", json!(format!("{cls}_instances"))),
            (
                "value",
                row.get("instances").cloned().unwrap_or(Value::Null),
            ),
        ]));
    }
    if let Some(caps) = captures(BITS, text) {
        if matches!(cls, "timer" | "lptim" | "hrtim") {
            if let Some(bits) = caps.get(1).cloned().flatten() {
                out.push(fact(vec![
                    ("kind", json!("timer_width_bits")),
                    ("value", json!(bits.parse::<u64>().unwrap_or(0))),
                    (
                        "count",
                        row.get("instances").cloned().unwrap_or(Value::Null),
                    ),
                ]));
            }
        }
    }
    if let Some(caps) = captures(PWM_CH, text) {
        let v = caps
            .get(1)
            .cloned()
            .flatten()
            .or_else(|| caps.get(2).cloned().flatten());
        if let Some(v) = v {
            let qualifier = if is_match(r"(?i)up\s+to", text) {
                json!("Up to")
            } else {
                Value::Null
            };
            out.push(fact(vec![
                ("kind", json!("pwm_channels")),
                ("value", json!(v.parse::<u64>().unwrap_or(0))),
                ("qualifier_verbatim", qualifier),
            ]));
        }
    }
    if let Some(caps) = captures(TIMER_CHANNELS, text) {
        if matches!(cls, "timer" | "lptim" | "hrtim" | "") {
            let lo = caps
                .get(1)
                .cloned()
                .flatten()
                .or_else(|| caps.get(3).cloned().flatten())
                .and_then(|s| s.parse::<u64>().ok())
                .unwrap_or(0);
            let hi = caps
                .get(2)
                .cloned()
                .flatten()
                .or_else(|| caps.get(4).cloned().flatten())
                .and_then(|s| s.parse::<u64>().ok());
            let value = match hi {
                Some(h) => json!([lo, h]),
                None => json!(lo),
            };
            out.push(fact(vec![
                ("kind", json!("timer_channels")),
                ("value", value),
                ("per_instance", yes()),
            ]));
        }
    }
    if let Some(caps) = captures(TIMER_MHZ, text) {
        if matches!(cls, "timer" | "lptim" | "hrtim") {
            if let Some(v) = caps.get(1).cloned().flatten() {
                let qualifier = if is_match(r"(?i)up\s+to", text) {
                    json!("up to")
                } else {
                    Value::Null
                };
                out.push(fact(vec![
                    ("kind", json!("timer_clock_mhz")),
                    ("value", json!(v.parse::<u64>().unwrap_or(0))),
                    ("qualifier_verbatim", qualifier),
                ]));
            }
        }
    }
    if cls == "rtc" || is_match(r"(?i)\bRTC\b|real[- ]time\s+clock", text) {
        if is_match(r"(?i)calendar", text) {
            out.push(fact(vec![
                ("kind", json!("rtc_calendar")),
                ("value", yes()),
            ]));
        }
        if is_match(r"(?i)sub-?second|tamper|alarm", text) {
            let mut features: Vec<String> = find_all(r"(?i)sub-?second|tamper|alarms?", text)
                .iter()
                .map(|m| m.to_lowercase().replace('-', ""))
                .collect::<std::collections::BTreeSet<String>>()
                .into_iter()
                .collect();
            features.sort();
            out.push(fact(vec![
                ("kind", json!("rtc_features")),
                (
                    "value",
                    Value::Array(features.into_iter().map(Value::String).collect()),
                ),
            ]));
        }
    }
    if is_match(
        r"(?i)motor[- ]control|complementary\s+outputs?|dead[- ]time",
        text,
    ) && matches!(cls, "timer" | "hrtim" | "")
    {
        out.push(fact(vec![
            ("kind", json!("motor_control_timer")),
            ("value", yes()),
        ]));
    }
    if is_match(r"(?i)quadrature|encoder\s+interface|\bQEI\b|\bQDEC\b", text) {
        out.push(fact(vec![
            ("kind", json!("encoder_interface")),
            ("value", yes()),
        ]));
    }
}

// -------------------------------------------------- power, clock and reset

#[allow(clippy::too_many_lines)]
fn power_facts(text: &str, row: &Value, out: &mut Vec<Value>) {
    let mut oscillator_count = 0;
    for hit in find_all(OSC, text) {
        let whole = hit.clone();
        let caps = captures(OSC, &whole);
        let name = caps
            .as_ref()
            .and_then(|c| c.get(1).cloned().flatten())
            .unwrap_or_else(|| whole.clone());
        let start = text.find(&whole).unwrap_or(0);
        let end = (start + whole.len() + 40).min(text.len());
        let win_start = start.saturating_sub(30);
        let window = text.get(start..end).unwrap_or_default().to_string();
        let before = text.get(win_start..start).unwrap_or_default().to_string();
        let freq = search(OSC_FREQ, &window).or_else(|| search(OSC_FREQ, &before));
        let mut pairs = vec![
            ("kind", json!("oscillator")),
            (
                "name",
                json!(name.split_whitespace().collect::<Vec<_>>().join(" ")),
            ),
        ];
        let probe = format!("{} {}", name, window.chars().take(20).collect::<String>());
        let internal = is_match(
            r"(?i)internal|on-chip|\bRC\b|HSI|LSI|MSI|CSI|IRC|LOCO|HOCO|MOCO|FRO|SIRC|FIRC|HFRCO|LFRCO|ULFRCO|DFLL|OSC8M|OSC16M|OSC48M",
            &probe,
        );
        pairs.push(("internal", if internal { yes() } else { Value::Null }));
        if let Some(hit) = freq {
            if let Some(fcap) = captures(OSC_FREQ, &hit) {
                pairs.push((
                    "value",
                    fcap.get(1)
                        .cloned()
                        .flatten()
                        .map_or(Value::Null, |v| n(&v)),
                ));
                pairs.push((
                    "unit",
                    fcap.get(2)
                        .cloned()
                        .flatten()
                        .map_or(Value::Null, Value::String),
                ));
            }
        }
        out.push(fact(pairs));
        oscillator_count += 1;
        if oscillator_count >= 6 {
            break;
        }
    }
    let mut modes: Vec<String> = find_all(LP_MODE, text)
        .iter()
        .map(|m| m.split_whitespace().collect::<Vec<_>>().join(" "))
        .collect::<std::collections::BTreeSet<String>>()
        .into_iter()
        .collect();
    // `sorted(..., key=str.lower)`; ties keep set order — rare (two spellings
    // differing only in case) and recorded as the one known divergence.
    modes.sort_by_key(|m| m.to_lowercase());
    if !modes.is_empty() && is_match(r"(?i)mode|power|energy|sleep|stop|standby", text) {
        out.push(fact(vec![
            ("kind", json!("low_power_modes")),
            (
                "value",
                Value::Array(modes.into_iter().map(Value::String).collect()),
            ),
        ]));
    }
    for (kind, pattern) in [
        ("brown_out_reset", r"(?i)brown-?out|\bBOR\b|\bBOD\b"),
        (
            "voltage_detector",
            r"(?i)\bPVD\b|programmable\s+voltage\s+detector|\bLVD\b|low[- ]voltage\s+detect",
        ),
        ("power_on_reset", r"(?i)\bPOR\b|power[- ]on\s+reset"),
        (
            "vbat_domain",
            r"(?i)\bVBAT\b|battery\s+backup|backup\s+domain",
        ),
        (
            "integrated_smps",
            r"(?i)\bSMPS\b|\bDC-?DC\b|step[- ]down\s+converter|buck\s+converter",
        ),
        ("integrated_ldo", r"(?i)\bLDO\b|voltage\s+regulator"),
    ] {
        if is_match(pattern, text) {
            out.push(fact(vec![(("kind"), json!(kind)), ("value", yes())]));
        }
    }
    let per_mhz = find_span(CURRENT_PER_MHZ, text);
    if let Some((start, hit)) = &per_mhz {
        if let Some(v) = captures(CURRENT_PER_MHZ, hit).and_then(|c| c.get(1).cloned().flatten()) {
            let _ = start;
            out.push(fact(vec![
                ("kind", json!("active_current_per_mhz")),
                ("value", n(&v)),
                ("unit", json!("\u{b5}A/MHz")),
            ]));
        }
    }
    for hit in find_all(CURRENT, text) {
        let caps = captures(CURRENT, &hit);
        let Some(caps) = caps else { continue };
        if per_mhz.as_ref().is_some_and(|(_, h)| h == &hit) {
            // The per-MHz match is emitted once above, not again as a mode.
            continue;
        }
        let value = caps.get(1).cloned().flatten().unwrap_or_default();
        let unit = caps.get(2).cloned().flatten().unwrap_or_default();
        let span = find_span_at(CURRENT, text, &hit);
        let window = match span {
            Some((s, e)) => text
                .get(s.saturating_sub(70)..(e + 70).min(text.len()))
                .unwrap_or("")
                .trim()
                .to_string(),
            None => hit.clone(),
        };
        let mode = captures(MODE_WORDS, &window).and_then(|c| c.get(1).cloned().flatten());
        out.push(fact(vec![
            ("kind", json!("current_in_mode")),
            ("value", n(&value)),
            ("unit", json!(unit.replace(['u', '\u{3bc}'], "\u{b5}"))),
            ("mode_verbatim", mode.map_or(Value::Null, Value::String)),
            (
                "condition_verbatim",
                json!(window.chars().take(140).collect::<String>()),
            ),
        ]));
    }
    if let Some(caps) = captures(WAKEUP, text) {
        let value = caps
            .get(1)
            .cloned()
            .flatten()
            .or_else(|| caps.get(3).cloned().flatten());
        let unit = caps
            .get(2)
            .cloned()
            .flatten()
            .or_else(|| caps.get(4).cloned().flatten());
        if let (Some(v), Some(u)) = (value, unit) {
            let whole = caps.first().cloned().flatten().unwrap_or_default();
            out.push(fact(vec![
                ("kind", json!("wakeup_time")),
                ("value", n(&v)),
                ("unit", json!(u.replace(['u', '\u{3bc}'], "\u{b5}"))),
                (
                    "condition_verbatim",
                    json!(whole.chars().take(120).collect::<String>()),
                ),
            ]));
        }
    }
    if row.get("section").and_then(Value::as_str) == Some("supply_range")
        && row.get("value").is_some_and(Value::is_array)
    {
        out.push(fact(vec![
            ("kind", json!("supply_range_v")),
            ("value", row.get("value").cloned().unwrap_or(Value::Null)),
            (
                "quantity_qualifier",
                row.get("quantity_qualifier")
                    .cloned()
                    .unwrap_or(Value::Null),
            ),
        ]));
    }
}

fn find_span(pattern: &str, text: &str) -> Option<(usize, String)> {
    let re = fancy_regex::Regex::new(pattern).ok()?;
    let m = re.find(text).ok().flatten()?;
    Some((m.start(), m.as_str().to_string()))
}

fn find_span_at(pattern: &str, text: &str, hit: &str) -> Option<(usize, usize)> {
    let re = fancy_regex::Regex::new(pattern).ok()?;
    for m in re.find_iter(text).filter_map(Result::ok) {
        if m.as_str() == hit {
            return Some((m.start(), m.end()));
        }
    }
    None
}

// --------------------------------------------------------------- security

fn security_facts(text: &str, out: &mut Vec<Value>) {
    for (name, pattern) in SECURITY_VOCAB {
        if let Some(hit) = search(pattern, text) {
            out.push(fact(vec![
                ("kind", json!("security_function")),
                ("value", json!(name)),
                ("verbatim", json!(hit)),
            ]));
        }
    }
}

// ----------------------------------------------------------- connectivity

#[allow(clippy::too_many_lines)]
fn connectivity_facts(text: &str, row: &Value, cls: &str, out: &mut Vec<Value>) {
    if row.get("instances").is_some_and(|i| *i != Value::Null) && !cls.is_empty() {
        out.push(fact(vec![
            ("kind", json!(format!("{cls}_instances"))),
            (
                "value",
                row.get("instances").cloned().unwrap_or(Value::Null),
            ),
        ]));
    }
    if let Some(caps) = captures(BITRATE, text) {
        if let (Some(v), Some(u)) = (
            caps.get(1).cloned().flatten(),
            caps.get(2).cloned().flatten(),
        ) {
            out.push(fact(vec![
                ("kind", json!("bit_rate")),
                ("value", n(&v)),
                ("unit", json!(u)),
                (
                    "peripheral_class",
                    if cls.is_empty() {
                        Value::Null
                    } else {
                        json!(cls)
                    },
                ),
            ]));
        }
    }
    // `USB` here is case-sensitive in the reference (`\bUSB\b`, no re.I).
    if cls == "usb" || is_match(r"\bUSB\b", text) {
        if let Some(caps) = captures(USB_SPEED, text) {
            let value = caps
                .get(1)
                .cloned()
                .flatten()
                .unwrap_or_default()
                .to_lowercase()
                .replace(' ', "-");
            out.push(fact(vec![
                ("kind", json!("usb_speed")),
                ("value", json!(value)),
            ]));
        }
        if is_match(r"(?i)\bOTG\b|on-the-go", text) {
            out.push(fact(vec![("kind", json!("usb_otg")), ("value", yes())]));
        }
    }
    let mut can_fd_already = false;
    if (cls == "can" || is_match(r"\bCAN\b", text))
        && is_match(r"(?i)CAN[- ]?FD|FDCAN|flexible\s+data", text)
    {
        can_fd_already = true;
        out.push(fact(vec![("kind", json!("can_fd")), ("value", yes())]));
    }
    if cls == "ethernet" || is_match(r"(?i)Ethernet|\bEMAC\b|\bETH\b", text) {
        if is_match(r"10/100|100\s*Mb", text) {
            out.push(fact(vec![
                ("kind", json!("ethernet_speed")),
                ("value", json!("10/100")),
            ]));
        }
        if is_match(r"(?i)gigabit|1000\s*Mb|\bGb\b", text) {
            out.push(fact(vec![
                ("kind", json!("ethernet_speed")),
                ("value", json!("gigabit")),
            ]));
        }
    }
    for (flag, pattern) in PROTOCOL_FLAGS {
        if *flag == "can_fd" && can_fd_already {
            continue;
        }
        if is_match(pattern, text) {
            out.push(fact(vec![
                ("kind", json!("protocol_support")),
                ("value", json!(flag)),
                (
                    "peripheral_class",
                    if cls.is_empty() {
                        Value::Null
                    } else {
                        json!(cls)
                    },
                ),
            ]));
        }
    }
}

// ------------------------------------------------ graphics, vision and HMI

#[allow(clippy::too_many_lines)]
fn graphics_facts(text: &str, row: &Value, cls: &str, out: &mut Vec<Value>) {
    if let Some(caps) = captures(LCD_SEG, text) {
        if let (Some(a), Some(b)) = (
            caps.get(1).cloned().flatten(),
            caps.get(2).cloned().flatten(),
        ) {
            out.push(fact(vec![
                ("kind", json!("lcd_segments")),
                (
                    "value",
                    json!([a.parse::<u64>().unwrap_or(0), b.parse::<u64>().unwrap_or(0)]),
                ),
            ]));
        }
    }
    if let Some(caps) = captures(TOUCH_CH, text) {
        let v = caps
            .get(1)
            .cloned()
            .flatten()
            .or_else(|| caps.get(2).cloned().flatten());
        if let Some(v) = v {
            let qualifier = if is_match(r"(?i)up\s+to", text) {
                json!("Up to")
            } else {
                Value::Null
            };
            out.push(fact(vec![
                ("kind", json!("touch_channels")),
                ("value", json!(v.parse::<u64>().unwrap_or(0))),
                ("qualifier_verbatim", qualifier),
            ]));
        }
    }
    if let Some(caps) = captures(RESOLUTION_PX, text) {
        if is_match(r"(?i)resolution|display|LCD|LTDC|pixel", text) {
            if let (Some(a), Some(b)) = (
                caps.get(1).cloned().flatten(),
                caps.get(2).cloned().flatten(),
            ) {
                out.push(fact(vec![
                    ("kind", json!("display_resolution")),
                    (
                        "value",
                        json!([a.parse::<u64>().unwrap_or(0), b.parse::<u64>().unwrap_or(0)]),
                    ),
                ]));
            }
        }
    }
    if row.get("instances").is_some_and(|i| *i != Value::Null)
        && matches!(cls, "display" | "dcmi" | "touch")
    {
        out.push(fact(vec![
            ("kind", json!(format!("{cls}_instances"))),
            (
                "value",
                row.get("instances").cloned().unwrap_or(Value::Null),
            ),
        ]));
    }
    let _ = num_as_i64(&Value::Null);
}
