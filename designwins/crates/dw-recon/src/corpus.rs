//! The corpus registry: a hash-bound join over datasheet and CR assets
//! (`harness/electronics/corpus.py`, ported 1:1).
//!
//! The module walks three roots (PDFs, ground truth, validated pinouts),
//! hashes every file, and joins rows onto documents by `document_sha256`,
//! exact source stem, or unambiguous vendor+part. Duplicate PDF bytes stay
//! ONE document carrying every path — the join must not collapse them.
//! Asset rows count `direct` / `unambiguous_part` / `ambiguous` /
//! `unresolved` apart; a source-audit PDF hash that disagrees with the live
//! corpus is an error, not a warning.
//!
//! Every `evidence_sha256` is `sha256(canonical_json(core))` where `core`
//! carries absolute paths — the gold test normalises its tree root to
//! `$ROOT` and re-derives the digest the same way on both sides, making the
//! digest a live differential check against Python's `hashlib`.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::io::Write as _;
use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};
use thiserror::Error;

use crate::sha256::sha256_hex;

/// The registry schema id.
pub const SCHEMA: &str = "harness.electronics-corpus-registry.v1";

/// Ground-truth sections counted as active, in report order.
pub const ACTIVE_GROUND_TRUTH_SECTIONS: &[&str] = &[
    "pinout",
    "interface_atoms",
    "peripheral_depth",
    "clock_specs",
    "abs_max_ratings",
    "power_modes",
    "overview",
];

/// Row keys whose string value names the record, in priority order.
const RECORD_ID_KEYS: &[&str] = &[
    "record_id",
    "queue_id",
    "part_number",
    "base_mpn",
    "series_key",
    "custom_id",
];

/// Row keys that may carry the document digest, in priority order.
const DOCUMENT_SHA_KEYS: &[&str] = &[
    "pdf_sha256",
    "source_document_sha256",
    "source_content_sha256",
];

/// Vendor spellings normalised to house names.
const VENDOR_ALIASES: &[(&str, &str)] = &[
    ("freescale/nxp", "nxp"),
    ("microchip technology", "microchip"),
    ("silicon laboratories", "silabs"),
    ("silicon labs", "silabs"),
    ("stmicroelectronics", "st"),
    ("texas instruments", "ti"),
];

/// Everything the registry build can reject.
#[derive(Debug, Error)]
pub enum CorpusError {
    /// The asset name is not `[a-z0-9_-]+`.
    #[error("invalid asset name: {0}")]
    InvalidAssetName(String),
    /// The asset kind is not `auto`/`json`/`jsonl`/`directory`.
    #[error("invalid asset kind: {0}")]
    InvalidAssetKind(String),
    /// A corpus root is a symlink or not a directory.
    #[error("corpus root must be a regular directory: {0}")]
    CorruptRoot(String),
    /// `hash_workers` outside 1..=32.
    #[error("hash_workers must be between 1 and 32")]
    HashWorkers,
    /// A JSON source path is a symlink or not a file.
    #[error("JSON source must be a regular file: {0}")]
    JsonNotFile(String),
    /// A JSON source holds something other than an object.
    #[error("JSON source must contain an object: {0}")]
    JsonNotObject(String),
    /// A `records` entry is not an object.
    #[error("{0}: records entry {1} is not an object")]
    RecordsEntry(String, usize),
    /// A JSONL line failed to parse.
    #[error("{0}: invalid JSONL at line {1}")]
    InvalidJsonl(String, usize),
    /// A JSONL line is not an object.
    #[error("{0}: line {1} is not an object")]
    JsonlLineNotObject(String, usize),
    /// `auto` could not infer the asset kind from the path.
    #[error("cannot infer asset kind for {0}")]
    CannotInferAssetKind(String),
    /// An asset directory is a symlink or not a directory.
    #[error("asset directory is invalid: {0}")]
    AssetDirInvalid(String),
    /// An asset file is a symlink or not a file.
    #[error("asset file is invalid: {0}")]
    AssetFileInvalid(String),
    /// The PDF file count is not the expected one.
    #[error("expected {0} PDFs, found {1}")]
    ExpectedPdfFiles(usize, usize),
    /// The source audit schema id is not supported.
    #[error("source audit schema is not supported")]
    SourceAuditSchema,
    /// A source audit candidate row is not an object.
    #[error("source audit candidate is malformed")]
    SourceAuditCandidate,
    /// An evidence self-digest does not recompute.
    #[error("{0} has an invalid evidence_sha256")]
    InvalidEvidenceDigest(String),
    /// The audit's expected PDF hash differs from the live corpus.
    #[error("source audit PDF hashes differ from the live corpus: {0}")]
    SourceAuditHashMismatch(String),
    /// The row dataset manifest schema id is not supported.
    #[error("row dataset manifest schema is not supported")]
    RowManifestSchema,
    /// The registry schema id is not supported.
    #[error("corpus registry schema is not supported")]
    RegistrySchema,
    /// The registry evidence self-digest does not recompute.
    #[error("corpus registry evidence digest is invalid")]
    RegistryDigest,
    /// Counts or documents are missing/mistyped.
    #[error("corpus registry is malformed")]
    RegistryMalformed,
    /// `counts.unique_pdf_sha256` does not match `documents`.
    #[error("corpus registry document count does not match")]
    RegistryCount,
    /// A document row is not an object.
    #[error("corpus registry document is malformed")]
    RegistryDocument,
    /// Two documents share a digest.
    #[error("corpus registry contains duplicate documents")]
    RegistryDuplicate,
    /// The write destination already exists.
    #[error("destination already exists: {0}")]
    DestinationExists(String),
    /// Filesystem or JSON parse failure.
    #[error("{0}")]
    Io(String),
}

/// One named asset source to join.
#[derive(Debug, Clone)]
pub struct AssetSource {
    /// Membership key written into `asset_memberships`.
    pub name: String,
    /// File or directory of JSON/JSONL rows.
    pub path: PathBuf,
    /// `auto`, `json`, `jsonl` or `directory`.
    pub kind: String,
}

impl AssetSource {
    /// Build a validated asset source (the reference validates in
    /// `__post_init__`).
    ///
    /// # Errors
    ///
    /// Returns [`CorpusError::InvalidAssetName`] when `name` is empty or has a
    /// character outside `[a-z0-9_-]`, and [`CorpusError::InvalidAssetKind`]
    /// when `kind` is not one of the four accepted spellings.
    pub fn new(name: &str, path: PathBuf, kind: &str) -> Result<Self, CorpusError> {
        if name.is_empty()
            || name
                .chars()
                .any(|c| !matches!(c, 'a'..='z' | '0'..='9' | '_' | '-'))
        {
            return Err(CorpusError::InvalidAssetName(py_repr(name)));
        }
        if !matches!(kind, "auto" | "json" | "jsonl" | "directory") {
            return Err(CorpusError::InvalidAssetKind(py_repr(kind)));
        }
        Ok(Self {
            name: name.to_string(),
            path,
            kind: kind.to_string(),
        })
    }
}

/// Where the build reads from.
#[derive(Debug, Clone)]
pub struct CorpusInputs {
    /// Root of `*.pdf` files (non-recursive).
    pub pdf_root: PathBuf,
    /// Root of active ground-truth `*.json` files (non-recursive).
    pub ground_truth_root: PathBuf,
    /// Root of validated pinout `*.json` files (recursive).
    pub validated_root: PathBuf,
    /// Optional pinout-vision source audit JSON.
    pub source_audit: Option<PathBuf>,
    /// Optional pinout-vision row dataset manifest JSON.
    pub row_dataset_manifest: Option<PathBuf>,
    /// Extra row assets to join.
    pub assets: Vec<AssetSource>,
    /// Optional guard on the PDF file count.
    pub expected_pdf_files: Option<usize>,
}

/// Python `repr` for the two interpolated-value error messages.
fn py_repr(value: &str) -> String {
    if value.contains('\'') || value.contains('\\') {
        format!("\"{}\"", value.replace('\\', "\\\\").replace('"', "\\\""))
    } else {
        format!("'{value}'")
    }
}

/// Python `str(value)` for JSON scalars (the reference interpolates it).
fn py_str(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Number(n) => {
            if n.is_u64() || n.is_i64() {
                n.to_string()
            } else {
                format!("{:?}", n.as_f64().unwrap_or_default())
            }
        }
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// Python truthiness of a JSON value (`value or ""` chains).
fn py_truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                i != 0
            } else if let Some(u) = n.as_u64() {
                u != 0
            } else {
                n.as_f64().is_some_and(|f| f != 0.0)
            }
        }
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
/// allow_nan=False)` — the digest encoding.
///
/// Sorts object keys **explicitly** rather than relying on `serde_json`'s map
/// order. `serde_json`'s `Map` is sorted only when its `preserve_order` feature
/// is off; Cargo unifies features across a workspace, so any crate that enables
/// `preserve_order` (e.g. `dw-learn`, for Python insertion-order pairs) silently
/// flips this to insertion order — changing every `evidence_sha256`. Rebuilding
/// each object with keys inserted in sorted order yields the same bytes under
/// either backend.
#[must_use]
pub fn canonical_json(value: &Value) -> String {
    serde_json::to_string(&sorted_keys(value)).unwrap_or_default()
}

/// A copy of `value` with every object's keys inserted in ascending order.
fn sorted_keys(value: &Value) -> Value {
    match value {
        Value::Array(items) => Value::Array(items.iter().map(sorted_keys).collect()),
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            let mut out = serde_json::Map::new();
            for key in keys {
                if let Some(v) = map.get(key) {
                    out.insert(key.clone(), sorted_keys(v));
                }
            }
            Value::Object(out)
        }
        other => other.clone(),
    }
}

/// SHA-256 of the canonical JSON encoding of `value`, lowercase hex.
#[must_use]
pub fn canonical_json_sha256(value: &Value) -> String {
    sha256_hex(canonical_json(value).as_bytes())
}

#[cfg(test)]
#[allow(clippy::unwrap_used, reason = "control asserts on its own literals")]
mod canonical_tests {
    use super::canonical_json;
    use serde_json::json;

    /// Negative control for the `preserve_order` hazard: `canonical_json` must
    /// sort keys itself, never inherit `serde_json::Map`'s order. A map built
    /// with deliberately descending insertion order must still encode sorted —
    /// if the explicit sort in `sorted_keys` is dropped, this goes red under
    /// either `serde_json` backend.
    #[test]
    fn canonical_json_sorts_keys_independently_of_map_order() {
        let mut map = serde_json::Map::new();
        map.insert("zeta".to_string(), json!(1));
        map.insert("alpha".to_string(), json!(2));
        map.insert("mu".to_string(), json!(3));
        assert_eq!(
            canonical_json(&serde_json::Value::Object(map)),
            r#"{"alpha":2,"mu":3,"zeta":1}"#
        );
        // Nested objects sort too (the digest walks the whole tree).
        assert_eq!(
            canonical_json(&json!({"b": {"y": 1, "x": 2}, "a": 3})),
            r#"{"a":3,"b":{"x":2,"y":1}}"#
        );
        // Array element order is preserved (positional, not keyed).
        assert_eq!(canonical_json(&json!([3, 1, 2])), "[3,1,2]");
    }
}

/// `VENDOR_ALIASES` normalisation of any printed vendor spelling.
#[must_use]
pub fn canonical_vendor(value: &Value) -> Option<String> {
    let raw = if py_truthy(value) {
        py_str(value)
    } else {
        String::new()
    };
    let raw = raw.trim().to_lowercase();
    if raw.is_empty() {
        return None;
    }
    let raw = raw.strip_suffix(".com").unwrap_or(&raw).to_string();
    Some(
        VENDOR_ALIASES
            .iter()
            .find(|(from, _)| *from == raw)
            .map_or(raw.clone(), |(_, to)| (*to).to_string()),
    )
}

/// Vendor implied by a file stem (`st_mcu` -> `st`).
#[must_use]
pub fn vendor_from_stem(stem: &str) -> Option<String> {
    let prefix = stem.split('_').next().unwrap_or(stem);
    canonical_vendor(&Value::String(prefix.to_string()))
}

/// Upper-alphanumeric fold of a part-ish value.
#[must_use]
pub fn part_key(value: &Value) -> Option<String> {
    let raw = if py_truthy(value) {
        py_str(value)
    } else {
        String::new()
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

/// `isinstance(value, (str, int, float))` — Python `bool` is an `int`.
fn is_record_id_value(value: &Value) -> bool {
    matches!(value, Value::String(_) | Value::Number(_) | Value::Bool(_))
}

/// Every `key:value` identifier the row claims, or the path fallback.
#[must_use]
pub fn record_ids(row: &Value, fallback: &str) -> Vec<String> {
    let mut output = Vec::new();
    for key in RECORD_ID_KEYS {
        if let Some(value) = row.get(*key) {
            if is_record_id_value(value) {
                let text = py_str(value);
                if !text.trim().is_empty() {
                    output.push(format!("{key}:{}", text.trim()));
                }
            }
        }
    }
    if output.is_empty() {
        vec![format!("path:{fallback}")]
    } else {
        output
    }
}

/// The document digest a row names, when it names a well-formed one.
#[must_use]
pub fn document_digest(row: &Value) -> Option<String> {
    for key in DOCUMENT_SHA_KEYS {
        if let Some(Value::String(text)) = row.get(*key) {
            if text.len() == 64
                && text
                    .chars()
                    .all(|c| c.is_ascii_digit() || (c as u8) >= b'a' && (c as u8) <= b'f')
            {
                return Some(text.clone());
            }
        }
    }
    None
}

/// Part keys a record claims (top-level `part_number` / `base_mpn` and the
/// overview's `part_number`).
#[must_use]
pub fn part_candidates(value: &Value) -> BTreeSet<String> {
    let overview = value.get("overview");
    let mut output = BTreeSet::new();
    let mut push = |candidate: Option<&Value>| {
        if let Some(key) = candidate.and_then(part_key) {
            output.insert(key);
        }
    };
    push(value.get("part_number"));
    push(value.get("base_mpn"));
    push(overview.and_then(|o| o.get("part_number")));
    output
}

/// The row summary the registry records for one ground-truth or validated
/// file. `rel_path` is the path relative to its root, POSIX-style.
#[must_use]
pub fn record_summary(rel_path: &str, digest: &str, value: &Value) -> Value {
    let metadata = value.get("_meta");
    let is_object = |v: Option<&Value>| v.is_some_and(Value::is_object);
    let provenance = metadata.and_then(|m| m.get("provenance"));
    let overview = value.get("overview");
    let stem = Path::new(rel_path)
        .file_stem()
        .map_or(rel_path, |s| s.to_str().unwrap_or(rel_path))
        .to_string();
    let vendor = overview
        .filter(|_| is_object(overview))
        .and_then(|o| o.get("manufacturer"))
        .and_then(canonical_vendor)
        .or_else(|| vendor_from_stem(&stem));
    let sections: Vec<&str> = ACTIVE_GROUND_TRUTH_SECTIONS
        .iter()
        .copied()
        .filter(|section| value.get(*section).is_some())
        .collect();
    let batch_id = metadata
        .filter(|_| is_object(metadata))
        .and_then(|m| m.get("batch_id"))
        .cloned();
    // Python: `provenance.get("validation") if isinstance(provenance, Mapping)
    // else metadata.get("validation") if ...` — a provenance object that lacks
    // the key does NOT fall through to metadata.
    let validation = if is_object(provenance) {
        provenance.and_then(|p| p.get("validation")).cloned()
    } else if is_object(metadata) {
        metadata.and_then(|m| m.get("validation")).cloned()
    } else {
        None
    };
    json!({
        "path": rel_path,
        "sha256": digest,
        "record_id": stem,
        "part_numbers": part_candidates(value).into_iter().collect::<Vec<_>>(),
        "vendor": vendor,
        "sections": sections,
        "batch_id": batch_id.unwrap_or(Value::Null),
        "validation": validation.unwrap_or(Value::Null),
    })
}

/// Recompute and check an evidence self-digest.
///
/// # Errors
///
/// Returns [`CorpusError::InvalidEvidenceDigest`] when the stored
/// `evidence_sha256` does not equal the digest of the object minus
/// `created_at` and `evidence_sha256`.
pub fn verify_evidence_digest(value: &Value, kind: &str) -> Result<(), CorpusError> {
    let expected = match value.get("evidence_sha256") {
        None | Some(Value::Null) => return Ok(()),
        Some(other) => other,
    };
    let core = strip_keys(value, &["created_at", "evidence_sha256"]);
    if expected != &Value::String(canonical_json_sha256(&core)) {
        return Err(CorpusError::InvalidEvidenceDigest(kind.to_string()));
    }
    Ok(())
}

/// `value` copied without the given keys.
fn strip_keys(value: &Value, keys: &[&str]) -> Value {
    let mut out = Map::new();
    for (key, item) in value.as_object().into_iter().flatten() {
        if !keys.contains(&key.as_str()) {
            out.insert(key.clone(), item.clone());
        }
    }
    Value::Object(out)
}

/// `sha256` of a file's bytes, lowercase hex.
///
/// # Errors
///
/// Returns [`CorpusError::Io`] when the file cannot be read.
pub fn sha256_file(path: &Path) -> Result<String, CorpusError> {
    let bytes = fs::read(path).map_err(|e| CorpusError::Io(format!("{}: {e}", path.display())))?;
    Ok(sha256_hex(&bytes))
}

/// Regular files under `root` matching `pattern`, sorted, symlinks skipped.
///
/// # Errors
///
/// Returns [`CorpusError::CorruptRoot`] when `root` is a symlink or not a
/// directory.
pub fn regular_files(
    root: &Path,
    pattern: &str,
    recursive: bool,
) -> Result<Vec<PathBuf>, CorpusError> {
    let root_meta = fs::symlink_metadata(root)
        .map_err(|_| CorpusError::CorruptRoot(root.display().to_string()))?;
    if root_meta.file_type().is_symlink() || !root.is_dir() {
        return Err(CorpusError::CorruptRoot(root.display().to_string()));
    }
    let suffix = pattern.strip_prefix("*").unwrap_or(pattern);
    let mut out = Vec::new();
    collect_files(root, recursive, suffix, &mut out)?;
    out.sort();
    Ok(out)
}

fn collect_files(
    dir: &Path,
    recursive: bool,
    suffix: &str,
    out: &mut Vec<PathBuf>,
) -> Result<(), CorpusError> {
    let mut entries: Vec<PathBuf> = Vec::new();
    for entry in
        fs::read_dir(dir).map_err(|e| CorpusError::Io(format!("{}: {e}", dir.display())))?
    {
        let path = entry
            .map_err(|e| CorpusError::Io(format!("{}: {e}", dir.display())))?
            .path();
        entries.push(path);
    }
    entries.sort();
    for path in entries {
        let Ok(meta) = fs::symlink_metadata(&path) else {
            continue;
        };
        let is_symlink = meta.file_type().is_symlink();
        if meta.is_dir() {
            if recursive && !is_symlink {
                collect_files(&path, recursive, suffix, out)?;
            }
            continue;
        }
        if is_symlink {
            continue;
        }
        let matched = if suffix.contains('*') {
            path.extension().is_some()
        } else {
            path.extension().is_some_and(|ext| {
                ext.to_string_lossy()
                    .eq_ignore_ascii_case(suffix.trim_start_matches('.'))
            }) || path
                .file_name()
                .is_some_and(|n| n.to_string_lossy().ends_with(suffix))
        };
        if matched {
            out.push(path.canonicalize().unwrap_or(path));
        }
    }
    Ok(())
}

/// Parse a JSON object file.
///
/// # Errors
///
/// Returns [`CorpusError::JsonNotFile`], [`CorpusError::JsonNotObject`] or
/// [`CorpusError::Io`].
pub fn json_object(path: &Path) -> Result<Value, CorpusError> {
    let meta = fs::symlink_metadata(path)
        .map_err(|_| CorpusError::JsonNotFile(path.display().to_string()))?;
    if meta.file_type().is_symlink() || !meta.is_file() {
        return Err(CorpusError::JsonNotFile(path.display().to_string()));
    }
    let text = fs::read_to_string(path)
        .map_err(|e| CorpusError::Io(format!("{}: {e}", path.display())))?;
    let value: Value = serde_json::from_str(&text)
        .map_err(|e| CorpusError::Io(format!("{}: {e}", path.display())))?;
    if !value.is_object() {
        return Err(CorpusError::JsonNotObject(path.display().to_string()));
    }
    Ok(value)
}

/// Every row a source file carries (`records` list or the object itself for
/// JSON; one object per non-blank line for JSONL).
///
/// # Errors
///
/// Returns [`CorpusError::RecordsEntry`],
/// [`CorpusError::InvalidJsonl`] or [`CorpusError::JsonlLineNotObject`].
pub fn rows(path: &Path, kind: &str) -> Result<Vec<Value>, CorpusError> {
    if kind == "json" {
        let value = json_object(path)?;
        if let Some(records) = value.get("records").and_then(Value::as_array) {
            let mut out = Vec::new();
            for (index, record) in records.iter().enumerate() {
                if !record.is_object() {
                    return Err(CorpusError::RecordsEntry(
                        path.display().to_string(),
                        index + 1,
                    ));
                }
                out.push(record.clone());
            }
            return Ok(out);
        }
        return Ok(vec![value]);
    }
    let text = fs::read_to_string(path)
        .map_err(|e| CorpusError::Io(format!("{}: {e}", path.display())))?;
    let mut out = Vec::new();
    for (index, line) in text.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let value: Value = serde_json::from_str(line)
            .map_err(|_| CorpusError::InvalidJsonl(path.display().to_string(), index + 1))?;
        if !value.is_object() {
            return Err(CorpusError::JsonlLineNotObject(
                path.display().to_string(),
                index + 1,
            ));
        }
        out.push(value);
    }
    Ok(out)
}

/// Kind and files of one asset source.
///
/// # Errors
///
/// Returns [`CorpusError::CannotInferAssetKind`],
/// [`CorpusError::AssetDirInvalid`] or [`CorpusError::AssetFileInvalid`].
fn asset_files(asset: &AssetSource) -> Result<(String, Vec<PathBuf>), CorpusError> {
    let path = asset
        .path
        .canonicalize()
        .map_err(|_| CorpusError::Io(format!("{}: not found", asset.path.display())))?;
    let mut kind = asset.kind.clone();
    if kind == "auto" {
        kind = if path.is_dir() {
            "directory".to_string()
        } else {
            let ext = path
                .extension()
                .map(|e| e.to_string_lossy().to_lowercase())
                .unwrap_or_default();
            match ext.as_str() {
                "jsonl" => "jsonl".to_string(),
                "json" => "json".to_string(),
                _ => {
                    return Err(CorpusError::CannotInferAssetKind(
                        path.display().to_string(),
                    ))
                }
            }
        };
    }
    if kind == "directory" {
        let meta = fs::symlink_metadata(&path)
            .map_err(|_| CorpusError::AssetDirInvalid(path.display().to_string()))?;
        if meta.file_type().is_symlink() || !path.is_dir() {
            return Err(CorpusError::AssetDirInvalid(path.display().to_string()));
        }
        let mut files = Vec::new();
        collect_asset_dir(&path, &mut files)?;
        files.sort();
        return Ok((kind, files));
    }
    let meta = fs::symlink_metadata(&path)
        .map_err(|_| CorpusError::AssetFileInvalid(path.display().to_string()))?;
    if meta.file_type().is_symlink() || !meta.is_file() {
        return Err(CorpusError::AssetFileInvalid(path.display().to_string()));
    }
    Ok((kind, vec![path]))
}

fn collect_asset_dir(dir: &Path, out: &mut Vec<PathBuf>) -> Result<(), CorpusError> {
    let mut entries: Vec<PathBuf> = fs::read_dir(dir)
        .map_err(|e| CorpusError::Io(format!("{}: {e}", dir.display())))?
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
            collect_asset_dir(&path, out)?;
            continue;
        }
        let ext = path
            .extension()
            .map(|e| e.to_string_lossy().to_lowercase())
            .unwrap_or_default();
        if ext == "json" || ext == "jsonl" {
            out.push(path.canonicalize().unwrap_or(path));
        }
    }
    Ok(())
}

/// A joined document: every path that carries the same bytes.
#[derive(Default)]
struct Document {
    byte_size: u64,
    paths: BTreeSet<String>,
    stems: BTreeSet<String>,
    vendors: BTreeSet<String>,
    ground_truth: Vec<Value>,
    published_pinouts: Vec<Value>,
    asset_memberships: BTreeMap<String, BTreeSet<String>>,
}

/// `(vendor, part)` index onto document digests; `None` vendor is the
/// vendor-less lookup.
type PartIndex = BTreeMap<(Option<String>, String), BTreeSet<String>>;

/// Join one asset's rows onto the documents, counting each route apart.
#[allow(clippy::too_many_lines, reason = "faithful port of _inventory_asset")]
fn inventory_asset(
    asset: &AssetSource,
    documents: &mut BTreeMap<String, Document>,
    part_index: &PartIndex,
) -> Result<Value, CorpusError> {
    let (kind, files) = asset_files(asset)?;
    let mut rows_total: i64 = 0;
    let mut direct_joins: i64 = 0;
    let mut part_joins: i64 = 0;
    let mut unresolved: i64 = 0;
    let mut ambiguous: i64 = 0;
    let mut unique_ids: BTreeSet<String> = BTreeSet::new();
    let mut file_receipts: Vec<Value> = Vec::new();
    for path in &files {
        let file_digest = sha256_file(path)?;
        let byte_size = fs::metadata(path).map_or(0, |m| m.len());
        file_receipts.push(json!({
            "path": path.display().to_string(),
            "sha256": file_digest,
            "bytes": byte_size,
        }));
        let row_kind = if path
            .extension()
            .is_some_and(|e| e.to_string_lossy().eq_ignore_ascii_case("jsonl"))
        {
            "jsonl"
        } else {
            "json"
        };
        for (row_index, row) in rows(path, row_kind)?.iter().enumerate() {
            rows_total += 1;
            let name = path
                .file_name()
                .map_or_else(String::new, |n| n.to_string_lossy().into_owned());
            let identifiers = record_ids(row, &format!("{}:{}", name, row_index + 1));
            unique_ids.extend(identifiers.iter().cloned());
            let mut candidates: BTreeSet<String> = BTreeSet::new();
            let mut route = "unresolved";
            let mut joined_direct = false;
            if let Some(digest) = document_digest(row) {
                if documents.contains_key(&digest) {
                    candidates.insert(digest);
                    route = "direct";
                    joined_direct = true;
                }
            }
            if !joined_direct {
                let part = row
                    .get("part_number")
                    .filter(|v| py_truthy(v))
                    .or_else(|| row.get("base_mpn").filter(|v| py_truthy(v)))
                    .or_else(|| row.get("series").filter(|v| py_truthy(v)))
                    .and_then(part_key);
                let vendor = row
                    .get("vendor")
                    .filter(|v| py_truthy(v))
                    .or_else(|| row.get("domain").filter(|v| py_truthy(v)))
                    .and_then(canonical_vendor);
                if let Some(part) = part {
                    if let Some(found) = part_index.get(&(vendor, part.clone())) {
                        candidates.extend(found.iter().cloned());
                    }
                    if candidates.is_empty() {
                        if let Some(found) = part_index.get(&(None, part)) {
                            candidates.extend(found.iter().cloned());
                        }
                    }
                    if candidates.len() == 1 {
                        route = "part";
                    }
                }
            }
            if candidates.len() == 1 {
                let joined = candidates.iter().next().cloned().unwrap_or_default();
                if let Some(document) = documents.get_mut(&joined) {
                    document
                        .asset_memberships
                        .entry(asset.name.clone())
                        .or_default()
                        .extend(identifiers.iter().cloned());
                }
                if route == "direct" {
                    direct_joins += 1;
                } else {
                    part_joins += 1;
                }
            } else {
                match candidates.len().cmp(&1) {
                    std::cmp::Ordering::Greater => ambiguous += 1,
                    _ => unresolved += 1,
                }
            }
        }
    }
    Ok(json!({
        "name": asset.name,
        "kind": kind,
        "files": file_receipts,
        "rows": rows_total,
        "unique_record_identifiers": unique_ids.len(),
        "joins": {
            "document_sha256": direct_joins,
            "unambiguous_part": part_joins,
            "ambiguous": ambiguous,
            "unresolved": unresolved,
        },
    }))
}

/// Build the hash-bound join over the corpus roots.
#[allow(
    clippy::too_many_lines,
    reason = "faithful port of build_corpus_registry"
)]
///
/// # Errors
///
/// Returns [`CorpusError`] on any malformed source, symlink, hash mismatch or
/// count mismatch; the reference raises `ValueError` with the same message.
pub fn build_corpus_registry(
    inputs: &CorpusInputs,
    hash_workers: usize,
) -> Result<Value, CorpusError> {
    if !(1..=32).contains(&hash_workers) {
        return Err(CorpusError::HashWorkers);
    }
    let pdf_root = inputs
        .pdf_root
        .canonicalize()
        .map_err(|_| CorpusError::CorruptRoot(inputs.pdf_root.display().to_string()))?;
    let gt_root = inputs
        .ground_truth_root
        .canonicalize()
        .map_err(|_| CorpusError::CorruptRoot(inputs.ground_truth_root.display().to_string()))?;
    let validated_root = inputs
        .validated_root
        .canonicalize()
        .map_err(|_| CorpusError::CorruptRoot(inputs.validated_root.display().to_string()))?;
    let pdf_paths = regular_files(&pdf_root, "*.pdf", false)?;
    if let Some(expected) = inputs.expected_pdf_files {
        if pdf_paths.len() != expected {
            return Err(CorpusError::ExpectedPdfFiles(expected, pdf_paths.len()));
        }
    }
    let gt_paths = regular_files(&gt_root, "*.json", false)?;
    let mut validated_paths = Vec::new();
    for path in regular_files(&validated_root, "*.json", true)? {
        let relative = path.strip_prefix(&validated_root).unwrap_or(&path);
        if !relative
            .components()
            .any(|c| c.as_os_str().to_string_lossy().starts_with('_'))
        {
            validated_paths.push(path);
        }
    }

    let primary: Vec<&PathBuf> = pdf_paths
        .iter()
        .chain(gt_paths.iter())
        .chain(validated_paths.iter())
        .collect();
    let mut hashes: BTreeMap<PathBuf, String> = BTreeMap::new();
    for path in primary {
        hashes.insert((*path).clone(), sha256_file(path)?);
    }
    let (source_by_pdf, source_by_published) = load_source_audit(inputs.source_audit.as_deref())?;

    let mut documents: BTreeMap<String, Document> = BTreeMap::new();
    let mut pdf_by_name: BTreeMap<String, String> = BTreeMap::new();
    let mut pdf_by_stem: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for path in &pdf_paths {
        let digest = hashes.get(path).cloned().unwrap_or_default();
        let document = documents.entry(digest.clone()).or_insert_with(|| Document {
            byte_size: fs::metadata(path).map_or(0, |m| m.len()),
            ..Document::default()
        });
        let relative = path
            .strip_prefix(&pdf_root)
            .unwrap_or(path)
            .to_string_lossy()
            .replace('\\', "/");
        document.paths.insert(relative);
        if let Some(stem) = path.file_stem() {
            let stem = stem.to_string_lossy().into_owned();
            if let Some(vendor) = vendor_from_stem(&stem) {
                document.vendors.insert(vendor);
            }
            document.stems.insert(stem.clone());
            pdf_by_stem.entry(stem).or_default().insert(digest.clone());
        }
        if let Some(name) = path.file_name() {
            pdf_by_name.insert(name.to_string_lossy().into_owned(), digest);
        }
    }

    let mut expected_hash_mismatches: Vec<Value> = Vec::new();
    let mut part_index: PartIndex = BTreeMap::new();
    let mut orphan_gt: Vec<Value> = Vec::new();
    for path in &gt_paths {
        let value = json_object(path)?;
        let digest = hashes.get(path).cloned().unwrap_or_default();
        let relative = path
            .strip_prefix(&gt_root)
            .unwrap_or(path)
            .to_string_lossy()
            .replace('\\', "/");
        let summary = record_summary(&relative, &digest, &value);
        let stem = path
            .file_stem()
            .map_or_else(String::new, |s| s.to_string_lossy().into_owned());
        let mut candidates: BTreeSet<String> = pdf_by_stem.get(&stem).cloned().unwrap_or_default();
        let audit_key = format!("{stem}.pdf");
        let audit_rows = source_by_pdf.get(&audit_key);
        let mut audit_list: Vec<&Value> = Vec::new();
        match audit_rows {
            Some(Value::Array(list)) => audit_list.extend(list.iter()),
            Some(single) => audit_list.push(single),
            None => {}
        }
        for audit in audit_list {
            if !audit.is_object() {
                return Err(CorpusError::SourceAuditCandidate);
            }
            let expected = audit.get("pdf_sha256");
            let actual = audit
                .get("pdf_path")
                .and_then(Value::as_str)
                .and_then(|name| pdf_by_name.get(name).cloned());
            if let Some(actual) = actual {
                candidates.insert(actual.clone());
                if let Some(Value::String(expected)) = expected {
                    if *expected != actual {
                        expected_hash_mismatches.push(json!({
                            "path": audit.get("pdf_path").cloned().unwrap_or(Value::Null),
                            "expected": expected,
                            "actual": actual,
                        }));
                    }
                }
            }
        }
        if candidates.len() == 1 {
            let joined = candidates.iter().next().cloned().unwrap_or_default();
            if let Some(document) = documents.get_mut(&joined) {
                document.ground_truth.push(summary.clone());
                if let Some(vendor) = summary.get("vendor").and_then(Value::as_str) {
                    document.vendors.insert(vendor.to_string());
                }
            }
            let vendor = summary
                .get("vendor")
                .and_then(Value::as_str)
                .filter(|v| !v.is_empty())
                .map(str::to_string);
            for part in summary
                .get("part_numbers")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
            {
                part_index
                    .entry((vendor.clone(), part.to_string()))
                    .or_default()
                    .insert(joined.clone());
                part_index
                    .entry((None, part.to_string()))
                    .or_default()
                    .insert(joined.clone());
            }
        } else {
            let reason = if candidates.is_empty() {
                "no_matching_pdf_stem"
            } else {
                "ambiguous_pdf_stem"
            };
            let mut row = summary.as_object().cloned().unwrap_or_default();
            row.insert("reason".into(), json!(reason));
            orphan_gt.push(Value::Object(row));
        }
    }

    let mut orphan_published: Vec<Value> = Vec::new();
    for path in &validated_paths {
        let value = json_object(path)?;
        let digest = hashes.get(path).cloned().unwrap_or_default();
        let relative = path
            .strip_prefix(&validated_root)
            .unwrap_or(path)
            .to_string_lossy()
            .replace('\\', "/");
        let summary = record_summary(&relative, &digest, &value);
        let stem = path
            .file_stem()
            .map_or_else(String::new, |s| s.to_string_lossy().into_owned());
        let mut candidates: BTreeSet<String> = pdf_by_stem.get(&stem).cloned().unwrap_or_default();
        if let Some(audit) = source_by_published.get(&relative) {
            if let Some(Value::String(expected)) = audit.get("pdf_sha256") {
                if documents.contains_key(expected) {
                    candidates.insert(expected.clone());
                }
            }
        }
        if candidates.len() == 1 {
            let joined = candidates.iter().next().cloned().unwrap_or_default();
            if let Some(document) = documents.get_mut(&joined) {
                document.published_pinouts.push(summary.clone());
                if let Some(vendor) = summary.get("vendor").and_then(Value::as_str) {
                    document.vendors.insert(vendor.to_string());
                }
            }
            let vendor = summary
                .get("vendor")
                .and_then(Value::as_str)
                .filter(|v| !v.is_empty())
                .map(str::to_string);
            for part in summary
                .get("part_numbers")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
            {
                part_index
                    .entry((vendor.clone(), part.to_string()))
                    .or_default()
                    .insert(joined.clone());
                part_index
                    .entry((None, part.to_string()))
                    .or_default()
                    .insert(joined.clone());
            }
        } else {
            let reason = if candidates.is_empty() {
                "no_matching_pdf_stem"
            } else {
                "ambiguous_pdf_stem"
            };
            let mut row = summary.as_object().cloned().unwrap_or_default();
            row.insert("reason".into(), json!(reason));
            orphan_published.push(Value::Object(row));
        }
    }

    if !expected_hash_mismatches.is_empty() {
        let shown: Vec<String> = expected_hash_mismatches
            .iter()
            .take(3)
            .map(|row| {
                format!(
                    "{{'path': {}, 'expected': {}, 'actual': {}}}",
                    py_repr(row["path"].as_str().unwrap_or_default()),
                    py_repr(row["expected"].as_str().unwrap_or_default()),
                    py_repr(row["actual"].as_str().unwrap_or_default()),
                )
            })
            .collect();
        return Err(CorpusError::SourceAuditHashMismatch(format!(
            "[{}]",
            shown.join(", ")
        )));
    }

    let mut asset_reports: Vec<Value> = Vec::new();
    for asset in &inputs.assets {
        asset_reports.push(inventory_asset(asset, &mut documents, &part_index)?);
    }

    let mut source_receipts = Map::new();
    source_receipts.insert("pdf_root".into(), json!(pdf_root.display().to_string()));
    source_receipts.insert(
        "ground_truth_root".into(),
        json!(gt_root.display().to_string()),
    );
    source_receipts.insert(
        "validated_root".into(),
        json!(validated_root.display().to_string()),
    );
    if let Some(audit_path) = &inputs.source_audit {
        let path = audit_path
            .canonicalize()
            .map_err(|_| CorpusError::Io(format!("{}: not found", audit_path.display())))?;
        source_receipts.insert(
            "source_audit".into(),
            json!({
                "path": path.display().to_string(),
                "sha256": sha256_file(&path)?,
                "bytes": fs::metadata(&path).map_or(0, |m| m.len()),
            }),
        );
    }
    if let Some(manifest_path) = &inputs.row_dataset_manifest {
        let path = manifest_path
            .canonicalize()
            .map_err(|_| CorpusError::Io(format!("{}: not found", manifest_path.display())))?;
        let manifest = json_object(&path)?;
        if manifest.get("schema").and_then(Value::as_str)
            != Some("harness.pinout-vision-row-dataset.v1")
        {
            return Err(CorpusError::RowManifestSchema);
        }
        source_receipts.insert(
            "row_dataset_manifest".into(),
            json!({
                "path": path.display().to_string(),
                "sha256": sha256_file(&path)?,
                "bytes": fs::metadata(&path).map_or(0, |m| m.len()),
                "evidence_sha256": manifest.get("evidence_sha256").cloned().unwrap_or(Value::Null),
                "counts": manifest.get("counts").cloned().unwrap_or(Value::Null),
            }),
        );
    }

    let mut document_rows: Vec<Value> = Vec::new();
    for (digest, document) in &documents {
        let mut record_ids: BTreeSet<String> = BTreeSet::new();
        for record in document
            .ground_truth
            .iter()
            .chain(&document.published_pinouts)
        {
            if let Some(Value::String(id)) = record.get("record_id") {
                record_ids.insert(id.clone());
            }
        }
        document_rows.push(json!({
            "document_sha256": digest,
            "byte_size": document.byte_size,
            "paths": document.paths.iter().collect::<Vec<_>>(),
            "stems": document.stems.iter().collect::<Vec<_>>(),
            "vendors": document.vendors.iter().collect::<Vec<_>>(),
            "record_ids": record_ids.iter().collect::<Vec<_>>(),
            "ground_truth": sorted_summaries(&document.ground_truth),
            "published_pinouts": sorted_summaries(&document.published_pinouts),
            "asset_memberships": document.asset_memberships.iter()
                .map(|(name, ids)| (name.clone(), ids.iter().collect::<Vec<_>>()))
                .collect::<BTreeMap<String, Vec<&String>>>(),
        }));
    }

    let mut section_counts: BTreeMap<String, i64> = BTreeMap::new();
    for document in documents.values() {
        for record in &document.ground_truth {
            if let Some(sections) = record.get("sections").and_then(Value::as_array) {
                for section in sections.iter().filter_map(Value::as_str) {
                    *section_counts.entry(section.to_string()).or_insert(0) += 1;
                }
            }
        }
    }

    let core = json!({
        "schema": SCHEMA,
        "policy": {
            "join_keys": ["document_sha256", "exact_source_stem", "unambiguous_vendor_and_part"],
            "active_ground_truth_only": true,
            "active_published_pinouts_only": true,
            "symlinks_followed": false,
            "purpose": "local_training_pair_generation",
        },
        "sources": source_receipts,
        "counts": {
            "pdf_files": pdf_paths.len(),
            "unique_pdf_sha256": documents.len(),
            "duplicate_pdf_files": pdf_paths.len().saturating_sub(documents.len()),
            "active_ground_truth": gt_paths.len(),
            "active_published_pinouts": validated_paths.len(),
            "documents_with_ground_truth": documents.values().filter(|d| !d.ground_truth.is_empty()).count(),
            "documents_with_published_pinouts": documents.values().filter(|d| !d.published_pinouts.is_empty()).count(),
            "orphan_ground_truth": orphan_gt.len(),
            "orphan_published_pinouts": orphan_published.len(),
            "ground_truth_sections": section_counts,
        },
        "assets": asset_reports,
        "documents": document_rows,
        "orphans": {
            "ground_truth": orphan_gt,
            "published_pinouts": orphan_published,
        },
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

/// `pdf_path` -> row(s) and `published_path` -> row of the source audit.
type SourceAudit = (BTreeMap<String, Value>, BTreeMap<String, Value>);

/// `pdf_path` -> row(s) and `published_path` -> row of the source audit.
fn load_source_audit(path: Option<&Path>) -> Result<SourceAudit, CorpusError> {
    let Some(path) = path else {
        return Ok((BTreeMap::new(), BTreeMap::new()));
    };
    let value = json_object(path)?;
    if value.get("schema").and_then(Value::as_str) != Some("harness.pinout-vision-source-audit.v1")
    {
        return Err(CorpusError::SourceAuditSchema);
    }
    verify_evidence_digest(&value, "source audit")?;
    let mut by_pdf: BTreeMap<String, Value> = BTreeMap::new();
    let mut by_published: BTreeMap<String, Value> = BTreeMap::new();
    for row in value
        .get("candidates")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        if !row.is_object() {
            return Err(CorpusError::SourceAuditCandidate);
        }
        if let Some(Value::String(pdf_path)) = row.get("pdf_path") {
            // One PDF can legitimately back multiple package records.
            match by_pdf.get_mut(pdf_path) {
                Some(Value::Array(list)) => list.push(row.clone()),
                Some(existing) => {
                    let previous = existing.clone();
                    *existing = Value::Array(vec![previous, row.clone()]);
                }
                None => {
                    by_pdf.insert(pdf_path.clone(), row.clone());
                }
            }
        }
        if let Some(Value::String(published_path)) = row.get("published_path") {
            by_published.insert(published_path.clone(), row.clone());
        }
    }
    Ok((by_pdf, by_published))
}

/// Registry self-check: schema, evidence digest, document count, duplicates.
///
/// # Errors
///
/// Returns [`CorpusError::RegistrySchema`], [`CorpusError::RegistryDigest`],
/// [`CorpusError::RegistryMalformed`], [`CorpusError::RegistryCount`] or
/// [`CorpusError::RegistryDuplicate`].
pub fn verify_corpus_registry(value: &Value) -> Result<(), CorpusError> {
    if value.get("schema").and_then(Value::as_str) != Some(SCHEMA) {
        return Err(CorpusError::RegistrySchema);
    }
    let expected = value.get("evidence_sha256").cloned().unwrap_or(Value::Null);
    // The digest was calculated before the digest field was attached.
    let digest_input = strip_keys(value, &["created_at", "evidence_sha256"]);
    if expected != Value::String(canonical_json_sha256(&digest_input)) {
        return Err(CorpusError::RegistryDigest);
    }
    let counts = value.get("counts");
    let documents = value.get("documents");
    if !counts.is_some_and(Value::is_object) || !documents.is_some_and(Value::is_array) {
        return Err(CorpusError::RegistryMalformed);
    }
    let unique = counts.and_then(|c| c.get("unique_pdf_sha256")).cloned();
    let documents = documents
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    if unique != Some(json!(documents.len())) {
        return Err(CorpusError::RegistryCount);
    }
    let mut seen: Vec<Value> = Vec::new();
    for document in &documents {
        if !document.is_object() {
            return Err(CorpusError::RegistryDocument);
        }
        let digest = document
            .get("document_sha256")
            .cloned()
            .unwrap_or(Value::Null);
        // Python stores `str(digest)` and tests `digest in seen` — a non-str
        // digest never collides with its own stringified form.
        if seen.contains(&digest) {
            return Err(CorpusError::RegistryDuplicate);
        }
        seen.push(Value::String(py_str(&digest)));
    }
    Ok(())
}

/// Write the registry exactly once, read-only.
///
/// # Errors
///
/// Returns [`CorpusError::DestinationExists`] when the destination exists,
/// [`CorpusError::RegistryDigest`] or friends when the value fails
/// [`verify_corpus_registry`], or [`CorpusError::Io`].
pub fn write_new_registry(path: &Path, value: &Value) -> Result<(), CorpusError> {
    verify_corpus_registry(value)?;
    let destination = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    if destination.exists() || fs::symlink_metadata(&destination).is_ok() {
        return Err(CorpusError::DestinationExists(
            destination.display().to_string(),
        ));
    }
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| CorpusError::Io(format!("{}: {e}", parent.display())))?;
    }
    let payload =
        serde_json::to_string_pretty(value).map_err(|e| CorpusError::Io(e.to_string()))? + "\n";
    let mut file = fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&destination)
        .map_err(|e| CorpusError::Io(format!("{}: {e}", destination.display())))?;
    file.write_all(payload.as_bytes())
        .map_err(|e| CorpusError::Io(format!("{}: {e}", destination.display())))?;
    file.sync_all()
        .map_err(|e| CorpusError::Io(format!("{}: {e}", destination.display())))?;
    drop(file);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&destination, fs::Permissions::from_mode(0o444))
            .map_err(|e| CorpusError::Io(format!("{}: {e}", destination.display())))?;
    }
    Ok(())
}
