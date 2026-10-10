//! D1 G7 — path-segment parity with the Python reference engine.
//!
//! **What is being gated.** PLAN §8 claims vector curve extraction at precision
//! 1.000. That precision comes from reading path geometry. If PDFium does not
//! report the same geometry PyMuPDF reports, the ported extractor cannot
//! reproduce the gold curve points, and `dw-pdf` must fall back to PyMuPDF
//! running out-of-process (D1).
//!
//! **Why not compare to the gold curve points directly.** Those points are in
//! *data space*, after axis calibration and tick fitting. Comparing them to raw
//! PDF geometry would test the calibration code, not the engine. G7 is
//! therefore segment parity; gold parity is tested later, once `dw-knives`
//! curve knives are ported.
//!
//! **Reference side.** `tests/fixtures/gold/pdf_engine_segments_v1.json`,
//! emitted by `scripts/emit_pdf_engine_reference.py` from PyMuPDF 1.28.2 over
//! the in-repo corpus `tests/fixtures/gold/typical_curves/pdfs/`.
//!
//! **Bar (D1).**
//! * page geometry: exact (≤ 0.001 pt)
//! * vertex multiset: ≥ 99% of reference vertices matched within 2 px @ 144 DPI
//!   (1.0 pt) in both axes
//! * segment count: within 2%

//! `tests/fixtures/gold/pdf_engine_segments_v1.json` emitted by
//! `scripts/emit_pdf_engine_reference.py`.
//!
//! # Lints
//!
//! The workspace turns `clippy::pedantic` on for library code. This is a
//! numeric comparison harness: it indexes, casts, and asserts freely. Those
//! restrictions are valuable in `dw-pdf`'s library and are kept there; they are
//! noise here.

#![allow(
    clippy::indexing_slicing,
    clippy::cast_precision_loss,
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::panic,
    clippy::format_push_string,
    clippy::match_same_arms,
    clippy::doc_markdown,
    clippy::single_match_else,
    clippy::redundant_closure_for_method_calls,
    clippy::uninlined_format_args,
    clippy::doc_lazy_continuation,
    clippy::items_after_statements
)]

use std::collections::HashMap;
use std::path::PathBuf;

use dw_pdf::{PdfiumEngine, Pt, SegmentKind};
use serde::Deserialize;

/// D1 bar: 2 px at 144 DPI is 2 * 72 / 144 = 1.0 pt.
const TOLERANCE_PT: f64 = 1.0;
/// Vertex match bar.
const VERTEX_MATCH_FRACTION: f64 = 0.99;
/// Segment count agreement bar.
const SEGMENT_COUNT_TOLERANCE: f64 = 0.02;

#[derive(Debug, Deserialize)]
struct Fixture {
    pages: Vec<FixturePage>,
}

#[derive(Debug, Deserialize)]
struct FixturePage {
    document: String,
    page_1based: u32,
    page_width_pt: f64,
    page_height_pt: f64,
    drawing_count: usize,
    segment_count: usize,
    drawings: Vec<FixtureDrawing>,
}

#[derive(Debug, Deserialize)]
struct FixtureDrawing {
    /// MuPDF calls this `type` (`s` / `f` / `fs`).
    #[serde(rename = "type")]
    #[allow(dead_code)]
    kind: String,
    #[allow(dead_code)]
    rect: [f64; 4],
    items: Vec<FixtureItem>,
}

/// A MuPDF drawing item: `["l", x0,y0, x1,y1]`, `["c", x0,y0,x1,y1,x2,y2,x3,y3]`,
/// `["re", x0,y0,x1,y1]`, `["qu", x0,y0,x1,y1]`.
type FixtureItem = Vec<serde_json::Value>;

/// Walk up from the crate until we find the repo root that holds the gold
/// fixtures. `dw-pdf` lives at `designwins/crates/dw-pdf`, so the gold tree at
/// `tests/fixtures/gold` is three levels up — but searching is more robust than
/// counting, and keeps the test working if the workspace moves.
fn repo_root() -> PathBuf {
    let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    loop {
        if dir.join("tests").join("fixtures").join("gold").is_dir() {
            return dir;
        }
        assert!(
            dir.pop(),
            "could not locate tests/fixtures/gold above {}",
            env!("CARGO_MANIFEST_DIR")
        );
    }
}

fn fixture_path() -> PathBuf {
    repo_root()
        .join("tests")
        .join("fixtures")
        .join("gold")
        .join("pdf_engine_segments_v1.json")
}

fn pdf_path(document: &str) -> PathBuf {
    repo_root()
        .join("tests")
        .join("fixtures")
        .join("gold")
        .join("typical_curves")
        .join("pdfs")
        .join(document)
}

/// Collect every vertex a MuPDF drawing item contributes.
///
/// MuPDF and PDFium decompose the same geometry differently, so this expands
/// both to a comparable vertex set:
///
/// * `"l" x0 y0 x1 y1`          -> the two endpoints
/// * `"c" x0 y0 x1 y1 x2 y2 x3 y3` -> all four points
/// * `"re" x0 y0 x1 y1 [orient]`   -> all **four** corners (MuPDF gives the
///   rectangle as one item; PDFium expands it to Move/Line segments)
/// * `"qu" x0 y0 x1 y1 ...`     -> all quad corners
fn reference_vertices(page: &FixturePage) -> Vec<Pt> {
    let mut out = Vec::new();
    for drawing in &page.drawings {
        for item in &drawing.items {
            let Some(kind) = item.first().and_then(|v| v.as_str()) else {
                continue;
            };
            let nums: Vec<f64> = item[1..]
                .iter()
                .filter_map(serde_json::Value::as_f64)
                .collect();
            match kind {
                "l" => {
                    if nums.len() >= 4 {
                        out.push(Pt::new(nums[0], nums[1]));
                        out.push(Pt::new(nums[2], nums[3]));
                    }
                }
                "c" => {
                    for i in (0..nums.len().saturating_sub(1)).step_by(2).take(4) {
                        out.push(Pt::new(nums[i], nums[i + 1]));
                    }
                }
                "re" => {
                    if nums.len() >= 4 {
                        let (x0, y0, x1, y1) = (nums[0], nums[1], nums[2], nums[3]);
                        out.push(Pt::new(x0, y0));
                        out.push(Pt::new(x1, y0));
                        out.push(Pt::new(x1, y1));
                        out.push(Pt::new(x0, y1));
                    }
                }
                "qu" => {
                    for i in (0..nums.len().saturating_sub(1)).step_by(2).take(4) {
                        out.push(Pt::new(nums[i], nums[i + 1]));
                    }
                }
                _ => {}
            }
        }
    }
    out
}

fn reference_segment_count(page: &FixturePage) -> usize {
    page.segment_count
}

fn engine_vertices(drawings: &[dw_pdf::Drawing]) -> Vec<Pt> {
    let mut out = Vec::new();
    for d in drawings {
        for s in &d.segments {
            out.extend(s.points.iter().copied());
        }
    }
    out
}

fn engine_segment_count(drawings: &[dw_pdf::Drawing]) -> usize {
    drawings.iter().map(dw_pdf::Drawing::segment_count).sum()
}

fn engine_kind_counts(drawings: &[dw_pdf::Drawing]) -> HashMap<&'static str, usize> {
    let mut m = HashMap::new();
    for d in drawings {
        for s in &d.segments {
            *m.entry(match s.kind {
                SegmentKind::Move => "move",
                SegmentKind::Line => "line",
                SegmentKind::Cubic => "cubic",
                SegmentKind::Close => "close",
            })
            .or_insert(0) += 1;
        }
    }
    m
}

/// Fraction of `reference` vertices with a partner in `engine` within tolerance.
/// Greedy nearest-match; vertices are consumed so duplicates cannot all claim
/// the same partner.
fn match_fraction(reference: &[Pt], engine: &[Pt]) -> f64 {
    if reference.is_empty() {
        return 1.0;
    }
    let mut used = vec![false; engine.len()];
    let mut matched = 0usize;
    for r in reference {
        let mut best: Option<(f64, usize)> = None;
        for (i, e) in engine.iter().enumerate() {
            if used[i] {
                continue;
            }
            let dx = (r.x - e.x).abs();
            let dy = (r.y - e.y).abs();
            if dx <= TOLERANCE_PT && dy <= TOLERANCE_PT {
                let d = dx * dx + dy * dy;
                if best.is_none_or(|(bd, _)| d < bd) {
                    best = Some((d, i));
                }
            }
        }
        if let Some((_, i)) = best {
            used[i] = true;
            matched += 1;
        }
    }
    matched as f64 / reference.len() as f64
}

#[test]
fn g7_path_segments_match_python_reference() {
    let fixture: Fixture = serde_json::from_str(
        &std::fs::read_to_string(fixture_path()).expect("reference fixture present"),
    )
    .expect("reference fixture parses");

    assert!(!fixture.pages.is_empty(), "reference fixture has pages");

    let engine = PdfiumEngine::bind()
        .expect("pdfium library available — run scripts/fetch_pdfium.sh or set PDFIUM_LIB_PATH");
    eprintln!("engine: {:?}", engine.engine_id());

    let mut report = String::new();
    let mut all_ok = true;

    for page in &fixture.pages {
        let path = pdf_path(&page.document);
        let doc = engine.open(&path).expect("pdf opens");
        let geometry = doc.page_geometry(page.page_1based).expect("page exists");

        let geom_delta = (geometry.width_pt - page.page_width_pt)
            .abs()
            .max((geometry.height_pt - page.page_height_pt).abs());
        let geom_ok = geom_delta <= 0.001;

        let drawings = doc.path_drawings(page.page_1based).expect("drawings");
        let ref_verts = reference_vertices(page);
        let eng_verts = engine_vertices(&drawings);
        let fraction = match_fraction(&ref_verts, &eng_verts);

        let ref_segs = reference_segment_count(page);
        let eng_segs = engine_segment_count(&drawings);
        let seg_delta = if ref_segs == 0 {
            0.0
        } else {
            (eng_segs as f64 - ref_segs as f64).abs() / ref_segs as f64
        };

        let kinds = engine_kind_counts(&drawings);

        let ok =
            geom_ok && fraction >= VERTEX_MATCH_FRACTION && seg_delta <= SEGMENT_COUNT_TOLERANCE;
        all_ok &= ok;

        report.push_str(&format!(
            "\n  {} p{}  {}\n\
             \x20   page geometry  {width:.3} x {height:.3} pt  (ref {rw:.3} x {rh:.3})  delta {delta:.5}  [{g}]\n\
             \x20   drawings       engine {ed}  ref {rd}\n\
             \x20   segments       engine {es}  ref {rs}  delta {sd:.4}  [{s}]\n\
             \x20   vertices       engine {ev}  ref {rv}  matched {frac:.4}  [{v}]\n\
             \x20   engine kinds   {kinds:?}\n",
            page.document,
            page.page_1based,
            if ok { "OK" } else { "FAIL" },
            width = geometry.width_pt,
            height = geometry.height_pt,
            rw = page.page_width_pt,
            rh = page.page_height_pt,
            delta = geom_delta,
            g = if geom_ok { "ok" } else { "FAIL" },
            ed = drawings.len(),
            rd = page.drawing_count,
            es = eng_segs,
            rs = ref_segs,
            sd = seg_delta,
            s = if seg_delta <= SEGMENT_COUNT_TOLERANCE {
                "ok"
            } else {
                "FAIL"
            },
            ev = eng_verts.len(),
            rv = ref_verts.len(),
            frac = fraction,
            v = if fraction >= VERTEX_MATCH_FRACTION {
                "ok"
            } else {
                "FAIL"
            },
            kinds = kinds,
        ));
    }

    eprintln!("\nD1 G7 path-segment parity{report}");
    eprintln!(
        "\nbar: geometry <= 0.001 pt | vertices >= {VERTEX_MATCH_FRACTION} within {TOLERANCE_PT} pt | segments <= {SEGMENT_COUNT_TOLERANCE}"
    );

    assert!(
        all_ok,
        "G7 FAILED — D1 requires PDFium to match the Python reference before dw-pdf work proceeds"
    );
}
