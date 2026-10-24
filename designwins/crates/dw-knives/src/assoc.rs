//! Association Ledger — the join layer between artifacts (any type) and
//! parts (OPN / Family), per `SPEC-ASSOCIATION-LEDGER.md` (2026-10-21).
//!
//! Laws AL1–AL8 are frozen in the spec; this module encodes the ones that
//! are *checkable at the type/record level*:
//!
//! - **AL5 dead-data** — an association below the consumer's threshold is
//!   inert ([`Threshold::admits`]). What is not admitted reaches no knife,
//!   no comparison, no surface. Surfacing an unadmitted record is a
//!   violation, not a gap.
//! - **AL6 evidence-or-not** — confidence without retained evidence is 0
//!   ([`Association::effective_confidence`]).
//! - **AL8 no self-attestation above 0.50** — a single evidence kind caps
//!   at 0.50 regardless of method ceiling ([`Association::effective_confidence`]).
//! - **§5.3 merge discipline** — higher `(epoch, confidence)` wins reads;
//!   both records are retained ([`resolve_pair`]). Epochs are
//!   method-generation numbers, never timestamps.
//! - **§4 family authority** — only `MfgPage` creates family edges; the
//!   decoder is corroboration and may not mint edges
//!   ([`FamilyEdge::corroborate`], ruling 2026-10-21).
//!
//! Ruling 2026-10-21 (Sam), recorded: manufacturer family pages are the
//! family-edge authority; unassociated artifacts are dead data; confidence
//! is grounded by multi-model vision consensus (consumed here as a method,
//! orchestrated in Python).

use std::fmt;

/// Consumer thresholds, spec §7.5. Working values; frozen only after gate
/// G5 (500-doc gold calibration). A threshold *admits* or it does not —
/// there is no partial admission (AL5). Interior `f64` is fine for
/// `PartialEq`; `Eq` is deliberately not derived.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Threshold(f64);

impl Threshold {
    /// Admission for extraction lanes (spec §8).
    pub const EXTRACT: Self = Self(0.70);
    /// Admission for spine / compare consumers (spec §8).
    pub const SPINE: Self = Self(0.80);

    /// The numeric admission value.
    #[must_use]
    pub fn value(self) -> f64 {
        self.0
    }

    /// AL5: does this confidence admit? NaN never admits.
    #[must_use]
    pub fn admits(self, confidence: f64) -> bool {
        confidence >= self.0
    }
}

/// What the artifact is *for* the part — the authority axis (AL3).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
/// What an artifact *is* for a part (AL3).
pub enum Role {
    /// Primary manufacturer datasheet.
    Datasheet,
    /// Application note / design guide.
    AppNote,
    /// Overrides datasheet claims (AL3 precedence); must cite the affected claim.
    /// Erratum sheet; overrides datasheet claims (AL3).
    Errata,
    /// Family selection guide; parametrics at reduced confidence.
    SelectionGuide,
    /// The only role that may set lifecycle state (spec §5.2).
    /// Lifecycle authority (NRND/EOL dates) — spec §5.2.
    EolNotice,
    /// Signal-integrity model file.
    IbisModel,
    /// Reference design package.
    ReferenceDesign,
    /// Product photo or diagram.
    Image,
    /// Marketing collateral; lowest parametric rank.
    Marketing,
    /// Role not yet determined.
    Unknown,
}

impl Role {
    /// Stable wire name.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Datasheet => "datasheet",
            Self::AppNote => "app_note",
            Self::Errata => "errata",
            Self::SelectionGuide => "selection_guide",
            Self::EolNotice => "eol_notice",
            Self::IbisModel => "ibis_model",
            Self::ReferenceDesign => "reference_design",
            Self::Image => "image",
            Self::Marketing => "marketing",
            Self::Unknown => "unknown",
        }
    }

    /// AL3 precedence for conflicting claims on the same (part, parameter).
    /// Higher wins; `EolNotice`/`IbisModel`/`ReferenceDesign`/`Image` do not
    /// contest parametric claims at all and rank lowest here.
    #[must_use]
    pub const fn precedence(self) -> u8 {
        match self {
            Self::Errata => 9,
            Self::Datasheet => 8,
            Self::AppNote => 7,
            Self::SelectionGuide => 6,
            Self::Marketing => 5,
            Self::Unknown => 4,
            Self::EolNotice | Self::IbisModel | Self::ReferenceDesign | Self::Image => 0,
        }
    }
}

/// Ledger association methods, spec §6. Ceilings are frozen per method.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum Method {
    /// Distributor/factory manifest row. Ceiling 0.98.
    ManifestUrl,
    /// Page-1 OPN quote (the current stamp method, unchanged). Ceiling 0.90.
    Page1Opn,
    /// Corroboration only — never creates associations (spec §4).
    OpnDecoder,
    /// Artifact→family edge + family→OPN edge; ceiling = min(both) (AL4).
    FamilyGraph,
    /// Cross-document references. Ceiling 0.70.
    Citation,
    /// 3-engine consensus: cloud `MiMo`, cloud `DeepSeek`, local `TP4` (spec §7.4).
    /// Unanimous 0.90 / majority 0.70 / split = conflict.
    VisionConsensus,
    /// Manufacturer-printed ordering/device table enumerated by the family
    /// census (2026-09-08). Authority class per spec §4.2 wave 1; the
    /// self-quote still caps at 0.50 under AL8 until a second document
    /// corroborates.
    OrderingTable,
}

impl Method {
    /// Frozen method ceiling (spec §6 table).
    #[must_use]
    pub const fn ceiling(self) -> f64 {
        match self {
            Self::ManifestUrl => 0.98,
            Self::Page1Opn | Self::VisionConsensus | Self::OrderingTable => 0.90,
            Self::OpnDecoder => 0.80,
            Self::FamilyGraph => 0.95, // inherited via edges; capped at resolution
            Self::Citation => 0.70,
        }
    }

    /// Stable wire name.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::ManifestUrl => "manifest_url",
            Self::Page1Opn => "page1_opn",
            Self::OpnDecoder => "opn_decoder",
            Self::FamilyGraph => "family_graph",
            Self::Citation => "citation",
            Self::VisionConsensus => "vision_consensus",
            Self::OrderingTable => "ordering_table",
        }
    }
}

/// Evidence kinds, spec §7.1 reliabilities. Each retained kind contributes
/// to effective confidence; kinds are *independent* by construction — a
/// document quoting itself is one kind no matter how many times (AL8).
#[derive(Debug, Clone, PartialEq)]
pub enum Evidence {
    /// URL pattern matched, e.g. distributor slug containing the OPN.
    UrlPattern {
        /// The URL the artifact arrived under.
        url: String,
        /// The substring that matched the target.
        matched: String,
    },
    /// Verbatim quote at a page locator. Same sha as the artifact = self
    /// evidence (counts once, AL8).
    Quote {
        /// Document the quote was read from.
        sha: String,
        /// 1-based page locator.
        page: u32,
        /// Verbatim text; must appear on the claimed page.
        quote: String,
    },
    /// A *different* document citing this one naming the target.
    Citation {
        /// The citing document.
        from_sha: String,
        /// The citing sentence.
        quote: String,
    },
    /// Resolved family edge (itself authority-backed, spec §4).
    FamilyEdge {
        /// Manufacturer owning the family.
        manufacturer: String,
        /// Family key from the manufacturer page slug.
        family_key: String,
    },
}

impl Evidence {
    /// Reliability weight, spec §7.1.
    #[must_use]
    pub fn reliability(&self) -> f64 {
        match self {
            Self::UrlPattern { .. } => 0.95,
            Self::Quote { .. } => 0.85,
            Self::Citation { .. } => 0.70,
            Self::FamilyEdge { .. } => 0.90,
        }
    }

    /// Whether this kind counts as *external* (not self-attestation) for
    /// AL8: quotes from the artifact itself are self; everything else
    /// (URL, foreign citation, family edge) is external.
    #[must_use]
    pub fn is_external(&self, artifact_sha: &str) -> bool {
        match self {
            Self::Quote { sha, .. } => sha != artifact_sha,
            _ => true,
        }
    }
}

/// One ledger record. Keyed `(artifact_sha, target, role, method)`;
/// reads resolve by `(epoch, confidence)` (spec §5.3).
#[derive(Debug, Clone, PartialEq)]
pub struct Association {
    /// Content address of the artifact being bound.
    pub artifact_sha: String,
    /// Rip-adapter type of the artifact (spec §9).
    pub artifact_type: ArtifactType,
    /// Whether `target` is an OPN or a family key.
    pub target_kind: TargetKind,
    /// OPN string or family key. Canonicalization is out of scope (spec §1).
    pub target: String,
    /// Manufacturer if known from evidence; `None` never blocks admission.
    pub manufacturer: Option<String>,
    /// What the artifact is for the part (AL3).
    pub role: Role,
    /// Which ladder method produced this record (spec §6).
    pub method: Method,
    /// Retained evidence kinds; empty means telemetry-only (AL6).
    pub evidence: Vec<Evidence>,
    /// Marks two methods proposing different targets for the same role
    /// (spec §7.3): excluded from all reads until resolved.
    pub conflict: bool,
    /// Method-generation number; bumped when a method's implementation
    /// changes. Never a timestamp.
    pub epoch: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
/// What an association points at (AL4 two-level targets).
pub enum TargetKind {
    /// Ordering part number target.
    Opn,
    /// Manufacturer family target.
    Family,
}

impl TargetKind {
    /// Stable wire name.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Opn => "opn",
            Self::Family => "family",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
/// Artifact payload type; selects the rip adapter (spec §9).
pub enum ArtifactType {
    /// Datasheets, app notes, errata — the current corpus majority.
    Pdf,
    /// Captured web pages (distributor, manufacturer family pages).
    Html,
    /// Product photos, diagrams — OCR lane.
    Image,
    /// IBIS, SPICE, footprint models.
    Model,
    /// Bills of material, ordering tables.
    Bom,
    /// Plain text artifacts.
    Text,
}

impl ArtifactType {
    /// Stable wire name.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Pdf => "pdf",
            Self::Html => "html",
            Self::Image => "image",
            Self::Model => "model",
            Self::Bom => "bom",
            Self::Text => "text",
        }
    }
}

impl Association {
    /// Nominal confidence per spec §7.1: product-complement over the
    /// reliabilities of the retained evidence kinds, capped by method
    /// ceiling. AL8: with no *external* kind, the cap is 0.50. AL6: with
    /// no evidence at all, confidence is 0 — the record exists only to be
    /// counted in telemetry.
    #[must_use]
    pub fn effective_confidence(&self) -> f64 {
        if self.evidence.is_empty() {
            return 0.0;
        }
        let base = self
            .evidence
            .iter()
            .map(Evidence::reliability)
            .fold(1.0_f64, |acc, r| acc * (1.0 - r));
        let combined = 1.0 - base;
        let external = self
            .evidence
            .iter()
            .any(|e| e.is_external(&self.artifact_sha));
        let cap = if external {
            self.method.ceiling()
        } else {
            0.50_f64.min(self.method.ceiling())
        };
        combined.min(cap)
    }

    /// AL5 admission for a consumer threshold. Conflicted records never
    /// admit (spec §7.3).
    #[must_use]
    pub fn admits(&self, threshold: Threshold) -> bool {
        !self.conflict && threshold.admits(self.effective_confidence())
    }
}

/// Read resolution for two records sharing a key (spec §5.3): higher
/// `(epoch, confidence)` wins; both are retained by the store. Equal on
/// both axes is a deterministic tie-break on method ceiling then name —
/// the ledger never silently merges (AL6).
#[must_use]
pub fn resolve_pair<'a>(a: &'a Association, b: &'a Association) -> &'a Association {
    let ka = (a.epoch, a.effective_confidence(), a.method.ceiling());
    let kb = (b.epoch, b.effective_confidence(), b.method.ceiling());
    if ka >= kb {
        a
    } else {
        b
    }
}

/// Family edge — authority is the manufacturer family page (ruling
/// 2026-10-21). The page snapshot itself is content-addressed, so the
/// authority has provenance. The OPN decoder only corroborates.
#[derive(Debug, Clone, PartialEq)]
pub struct FamilyEdge {
    /// Manufacturer owning the family.
    pub manufacturer: String,
    /// Family key (page slug form).
    pub family_key: String,
    /// The OPN listed under the family on the authority page.
    pub opn: String,
    /// The manufacturer page URL — the authority itself (ruling 2026-10-21).
    pub authority_url: String,
    /// Verbatim listing quote from the authority page.
    pub authority_quote: String,
    /// Content-addressed snapshot of the authority page.
    pub captured_sha: String,
    /// Decoder results; corroboration only.
    pub corroborations: Vec<Corroboration>,
    /// Lifecycle of the edge.
    pub status: EdgeStatus,
}

#[derive(Debug, Clone, PartialEq)]
/// Decoder corroboration results — the only effect a decoder may have
/// on a family edge (ruling 2026-10-21).
pub enum Corroboration {
    /// Decoder agrees. Raises confidence, creates nothing (spec §4).
    DecoderAgrees {
        /// Family the decoder decoded.
        decoded_family: String,
    },
    /// Decoder disagrees: edge stays usable at −0.10 and enters review —
    /// never silently flipped.
    DecoderDisagrees {
        /// Family the decoder decoded instead.
        decoded_family: String,
    },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
/// Lifecycle status of a family edge.
pub enum EdgeStatus {
    /// Usable edge.
    Active,
    /// Replaced by a newer epoch; confidence 0.
    Superseded,
    /// Decoder (or review) flagged; reduced confidence.
    Disputed,
}

impl FamilyEdge {
    /// Edge confidence: authority base 0.95; dispute costs 0.10 and *dominates*
    /// (no agreement bonus stacks on a disputed edge — mixed decoder signals
    /// resolve in review, not in the number); a decoder agreement on an active
    /// edge adds 0.02.
    #[must_use]
    pub fn confidence(&self) -> f64 {
        match self.status {
            EdgeStatus::Superseded => return 0.0,
            EdgeStatus::Disputed => return 0.85,
            EdgeStatus::Active => {}
        }
        if self
            .corroborations
            .iter()
            .any(|x| matches!(x, Corroboration::DecoderAgrees { .. }))
        {
            0.97
        } else {
            0.95
        }
    }

    /// Apply a decoder result. This is the *only* effect a decoder can
    /// have on an edge (ruling 2026-10-21).
    pub fn corroborate(&mut self, decoded_family: &str) {
        if decoded_family.eq_ignore_ascii_case(&self.family_key) {
            self.corroborations
                .push(Corroboration::DecoderAgrees {
                    decoded_family: decoded_family.to_owned(),
                });
        } else {
            self.corroborations
                .push(Corroboration::DecoderDisagrees {
                    decoded_family: decoded_family.to_owned(),
                });
            if self.status == EdgeStatus::Active {
                self.status = EdgeStatus::Disputed;
            }
        }
    }
}

impl fmt::Display for Role {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}
impl fmt::Display for Method {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn opn_assoc(method: Method, evidence: Vec<Evidence>) -> Association {
        Association {
            artifact_sha: "a".repeat(64),
            artifact_type: ArtifactType::Pdf,
            target_kind: TargetKind::Opn,
            target: "STM32F103C8T7".into(),
            manufacturer: Some("STMicroelectronics".into()),
            role: Role::Datasheet,
            method,
            evidence,
            conflict: false,
            epoch: 1,
        }
    }

    fn self_quote() -> Evidence {
        Evidence::Quote {
            sha: "a".repeat(64),
            page: 1,
            quote: "STM32F103C8T7".into(),
        }
    }

    #[test]
    fn al6_no_evidence_is_zero_confidence() {
        let a = opn_assoc(Method::Page1Opn, vec![]);
        assert_eq!(a.effective_confidence(), 0.0);
        assert!(!a.admits(Threshold::EXTRACT));
    }

    #[test]
    fn al8_self_attestation_caps_at_half() {
        let a = opn_assoc(Method::Page1Opn, vec![self_quote()]);
        assert!(a.effective_confidence() <= 0.50);
        assert!(!a.admits(Threshold::EXTRACT));
    }

    #[test]
    fn external_evidence_reaches_method_ceiling() {
        let a = opn_assoc(
            Method::ManifestUrl,
            vec![
                Evidence::UrlPattern {
                    url: "https://digikey.example/stm32f103c8.pdf".into(),
                    matched: "stm32f103c8".into(),
                },
                self_quote(),
            ],
        );
        assert!((a.effective_confidence() - 0.98).abs() < 1e-9);
        assert!(a.admits(Threshold::SPINE));
    }

    #[test]
    fn al5_dead_data_below_threshold_is_inert() {
        let a = opn_assoc(Method::Citation, vec![Evidence::Citation {
            from_sha: "b".repeat(64),
            quote: "replaces STM32F103C8T6".into(),
        }]);
        // citation-only: 0.70 combined vs 0.70 ceiling → admits extract, not spine
        assert!(a.admits(Threshold::EXTRACT));
        assert!(!a.admits(Threshold::SPINE));
    }

    #[test]
    fn conflicted_never_admits() {
        let mut a = opn_assoc(Method::ManifestUrl, vec![]);
        a.conflict = true;
        assert!(!a.admits(Threshold::EXTRACT));
    }

    #[test]
    fn merge_discipline_epoch_beats_confidence() {
        let mut old = opn_assoc(Method::ManifestUrl, vec![]);
        old.epoch = 1;
        let mut new = opn_assoc(Method::Page1Opn, vec![]);
        new.epoch = 2;
        assert_eq!(resolve_pair(&old, &new).epoch, 2);
    }

    #[test]
    fn errata_outranks_datasheet() {
        assert!(Role::Errata.precedence() > Role::Datasheet.precedence());
        assert!(Role::Datasheet.precedence() > Role::SelectionGuide.precedence());
        assert_eq!(Role::Image.precedence(), 0);
    }

    #[test]
    fn family_edge_decoder_only_corroborates() {
        let mut e = FamilyEdge {
            manufacturer: "STMicroelectronics".into(),
            family_key: "stm32f1".into(),
            opn: "STM32F103C8T7".into(),
            authority_url: "https://st.example/stm32f1".into(),
            authority_quote: "STM32F103C8T7".into(),
            captured_sha: "c".repeat(64),
            corroborations: vec![],
            status: EdgeStatus::Active,
        };
        assert!((e.confidence() - 0.95).abs() < 1e-9);
        e.corroborate("STM32F1");
        assert!((e.confidence() - 0.97).abs() < 1e-9);
        e.corroborate("STM32F4"); // disagreement
        assert_eq!(e.status, EdgeStatus::Disputed);
        assert!((e.confidence() - 0.85).abs() < 1e-9); // 0.95 - 0.10
    }
}
