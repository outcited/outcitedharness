//! # dw-controller — the factory's durable operational state
//!
//! Port of `harness/electronics/factory_control.py`: SQLite-backed source
//! observations with stability windows, chunk registration and leasing, and
//! the immutable `factory_events` ledger. The schema is kept **verbatim** so
//! the Python factory and this port can operate one `factory.sqlite3`.
//!
//! Phase 1 ports the unattended-run lifecycle: [`FactoryState::discover_pdfs`],
//! [`FactoryState::register_queue`], claim/renew/complete/fail,
//! [`FactoryState::recover_expired`], [`FactoryState::status`] and the queue
//! summaries. The snapshot-sealing and frontier-run methods stay Python-side
//! (their tables ship in the schema now, so the db shape is already shared).
//!
//! Determinism: every mutating method takes an explicit `now` (ISO-8601 UTC),
//! mirroring the reference's `now: datetime | None`. Lease tokens are random
//! by design (`secrets.token_urlsafe`) and observation ids fold device+inode,
//! so the gold battery normalises both — the semantics (ordering, stability
//! gating, dedupe, retry accounting) are the port's contract.
//!
//! No models are called; `forbid(unsafe_code)`.

#![forbid(unsafe_code)]
#![deny(missing_docs)]
// Stat identities are positive u64/i64 stored in SQLite's i64 columns and
// the parser indexes fixed-width timestamp slices — both bounded by
// construction; the reference's own semantics are width-checked at parse.
#![allow(
    clippy::cast_possible_wrap,
    clippy::cast_sign_loss,
    clippy::cast_possible_truncation,
    clippy::indexing_slicing,
    clippy::manual_let_else,
    clippy::single_match_else,
    reason = "stat identities into i64 columns; timestamp slices are fixed-width"
)]

use std::collections::BTreeMap;
use std::os::unix::fs::{MetadataExt as _, PermissionsExt as _};

fn set_mode_700(path: &Path) -> Result<(), FactoryError> {
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o700)).map_err(|source| {
        FactoryError::Io {
            context: format!("chmod 700 {}", path.display()),
            source,
        }
    })
}

fn set_mode_600(path: &Path) -> Result<(), FactoryError> {
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600)).map_err(|source| {
        FactoryError::Io {
            context: format!("chmod 600 {}", path.display()),
            source,
        }
    })
}

/// `st_mtime_ns` — std exposes sec + nsec separately.
fn mtime_ns(meta: &std::fs::Metadata) -> i64 {
    meta.mtime()
        .saturating_mul(1_000_000_000)
        .saturating_add(meta.mtime_nsec())
}
use std::path::{Path, PathBuf};

use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use serde::Serialize;
use serde_json::{json, Map, Value};

use dw_extract::canonical::canonical_json;
use dw_extract::sha256::sha256_hex;

/// The schema, verbatim from the reference so both engines share one file.
const SCHEMA: &str = r"
CREATE TABLE IF NOT EXISTS factory_source_observations (
    observation_id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    device INTEGER NOT NULL,
    inode INTEGER NOT NULL,
    byte_size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    first_seen_at TEXT NOT NULL,
    stable_after_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    status TEXT NOT NULL
        CHECK (status IN (
            'observed', 'ready', 'duplicate',
            'quarantined', 'superseded'
        )),
    sha256 TEXT,
    canonical_observation_id TEXT,
    reason TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (
        source_path, device, inode, byte_size, mtime_ns
    ),
    FOREIGN KEY (canonical_observation_id)
        REFERENCES factory_source_observations(observation_id)
);

CREATE INDEX IF NOT EXISTS idx_factory_sources_status
    ON factory_source_observations(status, stable_after_at);
CREATE INDEX IF NOT EXISTS idx_factory_sources_path
    ON factory_source_observations(source_path, first_seen_at);
CREATE INDEX IF NOT EXISTS idx_factory_sources_sha
    ON factory_source_observations(sha256, status);

CREATE TABLE IF NOT EXISTS factory_source_batches (
    observation_id TEXT PRIMARY KEY,
    cohort_id TEXT NOT NULL,
    snapshot_path TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (observation_id)
        REFERENCES factory_source_observations(observation_id)
);

CREATE INDEX IF NOT EXISTS idx_factory_source_batches_cohort
    ON factory_source_batches(cohort_id, observation_id);

CREATE TABLE IF NOT EXISTS factory_chunks (
    chunk_id TEXT PRIMARY KEY,
    queue_path TEXT NOT NULL,
    queue_sha256 TEXT NOT NULL,
    queue_evidence_sha256 TEXT NOT NULL,
    offset INTEGER NOT NULL,
    item_count INTEGER NOT NULL,
    output_directory TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL
        CHECK (status IN (
            'queued', 'leased', 'completed', 'failed'
        )),
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    available_at TEXT NOT NULL,
    lease_node TEXT,
    lease_token TEXT,
    lease_expires_at TEXT,
    output_manifest_sha256 TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (queue_sha256, offset, item_count)
);

CREATE INDEX IF NOT EXISTS idx_factory_chunks_queue
    ON factory_chunks(status, available_at, offset);
CREATE INDEX IF NOT EXISTS idx_factory_chunks_lease
    ON factory_chunks(status, lease_expires_at);

CREATE TABLE IF NOT EXISTS factory_frontier_runs (
    run_id TEXT PRIMARY KEY,
    prepared_bundle TEXT NOT NULL,
    prepared_evidence_sha256 TEXT NOT NULL,
    submission_state TEXT NOT NULL,
    lifecycle_root TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL
        CHECK (status IN (
            'submitted', 'processing', 'ended',
            'retrieved', 'reconciled', 'verified',
            'finalized', 'failed'
        )),
    request_count INTEGER NOT NULL,
    estimated_maximum_usd REAL NOT NULL,
    latest_status_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS factory_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_factory_events_subject
    ON factory_events(subject_id, created_at);

CREATE TRIGGER IF NOT EXISTS factory_events_no_update
BEFORE UPDATE ON factory_events
BEGIN
    SELECT RAISE(ABORT, 'factory events are immutable');
END;

CREATE TRIGGER IF NOT EXISTS factory_events_no_delete
BEFORE DELETE ON factory_events
BEGIN
    SELECT RAISE(ABORT, 'factory events are immutable');
END;
";

/// `FactoryStateError` / `ValueError` from the reference; messages are
/// byte-identical so the battery can compare them directly.
#[derive(Debug, thiserror::Error)]
pub enum FactoryError {
    /// A validation failure (`ValueError` in the reference).
    #[error("{0}")]
    Value(String),
    /// A durable-state inconsistency (`FactoryStateError`).
    #[error("{0}")]
    State(String),
    /// A strict path resolution failed (`FileNotFoundError`).
    #[error("{0}")]
    FileNotFound(String),
    /// A SQLite failure; carries the driver message.
    #[error("sqlite: {0}")]
    Sqlite(#[from] rusqlite::Error),
    /// An I/O failure with context.
    #[error("{context}: {source}")]
    Io {
        /// What was being attempted.
        context: String,
        /// The OS error.
        source: std::io::Error,
    },
}

impl FactoryError {
    /// The exception class the reference would have raised, for batteries
    /// that compare `"{type}: {msg}"` strings.
    #[must_use]
    pub fn kind(&self) -> &'static str {
        match self {
            FactoryError::Value(_) => "ValueError",
            FactoryError::State(_) => "FactoryStateError",
            FactoryError::FileNotFound(_) => "FileNotFoundError",
            FactoryError::Sqlite(_) => "SqliteError",
            FactoryError::Io { .. } => "OSError",
        }
    }
}

/// A claimed chunk lease (`ChunkLease` in the reference).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChunkLease {
    /// The leased chunk.
    pub chunk_id: String,
    /// The claiming node's canonical name.
    pub node: String,
    /// The attempt number (1-based).
    pub attempt: i64,
    /// The opaque lease bearer token.
    pub lease_token: String,
    /// When the lease expires (ISO-8601 UTC, microsecond precision).
    pub lease_expires_at: String,
    /// The queue file the chunk belongs to.
    pub queue_path: PathBuf,
    /// SHA-256 of the queue file bytes.
    pub queue_sha256: String,
    /// Item offset within the queue.
    pub offset: i64,
    /// Items in this chunk.
    pub item_count: i64,
    /// Where the worker must write its output.
    pub output_directory: PathBuf,
}

/// `DiscoveryReport` — one `discover_pdfs` pass.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct DiscoveryReport {
    /// Candidate PDFs walked (symlinks excluded).
    pub discovered_paths: usize,
    /// First-time observations inserted.
    pub new_observations: usize,
    /// Observations not yet past their stability window.
    pub waiting_for_stability: usize,
    /// Observations hashed and admitted as ready.
    pub ready: usize,
    /// Ready observations whose content matched an earlier ready source.
    pub duplicates: usize,
    /// Observations quarantined (hash/inspect failure).
    pub quarantined: usize,
    /// Re-seen unchanged observations.
    pub unchanged: usize,
    /// Symlinked or unstatable paths skipped.
    pub unsafe_paths: usize,
}

// ── timestamps ───────────────────────────────────────────────────────────

/// Microseconds since the Unix epoch, UTC. The reference stores
/// `datetime.isoformat(timespec="microseconds")` — a fixed-width, sortable
/// string — so every comparison in SQL is lexicographic and stays exact.
pub mod ts {
    use std::time::{SystemTime, UNIX_EPOCH};

    /// Days from 1970-01-01 for a civil date (Hinnant's algorithm).
    fn days_from_civil(y: i64, m: u32, d: u32) -> i64 {
        let y = if m <= 2 { y - 1 } else { y };
        let era = if y >= 0 { y } else { y - 399 } / 400;
        let yoe = y - era * 400;
        let mp = (i64::from(m) + 9) % 12;
        let doy = (153 * mp + 2) / 5 + i64::from(d) - 1;
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
        era * 146_097 + doe - 719_468
    }

    /// Civil date from days since epoch (Hinnant's algorithm).
    fn civil_from_days(z: i64) -> (i64, u32, u32) {
        let z = z + 719_468;
        let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
        let doe = z - era * 146_097;
        let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
        let y = yoe + era * 400;
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        let mp = (5 * doy + 2) / 153;
        let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
        let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
        (if m <= 2 { y + 1 } else { y }, m, d)
    }

    /// Format epoch micros exactly like Python's
    /// `datetime.isoformat(timespec="microseconds")` in UTC.
    #[must_use]
    pub fn format(micros: i64) -> String {
        let days = micros.div_euclid(86_400_000_000);
        let time = micros.rem_euclid(86_400_000_000);
        let (y, m, d) = civil_from_days(days);
        let (hh, rest) = (time / 3_600_000_000, time % 3_600_000_000);
        let (mm, rest) = (rest / 60_000_000, rest % 60_000_000);
        let (ss, us) = (rest / 1_000_000, rest % 1_000_000);
        format!("{y:04}-{m:02}-{d:02}T{hh:02}:{mm:02}:{ss:02}.{us:06}+00:00")
    }

    /// Parse the shapes the battery and the reference emit:
    /// `YYYY-MM-DDTHH:MM:SS[.ffffff][Z|+00:00|-00:00]`. Naive values are
    /// rejected, matching `_parse_timestamp`.
    ///
    /// # Errors
    ///
    /// A human-readable reason for malformed or timezone-naive input.
    pub fn parse(text: &str) -> Result<i64, String> {
        let reject = |msg: &str| Err(format!("{msg}: {text}"));
        let bytes = text.as_bytes();
        if bytes.len() < 20 || bytes[4] != b'-' || bytes[7] != b'-' {
            return reject("malformed timestamp");
        }
        let sep = bytes[10];
        if sep != b'T' && sep != b' ' {
            return reject("malformed timestamp");
        }
        let y: i64 = text[0..4]
            .parse()
            .map_err(|_| format!("bad year: {text}"))?;
        let m: u32 = text[5..7]
            .parse()
            .map_err(|_| format!("bad month: {text}"))?;
        let d: u32 = text[8..10]
            .parse()
            .map_err(|_| format!("bad day: {text}"))?;
        let hh: i64 = text[11..13]
            .parse()
            .map_err(|_| format!("bad hour: {text}"))?;
        let mi: i64 = text[14..16]
            .parse()
            .map_err(|_| format!("bad minute: {text}"))?;
        let ss: i64 = text[17..19]
            .parse()
            .map_err(|_| format!("bad second: {text}"))?;
        let mut rest = &text[19..];
        let mut micros: i64 = 0;
        if rest.starts_with('.') {
            let fraction_end = rest[1..]
                .find(|c: char| !c.is_ascii_digit())
                .map_or(rest.len(), |i| i + 1);
            let digits = &rest[1..fraction_end];
            if digits.is_empty() {
                return reject("empty fractional seconds");
            }
            let mut padded = digits.to_string();
            padded.truncate(6);
            while padded.len() < 6 {
                padded.push('0');
            }
            micros = padded
                .parse()
                .map_err(|_| format!("bad fraction: {text}"))?;
            rest = &rest[fraction_end..];
        }
        match rest {
            "Z" | "+00:00" | "-00:00" => {}
            _ => return reject("timestamp must be timezone-aware UTC"),
        }
        if !(1..=12).contains(&m) || !(1..=31).contains(&d) {
            return reject("date out of range");
        }
        if !(0..=23).contains(&hh) || !(0..=59).contains(&mi) || !(0..=59).contains(&ss) {
            return reject("time out of range");
        }
        let days = days_from_civil(y, m, d);
        Ok(days * 86_400_000_000 + hh * 3_600_000_000 + mi * 60_000_000 + ss * 1_000_000 + micros)
    }

    /// Wall-clock now, for the reference's `now or _utcnow()` fallback.
    #[must_use]
    pub fn now_micros() -> i64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| i64::try_from(d.as_micros()).unwrap_or(i64::MAX))
            .unwrap_or_default()
    }
}

/// `now or _utcnow()` — resolve the caller's timestamp or wall clock.
fn resolve_now(now: Option<&str>) -> Result<i64, FactoryError> {
    match now {
        Some(text) => ts::parse(text).map_err(FactoryError::Value),
        None => Ok(ts::now_micros()),
    }
}

fn fmt(micros: i64) -> String {
    ts::format(micros)
}

fn plus_seconds(micros: i64, seconds: i64) -> i64 {
    micros + seconds * 1_000_000
}

// ── the state machine ────────────────────────────────────────────────────

/// SQLite-backed factory state (`ElectronicsFactoryState`).
pub struct FactoryState {
    /// The state root (0700).
    pub root: PathBuf,
    /// The database file (0600, WAL).
    pub database: PathBuf,
}

impl FactoryState {
    /// Open (or create) state under `root`, enforcing the reference's
    /// ownership and permission posture.
    ///
    /// # Errors
    ///
    /// [`FactoryError::State`] when the root/database is not a real,
    /// self-owned regular-file tree; [`FactoryError::Sqlite`] on init.
    pub fn open(root: &Path) -> Result<Self, FactoryError> {
        let root = std::fs::canonicalize(root).unwrap_or_else(|_| root.to_path_buf());
        std::fs::create_dir_all(&root).map_err(|source| FactoryError::Io {
            context: format!("mkdir {}", root.display()),
            source,
        })?;
        set_mode_700(&root)?;
        let root_meta = root.symlink_metadata().map_err(|source| FactoryError::Io {
            context: format!("stat {}", root.display()),
            source,
        })?;
        if !root_meta.is_dir() {
            return Err(FactoryError::State(
                "factory state root must be a real directory".into(),
            ));
        }
        let database = root.join("factory.sqlite3");
        if database
            .symlink_metadata()
            .is_ok_and(|m| m.file_type().is_symlink())
        {
            return Err(FactoryError::State(
                "factory database cannot be a symlink".into(),
            ));
        }
        if !database.exists() {
            std::fs::OpenOptions::new()
                .read(true)
                .write(true)
                .create_new(true)
                .open(&database)
                .map_err(|source| FactoryError::Io {
                    context: format!("create {}", database.display()),
                    source,
                })?;
        }
        let info = database
            .symlink_metadata()
            .map_err(|source| FactoryError::Io {
                context: format!("stat {}", database.display()),
                source,
            })?;
        let uid = libc_geteuid();
        if !info.is_file() || info.uid() != uid {
            return Err(FactoryError::State(
                "factory database must be an owned regular file".into(),
            ));
        }
        set_mode_600(&database)?;
        let state = Self { root, database };
        state.initialize()?;
        Ok(state)
    }

    /// A connection with the reference's pragmas.
    ///
    /// # Errors
    ///
    /// [`FactoryError::Sqlite`] on failure.
    pub fn connect(&self) -> Result<Connection, FactoryError> {
        let connection = Connection::open(&self.database)?;
        connection.busy_timeout(std::time::Duration::from_secs(10))?;
        connection.pragma_update(None, "foreign_keys", "ON")?;
        connection.pragma_update(None, "journal_mode", "WAL")?;
        connection.pragma_update(None, "synchronous", "FULL")?;
        Ok(connection)
    }

    fn initialize(&self) -> Result<(), FactoryError> {
        let connection = self.connect()?;
        connection.execute_batch(SCHEMA)?;
        for sidecar in [
            self.database.with_extension("sqlite3-wal"),
            self.database.with_extension("sqlite3-shm"),
        ] {
            if sidecar.exists() {
                let _ = std::fs::set_permissions(&sidecar, std::fs::Permissions::from_mode(0o600));
            }
        }
        Ok(())
    }

    // ── discovery ────────────────────────────────────────────────────────

    /// `discover_pdfs` — observe candidate PDFs under `roots`, gate new
    /// sources behind a stability window, then hash eligible sources into
    /// ready / duplicate / quarantined states.
    ///
    /// # Errors
    ///
    /// [`FactoryError::Value`] for out-of-range `stability_seconds`;
    /// [`FactoryError::State`] for unusable roots; [`FactoryError::Sqlite`].
    #[allow(clippy::too_many_lines, reason = "one transaction, reference-shaped")]
    pub fn discover_pdfs(
        &self,
        roots: &[PathBuf],
        stability_seconds: i64,
        now: Option<&str>,
    ) -> Result<DiscoveryReport, FactoryError> {
        if !(0..=86_400).contains(&stability_seconds) {
            return Err(FactoryError::Value(
                "stability_seconds must be within 0..86400".into(),
            ));
        }
        let observed_micros = resolve_now(now)?;
        let observed_at = fmt(observed_micros);
        let mut candidates: Vec<(PathBuf, FileIdentity)> = Vec::new();
        let mut unsafe_paths = 0usize;
        for raw_root in roots {
            // Python: `resolve(strict=True)` first (missing roots raise
            // FileNotFoundError with the partially-resolved path), then the
            // symlink/dir guard on the resolved value.
            let meta = raw_root.symlink_metadata().map_err(|source| {
                if source.kind() == std::io::ErrorKind::NotFound {
                    let partial = raw_root.parent().and_then(|parent| {
                        std::fs::canonicalize(parent)
                            .ok()
                            .map(|resolved| resolved.join(raw_root.file_name().unwrap_or_default()))
                    });
                    let partial = partial.unwrap_or_else(|| raw_root.clone());
                    FactoryError::FileNotFound(format!(
                        "[Errno 2] No such file or directory: '{}'",
                        partial.display()
                    ))
                } else {
                    FactoryError::Io {
                        context: format!("stat {}", raw_root.display()),
                        source,
                    }
                }
            })?;
            if meta.file_type().is_symlink() || !meta.is_dir() {
                return Err(FactoryError::State(format!(
                    "download root must be a real directory: {}",
                    raw_root.display()
                )));
            }
            let (mut walked, unsafe_walked) = walk_pdfs(raw_root);
            candidates.append(&mut walked);
            unsafe_paths += unsafe_walked;
        }

        let mut new_observations = 0usize;
        let mut unchanged = 0usize;
        let mut waiting_for_stability = 0usize;
        let mut eligible: Vec<EligibleSource> = Vec::new();

        {
            let mut connection = self.connect()?;
            let transaction =
                connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
            for (path, identity) in &candidates {
                let observation_id = observation_id(path, identity);
                let status: Option<String> = transaction
                    .query_row(
                        "SELECT status FROM factory_source_observations WHERE observation_id = ?1",
                        params![observation_id],
                        |row| row.get::<_, String>(0),
                    )
                    .optional()?;
                match status {
                    None => {
                        transaction.execute(
                            "UPDATE factory_source_observations
                             SET status = 'superseded', updated_at = ?1
                             WHERE source_path = ?2 AND status = 'observed'",
                            params![observed_at, path.display().to_string()],
                        )?;
                        let stable_after = fmt(plus_seconds(observed_micros, stability_seconds));
                        transaction.execute(
                            "INSERT INTO factory_source_observations (
                                 observation_id, source_path, device, inode,
                                 byte_size, mtime_ns, first_seen_at,
                                 stable_after_at, last_seen_at, status,
                                 updated_at
                             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?7,
                                       'observed', ?7)",
                            params![
                                observation_id,
                                path.display().to_string(),
                                identity.0 as i64,
                                identity.1 as i64,
                                identity.2 as i64,
                                identity.3,
                                observed_at,
                                stable_after,
                            ],
                        )?;
                        append_event(
                            &transaction,
                            "source_observed",
                            &observation_id,
                            &json!({
                                "path": path.display().to_string(),
                                "byte_size": identity.2,
                                "mtime_ns": identity.3,
                                "stable_after_at": stable_after,
                            }),
                            &observed_at,
                        )?;
                        new_observations += 1;
                        if observed_micros >= ts::parse(&stable_after).unwrap_or_default() {
                            eligible.push((observation_id, path.clone(), *identity));
                        } else {
                            waiting_for_stability += 1;
                        }
                    }
                    Some(current_status) => {
                        let stable_after: String = transaction.query_row(
                            "SELECT stable_after_at FROM factory_source_observations WHERE observation_id = ?1",
                            params![observation_id],
                            |row| row.get(0),
                        )?;
                        transaction.execute(
                            "UPDATE factory_source_observations
                             SET last_seen_at = ?1, updated_at = ?1
                             WHERE observation_id = ?2",
                            params![observed_at, observation_id],
                        )?;
                        unchanged += 1;
                        if current_status == "observed"
                            && observed_micros >= ts::parse(&stable_after).unwrap_or_default()
                        {
                            eligible.push((observation_id, path.clone(), *identity));
                        } else if current_status == "observed" {
                            waiting_for_stability += 1;
                        }
                    }
                }
            }
            transaction.commit()?;
        }

        let mut ready = 0usize;
        let mut duplicates = 0usize;
        let mut quarantined = 0usize;
        let finalized_at = observed_at.clone();
        for (observation_id, path, identity) in eligible {
            let inspected = inspect_pdf(&path, identity);
            let (digest, error) = match inspected {
                Ok(digest) => (Some(digest), None),
                Err(message) => (None, Some(message)),
            };
            let mut connection = self.connect()?;
            let transaction =
                connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
            let status: Option<String> = transaction
                .query_row(
                    "SELECT status FROM factory_source_observations
                     WHERE observation_id = ?1",
                    params![observation_id],
                    |row| row.get(0),
                )
                .optional()?;
            if status.as_deref() != Some("observed") {
                continue;
            }
            let (status, canonical_id, counter);
            match &digest {
                None => {
                    status = "quarantined".to_string();
                    canonical_id = None;
                    quarantined += 1;
                    counter = "quarantined";
                }
                Some(sha) => {
                    let duplicate: Option<String> = transaction
                        .query_row(
                            "SELECT observation_id FROM factory_source_observations
                             WHERE sha256 = ?1 AND status = 'ready'
                             ORDER BY first_seen_at, observation_id LIMIT 1",
                            params![sha],
                            |row| row.get(0),
                        )
                        .optional()?;
                    if duplicate.is_some() {
                        duplicates += 1;
                        counter = "duplicates";
                    } else {
                        ready += 1;
                        counter = "ready";
                    }
                    status = if duplicate.is_some() {
                        "duplicate".to_string()
                    } else {
                        "ready".to_string()
                    };
                    canonical_id = duplicate;
                }
            }
            let _ = counter;
            transaction.execute(
                "UPDATE factory_source_observations
                 SET status = ?1, sha256 = ?2, canonical_observation_id = ?3,
                     reason = ?4, updated_at = ?5
                 WHERE observation_id = ?6",
                params![
                    status,
                    digest,
                    canonical_id,
                    error,
                    finalized_at,
                    observation_id,
                ],
            )?;
            append_event(
                &transaction,
                &format!("source_{status}"),
                &observation_id,
                &json!({
                    "path": path.display().to_string(),
                    "sha256": digest,
                    "canonical_observation_id": canonical_id,
                    "reason": error,
                }),
                &finalized_at,
            )?;
            transaction.commit()?;
        }

        Ok(DiscoveryReport {
            discovered_paths: candidates.len(),
            new_observations,
            waiting_for_stability,
            ready,
            duplicates,
            quarantined,
            unchanged,
            unsafe_paths,
        })
    }

    // ── queue registration ───────────────────────────────────────────────

    /// `register_queue` — chunk a structural work queue and register each
    /// chunk idempotently.
    ///
    /// # Errors
    ///
    /// Validation errors, queue-shape errors, or [`FactoryError::Sqlite`].
    #[allow(clippy::too_many_lines, reason = "one transaction, reference-shaped")]
    pub fn register_queue(
        &self,
        queue_path: &Path,
        output_root: &Path,
        chunk_size: i64,
        max_attempts: i64,
        start_offset: i64,
        now: Option<&str>,
    ) -> Result<Vec<String>, FactoryError> {
        if !(1..=1000).contains(&chunk_size) {
            return Err(FactoryError::Value(
                "chunk_size must be within 1..1000".into(),
            ));
        }
        if !(1..=20).contains(&max_attempts) {
            return Err(FactoryError::Value(
                "max_attempts must be within 1..20".into(),
            ));
        }
        let (queue, queue_sha) = load_queue(queue_path)?;
        let resolved_queue =
            std::fs::canonicalize(queue_path).map_err(|source| FactoryError::Io {
                context: format!("resolve {}", queue_path.display()),
                source,
            })?;
        let resolved_output = if output_root
            .symlink_metadata()
            .is_ok_and(|m| m.file_type().is_symlink())
        {
            return Err(FactoryError::State(
                "chunk output root cannot be a symlink".into(),
            ));
        } else {
            std::fs::canonicalize(output_root).unwrap_or_else(|_| output_root.to_path_buf())
        };
        let work = queue
            .get("work")
            .and_then(Value::as_array)
            .map_or(0usize, Vec::len);
        let total = work as i64;
        if !(0..total).contains(&start_offset) {
            return Err(FactoryError::Value(
                "start_offset must select an item in the queue".into(),
            ));
        }
        let created_micros = resolve_now(now)?;
        let created_at = fmt(created_micros);
        let mut registered: Vec<String> = Vec::new();
        let mut connection = self.connect()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let mut offset = start_offset;
        while offset < total {
            let count = chunk_size.min(total - offset);
            let identity = canonical_json(&json!({
                "queue_sha256": queue_sha,
                "offset": offset,
                "item_count": count,
            }))
            .map_err(|e| FactoryError::State(e.to_string()))?;
            let chunk_id = format!(
                "chunk-{}-{:06}-{:04}-{}",
                &queue_sha[..12],
                offset,
                count,
                &sha256_hex(&identity)[..12]
            );
            let output = resolved_output.join(&chunk_id);
            let existing: Option<ChunkRow> = transaction
                .query_row(
                    "SELECT chunk_id, queue_sha256, offset, item_count,
                            output_directory
                     FROM factory_chunks WHERE chunk_id = ?1",
                    params![chunk_id],
                    |row| {
                        Ok((
                            row.get(0)?,
                            row.get(1)?,
                            row.get(2)?,
                            row.get(3)?,
                            row.get(4)?,
                        ))
                    },
                )
                .optional()?;
            if let Some((_, q_sha, q_off, q_count, q_output)) = existing {
                if q_sha != queue_sha
                    || q_off != offset
                    || q_count != count
                    || q_output != output.display().to_string()
                {
                    return Err(FactoryError::State(format!(
                        "chunk identity conflict: {chunk_id}"
                    )));
                }
                registered.push(chunk_id);
                offset += chunk_size;
                continue;
            }
            transaction.execute(
                "INSERT INTO factory_chunks (
                     chunk_id, queue_path, queue_sha256,
                     queue_evidence_sha256, offset, item_count,
                     output_directory, status, max_attempts,
                     available_at, created_at, updated_at
                 ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, 'queued', ?8, ?9, ?9, ?9)",
                params![
                    chunk_id,
                    resolved_queue.display().to_string(),
                    queue_sha,
                    queue
                        .get("evidence_sha256")
                        .and_then(Value::as_str)
                        .unwrap_or_default(),
                    offset,
                    count,
                    output.display().to_string(),
                    max_attempts,
                    created_at,
                ],
            )?;
            append_event(
                &transaction,
                "chunk_registered",
                &chunk_id,
                &json!({
                    "queue_sha256": queue_sha,
                    "offset": offset,
                    "item_count": count,
                    "output_directory": output.display().to_string(),
                }),
                &created_at,
            )?;
            registered.push(chunk_id);
            offset += chunk_size;
        }
        transaction.commit()?;
        Ok(registered)
    }

    // ── the lease lifecycle ──────────────────────────────────────────────

    /// `recover_expired` — reclaim leases whose expiry passed.
    ///
    /// # Errors
    ///
    /// [`FactoryError::Sqlite`] on failure.
    pub fn recover_expired(&self, now: Option<&str>) -> Result<Vec<String>, FactoryError> {
        let recovered_at = fmt(resolve_now(now)?);
        let mut connection = self.connect()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let recovered = recover_expired_in_transaction(&transaction, &recovered_at)?;
        transaction.commit()?;
        Ok(recovered)
    }

    /// `claim_chunk` — hand the oldest available chunk to `node`.
    ///
    /// # Errors
    ///
    /// Validation errors or [`FactoryError::Sqlite`].
    pub fn claim_chunk(
        &self,
        node: &str,
        lease_seconds: i64,
        now: Option<&str>,
    ) -> Result<Option<ChunkLease>, FactoryError> {
        if node.is_empty() || node != node.trim() {
            return Err(FactoryError::Value(
                "node must be a non-empty canonical name".into(),
            ));
        }
        if !(30..=86_400).contains(&lease_seconds) {
            return Err(FactoryError::Value(
                "lease_seconds must be within 30..86400".into(),
            ));
        }
        let claimed_micros = resolve_now(now)?;
        let claimed_at = fmt(claimed_micros);
        let mut connection = self.connect()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        recover_expired_in_transaction(&transaction, &claimed_at)?;
        let row = transaction
            .query_row(
                "SELECT chunk_id, queue_path, queue_sha256, offset, item_count,
                        output_directory, attempt
                 FROM factory_chunks
                 WHERE status = 'queued' AND available_at <= ?1
                     AND attempt < max_attempts
                 ORDER BY created_at, offset, chunk_id
                 LIMIT 1",
                params![claimed_at],
                |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                        row.get::<_, i64>(3)?,
                        row.get::<_, i64>(4)?,
                        row.get::<_, String>(5)?,
                        row.get::<_, i64>(6)?,
                    ))
                },
            )
            .optional()?;
        let Some((
            chunk_id,
            queue_path,
            queue_sha256,
            offset,
            item_count,
            output_directory,
            attempt,
        )) = row
        else {
            transaction.commit()?;
            return Ok(None);
        };
        let token = token_urlsafe_32();
        let attempt = attempt + 1;
        let expires = fmt(plus_seconds(claimed_micros, lease_seconds));
        transaction.execute(
            "UPDATE factory_chunks
             SET status = 'leased', attempt = ?1, lease_node = ?2,
                 lease_token = ?3, lease_expires_at = ?4, error = NULL,
                 updated_at = ?5
             WHERE chunk_id = ?6 AND status = 'queued'",
            params![attempt, node, token, expires, claimed_at, chunk_id],
        )?;
        append_event(
            &transaction,
            "chunk_claimed",
            &chunk_id,
            &json!({
                "node": node,
                "attempt": attempt,
                "lease_expires_at": expires,
            }),
            &claimed_at,
        )?;
        transaction.commit()?;
        Ok(Some(ChunkLease {
            chunk_id,
            node: node.to_string(),
            attempt,
            lease_token: token,
            lease_expires_at: expires,
            queue_path: PathBuf::from(queue_path),
            queue_sha256,
            offset,
            item_count,
            output_directory: PathBuf::from(output_directory),
        }))
    }

    /// `renew_chunk` — extend an active lease.
    ///
    /// # Errors
    ///
    /// Validation errors, stale leases, or [`FactoryError::Sqlite`].
    pub fn renew_chunk(
        &self,
        lease: &ChunkLease,
        lease_seconds: i64,
        now: Option<&str>,
    ) -> Result<String, FactoryError> {
        if !(30..=86_400).contains(&lease_seconds) {
            return Err(FactoryError::Value(
                "lease_seconds must be within 30..86400".into(),
            ));
        }
        let renewed_micros = resolve_now(now)?;
        let renewed_at = fmt(renewed_micros);
        let expires = fmt(plus_seconds(renewed_micros, lease_seconds));
        let mut connection = self.connect()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let row = lease_row(&transaction, &lease.chunk_id)?;
        assert_active_lease(row.as_ref(), lease)?;
        transaction.execute(
            "UPDATE factory_chunks
             SET lease_expires_at = ?1, updated_at = ?2
             WHERE chunk_id = ?3",
            params![expires, renewed_at, lease.chunk_id],
        )?;
        append_event(
            &transaction,
            "chunk_lease_renewed",
            &lease.chunk_id,
            &json!({
                "node": lease.node,
                "attempt": lease.attempt,
                "lease_expires_at": expires,
            }),
            &renewed_at,
        )?;
        transaction.commit()?;
        Ok(expires)
    }

    /// `complete_chunk` — verify the chunk output and seal the chunk.
    ///
    /// # Errors
    ///
    /// Output-verification errors, stale leases, or [`FactoryError::Sqlite`].
    pub fn complete_chunk(
        &self,
        lease: &ChunkLease,
        now: Option<&str>,
    ) -> Result<String, FactoryError> {
        let manifest_sha = verify_chunk_output(lease)?;
        let completed_at = fmt(resolve_now(now)?);
        let mut connection = self.connect()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let row = lease_row(&transaction, &lease.chunk_id)?;
        if row.as_ref().is_some_and(|r| r.0 == "completed") {
            let recorded = row.as_ref().and_then(|r| r.5.clone());
            if recorded != Some(manifest_sha.clone()) {
                return Err(FactoryError::State("completed chunk output changed".into()));
            }
            transaction.commit()?;
            return Ok(manifest_sha);
        }
        assert_active_lease(row.as_ref(), lease)?;
        transaction.execute(
            "UPDATE factory_chunks
             SET status = 'completed', output_manifest_sha256 = ?1,
                 lease_node = NULL, lease_token = NULL,
                 lease_expires_at = NULL, error = NULL, updated_at = ?2
             WHERE chunk_id = ?3",
            params![manifest_sha, completed_at, lease.chunk_id],
        )?;
        append_event(
            &transaction,
            "chunk_completed",
            &lease.chunk_id,
            &json!({
                "node": lease.node,
                "attempt": lease.attempt,
                "manifest_sha256": manifest_sha,
            }),
            &completed_at,
        )?;
        transaction.commit()?;
        Ok(manifest_sha)
    }

    /// `fail_chunk` — release a lease as retriable or terminal.
    ///
    /// # Errors
    ///
    /// Validation errors, stale leases, or [`FactoryError::Sqlite`].
    pub fn fail_chunk(
        &self,
        lease: &ChunkLease,
        error: &str,
        terminal: bool,
        retry_delay_seconds: i64,
        now: Option<&str>,
    ) -> Result<String, FactoryError> {
        if error.is_empty() || error != error.trim() {
            return Err(FactoryError::Value(
                "error must be non-empty and trimmed".into(),
            ));
        }
        if !(0..=86_400).contains(&retry_delay_seconds) {
            return Err(FactoryError::Value(
                "retry delay must be within 0..86400".into(),
            ));
        }
        let failed_micros = resolve_now(now)?;
        let failed_at = fmt(failed_micros);
        let mut connection = self.connect()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let row = lease_row(&transaction, &lease.chunk_id)?;
        assert_active_lease(row.as_ref(), lease)?;
        let (attempt, max_attempts) = row
            .as_ref()
            .map(|r| (r.1, r.2))
            .ok_or_else(|| FactoryError::State(format!("unknown chunk: {}", lease.chunk_id)))?;
        let exhausted = attempt >= max_attempts;
        let status = if terminal || exhausted {
            "failed"
        } else {
            "queued"
        };
        let available = fmt(plus_seconds(failed_micros, retry_delay_seconds));
        let truncated: String = error.chars().take(4000).collect();
        transaction.execute(
            "UPDATE factory_chunks
             SET status = ?1, available_at = ?2, lease_node = NULL,
                 lease_token = NULL, lease_expires_at = NULL,
                 error = ?3, updated_at = ?4
             WHERE chunk_id = ?5",
            params![status, available, truncated, failed_at, lease.chunk_id],
        )?;
        append_event(
            &transaction,
            "chunk_failed",
            &lease.chunk_id,
            &json!({
                "node": lease.node,
                "attempt": lease.attempt,
                "new_status": status,
                "error": truncated,
            }),
            &failed_at,
        )?;
        transaction.commit()?;
        Ok(status.to_string())
    }

    // ── visibility ───────────────────────────────────────────────────────

    /// `status` — the operational summary. `created_at` is wall-clock.
    ///
    /// # Errors
    ///
    /// [`FactoryError::Sqlite`] on failure.
    pub fn status(&self) -> Result<Value, FactoryError> {
        let connection = self.connect()?;
        let sources = count_by(
            &connection,
            "SELECT status, COUNT(*) AS count
             FROM factory_source_observations GROUP BY status ORDER BY status",
        )?;
        let unassigned_ready_sources: i64 = connection.query_row(
            "SELECT COUNT(*)
             FROM factory_source_observations AS source
             LEFT JOIN factory_source_batches AS assigned
                 ON assigned.observation_id = source.observation_id
             WHERE source.status = 'ready' AND assigned.observation_id IS NULL",
            [],
            |row| row.get(0),
        )?;
        let source_cohorts: i64 = connection.query_row(
            "SELECT COUNT(DISTINCT cohort_id) FROM factory_source_batches",
            [],
            |row| row.get(0),
        )?;
        let chunks = count_by(
            &connection,
            "SELECT status, COUNT(*) AS count
             FROM factory_chunks GROUP BY status ORDER BY status",
        )?;
        let mut active_leases: Vec<Value> = Vec::new();
        {
            let mut statement = connection.prepare(
                "SELECT chunk_id, lease_node, attempt, offset, item_count,
                        lease_expires_at
                 FROM factory_chunks
                 WHERE status = 'leased'
                 ORDER BY lease_node, chunk_id",
            )?;
            let rows = statement.query_map([], |row| {
                Ok(json!({
                    "chunk_id": row.get::<_, String>(0)?,
                    "node": row.get::<_, Option<String>>(1)?,
                    "attempt": row.get::<_, i64>(2)?,
                    "offset": row.get::<_, i64>(3)?,
                    "item_count": row.get::<_, i64>(4)?,
                    "lease_expires_at": row.get::<_, Option<String>>(5)?,
                }))
            })?;
            for row in rows {
                active_leases.push(row?);
            }
        }
        let mut frontier_runs: Vec<Value> = Vec::new();
        {
            let mut statement = connection.prepare(
                "SELECT run_id, status, request_count,
                        estimated_maximum_usd, updated_at, error
                 FROM factory_frontier_runs
                 ORDER BY created_at, run_id",
            )?;
            let rows = statement.query_map([], |row| {
                Ok(json!({
                    "run_id": row.get::<_, String>(0)?,
                    "status": row.get::<_, String>(1)?,
                    "request_count": row.get::<_, i64>(2)?,
                    "estimated_maximum_usd": row.get::<_, f64>(3)?,
                    "updated_at": row.get::<_, String>(4)?,
                    "error": row.get::<_, Option<String>>(5)?,
                }))
            })?;
            for row in rows {
                frontier_runs.push(row?);
            }
        }
        let events: i64 =
            connection.query_row("SELECT COUNT(*) FROM factory_events", [], |row| row.get(0))?;
        Ok(json!({
            "schema": "harness.electronics-factory-status.v1",
            "created_at": fmt(ts::now_micros()),
            "sources": sources,
            "unassigned_ready_sources": unassigned_ready_sources,
            "source_cohorts": source_cohorts,
            "chunks": chunks,
            "active_leases": active_leases,
            "frontier_runs": frontier_runs,
            "events": events,
        }))
    }

    /// `completed_bundle_paths` — completed output dirs for a queue.
    ///
    /// # Errors
    ///
    /// [`FactoryError::Sqlite`] on failure.
    pub fn completed_bundle_paths(&self, queue_sha256: &str) -> Result<Vec<PathBuf>, FactoryError> {
        let connection = self.connect()?;
        let mut statement = connection.prepare(
            "SELECT output_directory FROM factory_chunks
             WHERE queue_sha256 = ?1 AND status = 'completed'
             ORDER BY offset",
        )?;
        let rows = statement.query_map(params![queue_sha256], |row| {
            row.get::<_, String>(0).map(PathBuf::from)
        })?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row?);
        }
        Ok(out)
    }

    /// `queue_chunk_summary` — per-queue chunk and item counts.
    ///
    /// # Errors
    ///
    /// [`FactoryError::Sqlite`] on failure.
    pub fn queue_chunk_summary(&self, queue_sha256: &str) -> Result<Value, FactoryError> {
        let connection = self.connect()?;
        let chunks = count_by_p(
            &connection,
            "SELECT status, COUNT(*) AS count FROM factory_chunks
             WHERE queue_sha256 = ?1 GROUP BY status ORDER BY status",
            queue_sha256,
        )?;
        let items = sum_by_p(
            &connection,
            "SELECT status, SUM(item_count) AS items FROM factory_chunks
             WHERE queue_sha256 = ?1 GROUP BY status ORDER BY status",
            queue_sha256,
        )?;
        let registered_chunks: i64 = chunks.values().sum();
        let registered_items: i64 = items.values().sum();
        Ok(json!({
            "queue_sha256": queue_sha256,
            "chunks": chunks,
            "items": items,
            "registered_chunks": registered_chunks,
            "registered_items": registered_items,
        }))
    }
}

// ── helpers ──────────────────────────────────────────────────────────────

/// `geteuid` without a libc dependency: read `/proc` where available, else
/// parse `id -u`. macOS lacks /proc/self, so shell out once at open time.
fn libc_geteuid() -> u32 {
    if let Ok(text) = std::fs::read_to_string("/proc/self/status") {
        for line in text.lines() {
            if let Some(rest) = line.strip_prefix("Uid:") {
                if let Some(first) = rest.split_whitespace().next() {
                    if let Ok(uid) = first.parse::<u32>() {
                        return uid;
                    }
                }
            }
        }
    }
    std::process::Command::new("id")
        .arg("-u")
        .output()
        .ok()
        .and_then(|out| String::from_utf8(out.stdout).ok())
        .and_then(|text| text.trim().parse::<u32>().ok())
        .unwrap_or(0)
}

/// A stat identity: `(device, inode, size, mtime_ns)`.
type FileIdentity = (u64, u64, u64, i64);
/// An eligible observation: `(observation_id, path, identity)`.
type EligibleSource = (String, PathBuf, FileIdentity);

fn walk_pdfs(root: &Path) -> (Vec<(PathBuf, FileIdentity)>, usize) {
    let mut out = Vec::new();
    let mut unsafe_paths = 0usize;
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = std::fs::read_dir(&dir) else {
            unsafe_paths += 1;
            continue;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = path.file_name().map(|n| n.to_string_lossy().into_owned());
            let Some(name) = name else { continue };
            let Ok(meta) = path.symlink_metadata() else {
                unsafe_paths += 1;
                continue;
            };
            if meta.file_type().is_symlink() {
                if name.to_ascii_lowercase().ends_with(".pdf") {
                    unsafe_paths += 1;
                }
                continue;
            }
            if meta.is_dir() {
                stack.push(path);
                continue;
            }
            if !name.to_ascii_lowercase().ends_with(".pdf") {
                continue;
            }
            if !meta.is_file() {
                unsafe_paths += 1;
                continue;
            }
            let Ok(info) = std::fs::metadata(&path) else {
                unsafe_paths += 1;
                continue;
            };
            // Python: `resolved = path.resolve(strict=True)` — observations
            // key and store the RESOLVED path (/tmp -> /private/tmp on
            // macOS), which also fixes cross-prefix sort order.
            let resolved = std::fs::canonicalize(&path).unwrap_or(path.clone());
            out.push((
                resolved,
                (info.dev(), info.ino(), info.len(), mtime_ns(&info)),
            ));
        }
    }
    out.sort_by_key(|(path, _)| path.display().to_string());
    (out, unsafe_paths)
}

fn observation_id(path: &Path, identity: &(u64, u64, u64, i64)) -> String {
    let payload = canonical_json(&json!({
        "path": path.display().to_string(),
        "device": identity.0,
        "inode": identity.1,
        "byte_size": identity.2,
        "mtime_ns": identity.3,
    }))
    .unwrap_or_default();
    format!("source-{}", &sha256_hex(&payload)[..32])
}

fn append_event(
    transaction: &rusqlite::Transaction<'_>,
    event_type: &str,
    subject_id: &str,
    payload: &Value,
    created_at: &str,
) -> Result<(), FactoryError> {
    let encoded = canonical_json(payload).map_err(|e| FactoryError::State(e.to_string()))?;
    transaction.execute(
        "INSERT INTO factory_events (
             event_id, event_type, subject_id, payload_json,
             payload_sha256, created_at
         ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
        params![
            format!("factory-event-{}", token_hex_32()),
            event_type,
            subject_id,
            String::from_utf8_lossy(&encoded),
            sha256_hex(&encoded),
            created_at,
        ],
    )?;
    Ok(())
}

/// `_inspect_pdf` — verify identity stability, size floor, PDF markers, and
/// hash. The reference opens `O_NOFOLLOW` and streams 8 MiB chunks; the port
/// pre-checks the symlink and reads once, re-verifying identity after the
/// read — equivalent on a quiescent tree (the battery's domain).
fn inspect_pdf(path: &Path, expected: (u64, u64, u64, i64)) -> Result<String, String> {
    if path
        .symlink_metadata()
        .is_ok_and(|m| m.file_type().is_symlink())
    {
        return Err("FactoryStateError: source is not a regular file".into());
    }
    let before = std::fs::metadata(path).map_err(|e| format!("OSError: {e}"))?;
    let identity = (before.dev(), before.ino(), before.len(), mtime_ns(&before));
    if identity != expected || !before.is_file() {
        return Err("FactoryStateError: source changed before hashing".into());
    }
    if before.len() < 128 {
        return Err("FactoryStateError: PDF is implausibly small".into());
    }
    let payload = std::fs::read(path).map_err(|e| format!("OSError: {e}"))?;
    let after = std::fs::metadata(path).map_err(|e| format!("OSError: {e}"))?;
    let current = std::fs::symlink_metadata(path).map_err(|e| format!("OSError: {e}"))?;
    let after_identity = (after.dev(), after.ino(), after.len(), mtime_ns(&after));
    let path_identity = (
        current.dev(),
        current.ino(),
        current.len(),
        mtime_ns(&current),
    );
    if expected != after_identity || expected != path_identity {
        return Err("FactoryStateError: source changed while hashing".into());
    }
    let prefix = &payload[..payload.len().min(1024)];
    let tail_start = payload.len().saturating_sub(8192);
    let tail = &payload[tail_start..];
    if !prefix.windows(5).any(|w| w == b"%PDF-") {
        return Err("FactoryStateError: file has no PDF header".into());
    }
    if !tail.windows(5).any(|w| w == b"%%EOF") {
        return Err("FactoryStateError: file has no terminal PDF marker".into());
    }
    Ok(sha256_hex(&payload))
}

/// `_load_queue` — validate a structural work queue and hash its bytes.
fn load_queue(path: &Path) -> Result<(Value, String), FactoryError> {
    let queue_path = std::fs::canonicalize(path).map_err(|source| FactoryError::Io {
        context: format!("resolve {}", path.display()),
        source,
    })?;
    let meta = queue_path
        .symlink_metadata()
        .map_err(|source| FactoryError::Io {
            context: format!("stat {}", queue_path.display()),
            source,
        })?;
    if meta.file_type().is_symlink() || !meta.is_file() {
        return Err(FactoryError::State(format!(
            "expected a regular non-symlink file: {}",
            queue_path.display()
        )));
    }
    let payload = std::fs::read(&queue_path).map_err(|source| FactoryError::Io {
        context: format!("read {}", queue_path.display()),
        source,
    })?;
    let value: Value =
        serde_json::from_slice(&payload).map_err(|e| FactoryError::State(e.to_string()))?;
    if value.get("schema").and_then(Value::as_str)
        != Some("harness.electronics-structural-local-work.v1")
    {
        return Err(FactoryError::State(
            "unsupported structural work queue".into(),
        ));
    }
    let mut core = serde_json::Map::new();
    if let Some(map) = value.as_object() {
        for (key, item) in map {
            if key != "created_at" && key != "evidence_sha256" {
                core.insert(key.clone(), item.clone());
            }
        }
    }
    let digest = sha256_hex(
        &canonical_json(&Value::Object(core)).map_err(|e| FactoryError::State(e.to_string()))?,
    );
    if value.get("evidence_sha256").and_then(Value::as_str) != Some(digest.as_str()) {
        return Err(FactoryError::State(
            "structural work queue evidence is invalid".into(),
        ));
    }
    if value
        .get("work")
        .and_then(Value::as_array)
        .is_none_or(|work: &Vec<serde_json::Value>| work.is_empty())
    {
        return Err(FactoryError::State("structural work queue is empty".into()));
    }
    Ok((value, sha256_hex(&payload)))
}

/// `_verify_chunk_output` — the chunk's sealed manifest must match the lease.
fn verify_chunk_output(lease: &ChunkLease) -> Result<String, FactoryError> {
    if lease
        .output_directory
        .symlink_metadata()
        .is_ok_and(|m| m.file_type().is_symlink())
    {
        return Err(FactoryError::State(
            "chunk output must be a real directory".into(),
        ));
    }
    let output = std::fs::canonicalize(&lease.output_directory)
        .ok()
        .filter(|path| path.is_dir())
        .ok_or_else(|| FactoryError::State("chunk output must be a real directory".into()))?;
    let manifest_path = output.join("manifest.json");
    let meta = manifest_path.symlink_metadata().map_err(|_| {
        FactoryError::State(format!(
            "expected a regular non-symlink file: {}",
            manifest_path.display()
        ))
    })?;
    if meta.file_type().is_symlink() || !meta.is_file() {
        return Err(FactoryError::State(format!(
            "expected a regular non-symlink file: {}",
            manifest_path.display()
        )));
    }
    let manifest: Value = serde_json::from_slice(&std::fs::read(&manifest_path).map_err(|_| {
        FactoryError::State(format!(
            "expected a regular non-symlink file: {}",
            manifest_path.display()
        ))
    })?)
    .map_err(|e| FactoryError::State(e.to_string()))?;
    if manifest.get("schema").and_then(Value::as_str)
        != Some("harness.electronics-structural-local-extraction.v1")
    {
        return Err(FactoryError::State(
            "chunk output has an unsupported schema".into(),
        ));
    }
    let sources = manifest.get("sources").cloned().unwrap_or(Value::Null);
    let selection = manifest.get("selection").cloned().unwrap_or(Value::Null);
    if sources
        .get("structural_queue_sha256")
        .and_then(Value::as_str)
        != Some(lease.queue_sha256.as_str())
    {
        return Err(FactoryError::State(
            "chunk output used a different queue".into(),
        ));
    }
    let offset = selection
        .get("offset")
        .and_then(Value::as_i64)
        .unwrap_or(-1);
    let work_items = selection
        .get("work_items")
        .and_then(Value::as_i64)
        .unwrap_or(-1);
    if offset != lease.offset || work_items != lease.item_count {
        return Err(FactoryError::State(
            "chunk output selection does not match lease".into(),
        ));
    }
    let bytes = std::fs::read(&manifest_path).map_err(|source| FactoryError::Io {
        context: format!("read {}", manifest_path.display()),
        source,
    })?;
    Ok(sha256_hex(&bytes))
}

/// `(status, attempt, max_attempts, lease_node, lease_token,
/// output_manifest_sha256)` — the chunk columns lease checks read.
#[allow(clippy::type_complexity, reason = "the chunk columns read")]
type LeaseRow = (
    String,
    i64,
    i64,
    Option<String>,
    Option<String>,
    Option<String>,
);

/// `(chunk_id, queue_sha256, offset, item_count, output_directory)` — the
/// idempotency check in `register_queue`.
#[allow(clippy::type_complexity, reason = "the idempotency columns read")]
type ChunkRow = (String, String, i64, i64, String);

fn lease_row(
    transaction: &rusqlite::Transaction<'_>,
    chunk_id: &str,
) -> Result<Option<LeaseRow>, FactoryError> {
    Ok(transaction
        .query_row(
            "SELECT status, attempt, max_attempts, lease_node,
                    lease_token, output_manifest_sha256
             FROM factory_chunks WHERE chunk_id = ?1",
            params![chunk_id],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get(3)?,
                    row.get(4)?,
                    row.get(5)?,
                ))
            },
        )
        .optional()?)
}

fn assert_active_lease(row: Option<&LeaseRow>, lease: &ChunkLease) -> Result<(), FactoryError> {
    let Some((status, attempt, _max, lease_node, lease_token, _sha)) = row else {
        return Err(FactoryError::State(format!(
            "unknown chunk: {}",
            lease.chunk_id
        )));
    };
    if status != "leased"
        || *attempt != lease.attempt
        || lease_node.as_deref() != Some(lease.node.as_str())
        || lease_token.as_deref() != Some(lease.lease_token.as_str())
    {
        return Err(FactoryError::State(
            "chunk lease is stale or belongs to another node".into(),
        ));
    }
    Ok(())
}

fn recover_expired_in_transaction(
    transaction: &rusqlite::Transaction<'_>,
    now: &str,
) -> Result<Vec<String>, FactoryError> {
    let mut statement = transaction.prepare(
        "SELECT chunk_id, attempt, max_attempts, lease_node
         FROM factory_chunks
         WHERE status = 'leased' AND lease_expires_at <= ?1
         ORDER BY chunk_id",
    )?;
    let rows = statement.query_map(params![now], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, i64>(1)?,
            row.get::<_, i64>(2)?,
            row.get::<_, Option<String>>(3)?,
        ))
    })?;
    let mut expired = Vec::new();
    for row in rows {
        expired.push(row?);
    }
    drop(statement);
    let mut recovered = Vec::new();
    for (chunk_id, attempt, max_attempts, previous_node) in expired {
        let status = if attempt >= max_attempts {
            "failed"
        } else {
            "queued"
        };
        transaction.execute(
            "UPDATE factory_chunks
             SET status = ?1, available_at = ?2, lease_node = NULL,
                 lease_token = NULL, lease_expires_at = NULL,
                 error = ?3, updated_at = ?2
             WHERE chunk_id = ?4 AND status = 'leased'",
            params![status, now, "lease expired", chunk_id],
        )?;
        append_event(
            transaction,
            "chunk_lease_expired",
            &chunk_id,
            &json!({
                "attempt": attempt,
                "previous_node": previous_node,
                "new_status": status,
            }),
            now,
        )?;
        recovered.push(chunk_id);
    }
    Ok(recovered)
}

fn count_by(connection: &Connection, sql: &str) -> Result<BTreeMap<String, i64>, FactoryError> {
    let mut statement = connection.prepare(sql)?;
    let rows = statement.query_map([], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
    })?;
    let mut out = BTreeMap::new();
    for row in rows {
        let (key, count) = row?;
        out.insert(key, count);
    }
    Ok(out)
}

fn count_by_p(
    connection: &Connection,
    sql: &str,
    param: &str,
) -> Result<BTreeMap<String, i64>, FactoryError> {
    let mut statement = connection.prepare(sql)?;
    let rows = statement.query_map(params![param], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, i64>(1)?))
    })?;
    let mut out = BTreeMap::new();
    for row in rows {
        let (key, count) = row?;
        out.insert(key, count);
    }
    Ok(out)
}

fn sum_by_p(
    connection: &Connection,
    sql: &str,
    param: &str,
) -> Result<BTreeMap<String, i64>, FactoryError> {
    let mut statement = connection.prepare(sql)?;
    let rows = statement.query_map(params![param], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, Option<i64>>(1)?.unwrap_or_default(),
        ))
    })?;
    let mut out = BTreeMap::new();
    for row in rows {
        let (key, count) = row?;
        out.insert(key, count);
    }
    Ok(out)
}

/// `secrets.token_urlsafe(32)` — 43 url-safe characters.
fn token_urlsafe_32() -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    // /dev/urandom -> 32 bytes -> base64url; padding stripped exactly like
    // `secrets.token_urlsafe` (43 chars for 32 bytes).
    let mut bytes = [0u8; 32];
    read_random(&mut bytes);
    let mut out = String::new();
    for chunk in bytes.chunks(3) {
        let b0 = chunk.first().copied().unwrap_or(0);
        let b1 = chunk.get(1).copied();
        let b2 = chunk.get(2).copied();
        let triple =
            (u32::from(b0) << 16) | (b1.map_or(0u32, u32::from) << 8) | b2.map_or(0u32, u32::from);
        let quad = [
            (triple >> 18) & 63,
            (triple >> 12) & 63,
            (triple >> 6) & 63,
            triple & 63,
        ];
        let keep = 2 + usize::from(b1.is_some()) + usize::from(b2.is_some());
        for sextet in quad.iter().take(keep) {
            out.push(ALPHABET[*sextet as usize] as char);
        }
    }
    out
}

/// uuid4().hex equivalent — 32 hex chars from urandom.
fn token_hex_32() -> String {
    let mut bytes = [0u8; 16];
    read_random(&mut bytes);
    bytes.iter().fold(String::with_capacity(32), |mut out, b| {
        use std::fmt::Write as _;
        let _ = write!(out, "{b:02x}");
        out
    })
}

fn read_random(bytes: &mut [u8]) {
    use std::io::Read as _;
    if let Ok(mut file) = std::fs::File::open("/dev/urandom") {
        if file.read_exact(bytes).is_ok() {
            return;
        }
    }
    fill_pseudo(bytes);
}

fn fill_pseudo(bytes: &mut [u8]) {
    let mut state = (ts::now_micros() as u64) ^ (u64::from(std::process::id()) << 32);
    for byte in bytes.iter_mut() {
        state = state
            .wrapping_mul(6_364_136_223_846_793_005)
            .wrapping_add(1_442_695_040_888_963_407);
        *byte = (state >> 33) as u8;
    }
}

// ── phase 2: snapshot sealing, corpus seeding, frontier runs ─────────────

impl FactoryState {
    /// `seal_ready_source_snapshot` — write the ready-source manifest
    /// exactly once (`created_at` is wall-clock, as in the reference).
    ///
    /// # Errors
    ///
    /// [`FactoryError::State`] when the destination exists;
    /// [`FactoryError::Sqlite`] on failure.
    pub fn seal_ready_source_snapshot(&self, destination: &Path) -> Result<Value, FactoryError> {
        let connection = self.connect()?;
        let mut statement = connection.prepare(
            "SELECT observation_id, source_path, byte_size, mtime_ns, sha256
             FROM factory_source_observations
             WHERE status = 'ready'
             ORDER BY sha256, source_path",
        )?;
        let rows = statement.query_map([], |row| {
            Ok(json!({
                "observation_id": row.get::<_, String>(0)?,
                "source_path": row.get::<_, String>(1)?,
                "byte_size": row.get::<_, i64>(2)?,
                "mtime_ns": row.get::<_, i64>(3)?,
                "sha256": row.get::<_, Option<String>>(4)?,
            }))
        })?;
        let mut documents = Vec::new();
        for row in rows {
            documents.push(row?);
        }
        drop(statement);
        let core = json!({
            "schema": "harness.electronics-incremental-source-snapshot.v1",
            "purpose": "stable_deduplicated_datasheet_intake",
            "counts": {"documents": documents.len()},
            "documents": documents,
        });
        let evidence =
            sha256_hex(&canonical_json(&core).map_err(|e| FactoryError::State(e.to_string()))?);
        let manifest = {
            let mut map = Map::new();
            map.insert("created_at".into(), json!(fmt(ts::now_micros())));
            if let Value::Object(fields) = core {
                for (key, value) in fields {
                    map.insert(key, value);
                }
            }
            map.insert("evidence_sha256".into(), json!(evidence));
            Value::Object(map)
        };
        write_new_json(destination, &manifest)?;
        Ok(manifest)
    }

    /// `seal_unassigned_source_snapshot` — assign each unassigned ready
    /// source to exactly one cohort, sealing the snapshot file first.
    ///
    /// # Errors
    ///
    /// Validation errors, [`FactoryError::State`] on assignment conflicts
    /// (the snapshot file is removed), or [`FactoryError::Sqlite`].
    #[allow(clippy::too_many_lines, reason = "one transaction, reference-shaped")]
    pub fn seal_unassigned_source_snapshot(
        &self,
        destination: &Path,
        cohort_id: &str,
        maximum_documents: i64,
        now: Option<&str>,
    ) -> Result<Option<Value>, FactoryError> {
        if cohort_id.is_empty() || cohort_id != cohort_id.trim() || cohort_id.len() > 160 {
            return Err(FactoryError::Value(
                "cohort_id must be non-empty, trimmed, and bounded".into(),
            ));
        }
        if !(1..=100_000).contains(&maximum_documents) {
            return Err(FactoryError::Value(
                "maximum_documents must be within 1..100000".into(),
            ));
        }
        let connection = self.connect()?;
        let mut statement = connection.prepare(
            "SELECT source.observation_id, source.source_path,
                    source.byte_size, source.mtime_ns, source.sha256
             FROM factory_source_observations AS source
             LEFT JOIN factory_source_batches AS assigned
                 ON assigned.observation_id = source.observation_id
             WHERE source.status = 'ready' AND assigned.observation_id IS NULL
             ORDER BY source.first_seen_at, source.observation_id
             LIMIT ?1",
        )?;
        let rows = statement.query_map(params![maximum_documents], |row| {
            Ok((
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, i64>(2)?,
                row.get::<_, i64>(3)?,
                row.get::<_, Option<String>>(4)?,
            ))
        })?;
        let mut selected: Vec<(String, String, i64, i64, Option<String>)> = Vec::new();
        for row in rows {
            selected.push(row?);
        }
        drop(statement);
        drop(connection);
        if selected.is_empty() {
            return Ok(None);
        }
        let documents: Vec<Value> = selected
            .iter()
            .map(|(id, path, size, mtime, sha)| {
                json!({
                    "observation_id": id,
                    "source_path": path,
                    "byte_size": size,
                    "mtime_ns": mtime,
                    "sha256": sha,
                })
            })
            .collect();
        let sealed_at = fmt(resolve_now(now)?);
        let core = json!({
            "schema": "harness.electronics-incremental-source-snapshot.v1",
            "purpose": "stable_deduplicated_datasheet_intake",
            "cohort_id": cohort_id,
            "counts": {"documents": documents.len()},
            "documents": documents,
        });
        let evidence =
            sha256_hex(&canonical_json(&core).map_err(|e| FactoryError::State(e.to_string()))?);
        let manifest = {
            let mut map = Map::new();
            map.insert("created_at".into(), json!(sealed_at.clone()));
            if let Value::Object(fields) = core {
                for (key, value) in fields {
                    map.insert(key, value);
                }
            }
            map.insert("evidence_sha256".into(), json!(evidence));
            Value::Object(map)
        };
        write_new_json(destination, &manifest)?;
        let snapshot_path = std::fs::canonicalize(destination).map_err(|_| {
            FactoryError::FileNotFound(format!(
                "[Errno 2] No such file or directory: '{}'",
                destination.display()
            ))
        })?;
        let snapshot_sha = dw_recon::corpus::sha256_file(&snapshot_path)
            .map_err(|e| FactoryError::State(e.to_string()))?;

        let assign = (|| -> Result<(), FactoryError> {
            let mut connection = self.connect()?;
            let transaction =
                connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
            for (observation_id, _, _, _, _) in &selected {
                let current: Option<(String, String)> = transaction
                    .query_row(
                        "SELECT cohort_id, snapshot_sha256
                         FROM factory_source_batches WHERE observation_id = ?1",
                        params![observation_id],
                        |row| Ok((row.get(0)?, row.get(1)?)),
                    )
                    .optional()?;
                if let Some((existing_cohort, existing_sha)) = current {
                    if existing_cohort != cohort_id || existing_sha != snapshot_sha {
                        return Err(FactoryError::State(
                            "source was concurrently assigned to another cohort".into(),
                        ));
                    }
                    continue;
                }
                transaction.execute(
                    "INSERT INTO factory_source_batches (
                         observation_id, cohort_id, snapshot_path,
                         snapshot_sha256, created_at
                     ) VALUES (?1, ?2, ?3, ?4, ?5)",
                    params![
                        observation_id,
                        cohort_id,
                        snapshot_path.display().to_string(),
                        snapshot_sha,
                        sealed_at,
                    ],
                )?;
            }
            append_event(
                &transaction,
                "source_cohort_sealed",
                cohort_id,
                &json!({
                    "snapshot_path": snapshot_path.display().to_string(),
                    "snapshot_sha256": snapshot_sha,
                    "documents": documents.len(),
                }),
                &sealed_at,
            )?;
            transaction.commit()?;
            Ok(())
        })();
        if let Err(error) = assign {
            // The reference: chmod 0600, unlink, re-raise.
            let _ =
                std::fs::set_permissions(&snapshot_path, std::fs::Permissions::from_mode(0o600));
            let _ = std::fs::remove_file(&snapshot_path);
            return Err(error);
        }
        Ok(Some(manifest))
    }

    /// `seed_corpus_registry` — backfill already-sealed PDFs so discovery
    /// hashes only genuinely new files.
    ///
    /// # Errors
    ///
    /// Registry-shape errors (from the corpus verifier), root-resolution
    /// failures, or [`FactoryError::Sqlite`].
    #[allow(clippy::too_many_lines, reason = "one transaction, reference-shaped")]
    pub fn seed_corpus_registry(
        &self,
        registry_path: &Path,
        now: Option<&str>,
    ) -> Result<Value, FactoryError> {
        let path = resolve_or_not_found(registry_path)?;
        secure_regular_file(&path)?;
        let registry: Value =
            serde_json::from_slice(&std::fs::read(&path).map_err(|source| FactoryError::Io {
                context: format!("read {}", path.display()),
                source,
            })?)
            .map_err(|e| FactoryError::Value(e.to_string()))?;
        dw_recon::corpus::verify_corpus_registry(&registry)
            .map_err(|e| FactoryError::Value(e.to_string()))?;
        let pdf_root = registry
            .pointer("/sources/pdf_root")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let root = resolve_or_not_found(Path::new(pdf_root))?;
        if !root.is_dir() {
            return Err(FactoryError::State(
                "sealed corpus PDF root is unavailable".into(),
            ));
        }
        let registry_bytes = std::fs::read(&path).map_err(|source| FactoryError::Io {
            context: format!("read {}", path.display()),
            source,
        })?;
        let registry_sha = sha256_hex(&registry_bytes);
        let seeded_at = fmt(resolve_now(now)?);
        let baseline_cohort = format!("baseline-{}", &registry_sha[..24]);

        let mut inserted = 0i64;
        let mut existing = 0i64;
        let mut missing = 0i64;
        let mut unsafe_count = 0i64;
        let mut canonical_by_digest: BTreeMap<String, String> = BTreeMap::new();

        let mut connection = self.connect()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        for document in registry
            .get("documents")
            .and_then(Value::as_array)
            .map(Vec::as_slice)
            .unwrap_or_default()
        {
            let digest = document
                .get("document_sha256")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string();
            let paths = document
                .get("paths")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            let declared_size = document.get("byte_size").and_then(Value::as_i64);
            for relative in paths {
                let relative = relative.as_str().unwrap_or_default();
                // Python: `(root / relative).resolve()` — NON-strict; a
                // missing target is the `missing` counter, not an error.
                let joined = root.join(relative);
                let candidate = std::fs::canonicalize(&joined).unwrap_or_else(|_| joined.clone());
                if !candidate.starts_with(&root) {
                    unsafe_count += 1;
                    continue;
                }
                // Missing targets are the `missing` counter (Python checks
                // `not candidate.exists()` after the non-strict resolve).
                let Some(meta) = candidate.symlink_metadata().ok() else {
                    missing += 1;
                    continue;
                };
                if meta.file_type().is_symlink() || !meta.is_file() {
                    missing += 1;
                    continue;
                }
                let identity = (meta.dev(), meta.ino(), meta.len(), mtime_ns(&meta));
                if Some(identity.2 as i64) != declared_size {
                    unsafe_count += 1;
                    continue;
                }
                let observation_id = observation_id(&candidate, &identity);
                let current: Option<Option<String>> = transaction
                    .query_row(
                        "SELECT sha256 FROM factory_source_observations
                         WHERE observation_id = ?1",
                        params![observation_id],
                        |row| row.get(0),
                    )
                    .optional()?;
                match current {
                    Some(recorded_sha) => {
                        if recorded_sha.as_deref().is_some_and(|s| s != digest) {
                            return Err(FactoryError::State(
                                "seeded corpus conflicts with source observation".into(),
                            ));
                        }
                        existing += 1;
                        canonical_by_digest
                            .entry(digest.clone())
                            .or_insert_with(|| observation_id.clone());
                    }
                    None => {
                        let canonical = canonical_by_digest.get(&digest).cloned();
                        let status = if canonical.is_some() {
                            "duplicate"
                        } else {
                            "ready"
                        };
                        transaction.execute(
                            "INSERT INTO factory_source_observations (
                                 observation_id, source_path, device, inode,
                                 byte_size, mtime_ns, first_seen_at,
                                 stable_after_at, last_seen_at, status, sha256,
                                 canonical_observation_id, reason, updated_at
                             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?7, ?7,
                                      ?8, ?9, ?10, ?11, ?7)",
                            params![
                                observation_id,
                                candidate.display().to_string(),
                                identity.0 as i64,
                                identity.1 as i64,
                                identity.2 as i64,
                                identity.3,
                                seeded_at,
                                status,
                                digest,
                                canonical,
                                format!("seeded from sealed registry {registry_sha}"),
                            ],
                        )?;
                        append_event(
                            &transaction,
                            "source_seeded_from_registry",
                            &observation_id,
                            &json!({
                                "path": candidate.display().to_string(),
                                "sha256": digest,
                                "registry_sha256": registry_sha,
                                "status": status,
                                "canonical_observation_id": canonical,
                            }),
                            &seeded_at,
                        )?;
                        canonical_by_digest
                            .entry(digest.clone())
                            .or_insert_with(|| observation_id.clone());
                        inserted += 1;
                    }
                }
                transaction.execute(
                    "INSERT OR IGNORE INTO factory_source_batches (
                         observation_id, cohort_id, snapshot_path,
                         snapshot_sha256, created_at
                     ) VALUES (?1, ?2, ?3, ?4, ?5)",
                    params![
                        observation_id,
                        baseline_cohort,
                        path.display().to_string(),
                        registry_sha,
                        seeded_at,
                    ],
                )?;
            }
        }
        transaction.commit()?;
        Ok(json!({
            "inserted": inserted,
            "existing": existing,
            "missing": missing,
            "unsafe": unsafe_count,
        }))
    }

    /// `register_frontier_run` — idempotent registration of a prepared
    /// bundle; `Ok(false)` means an identical run was already registered.
    ///
    /// # Errors
    ///
    /// Bundle/state shape errors, identity conflicts, or
    /// [`FactoryError::Sqlite`].
    #[allow(clippy::too_many_lines, reason = "one transaction, reference-shaped")]
    pub fn register_frontier_run(
        &self,
        run_id: &str,
        prepared_bundle: &Path,
        submission_state: &Path,
        lifecycle_root: &Path,
        now: Option<&str>,
    ) -> Result<bool, FactoryError> {
        let prepared = resolve_or_not_found(prepared_bundle)?;
        let state = resolve_or_not_found(submission_state)?;
        let manifest_path = prepared.join("manifest.json");
        secure_regular_file(&manifest_path)?;
        let manifest: Value =
            serde_json::from_slice(&std::fs::read(&manifest_path).map_err(|source| {
                FactoryError::Io {
                    context: format!("read {}", manifest_path.display()),
                    source,
                }
            })?)
            .map_err(|e| FactoryError::State(e.to_string()))?;
        if manifest.get("schema").and_then(Value::as_str)
            != Some("harness.electronics-frontier-batch.v1")
        {
            return Err(FactoryError::State(
                "unsupported prepared frontier bundle".into(),
            ));
        }
        if !state.join("submission-intent.json").is_file() {
            return Err(FactoryError::State(
                "frontier submission state is incomplete".into(),
            ));
        }
        let created_at = fmt(resolve_now(now)?);
        let lifecycle =
            std::fs::canonicalize(lifecycle_root).unwrap_or_else(|_| lifecycle_root.to_path_buf());
        let mut connection = self.connect()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let current: Option<(String, String, String)> = transaction
            .query_row(
                "SELECT prepared_evidence_sha256, submission_state,
                        lifecycle_root
                 FROM factory_frontier_runs WHERE run_id = ?1",
                params![run_id],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .optional()?;
        if let Some((recorded_evidence, recorded_state, recorded_lifecycle)) = current {
            if recorded_evidence
                != manifest
                    .get("evidence_sha256")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
            {
                return Err(FactoryError::State(format!(
                    "frontier run identity conflict: {run_id}"
                )));
            }
            if recorded_state != state.display().to_string()
                || recorded_lifecycle != lifecycle.display().to_string()
            {
                return Err(FactoryError::State(format!(
                    "frontier run identity conflict: {run_id}"
                )));
            }
            transaction.commit()?;
            return Ok(false);
        }
        let requests = manifest
            .pointer("/counts/requests")
            .and_then(Value::as_i64)
            .unwrap_or_default();
        let estimated = manifest
            .pointer("/pricing/estimated_maximum_usd")
            .and_then(Value::as_f64)
            .unwrap_or_default();
        transaction.execute(
            "INSERT INTO factory_frontier_runs (
                 run_id, prepared_bundle, prepared_evidence_sha256,
                 submission_state, lifecycle_root, status, request_count,
                 estimated_maximum_usd, created_at, updated_at
             ) VALUES (?1, ?2, ?3, ?4, ?5, 'submitted', ?6, ?7, ?8, ?8)",
            params![
                run_id,
                prepared.display().to_string(),
                manifest
                    .get("evidence_sha256")
                    .and_then(Value::as_str)
                    .unwrap_or_default(),
                state.display().to_string(),
                lifecycle.display().to_string(),
                requests,
                estimated,
                created_at,
            ],
        )?;
        append_event(
            &transaction,
            "frontier_run_registered",
            run_id,
            &json!({
                "prepared_evidence_sha256":
                    manifest.get("evidence_sha256").cloned()
                        .unwrap_or(Value::Null),
                "request_count": requests,
                "estimated_maximum_usd": estimated,
            }),
            &created_at,
        )?;
        transaction.commit()?;
        Ok(true)
    }

    /// `record_frontier_status` — advance a frontier run's status, inferring
    /// `ended` when every batch reports `processing_status` of `ended`.
    ///
    /// # Errors
    ///
    /// [`FactoryError::Value`] for unsupported stages,
    /// [`FactoryError::State`] for unknown runs, [`FactoryError::Sqlite`].
    pub fn record_frontier_status(
        &self,
        run_id: &str,
        status_payload: &Value,
        stage: Option<&str>,
        error: Option<&str>,
        now: Option<&str>,
    ) -> Result<(), FactoryError> {
        let recorded_at = fmt(resolve_now(now)?);
        let batches = status_payload
            .get("batches")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mut processing: Vec<String> = batches
            .iter()
            .filter(|batch| batch.is_object())
            .map(|batch| {
                batch
                    .get("processing_status")
                    .map_or_else(|| "null".to_string(), py_str_of)
            })
            .collect();
        processing.sort();
        processing.dedup();
        let inferred = if !processing.is_empty() && processing == ["ended".to_string()] {
            "ended"
        } else {
            "processing"
        };
        let new_status = stage.unwrap_or(inferred);
        let allowed = [
            "submitted",
            "processing",
            "ended",
            "retrieved",
            "reconciled",
            "verified",
            "finalized",
            "failed",
        ];
        if !allowed.contains(&new_status) {
            return Err(FactoryError::Value(format!(
                "unsupported frontier run stage: {new_status}"
            )));
        }
        let encoded = String::from_utf8(
            canonical_json(status_payload).map_err(|e| FactoryError::State(e.to_string()))?,
        )
        .map_err(|e| FactoryError::State(e.to_string()))?;
        let mut connection = self.connect()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let current: Option<String> = transaction
            .query_row(
                "SELECT status FROM factory_frontier_runs WHERE run_id = ?1",
                params![run_id],
                |row| row.get(0),
            )
            .optional()?;
        let Some(previous_status) = current else {
            return Err(FactoryError::State(format!(
                "unknown frontier run: {run_id}"
            )));
        };
        transaction.execute(
            "UPDATE factory_frontier_runs
             SET status = ?1, latest_status_json = ?2, error = ?3,
                 updated_at = ?4
             WHERE run_id = ?5",
            params![new_status, encoded, error, recorded_at, run_id],
        )?;
        append_event(
            &transaction,
            "frontier_status_recorded",
            run_id,
            &json!({
                "previous_status": previous_status,
                "new_status": new_status,
                "error": error,
            }),
            &recorded_at,
        )?;
        transaction.commit()?;
        Ok(())
    }
}

/// Python `str(value)` for the `processing_status` set membership check.
fn py_str_of(value: &Value) -> String {
    match value {
        Value::Null => "None".to_string(),
        Value::Bool(b) => if *b { "True" } else { "False" }.to_string(),
        Value::String(s) => s.clone(),
        other => other.to_string(),
    }
}

/// `Path.resolve(strict=True)` with Python's errno message on `NotFound`.
fn resolve_or_not_found(path: &Path) -> Result<PathBuf, FactoryError> {
    match std::fs::canonicalize(path) {
        Ok(resolved) => Ok(resolved),
        Err(source) if source.kind() == std::io::ErrorKind::NotFound => {
            Err(FactoryError::FileNotFound(format!(
                "[Errno 2] No such file or directory: '{}'",
                path.display()
            )))
        }
        Err(source) => Err(FactoryError::Io {
            context: format!("resolve {}", path.display()),
            source,
        }),
    }
}

/// `_secure_regular_file` — a real, non-symlink file.
fn secure_regular_file(path: &Path) -> Result<(), FactoryError> {
    let meta = path.symlink_metadata().map_err(|source| FactoryError::Io {
        context: format!("stat {}", path.display()),
        source,
    })?;
    if meta.file_type().is_symlink() || !meta.is_file() {
        return Err(FactoryError::State(format!(
            "expected a regular non-symlink file: {}",
            path.display()
        )));
    }
    Ok(())
}

/// `_write_new_json` — write-once JSON: temp file, fsync, chmod 0444,
/// hardlink into place (atomic appearance), temp removed.
fn write_new_json(path: &Path, value: &Value) -> Result<(), FactoryError> {
    use std::io::Write as _;
    let destination = std::fs::canonicalize(path.parent().unwrap_or(path)).map_or_else(
        |_| path.to_path_buf(),
        |parent| parent.join(path.file_name().unwrap_or_default()),
    );
    if destination.symlink_metadata().is_ok() {
        return Err(FactoryError::State(format!(
            "immutable output already exists: {}",
            destination.display()
        )));
    }
    if let Some(parent) = destination.parent() {
        std::fs::create_dir_all(parent).map_err(|source| FactoryError::Io {
            context: format!("mkdir {}", parent.display()),
            source,
        })?;
    }
    let payload =
        dw_model::py_dumps_indent2_sorted(value).map_err(|e| FactoryError::State(e.to_string()))?;
    let temporary = destination.with_file_name(format!(
        ".{}.{}.tmp",
        destination.file_name().map_or_else(
            || "snapshot".to_string(),
            |n| n.to_string_lossy().into_owned()
        ),
        std::process::id()
    ));
    {
        let mut handle = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
            .map_err(|source| FactoryError::Io {
                context: format!("create {}", temporary.display()),
                source,
            })?;
        handle
            .write_all(payload.as_bytes())
            .and_then(|()| handle.write_all(b"\n"))
            .and_then(|()| handle.sync_all())
            .map_err(|source| FactoryError::Io {
                context: format!("write {}", temporary.display()),
                source,
            })?;
    }
    std::fs::set_permissions(&temporary, std::fs::Permissions::from_mode(0o444)).map_err(
        |source| FactoryError::Io {
            context: format!("chmod 444 {}", temporary.display()),
            source,
        },
    )?;
    let linked = std::fs::hard_link(&temporary, &destination);
    let _ = std::fs::remove_file(&temporary);
    linked.map_err(|source| FactoryError::Io {
        context: format!("link {}", destination.display()),
        source,
    })?;
    Ok(())
}

#[cfg(test)]
// Controls assert on fixed vectors; unwrap noise is fine here.
#[allow(clippy::unwrap_used, clippy::expect_used, reason = "fixed vectors")]
mod tests {
    use super::ts;

    /// The timestamp engine must round-trip Python's isoformat exactly.
    #[test]
    fn timestamps_format_like_python() {
        let text = "2026-10-14T01:02:03.000000+00:00";
        let micros = ts::parse(text).expect("parses");
        assert_eq!(ts::format(micros), text);
        assert_eq!(ts::parse("2026-10-14T01:02:03Z").unwrap(), micros);
        assert_eq!(ts::parse("2026-10-14T01:02:03+00:00").unwrap(), micros);
        // leap-day arithmetic
        let feb = ts::parse("2024-02-29T23:59:59+00:00").unwrap();
        assert_eq!(
            ts::format(feb + 1_000_000),
            "2024-03-01T00:00:00.000000+00:00"
        );
        assert!(ts::parse("2026-10-14T01:02:03").is_err(), "naive rejected");
    }
}
