//! Row construction and merging — `emit`, `_dedupe`, `_prefer_specific`,
//! `_sram_bank_check`.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Map, Value};

use super::helpers::{
    is_match, json_value, label_key, num_as_i64, py_str, set_key, tokens, varies_by_part,
};
use super::tables::SUPPLY_RANGE_TEXT;
use crate::ExtractError;

/// # Lints
///
/// Mirrors the reference's `emit(**kwargs)` call shape; the argument list is
/// the ported contract, not a design choice.
#[allow(clippy::too_many_arguments, clippy::unnecessary_wraps)]
pub(super) fn emit(
    base: &Map<String, Value>,
    group: &str,
    cls: Option<&str>,
    label: &str,
    pages: &[i64],
    verbatim: &str,
    tier: &str,
    instances: Option<Value>,
    value: Value,
    unit: Value,
    qualifier: Value,
    section: Option<&str>,
    flags: Option<Map<String, Value>>,
) -> Result<Value, ExtractError> {
    let mut row = base.clone();
    row.insert("group".to_string(), json!(group));
    row.insert(
        "peripheral_class".to_string(),
        match cls {
            Some(c) => Value::String(c.to_string()),
            None => Value::Null,
        },
    );
    row.insert("label".to_string(), json!(label));
    row.insert("instances".to_string(), instances.unwrap_or(Value::Null));
    row.insert("value".to_string(), value);
    row.insert("unit".to_string(), unit);
    row.insert("qualifier_verbatim".to_string(), qualifier);
    // A supply range is the envelope of every member (CR decision); anything
    // "depending on MPN" varies by part.
    let vary = if section == Some("supply_range")
        || is_match(SUPPLY_RANGE_TEXT, label)
        || is_match(SUPPLY_RANGE_TEXT, verbatim)
    {
        json!(false)
    } else {
        json!(varies_by_part(verbatim))
    };
    row.insert("varies_by_part".to_string(), vary);
    row.insert("tier".to_string(), json!(tier));
    let mut pages: Vec<i64> = pages.to_vec();
    pages.sort_unstable();
    pages.dedup();
    pages.truncate(12);
    row.insert("source_pages".to_string(), json!(pages));
    // Python slices `verbatim[:300]` in code points.
    row.insert(
        "verbatim".to_string(),
        json!(verbatim.chars().take(300).collect::<String>()),
    );
    if let Some(section) = section {
        row.insert("section".to_string(), json!(section));
    }
    if let Some(flags) = flags {
        for (k, v) in flags {
            row.insert(k, v);
        }
    }
    Ok(Value::Object(row))
}

/// `_dedupe` (then the valued-prose filter, then `_prefer_specific`).
///
/// # Lints
///
/// `merged[pos]` / key-extend bookkeeping is the ported dict-insertion
/// algorithm; positions come from `position()` over the same Vec. Bounded
/// here and nowhere else (same call as `sha256::sha256_hex`).
#[allow(clippy::indexing_slicing, clippy::unnecessary_wraps)]
pub(super) fn dedupe(rows: Vec<Value>) -> Result<Vec<Value>, ExtractError> {
    let mut merged: Vec<(Vec<String>, Value)> = Vec::new();
    for row in rows {
        let group = row
            .get("group")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let tier = row
            .get("tier")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let label = row
            .get("label")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let mut key = vec![group, tier, label_key(&label)];
        let row_instances = row.get("instances").cloned().unwrap_or(Value::Null);
        let mut merge_into: Option<usize> = None;
        if let Some(pos) = merged.iter().position(|(k, _)| *k == key) {
            let existing_inst = merged[pos]
                .1
                .get("instances")
                .cloned()
                .unwrap_or(Value::Null);
            if existing_inst != Value::Null
                && row_instances != Value::Null
                && existing_inst != row_instances
            {
                // "16 digital comparators" and "8 digital comparators" are
                // two statements: the count joins the key.
                key.push(py_str(&row_instances));
            } else {
                merge_into = Some(pos);
            }
        }
        if merge_into.is_none() {
            // The key may have just been extended with the instance count.
            merge_into = merged.iter().position(|(k, _)| *k == key);
        }
        if let Some(pos) = merge_into {
            let (_, existing) = &mut merged[pos];
            merge_pages(existing, &row);
            if existing.get("instances").is_some_and(|i| i == &Value::Null)
                && row_instances != Value::Null
            {
                set_key(existing, "instances", row_instances);
            }
            continue;
        }
        merged.push((key, row));
    }
    let out: Vec<Value> = merged.into_iter().map(|(_, v)| v).collect();
    let mut valued: BTreeSet<(String, String, String)> = BTreeSet::new();
    for row in &out {
        let value = row.get("value").cloned().unwrap_or(Value::Null);
        if value != Value::Null
            && !matches!(
                row.get("section").and_then(Value::as_str),
                Some("max_frequency" | "supply_range" | "temperature_range")
            )
        {
            valued.insert(valued_key(row, &value));
        }
    }
    let mut final_rows: Vec<Value> = Vec::new();
    for row in out {
        let value = row.get("value").cloned().unwrap_or(Value::Null);
        if matches!(
            row.get("section").and_then(Value::as_str),
            Some("max_frequency" | "supply_range" | "temperature_range")
        ) && valued.contains(&valued_key(&row, &value))
        {
            // A bare prose number is dropped when a vendor bullet in the same
            // group already carries that value+unit.
            continue;
        }
        final_rows.push(row);
    }
    prefer_specific(final_rows)
}

fn valued_key(row: &Value, value: &Value) -> (String, String, String) {
    let group = row
        .get("group")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let unit = row
        .get("unit")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_lowercase();
    (group, json_value(value), unit)
}

fn merge_pages(existing: &mut Value, row: &Value) {
    let mut pages: BTreeSet<i64> = existing
        .get("source_pages")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(num_as_i64).collect())
        .unwrap_or_default();
    if let Some(more) = row.get("source_pages").and_then(Value::as_array) {
        pages.extend(more.iter().filter_map(num_as_i64));
    }
    let mut pages: Vec<i64> = pages.into_iter().collect();
    pages.truncate(12);
    set_key(existing, "source_pages", json!(pages));
}

/// `_prefer_specific` — keep the more specific label of two rows saying the
/// same thing; the discarded spelling lands in `also_printed`.
///
/// # Lints
///
/// The reference's `for i, a in enumerate(bucket)` / `for j, b in ...` index
/// pairs are the algorithm; indices derive from `0..bucket.len()`. Bounded
/// here and nowhere else.
#[allow(clippy::indexing_slicing, clippy::unnecessary_wraps)]
pub(super) fn prefer_specific(rows: Vec<Value>) -> Result<Vec<Value>, ExtractError> {
    let mut by_bucket: Vec<(String, Vec<Value>)> = Vec::new();
    for row in rows {
        let group = row
            .get("group")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let tier = row
            .get("tier")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        let cls = py_str(&row.get("peripheral_class").cloned().unwrap_or(Value::Null));
        let key = format!("{group}\u{1}{tier}\u{1}{cls}");
        if let Some((_, bucket)) = by_bucket.iter_mut().find(|(k, _)| *k == key) {
            bucket.push(row);
        } else {
            by_bucket.push((key, vec![row]));
        }
    }
    let mut out: Vec<Value> = Vec::new();
    for (_, mut bucket) in by_bucket {
        if bucket.len() == 1 {
            out.extend(bucket);
            continue;
        }
        let toks: Vec<BTreeSet<String>> = bucket
            .iter()
            .map(|r| tokens(r.get("label").and_then(Value::as_str).unwrap_or_default()))
            .collect();
        let mut keep = vec![true; bucket.len()];
        for i in 0..bucket.len() {
            for j in 0..bucket.len() {
                if i == j || !keep[i] || toks[i].is_empty() {
                    continue;
                }
                let a = bucket[i].clone();
                let b = &bucket[j];
                let a_inst = a.get("instances").cloned().unwrap_or(Value::Null);
                let b_inst = b.get("instances").cloned().unwrap_or(Value::Null);
                // Two counts that disagree are two facts, never one.
                if a_inst != Value::Null && b_inst != Value::Null && a_inst != b_inst {
                    continue;
                }
                let a_val = a.get("value").cloned().unwrap_or(Value::Null);
                let b_val = b.get("value").cloned().unwrap_or(Value::Null);
                if a_val != Value::Null
                    && b_val != Value::Null
                    && json_value(&a_val) != json_value(&b_val)
                {
                    continue;
                }
                let subset = toks[i].is_subset(&toks[j]) && toks[i] != toks[j];
                let equal = toks[i] == toks[j];
                let spec_i = specificity(&a);
                let spec_j = specificity(b);
                if subset || (equal && spec_i < spec_j) || (equal && spec_i == spec_j && i > j) {
                    keep[i] = false;
                    let label = a.get("label").cloned().unwrap_or(Value::Null);
                    let b = &mut bucket[j];
                    let mut also: Vec<Value> = b
                        .get("also_printed")
                        .and_then(Value::as_array)
                        .cloned()
                        .unwrap_or_default();
                    if !also.contains(&label) {
                        also.push(label);
                    }
                    set_key(b, "also_printed", Value::Array(also));
                    merge_pages(b, &a);
                    if b.get("instances").is_some_and(|i| i == &Value::Null)
                        && a_inst != Value::Null
                    {
                        set_key(b, "instances", a_inst);
                    }
                    if b.get("value").is_some_and(|v| v == &Value::Null) && a_val != Value::Null {
                        set_key(b, "value", a_val);
                        set_key(b, "unit", a.get("unit").cloned().unwrap_or(Value::Null));
                    }
                    let b_qv = b.get("qualifier_verbatim").cloned().unwrap_or(Value::Null);
                    let a_qv = a.get("qualifier_verbatim").cloned().unwrap_or(Value::Null);
                    if (b_qv == Value::Null || b_qv == json!(""))
                        && a_qv != Value::Null
                        && a_qv != json!("")
                    {
                        set_key(b, "qualifier_verbatim", a_qv);
                    }
                    break;
                }
            }
        }
        for (r, k) in bucket.into_iter().zip(keep) {
            if k {
                out.push(r);
            }
        }
    }
    Ok(out)
}

/// `_specificity` — source rank, then label length.
fn specificity(row: &Value) -> (i32, usize) {
    let section = row.get("section").and_then(Value::as_str).unwrap_or("");
    let rank = crate::grid::tables::SOURCE_RANK
        .iter()
        .find(|(s, _)| *s == section)
        .map_or(0, |(_, r)| *r);
    let label_len = row
        .get("label")
        .and_then(Value::as_str)
        .map_or(0, |l| l.chars().count());
    (rank, label_len)
}

/// `_sram_bank_check` — banks must sum to the stated total; if they do not,
/// the total row is refused (moved below the grid) rather than reconciled.
///
/// # Lints
///
/// `rows[i]` over indices collected from `enumerate()` over the same slice;
/// positions are structural. Unnecessary `Result` kept to mirror the
/// reference's error-returning seam (`build_grid` propagates).
#[allow(clippy::indexing_slicing, clippy::unnecessary_wraps)]
pub(super) fn sram_bank_check(rows: &mut [Value]) -> Result<Value, ExtractError> {
    let kb = |r: &Value| {
        r.get("unit")
            .and_then(Value::as_str)
            .map(str::to_lowercase)
            .as_deref()
            == Some("kb")
    };
    let totals_idx: Vec<usize> = rows
        .iter()
        .enumerate()
        .filter(|(_, r)| {
            r.get("quantity_qualifier") == Some(&json!("total_sram"))
                && r.get("tier") == Some(&json!("grid"))
                && kb(r)
        })
        .map(|(i, _)| i)
        .collect();
    let banks_idx: Vec<usize> = rows
        .iter()
        .enumerate()
        .filter(|(_, r)| {
            r.get("quantity_qualifier") == Some(&json!("sram_bank"))
                && r.get("tier") == Some(&json!("grid"))
                && kb(r)
                && r.get("value").is_some_and(Value::is_number)
        })
        .map(|(i, _)| i)
        .collect();
    if totals_idx.is_empty() || banks_idx.is_empty() {
        return Ok(json!({"sram_banks_checked": false}));
    }
    // The reference sums a label-keyed dict of banks (last wins).
    let mut by_label: Vec<(String, f64)> = Vec::new();
    for i in &banks_idx {
        let label = rows[*i].get("label").map(py_str).unwrap_or_default();
        let value = rows[*i].get("value").and_then(Value::as_f64).unwrap_or(0.0);
        if let Some(slot) = by_label.iter_mut().find(|(l, _)| *l == label) {
            slot.1 = value;
        } else {
            by_label.push((label, value));
        }
    }
    let bank_sum: f64 = by_label.iter().map(|(_, v)| *v).sum();
    let mut totals_vals: BTreeSet<String> = BTreeSet::new();
    for i in &totals_idx {
        if let Some(v) = rows[*i].get("value").and_then(Value::as_f64) {
            totals_vals.insert(v.to_string());
        }
    }
    let matched = totals_idx.iter().any(|i| {
        rows[*i]
            .get("value")
            .and_then(Value::as_f64)
            .is_some_and(|v| (v - bank_sum).abs() < 0.5)
    });
    let mut totals_sorted: Vec<f64> = totals_vals
        .iter()
        .filter_map(|s| s.parse::<f64>().ok())
        .collect();
    totals_sorted.sort_by(f64::total_cmp);
    let mut result = Map::new();
    result.insert("sram_banks_checked".to_string(), json!(true));
    result.insert("sram_bank_sum_kb".to_string(), json!(bank_sum));
    result.insert("sram_totals_kb".to_string(), json!(totals_sorted));
    result.insert("sram_banks_sum_to_total".to_string(), json!(matched));
    if !matched {
        for i in totals_idx {
            set_key(&mut rows[i], "tier", json!("below_grid"));
            set_key(
                &mut rows[i],
                "refused",
                json!("sram_banks_do_not_sum_to_total"),
            );
        }
    }
    Ok(Value::Object(result))
}

/// Counter of grid-row quantity qualifiers (`quantity_qualifiers` key).
pub(super) fn qualifier_counts(grid_rows: &[&Value]) -> BTreeMap<String, u64> {
    let mut counts: BTreeMap<String, u64> = BTreeMap::new();
    for row in grid_rows {
        if row.get("value").is_some_and(|v| v != &Value::Null) {
            // Python's `json.dumps` writes a None counter key as `"null"`.
            let key = match row.get("quantity_qualifier") {
                Some(Value::String(s)) => s.clone(),
                Some(Value::Null) | None => "null".to_string(),
                Some(other) => py_str(other),
            };
            *counts.entry(key).or_insert(0) += 1;
        }
    }
    counts
}
