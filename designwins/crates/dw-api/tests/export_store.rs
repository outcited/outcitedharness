//! The export loader: `part()` walks `parts_*.jsonl`, `cube()` reads
//! `cube.json`, and nothing is invented where the corpus is silent.
//!
//! Fixtures use the **exact wire shapes** `tools/export_api_store.py` emits,
//! including the two feed conventions (catalog: document in `sha`, page
//! `null`; family: verbatim hash in `sha`, document in `doc_sha256`).

#![allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    reason = "tests assert on their own fixtures"
)]

use std::path::PathBuf;

use dw_api::{handle, ApiStore, ClaimSource, ExportStore, Request};

fn fixture_export(name: &str) -> PathBuf {
    let root =
        std::env::temp_dir().join(format!("dw-api-export-test-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(&root).unwrap();
    let catalog = concat!(
        r#"{"part_number": "2N7002L", "vendor": "unknown", "claims": [{"field": "vds", "value": "6", "quote": "6", "source": {"column_header": "MAX", "crop": null, "page": null, "row_header": "VDS", "sha": "5fb91e241c17fb24d4bfdd8d9f77f5bc4a9d55c1358d00cc605b85450e082c99", "doc_sha256": null}}]}"#,
        "\n",
        "not json at all\n",
        concat!(
            r#"{"part_number": "DUP", "vendor": "catalog", "claims": []}"#,
            "\n"
        ),
    );
    std::fs::write(root.join("parts_catalog.jsonl"), catalog).unwrap();
    let family = concat!(
        concat!(
            r#"{"part_number": "AURIX-TC212", "vendor": "infineon.com", "claims": [{"field": "DMA", "value": "16", "quote": "16-Channel DMA", "source": {"column_header": null, "crop": null, "page": 6, "row_header": "peripherals", "sha": "8eee8f50b217cc722fc12e6f6e9a047e522b805d7d5961064092e47cbe86fc8d", "doc_sha256": "0f2c2f5aee4dc6bd81b0e0b210de18a4a35b2a22a89f2f32d4ce7d9c840489be"}}]}"#,
            "\n"
        ),
        concat!(
            r#"{"part_number": "DUP", "vendor": "family", "claims": []}"#,
            "\n"
        ),
    );
    std::fs::write(root.join("parts_family.jsonl"), family).unwrap();
    root
}

#[test]
fn part_walks_the_feeds_and_404s_honestly() {
    let root = fixture_export("walk");
    let store = ExportStore::open(&root);

    let cat = store.part("2N7002L").expect("catalog part found");
    assert_eq!(cat.vendor, "unknown");
    assert_eq!(cat.claims[0].value, "6");
    assert_eq!(
        cat.claims[0].source.page, None,
        "catalog feed prints no page"
    );

    let fam = store.part("AURIX-TC212").expect("family part found");
    assert_eq!(fam.claims[0].source.page, Some(6));
    assert_eq!(
        fam.claims[0].source.document(),
        Some("0f2c2f5aee4dc6bd81b0e0b210de18a4a35b2a22a89f2f32d4ce7d9c840489be")
    );

    // First match wins: catalog before family.
    assert_eq!(store.part("DUP").unwrap().vendor, "catalog");
    // A malformed line is skipped, not fatal.
    assert!(store.part("not json at all").is_none());
    // Unknown parts 404 — never a demo part wearing corpus clothes.
    assert!(store.part("NOPE").is_none());

    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn cube_reads_cube_json_and_picks_up_fresh_exports() {
    let root = fixture_export("cube");
    let store = ExportStore::open(&root);
    // Before cube.json exists the demo substrate answers — observably.
    assert_eq!(
        store.cube().cells[0].category,
        "pmic/ldo",
        "fallback demo cube"
    );
    std::fs::write(
        root.join("cube.json"),
        r#"{"dimensions": ["category", "aisle", "vendor"], "laned": true, "cells": [{"category": "power/semiconductor", "aisle": "catalog", "vendor": "unknown", "parts": 2, "claims": 3, "laned": true, "curves": false}]}"#,
    )
    .unwrap();
    let cube = store.cube();
    assert_eq!(cube.cells.len(), 1, "cube.json re-read per request");
    assert_eq!(cube.cells[0].parts, 2);
    assert_eq!(cube.cells[0].claims, 3);
    assert!(cube.laned);
    let _ = std::fs::remove_dir_all(&root);
}

/// Negative control — the resolution rule: a claim with no printed page has
/// **no** feed entry (never a link that 404s), and a claim with document +
/// page resolves to renderable evidence.
#[test]
fn negative_control_resolution_never_invents_a_page() {
    // Distinct fixture name from `part_walks_the_feeds_and_404s_honestly` —
    // both would otherwise share one temp dir and race its `remove_dir_all`.
    let root = fixture_export("resolve");
    let store = ExportStore::open(&root);

    let cat = store.part("2N7002L").unwrap();
    assert!(
        store
            .resolve_evidence("vds", &cat.claims[0].source)
            .is_none(),
        "catalog claim printed no page — nothing to open"
    );

    let fam = store.part("AURIX-TC212").unwrap();
    let ev = store
        .resolve_evidence("DMA", &fam.claims[0].source)
        .expect("family claim has document + page");
    assert_eq!(ev.page, 6);
    assert!(ev.page_render.href.starts_with("/evidence/pages/"));
    assert!(ev.crop_sha256.is_none(), "no crop printed, none invented");

    // The route keeps the audit honest: only resolvable evidence feeds.
    let req = Request {
        method: "GET".into(),
        path: "/api/extraction/v1/parts/2N7002L".into(),
        body: None,
    };
    let body = handle(&req, &store).expect("part route").to_json();
    assert_eq!(
        body["evidence"].as_array().unwrap().len(),
        0,
        "unresolvable claims must not appear as evidence links"
    );
    dw_api::FeedAudit::of(&body).expect("feed stays renderable");
    let _ = std::fs::remove_dir_all(&root);
}

/// The loose source wire shape parses the export's two conventions.
#[test]
fn claim_source_parses_both_feed_conventions() {
    let catalog: ClaimSource = serde_json::from_str(
        r#"{"column_header": "MAX", "crop": null, "page": null, "row_header": "VDS", "sha": "5fb91e241c17fb24d4bfdd8d9f77f5bc4a9d55c1358d00cc605b85450e082c99", "doc_sha256": null}"#,
    )
    .unwrap();
    assert_eq!(
        catalog.document(),
        Some("5fb91e241c17fb24d4bfdd8d9f77f5bc4a9d55c1358d00cc605b85450e082c99")
    );
    let family: ClaimSource = serde_json::from_str(
        r#"{"column_header": null, "crop": null, "page": 6, "row_header": "peripherals", "sha": "8eee8f50b217cc722fc12e6f6e9a047e522b805d7d5961064092e47cbe86fc8d", "doc_sha256": "0f2c2f5aee4dc6bd81b0e0b210de18a4a35b2a22a89f2f32d4ce7d9c840489be"}"#,
    )
    .unwrap();
    assert_eq!(
        family.document(),
        Some("0f2c2f5aee4dc6bd81b0e0b210de18a4a35b2a22a89f2f32d4ce7d9c840489be"),
        "the document is doc_sha256 when the feed carries one"
    );
}
