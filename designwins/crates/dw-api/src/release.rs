//! An immutable DesignWins spine release behind [`ApiStore`] — the r2
//! integration (isolated and reviewed; **not** the live ExportStore).
//!
//! A release directory is `RELEASE.json` + content-hashed feeds:
//!
//! ```text
//! `designwins-spine-s6-r2-20261021`/
//!   RELEASE.json             release_id, previous, what_changed
//!   CONTENT_HASHES.sha256    shasum -a 256 listing of every feed
//!   parts_spine.jsonl        part records with normalized, verified claims
//!   parts_structural.jsonl   verified pin tables + typical curves (per part)
//!   compare_ready_spine.jsonl
//! ```
//!
//! Mount is **fail-closed**: `RELEASE.json` must parse and every hash in
//! `CONTENT_HASHES.sha256` must match the bytes on disk, or [`ReleaseStore::open`]
//! returns an error naming the offender. The release id served is the real one
//! — never a demo constant — and lineage points at the release this one
//! supersedes.
//!
//! Evidence is **real or absent**: a claim's page render resolves only when
//! the rendered bytes exist under the evidence root
//! (`pages/{prefix}/{doc}-{page}.png`). No synthetic page hashes — the
//! document+page+quote locator rides on the claim itself, and an unresolvable
//! render is an honest absence (`D7`), never a link that 404s or a hash that
//! pretends.
//!
//! Surfaces this release does not carry (designs, jobs, search, gaps, the
//! standing build) fall through to [`DemoStore`], exactly like
//! [`crate::export::ExportStore`]; `cube` reads `cube.json` when present.
//! Those fallbacks are demo data and say so on `/healthz` lineage — the
//! part surface is the real one this store exists for.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::Deserialize;

use crate::feed::{EvidenceAsset, EvidenceRef, Lineage};
use crate::store::{
    ApiStore, ClaimSource, CoverageCube, CurveFacts, CurvePageRef, DemoStore, GapLedger, JobRecord,
    PartClaim, PartRecord, PartStructural, PinRowRef, PinTableRef,
};
use dw_decide::Build;
use dw_types::{ReleaseId, Sha256};

/// Why a release directory refused to mount. Renderable, named, never vague.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MountError {
    /// The directory that failed.
    pub root: PathBuf,
    /// The named offense (`release_json`, `hash_mismatch`, …).
    pub reason: String,
}

impl std::fmt::Display for MountError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "release mount failed at {}: {}", self.root.display(), self.reason)
    }
}
impl std::error::Error for MountError {}

/// The r2 spine release, mounted read-only.
#[derive(Debug, Clone)]
pub struct ReleaseStore {
    root: PathBuf,
    evidence_root: Option<PathBuf>,
    release_id: ReleaseId,
    lineage: Lineage,
    parts: BTreeMap<String, PartRecord>,
    structural: BTreeMap<String, PartStructural>,
    fallback: DemoStore,
}

/// The spine feed row, loose on purpose: the loader is a mapper, not a gate.
#[derive(Debug, Deserialize)]
struct SpineRow {
    part_number: String,
    #[serde(default)]
    vendor: String,
    #[serde(default)]
    claims: Vec<SpineClaim>,
}

#[derive(Debug, Deserialize)]
struct SpineClaim {
    field: String,
    value: serde_json::Value,
    #[serde(default)]
    unit: Option<String>,
    #[serde(default)]
    rating_class: Option<String>,
    #[serde(default)]
    role: Option<String>,
    #[serde(default)]
    conditions: Option<String>,
    source: SpineSource,
}

#[derive(Debug, Deserialize)]
struct SpineSource {
    #[serde(default)]
    doc_sha256: Option<String>,
    #[serde(default)]
    page: Option<u32>,
    #[serde(default)]
    quote: Option<String>,
}

/// The structural feed row (`harness.spine.structural.v1`).
#[derive(Debug, Deserialize)]
struct StructuralRow {
    part_number: String,
    #[serde(default)]
    pin_tables: Vec<StructuralPinTable>,
    #[serde(default)]
    pin_row_total: u32,
    #[serde(default)]
    typical_curves: u32,
    #[serde(default)]
    curve_pages: Vec<StructuralCurvePage>,
    #[serde(default)]
    association_methods: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct StructuralPinTable {
    sha: String,
    #[serde(default)]
    page: Option<u32>,
    #[serde(default)]
    rows: Vec<PinRowRef>,
}

#[derive(Debug, Deserialize)]
struct StructuralCurvePage {
    sha: String,
    page: u32,
    n: u32,
}

/// sha256 of a byte slice, lowercase hex. FIPS 180-4 notation kept
/// single-character (`h`, `w`, `a`..`hh`) and the K table verbatim — renaming
/// the standard's variables would obscure the algorithm being audited.
#[allow(
    clippy::many_single_char_names,
    reason = "FIPS 180-4 notation; see comment"
)]
#[allow(
    clippy::unreadable_literal,
    reason = "the round-constant table is copied from the standard"
)]
#[allow(
    clippy::indexing_slicing,
    reason = "chunk sizes are constructed so indexes are always in range (chunks(4), get(..2))"
)]
fn sha256_hex(bytes: &[u8]) -> String {
    // Minimal SHA-256 (FIPS 180-4) — no external crate; the workspace keeps
    // dw-api dependency-free and this is 60 lines of arithmetic that the
    // mount gate depends on, so it lives where it is used.
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    ];
    let mut h: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ];
    let bit_len = (bytes.len() as u64).wrapping_mul(8);
    let mut data = bytes.to_vec();
    data.push(0x80);
    while data.len() % 64 != 56 {
        data.push(0);
    }
    data.extend_from_slice(&bit_len.to_be_bytes());
    for block in data.chunks(64) {
        let mut w = [0u32; 64];
        for (i, chunk) in block.chunks(4).enumerate() {
            w[i] = u32::from_be_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }
        let (mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut hh) =
            (h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7]);
        for (i, k) in K.iter().enumerate() {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ ((!e) & g);
            let t1 = hh
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(*k)
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            hh = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        h[0] = h[0].wrapping_add(a);
        h[1] = h[1].wrapping_add(b);
        h[2] = h[2].wrapping_add(c);
        h[3] = h[3].wrapping_add(d);
        h[4] = h[4].wrapping_add(e);
        h[5] = h[5].wrapping_add(f);
        h[6] = h[6].wrapping_add(g);
        h[7] = h[7].wrapping_add(hh);
    }
    h.iter().fold(String::new(), |mut s, x| { use std::fmt::Write as _; let _ = write!(s, "{x:08x}"); s })
}


/// The content-hash gate: every listed file must hash to its published
/// value. Named offenses only — which file, expected vs got.
fn verify_content_hashes(root: &Path) -> Result<(), MountError> {
    let fail = |reason: String| MountError { root: root.to_path_buf(), reason };
    let hashes_text = std::fs::read_to_string(root.join("CONTENT_HASHES.sha256"))
        .map_err(|e| fail(format!("CONTENT_HASHES.sha256 unreadable: {e}")))?;
    for line in hashes_text.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let mut parts = line.split_whitespace();
        let (Some(expect), Some(name)) = (parts.next(), parts.next()) else {
            continue; // not a shasum line (header/footer) — nothing to check
        };
        let bytes = std::fs::read(root.join(name))
            .map_err(|_| fail(format!("hash list names {name}, which is missing")))?;
        let got = sha256_hex(&bytes);
        if got != expect {
            return Err(fail(format!("hash mismatch for {name}: expected {expect}, got {got}")));
        }
    }
    Ok(())
}

impl ReleaseStore {
    /// Mount a release directory, fail-closed on any inconsistency.
    ///
    /// # Errors
    ///
    /// [`MountError`] when `RELEASE.json` is missing or unparseable, a listed
    /// feed is missing, or any content hash does not match the bytes.
    pub fn open(root: impl Into<PathBuf>) -> Result<Self, MountError> {
        Self::open_with_evidence(root, None)
    }

    /// Mount with an evidence root for real page renders
    /// (`pages/{prefix}/{doc}-{page}.png`).
    ///
    /// # Errors
    ///
    /// As [`ReleaseStore::open`].
    pub fn open_with_evidence(
        root: impl Into<PathBuf>,
        evidence_root: Option<PathBuf>,
    ) -> Result<Self, MountError> {
        let root = root.into();
        let fail = |reason: String| MountError { root: root.clone(), reason };

        let release_json: serde_json::Value = std::fs::read_to_string(root.join("RELEASE.json"))
            .map_err(|e| fail(format!("RELEASE.json unreadable: {e}")))?
            .parse()
            .map_err(|e| fail(format!("RELEASE.json unparseable: {e}")))?;
        let release_id = release_json
            .get("release_id")
            .and_then(|v| v.as_str())
            .ok_or_else(|| fail("RELEASE.json: release_id missing".into()))?;
        let release_id = ReleaseId::new(release_id).map_err(|_| fail("RELEASE.json: release_id malformed".into()))?;
        let previous = release_json
            .get("previous")
            .and_then(|v| v.as_str())
            .and_then(|s| ReleaseId::new(s.split_whitespace().next().unwrap_or("")).ok());

        verify_content_hashes(&root)?;

        // Feeds.
        let mut parts = BTreeMap::new();
        let mut structural: BTreeMap<String, PartStructural> = BTreeMap::new();
        for line in read_lines(&root.join("parts_spine.jsonl")) {
            let Ok(row) = serde_json::from_str::<SpineRow>(&line) else {
                continue;
            };
            let claims = row
                .claims
                .iter()
                .map(|c| PartClaim {
                    field: c.field.clone(),
                    value: value_as_printed(&c.value),
                    quote: c.source.quote.clone(),
                    unit: c.unit.clone(),
                    rating_class: c.rating_class.clone(),
                    role: c.role.clone(),
                    conditions: c.conditions.clone(),
                    source: ClaimSource {
                        doc_sha256: c.source.doc_sha256.clone(),
                        sha: None,
                        page: c.source.page,
                        row_header: None,
                        column_header: None,
                        crop: None,
                        source_pdf: None,
                        provenance_tier: None,
                    },
                })
                .collect();
            parts.insert(
                row.part_number.clone(),
                PartRecord {
                    part_number: row.part_number,
                    vendor: row.vendor,
                    claims,
                    structural: None,
                },
            );
        }
        for line in read_lines(&root.join("parts_structural.jsonl")) {
            let Ok(row) = serde_json::from_str::<StructuralRow>(&line) else {
                continue;
            };
            let entry = PartStructural {
                pin_tables: row
                    .pin_tables
                    .into_iter()
                    .map(|t| PinTableRef { sha: t.sha, page: t.page, rows: t.rows })
                    .collect(),
                pin_row_total: row.pin_row_total,
                typical_curves: CurveFacts {
                    count: row.typical_curves,
                    pages: row
                        .curve_pages
                        .into_iter()
                        .map(|p| CurvePageRef { sha: p.sha, page: p.page, n: p.n })
                        .collect(),
                },
                association_methods: row.association_methods,
            };
            structural.insert(row.part_number, entry);
        }

        let lineage = Lineage {
            release_id: release_id.clone(),
            parent: previous.clone(),
            engines: vec![
                "pymupdf:2.0".into(),
                format!("dw-api:release-store:{}", env!("CARGO_PKG_VERSION")),
            ],
            rollback_to: previous,
        };

        Ok(Self {
            release_id,
            lineage,
            parts,
            structural,
            fallback: DemoStore::new(),
            root,
            evidence_root,
        })
    }

    /// The mounted directory.
    #[must_use]
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// The real render for a doc page, if the bytes exist. The returned asset
    /// carries the **content hash of the rendered file** — verified, not
    /// asserted.
    fn real_render(&self, doc: &str, page: u32) -> Option<EvidenceAsset> {
        let root = self.evidence_root.as_ref()?;
        let name = format!("{doc}-{page}.png");
        let prefix = name.get(..2).unwrap_or("00").to_string();
        let path = root.join("pages").join(prefix).join(&name);
        let bytes = std::fs::read(&path).ok()?;
        Some(EvidenceAsset {
            sha256: Sha256::new(sha256_hex(&bytes)).ok()?,
            href: format!("/evidence/pages/{name}"),
            dpi: dw_types::CropRef::PROVENANCE_DPI,
            bbox: [0, 0, 0, 0],
        })
    }
}

fn read_lines(path: &Path) -> Vec<String> {
    std::fs::read_to_string(path)
        .map(|t| t.lines().map(str::to_string).collect())
        .unwrap_or_default()
}

/// Numeric values pass through exactly as the spine printed them —
/// `1.0999999999999999e-7` stays that string, because re-formatting is a
/// normalization decision, not a loader's.
fn value_as_printed(v: &serde_json::Value) -> String {
    match v {
        serde_json::Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

impl ApiStore for ReleaseStore {
    fn release_id(&self) -> &ReleaseId {
        &self.release_id
    }

    fn lineage(&self) -> &Lineage {
        &self.lineage
    }

    /// The standing build, **restamped with this release's identity**: the
    /// demo substrate may provide the shape, but a response from this server
    /// never carries another release's id (issue: reject wrong-release joins).
    fn feed_build(&self) -> Build {
        let mut build = self.fallback.feed_build();
        build.release = self.release_id.clone();
        build
    }

    /// Exact 1:1 join. Structural facts attach **only** to the identical
    /// part string — a case-variant or family-lookalike never joins
    /// (`C7`: the facade join is exact, not a search).
    fn part(&self, part_number: &str) -> Option<PartRecord> {
        let mut record = self.parts.get(part_number).cloned()?;
        if let Some(s) = self.structural.get(part_number) {
            record.structural = Some(s.clone());
        }
        Some(record)
    }

    fn design(&self, design_id: &str) -> Option<Build> {
        self.fallback.design(design_id)
    }

    fn job(&self, job_id: &str) -> Option<JobRecord> {
        self.fallback.job(job_id)
    }

    fn search(&self, raw: &str) -> Build {
        let mut build = self.fallback.search(raw);
        build.release = self.release_id.clone();
        build
    }

    fn cube(&self) -> CoverageCube {
        std::fs::read_to_string(self.root.join("cube.json"))
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_else(|| self.fallback.cube())
    }

    fn gaps(&self) -> GapLedger {
        self.fallback.gaps()
    }

    /// Real renders only: no page, no doc hash, or no rendered bytes ⇒
    /// `None`. The locator (doc, page, quote) stays on the claim; the feed
    /// never carries a link that cannot be opened.
    fn resolve_evidence(&self, field: &str, source: &ClaimSource) -> Option<EvidenceRef> {
        let doc = source.document()?;
        let page = source.page?;
        let doc_sha = Sha256::new(doc).ok()?;
        let page_render = self.real_render(doc, page)?;
        Some(EvidenceRef {
            field: field.to_string(),
            document_sha256: doc_sha,
            page,
            row_header: source.row_header.clone(),
            column_header: source.column_header.clone(),
            quote: None,
            page_render,
            crop: None,
            crop_sha256: None,
        })
    }
}
