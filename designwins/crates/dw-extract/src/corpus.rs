//! Corpus registry invariants — `harness/electronics/corpus.py` observable
//! behaviour (`verify_corpus_registry`, `sha256_file`).

use std::path::Path;

use serde_json::Value;

use crate::canonical::canonical_json;
use crate::sha256::sha256_hex;
use crate::ExtractError;

/// `corpus.SCHEma` — corpus registry schema string.
pub const SCHEMA: &str = "harness.electronics-corpus-registry.v1";

/// `corpus.sha256_file` — lowercase hex digest of a file's bytes.
///
/// # Errors
///
/// [`ExtractError::Integrity`] if the file cannot be read.
pub fn sha256_file(path: &Path) -> Result<String, ExtractError> {
    let bytes = std::fs::read(path)
        .map_err(|e| ExtractError::Integrity(format!("{}: {e}", path.display())))?;
    Ok(sha256_hex(&bytes))
}

/// `corpus.verify_corpus_registry` — schema, digest, counts, duplicate check.
///
/// The reference computes the evidence digest over every key except
/// `created_at` and `evidence_sha256` (it re-attaches the digest before
/// re-filtering it out — same set either way).
///
/// # Errors
///
/// [`ExtractError::Registry`] on any violation.
pub fn verify_corpus_registry(value: &Value) -> Result<(), ExtractError> {
    let fail = |m: &str| ExtractError::Registry(m.to_string());
    if value.get("schema").and_then(Value::as_str) != Some(SCHEMA) {
        return Err(fail("corpus registry schema is not supported"));
    }
    let expected = value
        .get("evidence_sha256")
        .and_then(Value::as_str)
        .ok_or_else(|| fail("corpus registry is malformed"))?;
    let obj = value
        .as_object()
        .ok_or_else(|| fail("corpus registry is malformed"))?;
    let mut digest_input = serde_json::Map::new();
    for (k, v) in obj {
        if k != "created_at" && k != "evidence_sha256" {
            digest_input.insert(k.clone(), v.clone());
        }
    }
    let canonical = canonical_json(&Value::Object(digest_input))
        .map_err(|e| fail(&format!("canonical encoding: {e}")))?;
    if expected != sha256_hex(&canonical) {
        return Err(fail("corpus registry evidence digest is invalid"));
    }
    let counts = value.get("counts").filter(|c| c.is_object());
    let documents = value.get("documents").and_then(Value::as_array);
    let (Some(counts), Some(documents)) = (counts, documents) else {
        return Err(fail("corpus registry is malformed"));
    };
    if counts.get("unique_pdf_sha256").and_then(Value::as_u64) != Some(documents.len() as u64) {
        return Err(fail("corpus registry document count does not match"));
    }
    let mut seen: Vec<String> = Vec::new();
    for document in documents {
        let document = document
            .as_object()
            .ok_or_else(|| fail("corpus registry document is malformed"))?;
        let digest = document
            .get("document_sha256")
            .map(ToString::to_string)
            .unwrap_or_default();
        if seen.contains(&digest) {
            return Err(fail("corpus registry contains duplicate documents"));
        }
        seen.push(digest);
    }
    Ok(())
}

#[cfg(test)]
// Test module: asserts are the point here; unwraps on fixture data are the
// documented allowance from HANDOFF.md ("Test modules may carry a documented
// #[allow]").
#[allow(clippy::unwrap_used, clippy::expect_used, clippy::indexing_slicing)]
mod tests {
    use serde_json::json;

    use super::*;
    use crate::canonical::canonical_json;
    use crate::sha256::sha256_hex;

    fn valid_registry() -> Value {
        let mut core = json!({
            "schema": SCHEMA,
            "documents": [
                {"document_sha256": "a".repeat(64), "paths": ["x.pdf"]},
                {"document_sha256": "b".repeat(64), "paths": []},
            ],
            "counts": {"unique_pdf_sha256": 2},
            "sources": {"pdf_root": "/p", "ground_truth_root": "/g"},
        });
        let digest = sha256_hex(&canonical_json(&core).unwrap());
        core.as_object_mut()
            .unwrap()
            .insert("evidence_sha256".to_string(), json!(digest));
        core
    }

    #[test]
    fn valid_registry_verifies() {
        assert!(verify_corpus_registry(&valid_registry()).is_ok());
    }

    /// Negative control (ASSURANCE R2): a mutated digest must trip the gate —
    /// otherwise the "verification" is decoration.
    #[test]
    fn negative_control_digest_mutation_trips() {
        let mut reg = valid_registry();
        reg["evidence_sha256"] = json!("c".repeat(64));
        assert!(matches!(
            verify_corpus_registry(&reg),
            Err(ExtractError::Registry(_))
        ));
    }

    /// Negative control: duplicate documents must be refused even when the
    /// digest is valid (recompute the digest over the duplicated registry —
    /// the duplicate check is the property under test, not the digest).
    #[test]
    fn negative_control_duplicate_documents_trips() {
        let mut core = json!({
            "schema": SCHEMA,
            "documents": [
                {"document_sha256": "a".repeat(64)},
                {"document_sha256": "a".repeat(64)},
            ],
            "counts": {"unique_pdf_sha256": 2},
        });
        let digest = sha256_hex(&canonical_json(&core).unwrap());
        core.as_object_mut()
            .unwrap()
            .insert("evidence_sha256".to_string(), json!(digest));
        assert!(matches!(
            verify_corpus_registry(&core),
            Err(ExtractError::Registry(_))
        ));
    }
}
