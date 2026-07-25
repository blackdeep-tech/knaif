//! PDFium desktop [`Rasterizer`] (feature `pdfium`). Binds the PDFium runtime library dynamically
//! at run time (so this compiles without the binary present); the binary is bundled in packaging
//! (Phase 9). Uses `as_rgba_bytes` so the `image` crate isn't needed here.

use std::path::{Path, PathBuf};

use pdfium_render::prelude::*;

use super::{RasterImage, Rasterizer};

/// Renders PDF pages via a bundled PDFium runtime library.
pub struct PdfiumRasterizer {
    pdfium: Pdfium,
}

impl PdfiumRasterizer {
    /// Bind to the PDFium library and build a rasterizer. Search order: `$KNAIF_PDFIUM_PATH` (a
    /// directory), the executable's directory, then the system library.
    pub fn new() -> anyhow::Result<Self> {
        let bindings = bind_pdfium().map_err(|e| {
            anyhow::anyhow!(
                "could not load the PDFium library ({e:?}). Put the pdfium runtime library next to \
                 the executable or set KNAIF_PDFIUM_PATH to the folder containing it."
            )
        })?;
        Ok(Self {
            pdfium: Pdfium::new(bindings),
        })
    }
}

fn bind_pdfium() -> Result<Box<dyn PdfiumLibraryBindings>, PdfiumError> {
    let candidates = [
        std::env::var_os("KNAIF_PDFIUM_PATH").map(PathBuf::from),
        std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(Path::to_path_buf)),
    ];
    for dir in candidates.into_iter().flatten() {
        let name = Pdfium::pdfium_platform_library_name_at_path(&dir);
        if let Ok(bindings) = Pdfium::bind_to_library(&name) {
            return Ok(bindings);
        }
    }
    Pdfium::bind_to_system_library()
}

impl Rasterizer for PdfiumRasterizer {
    fn page_count(&self, pdf: &Path) -> anyhow::Result<u32> {
        let doc = self
            .pdfium
            .load_pdf_from_file(pdf, None)
            .map_err(|e| anyhow::anyhow!("could not open PDF {} ({e:?})", pdf.display()))?;
        Ok(doc.pages().len() as u32)
    }

    fn render_page(&self, pdf: &Path, page: u32, dpi: f32) -> anyhow::Result<RasterImage> {
        if page < 1 {
            anyhow::bail!("page must be 1-based");
        }
        let doc = self
            .pdfium
            .load_pdf_from_file(pdf, None)
            .map_err(|e| anyhow::anyhow!("could not open PDF {} ({e:?})", pdf.display()))?;
        let page_obj = doc
            .pages()
            .get((page - 1) as u16)
            .map_err(|e| anyhow::anyhow!("no page {page} in {} ({e:?})", pdf.display()))?;
        // 72 dpi = PDF points 1:1; scale accordingly.
        let config = PdfRenderConfig::new().scale_page_by_factor(dpi / 72.0);
        let bitmap = page_obj
            .render_with_config(&config)
            .map_err(|e| anyhow::anyhow!("could not render page {page} ({e:?})"))?;
        let (width, height) = (bitmap.width() as u32, bitmap.height() as u32);
        Ok(RasterImage::new(width, height, bitmap.as_rgba_bytes()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Real render, gated on the PDFium runtime lib + a test PDF (`$KNAIF_TEST_PDF`, plus
    /// `$KNAIF_PDFIUM_PATH` or a system lib). A no-op when unset — mirrors the `$KNAIF_TEST_GGUF`
    /// pattern so CI without the native lib stays green.
    #[test]
    fn renders_a_real_pdf_when_lib_present() {
        let Ok(pdf) = std::env::var("KNAIF_TEST_PDF") else {
            return;
        };
        let pdf = std::path::PathBuf::from(pdf);
        let rasterizer = PdfiumRasterizer::new().expect("bind PDFium");
        assert!(rasterizer.page_count(&pdf).unwrap() >= 1);
        let img = rasterizer.render_page(&pdf, 1, 100.0).unwrap();
        assert!(img.width > 0 && img.height > 0);
        assert_eq!(img.rgba.len(), (img.width * img.height * 4) as usize);
    }
}
