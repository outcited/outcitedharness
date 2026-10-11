//! The D7 feed contract: what every response carries inline.
//!
//! The seven required items (`docs/CONTRACTS.md` C7, *Per-response feed
//! contract*):
//!
//! | # | required inline | serves |
//! |---|---|---|
//! | 1 | `release_id` + lineage affordance | release / lineage |
//! | 2 | evidence refs resolving to page render + row-band crop + `crop_sha256` | evidence viewer |
//! | 3 | `unknowns` — never inferred from a missing field | constraint ledger, coverage |
//! | 4 | per-exclusion reason + evidence on every `Ineligible` | cohort explorer |
//! | 5 | ranked `next` questions | next-question rail |
//! | 6 | `derived` vs `declared` preserved | constraint ledger |
//! | 7 | `cohort` as distinct parts with per-part verdict | cohort explorer |
//!
//! ## The wire law
//!
//! Empty blocks are **statements**. `unknowns: []` says "everything was
//! evaluated"; a missing `unknowns` key says nothing and lets a UI invent
//! certainty from absence. So no feed field is `Option`, and none carries
//! `skip_serializing_if` — the keys are present on every response, always.
//! [`FeedAudit`] re-checks that property on the *serialised* body, because
//! the failure mode it exists to catch is a wire shape that quietly loses a
//! block.
//!
//! [`QueryResult`] is C7's struct widened to the full table: the struct in
//! the contract predates D7 and omits `declared` / `derived` / `constraints`
//! / `exclusions`, which the table requires. The table wins.

use serde::{Deserialize, Serialize};

use dw_decide::{
    Atom, Build, Cohort, Derived, DesignIntent, Exclusion, Judgement, MissingAxis, Question,
    Requirement,
};
use dw_types::{ReleaseId, Sha256, SourceRef};

/// `POST /api/search/v1/query` — C7's request shape.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Query {
    /// The sentence, as typed.
    pub raw: String,
    /// A structured intent, when the surface has one. Text wins over `raw`.
    pub intent: Option<DesignIntent>,
    /// Caller-supplied atoms. This release's stores reject them honestly
    /// rather than ignore them (`501`) — see `routes::handle`.
    #[serde(default)]
    pub filters: Vec<Atom>,
    /// Cap on cohort members. `0` means no cap.
    #[serde(default)]
    pub limit: u32,
    /// Which publication the answer must be pinned to.
    pub release: ReleaseId,
}

/// Which immutable publication this answer came from, and how to walk and
/// roll back the lineage from inside the response (D7: *renderable*, not
/// merely present).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Lineage {
    /// This release.
    pub release_id: ReleaseId,
    /// The release this one superseded, when there is one.
    pub parent: Option<ReleaseId>,
    /// Which engines produced the facts in this release (`C2`).
    pub engines: Vec<String>,
    /// What a "roll back" action would roll back to, when one exists. This is
    /// the affordance: a front end renders a button, not a mystery.
    pub rollback_to: Option<ReleaseId>,
}

/// A fetchable evidence artifact: pixels the front end can render directly.
///
/// The ref is **complete** — content address plus the exact rectangle and
/// resolution — so a UI never queries for evidence metadata. `href` is the
/// asset the serve binary exposes; it is pixels, not a second data call.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EvidenceAsset {
    /// Content hash of the encoded image bytes (`pages/` or `crops/`).
    pub sha256: Sha256,
    /// Where the front end fetches the pixels.
    pub href: String,
    /// Render resolution. [`dw_types::CropRef::PROVENANCE_DPI`] is `C13`.
    pub dpi: u32,
    /// Crop rectangle in device pixels at `dpi`, for the crop asset.
    pub bbox: [u32; 4],
}

/// One claim's evidence, resolvable end to end with no second call.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EvidenceRef {
    /// Which claim field this evidence supports.
    pub field: String,
    /// Document content hash — joins to the vault on this, never a filename.
    pub document_sha256: Sha256,
    /// 1-based page within the document.
    pub page: u32,
    /// Row header that labels the value, when it came from a table.
    pub row_header: Option<String>,
    /// Column header that labels the value, when it came from a table.
    pub column_header: Option<String>,
    /// The exact text copied from the page, when the surface needs to show it.
    pub quote: Option<String>,
    /// The page render, resolvable.
    pub page_render: EvidenceAsset,
    /// The row-band crop, resolvable. `Some` for anything shown as a number.
    pub crop: Option<EvidenceAsset>,
    /// `crop_sha256` at the top level because C7 names it there and a diff
    /// against the contract should not need to know where else it lives.
    pub crop_sha256: Option<Sha256>,
}

/// The complete rendering substrate — C7's `QueryResult` widened to the full
/// feed table. Every route embeds this block; the search route returns it
/// directly.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct QueryResult {
    /// Feed item 1: which truth am I looking at.
    pub release_id: ReleaseId,
    /// Feed item 1: the lineage affordance, renderable.
    pub lineage: Lineage,
    /// What the human said, so an intent composer can echo it.
    pub raw: String,
    /// Feed item 7: distinct parts — a candidate you could buy.
    pub cohort: Cohort,
    /// Per-part verdicts with evidence, for the cohort explorer.
    pub explain: Vec<Judgement>,
    /// Feed item 4: every exclusion with its reason and evidence.
    pub exclusions: Vec<Exclusion>,
    /// Feed item 3: what we could not evaluate. **Always present.**
    pub unknowns: Vec<MissingAxis>,
    /// Feed item 5: ranked questions, for the next-question rail.
    pub next: Vec<Question>,
    /// Feed item 6: explicitly stated requirements.
    pub declared: Vec<Requirement>,
    /// Feed item 6: inferred, shown, confirmable — never silent.
    pub derived: Vec<Derived>,
    /// The atoms actually in force.
    pub constraints: Vec<Atom>,
    /// Feed item 2: every number opens to its page.
    pub evidence: Vec<EvidenceRef>,
}

impl QueryResult {
    /// Assemble the feed from a [`Build`] plus resolved evidence.
    ///
    /// This is the only constructor on purpose: the seven items are pulled
    /// from the build, so none can be forgotten at a call site.
    #[must_use]
    pub fn from_build(build: Build, lineage: Lineage, evidence: Vec<EvidenceRef>) -> Self {
        Self {
            raw: match &build.intent {
                dw_decide::DesignIntent::Text(s) => s.clone(),
                other => format!("{other:?}"),
            },
            release_id: build.release.clone(),
            cohort: build.cohort,
            explain: build.explain,
            exclusions: build.exclusions,
            unknowns: build.unknowns,
            next: build.next,
            declared: build.declared,
            derived: build.derived,
            constraints: build.constraints,
            evidence,
            lineage,
        }
    }

    /// Serialise the body a front end receives.
    ///
    /// # Panics
    ///
    /// If serde fails on a type made only of strings, numbers, and vectors of
    /// them — which would be a bug in the derive, not a runtime condition.
    #[must_use]
    #[allow(
        clippy::expect_used,
        reason = "serde on this shape cannot fail; see # Panics"
    )]
    pub fn to_json(&self) -> serde_json::Value {
        serde_json::to_value(self).expect("QueryResult serialises")
    }
}

/// Names of the feed items as they must appear on the wire.
///
/// Item 1 is checked twice: the id and the lineage affordance. Item 2 is
/// checked as the `evidence` block being present and every ref inside it
/// resolving (`crop_sha256` or a page render, never neither).
const FEED_KEYS: &[&str] = &[
    "release_id", // 1
    "lineage",    // 1
    "evidence",   // 2
    "unknowns",   // 3
    "exclusions", // 4
    "next",       // 5
    "declared",   // 6
    "derived",    // 6
    "cohort",     // 7
    "explain",    // 7
];

/// One feed item the serialised body does not carry.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MissingFeedItem(pub &'static str);

/// Audit a **serialised** body against the feed table.
///
/// This runs on the wire shape, not on the struct, because the struct cannot
/// lose a required field — but the wire can, via a `skip_serializing_if`, a
/// renamed field, or a route that builds JSON by hand. Every gate ships with
/// a negative control; these tests are that control for D7.
///
/// # Errors
///
/// Returns every feed item the body does not carry, or that does not resolve.
/// `Ok` means all seven items are present and openable.
pub fn audit(body: &serde_json::Value) -> Result<(), Vec<MissingFeedItem>> {
    let mut missing = Vec::new();
    let Some(obj) = body.as_object() else {
        return Err(FEED_KEYS
            .iter()
            .map(|k| MissingFeedItem(k))
            .collect::<Vec<_>>());
    };
    for key in FEED_KEYS {
        match obj.get(*key) {
            None => missing.push(MissingFeedItem(key)),
            Some(v) if v.is_null() => missing.push(MissingFeedItem(key)),
            Some(_) => {}
        }
    }
    // Item 2, resolved: every evidence ref must actually open somewhere.
    if let Some(evidence) = obj.get("evidence").and_then(|v| v.as_array()) {
        for (i, e) in evidence.iter().enumerate() {
            let resolves = e
                .get("page_render")
                .is_some_and(|p| p.get("href").is_some_and(serde_json::Value::is_string))
                || e.get("crop_sha256")
                    .is_some_and(serde_json::Value::is_string);
            if !resolves {
                missing.push(MissingFeedItem(if i == 0 {
                    "evidence[].page_render.href"
                } else {
                    "evidence[].resolves"
                }));
            }
        }
    }
    // Item 4, resolved: an exclusion that shows up without a reason is the
    // exact failure D7 names ("an exclusion the engineer cannot click open").
    if let Some(exs) = obj.get("exclusions").and_then(|v| v.as_array()) {
        for (i, e) in exs.iter().enumerate() {
            let has_reason = e
                .get("ineligible")
                .is_some_and(|r| r.get("why").is_some_and(serde_json::Value::is_string));
            if !has_reason {
                missing.push(MissingFeedItem(if i == 0 {
                    "exclusions[].ineligible.why"
                } else {
                    "exclusions[].reason"
                }));
            }
        }
    }
    if missing.is_empty() {
        Ok(())
    } else {
        Err(missing)
    }
}

/// Adapter so call sites read as `FeedAudit::of(&body)`.
pub struct FeedAudit;

impl FeedAudit {
    /// See [`audit`].
    ///
    /// # Errors
    ///
    /// See [`audit`].
    pub fn of(body: &serde_json::Value) -> Result<(), Vec<MissingFeedItem>> {
        audit(body)
    }
}

/// Evidence refs for a set of [`SourceRef`]s, via a resolver callable.
///
/// Kept here rather than in `routes` so every surface builds evidence blocks
/// the same way.
#[must_use]
pub fn evidence_from_sources(
    field: &str,
    sources: &[SourceRef],
    mut resolve: impl FnMut(&SourceRef) -> EvidenceRef,
) -> Vec<EvidenceRef> {
    sources
        .iter()
        .map(|s| {
            let mut e = resolve(s);
            e.field = field.to_string();
            e
        })
        .collect()
}
