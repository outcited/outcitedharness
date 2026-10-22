//! r2 release-store integration tests: fail-closed mount, exact-identity
//! joins, real-evidence-only resolution, release swap, and the wire contract
//! with structural facts. Fixtures are built in tempdirs so the tests are
//! self-contained (the NAS unmounts; tests must not depend on it).

use std::fs;
use std::path::PathBuf;

use dw_api::release::{MountError, ReleaseStore};
use dw_api::routes::{handle, Request};
use dw_api::ApiStore;

fn tmpdir(tag: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("dw-api-r2-test-{tag}-{}", std::process::id()));
    let _ = fs::remove_dir_all(&d);
    fs::create_dir_all(&d).expect("tempdir");
    d
}

/// Minimal PNG bytes (1×1, black). A real PNG so content-hashing behaves.
fn tiny_png() -> Vec<u8> {
    vec![
        0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, b'I', b'H', b'D',
        b'R', 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x06, 0x00, 0x00, 0x00,
        0x1f, 0x15, 0xc4, 0x89, 0x00, 0x00, 0x00, 0x0d, b'I', b'D', b'A', b'T', 0x78, 0x9c,
        0x62, 0x60, 0x01, 0x00, 0x00, 0x00, 0xff, 0xff, 0x03, 0x00, 0x06, 0x00, 0x05, 0x57,
        0xbf, 0xab, 0xd4, 0x00, 0x00, 0x00, 0x00, b'I', b'E', b'N', b'D', 0xae, 0x42, 0x60,
        0x82,
    ]
}

fn sha256_hex_local(bytes: &[u8]) -> String {
    // same arithmetic as the store; duplicated in test so a store bug cannot
    // hide behind sharing the implementation
    use std::fmt::Write as _;
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
        0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
        0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
        0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
        0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
        0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2,
    ];
    let mut h: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
        0x5be0cd19,
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
        for (i, c) in block.chunks(4).enumerate() {
            w[i] = u32::from_be_bytes([c[0], c[1], c[2], c[3]]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16].wrapping_add(s0).wrapping_add(w[i - 7]).wrapping_add(s1);
        }
        let (mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut hh) =
            (h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7]);
        for (i, k) in K.iter().enumerate() {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ ((!e) & g);
            let t1 = hh.wrapping_add(s1).wrapping_add(ch).wrapping_add(*k).wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            hh = g; g = f; f = e; e = d.wrapping_add(t1);
            d = c; c = b; b = a; a = t1.wrapping_add(t2);
        }
        for (i, v) in [a, b, c, d, e, f, g, hh].into_iter().enumerate() {
            h[i] = h[i].wrapping_add(v);
        }
    }
    let mut s = String::new();
    for x in h {
        let _ = write!(s, "{x:08x}");
    }
    s
}

const DOC: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

struct ReleaseDir {
    root: PathBuf,
}

impl ReleaseDir {
    fn build(tag: &str, release_id: &str) -> Self {
        let root = tmpdir(tag);
        fs::write(
            root.join("RELEASE.json"),
            format!(r#"{{"release_id": "{release_id}", "previous": "designwins-spine-s6-staging-20261020 (PROMOTED)"}}"#),
        )
        .unwrap();
        let spine = serde_json::json!({
            "part_number": "LMR14030",
            "vendor": "texas_instruments",
            "claims": [{
                "field": "mode_idle",
                "value": 0.029,
                "unit": "ma",
                "rating_class": "typical",
                "role": "typ",
                "conditions": "VIN=12V",
                "source": {"doc_sha256": DOC, "page": 3, "quote": "Iq 29 uA"}
            }]
        });
        fs::write(root.join("parts_spine.jsonl"), spine.to_string()).unwrap();
        let structural = serde_json::json!({
            "schema": "harness.spine.structural.v1",
            "part_number": "LMR14030",
            "pin_tables": [{
                "sha": DOC, "page": 3, "n_rows": 1,
                "rows": [{"name": "BOOT", "pin": "1", "quote": "1 BOOT"}]
            }],
            "pin_row_total": 1,
            "typical_curves": 2,
            "curve_pages": [{"sha": DOC, "page": 5, "n": 2}],
            "association_methods": ["pin:ds_vision"]
        });
        fs::write(root.join("parts_structural.jsonl"), structural.to_string()).unwrap();
        let mut hashes = String::new();
        for name in ["parts_spine.jsonl", "parts_structural.jsonl"] {
            let bytes = fs::read(root.join(name)).unwrap();
            hashes.push_str(&format!("{}  {}\n", sha256_hex_local(&bytes), name));
        }
        fs::write(root.join("CONTENT_HASHES.sha256"), hashes).unwrap();
        Self { root }
    }

    fn with_evidence(self, doc: &str, page: u32) -> Self {
        let ev = self.root.join("evidence").join("pages").join(&doc[..2]);
        fs::create_dir_all(&ev).unwrap();
        fs::write(ev.join(format!("{doc}-{page}.png")), tiny_png()).unwrap();
        self
    }
}

fn store_with_evidence(rel: &ReleaseDir) -> ReleaseStore {
    ReleaseStore::open_with_evidence(&rel.root, Some(rel.root.join("evidence"))).expect("mount")
}

#[test]
fn mount_verifies_hashes_and_fails_closed_on_tamper() {
    let rel = ReleaseDir::build("tamper", "designwins-spine-s6-r2-test");
    let store = ReleaseStore::open(&rel.root);
    assert!(store.is_ok(), "clean release mounts");

    // tamper: change a byte after hashing
    let feed = rel.root.join("parts_spine.jsonl");
    let mut text = fs::read_to_string(&feed).unwrap();
    text.push(' ');
    fs::write(&feed, text).unwrap();
    let err: MountError = ReleaseStore::open(&rel.root).expect_err("tampered mount must fail");
    assert!(err.reason.contains("hash mismatch"), "named offense: {}", err.reason);
}

#[test]
fn missing_release_json_is_named_not_vague() {
    let rel = ReleaseDir::build("nojson", "designwins-spine-s6-r2-test");
    fs::remove_file(rel.root.join("RELEASE.json")).unwrap();
    let err = ReleaseStore::open(&rel.root).expect_err("no RELEASE.json");
    assert!(err.reason.contains("RELEASE.json"), "{}", err.reason);
}

#[test]
fn real_release_id_and_lineage_never_demo() {
    let rel = ReleaseDir::build("identity", "designwins-spine-s6-r2-20261021");
    let store = ReleaseStore::open(&rel.root).expect("mount");
    assert_eq!(store.release_id().as_str(), "designwins-spine-s6-r2-20261021");
    let lineage = store.lineage();
    assert_eq!(
        lineage.parent.as_ref().map(|p| p.as_str()),
        Some("designwins-spine-s6-staging-20261020")
    );
    assert!(!store.release_id().as_str().contains("demo"));
}

#[test]
fn part_joins_structural_on_exact_identity_only() {
    let rel = ReleaseDir::build("join", "designwins-spine-s6-r2-test");
    let store = ReleaseStore::open(&rel.root).expect("mount");

    let part = store.part("LMR14030").expect("exact part");
    let structural = part.structural.as_ref().expect("structural joined");
    assert_eq!(structural.pin_row_total, 1);
    assert_eq!(structural.typical_curves.count, 2);
    assert_eq!(structural.pin_tables[0].page, Some(3));
    // claims preserved with conditions and rating class
    let claim = &part.claims[0];
    assert_eq!(claim.conditions.as_deref(), Some("VIN=12V"));
    assert_eq!(claim.rating_class.as_deref(), Some("typical"));
    assert_eq!(claim.value, "0.029");

    // case-variant lookalikes never join structural facts
    let lower = store.part("lmr14030");
    assert!(lower.is_none(), "facade join is exact, not case-folded");
}

#[test]
fn evidence_resolves_only_when_render_bytes_exist() {
    let rel = ReleaseDir::build("evidence", "designwins-spine-s6-r2-test").with_evidence(DOC, 3);
    let store = store_with_evidence(&rel);

    // page 3 has real bytes -> resolves with the RENDER's content hash
    let source = dw_api::store::ClaimSource {
        doc_sha256: Some(DOC.into()),
        sha: None,
        page: Some(3),
        row_header: None,
        column_header: None,
        crop: None,
        source_pdf: None,
        provenance_tier: None,
    };
    let ev = store.resolve_evidence("mode_idle", &source).expect("real render resolves");
    assert_eq!(ev.page_render.sha256.as_str(), &sha256_hex_local(&tiny_png()));

    // page 7 has no bytes -> honest absence, no synthetic hash
    let absent = dw_api::store::ClaimSource { page: Some(7), ..source };
    assert!(store.resolve_evidence("mode_idle", &absent).is_none());
}

#[test]
fn wrong_release_query_is_refused_by_the_routes() {
    let rel = ReleaseDir::build("wrongrel", "designwins-spine-s6-r2-20261021");
    let store = ReleaseStore::open(&rel.root).expect("mount");
    let req = Request {
        method: "POST".into(),
        path: "/api/search/v1/query".into(),
        body: Some(serde_json::json!({"raw": "3.3v ldo", "release": "rel-demo-20261011"})),
    };
    let problem = handle(&req, &store).expect_err("wrong release refused");
    assert_eq!(problem.status, 404);
}

#[test]
fn release_swap_serves_the_new_identity_no_stale_cache() {
    let r1 = ReleaseDir::build("swap1", "designwins-spine-s6-r1-test");
    let r2 = ReleaseDir::build("swap2", "designwins-spine-s6-r2-test");
    let s1 = ReleaseStore::open(&r1.root).expect("mount r1");
    let s2 = ReleaseStore::open(&r2.root).expect("mount r2");
    assert_ne!(s1.release_id().as_str(), s2.release_id().as_str());
    // fresh mounts never observe each other's identity
    assert_eq!(s2.release_id().as_str(), "designwins-spine-s6-r2-test");
}

#[test]
fn part_route_serves_claims_structural_and_honest_evidence() {
    let rel = ReleaseDir::build("wire", "designwins-spine-s6-r2-20261021")
        .with_evidence(DOC, 3);
    let store = store_with_evidence(&rel);
    let req = Request {
        method: "GET".into(),
        path: "/api/extraction/v1/parts/LMR14030".into(),
        body: None,
    };
    let resp = handle(&req, &store).expect("part serves");
    let json = resp.to_json();
    let part = json.get("part").expect("part block");
    assert_eq!(part["part_number"], "LMR14030");
    assert_eq!(part["claims"][0]["conditions"], "VIN=12V");
    let structural = part["structural"].as_object().expect("structural on the wire");
    assert_eq!(structural["pin_row_total"], 1);
    assert_eq!(structural["typical_curves"]["count"], 2);
    // the claim's evidence resolves because page 3 has real bytes
    let feed = json.get("evidence").and_then(|e| e.as_array()).expect("feed evidence");
    assert!(feed.iter().any(|e| e["field"] == "mode_idle" && e["page_render"]["sha256"].is_string()));
    // honest unknowns: structural rows with no render byte simply have no
    // feed entry — verified by the absence of any page-5 evidence entry
    assert!(!feed.iter().any(|e| e["page"] == 5));
}
