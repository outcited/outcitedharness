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

/// What a single path segment does. Mirrors `MuPDF`'s item kind so the reference
/// fixture and this engine can be compared field for field.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SegmentKind {
    /// Starts a new sub-path.
    Move,
    /// A straight line to the point.
    Line,
    /// A cubic Bézier. pdfium emits one `BezierTo` per control/end point; the
    /// extractor regroups them into triples.
    Cubic,
    /// The sub-path is closed. Carries no points.
    Close,
}

/// One segment of a vector path.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PathSegment {
    /// What the segment does.
    pub kind: SegmentKind,
    /// Destination point(s). Empty for [`SegmentKind::Close`].
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
        // Compose this object's own matrix onto the parent's.
        let matrix = object.matrix().map_or(parent, |m| parent.multiply(m));

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

                let mut segments = Vec::new();
                let mut x0 = f64::MAX;
                let mut y0 = f64::MAX;
                let mut x1 = f64::MIN;
                let mut y1 = f64::MIN;

                for segment in path.segments().iter() {
                    let seg = convert_segment(&segment, matrix, page_height);
                    for p in &seg.points {
                        x0 = x0.min(p.x);
                        y0 = y0.min(p.y);
                        x1 = x1.max(p.x);
                        y1 = y1.max(p.y);
                    }
                    segments.push(seg);
                }

                if segments.is_empty() {
                    continue;
                }

                out.push(Drawing {
                    kind: kind.to_string(),
                    rect: Rect { x0, y0, x1, y1 },
                    segments,
                });
            }
            PdfPageObject::XObjectForm(form) => {
                // Measured: pdfium bakes a parent's transform into the child
                // coordinates when they are enumerated, so composing the form
                // matrix here double-applies it. Recurse with the *parent*
                // matrix, not `matrix`. Evidence in docs/DECISIONS.md G7.
                let children: Vec<PdfPageObject<'_>> =
                    form.as_range().filter_map(|i| form.get(i).ok()).collect();
                collect_paths(children, parent, page_height, out);
            }
            _ => {}
        }
    }
}

/// Convert one pdfium segment to page space.
///
/// pdfium gives PDF user space: origin bottom-left, y up. The reference engine
/// (`MuPDF`) and therefore the whole extraction stack use page space: origin
/// top-left, y down. So: apply the object's transform, then flip y.
fn convert_segment(
    segment: &PdfPathSegment<'_>,
    matrix: PdfMatrix,
    page_height: f64,
) -> PathSegment {
    let (x, y) = segment.point();
    let (tx, ty) = matrix.apply_to_points(x, y);
    let pt = Pt::new(f64::from(tx.value), page_height - f64::from(ty.value));
    match segment.segment_type() {
        PdfPathSegmentType::MoveTo => PathSegment {
            kind: SegmentKind::Move,
            points: vec![pt],
        },
        PdfPathSegmentType::LineTo => PathSegment {
            kind: SegmentKind::Line,
            points: vec![pt],
        },
        PdfPathSegmentType::BezierTo => PathSegment {
            kind: SegmentKind::Cubic,
            points: vec![pt],
        },
        PdfPathSegmentType::Unknown => PathSegment {
            kind: SegmentKind::Close,
            points: vec![],
        },
    }
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
#[allow(clippy::expect_used, clippy::unwrap_used)]
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
            segments: vec![
                PathSegment {
                    kind: SegmentKind::Move,
                    points: vec![Pt::new(0.0, 0.0)],
                },
                PathSegment {
                    kind: SegmentKind::Line,
                    points: vec![Pt::new(1.0, 1.0)],
                },
            ],
        };
        assert_eq!(d.segment_count(), 2);
    }
}
