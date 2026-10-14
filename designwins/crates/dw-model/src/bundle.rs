//! Sealed Anthropic Message Batch preparation — port of
//! `prepare_batch_bundle` + `_request` in `harness/electronics/frontier_batch.py`.
//!
//! Packages frontier candidates into a write-once bundle directory:
//! `candidates.jsonl` + `requests.jsonl` (canonical JSON lines) sealed by a
//! `manifest.json` whose `evidence_sha256` covers the manifest core. Files are
//! created exclusively, fsynced, chmod `0444`; the directory is chmod `0555`
//! and atomically renamed into place. Any failure tears the staging tree down
//! and leaves no partial bundle.
//!
//! The Pydantic *construction-time* validators (bbox monotonicity, image
//! evidence presence, completed local attempts, …) stay on the Python side —
//! candidates arrive here already validated, exactly as the reference receives
//! them. What this port enforces is everything the reference checks at
//! preparation time: candidate identity, duplicate ids, evidence resolution
//! (regular file, under an allowed root, SHA-256 match), spend cap, and the
//! sealed-write protocol itself.

use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::io::Write as _;
use std::os::unix::fs::PermissionsExt as _;
use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

use dw_extract::canonical::{canonical_json, python_float_repr, stable_id, CanonicalError};
use dw_extract::sha256::sha256_hex;

/// `PREPARED_SCHEMA` in the reference.
pub const PREPARED_SCHEMA: &str = "harness.electronics-frontier-batch.v1";

/// The fixed base of the teacher system prompt (verbatim).
const SYSTEM_BASE: &str = "You extract electronics facts from supplied source evidence. Do not infer an absent value. Preserve printed units and package identity. Your output is teacher data for a local model and will be independently verified before admission.";

/// Inputs to [`prepare_batch_bundle`]. `candidates` are wire-shaped
/// (`model_dump(mode="json", by_alias=True)`) frontier-candidate objects.
pub struct BundleSpec<'a> {
    /// Destination bundle directory; must not exist yet.
    pub destination: &'a Path,
    /// Wire-shaped candidate objects.
    pub candidates: &'a [Value],
    /// Non-blank model name for every request.
    pub model: &'a str,
    /// Evidence files must resolve under one of these roots.
    pub allowed_roots: &'a [PathBuf],
    /// USD per million input tokens; finite and positive.
    pub input_price_per_million: f64,
    /// USD per million output tokens; finite and positive.
    pub output_price_per_million: f64,
    /// Batch discount multiplier within `(0, 1]`.
    pub batch_discount: f64,
    /// Maximum estimated cost in USD; finite and positive.
    pub spend_cap_usd: f64,
    /// `created_at` as the caller's ISO-8601 string (the reference takes a
    /// `datetime` and calls `.isoformat()`).
    pub created_at: &'a str,
}

/// A preparation failure; every message is byte-identical to the reference's
/// `ValueError` strings.
#[derive(Debug, thiserror::Error)]
pub enum BundleError {
    /// Blank model name.
    #[error("model cannot be blank")]
    BlankModel,
    /// Non-finite or non-positive price / cap.
    #[error("{0} must be finite and positive")]
    InvalidPrice(&'static str),
    /// Discount outside `(0, 1]`.
    #[error("batch_discount must be within (0, 1]")]
    InvalidDiscount,
    /// Destination already exists (or is a symlink).
    #[error("batch bundle already exists: {0}")]
    DestinationExists(String),
    /// `candidate_id` does not match the identity digest.
    #[error("candidate identity mismatch: {0}")]
    IdentityMismatch(String),
    /// The same candidate appeared twice.
    #[error("duplicate candidate: {0}")]
    DuplicateCandidate(String),
    /// Two requests produced the same `custom_id`.
    #[error("duplicate custom_id: {0}")]
    DuplicateCustomId(String),
    /// No candidates supplied.
    #[error("cannot prepare an empty frontier batch")]
    EmptyBatch,
    /// Estimated cost above the cap.
    #[error("estimated batch cost ${cost:.6} exceeds ${cap:.6} cap")]
    CostCapExceeded {
        /// Estimated maximum cost.
        cost: f64,
        /// The configured cap.
        cap: f64,
    },
    /// Evidence path resolves to something that is not a regular file.
    #[error("evidence is not a regular file: {0}")]
    NotRegularFile(String),
    /// Evidence path resolves outside every allowed root.
    #[error("evidence is outside allowed roots: {0}")]
    OutsideRoots(String),
    /// Evidence bytes do not hash to the recorded SHA-256.
    #[error("evidence SHA-256 mismatch: {0}")]
    ShaMismatch(String),
    /// A strict path resolution failed; carries Python's errno message shape.
    #[error("{0}")]
    ResolveFailed(String),
    /// Canonical encoding rejected a non-finite float.
    #[error(transparent)]
    Canonical(#[from] CanonicalError),
    /// Underlying filesystem error with context.
    #[error("{context}: {source}")]
    Io {
        /// What was being attempted.
        context: String,
        /// The OS error.
        source: std::io::Error,
    },
}

/// Prepare a sealed batch bundle; returns the written manifest.
///
/// # Errors
///
/// Any [`BundleError`]; on error the staging directory is removed and the
/// destination is left absent.
pub fn prepare_batch_bundle(spec: &BundleSpec) -> Result<Value, BundleError> {
    if spec.model.trim().is_empty() {
        return Err(BundleError::BlankModel);
    }
    for (value, name) in [
        (spec.input_price_per_million, "input_price_per_million"),
        (spec.output_price_per_million, "output_price_per_million"),
        (spec.spend_cap_usd, "spend_cap_usd"),
    ] {
        if !value.is_finite() || value <= 0.0 {
            return Err(BundleError::InvalidPrice(name));
        }
    }
    if !spec.batch_discount.is_finite() || spec.batch_discount <= 0.0 || spec.batch_discount > 1.0 {
        return Err(BundleError::InvalidDiscount);
    }
    let output =
        std::fs::canonicalize(spec.destination).unwrap_or_else(|_| spec.destination.to_path_buf());
    // `symlink_metadata` succeeding covers an existing path OR a dangling
    // symlink — both refuse the write, matching the reference's
    // `exists() or is_symlink()` on the resolved destination.
    if output.symlink_metadata().is_ok() {
        return Err(BundleError::DestinationExists(output.display().to_string()));
    }
    if let Some(parent) = output.parent() {
        std::fs::create_dir_all(parent).map_err(|source| BundleError::Io {
            context: format!("create parent {}", parent.display()),
            source,
        })?;
    }

    // Staging directory next to the destination (the reference uses mkdtemp
    // with a random suffix; the name never escapes — it is renamed away).
    let mut staging = None;
    for attempt in 0..100 {
        let candidate = output.with_file_name(format!(
            ".{}.{}-{}",
            file_name_of(&output),
            std::process::id(),
            attempt
        ));
        match std::fs::create_dir(&candidate) {
            Ok(()) => {
                staging = Some(candidate);
                break;
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(source) => {
                return Err(BundleError::Io {
                    context: format!("create staging dir {}", candidate.display()),
                    source,
                })
            }
        }
    }
    let staging = staging.ok_or_else(|| BundleError::Io {
        context: "staging dir exhausted".to_string(),
        source: std::io::Error::new(std::io::ErrorKind::AlreadyExists, "no free staging name"),
    })?;

    match build_bundle(&staging, spec) {
        Ok(manifest) => Ok(manifest),
        Err(error) => {
            // The reference: chmod 0755 (best effort) then rmtree, re-raise.
            let _ = std::fs::set_permissions(&staging, std::fs::Permissions::from_mode(0o755));
            let _ = std::fs::remove_dir_all(&staging);
            Err(error)
        }
    }
}

/// Write the two JSONL files, seal the manifest, apply modes, rename home.
fn build_bundle(staging: &Path, spec: &BundleSpec) -> Result<Value, BundleError> {
    let candidates_path = staging.join("candidates.jsonl");
    let requests_path = staging.join("requests.jsonl");
    let mut candidate_handle = create_exclusive(&candidates_path)?;
    let mut request_handle = create_exclusive(&requests_path)?;

    let roots: Vec<PathBuf> = spec
        .allowed_roots
        .iter()
        .map(|r| resolve_strict(r))
        .collect::<Result<_, _>>()?;

    let mut seen_candidates: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
    let mut seen_custom: std::collections::BTreeSet<String> = std::collections::BTreeSet::new();
    let mut total_input: u64 = 0;
    let mut total_output: u64 = 0;
    let mut capability_counts: BTreeMap<&str, u64> = BTreeMap::new();

    let mut outcome = Ok(());
    for candidate in spec.candidates {
        match process_candidate(candidate, spec, &roots) {
            Ok((request, custom_id)) => {
                outcome = record_candidate(
                    candidate,
                    &request,
                    &custom_id,
                    &mut candidate_handle,
                    &mut request_handle,
                    &mut seen_candidates,
                    &mut seen_custom,
                    &mut total_input,
                    &mut total_output,
                    &mut capability_counts,
                );
                if outcome.is_err() {
                    break;
                }
            }
            Err(error) => {
                outcome = Err(error);
                break;
            }
        }
    }
    outcome?;
    candidate_handle
        .sync_all()
        .map_err(|source| io_err("fsync candidates.jsonl", source))?;
    request_handle
        .sync_all()
        .map_err(|source| io_err("fsync requests.jsonl", source))?;

    // Python multiplies ints by floats directly; token counts sit far below
    // 2^53 so the conversion is exact.
    #[allow(
        clippy::cast_precision_loss,
        reason = "token counts are far below 2^53; matches Python int*float"
    )]
    let cost = |tokens: u64, price: f64| tokens as f64 * price / 1_000_000.0;
    let estimated_cost = spec.batch_discount
        * (cost(total_input, spec.input_price_per_million)
            + cost(total_output, spec.output_price_per_million));
    if seen_candidates.is_empty() {
        return Err(BundleError::EmptyBatch);
    }
    if estimated_cost > spec.spend_cap_usd {
        return Err(BundleError::CostCapExceeded {
            cost: estimated_cost,
            cap: spec.spend_cap_usd,
        });
    }

    let requests_count = seen_candidates.len();
    let manifest = assemble_manifest(
        spec,
        &ManifestTotals {
            requests: requests_count,
            total_input,
            total_output,
            estimated_cost,
            capabilities: &capability_counts,
            candidates_path: &candidates_path,
            requests_path: &requests_path,
        },
    )?;

    let manifest_path = staging.join("manifest.json");
    let mut manifest_handle = create_exclusive(&manifest_path)?;
    let manifest_bytes = py_dumps_indent2_sorted(&manifest)?;
    manifest_handle
        .write_all(manifest_bytes.as_bytes())
        .map_err(|source| io_err("write manifest.json", source))?;
    manifest_handle
        .write_all(b"\n")
        .map_err(|source| io_err("write manifest.json", source))?;
    manifest_handle
        .sync_all()
        .map_err(|source| io_err("fsync manifest.json", source))?;
    drop(manifest_handle);

    set_mode(&manifest_path, 0o444)?;
    set_mode(&candidates_path, 0o444)?;
    set_mode(&requests_path, 0o444)?;
    set_mode(staging, 0o555)?;
    std::fs::rename(staging, spec.destination).map_err(|source| BundleError::Io {
        context: format!("rename into {}", spec.destination.display()),
        source,
    })?;
    Ok(manifest)
}

/// Totals and artifact paths feeding the manifest core.
struct ManifestTotals<'a> {
    requests: usize,
    total_input: u64,
    total_output: u64,
    estimated_cost: f64,
    capabilities: &'a BTreeMap<&'a str, u64>,
    candidates_path: &'a Path,
    requests_path: &'a Path,
}

/// Build the manifest object: the core (schema/pricing/budget/counts/
/// artifacts), its `evidence_sha256`, plus `created_at`.
fn assemble_manifest(spec: &BundleSpec, totals: &ManifestTotals) -> Result<Value, BundleError> {
    let core = json!({
        "schema": PREPARED_SCHEMA,
        "purpose": "local_training_pair_generation",
        "model": spec.model,
        "pricing": {
            "input_per_million_usd": spec.input_price_per_million,
            "output_per_million_usd": spec.output_price_per_million,
            "batch_discount_multiplier": spec.batch_discount,
            "spend_cap_usd": spec.spend_cap_usd,
            "estimated_maximum_usd": totals.estimated_cost,
        },
        "token_budget": {
            "estimated_input_tokens": totals.total_input,
            "maximum_output_tokens": totals.total_output,
        },
        "counts": {
            "requests": totals.requests,
            "capabilities": capabilities_map(totals.capabilities),
        },
        "artifacts": {
            "candidates.jsonl": file_receipt(totals.candidates_path)?,
            "requests.jsonl": file_receipt(totals.requests_path)?,
        },
    });
    let evidence = sha256_hex(&canonical_json(&core).map_err(BundleError::Canonical)?);
    let mut map = Map::new();
    map.insert("created_at".into(), json!(spec.created_at));
    if let Value::Object(fields) = core {
        for (key, value) in fields {
            map.insert(key, value);
        }
    }
    map.insert("evidence_sha256".into(), json!(evidence));
    Ok(Value::Object(map))
}

/// Dedup-check one candidate, update totals, and append both JSONL lines.
#[allow(clippy::too_many_arguments, reason = "accumulator tuple by design")]
fn record_candidate<'a>(
    candidate: &'a Value,
    request: &Value,
    custom_id: &str,
    candidate_handle: &mut std::fs::File,
    request_handle: &mut std::fs::File,
    seen_candidates: &mut std::collections::BTreeSet<String>,
    seen_custom: &mut std::collections::BTreeSet<String>,
    total_input: &mut u64,
    total_output: &mut u64,
    capability_counts: &mut BTreeMap<&'a str, u64>,
) -> Result<(), BundleError> {
    let id = candidate
        .get("candidate_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    if !seen_candidates.insert(id.clone()) {
        return Err(BundleError::DuplicateCandidate(id));
    }
    if !seen_custom.insert(custom_id.to_string()) {
        return Err(BundleError::DuplicateCustomId(custom_id.to_string()));
    }
    *total_input += candidate
        .get("estimated_input_tokens")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    *total_output += candidate
        .get("max_output_tokens")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    if let Some(capability) = candidate.get("capability").and_then(Value::as_str) {
        *capability_counts.entry(capability).or_insert(0) += 1;
    }
    let candidate_line = canonical_json(candidate).map_err(BundleError::Canonical)?;
    candidate_handle
        .write_all(&candidate_line)
        .and_then(|()| candidate_handle.write_all(b"\n"))
        .map_err(|source| io_err("write candidates.jsonl", source))?;
    let request_line = canonical_json(request).map_err(BundleError::Canonical)?;
    request_handle
        .write_all(&request_line)
        .and_then(|()| request_handle.write_all(b"\n"))
        .map_err(|source| io_err("write requests.jsonl", source))
}

/// `_request(candidate, model, allowed_roots)` — returns `(request, custom_id)`.
fn process_candidate(
    candidate: &Value,
    spec: &BundleSpec,
    roots: &[PathBuf],
) -> Result<(Value, String), BundleError> {
    verify_candidate_identity(candidate)?;

    let prompt = candidate
        .get("prompt")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let schema = candidate
        .get("response_schema")
        .cloned()
        .unwrap_or(Value::Null);
    let schema_json =
        String::from_utf8(canonical_json(&schema).map_err(BundleError::Canonical)?)
            .map_err(|_| BundleError::ResolveFailed("response schema is not UTF-8".into()))?;
    let mut content: Vec<Value> = vec![json!({
        "type": "text",
        "text": format!("{prompt}\n\nReturn only JSON matching this schema:\n{schema_json}"),
    })];

    let mut evidence_receipts: Vec<Value> = Vec::new();
    for evidence in candidate
        .get("evidence")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or_default()
    {
        let (resolved_str, media_type, payload) = resolve_evidence(evidence, roots)?;
        let recorded = evidence.get("sha256").and_then(Value::as_str).unwrap_or("");
        let block_type = if media_type == "application/pdf" {
            "document"
        } else {
            "image"
        };
        content.push(json!({
            "type": block_type,
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64_standard(&payload),
            },
        }));
        evidence_receipts.push(json!({
            "path": resolved_str,
            "sha256": recorded,
            "media_type": media_type,
            "page_1based": evidence.get("page_1based").cloned().unwrap_or(Value::Null),
            "bbox": evidence.get("bbox").cloned().unwrap_or(Value::Null),
        }));
    }

    let identity = identity_payload(candidate);
    let custom_id = {
        let pair_id = stable_id("pair", &identity).map_err(BundleError::Canonical)?;
        format!("teach-{}", &pair_id["pair-".len()..])
    };

    let system = teacher_system(
        candidate
            .get("capability")
            .and_then(Value::as_str)
            .unwrap_or_default(),
    );
    let attempt_receipts: Vec<Value> = candidate
        .get("local_attempts")
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or_default()
        .iter()
        .map(|attempt| {
            json!(attempt
                .get("receipt_sha256")
                .cloned()
                .unwrap_or(Value::Null))
        })
        .collect();

    let request = json!({
        "custom_id": custom_id,
        "params": {
            "model": spec.model,
            "max_tokens": candidate.get("max_output_tokens").cloned().unwrap_or(Value::Null),
            "thinking": {"type": "disabled"},
            "system": system,
            "messages": [{"role": "user", "content": content}],
        },
        "_harness": {
            "candidate_id": candidate.get("candidate_id").cloned().unwrap_or(Value::Null),
            "purpose": candidate.get("purpose").cloned().unwrap_or(Value::Null),
            "capability": candidate.get("capability").cloned().unwrap_or(Value::Null),
            "document_sha256": candidate.get("document_sha256").cloned().unwrap_or(Value::Null),
            "evidence": evidence_receipts,
            "local_attempt_receipts": attempt_receipts,
        },
    });
    Ok((request, custom_id))
}

/// `_resolved_evidence` for one entry: strict-resolve, require a regular
/// file under an allowed root, and verify the recorded SHA-256.
fn resolve_evidence(
    evidence: &Value,
    roots: &[PathBuf],
) -> Result<(String, String, Vec<u8>), BundleError> {
    let path_field = evidence
        .get("path")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let resolved = resolve_strict(Path::new(path_field))?;
    let resolved_str = resolved.display().to_string();
    let metadata = std::fs::metadata(&resolved).map_err(|source| BundleError::Io {
        context: format!("stat {resolved_str}"),
        source,
    })?;
    if !metadata.is_file() {
        return Err(BundleError::NotRegularFile(resolved_str));
    }
    if !roots.iter().any(|root| resolved.starts_with(root)) {
        return Err(BundleError::OutsideRoots(resolved_str));
    }
    let payload = std::fs::read(&resolved).map_err(|source| BundleError::Io {
        context: format!("read {resolved_str}"),
        source,
    })?;
    let recorded = evidence.get("sha256").and_then(Value::as_str).unwrap_or("");
    if sha256_hex(&payload) != recorded {
        return Err(BundleError::ShaMismatch(resolved_str));
    }
    let media_type = evidence
        .get("media_type")
        .and_then(Value::as_str)
        .unwrap_or_default();
    Ok((resolved_str, media_type.to_string(), payload))
}

/// `candidate_identity_payload` — the wire dump minus `candidate_id`.
fn identity_payload(candidate: &Value) -> Value {
    let mut value = candidate.clone();
    if let Some(map) = value.as_object_mut() {
        map.remove("candidate_id");
    }
    value
}

/// `verify_candidate_identity` — the id must equal the identity digest.
fn verify_candidate_identity(candidate: &Value) -> Result<(), BundleError> {
    let recorded = candidate
        .get("candidate_id")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let expected = format!(
        "candidate-{}",
        &sha256_hex(&canonical_json(&identity_payload(candidate)).map_err(BundleError::Canonical)?)
            [..32]
    );
    if recorded != expected {
        return Err(BundleError::IdentityMismatch(recorded.to_string()));
    }
    Ok(())
}

/// The teacher system prompt: base plus the capability's verbatim addendum.
fn teacher_system(capability: &str) -> String {
    let suffix = match capability {
        "pin_semantics" => " For pin semantics, omit any row whose selected-package pin or ball cell is blank, -, —, or N/A. Use dir only for a printed direction or pin-category value such as I, O, I/O, P, or S. Use type only for a printed electrical I/O structure or level. Use supply_domain only for a value under an explicit supply-domain header. Put Description and Function column values in functions. Never copy a description or supply domain into type. Every semantic value must come from the matching identity row and its corresponding semantic column.",
        "parametrics" => " For parametrics, omit blank cells and non-values such as -, —, or N/A. Copy field exactly from one printed Parameter or Symbol cell; never construct a field by joining cells. Put printed size, test, and operating qualifiers in conditions. Copy every condition value verbatim; do not paraphrase, expand an acronym, interpret a label, or combine qualifiers from separate cells. Omit any fact that requires such a transformation. Emit only facts whose field, value, role, unit, and conditions can be traced to the same table row and its headers.",
        "power_modes" => " For power modes, return one row per printed power or low-power mode current. Copy mode names verbatim; never normalize or invent one. Every current value must come from the same printed row as its mode and unit, with the printed min/typ/max header as the role. Copy VDD, frequency, temperature, and peripheral qualifiers verbatim into conditions; TA = 25 C is recorded, never assumed. Skip blank and non-value cells and prose. Do not convert units.",
        "typical_characteristics" => " For typical characteristics, digitize only the printed plot. Copy the plot title and each axis label and unit verbatim; set axis min/max from the printed range or the plotted tick span. Each printed curve is one series whose name and condition copy its legend entry verbatim. Emit (x, y) points only at visually identifiable features in printed axis units; never invent intermediate samples or extrapolate past the plotted range.",
        "series_summary" => " For series summaries, copy each characteristic and application nearly verbatim from visible source text. Do not add competitors, recommendations, positioning, or facts from outside the supplied page.",
        "opn_decoder" => " For OPN decoding, emit only segments, suffix codes, and meanings explicitly printed in the supplied ordering or nomenclature evidence. Use JSON null for unstated scalar segments and never infer a code meaning.",
        _ => "",
    };
    format!("{SYSTEM_BASE}{suffix}")
}

/// `dict(sorted(counter.items()))` as a JSON object.
fn capabilities_map(counts: &BTreeMap<&str, u64>) -> Value {
    let mut map = Map::new();
    for (capability, count) in counts {
        map.insert((*capability).to_string(), json!(count));
    }
    Value::Object(map)
}

/// Artifact receipt: `{sha256, bytes}` over the file as written.
fn file_receipt(path: &Path) -> Result<Value, BundleError> {
    let bytes = std::fs::read(path).map_err(|source| BundleError::Io {
        context: format!("hash {}", path.display()),
        source,
    })?;
    Ok(json!({
        "sha256": sha256_hex(&bytes),
        "bytes": bytes.len(),
    }))
}

/// `Path.resolve(strict=True)`; missing paths carry Python's errno text.
///
/// Python resolves the longest existing prefix first, so the errno message
/// names the partially-resolved path (`/tmp/...` → `/private/tmp/...` on
/// macOS). Match that by canonicalizing the parent and rejoining the leaf.
fn resolve_strict(path: &Path) -> Result<PathBuf, BundleError> {
    match std::fs::canonicalize(path) {
        Ok(resolved) => Ok(resolved),
        Err(source) if source.kind() == std::io::ErrorKind::NotFound => {
            let partial = match (path.parent(), path.file_name()) {
                (Some(parent), Some(name)) => std::fs::canonicalize(parent)
                    .map_or_else(|_| path.to_path_buf(), |resolved| resolved.join(name)),
                _ => path.to_path_buf(),
            };
            Err(BundleError::ResolveFailed(format!(
                "[Errno 2] No such file or directory: '{}'",
                partial.display()
            )))
        }
        Err(source) => Err(BundleError::Io {
            context: format!("resolve {}", path.display()),
            source,
        }),
    }
}

/// Open for writing, failing if the path exists (Python mode `"xb"`).
fn create_exclusive(path: &Path) -> Result<std::fs::File, BundleError> {
    std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|source| BundleError::Io {
            context: format!("create {}", path.display()),
            source,
        })
}

fn set_mode(path: &Path, bits: u32) -> Result<(), BundleError> {
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(bits)).map_err(|source| {
        BundleError::Io {
            context: format!("chmod {bits:o} {}", path.display()),
            source,
        }
    })
}

fn io_err(context: &str, source: std::io::Error) -> BundleError {
    BundleError::Io {
        context: context.to_string(),
        source,
    }
}

fn file_name_of(path: &Path) -> String {
    path.file_name().map_or_else(
        || "bundle".to_string(),
        |n| n.to_string_lossy().into_owned(),
    )
}

/// Standard base64 with padding (RFC 4648); no dependency, one alphabet.
fn base64_standard(data: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let alphabet = |sextet: u32| -> char {
        // The mask keeps the index in 0..64; `get` makes the bound explicit.
        ALPHABET
            .get((sextet & 63) as usize)
            .map_or('A', |&byte| byte as char)
    };
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for chunk in data.chunks(3) {
        let b0 = chunk.first().copied().unwrap_or(0);
        let b1 = chunk.get(1).copied();
        let b2 = chunk.get(2).copied();
        let triple =
            (u32::from(b0) << 16) | (b1.map_or(0u32, u32::from) << 8) | b2.map_or(0u32, u32::from);
        let quad = [
            alphabet(triple >> 18),
            alphabet(triple >> 12),
            alphabet(triple >> 6),
            alphabet(triple),
        ];
        let keep = match (b1.is_some(), b2.is_some()) {
            (true, true) => 4,
            (true, false) => 3,
            (false, _) => 2,
        };
        for c in quad.into_iter().take(keep) {
            out.push(c);
        }
        for _ in keep..4 {
            out.push('=');
        }
    }
    out
}

/// `json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
/// allow_nan=False)` — the sealed-manifest encoding. Public so sibling
/// crates writing Python-shaped read-only JSON share one writer.
///
/// # Errors
///
/// [`CanonicalError::NonFinite`] on NaN/±inf, mirroring `allow_nan=False`.
pub fn py_dumps_indent2_sorted(value: &Value) -> Result<String, BundleError> {
    let mut out = String::new();
    write_pretty(value, &mut out, 0)?;
    Ok(out)
}

fn write_pretty(value: &Value, out: &mut String, indent: usize) -> Result<(), BundleError> {
    let pad = "  ".repeat(indent);
    let inner = "  ".repeat(indent + 1);
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                out.push_str(&i.to_string());
            } else if let Some(u) = n.as_u64() {
                out.push_str(&u.to_string());
            } else if let Some(f) = n.as_f64() {
                out.push_str(&python_float_repr(f).map_err(BundleError::Canonical)?);
            }
        }
        Value::String(s) => write_json_string(s, out),
        Value::Array(items) if items.is_empty() => out.push_str("[]"),
        Value::Array(items) => {
            out.push_str("[\n");
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push_str(",\n");
                }
                out.push_str(&inner);
                write_pretty(item, out, indent + 1)?;
            }
            out.push('\n');
            out.push_str(&pad);
            out.push(']');
        }
        Value::Object(map) if map.is_empty() => out.push_str("{}"),
        Value::Object(map) => {
            let mut keys: Vec<&String> = map.keys().collect();
            keys.sort();
            out.push_str("{\n");
            for (index, key) in keys.iter().enumerate() {
                if index > 0 {
                    out.push_str(",\n");
                }
                out.push_str(&inner);
                write_json_string(key, out);
                out.push_str(": ");
                write_pretty(&map[*key], out, indent + 1)?;
            }
            out.push('\n');
            out.push_str(&pad);
            out.push('}');
        }
    }
    Ok(())
}

/// `ensure_ascii=False` escaping: quotes, backslash, and controls only.
fn write_json_string(s: &str, out: &mut String) {
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

#[cfg(test)]
// Controls assert on fixed vectors; unwrap/index noise is fine here.
#[allow(clippy::unwrap_used, reason = "unit controls on literals")]
mod tests {
    use super::{base64_standard, py_dumps_indent2_sorted};
    use serde_json::json;

    /// RFC 4648 test vectors.
    #[test]
    fn base64_matches_reference_vectors() {
        assert_eq!(base64_standard(b""), "");
        assert_eq!(base64_standard(b"f"), "Zg==");
        assert_eq!(base64_standard(b"fo"), "Zm8=");
        assert_eq!(base64_standard(b"foo"), "Zm9v");
        assert_eq!(base64_standard(b"foob"), "Zm9vYg==");
        assert_eq!(base64_standard(b"fooba"), "Zm9vYmE=");
        assert_eq!(base64_standard(b"foobar"), "Zm9vYmFy");
    }

    /// Exactly matches Python's
    /// `json.dumps(v, sort_keys=True, indent=2, ensure_ascii=False)`.
    #[test]
    fn pretty_writer_matches_python_indent2() {
        let value = json!({
            "b": [1, 2.5, null, true],
            "a": {"x": "q\"uote\nline", "empty_arr": [], "empty_obj": {}},
            "c": 3.0,
        });
        assert_eq!(
            py_dumps_indent2_sorted(&value).unwrap(),
            "{\n  \"a\": {\n    \"empty_arr\": [],\n    \"empty_obj\": {},\n    \"x\": \"q\\\"uote\\nline\"\n  },\n  \"b\": [\n    1,\n    2.5,\n    null,\n    true\n  ],\n  \"c\": 3.0\n}"
        );
    }
}
