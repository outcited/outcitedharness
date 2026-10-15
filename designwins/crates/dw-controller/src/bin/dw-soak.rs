//! `dw-soak` — the M3 Rust factory soak orchestration (POWER-DATA-01/M5).
//!
//! Thin wiring over proven surfaces: dw-controller owns ALL durable state
//! (queue, chunk leases, retries, recovery, event ledger — 43-step gold
//! battery vs the live factory), dw-truth owns every deterministic check
//! (`find_quote` / `value_in_quote` / `column_position_binds` — replacement-gate
//! proven, 0 diffs). The only Python is `scripts/soak_model_shim.py`, a
//! provider shell that imports the live `burn_aisle.py` so prompts, endpoint
//! chain and repair protocol stay byte-identical to the Python factory.
//!
//! The per-document cascade replicates `burn_doc` in `burn_aisle.py`
//! step-for-step so outputs are directly diffable:
//! substrate -> student/V41 propose -> `quick_ok` repair rounds ->
//! deterministic verify -> page attribution -> per-stem ledger row.

// An operational soak CLI fails fast and loudly: every expect/panic below
// aborts the worker, the chunk lease expires, and `dw-soak recover` requeues
// it — recovery is the designed failure path, not silent degradation.
#![allow(
    clippy::expect_used,
    clippy::panic,
    clippy::indexing_slicing,
    clippy::cast_possible_wrap,
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    reason = "CLI fail-fast; lease expiry + recover is the designed retry path"
)]

use std::collections::BTreeMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Instant;

use dw_controller::FactoryState;
use dw_truth::{column_position_binds, find_quote, value_in_quote, Field};
use serde_json::{json, Value};

const QUEUE_SCHEMA: &str = "harness.electronics-soak-work.v1";
const MANIFEST_SCHEMA: &str = "harness.electronics-soak-extraction.v1";
const TEXT_CAP_CHARS: usize = 28_000;
const REPAIR_ROUNDS: usize = 2;

fn usage() -> ! {
    eprintln!(
        "usage: dw-soak enqueue --state DIR --cohort FROZEN.json --queue-out PATH \
         --out-root DIR [--chunk-size N] [--max-attempts N] [--substrate-root DIR]...\n\
         \x20      dw-soak worker --state DIR --node NAME --shim PATH [--python EXE] \
         [--lease-secs N] [--max-jobs N] [--adopt]\n\
         \x20      dw-soak status --state DIR\n\
         \x20      dw-soak recover --state DIR"
    );
    std::process::exit(2);
}

fn need(args: &[String], name: &str) -> String {
    flag(args, name).unwrap_or_else(|| usage())
}

fn flag(args: &[String], name: &str) -> Option<String> {
    args.iter()
        .position(|a| a == name)
        .and_then(|i| args.get(i + 1))
        .cloned()
}

fn flag_usize(args: &[String], name: &str, default: usize) -> usize {
    flag(args, name)
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

fn canonical_core(value: &Value) -> String {
    // mirrors harness.electronics.claims.canonical_json over the queue core
    // (created_at / evidence_sha256 excluded): sorted keys, compact
    // separators, unicode verbatim. serde_json's BTreeMap ordering matches.
    let mut core = serde_json::Map::new();
    if let Some(map) = value.as_object() {
        for (key, item) in map {
            if key != "created_at" && key != "evidence_sha256" {
                core.insert(key.clone(), item.clone());
            }
        }
    }
    Value::Object(core).to_string()
}

fn sha256_hex(bytes: &[u8]) -> String {
    dw_extract::sha256::sha256_hex(bytes)
}

// ── enqueue ─────────────────────────────────────────────────────────────

fn substrate_index(roots: &[PathBuf]) -> BTreeMap<String, PathBuf> {
    let mut index = BTreeMap::new();
    for root in roots {
        let mut stack = vec![root.clone()];
        while let Some(dir) = stack.pop() {
            let Ok(entries) = std::fs::read_dir(&dir) else {
                continue;
            };
            for entry in entries.flatten() {
                let path = entry.path();
                if path.is_dir() {
                    stack.push(path);
                } else if path.extension().is_some_and(|e| e == "json") {
                    let stem = path
                        .file_stem()
                        .map(|s| s.to_string_lossy().to_string())
                        .unwrap_or_default();
                    // first-root-wins, matching burn_aisle's root loop order
                    index.entry(stem).or_insert(path);
                }
            }
        }
    }
    index
}

fn enqueue(args: &[String]) -> i32 {
    let state_dir = PathBuf::from(need(args, "--state"));
    let cohort_path = PathBuf::from(need(args, "--cohort"));
    let queue_out = PathBuf::from(need(args, "--queue-out"));
    let out_root = PathBuf::from(need(args, "--out-root"));
    let chunk_size = flag_usize(args, "--chunk-size", 25);
    let max_attempts = flag_usize(args, "--max-attempts", 3);
    let substrate_roots: Vec<PathBuf> = {
        let mut roots = Vec::new();
        for (i, a) in args.iter().enumerate() {
            if a == "--substrate-root" {
                if let Some(v) = args.get(i + 1) {
                    roots.push(PathBuf::from(v));
                }
            }
        }
        roots
    };

    let cohort: Value = serde_json::from_slice(&std::fs::read(&cohort_path).expect("read cohort"))
        .expect("parse cohort");
    let pdfs: Vec<String> = cohort["pdfs"]
        .as_array()
        .expect("cohort pdfs")
        .iter()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect();

    println!("substrate index: scanning {} roots", substrate_roots.len());
    let index = if let Some(index_path) = flag(args, "--substrate-index") {
        let raw = std::fs::read(&index_path).expect("read substrate index");
        let parsed: BTreeMap<String, Option<String>> =
            serde_json::from_slice(&raw).expect("parse substrate index");
        println!("substrate index: loaded {}", index_path);
        parsed
            .into_iter()
            .filter_map(|(stem, path)| path.map(|p| (stem, PathBuf::from(p))))
            .collect()
    } else {
        if substrate_roots.is_empty() {
            eprintln!("no --substrate-index and no --substrate-root: every doc will skip");
        }
        substrate_index(&substrate_roots)
    };
    println!("substrate index: {} stems", index.len());

    let mut work = Vec::with_capacity(pdfs.len());
    for pdf in &pdfs {
        let stem = Path::new(pdf)
            .file_stem()
            .map(|s| s.to_string_lossy().to_string())
            .unwrap_or_default();
        let substrate = index.get(&stem).map(|p| p.display().to_string());
        work.push(json!({
            "work_id": stem,
            "stem": stem,
            "pdf": pdf,
            "substrate": substrate,
        }));
    }

    let created_at = chrono_free_now();
    let mut queue = json!({
        "schema": QUEUE_SCHEMA,
        "policy": {
            "pipeline": "burn_doc parity: substrate -> student/V41 propose -> quick_ok repair x2 -> deterministic verify -> page spans",
            "model_lane": "shim -> live burn_aisle.py (student then local chain, text fail-closed)",
            "verify": "dw-truth (replacement-gate proven)",
            "chunk_size": chunk_size,
            "max_attempts": max_attempts,
        },
        "sources": {
            "cohort_path": cohort_path.display().to_string(),
            "cohort_count": pdfs.len(),
        },
        "work": work,
        "created_at": created_at,
    });
    let evidence = {
        let obj = queue.as_object().expect("queue object").clone();
        let mut core = serde_json::Map::new();
        for (k, v) in &obj {
            if k != "created_at" && k != "evidence_sha256" {
                core.insert(k.clone(), v.clone());
            }
        }
        sha256_hex(canonical_core(&Value::Object(core)).as_bytes())
    };
    queue["evidence_sha256"] = Value::String(evidence.clone());
    std::fs::write(
        &queue_out,
        serde_json::to_vec(&queue).expect("serialize queue"),
    )
    .expect("write queue");
    println!(
        "queue written: {} ({} work items, evidence {evidence})",
        queue_out.display(),
        pdfs.len()
    );

    std::fs::create_dir_all(&out_root).expect("create out root");
    let state = FactoryState::open(&state_dir).expect("open factory state");
    let registered = state
        .register_queue(
            &queue_out,
            &out_root,
            chunk_size as i64,
            max_attempts as i64,
            0,
            None,
        )
        .expect("register queue");
    println!(
        "registered {} chunks under {}",
        registered.len(),
        out_root.display()
    );
    0
}

// ── the per-doc cascade (burn_doc parity) ───────────────────────────────

struct Joined {
    text: String,
    spans: Vec<(usize, usize, String)>,
}

fn join_pages(sub: &Value) -> Joined {
    let pages = sub["pages"].as_array().cloned().unwrap_or_default();
    let texts: Vec<String> = pages
        .iter()
        .map(|p| p["text"].as_str().unwrap_or("").to_string())
        .collect();
    let full: String = texts.join("\n").chars().take(TEXT_CAP_CHARS).collect();
    let mut spans = Vec::new();
    let mut off = 0usize;
    for (p, t) in pages.iter().zip(texts.iter()) {
        spans.push((
            off,
            off + t.chars().count(),
            p["page"].as_str().unwrap_or("").to_string(),
        ));
        off += t.chars().count() + 1;
    }
    Joined { text: full, spans }
}

fn page_for(quote: &str, text: &str, spans: &[(usize, usize, String)]) -> Option<String> {
    // char-offset parity with Python's str.find + len arithmetic
    if quote.is_empty() {
        return None;
    }
    let idx_byte = text.find(quote)?;
    let idx = text[..idx_byte].chars().count();
    let mid = idx + std::cmp::max(1, quote.chars().count()) / 2;
    for (start, end, page) in spans {
        if *start <= mid && mid <= end + 1 {
            return Some(page.clone());
        }
    }
    None
}

fn alias_field(symbol: &str) -> Option<Field> {
    let s = symbol.to_lowercase();
    if s.contains("rds") {
        Field::parse("v_abs_max")
    } else if s.contains("vds") || s.contains("vin") {
        Field::parse("vin_max_v")
    } else if s.trim() == "id" || s.contains("iout") || s.contains("id_") {
        Field::parse("iout_max_a")
    } else if s.contains("tj") || s.contains("temp") {
        Field::parse("temp_max_c")
    } else {
        None
    }
}

fn quick_ok(claim: &Value, text: &str) -> bool {
    let Some(quote) = claim["quote"].as_str() else {
        return false;
    };
    if quote.is_empty() {
        return false;
    }
    let field = alias_field(claim["symbol"].as_str().unwrap_or(""));
    find_quote(quote, text, None, field)
}

fn value_f64(claim: &Value) -> Option<f64> {
    claim["value"]
        .as_f64()
        .or_else(|| claim["value"].as_str().and_then(|s| s.trim().parse().ok()))
}

fn shim_call(python: &str, shim: &str, mode: &str, payload: &Value) -> Option<Value> {
    let mut child = Command::new(python)
        .arg(shim)
        .arg(mode)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    {
        let stdin = child.stdin.as_mut()?;
        stdin.write_all(payload.to_string().as_bytes()).ok()?;
    }
    let out = child.wait_with_output().ok()?;
    if !out.status.success() {
        return None;
    }
    serde_json::from_slice(&out.stdout).ok()
}

fn burn_doc_parity(item: &Value, python: &str, shim: &str) -> Value {
    let stem = item["stem"].as_str().unwrap_or_default().to_string();
    let Some(substrate_path) = item["substrate"].as_str() else {
        return json!({"stem": stem, "skip": "no_substrate"});
    };
    let Ok(substrate_raw) = std::fs::read(substrate_path) else {
        return json!({"stem": stem, "skip": "no_substrate"});
    };
    let Ok(sub) = serde_json::from_slice::<Value>(&substrate_raw) else {
        return json!({"stem": stem, "skip": "no_substrate"});
    };
    let joined = join_pages(&sub);
    let text = &joined.text;
    let doc_sha = sub["sha256"].as_str().map(str::to_string);
    // Python: part = sub.get("filename", stem).rsplit(".", 1)[0]
    let base = sub["filename"].as_str().unwrap_or(&stem);
    let part = base
        .rsplit_once('.')
        .map_or(base.to_string(), |(a, _)| a.to_string());

    let mut tier: Option<String> = None;
    let mut claims: Vec<Value> = match shim_call(
        python,
        shim,
        "propose",
        &json!({"part": part, "text": text}),
    ) {
        Some(mut out) => {
            let t = out["tier"].take();
            tier = t.as_str().map(str::to_string);
            out["claims"].as_array().cloned().unwrap_or_default()
        }
        None => Vec::new(),
    };
    if claims.is_empty() {
        return json!({"stem": stem, "skip": "no_claims"});
    }

    for _ in 0..REPAIR_ROUNDS {
        let bad: Vec<usize> = claims
            .iter()
            .enumerate()
            .filter(|(_, c)| c.is_object() && !c["value"].is_null() && !quick_ok(c, text))
            .map(|(i, _)| i)
            .collect();
        if bad.is_empty() {
            break;
        }
        if let Some(out) = shim_call(
            python,
            shim,
            "repair",
            &json!({"bad": bad, "claims": claims, "text": text}),
        ) {
            if let Some(fixed) = out["claims"].as_array() {
                claims.clone_from(fixed);
            }
        }
    }

    let (p0, supported, surviving_len, surviving) =
        verify_and_survive(&claims, text, &joined.spans, doc_sha.as_deref());

    json!({
        "stem": stem,
        "tier": tier,
        "claims": claims.len(),
        "surviving": surviving_len,
        "p0": p0,
        "supported": supported,
        "claim_data": surviving,
    })
}

fn verify_and_survive(
    claims: &[Value],
    text: &str,
    spans: &[(usize, usize, String)],
    doc_sha: Option<&str>,
) -> (usize, usize, usize, Vec<Value>) {
    let mut verdicts: Vec<(usize, bool)> = Vec::new(); // (idx, unsupported)
    for (i, c) in claims.iter().enumerate() {
        if !c.is_object() || c["value"].is_null() {
            continue;
        }
        let quote = c["quote"].as_str().unwrap_or("");
        if quote.is_empty() {
            verdicts.push((i, true));
            continue;
        }
        let field = alias_field(c["symbol"].as_str().unwrap_or(""));
        let mut ok = find_quote(quote, text, None, field) && value_in_quote(c.get("value"), quote);
        if ok
            && (!c["column_header"].is_null() || !c["row_header"].is_null())
            && column_position_binds(c["column_header"].as_str(), quote, text, value_f64(c))
                == Some(false)
        {
            ok = false;
        }
        verdicts.push((i, !ok));
    }

    let p0_idx: Vec<usize> = verdicts
        .iter()
        .filter(|(_, unsupported)| *unsupported)
        .map(|(i, _)| *i)
        .collect();
    let mut surviving = Vec::new();
    for (i, c) in claims.iter().enumerate() {
        if !c.is_object() || c["value"].is_null() || p0_idx.contains(&i) {
            continue;
        }
        let mut c = c.clone();
        c["page"] = match page_for(c["quote"].as_str().unwrap_or(""), text, spans) {
            Some(p) => Value::String(p),
            None => Value::Null,
        };
        match doc_sha {
            Some(sha) => c["doc_sha256"] = Value::String(sha.to_string()),
            None => c["doc_sha256"] = Value::Null,
        }
        surviving.push(c);
    }
    let supported = verdicts.iter().filter(|(_, u)| !u).count();
    (p0_idx.len(), supported, surviving.len(), surviving)
}

// ── worker ──────────────────────────────────────────────────────────────

fn worker(args: &[String]) -> i32 {
    let state_dir = PathBuf::from(need(args, "--state"));
    let node = need(args, "--node");
    let shim = need(args, "--shim");
    let python = flag(args, "--python").unwrap_or_else(|| "python3".into());
    let lease_secs = flag_usize(args, "--lease-secs", 1800);
    let max_jobs = flag_usize(args, "--max-jobs", usize::MAX);
    let adopt = args.iter().any(|a| a == "--adopt");

    let state = match FactoryState::open(&state_dir) {
        Ok(s) => s,
        Err(e) => {
            eprintln!("open state: {e}");
            return 1;
        }
    };
    let mut jobs = 0usize;
    while jobs < max_jobs {
        let lease = match state.claim_chunk(&node, lease_secs as i64, None) {
            Ok(Some(l)) => l,
            Ok(None) => {
                println!("[{node}] no queued chunk — done");
                break;
            }
            Err(e) => {
                eprintln!("[{node}] claim error: {e}");
                return 1;
            }
        };
        println!(
            "[{node}] claimed {} (offset {} x{}) attempt {}",
            lease.chunk_id, lease.offset, lease.item_count, lease.attempt
        );
        let manifest_path = lease.output_directory.join("manifest.json");
        if adopt && manifest_path.exists() {
            match state.complete_chunk(&lease, None) {
                Ok(sha) => {
                    println!("[{node}] adopted completed output {sha}");
                    jobs += 1;
                    continue;
                }
                Err(e) => eprintln!("[{node}] adopt failed: {e}"),
            }
        }
        std::fs::create_dir_all(&lease.output_directory).expect("chunk dir");
        let queue: Value =
            serde_json::from_slice(&std::fs::read(&lease.queue_path).expect("read queue"))
                .expect("parse queue");
        let work = queue["work"].as_array().cloned().unwrap_or_default();
        let items = &work[lease.offset as usize..(lease.offset + lease.item_count) as usize];

        let started = Instant::now();
        let mut ok = 0usize;
        let mut skipped = 0usize;
        for item in items {
            let result = burn_doc_parity(item, &python, &shim);
            let stem = result["stem"].as_str().unwrap_or("unknown").to_string();
            let path = lease.output_directory.join(format!("{stem}.json"));
            let mut file = std::fs::File::create(&path).expect("doc output");
            serde_json::to_writer(&mut file, &result).expect("write doc output");
            if result.get("skip").is_some() {
                skipped += 1;
            } else {
                ok += 1;
            }
            // renew per document: a chunk of N docs outlives any sane
            // lease, and an expired-but-live worker is how double-claims
            // happen (measured in the pilot rehearsal 2026-10-15)
            if state.renew_chunk(&lease, lease_secs as i64, None).is_err() {
                eprintln!(
                    "[{node}] lease lost mid-chunk (chunk {} attempt {}) — aborting for recovery",
                    lease.chunk_id, lease.attempt
                );
                return 1;
            }
        }
        let elapsed = started.elapsed().as_secs_f64();

        let manifest = json!({
            "schema": MANIFEST_SCHEMA,
            "sources": {
                "queue_sha256": lease.queue_sha256,
                "shim": shim,
            },
            "selection": {
                "offset": lease.offset,
                "work_items": lease.item_count,
            },
            "docs": {"ok": ok, "skipped": skipped},
            "elapsed_s": elapsed,
            "node": node,
        });
        let mut file = std::fs::File::create(&manifest_path).expect("manifest");
        serde_json::to_writer(&mut file, &manifest).expect("write manifest");

        match state.complete_chunk(&lease, None) {
            Ok(sha) => {
                println!(
                    "[{node}] completed {} in {elapsed:.1}s ({ok} ok, {skipped} skipped) {sha}",
                    lease.chunk_id
                );
            }
            Err(e) => {
                eprintln!("[{node}] complete failed: {e} — failing chunk for retry");
                let _ = state.fail_chunk(&lease, "complete_verification_failed", false, 0, None);
                return 1;
            }
        }
        jobs += 1;
    }
    0
}

fn status(args: &[String]) -> i32 {
    let state_dir = PathBuf::from(need(args, "--state"));
    let state = FactoryState::open(&state_dir).expect("open state");
    println!(
        "{}",
        serde_json::to_string_pretty(&state.status().expect("status")).expect("serialize")
    );
    0
}

fn recover(args: &[String]) -> i32 {
    let state_dir = PathBuf::from(need(args, "--state"));
    let state = FactoryState::open(&state_dir).expect("open state");
    let recovered = state.recover_expired(None).expect("recover");
    println!("recovered {} expired leases", recovered.len());
    0
}

// ── small utilities ─────────────────────────────────────────────────────

fn chrono_free_now() -> String {
    // controller timestamps carry their own civil-date engine; the queue's
    // created_at only needs ISO-Z for provenance
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_secs());
    let days = secs / 86_400;
    let (y, m, d) = civil_from_days(days as i64);
    let rem = secs % 86_400;
    format!(
        "{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z",
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    )
}

fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (if m <= 2 { y + 1 } else { y }, m, d)
}

fn exit_code(code: i32) -> std::process::ExitCode {
    if code == 0 {
        std::process::ExitCode::SUCCESS
    } else {
        std::process::ExitCode::FAILURE
    }
}

fn main() -> std::process::ExitCode {
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("enqueue") => exit_code(enqueue(&args[2..])),
        Some("worker") => exit_code(worker(&args[2..])),
        Some("status") => exit_code(status(&args[2..])),
        Some("recover") => exit_code(recover(&args[2..])),
        _ => usage(),
    }
}
