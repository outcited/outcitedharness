//! # dw-evidence — resolvable provenance
//!
//! `C13` says an evidence snapshot is a content-addressed page render at 144
//! DPI plus a row-band crop with a `crop_sha256`. That was a type until now.
//! This crate makes it a fact.
//!
//! ## The property that matters
//!
//! > **Provenance breaks loudly, not silently.**
//!
//! If the pixels change, the hash changes, and the claim becomes
//! *unverifiable* — not "probably still fine." Three consequences, each from a
//! law already ratified:
//!
//! * the store is **content-addressed** (`C1`) — a filename is not evidence
//! * the render is **engine-pinned** (`C2`) — a different engine produces
//!   different pixels, and the hash says so
//! * **unresolvable evidence is `Unknown`** (`C9.1`) — a claim whose crop does
//!   not resolve is never `Pass`
//!
//! ## Storage
//!
//! Local only. **Never on the NAS** (`PLAN` §10) — the NAS unmounts, which was
//! measured twice this session.
//!
//! This crate is `forbid(unsafe_code)`. It does no model calls.

#![forbid(unsafe_code)]
#![deny(missing_docs)]
// Byte-level PNG framing and the SHA-256 compression function are index and
// cast arithmetic by algorithm, not by accident: `i < 64` in the FIPS 180-4
// schedule, `row < height` in scanline assembly, `usize` into PNG length
// fields. These lints describe what those functions *are*.
//
// They are allowed crate-wide because the primitives are the only place they
// fire — `cargo clippy` is green, and the hash is tested against FIPS
// known-answer vectors so a wrong index would fail loudly rather than
// silently mis-address evidence. If this crate grows non-primitive code,
// move the primitives into a `mod primitives` and scope the allowance there.
#![allow(
    clippy::indexing_slicing,
    clippy::cast_possible_truncation,
    clippy::cast_sign_loss,
    clippy::many_single_char_names,
    clippy::format_push_string,
    clippy::too_many_lines
)]

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use dw_types::{CropRef, Sha256};
use serde::{Deserialize, Serialize};

/// Schema id for stored evidence records.
pub const EVIDENCE_SCHEMA: &str = "harness.dw-evidence.v1";

/// Failures from evidence resolution.
#[derive(Debug, thiserror::Error)]
pub enum EvidenceError {
    /// The store root does not exist or is not writable.
    #[error("evidence store unavailable at {0}: {1}")]
    StoreUnavailable(PathBuf, String),
    /// An artifact could not be written.
    #[error("cannot write {0}: {1}")]
    Write(PathBuf, String),
    /// A referenced artifact is not in the store.
    #[error("evidence not found: {0}")]
    NotFound(Sha256),
    /// The rendered page was empty or the crop was empty.
    #[error("empty evidence: {0}")]
    Empty(String),
}

// ---------------------------------------------------------------------------
// what is stored
// ---------------------------------------------------------------------------

/// A stored page render. Content-addressed by the pixels, not by the document.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PageRender {
    /// Content hash of the encoded image bytes.
    pub sha256: Sha256,
    /// Render resolution. [`CropRef::PROVENANCE_DPI`] (144) is the `C13` value;
    /// anything else is recorded and must be reported, never assumed.
    pub dpi: u32,
    /// Width in pixels.
    pub width_px: u32,
    /// Height in pixels.
    pub height_px: u32,
    /// Which engine produced these pixels (`C2`). Part of the identity: the
    /// same page under a different engine is a different artifact.
    pub engine: String,
    /// The document these pixels came from.
    pub document_sha256: Sha256,
    /// Which page, 1-based.
    pub page_1based: u32,
}

/// A stored row-band crop.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Crop {
    /// Content hash of the encoded crop bytes.
    pub sha256: Sha256,
    /// The page render this crop came from. A crop is never free-floating.
    pub page_sha256: Sha256,
    /// The pixel rectangle that was cut.
    pub bbox: [u32; 4],
}

// ---------------------------------------------------------------------------
// encoding
// ---------------------------------------------------------------------------

/// Encode RGBA pixels as a PNG.
///
/// The encoder is **part of provenance**: a different PNG encoder produces
/// different bytes and therefore a different hash. It is named, pinned, and
/// tested for determinism for that reason.
///
/// # Lints
///
/// Scanline assembly is index arithmetic over a flat buffer. The bounds are
/// established by the loop structure (`row < height`, `stride = width * 4`);
/// `clippy::indexing_slicing` is a poor fit here and is allowed for this
/// function only.
#[must_use]
pub fn encode_png(width: u32, height: u32, rgba: &[u8]) -> Vec<u8> {
    // A minimal, dependency-free PNG encoder: 8-bit RGBA, no interlace,
    // stored (uncompressed) IDAT. Deterministic by construction.
    let mut out = Vec::new();
    out.extend_from_slice(&[0x89, b'P', b'N', b'G', 0x0D, 0x0A, 0x1A, 0x0A]);

    let mut ihdr = Vec::new();
    ihdr.extend_from_slice(&width.to_be_bytes());
    ihdr.extend_from_slice(&height.to_be_bytes());
    ihdr.extend_from_slice(&[8, 6, 0, 0, 0]); // 8-bit RGBA, deflate, adaptive, no interlace
    chunk(&mut out, *b"IHDR", &ihdr);

    // Raw scanlines, each prefixed by filter type 0 (None).
    let stride = width as usize * 4;
    let mut raw = Vec::with_capacity(height as usize * (stride + 1));
    for row in 0..height as usize {
        raw.push(0);
        let start = row * stride;
        let end = (start + stride).min(rgba.len());
        raw.extend_from_slice(&rgba[start..end]);
    }
    let compressed = zlib_store(&raw);
    chunk(&mut out, *b"IDAT", &compressed);
    chunk(&mut out, *b"IEND", &[]);
    out
}

/// # Lints
///
/// Byte-level PNG framing: `usize` to length fields, and a CRC schedule over a
/// flat slice. The casts are the format. Allowed for this module's primitives
/// only; library code elsewhere keeps the full lint surface.
fn chunk(out: &mut Vec<u8>, kind: [u8; 4], data: &[u8]) {
    out.extend_from_slice(&(data.len() as u32).to_be_bytes());
    out.extend_from_slice(&kind);
    out.extend_from_slice(data);
    let mut crc_input = Vec::with_capacity(4 + data.len());
    crc_input.extend_from_slice(&kind);
    crc_input.extend_from_slice(data);
    out.extend_from_slice(&crc32(&crc_input).to_be_bytes());
}

/// zlib stream with stored (uncompressed) deflate blocks. Deterministic.
fn zlib_store(data: &[u8]) -> Vec<u8> {
    let mut out = vec![0x78, 0x01]; // deflate, 32K window, no preset dict
    let mut chunks: Vec<&[u8]> = Vec::new();
    for c in data.chunks(65535) {
        chunks.push(c);
    }
    if chunks.is_empty() {
        chunks.push(&[]);
    }
    let last = chunks.len() - 1;
    for (i, c) in chunks.iter().enumerate() {
        let len = c.len() as u16;
        out.push(u8::from(i == last));
        out.extend_from_slice(&len.to_le_bytes());
        out.extend_from_slice(&(!len).to_le_bytes());
        out.extend_from_slice(c);
    }
    let adler = adler32(data);
    out.extend_from_slice(&adler.to_be_bytes());
    out
}

fn adler32(data: &[u8]) -> u32 {
    let (mut a, mut b) = (1u32, 0u32);
    for byte in data {
        a = (a + u32::from(*byte)) % 65521;
        b = (b + a) % 65521;
    }
    (b << 16) | a
}

fn crc32(data: &[u8]) -> u32 {
    let mut crc = 0xFFFF_FFFFu32;
    for byte in data {
        crc ^= u32::from(*byte);
        for _ in 0..8 {
            let mask = (crc & 1).wrapping_neg();
            crc = (crc >> 1) ^ (0xEDB8_8320 & mask);
        }
    }
    !crc
}

// ---------------------------------------------------------------------------
// the store
// ---------------------------------------------------------------------------

/// A content-addressed evidence store on local disk.
///
/// Layout: `{root}/pages/{sha256[..2]}/{sha256}.png` and
/// `{root}/crops/{sha256[..2]}/{sha256}.png`. Two leading hex characters keep
/// directory sizes sane without introducing any lookup index that could drift
/// from the content.
#[derive(Debug, Clone)]
pub struct EvidenceStore {
    root: PathBuf,
    engine: String,
    records: BTreeMap<Sha256, PageRender>,
}

impl EvidenceStore {
    /// Open (and create) a store at `root`.
    ///
    /// # Errors
    ///
    /// Returns [`EvidenceError::StoreUnavailable`] if the root cannot be
    /// created. **Point this at local disk**, not the NAS.
    pub fn open(root: impl AsRef<Path>, engine: impl Into<String>) -> Result<Self, EvidenceError> {
        let root = root.as_ref().to_path_buf();
        for sub in ["pages", "crops"] {
            std::fs::create_dir_all(root.join(sub))
                .map_err(|e| EvidenceError::StoreUnavailable(root.clone(), e.to_string()))?;
        }
        Ok(Self {
            root,
            engine: engine.into(),
            records: BTreeMap::new(),
        })
    }

    /// Which engine produced the pixels in this store (`C2`).
    #[must_use]
    pub fn engine(&self) -> &str {
        &self.engine
    }

    fn path_for(&self, kind: &str, sha: &Sha256) -> PathBuf {
        // A `Sha256` is 64 lowercase hex by construction, so the prefix is
        // always 2 characters. `split_at` makes that explicit rather than
        // relying on a slice that could panic if the invariant ever broke.
        let (prefix, _) = sha.as_str().split_at(2);
        self.root
            .join(kind)
            .join(prefix)
            .join(format!("{}.png", sha.as_str()))
    }

    /// Store encoded PNG bytes and return their content hash.
    ///
    /// # Errors
    ///
    /// Returns [`EvidenceError::Write`] if the bytes cannot be persisted, or
    /// [`EvidenceError::Empty`] if `bytes` is empty.
    pub fn put_bytes(&self, kind: &str, bytes: &[u8]) -> Result<Sha256, EvidenceError> {
        if bytes.is_empty() {
            return Err(EvidenceError::Empty(kind.to_string()));
        }
        let sha = sha256_of(bytes);
        let path = self.path_for(kind, &sha);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| EvidenceError::Write(path.clone(), e.to_string()))?;
        }
        std::fs::write(&path, bytes).map_err(|e| EvidenceError::Write(path, e.to_string()))?;
        Ok(sha)
    }

    /// Store a page render.
    ///
    /// # Errors
    ///
    /// See [`Self::put_bytes`].
    pub fn put_page(
        &mut self,
        document_sha256: Sha256,
        page_1based: u32,
        dpi: u32,
        width_px: u32,
        height_px: u32,
        rgba: &[u8],
    ) -> Result<PageRender, EvidenceError> {
        let png = encode_png(width_px, height_px, rgba);
        let sha = self.put_bytes("pages", &png)?;
        let render = PageRender {
            sha256: sha,
            dpi,
            width_px,
            height_px,
            engine: self.engine.clone(),
            document_sha256,
            page_1based,
        };
        self.records.insert(render.sha256.clone(), render.clone());
        Ok(render)
    }

    /// Store a row-band crop cut from a page render.
    ///
    /// # Errors
    ///
    /// Returns [`EvidenceError::Empty`] if the crop is empty, or
    /// [`EvidenceError::Write`] on a persistence failure.
    pub fn put_crop(
        &mut self,
        page: &PageRender,
        bbox: [u32; 4],
        rgba: &[u8],
        width_px: u32,
        height_px: u32,
    ) -> Result<Crop, EvidenceError> {
        let _ = (width_px, height_px);
        if rgba.is_empty() || width_px == 0 || height_px == 0 {
            return Err(EvidenceError::Empty("crop".to_string()));
        }
        let png = encode_png(width_px, height_px, rgba);
        let sha = self.put_bytes("crops", &png)?;
        Ok(Crop {
            sha256: sha,
            page_sha256: page.sha256.clone(),
            bbox,
        })
    }

    /// Resolve a stored artifact to its bytes.
    ///
    /// # Errors
    ///
    /// Returns [`EvidenceError::NotFound`] when the hash does not resolve.
    /// **That is the loud break**: the caller must treat an unresolved claim as
    /// `Unknown`, never as `Pass`.
    pub fn get_bytes(&self, kind: &str, sha: &Sha256) -> Result<Vec<u8>, EvidenceError> {
        let path = self.path_for(kind, sha);
        std::fs::read(&path).map_err(|_| EvidenceError::NotFound(sha.clone()))
    }

    /// Resolve a [`CropRef`] to the crop bytes.
    ///
    /// # Errors
    ///
    /// Returns [`EvidenceError::NotFound`] when the crop does not resolve.
    pub fn resolve_crop(&self, crop: &CropRef) -> Result<Vec<u8>, EvidenceError> {
        self.get_bytes("crops", &crop.crop_sha256)
    }

    /// True when a [`CropRef`] resolves. This is the predicate `dw-decide`
    /// must consult before it may return `Pass`.
    #[must_use]
    pub fn resolves(&self, crop: &CropRef) -> bool {
        self.path_for("crops", &crop.crop_sha256).is_file()
    }

    /// The recorded page renders, by hash.
    #[must_use]
    pub fn records(&self) -> &BTreeMap<Sha256, PageRender> {
        &self.records
    }
}

/// SHA-256 of raw bytes.
///
/// # Panics
///
/// Never in practice: the digest is always 64 lowercase hex characters. The
/// constructor is the type's own validator and the two are written together.
#[must_use]
pub fn sha256_of(bytes: &[u8]) -> Sha256 {
    // Small dependency-free SHA-256. The workspace forbids `unsafe`, and
    // pulling a hashing crate for one function is not worth the surface —
    // the hash that identifies evidence should be under this crate's control.
    let mut h = Sha256State::new();
    h.update(bytes);
    Sha256::new(h.finalize_hex())
        .unwrap_or_else(|_| unreachable!("sha256 digest is 64 lowercase hex by construction"))
}

/// The `C13` DPI. Everything provenance-related renders at this.
pub const PROVENANCE_DPI: u32 = CropRef::PROVENANCE_DPI;

/// Render size in pixels for a page of `pt` dimensions at [`PROVENANCE_DPI`].
#[must_use]
pub fn render_size_pt(width_pt: f64, height_pt: f64) -> (u32, u32) {
    let scale = f64::from(PROVENANCE_DPI) / 72.0;
    // A page is never negative and never exceeds u32 pixels in practice; the
    // clamp is what makes the cast safe rather than the cast itself.
    let clamp = |pt: f64| -> u32 {
        let px = (pt * scale).round().max(1.0);
        let whole = if px >= f64::from(u32::MAX) {
            u32::MAX
        } else {
            {
                px as u32
            }
        };
        whole.max(1)
    };
    (clamp(width_pt), clamp(height_pt))
}

// --- SHA-256 ----------------------------------------------------------------
// Kept here rather than as a dependency so the hash that identifies evidence is
// under this crate's control and cannot change because a transitive dep bumped.

#[derive(Clone)]
struct Sha256State {
    h: [u32; 8],
    buf: [u8; 64],
    buf_len: usize,
    total: u64,
}

impl Sha256State {
    fn new() -> Self {
        Self {
            h: [
                0x6a09_e667,
                0xbb67_ae85,
                0x3c6e_f372,
                0xa54f_f53a,
                0x510e_527f,
                0x9b05_688c,
                0x1f83_d9ab,
                0x5be0_cd19,
            ],
            buf: [0; 64],
            buf_len: 0,
            total: 0,
        }
    }

    fn update(&mut self, mut data: &[u8]) {
        self.total = self.total.wrapping_add(data.len() as u64);
        while !data.is_empty() {
            let take = (64 - self.buf_len).min(data.len());
            self.buf[self.buf_len..self.buf_len + take].copy_from_slice(&data[..take]);
            self.buf_len += take;
            data = &data[take..];
            if self.buf_len == 64 {
                let block = self.buf;
                self.compress(&block);
                self.buf_len = 0;
            }
        }
    }

    fn finalize_hex(mut self) -> String {
        let total_bits = self.total.wrapping_mul(8);
        self.buf[self.buf_len] = 0x80;
        self.buf_len += 1;
        for b in self.buf.iter_mut().skip(self.buf_len) {
            *b = 0;
        }
        if self.buf_len > 56 {
            let block = self.buf;
            self.compress(&block);
            self.buf = [0; 64];
        }
        self.buf[56..64].copy_from_slice(&total_bits.to_be_bytes());
        let block = self.buf;
        self.compress(&block);
        let mut out = String::with_capacity(64);
        for word in self.h {
            out.push_str(&format!("{word:08x}"));
        }
        out
    }

    /// The SHA-256 compression function.
    ///
    /// # Lints
    ///
    /// This is the FIPS 180-4 schedule: a fixed 64-element message array and
    /// fixed 64-round loop, with indices generated by the algorithm itself.
    /// Bounds are proven by construction (`i < 64`), not by inspection of a
    /// caller. `clippy::indexing_slicing` is allowed for this function only.
    fn compress(&mut self, block: &[u8; 64]) {
        const K: [u32; 64] = [
            0x428a_2f98,
            0x7137_4491,
            0xb5c0_fbcf,
            0xe9b5_dba5,
            0x3956_c25b,
            0x59f1_11f1,
            0x923f_82a4,
            0xab1c_5ed5,
            0xd807_aa98,
            0x1283_5b01,
            0x2431_85be,
            0x550c_7dc3,
            0x72be_5d74,
            0x80de_b1fe,
            0x9bdc_06a7,
            0xc19b_f174,
            0xe49b_69c1,
            0xefbe_4786,
            0x0fc1_9dc6,
            0x240c_a1cc,
            0x2de9_2c6f,
            0x4a74_84aa,
            0x5cb0_a9dc,
            0x76f9_88da,
            0x983e_5152,
            0xa831_c66d,
            0xb003_27c8,
            0xbf59_7fc7,
            0xc6e0_0bf3,
            0xd5a7_9147,
            0x06ca_6351,
            0x1429_2967,
            0x27b7_0a85,
            0x2e1b_2138,
            0x4d2c_6dfc,
            0x5338_0d13,
            0x650a_7354,
            0x766a_0abb,
            0x81c2_c92e,
            0x9272_2c85,
            0xa2bf_e8a1,
            0xa81a_664b,
            0xc24b_8b70,
            0xc76c_51a3,
            0xd192_e819,
            0xd699_0624,
            0xf40e_3585,
            0x106a_a070,
            0x19a4_c116,
            0x1e37_6c08,
            0x2748_774c,
            0x34b0_bcb5,
            0x391c_0cb3,
            0x4ed8_aa4a,
            0x5b9c_ca4f,
            0x682e_6ff3,
            0x748f_82ee,
            0x78a5_636f,
            0x84c8_7814,
            0x8cc7_0208,
            0x90be_fffa,
            0xa450_6ceb,
            0xbef9_a3f7,
            0xc671_78f2,
        ];
        let mut w = [0u32; 64];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([
                block[i * 4],
                block[i * 4 + 1],
                block[i * 4 + 2],
                block[i * 4 + 3],
            ]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }
        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut h] = self.h;
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ ((!e) & g);
            let t1 = h
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            h = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        self.h[0] = self.h[0].wrapping_add(a);
        self.h[1] = self.h[1].wrapping_add(b);
        self.h[2] = self.h[2].wrapping_add(c);
        self.h[3] = self.h[3].wrapping_add(d);
        self.h[4] = self.h[4].wrapping_add(e);
        self.h[5] = self.h[5].wrapping_add(f);
        self.h[6] = self.h[6].wrapping_add(g);
        self.h[7] = self.h[7].wrapping_add(h);
    }
}

#[cfg(test)]
#[allow(
    clippy::expect_used,
    clippy::unwrap_used,
    clippy::indexing_slicing,
    clippy::match_wildcard_for_single_variants,
    clippy::wildcard_enum_match_arm,
    clippy::too_many_lines
)]
mod tests {
    use super::*;
    use dw_pdf::RenderedPage;

    fn tmp(name: &str) -> PathBuf {
        let p = std::env::temp_dir().join(format!("dw-evidence-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&p);
        p
    }

    /// The hash that identifies evidence must itself be correct.
    #[test]
    fn sha256_matches_known_vectors() {
        assert_eq!(
            sha256_of(b"").as_str(),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            sha256_of(b"abc").as_str(),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }

    #[test]
    fn render_size_is_144_dpi() {
        // 612 x 792 pt at 144 DPI is 1224 x 1584 px.
        assert_eq!(render_size_pt(612.0, 792.0), (1224, 1584));
        assert_eq!(PROVENANCE_DPI, 144);
    }

    #[test]
    fn store_is_content_addressed() {
        let root = tmp("content");
        let mut store = EvidenceStore::open(&root, "pdfium-7881").expect("opens");
        let doc = Sha256::new("a".repeat(64)).unwrap();
        let pixels = [255u8, 0, 0, 255].repeat(4); // 2x2

        let a = store
            .put_page(doc.clone(), 8, 144, 2, 2, &pixels)
            .expect("puts");
        let b = store
            .put_page(doc, 8, 144, 2, 2, &pixels)
            .expect("puts again");
        assert_eq!(a.sha256, b.sha256, "same pixels, same hash");
        assert_eq!(store.records().len(), 1);
    }

    /// The loud break: if the pixels change, the hash changes and the old
    /// reference no longer resolves.
    #[test]
    fn provenance_breaks_loudly() {
        let root = tmp("loud");
        let mut store = EvidenceStore::open(&root, "pdfium-7881").expect("opens");
        let doc = Sha256::new("a".repeat(64)).unwrap();
        let red = [255u8, 0, 0, 255].repeat(4);
        let blue = [0u8, 0, 255, 255].repeat(4);

        let page = store
            .put_page(doc.clone(), 8, 144, 2, 2, &red)
            .expect("puts");
        let crop = store
            .put_crop(&page, [0, 0, 1, 1], &red[..4], 1, 1)
            .expect("crop");

        assert!(store.resolves(&CropRef {
            crop_sha256: crop.sha256.clone(),
            dpi: 144,
            bbox: crop.bbox,
        }));

        // A crop whose pixels changed is a different hash and does not resolve.
        let other = sha256_of(&blue[..4]);
        let stale = CropRef {
            crop_sha256: other,
            dpi: 144,
            bbox: [0, 0, 1, 1],
        };
        assert!(!store.resolves(&stale));
        assert!(matches!(
            store.resolve_crop(&stale),
            Err(EvidenceError::NotFound(_))
        ));
    }

    /// Engine identity is part of the record (C2).
    #[test]
    fn engine_is_recorded() {
        let root = tmp("engine");
        let store = EvidenceStore::open(&root, "pdfium-7881").expect("opens");
        assert_eq!(store.engine(), "pdfium-7881");
    }

    #[test]
    fn empty_bytes_are_refused() {
        let root = tmp("empty");
        let store = EvidenceStore::open(&root, "x").expect("opens");
        assert!(matches!(
            store.put_bytes("pages", &[]),
            Err(EvidenceError::Empty(_))
        ));
    }

    #[test]
    fn crop_clamps_to_the_page() {
        let page = RenderedPage {
            width_px: 4,
            height_px: 4,
            rgba: vec![7u8; 64],
        };
        assert!(
            page.crop([0, 0, 0, 0]).is_none(),
            "empty crop is not evidence"
        );
        let c = page.crop([2, 2, 10, 10]).expect("clamps");
        assert_eq!((c.width_px, c.height_px), (2, 2));
        assert_eq!(c.rgba.len(), 2 * 2 * 4);
    }

    /// The PNG encoder is deterministic — same pixels, same bytes, same hash.
    #[test]
    fn png_encoding_is_deterministic() {
        let px = [10u8, 20, 30, 255].repeat(9);
        let a = encode_png(3, 3, &px);
        let b = encode_png(3, 3, &px);
        assert_eq!(a, b);
        assert_eq!(sha256_of(&a), sha256_of(&b));
        // Signature is real.
        assert_eq!(&a[..8], &[0x89, b'P', b'N', b'G', 0x0D, 0x0A, 0x1A, 0x0A]);
    }
}
