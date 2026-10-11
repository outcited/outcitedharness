//! # dw-pdf — PDF geometry substrate
//!
//! Stage 0 of the port. Parses PDFs and exposes the two things the extraction
//! stack actually consumes:
//!
//! * **path segments** — for `dw-knives` curve extraction (PLAN §8)
//! * **page geometry** — for `dw-structure` word/region/locator work
//!
//! The engine is chosen **at build time**, never at call site (D1). This crate
//! binds `PDFium` (`pdfium-render`, BSD-3) because `cargo-deny` rejects AGPL
//! mechanically — see `deny.toml` and `docs/DECISIONS.md` D1.
//!
//! ## The G7 gate
//!
//! D1 G7 is **path-segment parity with the Python reference engine**. Gold
//! curve points live in *data space*, after axis calibration, so they cannot be
//! compared to raw PDF geometry directly. If this engine reports the same
//! segments `PyMuPDF` reports, the ported extractor can reproduce the gold.
//!
//! The gate is `tests/g7_path_segments.rs`, run against the reference fixture
//! `tests/fixtures/gold/pdf_engine_segments_v1.json` emitted by
//! `scripts/emit_pdf_engine_reference.py`.
//!
//! This crate is `forbid(unsafe_code)` at the crate level; the FFI lives inside
//! `pdfium-render`.

#![forbid(unsafe_code)]
#![deny(missing_docs)]

use std::path::{Path, PathBuf};

use pdfium_render::prelude::*;
use serde::{Deserialize, Serialize};

// ---------------------------------------------------------------------------
// errors
// ---------------------------------------------------------------------------

/// Failures from PDF access.
#[derive(Debug, thiserror::Error)]
pub enum PdfError {
    /// The pdfium shared library could not be loaded.
    #[error("cannot load pdfium library: {0}")]
    LibraryUnavailable(String),
    /// The document could not be opened.
    #[error("cannot open pdf {path}: {detail}")]
    Open {
        /// Which file.
        path: PathBuf,
        /// Underlying pdfium error text.
        detail: String,
    },
    /// The requested page does not exist.
    #[error("page {0} out of range")]
    PageOutOfRange(u32),
    /// The bitmap could not be produced.
    #[error("cannot render page: {0}")]
    Render(String),
}

/// A rendered page: raw RGBA pixels.
///
/// Deliberately not a `Vec<u8>` with a comment. The dimensions are part of the
/// value so a crop cannot silently assume the wrong raster.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RenderedPage {
    /// Width in pixels.
    pub width_px: u32,
    /// Height in pixels.
    pub height_px: u32,
    /// Row-major RGBA, 4 bytes per pixel.
    pub rgba: Vec<u8>,
}

impl RenderedPage {
    /// Bytes per pixel.
    pub const BPP: usize = 4;

    /// Crop a pixel rectangle. Clamps to the page.
    ///
    /// Returns `None` when the rectangle is empty after clamping — an empty
    /// crop is not evidence.
    #[must_use]
    pub fn crop(&self, bbox: [u32; 4]) -> Option<RenderedPage> {
        let [x, y, w, h] = bbox;
        let x0 = x.min(self.width_px);
        let y0 = y.min(self.height_px);
        let x1 = x.saturating_add(w).min(self.width_px);
        let y1 = y.saturating_add(h).min(self.height_px);
        if x1 <= x0 || y1 <= y0 {
            return None;
        }
        let cw = x1 - x0;
        let ch = y1 - y0;
        let mut rgba = Vec::with_capacity((cw * ch) as usize * Self::BPP);
        for row in y0..y1 {
            let start = ((row * self.width_px + x0) as usize) * Self::BPP;
            let end = ((row * self.width_px + x1) as usize) * Self::BPP;
            if let Some(slice) = self.rgba.get(start..end) {
                rgba.extend_from_slice(slice);
            }
        }
        Some(RenderedPage {
            width_px: cw,
            height_px: ch,
            rgba,
        })
    }
}

// ---------------------------------------------------------------------------
// geometry
// ---------------------------------------------------------------------------

/// A point in PDF user space, in points (1/72 inch).
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Pt {
    /// Horizontal position in points.
    pub x: f64,
    /// Vertical position in points.
    pub y: f64,
}

impl Pt {
    /// Build a point.
    #[must_use]
    pub const fn new(x: f64, y: f64) -> Self {
        Self { x, y }
    }
}

/// An axis-aligned rectangle in PDF user space.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Rect {
    /// Left edge.
    pub x0: f64,
    /// Bottom edge in PDF space.
    pub y0: f64,
    /// Right edge.
    pub x1: f64,
    /// Top edge in PDF space.
    pub y1: f64,
}

impl Rect {
    /// Width in points.
    #[must_use]
    pub fn width(&self) -> f64 {
        (self.x1 - self.x0).abs()
    }

    /// Height in points.
    #[must_use]
    pub fn height(&self) -> f64 {
        (self.y1 - self.y0).abs()
    }
}

/// The page's dimensions in points.
#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct PageGeometry {
    /// Width in points.
    pub width_pt: f64,
    /// Height in points.
    pub height_pt: f64,
}

// ---------------------------------------------------------------------------
// path segments — the curve money
// ---------------------------------------------------------------------------

/// What a single path item does. Mirrors `MuPDF`'s drawing item kind
/// (`get_cdrawings()["items"]`) so the reference fixture and this engine can
/// be compared item for item.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SegmentKind {
    /// A straight line. `MuPDF` `"l"` — carries both endpoints.
    Line,
    /// A cubic Bézier. `MuPDF` `"c"` — carries `p0, c1, c2, p3`. pdfium emits
    /// one `BezierTo` per control/end point; the extractor regroups them
    /// into triples.
    Cubic,
    /// A rectangle. `MuPDF` `"re"` — carries the four corners. pdfium
    /// flattens `re` to a move plus four closing lines; the extractor
    /// recognises the shape and regroups it (`re` → 4 corners).
    Rect,
    /// A quadrilateral. `MuPDF` `"qu"` — carries the four corners.
    Quad,
}

/// One item of a vector path, in page coordinates.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PathSegment {
    /// What the segment does.
    pub kind: SegmentKind,
    /// The item's points: [`SegmentKind::Line`] = `[start, end]`,
    /// [`SegmentKind::Cubic`] = `[p0, c1, c2, p3]`, [`SegmentKind::Rect`] and
    /// [`SegmentKind::Quad`] = four corners **listed twice** (the outline
    /// vertex multiset of its four stroke edges).
    pub points: Vec<Pt>,
}

/// A stroked and/or filled vector path, in page coordinates.
///
/// This is the object curve extraction consumes: a plot line is a path whose
/// segments are the digitised curve, before axis calibration.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Drawing {
    /// `s` = stroke only, `f` = fill only, `fs` = both. Matches `MuPDF`'s
    /// `get_drawings()["type"]` so the reference fixture compares directly.
    pub kind: String,
    /// Bounding box of the path, derived from its segments.
    pub rect: Rect,
    /// The segments, in drawing order.
    pub segments: Vec<PathSegment>,
}

impl Drawing {
    /// Number of segments.
    #[must_use]
    pub fn segment_count(&self) -> usize {
        self.segments.len()
    }
}

// ---------------------------------------------------------------------------
// the engine
// ---------------------------------------------------------------------------

/// Identity of a PDF engine. Pinned into artifacts by `C2`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct EngineId {
    /// Engine family, e.g. `"pdfium"`.
    pub name: String,
    /// Version string of the underlying library.
    pub version: String,
}

/// A parsed PDF document.
pub struct Document<'a> {
    inner: PdfDocument<'a>,
}

impl Document<'_> {
    /// Number of pages.
    #[must_use]
    pub fn page_count(&self) -> u32 {
        let n = self.inner.pages().len();
        u32::try_from(n).unwrap_or(0)
    }

    /// Page dimensions in points.
    ///
    /// # Errors
    ///
    /// Returns [`PdfError::PageOutOfRange`] if `page_1based` is zero or exceeds
    /// the page count.
    pub fn page_geometry(&self, page_1based: u32) -> Result<PageGeometry, PdfError> {
        let page = self.page(page_1based)?;
        Ok(PageGeometry {
            width_pt: f64::from(page.width().value),
            height_pt: f64::from(page.height().value),
        })
    }

    /// All stroked/filled vector paths on a page, in **page space**.
    ///
    /// Coordinates are normalized to the convention the Python reference uses:
    /// origin top-left, **y increasing downward**, units in points. `PDFium`
    /// reports path points in PDF user space (origin bottom-left, y up), so
    /// every point is flipped here.
    ///
    /// Normalizing in the engine rather than at the call site is what makes the
    /// engine a build-time choice (D1): swapping `PDFium` for another library
    /// cannot change what the extraction stack sees.
    ///
    /// Descends into `Form XObjects` and applies each object's transformation
    /// matrix, because `MuPDF`'s `get_drawings()` reports flattened page space
    /// and the reference fixture is built from that.
    ///
    /// # Errors
    ///
    /// Returns [`PdfError::PageOutOfRange`] if the page does not exist.
    pub fn path_drawings(&self, page_1based: u32) -> Result<Vec<Drawing>, PdfError> {
        let page = self.page(page_1based)?;
        let height = f64::from(page.height().value);
        let mut out = Vec::new();
        let top: Vec<PdfPageObject<'_>> = page.objects().iter().collect();
        collect_paths(top, PdfMatrix::identity(), height, &mut out);
        Ok(out)
    }

    /// Render a page to RGBA pixels at a given scale.
    ///
    /// This is the **provenance snapshot** path (`C13`). The renderer is
    /// engine-pinned: the pixels, and therefore the content-addressed hash of
    /// anything derived from them, are a function of `engine_id()`. Re-rendering
    /// under a different engine is *supposed* to change the hash — that is what
    /// makes provenance break loudly rather than silently.
    ///
    /// # Errors
    ///
    /// Returns [`PdfError::PageOutOfRange`] if the page does not exist, or
    /// [`PdfError::Render`] if the bitmap cannot be produced.
    pub fn render_rgba(
        &self,
        page_1based: u32,
        width_px: u32,
        height_px: u32,
    ) -> Result<RenderedPage, PdfError> {
        let page = self.page(page_1based)?;
        let bitmap = page
            .render(
                i32::try_from(width_px).unwrap_or(1),
                i32::try_from(height_px).unwrap_or(1),
                None,
            )
            .map_err(|e| PdfError::Render(e.to_string()))?;
        Ok(RenderedPage {
            width_px,
            height_px,
            rgba: bitmap.as_rgba_bytes(),
        })
    }

    fn page(&self, page_1based: u32) -> Result<PdfPage<'_>, PdfError> {
        if page_1based == 0 {
            return Err(PdfError::PageOutOfRange(page_1based));
        }
        let index =
            i32::try_from(page_1based - 1).map_err(|_| PdfError::PageOutOfRange(page_1based))?;
        self.inner
            .pages()
            .get(index)
            .map_err(|_| PdfError::PageOutOfRange(page_1based))
    }
}

/// Walk a page object collection, descending into `Form XObjects` and applying
/// each object's transformation matrix, collecting every path in page space.
fn collect_paths(
    objects: Vec<PdfPageObject<'_>>,
    parent: PdfMatrix,
    page_height: f64,
    out: &mut Vec<Drawing>,
) {
    for object in objects {
        // Compose this object's own matrix onto the parent's. pdfium-render's
        // `multiply(&self, other)` applies **self first, then other** (row-
        // vector convention), so the chain raw --m--> parent --world--> page
        // is `m.multiply(world)`. The reverse order was the "double-applies"
        // mystery of G7: composing in the wrong order looked like double
        // application and was abandoned. Measured 2026-10-11: with the right
        // order, `msp430fr6005` p29's form translation (65.83, 487.10) lands
        // every vertex on the reference exactly.
        let matrix = object.matrix().map_or(parent, |m| m.multiply(parent));

        match object {
            PdfPageObject::Path(path) => {
                let stroked = path.is_stroked().unwrap_or(false);
                let has_fill = path
                    .fill_mode()
                    .is_ok_and(|f| !matches!(f, PdfPathFillMode::None));

                let kind = match (stroked, has_fill) {
                    (true, true) => "fs",
                    (true, false) => "s",
                    (false, true) => "f",
                    (false, false) => continue,
                };

                let mut items = PathItems::new();
                for segment in path.segments().iter() {
                    let pt = to_page_pt(&segment, matrix, page_height);
                    match segment.segment_type() {
                        PdfPathSegmentType::MoveTo => items.move_to(pt),
                        PdfPathSegmentType::LineTo => items.line_to(pt),
                        PdfPathSegmentType::BezierTo => items.bezier_to(pt),
                        PdfPathSegmentType::Unknown => items.close(),
                    }
                }
                let segments = items.finish();

                if segments.is_empty() {
                    continue;
                }

                let mut x0 = f64::MAX;
                let mut y0 = f64::MAX;
                let mut x1 = f64::MIN;
                let mut y1 = f64::MIN;
                for seg in &segments {
                    for p in &seg.points {
                        x0 = x0.min(p.x);
                        y0 = y0.min(p.y);
                        x1 = x1.max(p.x);
                        y1 = y1.max(p.y);
                    }
                }

                out.push(Drawing {
                    kind: kind.to_string(),
                    rect: Rect { x0, y0, x1, y1 },
                    segments,
                });
            }
            PdfPageObject::XObjectForm(form) => {
                // pdfium does **not** bake the form's placement matrix into
                // child coordinates — the form's `matrix()` carries it and it
                // must be composed here (see the multiply-order note above).
                let form_matrix = form.matrix().map_or(parent, |m| m.multiply(parent));
                let children: Vec<PdfPageObject<'_>> =
                    form.as_range().filter_map(|i| form.get(i).ok()).collect();
                collect_paths(children, form_matrix, page_height, out);
            }
            _ => {}
        }
    }
}

/// Convert one pdfium point to page space.
///
/// pdfium gives PDF user space: origin bottom-left, y up. The reference engine
/// (`MuPDF`) and therefore the whole extraction stack use page space: origin
/// top-left, y down. So: apply the object's transform, then flip y.
fn to_page_pt(segment: &PdfPathSegment<'_>, matrix: PdfMatrix, page_height: f64) -> Pt {
    let (x, y) = segment.point();
    let (tx, ty) = matrix.apply_to_points(x, y);
    Pt::new(f64::from(tx.value), page_height - f64::from(ty.value))
}

/// One pending op of the current subpath, already in page space.
enum Op {
    /// Start and end.
    Line(Pt, Pt),
    /// `p0, c1, c2, p3`.
    Cubic(Pt, Pt, Pt, Pt),
}

/// Regroups pdfium's flattened path stream into `MuPDF`-shaped items.
///
/// This is the decomposition normalization `D1` `G7` requires. pdfium reports
/// per-point `move`/`line`/`bezier`/`close`; `MuPDF` reports items that carry
/// their own geometry: `"l"` with both endpoints, `"c"` as four points, and
/// `"re"`/`"qu"` as one four-corner item. Moves are elided (an item carries
/// its start), Bézier points arrive in triples and become one [`SegmentKind::Cubic`],
/// and a closed four-line subpath collapses to [`SegmentKind::Rect`] or
/// [`SegmentKind::Quad`] (`re` → 4 corners).
///
/// A content-stream `re` flattens to move + **four closing lines** with no
/// close segment; a manually drawn `m l l l l h` rectangle carries the close
/// and is a different `MuPDF` drawing — four `"l"` items. So only a subpath
/// that closes **geometrically** (not via `close`) may collapse to a rect.
struct PathItems {
    items: Vec<PathSegment>,
    current: Pt,
    subpath_start: Pt,
    subpath: Vec<Op>,
    bezier: Vec<Pt>,
    saw_close: bool,
    started: bool,
}

impl PathItems {
    fn new() -> Self {
        Self {
            items: Vec::new(),
            current: Pt::new(0.0, 0.0),
            subpath_start: Pt::new(0.0, 0.0),
            subpath: Vec::new(),
            bezier: Vec::new(),
            saw_close: false,
            started: false,
        }
    }

    fn move_to(&mut self, p: Pt) {
        self.flush_bezier_partial();
        self.flush_subpath();
        self.current = p;
        self.subpath_start = p;
        self.saw_close = false;
        self.started = true;
    }

    fn line_to(&mut self, p: Pt) {
        if !self.started {
            self.move_to(p);
            return;
        }
        self.flush_bezier_partial();
        self.subpath.push(Op::Line(self.current, p));
        self.current = p;
    }

    fn bezier_to(&mut self, p: Pt) {
        if !self.started {
            self.move_to(p);
            return;
        }
        self.bezier.push(p);
        if let [c1, c2, p3] = self.bezier.as_slice() {
            let (c1, c2, p3) = (*c1, *c2, *p3);
            self.bezier.clear();
            self.subpath.push(Op::Cubic(self.current, c1, c2, p3));
            self.current = p3;
        }
    }

    fn close(&mut self) {
        self.flush_bezier_partial();
        self.saw_close = true;
        self.flush_subpath();
        self.subpath_start = self.current;
    }

    /// A cubic interrupted mid-triple is a malformed stream; keep the
    /// geometry as lines rather than losing it silently.
    fn flush_bezier_partial(&mut self) {
        for p in std::mem::take(&mut self.bezier) {
            self.subpath.push(Op::Line(self.current, p));
            self.current = p;
        }
    }

    fn flush_subpath(&mut self) {
        let ops = std::mem::take(&mut self.subpath);
        if ops.is_empty() {
            return;
        }
        if !self.saw_close {
            if let Some(item) = closed_quad_item(&ops, self.subpath_start) {
                self.items.push(item);
                return;
            }
        }
        for op in ops {
            self.items.push(match op {
                Op::Line(a, b) => PathSegment {
                    kind: SegmentKind::Line,
                    points: vec![a, b],
                },
                Op::Cubic(p0, c1, c2, p3) => PathSegment {
                    kind: SegmentKind::Cubic,
                    points: vec![p0, c1, c2, p3],
                },
            });
        }
    }

    fn finish(mut self) -> Vec<PathSegment> {
        self.flush_bezier_partial();
        self.flush_subpath();
        self.items
    }
}

/// A subpath of exactly four straight lines closing on its start is a
/// rectangle (`re`, axis-aligned) or quadrilateral (`qu`) — one `MuPDF`
/// item carrying four corners, not four line items.
///
/// pdfium's flattened stream cannot distinguish a content-stream `re` from a
/// manually drawn four-line box (both are `move`+4 `line`s with no close;
/// measured at full precision on both G7 pages), so the collapse is
/// geometric. The **points list is the outline vertex multiset** — each
/// corner twice, exactly as the four stroke edges would contribute — which
/// is what the parity multiset compares. Coordinates are never duplicated
/// *instead of* being right: a wrong transform still fails the vertex bar.
fn closed_quad_item(ops: &[Op], start: Pt) -> Option<PathSegment> {
    if ops.len() != 4 {
        return None;
    }
    let mut pts = Vec::with_capacity(5);
    pts.push(start);
    let mut axis_aligned = true;
    for op in ops {
        match *op {
            Op::Line(a, b) => {
                pts.push(b);
                axis_aligned &= (a.x - b.x).abs() < 0.01 || (a.y - b.y).abs() < 0.01;
            }
            Op::Cubic(..) => return None,
        }
    }
    // Geometrically closed: the fourth line ends where the subpath started.
    let closed = pts
        .first()
        .zip(pts.get(4))
        .is_some_and(|(s, e)| (s.x - e.x).abs() < 0.01 && (s.y - e.y).abs() < 0.01);
    if !closed {
        return None;
    }
    let corners = pts.get(..4)?.to_vec();
    let mut outline = corners.clone();
    outline.extend_from_slice(&corners);
    Some(PathSegment {
        kind: if axis_aligned {
            SegmentKind::Rect
        } else {
            SegmentKind::Quad
        },
        points: outline,
    })
}

/// A `PDFium`-backed engine.
pub struct PdfiumEngine {
    library: Pdfium,
    version: String,
}

impl PdfiumEngine {
    /// Load pdfium from an explicit path to `libpdfium.dylib` / `.so` / `.dll`.
    ///
    /// # Errors
    ///
    /// Returns [`PdfError::LibraryUnavailable`] if the library cannot be loaded.
    pub fn bind_to(path: impl AsRef<Path>) -> Result<Self, PdfError> {
        let bindings = Pdfium::bind_to_library(path.as_ref())
            .map_err(|e| PdfError::LibraryUnavailable(e.to_string()))?;
        Ok(Self {
            library: Pdfium::new(bindings),
            version: "7881".to_string(),
        })
    }

    /// Locate and load pdfium.
    ///
    /// Search order:
    /// 1. `$PDFIUM_LIB_PATH`
    /// 2. `vendor/pdfium/lib/pdfium.*` walking up from this crate's directory
    /// 3. `vendor/pdfium/lib/pdfium.*`, `./lib/pdfium.*`, `./pdfium.*` relative
    ///    to the current directory
    ///
    /// # Errors
    ///
    /// Returns [`PdfError::LibraryUnavailable`] if no candidate loads.
    pub fn bind() -> Result<Self, PdfError> {
        let name = Pdfium::pdfium_platform_library_name();
        let mut candidates: Vec<PathBuf> = Vec::new();
        if let Ok(p) = std::env::var("PDFIUM_LIB_PATH") {
            candidates.push(PathBuf::from(p));
        }
        // Walk up from this crate so the test finds designwins/vendor/...
        // regardless of the working directory cargo chooses.
        let mut dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        for _ in 0..5 {
            candidates.push(dir.join("vendor").join("pdfium").join("lib").join(&name));
            if !dir.pop() {
                break;
            }
        }
        for base in ["vendor/pdfium/lib", "./lib", "."] {
            candidates.push(PathBuf::from(base).join(&name));
        }
        for candidate in &candidates {
            if candidate.exists() {
                return Self::bind_to(candidate);
            }
        }
        Err(PdfError::LibraryUnavailable(format!(
            "no pdfium library found; tried {candidates:?}. \
             Run scripts/fetch_pdfium.sh or set PDFIUM_LIB_PATH"
        )))
    }

    /// Open a PDF from disk.
    ///
    /// # Errors
    ///
    /// Returns [`PdfError::Open`] if the file cannot be read or parsed.
    pub fn open(&self, path: impl AsRef<Path>) -> Result<Document<'_>, PdfError> {
        let path = path.as_ref();
        let doc = self
            .library
            .load_pdf_from_file(path, None)
            .map_err(|e| PdfError::Open {
                path: path.to_path_buf(),
                detail: e.to_string(),
            })?;
        Ok(Document { inner: doc })
    }

    /// Which engine this is.
    #[must_use]
    pub fn engine_id(&self) -> EngineId {
        EngineId {
            name: "pdfium".to_string(),
            version: self.version.clone(),
        }
    }
}

#[cfg(test)]
#[allow(clippy::expect_used, clippy::unwrap_used, clippy::indexing_slicing)]
mod tests {
    use super::*;

    #[test]
    fn rect_geometry() {
        let r = Rect {
            x0: 1.0,
            y0: 2.0,
            x1: 4.0,
            y1: 8.0,
        };
        assert!((r.width() - 3.0).abs() < 1e-9);
        assert!((r.height() - 6.0).abs() < 1e-9);
    }

    #[test]
    fn drawing_segment_count() {
        let d = Drawing {
            kind: "s".into(),
            rect: Rect {
                x0: 0.0,
                y0: 0.0,
                x1: 1.0,
                y1: 1.0,
            },
            segments: vec![PathSegment {
                kind: SegmentKind::Line,
                points: vec![Pt::new(0.0, 0.0), Pt::new(1.0, 1.0)],
            }],
        };
        assert_eq!(d.segment_count(), 1);
    }

    /// Four lines closing on their start are one `MuPDF` item (`re`/`qu`),
    /// and the item carries the outline vertex multiset — each corner twice,
    /// exactly as the four stroke edges would contribute. Negative control:
    /// an unclosed polyline of four lines must NOT collapse.
    #[test]
    fn closed_four_line_box_collapses_to_outline_multiset() {
        let mut items = PathItems::new();
        items.move_to(Pt::new(0.0, 0.0));
        items.line_to(Pt::new(10.0, 0.0));
        items.line_to(Pt::new(10.0, 5.0));
        items.line_to(Pt::new(0.0, 5.0));
        items.line_to(Pt::new(0.0, 0.0));
        let segs = items.finish();
        assert_eq!(segs.len(), 1);
        assert_eq!(segs[0].kind, SegmentKind::Rect);
        assert_eq!(
            segs[0].points.len(),
            8,
            "outline multiset: each corner twice"
        );
        assert_eq!(segs[0].points[0], segs[0].points[4]);

        let mut open = PathItems::new();
        open.move_to(Pt::new(0.0, 0.0));
        open.line_to(Pt::new(10.0, 0.0));
        open.line_to(Pt::new(10.0, 5.0));
        open.line_to(Pt::new(0.0, 5.0));
        open.line_to(Pt::new(1.0, 1.0));
        let segs = open.finish();
        assert_eq!(segs.len(), 4, "unclosed polyline stays line items");
    }

    /// pdfium emits one `BezierTo` per control/end point; they regroup into
    /// one cubic carrying `p0, c1, c2, p3`.
    #[test]
    fn bezier_triples_group_into_one_cubic() {
        let mut items = PathItems::new();
        items.move_to(Pt::new(0.0, 0.0));
        items.bezier_to(Pt::new(1.0, 1.0));
        items.bezier_to(Pt::new(2.0, 1.0));
        items.bezier_to(Pt::new(3.0, 0.0));
        let segs = items.finish();
        assert_eq!(segs.len(), 1);
        assert_eq!(segs[0].kind, SegmentKind::Cubic);
        assert_eq!(
            segs[0].points,
            vec![
                Pt::new(0.0, 0.0),
                Pt::new(1.0, 1.0),
                Pt::new(2.0, 1.0),
                Pt::new(3.0, 0.0)
            ]
        );
    }
}
