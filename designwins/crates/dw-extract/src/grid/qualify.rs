//! `quantity_qualifier` — which QUANTITY a numeric row is (sibling of
//! `qualifier_verbatim`: that one says which bound, this one says which
//! quantity). Anchored on the document's own words; a bare "flash" is left
//! null, never called code flash.

use serde_json::Value;

use super::helpers::is_match;
use super::tables::{
    QQ_ABS_MAX, QQ_AMBIENT, QQ_CODE_FLASH, QQ_DATA_FLASH, QQ_EEPROM, QQ_JUNCTION, QQ_MAXIMUM,
    QQ_MINIMUM, QQ_RATED, QQ_SRAM_BANK, QQ_SRAM_SPECIAL, QQ_STORAGE, QQ_TYPICAL, SIZE_IN_TEXT,
    SUPPLY_RANGE_TEXT, TEMP_RANGE_TEXT,
};

/// `quantity_qualifier(row)` — the quantity a numeric row measures.
#[must_use]
pub fn quantity_qualifier(row: &Value) -> Option<String> {
    let get = |k: &str| -> String {
        match row.get(k) {
            Some(Value::Null) | None => String::new(),
            Some(Value::String(s)) => s.clone(),
            Some(other) => py_scalar(other),
        }
    };
    // Fields join with a separator so a label ending "SRAM" and a verbatim
    // starting "6 or 10" do not read as a bank designator "SRAM 6".
    let text = ["label", "verbatim", "context", "section", "parent"]
        .iter()
        .map(|k| get(k))
        .collect::<Vec<String>>()
        .join(" \u{a6} ");
    let unit = get("unit").to_lowercase();
    let cls = get("peripheral_class");
    let section = get("section");
    let group = row.get("group").and_then(Value::as_str).unwrap_or("memory");
    let memory_row = matches!(
        unit.as_str(),
        "kb" | "mb" | "kbyte" | "kbytes" | "mbyte" | "mbytes" | "bytes" | "kbit" | "mbit"
    ) || matches!(cls.as_str(), "flash" | "sram" | "memory_map");
    let supply_text = is_match(SUPPLY_RANGE_TEXT, &get("label"));
    let temp_text = is_match(TEMP_RANGE_TEXT, &get("label"));
    let value_is_null = row.get("value").is_none_or(|v| *v == Value::Null);
    // A multi-valued memory line ("16 or 32 Kbytes of Flash") has no scalar
    // value but is exactly the family fact that needs its qualifier.
    let sized_memory_line = memory_row && group == "memory" && is_match(SIZE_IN_TEXT, &text);
    if value_is_null && !sized_memory_line && !supply_text && !temp_text {
        return None;
    }
    if temp_text && value_is_null && !supply_text {
        return temp_quantity(&text);
    }
    if supply_text && value_is_null {
        if is_match(QQ_ABS_MAX, &text) {
            return Some("absolute_maximum".to_string());
        }
        if is_match(QQ_RATED, &text)
            || is_match(r"(?i)\bsupply\b|\boperating\b|\bV(?:DD|CC)\b", &text)
        {
            return Some("rated".to_string());
        }
        return None;
    }
    if memory_row {
        return memory_quantity(&text);
    }
    if matches!(unit.as_str(), "\u{b0}c" | "\u{ba}c" | "c") || section == "temperature_range" {
        return temp_quantity(&text);
    }
    if unit == "v" || section == "supply_range" {
        if is_match(QQ_ABS_MAX, &text) {
            return Some("absolute_maximum".to_string());
        }
        if is_match(QQ_RATED, &text) {
            return Some("rated".to_string());
        }
        return None;
    }
    if matches!(unit.as_str(), "mhz" | "khz" | "ghz") {
        let qualifier = get("qualifier_verbatim");
        if !qualifier.is_empty() && is_match(QQ_MAXIMUM, &qualifier) {
            return Some("maximum".to_string());
        }
        if is_match(QQ_TYPICAL, &text) {
            return Some("typical".to_string());
        }
        if is_match(QQ_MINIMUM, &text) {
            return Some("minimum".to_string());
        }
        if is_match(QQ_MAXIMUM, &text) {
            return Some("maximum".to_string());
        }
        return None;
    }
    None
}

fn temp_quantity(text: &str) -> Option<String> {
    if is_match(QQ_STORAGE, text) {
        return None;
    }
    if is_match(QQ_JUNCTION, text) {
        return Some("junction".to_string());
    }
    if is_match(QQ_AMBIENT, text) {
        return Some("ambient".to_string());
    }
    None
}

fn memory_quantity(text: &str) -> Option<String> {
    let eeprom = is_match(QQ_EEPROM, text);
    if eeprom
        && is_match(
            r"(?i)emulat|high[- ]cycl|to use for data|use as|used as",
            text,
        )
    {
        // Flash region used as EEPROM (ST "high cycling ... (EEPROM
        // emulation)"): emulated EEPROM is data flash.
        return Some("data_flash".to_string());
    }
    if eeprom {
        // Its own kind (CR: eeprom_kb is a dictionary attribute). A prose row
        // that also sizes flash or SRAM carries more than one quantity, so no
        // single qualifier is honest.
        if is_match(r"(?i)\bflash\b|\bs?ram\b", text) {
            return None;
        }
        return Some("eeprom".to_string());
    }
    if is_match(QQ_DATA_FLASH, text) {
        return Some("data_flash".to_string());
    }
    if is_match(QQ_CODE_FLASH, text) {
        return Some("code_flash".to_string());
    }
    if is_match(r"(?i)\bs?ram(?:\s?\d|[A-D]\b|\b)", text) && !is_match(r"(?i)\bflash\b", text) {
        if is_match(QQ_SRAM_BANK, text) {
            return Some("sram_bank".to_string());
        }
        if is_match(QQ_SRAM_SPECIAL, text) {
            return None;
        }
        return Some("total_sram".to_string());
    }
    // Bare "Flash": the document did not say which. The null is wanted, not a
    // guess (CR renesas-correction-qualifier-ack-20260908).
    None
}

/// `str(value)` for the scalar shapes the reference stringifies into the
/// joined probe text.
fn py_scalar(value: &Value) -> String {
    match value {
        Value::Bool(b) => b.to_string(),
        Value::Number(n) => n.to_string(),
        other => other.to_string(),
    }
}
