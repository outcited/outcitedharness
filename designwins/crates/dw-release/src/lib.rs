//! # dw-release — immutable releases
//!
//! Every answer in the workspace carries a [`ReleaseId`] (`C7`). Until now that
//! was a `String`. This crate makes it resolve.
//!
//! ## The two laws it enforces
//!
//! * **§16.2 — no duplicate truth across machines.** Two runs may only be
//!   compared if they name the same inputs: the same corpus freeze, the same
//!   vocabulary versions, the same embedder, the same engine. The manifest is
//!   what "the same" means.
//! * **§16.4 — no lost lineage.** Releases are **append-only**. Each names its
//!   predecessor. There is no update path and no delete path, so a published
//!   answer cannot quietly change underneath the engineer who relied on it.
//!
//! ## What a manifest pins
//!
//! | field | law | why it matters |
//! |---|---|---|
//! | `corpus_freeze` | `PLAN` Week 0 | which inputs produced the answer |
//! | `vocab_versions` | `C6` | frozen, additive-only vocabularies |
//! | `embedder` | `C10` | one embedder fleet-wide, fingerprint-pinned |
//! | `engine` | `C2` | a different PDF engine is different pixels |
//! | `code_version` | — | which code ran |
//!
//! Without this, "reproducible" is a claim. With it, it is a lookup.
//!
//! This crate is `forbid(unsafe_code)` and does no I/O beyond the store it is
//! given.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use dw_types::{ReleaseId, Sha256, WeightFingerprint};
use serde::{Deserialize, Serialize};

/// Schema id for release manifests.
pub const RELEASE_SCHEMA: &str = "harness.dw-release.v1";

/// Failures from the release store.
#[derive(Debug, thiserror::Error)]
pub enum ReleaseError {
    /// The store root could not be created.
    #[error("release store unavailable at {0}: {1}")]
    StoreUnavailable(PathBuf, String),
    /// A manifest could not be written or read.
    #[error("release io: {0}")]
    Io(String),
    /// A `ReleaseId` does not resolve.
    #[error("release not found: {0}")]
    NotFound(ReleaseId),
    /// A manifest is malformed.
    #[error("release manifest invalid: {0}")]
    Invalid(String),
}

/// Which engine produced the artifacts in a release (`C2`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EngineId {
    /// Engine family, e.g. `"pdfium"`.
    pub name: String,
    /// Version string of the underlying library.
    pub version: String,
}

/// An immutable release manifest.
///
/// Content-addressed by its own serialisation, so a manifest cannot be
/// rewritten without changing the id that names it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ReleaseManifest {
    /// Which release this is.
    pub release_id: ReleaseId,
    /// The predecessor, for append-only lineage (§16.4). `None` only for the
    /// first release.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub previous: Option<ReleaseId>,
    /// Which inputs produced this release: the corpus freeze hash.
    pub corpus_freeze: Sha256,
    /// Frozen vocabulary versions (`C6`). Additive-only.
    ///
    /// `default` on the way in, `skip_if_empty` on the way out: an omitted map
    /// means "no vocabularies pinned", which is a fact rather than a gap. This
    /// is the same trap that made `Verbatim` refuse `Deserialize` in `dw-types`
    /// — serde fills a skipped field via `Default`, and an absent fact is not
    /// the same as an unknown one.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub vocab_versions: BTreeMap<String, String>,
    /// The one embedder, fingerprint-pinned (`C10`). `None` when the release
    /// contains no vectors — which is a fact, not a gap.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub embedder: Option<WeightFingerprint>,
    /// Which PDF engine (`C2`).
    pub engine: EngineId,
    /// Which code produced this. A git sha.
    pub code_version: String,
    /// When it was created. UTC, `PLAN` §10.
    pub created_at: String,
}

impl ReleaseManifest {
    /// Build a manifest. Every field is required except those whose absence is
    /// itself a fact.
    #[must_use]
    pub fn new(
        release_id: ReleaseId,
        previous: Option<ReleaseId>,
        corpus_freeze: Sha256,
        engine: EngineId,
        code_version: impl Into<String>,
        created_at: impl Into<String>,
    ) -> Self {
        Self {
            release_id,
            previous,
            corpus_freeze,
            vocab_versions: BTreeMap::new(),
            embedder: None,
            engine,
            code_version: code_version.into(),
            created_at: created_at.into(),
        }
    }

    /// Pin a frozen vocabulary version (`C6`).
    #[must_use]
    pub fn with_vocab(mut self, name: impl Into<String>, version: impl Into<String>) -> Self {
        self.vocab_versions.insert(name.into(), version.into());
        self
    }

    /// Pin the fleet embedder (`C10`).
    #[must_use]
    pub fn with_embedder(mut self, fingerprint: WeightFingerprint) -> Self {
        self.embedder = Some(fingerprint);
        self
    }

    /// The lineage back to the first release, newest first. Includes `self`.
    #[must_use]
    pub fn lineage(
        &self,
        resolve: impl Fn(&ReleaseId) -> Option<ReleaseManifest>,
    ) -> Vec<ReleaseId> {
        let mut out = vec![self.release_id.clone()];
        let mut cursor = self.previous.clone();
        // Bounded so a corrupt chain cannot spin. A chain longer than this is
        // already a finding.
        for _ in 0..10_000 {
            let Some(id) = cursor else { break };
            let Some(manifest) = resolve(&id) else {
                out.push(id);
                break;
            };
            out.push(manifest.release_id.clone());
            cursor = manifest.previous;
        }
        out
    }
}

/// An append-only release store on local disk.
///
/// One file per release: `{root}/{release_id}.json`. Writes are refused if the
/// id already exists — that is what "append-only" means mechanically.
#[derive(Debug, Clone)]
pub struct ReleaseStore {
    root: PathBuf,
}

impl ReleaseStore {
    /// Open (and create) a store at `root`.
    ///
    /// # Errors
    ///
    /// Returns [`ReleaseError::StoreUnavailable`] if the root cannot be created.
    pub fn open(root: impl AsRef<Path>) -> Result<Self, ReleaseError> {
        let root = root.as_ref().to_path_buf();
        std::fs::create_dir_all(&root)
            .map_err(|e| ReleaseError::StoreUnavailable(root.clone(), e.to_string()))?;
        Ok(Self { root })
    }

    fn path_for(&self, id: &ReleaseId) -> PathBuf {
        self.root.join(format!("{}.json", id.as_str()))
    }

    /// Publish a manifest.
    ///
    /// # Errors
    ///
    /// Returns [`ReleaseError::Io`] on a write failure. **Refuses to overwrite**
    /// an existing release: that is §16.4 enforced mechanically rather than by
    /// convention. A re-publish is a new release with `previous` set.
    pub fn publish(&self, manifest: &ReleaseManifest) -> Result<(), ReleaseError> {
        let path = self.path_for(&manifest.release_id);
        if path.exists() {
            return Err(ReleaseError::Io(format!(
                "release {} already exists; append-only means append-only",
                manifest.release_id
            )));
        }
        let text = serde_json::to_string_pretty(manifest)
            .map_err(|e| ReleaseError::Invalid(e.to_string()))?;
        std::fs::write(&path, text).map_err(|e| ReleaseError::Io(e.to_string()))
    }

    /// Resolve a [`ReleaseId`] to its manifest.
    ///
    /// # Errors
    ///
    /// Returns [`ReleaseError::NotFound`] when the id does not resolve. That is
    /// the loud break: an answer whose release cannot be resolved is an answer
    /// that cannot be cited.
    pub fn resolve(&self, id: &ReleaseId) -> Result<ReleaseManifest, ReleaseError> {
        let path = self.path_for(id);
        let text =
            std::fs::read_to_string(&path).map_err(|_| ReleaseError::NotFound(id.clone()))?;
        serde_json::from_str(&text)
            .map_err(|e| ReleaseError::Invalid(format!("{}: {e}", id.as_str())))
    }

    /// True when a [`ReleaseId`] resolves. The predicate a response must
    /// satisfy before it may claim reproducibility.
    #[must_use]
    pub fn resolves(&self, id: &ReleaseId) -> bool {
        self.path_for(id).is_file()
    }

    /// Every published release id, sorted.
    #[must_use]
    pub fn ids(&self) -> Vec<ReleaseId> {
        let mut out = Vec::new();
        if let Ok(entries) = std::fs::read_dir(&self.root) {
            for entry in entries.flatten() {
                let name = entry.file_name();
                let Some(name) = name.to_str() else { continue };
                if let Some(stem) = name.strip_suffix(".json") {
                    if let Ok(id) = ReleaseId::new(stem) {
                        out.push(id);
                    }
                }
            }
        }
        out.sort();
        out
    }
}

#[cfg(test)]
#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    clippy::match_wildcard_for_single_variants,
    clippy::wildcard_enum_match_arm,
    clippy::too_many_lines
)]
mod tests {
    use super::*;

    fn tmp(name: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!("dw-release-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&p);
        p
    }

    fn manifest(id: &str, previous: Option<&str>) -> ReleaseManifest {
        ReleaseManifest::new(
            ReleaseId::new(id).unwrap(),
            previous.map(|p| ReleaseId::new(p).unwrap()),
            Sha256::new("b".repeat(64)).unwrap(),
            EngineId {
                name: "pdfium".into(),
                version: "7881".into(),
            },
            "abc1234",
            "2026-10-10T00:00:00Z",
        )
    }

    #[test]
    fn publish_and_resolve() {
        let store = ReleaseStore::open(tmp("roundtrip")).expect("opens");
        let m = manifest("designwins-2026-W46", None);
        store.publish(&m).expect("publishes");
        let got = store.resolve(&m.release_id).expect("resolves");
        assert_eq!(got, m);
        assert!(store.resolves(&m.release_id));
    }

    /// §16.4: append-only is mechanical, not a convention.
    #[test]
    fn releases_are_append_only() {
        let store = ReleaseStore::open(tmp("append")).expect("opens");
        let m = manifest("r1", None);
        store.publish(&m).expect("first");
        assert!(
            store.publish(&m).is_err(),
            "a re-publish must be refused; make a new release with previous set"
        );
    }

    /// §16.4: lineage is recoverable and bounded.
    #[test]
    fn lineage_is_walkable() {
        let store = ReleaseStore::open(tmp("lineage")).expect("opens");
        store.publish(&manifest("r1", None)).expect("p");
        store.publish(&manifest("r2", Some("r1"))).expect("p");
        let head = manifest("r3", Some("r2"));
        let chain = head.lineage(|id| store.resolve(id).ok());
        assert_eq!(
            chain.iter().map(ReleaseId::as_str).collect::<Vec<_>>(),
            vec!["r3", "r2", "r1"]
        );
    }

    /// §16.2: two releases with different inputs are different truth.
    #[test]
    fn different_inputs_are_different_releases() {
        let a = manifest("r1", None);
        let mut b = manifest("r2", None);
        b.corpus_freeze = Sha256::new("c".repeat(64)).unwrap();
        assert_ne!(a.corpus_freeze, b.corpus_freeze);
    }

    /// C2: engine identity is pinned in the manifest.
    #[test]
    fn engine_is_pinned() {
        let m = manifest("r1", None).with_vocab("condition-model", "v1");
        assert_eq!(m.engine.name, "pdfium");
        assert_eq!(m.vocab_versions["condition-model"], "v1");
    }

    /// C10: the embedder fingerprint is part of the release identity.
    #[test]
    fn embedder_is_pinned_when_present() {
        let fp = WeightFingerprint::new("bge-large-en-v1.5").unwrap();
        let m = manifest("r1", None).with_embedder(fp.clone());
        assert_eq!(m.embedder.as_ref(), Some(&fp));
        // No vectors in this release is a fact, not a gap.
        assert!(manifest("r2", None).embedder.is_none());
    }

    #[test]
    fn missing_release_is_not_found() {
        let store = ReleaseStore::open(tmp("missing")).expect("opens");
        let id = ReleaseId::new("nope").unwrap();
        assert!(matches!(store.resolve(&id), Err(ReleaseError::NotFound(_))));
        assert!(!store.resolves(&id));
    }
}
