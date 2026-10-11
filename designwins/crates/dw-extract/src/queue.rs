//! Work planning and claim import — port of `harness/electronics/extraction.py`.
//!
//! The reference's claim iterators read a sealed corpus from disk. The
//! observable behaviour splits cleanly: claim minting over registry/record
//! values (pure — gold-gated from JSON fixtures) and integrity checks over the
//! file system (thin wrappers, tested directly). Both are ported; neither is
//! improved.

use std::collections::BTreeMap;
use std::path::Path;

use serde_json::{json, Map, Value};

use crate::canonical::canonical_json;
use crate::claims::{
    make_claim, ClaimClass, ClaimSpec, EntityGrain, EntityReference, EvidenceKind,
    EvidenceReference, FactClaim, ModelIdentity,
};
use crate::corpus::sha256_file;
use crate::sha256::sha256_hex;
use crate::ExtractError;

/// `extraction.WORK_QUEUE_SCHEMA`.
pub const WORK_QUEUE_SCHEMA: &str = "harness.electronics-extraction-work-queue.v1";
/// `extraction.SUPPORTED_ROW_DATASET_SCHEMA`.
pub const SUPPORTED_ROW_DATASET_SCHEMA: &str = "harness.pinout-vision-row-dataset.v1";
/// `extraction.GT_SECTIONS` — ground-truth sections imported as claims.
pub const GT_SECTIONS: &[&str] = &[
    "interface_atoms",
    "peripheral_depth",
    "clock_specs",
    "abs_max_ratings",
    "power_modes",
    "overview",
];

/// `extraction.UNIT_SUFFIXES` — field-name suffix to unit.
pub const UNIT_SUFFIXES: &[(&str, &str)] = &[
    ("_mhz", "MHz"),
    ("_khz", "kHz"),
    ("_hz", "Hz"),
    ("_kb", "KiB"),
    ("_mb", "MiB"),
    ("_ua", "uA"),
    ("_ma", "mA"),
    ("_c", "degC"),
    ("_v", "V"),
    ("_bits", "bit"),
];

/// `_safe_component`: path-safe component, hashed when nothing survives.
#[must_use]
pub fn safe_component(value: &Value) -> String {
    let raw = match value {
        Value::String(s) => s.trim().to_string(),
        other => other.to_string(),
    };
    let Ok(re) = fancy_regex::Regex::new(r"[^A-Za-z0-9._+-]+") else {
        return String::new();
    };
    let cleaned = re.replace_all(&raw, "-").to_string();
    let cleaned = cleaned.trim_matches(['-', '.']).to_string();
    if cleaned.is_empty() {
        sha256_hex(raw.as_bytes()).chars().take(12).collect()
    } else {
        cleaned.chars().take(80).collect()
    }
}

/// `_field_component`: lowercase field path component.
#[must_use]
pub fn field_component(value: &str) -> String {
    let Ok(re) = fancy_regex::Regex::new(r"[^a-z0-9_.-]+") else {
        return String::new();
    };
    let folded = value.to_lowercase();
    let mut output = re.replace_all(&folded, "_").to_string();
    output = output.trim_matches(['_', '.', '-']).to_string();
    let first_alpha = output.starts_with(|c: char| c.is_ascii_alphabetic());
    if output.is_empty() || !first_alpha {
        output = format!("value_{output}");
    }
    output.chars().take(128).collect()
}

/// `_unit`: unit implied by a field-name suffix.
#[must_use]
pub fn unit_for(field: &str) -> Option<&'static str> {
    let lowered = field.to_lowercase();
    UNIT_SUFFIXES
        .iter()
        .find(|(suffix, _)| lowered.ends_with(suffix))
        .map(|(_, unit)| *unit)
}

/// `_valid_value`: the reference's claim-value admission predicate.
#[must_use]
pub fn valid_value(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Number(n) => n.as_f64().is_none_or(f64::is_finite),
        Value::String(s) => !s.trim().is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
        Value::Bool(_) => true,
    }
}

/// `_pdf_uris`: document digest -> resolved file URI.
///
/// # Errors
///
/// [`ExtractError::Registry`] when `sources.pdf_root` is absent or not a
/// string.
pub fn pdf_uris(registry: &Value) -> Result<BTreeMap<String, String>, ExtractError> {
    let root_str = registry
        .get("sources")
        .and_then(|s| s.get("pdf_root"))
        .and_then(Value::as_str)
        .ok_or_else(|| ExtractError::Registry("sources.pdf_root missing".to_string()))?;
    let root = expand_resolve(Path::new(root_str));
    let mut out = BTreeMap::new();
    let empty = Vec::new();
    let documents = registry
        .get("documents")
        .and_then(Value::as_array)
        .unwrap_or(&empty);
    for document in documents {
        let paths = document.get("paths").and_then(Value::as_array);
        let Some(paths) = paths else { continue };
        let Some(first) = paths.first() else { continue };
        let Some(digest) = document.get("document_sha256").and_then(Value::as_str) else {
            continue;
        };
        let uri = root.join(first.as_str().unwrap_or(""));
        out.insert(digest.to_string(), file_uri(&uri));
    }
    Ok(out)
}

/// The ground-truth side of `_document_records` after loading: one sealed
/// summary plus its parsed value.
#[derive(Debug, Clone)]
pub struct GroundTruthRecord<'a> {
    /// The registry document this record belongs to.
    pub document: &'a Value,
    /// The ground-truth summary entry from the registry.
    pub summary: &'a Value,
    /// The parsed ground-truth JSON.
    pub value: &'a Value,
    /// The record path stem (fallback part number).
    pub stem: String,
}

/// `extraction.iter_ground_truth_claims` over one loaded record (pure core).
///
/// # Errors
///
/// [`ExtractError::Malformed`] when the record cannot name its entity.
///
/// # Lints
///
/// Long because the reference's `iter_ground_truth_claims` body is one
/// linear import loop (entity, evidence, sections, pinout); splitting at
/// arbitrary points would obscure the ported emission order. Bounded here.
#[allow(clippy::too_many_lines)]
pub fn ground_truth_claims(
    record: &GroundTruthRecord<'_>,
    source_uri: &str,
    created_at: &str,
) -> Result<Vec<FactClaim>, ExtractError> {
    let document_sha = record
        .document
        .get("document_sha256")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let artifact_sha = record
        .summary
        .get("sha256")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let record_id = record
        .summary
        .get("record_id")
        .cloned()
        .unwrap_or(Value::Null);
    let overview = record.value.get("overview");
    let part_number = overview
        .and_then(|o| o.get("part_number"))
        .cloned()
        .filter(|v| !v.is_null())
        .unwrap_or_else(|| Value::String(record.stem.clone()));
    let vendor = overview
        .and_then(|o| o.get("manufacturer"))
        .cloned()
        .filter(|v| !v.is_null())
        .or_else(|| record.summary.get("vendor").cloned())
        .unwrap_or(Value::Null);
    let vendor_component = if vendor.is_null() {
        safe_component(&json!("unknown"))
    } else {
        safe_component(&vendor)
    };
    let entity = EntityReference {
        entity_id: format!("opn:{}:{}", vendor_component, safe_component(&part_number)),
        grain: EntityGrain::Opn,
        canonical_id: value_to_string(&part_number),
        vendor: (!vendor.is_null()).then(|| value_to_string(&vendor)),
        family: None,
        package: None,
        parent_ids: Vec::new(),
    };
    let evidence = EvidenceReference {
        kind: EvidenceKind::SourceRecord,
        document_sha256: document_sha,
        source_uri: source_uri.to_string(),
        artifact_sha256: Some(artifact_sha),
        page_1based: None,
        bbox: None,
        table_index: None,
        row_index: None,
        column_index: None,
        quoted_text: None,
    };
    let mut conditions = Map::new();
    conditions.insert("source_record_id".to_string(), record_id);
    conditions.insert("page_evidence_status".to_string(), json!("not_localized"));
    let mut claims = Vec::new();
    for section in GT_SECTIONS {
        let Some(section_value) = record.value.get(section).and_then(Value::as_object) else {
            continue;
        };
        let mut names: Vec<&String> = section_value.keys().collect();
        names.sort();
        for name in names {
            let fact_value = section_value.get(name).unwrap_or(&Value::Null);
            if !valid_value(fact_value) {
                continue;
            }
            let spec = ClaimSpec {
                entity: entity.clone(),
                field: field_component(&format!("{section}.{name}")),
                value: fact_value.clone(),
                unit: unit_for(name).map(ToString::to_string),
                conditions: conditions.clone(),
                claim_class: ClaimClass::SemanticLabel,
                extraction_method: "imported_frontier_ground_truth".to_string(),
                evidence: vec![evidence.clone()],
                source_claim_ids: Vec::new(),
                model: None,
                created_at: created_at.to_string(),
            };
            claims.push(make_claim(spec)?);
        }
    }
    let Some(pinout) = record.value.get("pinout").and_then(Value::as_object) else {
        return Ok(claims);
    };
    for name in ["packages", "declared_pin_total", "rows_emitted"] {
        let Some(fact_value) = pinout.get(name) else {
            continue;
        };
        if !valid_value(fact_value) {
            continue;
        }
        let spec = ClaimSpec {
            entity: entity.clone(),
            field: field_component(&format!("pinout.{name}")),
            value: fact_value.clone(),
            unit: None,
            conditions: conditions.clone(),
            claim_class: ClaimClass::SemanticLabel,
            extraction_method: "imported_frontier_ground_truth".to_string(),
            evidence: vec![evidence.clone()],
            source_claim_ids: Vec::new(),
            model: None,
            created_at: created_at.to_string(),
        };
        claims.push(make_claim(spec)?);
    }
    Ok(claims)
}

/// `extraction.iter_pinout_row_claims` over one row example (pure core).
///
/// `pdf_uris` maps document digests to source URIs; the reference refuses a
/// row whose PDF is outside the corpus, which this mirrors.
///
/// # Errors
///
/// [`ExtractError::Malformed`] on an invalid row example;
/// [`ExtractError::Integrity`] when the PDF is outside the corpus.
///
/// # Lints
///
/// Long because `iter_pinout_row_claims` is one linear validation-and-emit
/// chain in the reference. Bounded here and nowhere else.
#[allow(clippy::too_many_lines)]
pub fn pinout_row_claims(
    example: &Value,
    pdf_uris: &BTreeMap<String, String>,
    created_at: &str,
) -> Result<Vec<FactClaim>, ExtractError> {
    let fail = |m: String| ExtractError::Malformed(m);
    let provenance = example.get("provenance").cloned().unwrap_or(Value::Null);
    let alignment = example.get("alignment").cloned().unwrap_or(Value::Null);
    let document_sha = provenance
        .get("pdf_sha256")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let source_uri = pdf_uris.get(&document_sha).cloned().ok_or_else(|| {
        ExtractError::Integrity(format!(
            "row example references a PDF outside the corpus: {document_sha}"
        ))
    })?;
    let response = example
        .get("response")
        .and_then(Value::as_str)
        .map_or(Value::Null, |s| {
            serde_json::from_str(s).unwrap_or(Value::Null)
        });
    let pins = response
        .get("pins")
        .and_then(Value::as_array)
        .filter(|&p| !p.is_empty());
    let example_id = example
        .get("example_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let Some(pins) = pins else {
        return Err(fail(format!(
            "row example has invalid response: {example_id}"
        )));
    };
    let images = example.get("images").and_then(Value::as_array);
    let image_hashes = example.get("image_sha256").and_then(Value::as_array);
    let (Some(images), Some(image_hashes)) = (images, image_hashes) else {
        return Err(fail(
            "row example must include header and body image receipts".to_string(),
        ));
    };
    if images.len() != 2 || image_hashes.len() != 2 {
        return Err(fail(
            "row example must include header and body image receipts".to_string(),
        ));
    }
    let batch_id = provenance
        .get("frontier_batch_id")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty());
    let Some(batch_id) = batch_id else {
        return Err(fail(
            "row example is missing frontier batch identity".to_string(),
        ));
    };
    let request_payload = json!({
        "prompt": example.get("prompt"),
        "image_sha256": image_hashes,
    });
    let request_sha = sha256_hex(
        &canonical_json(&request_payload).map_err(|e| ExtractError::Canonical(e.to_string()))?,
    );
    let model = ModelIdentity {
        provider: "anthropic".to_string(),
        model: "claude-sonnet-5".to_string(),
        revision: None,
        request_sha256: request_sha,
        response_id: None,
        batch_id: Some(batch_id.to_string()),
    };
    let body_hash = image_hashes
        .get(1)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let bbox = alignment
        .get("body_bbox")
        .and_then(Value::as_array)
        .map(|b| {
            let nums: Vec<f64> = b.iter().map(|v| v.as_f64().unwrap_or_default()).collect();
            [
                nums.first().copied().unwrap_or_default(),
                nums.get(1).copied().unwrap_or_default(),
                nums.get(2).copied().unwrap_or_default(),
                nums.get(3).copied().unwrap_or_default(),
            ]
        });
    let evidence = EvidenceReference {
        kind: EvidenceKind::ImageRegion,
        document_sha256: document_sha,
        source_uri,
        artifact_sha256: Some(body_hash),
        page_1based: alignment
            .get("page_1based")
            .and_then(Value::as_u64)
            .and_then(|p| u32::try_from(p).ok()),
        bbox,
        table_index: None,
        row_index: None,
        column_index: None,
        quoted_text: None,
    };
    let package = alignment
        .get("package")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim()
        .to_string();
    if package.is_empty() {
        return Err(fail(
            "row example is missing exact package identity".to_string(),
        ));
    }
    let record_id = example
        .get("record_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string();
    let package_hash: String = sha256_hex(package.as_bytes()).chars().take(12).collect();
    let mut conditions = Map::new();
    conditions.insert("package".to_string(), json!(package));
    conditions.insert(
        "source_split".to_string(),
        example.get("split").cloned().unwrap_or(Value::Null),
    );
    conditions.insert("source_example_id".to_string(), json!(example_id));
    let mut claims = Vec::new();
    for pin in pins {
        let Some(pin) = pin.as_object() else {
            return Err(fail("row example pin is not an object".to_string()));
        };
        let pin_no = pin.get("pin_no").cloned().unwrap_or(Value::Null);
        let pin_name = pin.get("name").cloned().unwrap_or(Value::Null);
        if !valid_value(&pin_no) || !valid_value(&pin_name) {
            return Err(fail(
                "row example pin has invalid physical identity".to_string(),
            ));
        }
        let entity = EntityReference {
            entity_id: format!(
                "pin:{}:{}:{}",
                safe_component(&Value::String(record_id.clone())),
                package_hash,
                safe_component(&pin_no)
            ),
            grain: EntityGrain::PinOrBall,
            canonical_id: value_to_string(&pin_no),
            vendor: record_id
                .split('_')
                .next()
                .filter(|s| !s.is_empty())
                .map(ToString::to_string),
            family: Some(record_id.clone()),
            package: Some(package.clone()),
            parent_ids: Vec::new(),
        };
        for (field, fact_value, claim_class) in [
            ("pin.number", &pin_no, ClaimClass::VisibleFact),
            ("pin.name", &pin_name, ClaimClass::VisibleFact),
            (
                "pin.type",
                &pin.get("type").cloned().unwrap_or(Value::Null),
                ClaimClass::SemanticLabel,
            ),
            (
                "pin.functions",
                &pin.get("functions").cloned().unwrap_or(Value::Null),
                ClaimClass::SemanticLabel,
            ),
            (
                "pin.direction",
                &pin.get("dir").cloned().unwrap_or(Value::Null),
                ClaimClass::SemanticLabel,
            ),
        ] {
            if !valid_value(fact_value) {
                continue;
            }
            let spec = ClaimSpec {
                entity: entity.clone(),
                field: field.to_string(),
                value: fact_value.clone(),
                unit: None,
                conditions: conditions.clone(),
                claim_class,
                extraction_method: "frontier_vision_teacher".to_string(),
                evidence: vec![evidence.clone()],
                source_claim_ids: Vec::new(),
                model: Some(model.clone()),
                created_at: created_at.to_string(),
            };
            claims.push(make_claim(spec)?);
        }
    }
    Ok(claims)
}

/// `extraction.build_extraction_work_queue` — routes per document, policy,
/// counts, and a self-digest.
///
/// # Errors
///
/// [`ExtractError::Registry`] when the registry fails verification.
///
/// # Lints
///
/// Long because the reference's routing loop is one pass over the documents
/// with four route decisions each. Bounded here and nowhere else.
#[allow(clippy::too_many_lines)]
pub fn build_extraction_work_queue(
    registry: &Value,
    aligned_document_sha256: &[String],
) -> Result<Value, ExtractError> {
    crate::corpus::verify_corpus_registry(registry)?;
    let aligned: Vec<String> = aligned_document_sha256.to_vec();
    let empty = Vec::new();
    let documents = registry
        .get("documents")
        .and_then(Value::as_array)
        .unwrap_or(&empty);
    let mut rows: Vec<Value> = Vec::new();
    let mut route_counts: BTreeMap<String, u64> = BTreeMap::new();
    for document in documents {
        let digest = document
            .get("document_sha256")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let has_gt = document
            .get("ground_truth")
            .and_then(Value::as_array)
            .is_some_and(|g| !g.is_empty());
        let mut gt_sections: Vec<String> = Vec::new();
        if let Some(gt) = document.get("ground_truth").and_then(Value::as_array) {
            for record in gt {
                if let Some(sections) = record.get("sections").and_then(Value::as_array) {
                    for s in sections.iter().filter_map(Value::as_str) {
                        if !gt_sections.contains(&s.to_string()) {
                            gt_sections.push(s.to_string());
                        }
                    }
                }
            }
        }
        let pin_route = if aligned.iter().any(|a| a == digest) {
            "owned_exact_vision_pair"
        } else if has_gt {
            "pymupdf_localize_then_local_vision"
        } else {
            "pymupdf_localize_then_local_text_or_vision"
        };
        let semantic_route = if has_gt {
            "existing_ground_truth_then_page_verify"
        } else {
            "pymupdf_then_local_text"
        };
        let parametric_sections = [
            "interface_atoms",
            "peripheral_depth",
            "clock_specs",
            "abs_max_ratings",
            "power_modes",
        ];
        let parametric_route = if parametric_sections
            .iter()
            .any(|s| gt_sections.iter().any(|g| g == s))
        {
            "existing_ground_truth_then_page_verify"
        } else {
            "pymupdf_then_local_text"
        };
        let summary_route = if gt_sections.iter().any(|g| g == "overview") {
            "existing_ground_truth_then_page_verify"
        } else {
            "pymupdf_then_local_text"
        };
        for route in [pin_route, semantic_route, parametric_route, summary_route] {
            *route_counts.entry(route.to_string()).or_insert(0) += 1;
        }
        rows.push(json!({
            "document_sha256": digest,
            "paths": document.get("paths").cloned().unwrap_or_else(|| json!([])),
            "vendors": document.get("vendors").cloned().unwrap_or_else(|| json!([])),
            "record_ids": document.get("record_ids").cloned().unwrap_or_else(|| json!([])),
            "routes": {
                "pin_or_ball": pin_route,
                "pin_semantics": semantic_route,
                "parametrics": parametric_route,
                "series_summary": summary_route,
            },
            "frontier_batch_eligibility": {
                "eligible": false,
                "required_failures": [
                    "deterministic_extractor_failed",
                    "local_model_failed_or_disagreed",
                ],
                "required_evidence": [
                    "source_document_sha256",
                    "page_or_region_receipt",
                    "local_attempt_receipt",
                ],
            },
        }));
    }
    let mut sorted_routes: Vec<(&String, &u64)> = route_counts.iter().collect();
    sorted_routes.sort();
    let routes_json: Map<String, Value> = sorted_routes
        .into_iter()
        .map(|(k, v)| (k.clone(), json!(v)))
        .collect();
    let mut core = Map::new();
    core.insert("schema".to_string(), json!(WORK_QUEUE_SCHEMA));
    core.insert(
        "policy".to_string(),
        json!({
            "ordering": [
                "existing_verified_facts",
                "pymupdf",
                "local_text_or_vision",
                "anthropic_message_batch",
            ],
            "frontier_purpose": "local_training_pair_generation",
            "direct_database_writes": false,
        }),
    );
    core.insert(
        "corpus_evidence_sha256".to_string(),
        registry
            .get("evidence_sha256")
            .cloned()
            .unwrap_or(Value::Null),
    );
    core.insert(
        "counts".to_string(),
        json!({
            "documents": rows.len(),
            "routes": Value::Object(routes_json),
        }),
    );
    core.insert("work".to_string(), Value::Array(rows));
    let digest = sha256_hex(
        &canonical_json(&Value::Object(core.clone()))
            .map_err(|e| ExtractError::Canonical(e.to_string()))?,
    );
    core.insert("evidence_sha256".to_string(), json!(digest));
    Ok(Value::Object(core))
}

/// `_load_row_manifest` — schema + artifact hash/size checks (integrity half).
///
/// # Errors
///
/// [`ExtractError::Malformed`] on schema/JSON violations,
/// [`ExtractError::Integrity`] on hash or size mismatch.
pub fn load_row_manifest(root: &Path) -> Result<Value, ExtractError> {
    let path = root.join("manifest.json");
    let raw = std::fs::read_to_string(&path)
        .map_err(|e| ExtractError::Integrity(format!("{}: {e}", path.display())))?;
    let value: Value = serde_json::from_str(&raw)
        .map_err(|e| ExtractError::Malformed(format!("manifest.json: {e}")))?;
    if value.get("schema").and_then(Value::as_str) != Some(SUPPORTED_ROW_DATASET_SCHEMA) {
        return Err(ExtractError::Malformed(
            "pinout row dataset schema is not supported".to_string(),
        ));
    }
    let empty = Map::new();
    let artifacts = value
        .get("artifacts")
        .and_then(Value::as_object)
        .unwrap_or(&empty);
    for (relative, receipt) in artifacts {
        if !relative.starts_with("canonical/") {
            continue;
        }
        let artifact = root.join(relative);
        let digest = sha256_file(&artifact)?;
        if receipt.get("sha256").and_then(Value::as_str) != Some(digest.as_str()) {
            return Err(ExtractError::Integrity(format!(
                "pinout row artifact hash mismatch: {relative}"
            )));
        }
        let size = std::fs::metadata(&artifact)
            .map_err(|e| ExtractError::Integrity(format!("{}: {e}", artifact.display())))?
            .len();
        if receipt.get("bytes").and_then(Value::as_u64) != Some(size) {
            return Err(ExtractError::Integrity(format!(
                "pinout row artifact size mismatch: {relative}"
            )));
        }
    }
    Ok(value)
}

/// `Value` to string exactly as Python's `str()` for the scalar shapes the
/// reference stringifies (`part_number`, `pin_no`, vendor).
fn value_to_string(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        Value::Bool(b) => b.to_string(),
        Value::Null => "None".to_string(),
        other => other.to_string(),
    }
}

/// `Path.resolve()`-ish: expand `~` and make absolute against the current dir.
fn expand_resolve(path: &Path) -> std::path::PathBuf {
    let as_str = path.to_string_lossy();
    if as_str.starts_with('~') {
        if let Ok(home) = std::env::var("HOME") {
            let rest = as_str.trim_start_matches('~');
            let rest = rest.strip_prefix('/').unwrap_or(rest);
            return std::path::PathBuf::from(home).join(rest);
        }
    }
    if path.is_absolute() {
        return path.to_path_buf();
    }
    std::env::current_dir().unwrap_or_default().join(path)
}

/// `Path.as_uri()` for absolute paths (the corpus evidence URIs).
fn file_uri(path: &Path) -> String {
    let mut uri = String::from("file://");
    for component in path.components() {
        if let std::path::Component::Normal(part) = component {
            uri.push('/');
            uri.push_str(&part.to_string_lossy());
        }
    }
    uri
}

#[cfg(test)]
// Test module: asserts are the point here; unwraps on fixture data are the
// documented allowance from HANDOFF.md ("Test modules may carry a documented
// #[allow]").
#[allow(clippy::unwrap_used, clippy::expect_used, clippy::indexing_slicing)]
mod tests {
    use super::*;

    /// R1: what fails this? `&`/`+` surviving in a component (regex drift) or
    /// the sha fallback not firing on `"///"`.
    #[test]
    fn safe_component_matches_reference_rules() {
        assert_eq!(safe_component(&json!(" acme ")), "acme");
        assert_eq!(safe_component(&json!("a b/c")), "a-b-c");
        assert_eq!(safe_component(&json!("---")), {
            let h: String = sha256_hex(b"---").chars().take(12).collect();
            h
        });
        let long = "x".repeat(200);
        assert_eq!(safe_component(&json!(long)).chars().count(), 80);
    }

    /// Negative control: `field_component("0abc")` must be prefixed — the
    /// reference's `^` field pattern forbids a leading digit.
    #[test]
    fn negative_control_leading_digit_field_is_prefixed() {
        assert_eq!(
            field_component("clock_specs.max_freq_mhz"),
            "clock_specs.max_freq_mhz"
        );
        assert_eq!(field_component("0abc"), "value_0abc");
        assert!(field_component("no").len() <= 128);
    }

    /// Gate: `build_extraction_work_queue` counts all four routes per document
    /// and self-digests. Failure: a route dropped or the digest stale.
    #[test]
    fn work_queue_routes_and_self_digest() {
        let mut core = json!({
            "schema": crate::corpus::SCHEMA,
            "documents": [
                {"document_sha256": "a".repeat(64), "paths": ["x.pdf"], "ground_truth": [{"sections": ["overview", "clock_specs"]}]},
                {"document_sha256": "b".repeat(64), "paths": []},
            ],
            "counts": {"unique_pdf_sha256": 2},
        });
        // verify_corpus_registry hashes everything except created_at and
        // evidence_sha256; the fixture must hash the same input.
        let digest = sha256_hex(&canonical_json(&core).unwrap());
        core.as_object_mut()
            .unwrap()
            .insert("evidence_sha256".to_string(), json!(digest));
        let queue = build_extraction_work_queue(&core, &[format!("a{}", "a".repeat(63))]).unwrap();
        assert_eq!(queue["counts"]["documents"], json!(2));
        assert_eq!(
            queue["work"][0]["routes"]["pin_or_ball"],
            json!("owned_exact_vision_pair")
        );
        assert_eq!(
            queue["work"][1]["routes"]["pin_or_ball"],
            json!("pymupdf_localize_then_local_text_or_vision")
        );
        let without = queue.as_object().unwrap().clone();
        let mut core2 = without;
        let recorded = core2.remove("evidence_sha256").unwrap();
        let recomputed = sha256_hex(&canonical_json(&Value::Object(core2)).unwrap());
        assert_eq!(recorded, json!(recomputed));
    }
}
