//! The released evidence substrate: the pillar feed, admitted under an
//! explicit, deterministic policy, loaded into engine `Cohort` + `CandidateFacts`.
//!
//! ## Admission policy (M5-owned, deterministic, test-covered)
//!
//! A pillar row is admitted as evidence only when **all** hold:
//!
//! 1. `identity_status == "stamped"` — the slug→OPN join is proven;
//! 2. `manufacturer` is present — no brand, no projection;
//! 3. `_verify.verdict == "verified"` — the spine's L2 gates passed;
//! 4. the field is in the substrate map below;
//! 5. the value is numeric, finite, and inside the field's plausibility band.
//!    Measured garbage (a part number parsed as `n_can = 734`, an MHz value
//!    canonicalized to Hz) is **refused**, which yields `Unknown` downstream —
//!    never a false elimination.
//!
//! ## Multi-claim policy
//!
//! A part may print a field more than once. For min-rating axes the loader
//! keeps the **best case for the part** (max admitted value) and records the
//! disagreement count — a part with any printed 512 KB flash passes a
//! `>= 128` atom; the receipt names the conflicting claims as an extraction
//! gap rather than letting a stale row eliminate the part.
//!
//! ## Cohort definition
//!
//! The MCU cohort is parts with at least one admitted `flash_kb` **or**
//! `clk_max_mhz` claim (memory/clock markers). Parts outside are **not
//! eliminated** — they are not in the relevant released cohort at all
//! (recorded in feed stats).

use std::cell::RefCell;
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use dw_decide::{AlwaysResolvable, Candidate, CandidateFacts, Cohort, Evidence};
use dw_types::SourceRef;
use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::parse_sha;
use crate::sha256_hex;

/// The substrate map: pillar field → (axis, plausibility band, role).
#[derive(Debug, Clone, Copy, PartialEq)]
struct Substrate {
    field: &'static str,
    axis: &'static str,
    min: f64,
    max: f64,
    /// `Max` keeps the largest admitted value; counts keep the largest too
    /// (presence/quantity thresholds are all min-rating).
    role: Role,
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum Role {
    Max,
}

const SUBSTRATES: &[Substrate] = &[
    Substrate {
        field: "flash_kb",
        axis: "flash_kb",
        min: 1.0,
        max: 8192.0,
        role: Role::Max,
    },
    Substrate {
        field: "clk_max_mhz",
        axis: "freq_mhz",
        min: 0.032,
        max: 1000.0,
        role: Role::Max,
    },
    Substrate {
        field: "n_can",
        axis: "has_can",
        min: 0.0,
        max: 8.0,
        role: Role::Max,
    },
    Substrate {
        field: "n_usb",
        axis: "has_usb",
        min: 0.0,
        max: 4.0,
        role: Role::Max,
    },
];

/// The axis that marks a part as an MCU (cohort membership): admitted
/// **flash** evidence. Clock-frequency prints alone do not make a part an
/// MCU — RF and divider parts print frequencies all day (measured 2026-10-22:
/// STB5600, an RF part, entered the cohort on a divider-chain claim and
/// falsely "qualified" a 170 MHz core-clock atom). Keys are axis names,
/// matching `per_part`.
const MCU_MARKERS: &[&str] = &["flash_kb"];

/// Admission policy version — receipts pin it; a change is a replay event.
pub const ADMISSION_POLICY_VERSION: &str = "bcmp-admission-v2";

/// Is this stamped identity a DOCUMENT id rather than an orderable part?
///
/// A cohort member is a candidate you could buy (CONTRACTS law 2). Measured
/// 2026-10-22: 231/1,856 cohort "parts" (12.45%) were vendor document-series
/// identities — NXP `AN13224`, ST `RM0316…`, onsemi `and9097-d`/`ena2150-d`
/// (doc-numbering filenames), Espressif `…_datasheet` slugs. Their claims
/// are real datasheet content behind a broken identity join; the part behind
/// them is Unknown to us, so refusing the row is honest (Unknown never
/// eliminates) while keeping it would invent phantom OPNs.
fn is_document_identity(part: &str, mfr: &str) -> bool {
    if part.ends_with("_datasheet") || part.ends_with("-datasheet") {
        return true;
    }
    let doc_series: &[&str] = match mfr {
        "st" => &["an", "en", "ta", "um", "pm", "rm", "db", "is", "ts"],
        "nxp" => &["an"],
        "onsemi" => &["and", "ena", "en", "br", "dn"],
        "texas_instruments" => &["slaa", "slau", "slas", "slds", "slla", "sllu", "spr"],
        _ => &[],
    };
    let lower = part.to_lowercase();
    let stem = lower.split('-').next().unwrap_or(&lower);
    for prefix in doc_series {
        let rest = stem.strip_prefix(prefix).unwrap_or_default();
        if !rest.is_empty() && rest.chars().all(|c| c.is_ascii_digit()) {
            return true;
        }
    }
    false
}

/// One pillar feed row, as written by the spine.
#[derive(Debug, Clone, Deserialize)]
pub struct PillarRow {
    /// Document content hash (joins to the vault CAS).
    pub document_sha256: Option<String>,
    /// 1-based page the fact was printed on.
    pub page_1based: Option<u32>,
    /// Pillar field name (`flash_kb`, `n_can`, …).
    pub field: String,
    /// The value as extracted (may be number, bool, or array).
    pub value: Value,
    /// Unit as extracted.
    pub unit: Option<String>,
    /// Verbatim quote from the page.
    pub verbatim: Option<String>,
    /// Manufacturer, when identity stamped one.
    pub manufacturer: Option<String>,
    /// The stamped part identity (OPN or proven slug).
    pub part_identity: Option<String>,
    /// `stamped` / `unresolved` / …
    pub identity_status: Option<String>,
    /// Spine L2 verdict (`verified` / `gate2_fail` / `gate3_fail`).
    #[serde(default, rename = "_verify")]
    pub verify: Value,
}

impl PillarRow {
    fn verify_verdict(&self) -> Option<&str> {
        self.verify.get("verdict").and_then(Value::as_str)
    }
}

/// Where a verbatim quote came from (receipt-level provenance beyond
/// `SourceRef`, which carries no quote text).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct VerbatimRef {
    /// Document sha.
    pub sha256: String,
    /// 1-based page.
    pub page: u32,
    /// The quote.
    pub verbatim: String,
    /// The pillar field.
    pub field: String,
    /// The value as admitted.
    pub value: f64,
}

/// Statistics from one feed load (receipts pin these).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FeedMeta {
    /// SHA-256 of the feed file — the release pin.
    pub feed_sha256: String,
    /// Total rows read.
    pub rows_total: usize,
    /// Rows passing identity + verify gates.
    pub rows_identity_verified: usize,
    /// Rows admitted as axis evidence.
    pub rows_admitted: usize,
    /// Rows refused by the plausibility bands (garbage values).
    pub rows_refused_implausible: usize,
    /// Rows refused because the stamped identity is a document series
    /// (app note / reference manual / datasheet slug), not an OPN.
    pub rows_refused_doc_identity: usize,
    /// Parts in the MCU cohort.
    pub cohort_parts: usize,
    /// Parts stamped but outside the MCU cohort (no memory/clock evidence).
    pub parts_outside_cohort: usize,
    /// Parts whose admitted claims materially disagree (extraction gaps).
    pub parts_with_conflicts: usize,
}

/// The loaded evidence substrate.
pub struct LoadedEvidence {
    /// The MCU cohort (distinct parts, deterministic order).
    pub cohort: Cohort,
    /// Facts per candidate.
    pub facts: BTreeMap<Candidate, CandidateFacts>,
    /// (part, axis) → verbatim refs, capped at 4 entries per key.
    pub verbatim: BTreeMap<(String, String), Vec<VerbatimRef>>,
    /// Load statistics.
    pub meta: FeedMeta,
}

/// Provenance law as a real check: a source resolves when the document sha
/// exists in the vault CAS (content-addressed PDF on the tapes store).
pub struct VaultProvenance {
    root: PathBuf,
    memo: RefCell<BTreeMap<String, bool>>,
}

impl VaultProvenance {
    /// Open over a vault root (the directory containing `cas/`).
    ///
    /// # Errors
    ///
    /// Returns the error message when the CAS directory does not exist — a
    /// provenance resolver over a missing store must fail loudly, not
    /// silently mark everything unresolvable *by accident* (that it marks
    /// everything unresolvable when files are gone is the law working).
    pub fn open(root: impl Into<PathBuf>) -> Result<Self, String> {
        let root = root.into();
        let cas = root.join("cas");
        if cas.is_dir() {
            Ok(Self {
                root,
                memo: RefCell::new(BTreeMap::new()),
            })
        } else {
            Err(format!("vault CAS not found at {}", cas.display()))
        }
    }

    fn resolves(&self, sha_hex: &str) -> bool {
        if let Some(hit) = self.memo.borrow().get(sha_hex) {
            return *hit;
        }
        let hit = sha_hex.get(0..2).is_some_and(|prefix| {
            self.root
                .join("cas")
                .join(prefix)
                .join(format!("{sha_hex}.pdf"))
                .exists()
        });
        self.memo.borrow_mut().insert(sha_hex.to_string(), hit);
        hit
    }
}

impl dw_decide::Provenance for VaultProvenance {
    fn resolvable(&self, sources: &[SourceRef]) -> bool {
        !sources.is_empty() && sources.iter().all(|s| self.resolves(&s.sha256.to_string()))
    }
}

fn axis_from_name(name: &str) -> Option<dw_decide::Axis> {
    serde_json::from_value::<dw_decide::Axis>(serde_json::Value::String(name.into())).ok()
}

/// Per-part, per-axis accumulator while loading.
struct Acc {
    value: Option<f64>,
    refs: Vec<VerbatimRef>,
    all_values: Vec<f64>,
}

/// Load a pillar feed file (JSONL) into the evidence substrate.
///
/// # Errors
///
/// Returns a message when the file cannot be read.
#[allow(
    clippy::too_many_lines,
    reason = "one linear feed walk; splitting would hide the admission order"
)]
pub fn load_feed(path: &Path) -> Result<LoadedEvidence, String> {
    let bytes = std::fs::read(path).map_err(|e| format!("read {}: {e}", path.display()))?;
    let feed_sha256 = sha256_hex(&bytes);
    let text = String::from_utf8_lossy(&bytes);

    let mut per_part: BTreeMap<String, BTreeMap<String, Acc>> = BTreeMap::new();
    let mut vendors: BTreeMap<String, String> = BTreeMap::new();
    let mut stamped_parts = BTreeSet::new();
    let mut meta = FeedMeta {
        feed_sha256,
        rows_total: 0,
        rows_identity_verified: 0,
        rows_admitted: 0,
        rows_refused_implausible: 0,
        rows_refused_doc_identity: 0,
        cohort_parts: 0,
        parts_outside_cohort: 0,
        parts_with_conflicts: 0,
    };

    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        meta.rows_total += 1;
        let row: PillarRow = match serde_json::from_str(line) {
            Ok(r) => r,
            Err(_) => continue,
        };
        let Some(part) = row.part_identity.clone() else {
            continue;
        };
        let Some(mfr) = row.manufacturer.clone() else {
            continue;
        };
        if row.identity_status.as_deref() != Some("stamped") {
            continue;
        }
        if is_document_identity(&part, &mfr) {
            // Phantom OPN: a document-series identity, not a buyable part.
            meta.rows_refused_doc_identity += 1;
            continue;
        }
        if row.verify_verdict() != Some("verified") {
            continue;
        }
        meta.rows_identity_verified += 1;
        stamped_parts.insert(part.clone());
        vendors.insert(part.clone(), mfr);

        let Some(substrate) = SUBSTRATES.iter().find(|s| s.field == row.field) else {
            continue;
        };
        let Some(value) = row.value.as_f64().filter(|v| v.is_finite()) else {
            continue;
        };
        if !(substrate.min..=substrate.max).contains(&value) {
            // Measured garbage: refused, which is Unknown downstream —
            // never a false elimination.
            meta.rows_refused_implausible += 1;
            continue;
        }
        meta.rows_admitted += 1;

        let sha = row.document_sha256.clone().unwrap_or_default();
        let page = row.page_1based.unwrap_or(0);
        let vref = VerbatimRef {
            sha256: sha.clone(),
            page,
            verbatim: row.verbatim.clone().unwrap_or_default(),
            field: row.field.clone(),
            value,
        };
        let axes = per_part.entry(part).or_default();
        let acc = axes.entry(substrate.axis.to_string()).or_insert(Acc {
            value: None,
            refs: Vec::new(),
            all_values: Vec::new(),
        });
        acc.all_values.push(value);
        // Best case for the part: max admitted value. The refs must name the
        // claims that ACHIEVE the max — the receipt then quotes exactly the
        // evidence the verdict was judged on, never a superseded claim.
        let better = acc.value.is_none_or(|v| value > v);
        let tie = acc.value.is_some_and(|v| v.to_bits() == value.to_bits());
        if better {
            acc.value = Some(value);
            acc.refs.clear();
            acc.refs.push(vref);
        } else if tie && acc.refs.len() < 4 {
            acc.refs.push(vref);
        }
        // Best case for the part: max admitted value.
        if acc.value.is_none_or(|v| value > v) {
            acc.value = Some(value);
        }
    }

    // Build cohort + facts.
    let mut members = Vec::new();
    let mut facts = BTreeMap::new();
    let mut verbatim_index = BTreeMap::new();

    for (part, axes) in per_part {
        // MCU cohort marker: admitted memory or clock evidence.
        let is_mcu = MCU_MARKERS
            .iter()
            .any(|marker| axes.get(*marker).is_some_and(|acc| acc.value.is_some()));
        let Some(vendor) = vendors.get(&part) else {
            continue;
        };
        if !is_mcu {
            meta.parts_outside_cohort += 1;
            continue;
        }
        let candidate = Candidate {
            part_number: part.clone(),
            vendor: vendor.clone(),
        };
        let mut cf = CandidateFacts::default();
        let mut had_conflict = false;
        for (axis_name, acc) in axes {
            let Some(value) = acc.value else { continue };
            let distinct: BTreeSet<u64> = acc.all_values.iter().map(|v| v.to_bits()).collect();
            if distinct.len() > 1 {
                had_conflict = true;
            }
            let Some(axis) = axis_from_name(&axis_name) else {
                continue;
            };
            let mut sources = Vec::new();
            for r in &acc.refs {
                if let Some(sha) = parse_sha(&r.sha256) {
                    sources.push(SourceRef::page(sha, r.page));
                }
            }
            // A part whose only source cannot be constructed (bad sha shape)
            // still carries the value but empty sources — the provenance law
            // then makes it Unknown at judgement time. Honest either way.
            let evidence = Evidence::measured(value, true, sources);
            cf.axes.insert(axis, evidence);
            verbatim_index.insert((part.clone(), axis_name.clone()), acc.refs.clone());
        }
        if had_conflict {
            meta.parts_with_conflicts += 1;
        }
        members.push(candidate.clone());
        facts.insert(candidate, cf);
    }

    meta.cohort_parts = members.len();
    let cohort = Cohort::from_candidates(members);

    Ok(LoadedEvidence {
        cohort,
        facts,
        verbatim: verbatim_index,
        meta,
    })
}

/// A resolver that accepts everything — **tests only**, the same contract as
/// [`dw_decide::AlwaysResolvable`].
#[must_use]
pub fn always_resolvable() -> AlwaysResolvable {
    AlwaysResolvable
}
