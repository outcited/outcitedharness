//! Deterministic `PyMuPDF` evidence extraction from indexed datasheet pages
//! (`harness/electronics/page_evidence.py`, ported 1:1).
//!
//! The profile's lane pages (capped per lane) and exact pin locations select
//! the pages; each selected page is extracted to blocks, tables (only on pin
//! and parametric lanes), and a compact layout IR for deterministic retries.
//! The `PyMuPDF` layer (blocks, dict structure, `find_tables`, OCR) is
//! PDF-layer: its outputs travel as captured inputs — including OCR results
//! and OCR failures, which decide the `extraction_status` chain
//! `text_layer` -> `ocr_fallback` -> `no_text_after_ocr` / `ocr_failed`.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{json, Value};

use crate::corpus::canonical_json;
use crate::sha256::sha256_hex;

/// The page-evidence schema id.
pub const PAGE_EVIDENCE_SCHEMA: &str = "harness.electronics-page-evidence.v2";

/// One page of the PDF layer's output for evidence extraction.
#[derive(Debug, Clone, Default)]
pub struct EvidencePage {
    /// `page.rect.width`.
    pub width: f64,
    /// `page.rect.height`.
    pub height: f64,
    /// `get_text("blocks")` rows: `[x0, y0, x1, y1, text]`.
    pub blocks: Vec<Value>,
    /// `get_text("dict")` output; `None` when extraction raised.
    pub dict: Option<Value>,
    /// `find_tables()` outputs: `{rows, bbox}`; `None` when the finder
    /// returned nothing.
    pub tables: Option<Vec<Value>>,
    /// `get_text("blocks", textpage=...)` OCR rows; `None` when OCR was not
    /// attempted.
    pub ocr_blocks: Option<Vec<Value>>,
    /// The OCR error as `"{Type}: {message}"`; `None` when OCR succeeded or
    /// was not attempted.
    pub ocr_error: Option<String>,
}

/// `selected_pages`: lane pages capped per lane, exact pin locations
/// overriding the cap with both pin lanes.
///
/// # Errors
///
/// Returns `"maximum_pages_per_lane must be positive"` when the cap is 0.
pub fn selected_pages(
    profile: &Value,
    maximum_pages_per_lane: usize,
) -> Result<BTreeMap<i64, BTreeSet<String>>, String> {
    if maximum_pages_per_lane < 1 {
        return Err("maximum_pages_per_lane must be positive".to_string());
    }
    let mut output: BTreeMap<i64, BTreeSet<String>> = BTreeMap::new();
    if let Some(lane_pages) = profile.get("lane_pages").and_then(Value::as_object) {
        for (lane, pages) in lane_pages {
            let mut sorted: Vec<i64> = pages
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|v| {
                    v.as_i64()
                        .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
                })
                .collect();
            sorted.sort_unstable();
            sorted.dedup();
            for page in sorted.iter().take(maximum_pages_per_lane) {
                output.entry(*page).or_default().insert(lane.clone());
            }
        }
    }
    for location in profile
        .get("exact_pin_locations")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        if location.get("status").and_then(Value::as_str) != Some("send") {
            continue;
        }
        for page in location
            .get("pages_1based")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_i64)
        {
            let lanes = output.entry(page).or_default();
            lanes.insert("pin_or_ball".to_string());
            lanes.insert("pin_semantics".to_string());
        }
    }
    Ok(output)
}

/// `get_text("blocks")` rows -> normalized block records.
#[allow(
    clippy::indexing_slicing,
    reason = "the block row is a fixed 5-tuple as extract() returns it"
)]
fn blocks_of(raw: &[Value]) -> Vec<Value> {
    let mut output = Vec::new();
    for (index, row) in raw.iter().enumerate() {
        let Some(row) = row.as_array() else {
            continue;
        };
        if row.len() < 5 {
            continue;
        }
        let text = match &row[4] {
            Value::String(t) => t.trim().to_string(),
            Value::Null => String::new(),
            other => other.to_string().trim().to_string(),
        };
        if text.is_empty() {
            continue;
        }
        output.push(json!({
            "bbox": [
                row[0].as_f64().unwrap_or_default(),
                row[1].as_f64().unwrap_or_default(),
                row[2].as_f64().unwrap_or_default(),
                row[3].as_f64().unwrap_or_default(),
            ],
            "text": text,
            "block_id": format!("b{index}"),
            "reading_order": index,
        }));
    }
    output
}

/// The compact page-structure artifact for deterministic retries.
#[allow(clippy::too_many_lines, reason = "faithful port of _layout_ir")]
fn layout_ir(page: &EvidencePage, blocks: &[Value]) -> Value {
    let mut output_blocks: Vec<Value> = Vec::new();
    let mut output_images: Vec<Value> = Vec::new();
    let raw = page.dict.as_ref().and_then(Value::as_object);
    for (index, block) in raw
        .and_then(|m| m.get("blocks"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .enumerate()
    {
        let block_type = block.get("type").and_then(Value::as_i64).unwrap_or(0);
        let bbox = block
            .get("bbox")
            .and_then(Value::as_array)
            .filter(|b| !b.is_empty())
            .map(|b| {
                json!(b
                    .iter()
                    .map(|v| v.as_f64().unwrap_or_default())
                    .collect::<Vec<_>>())
            });
        let block_id = format!("b{index}");
        if block_type != 0 {
            if let Some(bbox) = bbox {
                output_images.push(json!({
                    "block_id": block_id,
                    "kind": "visual_object",
                    "bbox": bbox,
                }));
            }
            continue;
        }
        let mut lines_out: Vec<Value> = Vec::new();
        let mut line_index = 0usize;
        for line in block
            .get("lines")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            let line_text: String = line
                .get("spans")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .map(|span| match span.get("text") {
                    Some(Value::String(t)) => t.clone(),
                    Some(Value::Null) | None => String::new(),
                    other => other.map(Value::to_string).unwrap_or_default(),
                })
                .fold(String::new(), |mut acc, part| {
                    acc.push_str(&part);
                    acc
                });
            let line_text = line_text.trim().to_string();
            if line_text.is_empty() {
                continue;
            }
            let line_bbox = line
                .get("bbox")
                .and_then(Value::as_array)
                .filter(|b| !b.is_empty())
                .map(|b| {
                    json!(b
                        .iter()
                        .map(|v| v.as_f64().unwrap_or_default())
                        .collect::<Vec<_>>())
                });
            lines_out.push(json!({
                "line_id": format!("{block_id}.l{line_index}"),
                "text": line_text,
                "bbox": line_bbox,
            }));
            line_index += 1;
        }
        if lines_out.is_empty() {
            continue;
        }
        let Some(bbox) = bbox else {
            continue;
        };
        output_blocks.push(json!({
            "block_id": block_id,
            "bbox": bbox,
            "reading_order": output_blocks.len(),
            "lines": lines_out,
        }));
    }
    if !output_blocks.is_empty() {
        return json!({
            "source": "text_layer",
            "reading_order": "pymupdf_native",
            "text_blocks": output_blocks,
            "visual_blocks": output_images,
        });
    }
    // Fallback artifact: normalized block text + bbox when line geometry is
    // unavailable.
    let text_blocks: Vec<Value> = blocks
        .iter()
        .map(|block| {
            let block_id = block
                .get("block_id")
                .and_then(Value::as_str)
                .unwrap_or_default();
            json!({
                "block_id": block_id,
                "bbox": block.get("bbox").cloned().unwrap_or(Value::Null),
                "reading_order": block.get("reading_order").cloned().unwrap_or(Value::Null),
                "lines": [{
                    "line_id": format!("{block_id}.l0"),
                    "text": block.get("text").cloned().unwrap_or(Value::Null),
                    "bbox": block.get("bbox").cloned().unwrap_or(Value::Null),
                }],
            })
        })
        .collect();
    json!({
        "source": "normalized_blocks",
        "reading_order": "pymupdf_native",
        "text_blocks": text_blocks,
        "visual_blocks": output_images,
    })
}

/// `find_tables()` outputs -> table records (pin/parametric lanes only).
fn tables_of(page: &EvidencePage) -> Vec<Value> {
    let Some(tables) = &page.tables else {
        return Vec::new();
    };
    tables
        .iter()
        .enumerate()
        .filter_map(|(index, table)| {
            let rows: Vec<Value> = table
                .get("rows")
                .and_then(Value::as_array)?
                .iter()
                .map(|row| {
                    row.as_array().map_or(Value::Null, |cells| {
                        json!(cells
                            .iter()
                            .map(|cell| match cell {
                                Value::Null => Value::Null,
                                Value::String(t) => json!(t.trim()),
                                other => json!(other.to_string().trim()),
                            })
                            .collect::<Vec<_>>())
                    })
                })
                .collect();
            if rows.is_empty() {
                return None;
            }
            let bbox = table
                .get("bbox")
                .and_then(Value::as_array)
                .filter(|b| !b.is_empty())
                .map(|b| {
                    json!(b
                        .iter()
                        .map(|v| v.as_f64().unwrap_or_default())
                        .collect::<Vec<_>>())
                });
            Some(json!({
                "table_index": index,
                "bbox": bbox.unwrap_or(Value::Null),
                "rows": rows,
            }))
        })
        .collect()
}

/// Extract the per-page evidence records for the profile's selected pages.
///
/// # Errors
///
/// Returns `"indexed page N is outside document bounds"` when the profile
/// names a page the document does not carry.
#[allow(
    clippy::too_many_lines,
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    clippy::missing_panics_doc,
    clippy::unwrap_used,
    reason = "faithful port of extract_profile_evidence; the page walks index as the reference does"
)]
pub fn extract_profile_evidence(
    pages: &[EvidencePage],
    profile: &Value,
    maximum_pages_per_lane: usize,
    enable_ocr_fallback: bool,
    ocr_language: &str,
) -> Result<Vec<Value>, String> {
    // Python `str(profile["document_sha256"])` — the value is a plain
    // string; serde would re-quote it.
    let document_sha256 = match profile.get("document_sha256") {
        Some(Value::String(s)) => s.clone(),
        Some(other) => other.to_string().trim_matches('"').to_string(),
        None => String::new(),
    };
    let source_path = profile.get("source_path").cloned().unwrap_or(Value::Null);
    let page_count = i64::try_from(pages.len()).unwrap_or(i64::MAX);
    let mut out = Vec::new();
    for (page_1based, lanes) in selected_pages(profile, maximum_pages_per_lane)? {
        if !(1..=page_count).contains(&page_1based) {
            return Err(format!(
                "indexed page {page_1based} is outside document bounds"
            ));
        }
        let page = &pages[(page_1based - 1) as usize];
        let mut blocks = blocks_of(&page.blocks);
        let mut ocr_attempted = false;
        let mut ocr_used = false;
        let mut ocr_error: Option<String> = None;
        let mut extraction_status = "text_layer".to_string();
        if blocks.is_empty() {
            extraction_status = "no_text_layer".to_string();
            if enable_ocr_fallback {
                ocr_attempted = true;
                if let Some(error) = &page.ocr_error {
                    ocr_error = Some(error.clone());
                    extraction_status = "ocr_failed".to_string();
                } else {
                    let raw = page.ocr_blocks.clone().unwrap_or_default();
                    for (index, row) in raw.iter().enumerate() {
                        let Some(row) = row.as_array() else {
                            continue;
                        };
                        if row.len() < 5 {
                            continue;
                        }
                        let text = match &row[4] {
                            Value::String(t) => t.trim().to_string(),
                            Value::Null => String::new(),
                            other => other.to_string().trim().to_string(),
                        };
                        if text.is_empty() {
                            continue;
                        }
                        blocks.push(json!({
                            "bbox": [
                                row[0].as_f64().unwrap_or_default(),
                                row[1].as_f64().unwrap_or_default(),
                                row[2].as_f64().unwrap_or_default(),
                                row[3].as_f64().unwrap_or_default(),
                            ],
                            "text": text,
                            "block_id": format!("ocr_b{index}"),
                            "reading_order": index,
                        }));
                    }
                    if blocks.is_empty() {
                        extraction_status = "no_text_after_ocr".to_string();
                    } else {
                        ocr_used = true;
                        extraction_status = "ocr_fallback".to_string();
                    }
                }
            }
        }
        let tables = if lanes.iter().any(|lane| {
            matches!(
                lane.as_str(),
                "pin_or_ball" | "pin_semantics" | "parametrics"
            )
        }) {
            tables_of(page)
        } else {
            Vec::new()
        };
        let layout = layout_ir(page, &blocks);
        let needs_visual = matches!(
            extraction_status.as_str(),
            "no_text_layer" | "no_text_after_ocr" | "ocr_failed"
        );
        let mut core = json!({
            "schema": PAGE_EVIDENCE_SCHEMA,
            "document_sha256": document_sha256,
            "source_path": source_path,
            "page_1based": page_1based,
            "page_size": {"width": page.width, "height": page.height},
            "lanes": lanes.iter().cloned().collect::<Vec<_>>(),
            "blocks": blocks,
            "tables": tables,
            "layout_ir": layout,
            "extraction_status": extraction_status,
            "needs_visual_verification": needs_visual,
            "extractor": {
                "name": "pymupdf",
                "network_used": false,
                "ocr_used": ocr_used,
                "ocr_attempted": ocr_attempted,
                "ocr_language": if ocr_attempted { json!(ocr_language) } else { Value::Null },
                "ocr_error": ocr_error.map_or(Value::Null, Value::from),
            },
        });
        let digest = sha256_hex(canonical_json(&core).as_bytes());
        core.as_object_mut()
            .unwrap()
            .insert("evidence_sha256".into(), json!(digest));
        out.push(core);
    }
    Ok(out)
}
