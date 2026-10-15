//! Tests for the additive soak queue/manifest schemas (M5 Rust factory
//! soak, 2026-10-14). The structural schemas and the 43-step gold battery
//! are unchanged — these pin the ADDITION, including its fail-closed edges.

// House pattern: test modules carry a documented allow (ASSURANCE.md).
#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic,
    clippy::needless_pass_by_value,
    reason = "tests: fail loudly on fixture breakage"
)]

use std::path::Path;

use dw_controller::FactoryState;
use serde_json::{json, Value};

fn canonical_core(value: &Value) -> Vec<u8> {
    // the SAME canonical writer load_queue hashes with — NOT Value::to_string
    // (preserve_order-sensitive under workspace feature unification)
    let mut core = serde_json::Map::new();
    if let Some(map) = value.as_object() {
        for (key, item) in map {
            if key != "created_at" && key != "evidence_sha256" {
                core.insert(key.clone(), item.clone());
            }
        }
    }
    dw_extract::canonical::canonical_json(&Value::Object(core)).unwrap()
}

fn write_soak_queue(dir: &Path, name: &str, work: Value, evidence_ok: bool) -> std::path::PathBuf {
    let mut queue = json!({
        "schema": "harness.electronics-soak-work.v1",
        "policy": {"pipeline": "test"},
        "work": work,
        "created_at": "2026-10-15T00:00:00Z",
    });
    let evidence = dw_extract::sha256::sha256_hex(&canonical_core(&queue));
    let evidence = if evidence_ok {
        evidence
    } else {
        format!("{}0", &evidence[..63])
    };
    queue["evidence_sha256"] = Value::String(evidence);
    let path = dir.join(name);
    std::fs::write(&path, queue.to_string()).unwrap();
    path
}

fn write_manifest(dir: &Path, queue_sha: &str, offset: i64, items: i64, schema: &str) {
    let manifest = json!({
        "schema": schema,
        "sources": {"queue_sha256": queue_sha},
        "selection": {"offset": offset, "work_items": items},
    });
    std::fs::create_dir_all(dir).unwrap();
    std::fs::write(dir.join("manifest.json"), manifest.to_string()).unwrap();
}

#[test]
fn soak_queue_registers_and_chunks_seal() {
    let root = tempfile_dir("soak_register");
    let state = FactoryState::open(&root).unwrap();
    let queue_path = write_soak_queue(
        &root,
        "queue-good.json",
        json!([{"work_id": "a", "stem": "a", "pdf": "/tmp/a.pdf", "substrate": null},
               {"work_id": "b", "stem": "b", "pdf": "/tmp/b.pdf", "substrate": null}]),
        true,
    );
    let out_root = root.join("out");
    std::fs::create_dir_all(&out_root).unwrap();
    let chunks = state
        .register_queue(&queue_path, &out_root, 1, 3, 0, None)
        .unwrap();
    assert_eq!(chunks.len(), 2);

    let lease = state.claim_chunk("node-1", 60, None).unwrap().unwrap();
    write_manifest(
        &lease.output_directory,
        &lease.queue_sha256,
        lease.offset,
        lease.item_count,
        "harness.electronics-soak-extraction.v1",
    );
    let sha = state.complete_chunk(&lease, None).unwrap();
    assert_eq!(sha.len(), 64);
}

#[test]
fn soak_queue_with_wrong_evidence_is_refused() {
    let root = tempfile_dir("soak_bad_evidence");
    let state = FactoryState::open(&root).unwrap();
    let queue_path = write_soak_queue(
        &root,
        "queue-bad.json",
        json!([{"work_id": "a", "stem": "a", "pdf": "/tmp/a.pdf", "substrate": null}]),
        false,
    );
    let out_root = root.join("out");
    std::fs::create_dir_all(&out_root).unwrap();
    let err = state
        .register_queue(&queue_path, &out_root, 1, 3, 0, None)
        .unwrap_err();
    assert!(err.to_string().contains("evidence is invalid"));
}

#[test]
fn unknown_queue_schema_is_still_refused() {
    let root = tempfile_dir("soak_unknown_schema");
    let state = FactoryState::open(&root).unwrap();
    let queue = json!({
        "schema": "harness.electronics-invented-work.v1",
        "work": [{"work_id": "a"}],
        "evidence_sha256": "0".repeat(64),
    });
    let path = root.join("queue-unknown.json");
    std::fs::write(&path, queue.to_string()).unwrap();
    let out_root = root.join("out");
    std::fs::create_dir_all(&out_root).unwrap();
    let err = state
        .register_queue(&path, &out_root, 1, 3, 0, None)
        .unwrap_err();
    assert!(err
        .to_string()
        .contains("unsupported structural work queue"));
}

#[test]
fn soak_manifest_with_wrong_queue_sha_is_refused() {
    let root = tempfile_dir("soak_bad_manifest");
    let state = FactoryState::open(&root).unwrap();
    let queue_path = write_soak_queue(
        &root,
        "queue-m.json",
        json!([{"work_id": "a", "stem": "a", "pdf": "/tmp/a.pdf", "substrate": null}]),
        true,
    );
    let out_root = root.join("out");
    std::fs::create_dir_all(&out_root).unwrap();
    state
        .register_queue(&queue_path, &out_root, 1, 3, 0, None)
        .unwrap();
    let lease = state.claim_chunk("node-1", 60, None).unwrap().unwrap();
    write_manifest(
        &lease.output_directory,
        "deadbeef",
        lease.offset,
        lease.item_count,
        "harness.electronics-soak-extraction.v1",
    );
    let err = state.complete_chunk(&lease, None).unwrap_err();
    assert!(err.to_string().contains("different queue"));
}

#[test]
fn structural_manifest_still_pins_its_own_key() {
    // regression: the structural manifest keeps its
    // sources.structural_queue_sha256 contract unchanged
    let root = tempfile_dir("soak_structural_key");
    let state = FactoryState::open(&root).unwrap();
    let queue_path = write_soak_queue(
        &root,
        "queue-s.json",
        json!([{"work_id": "a", "stem": "a", "pdf": "/tmp/a.pdf", "substrate": null}]),
        true,
    );
    let out_root = root.join("out");
    std::fs::create_dir_all(&out_root).unwrap();
    state
        .register_queue(&queue_path, &out_root, 1, 3, 0, None)
        .unwrap();
    let lease = state.claim_chunk("node-1", 60, None).unwrap().unwrap();
    let manifest = json!({
        "schema": "harness.electronics-structural-local-extraction.v1",
        "sources": {"queue_sha256": lease.queue_sha256.clone()},
        "selection": {"offset": lease.offset, "work_items": lease.item_count},
    });
    std::fs::create_dir_all(&lease.output_directory).unwrap();
    std::fs::write(
        lease.output_directory.join("manifest.json"),
        manifest.to_string(),
    )
    .unwrap();
    // the structural schema ignores queue_sha256 — the structural key is
    // absent, so the seal must refuse
    let err = state.complete_chunk(&lease, None).unwrap_err();
    assert!(err.to_string().contains("different queue"));
}

fn tempfile_dir(name: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("dw_soak_test_{name}_{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}
