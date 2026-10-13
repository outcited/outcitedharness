//! The incremental corpus registry: a sealed view over newly stabilised
//! datasheet downloads (`harness/electronics/incremental_corpus.py`, ported
//! 1:1).
//!
//! A source snapshot (evidence-digested, count-checked) names the documents
//! admitted at intake; every named source must still hash and size as
//! intaked or the build refuses ("changed after intake"). Ground truth joins
//! by EXACT stem only — including the alias stems a directory vendor prefix
//! invents (`ti.com/ti_mcu.pdf` -> `ti_ti_mcu`) — and a stem carrying more
//! than one digest joins nothing. Unlike `corpus`, `_part_key` here treats
//! only `None` as absent and `_summary` has no stem vendor fallback; those
//! divergences are deliberate and gold-pinned.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

use crate::corpus::{canonical_json_sha256, canonical_vendor, sha256_file, vendor_from_stem};

/// The registry schema id (same wire as `corpus`).
const SCHEMA: &str = "harness.electronics-corpus-registry.v1";

/// The intake snapshot schema id.
const SNAPSHOT_SCHEMA: &str = "harness.electronics-incremental-source-snapshot.v1";

/// Ground-truth sections counted as active, in report order.
const ACTIVE_GROUND_TRUTH_SECTIONS: &[&str] = &[
    "pinout",
    "interface_atoms",
    "peripheral_depth",
    "clock_specs",
    "abs_max_ratings",
    "power_modes",
    "overview",
];

/// Everything the incremental build can reject.
#[derive(Debug, thiserror::Error)]
pub enum IncError {
    /// The snapshot is a symlink or not a file.
    #[error("source snapshot must be a regular non-symlink file")]
    SnapshotNotFile,
    /// The snapshot schema id is not supported.
    #[error("unsupported incremental source snapshot")]
    SnapshotSchema,
    /// The snapshot evidence self-digest does not recompute.
    #[error("incremental source snapshot evidence is invalid")]
    SnapshotEvidence,
    /// `counts.documents` does not match the document list.
    #[error("incremental source snapshot count is invalid")]
    SnapshotCount,
    /// A sidecar root is a symlink or not a directory.
    #[error("corpus sidecar root must be a real directory: {0}")]
    SidecarRoot(String),
    /// A sidecar file holds something other than an object.
    #[error("corpus sidecar JSON is not an object: {0}")]
    SidecarJson(String),
    /// A snapshot source lies outside the PDF root.
    #[error("snapshot source escapes PDF root: {0}")]
    EscapesRoot(String),
    /// A snapshot source is a symlink or not a file.
    #[error("snapshot source is unsafe: {0}")]
    SourceUnsafe(String),
    /// A snapshot source no longer matches its intake hash or size.
    #[error("snapshot source changed after intake: {0}")]
    SourceChanged(String),
    /// The PDF root is a symlink or not a directory.
    #[error("PDF root must be a real directory")]
    PdfRoot,
    /// Filesystem or JSON parse failure.
    #[error("{0}")]
    Io(String),
}

/// `str("" if value is None else value)` — only `None` is absent here (the
/// `corpus` variant folds all falsy values).
#[must_use]
pub fn part_key(value: &Value) -> Option<String> {
    let raw = match value {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(n) => {
            if n.is_u64() || n.is_i64() {
                n.to_string()
            } else {
                format!("{:?}", n.as_f64().unwrap_or_default())
            }
        }
        other => other.to_string(),
    };
    let raw: String = raw
        .trim()
        .to_uppercase()
        .chars()
        .filter(|c| c.is_alphanumeric())
        .collect();
    if raw.is_empty() {
        None
    } else {
        Some(raw)
    }
}

/// Verify and load an intake snapshot; also returns the file's own digest.
///
/// # Errors
///
/// Returns [`IncError::SnapshotNotFile`], [`IncError::SnapshotSchema`],
/// [`IncError::SnapshotEvidence`], [`IncError::SnapshotCount`] or
/// [`IncError::Io`].
pub fn verify_snapshot(path: &Path) -> Result<(Value, String), IncError> {
    let resolved = path
        .canonicalize()
        .map_err(|e| IncError::Io(format!("{}: {e}", path.display())))?;
    if fs::symlink_metadata(path).is_ok_and(|m| m.file_type().is_symlink()) || !resolved.is_file() {
        return Err(IncError::SnapshotNotFile);
    }
    let text = fs::read_to_string(&resolved)
        .map_err(|e| IncError::Io(format!("{}: {e}", resolved.display())))?;
    let value: Value = serde_json::from_str(&text).map_err(|e| IncError::Io(format!("{e}")))?;
    if value.get("schema").and_then(Value::as_str) != Some(SNAPSHOT_SCHEMA) {
        return Err(IncError::SnapshotSchema);
    }
    let mut core = Map::new();
    for (key, item) in value.as_object().into_iter().flatten() {
        if key != "created_at" && key != "evidence_sha256" {
            core.insert(key.clone(), item.clone());
        }
    }
    if value.get("evidence_sha256").and_then(Value::as_str)
        != Some(canonical_json_sha256(&Value::Object(core)).as_str())
    {
        return Err(IncError::SnapshotEvidence);
    }
    let documents = value.get("documents").and_then(Value::as_array);
    let counts_documents = value
        .get("counts")
        .and_then(Value::as_object)
        .and_then(|c| c.get("documents"))
        .cloned();
    match documents {
        Some(list) if counts_documents == Some(json!(list.len())) => {}
        _ => return Err(IncError::SnapshotCount),
    }
    let sha = sha256_file(&resolved).map_err(|e| IncError::Io(e.to_string()))?;
    Ok((value, sha))
}

/// Regular files under `root` matching `pattern`, sorted, symlinks skipped.
///
/// # Errors
///
/// Returns [`IncError::SidecarRoot`] when `root` is a symlink or not a
/// directory.
pub fn safe_files(root: &Path, pattern: &str, recursive: bool) -> Result<Vec<PathBuf>, IncError> {
    let resolved = root
        .canonicalize()
        .map_err(|_| IncError::SidecarRoot(root.display().to_string()))?;
    if fs::symlink_metadata(root).is_ok_and(|m| m.file_type().is_symlink()) || !resolved.is_dir() {
        return Err(IncError::SidecarRoot(root.display().to_string()));
    }
    let suffix = pattern.strip_prefix('*').unwrap_or(pattern);
    let mut out = Vec::new();
    collect(&resolved, recursive, suffix, &mut out)?;
    out.sort();
    Ok(out)
}

fn collect(
    dir: &Path,
    recursive: bool,
    suffix: &str,
    out: &mut Vec<PathBuf>,
) -> Result<(), IncError> {
    let mut entries: Vec<PathBuf> = fs::read_dir(dir)
        .map_err(|e| IncError::Io(format!("{}: {e}", dir.display())))?
        .filter_map(Result::ok)
        .map(|e| e.path())
        .collect();
    entries.sort();
    for path in entries {
        let Ok(meta) = fs::symlink_metadata(&path) else {
            continue;
        };
        if meta.file_type().is_symlink() {
            continue;
        }
        if meta.is_dir() {
            if recursive {
                collect(&path, recursive, suffix, out)?;
            }
            continue;
        }
        let matched = path.extension().is_some_and(|e| {
            e.to_string_lossy()
                .trim_start_matches('.')
                .eq_ignore_ascii_case(suffix.trim_start_matches('.'))
        });
        if matched {
            out.push(path.canonicalize().unwrap_or(path));
        }
    }
    Ok(())
}

/// The sidecar summary the registry records for one ground-truth or validated
/// file. Note: no stem vendor fallback (unlike `corpus::record_summary`).
///
/// # Errors
///
/// Returns [`IncError::SidecarJson`] or [`IncError::Io`].
pub fn summary(path: &Path, root: &Path) -> Result<Value, IncError> {
    let text =
        fs::read_to_string(path).map_err(|e| IncError::Io(format!("{}: {e}", path.display())))?;
    let value: Value = serde_json::from_str(&text)
        .map_err(|_| IncError::SidecarJson(path.display().to_string()))?;
    if !value.is_object() {
        return Err(IncError::SidecarJson(path.display().to_string()));
    }
    let metadata = value.get("_meta");
    let is_object = |v: Option<&Value>| v.is_some_and(Value::is_object);
    let provenance = metadata.and_then(|m| m.get("provenance"));
    let overview = value.get("overview");
    let mut part_numbers: BTreeSet<String> = BTreeSet::new();
    for candidate in [
        value.get("part_number"),
        value.get("base_mpn"),
        overview.and_then(|o| o.get("part_number")),
    ]
    .into_iter()
    .flatten()
    {
        if let Some(part) = part_key(candidate) {
            part_numbers.insert(part);
        }
    }
    let relative = path
        .strip_prefix(root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/");
    let stem = path
        .file_stem()
        .map_or(relative.as_str(), |s| s.to_str().unwrap_or(&relative))
        .to_string();
    let batch_id = metadata
        .filter(|_| is_object(metadata))
        .and_then(|m| m.get("batch_id"))
        .cloned()
        .unwrap_or(Value::Null);
    // Python: provenance being a Mapping short-circuits the fallback.
    let validation = if is_object(provenance) {
        provenance.and_then(|p| p.get("validation")).cloned()
    } else if is_object(metadata) {
        metadata.and_then(|m| m.get("validation")).cloned()
    } else {
        None
    }
    .unwrap_or(Value::Null);
    let sections: Vec<&str> = ACTIVE_GROUND_TRUTH_SECTIONS
        .iter()
        .copied()
        .filter(|section| value.get(*section).is_some())
        .collect();
    Ok(json!({
        "path": relative,
        "sha256": sha256_file(path).map_err(|e| IncError::Io(e.to_string()))?,
        "record_id": stem,
        "part_numbers": part_numbers.into_iter().collect::<Vec<_>>(),
        "vendor": overview
            .filter(|_| is_object(overview))
            .and_then(|o| o.get("manufacturer"))
            .and_then(canonical_vendor)
            .map_or(Value::Null, Value::from),
        "sections": sections,
        "batch_id": batch_id,
        "validation": validation,
    }))
}

/// A document admitted at intake and its joins.
#[derive(Default)]
struct Document {
    byte_size: u64,
    paths: BTreeSet<String>,
    stems: BTreeSet<String>,
    vendors: BTreeSet<String>,
    record_ids: BTreeSet<String>,
    ground_truth: Vec<Value>,
    published_pinouts: Vec<Value>,
    asset_memberships: BTreeMap<String, BTreeSet<String>>,
}

/// Build the sealed registry over the snapshot's admitted sources.
#[allow(
    clippy::too_many_lines,
    reason = "faithful port of build_incremental_corpus_registry"
)]
///
/// # Errors
///
/// Returns [`IncError`] on any source that no longer matches intake, escapes
/// the PDF root, or any malformed snapshot/sidecar; the reference raises
/// `ValueError` with the same message.
pub fn build_incremental_corpus_registry(
    source_snapshot: &Path,
    pdf_root: &Path,
    ground_truth_root: &Path,
    validated_root: &Path,
) -> Result<Value, IncError> {
    let (snapshot, snapshot_sha) = verify_snapshot(source_snapshot)?;
    let pdf = pdf_root.canonicalize().map_err(|_| IncError::PdfRoot)?;
    if fs::symlink_metadata(pdf_root).is_ok_and(|m| m.file_type().is_symlink()) || !pdf.is_dir() {
        return Err(IncError::PdfRoot);
    }
    let ground_truth = ground_truth_root
        .canonicalize()
        .map_err(|_| IncError::SidecarRoot(ground_truth_root.display().to_string()))?;
    let validated = validated_root
        .canonicalize()
        .map_err(|_| IncError::SidecarRoot(validated_root.display().to_string()))?;

    let mut by_digest: BTreeMap<String, Document> = BTreeMap::new();
    let mut by_stem: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for row in snapshot
        .get("documents")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let source = PathBuf::from(row["source_path"].as_str().unwrap_or_default())
            .canonicalize()
            .map_err(|e| IncError::Io(format!("{}: {e}", row["source_path"])))?;
        let relative = match source.strip_prefix(&pdf) {
            Ok(relative) => relative.to_string_lossy().replace('\\', "/"),
            Err(_) => return Err(IncError::EscapesRoot(source.display().to_string())),
        };
        if fs::symlink_metadata(&source).is_ok_and(|m| m.file_type().is_symlink())
            || !source.is_file()
        {
            return Err(IncError::SourceUnsafe(source.display().to_string()));
        }
        let digest = sha256_file(&source).map_err(|e| IncError::Io(e.to_string()))?;
        let byte_size = fs::metadata(&source).map_or(0, |m| m.len());
        if Some(Value::String(digest.clone())) != row.get("sha256").cloned()
            || Some(json!(byte_size)) != row.get("byte_size").cloned()
        {
            return Err(IncError::SourceChanged(source.display().to_string()));
        }
        let document = by_digest.entry(digest.clone()).or_insert_with(|| Document {
            byte_size,
            ..Document::default()
        });
        document.paths.insert(relative.clone());
        let stem = source
            .file_stem()
            .map_or_else(String::new, |s| s.to_string_lossy().into_owned());
        document.stems.insert(stem.clone());
        if let Some(vendor) = vendor_from_stem(&stem) {
            document.vendors.insert(vendor);
        }
        let directory_vendor = relative
            .split('/')
            .next()
            .filter(|_| relative.contains('/'))
            .and_then(|top| {
                canonical_vendor(&Value::String(
                    top.split('.').next().unwrap_or(top).to_string(),
                ))
            });
        if let Some(vendor) = directory_vendor {
            document.vendors.insert(vendor.clone());
            let alias = format!("{vendor}_{stem}").to_lowercase();
            document.stems.insert(alias.clone());
            by_stem.entry(alias).or_default().insert(digest.clone());
        }
        for binding in row
            .get("bindings")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            if !binding.is_object() {
                continue;
            }
            let record_id = binding
                .get("part_id")
                .filter(|v| !matches!(v, Value::Null))
                .or_else(|| binding.get("opn").filter(|v| !matches!(v, Value::Null)));
            let opn = binding.get("opn").filter(|v| !matches!(v, Value::Null));
            if let Some(Value::String(id)) = record_id {
                if !id.is_empty() {
                    document.record_ids.insert(id.clone());
                }
            }
            if let Some(Value::String(id)) = opn {
                if !id.is_empty() {
                    document
                        .asset_memberships
                        .entry("manifested_opn".to_string())
                        .or_default()
                        .insert(id.clone());
                }
            }
            if let Some(vendor) = binding
                .get("vendor")
                .filter(|v| !matches!(v, Value::Null))
                .and_then(canonical_vendor)
            {
                document.vendors.insert(vendor);
            }
        }
        by_stem.entry(stem).or_default().insert(digest);
    }

    let gt_files = safe_files(&ground_truth, "*.json", false)?;
    let mut gt_by_stem: BTreeMap<String, PathBuf> = BTreeMap::new();
    for path in gt_files {
        let stem = path
            .file_stem()
            .map_or_else(String::new, |s| s.to_string_lossy().into_owned());
        if by_stem.contains_key(&stem) {
            gt_by_stem.insert(stem, path);
        }
    }
    let mut validated_by_stem: BTreeMap<String, Vec<PathBuf>> = BTreeMap::new();
    for path in safe_files(&validated, "*.json", true)? {
        let stem = path
            .file_stem()
            .map_or_else(String::new, |s| s.to_string_lossy().into_owned());
        let relative = path.strip_prefix(&validated).unwrap_or(&path);
        if by_stem.contains_key(&stem)
            && !relative
                .components()
                .any(|c| c.as_os_str().to_string_lossy().starts_with('_'))
        {
            validated_by_stem.entry(stem).or_default().push(path);
        }
    }

    for (stem, digests) in &by_stem {
        if digests.len() != 1 {
            continue;
        }
        let joined = digests.iter().next().cloned().unwrap_or_default();
        let Some(document) = by_digest.get_mut(&joined) else {
            continue;
        };
        if let Some(path) = gt_by_stem.get(stem) {
            let record = summary(path, &ground_truth)?;
            if let Some(id) = record.get("record_id").and_then(Value::as_str) {
                document.record_ids.insert(id.to_string());
            }
            if let Some(vendor) = record.get("vendor").and_then(Value::as_str) {
                if !vendor.is_empty() {
                    document.vendors.insert(vendor.to_string());
                }
            }
            document.ground_truth.push(record);
        }
        for path in validated_by_stem.get(stem).into_iter().flatten() {
            let record = summary(path, &validated)?;
            if let Some(id) = record.get("record_id").and_then(Value::as_str) {
                document.record_ids.insert(id.to_string());
            }
            if let Some(vendor) = record.get("vendor").and_then(Value::as_str) {
                if !vendor.is_empty() {
                    document.vendors.insert(vendor.to_string());
                }
            }
            document.published_pinouts.push(record);
        }
    }

    let mut document_rows: Vec<Value> = Vec::new();
    let mut section_counts: BTreeMap<String, i64> = BTreeMap::new();
    for (digest, document) in &by_digest {
        for record in &document.ground_truth {
            if let Some(sections) = record.get("sections").and_then(Value::as_array) {
                for section in sections.iter().filter_map(Value::as_str) {
                    *section_counts.entry(section.to_string()).or_insert(0) += 1;
                }
            }
        }
        document_rows.push(json!({
            "document_sha256": digest,
            "byte_size": document.byte_size,
            "paths": document.paths.iter().collect::<Vec<_>>(),
            "stems": document.stems.iter().collect::<Vec<_>>(),
            "vendors": document.vendors.iter().collect::<Vec<_>>(),
            "record_ids": document.record_ids.iter().collect::<Vec<_>>(),
            "ground_truth": sorted_summaries(&document.ground_truth),
            "published_pinouts": sorted_summaries(&document.published_pinouts),
            "asset_memberships": document.asset_memberships.iter()
                .map(|(name, ids)| (name.clone(), ids.iter().collect::<Vec<_>>()))
                .collect::<BTreeMap<String, Vec<&String>>>(),
        }));
    }
    let ground_truth_count: usize = by_digest.values().map(|d| d.ground_truth.len()).sum();
    let published_count: usize = by_digest.values().map(|d| d.published_pinouts.len()).sum();
    let core = json!({
        "schema": SCHEMA,
        "policy": {
            "join_keys": ["document_sha256", "exact_source_stem"],
            "active_ground_truth_only": true,
            "active_published_pinouts_only": true,
            "symlinks_followed": false,
            "purpose": "incremental_local_training_pair_generation",
        },
        "sources": {
            "pdf_root": pdf.display().to_string(),
            "ground_truth_root": ground_truth.display().to_string(),
            "validated_root": validated.display().to_string(),
            "incremental_source_snapshot": {
                "path": source_snapshot.canonicalize().unwrap_or_else(|_| source_snapshot.to_path_buf()).display().to_string(),
                "sha256": snapshot_sha,
                "evidence_sha256": snapshot.get("evidence_sha256").cloned().unwrap_or(Value::Null),
            },
        },
        "counts": {
            "pdf_files": snapshot.get("documents").and_then(Value::as_array).map_or(0, Vec::len),
            "unique_pdf_sha256": document_rows.len(),
            "duplicate_pdf_files": snapshot.get("documents").and_then(Value::as_array).map_or(0, Vec::len).saturating_sub(document_rows.len()),
            "active_ground_truth": ground_truth_count,
            "active_published_pinouts": published_count,
            "documents_with_ground_truth": by_digest.values().filter(|d| !d.ground_truth.is_empty()).count(),
            "documents_with_published_pinouts": by_digest.values().filter(|d| !d.published_pinouts.is_empty()).count(),
            "orphan_ground_truth": 0,
            "orphan_published_pinouts": 0,
            "ground_truth_sections": section_counts,
        },
        "assets": [],
        "documents": document_rows,
        "orphans": {"ground_truth": [], "published_pinouts": []},
    });
    let mut out = core.as_object().cloned().unwrap_or_default();
    out.insert(
        "evidence_sha256".into(),
        json!(canonical_json_sha256(&core)),
    );
    let mut result = Map::new();
    result.insert("created_at".into(), Value::Null);
    result.extend(out);
    Ok(Value::Object(result))
}

/// Summaries sorted by `(path, sha256)` — the reference's report order.
fn sorted_summaries(rows: &[Value]) -> Vec<Value> {
    let mut rows = rows.to_vec();
    rows.sort_by_key(|row| {
        let path = row.get("path").and_then(Value::as_str).unwrap_or("");
        let digest = row.get("sha256").and_then(Value::as_str).unwrap_or("");
        (path.to_string(), digest.to_string())
    });
    rows
}
