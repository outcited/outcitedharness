//! Claim wire models — `harness/electronics/{models,claims}.py` observable
//! behaviour: `make_claim`, `stable_id` identity payloads, and the
//! `model_dump(by_alias=True, mode="json")` wire shape the corpus stores.
//!
//! The API surface is this implementer's design; the *wire* is the
//! reference's. `claim_id` is computed over the identity payload exactly as
//! `claims.claim_identity_payload` builds it, so every gold fixture's claim id
//! is a differential check against Python.

use serde_json::{json, Map, Value};

use crate::canonical::{stable_id, CanonicalError};
use crate::ExtractError;

/// Fact-claim schema string (pydantic `schema` alias).
pub const CLAIM_SCHEMA: &str = "harness.electronics-fact-claim.v1";

/// Claim taxonomy — `models.ClaimClass`, wire value lowercase.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClaimClass {
    /// Directly visible on the page.
    VisibleFact,
    /// Model- or rule-assigned label.
    SemanticLabel,
    /// Recommendation derived from other claims.
    DerivedRecommendation,
}

impl ClaimClass {
    /// Wire value, e.g. `"visible_fact"`.
    #[must_use]
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::VisibleFact => "visible_fact",
            Self::SemanticLabel => "semantic_label",
            Self::DerivedRecommendation => "derived_recommendation",
        }
    }
}

/// Entity grain — `models.EntityGrain`, wire value lowercase.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EntityGrain {
    /// Whole document.
    Document,
    /// Device series.
    Series,
    /// Device family.
    Family,
    /// Base part number.
    BasePart,
    /// Orderable part number.
    Opn,
    /// Package variant.
    Package,
    /// Single pin or ball.
    PinOrBall,
}

impl EntityGrain {
    /// Wire value, e.g. `"pin_or_ball"`.
    #[must_use]
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Document => "document",
            Self::Series => "series",
            Self::Family => "family",
            Self::BasePart => "base_part",
            Self::Opn => "opn",
            Self::Package => "package",
            Self::PinOrBall => "pin_or_ball",
        }
    }
}

/// Evidence kind — `models.EvidenceKind`, wire value lowercase.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EvidenceKind {
    /// Whole source record as evidence.
    SourceRecord,
    /// A text span on a page.
    TextSpan,
    /// One table cell.
    TableCell,
    /// An image region.
    ImageRegion,
}

impl EvidenceKind {
    /// Wire value, e.g. `"source_record"`.
    #[must_use]
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::SourceRecord => "source_record",
            Self::TextSpan => "text_span",
            Self::TableCell => "table_cell",
            Self::ImageRegion => "image_region",
        }
    }
}

/// `models.EntityReference` — the who of a claim.
#[derive(Debug, Clone)]
pub struct EntityReference {
    /// Stable entity identifier.
    pub entity_id: String,
    /// Grain of the entity.
    pub grain: EntityGrain,
    /// Canonical identifier as printed.
    pub canonical_id: String,
    /// Vendor domain or name, when known.
    pub vendor: Option<String>,
    /// Family, when known.
    pub family: Option<String>,
    /// Package, when known.
    pub package: Option<String>,
    /// Parent entity identifiers.
    pub parent_ids: Vec<String>,
}

impl EntityReference {
    /// Wire object, keys exactly as `model_dump(by_alias=True, mode="json")`.
    #[must_use]
    pub fn to_wire(&self) -> Value {
        json!({
            "entity_id": self.entity_id,
            "grain": self.grain.as_str(),
            "canonical_id": self.canonical_id,
            "vendor": self.vendor,
            "family": self.family,
            "package": self.package,
            "parent_ids": self.parent_ids,
        })
    }
}

/// `models.EvidenceReference` — where a claim came from.
#[derive(Debug, Clone)]
pub struct EvidenceReference {
    /// Evidence kind.
    pub kind: EvidenceKind,
    /// Source document digest.
    pub document_sha256: String,
    /// Source URI (scheme required by the reference).
    pub source_uri: String,
    /// Artifact digest, when the evidence is an artifact.
    pub artifact_sha256: Option<String>,
    /// 1-based page, when page-level.
    pub page_1based: Option<u32>,
    /// Bounding box `[x0, y0, x1, y1]`, for image-region evidence.
    pub bbox: Option<[f64; 4]>,
    /// Table index, for table-cell evidence.
    pub table_index: Option<u64>,
    /// Row index, for table-cell evidence.
    pub row_index: Option<u64>,
    /// Column index, for table-cell evidence.
    pub column_index: Option<u64>,
    /// Verbatim quote, when quoted.
    pub quoted_text: Option<String>,
}

impl EvidenceReference {
    /// Wire object, keys exactly as `model_dump(by_alias=True, mode="json")`.
    #[must_use]
    pub fn to_wire(&self) -> Value {
        json!({
            "kind": self.kind.as_str(),
            "document_sha256": self.document_sha256,
            "source_uri": self.source_uri,
            "artifact_sha256": self.artifact_sha256,
            "page_1based": self.page_1based,
            "bbox": self.bbox.map(|b| vec![b[0], b[1], b[2], b[3]]),
            "table_index": self.table_index,
            "row_index": self.row_index,
            "column_index": self.column_index,
            "quoted_text": self.quoted_text,
        })
    }
}

/// `models.ModelIdentity` — which model produced a claim.
#[derive(Debug, Clone)]
pub struct ModelIdentity {
    /// Provider name.
    pub provider: String,
    /// Model name.
    pub model: String,
    /// Model revision, when pinned.
    pub revision: Option<String>,
    /// Digest of the canonical request.
    pub request_sha256: String,
    /// Provider response id, when captured.
    pub response_id: Option<String>,
    /// Batch id (required for Anthropic teachers by the reference).
    pub batch_id: Option<String>,
}

impl ModelIdentity {
    /// Wire object, keys exactly as `model_dump(by_alias=True, mode="json")`.
    #[must_use]
    pub fn to_wire(&self) -> Value {
        json!({
            "provider": self.provider,
            "model": self.model,
            "revision": self.revision,
            "request_sha256": self.request_sha256,
            "response_id": self.response_id,
            "batch_id": self.batch_id,
        })
    }
}

/// Everything `make_claim` accepts; mirrors the Python keyword set.
#[derive(Debug, Clone)]
pub struct ClaimSpec {
    /// Entity the claim is about.
    pub entity: EntityReference,
    /// Field name (reference pattern `^[a-z][a-z0-9_.-]{0,127}$`).
    pub field: String,
    /// Claim value: JSON, as a string in the corpus (`"0.5"`, not `0.5`).
    pub value: Value,
    /// Unit string, when the field carries one.
    pub unit: Option<String>,
    /// Condition map (JSON-compatible).
    pub conditions: Map<String, Value>,
    /// Claim class.
    pub claim_class: ClaimClass,
    /// Extraction method name.
    pub extraction_method: String,
    /// Evidence (required for visible/semantic classes).
    pub evidence: Vec<EvidenceReference>,
    /// Source claim ids (required for derived recommendations).
    pub source_claim_ids: Vec<String>,
    /// Model identity, for model-based extractions.
    pub model: Option<ModelIdentity>,
    /// Creation timestamp **as the wire string** (pydantic `mode="json"` RFC
    /// 3339); this port passes the string through instead of owning a
    /// datetime library, so the caller fixes the format.
    pub created_at: String,
}

/// A fact claim with its computed identity.
#[derive(Debug, Clone)]
pub struct FactClaim {
    /// `stable_id("claim", identity_payload)`.
    pub claim_id: String,
    /// The spec this claim was minted from.
    pub spec: ClaimSpec,
}

impl FactClaim {
    /// Wire object — `claim.model_dump(by_alias=True, mode="json")`.
    #[must_use]
    pub fn to_wire(&self) -> Value {
        json!({
            "schema": CLAIM_SCHEMA,
            "claim_id": self.claim_id,
            "entity": self.spec.entity.to_wire(),
            "field": self.spec.field,
            "value": self.spec.value,
            "unit": self.spec.unit,
            "conditions": self.spec.conditions,
            "claim_class": self.spec.claim_class.as_str(),
            "extraction_method": self.spec.extraction_method,
            "evidence": self.spec.evidence.iter().map(EvidenceReference::to_wire).collect::<Vec<_>>(),
            "source_claim_ids": self.spec.source_claim_ids,
            "model": self.spec.model.as_ref().map(ModelIdentity::to_wire),
            "created_at": self.spec.created_at,
        })
    }
}

/// `claims.claim_identity_payload` — the preimage of `stable_id`.
///
/// Keys are exactly the reference's: `entity`, `field`, `value`, `unit`,
/// `conditions`, `claim_class`, `extraction_method`, `evidence`,
/// `source_claim_ids`, `model` (`claim_id` and `created_at` excluded).
#[must_use]
pub fn claim_identity_payload(spec: &ClaimSpec) -> Value {
    json!({
        "entity": spec.entity.to_wire(),
        "field": spec.field,
        "value": spec.value,
        "unit": spec.unit,
        "conditions": spec.conditions,
        "claim_class": spec.claim_class.as_str(),
        "extraction_method": spec.extraction_method,
        "evidence": spec.evidence.iter().map(EvidenceReference::to_wire).collect::<Vec<_>>(),
        "source_claim_ids": spec.source_claim_ids,
        "model": spec.model.as_ref().map(ModelIdentity::to_wire),
    })
}

/// `claims.make_claim` — validate the spec and mint the identity.
///
/// # Errors
///
/// [`ExtractError::InvalidClaim`] on any violation of the pydantic contract
/// enforced at claim-minting time (`models.FactClaim` invariants).
pub fn make_claim(spec: ClaimSpec) -> Result<FactClaim, ExtractError> {
    validate(&spec)?;
    let payload = claim_identity_payload(&spec);
    let claim_id =
        stable_id("claim", &payload).map_err(|e| ExtractError::Canonical(e.to_string()))?;
    Ok(FactClaim { claim_id, spec })
}

fn validate(spec: &ClaimSpec) -> Result<(), ExtractError> {
    let invalid = |m: &str| ExtractError::InvalidClaim(m.to_string());
    if spec.field.is_empty()
        || !spec.field.starts_with(|c: char| c.is_ascii_lowercase())
        || !spec
            .field
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '_' | '.' | '-'))
        || spec.field.len() > 128
    {
        return Err(invalid("field pattern ^[a-z][a-z0-9_.-]{0,127}$"));
    }
    if matches!(
        spec.claim_class,
        ClaimClass::VisibleFact | ClaimClass::SemanticLabel
    ) && spec.evidence.is_empty()
    {
        return Err(invalid(
            "visible and semantic claims require source evidence",
        ));
    }
    if spec.claim_class == ClaimClass::DerivedRecommendation && spec.source_claim_ids.is_empty() {
        return Err(invalid("derived recommendations require source claims"));
    }
    if let Some(u) = &spec.unit {
        if u.trim().is_empty() {
            return Err(invalid("unit cannot be blank"));
        }
    }
    if spec.model.is_some() {
        let m = spec.extraction_method.to_lowercase();
        let modeled = ["model", "vision", "frontier", "llm", "vlm", "teacher"]
            .iter()
            .any(|word| m.contains(word));
        if !modeled {
            return Err(invalid(
                "model identity requires a model-based extraction method",
            ));
        }
    }
    let id_ok = !spec.entity.entity_id.is_empty()
        && spec.entity.entity_id.len() <= 256
        && spec
            .entity
            .entity_id
            .starts_with(|c: char| c.is_ascii_alphanumeric())
        && spec
            .entity
            .entity_id
            .chars()
            .skip(1)
            .all(|c| c.is_ascii_alphanumeric() || "._:/#@+-".contains(c));
    if !id_ok {
        return Err(invalid("entity_id pattern"));
    }
    if matches!(
        spec.entity.grain,
        EntityGrain::Package | EntityGrain::PinOrBall
    ) && spec.entity.package.as_deref().unwrap_or("").is_empty()
    {
        return Err(invalid(
            "package and pin/ball entities require package identity",
        ));
    }
    if spec.source_claim_ids.len() != dedup_len(&spec.source_claim_ids) {
        return Err(invalid("source_claim_ids must be unique"));
    }
    Ok(())
}

fn dedup_len(ids: &[String]) -> usize {
    let mut seen: Vec<&String> = Vec::new();
    for id in ids {
        if !seen.contains(&id) {
            seen.push(id);
        }
    }
    seen.len()
}

/// From `CanonicalError` so `stable_id` failures surface as extraction errors.
impl From<CanonicalError> for ExtractError {
    fn from(value: CanonicalError) -> Self {
        Self::Canonical(value.to_string())
    }
}

#[cfg(test)]
// Test module: asserts are the point here; unwraps on fixture data are the
// documented allowance from HANDOFF.md ("Test modules may carry a documented
// #[allow]").
#[allow(clippy::unwrap_used, clippy::expect_used, clippy::indexing_slicing)]
mod tests {
    use serde_json::json;

    use super::*;

    fn sample() -> ClaimSpec {
        ClaimSpec {
            entity: EntityReference {
                entity_id: "opn:acme:ATOM1".to_string(),
                grain: EntityGrain::Opn,
                canonical_id: "ATOM1".to_string(),
                vendor: Some("acme".to_string()),
                family: None,
                package: None,
                parent_ids: Vec::new(),
            },
            field: "clock_specs.max_freq_mhz".to_string(),
            value: json!(100),
            unit: Some("MHz".to_string()),
            conditions: Map::new(),
            claim_class: ClaimClass::SemanticLabel,
            extraction_method: "imported_frontier_ground_truth".to_string(),
            evidence: vec![EvidenceReference {
                kind: EvidenceKind::SourceRecord,
                document_sha256: "a".repeat(64),
                source_uri: "file:///x/y.pdf".to_string(),
                artifact_sha256: Some("b".repeat(64)),
                page_1based: None,
                bbox: None,
                table_index: None,
                row_index: None,
                column_index: None,
                quoted_text: None,
            }],
            source_claim_ids: Vec::new(),
            model: None,
            created_at: "2026-09-01T00:00:00+00:00".to_string(),
        }
    }

    /// Gate: identity is stable and deterministic.
    #[test]
    fn make_claim_mints_deterministic_identity() {
        let a = make_claim(sample()).unwrap();
        let b = make_claim(sample()).unwrap();
        assert_eq!(a.claim_id, b.claim_id);
        assert!(a.claim_id.starts_with("claim-"));
    }

    /// Negative control (ASSURANCE R2): the gate must go red when an input
    /// that cannot produce a legal claim is accepted. Semantic claim with no
    /// evidence is exactly the case `models.FactClaim` rejects.
    #[test]
    fn negative_control_evidenceless_semantic_claim_is_rejected() {
        let mut spec = sample();
        spec.evidence.clear();
        assert!(matches!(
            make_claim(spec),
            Err(ExtractError::InvalidClaim(_))
        ));
    }
}
