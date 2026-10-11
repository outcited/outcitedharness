//! BOM parsing — port of `harness/electronics/bom_extract.py`.
//!
//! Two printed shapes, both handled: standalone BOM sheets (column flow) and
//! EV-kit embedded BOM tables (header-matched rows). Every item keeps the
//! printed part number, manufacturer, value and package verbatim; unparseable
//! cells stay absent. Nothing is guessed.

use std::collections::HashMap;

use serde_json::{json, Map, Value};

/// `bom_extract.BOM_SCHEMA`.
pub const BOM_SCHEMA: &str = "harness.electronics-eval-bom.v1";

/// A parsed BOM item: canonical field -> verbatim cell text.
pub type Item = HashMap<String, String>;

const DESIGN_ID: &str = r"\b(?:TIDA?-\d{4,6}[A-Z]?|PMP\d{4,6}[A-Z]??\d*|TIDM-\d{4,6}[A-Z]?|[A-Z][A-Z0-9]{2,15}(?:EVKIT|EVB|EK|EVM|GEVB|AGEVB|-EVK\d*))\b";

/// Canonical header field names and their printed aliases.
pub const HEADER_ALIASES: &[(&str, &[&str])] = &[
    (
        "designator",
        &[
            "designator",
            "designators",
            "reference",
            "refdes",
            "ref des",
            "item",
        ],
    ),
    ("qty", &["quantity", "qty", "count"]),
    ("value", &["value", "val"]),
    (
        "part_number",
        &[
            "partnumber",
            "part number",
            "mpn",
            "mfr part",
            "mfr part number",
            "manufacturer part number",
            "part no",
            "part#",
            "catalog number",
        ],
    ),
    (
        "manufacturer",
        &["manufacturer", "vendor", "mfr", "manufacturer name"],
    ),
    (
        "description",
        &["description", "desc", "component description"],
    ),
    (
        "package",
        &[
            "packagereference",
            "package reference",
            "package",
            "footprint",
            "case",
            "package/case",
        ],
    ),
];

/// Reference exclusions: cells that read as empty in a BOM row.
const NULLISH: &[&str] = &["none", "n/a", "-", "–", "dnp"];
/// Flow parser additionally drops `nc`.
const NULLISH_FLOW: &[&str] = &["none", "n/a", "-", "–", "dnp", "nc"];

/// `_norm_cell`: collapse whitespace, strip, stringify-or-empty.
#[must_use]
pub fn norm_cell(text: Option<&str>) -> String {
    collapse(text.unwrap_or(""))
}

/// `_canon_header`: normalise a header cell and look up its canonical name.
#[must_use]
pub fn canon_header(cell: &str) -> Option<&'static str> {
    let key = collapse(cell).to_lowercase();
    for (canon, aliases) in HEADER_ALIASES {
        if aliases.iter().any(|a| *a == key) {
            return Some(canon);
        }
    }
    None
}

/// `_design_id_from`: the printed design identifier, when present.
#[must_use]
pub fn design_id_from(text: &str) -> Option<String> {
    let re = fancy_regex::Regex::new(DESIGN_ID).ok()?;
    re.find(text).ok().flatten().map(|m| m.as_str().to_string())
}

/// `_items_from_table`: header-matched table rows -> (header order, items).
#[must_use]
pub fn items_from_table(rows: &[Vec<String>]) -> (Vec<Option<&'static str>>, Vec<Item>) {
    if rows.is_empty() {
        return (Vec::new(), Vec::new());
    }
    let mut header_idx: Option<usize> = None;
    let mut order: Vec<Option<&'static str>> = Vec::new();
    for (i, row) in rows.iter().take(6).enumerate() {
        let canon: Vec<Option<&'static str>> =
            row.iter().map(|c| canon_header(&collapse(c))).collect();
        let hits: Vec<&str> = canon.iter().flatten().copied().collect();
        if hits.len() >= 3 && (hits.contains(&"part_number") || hits.contains(&"manufacturer")) {
            header_idx = Some(i);
            order = canon;
            break;
        }
    }
    let Some(header_idx) = header_idx else {
        return (Vec::new(), Vec::new());
    };
    let mut items = Vec::new();
    for row in rows.iter().skip(header_idx + 1) {
        let mut item = Item::new();
        for (j, cell) in row.iter().enumerate() {
            let Some(canon) = order.get(j).copied().flatten() else {
                continue;
            };
            let val = collapse(cell);
            if !val.is_empty() && !NULLISH.contains(&val.to_lowercase().as_str()) {
                item.insert(canon.to_string(), val);
            }
        }
        if item.contains_key("part_number") || item.contains_key("description") {
            items.push(item);
        }
    }
    (order, items)
}

/// `_items_from_flow`: column-flow state machine (header block, then
/// width-sized groups).
///
/// # Lints
///
/// `lines[j]` / `lines[start..]` mirror the reference's list scans; `j` and
/// `start` are advanced inside `j < lines.len()` / derived from that scan.
/// Bounded here and nowhere else.
#[allow(clippy::indexing_slicing)]
#[must_use]
pub fn items_from_flow(lines: &[String]) -> Vec<Item> {
    let mut start: Option<usize> = None;
    let mut order: Vec<&'static str> = Vec::new();
    for i in 0..lines.len().saturating_sub(2) {
        let mut probe: Vec<&'static str> = Vec::new();
        let mut j = i;
        while j < lines.len() && probe.len() < 9 {
            if let Some(c) = canon_header(lines[j].trim()) {
                probe.push(c);
                j += 1;
            } else if lines[j].trim().is_empty() {
                j += 1;
            } else {
                break;
            }
        }
        if probe.len() >= 4 && (probe.contains(&"part_number") || probe.contains(&"manufacturer")) {
            start = Some(j);
            order = probe;
            break;
        }
    }
    let Some(start) = start else {
        return Vec::new();
    };
    let width = order.len();
    let mut items = Vec::new();
    let mut buf: Vec<String> = Vec::new();
    for line in &lines[start..] {
        let s = line.trim();
        if s.is_empty() {
            continue;
        }
        buf.push(s.to_string());
        if buf.len() != width {
            continue;
        }
        let mut item = Item::new();
        for (key, val) in order.iter().zip(buf.iter()) {
            if !NULLISH_FLOW.contains(&val.to_lowercase().as_str()) {
                item.insert((*key).to_string(), val.clone());
            }
        }
        let mut ok = true;
        if let Some(des) = item.get("designator") {
            if !designator_ok(des.split(',').next().unwrap_or("").trim()) {
                ok = item.contains_key("part_number") || item.contains_key("description");
            }
        }
        if let Some(qty) = item.get("qty") {
            if !qty_ok(qty) {
                ok = false;
            }
        }
        if ok && (item.contains_key("part_number") || item.contains_key("description")) {
            items.push(item);
        }
        buf.clear();
    }
    items
}

/// `_DESIG_RE` against the first designator token.
///
fn designator_ok(token: &str) -> bool {
    // The pattern is a literal; a compile failure degrades to `false`, which
    // the gold gate would catch.
    let Ok(re) = fancy_regex::Regex::new(
        r"^[A-Z]{1,4}\d{1,4}(?:\s*[,&]\s*[A-Z]{1,4}\d{1,4})*,?$|^(?:JP|TP|MH|H)\d{1,3}(?:\s*[,&]\s*(?:JP|TP|MH|H)\d{1,3})*,?$|^(?:PCB\d*|FID\d*)$",
    ) else {
        return false;
    };
    re.is_match(&token.to_uppercase()).unwrap_or(false)
}

/// `_QTY_RE`: `^\d{1,4}$`.
fn qty_ok(token: &str) -> bool {
    !token.is_empty() && token.len() <= 4 && token.chars().all(|c| c.is_ascii_digit())
}

/// One page of pre-extracted document content (text plus any detected tables).
#[derive(Debug, Clone, Default)]
pub struct PageInput {
    /// `page.get_text()` output.
    pub text: String,
    /// `page.find_tables()` -> `t.extract()` outputs.
    pub tables: Vec<Vec<Vec<String>>>,
}

/// `bom_from_document` over abstract pages (the pymupdf loop is the
/// caller's/dw-pdf's; this is the deterministic parsing surface).
///
/// Returns the same dict shape as the reference: `schema`, `design_id`,
/// `board_name`, `items`, `item_count`, `parse_method`, `bom_pages`.
#[must_use]
pub fn bom_from_pages(pages: &[PageInput]) -> Value {
    let first_text = pages
        .first()
        .map(|p| p.text.as_str())
        .unwrap_or_default()
        .to_string();
    let mut items: Vec<Item> = Vec::new();
    let mut method: Option<String> = None;
    let mut page_hits: Vec<u64> = Vec::new();
    for (i, page) in pages.iter().take(40).enumerate() {
        let pno = u64::try_from(i + 1).unwrap_or(0);
        // 1. structured tables: a strictly-larger extract wins the running best.
        for table in &page.tables {
            let (_order, t_items) = items_from_table(table);
            if !t_items.is_empty() && t_items.len() > items.len() {
                items = t_items;
                method = Some(format!("find_tables:p{pno}"));
                page_hits.push(pno);
            }
        }
        // 2. column-flow fallback (standalone BOM sheets): only while empty.
        if items.is_empty() {
            let lines: Vec<String> = page.text.split('\n').map(ToString::to_string).collect();
            let f_items = items_from_flow(&lines);
            if f_items.len() > items.len() {
                items = f_items;
                method = Some(format!("column_flow:p{pno}"));
                page_hits.push(pno);
            }
        }
    }
    let design_id = design_id_from(&first_text);
    let board = board_name(&first_text);
    page_hits.sort_unstable();
    page_hits.dedup();
    json!({
        "schema": BOM_SCHEMA,
        "design_id": design_id,
        "board_name": board,
        "items": items.iter().map(item_to_wire).collect::<Vec<_>>(),
        "item_count": items.len(),
        "parse_method": method,
        "bom_pages": page_hits,
    })
}

fn item_to_wire(item: &Item) -> Value {
    // Reference item dicts are built in field order; the gate compares
    // structure, so a sorted-key object is the stable comparison form.
    let mut map = Map::new();
    let mut keys: Vec<&String> = item.keys().collect();
    keys.sort();
    for k in keys {
        if let Some(v) = item.get(k) {
            map.insert(k.clone(), Value::String(v.clone()));
        }
    }
    Value::Object(map)
}

/// Board name: `Evaluates: <token>` (group 1 only) else the last non-empty
/// line before "Bill of Materials".
fn board_name(first_text: &str) -> Option<String> {
    let re = fancy_regex::Regex::new(r"Evaluates?:\s*([A-Z0-9\-/]+)").ok()?;
    if let Ok(Some(caps)) = re.captures(first_text) {
        return caps.get(1).map(|m| m.as_str().to_string());
    }
    let re2 = fancy_regex::Regex::new("Bill of Materials").ok()?;
    let start = re2.find(first_text).ok().flatten()?.start();
    let head: Vec<&str> = first_text
        .get(..start)?
        .split('\n')
        .map(str::trim)
        .filter(|h| !h.is_empty())
        .collect();
    head.last().map(ToString::to_string)
}

/// `_norm_cell` core: collapse runs of whitespace to one space, trim.
fn collapse(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut in_space = false;
    for c in text.chars() {
        if c.is_whitespace() {
            if !in_space {
                out.push(' ');
                in_space = true;
            }
        } else {
            out.push(c);
            in_space = false;
        }
    }
    out.trim().to_string()
}
