//! The extraction export behind [`ApiStore`] — the loader the seam exists
//! for.
//!
//! `dw-extract`'s `tools/export_api_store.py` writes the corpus into wire
//! shapes at a directory (`parts_catalog.jsonl`, `parts_family.jsonl`,
//! `cube.json`, `manifest.json`); this store reads them. It is a **loader,
//! not a redesign**: [`ExportStore::part`] walks `parts_*.jsonl` and
//! [`ExportStore::cube`] reads `cube.json`. Surfaces the export does not
//! carry (designs, jobs, search, gaps, the standing build) fall through to
//! the [`DemoStore`] fallback until the catalog store lands.
//!
//! Two feed conventions meet here and are named rather than papered over:
//! the catalog feed records the **document** in `sha` (no pages), the family
//! feed records a *verbatim* hash in `sha` and the document in `doc_sha256`.
//! [`ClaimSource::document`] resolves that, and evidence only resolves when
//! the corpus printed a page to open.

use std::path::{Path, PathBuf};

use crate::feed::EvidenceRef;
use crate::feed::Lineage;
use crate::store::{
    resolve_claim_source, ApiStore, ClaimSource, CoverageCube, DemoStore, GapLedger, JobRecord,
    PartRecord,
};
use dw_decide::Build;
use dw_types::ReleaseId;

/// A store backed by the extraction export on disk.
#[derive(Debug, Clone)]
pub struct ExportStore {
    root: PathBuf,
    fallback: DemoStore,
}

impl ExportStore {
    /// Open an export directory (`api-store-export/`). The directory must
    /// contain `parts_*.jsonl`; `cube.json` is read per request so a fresh
    /// export is picked up on reload without a restart.
    #[must_use]
    pub fn open(root: impl Into<PathBuf>) -> Self {
        Self {
            root: root.into(),
            fallback: DemoStore::new(),
        }
    }

    /// The export root.
    #[must_use]
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// The `parts_*.jsonl` feeds, catalog first (first match wins on part
    /// lookups). `AppleDouble` `._*` noise never matches the glob.
    fn feeds(&self) -> Vec<PathBuf> {
        let mut feeds: Vec<PathBuf> = self
            .root
            .read_dir()
            .map(|entries| {
                entries
                    .filter_map(Result::ok)
                    .map(|e| e.path())
                    .filter(|p| {
                        p.file_name()
                            .and_then(|n| n.to_str())
                            .is_some_and(|n| n.starts_with("parts_"))
                            && p.extension().is_some_and(|x| x == "jsonl")
                    })
                    .collect()
            })
            .unwrap_or_default();
        feeds.sort();
        feeds
    }
}

impl ApiStore for ExportStore {
    fn release_id(&self) -> &ReleaseId {
        self.fallback.release_id()
    }

    fn lineage(&self) -> &Lineage {
        self.fallback.lineage()
    }

    fn feed_build(&self) -> Build {
        self.fallback.feed_build()
    }

    /// Walk `parts_*.jsonl` for the first row naming `part_number`. Lines
    /// that do not parse are skipped — one bad row must not take the store
    /// down — and the walk order is catalog before family.
    fn part(&self, part_number: &str) -> Option<PartRecord> {
        for feed in self.feeds() {
            let Ok(text) = std::fs::read_to_string(&feed) else {
                continue;
            };
            for line in text.lines() {
                if line.trim().is_empty() {
                    continue;
                }
                let Ok(row) = serde_json::from_str::<PartRecord>(line) else {
                    continue;
                };
                if row.part_number == part_number {
                    return Some(row);
                }
            }
        }
        None
    }

    fn design(&self, design_id: &str) -> Option<Build> {
        self.fallback.design(design_id)
    }

    fn job(&self, job_id: &str) -> Option<JobRecord> {
        self.fallback.job(job_id)
    }

    fn search(&self, raw: &str) -> Build {
        self.fallback.search(raw)
    }

    /// The export's `cube.json`, re-read per request — a fresh export shows
    /// on reload.
    fn cube(&self) -> CoverageCube {
        std::fs::read_to_string(self.root.join("cube.json"))
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_else(|| self.fallback.cube())
    }

    fn gaps(&self) -> GapLedger {
        self.fallback.gaps()
    }

    fn resolve_evidence(&self, field: &str, source: &ClaimSource) -> Option<EvidenceRef> {
        resolve_claim_source(field, source)
    }
}
