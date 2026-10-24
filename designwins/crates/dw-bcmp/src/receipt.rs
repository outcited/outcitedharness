//! Durable batch runner: scenario feed → receipts, with checkpointing,
//! idempotent restart, a stop switch, and a release (feed-sha) guard.

use std::collections::BTreeMap;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use dw_decide::{evaluate, Axis};
use serde::{Deserialize, Serialize};

use crate::evidence::{load_feed, LoadedEvidence, VaultProvenance};
use crate::project::{project_brands, BrandOutcome};
use crate::scenario::{validate, Scenario, Validated};
use crate::{knife, sha256_hex};

/// Errors the runner can refuse on.
#[derive(Debug, thiserror::Error)]
pub enum RunError {
    /// The feed changed under an existing job — replay, don't silently rerun.
    #[error("replay_required: feed sha {existing} != {current}")]
    ReplayRequired {
        /// Feed sha recorded in the job state.
        existing: String,
        /// Feed sha observed now.
        current: String,
    },
    /// I/O or data error, with context.
    #[error("{0}")]
    Failed(String),
}

/// Runner options.
#[derive(Debug, Clone)]
pub struct RunOpts {
    /// Job directory (scenario files, receipts, state).
    pub job_dir: PathBuf,
    /// Pillar feed path.
    pub feed: PathBuf,
    /// Vault root (contains `cas/`).
    pub vault_root: PathBuf,
    /// Repo root for the engine git pin.
    pub repo_root: PathBuf,
    /// Max scenarios to evaluate this invocation (0 = unlimited).
    pub max_scenarios: usize,
    /// Max evidence refs/quotes per receipt entry.
    pub evidence_cap: usize,
}

/// Summary of one invocation.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct BatchOutcome {
    /// Scenarios read this run.
    pub scenarios_read: usize,
    /// Scenarios with an empty atom set (parked whole).
    pub scenarios_unscorable: usize,
    /// Atom sets evaluated this run (receipts written).
    pub receipts_written: usize,
    /// Atom sets skipped because a valid receipt already existed.
    pub receipts_skipped_existing: usize,
    /// Duplicate atom sets skipped (dedupe).
    pub duplicates_skipped: usize,
    /// Whether the run stopped early via the STOP file.
    pub stopped_early: bool,
    /// Error string, when the run refused (e.g. `replay_required`).
    pub error: Option<String>,
}

/// Durable job state (`job_dir/state.json`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JobState {
    /// Schema tag.
    pub schema: String,
    /// Feed pin at job creation.
    pub feed_sha256: String,
    /// Engine git sha at job creation (recorded, informational).
    pub engine_git: String,
    /// Knife spec version pin.
    pub knife_spec_version: String,
    /// Scenario ids seen (idempotence ledger).
    pub scenario_ids_seen: Vec<String>,
    /// Atom-set shas already receipted.
    pub atom_sets_done: Vec<String>,
    /// Counters: scenarios accepted (scorable).
    pub scenarios_accepted: usize,
    /// Counters: scenarios parked whole (nothing scorable).
    pub scenarios_parked: usize,
    /// Counters: atoms honored.
    pub atoms_honored: usize,
    /// Counters: atoms parked.
    pub atoms_parked: usize,
    /// Last write time (UTC, best-effort).
    pub updated_utc: String,
}

impl JobState {
    fn new(feed_sha256: String, engine_git: String) -> Self {
        Self {
            schema: "harness.bcmp-job-state.v1".into(),
            feed_sha256,
            engine_git,
            knife_spec_version: knife::KNIFE_SPEC_VERSION.to_string(),
            scenario_ids_seen: Vec::new(),
            atom_sets_done: Vec::new(),
            scenarios_accepted: 0,
            scenarios_parked: 0,
            atoms_honored: 0,
            atoms_parked: 0,
            updated_utc: now_utc(),
        }
    }
}

/// Receipt for one atom set (`job_dir/receipts`/<sha>.json).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Receipt {
    /// Schema tag.
    pub schema: String,
    /// Atom-set identity.
    pub atom_set_sha256: String,
    /// The scenario (question + proposals), verbatim.
    pub scenario: Scenario,
    /// Disposition of every proposal (honored or parked+code).
    pub dispositions: Vec<serde_json::Value>,
    /// Canonical atom set.
    pub canonical: Vec<crate::scenario::CanonicalAtom>,
    /// Pins: feed, engine, knife spec, provenance policy.
    pub pins: serde_json::Value,
    /// Cohort snapshot stats.
    pub feed_meta: crate::evidence::FeedMeta,
    /// Survivors after evaluation.
    pub survivors: Vec<String>,
    /// Brand projections.
    pub brands: BTreeMap<String, BrandOutcome>,
    /// Engine unknowns (`MissingAxis` entries, serialized).
    pub unknowns: Vec<serde_json::Value>,
    /// Parked-atom counts by code.
    pub parked_counts: BTreeMap<String, usize>,
}

fn now_utc() -> String {
    // Best-effort UTC stamp via the system clock. No chrono dependency; if
    // the command fails the stamp is empty and the mtime still carries time.
    std::process::Command::new("date")
        .args(["-u", "+%Y-%m-%dT%H:%M:%SZ"])
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default()
}

fn engine_git(repo_root: &Path) -> String {
    std::process::Command::new("git")
        .args(["rev-parse", "HEAD"])
        .current_dir(repo_root)
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map_or_else(
            || "unknown".into(),
            |o| String::from_utf8_lossy(&o.stdout).trim().to_string(),
        )
}

/// The engine crates' git TREE hashes — the true engine-code identity.
///
/// `HEAD` moves when unrelated lanes commit; the tree hash of `dw-decide` +
/// friends only moves when the engine itself changes. Receipts pin both;
/// replay comparison strips the informational `git` and requires the trees
/// to match (a changed tree is `replay_required`, exactly as a changed feed).
fn engine_trees(repo_root: &Path) -> BTreeMap<String, String> {
    let crates = [
        "dw-decide",
        "dw-knives",
        "dw-types",
        "dw-normalize",
        "dw-extract",
    ];
    let mut out = BTreeMap::new();
    for c in crates {
        let spec = format!("HEAD:designwins/crates/{c}");
        let hit = std::process::Command::new("git")
            .args(["rev-parse", &spec])
            .current_dir(repo_root)
            .output()
            .ok()
            .filter(|o| o.status.success())
            .map_or_else(
                || "unknown".into(),
                |o| String::from_utf8_lossy(&o.stdout).trim().to_string(),
            );
        out.insert(c.to_string(), hit);
    }
    out
}

fn atomic_write(path: &Path, body: &str) -> Result<(), RunError> {
    let tmp = path.with_extension("tmp");
    fs::write(&tmp, body).map_err(|e| RunError::Failed(format!("write {}: {e}", tmp.display())))?;
    fs::rename(&tmp, path).map_err(|e| RunError::Failed(format!("rename {}: {e}", path.display())))
}

fn scenario_files(job_dir: &Path) -> Vec<PathBuf> {
    let mut files: Vec<PathBuf> = fs::read_dir(job_dir.join("scenarios"))
        .map(|rd| {
            rd.filter_map(Result::ok)
                .map(|e| e.path())
                .filter(|p| p.extension().is_some_and(|e| e == "jsonl"))
                .collect()
        })
        .unwrap_or_default();
    files.sort();
    files
}

fn load_state(job_dir: &Path) -> Option<JobState> {
    let p = job_dir.join("state.json");
    let body = fs::read_to_string(p).ok()?;
    serde_json::from_str(&body).ok()
}

fn save_state(job_dir: &Path, state: &JobState) -> Result<(), RunError> {
    let mut st = state.clone();
    st.updated_utc = now_utc();
    let body = serde_json::to_string_pretty(&st)
        .map_err(|e| RunError::Failed(format!("serialize state: {e}")))?;
    atomic_write(&job_dir.join("state.json"), &body)
}

fn receipt_valid(path: &Path, want_sha: &str) -> bool {
    if let Ok(body) = fs::read_to_string(path) {
        if let Ok(r) = serde_json::from_str::<Receipt>(&body) {
            return r.atom_set_sha256 == want_sha;
        }
    }
    false
}

/// Run the batch. Returns the invocation summary; a refused run returns
/// `BatchOutcome` with `error` set (and `Err` only for setup failures that
/// produced no state at all).
///
/// # Errors
///
/// `Err` for unreadable feed / missing vault; refusals after state exists are
/// reported through `BatchOutcome.error`.
#[allow(
    clippy::too_many_lines,
    reason = "the durable loop: guards, dedupe, evaluate, checkpoint; one place so recovery reads top-to-bottom"
)]
pub fn run_batch(opts: &RunOpts) -> Result<BatchOutcome, RunError> {
    let mut outcome = BatchOutcome {
        scenarios_read: 0,
        scenarios_unscorable: 0,
        receipts_written: 0,
        receipts_skipped_existing: 0,
        duplicates_skipped: 0,
        stopped_early: false,
        error: None,
    };

    let loaded: LoadedEvidence = load_feed(&opts.feed).map_err(RunError::Failed)?;
    let provenance = VaultProvenance::open(&opts.vault_root).map_err(RunError::Failed)?;

    fs::create_dir_all(opts.job_dir.join("receipts"))
        .and_then(|()| fs::create_dir_all(opts.job_dir.join("scenarios")))
        .and_then(|()| fs::create_dir_all(opts.job_dir.join("logs")))
        .map_err(|e| RunError::Failed(format!("job dirs: {e}")))?;

    // Release guard: the job is pinned to one feed release.
    let mut state = match load_state(&opts.job_dir) {
        Some(st) => {
            if st.feed_sha256 != loaded.meta.feed_sha256 {
                outcome.error = Some(
                    RunError::ReplayRequired {
                        existing: st.feed_sha256.clone(),
                        current: loaded.meta.feed_sha256.clone(),
                    }
                    .to_string(),
                );
                return Ok(outcome);
            }
            st
        }
        None => JobState::new(loaded.meta.feed_sha256.clone(), engine_git(&opts.repo_root)),
    };

    let pins = serde_json::json!({
        "feed_sha256": loaded.meta.feed_sha256,
        "feed_path": opts.feed.display().to_string(),
        "engine": {
            // Informational: repo HEAD at run time (moves with unrelated
            // lanes). Replay gates on `engine_trees` + feed sha, not this.
            "git": state.engine_git,
            "engine_trees": engine_trees(&opts.repo_root),
            "evaluate": "dw_decide::evaluate (M4-owned, unmodified)",
        },
        "knife_spec_version": knife::KNIFE_SPEC_VERSION,
        "admission_policy_version": crate::evidence::ADMISSION_POLICY_VERSION,
        "knife_spec": knife::knife_spec_json(),
        "provenance_policy": {
            "policy": "vault-cas-pdf-exists",
            "vault_root": opts.vault_root.display().to_string(),
        },
        "run_utc": now_utc(),
    });

    let done: std::collections::BTreeSet<String> = state.atom_sets_done.iter().cloned().collect();
    let seen_ids: std::collections::BTreeSet<String> =
        state.scenario_ids_seen.iter().cloned().collect();

    let mut local_done = done;
    let mut local_seen = seen_ids;

    'outer: for file in scenario_files(&opts.job_dir) {
        let body = fs::read_to_string(&file)
            .map_err(|e| RunError::Failed(format!("read {}: {e}", file.display())))?;
        for line in body.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            // Stop switch.
            if opts.job_dir.join("STOP").exists() {
                outcome.stopped_early = true;
                break 'outer;
            }
            if opts.max_scenarios != 0 && outcome.scenarios_read >= opts.max_scenarios {
                break 'outer;
            }
            let scenario: Scenario = if let Ok(s) = serde_json::from_str(line) {
                s
            } else {
                // Unparseable row: park it whole in the log, keep going.
                let _ = writeln_parked(&opts.job_dir, line, "unparseable_scenario");
                continue;
            };
            outcome.scenarios_read += 1;
            if local_seen.contains(&scenario.scenario_id) {
                outcome.duplicates_skipped += 1;
                continue;
            }
            local_seen.insert(scenario.scenario_id.clone());

            let valid: Validated = validate(scenario);
            state.atoms_honored += valid.atoms.len();
            let parked: usize = valid.dispositions.len() - valid.atoms.len();
            state.atoms_parked += parked;

            if !valid.is_scorable() {
                state.scenarios_parked += 1;
                outcome.scenarios_unscorable += 1;
                let _ = writeln_parked(
                    &opts.job_dir,
                    &serde_json::to_string(&serde_json::json!({
                        "scenario_id": valid.scenario.scenario_id,
                        "question": valid.scenario.question,
                        "parked_counts": valid.parked_counts(),
                    }))
                    .unwrap_or_default(),
                    "no_scorable_atoms",
                );
                continue;
            }
            state.scenarios_accepted += 1;

            let sha = valid.atom_set_sha256.clone();
            let receipt_path = opts.job_dir.join("receipts").join(format!("{sha}.json"));
            if local_done.contains(&sha) || receipt_valid(&receipt_path, &sha) {
                outcome.receipts_skipped_existing += 1;
                local_done.insert(sha);
                continue;
            }

            let receipt =
                evaluate_and_build(&valid, &loaded, &provenance, &pins, opts.evidence_cap);
            let body = serde_json::to_string_pretty(&receipt)
                .map_err(|e| RunError::Failed(format!("serialize receipt: {e}")))?;
            atomic_write(&receipt_path, &body)?;
            outcome.receipts_written += 1;
            local_done.insert(sha);
            state.atom_sets_done = local_done.iter().cloned().collect();
            state.scenario_ids_seen = local_seen.iter().cloned().collect();
            save_state(&opts.job_dir, &state)?;
        }
    }

    state.atom_sets_done = local_done.iter().cloned().collect();
    state.scenario_ids_seen = local_seen.iter().cloned().collect();
    save_state(&opts.job_dir, &state)?;
    let _ = fs::write(
        opts.job_dir.join("logs").join("last-run.json"),
        serde_json::to_string_pretty(&outcome).unwrap_or_default(),
    );
    Ok(outcome)
}

fn writeln_parked(job_dir: &Path, line: &str, code: &str) -> std::io::Result<()> {
    use std::io::BufWriter;
    let path = job_dir.join("parked.log.jsonl");
    let f = fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)?;
    let mut w = BufWriter::new(f);
    writeln!(w, "{{\"code\":\"{code}\",\"row\":{line}}}")
}

fn evaluate_and_build(
    valid: &Validated,
    loaded: &LoadedEvidence,
    provenance: &VaultProvenance,
    pins: &serde_json::Value,
    cap: usize,
) -> Receipt {
    let eval = evaluate(&loaded.cohort, &valid.atoms, &loaded.facts, provenance);
    let atom_axes: Vec<Axis> = valid
        .atoms
        .iter()
        .filter_map(|a| match a.knife {
            dw_decide::KnifeId::Axis(ax) => Some(ax),
            dw_decide::KnifeId::Category(_) => None,
        })
        .collect();
    let brands = project_brands(&eval, &atom_axes, &loaded.verbatim, cap);
    let dispositions = valid
        .dispositions
        .iter()
        .map(|d| serde_json::to_value(d).unwrap_or(serde_json::Value::Null))
        .collect();
    let unknowns = eval
        .unknowns
        .iter()
        .map(|u| serde_json::to_value(u).unwrap_or(serde_json::Value::Null))
        .collect();
    Receipt {
        schema: "harness.bcmp-receipt.v1".into(),
        atom_set_sha256: valid.atom_set_sha256.clone(),
        scenario: valid.scenario.clone(),
        dispositions,
        canonical: valid.canonical.clone(),
        pins: pins.clone(),
        feed_meta: loaded.meta.clone(),
        survivors: eval
            .cohort
            .members
            .iter()
            .map(|c| c.part_number.clone())
            .collect(),
        brands,
        unknowns,
        parked_counts: valid.parked_counts(),
    }
}

/// The scenario dedupe key for generators (same law as the runner).
#[must_use]
pub fn atom_set_key(canonical_json: &str) -> String {
    sha256_hex(canonical_json.as_bytes())
}
