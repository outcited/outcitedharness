//! What the routes serve, and the one trait a real store implements.
//!
//! Handlers are pure functions over [`ApiStore`]. [`DemoStore`] is
//! in-memory and fixture-backed — no corpus dependency — so the wire shape is
//! hammerable today; a catalog-backed store slots in behind the same trait
//! without touching a single route.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

use dw_decide::{Build, Candidate, CandidateFacts, Cohort};
use dw_types::{ReleaseId, Sha256};

use crate::feed::{EvidenceAsset, EvidenceRef, Lineage};

/// Where a claim came from — the **loose** wire shape the extraction export
/// prints (`tools/export_api_store.py`): a source may carry a document hash
/// and no page, a page and no crop, or nothing at all. A missing field is a
/// fact about the corpus, not a reason to invent one.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize)]
pub struct ClaimSource {
    /// Document hash, when the feed recorded one (the family feed).
    #[serde(default)]
    pub doc_sha256: Option<String>,
    /// Document hash in the catalog feed's naming — also a hash of the
    /// verbatim text on the family feed, so resolution uses
    /// `doc_sha256.or(sha)` and never trusts this alone for page renders
    /// without checking.
    #[serde(default)]
    pub sha: Option<String>,
    /// 1-based page, when located.
    #[serde(default)]
    pub page: Option<u32>,
    /// Row header that labels the value.
    #[serde(default)]
    pub row_header: Option<String>,
    /// Column header that labels the value.
    #[serde(default)]
    pub column_header: Option<String>,
    /// Row-band crop, when provenanced.
    #[serde(default)]
    pub crop: Option<dw_types::CropRef>,
    /// Source PDF as printed, when the feed recorded one (the cr-core T1
    /// rows carry a filename, not a hash — named, never invented).
    #[serde(default)]
    pub source_pdf: Option<String>,
    /// Provenance tier as printed (e.g. `T1_DATASHEET`).
    #[serde(default)]
    pub provenance_tier: Option<String>,
}

impl ClaimSource {
    /// The document this source points at: the family feed's `doc_sha256`,
    /// else the catalog feed's `sha`.
    #[must_use]
    pub fn document(&self) -> Option<&str> {
        self.doc_sha256.as_deref().or(self.sha.as_deref())
    }
}

/// The funnel's typed [`dw_types::SourceRef`] is a complete source — it
/// converts losslessly into the loose shape so one resolver serves both.
impl From<&dw_types::SourceRef> for ClaimSource {
    fn from(s: &dw_types::SourceRef) -> Self {
        Self {
            doc_sha256: Some(s.sha256.as_str().to_string()),
            sha: Some(s.sha256.as_str().to_string()),
            page: Some(s.page),
            row_header: s.row_header.clone(),
            column_header: s.column_header.clone(),
            crop: s.crop.clone(),
            source_pdf: None,
            provenance_tier: None,
        }
    }
}

/// A claim as the extraction surface shows it: value, and the page it opens to.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PartClaim {
    /// Which parameter.
    pub field: String,
    /// The printed value, as printed.
    pub value: String,
    /// The quote that proves it, when the surface shows one.
    pub quote: Option<String>,
    /// Where it came from. Resolved to assets by the handler.
    pub source: ClaimSource,
}

/// One part, joined 1:1 (`C7`: the facade join is not a search).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PartRecord {
    /// The orderable part number.
    pub part_number: String,
    /// The manufacturer.
    pub vendor: String,
    /// Its claims.
    pub claims: Vec<PartClaim>,
}

/// Pipeline job state. Every variant is renderable — no `Other(String)`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum JobState {
    /// Waiting for a worker.
    Queued,
    /// A worker holds it.
    Running,
    /// Done, output recorded.
    Succeeded,
    /// Permanently refused or transiently broken — `detail` says which.
    Failed,
}

/// One pipeline job (`C5` surface).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JobRecord {
    /// The job id.
    pub job_id: String,
    /// What kind of work it is.
    pub kind: String,
    /// Where it is.
    pub state: JobState,
    /// What a human needs to know about it. Always present, even when empty
    /// of news — a job UI renders this verbatim.
    pub detail: String,
}

/// One cell of the coverage cube: category × aisle × vendor.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CubeCell {
    /// Category node.
    pub category: String,
    /// Aisle.
    pub aisle: String,
    /// Vendor.
    pub vendor: String,
    /// Distinct parts in the cell.
    pub parts: usize,
    /// Claims in the cell.
    pub claims: usize,
    /// `D7` honesty: whether this cell is actually covered end to end.
    pub laned: bool,
    /// Whether characteristic curves exist for the cell.
    pub curves: bool,
}

/// The coverage cube slice.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CoverageCube {
    /// Which axes the cube is cut by, in order.
    pub dimensions: Vec<String>,
    /// The cells.
    pub cells: Vec<CubeCell>,
    /// Whether the whole slice is laned. Honesty flag, never optimistic.
    pub laned: bool,
}

/// One gap: a thing this release knows it does not know.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Gap {
    /// Which axis, when it has a name.
    pub axis: Option<String>,
    /// The colloquial phrase that bound to nothing, when that is the origin.
    pub phrase: Option<String>,
    /// Why it is a gap.
    pub why: String,
    /// How many parts it affects.
    pub affected: usize,
}

/// The gap ledger.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct GapLedger {
    /// Which release the gaps are about.
    pub release_id: ReleaseId,
    /// The gaps.
    pub gaps: Vec<Gap>,
}

/// Everything a route needs from the world. A real store implements this;
/// nothing above it knows where the data came from.
pub trait ApiStore {
    /// Which immutable publication is being served.
    fn release_id(&self) -> &ReleaseId;
    /// The lineage affordance for that publication.
    fn lineage(&self) -> &Lineage;
    /// The release's standing [`Build`] — the funnel state a UI is showing.
    /// Non-funnel routes build their feed block from this, so every response
    /// carries the same truth.
    fn feed_build(&self) -> Build;
    /// `GET /api/extraction/v1/parts/{part_number}`.
    fn part(&self, part_number: &str) -> Option<PartRecord>;
    /// `GET /api/discovery/v1/designs/{design_id}`.
    fn design(&self, design_id: &str) -> Option<Build>;
    /// `GET /api/pipeline/v1/jobs/{job_id}`.
    fn job(&self, job_id: &str) -> Option<JobRecord>;
    /// `POST /api/search/v1/query` — run the funnel over one sentence.
    fn search(&self, raw: &str) -> Build;
    /// `GET /api/coverage/v1/cube`.
    fn cube(&self) -> CoverageCube;
    /// `GET /api/coverage/v1/gaps`.
    fn gaps(&self) -> GapLedger;
    /// Resolve a [`ClaimSource`] to a complete [`EvidenceRef`] — page render
    /// and crop refs in the payload so no second call is needed.
    ///
    /// `None` means the corpus printed no page to open: the claim stays on
    /// the wire with its gaps, and the feed carries only what actually
    /// resolves (`D7`: evidence links or an honest absence — never a link
    /// that 404s).
    fn resolve_evidence(&self, field: &str, source: &ClaimSource) -> Option<EvidenceRef>;
}

/// In-memory demo store. Honest data, real types — the point is the wire
/// shape, and the wire shape is the same for every store.
#[derive(Debug, Clone)]
pub struct DemoStore {
    release: ReleaseId,
    lineage: Lineage,
    parts: BTreeMap<String, PartRecord>,
    designs: BTreeMap<String, Build>,
    jobs: BTreeMap<String, JobRecord>,
    cube: CoverageCube,
    gaps: GapLedger,
    /// Document hash used for every demo evidence ref.
    demo_doc: Sha256,
}

impl DemoStore {
    /// Build the demo world: one part, one design, one job, an honest cube,
    /// and a gap ledger that says what it does not know.
    ///
    /// # Panics
    ///
    /// If the literal `ReleaseId`s or `Sha256` constants fail their own
    /// invariants — impossible by construction (64 lowercase hex, non-empty),
    /// and loud if someone edits them wrong.
    #[must_use]
    #[allow(clippy::expect_used, reason = "constant id literals; see # Panics")]
    pub fn new() -> Self {
        let release = ReleaseId::new("rel-demo-20261011").expect("non-empty demo release id");
        let demo_doc =
            Sha256::new("d3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
                .expect("64 hex");
        let lineage = Lineage {
            release_id: release.clone(),
            parent: Some(ReleaseId::new("rel-demo-20261004").expect("non-empty")),
            engines: vec!["pdfium-render:0.9.4".into(), "rust:dw-0.1.0".into()],
            rollback_to: Some(ReleaseId::new("rel-demo-20261004").expect("non-empty")),
        };

        let source = |page: u32, row: &str, col: &str| -> ClaimSource {
            ClaimSource {
                doc_sha256: Some(demo_doc.as_str().to_string()),
                sha: Some(demo_doc.as_str().to_string()),
                page: Some(page),
                row_header: Some(row.into()),
                column_header: Some(col.into()),
                crop: None,
                source_pdf: None,
                provenance_tier: None,
            }
        };

        let mut parts = BTreeMap::new();
        parts.insert(
            "BU15TA2WHFV".into(),
            PartRecord {
                part_number: "BU15TA2WHFV".into(),
                vendor: "ROHM".into(),
                claims: vec![
                    PartClaim {
                        field: "vout_typ_v".into(),
                        value: "1.5".into(),
                        quote: Some("1.5V(Typ.)".into()),
                        source: source(2, "Output voltage (V)", "Typ"),
                    },
                    PartClaim {
                        field: "temp_max_c".into(),
                        value: "85".into(),
                        quote: Some("-40 to +85".into()),
                        source: source(3, "Operating temperature", "Max"),
                    },
                ],
            },
        );

        let mut designs = BTreeMap::new();
        let build = Self::demo_build(&release);
        designs.insert(build.id.clone(), build);

        let mut jobs = BTreeMap::new();
        jobs.insert(
            "job-42".into(),
            JobRecord {
                job_id: "job-42".into(),
                kind: "substrate".into(),
                state: JobState::Succeeded,
                detail: "48 pages rendered, 3 claims extracted, 0 held out".into(),
            },
        );

        let cube = CoverageCube {
            dimensions: vec!["category".into(), "aisle".into(), "vendor".into()],
            cells: vec![CubeCell {
                category: "pmic/ldo".into(),
                aisle: "low-dropout".into(),
                vendor: "ROHM".into(),
                parts: 12,
                claims: 411,
                laned: true,
                curves: true,
            }],
            laned: true,
        };

        let gaps = GapLedger {
            release_id: release.clone(),
            gaps: vec![Gap {
                axis: Some("quiescent_current".into()),
                phrase: Some("battery drain".into()),
                why: "no claim printed for Iq on 3 of 12 parts in this cell".into(),
                affected: 3,
            }],
        };

        Self {
            release,
            lineage,
            parts,
            designs,
            jobs,
            cube,
            gaps,
            demo_doc,
        }
    }

    /// A real [`dw_funnel::accept`] run, so the demo response is produced by
    /// the same code path the product runs.
    fn demo_build(release: &ReleaseId) -> Build {
        let registry = dw_knives::seed_from_measured_chips();
        let cohort = Cohort::from_candidates(vec![
            Candidate {
                part_number: "BU15TA2WHFV".into(),
                vendor: "ROHM".into(),
            },
            Candidate {
                part_number: "BU15TD3WG".into(),
                vendor: "ROHM".into(),
            },
            Candidate {
                part_number: "BD50FC0WEFJ".into(),
                vendor: "ROHM".into(),
            },
        ]);
        let facts: BTreeMap<Candidate, CandidateFacts> = BTreeMap::new();
        let answer = dw_funnel::accept(
            "3.3v ldo for a weather sensor",
            &registry,
            &cohort,
            &facts,
            release.clone(),
        );
        answer.build
    }

    /// The document hash every demo evidence ref points at.
    #[must_use]
    pub fn demo_document(&self) -> &Sha256 {
        &self.demo_doc
    }
}

impl Default for DemoStore {
    fn default() -> Self {
        Self::new()
    }
}

impl ApiStore for DemoStore {
    fn release_id(&self) -> &ReleaseId {
        &self.release
    }

    fn lineage(&self) -> &Lineage {
        &self.lineage
    }

    fn feed_build(&self) -> Build {
        self.designs
            .values()
            .next()
            .cloned()
            .unwrap_or_else(|| Self::demo_build(&self.release))
    }

    fn part(&self, part_number: &str) -> Option<PartRecord> {
        self.parts.get(part_number).cloned()
    }

    fn design(&self, design_id: &str) -> Option<Build> {
        self.designs.get(design_id).cloned()
    }

    fn job(&self, job_id: &str) -> Option<JobRecord> {
        self.jobs.get(job_id).cloned()
    }

    fn search(&self, raw: &str) -> Build {
        let registry = dw_knives::seed_from_measured_chips();
        let cohort = Cohort::from_candidates(
            self.parts
                .values()
                .map(|p| Candidate {
                    part_number: p.part_number.clone(),
                    vendor: p.vendor.clone(),
                })
                .collect(),
        );
        let facts: BTreeMap<Candidate, CandidateFacts> = BTreeMap::new();
        dw_funnel::accept(raw, &registry, &cohort, &facts, self.release.clone()).build
    }

    fn cube(&self) -> CoverageCube {
        self.cube.clone()
    }

    fn gaps(&self) -> GapLedger {
        self.gaps.clone()
    }

    fn resolve_evidence(&self, field: &str, source: &ClaimSource) -> Option<EvidenceRef> {
        resolve_claim_source(field, source)
    }
}

/// Resolve a [`ClaimSource`] to renderable evidence — the one place the
/// document/page/crop rules live, shared by every store.
///
/// `None` when the corpus printed no page to open (or no well-formed
/// document hash): the claim keeps its gaps on the wire and the feed carries
/// only what actually resolves.
#[must_use]
pub fn resolve_claim_source(field: &str, source: &ClaimSource) -> Option<EvidenceRef> {
    let doc = source.document()?;
    let page = source.page?;
    let doc_sha = Sha256::new(doc).ok()?;
    let page_sha = doc_sha.clone();
    let crop_sha = source.crop.as_ref().map(|c| c.crop_sha256.clone());
    Some(EvidenceRef {
        field: field.to_string(),
        document_sha256: doc_sha,
        page,
        row_header: source.row_header.clone(),
        column_header: source.column_header.clone(),
        quote: None,
        page_render: EvidenceAsset {
            sha256: page_sha,
            href: format!("/evidence/pages/{doc}.png"),
            dpi: dw_types::CropRef::PROVENANCE_DPI,
            bbox: [0, 0, 0, 0],
        },
        crop: source.crop.as_ref().map(|c| EvidenceAsset {
            sha256: c.crop_sha256.clone(),
            href: format!("/evidence/crops/{}.png", c.crop_sha256),
            dpi: dw_types::CropRef::PROVENANCE_DPI,
            bbox: c.bbox,
        }),
        crop_sha256: crop_sha,
    })
}
