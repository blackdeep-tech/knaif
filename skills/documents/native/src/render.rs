//! Rendering / OCR trait boundary — the seam between the shared deterministic core and the
//! platform-specific backends (decided 2026-07-05; see project memory
//! `project_native_rendering_tooling_strategy`).
//!
//! The orchestration (which pages, quality, output naming) stays platform-agnostic and depends only
//! on these traits. Desktop wires PDFium (bundled, feature `pdfium`) as the [`Rasterizer`] and
//! Tesseract as the [`OcrEngine`]; mobile will wire PDFium in-process + a platform-native OCR
//! (iOS Vision / Android ML Kit) via FFI. gs/PDFium are **not** substitutes — PDFium *renders*,
//! Ghostscript *compresses*; compression keeps its own (lopdf lossless + optional gs) path.

use std::path::Path;

/// A rasterized page: 8-bit RGBA pixels, row-major, `width * height * 4` bytes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RasterImage {
    pub width: u32,
    pub height: u32,
    pub rgba: Vec<u8>,
}

impl RasterImage {
    pub fn new(width: u32, height: u32, rgba: Vec<u8>) -> Self {
        debug_assert_eq!(rgba.len(), (width * height * 4) as usize);
        Self {
            width,
            height,
            rgba,
        }
    }
}

/// Renders PDF pages to rasters. Desktop = PDFium (bundled); mobile = PDFium in-proc / platform-native.
pub trait Rasterizer {
    /// Number of pages in the PDF at `pdf`.
    fn page_count(&self, pdf: &Path) -> anyhow::Result<u32>;
    /// Render 1-based `page` at `dpi` (72 dpi renders PDF points 1:1).
    fn render_page(&self, pdf: &Path, page: u32, dpi: f32) -> anyhow::Result<RasterImage>;
}

/// Recognizes text in a raster. Desktop = Tesseract; mobile = iOS Vision / Android ML Kit.
pub trait OcrEngine {
    /// Recognize text in `image` for `language` (e.g. `"eng"`).
    fn recognize(&self, image: &RasterImage, language: &str) -> anyhow::Result<String>;
}

#[cfg(feature = "pdfium")]
pub use pdfium_backend::PdfiumRasterizer;

#[cfg(feature = "pdfium")]
#[path = "pdfium_backend.rs"]
mod pdfium_backend;

#[cfg(test)]
mod tests {
    use super::*;

    /// A deterministic stand-in so the trait-consuming orchestration is testable without a native lib.
    struct MockRasterizer;
    impl Rasterizer for MockRasterizer {
        fn page_count(&self, _pdf: &Path) -> anyhow::Result<u32> {
            Ok(2)
        }
        fn render_page(&self, _pdf: &Path, _page: u32, _dpi: f32) -> anyhow::Result<RasterImage> {
            // 2x2 opaque white
            Ok(RasterImage::new(2, 2, vec![255; 16]))
        }
    }

    fn describe<R: Rasterizer>(r: &R, pdf: &Path) -> anyhow::Result<(u32, (u32, u32))> {
        let n = r.page_count(pdf)?;
        let img = r.render_page(pdf, 1, 150.0)?;
        Ok((n, (img.width, img.height)))
    }

    #[test]
    fn rasterizer_trait_is_object_and_generic_usable() {
        let (pages, dims) = describe(&MockRasterizer, Path::new("x.pdf")).unwrap();
        assert_eq!(pages, 2);
        assert_eq!(dims, (2, 2));

        // usable as a trait object too (dynamic dispatch, for the platform-swappable backend)
        let boxed: Box<dyn Rasterizer> = Box::new(MockRasterizer);
        assert_eq!(boxed.page_count(Path::new("x.pdf")).unwrap(), 2);
    }

    #[test]
    fn raster_image_holds_rgba() {
        let img = RasterImage::new(1, 1, vec![10, 20, 30, 255]);
        assert_eq!(img.rgba, vec![10, 20, 30, 255]);
    }
}
