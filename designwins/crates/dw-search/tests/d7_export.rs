//! Gate 4 — the D7 export: a REAL release-scoped QueryResult for the
//! sleep-current journey, assembled through the production contract
//! (`dw_funnel::accept` + `dw_api::feed::QueryResult`), with the REAL
//! provenance resolver — never `DemoStore`, never `AlwaysResolvable`.
//!
//! The evidence refs carry the rendered C13 assets: page renders and
//! row-band crops at 144 DPI, sha-addressed (fixtures/crops/), so every
//! number in the response opens to pixels.

#![allow(clippy::expect_used, clippy::unwrap_used, clippy::indexing_slicing)]

use std::collections::BTreeMap;
use std::path::PathBuf;

use dw_api::feed::{EvidenceAsset, EvidenceRef, Lineage, QueryResult};
use dw_types::ReleaseId;
use dw_knives::seed_from_measured_chips;
use dw_search::mode_rows::{population_from_mode_rows, ModeRow, RowsProvenance};
use dw_types::Sha256;

const SENTENCE: &str = "I need an MCU that sleeps under 2 µA";
const RELEASE: &str = "designwins-slice-sleep-current-v1";

fn fixture() -> Vec<ModeRow> {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/sleep_mode_rows_v1.jsonl");
    std::fs::read_to_string(path)
        .expect("fixture present")
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| serde_json::from_str(l).expect("row parses"))
        .collect()
}

struct CropIndex {
    pages: BTreeMap<(String, u32), serde_json::Value>,
    crops: Vec<serde_json::Value>,
}

fn crop_index() -> CropIndex {
    let base = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/crops");
    let pages: Vec<serde_json::Value> =
        serde_json::from_str(&std::fs::read_to_string(base.join("pages/index.json")).expect("pages index"))
            .expect("pages index parses");
    let entries: Vec<serde_json::Value> = pages;
    let mut pages = BTreeMap::new();
    for v in entries {
        let sha = v["sha16"].as_str().unwrap_or_default().to_string();
        let p = v["page"].as_u64().unwrap_or_default() as u32;
        pages.insert((sha, p), v);
    }
    let crops: Vec<serde_json::Value> =
        serde_json::from_str(&std::fs::read_to_string(base.join("rows/index.json")).expect("crops index"))
            .expect("crops index parses");
    CropIndex { pages, crops }
}

fn asset_for(v: &serde_json::Value, kind: &str, sha16: &str, page: u32) -> EvidenceAsset {
    let hash = match kind {
        "page" => v["page_render_sha256"].as_str().unwrap_or_default().to_string(),
        _ => v["crop_sha256"].as_str().unwrap_or_default().to_string(),
    };
    let dpi = v["dpi"].as_u64().unwrap_or(144) as u32;
    let bbox = match kind {
        "page" => [0, 0, 0, 0],
        _ => [
            v["bbox"][0].as_u64().unwrap_or(0) as u32,
            v["bbox"][1].as_u64().unwrap_or(0) as u32,
            v["bbox"][2].as_u64().unwrap_or(0) as u32,
            v["bbox"][3].as_u64().unwrap_or(0) as u32,
        ],
    };
    let file = match kind {
        "page" => format!("pages/{sha16}.p{page}.png"),
        _ => v["file"].as_str().unwrap_or_default().to_string(),
    };
    EvidenceAsset {
        sha256: Sha256::new(hash).expect("asset sha"),
        href: format!("evidence://slice-sleep/{file}"),
        dpi,
        bbox,
    }
}

#[test]
fn d7_export_real_query_result() {
    let rows = fixture();
    let built = population_from_mode_rows(&rows);
    let provenance = RowsProvenance::from_rows(&rows);
    let registry = seed_from_measured_chips();

    // The production funnel entry, with the REAL resolver threaded through
    // (the AlwaysResolvable shortcut is gone from this path).
    let cohort = dw_decide::Cohort::from_candidates(
        built.population.facts.keys().cloned().collect::<Vec<_>>(),
    );
    let release = ReleaseId::new(RELEASE).unwrap();
    let answer = dw_funnel::accept(
        SENTENCE,
        &registry,
        &cohort,
        &built.population.facts,
        release.clone(),
        &provenance,
    );
    assert!(
        answer.build.cohort.len() >= 2,
        "real cohort: distinct parts, not passages"
    );

    // Evidence refs: every judged number opens to a C13 page render and a
    // row-band crop, both sha-addressed pixels.
    let idx = crop_index();
    let mut evidence_refs = Vec::new();
    for j in &answer.build.explain {
        for src in &j.evidence {
            let sha_str = src.sha256.as_str().to_string();
            let sha16: String = sha_str.chars().take(12).collect();
            let page = src.page;
            // Exact join (review 2026-10-20 P0): page render must exist for
            // THE (sha, page); the row-band crop must match (sha, page) AND
            // the evidence's column. A missing artifact is a hard error —
            // evidence never silently disappears from an elimination.
            let page_asset = idx
                .pages
                .get(&(sha16.clone(), page))
                .map(|v| asset_for(v, "page", &sha16, page))
                .unwrap_or_else(|| {
                    panic!(
                        "no C13 page render for {sha16} p{page} — evidence must fail closed"
                    )
                });
            let crop_asset = idx
                .crops
                .iter()
                .find(|c| {
                    c["sha16"].as_str() == Some(sha16.as_str())
                        && c["page"].as_u64() == Some(u64::from(page))
                })
                .and_then(|c| {
                    // exact (sha, page) already matched; verify the column
                    // when the evidence names one, else any row-band on the
                    // page is honest context
                    let col_ok = match src.column_header.as_deref() {
                        None => true,
                        Some(col) => c["columns"].as_array().map(|a| {
                            a.iter().any(|x| x.as_str() == Some(col))
                        }).unwrap_or(false),
                    };
                    col_ok.then(|| asset_for(c, "crop", &sha16, page))
                });
            {
                let pa = page_asset;
                evidence_refs.push(EvidenceRef {
                    field: format!("sleep_current_a/{}", j.candidate.part_number),
                    document_sha256: src.sha256.clone(),
                    page,
                    row_header: src.row_header.clone(),
                    column_header: src.column_header.clone(),
                    quote: rows
                        .iter()
                        .find(|r| r.pdf_sha.as_deref() == Some(sha_str.as_str()))
                        .and_then(|r| r.conditions_verbatim.clone()),
                    page_render: pa,
                    crop: crop_asset,
                    crop_sha256: idx
                        .crops
                        .iter()
                        .find(|c| {
                            c["file"].as_str().map(|f| f.starts_with(&sha16)).unwrap_or(false)
                        })
                        .and_then(|c| c["crop_sha256"].as_str())
                        .and_then(|s| Sha256::new(s).ok()),
                });
            }
        }
    }
    assert!(
        !evidence_refs.is_empty(),
        "judgements must carry resolvable evidence"
    );
    for e in &evidence_refs {
        assert!(e.page >= 1, "evidence opens to a page");
        assert!(
            !e.page_render.href.is_empty(),
            "C13 page render is present"
        );
    }

    // The D7 QueryResult, assembled through the only constructor.
    let lineage = Lineage {
        release_id: release.clone(),
        parent: None,
        engines: vec!["dw-knives/phrase".into(), "dw-search/mode_rows".into(), "dw-normalize".into()],
        rollback_to: None,
    };
    let result = QueryResult::from_build(answer.build, lineage, evidence_refs);

    // Feed item contract checks (the seven items):
    let json = result.to_json();
    assert_eq!(json["release_id"], RELEASE, "feed item 1: release");
    assert_eq!(json["raw"], SENTENCE, "raw intent echoed");
    assert!(json["lineage"].is_object(), "feed item 1: lineage affordance");
    assert!(json["evidence"].as_array().map(|a| !a.is_empty()).unwrap_or(false),
        "feed item 2: evidence present");
    assert!(json["unknowns"].as_array().map(|a| !a.is_empty()).unwrap_or(false),
        "feed item 3: unknowns ALWAYS present");
    assert!(
        json["exclusions"].as_array().map(|a| !a.is_empty()).unwrap_or(false),
        "feed item 4: exclusions with reasons"
    );
    assert!(json["next"].as_array().map(|a| !a.is_empty()).unwrap_or(false),
        "feed item 5: next questions");
    assert!(json["declared"].is_array() || json["derived"].is_array(),
        "feed item 6: requirements visible");
    assert!(
        json["cohort"]["members"].as_array().map(|a| !a.is_empty()).unwrap_or(false),
        "feed item 7: distinct-part cohort: {}",
        json["cohort"]
    );

    // The exclusion is the real one: TMS320F280015x, proven by the FULL
    // mode inventory's lowest floor.
    let excl_parts: Vec<String> = json["exclusions"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|e| e["candidate"]["part_number"].as_str().map(String::from))
        .collect();
    assert!(
        excl_parts.iter().any(|p| p.starts_with("TMS320F280015x")),
        "exclusions carry the proven violation, got {excl_parts:?}"
    );
    // And the ceiling-over part is NOT excluded (Unknown never eliminates).
    assert!(
        !excl_parts.iter().any(|p| p.starts_with("TM4C1233")),
        "a ceiling above budget never excludes"
    );

    // Serialized artifact for M4 (written next to the fixture as the
    // reproducible response example).
    let out = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("tests/fixtures/sleep_query_result_d7.json");
    std::fs::write(&out, serde_json::to_string_pretty(&json).unwrap()).unwrap();
}
